# ZPDF v0.70.0 — Print-Production CMYK Preflight v3: Implementation Plan

> **For agentic workers:** Follow the `superpowers` skill (TDD RED → GREEN → REFACTOR, atomic commits, fresh `pytest` + invariant checks).

**Spec:** [`docs/superpowers/specs/2026-11-24-cmyk-preflight-v3-design.md`](../specs/2026-11-24-cmyk-preflight-v3-design.md)

---

### Phase 1: TDD RED — CMYK Preflight v3 Test Suite (`tests/test_cmyk_preflight_v3.py`)
- [x] Create `tests/test_cmyk_preflight_v3.py` with failing tests for:
  1. **Recursive Form XObject inspection & plate separation**: nested `/Subtype /Form` containing >300% CMYK fill + hairline + RGB color is detected by `preflight.check()` and accurately separated on `"c"`/`"k"`/`"tac"` plates in `render_plate_preview()`.
  2. **Pure-Python one-click CMYK fixes (no Ghostscript)**:
     - `fix_rich_black_text=True` removes `rich_black_text` warning and converts small text to `0 0 0 1 k`.
     - `clamp_tac=280.0` clamps both 355% CMYK vector fills and 368% CMYK images down to `<= 280%` TAC (`max_tac_pct <= 281.0`).
     - `fix_hairlines=0.25` thickens `0.08 pt` hairline strokes so `hairline_stroke` disappears.
     - `convert_rgb_to_cmyk=True` converts `DeviceRGB` vectors & images to `DeviceCMYK` so `rgb_color` disappears cleanly.
  3. **Spot ink inventory & Registration-colour hazard check**: detects named spot inks in `spot_colors`, warns `many_spot_colors` on >2 spot inks, and flags `registration_color` (`error`) when `/Separation /All` is used on the page.
  4. **Live Ink-Density Eyedropper (`compute_ink_grid` & `POST /api/preflight/inkmap`)**: verifies pure-K left rect reads `C≈0%, M≈0%, Y≈0%, K≈100%` and rich-CMYK right rect reads `TAC >= 340%` via both Python API and HTTP `/api/preflight/inkmap`.

### Phase 2: Implement CMYK Preflight v3 Engine (`pdfmanager/preflight.py`)
- [x] Refactor `preflight.check()` content-stream walker to recursively descend into nested Form XObjects (`/Subtype /Form`) with CTM matrix composition, Form-local `/Resources`, and cycle guards.
- [x] Add Spot/Separation ink inventory (`spot_colors`), `many_spot_colors`, and `/Separation /All` (`registration_color`) detection.
- [x] Implement pure-Python `fix()` transforms: `fix_rich_black_text`, `clamp_tac`, `fix_hairlines`, and `convert_rgb_to_cmyk` (recursing into Form XObjects as well).
- [x] Update `_build_single_plate_pdf_bytes()` to recursively separate nested Form XObjects.
- [x] Implement `compute_ink_grid(path, password=None, *, page=1, dpi=72)` returning `{width, height, cmyk_b64}`.

### Phase 3: Wire HTTP `/api/preflight/inkmap`, Fix Flags & Reader Eyedropper UI
- [x] Wire `fix_rich_black_text`, `clamp_tac`, `fix_hairlines`, `convert_rgb_to_cmyk`, and `POST /api/preflight/inkmap` in `pdfmanager/server/tool_routes.py` and `pdfmanager/gui.py`.
- [x] Upgrade `checkPreflight()` in `pdfmanager/static/reader.js` (+ `reader.css` + `tests/test_ui_wiring.py`) with the live **hover CMYK / TAC eyedropper readout** and one-click **"Fix all press issues"** button.

### Phase 4: Verify, Docs & Ship `v0.70.0`
- [x] Run `pytest -q tests/test_cmyk_preflight_v3.py tests/test_cmyk_preflight_v2.py tests/test_preflight.py tests/test_ui_wiring.py tests/test_code_quality.py tests/test_imports.py tests/test_headers.py tests/test_privacy.py` + cold-startup check (`import pdfmanager.gui`).
- [x] Bump version to `0.70.0` (`__init__.py`, `CHANGELOG.md`, `ROADMAP.md`, Homebrew, Winget), open PR, wait for green CI, merge cleanly, publish `v0.70.0`, verify 12/12 assets, update `ZPDF-releases/README.md`, and bookmark `ZPDF v0.70.0 Release.url.json`.
