# ZPDF v0.68.0 — Manga / Comic Speech-Bubble Mode v2: Implementation Plan

> **For agentic workers:** Follow the `superpowers` skill (TDD RED → GREEN → REFACTOR, atomic commits, fresh `pytest` + invariant checks).

**Spec:** [`docs/superpowers/specs/2026-11-24-manga-bubble-mode-v2-design.md`](../specs/2026-11-24-manga-bubble-mode-v2-design.md)

---

### Phase 1: TDD RED — Manga v2 Tests (`tests/test_manga_v2.py`)
- [x] Create `tests/test_manga_v2.py` with failing tests for:
  1. **Dark / inverted speech bubbles**: black bubble with white ink strokes detected with `inverted=True`, dark `bg_rgb`, light `text_rgb`, and translated text rendered in white inside the dark bubble.
  2. **Shape classification (`rect` vs `oval` vs `polygon`)**: rectangular narration box classified as `"rect"`, oval speech bubble as `"oval"`, spiky/irregular burst as `"polygon"` with pixel-accurate `mask` preserved.
  3. **Reading direction (`rtl` vs `ltr`)**: `order_manga_bubbles(..., reading_dir="ltr")` orders left-to-right for Western comics; `"rtl"` orders right-to-left for Japanese/Chinese/Korean manga.
  4. **OCR preprocessing (`preprocess_bubble_for_ocr`)**: inverts dark bubbles to white background + dark text, upscales small crops (`min_side < 160`), and adds white margin padding.
  5. **Balanced comic wrapping (`_wrap_balanced`)**: eliminates single-word orphan lines when a cleaner balanced split exists.
  6. **Digital manga zero-OCR fast path**: PDF with selectable vertical Japanese text inside speech bubbles extracts & translates without calling `ocr_fn` at all (`ocr_calls == 0`).

### Phase 2: Implement Manga v2 Engine (`pdfmanager/translate/manga.py` & `pipeline.py`)
- [x] Upgrade `detect_speech_bubbles` to detect both light and dark/inverted bubbles, classify `"rect" | "oval" | "polygon"`, compute hole-filled eroded interior mask for `"polygon"` shapes, and support `reading_dir="rtl"|"ltr"`.
- [x] Add `preprocess_bubble_for_ocr(crop, *, inverted=False)` and `_wrap_balanced(...)`.
- [x] Add `extract_digital_bubble_text(plumber_page, bubble, img_w, img_h)` and wire zero-OCR fast path + auto `reading_dir` into `pdfmanager/translate/pipeline.py` (`mode="manga"`).
- [x] Upgrade `make_manga_overlay` to clean `"rect"` (rounded-rect), `"oval"` (ellipse), and `"polygon"` (pixel-accurate RGBA mask patch), use `text_rgb` (white on dark bubbles), and use balanced comic wrapping.
- [x] Re-export new helpers in `pdfmanager/translate/__init__.py`.

### Phase 3: Verify, Docs & Ship `v0.68.0`
- [x] Run `pytest -q tests/test_manga_v2.py tests/test_vertical_and_manga.py tests/test_translate*.py tests/test_code_quality.py tests/test_imports.py tests/test_headers.py tests/test_privacy.py` and cold-startup check (`import pdfmanager.gui`).
- [x] Bump version to `0.68.0` (`__init__.py`, `CHANGELOG.md`, `ROADMAP.md`, Homebrew, Winget), open PR, wait for green CI, merge cleanly, publish `v0.68.0`, verify 12/12 assets, update `ZPDF-releases/README.md`, and bookmark `ZPDF v0.68.0 Release.url.json`.
