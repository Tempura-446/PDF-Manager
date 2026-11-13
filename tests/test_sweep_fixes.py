"""Fixes from the 0.27.0 maturity sweep (tests/sweep): each test makes a
small file with the trait that broke a tool, and checks the tool now works
or stops with a plain message."""
import sys
from pathlib import Path

import pikepdf
import pytest
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).parent))
from test_certsign import PFX_PASSWORD, make_id  # noqa: E402

from pdfmanager import gui  # noqa: E402


def text_pdf(path, pages=2, size=(612, 792), words="The quick brown fox"):
    c = canvas.Canvas(str(path), pagesize=size)
    for n in range(pages):
        c.drawString(72, size[1] - 72, f"{words}, page {n + 1}")
        c.showPage()
    c.save()
    return path


def edit(path, change):
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        change(pdf)
        pdf.save(path)
    return path


def run(op, files, opts, tmp_path, extra=None):
    work = tmp_path / f"work-{op}"
    n = 1
    while work.exists():
        n += 1
        work = tmp_path / f"work-{op}-{n}"
    work.mkdir()
    notes = []
    name, data, ctype = gui.run_operation(op, [Path(f) for f in files], opts, None, extra or {}, work, notes)
    return name, data, notes


def test_a_rotation_that_isnt_a_quarter_turn_counts_as_none(tmp_path):
    src = edit(text_pdf(tmp_path / "odd.pdf"), lambda pdf: [setattr(p.obj, "Rotate", 45) for p in pdf.pages])
    pfx = make_id(tmp_path / "id.pfx")[0]
    name, data, notes = run("sign-cert", [src], {"pfx_password": PFX_PASSWORD, "appearance": "visible",
                                                 "page": "last", "position": "bottom-right", "size": "medium"},
                            tmp_path, {"pfx": pfx, "pfx[]": [pfx]})
    assert data.startswith(b"%PDF") and any("signed" in n for n in notes)


def test_a_table_cell_with_only_a_sign_or_a_currency_is_text():
    from pdfmanager.tables import parse_number

    for text in ("-", "–", "+", "$", "€", "($)", "(-)", "$ -"):
        assert parse_number(text) is None, text
    assert parse_number("$-12")[0] == -12 and parse_number("12 €")[0] == 12


def test_a_table_with_dashes_for_empty_cells_goes_to_excel(tmp_path):
    src = tmp_path / "dashes.pdf"
    c = canvas.Canvas(str(src))
    for r in range(6):
        for col in range(4):
            c.rect(50 + col * 100, 700 - r * 20, 100, 20)
            c.drawString(54 + col * 100, 706 - r * 20, ["Item", "-", "$", "12.50"][col] if r else f"Head {col}")
    c.showPage()
    c.save()
    name, data, notes = run("to-excel", [src], {"layout": "sheet-per-table", "format": "xlsx"}, tmp_path)
    assert name.endswith(".xlsx") and data[:2] == b"PK"


def test_inline_images_pass_the_pdfa_check(tmp_path):
    from pdfmanager import pdfacheck

    src = tmp_path / "inline.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.pages[0].obj.Contents = pdf.make_stream(
        b"q 100 0 0 100 50 600 cm BI /W 2 /H 2 /CS /G /BPC 8 /F /AHx ID 00ff00ff> EI Q")
    pdf.save(src)
    result = pdfacheck.check(src, level="2b", engine="builtin")
    assert isinstance(result["issues"], list)
    name, data, notes = run("pdfa", [src], {"level": "2"}, tmp_path)
    assert data.startswith(b"%PDF")


@pytest.mark.parametrize("op", ["to-images", "to-pptx", "to-word", "to-html", "form", "compare", "autocrop"])
def test_a_huge_page_is_drawn_with_fewer_pixels_not_out_of_memory(tmp_path, monkeypatch, op):
    import io
    import zipfile

    from PIL import Image

    from pdfmanager import core

    drawn = []
    real = core.render_scale

    def watched(page, scale, limit=None):
        got = real(page, scale, limit)
        w, h = page.get_size()
        drawn.append(w * h * got * got)
        return got

    monkeypatch.setattr(core, "MAX_RENDER_PIXELS", 2_000_000)
    monkeypatch.setattr(core, "render_scale", watched)
    src = text_pdf(tmp_path / "poster.pdf", pages=1, size=(14400, 14400))
    files = [src, text_pdf(tmp_path / "other.pdf", pages=1, size=(14400, 14400))] if op == "compare" else [src]
    opts = {"to-word": {"engine": "basic"}, "form": {"detect": "true"}}.get(op, {})
    try:
        name, data, notes = run(op, files, opts, tmp_path)
    except gui.EXPECTED_ERRORS as exc:  # (Add form fields finds nothing to fill in on a poster)
        assert op == "form", exc
        return
    assert drawn and max(drawn) <= 2_000_001
    if op == "to-images":
        image = Image.open(io.BytesIO(data)) if name.endswith(".png") else None
        if image is None:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                image = Image.open(io.BytesIO(z.read(z.namelist()[0])))
        assert image.width * image.height <= 2_020_000
        assert round(image.info["dpi"][0]) < 150  # the picture still says how big the page is


def _raw_pdf(page_dict):
    """A one-page PDF written by hand, with ``page_dict`` in its page."""
    content = b"BT /F1 24 Tf 72 700 Td (Hello odd boxes) Tj ET"
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               f"<< /Type /Page /Parent 2 0 R {page_dict} /Contents 4 0 R /Resources << /Font << /F1 << "
               "/Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>",
               f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream"]
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + (body.encode() if isinstance(body, str) else body) + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


@pytest.mark.parametrize("box", ["/MediaBox [(0) 0 612 792]", "/MediaBox [0 0 612]"])
@pytest.mark.parametrize("op", ["to-word", "to-html", "tag", "to-excel"])
def test_a_page_box_pdfplumber_cant_read_converts_or_says_so(tmp_path, op, box):
    src = tmp_path / "box.pdf"
    src.write_bytes(_raw_pdf(box))
    opts = {"to-word": {"engine": "basic"}}.get(op, {})
    try:
        name, data, notes = run(op, [src], opts, tmp_path)
    except gui.EXPECTED_ERRORS as exc:
        assert op == "to-excel" and "box.pdf" in str(exc)
        return
    assert data
    if op == "to-word":
        import io

        import docx

        text = " ".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)
        assert "Hello odd boxes" in text


def test_a_page_without_its_type_is_signed(tmp_path):
    src = tmp_path / "notype.pdf"
    src.write_bytes(_raw_pdf("/MediaBox [0 0 612 792]").replace(b"<< /Type /Page /Parent", b"<< /Parent"))
    pfx = make_id(tmp_path / "id.pfx")[0]
    name, data, notes = run("sign-cert", [src], {"pfx_password": PFX_PASSWORD, "appearance": "visible",
                                                 "page": "last", "position": "bottom-right", "size": "medium"},
                            tmp_path, {"pfx": pfx, "pfx[]": [pfx]})
    assert any("signed" in n for n in notes)
    from pdfmanager import certsign

    out = tmp_path / "signed.pdf"
    out.write_bytes(data)
    assert certsign.verify(out)[0]["intact"]


@pytest.mark.parametrize("op", ["to-pptx", "to-images", "to-word"])
def test_a_crop_box_outside_the_page_shows_the_whole_page(tmp_path, op):
    src = tmp_path / "outside.pdf"
    src.write_bytes(_raw_pdf("/MediaBox [0 0 612 792] /CropBox [1000 1000 1200 1200]"))
    name, data, notes = run(op, [src], {"engine": "basic"} if op == "to-word" else {}, tmp_path)
    if op == "to-images":
        import io

        from PIL import Image

        image = Image.open(io.BytesIO(data))
        assert abs(image.width - 1275) <= 1 and abs(image.height - 1650) <= 1
        assert image.convert("L").getextrema()[0] < 128  # the text is on it


@pytest.mark.parametrize("op", ["compress", "color"])
def test_a_stream_in_an_unknown_compression_doesnt_stop_compress(tmp_path, op):
    src = text_pdf(tmp_path / "odd.pdf", pages=1)

    def add(pdf):
        image = pikepdf.Stream(pdf, b"\x00" * 300, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                               Width=10, Height=10, ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8,
                               Filter=pikepdf.Name("/MadeUpDecode"))
        pdf.pages[0].obj.Resources.XObject = pikepdf.Dictionary(Im9=image)
    edit(src, add)
    name, data, notes = run(op, [src], {}, tmp_path)
    out = tmp_path / "out.pdf"
    out.write_bytes(data)
    from pypdf import PdfReader

    assert "quick brown fox" in PdfReader(out).pages[0].extract_text()


@pytest.mark.parametrize("op", ["to-word", "to-pptx", "to-html"])
def test_control_characters_in_the_title_or_a_link_dont_spoil_the_document(tmp_path, op):
    import io
    import zipfile
    from xml.dom import minidom

    c = canvas.Canvas(str(tmp_path / "ctl.pdf"))
    c.setTitle("Report\x00 one\x07")
    c.drawString(72, 720, "Body text with a link")
    c.linkURL("https://example.com/a\x01b", (70, 715, 300, 735))
    c.showPage()
    c.save()
    name, data, notes = run(op, [tmp_path / "ctl.pdf"], {"engine": "basic"} if op == "to-word" else {}, tmp_path)
    if op == "to-html":
        assert "\x00" not in data.decode("utf-8")
        return
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for member in z.namelist():
            if member.endswith((".xml", ".rels")):
                minidom.parseString(z.read(member))  # well-formed XML, as Word and PowerPoint need


@pytest.mark.parametrize("count", ["5", "-1", "0"])
@pytest.mark.parametrize("op", ["rotate", "metadata", "watermark"])
def test_a_wrong_page_count_in_the_page_tree_is_put_right(tmp_path, op, count):
    import pypdfium2 as pdfium

    src = tmp_path / "count.pdf"
    src.write_bytes(_raw_pdf("/MediaBox [0 0 612 792]").replace(b"/Count 1", f"/Count {count}".encode()))
    opts = {"rotate": {"degrees": "90"}, "metadata": {"title": "T"}, "watermark": {"text": "DRAFT"}}[op]
    name, data, notes = run(op, [src], opts, tmp_path)
    out = tmp_path / "out.pdf"
    out.write_bytes(data)
    doc = pdfium.PdfDocument(str(out))
    assert len(doc) == 1 and doc[0].get_size()[0] > 0
    with pikepdf.open(out) as pdf:
        assert int(pdf.Root.Pages.Count) == 1


@pytest.mark.parametrize("op", ["sanitize", "metadata"])
def test_document_details_that_are_the_page_tree_are_left_alone(tmp_path, op):
    from pypdf import PdfReader

    src = tmp_path / "info.pdf"
    src.write_bytes(_raw_pdf("/MediaBox [0 0 612 792]").replace(b"/Root 1 0 R", b"/Root 1 0 R /Info 2 0 R"))
    name, data, notes = run(op, [src], {"title": "New title"} if op == "metadata" else {}, tmp_path)
    out = tmp_path / "out.pdf"
    out.write_bytes(data)
    reader = PdfReader(out)
    assert len(reader.pages) == 1 and "Hello odd boxes" in reader.pages[0].extract_text()
    if op == "metadata":
        assert reader.metadata["/Title"] == "New title"
        with pikepdf.open(out) as pdf:
            assert "/Title" not in pdf.Root.Pages


def test_ocr_with_tesseract_keeps_form_fields_and_a_portfolios_pages(tmp_path, monkeypatch):
    import types

    from pdfmanager import ocr

    asked = {}
    fake = types.ModuleType("ocrmypdf")
    exceptions = types.ModuleType("ocrmypdf.exceptions")
    exceptions.ExitCodeException = type("ExitCodeException", (Exception,), {})
    exceptions.MissingDependencyError = type("Missing", (Exception,), {})

    def fake_ocr(src, out, **kwargs):
        asked.update(kwargs)
        Path(out).write_bytes(Path(src).read_bytes())

    fake.ocr = fake_ocr
    monkeypatch.setitem(sys.modules, "ocrmypdf", fake)
    monkeypatch.setitem(sys.modules, "ocrmypdf.exceptions", exceptions)
    monkeypatch.setattr(ocr.scan, "add_programs_to_path", lambda: None)
    monkeypatch.setattr(ocr, "_tesseract_languages", lambda: {"eng"})
    ocr._run_tesseract(text_pdf(tmp_path / "a.pdf"), tmp_path / "b.pdf", "eng", False, False)
    # OCRmyPDF's PDF/A goes through Ghostscript, which loses form fields and
    # prints a portfolio's attachments instead of its pages.
    assert asked["output_type"] == "pdf"


def test_ocr_of_a_portfolio_keeps_its_own_pages(tmp_path):
    from pypdf import PdfReader

    from pdfmanager import ocr

    if not ocr.tesseract_available():
        pytest.skip("needs Tesseract")
    src = text_pdf(tmp_path / "cover.pdf", pages=2)
    blank = tmp_path / "blank.pdf"
    c = canvas.Canvas(str(blank))
    for _ in range(3):
        c.showPage()
    c.save()

    def portfolio(pdf):
        pdf.attachments["blank.pdf"] = pikepdf.AttachedFileSpec(pdf, blank.read_bytes())
        pdf.Root.Collection = pikepdf.Dictionary(Type=pikepdf.Name.Collection, View=pikepdf.Name.T)
    edit(src, portfolio)
    name, data, notes = run("ocr", [src], {"engine": "tesseract", "language": "eng"}, tmp_path)
    out = tmp_path / "out.pdf"
    out.write_bytes(data)
    reader = PdfReader(out)
    assert len(reader.pages) == 2 and "quick brown fox" in reader.pages[0].extract_text()


def _picture_pdf(path):
    from PIL import Image

    Image.new("L", (200, 300), 230).save(path, "PDF", resolution=50)
    return path


def test_copying_text_from_a_scan_says_there_is_none(tmp_path):
    scan = _picture_pdf(tmp_path / "scan.pdf")
    with pytest.raises(gui.EXPECTED_ERRORS) as caught:
        run("text", [scan], {}, tmp_path)
    assert "scan.pdf has no text" in str(caught.value) and "OCR" in str(caught.value)
    # With another file that has text: that one's text, and a note about the scan.
    name, data, notes = run("text", [text_pdf(tmp_path / "words.pdf"), scan], {}, tmp_path)
    assert b"quick brown fox" in data and any("scan.pdf has no text" in n for n in notes)
