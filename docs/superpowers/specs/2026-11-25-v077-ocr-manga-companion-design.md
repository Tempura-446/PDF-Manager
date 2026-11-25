# `v0.77.0`: OCR, Translation & Manga v5 + Android Companion App Phase 2 — Design Spec

## Overview
Delivers a fifth round of OCR/translation/manga polish alongside Phase 2 of ZPDF's Android/Mobile Companion App:

### 1. OCR, Translation & Manga v5
- **OCR (`pdfmanager/ocr/layout.py`)**:
  - `_is_repeated_border_noise(text)` filters hallucinated repeated-character ruler/border runs (e.g. `||||||||`, `=======`, `-------`, `........` ≥ 5 identical non-alphanumeric chars) inside `_lines()` so table borders and scan lines never pollute OCR text layers or translation blocks.
- **Translation (`pdfmanager/translate/text.py`)**:
  - Automatically chunks overlong sentences (> 450 chars) with `_split_long_for_mt` inside `translate_texts()` for offline neural MT models so 512-token models never truncate tails.
  - Adds `_protect_llm_entities` / `_restore_neural_entities` enforcement on the LLM path for critical verbatim tokens (URLs, emails, reference codes, user glossary entries) so local 7B LLMs cannot mangle identifiers.
- **Manga Formatting (`pdfmanager/translate/manga.py`)**:
  - Adds `_to_comic_caps(text)` and optional `comic_caps=False` parameter in `make_manga_overlay` for authentic Western comic ALL-CAPS lettering.
  - Distinguishes rectangular caption/narration boxes (`shape == "rect"`) from dialogue bubbles using clean upright regular/semi-bold styling.

### 2. Android & Mobile Companion App (Phase 2)
- **Opt-in LAN Companion Mode (`pdfmanager/gui.py`, `pdfmanager/server/common.py`)**:
  - `make_server(..., lan=False)` and `_is_lan_host(host)`: strictly opt-in (default remains `127.0.0.1` loopback-only). When `lan=True`, binds `0.0.0.0` and allows RFC-1918 private LAN `Host` headers (`10.*`, `192.168.*`, `172.16–31.*`, `*.local`) while keeping public/untrusted Host headers rejected.
  - Adds `GET /api/companion/info` returning `{ lan_enabled, url, version, pwa_manifest }`.
- **Touch-First Mobile Reader v2 (`pdfmanager/static/mobile-touch.js`)**:
  - Adds **RTL Manga swipe-direction toggle** (button on the floating mobile dock: when enabled, swipe-right advances to the next page and swipe-left goes back) + **Local AI Assistant** quick button on the mobile dock!
- **Android Kotlin App Phase 2 (`mobile/android/app/src/main/java/com/tempura446/zpdf/MainActivity.kt`)**:
  - Handles incoming `ACTION_VIEW` `application/pdf` intents via `ContentResolver` → base64 bridge into the WebView reader.
  - Replaces raw WebView error pages (`onReceivedError`) with a clean native offline/reconnect HTML card with a "Change Server URL" trigger (`window.ZpdfAndroid.openSettings()`) and "Retry" button.
