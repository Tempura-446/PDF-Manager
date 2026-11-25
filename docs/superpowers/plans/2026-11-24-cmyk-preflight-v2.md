# ZPDF v0.69.0 — Print-Production CMYK Preflight v2: Implementation Plan

> **For agentic workers:** Follow the `superpowers` skill (TDD RED → GREEN → REFACTOR, atomic commits, fresh `pytest` + invariant checks).

**Spec:** [`docs/superpowers/specs/2026-11-24-cmyk-preflight-v2-design.md`](../specs/2026-11-24-cmyk-preflight-v2-design.md)

---

### Phase 1: TDD RED — CMYK Preflight v2 Test Suite (`tests/test_cmyk_preflight_v2.py`)
- [x] Create `tests/test_cmyk_preflight_v2.py` with failing tests for:
  1. **Total Ink Coverage (TAC) check**: flags `high_tac` on both >300% CMYK vector fills (`0.9/0.9/0.8/0.9 k` = 350%) and >300% CMYK raster images (`240/240/220/240` = 368%), reporting accurate `max_tac_pct`, while passing a 220% CMYK page.
  2. **Overprint & Knockout audit**: flags `white_overprint` (`error`) when `/OP true` / `/op true` is active with white fill/text (`0/0/0/0 k` or `1/1/1 rg`), and flags `black_knockout_text` when pure-K 10pt text sets `/op false`.
  3. **PDF/X-1a & PDF/X-4 compliance profiles**: `profile="pdfx-1a"` flags missing `GTS_PDFX` `/OutputIntents`, missing `/Trapped`, missing `/TrimBox`, RGB colours (`error`), and transparency (`error`), while `profile="pdfx-4"` allows transparency when `GTS_PDFX` intent + `/TrimBox` + `/Trapped` are present.
  4. **One-click fixes (`fix_white_overprint`, `add_pdfx_intent`)**: `preflight.fix(..., fix_white_overprint=True, add_pdfx_intent=True)` removes white-overprint errors and adds `/Trapped /False`, `/TrimBox`, and `GTS_PDFX` `/OutputIntents`.
  5. **True CMYK Plate Separation & TAC Heatmap (`render_plate_preview`)**: verifies pure-K (`0/0/0/1 k`) renders dark on `"k"` plate and blank white on `"c"`/`"m"`/`"y"` plates, whereas rich CMYK renders on `"c"`/`"m"`/`"y"`/`"k"` and lights up hot-pixels on `"tac"` heatmap.
  6. **CLI & HTTP API (`--profile`, `--max-tac`, `/api/preflight/preview`)**: verifies CLI flags and HTTP `/api/preflight/check`, `/api/preflight/fix`, and `/api/preflight/preview` endpoints.

### Phase 2: Implement CMYK Preflight Engine & Plate Separator (`pdfmanager/preflight.py`)
- [x] Upgrade `preflight.check(...)` with `max_tac=300.0` and `profile="standard"` (`"standard" | "pdfx-1a" | "pdfx-4"`), adding TAC vector/image measurement, `/ExtGState` overprint tracking (`white_overprint`, `black_knockout_text`), and PDF/X-1a / PDF/X-4 structural checks.
- [x] Upgrade `preflight.fix(...)` with `fix_white_overprint=False` and `add_pdfx_intent=False`.
- [x] Implement `preflight.render_plate_preview(path, password=None, *, page=1, plate="all", max_tac=300.0, dpi=110)` with true operator/XObject channel separation and OOM-safe `core.render_scale` clamping.

### Phase 3: Wire CLI, HTTP Routes & Reader Output Preview UI
- [x] Add `--profile` and `--max-tac` to `pdfmanager/cli/parser.py` and `pdfmanager/cli/commands_edit.py`.
- [x] Wire `profile`, `max_tac`, `fix_white_overprint`, `add_pdfx_intent`, and `POST /api/preflight/preview` in `pdfmanager/server/tool_routes.py` and `pdfmanager/gui.py` (supporting both `id` zero-upload fast path and `file` upload).
- [x] Upgrade `checkPreflight()` in `pdfmanager/static/reader.js` (+ styles in `pdfmanager/static/reader.css` + route check in `tests/test_ui_wiring.py`) with Profile & TAC selectors, interactive **C / M / Y / K / TAC Heatmap** Output Preview, and one-click **Fix white overprint** / **Add PDF/X intent** actions.

### Phase 4: Verify, Docs & Ship `v0.69.0`
- [x] Run `pytest -q tests/test_cmyk_preflight_v2.py tests/test_preflight.py tests/test_ui_wiring.py tests/test_code_quality.py tests/test_imports.py tests/test_headers.py tests/test_privacy.py` + cold-startup check (`import pdfmanager.gui`).
- [x] Bump version to `0.69.0` (`__init__.py`, `CHANGELOG.md`, `ROADMAP.md`, Homebrew, Winget), open PR, wait for green CI, merge cleanly, publish `v0.69.0`, verify 12/12 assets, update `ZPDF-releases/README.md`, and bookmark `ZPDF v0.69.0 Release.url.json`.
