# `v0.75.0`: OCR, Translation & Manga Formatting Power Upgrade — Design Spec

## Overview
Strengthens ZPDF's core OCR, document translation, and manga typesetting engines using 100% offline, zero-new-dependency improvements:

### 1. Strengthened OCR (`pdfmanager/ocr/preprocess.py` & `pdfmanager/ocr/layout.py`)
- **Border-safe contrast measurement**: `_measure_image()` crops out the outer 4% scanner border when measuring paper brightness and ink contrast so black scan margins/gutters don't skew background flattening.
- **Smarter low-confidence retry (`_retry_low_confidence`)**:
  - Adds mild `UnsharpMask` after 2× upscaling on blurry/small-text crops.
  - Adds an **inverted-polarity retry** when a low-confidence crop has dark background (`median < 95`, e.g., white text on dark headers/banners).
- **Vertical CJK–safe orientation & CJK-aware XY-cut (`layout.py`)**:
  - `_prose()` counts CJK characters as effective words so multi-column CJK pages split cleanly into columns instead of interleaving.
  - `_reads_wrong_way()` preserves upright vertical CJK (*tategaki*) pages instead of misclassifying tall CJK columns as rotated 90°/270° sideways pages.
- **Post-OCR text normalization (`layout.py`)**:
  - Expands Latin ligatures (`ﬁ/ﬂ/ﬀ/ﬃ/ﬄ`), strips stray control characters, drops isolated 1-char low-confidence punctuation artifacts (`conf < 0.65`), and supports soft-hyphen/line-wrap de-hyphenation in `text_of()`.

### 2. Improved Translation (`pdfmanager/translate/text.py`, `layout.py`, `engines.py`)
- **Hardened entity & glossary protection**:
  - `_restore_neural_entities()` normalizes fullwidth digits (`０-９`) and tolerates optional `,`/`.`/space separators inserted by MT inside 6-digit sentinels, dramatically reducing fallback fragmentation.
  - `_NEURAL_PROTECT_RE` also protects inline `` `code` ``, semantic versions (`v1.2.3`), `snake_case` identifiers, and ISO currency amounts (`USD 100`, `100 EUR`, `￥500`).
- **Cleaner block merging & column-aware ordering (`layout.py`, `engines.py`)**:
  - De-hyphenation handles ASCII `-`, soft hyphen `\u00ad`, and Unicode hyphens `\u2010`/`\u2011`.
  - CJK-to-CJK wrapped lines join with `""` instead of inserting stray spaces.
  - Bold subheadings at body font size no longer merge into following normal paragraphs.
  - Column-aware block sorting (`_sort_blocks_reading_order`) prevents row-by-row interleaving on multi-column layouts.
- **Smarter overflow fitting & border-safe background sampling (`layout.py`)**:
  - Progressive line-leading tightening (`1.20 → 1.08`) + mild horizontal scale (`92%`) before truncating dense translated blocks.
  - Border-safe background luminance sampling (trimming darkest 25% border pixels) + WCAG relative-luminance contrast check (`< 4.5`) so dark table cell borders never flip text to invisible white.

### 3. Improved Manga Formatting (`pdfmanager/translate/manga.py` & `pdfmanager/cbz.py`)
- **Raster furigana stripping & vertical RTL column ordering (`extract_bubble_text`)**:
  - Filters narrow furigana/ruby columns (`w < 0.62 * median_col_w`) beside taller main Japanese columns and sorts vertical columns **right-to-left, top-to-bottom** on scanned/CBZ manga pages.
- **Elliptical / diamond-shaped comic typesetting (`_wrap_elliptical`)**:
  - For `oval` bubbles, computes per-line safe width `2 * rx * sqrt(max(0.28, 1 - (y / ry)^2))` so top/bottom lines taper naturally with the bubble curve, allowing larger, cleaner comic lettering.
- **Comic dialogue polish & emphasis (`_format_comic_text`)**:
  - Normalizes `...` → `…`, `--` → `—`, cleans spaced `?!`/`!?`, applies bold emphasis for shouts (`!`/`?!`) with dynamic font-size ceiling up to `min(22.0, ph * 0.25)` via binary search, and italic styling for whispered/thought parentheticals.
- **Feathered mask erasing & adaptive paper threshold (`manga.py`)**:
  - Uses Gaussian-feathered eroded masks (`_eroded_mask_image`) for smooth anti-aliased erasing against screentones/gradients, and adapts the light-bubble threshold to page paper luminance (`max(205, int(paper - 22))`).
- **Lossless grayscale preservation in `cbz_to_pdf` (`cbz.py`)**:
  - Stores crisp monochrome/B&W manga CBZ pages losslessly (PNG/Flate) instead of lossy JPEG q=92 so fine kanji strokes & screentones stay sharp for OCR.
