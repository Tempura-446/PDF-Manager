# `v0.75.0`: OCR, Translation & Manga Formatting Power Upgrade — Implementation Plan

- [x] **Step 1 (TDD RED)**: Create `tests/test_ocr_translate_manga_v3.py` covering:
  - OCR: ligature expansion + low-confidence punctuation filtering, CJK-aware `_prose()`, upright vertical CJK `_reads_wrong_way()` safety, border-safe `_measure_image()`, and inverted-polarity retry helper.
  - Translation: fullwidth/comma-separated sentinel restoration, code/version/currency protection, Unicode de-hyphenation + CJK zero-space line joining, and column-aware block ordering.
  - Manga: `_wrap_elliptical()` diamond line shaping, `_format_comic_text()` punctuation/emphasis normalization, raster vertical CJK furigana stripping & RTL ordering (`_order_and_clean_bubble_lines`), adaptive paper threshold, and lossless B&W CBZ→PDF ingest.
- [x] **Step 2 (OCR Upgrades)**: Update `pdfmanager/ocr/preprocess.py` and `pdfmanager/ocr/layout.py`.
- [x] **Step 3 (Translation Upgrades)**: Update `pdfmanager/translate/text.py`, `pdfmanager/translate/layout.py`, and `pdfmanager/translate/engines.py`.
- [x] **Step 4 (Manga Formatting Upgrades)**: Update `pdfmanager/translate/manga.py` and `pdfmanager/cbz.py`.
- [x] **Step 5 (Version Bump & Full Verification)**: Bump to `0.75.0`, run all new + existing OCR, translation, manga, CBZ, UI wiring, code quality, import, header, and privacy tests + cold-startup check.
