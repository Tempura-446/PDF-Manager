# Reader Productivity & Print Preflight: Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three high-impact power-user capabilities to the reader and print pipeline:
1. **Side-by-side split view** (with optional synchronized scrolling) for comparing two documents or two sections of the same document.
2. **Interactive visual crop & N-up/poster preview** directly in the reader.
3. **Print preflight check** (low-DPI images, unembedded fonts, RGB vs CMYK, bleed/trim boxes) with one-click print fixes.

## Global constraints

- Offline; works across light, dark, and forced-colors themes; keyboard reachable.
- No extra dependencies; builds on `pdf.js`, `core.py`, `pdfacheck.py`, and `filefacts.py`.
- Python 3.10 to 3.14 on Linux and Windows; copyright header in every source file.

---

### Phase 1 (release A): Side-by-Side Split View in the Reader

- [x] **Split pane layout:** Toggle **Split view** from the reader toolbar / View menu / command palette (`Ctrl+\`) to show two independent document panes side by side with a draggable divider.
- [x] **Same-document or two-tab mode:** Open either a second tab in the right pane or a second independent view of the current tab (e.g., reading an appendix or legend alongside page 12).
- [x] **Synchronized scroll & zoom toggle:** Optional **Sync scroll** lock (matching page index + relative scroll offset, with a "Lock offset" option when revisions have an extra cover page).
- [x] **Keyboard & state:** `F6` / `Ctrl+Shift+\` switches active pane; closing split view returns cleanly to single-pane without losing unsaved edits.
- [x] **Tests (browser):** open two PDFs in split view, toggle sync scroll, verify both panes track and close cleanly (`tests/test_splitview.py`).

---

### Phase 2 (release B): Interactive Visual Crop & Imposition Preview

- [x] **Interactive crop mode in the reader:** Drag a shaded crop rectangle directly over a page with live width/height readout (in pt / mm / in) and corner/edge handles (`static/cropmode.js`).
- [x] **Apply scope:** Apply crop to **This page**, **Odd pages**, **Even pages**, **Page range**, or **All pages**; supports **Undo/Redo** and **Save** (updates `/CropBox` non-destructively, with an optional "Trim outside permanently" mode via `pdfmanager/crop.py` and `/api/crop`).
- [x] **N-up / Booklet / Poster preview:** Live first-sheet preview enabled for both N-up and Booklet tool panels before writing the output PDF.
- [x] **Tests:** crop box coordinates across rotated pages (`/Rotate` 90/180/270), odd/even scope, permanent trim, `/api/crop`, and Booklet preview (`tests/test_crop.py`).

---

### Phase 3 (release C): Print Preflight Check

- [x] **Preflight engine (`pdfmanager/preflight.py`):**
  - Detect low-resolution images (configurable threshold, e.g., `< 150 DPI` error, `< 300 DPI` warning) using effective rendered DPI on the page (transform matrix + pixel dimensions).
  - Detect unembedded fonts and Type 3 / substitute fonts.
  - Inspect colour spaces (RGB vs CMYK vs Gray vs Spot/Separation) and transparency groups.
  - Check page box geometry (`/MediaBox`, `/TrimBox`, `/BleedBox`) and flag inconsistent page sizes or missing bleed (e.g., `< 3 mm`).
  - Detect hairline strokes (`< 0.25 pt`) and rich-black small text warnings.
- [x] **One-click fixes (where safe):** flatten transparency / convert to CMYK or grayscale (via Ghostscript when present), normalize page sizes, add standard bleed/crop marks.
- [x] **CLI & GUI:** `pdfm preflight <input.pdf> [--min-dpi 300] [--json]` and a **Print preflight** dialog in the reader (clicking an issue jumps to the page, with one-click safe fixes).
- [x] **Tests:** synthetic PDFs with 72 DPI vs 300 DPI images, unembedded font, mixed page sizes, and RGB vs CMYK checks (`tests/test_preflight.py`).
