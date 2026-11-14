# Better OCR: Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make searchable (OCR) reads more pages right, says where it wasn't sure, lets you correct what it got wrong, and is faster on long files. Everything stays offline.

**Where it stands (0.33):** `ocr.py` runs PaddleOCR's PP-OCR models through rapidocr on ONNX Runtime, with Tesseract through OCRmyPDF as the second engine.
- Pages are rendered at 300 dpi, turned upright, and deskewed when at least three wide boxes agree.
- Multi-column pages are read column by column.
- Words the model is less than 0.3 sure of are dropped.
- A page that already has any text is skipped.
- The text layer is ZPDF's own (`textlayer.py`).

**What's missing:**
1. **No accuracy number.** Changes are judged by a few test pages, not by a score on a fixed set.
2. **Hard pages:** faint or uneven photocopies, phone photos (curved, shadowed), small print (under about 8 pt), coloured backgrounds, stamps over text.
3. **Pages with a bad text layer are skipped.** A scan that an old program gave a garbage layer keeps the garbage; a page that mixes real text with a scanned picture of text only has the real text.
4. **Unsure words vanish silently.** The user isn't told which pages read badly and can't fix a wrong word.
5. **Tables and forms** are read as loose lines; columns of numbers can come out in the wrong order.
6. **Speed:** one page at a time on the CPU.
7. **Model choice:** one model size; no "best accuracy" choice for hard files.

## Global constraints

- Offline. Models that aren't shipped are fetched once, checked by SHA-256, as the script models are now.
- The page image is never re-encoded unless the user asks (`force`), as today.
- No words are dropped that 0.33 kept: each change is measured on the holdout and must not lower the score on any category.
- Python 3.10 to 3.14, Linux and Windows. No model identifiers in code or commits.

## Measuring it (first, before any change)

- [x] Build an OCR holdout (`tests/ocrscore.py` makes it; nothing but code is committed): pages rendered from known text, then degraded the ways real scans are. Categories: clean 300 dpi; 150 dpi; faint; skewed 3°; noisy photocopy; phone photo (perspective and shadow); small print; two and three columns; a table of numbers; a form; mixed text and picture; CJK; a page with a garbage text layer.
- [x] A scorer (`python tests/ocrscore.py`; still to do: run it in the weekly sweep workflow and keep the report): character error rate and word error rate per category, reading-order errors (Kendall tau of line order), numbers read exactly, seconds per page. Writes a table; the weekly sweep workflow runs it and keeps the report.
- [x] Record the 0.33 baseline in this file.

**Baseline (0.33.0, 2026-11-14, `python tests/ocrscore.py`, built-in engine, a Linux machine):**

| Page | Characters wrong | Words wrong | Numbers exact | Seconds |
|---|---|---|---|---|
| Clean, 300 dpi | 0.0% | 0.0% | 100% | 1.4 |
| 150 dpi | 0.0% | 0.0% | 100% | 1.2 |
| Faint print | 0.0% | 0.0% | 100% | 1.3 |
| Skewed 3 degrees | 0.0% | 0.0% | 100% | 1.3 |
| Noisy photocopy | 0.2% | 2.4% | 0% | 1.2 |
| Phone photo | 0.2% | 2.4% | 100% | 1.3 |
| Very faint, speckled | 0.0% | 0.0% | 100% | 1.3 |
| Fax (100 dpi, black and white) | 0.2% | 1.2% | 100% | 0.7 |
| Blurred phone photo | 0.2% | 1.2% | 100% | 1.3 |
| Grey paper with a stamp across | 1.7% | 1.2% | 100% | 1.4 |
| Small print (7 pt) | 0.0% | 0.0% | 100% | 1.7 |
| Small print at 150 dpi | 0.0% | 0.0% | 100% | 1.4 |
| Two columns | 0.2% | 2.2% | 100% | 1.3 |
| Three columns | 0.0% | 0.0% | 100% | 1.6 |
| Table of numbers | 0.0% | 0.0% | 100% | 1.2 |
| Form | 0.0% | 0.0% | 100% | 1.2 |
| Chinese | 1.6% | 20.0% | 100% | 1.0 |

| Through the tool | Its words found afterwards | Note |
|---|---|---|
| Scan with a rubbish text layer | 0% |  |
| Real text with a scanned figure | 17% |  |

**Reading the baseline:**
- On made pages the engine already reads nearly everything, also when they are faint, speckled, low-resolution, blurred or photographed at a slant. Made pages can't stand in for real bad scans: **Phase 2 (cleaning the picture) waits for a handful of real scans that read badly**, added to the set as private files (`--extra FOLDER` of picture + true text), so each cleaning step is judged on something it can improve.
- The clear failures are in the tool, not in the reading: a scan with a rubbish text layer stays unreadable (0 % of its words), and a scanned figure on a page of real text is never read (17 %). **Phase 1.2 is the first thing to build.**
- The noisy photocopy's numbers (0 % exact) and the two-column page's small errors are worth a look in Phase 1.
- The Chinese page's word figure counts unspaced lines as words; its character figure (1.6 %) is the one to follow.
- About 1.3 seconds a page on the processor: Phase 5's target (twice as fast on long files) stands.

| Score | Target |
|---|---|
| Character error rate, clean pages | no worse than baseline |
| Character error rate, faint / noisy / photo / small print | at least a third lower than baseline |
| Numbers in tables read exactly | ≥ 99 % |
| Reading-order errors on multi-column pages | 0 |
| Pages with a garbage layer detected and re-read | 100 % of the holdout's |
| Seconds per page (CPU, 4 cores) | at least 2× faster than baseline on a 50-page file |

---

### Phase 1 (release A): the score, and pages that are skipped wrongly

**Task 1.1: holdout and scorer** (above).

**Task 1.2: bad and partial text layers**
- [ ] Judge an existing text layer before skipping the page: the share of its characters that are letters, digits or common punctuation; whether its words are in the picture's area; whether the text covers the inked area (render at low resolution and compare where there is ink with where the layer has words).
- [ ] A garbage layer: the page is read again and the old layer is replaced (it says so). A page with real text and a picture of text: only the picture's area is read, and the new words are added beside the real text, never over it.
- [ ] Test: the holdout's garbage-layer page comes out searchable for its real words; a page with real text and a scanned figure gains the figure's words and keeps its own text once.

### Phase 2 (release B): hard pages

**Task 2.1: cleaning the picture before reading** (for reading only; the page keeps its image)
- [ ] Per page, measure contrast, background evenness and stroke width, and apply only what the page needs: background flattening (uneven light, coloured paper), contrast stretch for faint print, light denoising for photocopy speckle.
- [ ] Small print: when the median text height is under about 20 px at 300 dpi, read from a 450 or 600 dpi render of those regions.
- [ ] Phone photos: find the page's corners and straighten the perspective (`pagephoto.py` already does this for Scan); mild dewarping of curved lines by fitting each text line's baseline.
- [ ] Each step is kept only if the holdout score for its category improves and no other category gets worse.

**Task 2.2: two readings, the better kept**
- [ ] When a line's confidence is low, read it again from the cleaned or enlarged picture and keep the reading with the higher confidence; never both.
- [ ] Test: the faint and small-print holdout pages meet the target.

**Task 2.3: a "Best accuracy" choice**
- [ ] The larger PP-OCR models as an optional download ("Best accuracy, slower"), next to the built-in one, chosen in the tool and in Settings. Fetched once and checked, as the script models are.
- [ ] Test: with the model faked, the choice reaches the engine; the scorer reports both.

### Phase 3 (release C): saying what it wasn't sure of, and fixing it

**Task 3.1: a report**
- [ ] After OCR: pages read, pages skipped and why, and the pages with many unsure words ("3 pages may have mistakes: 4, 9, 17"), with a link that opens the page.
- [ ] Unsure words are kept in the text layer when their confidence is between 0.3 and 0.6, and marked as unsure in a private key of the file (not shown in other programs).

**Task 3.2: correct recognised text in the reader**
- [ ] A "Check OCR" mode: unsure words are outlined on the page; click a word to see its picture beside what was read, type the right word, Tab to the next. Only the invisible text changes, never the picture.
- [ ] Test (browser): correcting one word changes what Find and Copy give, and nothing drawn changes (pixel-identical page).

### Phase 4 (release D): tables and forms

- [ ] Find ruled and unruled tables on a scanned page (the layout model and `tables.py` already find them on text pages), and read cell by cell, so a column of numbers stays a column. Digits get a digits-only second reading when a cell looks numeric (O/0, l/1, S/5).
- [ ] The text layer keeps cell order (row by row), so Copy and PDF to Excel work on scans.
- [ ] Test: the holdout's table of numbers exports to Excel with every number exact.

### Phase 5 (release E): speed

- [ ] Pages in parallel: a small pool of worker processes, each with its own engine (the render lock stays per process), sized to the cores and memory; progress and Cancel as now.
- [ ] On Windows, try ONNX Runtime's DirectML provider (the PC's graphics card) when it is there, and fall back to the CPU at the first error. Off by default until it has been checked on a few PCs; a Settings choice.
- [ ] Skip what needs no reading: blank pages, and pages whose picture is the same as one already read (hash of the image stream).
- [ ] Test: a 50-page holdout file is at least twice as fast with four workers, and the text is identical to one worker's.

---

## Order and size

| Release | Phase | Size | Risk |
|---|---|---|---|
| A | 1: score; bad and partial layers | medium | low |
| B | 2: hard pages; best-accuracy model | medium–large | medium: each cleaning step must earn its place |
| C | 3: report; correct recognised text | medium | low |
| D | 4: tables and forms | medium | medium |
| E | 5: speed | medium | medium: memory with several engines; graphics drivers |

**Place in the roadmap:** release A next to paragraph reflow B (they touch different code). Each release needs a Windows check with real scans: a faint photocopy, a phone photo, a scanned invoice with a table, a long report.
