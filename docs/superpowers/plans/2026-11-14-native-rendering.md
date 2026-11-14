# Native (C++) page rendering in the reader: Assessment and plan

**Question:** can the reader draw pages with a C++ engine instead of pdf.js?

**Answer:** yes. ZPDF already ships one: PDFium, Chrome's C++ PDF engine, through `pypdfium2`. It is used for thumbnails, tool previews, OCR pictures, text reading and the text editor. Only the reader's page view is drawn by pdf.js (JavaScript, with WebAssembly decoders) on a canvas in the window.

## The ways to do it

| Way | What it is | Verdict |
|---|---|---|
| **A. PDFium draws, pdf.js stays for everything else** | The server renders each visible page (or tile) with PDFium and the reader shows the picture; pdf.js keeps the text layer (select, find), links, forms, the annotation editors and layers | Feasible. The only one worth doing |
| B. PDFium as WebAssembly inside the window | PDFium compiled to wasm, drawing to the canvas | No gain over A: slower than native, a second 5 MB+ engine in the page, and a build to maintain |
| C. A native window (Qt or WinUI with a C++ viewer) | Replace the web interface | A rewrite of the whole app. No |

## What A would gain, and what it costs

**Gains (to be measured, not assumed):**
- Heavy vector pages, such as CAD sheets with hundreds of thousands of segments, where pdf.js is slowest.
- Fidelity: the same engine as Chrome and Edge, so odd fonts, blend modes and shadings match what prints.
- The window's memory: the canvas and pdf.js's operator lists for big pages go.

**Costs and risks:**
- **Two engines must agree.** The text layer, links, form fields, annotations and the snap geometry come from pdf.js and must sit exactly on PDFium's picture. Differences in font metrics show as selection boxes slightly off the letters.
- **Things pdf.js draws that would need redoing:** live annotation editing (pdf.js draws annotations being edited), layer visibility (PDFium has its own optional-content API, which `pypdfium2` reaches through the raw bindings), forms being filled in, and overlay compare.
- **One render at a time today.** PDFium isn't thread-safe; ZPDF holds one lock (`core.PDFIUM_LOCK`) around it. A reader that renders through it would wait behind OCR or a tool. It needs render worker processes of its own.
- **Zooming and scrolling** need tiles, a cache, cancelling of renders that are no longer wanted, and a low-resolution picture first. pdf.js does this already.
- **Unsaved changes:** the reader holds the changed file in the window; the server would need the current bytes for every render (it has them per tab after a change, but the round trip must stay fast for big files).

## Plan

### Step 1: measure (small, do first)
- [x] A benchmark (`tests/renderbench.py`): time to first paint and time to re-render at 200 % for pdf.js and for PDFium (through a throwaway endpoint), on: a text report, a scanned page, a brochure with transparency, a 200,000-segment drawing, a 36×48 inch sheet. Record memory too.
- [x] Go on only if PDFium is at least 2× faster on the drawing pages or fixes fidelity problems seen in real files. Record the numbers here.

**Result (2026-11-14, `python tests/renderbench.py`, a Linux machine, headless browser, median of three):**

| Page | pdf.js first, 100 % | pdf.js again, 200 % | PDFium first, 100 % | PDFium again, 200 % |
|---|---|---|---|---|
| Text report (Letter) | 62 ms | 23 ms | 3 ms | 7 ms |
| Scanned page (300 dpi JPEG) | 129 ms | 34 ms | 81 ms | 52 ms |
| Brochure (gradient, 400 see-through shapes) | 82 ms | 45 ms | 84 ms | 207 ms |
| Drawing, 200,000 segments (24 x 36 in) | 356 ms | 378 ms | 684 ms | 1609 ms |
| Big sheet, 50,000 segments (36 x 48 in) | 143 ms | 287 ms | 231 ms | 530 ms |

**Reading:** PDFium is much faster only on a plain text page, where both are already instant. On the pages this was meant to help (heavy drawings, big sheets, see-through shapes) it is 2 to 4 times **slower** than pdf.js, and slower still when zoomed in: the PDFium build ZPDF ships draws on the processor alone, while the browser's canvas is the better rasteriser. A real window with the graphics card (`--headed`, on the PC) can only widen pdf.js's lead.

**Decision: stop here.** Step 2 does not go ahead; the reader keeps pdf.js for drawing pages. To be confirmed once on the Windows PC with `python tests/renderbench.py --headed`; only a result opposite to this one would reopen the question. PDFium stays where it is used now (thumbnails, previews, OCR, text editing, printing).

### Step 2 (not going ahead; kept for the record): "fast drawing" for heavy pages only
- [ ] A render service: two or three worker processes, each with the document open in PDFium, answering `/api/render?page&scale&tile` with a PNG or raw RGBA; requests can be cancelled; tiles are cached by (document version, page, scale, tile, layers shown).
- [ ] The reader uses it for a page when pdf.js's own render of that page took longer than a threshold (or the page has more than N path segments), and keeps pdf.js for the rest. The pdf.js text layer and annotation layer stay on top.
- [ ] Layers shown, rotation and unsaved changes go with the request.
- [ ] Alignment test: on the holdout pages, every pdf.js text-layer span sits within 1 px of PDFium's letters at 100 % and 400 %.
- [ ] A Settings switch (Automatic / pdf.js only), and it falls back to pdf.js at the first error.

### Step 3 (only if step 2 proves itself): all pages
- [ ] Decide from use whether to make PDFium the default drawer for every page. Printing can already go through it.

**Place in the roadmap:** step 1 is a day's work and can go in any release. Step 2 fits with the drawing tools (PDF → DXF, big sheets), where heavy pages matter most.
