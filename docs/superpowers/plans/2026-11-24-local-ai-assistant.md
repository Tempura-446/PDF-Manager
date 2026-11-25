# Track 3 (`v0.74.0`): Local AI Document Assistant — Implementation Plan

- [x] **Step 1 (TDD RED)**: Create `tests/test_ai_assistant.py` testing:
  - `assistant.select_relevant_pages` ranking and character budget capping.
  - `assistant.parse_citations` extracting `[p. 2]`, `[Page 5]`, `[pp. 1, 3]` cleanly.
  - `assistant.ask_pdf`, `assistant.summarize_pdf`, and `assistant.extract_structured` (JSON + CSV) using a mocked `i18n._http_post_json` response.
- [x] **Step 2 (Backend Engine)**: Create `pdfmanager/assistant.py` reusing `i18n._http_post_json` and `update.settings()`.
- [x] **Step 3 (Server Routes)**: Wire `POST /api/ai/ask`, `POST /api/ai/summarize`, `POST /api/ai/extract` in `pdfmanager/gui.py` and `pdfmanager/server/reader_routes.py`.
- [x] **Step 4 (Reader UI)**: Create `pdfmanager/static/ai-assistant.js` (Ask with clickable page citations, Summarize, Extract JSON/CSV) and wire into `pdfmanager/static/reader.js` More menu & Command Palette.
- [x] **Step 5 (Version Bump & Verification)**: Bump version to `0.74.0` in `pdfmanager/__init__.py`, `CHANGELOG.md`, `docs/ROADMAP.md`, `packaging/homebrew/zpdf.rb`, `packaging/winget/Tempura446.ZPDF.yaml`. Run full verification suite + `node --check` + cold-startup check.
