# Markdown & Review Exports: Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn PDFs and their review markups into clean, structured text formats completely offline:
1. **PDF → Markdown** that preserves reading order, headings, lists, Markdown tables, code/monospace blocks, and (optionally) extracted images — ready for Obsidian, Notion, docs, and local LLM workflows.
2. **Annotation & review summary export** that collects every highlight, sticky note, callout, and markup across a PDF (or drawing sheet set) into Markdown, CSV, or a printable summary PDF.

**Already in ZPDF:**
- Column-aware reading order and block/heading/list classification (`layout.py`, `accessibility.py`);
- Ruled and borderless table extraction (`tables.py`);
- Image extraction (`pageimages.py`);
- Comment and markup reading (`comments.py`, `markup.py`).

## Global constraints

- Offline, zero new dependencies (uses existing `pypdf`, `pdfplumber`, `Pillow`, `reportlab`).
- Deterministic UTF-8 output; clean escaping of Markdown special characters inside tables and prose without corrupting URLs or code.
- Works on the CLI (`pdfm to-md`, `pdfm export-comments`), in the GUI (Tools & Reader More menu), in recipes, and in watch folders.
- Python 3.10 to 3.14 on Linux and Windows; copyright header in every source file.

---

### Phase 1 (release A): PDF → Markdown (`markdown_export.py`)

**Task 1.1: Core exporter (`pdfmanager/markdown_export.py`)**
- [ ] Extract page blocks in reading order using `layout.py` / `pdfplumber` (multi-column aware, skipping repeated running headers/footers when detected).
- [ ] Detect headings (`#`–`####`) from font-size tiers and bold hierarchy (reusing structure/heading heuristics from `accessibility.py` / `office.py`).
- [ ] Preserve inline emphasis (`**bold**`, `*italic*`, `` `code` ``) and hyperlinks (`[text](url)` from `/Link` annotations).
- [ ] Detect bulleted and numbered lists (including nested indentation).
- [ ] Detect tables via `tables.py` and render as GitHub-Flavored Markdown pipe tables, deduplicating table text so cells aren't emitted twice as paragraphs.
- [ ] Optional image extraction (`--images` / `extract_images=True`): save page images into `<stem>_assets/` and insert `![Figure N](<stem>_assets/page1_img1.png)` at their reading-order position.
- [ ] Options: `pages` range, `page_breaks` (HTML comment or `---` separator between pages), `front_matter` (optional YAML metadata: title, author, pages, source).
- [ ] Tests (`tests/test_markdown_export.py`): multi-column text, headings, bold/italic/code, links, bulleted/numbered lists, ruled table deduplication, image extraction, page ranges, and empty/scanned-only PDFs (graceful notice).

**Task 1.2: CLI, GUI, Recipes, and Docs**
- [ ] CLI command `pdfm to-md <input.pdf> [-o out.md] [--pages ...] [--images] [--page-breaks] [--front-matter]`.
- [ ] GUI tool card **PDF → Markdown** (under Convert) + **Export as Markdown** action in the reader's More menu / command palette.
- [ ] Recipe / watch-folder support (`to-md` step).
- [ ] Update `README.md` and `CHANGELOG.md`.

---

### Phase 2 (release B): Annotation & Review Summary Export

**Task 2.1: Unified review extractor (`pdfmanager/review_export.py` or in `comments.py`)**
- [ ] Collect all user annotations across a PDF (or list of PDFs / sheet set): highlights (with the highlighted passage text extracted from the page!), underlines, strikethroughs, sticky notes, free-text callouts, construction markups (clouds, arrows, stamps, measurements).
- [ ] Preserve page number (and PDF page label / sheet number when present), author, modified date, annotation type, colour label, quoted text, and comment body.
- [ ] Filter options: by author, annotation type, colour, or page range.

**Task 2.2: Output formats & UI**
- [ ] Export to **Markdown** (grouped by page or by type/colour, with blockquoted highlighted text and comment underneath).
- [ ] Export to **CSV** (spreadsheet-ready punch list / review log).
- [ ] Export to **Summary PDF** (clean printable review report with page references, colour swatches, and counts).
- [ ] CLI command `pdfm review-summary <input.pdf> [-o out.md|.csv|.pdf] [--format md|csv|pdf] [--group-by page|type|author]`.
- [ ] Reader **Comments** sidebar button: **Export summary…** (Markdown / CSV / PDF).
- [ ] Tests covering highlights with quoted text, sticky notes, construction markups, filters, and all three output formats.
