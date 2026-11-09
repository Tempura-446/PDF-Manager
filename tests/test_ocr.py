"""OCR: the built-in engine, the Tesseract engine, and choosing between them."""
import pytest
from PIL import Image

import ocrpages
from pdfmanager import core, ocr

needs_builtin = pytest.mark.skipif(not ocr.builtin_available(), reason="needs rapidocr")


# -- choosing an engine -------------------------------------------------------

def test_engine_ids():
    assert ocr.ENGINES == ("auto", "builtin", "tesseract")
    with pytest.raises(core.PdfManagerError, match="unknown OCR engine"):
        ocr.choose_engine("abbyy")


def test_auto_prefers_builtin_then_tesseract(monkeypatch):
    monkeypatch.setattr(ocr, "builtin_available", lambda: True)
    assert ocr.choose_engine("auto") == "builtin"
    monkeypatch.setattr(ocr, "builtin_available", lambda: False)
    monkeypatch.setattr(ocr, "tesseract_available", lambda: True)
    assert ocr.choose_engine("auto") == "tesseract"
    monkeypatch.setattr(ocr, "tesseract_available", lambda: False)
    with pytest.raises(core.PdfManagerError) as e:
        ocr.choose_engine("auto")
    assert "pip install pdf-manager[ocr]" in str(e.value) and "Tesseract" in str(e.value)


def test_named_engine_that_is_missing_says_what_to_install(monkeypatch):
    monkeypatch.setattr(ocr, "builtin_available", lambda: False)
    with pytest.raises(core.PdfManagerError, match=r"pip install pdf-manager\[ocr\]"):
        ocr.choose_engine("builtin")
    monkeypatch.setattr(ocr, "tesseract_available", lambda: False)
    with pytest.raises(core.PdfManagerError, match="tesseract-ocr.github.io"):
        ocr.choose_engine("tesseract")


# -- recognising an image -----------------------------------------------------

@needs_builtin
def test_recognise_gives_lines_and_words_with_boxes():
    im = ocrpages.render()
    lines = ocr.recognise(im)
    assert len(lines) >= 12
    text = ocr.text_of(lines)
    assert "Northwind" in text and "176.16" in text
    # Reading order: rows top to bottom, and a table row the detector split
    # at its wide gaps still reads across.
    rows = text.splitlines()
    assert rows[0].startswith("Invoice No.") and rows[-1].startswith("Zusammenfassung")
    assert "Item Qty Unit price Amount" in rows[2]
    for w in (w for line in lines for w in line.words):
        assert len(w.quad) == 4 and 0 <= w.quad[0][0] < im.width
        assert w.confidence >= ocr.MIN_CONFIDENCE


@needs_builtin
def test_recognise_reads_a_degraded_page_well():
    im = ocrpages.degrade(ocrpages.render(font_size=40, serif=True))
    text = ocr.text_of(ocr.recognise(im))
    assert ocrpages.cer(ocrpages.TEXT, text) < 0.06
    assert ocrpages.missing_words(ocrpages.TEXT, text) < 0.3


@needs_builtin
def test_recognise_handles_a_page_without_text():
    assert ocr.recognise(Image.new("L", (800, 1000), 255)) == []


@needs_builtin
def test_skew_angle_finds_a_tilted_page_and_ignores_a_straight_one():
    straight = ocrpages.render()
    assert ocr.skew_angle(straight) == 0.0
    tilted = straight.rotate(2.0, resample=Image.BICUBIC, fillcolor=255)  # counter-clockwise
    assert ocr.skew_angle(tilted) == pytest.approx(2.0, abs=0.4)
    assert ocr.skew_angle(Image.new("L", (800, 1000), 255)) == 0.0
