# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Track C depth tests: GUI regulated redaction, custom regex, reason codes, sec-profile tool, and audit/policy APIs."""

import json
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pdfmanager.server import operations


def _make_pdf(path: Path, lines: list[str]) -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica", 11)
    y = 740
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.showPage()
    c.save()
    return path


def test_gui_redact_supports_regulated_presets_regex_reason_and_log(tmp_path):
    src = _make_pdf(tmp_path / "doc.pdf", [
        "SSN: 123-45-6789",
        "Matter ID: CASE-987654",
    ])
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    notes = []

    outputs = operations._run_file_op(
        "redact",
        src,
        out_dir,
        {
            "ssn": "true",
            "regex": r"CASE-\d{6}",
            "reason_code": "(b)(6)",
            "export_log": "true",
            "method": "image",
        },
        None,
        {},
        notes,
    )
    assert len(outputs) == 2
    pdf_out, log_out = outputs
    assert pdf_out.suffix == ".pdf" and pdf_out.exists()
    assert log_out.name.endswith("_redaction_log.json") and log_out.exists()

    log_data = json.loads(log_out.read_text(encoding="utf-8"))
    assert len(log_data) == 2
    assert all(entry["reason"] == "(b)(6)" for entry in log_data)


def test_gui_sec_profile_tool_runs_and_protects_pdf(tmp_path):
    assert "sec-profile" in operations.OPS
    src = _make_pdf(tmp_path / "draft.pdf", ["Confidential business plan."])
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    notes = []

    outputs = operations._run_file_op(
        "sec-profile",
        src,
        out_dir,
        {"profile": "Confidential", "user_password": "Open123!"},
        None,
        {},
        notes,
    )
    assert len(outputs) == 1
    out_pdf = outputs[0]
    assert out_pdf.exists()
    reader = PdfReader(str(out_pdf))
    assert reader.is_encrypted
    assert reader.decrypt("Open123!")
    assert "CONFIDENTIAL" in (reader.pages[0].extract_text() or "")
    assert any("Confidential" in n for n in notes)
