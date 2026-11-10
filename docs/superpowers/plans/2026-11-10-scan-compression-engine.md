# Scan Compression Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compress keeps the text of scanned pages at full resolution, at the file size today's High level reaches by blurring it.

**Architecture:** A new module, `pdfmanager/scanlayers.py`, splits a page picture into a 1-bit text shape, a low-resolution background and a very low-resolution ink-colour picture (no PDF knowledge). `imaging.downsample_images` decides which pictures are scans, calls it, and swaps the picture for a form XObject drawing the three. `core.compress` gains `sharp_scans`; the app, command line, recipes and watch folder pass it on.

**Tech Stack:** Pillow, NumPy (new core dependency), pypdf objects (as `imaging.py` already uses).

**Spec:** `docs/superpowers/specs/2026-11-10-scan-compression-engine-design.md`

## Global Constraints

- Python 3.10 to 3.14; tests pass on Linux and Windows.
- `scanlayers.py` imports only Pillow, NumPy and the standard library.
- Compress must never fail because of the engine: any exception inside it for a picture falls back to the existing path for that picture.
- Low level never uses the engine. Medium and High use it when `sharp_scans` is true (the default).
- Levels: Medium background 150 dpi quality 50, ink colour 37 dpi quality 40; High background 100 dpi quality 40, ink colour 25 dpi quality 35. Text shape at the scan's resolution, capped at 300 dpi.
- A scan picture: covers at least 80 % of its page, drawn on one page only, 8-bit with 1, 3 or 4 components, not bilevel, no `/SMask`, no colour key, drawn at 150 dpi or more.
- The layers result is used only when smaller than the original and no larger than 1.10 × the normal result for the level.
- No model identifiers in code or commits. Commit messages end with the two attribution lines this session uses.
- User-visible wording: checkbox "Keep scanned text sharp"; note "N scanned pages kept sharp" ("1 scanned page kept sharp").

## Review Focus

- A scan whose page is rotated (`/Rotate 90`) or whose picture is drawn mirrored or rotated by the content stream: the layers must draw exactly where the picture did (the form XObject takes the picture's place, so the page's own matrix applies).
- A CMYK scan: layers come out RGB; colours must stay close, not inverted.
- A scan of white text on a dark page (not supported): must fall to the normal path, not produce an empty or all-ink text shape.
- A very large scan (600 dpi A3): the text shape is capped at 300 dpi and memory stays at one page.
- A picture used on two pages, or twice on one page: left to the normal path.

---

### Task 1: The engine: `scanlayers.split` and `group4`

**Files:**
- Create: `pdfmanager/scanlayers.py`
- Create: `tests/test_scanlayers.py`
- Modify: `pyproject.toml` (add `"numpy>=1.24"` to `dependencies`)
- Modify: `pdfmanager/notices.py` only if its tests list dependencies by hand (run `tests/test_notices.py`)

**Interfaces:**
- Produces:
  - `scanlayers.Layers`: dataclass with `mask` (PIL "1" image, 0 = ink), `background` (PIL "RGB" or "L"), `ink` (PIL "RGB" or "L"), `picture_share` (float, 0–1).
  - `scanlayers.split(image, dpi, background_dpi, ink_dpi) -> Layers | None`. `image` is PIL "L", "RGB" or "CMYK"; `dpi` is the resolution it is drawn at. Returns None when the page isn't a text scan (picture cells over 60 %, or no ink found, or ink over 35 % of the page).
  - `scanlayers.group4(mask) -> bytes | None`: CCITT Group 4 data such that a PDF `/CCITTFaxDecode` with `/K -1`, `/Columns`, `/Rows`, `/BlackIs1 false` used as an `/ImageMask` paints the ink. None when this Pillow can't write Group 4.
  - Constants at the top of the module: `INK_MARGIN = 30`, `INK_SHARE = 0.22`, `NEIGHBOURS = 4`, `CELL_INCH = 1 / 6`, `PICTURE_INK = 0.45`, `PICTURE_DARK = 110`, `PICTURE_PAGE = 0.60`, `MAX_INK = 0.35`, `MASK_DPI = 300`.

- [ ] **Step 1: Write the failing tests** in `tests/test_scanlayers.py`, using `tests/ocrpages.py` for pages (`sys.path` already has `tests`):

```python
def test_text_goes_to_the_shape_and_paper_to_the_background():
    page = ocrpages.render()                      # 2480 x 3508, 300 dpi, grey
    layers = scanlayers.split(page, 300, 100, 25)
    assert layers.mask.size == page.size and layers.mask.mode == "1"
    ink = 1 - np.asarray(layers.mask, np.uint8)
    assert 0.005 < ink.mean() < 0.10
    assert layers.background.width == round(2480 * 100 / 300)
    assert np.asarray(layers.background.convert("L")).min() > 200   # no ink left in the paper

def test_a_scan_above_300_dpi_gets_a_300_dpi_shape():
    page = ocrpages.render().resize((4960, 7016))                   # as if 600 dpi
    assert scanlayers.split(page, 600, 100, 25).mask.size == (2480, 3508)

def test_speckles_stay_out_of_the_shape():
    noisy = ocrpages.degrade(ocrpages.render())
    clean = scanlayers.split(noisy, 300, 100, 25)
    assert len(scanlayers.group4(clean.mask)) < 40_000              # 149 KB without despeckling

def test_ink_keeps_its_colour():
    # colour_page(): the plan's helper below; red stamp at (1500..2100, 1500..1900), blue text at y 2050
    layers = scanlayers.split(colour_page(), 300, 100, 25)
    r, g, b = layers.ink.resize(layers.mask.size).getpixel((1505, 1700))  # on the stamp's ring
    assert r > 150 and g < 90 and b < 90

def test_a_photograph_stays_out_of_the_shape():
    page = page_with_photo()                      # dark photo at (700..1700, 2300..3000)
    layers = scanlayers.split(page, 300, 100, 25)
    ink = 1 - np.asarray(layers.mask, np.uint8)
    assert ink[2350:2950, 750:1650].mean() < 0.01
    assert ink[300:1300].mean() > 0.005           # the text above it is still found

def test_a_page_that_is_all_photograph_is_not_a_text_scan():
    assert scanlayers.split(photo_only(), 300, 100, 25) is None

def test_light_text_on_a_dark_page_is_not_a_text_scan():
    assert scanlayers.split(ImageOps.invert(ocrpages.render()), 300, 100, 25) is None

def test_cmyk_comes_out_rgb_and_not_inverted():
    layers = scanlayers.split(ocrpages.render().convert("CMYK"), 300, 100, 25)
    assert layers.background.mode == "RGB" and np.asarray(layers.background).mean() > 200

def test_group4_round_trips():
    mask = scanlayers.split(ocrpages.render(), 300, 100, 25).mask
    # written into a one-page PDF as an /ImageMask painted black, rendered with pdfium at 300 dpi:
    # the render matches the mask (mean absolute difference under 2 of 255)
```

Test helpers in the same file: `colour_page()` (tinted paper `(244, 238, 220)`, `ocrpages.render()` text multiplied in, a red `(200, 30, 30)` ellipse ring width 14 at `(1500, 1500, 2100, 1900)` with "PAID", blue `(20, 40, 170)` serif text at `(300, 2050)`, Gaussian blur 0.6); `page_with_photo()` (`ocrpages.render()` with a 1000×700 picture pasted at `(700, 2300)` made of blurred noise with values 10–200); `photo_only()` (that picture resized to the whole page).

- [ ] **Step 2: Run** `pytest tests/test_scanlayers.py -q`. Expected: fails on `import scanlayers`.

- [ ] **Step 3: Implement `scanlayers.py`.** The algorithm, which the tests don't fully determine:

  1. If `dpi > MASK_DPI`, resize the image by `MASK_DPI / dpi` (LANCZOS) and work at 300. CMYK converts to RGB first.
  2. Grey copy `g`. Paper estimate `p`: grey reduced 8×, `MaxFilter(7)`, `GaussianBlur(4)`, resized back (bilinear).
  3. `ink = g < p - max(INK_MARGIN, INK_SHARE * p)`.
  4. Despeckle: keep an ink pixel only when at least `NEIGHBOURS` of its 8 neighbours are ink (sum of shifted copies; pad, don't wrap around the edges).
  5. Cells of `round(dpi_worked * CELL_INCH)` pixels: a cell is picture when its ink share exceeds `PICTURE_INK` or its mean paper estimate is below `PICTURE_DARK`. Grow picture cells by one cell. `picture_share` is their share of the page. Clear ink in picture cells.
  6. Return None when `picture_share > PICTURE_PAGE`, when no ink remains, or when ink exceeds `MAX_INK` of the page.
  7. Background: block mean over `round(dpi_worked / background_dpi)` pixels of the pixels that are not within 2 pixels of ink (dilate with `MaxFilter(5)`); blocks with none take the mean of their filled neighbours, repeated until full.
  8. Ink colour: block mean over `round(dpi_worked / ink_dpi)` pixels of ink pixels only; empty blocks filled the same way.
  9. Grey input gives grey ("L") background and ink pictures.

  `group4`: save the mask as TIFF with `compression="group4"` and `tiffinfo={278: mask.height}` (one strip); take the strip (tags 273, 279) from the bytes; if tag 262 (photometric) is 1, the image was written with ink as codec-white, so invert the mask first and write again, so that `/BlackIs1 false` is right. Any exception returns None.

- [ ] **Step 4: Run** `pytest tests/test_scanlayers.py tests/test_notices.py -q`. Expected: pass.

- [ ] **Step 5: Commit** "Scan layers engine: text shape, background and ink colour from a page picture".

### Task 2: Compress uses the engine

**Files:**
- Modify: `pdfmanager/imaging.py` (`downsample_images`, new `_scan_candidate`, `_store_layers`, `SCAN_LAYERS`)
- Modify: `pdfmanager/core.py` (`compress`)
- Test: `tests/test_scanlayers.py`

**Interfaces:**
- Consumes: `scanlayers.split`, `scanlayers.group4`, `scanlayers.Layers` (Task 1).
- Produces:
  - `imaging.SCAN_LAYERS = {"medium": (150, 50, 37, 40), "high": (100, 40, 25, 35)}` (background dpi, background quality, ink dpi, ink quality).
  - `imaging.downsample_images(writer, max_dpi, jpeg_quality, scan_layers=None) -> (changed, saved, sharp)`; `scan_layers` is one of the tuples above or None. Existing callers that unpack two values are updated.
  - `core.compress(path, output, level="medium", image_quality=None, max_dpi=None, password=None, sharp_scans=True, notes=None)`. When `image_quality` or `max_dpi` are given they replace the background's quality and dpi. Appends "N scanned pages kept sharp" (singular for 1) to `notes` when N > 0.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_scanlayers.py`; `scan(image, path, dpi=300, quality=85)` is a helper writing one full-page JPEG picture per page with pikepdf, as a scanner does):

```python
@pytest.mark.parametrize("make", [ocrpages.render, lambda: ocrpages.degrade(ocrpages.render()), colour_page])
def test_high_is_as_small_with_sharp_text(make, tmp_path):
    src = scan(make(), tmp_path / "scan.pdf")
    notes = []
    sharp = core.compress(src, tmp_path / "sharp.pdf", level="high", notes=notes)
    plain = core.compress(src, tmp_path / "plain.pdf", level="high", sharp_scans=False)
    assert sharp.stat().st_size <= plain.stat().st_size * 1.10
    assert notes == ["1 scanned page kept sharp"]
    filters = image_filters(sharp)                 # helper: every image XObject's /Filter, forms included
    assert "/CCITTFaxDecode" in filters and image_filters(plain) == ["/DCTDecode"]
    # the text shape is at the scan's resolution
    assert mask_size(sharp) == (2480, 3508)

def test_the_page_still_looks_like_the_page(tmp_path):
    src = scan(colour_page(), tmp_path / "scan.pdf")
    out = core.compress(src, tmp_path / "out.pdf", level="high")
    a, b = render(src, 100), render(out, 100)      # helper: pdfium, RGB
    assert np.abs(np.asarray(a, int) - np.asarray(b, int)).mean() < 6
    r, g, bl = render(out, 300).getpixel((1505, 1700))
    assert r > 150 and g < 100 and bl < 100

def test_ocr_reads_it_as_well(tmp_path):          # skip without ocr.builtin_available()
    # for the three pages: cer(sharp) <= cer(plain high) + 0.005

def test_low_and_the_switch_leave_scans_alone(tmp_path):
    # level="low": no CCITT; sharp_scans=False at medium: no CCITT, no note

def test_what_is_not_a_scan_is_left_to_the_usual_way(tmp_path):
    # each of: a picture covering 40 % of the page; a 100 dpi full-page picture; a CCITT scan;
    # a picture with /SMask; one picture drawn on two pages; one picture drawn twice on a page;
    # photo_only() -> no /CCITTFaxDecode in the result, and the result equals sharp_scans=False in size

def test_a_rotated_page_and_a_mirrored_picture_draw_in_place(tmp_path):
    # page /Rotate 90, and a content stream "q -595 0 0 842 595 0 cm /Im Do Q":
    # render of the result differs from render of the source by mean < 6

def test_the_rest_of_the_page_survives(tmp_path):
    # scan + core.ocr (or textlayer) text + a link annotation: after compress the text extracts
    # the same and the annotation count is unchanged

def test_an_engine_failure_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(scanlayers, "split", lambda *a, **k: 1 / 0)
    out = core.compress(src, tmp_path / "out.pdf", level="high")
    assert out.stat().st_size == plain.stat().st_size

def test_without_group4_the_usual_way_is_used_unless_flate_is_small_enough(tmp_path, monkeypatch):
    monkeypatch.setattr(scanlayers, "group4", lambda mask: None)
    # compress succeeds; result is no larger than 1.10 x plain
```

- [ ] **Step 2: Run** the new tests. Expected: fail (`compress() got an unexpected keyword argument 'sharp_scans'`).

- [ ] **Step 3: Implement.**
  - `_scan_candidate(obj, drawn, page_size, uses)`: the Global Constraints test. `drawn` is the picture's displayed size in points from `image_display_sizes`; count how often each picture is drawn (extend `_walk_images` / `image_display_sizes` to return a use count, or count `Do` operators per key) so "once, on one page" can be checked.
  - In `downsample_images`, for a candidate when `scan_layers` is set: decode at full size, make the normal result as now (`normal`), call `scanlayers.split(image, dpi, bg_dpi, ink_dpi)`; encode background and ink as JPEG at their qualities, mask with `group4` (None: Flate of the packed bits with `/BitsPerComponent 1`); total the three; use when `total < original and total <= 1.10 * len(normal)`; otherwise store `normal` as today. All of the engine part inside `try/except Exception` that falls to `normal`.
  - `_store_layers(writer, obj, layers, datas)`: turn `obj` in place into a form XObject (`/Subtype /Form`, `/BBox [0 0 1 1]`, no `/Matrix`, `/Resources` with `/XObject << /Bg … /Fg … >>`), content `q /Bg Do /Fg Do Q` Flate-compressed, removing the image-only keys (`/Width`, `/Height`, `/ColorSpace`, `/BitsPerComponent`, `/Filter` of the image, `/DecodeParms`, `/Decode`, `/Intent`, `/Interpolate`). `/Fg` carries `/Mask` pointing at the mask picture (`/ImageMask true`). Changing the object in place keeps every reference to it (the page's `/Im Do`) valid, so the page's own matrix places it.
  - `core.compress`: pass `imaging.SCAN_LAYERS[level]` (with `max_dpi` / `image_quality` overrides in the first two places) when `sharp_scans` and level is not low; add the note.

- [ ] **Step 4: Run** `pytest tests/test_scanlayers.py tests/test_pdfmanager.py tests/test_robustness.py -q -k "scan or compress or survives"`. Expected: pass.

- [ ] **Step 5: Commit** "Compress keeps scanned text sharp: a text shape over a low-resolution background".

### Task 3: The option in the app, command line, recipes and watch folder

**Files:**
- Modify: `pdfmanager/gui.py` (the `compress` operation's fields and its call, around lines 210 and 628)
- Modify: `pdfmanager/cli.py` (`run_compress`, the `compress` parser near line 1241, the watch command near 1634)
- Modify: `pdfmanager/recipes.py`, `pdfmanager/watch.py` (pass the option; default on)
- Modify: `packaging/launcher.py` (`self_test`: one scan page through `core.compress(level="high")`, asserting a `/CCITTFaxDecode` picture is in the result)
- Test: `tests/test_scanlayers.py`

**Interfaces:**
- Consumes: `core.compress(..., sharp_scans=True, notes=None)` (Task 2).
- Produces: app field `sharp_scans` (check, default true, label "Keep scanned text sharp", hint "Scanned pages keep their text at full sharpness; only the paper behind it is reduced."); `pdfm compress --no-sharp-scans`; recipe step option `sharp_scans=false`; `watch.Settings.sharp_scans: bool = True` and `pdfm watch --no-sharp-scans`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_the_app_offers_the_choice_and_says_what_it_did(tmp_path):
    op = next(o for o in gui.OPERATIONS if o["id"] == "compress")
    field = next(f for f in op["fields"] if f["key"] == "sharp_scans")
    assert field["label"] == "Keep scanned text sharp" and field["default"] is True
    notes = []
    gui.run_operation("compress", [src], {"level": "high"}, None, {}, work, notes)
    assert "1 scanned page kept sharp" in notes
    notes = []
    name, data, _ = gui.run_operation("compress", [src], {"level": "high", "sharp_scans": "false"}, None, {}, work2, notes)
    assert notes == [] and b"CCITTFaxDecode" not in data

def test_the_command_line_can_turn_it_off(tmp_path):
    # cli.main(["compress", str(src), "-o", str(a), "--level", "high"]) -> CCITT present
    # ... + "--no-sharp-scans" -> absent

def test_recipes_and_the_watch_folder_pass_it_on(tmp_path):
    # a recipe [{"op": "compress", "level": "high", "sharp_scans": "false"}] -> absent; default -> present
    # watch.Settings(compress="high") -> present; sharp_scans=False -> absent
```

- [ ] **Step 2: Run** them. Expected: fail (no `sharp_scans` field).
- [ ] **Step 3: Implement** the four call sites and the self-test line, following how `level` already travels in each.
- [ ] **Step 4: Run** `pytest tests/test_scanlayers.py tests/test_more.py tests/test_audit_cli.py -q`. Expected: pass.
- [ ] **Step 5: Commit** "Keep scanned text sharp: the choice in the app, pdfm, recipes and the watch folder".

### Task 4: Limits, documents, version

**Files:**
- Modify: `tests/test_perf.py`, `tests/perfwork.py` (a scanned file through compress, 3 and 15 pages)
- Modify: `README.md` (Compression levels section), `CHANGELOG.md` (new section), `pdfmanager/__init__.py` (version `0.17.0`)
- Modify: `packaging/build_app.py` only if NumPy needs naming for the standalone build (check that the build's hidden imports already cover it through OCR)

**Interfaces:**
- Consumes: everything above.
- Produces: `perfwork.py` work name `"compress-scan"` (runs `core.compress(src, out, level="high")`).

- [ ] **Step 1: Write the failing test** in `tests/test_perf.py`:

```python
def test_compressing_scans_keeps_memory_to_a_page(tmp_path):
    # 3 and 15 pages of the 1654 x 2339 grey scan used by the OCR check, as colour pages at 300 dpi
    _check_growth("compress-scan", small, large, tmp_path, seconds_each=15 * 10)   # 10 s a page
```

- [ ] **Step 2: Run** `pytest tests/test_perf.py --perf -q -k scans`. Expected: fail (`unknown work compress-scan`).
- [ ] **Step 3: Implement** the work name; write the README paragraph (what it does, the checkbox, `--no-sharp-scans`, that Low never uses it) and the CHANGELOG 0.17.0 section; set the version.
- [ ] **Step 4: Run** the full suite with `--perf` on Python 3.13 with OCR, the plain suite on system Python and on 3.10, and `python packaging/release.py --dry-run --skip-checks`. Expected: all pass; the release files are made.
- [ ] **Step 5: Commit** (version stays 0.16.0: see the ledger ruling; 0.16.0 was never published).
