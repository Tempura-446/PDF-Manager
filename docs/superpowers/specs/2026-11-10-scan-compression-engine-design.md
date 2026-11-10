# Scan compression engine: design

Engine 1 of four (scan compression, built-in PDF/A, page understanding,
search by meaning). Each gets its own spec, plan and release.

## Purpose

Compress shrinks a scanned page by lowering its resolution and re-encoding
the picture as a JPEG. At the High level (96 dpi, quality 50) the file is
small but the text is soft and smeared. The scan compression engine reaches
the same size while keeping the text at the scan's full resolution.

It is for the user and friends who scan paper and email the result. Success
is: a scanned page compressed at High is no larger than today's High, reads
as well by OCR, and its text is visibly sharp.

## Measured before designing

A throwaway test on three synthetic 300 dpi scans (not kept in the repo):

| Page | Scanner file | Medium today | High today | Layers |
|---|---|---|---|---|
| Clean | 209 KB | 69 KB | 28 KB | 22 KB |
| Grey, noisy copy | 1,279 KB | 254 KB | 39 KB | 39 KB |
| Colour, stamp, blue ink | 344 KB | 111 KB | 43 KB | 40 KB |

OCR error rate after compression matched High on all three. About 2 s a
page, unoptimised. Not tested: real scanner output, photographs in a scan.

## What the user sees

- Compress, at Medium and High, treats a scanned page with the layers
  method automatically. No new tool.
- A checkbox "Keep scanned text sharp" in Compress, on by default.
- `pdfm compress --no-sharp-scans` turns it off; `core.compress(...,
  sharp_scans=True)` is the library form. Recipes and the watch folder
  take the same option, defaulting to on.
- A note after the run: "N scanned pages kept sharp" when N > 0.
- Low (lossless) never uses it.

## How a page is compressed

A scan picture is replaced by three smaller pictures that draw the same
page:

1. **Text shape**: a 1-bit picture at the scan's own resolution (capped at
   300 dpi), marking the dark ink. Encoded as CCITT Group 4.
2. **Background**: the page with the ink removed and filled in from the
   paper around it, at low resolution, as a JPEG.
3. **Ink colour**: a very low-resolution JPEG giving the colour of the ink
   under the text shape, so red stamps and blue pen stay red and blue.

In the PDF, the picture's place is taken by a form XObject that draws the
background, then the ink colour picture masked by the text shape. The
page's content stream, the invisible OCR text, links and comments are not
touched.

Resolutions by level:

| Level | Text shape | Background | Ink colour |
|---|---|---|---|
| Medium | up to 300 dpi | 150 dpi, quality 50 | 37 dpi, quality 40 |
| High | up to 300 dpi | 100 dpi, quality 40 | 25 dpi, quality 35 |

`image_quality` and `max_dpi`, when given, set the background's quality and
resolution.

### Finding the ink

- Grey version of the page; the paper's brightness around each point is
  the brightest nearby value, smoothed.
- A pixel is ink when it is darker than the paper around it by a margin
  (the larger of 30 levels and 22 %).
- Despeckle: an ink pixel is kept only with at least four ink neighbours
  out of eight. Without this, scanner noise goes into the text shape,
  which Group 4 stores badly (149 KB against 12 KB in the test).

### Keeping pictures out of the text shape

Dark parts of a photograph would otherwise be taken for ink and come out
as flat blocks. So:

- The page is divided into cells of about 1/6 inch. A cell is "picture"
  when more than 45 % of it is ink, or when its paper estimate is itself
  dark (below 110 of 255): text covers a small part of a cell, on light
  paper.
- Picture cells, grown by one cell, are cleared from the text shape, so
  they stay whole in the background layer.
- If picture cells cover more than 60 % of the page, the page is not a
  text scan: the layers method is not used for it.

The thresholds are starting values, fixed by the tests below; they are
constants in one place.

## Which pictures qualify

A picture is treated as a scan when all hold:

- It covers at least 80 % of its page, and is drawn on one page only.
- It is grey or colour (1, 3 or 4 components, 8 bits), not already
  black-and-white (CCITT, JBIG2, 1-bit), has no soft mask or colour key,
  and decodes with the code Compress already uses.
- It is drawn at 150 dpi or more. Below that there is no sharpness to keep.

Anything else goes through the existing path unchanged.

## Never worse than today

For each scan picture, the layers result and the normal result for the
level are both made; the layers result is used only when it is no larger
than the normal one plus 10 % (sharpness is worth a little) and smaller
than the original. Otherwise the normal result is used, as today.

## Where the code goes

- `pdfmanager/scanlayers.py` (new): the engine, with no PDF knowledge.
  - `split(image, background_dpi_factor, ...) -> Layers | None`: a PIL
    image in; the text shape, background and ink colour pictures out, or
    None when the page isn't a text scan.
  - `group4(mask) -> bytes`: the CCITT Group 4 data of a 1-bit picture
    (written by Pillow as a one-strip TIFF, the strip taken out). If this
    Pillow can't write Group 4, the mask is stored with Flate.
- `pdfmanager/imaging.py`: `downsample_images` gains the scan test, calls
  `scanlayers.split`, and a `_store_layers` that writes the form XObject
  and its three pictures. It reports the count of pages kept sharp.
- `pdfmanager/core.py`: `compress(..., sharp_scans=True, notes=None)`.
- `pdfmanager/gui.py`, `cli.py`, `recipes.py`, `watch.py`: the option.

`scanlayers.py` depends on Pillow and NumPy only. NumPy becomes a core
dependency in `pyproject.toml` (the installer and the standalone app
already carry it for OCR).

## Errors

- Any failure inside the engine for a picture (decoding, an odd colour
  space, Group 4 unavailable and Flate larger) falls back to the normal
  path for that picture; Compress never fails because of the engine.
- Memory: one page's picture is held at a time, as now.

## Tests

- `tests/test_scanlayers.py`:
  - On the clean, grey-noisy and colour test scans, the layered file is no
    larger than High's plus 10 %, and OCR error rate (built-in engine,
    skipped without it) is no worse than High's plus 0.5 points.
  - The text shape is at the scan's resolution; the page renders with the
    red stamp red and the blue ink blue (sampled pixels).
  - A page with a large dark photograph: the photograph's area has no ink
    in the text shape, and renders close to the original (mean difference
    under a limit).
  - A page that is all photograph is left to the normal path.
  - Already black-and-white scans, small pictures, pictures with soft
    masks and pictures used on two pages are left alone.
  - The OCR text layer, links and comments of a page survive.
  - `sharp_scans=False`, the CLI flag and the app's checkbox turn it off;
    Low never uses it.
  - A failure inside the engine falls back without an error.
- `tests/test_robustness.py`: Compress already runs on every hostile file;
  the broken-image and absurd-size files now also pass through the scan
  test.
- `tests/test_perf.py`: the compress check gains a scanned file, small and
  large: memory follows the file, and a time limit per page.

## Not in this design

- JBIG2 for the text shape (smaller than Group 4, but needs an encoder
  that isn't installable with pip).
- Light text on dark paper.
- Using OCR results to find the text.
- A separate tool or level names.

## Open risk

The thresholds were set on synthetic pages. After the build, the user
tries it on real scans; the constants are adjusted if text is missed or a
picture comes out flat.
