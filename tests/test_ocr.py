"""OCR: the built-in engine, the Tesseract engine, and choosing between them."""
from pathlib import Path

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


# What the model returns for a page with nothing on it.
_NOTHING_READ = type("Nothing", (), {"txts": None, "boxes": None, "scores": None, "word_results": None})()


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
    # rotate=False: the page is shown sideways and must stay so (with it on,
    # it is turned upright: see test_a_page_shown_sideways_is_turned_back)
    out = core.ocr(turned, tmp_path / "out.pdf", engine="builtin", force=True, rotate=False)
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

    def cancel_after_first(image, script=None):
        job.cancel()
        return _NOTHING_READ

    monkeypatch.setattr(ocr, "_read", cancel_after_first)
    job.run(core.ocr, both, tmp_path / "out.pdf", engine="builtin")
    assert job.state == "cancelled"
    assert not (tmp_path / "out.pdf").exists() and not list(tmp_path.glob("*.tmp*"))


def test_core_ocr_passes_the_engine_through(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(ocr, "run", lambda *a, **k: seen.update(k) or tmp_path / "x.pdf")
    core.ocr(tmp_path / "a.pdf", tmp_path / "x.pdf", engine="tesseract", language="deu")
    assert seen["engine"] == "tesseract" and seen["language"] == "deu"


# -- findings from the branch review ------------------------------------------

@needs_builtin
def test_other_pdfium_users_are_not_locked_out_while_recognising(tmp_path, monkeypatch):
    # The app renders thumbnails and previews under the same lock; a long
    # OCR job must not hold it while the model runs.
    scan = clean_scan(tmp_path)
    free = []

    def read_checking_the_lock(image, script=None):
        got = core.PDFIUM_LOCK.acquire(blocking=False)
        if got:
            core.PDFIUM_LOCK.release()
        free.append(got)
        return _NOTHING_READ

    monkeypatch.setattr(ocr, "_read", read_checking_the_lock)
    core.ocr(scan, tmp_path / "out.pdf", engine="builtin")
    assert free == [True]


def test_builtin_counts_as_available_only_when_it_imports(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name.startswith("onnxruntime"):
            raise ImportError("DLL load failed while importing onnxruntime")
        return real_import(name, *args, **kwargs)

    ocr.reset()
    monkeypatch.setattr(builtins, "__import__", failing_import)
    try:
        assert ocr.builtin_available() is False
    finally:
        ocr.reset()


@needs_builtin
def test_engine_trouble_is_reported_plainly(tmp_path, monkeypatch):
    scan = clean_scan(tmp_path)

    def broken_engine(script=None):
        raise RuntimeError("DLL load failed")

    monkeypatch.setattr(ocr, "_engine", broken_engine)
    for deskew in (False, True):
        with pytest.raises(core.PdfManagerError, match="OCR failed on page 1"):
            core.ocr(scan, tmp_path / "out.pdf", engine="builtin", deskew=deskew)


@needs_builtin
def test_a_model_that_will_not_load_says_how_to_fix_it(monkeypatch):
    import rapidocr

    def wont_load(*args, **kwargs):
        raise RuntimeError("Failed to download model")

    ocr.reset()
    monkeypatch.setattr(rapidocr, "RapidOCR", wont_load)
    try:
        with pytest.raises(core.PdfManagerError, match=r"pip install pdf-manager\[ocr\]"):
            ocr.recognise(ocrpages.render())
    finally:
        ocr.reset()


def test_auto_leaves_other_scripts_to_tesseract(monkeypatch):
    monkeypatch.setattr(ocr, "builtin_available", lambda: True)
    monkeypatch.setattr(ocr, "tesseract_available", lambda: True)
    assert ocr.choose_engine("auto", "eng+deu") == "builtin"
    assert ocr.choose_engine("auto", "chi_sim") == "builtin"
    assert ocr.choose_engine("auto", "heb") == "tesseract"
    assert ocr.choose_engine("auto", "eng+kor+ara") == "tesseract"
    monkeypatch.setattr(ocr, "tesseract_available", lambda: False)
    assert ocr.choose_engine("auto", "heb") == "builtin"  # the best there is
    assert ocr.choose_engine("builtin", "heb") == "builtin"  # asked for by name


def test_core_ocr_lets_the_language_choose_the_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "builtin_available", lambda: True)
    monkeypatch.setattr(ocr, "tesseract_available", lambda: True)
    used = []
    monkeypatch.setattr(ocr, "_run_tesseract", lambda *a, **k: used.append("tesseract"))
    monkeypatch.setattr(ocr, "_run_builtin", lambda *a, **k: used.append("builtin"))
    monkeypatch.setattr(ocr.os, "replace", lambda a, b: None)
    scan = ocrpages.scan_pdf(Image.new("L", (100, 100), 255), tmp_path / "s.pdf", dpi=72)
    core.ocr(scan, tmp_path / "o.pdf", language="heb")
    core.ocr(scan, tmp_path / "o2.pdf", language="eng")
    assert used == ["tesseract", "builtin"]


@needs_builtin
def test_chinese_is_read_and_extractable(tmp_path):
    font = ocrpages.cjk_font()
    if font is None:
        pytest.skip("no Chinese font installed")
    text = "发票号码 2024-0117\n收货人：北京市朝阳区建国路 88 号\n合计金额 176.16 元"
    im = ocrpages.render(text, font_size=48, font_path=font)
    scan = ocrpages.scan_pdf(im, tmp_path / "scan.pdf")
    out = core.ocr(scan, tmp_path / "out.pdf", engine="builtin")
    got = core.extract_text(out)
    for word in ("发票号码", "北京市朝阳区", "合计金额", "176.16"):
        assert word in got, got


def test_huge_pages_are_rendered_no_larger_than_the_cap():
    assert ocr.render_scale(595, 842) == pytest.approx(ocr.RENDER_DPI / 72)
    scale = ocr.render_scale(5000, 7000)   # an E-size drawing
    assert 7000 * scale <= ocr.MAX_RENDER_SIDE and 7000 * scale > ocr.MAX_RENDER_SIDE - 1


# -- beyond Acrobat: tables, small print, page orientation, columns, scripts ---

def _end_to_end(tmp_path, image, **options):
    scan = ocrpages.scan_pdf(image, tmp_path / "scan.pdf")
    out = core.ocr(scan, tmp_path / "out.pdf", engine="builtin", **options)
    return out, core.extract_text(out)


@needs_builtin
def test_a_ruled_table_is_read_exactly(tmp_path):
    # The model's per-line flip check took a lone "6" for an upside-down "9".
    image, truth = ocrpages.ruled_table()
    _, text = _end_to_end(tmp_path, image)
    assert ocrpages.cer(truth, text) == 0, text


@needs_builtin
def test_7pt_print_is_read(tmp_path):
    image, truth = ocrpages.small_print()
    _, text = _end_to_end(tmp_path, image)
    assert ocrpages.cer(truth, text) < 0.01


@needs_builtin
@pytest.mark.parametrize("turned, turn_back", [(90, 270), (180, 180), (270, 90)])
def test_page_orientation_says_how_to_turn_a_page_upright(turned, turn_back):
    image, _ = ocrpages.letter()
    assert ocr.page_orientation(image.rotate(turned, expand=True)) == turn_back


@needs_builtin
def test_an_upright_or_empty_page_needs_no_turn():
    image, _ = ocrpages.letter()
    assert ocr.page_orientation(image) == 0
    assert ocr.page_orientation(Image.new("L", (800, 1100), 255)) == 0


@needs_builtin
@pytest.mark.parametrize("turned", [90, 180, 270])
def test_sideways_and_upside_down_pages_are_turned_upright(tmp_path, turned):
    image, truth = ocrpages.letter()
    out, text = _end_to_end(tmp_path, image.rotate(turned, expand=True))
    assert ocrpages.cer(truth, text) < 0.01
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(out))
    shown = doc[0].render(scale=0.5).to_pil().convert("L")
    doc.close()
    assert shown.width < shown.height  # displayed as the portrait letter it is
    assert ocr.page_orientation(shown.resize((shown.width * 2, shown.height * 2))) == 0


@needs_builtin
def test_a_page_shown_sideways_is_turned_back(tmp_path):
    # An upright scan whose page says /Rotate 90: turned back to 0, not 360.
    scan = clean_scan(tmp_path)
    w = PdfWriter(clone_from=str(scan))
    w.pages[0].rotate(90)
    turned = tmp_path / "turned.pdf"
    w.write(turned)
    out = core.ocr(turned, tmp_path / "out.pdf", engine="builtin")
    page = PdfReader(out).pages[0]
    assert page.rotation == 0 and "Northwind" in page.extract_text()


@needs_builtin
def test_turning_pages_can_be_switched_off(tmp_path):
    image, _ = ocrpages.letter()
    out, _ = _end_to_end(tmp_path, image.rotate(180), rotate=False)
    assert PdfReader(out).pages[0].rotation == 0


@needs_builtin
def test_upright_pages_are_not_checked_for_orientation(tmp_path, monkeypatch):
    def not_needed(*args, **kwargs):
        raise AssertionError("an upright page was checked")

    monkeypatch.setattr(ocr, "page_orientation", not_needed)
    image, _ = ocrpages.letter()
    _end_to_end(tmp_path, image)


@needs_builtin
def test_two_columns_are_read_column_by_column(tmp_path):
    image, truth = ocrpages.two_columns()
    _, text = _end_to_end(tmp_path, image)
    assert ocrpages.cer(truth, text) < 0.01, text


def _line(text, x0, y0, x1, y1):
    words, x, step = [], x0, (x1 - x0) / max(len(text), 1)
    for word in text.split():
        w1 = x + step * len(word)
        words.append(ocr.Word(word, [(x, y0), (w1, y0), (w1, y1), (x, y1)]))
        x = w1 + step
    return ocr.Line(text, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], 1.0, words)


def test_reading_order_follows_columns_under_a_heading():
    lines = [_line("Heading across both columns", 200, 100, 1800, 160)]
    for i in range(6):
        y = 300 + i * 60
        lines.append(_line(f"left column line {i} with several words", 200, y, 1150, y + 40))
        lines.append(_line(f"right column line {i} with several words", 1250, y, 2200, y + 40))
    lines.append(_line("A footer that spans the page width", 200, 800, 2200, 840))
    text = ocr.text_of(ocr.reading_order(lines)).splitlines()
    assert text[0] == "Heading across both columns"
    assert text[1:7] == [f"left column line {i} with several words" for i in range(6)]
    assert text[7:13] == [f"right column line {i} with several words" for i in range(6)]
    assert text[13] == "A footer that spans the page width"


def test_reading_order_keeps_table_rows_together():
    lines = []
    for r, row in enumerate(ocrpages.TABLE):
        for c, cell in enumerate(row):
            if cell:
                x = [270, 1070, 1370, 1820][c]
                lines.append(_line(cell, x, 420 + r * 90, x + 30 * len(cell), 460 + r * 90))
    text = ocr.text_of(ocr.reading_order(lines))
    assert text.splitlines() == [" ".join(c for c in row if c) for row in ocrpages.TABLE]


def test_language_codes_pick_a_reading_model():
    assert ocr.script_for("eng") is None and ocr.script_for("eng+deu+chi_sim+jpn") is None
    assert ocr.script_for("rus") == "eslav" and ocr.script_for("eng+ukr") == "eslav"
    assert ocr.script_for("bul") == "cyrillic"
    assert ocr.script_for("kor") == "korean"
    assert ocr.script_for("ara+eng") == "arabic" and ocr.script_for("fas") == "arabic"
    assert ocr.script_for("hin") == "devanagari"
    assert ocr.script_for("tha") == "th" and ocr.script_for("ell") == "el"
    assert ocr.script_for("tam") == "ta" and ocr.script_for("tel") == "te"
    for unreadable in ("heb", "kor+ara", "amh"):
        with pytest.raises(ValueError):
            ocr.script_for(unreadable)


def test_auto_reads_more_scripts_itself(monkeypatch):
    monkeypatch.setattr(ocr, "builtin_available", lambda: True)
    monkeypatch.setattr(ocr, "tesseract_available", lambda: True)
    assert ocr.choose_engine("auto", "rus") == "builtin"
    assert ocr.choose_engine("auto", "kor") == "builtin"
    assert ocr.choose_engine("auto", "heb") == "tesseract"
    assert ocr.choose_engine("auto", "kor+ara") == "tesseract"


@pytest.fixture
def stand_in_models(tmp_path, monkeypatch):
    """Language models 'downloaded' from a local copy of the bundled model
    (the real ones come from the internet), into a scratch folder."""
    import hashlib

    import rapidocr

    stand_in = Path(rapidocr.__file__).parent / "models" / "PP-OCRv6_rec_small.onnx"
    digest = hashlib.sha256(stand_in.read_bytes()).hexdigest()
    fetched = []
    monkeypatch.setattr(ocr, "models_dir", lambda: tmp_path / "models")
    monkeypatch.setattr(ocr, "_model_source", lambda script: (f"https://example.invalid/{script}.onnx", digest))

    def fetch(url, target):
        fetched.append(url)
        target.write_bytes(stand_in.read_bytes())

    monkeypatch.setattr(ocr, "_fetch", fetch)
    ocr.reset()
    yield fetched
    ocr.reset()


@needs_builtin
def test_a_language_model_is_fetched_once_and_used(tmp_path, stand_in_models):
    image, _ = ocrpages.letter()
    for _ in range(2):
        lines = ocr.recognise(image, script="eslav")
        assert "committee" in ocr.text_of(lines)
    assert stand_in_models == ["https://example.invalid/eslav.onnx"]
    assert (tmp_path / "models" / "eslav.onnx").exists()


@needs_builtin
def test_a_damaged_download_is_refused(tmp_path, stand_in_models, monkeypatch):
    monkeypatch.setattr(ocr, "_fetch", lambda url, target: target.write_bytes(b"not a model"))
    with pytest.raises(core.PdfManagerError, match="damaged"):
        ocr.recognise(ocrpages.letter()[0], script="korean")
    assert not list((tmp_path / "models").glob("*"))


@needs_builtin
def test_no_internet_for_a_language_model_says_what_to_do(stand_in_models, monkeypatch):
    def offline(url, target):
        raise OSError("Network is unreachable")

    monkeypatch.setattr(ocr, "_fetch", offline)
    with pytest.raises(core.PdfManagerError, match="Korean.*internet.*Tesseract"):
        ocr.recognise(ocrpages.letter()[0], script="korean")


@needs_builtin
def test_auto_turns_to_tesseract_when_a_language_model_cannot_be_fetched(tmp_path, stand_in_models, monkeypatch):
    def offline(url, target):
        raise OSError("Network is unreachable")

    monkeypatch.setattr(ocr, "_fetch", offline)
    monkeypatch.setattr(ocr, "tesseract_available", lambda: True)
    used = []
    monkeypatch.setattr(ocr, "_run_tesseract", lambda source, output, *a, **k: used.append("tesseract") or Path(output).write_bytes(Path(source).read_bytes()))
    scan = ocrpages.scan_pdf(ocrpages.letter()[0], tmp_path / "scan.pdf")
    core.ocr(scan, tmp_path / "out.pdf", language="rus")
    assert used == ["tesseract"]


def test_tesseract_turns_pages_only_when_it_can(tmp_path, monkeypatch):
    # OCRmyPDF's page turning needs Tesseract's osd data; without it, OCR
    # must still work, just without turning pages.
    import sys
    import types

    calls = []
    fake = types.ModuleType("ocrmypdf")
    fake.ocr = lambda *a, **k: calls.append(k)
    exceptions = types.ModuleType("ocrmypdf.exceptions")
    exceptions.ExitCodeException = exceptions.MissingDependencyError = type("E", (Exception,), {})
    monkeypatch.setitem(sys.modules, "ocrmypdf", fake)
    monkeypatch.setitem(sys.modules, "ocrmypdf.exceptions", exceptions)
    monkeypatch.setattr(ocr.scan, "add_programs_to_path", lambda: None)
    for languages, expected in (("eng\nosd", True), ("eng", False)):
        monkeypatch.setattr(ocr, "_tesseract_languages", lambda: set(languages.split()))
        ocr._run_tesseract(tmp_path / "in.pdf", tmp_path / "out.pdf", "eng", False, False, rotate=True)
        assert calls[-1]["rotate_pages"] is expected


@needs_builtin
@pytest.mark.parametrize("script", sorted(ocr.SCRIPT_LANGUAGES))
def test_every_script_model_is_an_onnx_model_rapidocr_lists(script):
    # ZPDF runs the models on ONNX Runtime: the address must be of the ONNX
    # file, not of the same model built for another engine.
    url, digest = ocr._model_source(script)
    assert url.endswith(f"/onnx/PP-OCRv5/rec/{script}_PP-OCRv5_rec_mobile.onnx"), url
    assert len(digest) == 64
