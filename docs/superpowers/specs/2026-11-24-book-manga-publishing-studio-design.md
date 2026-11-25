# ZPDF v0.71.0 — Book & Manga Publishing Studio (CBZ + Booklet Imposition + Spine Calculator)

**Date:** 2026-11-24  
**Target Release:** `v0.71.0`  
**Status:** Approved

---

## 1. Goal
Add end-to-end **Book & Manga Publishing** tools to ZPDF — 100% offline, commercial-safe, pure Python + Pillow + `pypdf` + ReportLab (zero new dependencies):

1. **CBZ Comic / Manga Archive Engine (`pdfmanager/cbz.py`)**:
   - `cbz_to_pdf(cbz_path, output_pdf, *, rtl_viewer=False)`: natural-sorted image extraction with zip-slip & decompression-bomb guards, optional `/ViewerPreferences << /Direction /R2L >>`.
   - `pdf_to_cbz(pdf_path, output_cbz, *, password=None, dpi=150, image_format="webp", quality=88, comic_info=None)`: OOM-guarded (`core.render_scale`) rendering into `.cbz` with optional `ComicInfo.xml` metadata.
   - `translate_cbz(cbz_path, output_path, src_lang, dst_lang, *, mode="manga", translator=None, ocr_fn=None, dpi=150, image_format="webp")`: one-shot `.cbz` → translate bubbles → `.cbz` or `.pdf`.
2. **Booklet Imposition v2 (`pdfmanager/core/geometry.py:booklet`) & Spine Calculator (`spine_width_mm`)**:
   - `binding="ltr" | "rtl"`: authentic right-to-left manga / Japanese binding alongside Western LTR saddle-stitch.
   - `signature_pages`: `0` (single saddle-stitch) or `8`, `16`, `32` (multi-signature sections for thick books/tankōbon).
   - `creep_mm`: progressive inward shift on inner nested sheets for uniform trimmed outer margins.
   - `spine_width_mm(page_count, paper="white_90gsm", *, caliper_mm=None, trim_w_mm=None, trim_h_mm=None, bleed_mm=3.0)`: calculates print-on-demand spine width (mm & inches) and full-wrap cover dimensions for KDP / IngramSpark / manga stocks.
3. **CLI & GUI Integration**:
   - CLI: `zpdf cbz-to-pdf`, `zpdf pdf-to-cbz`, `zpdf spine`, and `--binding` / `--signature` / `--creep` flags on `zpdf booklet`.
   - GUI Tools (`catalog.py`, `dispatch.py`, `app-core.js`): upgraded **Booklet** tool + **PDF to CBZ** and **CBZ to PDF** tools.
