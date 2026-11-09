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


# -- the built-in engine end to end -------------------------------------------

import pdfplumber  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402

from pdfmanager import progress, textlayer  # noqa: E402


def clean_scan(tmp_path, name="scan.pdf"):
    return ocrpages.scan_pdf(ocrpages.render(), tmp_path / name)


@needs_builtin
@pytest.mark.parametrize("kind, ceiling", [("clean", 0.01), ("degraded", 0.06), ("lowres", 0.01)])
def test_builtin_makes_a_scan_searchable(tmp_path, kind, ceiling):
    im = ocrpages.render()
    if kind == "degraded":
        im = ocrpages.degrade(ocrpages.render(font_size=40, serif=True))
    if kind == "lowres":
        im = ocrpages.lowres(im)
    scan = ocrpages.scan_pdf(im, tmp_path / "scan.pdf", dpi=110 if kind == "lowres" else 300)
    assert core.extract_text(scan).strip() == ""
    out = core.ocr(scan, tmp_path / "out.pdf", engine="builtin")
    text = core.extract_text(out)
    assert ocrpages.cer(ocrpages.TEXT, text) < ceiling
    assert "Northwind" in text


@needs_builtin
def test_builtin_words_sit_on_the_printed_words(tmp_path):
    out = core.ocr(clean_scan(tmp_path), tmp_path / "out.pdf", engine="builtin")
    with pdfplumber.open(out) as pdf:
        page = pdf.pages[0]
        word = next(w for w in page.extract_words() if w["text"] == "Northwind")
        box = (word["x0"] - 3, word["top"] - 3, word["x1"] + 3, word["bottom"] + 3)
        inside = page.crop(box).to_image(resolution=72).original.convert("L")
        assert inside.getextrema()[0] < 128   # there is ink under the word
        # The box ends where the ink ends: the gap before the next word is white.
        gap = (word["x1"] + 1, word["top"], word["x1"] + 4, word["bottom"])
        outside = page.crop(gap).to_image(resolution=72).original.convert("L")
        assert outside.getextrema()[0] > 200


@needs_builtin
def test_builtin_skips_pages_that_already_have_text(tmp_path):
    scan = clean_scan(tmp_path)
    typed = tmp_path / "typed.pdf"
    c = core.pdf_canvas(str(typed))
    c.drawString(72, 720, "Typed page")
    c.save()
    both = core.merge([typed, scan], tmp_path / "both.pdf")
    out = core.ocr(both, tmp_path / "out.pdf", engine="builtin")
    r = PdfReader(out)
    assert r.pages[0].extract_text().count("Typed page") == 1
    assert "Northwind" in r.pages[1].extract_text()
    assert textlayer.GLYPHLESS_FONT_RESOURCE not in r.pages[0]["/Resources"].get("/Font", {})


@needs_builtin
def test_builtin_leaves_pages_without_words_alone(tmp_path):
    blank = ocrpages.scan_pdf(Image.new("L", (1240, 1754), 255), tmp_path / "blank.pdf", dpi=150)
    out = core.ocr(blank, tmp_path / "out.pdf", engine="builtin")
    assert core.extract_text(out).strip() == ""
    assert len(PdfReader(out).pages) == 1


@needs_builtin
def test_builtin_keeps_the_page_image_untouched(tmp_path):
    scan = ocrpages.scan_pdf(ocrpages.render().convert("RGB"), tmp_path / "scan.pdf")
    out = core.ocr(scan, tmp_path / "out.pdf", engine="builtin")
    before = PdfReader(scan).pages[0].images[0].image.tobytes()
    after = PdfReader(out).pages[0].images[0].image.tobytes()
    assert before == after
    assert out.stat().st_size < scan.stat().st_size + 80_000  # the font and the layer


@needs_builtin
def test_force_replaces_an_old_text_layer(tmp_path):
    scan = clean_scan(tmp_path)
    w = PdfWriter(clone_from=str(scan))
    textlayer.add_text_layer(w, w.pages[0], [textlayer.Word("WRONGTEXT", [(10, 10), (400, 10), (400, 60), (10, 60)])],
                             (2480, 3508), 300)
    bad = tmp_path / "bad.pdf"
    w.write(bad)
    assert "WRONGTEXT" in core.extract_text(bad)
    kept = core.ocr(bad, tmp_path / "kept.pdf", engine="builtin")
    assert "WRONGTEXT" in core.extract_text(kept) and "Northwind" not in core.extract_text(kept)
    out = core.ocr(bad, tmp_path / "out.pdf", engine="builtin", force=True)
    text = core.extract_text(out)
    assert "WRONGTEXT" not in text and "Northwind" in text
    assert out.stat().st_size < scan.stat().st_size  # a black-and-white scan packs tighter than before


@needs_builtin
def test_force_keeps_a_turned_page_as_it_was(tmp_path):
    scan = clean_scan(tmp_path)
    w = PdfWriter(clone_from=str(scan))
    w.pages[0].rotate(90)
    turned = tmp_path / "turned.pdf"
    w.write(turned)
    out = core.ocr(turned, tmp_path / "out.pdf", engine="builtin", force=True)
    page = PdfReader(out).pages[0]
    assert page.rotation == 90 and page.mediabox == PdfReader(turned).pages[0].mediabox
    assert "Northwind" in page.extract_text()
    with pdfplumber.open(out) as pdf:
        p = pdf.pages[0]
        word = next(wd for wd in p.extract_words() if wd["text"] == "Northwind")
        box = (word["x0"] - 3, word["top"] - 3, word["x1"] + 3, word["bottom"] + 3)
        assert p.crop(box).to_image(resolution=72).original.convert("L").getextrema()[0] < 128


@needs_builtin
def test_deskew_straightens_a_crooked_scan(tmp_path):
    im = ocrpages.render().rotate(2.0, resample=Image.BICUBIC, fillcolor=255)
    scan = ocrpages.scan_pdf(im, tmp_path / "scan.pdf")
    out = core.ocr(scan, tmp_path / "out.pdf", engine="builtin", deskew=True)
    assert "Northwind" in core.extract_text(out)
    with pdfplumber.open(out) as pdf:
        words = [w for w in pdf.pages[0].extract_words() if w["text"] in ("Invoice", "2024")]
        first, last = min(words, key=lambda w: w["x0"]), max(words, key=lambda w: w["x0"])
        assert abs(first["top"] - last["top"]) < 4  # the words of one line are level again
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(out))
    page_im = doc[0].render(scale=1).to_pil().convert("L")
    doc.close()
    assert ocr.skew_angle(page_im) == 0.0


@needs_builtin
def test_deskew_works_on_a_page_stored_sideways(tmp_path):
    # Scanners often store the picture sideways and set /Rotate so it shows
    # upright; the crooked text must still come out straight.
    im = ocrpages.render().rotate(2.0, resample=Image.BICUBIC, fillcolor=255)
    scan = ocrpages.scan_pdf(im.rotate(90, expand=True), tmp_path / "scan.pdf")
    w = PdfWriter(clone_from=str(scan))
    w.pages[0].rotate(90)
    turned = tmp_path / "turned.pdf"
    w.write(turned)
    out = core.ocr(turned, tmp_path / "out.pdf", engine="builtin", deskew=True)
    assert "Northwind" in core.extract_text(out)
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(out))
    page_im = doc[0].render(scale=1).to_pil().convert("L")
    doc.close()
    assert ocr.skew_angle(page_im) == 0.0


@needs_builtin
def test_the_engine_is_quiet(tmp_path):
    import subprocess
    import sys
    scan = clean_scan(tmp_path)
    run = subprocess.run([sys.executable, "-c",
                          "from pdfmanager import core; import sys; core.ocr(sys.argv[1], sys.argv[2], engine='builtin')",
                          str(scan), str(tmp_path / "out.pdf")], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert "INFO" not in run.stderr and "INFO" not in run.stdout


@needs_builtin
def test_builtin_opens_encrypted_input(tmp_path):
    locked = core.encrypt(clean_scan(tmp_path), tmp_path / "locked.pdf", "pw")
    out = core.ocr(locked, tmp_path / "out.pdf", engine="builtin", password="pw")
    assert "Northwind" in core.extract_text(out)


@needs_builtin
def test_builtin_cancel_leaves_no_output(tmp_path, monkeypatch):
    scan = clean_scan(tmp_path)
    both = core.merge([scan, scan], tmp_path / "both.pdf")
    job = progress.Job()

    def cancel_after_first(image):
        job.cancel()
        return []

    monkeypatch.setattr(ocr, "recognise", cancel_after_first)
    job.run(core.ocr, both, tmp_path / "out.pdf", engine="builtin")
    assert job.state == "cancelled"
    assert not (tmp_path / "out.pdf").exists() and not list(tmp_path.glob("*.tmp*"))


def test_core_ocr_passes_the_engine_through(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(ocr, "run", lambda *a, **k: seen.update(k) or tmp_path / "x.pdf")
    core.ocr(tmp_path / "a.pdf", tmp_path / "x.pdf", engine="tesseract", language="deu")
    assert seen["engine"] == "tesseract" and seen["language"] == "deu"
