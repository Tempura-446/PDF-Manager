# ZPDF v0.71.0 — Book & Manga Publishing Studio: Implementation Plan

> **For agentic workers:** Follow the `superpowers` skill (TDD RED → GREEN → REFACTOR, atomic commits, fresh `pytest` + invariant checks).

**Spec:** [`docs/superpowers/specs/2026-11-24-book-manga-publishing-studio-design.md`](../specs/2026-11-24-book-manga-publishing-studio-design.md)

---

### Phase 1: TDD RED — Publishing Studio Tests (`tests/test_publishing_studio.py`)
- [x] Create `tests/test_publishing_studio.py` with failing tests for:
  1. `cbz_to_pdf`: natural sorting (`page_2.png` before `page_10.png`), `/ViewerPreferences /Direction /R2L` when `rtl_viewer=True`, zip-slip path traversal rejection.
  2. `pdf_to_cbz`: exports valid `.cbz` with zero-padded pages and `ComicInfo.xml` metadata.
  3. `translate_cbz`: end-to-end `.cbz` → Manga v2 bubble translation → `.cbz` output.
  4. `core.booklet`: RTL manga binding page order, multi-signature (`signature_pages=8`) section imposition, and `creep_mm` inward shift.
  5. `core.spine_width_mm`: accurate KDP/manga paper-stock spine & full-wrap cover dimensions.
  6. CLI commands (`cbz-to-pdf`, `pdf-to-cbz`, `spine`, `booklet --binding rtl --signature 8`).

### Phase 2: Implement CBZ Engine & Booklet/Spine Upgrades
- [x] Create `pdfmanager/cbz.py` (with license header, zip-slip/bomb guards, natural sort, `cbz_to_pdf`, `pdf_to_cbz`, `translate_cbz`).
- [x] Upgrade `core.booklet` (`binding`, `signature_pages`, `creep_mm`) and add `core.spine_width_mm` in `pdfmanager/core/geometry.py`, re-exporting in `pdfmanager/core/__init__.py`.

### Phase 3: Wire CLI & GUI Operations
- [x] Add `cbz-to-pdf`, `pdf-to-cbz`, `spine`, and upgraded `booklet` flags in `pdfmanager/cli/parser.py` and `pdfmanager/cli/commands_edit.py`.
- [x] Upgrade `booklet` and add `pdf-to-cbz` in `pdfmanager/server/operations/catalog.py`, `dispatch.py`, and icons in `pdfmanager/static/app-core.js`.

### Phase 4: Verify, Docs & Ship `v0.71.0`
- [x] Run `pytest -q tests/test_publishing_studio.py tests/test_ui_wiring.py tests/test_code_quality.py tests/test_imports.py tests/test_headers.py tests/test_privacy.py` + cold-startup check (`import pdfmanager.gui`).
- [x] Bump version to `0.71.0` (`__init__.py`, `CHANGELOG.md`, `ROADMAP.md`, Homebrew, Winget), open PR, wait for green CI, merge cleanly, publish `v0.71.0`, verify 12/12 assets, update `ZPDF-releases/README.md`, and bookmark `ZPDF v0.71.0 Release.url.json`.
