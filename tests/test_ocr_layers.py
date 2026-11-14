# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.36.0 (ocr.py): pages OCR used to pass over though they needed it. A
scan whose text layer is rubbish (signs and broken letters from an old
program) gets its layer replaced, the page's picture untouched. A page of
real text with a scanned figure on it has the figure read, its words added
beside the page's own. A page already read well is left alone.

The reading itself is stood in for here (a made-up engine that says what it
was shown), so these run everywhere; the last test uses the real engine."""
import hashlib

import pypdfium2 as pdfium
import pytest
from pypdf import PdfReader

import ocrpages
import ocrscore
from pdfmanager import ocr, progress
from pdfmanager.textlayer import Word


def text_of(path, page=0):
    doc = pdfium.PdfDocument(str(path))
    try:
        return " ".join(doc[page].get_textpage().get_text_range().split())
    finally:
        doc.close()


def pictures(path):
    """The page pictures' own bytes, as stored: {name: sha256}."""
    out = {}
    for name, obj in (PdfReader(str(path)).pages[0]["/Resources"].get("/XObject") or {}).items():
        obj = obj.get_object()
        if obj.get("/Subtype") == "/Image":
            out[name] = hashlib.sha256(obj._data).hexdigest()
    return out


@pytest.fixture
def engine(monkeypatch):
    """A stand-in for the reading: every picture it is shown "says" its
    size, in three words across its middle. ``seen`` lists the sizes."""
    seen = []

    def recognise(image, script=None):
        w, h = image.size
        seen.append((w, h))
        words = []
        for k, text in enumerate(("READ", f"W{w}", f"H{h}")):
            x0, x1, y0, y1 = w * (0.1 + 0.25 * k), w * (0.3 + 0.25 * k), h * 0.45, h * 0.55
            words.append(Word(text, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], 0.99))
        return [ocr.Line(" ".join(word.text for word in words), words[0].quad, 0.99, words)]
    monkeypatch.setattr(ocr, "recognise", recognise)
    return seen


def run(src, out, **how):
    said = []
    ocr._run_builtin(src, out, how.pop("force", False), False, rotate=False, notes=said, **how)
    return said


# -- telling a rubbish layer --------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "~^|\\{}#@*lI1!¬¦` ~~^^|| {}{}## @@** ¬¬¦¦`` ~^|\\{}#@* ¬¦` |||| ^^^^ ~~~~ #### @@@@ ****",
    "��� �� ���� ��� ����� �� ���",
    "".join(chr(0xE000 + n % 40) + (" " if n % 3 == 2 else "") for n in range(60)),  # private-use letters
    ". , ; : ' \" - _ . . , , ; ; : : ' ' - - _ _ . , ; : . , ; : . , ; : . , ; :",
    "\x01\x02\x03 \x04\x05 \x06\x07\x08 \x0e\x0f \x10\x11\x12 \x13\x14 \x15\x16\x17 \x18\x19",
])
def test_rubbish_is_told(text):
    assert ocr.layer_is_rubbish(text)


@pytest.mark.parametrize("text", [
    ocrpages.TEXT,                                          # an invoice: numbers, signs, a little German
    "\n".join(" ".join(row) for row in ocrpages.TABLE),     # a table of figures
    ocrscore.CJK_TEXT,                                      # Chinese
    "Тверской бульвар, дом 12. Счёт № 4471 от 14.03.2024 на сумму 176,16 руб.",
    "INV-2024-0117 / PO 4471-B / 14.03.2024 / 176.16 / 20-45-77 / 83920114 / 023 9283 1100",
    "Total due: £176.16 (VAT 20% = £29.36). Pay within 30 days; ref. #2024-0117 @ accounts.",
    "ok",                                                   # too little to judge: left alone
    "",
])
def test_real_text_is_not_taken_for_rubbish(text):
    assert not ocr.layer_is_rubbish(text)


# -- a scan with a rubbish layer ------------------------------------------------------------

def test_a_rubbish_layer_is_replaced_and_the_picture_is_untouched(tmp_path, engine):
    src, truth = ocrscore.garbage_layer(tmp_path)
    out = tmp_path / "out.pdf"
    assert "~" in text_of(src) or "#" in text_of(src)
    said = run(src, out)
    read = text_of(out)
    assert read.startswith("READ W") and not any(ch in read for ch in "~^|{}#@¬¦`"), read
    assert len(engine) == 1 and engine[0][0] > 2000              # the whole page was shown, once
    assert pictures(out) == pictures(src) and pictures(src)       # same bytes: not drawn again
    assert said == ["page 1: its text layer was unreadable and has been replaced"]


def test_a_layer_that_reads_well_is_left_alone(tmp_path, engine):
    image, truth = ocrpages.letter()
    scan = ocrpages.scan_pdf(image, tmp_path / "scan.pdf")
    once = tmp_path / "once.pdf"
    run(scan, once)                                               # gives it a layer ("READ W2480 H3508")
    engine.clear()
    twice = tmp_path / "twice.pdf"
    said = run(once, twice)
    assert engine == [] and said == []                            # nothing shown to the engine again
    assert text_of(twice) == text_of(once)


def test_rubbish_someone_can_see_is_not_a_layer_to_replace(tmp_path, engine):
    """Visible signs (a page of symbols, ASCII art) are the page itself."""
    from reportlab.pdfgen import canvas
    path = tmp_path / "art.pdf"
    c = canvas.Canvas(str(path))
    for k in range(20):
        c.drawString(72, 760 - 14 * k, "~^|\\{}#@* ~~^^|| {}{}## @@** |||| ^^^^ ~~~~ ####")
    c.save()
    out = tmp_path / "out.pdf"
    assert run(path, out) == [] and engine == []
    assert text_of(out) == text_of(path)


# -- real text with a scanned figure ----------------------------------------------------------

def test_a_scanned_figure_among_real_text_is_read(tmp_path, engine):
    src, _ = ocrscore.mixed(tmp_path)
    out = tmp_path / "out.pdf"
    before = text_of(src)
    said = run(src, out)
    after = text_of(out)
    assert len(engine) == 1
    w, h = engine[0]
    assert 1200 < w < 1500 and 300 < h < 500, engine             # the figure (324 x 96 pt at 300 dpi), not the page
    assert f"READ W{w} H{h}" in after
    for sentence in ("The committee met on Tuesday", "support the scheme in principle."):
        assert after.count(sentence) == 1 == before.count(sentence)   # the page's own words, once
    assert pictures(out) == pictures(src)
    assert said == ["page 1: read the text in 1 picture"]
    # The new words are on the figure (60 to 384 pt across, 380 to 476 pt up the page).
    doc = pdfium.PdfDocument(str(out))
    try:
        tp = doc[0].get_textpage()
        at = tp.get_text_range().index("READ")
        l, b, r, t = tp.get_charbox(at)
    finally:
        doc.close()
    assert 60 <= l <= 384 and 380 <= b <= 476, (l, b, r, t)
    # Again: the figure has its words now, and isn't read twice.
    engine.clear()
    assert run(out, tmp_path / "again.pdf") == [] and engine == []


def test_words_the_page_already_has_over_a_picture_are_not_added_again(tmp_path, monkeypatch):
    """A scan with a stamp typed over it (a Bates number, a header): the
    scan is read, and the stamp's words aren't doubled."""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    image, _ = ocrpages.letter()
    path = tmp_path / "stamped.pdf"
    c = canvas.Canvas(str(path), pagesize=(595.2, 841.92))
    c.drawImage(ImageReader(image), 0, 0, 595.2, 841.92)
    c.setFont("Helvetica", 12)
    c.drawString(400, 800, "ZPDF-000123")
    c.save()

    def recognise(picture, script=None):
        w, h = picture.size
        def word(text, x, y, wide):  # (x, y in points from the top-left of the page)
            k = w / 595.2
            return Word(text, [(x * k, y * k), ((x + wide) * k, y * k), ((x + wide) * k, (y + 12) * k), (x * k, (y + 12) * k)], 0.99)
        words = [word("ZPDF-000123", 400, 841.92 - 800 - 10, 80), word("committee", 100, 200, 60)]
        return [ocr.Line("x", words[0].quad, 0.99, words)]
    monkeypatch.setattr(ocr, "recognise", recognise)
    out = tmp_path / "out.pdf"
    said = run(path, out)
    read = text_of(out)
    assert read.count("ZPDF-000123") == 1 and "committee" in read, read
    assert said == ["page 1: read the text in 1 picture"]


def test_small_pictures_and_pages_without_any_are_passed_over(tmp_path, engine):
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    path = tmp_path / "logo.pdf"
    c = canvas.Canvas(str(path), pagesize=(595.2, 841.92))
    c.setFont("Helvetica", 11)
    for k in range(30):
        c.drawString(60, 780 - 15 * k, "A page of ordinary text with a small logo in its corner.")
    c.drawImage(ImageReader(Image.new("RGB", (120, 60), (200, 30, 30))), 480, 790, 60, 30)   # a logo
    c.save()
    assert run(path, tmp_path / "out.pdf") == [] and engine == []


def test_force_still_reads_every_page_whole(tmp_path, engine):
    src, _ = ocrscore.mixed(tmp_path)
    run(src, tmp_path / "out.pdf", force=True)
    assert len(engine) == 1 and engine[0][0] > 2000


def test_cancel_between_pictures(tmp_path, engine, monkeypatch):
    src, _ = ocrscore.mixed(tmp_path)

    def cancelled(*args, **kwargs):
        raise progress.Cancelled()
    monkeypatch.setattr(ocr, "recognise", cancelled)
    with pytest.raises(progress.Cancelled):
        run(src, tmp_path / "out.pdf")


# -- with the real engine ---------------------------------------------------------------------

@pytest.mark.skipif(not ocr.builtin_available(), reason="needs rapidocr")
def test_the_two_holdout_files_come_out_searchable(tmp_path):
    src, truth = ocrscore.garbage_layer(tmp_path)
    out = tmp_path / "g.pdf"
    ocr.run(src, out, engine="builtin")
    assert ocrscore.found_words(truth, text_of(out)) > 0.97
    src, truth = ocrscore.mixed(tmp_path)
    out = tmp_path / "m.pdf"
    ocr.run(src, out, engine="builtin")
    assert ocrscore.found_words(truth, text_of(out)) > 0.9


def photo_page(path):
    """A page of real text with one large photo (no words in it)."""
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=(595.2, 841.92))
    c.setFont("Helvetica", 11)
    for k in range(12):
        c.drawString(60, 780 - 15 * k, "A report with a photograph of the lake below this paragraph.")
    c.drawImage(ImageReader(Image.effect_noise((300, 200), 40).convert("RGB")), 60, 300, 400, 260)
    c.save()
    return path


@pytest.mark.parametrize("found", [
    [("一", 0.92)],                       # a horizon read as the Chinese "one" (from the Windows check)
    [("l", 0.8)], [("-", 0.9), ("|", 0.7)],   # strokes and edges
    [("A1", 0.95)],                           # too little to be a caption
    [("Lakeside", 0.31), ("view", 0.4)],      # words the engine wasn't sure of
])
def test_scraps_read_in_a_photo_are_not_added(tmp_path, monkeypatch, found):
    def recognise(image, script=None):
        w, h = image.size
        words = [Word(text, [(w * 0.1, h * 0.4), (w * 0.3, h * 0.4), (w * 0.3, h * 0.5), (w * 0.1, h * 0.5)], conf)
                 for text, conf in found]
        return [ocr.Line(" ".join(t for t, _ in found), words[0].quad, 0.9, words)]
    monkeypatch.setattr(ocr, "recognise", recognise)
    src, out = photo_page(tmp_path / "photo.pdf"), tmp_path / "out.pdf"
    assert run(src, out) == []
    assert text_of(out) == text_of(src)


def test_a_caption_in_a_picture_is_still_added(tmp_path, monkeypatch):
    def recognise(image, script=None):
        w, h = image.size
        words = [Word(text, [(w * x, h * 0.9), (w * (x + 0.2), h * 0.9), (w * (x + 0.2), h * 0.95), (w * x, h * 0.95)], 0.9)
                 for text, x in (("Lake", 0.1), ("Windermere", 0.35), ("一", 0.7))]
        return [ocr.Line("Lake Windermere", words[0].quad, 0.9, words)]
    monkeypatch.setattr(ocr, "recognise", recognise)
    src, out = photo_page(tmp_path / "photo.pdf"), tmp_path / "out.pdf"
    assert run(src, out) == ["page 1: read the text in 1 picture"]
    assert "Lake Windermere" in text_of(out)


def test_a_picture_full_of_text_keeps_the_words_the_engine_was_less_sure_of(tmp_path, monkeypatch):
    """From the Windows re-check: a scan with a stamp typed over it lost
    "library", read at a confidence the scrap rule dropped. A picture with
    plenty of words is a scan, read as a whole page is."""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    image, _ = ocrpages.letter()
    path = tmp_path / "stamped.pdf"
    c = canvas.Canvas(str(path), pagesize=(595.2, 841.92))
    c.drawImage(ImageReader(image), 0, 0, 595.2, 841.92)
    c.setFont("Helvetica", 12)
    c.drawString(400, 800, "ZPDF-000123")
    c.save()
    said = "The committee approved the plans for the new library wing and the budget".split()

    def recognise(picture, script=None):
        w, h = picture.size
        words = []
        for k, text in enumerate(said):
            x = w * (0.08 + 0.065 * k)
            words.append(Word(text, [(x, h * 0.5), (x + w * 0.05, h * 0.5), (x + w * 0.05, h * 0.52), (x, h * 0.52)],
                              0.45 if text == "library" else 0.95))
        return [ocr.Line(" ".join(said), words[0].quad, 0.9, words)]
    monkeypatch.setattr(ocr, "recognise", recognise)
    out = tmp_path / "out.pdf"
    run(path, out)
    read = text_of(out)
    assert "new library wing" in read, read
