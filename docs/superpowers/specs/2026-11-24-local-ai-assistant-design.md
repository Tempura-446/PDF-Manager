# Track 3 (`v0.74.0`): Local AI Document Assistant — Design Spec

## Overview
Adds an optional, privacy-first **Local AI Document Assistant** to ZPDF reusing the existing Ollama / LM Studio / OpenAI-compatible configuration in Settings (`translate_api_url`, `translate_model`, `translate_api_key`):

1. **Core Engine (`pdfmanager/assistant.py`)**
   - Reuses `i18n._http_post_json` exclusively (no direct `urlopen`), preserving `tests/test_privacy.py` guarantees, loopback-HTTP allowance, HTTPS-only remote enforcement, redirect sanitization, and `policy.check_network_allowed`.
   - `select_relevant_pages(pages, query, max_pages=6, max_chars=18000)`: lightweight zero-dependency lexical scorer so large PDFs fit local LLM context windows.
   - `ask_pdf(...)`: answers questions grounded in `[Page N]` excerpts and parses `[p. N]` / `[Page N]` citations into a deduplicated `citations` list.
   - `summarize_pdf(...)`: summarizes full PDFs or page ranges in `bullets`, `executive`, or `study` styles with page citations.
   - `extract_structured(...)`: extracts structured `key_values` or `table` JSON from PDFs and formats CSV output when requested.

2. **Server Routes (`pdfmanager/gui.py` + `pdfmanager/server/reader_routes.py`)**
   - `POST /api/ai/ask`
   - `POST /api/ai/summarize`
   - `POST /api/ai/extract`

3. **Reader UI (`pdfmanager/static/ai-assistant.js` + `reader.js`)**
   - Modal/drawer with **Ask**, **Summarize**, and **Extract** tabs.
   - Clickable `[p. N]` citation pills jump directly to the cited page in the Reader.
   - One-click **Copy** and **Download JSON / CSV** for extracted data.
