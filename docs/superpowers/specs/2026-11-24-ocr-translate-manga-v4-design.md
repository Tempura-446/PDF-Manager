# `v0.76.0`: OCR, Translation & Manga Formatting v4 — Design Spec

## Overview
Deepens ZPDF's OCR accuracy, translation fidelity, and manga bubble detection/ordering with 100% offline, zero-new-dependency upgrades:

### 1. OCR v4 (`pdfmanager/ocr/preprocess.py`, `pdfmanager/ocr/layout.py`)
- **Local-adaptive binarization retry (`_adaptive_binarize_crop`)**: On faint/uneven low-confidence line crops, computes a local Gaussian background mean and produces a crisp binarized fallback candidate alongside autocontrast/dewarp, keeping whichever reading achieves strictly higher confidence.
- **Full-page vertical CJK (*tategaki*) RTL reading order**: When a page's OCR lines are predominantly tall CJK columns, `_ordered()` sorts columns right-to-left and top-to-bottom within each column so scanned Japanese books/manga produce natural reading order in search and copy-paste.
- **De-hyphenation support in `text_of(lines, dehyphenate=False)`**: Always strips invisible soft hyphens (`\u00ad`) and optionally joins hyphenated line wraps when `dehyphenate=True`.

### 2. Translation v4 (`pdfmanager/translate/text.py`, `pdfmanager/translate/layout.py`)
- **LLM-path entity & glossary sentinel protection**: Applies `_protect_neural_entities` / `_restore_neural_entities` to critical identifiers (URLs, emails, reference codes, glossary terms) in the LLM path as well, falling back cleanly if the LLM alters a sentinel — ensuring local 7B LLMs cannot corrupt invoice IDs or URLs.
- **WCAG relative-luminance contrast check (`_contrast_ratio`)**: Replaces the coarse `abs(lum_diff) < 0.25` heuristic in `_make_layout_overlay` with true WCAG contrast ratio (`< 4.5`) so translated text always has crisp legibility on tinted backgrounds.
- **Long-paragraph sentence chunking (`_split_long_for_mt`)**: Splits overly long paragraphs (> 450 chars) on sentence boundaries before neural MT calls so 512-token models never silently truncate the tail of long paragraphs.

### 3. Manga Formatting v4 (`pdfmanager/translate/manga.py`)
- **Broken-outline gap closing (`_close_border_gaps`)**: Applies a lightweight 1px morphological closing (`MinFilter(3)` on the light mask / `MaxFilter(3)` on dark outlines) during bubble detection so hand-drawn manga speech bubbles with tiny 1–2px ink gaps still enclose cleanly instead of leaking to the page edge.
- **Panel/gutter-aware XY-cut RTL bubble ordering (`order_manga_bubbles`)**: Recursively splits bubbles along clear horizontal and vertical panel gutters (reading right-to-left for `"rtl"` and left-to-right for `"ltr"`), fixing 2-column, 4-koma, and tall-panel manga pages.
- **Optional comic ALL-CAPS lettering & caption box polish**: Supports `comic_caps=False` in `make_manga_overlay` and renders rectangular narration/caption boxes with clean upright lettering.
