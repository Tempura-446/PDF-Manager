# ZPDF v0.66.0 — Interactive Diff, Book Reading Suite, Attachments GUI, Keyboard Crop/Redaction & Batch Hardening

**Date:** 2026-11-24  
**Target Release:** `v0.66.0`  
**Status:** Approved ("Let's do it all")

---

## 1. Track A — Interactive Side-by-Side Diff Viewer in Reader Split View
- **Backend (`pdfmanager/server/reader_routes.py`, `pdfmanager/gui.py`, `pdfmanager/compare.py`)**:
  - Add `POST /api/reader/compare` accepting either uploaded `file_old`/`file_new` or opened `old_id`/`new_id` (+ optional `old_password`, `new_password`, `mode`, `dpi`).
  - Include `dpi` in `compare_summary(...)` output so the Reader can scale pixel `areas` (`px * 72 / dpi`) cleanly onto PDF.js viewports.
- **Frontend (`pdfmanager/static/splitview.js`, `pdfmanager/static/reader-render.js`, `pdfmanager/static/reader.css`)**:
  - Fix latent `_bytesForSplitTab` URL bug (`/api/open/` → `/api/file/`).
  - Add a **Compare / Diff** toggle in Split View bar when comparing two different PDFs.
  - Render subtle red/green highlight overlays on changed `areas` in both left and right PDF.js panes on `pagerendered`.
  - Add **Prev change / Next change** jumper buttons and change badge (`N changed`) in Split View bar, syncing left/right pages via aligned `changes` pairs (`row.old <-> row.new`).

---

## 2. Track B — Book & Reading Experience Suite
- **Live Bilingual Split-View Reading (`pdfmanager/static/splitview.js`, `pdfmanager/static/reader.js`, `pdfmanager/static/reader.css`)**:
  - Add a `"__translate__"` ("Live translation") mode to Split View so readers see the original PDF on the left and a live, auto-updating translated reading pane on the right as they turn pages.
  - Uses existing `POST /api/translate/page` with zero-upload `fileId` fast path, persisted `From`/`To` languages, `dir="auto"` RTL support, in-memory per-page cache `(docKey, page, from, to)`, and a 1-click Original/Translated toggle.
- **Preserved & Remapped Bookmarks (`/Outlines`) in `bilingual-pages` (`pdfmanager/translate/pipeline.py`, `pdfmanager/translate/layout.py`)**:
  - Track `orig_page_map[src_idx] = dst_idx` while emitting interleaved pages.
  - Clone `/Outlines` from `reader`, remap every outline item's `/Dest` and `/A /GoTo /D` page reference from `reader.pages[src_idx]` to `writer.pages[dst_idx]`, and translate bookmark `/Title`s.
- **Persistent Reading Progress (`pdfmanager/static/reader-render.js`, `pdfmanager/static/reader-nav.js`)**:
  - Persist `maxPage`, `totalPages`, and `percent` alongside `{ page, scale, rotation }` in `pdfm-pos:<fingerprint>` and display a subtle progress readout (`p. 42 / 240 · 18%`) in the Reader toolbar tooltip/status.

---

## 3. Track C — Embedded Attachments GUI + Keyboard Crop & Redaction
- **Attachments GUI (`pdfmanager/server/reader_routes.py`, `pdfmanager/gui.py`, `pdfmanager/static/reader.js`)**:
  - Add `POST /api/attachments/update` calling `filetools.attach(...)` (supporting adding uploaded files, removing named attachments, or removing all, while preserving PDF encryption when applicable).
  - Upgrade the Reader's Attachments section in Document Properties with **"Attach file…"** and per-item **"Remove"** buttons routed through `this._changeDocument(...)` for full Undo/Redo + Save support.
- **Keyboard Crop & Redaction (`pdfmanager/static/cropmode.js`, `pdfmanager/static/annotate.js`, `pdfmanager/static/reader.js`, `pdfmanager/static/regions.js`)**:
  - **Visual Crop (`cropmode.js`)**: Arrow keys nudge crop box (`1 pt`, `Shift+Arrow` = `10 pt`), `Alt+Arrow` resizes right/bottom edges, `Enter` applies crop, `Escape` closes crop mode; box gets `tabindex="0"` and ARIA label.
  - **Reader Redaction (`annotate.js`)**: Track selected redaction mark (`_redactSel`), focusable/selectable marks, Arrow keys nudge (`1 pt` / `Shift` = `10 pt`), `Delete`/`Backspace` removes selected mark, `Tab` cycles marks on current page, `Ctrl+Enter` applies redactions.
  - **Tool-page `RegionEditor` (`regions.js`)**: Arrow keys nudge selected crop/redact/form box (`1 pt` / `Shift` = `10 pt`).

---

## 4. Track D — Multiprocessing `core.batch_run` Hardening + `RELEASE_READINESS.md` Refresh
- **Hardened `core.batch_run` (`pdfmanager/core/io.py`)**:
  - Handle `BrokenProcessPool` cleanly (fall back or surface structured error), support `mode="thread" | "process"` alias alongside `processes=bool`, and add dedicated unit tests for process-pool execution, error isolation, and cancellation.
- **Docs Refresh (`docs/RELEASE_READINESS.md`, `docs/ROADMAP.md`, `CHANGELOG.md`)**:
  - Update `docs/RELEASE_READINESS.md` from `0.60.3` → `0.66.0` reflecting commercial-safe MADLAD-400 translation, interactive diff viewer, live bilingual split-view reading, attachments GUI, keyboard crop/redaction, and Wave 2 modularization.
