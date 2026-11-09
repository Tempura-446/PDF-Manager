# Built-in OCR Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make scanned PDFs searchable with PaddleOCR's PP-OCR models (via `rapidocr` on ONNX Runtime) and ZPDF's own invisible text layer, keeping Tesseract/OCRmyPDF as a second engine.

**Architecture:** A new `pdfmanager/ocr.py` owns engine choice, recognition (RapidOCR, one process-wide instance), skew detection and the per-page pipeline; `pdfmanager/textlayer.py` writes the invisible text layer with a glyphless Type0 font using pypdf's generic objects; `core.ocr` keeps its signature plus `engine=` and delegates. GUI, CLI, watch folder and recipes gain an `engine` option; packaging bundles the engine.

**Tech Stack:** Python 3.10+, rapidocr ≥ 3.10, onnxruntime ≥ 1.17, pypdf 7, pypdfium2, Pillow, pdfplumber (tests), pytest.

**Spec:** `docs/superpowers/specs/2026-11-09-builtin-ocr-engine-design.md`

## Global Constraints

- Python ≥ 3.10; no syntax or stdlib newer than 3.10 (the CI runs 3.10 and 3.13).
- New dependencies only in the `ocr` extra: `rapidocr>=3.10`, `onnxruntime>=1.17`; `ocrmypdf>=15` moves to a new `tesseract` extra; `all` and `build` include `ocr`.
- Every import of `rapidocr`, `onnxruntime` or `ocrmypdf` is lazy (inside functions): the core library and the test suite must import without them.
- Errors for the user are `core.PdfManagerError` (GUI: `gui.UserError` where the GUI already raises it); messages are plain sentences, no tracebacks.
- Long loops report with `progress.report(n, total, "page")` so Cancel works.
- Text in the code and UI follows the project's voice (plain words, no jargon beyond "OCR"); comments explain why, not what.
- Commit messages end with the two attribution lines given in the session; no model names in code, commits or docs.
- Default engine is `"auto"`; engine ids are exactly `"auto"`, `"builtin"`, `"tesseract"`.
- Rendering for recognition is 300 dpi; words with confidence below 0.3 are dropped; skew is only corrected when at least 3 wide boxes agree and the median angle is ≥ 0.3°.

## Review Focus

- A scan whose pages are JPEG photos (colour, 3000 px wide): recognition must still finish in seconds per page and the output must not grow (the built-in engine never re-encodes a page unless `force`). Pinned in Task 3 (`test_builtin_keeps_the_page_image_untouched`).
- A page with no text at all (a photo, a blank): no words, no crash, page copied unchanged. Pinned in Task 3 (`test_builtin_leaves_pages_without_words_alone`).
- A PDF mixing text pages and scanned pages: only scanned pages get a layer; the text pages' existing text is not duplicated. Pinned in Task 3 (`test_builtin_skips_pages_that_already_have_text`).
- Cancelling from the app mid-file: the output must not be left half-written. Pinned in Task 3 (`test_builtin_cancel_leaves_no_output`).
- An encrypted input with a password: works as before for both engines. Pinned in Task 3 (`test_builtin_opens_encrypted_input`).

---

### Task 1: Glyphless text layer (`textlayer.py`)

**Files:**
- Create: `pdfmanager/textlayer.py`
- Create: `tests/test_textlayer.py`

**Interfaces:**
- Consumes: nothing new. Uses `pypdf.generic` (`DictionaryObject`, `NameObject`, `ArrayObject`, `NumberObject`, `StreamObject`, `DecodedStreamObject`), `pypdf.PageObject`, `pypdf.Transformation`, reportlab's `fonts/Vera.ttf` (path: `Path(reportlab.rl_config.__file__).parent / "fonts" / "Vera.ttf"`).
- Produces:
  - `@dataclass class Word: text: str; quad: list[tuple[float, float]]; confidence: float = 1.0` — quad is 4 points in image pixels, order top-left, top-right, bottom-right, bottom-left, y down. (Defined here; `ocr.py` re-exports it.)
  - `image_to_page_matrix(page, image_size, dpi, skew=0.0) -> tuple[float, float, float, float, float, float]` — the PDF matrix `(a, b, c, d, e, f)` taking display points (origin bottom-left of the rendered image, y up) to the page's user space, undoing `/Rotate` and the crop box offset, and the deskew rotation `skew` (degrees, the angle the image was turned by before recognition) about the crop box centre.
  - `add_text_layer(writer, page, words, image_size, dpi, skew=0.0) -> int` — merges an invisible layer onto `page` (a page of `writer`, a `pypdf.PdfWriter`), returns the number of words written. Registers the glyphless font once per writer (cache it on the writer object under the attribute `_zpdf_ocr_font`).
  - `GLYPHLESS_FONT_RESOURCE = "/FZPDFOCR"` — the resource name used in content streams.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_textlayer.py
import io
import pdfplumber
import pytest
from pypdf import PdfReader, PdfWriter
from pdfmanager import core, textlayer
from pdfmanager.textlayer import Word

def page_with_word(tmp_path, rotate=0, crop=None):
    """A page with the visible word 'Hello' at (100, 700)-(160, 720) in page points."""
    pdf = tmp_path / "src.pdf"
    c = core.pdf_canvas(str(pdf), pagesize=(595, 842))
    c.setFont("Helvetica", 20); c.drawString(100, 705, "Hello"); c.save()
    w = PdfWriter(clone_from=str(pdf)); p = w.pages[0]
    if rotate: p.rotate(rotate)
    if crop: p.cropbox = crop
    return w, p

def rendered(path, dpi=100):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(path)); im = doc[0].render(scale=dpi / 72).to_pil(); doc.close()
    return im

def ink_box(im):
    return im.convert("L").point(lambda v: 255 if v < 128 else 0).getbbox()  # (x0, y0, x1, y1) px

def test_words_are_extractable_and_invisible(tmp_path):
    w, p = page_with_word(tmp_path)
    words = [Word("Hello", [(100, 0), (300, 0), (300, 50), (100, 50)]),
             Word("发票", [(400, 0), (500, 0), (500, 50), (400, 50)]),
             Word("Fälligkeit€", [(100, 100), (300, 100), (300, 150), (100, 150)])]
    assert textlayer.add_text_layer(w, p, words, (827, 1169), 100) == 3
    out = tmp_path / "out.pdf"; w.write(out)
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
        textlayer.add_text_layer(w, page, [Word("x", [(0, 0), (10, 0), (10, 10), (0, 10)])], (827, 1169), 100)
    out = tmp_path / "out.pdf"; w.write(out)
    r = PdfReader(out)
    fonts = {r.pages[i]["/Resources"]["/Font"][textlayer.GLYPHLESS_FONT_RESOURCE].indirect_reference for i in range(2)}
    assert len(fonts) == 1
    font = r.pages[0]["/Resources"]["/Font"][textlayer.GLYPHLESS_FONT_RESOURCE].get_object()
    assert font["/Subtype"] == "/Type0" and font["/Encoding"] == "/Identity-H"
    assert "/FontFile2" in font["/DescendantFonts"][0].get_object()["/FontDescriptor"]

@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_layer_lands_on_the_visible_word(tmp_path, rotate):
    w, p = page_with_word(tmp_path, rotate=rotate)
    src = tmp_path / "rot.pdf"; w.write(src)
    im = rendered(src, dpi=100)
    x0, y0, x1, y1 = ink_box(im)
    w2 = PdfWriter(clone_from=str(src)); p2 = w2.pages[0]
    textlayer.add_text_layer(w2, p2, [Word("HELLO", [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])], im.size, 100)
    out = tmp_path / "out.pdf"; w2.write(out)
    with pdfplumber.open(out) as pdf:
        words = {wd["text"]: wd for wd in pdf.pages[0].extract_words()}
    a, b = words["Hello"], words["HELLO"]
    for key in ("x0", "x1", "top", "bottom"):
        assert abs(a[key] - b[key]) < 3, (key, a[key], b[key])

def test_layer_follows_a_shifted_crop_box(tmp_path):
    from pypdf.generic import RectangleObject
    w, p = page_with_word(tmp_path, crop=RectangleObject((50, 600, 400, 800)))
    src = tmp_path / "crop.pdf"; w.write(src)
    im = rendered(src, dpi=100); x0, y0, x1, y1 = ink_box(im)
    w2 = PdfWriter(clone_from=str(src)); p2 = w2.pages[0]
    textlayer.add_text_layer(w2, p2, [Word("HELLO", [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])], im.size, 100)
    out = tmp_path / "out.pdf"; w2.write(out)
    with pdfplumber.open(out) as pdf:
        words = {wd["text"]: wd for wd in pdf.pages[0].extract_words()}
    assert abs(words["Hello"]["x0"] - words["HELLO"]["x0"]) < 3
    assert abs(words["Hello"]["top"] - words["HELLO"]["top"]) < 3

def test_skew_is_undone(tmp_path):
    # The image was turned 2 degrees before recognition; a word found at the
    # image centre must land at the page centre either way.
    w, p = page_with_word(tmp_path)
    m0 = textlayer.image_to_page_matrix(p, (827, 1169), 100, skew=0.0)
    m2 = textlayer.image_to_page_matrix(p, (827, 1169), 100, skew=2.0)
    from pypdf import Transformation
    centre = (827 / 2 * 0.72, 1169 / 2 * 0.72)
    assert Transformation(m0).apply_on(centre) == pytest.approx(Transformation(m2).apply_on(centre), abs=0.01)
    corner0 = Transformation(m0).apply_on((0, 0)); corner2 = Transformation(m2).apply_on((0, 0))
    assert corner0 != pytest.approx(corner2, abs=1)

def test_non_bmp_characters_become_replacement_character(tmp_path):
    w, p = page_with_word(tmp_path)
    textlayer.add_text_layer(w, p, [Word("a😀b", [(0, 0), (30, 0), (30, 10), (0, 10)])], (827, 1169), 100)
    out = tmp_path / "out.pdf"; w.write(out)
    assert "a�b" in PdfReader(out).pages[0].extract_text()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_textlayer.py -q`
Expected: FAIL with `ImportError: cannot import name 'textlayer'`.

- [ ] **Step 3: Implement `pdfmanager/textlayer.py`**

Module docstring: why a glyphless font (one embedded font, any character, extraction via ToUnicode; what Tesseract's PDF output does). Then:

- `Word` dataclass as in Interfaces.
- `_glyphless_font(writer) -> IndirectObject`: builds once per writer and caches as `writer._zpdf_ocr_font`. Objects: Type0 font dict `{/Type /Font /Subtype /Type0 /BaseFont /GlyphLessFont /Encoding /Identity-H /DescendantFonts [cid] /ToUnicode cmap}`; CIDFontType2 dict `{/Type /Font /Subtype /CIDFontType2 /BaseFont /GlyphLessFont /CIDSystemInfo {/Registry (Adobe) /Ordering (Identity) /Supplement 0} /FontDescriptor fd /DW 500 /CIDToGIDMap gidmap}`; `gidmap` a Flate-compressed stream of 131072 zero bytes; `fd` a FontDescriptor `{/Type /FontDescriptor /FontName /GlyphLessFont /Flags 5 /FontBBox [0 0 500 1000] /ItalicAngle 0 /Ascent 1000 /Descent 0 /CapHeight 1000 /StemV 80 /FontFile2 file}` with `file` the Flate-compressed bytes of Vera.ttf and `/Length1` its raw length; `cmap` a stream with the standard ToUnicode header, `1 begincodespacerange <0000> <FFFF> endcodespacerange`, and `1 beginbfrange <0000> <FFFF> <0000> endbfrange` (CID = UCS-2 code). Add each via `writer._add_object(...)`.
- `_encode(text) -> bytes`: UTF-16BE of the text with characters above U+FFFF replaced by U+FFFD first.
- `image_to_page_matrix(page, image_size, dpi, skew=0.0)`: compose with `pypdf.Transformation`: scale `72/dpi` (pixels → points; the caller already flips y, see below), then undo `/Rotate` (`page.rotation % 360`; for 90: `(x, y) → (y, W_disp - x)`-style mapping expressed as a rotation plus translation so the display box lands on the crop box), translate by the crop box's lower-left, then rotate by `-skew` about the crop box centre. Return `.ctm`.
- `add_text_layer(writer, page, words, image_size, dpi, skew=0.0)`: build the content stream text: `BT 3 Tr /FZPDFOCR 1 Tf` then per word `a b c d e f Tm <hex> Tj` where the word's matrix maps the unit square onto the word's box in *display points* (flip y: `y_disp = image_h - y_px`, then × 72/dpi): `a = box_width / (0.5 * n_chars)`, `d = box_height`, `e, f` = bottom-left of the box, and for a skewed box (bottom edge not horizontal) rotate by the bottom edge's angle; then `ET`. Wrap into a `PageObject.create_blank_page`-sized page with that content and `/Resources /Font {/FZPDFOCR font}`, and merge with `page.merge_transformed_page(layer, Transformation(image_to_page_matrix(...)))`. Return `len(words)` after dropping words with empty text.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_textlayer.py -q`
Expected: all PASS. If `test_words_are_extractable_and_invisible` fails on pdfplumber reading `发票`, check the ToUnicode stream's bfrange (pdfminer needs the 3-token `beginbfrange` form above).

- [ ] **Step 5: Commit**

```bash
git add pdfmanager/textlayer.py tests/test_textlayer.py
git commit -m "Invisible OCR text layer with a glyphless font"
```

---

### Task 2: Recognition engine module (`ocr.py`: words, skew, engine choice)

**Files:**
- Create: `pdfmanager/ocr.py`
- Create: `tests/test_ocr.py` (first half)
- Create: `tests/ocrpages.py` (helper that makes synthetic scans; imported by tests only)

**Interfaces:**
- Consumes: `textlayer.Word`.
- Produces (in `pdfmanager/ocr.py`):
  - `Word = textlayer.Word` (re-export).
  - `@dataclass class Line: text: str; quad: list[tuple[float, float]]; confidence: float; words: list[Word]`.
  - `MIN_CONFIDENCE = 0.3`, `RENDER_DPI = 300`, `MIN_SKEW = 0.3`, `MIN_SKEW_BOXES = 3`.
  - `ENGINES = ("auto", "builtin", "tesseract")`.
  - `builtin_available() -> bool` (can `rapidocr` and `onnxruntime` be imported), `tesseract_available() -> bool` (can `ocrmypdf` be imported and `scan.find_tesseract()` finds the program).
  - `choose_engine(engine: str) -> str` — returns `"builtin"` or `"tesseract"`; raises `core.PdfManagerError` for an unknown id, for a named engine that is not available (message names what to install: `pip install pdf-manager[ocr]` for the built-in engine; `pip install pdf-manager[tesseract]` plus the Tesseract link for the other), and for `"auto"` when neither is available (message names both).
  - `recognise(image) -> list[Line]` — PIL image in; lines with words, both with image-pixel quads; lines and words below `MIN_CONFIDENCE` dropped.
  - `skew_angle(image) -> float` — degrees, positive = text rises to the right (counter-clockwise), 0.0 when undecided.
  - `reset()` — drops the cached engine (tests use it).

- [ ] **Step 1: Write the page helper**

`tests/ocrpages.py`: port `make_pages.py` from the benchmark: `TEXT` (the invoice text used in the benchmark, 14 lines incl. the German line), `render(text, font_size, serif=False) -> PIL.Image` (A4 at 300 dpi, DejaVu Sans/Serif with `ImageFont.load_default(size=...)` fallback), `degrade(im, angle=1.7, blur=1.6, noise=60000, seed=1) -> PIL.Image` (rotate, Gaussian blur, grey paper `90 + v*0.55`, salt-and-pepper), `lowres(im, dpi=110) -> PIL.Image`, and `scan_pdf(image, path, dpi=300) -> Path` using `core.images_to_pdf`. Also `cer(truth, text) -> float` and `missing_words(truth, text) -> float` (order-insensitive: fraction of truth words absent from the output), both whitespace-normalised, implemented with `difflib.SequenceMatcher` (no `jiwer` dependency).

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_ocr.py
import pytest
from PIL import Image
from pdfmanager import core, ocr
from tests import ocrpages

needs_builtin = pytest.mark.skipif(not ocr.builtin_available(), reason="needs rapidocr")

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

@needs_builtin
def test_recognise_gives_lines_and_words_with_boxes():
    im = ocrpages.render(ocrpages.TEXT, 44)
    lines = ocr.recognise(im)
    assert len(lines) >= 12
    texts = " ".join(l.text for l in lines)
    assert "Northwind" in texts and "176.16" in texts
    line = next(l for l in lines if "Unit price" in l.text)
    assert [w.text for w in line.words][:3] == ["Item", "Qty", "Unit"]
    for w in line.words:
        assert len(w.quad) == 4 and 0 <= w.quad[0][0] < im.width and w.confidence >= ocr.MIN_CONFIDENCE

@needs_builtin
def test_recognise_reads_a_degraded_page_well():
    im = ocrpages.degrade(ocrpages.render(ocrpages.TEXT, 40, serif=True))
    text = "\n".join(l.text for l in ocr.recognise(im))
    assert ocrpages.cer(ocrpages.TEXT, text) < 0.06
    assert ocrpages.missing_words(ocrpages.TEXT, text) < 0.3

@needs_builtin
def test_recognise_handles_a_page_without_text():
    assert ocr.recognise(Image.new("L", (800, 1000), 255)) == []

@needs_builtin
def test_skew_angle_finds_a_tilted_page_and_ignores_a_straight_one():
    straight = ocrpages.render(ocrpages.TEXT, 44)
    assert ocr.skew_angle(straight) == 0.0
    tilted = straight.rotate(2.0, resample=Image.BICUBIC, fillcolor=255)  # counter-clockwise
    assert ocr.skew_angle(tilted) == pytest.approx(2.0, abs=0.4)
    assert ocr.skew_angle(Image.new("L", (800, 1000), 255)) == 0.0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_ocr.py -q`
Expected: FAIL with `ImportError: cannot import name 'ocr'`.

- [ ] **Step 4: Implement `pdfmanager/ocr.py` (first half)**

- Module docstring: the two engines and why (built-in reads degraded scans and many languages with nothing to install; Tesseract stays for PDF/A output and other scripts).
- `builtin_available()`: `importlib.util.find_spec("rapidocr")` and `find_spec("onnxruntime")`.
- `tesseract_available()`: `find_spec("ocrmypdf")` and `scan.find_tesseract()`.
- `choose_engine(engine)`: messages exactly:
  - unknown: `f"unknown OCR engine {engine!r}; choose auto, builtin or tesseract"`
  - builtin missing: `"OCR's built-in engine isn't installed: pip install pdf-manager[ocr]"`
  - tesseract missing: `"OCR with Tesseract needs OCRmyPDF and the Tesseract program: pip install pdf-manager[tesseract], then install Tesseract (https://tesseract-ocr.github.io/tessdoc/Installation.html)"`
  - auto, neither: `"OCR needs an engine: pip install pdf-manager[ocr] for the built-in one, or OCRmyPDF and Tesseract (https://tesseract-ocr.github.io/tessdoc/Installation.html)"`
- `_engine()`: lazily builds `RapidOCR(params={"Global.return_word_box": True})` under a `threading.Lock`, after `logging.getLogger("RapidOCR").setLevel(logging.WARNING)` (rapidocr's logger name; confirm with `rapidocr.utils.logger`), caches in a module global; `reset()` clears it.
- `recognise(image)`: `result = _engine()(numpy.asarray(image.convert("RGB")))`; `result.txts` may be `None` → `[]`. For each line i: `Line(txts[i], [tuple(p) for p in boxes[i]], scores[i], words)`, words from `result.word_results[i]` (tuples `(text, score, quad)`), filtering by `MIN_CONFIDENCE` at both levels; a line whose words are all filtered out is dropped too; a line with no word results gets one `Word` spanning the line.
- `skew_angle(image)`: `_engine()(array, use_det=True, use_cls=False, use_rec=False).boxes`; for boxes whose width > 2 × height, the angle of the top edge `atan2(y1 - y0, x1 - x0)` in degrees, negated so counter-clockwise visual tilt is positive (image y grows downward); need ≥ `MIN_SKEW_BOXES`; return `0.0` if `abs(median) < MIN_SKEW` else `round(median, 2)`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_ocr.py -q`
Expected: all PASS (the `needs_builtin` ones run here because the build venv has rapidocr; run with the venv's python if the default interpreter lacks it).

- [ ] **Step 6: Commit**

```bash
git add pdfmanager/ocr.py tests/test_ocr.py tests/ocrpages.py
git commit -m "OCR engine module: PP-OCR recognition, skew angle, engine choice"
```

---

### Task 3: Built-in OCR pipeline and `core.ocr(engine=)`

**Files:**
- Modify: `pdfmanager/ocr.py` (add `run`, `_run_builtin`, `_run_tesseract`, `page_has_text`, `_rasterise`)
- Modify: `pdfmanager/core.py:1681-1721` (`ocr` becomes a thin delegate with `engine="auto"`)
- Modify: `tests/test_ocr.py` (second half), `tests/test_upgrades.py:329-345` (`engine="tesseract"`)

**Interfaces:**
- Consumes: Task 1 `textlayer.add_text_layer`, `textlayer.image_to_page_matrix`; Task 2 `recognise`, `skew_angle`, `choose_engine`, `RENDER_DPI`.
- Produces:
  - `ocr.run(path, output, language="eng", force=False, deskew=False, password=None, engine="auto") -> Path`.
  - `core.ocr(path, output, language="eng", force=False, deskew=False, password=None, engine="auto") -> Path` — unchanged behaviour for callers that pass no engine, except that `auto` now prefers the built-in engine.
  - `ocr.page_has_text(page) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ocr.py (append)
from pypdf import PdfReader
import pdfplumber
from pdfmanager import progress

@needs_builtin
@pytest.mark.parametrize("kind, ceiling", [("clean", 0.01), ("degraded", 0.06), ("lowres", 0.01)])
def test_builtin_makes_a_scan_searchable(tmp_path, kind, ceiling):
    im = ocrpages.render(ocrpages.TEXT, 44)
    if kind == "degraded": im = ocrpages.degrade(ocrpages.render(ocrpages.TEXT, 40, serif=True))
    if kind == "lowres": im = ocrpages.lowres(im)
    scan = ocrpages.scan_pdf(im, tmp_path / "scan.pdf", dpi=110 if kind == "lowres" else 300)
    assert core.extract_text(scan).strip() == ""
    out = core.ocr(scan, tmp_path / "out.pdf", engine="builtin")
    text = core.extract_text(out)
    assert ocrpages.cer(ocrpages.TEXT, text) < ceiling
    assert "Northwind" in text

@needs_builtin
def test_builtin_words_sit_on_the_printed_words(tmp_path):
    im = ocrpages.render(ocrpages.TEXT, 44)
    scan = ocrpages.scan_pdf(im, tmp_path / "scan.pdf")
    out = core.ocr(scan, tmp_path / "out.pdf", engine="builtin")
    with pdfplumber.open(out) as pdf:
        page = pdf.pages[0]
        word = next(w for w in page.extract_words() if w["text"] == "Northwind")
        # The word's ink in the page image lies inside the word's box (plus 3 pt).
        crop = page.crop((word["x0"] - 3, word["top"] - 3, word["x1"] + 3, word["bottom"] + 3)).to_image(resolution=72).original
        assert crop.convert("L").getextrema()[0] < 128  # there is ink there
        outside = page.crop((word["x1"] + 6, word["top"], word["x1"] + 30, word["bottom"])).to_image(resolution=72).original
        assert outside.convert("L").getextrema()[0] > 200  # and none right after it

@needs_builtin
def test_builtin_skips_pages_that_already_have_text(tmp_path):
    im = ocrpages.render(ocrpages.TEXT, 44)
    scan = ocrpages.scan_pdf(im, tmp_path / "scan.pdf")
    typed = tmp_path / "typed.pdf"
    c = core.pdf_canvas(str(typed)); c.drawString(72, 720, "Typed page"); c.save()
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
    im = ocrpages.render(ocrpages.TEXT, 44).convert("RGB")
    scan = ocrpages.scan_pdf(im, tmp_path / "scan.pdf")
    out = core.ocr(scan, tmp_path / "out.pdf", engine="builtin")
    before = PdfReader(scan).pages[0].images[0].image.tobytes()
    after = PdfReader(out).pages[0].images[0].image.tobytes()
    assert before == after
    assert out.stat().st_size < scan.stat().st_size + 80_000  # the font and the layer

@needs_builtin
def test_force_replaces_an_old_text_layer(tmp_path):
    im = ocrpages.render(ocrpages.TEXT, 44)
    scan = ocrpages.scan_pdf(im, tmp_path / "scan.pdf")
    w = PdfWriter(clone_from=str(scan))
    textlayer.add_text_layer(w, w.pages[0], [textlayer.Word("WRONGTEXT", [(10, 10), (400, 10), (400, 60), (10, 60)])], (2480, 3508), 300)
    bad = tmp_path / "bad.pdf"; w.write(bad)
    assert "WRONGTEXT" in core.extract_text(bad)
    kept = core.ocr(bad, tmp_path / "kept.pdf", engine="builtin")
    assert "WRONGTEXT" in core.extract_text(kept) and "Northwind" not in core.extract_text(kept)
    out = core.ocr(bad, tmp_path / "out.pdf", engine="builtin", force=True)
    text = core.extract_text(out)
    assert "WRONGTEXT" not in text and "Northwind" in text
    assert out.stat().st_size < 2 * scan.stat().st_size

@needs_builtin
def test_deskew_straightens_a_crooked_scan(tmp_path):
    im = ocrpages.render(ocrpages.TEXT, 44).rotate(2.0, resample=Image.BICUBIC, fillcolor=255)
    scan = ocrpages.scan_pdf(im, tmp_path / "scan.pdf")
    out = core.ocr(scan, tmp_path / "out.pdf", engine="builtin", deskew=True)
    assert "Northwind" in core.extract_text(out)
    with pdfplumber.open(out) as pdf:
        words = [w for w in pdf.pages[0].extract_words() if w["text"] in ("Invoice", "2024")]
        first, last = min(words, key=lambda w: w["x0"]), max(words, key=lambda w: w["x0"])
        assert abs(first["top"] - last["top"]) < 4  # the words of one line are level again
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(out)); page_im = doc[0].render(scale=0.5).to_pil().convert("L"); doc.close()
    assert ocr.skew_angle(page_im.resize((page_im.width * 2, page_im.height * 2))) == 0.0

@needs_builtin
def test_builtin_opens_encrypted_input(tmp_path):
    im = ocrpages.render(ocrpages.TEXT, 44)
    scan = ocrpages.scan_pdf(im, tmp_path / "scan.pdf")
    locked = core.encrypt(scan, tmp_path / "locked.pdf", "pw")
    out = core.ocr(locked, tmp_path / "out.pdf", engine="builtin", password="pw")
    assert "Northwind" in core.extract_text(out)

@needs_builtin
def test_builtin_cancel_leaves_no_output(tmp_path, monkeypatch):
    im = ocrpages.render(ocrpages.TEXT, 44)
    scan = ocrpages.scan_pdf(im, tmp_path / "scan.pdf")
    both = core.merge([scan, scan], tmp_path / "both.pdf")
    job = progress.Job()
    def cancel_after_first(image):
        job.cancel(); return []
    monkeypatch.setattr(ocr, "recognise", cancel_after_first)
    job.run(core.ocr, both, tmp_path / "out.pdf", engine="builtin")
    assert job.state == "cancelled"
    assert not (tmp_path / "out.pdf").exists() and not list(tmp_path.glob("*.tmp*"))

def test_core_ocr_passes_the_engine_through(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(ocr, "run", lambda *a, **k: seen.update(k) or tmp_path / "x.pdf")
    core.ocr(tmp_path / "a.pdf", tmp_path / "x.pdf", engine="tesseract", language="deu")
    assert seen["engine"] == "tesseract" and seen["language"] == "deu"
```

Also in `tests/test_upgrades.py::test_ocr` pass `engine="tesseract"` and in `test_ocr_missing_dependency_message` (read it first) make the fake `ocrmypdf` import failure go through `ocr.choose_engine("tesseract")`'s message (update the expected text to the Task 2 wording).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_ocr.py tests/test_upgrades.py -k "ocr" -q`
Expected: new tests FAIL (`TypeError: ocr() got an unexpected keyword argument 'engine'`).

- [ ] **Step 3: Implement the pipeline**

In `pdfmanager/ocr.py`:
- `page_has_text(page)`: `bool(page.extract_text().strip())` (wrap in `try/except Exception: return False`).
- `_run_tesseract(source, output, language, force, deskew)`: the body of today's `core.ocr` from `import ocrmypdf` to the `ocrmypdf.ocr(...)` call and its two `except` clauses (move it; keep the messages).
- `_run_builtin(source, output, force, deskew, password)`: `writer = PdfWriter(clone_from=str(source))` (opened with `password` when encrypted — reuse `core.open_pdf` conventions); `pdfium` document opened once under `core.PDFIUM_LOCK` (pattern at `core.py:1735-1760`); per page `progress.report(i, n, "page")`; skip if `page_has_text` and not `force`; render `doc[i].render(scale=RENDER_DPI / 72).to_pil()`; if `deskew`: `angle = skew_angle(image)`, if `angle`: `image = image.rotate(angle, resample=Image.BICUBIC, fillcolor="white")` and rotate the page's content by the same angle about its crop box centre with `page.add_transformation(Transformation().translate(-cx, -cy).rotate(angle).translate(cx, cy))` (sign checked by `test_deskew_straightens_a_crooked_scan`); `lines = recognise(image)`; `words = [w for l in lines for w in l.words]`; if `force`: `_rasterise(writer, page, image)` first; if words: `textlayer.add_text_layer(writer, page, words, image.size, RENDER_DPI, skew=angle)`. Write to `output` via the same tmp-then-`os.replace` pattern. On `progress.Cancelled` (and any exception), delete the tmp file and re-raise.
- `_rasterise(writer, page, image)`: replace the page's content with the image at the page's display size: build one page with `core.images_to_pdf([tmp_png], tmp_pdf, size=(w_pt, h_pt), margin=0)` → copy its `/Contents` and `/Resources` onto `page`, set `/Rotate 0` and media/crop box to `(0, 0, w_pt, h_pt)`. Save the image as JPEG quality 85 when it is colour or grey with more than 2 % of pixels neither near-white nor near-black, else as a 1-bit PNG (`image.convert("1")`) so black-and-white scans stay small.
- `run(...)`: decrypt-to-temp when `core.open_pdf(path, password).is_encrypted` (today's code), `engine = choose_engine(engine)`, `scan.add_programs_to_path()` for Tesseract, `progress.say("recognising text (this can take a while)")`, dispatch, clean temps in `finally`, return `Path(output)`.

In `pdfmanager/core.py`, replace the body of `ocr` with a docstring update ("engine: auto, builtin or tesseract …") and `from pdfmanager import ocr as _ocr; return _ocr.run(path, output, language=language, force=force, deskew=deskew, password=password, engine=engine)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_ocr.py tests/test_textlayer.py tests/test_upgrades.py -k "ocr or textlayer" -q`
Expected: all PASS; the Tesseract test passes here (Tesseract 5.3 and ocrmypdf are available in the system Python; skip is fine in the build venv).

- [ ] **Step 5: Commit**

```bash
git add pdfmanager/ocr.py pdfmanager/core.py tests/test_ocr.py tests/test_upgrades.py
git commit -m "Built-in OCR: recognise pages, write the text layer, keep Tesseract as an engine"
```

---

### Task 4: Engine option in the GUI, CLI, watch folder and recipes

**Files:**
- Modify: `pdfmanager/gui.py:220-223` (tool fields), `pdfmanager/gui.py:623-626` (dispatch)
- Modify: `pdfmanager/cli.py:281-282`, `pdfmanager/cli.py:1330-1333`, `pdfmanager/cli.py:1627-1629`, `pdfmanager/cli.py:1070` (watch settings)
- Modify: `pdfmanager/watch.py:28-29`, `pdfmanager/watch.py:78-79`
- Modify: `pdfmanager/recipes.py` (only if step options are validated per op; read `STEP_OPS` handling first)
- Modify: `tests/test_features.py` (GUI and recipe), `tests/test_audit_tools.py` or the CLI test module that covers `ocr` (grep `"ocr"` in tests)

**Interfaces:**
- Consumes: `core.ocr(..., engine=)`, `ocr.ENGINES`.
- Produces: GUI option key `engine` (select, default `auto`, choices `[["auto", "Automatic (built-in when installed, else Tesseract)"], ["builtin", "Built-in"], ["tesseract", "Tesseract"]]`); CLI `--engine {auto,builtin,tesseract}` on `ocr` and `watch`; `WatchSettings.ocr_engine: str = "auto"`; recipe step option `engine=`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_features.py (append near the other OCR/GUI tests)
def test_ocr_tool_offers_an_engine_choice_and_passes_it_on(tmp_path, monkeypatch):
    from pdfmanager import gui, core
    spec = next(op for op in gui.OPERATIONS if op["id"] == "ocr")
    engine = next(f for f in spec["fields"] if f["key"] == "engine")
    assert engine["default"] == "auto" and [c[0] for c in engine["choices"]] == ["auto", "builtin", "tesseract"]
    assert "language" in [f["key"] for f in spec["fields"]]
    seen = {}
    def fake(src, out, **kw):
        seen.update(kw); Path(out).write_bytes(Path(src).read_bytes()); return out
    monkeypatch.setattr(core, "ocr", fake)
    src = make_pdf(tmp_path / "a.pdf", 1)  # whatever helper this module uses
    gui.run_operation("ocr", [src], {"engine": "builtin", "language": "", "force": "true"})
    assert seen["engine"] == "builtin" and seen["language"] == "eng" and seen["force"] is True

def test_watch_and_cli_pass_the_ocr_engine(tmp_path, monkeypatch):
    from pdfmanager import cli, core, watch
    seen = {}
    monkeypatch.setattr(core, "ocr", lambda src, out, **kw: seen.update(kw) or out)
    settings = watch.WatchSettings(inbox=tmp_path / "in", dest=tmp_path / "out", ocr=True, ocr_engine="tesseract")
    assert settings.ocr_engine == "tesseract"
    src = make_pdf(tmp_path / "a.pdf", 1)
    cli.main(["ocr", str(src), "-o", str(tmp_path / "o.pdf"), "--engine", "tesseract", "--language", "deu"])
    assert seen["engine"] == "tesseract" and seen["language"] == "deu"
    with pytest.raises(SystemExit):
        cli.main(["ocr", str(src), "--engine", "abbyy"])

def test_recipe_step_accepts_the_engine_option(tmp_path, monkeypatch):
    from pdfmanager import core, recipes
    seen = {}
    monkeypatch.setattr(core, "ocr", lambda src, out, **kw: seen.update(kw) or out)
    steps = recipes.parse_steps(["ocr", "engine=builtin", "language=eng"])  # use the module's real parser name
    recipes.run(steps, make_pdf(tmp_path / "a.pdf", 1), tmp_path / "o.pdf")
    assert seen["engine"] == "builtin"
```

Adapt the helper names (`make_pdf`, the recipe parser) to what those modules actually export; read them first.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_features.py -k "engine" -q`
Expected: FAIL (`StopIteration` on the missing `engine` field; `TypeError` on `ocr_engine`; argparse error on `--engine`).

- [ ] **Step 3: Implement**

- `gui.py` OCR tool: add `_f("engine", "Engine", "select", "auto", choices=[...])` as the first field, update the language field's label to `"Language(s) for Tesseract"` with placeholder `eng+deu`, and a `"help"` on the tool: `"The built-in engine reads English, most European languages, Chinese and Japanese without a language choice. Tesseract needs its language codes."`; dispatch passes `engine=opts.get("engine") or "auto"`.
- `cli.py`: `--engine` with `choices=ocr.ENGINES` (import `ocr` lazily or at the top like the other modules) on `ocr` and `watch`; `run_ocr` passes `engine=a.engine`; watch settings construction passes `ocr_engine=a.engine`; the `ocr` subcommand help becomes `"make scanned PDFs searchable"`.
- `watch.py`: `ocr_engine: str = "auto"`; `core.ocr(work, work, language=settings.ocr_language, engine=settings.ocr_engine)`.
- `recipes.py`: if step options are whitelisted per op, add `engine` to `ocr`'s; the step runner passes `engine=` to `core.ocr`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_features.py tests/test_audit_tools.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pdfmanager/gui.py pdfmanager/cli.py pdfmanager/watch.py pdfmanager/recipes.py tests/test_features.py
git commit -m "OCR engine choice in the app, command line, watch folder and recipes"
```

---

### Task 5: Packaging, installer, CI, docs, version

**Files:**
- Modify: `pyproject.toml:25-29`
- Modify: `packaging/build_app.py:61-75` (collect rapidocr and onnxruntime)
- Modify: `packaging/launcher.py:65-74` (self-test runs the built-in engine)
- Modify: `packaging/windows/install.ps1` (install `[app,ocr]`; the optional step becomes Tesseract + Ghostscript)
- Modify: `tests/test_installer.py:101-150`
- Modify: `.github/workflows/tests.yml:48`
- Modify: `README.md` (OCR paragraph, installer paragraph, 0.14.0 notes), `pdfmanager/__init__.py` (`0.14.0`)

**Interfaces:**
- Consumes: `core.ocr(..., engine="builtin")`.
- Produces: extras `ocr`, `tesseract`; installer switches `-WithExtras`/`-NoExtras` (aliases `-WithOcr`/`-NoOcr`).

- [ ] **Step 1: Update the installer tests first**

In `tests/test_installer.py`: `test_installer_offers_ocr_and_pdfa_programs` → assert `[switch]$WithExtras`, `[switch]$NoExtras`, and that `[Alias("WithOcr")]`/`[Alias("NoOcr")]` appear; `test_installer_adds_the_ocrmypdf_the_ocr_extra_asks_for` → rename to `test_installer_installs_the_app_with_ocr_and_adds_ocrmypdf_from_the_tesseract_extra`: assert `"$source[app,ocr]"` is in the pip install line and the ocrmypdf version string matches the `tesseract` extra in `pyproject.toml`. Add `test_build_collects_the_ocr_engine`: read `packaging/build_app.py` and assert `"--collect-all", "rapidocr"` and `"--collect-all", "onnxruntime"` appear.

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_installer.py -q`
Expected: the three changed/new tests FAIL.

- [ ] **Step 3: Implement**

- `pyproject.toml`: `ocr = ["rapidocr>=3.10", "onnxruntime>=1.17"]`, `tesseract = ["ocrmypdf>=15"]`, `all = ["pywebview>=5", "rapidocr>=3.10", "onnxruntime>=1.17", "ocrmypdf>=15"]`, `build = ["pywebview>=5", "pyinstaller>=6", "rapidocr>=3.10", "onnxruntime>=1.17"]`.
- `build_app.py`: add `"--collect-all", "rapidocr", "--collect-all", "onnxruntime"` with a comment (models ship inside rapidocr; onnxruntime's shared libraries).
- `launcher.py` self-test: after the 0.13.0 block, `from pdfmanager import ocr` … render `src` to an image is not needed: call `core.ocr(scanned, Path(tmp) / "ocr.pdf", engine="builtin")` on a scan made with `core.images_to_pdf` from a Pillow image of the text `"ZPDF self-test"` (reuse the drawing code from `tests/ocrpages.render` inline, 600×200 px, font size 48), and `assert "ZPDF" in core.extract_text(Path(tmp) / "ocr.pdf")`. Update the module docstring's list.
- `install.ps1`: pip line → `"$source[app,ocr]"`; rename switches with `[Alias("WithOcr")]` / `[Alias("NoOcr")]`; the question becomes `"Also install Tesseract and Ghostscript? They add PDF/A output, turning pages upright and a second OCR engine (about 150 MB) [Y/n]"`; the status/Say texts drop "OCR support" wording in favour of "Tesseract and Ghostscript"; the ocrmypdf pip line stays (it is the `tesseract` extra's package).
- `tests.yml`: the OCR job installs `.[test,ocr,tesseract]`.
- `README.md`: the OCR sentence says it is built in; the installer paragraph describes the optional Tesseract + Ghostscript step; a 0.14.0 line in the version notes (if the README keeps them; follow the 0.13.0 pattern).
- `__init__.py`: `__version__ = "0.14.0"`.

- [ ] **Step 4: Verify**

Run: `python -m pytest tests/test_installer.py -q` → PASS.
Run the Linux build in the scratchpad build venv (the same command used for 0.13.0: `venv/bin/python packaging/build_app.py`), then `dist/ZPDF/ZPDF --self-test /tmp/result.txt; cat /tmp/result.txt` → `ok`. Note the built app's size before and after in the commit message body.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml packaging README.md pdfmanager/__init__.py tests/test_installer.py .github/workflows/tests.yml
git commit -m "Bundle the built-in OCR engine; installer offers Tesseract and Ghostscript as extras"
```

---

### Task 6: Full verification and PR

- [ ] **Step 1: Full suite on Python 3.13 (system) and 3.10 (scratchpad venv310, which has rapidocr)**

Run: `python -m pytest -q -x --ignore=tests/test_installer_windows.py` (the usual local command) and the same with `venv310/bin/python -m pytest -q`.
Expected: all pass; note skipped counts.

- [ ] **Step 2: pyflakes on changed files**

Run: `python -m pyflakes pdfmanager/ocr.py pdfmanager/textlayer.py pdfmanager/core.py pdfmanager/gui.py pdfmanager/cli.py pdfmanager/watch.py`
Expected: no new warnings (the pre-existing ones on main are known).

- [ ] **Step 3: Benchmark table for the PR**

Run the Task 3 CER tests with `-s` printing (add a `print` of CER per kind guarded by `-s`), or rerun the scratchpad `bench2.py`; copy the table into the PR body.

- [ ] **Step 4: Push and open the PR**

`git push -u origin claude/pdf-management-utility-rt0ovm`; PR titled "Version 0.14.0: built-in OCR engine" against `main`, body: what changed, the benchmark table, Linux results, "Windows: to be run on the user's PC", the attribution footer. Subscribe to the PR.
