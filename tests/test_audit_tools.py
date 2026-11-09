"""Fixes from the code audit, tools: redaction, flattening, hidden data,
PDF/A, comparing, scan clean-up, text files and recipes."""

import json
import tempfile

import pytest
from PIL import Image, ImageChops
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Rectangle, Text
from pypdf.generic import (ArrayObject, BooleanObject, DictionaryObject, IndirectObject, NameObject,
                           NullObject, NumberObject, RectangleObject, StreamObject, TextStringObject)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdfmanager import archive, compare, core, forms, office, recipes, redact, scan

needs_ghostscript = pytest.mark.skipif(not archive.find_ghostscript(), reason="needs Ghostscript")
needs_tesseract = pytest.mark.skipif(not scan.find_tesseract(), reason="needs Tesseract")

PHOTO = Image.new("RGB", (120, 80), (200, 30, 30))


def render(path, page=0, scale=1):
    """The page as readers show it (form fields included), in grey."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    doc.init_forms()
    p = doc[page]
    p.set_rotation(0)
    image = p.render(scale=scale, may_draw_forms=True).to_pil().convert("L")
    height = p.get_height()
    doc.close()
    return image, height


def dark_pixels(path, box, page=0):
    """Dark pixels in ``box`` (points, PDF coordinates) of a page."""
    image, height = render(path, page)
    x0, y0, x1, y1 = box
    crop = image.crop((int(x0), int(height - y1), int(x1), int(height - y0)))
    return sum(crop.histogram()[:100])


def everything_in(path):
    """The raw file plus every object it holds, streams decoded: what a
    hex editor, qpdf or `strings` could dig out."""
    parts = [path.read_bytes()]
    reader = PdfReader(str(path))
    for number in range(1, int(reader.trailer["/Size"])):
        try:
            obj = reader.get_object(number)
        except Exception:
            continue
        if isinstance(obj, StreamObject):
            parts.append(obj.get_data())
        parts.append(repr(obj).encode("utf-8", "replace"))
    return b"".join(parts)


def filled_form(path, name="Maria Garcia"):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica", 14)
    c.drawString(72, 760, "applicant John Smith")
    c.acroForm.textfield(name="name", value=name, x=72, y=700, width=200, height=20)
    c.acroForm.checkbox(name="agree", x=72, y=650, checked=True)
    c.showPage()
    c.save()
    return path


def with_annots(path, annots):
    """``path`` with its first page's /Annots replaced (raw objects)."""
    writer = PdfWriter(clone_from=str(path))
    writer.pages[0][NameObject("/Annots")] = ArrayObject(annots)
    writer.write(path)
    return path


# -- redaction ---------------------------------------------------------------------------

def test_redacted_text_pictures_and_fields_leave_no_trace(tmp_path):
    src = tmp_path / "secret.pdf"
    c = canvas.Canvas(str(src), pagesize=A4)
    c.setFont("Helvetica", 14)
    c.drawString(72, 700, "This mentions TOPSECRETWORD here.")
    c.drawImage(ImageReader(PHOTO), 72, 500, 120, 80)
    c.acroForm.textfield(name="ssn", value="SSNVALUE123", x=72, y=600, width=150, height=20)
    c.showPage()
    c.save()
    out = tmp_path / "out.pdf"
    assert redact.redact(src, out, terms=["TOPSECRETWORD"], boxes=["1:72,500,192,580"],
                         method="image") == {1: 2}
    left = everything_in(out)
    assert b"TOPSECRETWORD" not in left
    assert b"SSNVALUE123" not in left
    assert bytes(PHOTO.getpixel((0, 0))) * 20 not in left  # the photo's pixels
    assert not PdfReader(str(out)).get_fields()


def test_redaction_prunes_the_structure_tree(tmp_path):
    """A tagged PDF repeats the words in /ActualText; those go too, while
    the other pages keep their tags."""
    src = tmp_path / "tagged.pdf"
    c = canvas.Canvas(str(src), pagesize=A4)
    c.setFont("Helvetica", 14)
    c.drawString(72, 700, "TOPSECRETWORD on a tagged page")
    c.showPage()
    c.drawString(72, 700, "second page plain")
    c.showPage()
    c.save()
    w = PdfWriter(clone_from=str(src))

    def element(page, text):
        return w._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/StructElem"), NameObject("/S"): NameObject("/P"),
            NameObject("/Pg"): page.indirect_reference, NameObject("/ActualText"): TextStringObject(text),
            NameObject("/K"): ArrayObject([NumberObject(0)])}))

    first, second = (element(p, p.extract_text().strip()) for p in w.pages)
    document = w._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/StructElem"), NameObject("/S"): NameObject("/Document"),
        NameObject("/K"): ArrayObject([first, second])}))
    w._root_object[NameObject("/StructTreeRoot")] = w._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/StructTreeRoot"), NameObject("/K"): ArrayObject([document]),
        NameObject("/ParentTree"): DictionaryObject({NameObject("/Nums"): ArrayObject(
            [NumberObject(0), ArrayObject([first]), NumberObject(1), ArrayObject([second])])})}))
    w._root_object[NameObject("/MarkInfo")] = DictionaryObject({NameObject("/Marked"): BooleanObject(True)})
    for n, page in enumerate(w.pages):
        page[NameObject("/StructParents")] = NumberObject(n)
    w.write(src)
    out = tmp_path / "out.pdf"
    redact.redact(src, out, terms=["TOPSECRETWORD"], method="image")
    assert b"TOPSECRETWORD" not in everything_in(out)
    reader = PdfReader(str(out))
    assert "/StructParents" not in reader.pages[0]
    tree = reader.trailer["/Root"]["/StructTreeRoot"]
    assert [int(k) for k in tree["/ParentTree"]["/Nums"][0::2]] == [1]
    assert tree["/K"][0]["/K"][0]["/ActualText"] == "second page plain"
    assert reader.pages[1].extract_text().strip() == "second page plain"


@pytest.mark.parametrize("method", ["remove", "image"])
def test_black_boxes_land_on_the_text_with_an_oversized_cropbox(tmp_path, method):
    src = tmp_path / "crop.pdf"
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    c.setFont("Helvetica", 14)
    c.drawString(72, 700, "SECRET WORD here")
    c.showPage()
    c.save()
    w = PdfWriter(clone_from=str(src))
    w.pages[0][NameObject("/CropBox")] = RectangleObject([-50, -50, 662, 842])
    w.write(src)
    out = tmp_path / "out.pdf"
    assert redact.redact(src, out, terms=["SECRET WORD"], method=method) == {1: 1}
    if method == "image":
        page = PdfReader(str(out)).pages[0]
        assert [float(v) for v in page.mediabox] == [0, 0, 612, 792]  # what readers showed
        assert [float(v) for v in page.cropbox] == [0, 0, 612, 792]
    words = (72, 700, 160, 710)
    assert dark_pixels(out, words) > 0.9 * 88 * 10
    assert dark_pixels(out, (72, 650, 160, 660)) == 0  # blank paper below is untouched


def test_filled_fields_stay_in_the_redacted_picture(tmp_path):
    src = filled_form(tmp_path / "form.pdf")
    name, tick = (72, 700, 272, 720), (72, 650, 92, 670)
    before = dark_pixels(src, name), dark_pixels(src, tick)
    out = tmp_path / "out.pdf"
    assert redact.redact(src, out, terms=["John Smith"], method="image") == {1: 1}
    assert not PdfReader(str(out)).get_fields()  # a picture now, but still showing
    assert dark_pixels(out, name) > 0.8 * before[0] > 0
    assert dark_pixels(out, tick) > 0.8 * before[1] > 0


# -- compare and scan clean-up see form fields -------------------------------------------------

def test_compare_notices_changed_form_answers(tmp_path):
    old = filled_form(tmp_path / "v1.pdf", "Maria Garcia")
    new = filled_form(tmp_path / "v2.pdf", "Peter Jones")
    summary = compare.compare(old, new, tmp_path / "diff.pdf")
    assert [(r["removed"], r["added"]) for r in summary["changes"]] == \
        [(["Maria Garcia"], ["Peter Jones"])]
    assert summary["changes"][0]["areas"]  # the field is in the pictures too


def test_a_page_with_only_a_filled_field_is_not_blank(tmp_path):
    src = tmp_path / "field.pdf"
    c = canvas.Canvas(str(src), pagesize=A4)
    c.acroForm.textfield(name="note", value="A whole sentence typed into the form field.",
                         x=72, y=700, width=400, height=24, fontSize=14)
    c.showPage()
    c.save()
    assert scan.find_blank_pages(src) == []


def test_compare_keeps_page_pictures_compressed(tmp_path):
    src = tmp_path / "doc.pdf"
    c = canvas.Canvas(str(src), pagesize=A4)
    for n in range(3):
        c.drawString(72, 760, f"Page {n}")
        c.showPage()
    c.save()
    pages = compare._load(src, None, compare.DPI)
    assert len(pages) == 3
    assert all(isinstance(png, bytes) and len(png) < 100_000 for _, png in pages)  # not 2 MB each
    assert compare._picture(pages[0][1]).size[0] > 700


def test_compare_report_shows_words_in_other_alphabets(tmp_path):
    from pdfmanager import fonts

    def doc(path, city):
        c = canvas.Canvas(str(path), pagesize=A4)
        c.setFont(fonts.font_for("Договор"), 14)
        c.drawString(72, 700, f"Договор подписан в {city}")
        c.showPage()
        c.save()
        return path

    old, new = doc(tmp_path / "v1.pdf", "Москве"), doc(tmp_path / "v2.pdf", "Берлине")
    summary = compare.compare(old, new, tmp_path / "diff.pdf")
    assert summary["changes"][0]["removed"] == ["Москве"]
    report = PdfReader(str(tmp_path / "diff.pdf"))
    summary_text, page_text = (p.extract_text() for p in report.pages)
    assert "removed “Москве”; added “Берлине”" in summary_text
    assert "− Москве" in page_text and "+ Берлине" in page_text
    assert "■" not in summary_text + page_text


# -- flatten -----------------------------------------------------------------------------

def styled_form(path):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.acroForm.textfield(name="a", value="Hello there", x=72, y=700, width=200, height=30,
                         borderColor=colors.black, fillColor=colors.lightgrey, borderWidth=2,
                         fontSize=12)
    c.acroForm.textfield(name="b", value="Right", x=72, y=640, width=200, height=30,
                         borderColor=colors.darkblue, fillColor=colors.lightblue, borderWidth=2)
    c.showPage()
    c.save()
    return path


def test_flatten_keeps_the_look_of_fields_that_have_an_appearance(tmp_path):
    src = styled_form(tmp_path / "form.pdf")
    out = archive.flatten(src, tmp_path / "flat.pdf")
    assert not PdfReader(str(out)).get_fields()
    before, after = render(src)[0], render(out)[0]
    changed = ImageChops.difference(before, after).point(lambda v: 255 if v > 48 else 0)
    assert changed.getbbox() is None  # borders, shading and text exactly where they were


def test_flatten_draws_fields_that_have_no_appearance(tmp_path):
    src = styled_form(tmp_path / "form.pdf")
    w = PdfWriter(clone_from=str(src))
    for ref in w.pages[0]["/Annots"]:
        del ref.get_object()["/AP"]  # some programs leave drawing to the viewer
    w.write(src)
    out = archive.flatten(src, tmp_path / "flat.pdf")
    assert dark_pixels(out, (72, 700, 272, 730)) > 50  # "Hello there"
    assert "Hello there" in PdfReader(str(out)).pages[0].extract_text()


def test_flatten_keeps_comments_it_cannot_draw(tmp_path):
    src = tmp_path / "notes.pdf"
    c = canvas.Canvas(str(src), pagesize=A4)
    c.drawString(72, 760, "Some page text")
    c.showPage()
    c.save()
    w = PdfWriter(clone_from=str(src))
    w.add_annotation(0, Text(text="important remark", rect=(100, 600, 120, 620)))
    w.add_annotation(0, Rectangle(rect=(200, 400, 300, 500), interior_color="ff0000"))
    w.write(src)
    out = archive.flatten(src, tmp_path / "flat.pdf")
    annots = [a.get_object() for a in PdfReader(str(out)).pages[0]["/Annots"]]
    assert [a["/Subtype"] for a in annots] == ["/Text", "/Square"]
    assert annots[0]["/Contents"] == "important remark"
    assert dark_pixels(out, (200, 400, 300, 500)) == dark_pixels(src, (200, 400, 300, 500)) > 0


# -- remove hidden data --------------------------------------------------------------------

def test_sanitize_strips_scripts_from_fields_bookmarks_and_action_chains(tmp_path):
    src = tmp_path / "js.pdf"
    c = canvas.Canvas(str(src), pagesize=A4)
    c.drawString(72, 760, "scripted")
    c.acroForm.textfield(name="amount", value="1", x=72, y=700, width=100, height=20)
    c.linkURL("https://example.com", (72, 600, 200, 620))
    c.showPage()
    c.save()
    w = PdfWriter(clone_from=str(src))

    def script():
        return DictionaryObject({NameObject("/S"): NameObject("/JavaScript"),
                                 NameObject("/JS"): TextStringObject("app.alert(1)")})

    acroform = w._root_object["/AcroForm"]
    widget = acroform["/Fields"][0]
    # A parent field (Acrobat's layout) with the calculation script; the widget is its kid.
    parent = w._add_object(DictionaryObject({
        NameObject("/T"): TextStringObject("totals"), NameObject("/Kids"): ArrayObject([widget]),
        NameObject("/AA"): DictionaryObject({NameObject("/C"): script()})}))
    widget.get_object()[NameObject("/Parent")] = parent
    acroform[NameObject("/Fields")] = ArrayObject([parent])
    acroform[NameObject("/CO")] = ArrayObject([parent])
    w.add_outline_item("Chapter", 0)
    w._root_object["/Outlines"]["/First"].get_object()[NameObject("/A")] = script()
    w._root_object[NameObject("/OpenAction")] = DictionaryObject({
        NameObject("/S"): NameObject("/GoTo"), NameObject("/Next"): script(),
        NameObject("/D"): ArrayObject([w.pages[0].indirect_reference, NameObject("/Fit")])})
    link = w.pages[0]["/Annots"][1].get_object()
    link["/A"][NameObject("/Next")] = ArrayObject([DictionaryObject(
        {NameObject("/S"): NameObject("/Launch"), NameObject("/F"): TextStringObject("cmd.exe")})])
    w.write(src)
    assert b"/JavaScript" in src.read_bytes()
    out = tmp_path / "clean.pdf"
    counts = archive.sanitize(src, out)
    assert counts["javascript"] == 4
    raw = out.read_bytes()
    assert b"/JavaScript" not in raw and b"/Launch" not in raw and b"cmd.exe" not in raw
    reader = PdfReader(str(out))
    assert reader.trailer["/Root"]["/OpenAction"]["/S"] == "/GoTo"  # the harmless part stays
    assert reader.outline[0].title == "Chapter"
    assert reader.pages[0]["/Annots"][1]["/A"]["/URI"] == "https://example.com"
    assert "/CO" not in reader.trailer["/Root"]["/AcroForm"]
    assert archive.sanitize(out, tmp_path / "again.pdf")["javascript"] == 0


# -- dangling annotations -------------------------------------------------------------------

def test_dangling_annotation_entries_are_skipped(tmp_path):
    src = tmp_path / "dangling.pdf"
    c = canvas.Canvas(str(src), pagesize=A4)
    c.drawString(72, 760, "dangling")
    c.acroForm.textfield(name="f", value="kept", x=72, y=700, width=100, height=20)
    c.showPage()
    c.save()
    w = PdfWriter(clone_from=str(src))
    field = w.pages[0]["/Annots"][0]
    w.pages[0][NameObject("/Annots")] = ArrayObject([IndirectObject(9999, 0, w), NullObject(), field])
    w.write(src)
    assert forms.existing_fields(src) == {1: [{"rect": [72, 700, 172, 720], "name": "f", "type": "text"}]}
    archive.flatten(src, tmp_path / "flat.pdf")
    assert "kept" in PdfReader(str(tmp_path / "flat.pdf")).pages[0].extract_text()
    assert archive.sanitize(src, tmp_path / "clean.pdf")["javascript"] == 0
    assert redact.redact(src, tmp_path / "redacted.pdf", terms=["dangling"]) == {1: 1}
    assert core.resize(src, tmp_path / "a5.pdf", size="a5")


# -- PDF/A ---------------------------------------------------------------------------------

@needs_ghostscript
def test_pdfa_with_a_non_ascii_temp_folder_and_title(tmp_path, monkeypatch):
    src = tmp_path / "plain.pdf"
    c = canvas.Canvas(str(src), pagesize=A4)
    c.drawString(72, 700, "archive me")
    c.showPage()
    c.save()
    w = PdfWriter(clone_from=str(src))
    w._info = None  # no title of its own: the output's name becomes the title
    w.write(src)
    temp = tmp_path / "Temp-José"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))  # like %TEMP% under C:\Users\José
    out = archive.to_pdfa(src, tmp_path / "Résumé – 2024.pdf")
    assert archive.pdfa_level(out) == "2B"
    assert PdfReader(str(out)).metadata.title == "Résumé – 2024"
    assert not list(temp.iterdir())  # the working folder is cleaned up


# -- scan clean-up ----------------------------------------------------------------------------

def picture_pdf(path, text=True):
    image = Image.new("L", (1240, 1754), 255)
    if text:
        c = canvas.Canvas(str(path), pagesize=A4)
        c.setFont("Helvetica", 12)
        for k in range(25):
            c.drawString(72, 760 - 24 * k, "The quick brown fox jumps over the lazy dog. " * 2)
        c.showPage()
        c.save()
        w = PdfWriter(clone_from=str(path))
        w.pages[0].rotate(90)
        w.write(path)
        return path
    image.putpixel((600, 900), 0)
    image.save(path.with_suffix(".png"))
    return core.images_to_pdf([path.with_suffix(".png")], path)


@needs_tesseract
def test_auto_rotate_reports_a_broken_tesseract_setup(tmp_path, monkeypatch):
    src = picture_pdf(tmp_path / "sideways.pdf")
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))  # no osd.traineddata here
    with pytest.raises(core.PdfManagerError, match="Tesseract could not check"):
        scan.clean(src, tmp_path / "out.pdf", remove_blank=False, auto_rotate=True)
    monkeypatch.delenv("TESSDATA_PREFIX")
    report = scan.clean(src, tmp_path / "out.pdf", remove_blank=False, auto_rotate=True)
    assert report["rotated"] == {1: 270}


@needs_tesseract
def test_auto_rotate_leaves_pages_without_text_alone(tmp_path):
    src = picture_pdf(tmp_path / "speck.pdf", text=False)
    report = scan.clean(src, tmp_path / "out.pdf", remove_blank=False, auto_rotate=True)
    assert report == {"removed": [], "rotated": {}}


# -- plain text files -----------------------------------------------------------------------

def test_text_files_in_other_alphabets(tmp_path):
    src = tmp_path / "notes.txt"
    src.write_text("Привет мир\n日本語のテキスト\nplain ascii " * 1 + "x" * 200 + "\n", encoding="utf-8")
    out = office.text_to_pdf(src, tmp_path / "notes.pdf")
    text = PdfReader(str(out)).pages[0].extract_text()
    assert "Привет мир" in text and "plain ascii" in text
    assert "■" not in text
    assert len(text.splitlines()) >= 5  # the long line is wrapped as before
    src.write_bytes("Ärger\nПривет".encode("utf-16"))  # with a byte-order mark
    text = PdfReader(str(office.text_to_pdf(src, tmp_path / "bom.pdf"))).pages[0].extract_text()
    assert "Ärger" in text and "Привет" in text


# -- recipes ---------------------------------------------------------------------------------

def test_recipes_skip_malformed_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(recipes, "data_dir", lambda: tmp_path)
    (tmp_path / "recipes.json").write_text(json.dumps({"recipes": [
        {"name": "Good", "steps": [{"op": "clean", "options": {}}]},
        {"steps": [{"op": "ocr"}]}, "junk", {"name": 3, "steps": []}, None]}), encoding="utf-8")
    assert list(recipes.load_all()) == ["Good"]
    assert recipes.get("Good")["steps"][0]["op"] == "clean"
    recipes.save("Also fine", [{"op": "compress"}])
    assert list(recipes.load_all()) == ["Also fine", "Good"]
    with pytest.raises(core.PdfManagerError, match="no recipe called"):
        recipes.get("junk")
