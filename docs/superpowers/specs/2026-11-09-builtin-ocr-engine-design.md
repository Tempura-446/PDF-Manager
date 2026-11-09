# Built-in OCR engine (0.14.0)

## Goal

Make scanned PDFs searchable with accuracy at or above Adobe Acrobat's on
degraded scans and mixed languages, without installing programs beside
ZPDF. Today OCR means OCRmyPDF driving the Tesseract program, both external
installs that the Windows installer fetches with winget, and the user has
to pick Tesseract language codes.

Measured here on synthetic A4 scans (character error rate, lower is better):
clean page Tesseract 1.2 % vs PP-OCR 0.2 %; blurred, skewed, noisy page
6.5 % vs 3.7 %; 110 dpi JPEG 0.4 % vs 0.4 %. PP-OCR is 3 to 4 times slower
per page on a CPU (1 to 2 s against 0.5 s on a 4-core box).

## What ships

- A **built-in engine**: PaddleOCR's PP-OCR models run by the `rapidocr`
  package on ONNX Runtime. Pure pip, Apache 2.0, about 95 MB (ONNX Runtime
  63 MB, the default detection, recognition and orientation models 31 MB,
  shipped inside the rapidocr wheel so nothing is downloaded at run time).
  One multilingual model reads English and 40+ Latin-script languages,
  Chinese and Japanese without a language choice.
- ZPDF's **own text layer writer**: invisible text placed over each
  recognised line, so search, select, copy and PDF → Excel work. Same
  technique as OCRmyPDF's, built from libraries ZPDF already has (pypdfium2
  renders, reportlab writes the text, pypdf merges it onto the page).
- **Tesseract stays** as a second engine (through OCRmyPDF, as now), for
  people who have it and for PDF/A output straight from OCR.
- An **engine choice** in the GUI, CLI and watch folder: `auto` (built-in
  when installed, else Tesseract), `builtin`, `tesseract`.
- A **regression benchmark** in the tests: synthetic pages with known text,
  asserting error-rate ceilings for the built-in engine.

## Not in this version

- Page orientation (sideways or upside-down pages) still uses Tesseract in
  the "Clean up scans" tool. A built-in orientation check is a follow-up.
- Column-aware reading order. Lines are ordered top to bottom, left to
  right; two-column pages copy in row order.
- GPU and vision-language document parsers (PaddleOCR-VL and the like).
  They lead the layout benchmarks but need a GPU or tens of seconds per
  page. Possible later as an optional "best quality" engine.
- Korean, Arabic, Cyrillic, Thai and other scripts the default model does
  not cover. Tesseract with its language packs covers them as before.
- PDF/A output from the built-in engine. Run the PDF/A tool after OCR (a
  recipe can chain them), or choose the Tesseract engine.

## Architecture

New module `pdfmanager/ocr.py` (the library function `core.ocr` keeps its
signature plus `engine=` and delegates to it; `pdfmanager/textlayer.py`
holds the writer).

```
core.ocr(path, output, language, force, deskew, password, engine="auto")
  └─ ocr.run(...)
       ├─ engine == "tesseract": OCRmyPDF as today (moved from core.py)
       └─ engine == "builtin":
            for each page (progress.report per page, cancellable):
              skip if the page has text (unless force)
              image = render at 300 dpi with pypdfium2 (display orientation)
              if deskew: angle = ocr.skew_angle(image)   # detector only
                         image = image.rotate(-angle)
              lines = ocr.recognise(image)               # RapidOCR → Line objects
              layer = textlayer.page_layer(lines, image size, page box, /Rotate, angle)
              page.merge_transformed_page(layer, ...)    # or rasterised page + layer when force
            write output (same tmp-file-then-replace pattern as today)
```

### `ocr.py`

- `Line(text, box, confidence)`: `box` is the detector's quadrilateral in
  image pixels, four `(x, y)` points starting top-left.
- `engines_available() -> {"builtin": bool, "tesseract": bool}` and
  `choose_engine(name)`: `"auto"` picks built-in if `rapidocr` imports,
  else Tesseract if OCRmyPDF and the program are found, else raises
  `PdfManagerError` saying how to get either (`pip install pdf-manager[ocr]`
  or the Tesseract link).
- `recognise(image) -> list[Line]`: one process-wide `RapidOCR` instance
  (models load once, about 1 s), created lazily under a lock. Its logger is
  silenced. Lines with confidence below 0.3 are dropped.
- `skew_angle(image) -> degrees`: detector-only pass; the median angle of
  the long edges of boxes wider than they are tall; 0 when fewer than 3
  such boxes or the median is under 0.3°.
- `page_has_text(page)`: pypdf `extract_text().strip()`; this is OCRmyPDF's
  `skip_text` behaviour.
- `force` with the built-in engine replaces the page content with the
  300 dpi render (as OCRmyPDF's `--force-ocr` does) and adds the layer, so
  an old bad text layer does not double up with the new one. The render is
  stored as JPEG (quality 85) for colour or grey pages, and as a CCITT/Flate
  1-bit image when the render is black and white, keeping the file size near
  the original.

### `textlayer.py`

- `page_layer(lines, image_w, image_h, page_w, page_h, rotation, skew) -> PageObject`:
  a reportlab canvas of `page_w × page_h` points with text render mode 3
  (invisible). Each line's text is drawn along the line's baseline (the
  bottom edge of its box, so skewed text on an un-deskewed page is still
  selected along the words), with the font size chosen so the text's width
  matches the box width (as hOCR renderers do), and the box's bottom-left as
  the origin. Lines whose text contains spaces are drawn word by word, each
  word placed by the proportion of the line's characters before it, so a
  double-click selects one word and search hits land on the word.
- Coordinates: image pixels → display points (× 72/dpi) → page space:
  undo the page's `/Rotate` and move to the crop box origin, then undo the
  deskew rotation about the page centre. The layer is merged with pypdf's
  `merge_transformed_page`. Tests cover pages with `/Rotate 90` and a
  shifted crop box.
- Fonts: Latin text whose characters are all in WinAnsi uses Helvetica.
  Anything else uses an embedded TrueType font (reportlab's bundled
  Vera.ttf) with reportlab's ToUnicode map, so pypdf, pdfplumber and pdf.js
  extract the recognised characters even for glyphs the font does not have
  (our reader does not ship pdf.js's predefined CMaps, so non-embedded CID
  fonts are not an option). If Vera's ToUnicode turns out not to cover
  missing glyphs, the fallback is a Type0 font built with pikepdf: Identity-H
  encoding, one glyph, and a generated ToUnicode CMap, the way Tesseract's
  PDF renderer does it. A test with Chinese text decides which.

### GUI, CLI, watch folder

- GUI tool "Make searchable (OCR)" gains `Engine` (`auto` default,
  `builtin`, `tesseract`); its help says the built-in engine reads English,
  most European languages, Chinese and Japanese without a language choice
  and that the language field is for Tesseract.
- `pdfm ocr --engine {auto,builtin,tesseract}`; `pdfm watch` gets the same
  flag; recipes accept `engine=` like `language=`.
- The home screen and tool list need no change: OCR was already listed.

### Packaging and installer

- `pyproject.toml`: `ocr = ["rapidocr>=3.10", "onnxruntime>=1.17"]`,
  `tesseract = ["ocrmypdf>=15"]`, `all` and `build` include `ocr`.
- `build_app.py`: `--collect-all rapidocr`, `--collect-all onnxruntime`.
  The launcher self-test runs the built-in engine on its test page and
  asserts the result has a text layer with the test string.
- `install.ps1`: installs `[app,ocr]` so OCR works out of the box. The
  optional step becomes "Tesseract and Ghostscript" for PDF/A, auto-rotate
  and the Tesseract engine; `-WithOcr`/`-NoOcr` keep working as aliases of
  the new `-WithExtras`/`-NoExtras` so existing instructions do not break.
  `tests/test_installer.py` follows.
- CI `tests.yml`: the OCR job installs `.[test,ocr,tesseract]`.

### Errors and progress

- Built-in engine missing: `PdfManagerError("OCR needs the built-in engine
  (pip install pdf-manager[ocr]) or Tesseract (…)")`.
- A page that fails recognition (ONNX error) fails the whole file with the
  page number in the message; pages are not silently skipped.
- `progress.say("recognising text")` then `progress.report(n, pages, "page")`
  per page, so the app's bar moves and Cancel works between pages.
- Encrypted input is decrypted to a temp file first, as today.

## Testing

- `tests/test_ocr.py` (skipped when `rapidocr` is missing):
  - synthetic clean, degraded and low-resolution pages with known text;
    asserts built-in CER ≤ 1 % clean, ≤ 6 % degraded, ≤ 1 % low-res
    (pypdf extraction of the output, order-insensitive word check too);
  - text layer geometry: a word's rectangle from pdfplumber lies within a
    few points of where it was drawn on the scan, on a plain page, a
    `/Rotate 90` page and a cropped page;
  - skew: a 2° page with `deskew=True` comes out straight (the text layer's
    lines are horizontal and the page content is rotated);
  - `force` replaces an existing wrong text layer;
  - Chinese text is extractable from the output;
  - engine choice: `auto` falls back to Tesseract when `rapidocr` is made
    unimportable, and the error message when neither exists;
  - GUI `run_operation("ocr", ..., {"engine": "builtin"})` and the CLI flag.
- Existing OCRmyPDF test keeps passing with `engine="tesseract"`.
- Linux: full suite on 3.13 and 3.10, build and self-test. Windows: the
  user's PC, as for 0.13.0.
