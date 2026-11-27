# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for v0.93.0 Smart Redline Contract & Spec Diff."""

from unittest import mock

from reportlab.pdfgen import canvas as rl_canvas

from pdfmanager import redline
from pdfmanager.cli import parser as cli_parser
from pdfmanager.server.operations import catalog


def _make_contract(path, lines):
    c = rl_canvas.Canvas(str(path))
    y = 750
    for ln in lines:
        c.drawString(60, y, ln)
        y -= 24
    c.showPage()
    c.save()


def test_smart_redline_detects_material_changes_and_exports_all_formats(tmp_path):
    v1 = tmp_path / "agreement_v1.pdf"
    v2 = tmp_path / "agreement_v2.pdf"
    _make_contract(v1, [
        "1. Payment Terms: Client shall pay $10,000 on Net 30 terms.",
        "2. Confidentiality: Both parties keep data confidential for 2 years.",
        "3. Termination: Either party may terminate with 30 days notice.",
    ])
    _make_contract(v2, [
        "1. Payment Terms: Client shall pay $12,500 on Net 60 terms.",
        "2. Confidentiality: Both parties keep data confidential for 2 years.",
        "3. Termination: Either party may terminate with 30 days notice.",
        "4. Late Fee: Overdue invoices accrue 1.5% monthly interest.",
    ])

    report = redline.compute_redline(v1, v2)
    s = report["summary"]
    assert s["modified"] == 1
    assert s["added"] == 1
    assert s["removed"] == 0
    assert s["material"] == 2
    assert s["total_changes"] == 2

    mod_ch = next(c for c in report["changes"] if c["kind"] == "modified")
    assert mod_ch["material"] is True
    assert any("$12,500" in tok for tok in mod_ch["numeric_changes"])
    assert any("Net 60" in tok for tok in mod_ch["numeric_changes"])

    md = redline.export_redline_markdown(report, ai_summary="Price increased by $2,500.")
    assert "~~" in md and "**" in md and "[MATERIAL]" in md

    html_out = redline.export_redline_html(report, ai_summary="Price increased.")
    assert "<del>" in html_out and "<ins>" in html_out and "MATERIAL" in html_out

    pdf_out = tmp_path / "redline.pdf"
    redline.export_redline_pdf(report, pdf_out, ai_summary="Price increased.")
    assert pdf_out.is_file() and pdf_out.stat().st_size > 1000

    with mock.patch("pdfmanager.assistant._chat", return_value=("Risk: Net 60 extends payment window.", "qwen2.5:7b")):
        ai = redline.explain_redline_ai(report)
        assert "Net 60" in ai["explanation"]
        assert ai["model"] == "qwen2.5:7b"


def test_redline_cli_and_catalog_registered():
    p = cli_parser.build_parser()
    a = p.parse_args(["redline", "v1.pdf", "v2.pdf", "-o", "diff.md", "--explain"])
    assert a.old == "v1.pdf"
    assert a.new == "v2.pdf"
    assert a.explain is True
    assert "redline" in {op["id"] for op in catalog.OPERATIONS}
