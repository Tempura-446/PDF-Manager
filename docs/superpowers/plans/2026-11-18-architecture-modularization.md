# Architecture Modularization: Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Break up ZPDF's five largest monolithic files into cohesive, domain-focused modules so upcoming UI-heavy tracks (Language Packs / i18n, Check OCR mode, Split View, Security Phase 6–7) are faster, safer, and cleaner to implement and test.

**Current monolith sizes (as of `0.57.0`):**
- `pdfmanager/gui.html` (~345 KB)
- `pdfmanager/gui.py` (~253 KB)
- `pdfmanager/static/reader.js` (~194 KB)
- `pdfmanager/textedit.py` (~167 KB)
- `pdfmanager/forms.py` (~138 KB)

## Global constraints

- **Zero behaviour changes:** Pure structural refactors; every existing unit, integration, and browser test must pass without relaxing assertions.
- **Public API compatibility:** Keep re-exports in `gui.py`, `forms.py`, and `textedit.py` so external callers, CLI, and tests continue to work unchanged.
- **Incremental PRs:** One subsystem per PR so diffs stay reviewable and bisectable.
- Packaging check: `pyproject.toml` (`package-data`), `packaging/build_app.py`, and `packaging/release.py` must continue to bundle all new sub-modules/static files.

---

### Phase 1: Split `gui.py` HTTP routes by domain

- [x] Create `pdfmanager/server/` package (or `gui_routes/`):
  - `common.py` (request parsing, auth/CSRF token, path safety, JSON/error helpers, temp-file lifecycle)
  - `operations.py` (operation specs `OPERATIONS`, file/folder operation runners, preview & inspect helpers)
  - `reader_routes.py` (open, render, save, annotations, layers, text-edit, compare)
  - `form_routes.py` (fill, prepare-form CRUD, field properties, preview, check-form, import/export, fill-many)
  - `tool_routes.py` (merge, split, compress, OCR, convert, DXF, sheet sets, redact, sign, preflight)
  - `scan_watch_routes.py` (scanner discovery/scan, watch folders, recipes, settings, update)
- [x] Keep `gui.py` as the thin entry point (`main`, `main_app`, server startup, pywebview window bootstrap) that registers route tables.
- [x] Verify full test suite + PyInstaller build (`build_app.py --self-test`).

---

### Phase 2: Extract inline CSS and JS out of `gui.html`

- [x] Move inline `<style>` blocks from `gui.html` into `pdfmanager/static/app.css`.
- [x] Move inline `<script>` tool-panel controllers from `gui.html` into focused static modules (`static/early-open.js`, `static/app-core.js`, `static/tools.js`, `static/settings.js`, `static/scan-ui.js`, `static/watch-ui.js`).
- [x] Keep `gui.html` strictly declarative markup so **Language Packs Phase 1 (i18n)** can annotate and translate UI strings cleanly without fighting mixed HTML/JS/CSS.
- [x] Verify all browser tests pass in light, dark, and forced-colors themes.

---

### Phase 3: Split `reader.js`, `textedit.py`, and `forms.py`

- [x] Extract remaining cohesive subsystems from `static/reader.js` (`reader-nav.js`, `reader-render.js`, `reader-save.js`).
- [x] Split `forms.py` into `pdfmanager/forms/` (`__init__.py` re-exporting public API, `_base.py`, `fields.py`, `scripts.py`, `data.py`, `actions.py`, `check.py`).
- [x] Split `textedit.py` into `pdfmanager/textedit/` (`__init__.py`, `extract.py`, `rewrite.py`, `reflow_ops.py`).
- [x] Verify full test suite (`--perf`) and packaging.
