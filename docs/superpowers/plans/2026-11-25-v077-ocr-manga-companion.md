# `v0.77.0`: OCR, Translation & Manga v5 + Android Companion App Phase 2 — Implementation Plan

- [x] **Step 1 (TDD RED)**: Create `tests/test_v077_round5_companion.py` testing:
  - OCR: repeated border/ruler noise filtering (`||||||||`, `========`, `--------`) in `_lines()`.
  - Translation: LLM-path entity & glossary preservation in `translate_texts()`, and automatic long-sentence chunking (>450 chars) on neural path.
  - Manga: `_to_comic_caps()` formatting and `make_manga_overlay(..., comic_caps=True)` + rectangular caption box typesetting.
  - Companion: `_is_lan_host()` RFC-1918 validation (accepts `192.168.1.5:8765`, `10.0.2.2:8765`, `172.16.0.1:8765`, `mac.local:8765`; rejects `evil.com:8765`, `8.8.8.8:8765`), `GET /api/companion/info` endpoint, and Android `MainActivity.kt` `ACTION_VIEW` + `ZpdfAndroid` JS bridge presence.
- [x] **Step 2 (OCR, Translation & Manga v5)**: Update `pdfmanager/ocr/layout.py`, `pdfmanager/translate/text.py`, and `pdfmanager/translate/manga.py`.
- [x] **Step 3 (Companion Phase 2)**: Update `pdfmanager/server/common.py`, `pdfmanager/gui.py`, `pdfmanager/static/mobile-touch.js`, `mobile/android/app/src/main/java/com/tempura446/zpdf/MainActivity.kt`, and `mobile/android/README.md`.
- [x] **Step 4 (Version Bump & Verification)**: Bump to `0.77.0`, run full verification suite + cold-startup check, open PR, watch CI to 100% green, merge, and ship `v0.77.0`.
