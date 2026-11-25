# Track 2 (`v0.72.0`): Reader Comfort & Power-User Upgrade — Design Spec

## Overview
Adds three high-impact daily-driver reader upgrades to ZPDF while keeping 100% offline execution, strict `OpenFiles` filesystem sandboxing, and pixel-identical OCR text-layer editing:

1. **Smart Night / Sepia / E-Ink Reading Themes**
   - Replaces the single boolean Dark Pages toggle with four persistent page themes (`light`, `night`, `sepia`, `eink`) stored under `pdfm-reader-theme` (backward-compatible with legacy `pdfm-reader-dark`).
   - Applies `data-page-theme` to both the primary `.rd-container` and split-view `.rd-container`, with tuned CSS filters and desk backdrops for comfortable long-form reading.
   - Accessible from the Reader More menu and Command Palette.

2. **Workspace Sessions (`pdfmanager/sessions.py`)**
   - Named multi-tab workspace snapshots persisted atomically in `appdata.data_dir() / "sessions.json"` (gated by `privacy.remembering()`).
   - Stores open tabs (`path`, `name`, `page`, `scale`, `rotation`), `active_index`, and split-view configuration (`open`, `source_index`, `source_special`, `ratio`, `sync`, `offset`).
   - Strict security boundary: `POST /api/sessions` resolves `fileId`s exclusively via `self.opened` (never accepts arbitrary client paths); `GET /api/sessions` verifies files still exist on disk and issues fresh `fileId`s via `self.opened.add(path)`.
   - Managed from a clean Workspace Sessions modal in the Reader More menu and Command Palette.

3. **In-Place OCR Typo Editor (`pdfmanager/textlayer.py` + `checkocr.js`)**
   - `read_all_ocr_words(path, password=None, page=None)` inspects all invisible `/FZPDFOCR` words with page-space bounding boxes computed from `Tm` matrices, enabling click-to-fix on *any* OCR word even when confidence was high.
   - `replace_ocr_text(path, output, find_text, replace_text, password=None, case_sensitive=False)` performs batch find-and-replace across the invisible OCR text layer with proportional `Tm` horizontal rescaling, leaving page images pixel-identical.
   - Wired via `POST /api/ocr/words` and `POST /api/ocr/replace`, surfaced in the upgraded Check OCR bar with an **All OCR words** toggle and inline **Find & Replace** bar.
