# Track 2 (`v0.72.0`): Reader Comfort & Power-User Upgrade — Implementation Plan

- [x] **Step 1 (TDD RED)**: Create `tests/test_reader_comfort.py` covering:
  - `sessions.save_session`, `list_sessions`, `delete_session`, privacy gating, and missing-file handling.
  - `textlayer.read_all_ocr_words` (extracting all `/FZPDFOCR` words with boxes and unsure flags) and `textlayer.replace_ocr_text` (batch case-insensitive & case-sensitive OCR text replacement with proportional `Tm` scaling and pixel-identical preservation).
  - HTTP route integration tests for `/api/sessions`, `/api/sessions/delete`, `/api/ocr/words`, `/api/ocr/replace`.
- [x] **Step 2 (Backend — Sessions)**: Create `pdfmanager/sessions.py` and wire `GET /api/sessions`, `POST /api/sessions`, `POST /api/sessions/delete` in `pdfmanager/server/reader_routes.py` and `pdfmanager/gui.py`.
- [x] **Step 3 (Backend — OCR Typo Editor)**: Add `read_all_ocr_words` and `replace_ocr_text` in `pdfmanager/textlayer.py` and wire `POST /api/ocr/words` and `POST /api/ocr/replace` in `pdfmanager/server/reader_routes.py` and `pdfmanager/gui.py`.
- [x] **Step 4 (Frontend — Smart Reading Themes)**: Update `pdfmanager/static/reader.css`, `pdfmanager/static/reader-nav.js`, `pdfmanager/static/splitview.js`, and `pdfmanager/static/reader.js` with `light`/`night`/`sepia`/`eink` page themes.
- [x] **Step 5 (Frontend — Workspace Sessions & OCR Typo Editor)**: Add Workspace Sessions save/restore/delete modal in `pdfmanager/static/tabs.js` + `reader.js`, and upgrade `pdfmanager/static/checkocr.js` with "All OCR words" toggle and inline OCR Find & Replace.
- [x] **Step 6 (Version Bump & Verification)**: Bump version to `0.72.0` in `pdfmanager/__init__.py`, `CHANGELOG.md`, `docs/ROADMAP.md`, `packaging/homebrew/zpdf.rb`, `packaging/winget/Tempura446.ZPDF.yaml`. Run `pytest`, `node --check`, and cold-startup checks.
