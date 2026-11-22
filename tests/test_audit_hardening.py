# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Regression tests for deep source-code audit & hardening fixes (BUG-01 through BUG-07)."""

import csv
import json
import os
import stat
import sys
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pdfmanager import auditlog, core, policy, redact, secprofiles, update
from pdfmanager.server import operations


def _make_pdf(path: Path, text: str = "Hardening audit test.") -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(72, 720, text)
    c.showPage()
    c.save()
    return path


def test_inplace_compress_never_bloats_original(tmp_path):
    # BUG-01: compress(doc, doc) must never grow an already-tiny PDF
    pdf = _make_pdf(tmp_path / "tiny.pdf")
    orig_size = pdf.stat().st_size
    stats = {}
    core.compress(pdf, pdf, level="low", stats=stats)
    assert pdf.stat().st_size <= orig_size
    assert stats["output_bytes"] <= orig_size


def test_auditlog_csv_neutralizes_formulas_and_sets_private_perms(tmp_path):
    # BUG-02: CSV formula injection protection + 0o600 permissions on POSIX
    log_file = tmp_path / "audit.jsonl"
    auditlog.record(
        "redact",
        target="=SUM(A1:A2).pdf",
        actor="+admin",
        details={"note": "@SUM(1,2)"},
        log_path=log_file,
    )
    assert auditlog.verify_chain(log_file)["valid"] is True
    if sys.platform != "win32":
        mode = stat.S_IMODE(log_file.stat().st_mode)
        assert (mode & 0o077) == 0

    csv_out = auditlog.export_entries(tmp_path / "audit.csv", fmt="csv", log_path=log_file)
    with csv_out.open("r", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["target"].startswith("'=")
    assert rows[0]["actor"].startswith("'+")


def test_redact_log_csv_neutralizes_formulas(tmp_path):
    # BUG-03: CSV formula injection protection in export_redaction_log
    src = _make_pdf(tmp_path / "doc.pdf", "Secret word TOPSECRET.")
    out = tmp_path / "redacted.pdf"
    report = redact.redact(
        src, out, terms=["TOPSECRET"], method="image", dpi=72,
        reason_code="=CMD|'/C calc'!A0", actor="@reviewer",
    )
    csv_out = redact.export_redaction_log(report, tmp_path / "log.csv", fmt="csv")
    with csv_out.open("r", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["reason"].startswith("'=")
    assert rows[0]["actor"].startswith("'@")


def test_export_profile_never_leaks_stored_passwords(tmp_path):
    # BUG-04: export_profile strips user_password & owner_password
    store = tmp_path / "profiles.json"
    secprofiles.save_profile(
        "SecretProfile",
        {
            "make_safe": True,
            "user_password": "DoNotLeakUser!",
            "owner_password": "DoNotLeakOwner!",
        },
        store_path=store,
    )
    exported = secprofiles.export_profile("SecretProfile", tmp_path / "shared.json", store_path=store)
    text = exported.read_text(encoding="utf-8")
    assert "DoNotLeakUser!" not in text
    assert "DoNotLeakOwner!" not in text
    data = json.loads(text)
    assert "user_password" not in data["profile"]
    assert "owner_password" not in data["profile"]


def test_update_check_respects_disable_updates_policy(tmp_path, monkeypatch):
    # BUG-05: disable_updates / disable_network policy blocks update.check()
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps({"disable_updates": True}), encoding="utf-8")
    monkeypatch.setenv("ZPDF_POLICY_FILE", str(pol))
    policy.clear_cache()
    try:
        res_bg = update.check(force=False)
        assert res_bg["available"] is False
        assert res_bg["latest"] is None
        res_force = update.check(force=True)
        assert res_force["available"] is False
        assert "policy" in (res_force["error"] or "").lower()
    finally:
        monkeypatch.delenv("ZPDF_POLICY_FILE", raising=False)
        policy.clear_cache()


def test_gui_compress_invalid_target_mb_raises_user_error(tmp_path):
    # BUG-06: non-numeric target_mb raises UserError instead of raw ValueError
    src = _make_pdf(tmp_path / "doc.pdf")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with pytest.raises(operations.UserError, match="positive number"):
        operations._run_file_op("compress", src, out_dir, {"target_mb": "not-a-number"}, None, {}, [])
