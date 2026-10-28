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


# -- compare -------------------------------------------------------------------------------

from pdfmanager import compare  # noqa: E402


def versions(tmp_path):
    def doc(path, pages):
        c = canvas.Canvas(str(path), pagesize=A4)
        for lines in pages:
            y = 760
            for line in lines:
                if line == "BOX":
                    c.rect(300, 300, 120, 80, fill=1)
                    continue
                c.setFont("Helvetica", 13)
                c.drawString(72, y, line)
                y -= 20
            c.showPage()
        c.save()
        return path

    old = doc(tmp_path / "v1.pdf", [["Chapter one", "The price is 100 dollars."],
                                     ["Chapter two", "Nothing changes.", "BOX"],
                                     ["Chapter three", "Final words."]])
    new = doc(tmp_path / "v2.pdf", [["Chapter one", "The price is 120 dollars."],
                                     ["Inserted page", "brand new"],
                                     ["Chapter two", "Nothing changes."],
                                     ["Chapter three", "Final words."]])
    return old, new


def test_compare_finds_changes_and_inserted_pages(tmp_path):
    old, new = versions(tmp_path)
    summary = compare.compare(old, new, tmp_path / "diff.pdf")
    got = [(c["old"], c["new"], c["kind"], c["removed"], c["added"]) for c in summary["changes"]]
    assert got == [(1, 1, "changed", ["100"], ["120"]), (None, 2, "added", [], []),
                   (2, 3, "changed", [], [])]
    assert summary["changes"][2]["areas"]  # the removed box is outlined
    report = PdfReader(tmp_path / "diff.pdf")
    assert len(report.pages) == 4  # summary + three pages
    assert "3 pages with differences" in report.pages[0].extract_text()


def test_compare_identical(tmp_path):
    old, _ = versions(tmp_path)
    summary = compare.compare(old, old, tmp_path / "diff.pdf")
    assert summary["changes"] == []
    assert "No differences found" in PdfReader(tmp_path / "diff.pdf").pages[0].extract_text()


def test_cli_and_gui_compare(tmp_path, capsys):
    old, new = versions(tmp_path)
    assert main(["compare", str(old), str(new)]) == 0
    out = capsys.readouterr().out
    assert "page 1: - 100; + 120" in out and "3 page(s) differ" in out
    assert (tmp_path / "v1_vs_v2.pdf").exists()
    work = tmp_path / "work"
    work.mkdir()
    notes = []
    name, _, _ = gui.run_operation("compare", [old, new], {}, work=work, notes=notes)
    assert name == "v1_vs_v2.pdf" and notes == ["3 pages with differences"]


# -- Word and Office ------------------------------------------------------------------

import re  # noqa: E402
import zipfile  # noqa: E402

from pdfmanager import office  # noqa: E402

needs_libreoffice = pytest.mark.skipif(not office.find_libreoffice(), reason="needs LibreOffice")


def docx_paragraphs(path):
    xml = zipfile.ZipFile(path).read("word/document.xml").decode()
    rows = []
    for p in re.findall(r"<w:p>(.*?)</w:p>", xml):
        style = re.search(r'pStyle w:val="Heading(\d)"', p)
        text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p))
        rows.append((int(style.group(1)) if style else 0,
                     text or ("[picture]" if "<w:drawing>" in p else "[page break]")))
    return rows


def article_pdf(path, tmp_path):
    Image.new("RGB", (400, 300), (200, 60, 40)).save(tmp_path / "pic.png")
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(72, 770, "A Title")
    c.setFont("Helvetica", 11)
    y = 740
    for para in range(2):
        for k in range(3):
            c.drawString(72, y, f"Paragraph {para + 1} line {k + 1} with words.")
            y -= 14
        y -= 14
    c.drawImage(str(tmp_path / "pic.png"), 72, y - 150, 200, 150)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, y - 180, "Section two")
    c.showPage()
    c.save()
    return path


def test_pdf_to_word_builtin(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "has_word", lambda: False)
    out, engine = office.pdf_to_word(article_pdf(tmp_path / "a.pdf", tmp_path), tmp_path / "a.docx")
    assert engine == "basic"
    assert docx_paragraphs(out) == [
        (1, "A Title"),
        (0, "Paragraph 1 line 1 with words. Paragraph 1 line 2 with words. "
            "Paragraph 1 line 3 with words."),
        (0, "Paragraph 2 line 1 with words. Paragraph 2 line 2 with words. "
            "Paragraph 2 line 3 with words."),
        (0, "[picture]"), (2, "Section two")]
    assert any(n.startswith("word/media/") for n in zipfile.ZipFile(out).namelist())


def test_pdf_to_word_drops_running_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "has_word", lambda: False)
    out, _ = office.pdf_to_word(book_pdf(tmp_path / "book.pdf"), tmp_path / "book.docx")
    texts = [t for _, t in docx_paragraphs(out)]
    assert "ACME Report 2026" not in texts and "3" not in texts
    assert [(lvl, t) for lvl, t in docx_paragraphs(out) if lvl][:2] == [(1, "1 Introduction"),
                                                                       (2, "1.1 Background")]


def test_word_is_used_when_installed(tmp_path, monkeypatch):
    """With Word installed, the conversion runs a PowerShell script that
    automates it; the file paths travel in environment variables."""
    seen = {}

    def fake_run(command, env=None, what=""):
        seen.update(command=command, env=env)
        Path(env["PDFM_OUT"]).write_bytes(b"docx")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(office, "has_word", lambda: True)
    monkeypatch.setattr(office, "_run", fake_run)
    out, engine = office.pdf_to_word(text_pdf(tmp_path / "a.pdf"), tmp_path / "a.docx")
    assert engine == "word" and out.read_bytes() == b"docx"
    assert seen["command"][0] == "powershell" and "Word.Application" in seen["command"][-1]
    assert seen["env"]["PDFM_IN"].endswith("a.pdf")


def test_text_file_to_pdf_without_office(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "find_libreoffice", lambda: None)
    (tmp_path / "notes.txt").write_text("First line\nSecond line\n", encoding="utf-8")
    out, program = office.office_to_pdf(tmp_path / "notes.txt", tmp_path / "notes.pdf")
    assert program == "PDF Manager"
    assert "Second line" in PdfReader(out).pages[0].extract_text()
    (tmp_path / "x.docx").write_bytes(b"")
    with pytest.raises(core.PdfManagerError, match="needs Microsoft Office or LibreOffice"):
        office.office_to_pdf(tmp_path / "x.docx", tmp_path / "x.pdf")
    with pytest.raises(core.PdfManagerError, match="can't convert .zip"):
        office.office_to_pdf(tmp_path / "x.zip", tmp_path / "x.pdf")


@needs_libreoffice
def test_word_document_to_pdf_with_libreoffice(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "has_word", lambda: False)
    docx, _ = office.pdf_to_word(article_pdf(tmp_path / "a.pdf", tmp_path), tmp_path / "a.docx")
    out, program = office.office_to_pdf(docx, tmp_path / "back.pdf")
    assert program == "LibreOffice"
    assert "Section two" in PdfReader(out).pages[0].extract_text()


def test_cli_to_word(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(office, "has_word", lambda: False)
    src = text_pdf(tmp_path / "doc.pdf", ["Hello Word"])
    assert main(["to-word", str(src)]) == 0
    assert "built-in converter" in capsys.readouterr().out
    assert (0, "Hello Word") in docx_paragraphs(tmp_path / "doc.docx")


def test_gui_to_word_and_from_office(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "has_word", lambda: False)
    monkeypatch.setattr(office, "find_libreoffice", lambda: None)
    work = tmp_path / "w1"
    work.mkdir()
    notes = []
    name, data, ctype = gui.run_operation("to-word", [text_pdf(tmp_path / "doc.pdf")], {},
                                          work=work, notes=notes)
    assert name == "doc.docx" and ctype.endswith("wordprocessingml.document")
    assert data.startswith(b"PK") and "install Microsoft Word" in notes[0]
    (tmp_path / "t.txt").write_text("plain text")
    work = tmp_path / "w2"
    work.mkdir()
    name, data, _ = gui.run_operation("from-office", [tmp_path / "t.txt"], {}, work=work)
    assert name == "t.pdf" and data.startswith(b"%PDF")


# -- redaction ---------------------------------------------------------------------------

from pdfmanager import redact  # noqa: E402


def secret_pdf(path, rotate=0):
    from pypdf import PdfWriter

    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica", 14)
    c.drawString(72, 700, "Call John Smith at 555-1234 or mail john@example.com.")
    c.drawString(72, 680, "Card 4111 1111 1111 1111 expires soon.")
    c.acroForm.textfield(name="ssn", value="secret", x=72, y=600, width=150, height=20)
    c.showPage()
    c.drawString(72, 700, "Page two mentions nobody.")
    c.showPage()
    c.save()
    if rotate:
        writer = PdfWriter(clone_from=str(path))
        writer.pages[0].rotate(rotate)
        writer.write(path)
    return path


def dark_fraction(path, page, box):
    """Share of black pixels in ``box`` (points, PDF coordinates) of a page."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    p = doc[page]
    p.set_rotation(0)
    image = p.render(scale=1).to_pil().convert("L")
    height = p.get_height()
    x0, y0, x1, y1 = box
    crop = image.crop((int(x0), int(height - y1), int(x1), int(height - y0)))
    doc.close()
    return sum(crop.histogram()[:40]) / (crop.width * crop.height)


@pytest.mark.parametrize("rotate", [0, 90])
def test_redact_removes_the_text(tmp_path, rotate):
    src = secret_pdf(tmp_path / "s.pdf", rotate)
    found = redact.redact(src, tmp_path / "out.pdf", terms=["john smith"],
                          patterns=[redact.PRESETS["emails"]])
    assert found == {1: 2}
    reader = PdfReader(tmp_path / "out.pdf")
    assert reader.pages[0].extract_text() == ""  # the page is a picture now
    assert reader.pages[0].rotation == rotate
    assert "nobody" in reader.pages[1].extract_text()  # other pages untouched
    assert not reader.get_fields()  # the form field on the redacted page is gone
    assert dark_fraction(tmp_path / "out.pdf", 0, (102, 702, 170, 710)) > 0.9  # "John Smith"
    assert dark_fraction(tmp_path / "out.pdf", 0, (72, 682, 100, 690)) < 0.5  # "Card"


def test_redact_presets_and_boxes(tmp_path):
    src = secret_pdf(tmp_path / "s.pdf")
    found = redact.redact(src, tmp_path / "out.pdf", patterns=[redact.PRESETS["numbers"],
                                                               redact.PRESETS["phones"]],
                          boxes=["2:70,690,200,720"])
    assert found == {1: 2, 2: 1}
    assert dark_fraction(tmp_path / "out.pdf", 1, (75, 695, 195, 715)) > 0.95
    assert redact.redact(src, tmp_path / "none.pdf", terms=["nothing here"]) == {}


def test_redact_whole_words_and_case(tmp_path):
    src = secret_pdf(tmp_path / "s.pdf")
    assert len(redact.find_text(src, ["smit"])[0]) == 1
    assert redact.find_text(src, ["smit"], whole_words=True) == {}
    assert redact.find_text(src, ["JOHN"], case_sensitive=True) == {}


def test_cli_and_gui_redact(tmp_path, capsys):
    src = secret_pdf(tmp_path / "s.pdf")
    assert main(["redact", str(src), "-t", "John Smith", "--find", "emails"]) == 0
    assert "2 area(s) on page(s) 1" in capsys.readouterr().out
    assert main(["redact", str(src)]) == 1
    work = tmp_path / "work"
    work.mkdir()
    notes = []
    name, data, _ = gui.run_operation(
        "redact", [src], {"terms": "Smith, Card", "boxes": json.dumps({"2": [[70, 690, 200, 720]]})},
        work=work, notes=notes)
    assert name == "s_redacted.pdf"
    assert notes == ["s.pdf: blacked out 3 areas on pages 1, 2"]


# -- recipes -------------------------------------------------------------------------------

from pdfmanager import recipes, watch  # noqa: E402


@pytest.fixture
def recipe_home(tmp_path, monkeypatch):
    monkeypatch.setattr(recipes, "data_dir", lambda: tmp_path / "appdata")
    return tmp_path


def test_recipe_save_list_delete(recipe_home):
    recipes.save("  Scan   inbox ", [{"op": "clean"}, {"op": "compress", "options": {"level": "high"}}])
    assert list(recipes.load_all()) == ["Scan inbox"]
    assert recipes.get("Scan inbox")["steps"][1] == {"op": "compress", "options": {"level": "high"}}
    assert recipes.describe(recipes.get("Scan inbox")["steps"]) == "Clean up scans → Compress"
    with pytest.raises(core.PdfManagerError, match="can't be used in a recipe"):
        recipes.save("bad", [{"op": "merge"}])
    with pytest.raises(core.PdfManagerError, match="at least one step"):
        recipes.save("empty", [])
    recipes.delete("Scan inbox")
    assert recipes.load_all() == {}


def test_recipe_runs_steps_in_order(recipe_home):
    tmp_path = recipe_home
    src = scan_pdf(tmp_path / "scan.pdf", [scan_page(), scan_page(blank=True), scan_page()])
    core.encrypt(src, tmp_path / "locked.pdf", "pw")
    notes = []
    steps = [{"op": "clean"}, {"op": "rotate", "options": {"degrees": "90", "pages": "1"}},
             {"op": "number", "options": {"format": "Page {n}"}}]
    out = recipes.run(steps, tmp_path / "locked.pdf", tmp_path / "out.pdf", "pw", notes)
    reader = PdfReader(out)
    assert len(reader.pages) == 2 and reader.pages[0].rotation == 90
    assert "Page 2" in reader.pages[1].extract_text()
    assert notes == ["locked.pdf: removed blank page 2"]
    with pytest.raises(core.PdfManagerError, match="step 1 .Extract pages.: fill in"):
        recipes.run([{"op": "extract"}], src, tmp_path / "x.pdf")


def test_cli_recipe(recipe_home, capsys):
    tmp_path = recipe_home
    assert main(["recipe", "save", "share", "sanitize", "number", "format=p{n}",
                 "compress", "level=high"]) == 0
    assert "Remove hidden data → Page numbers → Compress" in capsys.readouterr().out
    assert main(["recipe", "show", "share"]) == 0
    assert capsys.readouterr().out.splitlines()[1] == "2. number format=p{n}"
    src = text_pdf(tmp_path / "doc.pdf", ["One", "Two"])
    assert main(["recipe", "run", "share", str(src)]) == 0
    assert "p2" in PdfReader(tmp_path / "doc_share.pdf").pages[1].extract_text()
    assert main(["recipe", "run", "missing", str(src)]) == 1
    assert main(["recipe", "delete", "share"]) == 0


def test_watch_runs_a_recipe(recipe_home):
    tmp_path = recipe_home
    inbox, dest = tmp_path / "inbox", tmp_path / "dest"
    inbox.mkdir()
    text_pdf(inbox / "a.pdf", ["One"])
    settings = watch.WatchSettings(inbox=inbox, dest=dest,
                                   recipe=[{"op": "number", "options": {"format": "#{n}"}}])
    result = watch.process_file(inbox / "a.pdf", settings)
    assert "#1" in PdfReader(result).pages[0].extract_text()


def test_gui_recipe(recipe_home):
    tmp_path = recipe_home
    work = tmp_path / "work"
    work.mkdir()
    steps = [{"op": "reverse"}, {"op": "extract", "options": {"pages": "1"}}]
    name, data, _ = gui.run_operation("recipe", [text_pdf(tmp_path / "d.pdf", ["A", "B"])],
                                      {"steps": json.dumps(steps), "recipe_name": "Last page!"},
                                      work=work)
    assert name == "d_last-page.pdf"
    (tmp_path / name).write_bytes(data)
    assert PdfReader(tmp_path / name).pages[0].extract_text().strip() == "B"
