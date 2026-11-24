# ZPDF v0.67.0 — Vertical CJK (Tategaki) Books & Manga Speech Bubble Translation

**Date:** 2026-11-24  
**Target Release:** `v0.67.0`  
**Status:** Approved

---

## 1. Goal
Add first-class offline support for:
1. **Vertical Asian layouts (*tategaki*)** in Japanese & Traditional/Simplified Chinese PDFs (novels, light novels, vertical books) across all translation modes (`side-by-side`, `bilingual-pages`, `bilingual`, `in-place`, `text`).
2. **Manga / Comic speech bubble translation (`mode="manga"`)** — detecting enclosed speech bubbles on raster or digital manga pages, ordering bubbles in right-to-left manga reading order, extracting vertical/horizontal dialogue (with furigana filtering), cleaning bubble interiors while preserving borders and artwork, and typesetting centered comic dialogue neatly inside each bubble.

All algorithms are **100% offline, commercial-safe, pure Python + NumPy + Pillow + ReportLab** (zero GPL/AGPL or non-commercial dependencies).

---

## 2. Architecture

### Track A — Vertical CJK (*Tategaki*) Extractor (`pdfmanager/translate/vertical.py`)
- `is_vertical_cjk_page(plumber_page) -> bool`:
  - Inspects `plumber_page.chars` (individual glyph boxes).
  - Counts CJK characters and compares vertical-downward neighbor continuity (`|dx| <= 0.65*sz`, `0 < dy <= 2.0*sz`) vs horizontal-rightward continuity (`|dy| <= 0.45*sz`, `0 < dx <= 1.8*sz`).
  - Returns `True` when ≥ 6 CJK chars exist and ≥ 60% of neighbor pairs are vertical.
- `extract_vertical_cjk_blocks(plumber_page) -> list[dict]`:
  - **Furigana / Ruby filtering**: filters out small ruby characters (`size <= 0.62 * median_body_size`) adjacent to main columns.
  - **Vertical presentation form normalization**: maps vertical punctuation (`﹁﹂﹃﹄︵︶︱` etc.) to standard horizontal equivalents before translation.
  - **Column grouping & RTL paragraph merging**: groups chars into vertical columns (`top`-sorted), orders columns right-to-left (`-x_center`), merges adjacent same-style columns into coherent paragraph blocks with full `[x0, top, x1, bottom]` bounds covering the merged columns.
  - Hooked automatically into `_extract_layout_blocks(plumber_page)` in `pdfmanager/translate/layout.py`.

### Track B — Manga Speech Bubble Engine (`pdfmanager/translate/manga.py`)
- `detect_speech_bubbles(pil_img) -> list[dict]`:
  - Pure NumPy/Pillow connected-component analysis on enclosed bright regions (`gray >= 232`) not touching page borders.
  - Filters by area ratio (`0.25%..25%`), aspect ratio (`0.25..4.0`), solidity (`>= 0.45`), and dark-ink presence (`1%..45%` pixels `< 140` inside inner box).
  - Sorts bubbles in **manga reading order** (top-to-bottom bands, right-to-left within each band).
- `translate_manga_page(...)` & `mode="manga"` in `pdfmanager/translate/pipeline.py`:
  - Supports both digital-text bubbles and raster-image bubbles (via built-in `ocr.recognise` or injectable `ocr_fn` for tests).
  - Cleans bubble interior strictly inside the inner ellipse/rounded box (preserving the artist's black bubble outline and surrounding artwork).
  - Typesets centered, bold/clean auto-fitted dialogue inside the inscribed region of each bubble.
- Exposed as `mode="manga"` (`"Manga / Comic (speech bubbles)"`) in `MODES`, `pdfmanager/server/operations/catalog.py`, and `pdfmanager/cli/parser.py`.
