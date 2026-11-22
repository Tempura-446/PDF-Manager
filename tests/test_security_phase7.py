# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Security Phase 7 tests: Security Profiles, Dynamic Watermarks, Regulated Redaction, Audit Log."""

import csv
import json
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pdfmanager import auditlog, cli, redact, secprofiles


def _make_pdf(path: Path, lines: list[str]) -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setAuthor("Alice Author")
    c.setTitle("Internal Draft")
    c.setFont("Helvetica", 11)
    y = 740
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.showPage()
    c.save()
    return path


# ---------------------------------------------------------------------------
# Tamper-evident hash-chained audit log
# ---------------------------------------------------------------------------

def test_audit_log_hash_chain_and_tamper_detection(tmp_path):
    log_file = tmp_path / "audit.jsonl"

    e1 = auditlog.record("encrypt", target="contract.pdf", actor="alice", details={"alg": "AES-256"}, log_path=log_file)
    e2 = auditlog.record("redact", target="contract.pdf", actor="alice", details={"boxes": 3}, log_path=log_file)
    e3 = auditlog.record("profile_applied", target="contract.pdf", actor="bob", details={"profile": "Confidential"}, log_path=log_file)

    assert e1["prev_hash"] == auditlog.GENESIS_HASH
    assert e2["prev_hash"] == e1["entry_hash"]
    assert e3["prev_hash"] == e2["entry_hash"]

    verdict = auditlog.verify_chain(log_file)
    assert verdict["valid"] is True
    assert verdict["count"] == 3
    assert verdict["errors"] == []

    # Export JSON and CSV
    json_out = auditlog.export_entries(tmp_path / "audit.json", fmt="json", log_path=log_file)
    csv_out = auditlog.export_entries(tmp_path / "audit.csv", fmt="csv", action="redact", log_path=log_file)
    assert len(json.loads(json_out.read_text(encoding="utf-8"))) == 3
    with csv_out.open("r", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["action"] == "redact"

    # Tamper with line 2 (change actor) -> verify_chain must detect it!
    lines = log_file.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["actor"] = "mallory"
    lines[1] = json.dumps(tampered, sort_keys=True)
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    verdict_bad = auditlog.verify_chain(log_file)
    assert verdict_bad["valid"] is False
    assert any(err["line"] == 2 for err in verdict_bad["errors"])


# ---------------------------------------------------------------------------
# Security Profiles & Dynamic Print Watermarks
# ---------------------------------------------------------------------------

def test_security_profile_apply_export_import_and_audit(tmp_path):
    store = tmp_path / "profiles.json"
    audit_file = tmp_path / "audit.jsonl"
    src = _make_pdf(tmp_path / "draft.pdf", ["Quarterly financial report."])
    out = tmp_path / "protected.pdf"

    # Save custom profile, export it, delete it, re-import it
    secprofiles.save_profile(
        "Board Packet",
        {
            "description": "Board distribution security profile",
            "make_safe": True,
            "remove_hidden": True,
            "watermark_text": "BOARD ONLY — {date}",
            "watermark_opacity": 0.2,
            "permissions": {"printing": "none", "changes": "none", "copy": False},
        },
        store_path=store,
    )
    exported = secprofiles.export_profile("Board Packet", tmp_path / "board.json", store_path=store)
    secprofiles.delete_profile("Board Packet", store_path=store)
    imported = secprofiles.import_profile(exported, store_path=store)
    assert imported["name"] == "Board Packet"

    result = secprofiles.apply_profile(
        src,
        out,
        "Board Packet",
        user_password="OpenPassword123!",
        owner_password="OwnerPassword456!",
        store_path=store,
        audit_path=audit_file,
    )
    assert out.exists()
    assert set(result["steps"]) == {"make_safe", "remove_hidden", "watermark", "encrypt"}

    reader = PdfReader(str(out))
    assert reader.is_encrypted
    assert reader.decrypt("OpenPassword123!")
    # Metadata was stripped by remove_hidden
    assert not (reader.metadata and reader.metadata.get("/Author"))
    # Watermark text is present on the page
    assert "BOARD ONLY" in (reader.pages[0].extract_text() or "")

    # Audit log recorded the profile application
    entries = auditlog.read_entries(action="profile_applied", log_path=audit_file)
    assert len(entries) == 1
    assert entries[0]["details"]["profile"] == "Board Packet"
    assert auditlog.verify_chain(audit_file)["valid"] is True


def test_dynamic_print_watermark_expands_placeholders(tmp_path):
    src = _make_pdf(tmp_path / "doc.pdf", ["Original content."])
    out = tmp_path / "printed.pdf"
    fixed_dt = datetime(2026, 11, 22, 14, 30)

    secprofiles.dynamic_print_watermark(
        src,
        out,
        template="Printed copy — {user} — {datetime} — {file}",
        user="jdoe",
        now=fixed_dt,
    )
    assert out.exists()
    text = PdfReader(str(out)).pages[0].extract_text() or ""
    assert "Printed copy" in text
    assert "jdoe" in text
    assert "2026-11-22 14:30" in text


# ---------------------------------------------------------------------------
# Regulated Redaction: National IDs, Luhn Check, Reason Codes & Redaction Log
# ---------------------------------------------------------------------------

def test_regulated_presets_and_luhn_filter(tmp_path):
    # 4532-0151-1283-0366 is a valid Luhn Visa test number;
    # 4532-0151-1283-0367 fails Luhn and must NOT be redacted by credit_card preset.
    assert redact.luhn_valid("4532-0151-1283-0366") is True
    assert redact.luhn_valid("4532-0151-1283-0367") is False

    src = _make_pdf(
        tmp_path / "regulated.pdf",
        [
            "US SSN: 123-45-6789",
            "US EIN: 12-3456789",
            "UK NI: AB 12 34 56 C",
            "IBAN: DE89 3704 0044 0532 0130 00",
            "Valid Card: 4532-0151-1283-0366",
            "Not a Card: 4532-0151-1283-0367",
        ],
    )
    out = tmp_path / "redacted.pdf"
    patterns = [
        redact.PRESETS["ssn"],
        redact.PRESETS["ein"],
        redact.PRESETS["uk_ni"],
        redact.PRESETS["iban"],
        redact.PRESETS["credit_card"],
    ]
    report = redact.redact(
        src, out, patterns=patterns, method="image", dpi=100,
        reason_code="(b)(6)", actor="reviewer1",
    )
    assert out.exists()
    # 5 valid regulated items matched (invalid Luhn card was skipped)
    assert len(report.redaction_log) == 5
    assert all(entry["reason"] == "(b)(6)" for entry in report.redaction_log)
    assert all(entry["actor"] == "reviewer1" for entry in report.redaction_log)

    log_json = redact.export_redaction_log(report, tmp_path / "redact_log.json", fmt="json")
    log_csv = redact.export_redaction_log(report, tmp_path / "redact_log.csv", fmt="csv")
    assert len(json.loads(log_json.read_text(encoding="utf-8"))) == 5
    with log_csv.open("r", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 5
    assert rows[0]["reason"] == "(b)(6)"


def test_cli_profile_list_and_audit_verify(capsys):
    assert cli.main(["profile", "list"]) == 0
    out = capsys.readouterr().out
    assert "Confidential" in out
    assert "Public Share" in out

    assert cli.main(["audit", "verify"]) == 0
    out2 = capsys.readouterr().out
    assert "verified ok" in out2.lower()
