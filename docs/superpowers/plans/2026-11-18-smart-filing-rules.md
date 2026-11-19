# Smart Auto-Rename & Filing Rules: Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically name and file PDFs (invoices, receipts, statements, contracts, scanned packets) based on their contents — completely offline, with a dry-run preview so nothing is ever renamed or moved by surprise.

**Already in ZPDF:**
- Folder organization (`organize.py`), watch folders (`watch.py`, `watchfolders.py`), recipes (`recipes.py`);
- Title-block extraction and template naming (`sheetsets.py`);
- Built-in OCR (`ocr.py`) for scanned files without a text layer.

## Global constraints

- Offline; no cloud APIs.
- Always safe: dry-run preview before applying, never silently overwrite an existing file (collision suffix `-2`, `-3`), and an undo log for the last batch rename/move.
- Cross-platform safe filenames (strips Windows-forbidden characters, trims length, normalizes whitespace).
- Python 3.10 to 3.14 on Linux and Windows; copyright header in every source file.

---

### Phase 1 (release A): Rule Engine & Dry-Run Preview

- [x] **Rule format (`pdfmanager/filing.py`):** JSON/YAML-serializable rules stored in app data, each with:
  - **Conditions:** filename glob, text contains / regex match (with named capture groups `(?P<vendor>...)`), page count, PDF metadata (author/title/subject), or date found on page 1.
  - **Built-in extractors:** `{date}` / `{year}` / `{month}` (ISO `YYYY-MM-DD` normalized from common date formats on page 1), `{first_line}`, `{title}`, `{pages}`, `{orig_name}`, plus any named regex capture group `{vendor}`, `{invoice_no}`, etc.
  - **Actions:** rename template (e.g., `{year}/{date}_{vendor}_{invoice_no}.pdf`), optional subfolder move, optional tag/metadata write, optional OCR-first if scanned.
- [x] **Dry-run & undo:** `plan_filing(files, rules)` returns `(src, dst, matched_rule, reason)` without touching disk; `apply_filing(plan)` writes an undo manifest so `undo_filing()` restores original paths.
- [x] **CLI:** `pdfm auto-file <folder-or-files> --rules rules.json [--apply] [--undo]`.
- [x] **Tests (`tests/test_filing.py`):** date extraction/normalization, named regex captures, collision avoidance, invalid-character sanitization, dry-run vs apply, and undo rollback.

---

### Phase 2 (release B): GUI Rule Builder & Watch-Folder Integration

- [x] **Rule editor & live preview API:** `/api/filing/rules` (GET/POST) and `/api/filing/preview` (dry-run match & target path preview against a sample PDF).
- [x] **Watch folder integration:** Any Watch folder can enable `auto_file=True` to run the filing rule set on processed PDFs so new scans land directly in e.g. `Done/2026/Invoices/2026-11-18_INV-1042.pdf`.
- [x] **Starter templates:** Built-in editable presets (`STARTER_RULES`) for Invoices/Receipts, Bank Statements, and Dated Scans.
- [x] **Tests (`tests/test_filing_watch.py`):** filing rules/preview HTTP API and watch folder routing a new PDF into the right dated subfolder.
