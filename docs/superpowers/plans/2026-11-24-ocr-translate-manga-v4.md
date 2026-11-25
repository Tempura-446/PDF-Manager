# `v0.76.0`: OCR, Translation & Manga Formatting v4 — Implementation Plan

- [x] **Step 1 (TDD RED)**: Create `tests/test_ocr_translate_manga_v4.py` testing:
  - OCR: `_adaptive_binarize_crop()` recovering faint text on uneven shaded background, vertical CJK RTL reading order in `_ordered()`, and `text_of(..., dehyphenate=True)`.
  - Translation: `_split_long_for_mt()` sentence-aware chunking, WCAG `_contrast_ratio()` contrast math, and LLM-path entity/glossary sentinel preservation.
  - Manga: broken-outline bubble recovery via 1px gap closing in `detect_speech_bubbles()`, and panel/gutter-aware XY-cut RTL ordering in `order_manga_bubbles()` (2-column / 4-koma layout).
- [x] **Step 2 (OCR v4)**: Update `pdfmanager/ocr/preprocess.py` and `pdfmanager/ocr/layout.py`.
- [x] **Step 3 (Translation v4)**: Update `pdfmanager/translate/text.py` and `pdfmanager/translate/layout.py`.
- [x] **Step 4 (Manga v4)**: Update `pdfmanager/translate/manga.py`.
- [x] **Step 5 (Version Bump & Full Verification)**: Bump to `0.76.0`, run full verification suite + cold-startup check, open PR #198, watch CI to 100% green, merge, and ship `v0.76.0`.
