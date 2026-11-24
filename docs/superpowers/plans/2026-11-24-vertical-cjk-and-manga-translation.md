# ZPDF v0.67.0 — Vertical CJK (Tategaki) & Manga Speech Bubble Translation: Plan

> **For agentic workers:** Follow the `superpowers` skill (TDD RED → GREEN → REFACTOR, atomic commits, fresh `pytest` + invariant checks).

**Spec:** [`docs/superpowers/specs/2026-11-24-vertical-cjk-and-manga-translation-design.md`](../specs/2026-11-24-vertical-cjk-and-manga-translation-design.md)

---

### Phase 1: TDD RED — Synthetic Vertical CJK (*Tategaki*) & Manga Speech Bubble Tests
- [x] Create `tests/test_vertical_and_manga.py` with failing tests for:
  - Vertical CJK detection & RTL column ordering (`is_vertical_cjk_page`, `extract_vertical_cjk_blocks`), including furigana/ruby stripping and vertical punctuation normalization.
  - End-to-end `translate_pdf` on a *tategaki* Japanese PDF in `bilingual-pages` and `bilingual` modes (verifying RTL paragraph order and zero cross-column scrambling).
  - Speech bubble detection (`detect_speech_bubbles`) on a synthetic raster manga page with 3 black-outlined white speech bubbles (top-right, top-left, bottom-right) + dark artwork, verifying exact RTL reading order and rejection of border margins / empty white panels.
  - End-to-end `translate_pdf(..., mode="manga")` on a raster manga PDF (with injectable `ocr_fn` & `translator`), verifying bubbles are cleaned and translated text is rendered centered inside each bubble while preserving artwork.

### Phase 2: Implement Track A — Vertical CJK (`pdfmanager/translate/vertical.py`)
- [x] Implement `is_vertical_cjk_page`, `normalize_vertical_punct`, `extract_vertical_cjk_blocks` in `pdfmanager/translate/vertical.py`.
- [x] Hook into `_extract_layout_blocks` in `pdfmanager/translate/layout.py` and re-export in `pdfmanager/translate/__init__.py`.

### Phase 3: Implement Track B — Manga Speech Bubble Mode (`pdfmanager/translate/manga.py`)
- [x] Implement `detect_speech_bubbles`, `order_manga_bubbles`, `make_manga_overlay` in `pdfmanager/translate/manga.py`.
- [x] Add `"manga"` to `MODES`, wire `mode="manga"` in `pdfmanager/translate/pipeline.py` (with optional `ocr_fn=None` for deterministic tests), and expose in `operations/catalog.py` and `cli/parser.py`.

### Phase 4: Verify, Docs & Ship `v0.67.0`
- [x] Run `pytest -q tests/test_vertical_and_manga.py tests/test_translate*.py tests/test_code_quality.py tests/test_imports.py tests/test_headers.py tests/test_privacy.py`.
- [x] Bump version to `0.67.0` (`__init__.py`, `CHANGELOG.md`, `ROADMAP.md`, Homebrew, Winget), open PR, wait for green CI, merge cleanly, publish `v0.67.0`, and verify 12/12 assets.
