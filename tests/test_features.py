"""Tests for scan clean-up, archiving and privacy, bookmarks, comparing,
Office conversion, redaction and recipes."""

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from pdfmanager import core, gui, scan
from pdfmanager.cli import main

needs_tesseract = pytest.mark.skipif(not scan.find_tesseract(), reason="needs Tesseract")


def text_pdf(path, pages=("Hello world",), size=A4):
    """One page per string, with the text in 14 pt Helvetica."""
    c = canvas.Canvas(str(path), pagesize=size)
    for text in pages:
        y = size[1] - 100
        for line in text.split("\n"):
            c.setFont("Helvetica", 14)
            c.drawString(72, y, line)
            y -= 20
        c.showPage()
    c.save()
    return path


def _font(size):
    for candidate in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                      "arial.ttf", "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def scan_page(turn=0, blank=False):
    """A 150 DPI 'scanned' A4 page of text, turned by ``turn`` degrees."""
    im = Image.new("L", (1240, 1754), 255)
    if not blank:
        draw, font = ImageDraw.Draw(im), _font(34)
        for k in range(30):
            draw.text((80, 100 + k * 50), "The quick brown fox jumps over the lazy dog.",
                      fill=0, font=font)
    else:
        im.putpixel((600, 900), 0)  # a speck of dust
    return im.rotate(turn, expand=True)


def scan_pdf(path, pages):
    images = []
    for k, im in enumerate(pages):
        target = path.parent / f"{path.stem}_{k}.png"
        im.save(target)
        images.append(target)
    return core.images_to_pdf(images, path)


# -- scan clean-up ----------------------------------------------------------------

def test_blank_pages_are_removed(tmp_path):
    src = scan_pdf(tmp_path / "scan.pdf", [scan_page(), scan_page(blank=True), scan_page()])
    assert scan.find_blank_pages(src) == [1]
    report = scan.clean(src, tmp_path / "out.pdf")
    assert report == {"removed": [2], "rotated": {}}
    assert len(PdfReader(tmp_path / "out.pdf").pages) == 2


def test_clean_refuses_to_remove_every_page(tmp_path):
    src = scan_pdf(tmp_path / "scan.pdf", [scan_page(blank=True)])
    with pytest.raises(core.PdfManagerError, match="every page looks blank"):
        scan.clean(src, tmp_path / "out.pdf")


def test_sensitivity_levels(tmp_path):
    c = canvas.Canvas(str(tmp_path / "num.pdf"), pagesize=A4)
    c.setFont("Helvetica", 9)
    c.drawString(290, 300, "3")  # just a page number
    c.showPage()
    c.save()
    assert scan.find_blank_pages(tmp_path / "num.pdf", sensitivity="low") == []
    assert scan.find_blank_pages(tmp_path / "num.pdf", sensitivity="high") == [0]


@needs_tesseract
def test_pages_are_turned_upright(tmp_path):
    src = scan_pdf(tmp_path / "scan.pdf", [scan_page(), scan_page(90), scan_page(180)])
    report = scan.clean(src, tmp_path / "out.pdf", remove_blank=False, auto_rotate=True)
    assert report["rotated"] == {2: 90, 3: 180}
    assert scan.detect_rotation(tmp_path / "out.pdf") == {}


def test_cli_clean(tmp_path, capsys):
    src = scan_pdf(tmp_path / "scan.pdf", [scan_page(blank=True), scan_page()])
    assert main(["clean", str(src)]) == 0
    assert "removed blank pages 1" in capsys.readouterr().out
    assert len(PdfReader(tmp_path / "scan_clean.pdf").pages) == 1


def test_gui_clean_reports_what_changed(tmp_path):
    src = scan_pdf(tmp_path / "scan.pdf", [scan_page(), scan_page(blank=True)])
    notes = []
    work = tmp_path / "work"
    work.mkdir()
    name, data, _ = gui.run_operation("clean", [src], {"remove_blank": "true"}, work=work,
                                      notes=notes)
    assert name == "scan_clean.pdf" and data.startswith(b"%PDF")
    assert notes == ["scan.pdf: removed blank page 2"]


# -- archive and privacy ------------------------------------------------------------

from pdfmanager import archive  # noqa: E402

needs_ghostscript = pytest.mark.skipif(not archive.find_ghostscript(), reason="needs Ghostscript")


def form_pdf(path):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawString(72, 760, "Name:")
    c.acroForm.textfield(name="name", value="Jane Doe", x=120, y=750, width=200, height=20)
    c.acroForm.checkbox(name="agree", x=120, y=700, checked=True)
    c.linkURL("https://example.com", (72, 600, 200, 620))
    c.showPage()
    c.save()
    return path


def test_flatten_draws_fields_into_the_page(tmp_path):
    out = archive.flatten(form_pdf(tmp_path / "form.pdf"), tmp_path / "flat.pdf")
    reader = PdfReader(out)
    page = reader.pages[0]
    assert [a.get_object()["/Subtype"] for a in page["/Annots"]] == ["/Link"]
    assert "/AcroForm" not in reader.trailer["/Root"]
    assert "Jane Doe" in page.extract_text()


def test_flatten_comments_only_keeps_the_form(tmp_path):
    out = archive.flatten(form_pdf(tmp_path / "form.pdf"), tmp_path / "flat.pdf", forms=False)
    assert set(PdfReader(out).get_fields()) == {"name", "agree"}


def test_sanitize_removes_hidden_data(tmp_path):
    from pypdf import PdfWriter

    writer = PdfWriter(clone_from=str(form_pdf(tmp_path / "form.pdf")))
    writer.add_metadata({"/Author": "Secret Person"})
    writer.add_js("app.alert('hi');")
    writer.add_attachment("notes.txt", b"hidden notes")
    writer.write(tmp_path / "dirty.pdf")
    counts = archive.sanitize(tmp_path / "dirty.pdf", tmp_path / "clean.pdf")
    assert counts["javascript"] == 1 and counts["attachments"] == 1 and counts["metadata"] >= 1
    reader = PdfReader(tmp_path / "clean.pdf")
    assert "/Author" not in reader.metadata and not reader.attachments
    assert "/Names" not in reader.trailer["/Root"]
    assert set(reader.get_fields()) == {"name", "agree"}  # forms are not hidden data


@needs_ghostscript
def test_pdfa(tmp_path):
    src = text_pdf(tmp_path / "doc.pdf", ["Archive me"])
    out = archive.to_pdfa(src, tmp_path / "doc_pdfa.pdf")
    assert archive.pdfa_level(out) == "2B"
    reader = PdfReader(out)
    assert "/OutputIntents" in reader.trailer["/Root"]
    assert "Archive me" in reader.pages[0].extract_text()


@needs_ghostscript
def test_cli_pdfa_drops_encryption(tmp_path):
    src = text_pdf(tmp_path / "doc.pdf", ["Secret"])
    core.encrypt(src, tmp_path / "locked.pdf", "pw")
    assert main(["pdfa", str(tmp_path / "locked.pdf"), "--password", "pw"]) == 0
    assert not PdfReader(tmp_path / "locked_pdfa.pdf").is_encrypted


def test_cli_flatten_and_sanitize(tmp_path):
    src = form_pdf(tmp_path / "form.pdf")
    assert main(["flatten", str(src)]) == 0
    assert not PdfReader(tmp_path / "form_flat.pdf").get_fields()
    assert main(["sanitize", str(src), "-o", str(tmp_path / "s.pdf")]) == 0
    assert PdfReader(tmp_path / "s.pdf").metadata.get("/Producer") == "PDF Manager"


# -- bookmarks -------------------------------------------------------------------------

from pdfmanager import outline  # noqa: E402


def book_pdf(path):
    """Five pages with 20/15/12.5 pt headings, 11 pt body text and a
    running header on every page."""
    c = canvas.Canvas(str(path), pagesize=A4)
    plan = [[("h1", "1 Introduction"), ("b", ""), ("h2", "1.1 Background")],
            [("b", ""), ("h2", "1.2 Scope")], [("h1", "2 Methods"), ("b", "")],
            [("b", ""), ("h3", "2.0.1 Detail")], [("h1", "3 Results")]]
    for k, parts in enumerate(plan):
        c.setFont("Helvetica", 9)
        c.drawString(72, 810, "ACME Report 2026")
        c.drawString(290, 30, str(k + 1))
        y = 760
        for kind, text in parts:
            if kind == "b":
                c.setFont("Helvetica", 11)
                for _ in range(10):
                    c.drawString(72, y, "Body text body text body text body text body text.")
                    y -= 15
            else:
                c.setFont("Helvetica-Bold", {"h1": 20, "h2": 15, "h3": 12.5}[kind])
                c.drawString(72, y, text)
                y -= 30
        c.showPage()
    c.save()
    return path


def test_detect_headings(tmp_path):
    items = outline.detect_headings(book_pdf(tmp_path / "book.pdf"))
    assert outline.to_text(items) == (
        "- 1 Introduction (p. 1)\n  - 1.1 Background (p. 1)\n  - 1.2 Scope (p. 2)\n"
        "- 2 Methods (p. 3)\n  - 2.0.1 Detail (p. 4)\n- 3 Results (p. 5)\n")


def test_bookmarks_round_trip(tmp_path):
    src = book_pdf(tmp_path / "book.pdf")
    items = [{"title": "Start", "page": 1, "level": 0}, {"title": "Inner", "page": 3, "level": 1},
             {"title": "Deeper", "page": 4, "level": 2}, {"title": "End", "page": 5, "level": 0}]
    out = outline.set_outline(src, tmp_path / "out.pdf", items)
    assert outline.get_outline(out) == items
    assert PdfReader(out).trailer["/Root"]["/PageMode"] == "/UseOutlines"
    assert outline.from_text(outline.to_text(items)) == items
    outline.set_outline(out, tmp_path / "none.pdf", [])
    assert outline.get_outline(tmp_path / "none.pdf") == []


def test_bookmark_checks(tmp_path):
    src = book_pdf(tmp_path / "book.pdf")
    with pytest.raises(core.PdfManagerError, match="page 9 doesn't exist"):
        outline.set_outline(src, tmp_path / "o.pdf", [{"title": "X", "page": 9, "level": 0}])
    with pytest.raises(core.PdfManagerError, match="no title"):
        outline.set_outline(src, tmp_path / "o.pdf", [{"title": " ", "page": 1, "level": 0}])
    # A level can't be skipped: a first item at level 2 becomes level 0.
    outline.set_outline(src, tmp_path / "o.pdf", [{"title": "A", "page": 1, "level": 2}])
    assert outline.get_outline(tmp_path / "o.pdf")[0]["level"] == 0


def test_cli_bookmarks(tmp_path, capsys):
    src = book_pdf(tmp_path / "book.pdf")
    assert main(["bookmarks", str(src)]) == 0
    assert "no bookmarks" in capsys.readouterr().out
    (tmp_path / "list.txt").write_text("- One (p. 1)\n  - Two (p. 2)\n- Three (page 5)\n")
    assert main(["bookmarks", str(src), "--set", str(tmp_path / "list.txt")]) == 0
    capsys.readouterr()
    assert main(["bookmarks", str(tmp_path / "book_bookmarks.pdf")]) == 0
    assert capsys.readouterr().out == "- One (p. 1)\n  - Two (p. 2)\n- Three (p. 5)\n"
    assert main(["bookmarks", str(src), "--detect", "-n"]) == 0
    assert "- 3 Results (p. 5)" in capsys.readouterr().out


def test_gui_bookmarks(tmp_path):
    src = book_pdf(tmp_path / "book.pdf")
    work = tmp_path / "work"
    work.mkdir()
    items = [{"title": "Only", "page": 2, "level": 0}]
    name, data, _ = gui.run_operation("bookmarks", [src], {"mode": "edit",
                                                           "outline": json.dumps(items)}, work=work)
    (tmp_path / name).write_bytes(data)
    assert outline.get_outline(tmp_path / name) == items
