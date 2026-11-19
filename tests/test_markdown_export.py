# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for PDF → Markdown export (markdown_export.py, CLI to-md, GUI to-md)."""

from pathlib import Path

from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdfmanager import cli, gui, markdown_export


def _make_rich_pdf(path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setTitle("Quarterly Report")
    c.setAuthor("Ada Lovelace")

    # Page 1: title heading, styled paragraph with link, bullet list, numbered list
    c.setFont("Helvetica-Bold", 22)
    c.drawString(72, 730, "Quarterly Report")

    c.setFont("Helvetica-Bold", 15)
    c.drawString(72, 690, "Overview")

    c.setFont("Helvetica", 11)
    c.drawString(72, 660, "Revenue grew ")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(145, 660, "strongly")
    c.setFont("Helvetica", 11)
    c.drawString(190, 660, " and ")
    c.setFont("Helvetica-Oblique", 11)
    c.drawString(216, 660, "steadily")
    c.setFont("Helvetica", 11)
    c.drawString(258, 660, " via ")
    c.setFont("Courier", 11)
    c.drawString(282, 660, "sync_v2")
    c.setFont("Helvetica", 11)
    c.drawString(332, 660, ".")

    # External hyperlink
    c.setFont("Helvetica", 11)
    c.drawString(72, 635, "Visit Example Docs for details.")
    c.linkURL("https://example.com/docs", (98, 630, 175, 648), relative=0)

    # Bulleted list
    c.drawString(72, 600, "• First bullet item")
    c.drawString(72, 582, "• Second bullet item")

    # Numbered list
    c.drawString(72, 550, "1. Step one")
    c.drawString(72, 532, "2. Step two")

    c.showPage()

    # Page 2: ruled table + embedded image
    c.setFont("Helvetica-Bold", 15)
    c.drawString(72, 730, "Metrics")

    # Ruled 3x2 table
    xs = [72, 220, 360]
    ys = [700, 675, 650, 625]
    for y in ys:
        c.line(xs[0], y, xs[-1], y)
    for x in xs:
        c.line(x, ys[0], x, ys[-1])
    rows = [
        ("Region", "Total"),
        ("North | West", "120"),
        ("South", "95"),
    ]
    for r_idx, (col0, col1) in enumerate(rows):
        y_text = ys[r_idx] - 17
        c.setFont("Helvetica-Bold" if r_idx == 0 else "Helvetica", 11)
        c.drawString(xs[0] + 6, y_text, col0)
        c.drawString(xs[1] + 6, y_text, col1)

    # Embedded image
    img = Image.new("RGB", (120, 80), color=(40, 110, 200))
    c.drawImage(ImageReader(img), 72, 480, width=120, height=80)

    c.showPage()
    c.save()
    return path


def _make_blank_pdf(path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    c.showPage()
    c.save()
    return path


def test_markdown_structure_styles_lists_and_links(tmp_path):
    pdf = _make_rich_pdf(tmp_path / "report.pdf")
    md = markdown_export.to_markdown_text(pdf, pages="1")

    assert "# Quarterly Report" in md
    assert "## Overview" in md
    assert "**strongly**" in md
    assert "*steadily*" in md
    assert "`sync_v2`" in md
    assert "](https://example.com/docs)" in md
    assert "- First bullet item\n- Second bullet item" in md
    assert "1. Step one\n2. Step two" in md


def test_markdown_table_pipe_escape_and_no_duplication(tmp_path):
    pdf = _make_rich_pdf(tmp_path / "report.pdf")
    out = tmp_path / "report.md"
    stats = markdown_export.pdf_to_markdown(pdf, out, pages="2")
    md = out.read_text(encoding="utf-8")

    assert stats["tables"] == 1
    assert "| Region | Total |" in md
    assert "| --- | --- |" in md
    assert "| North \\| West | 120 |" in md
    assert "| South | 95 |" in md
    # Cell contents should not be emitted a second time outside the table
    assert md.count("North \\| West") == 1
    assert md.count("South") == 1


def test_markdown_extract_images_and_front_matter_and_page_breaks(tmp_path):
    pdf = _make_rich_pdf(tmp_path / "report.pdf")
    out = tmp_path / "report.md"
    stats = markdown_export.pdf_to_markdown(
        pdf, out, extract_images=True, page_breaks=True, front_matter=True,
    )
    md = out.read_text(encoding="utf-8")

    assert md.startswith("---\n")
    assert 'title: "Quarterly Report"' in md
    assert 'author: "Ada Lovelace"' in md
    assert "pages: 2" in md
    assert 'source: "report.pdf"' in md
    assert "<!-- Page 2 -->" in md
    assert "\n---\n" in md
    assert stats["images"] >= 1
    assets = tmp_path / "report_assets"
    assert assets.is_dir()
    saved = list(assets.glob("*.png"))
    assert len(saved) == stats["images"]
    assert f"![Figure 1](report_assets/{saved[0].name})" in md


def test_markdown_blank_pdf_is_graceful(tmp_path):
    pdf = _make_blank_pdf(tmp_path / "blank.pdf")
    out = tmp_path / "blank.md"
    stats = markdown_export.pdf_to_markdown(pdf, out)
    assert stats["textless"] is True
    assert out.read_text(encoding="utf-8") == ""


def test_cli_to_md(tmp_path, capsys):
    pdf = _make_rich_pdf(tmp_path / "report.pdf")
    out = tmp_path / "custom.md"
    rc = cli.main(["to-md", str(pdf), "-o", str(out), "--front-matter", "--page-breaks", "--images"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "custom.md" in captured
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert 'title: "Quarterly Report"' in content
    assert "| Region | Total |" in content


def test_gui_to_md_registered_and_runs(tmp_path):
    tool = gui.OPS.get("to-md")
    assert tool is not None
    assert tool["group"] == "Convert"

    pdf = _make_rich_pdf(tmp_path / "report.pdf")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    notes = []
    gui._run_file_op("to-md", pdf, out_dir, {"front_matter": True, "page_breaks": True}, None, [], notes)
    md_file = out_dir / "report.md"
    assert md_file.exists()
    assert "# Quarterly Report" in md_file.read_text(encoding="utf-8")
    assert notes and "2 pages" in notes[0]
