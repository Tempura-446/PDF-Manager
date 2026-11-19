# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for annotation & review summary export (Markdown, filters, page labels, CLI, GUI)."""

from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    TextStringObject,
)
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pdfmanager import cli, comments, gui


def _make_annotated_pdf(path: Path) -> Path:
    raw = path.with_suffix(".raw.pdf")
    c = canvas.Canvas(str(raw), pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "Important clause requiring legal review.")
    c.showPage()
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "Revise duct routing near column B4.")
    c.showPage()
    c.save()

    reader = PdfReader(str(raw))
    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)

    # Highlight on page 1 with author, yellow color, and comment
    hl = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Highlight"),
        NameObject("/Rect"): ArrayObject([FloatObject(72), FloatObject(696), FloatObject(280), FloatObject(714)]),
        NameObject("/QuadPoints"): ArrayObject([
            FloatObject(72), FloatObject(714), FloatObject(280), FloatObject(714),
            FloatObject(72), FloatObject(696), FloatObject(280), FloatObject(696),
        ]),
        NameObject("/T"): TextStringObject("Alice"),
        NameObject("/Contents"): TextStringObject("Check indemnification cap."),
        NameObject("/C"): ArrayObject([FloatObject(1.0), FloatObject(1.0), FloatObject(0.0)]),
    })
    hl_ref = writer._add_object(hl)

    # Reply to highlight on page 1
    reply = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Text"),
        NameObject("/Rect"): ArrayObject([FloatObject(72), FloatObject(696), FloatObject(92), FloatObject(714)]),
        NameObject("/T"): TextStringObject("Bob"),
        NameObject("/Contents"): TextStringObject("Updated to 2x fees."),
        NameObject("/IRT"): hl_ref,
    })
    reply_ref = writer._add_object(reply)
    writer.pages[0][NameObject("/Annots")] = ArrayObject([hl_ref, reply_ref])

    # Revision cloud on page 2 by Bob with red color
    cloud = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Polygon"),
        NameObject("/BE"): DictionaryObject({NameObject("/S"): NameObject("/C")}),
        NameObject("/Rect"): ArrayObject([FloatObject(60), FloatObject(680), FloatObject(300), FloatObject(720)]),
        NameObject("/T"): TextStringObject("Bob"),
        NameObject("/Contents"): TextStringObject("Shift duct 200mm east."),
        NameObject("/C"): ArrayObject([FloatObject(1.0), FloatObject(0.0), FloatObject(0.0)]),
    })
    cloud_ref = writer._add_object(cloud)
    writer.pages[1][NameObject("/Annots")] = ArrayObject([cloud_ref])

    with open(path, "wb") as f:
        writer.write(f)
    raw.unlink()
    return path


def test_collect_captures_color_and_replies(tmp_path):
    pdf = _make_annotated_pdf(tmp_path / "review.pdf")
    found = comments.collect(pdf)
    assert len(found) == 2
    assert comments.count(found) == 3

    first, second = found
    assert first["kind"] == "Highlight"
    assert first["author"] == "Alice"
    assert first["color"] == "#FFFF00"
    assert "Important clause" in first["marked"]
    assert len(first["replies"]) == 1
    assert first["replies"][0]["author"] == "Bob"

    assert second["kind"] == "Cloud"
    assert second["author"] == "Bob"
    assert second["color"] == "#FF0000"


def test_filter_comments(tmp_path):
    pdf = _make_annotated_pdf(tmp_path / "review.pdf")
    found = comments.collect(pdf)

    assert len(comments.filter_comments(found, author="alice")) == 1
    assert len(comments.filter_comments(found, kind="cloud")) == 1
    assert len(comments.filter_comments(found, color="ff0000")) == 1
    assert len(comments.filter_comments(found, pages="2")) == 1
    assert len(comments.filter_comments(found, author="nobody")) == 0


def test_to_markdown_groupings(tmp_path):
    pdf = _make_annotated_pdf(tmp_path / "review.pdf")
    found = comments.collect(pdf)

    md_page = comments.to_markdown(found, title="review.pdf", group_by="page")
    assert "# Review Summary: review.pdf" in md_page
    assert "**3 comments**" in md_page
    assert "## Page 1" in md_page
    assert "## Page 2" in md_page
    assert "> Important clause" in md_page
    assert "Check indemnification cap." in md_page
    assert "- **Reply** (Bob): Updated to 2x fees." in md_page
    assert "`#FFFF00`" in md_page

    md_type = comments.to_markdown(found, title="review.pdf", group_by="type")
    assert "## Highlight" in md_type
    assert "## Cloud" in md_type

    md_author = comments.to_markdown(found, title="review.pdf", group_by="author")
    assert "## Alice" in md_author
    assert "## Bob" in md_author

    md_empty = comments.to_markdown([], title="empty.pdf")
    assert "_No comments._" in md_empty


def test_cli_comments_markdown_and_filters(tmp_path, capsys):
    pdf = _make_annotated_pdf(tmp_path / "review.pdf")
    out = tmp_path / "summary.md"
    rc = cli.main(["comments", str(pdf), "--markdown", "--group-by", "type", "--kind", "Cloud", "-o", str(out)])
    assert rc == 0
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "## Cloud" in content
    assert "Shift duct 200mm east." in content
    assert "Check indemnification cap." not in content


def test_gui_comments_markdown_mode(tmp_path):
    pdf = _make_annotated_pdf(tmp_path / "review.pdf")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    notes = []
    gui._run_file_op(
        "comments", pdf, out_dir,
        {"mode": "markdown", "group_by": "author", "author": "Alice"},
        None, {}, notes,
    )
    md_file = out_dir / "review_comments.md"
    assert md_file.exists()
    content = md_file.read_text(encoding="utf-8")
    assert "## Alice" in content
    assert "Check indemnification cap." in content
    assert "Shift duct" not in content
