"""The invisible text layer OCR writes over scanned pages."""
import pdfplumber
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

from pdfmanager import core, textlayer
from pdfmanager.textlayer import Word


def page_with_word(tmp_path, rotate=0, crop=None):
    """A writer whose one page shows the word 'Hello' at (100, 705) in 20 pt."""
    pdf = tmp_path / "src.pdf"
    c = core.pdf_canvas(str(pdf), pagesize=(595, 842))
    c.setFont("Helvetica", 20)
    c.drawString(100, 705, "Hello")
    c.save()
    w = PdfWriter(clone_from=str(pdf))
    p = w.pages[0]
    if rotate:
        p.rotate(rotate)
    if crop:
        p.cropbox = crop
    return w, p


def rendered(path, dpi=100):
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    im = doc[0].render(scale=dpi / 72).to_pil()
    doc.close()
    return im


def ink_box(im):
    """(x0, y0, x1, y1) in pixels around the dark pixels."""
    return im.convert("L").point(lambda v: 255 if v < 128 else 0).getbbox()


def one_word(text, x0, y0, x1, y1):
    return Word(text, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def plumber_words(path):
    """{"Hello": box of the visible word, "HELLO": box of the invisible one},
    each box a dict with x0, x1, top, bottom. (pdfplumber would merge the two
    words into one, as they sit on the same spot, so the characters are
    grouped by font instead.)"""
    with pdfplumber.open(path) as pdf:
        chars = pdf.pages[0].chars
    boxes = {}
    for key, ours in (("Hello", False), ("HELLO", True)):
        group = [c for c in chars if ("GlyphLess" in c["fontname"]) is ours]
        assert group, key
        boxes[key] = {"x0": min(c["x0"] for c in group), "x1": max(c["x1"] for c in group),
                      "top": min(c["top"] for c in group), "bottom": max(c["bottom"] for c in group)}
    return boxes


def test_words_are_extractable_and_invisible(tmp_path):
    w, p = page_with_word(tmp_path)
    words = [one_word("Hello", 100, 0, 300, 50), one_word("发票", 400, 0, 500, 50),
             one_word("Fälligkeit€", 100, 100, 300, 150)]
    assert textlayer.add_text_layer(w, p, words, (827, 1169), 100) == 3
    out = tmp_path / "out.pdf"
    w.write(out)
    text = PdfReader(out).pages[0].extract_text()
    for t in ("Hello", "发票", "Fälligkeit€"):
        assert t in text
    with pdfplumber.open(out) as pdf:
        assert "发票" in pdf.pages[0].extract_text()
    # Still looks the same: the only ink is the visible Hello.
    assert ink_box(rendered(out)) == ink_box(rendered(tmp_path / "src.pdf"))


def test_font_is_embedded_once_per_file(tmp_path):
    w, p = page_with_word(tmp_path)
    w.add_blank_page(595, 842)
    for page in w.pages:
        textlayer.add_text_layer(w, page, [one_word("x", 0, 0, 10, 10)], (827, 1169), 100)
    out = tmp_path / "out.pdf"
    w.write(out)
    r = PdfReader(out)
    fonts = {r.pages[i]["/Resources"]["/Font"].raw_get(textlayer.GLYPHLESS_FONT_RESOURCE).idnum
             for i in range(2)}
    assert len(fonts) == 1
    font = r.pages[0]["/Resources"]["/Font"][textlayer.GLYPHLESS_FONT_RESOURCE].get_object()
    assert font["/Subtype"] == "/Type0" and font["/Encoding"] == "/Identity-H"
    descriptor = font["/DescendantFonts"][0].get_object()["/FontDescriptor"].get_object()
    assert "/FontFile2" in descriptor


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_layer_lands_on_the_visible_word(tmp_path, rotate):
    w, p = page_with_word(tmp_path, rotate=rotate)
    src = tmp_path / "rot.pdf"
    w.write(src)
    im = rendered(src, dpi=100)
    x0, y0, x1, y1 = ink_box(im)
    w2 = PdfWriter(clone_from=str(src))
    textlayer.add_text_layer(w2, w2.pages[0], [one_word("HELLO", x0, y0, x1, y1)], im.size, 100)
    out = tmp_path / "out.pdf"
    w2.write(out)
    words = plumber_words(out)
    a, b = words["Hello"], words["HELLO"]
    for key in ("x0", "x1", "top", "bottom"):
        # Helvetica's descent (4 pt here) is below the ink the layer sits on.
        assert abs(a[key] - b[key]) < 6, (key, a[key], b[key])


def test_layer_follows_a_shifted_crop_box(tmp_path):
    w, p = page_with_word(tmp_path, crop=RectangleObject((50, 600, 400, 800)))
    src = tmp_path / "crop.pdf"
    w.write(src)
    im = rendered(src, dpi=100)
    x0, y0, x1, y1 = ink_box(im)
    w2 = PdfWriter(clone_from=str(src))
    textlayer.add_text_layer(w2, w2.pages[0], [one_word("HELLO", x0, y0, x1, y1)], im.size, 100)
    out = tmp_path / "out.pdf"
    w2.write(out)
    words = plumber_words(out)
    assert abs(words["Hello"]["x0"] - words["HELLO"]["x0"]) < 6
    assert abs(words["Hello"]["top"] - words["HELLO"]["top"]) < 6


def test_non_bmp_characters_become_replacement_character(tmp_path):
    w, p = page_with_word(tmp_path)
    textlayer.add_text_layer(w, p, [one_word("a😀b", 0, 0, 30, 10)], (827, 1169), 100)
    out = tmp_path / "out.pdf"
    w.write(out)
    assert "a�b" in PdfReader(out).pages[0].extract_text()
