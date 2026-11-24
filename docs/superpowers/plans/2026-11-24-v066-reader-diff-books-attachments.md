# ZPDF v0.66.0: Implementation Plan

> **For agentic workers:** Follow the `superpowers` skill (TDD RED → GREEN → REFACTOR, atomic commits, `node --check` + `pytest` + invariant tests after each track).

**Spec:** [`docs/superpowers/specs/2026-11-24-v066-reader-diff-books-attachments-design.md`](../specs/2026-11-24-v066-reader-diff-books-attachments-design.md)

---

### Track A: Interactive Side-by-Side Diff Viewer in Reader Split View
- [x] **A1 (Backend + TDD)**: Include `dpi` in `compare.compare_summary(...)`, add `POST /api/reader/compare` in `reader_routes.py` + `gui.py` (supporting both `old_id`/`new_id` and multipart `file_old`/`file_new`), and add integration tests in `tests/test_v066_features.py`.
- [x] **A2 (Frontend)**: Fix `/api/file/${tab.fileId}` in `splitview.js`, add **Diff mode** toggle, Prev/Next change buttons, aligned page syncing (`row.old <-> row.new`), and red/green highlight overlays in `splitview.js`, `reader-render.js`, and `reader.css`.

### Track B: Book & Reading Experience Suite
- [x] **B1 (Backend + TDD)**: Add `_clone_and_remap_outlines(...)` in `translate/layout.py`, wire it into `bilingual-pages` in `translate/pipeline.py` so chapter bookmarks survive with remapped interleaved page targets + translated titles, and add unit tests in `tests/test_translate.py`.
- [x] **B2 (Frontend)**: Add **Live Translation (`__translate__`)** split-view reading mode in `splitview.js` + `reader.css` (auto-translates current page as you flip pages, per-page cache, `From`/`To` selectors, `dir="auto"` RTL, Original/Translated toggle).
- [x] **B3 (Frontend)**: Persist `maxPage` / `percent` reading progress per document fingerprint in `reader-render.js` / `reader-nav.js` and display reading progress percentage in the Reader page readout.

### Track C: Embedded Attachments GUI + Keyboard Crop & Redaction
- [x] **C1 (Backend + TDD)**: Add `POST /api/attachments/update` in `reader_routes.py` + `gui.py` calling `filetools.attach(...)` and add HTTP integration tests in `tests/test_v066_features.py`.
- [x] **C2 (Frontend)**: Add **"Attach file…"** and per-item **"Remove"** buttons to the Reader's Attachments list in `reader.js` using `this._changeDocument(...)`.
- [x] **C3 (Frontend)**: Add keyboard Arrow-key nudge/resize (`1 pt` / `Shift=10 pt`, `Alt=resize`, `Enter=apply`, `Delete=remove`, `Escape=close`) to `cropmode.js`, `annotate.js`, `reader.js`, and `regions.js`.

### Track D: Multiprocessing `core.batch_run` Hardening + `RELEASE_READINESS.md` & Release (`v0.66.0`)
- [x] **D1 (Backend + TDD)**: Harden `core.batch_run` in `pdfmanager/core/io.py` (`BrokenProcessPool` handling + `mode="thread"|"process"` option) and add process-pool tests in `tests/test_v066_features.py`.
- [x] **D2 (Docs & Release)**: Refresh `docs/RELEASE_READINESS.md`, update `docs/ROADMAP.md`, bump version to `0.66.0` (`__init__.py`, `CHANGELOG.md`, Homebrew, Winget), verify with `pytest` + `node --check` + cold-startup check, open PR, merge on green CI, and publish `v0.66.0`.
