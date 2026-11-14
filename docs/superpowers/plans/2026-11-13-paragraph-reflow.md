# Better Multi-Line Paragraph Reflow: Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewriting a paragraph in the reader looks as if the document's author had typed it. The words keep their own styles, lines break as a typesetter would break them, lists and indents keep their shape, and the text flows around what's on the page and on to the next page, all without disturbing anything that wasn't changed.

**Where it stands (0.27):** `textedit.py` (`_Page.paragraph`, `_wrap`, `_alignment`) and `textflow.py` (`Flow`) already do the following:
- A paragraph is rewritten in its own font, at the same width and line spacing, and stays justified if it was.
- The old words are taken out of the drawing glyph by glyph, and covered only when that can't be done safely.
- The paragraphs below in the same column move up or down using `Ts`, and the rules between them move with them.
- An overflow is refused, or shrunk to fit (to 75 % at most), or run on into the next column on the same page.
- The reader (`#paragraphOf`, `#openTextEditor` in `reader.js`) picks the paragraph's lines and opens a plain-text editor.

**What's missing:**
1. **One style per paragraph.** Bold, italic, colour and links on single words are lost when a paragraph is rewritten. (A line edit keeps them; a paragraph edit doesn't.)
2. **Greedy line breaking (`_wrap`), no hyphenation.** Justified text gets uneven word gaps and rivers, and a long word leaves a short line.
3. **Paragraph shape isn't understood:**
   - a first-line indent;
   - a hanging indent under a bullet or number (the text should wrap under the text, not under the bullet);
   - space before and after the paragraph;
   - line breaks the author put in on purpose.
4. **One paragraph at a time.** You can't edit two paragraphs together, and Enter can't split one into two.
5. **Flow stops at the page.** Nothing runs on to the next page, and lines don't get shorter where a picture sits beside the column.
6. **The whole paragraph is set again.** Lines before the first changed word are re-set too, so they can shift slightly even when their words didn't change.
7. **Latin-script assumptions.** CJK has no spaces to break at (and kinsoku rules apply); right-to-left text is only partly handled.

**Tech stack:** pure Python on top of the existing `textedit.py`, `textflow.py` and `fonts.py`. Hyphenation patterns come as a data file (TeX's `hyph-en-us` patterns, which are permissively licensed). The licence text goes into `notices.py`, and language-specific pattern files are added only when needed.

## Global constraints

- **Nothing that wasn't changed moves**, except text below that has to move to make room (as today). Every glyph before the first changed word, and every glyph outside the paragraph and its column, keeps its exact position. This is checked by comparing pdfium's letters before and after.
- **No words are ever lost.** If something can't be done for sure, the edit falls back to today's behaviour (a single style, cover instead of remove), never to a worse result. Each new step can be turned off with an option, for tests and as an escape hatch.
- **No new runtime dependency** beyond the pattern data file.
- **Plain words in the reader.** It says when a style couldn't be kept, when text ran on to the next page, or when a picture shortened lines.
- Python 3.10 to 3.14, Linux and Windows. No model identifiers in code or commits.

## Measuring it

Extend the Gap 1 holdout (`gap1hold` in the scratchpad, plus `tests/`) with multi-line cases:
- justified and left-aligned body text in serif and sans fonts;
- paragraphs with inline bold, italic and links;
- bulleted and numbered lists with hanging indents;
- first-line indents;
- two- and three-column pages;
- a picture beside a column;
- a paragraph at the foot of a page;
- a CJK paragraph and an RTL paragraph.

Scores, each automatic:

| Score | Target |
|---|---|
| Words present and in order (pdfium text) | 100 % |
| Untouched glyphs moved (outside the paragraph and its moving column) | 0 |
| Styles kept per word (font, bold, italic, colour, link) | ≥ 98 % |
| Justified right-edge error | ≤ 0.5 pt |
| Word-gap spread in justified lines (max/min gap) | lower than the greedy baseline on ≥ 90 % of paragraphs |
| Hanging-indent lines aligned with the text after the bullet | 100 % |
| Fallbacks to cover-instead-of-remove | no more than in 0.27 |

Visual check: before/after renders of the untouched regions must be pixel-identical.

---

### Phase 1 (release A, done in 0.33.0): words keep their styles

**Task 1.1: style runs from the page**
- [x] For the paragraph's letters (already read with pdfium in `_letters`), group runs of the same font, size, colour, rise and link annotation. Map each run to its words.
- [x] Return the runs alongside the text through the edit API, as `"runs": [{"text", "font", "bold", "italic", "color", "link"}]`.
- [x] Unit test: a paragraph with one bold word, one italic word and one link gives four runs with the right words.

**Task 1.2: a rich paragraph editor in the reader**
- [x] Replace the plain-text paragraph box with a small contenteditable that shows the runs: bold and italic as they are, and colour.
- [x] Ctrl+B and Ctrl+I toggle bold and italic. Typing inherits the style of the run it's typed into, like a word processor. Pasting is plain text.
- [x] Send the runs, not just the text.
- [x] Browser test: change one word next to a bold word; the bold word stays bold after saving.

**Task 1.3: setting runs**
- [x] `_Page.paragraph` measures and writes each run in its own font. Each run uses the document font if it has the letters, else installed, else standard, as today. Mixed runs on one line share the baseline.
- [x] Links: the link annotation's `/Rect` follows its words to their new place, or splits into several rects if the words now wrap across lines.
- [x] Test: a rewritten paragraph keeps per-word fonts and colours. The link rect covers the linked words after the rewrap.

### Phase 2 (release B): lines break well

**Task 2.1: optimal line breaking**
- [ ] Replace `_wrap` with a Knuth–Plass style total-fit breaker: boxes (words measured with the run's font), glue (the space width with stretch and shrink limits from the paragraph's current spacing) and penalties (hyphens).
- [ ] Left-aligned text uses the same breaker with ragged-right demerits, so it avoids a very short last line and one-word lines.
- [ ] Keep greedy as the fallback for very long paragraphs (over about 2,000 words) or when the breaker can't find a feasible solution.
- [ ] Test: on the holdout, the word-gap spread is lower than greedy, every line fits, and the number of lines is the same or fewer.

**Task 2.2: hyphenation**
- [ ] Liang's algorithm with `hyph-en-us` patterns, used only for justified paragraphs, or when a word is longer than the column. Language comes from the PDF's `/Lang`, else English. No other language is hyphenated until its patterns are added.
- [ ] Words that were hyphenated across a line end in the original are joined back first (as PDF to Word already does). A real hyphen (as in "well-known") is a break point without adding a hyphen.
- [ ] Rules: at least 2 letters before and 3 after a break; no more than 2 hyphenated lines in a row; never hyphenate the last word of a paragraph.
- [ ] Test: a narrow justified column breaks "extraordinarily" at a pattern point; "well-known" breaks after the hyphen.

**Task 2.3: author's line breaks**
- [ ] A line break inside the paragraph that wasn't caused by the width (the line ends well short of the right edge, and the next line doesn't start lower case) is kept as a forced break.
- [ ] In the editor, Shift+Enter makes one.
- [ ] Test: an address block or a short-line poem keeps its line ends after a word is changed.

### Phase 3 (release C): paragraph shape

**Task 3.1: indents and bullets**
- [ ] Find the first-line indent (the first line starts further right than the others) and the hanging indent: the first line starts with a bullet glyph (•, –, ▪, ◦) or a number or letter followed by `.` or `)`, and the next lines start under the text, not under the marker.
- [ ] Keep the marker glyphs exactly where they are. Wrap the text to the indented left edge.
- [ ] Test: a bulleted item that grows by a line wraps under its text, and the bullet doesn't move.

**Task 3.2: several paragraphs at once; Enter splits**
- [ ] Shift+drag, or Shift+click a second paragraph, selects a run of consecutive paragraphs in one column. They are edited as one block.
- [ ] Enter starts a new paragraph with the same space between paragraphs as the original. Joining two paragraphs (Backspace at the start) removes that space.
- [ ] Test: two paragraphs edited together keep their gap. Splitting one into two adds a gap equal to the document's own.

**Task 3.3: re-set only from the first change**
- [ ] Lines before the first changed word are left as drawn, not taken out and written again. Breaking starts from the first changed line, carrying that line's start.
- [ ] If the new breaks line up with the old ones again further down (same words at the start of a line, same width), stop: the rest of the paragraph stays as drawn.
- [ ] Test: changing a word in the last line of a 10-line paragraph leaves the first 9 lines' glyphs byte-identical in the content stream.

### Phase 4 (release D): flow around and across

**Task 4.1: around pictures**
- [ ] The column's free shape per line: if a picture (image XObject or filled path larger than a rule) overlaps a line's band inside the column, that line's width is the part beside it (left or right, whichever is wider, if wide enough; else skip the band).
- [ ] The breaker gets a width per line, which Knuth–Plass handles naturally.
- [ ] Test: a paragraph beside a picture wraps shorter beside it and full width below it, and no letter overlaps the picture.

**Task 4.2: on to the next page**
- [ ] When a paragraph no longer fits in the page's last column, and "run on" is chosen, the overflow lines go to the top of the next page's matching column. That column's text moves down using the same `Flow` machinery.
- [ ] Ask first: the reader offers "Continue on the next page" next to Shrink to fit.
- [ ] Never move text off the last page. Offer "Add a page" instead.
- [ ] Test: a two-page document. Growing the last paragraph of page 1 by three lines puts them at the top of page 2, and page 2's text moves down by three lines.

### Phase 5 (release E): other scripts

**Task 5.1: CJK**
- [ ] Break between any two CJK characters, except where kinsoku forbids it: closing punctuation can't start a line, opening punctuation can't end one. Keep Latin words inside CJK text whole.
- [ ] Justify CJK by spreading inter-character spacing (`Tc`), not word spacing.
- [ ] Test: a Japanese paragraph rewraps with no line starting with 。, and fills the width when justified.

**Task 5.2: right-to-left**
- [ ] Use the Unicode bidi algorithm's paragraph level and runs, implemented in-house or with a small pure-Python helper if it's licensed compatibly. Lines are filled right to left, with numbers and Latin runs inside kept left to right.
- [ ] Test: a Hebrew paragraph with an English word and a number rewraps with the right visual order.

---

## Order and size

| Release | Phases | Size | Risk |
|---|---|---|---|
| A | 1: styles kept | medium | low: today's path stays as the fallback |
| B | 2: breaking, hyphenation | medium | low, measured against greedy |
| C | 3: shape, multi-paragraph, re-set from the first change | medium–large | medium: touches how text is taken out |
| D | 4: around pictures, next page | large | medium: the cross-page flow |
| E | 5: CJK, RTL | medium | low for CJK, medium for RTL |

Each release needs a Windows check of real documents (Word-made PDFs, invoices, a brochure) in the reader, besides the holdout scores. A, B and the "re-set from the first change" task (3.3) give most of the visible improvement. D and E can wait until A–C have been used for a while.

**Place in the roadmap:** after 0.30 (File Explorer actions and the watch folder). It comes after the reader state refactor (0.29), because the rich editor in Task 1.2 builds on the cleaner per-tab state.

---

## Review after release A (0.33.0), and what it adds to the plan

Release A shipped: per-word styles are kept, the editor shows them, links follow their words. Reviewing it against the goal found these gaps. Each is a task; the ones marked (B) join release B, the rest release C.

**Not yet measured**
- [ ] (B) The holdout and scores in "Measuring it" were never built: release A is covered by tests of made-up paragraphs only. Build the multi-line holdout first (Word-made, LaTeX-made, InDesign-made and browser-printed PDFs, each rendered to known positions) and record 0.33's scores here, so B's line breaker is judged against numbers.

**Styles that are still lost or wrong**
- [ ] (B) **Sizes inside a paragraph.** A word in a bigger or smaller size keeps its size, but the line spacing and the wrap width are worked out from the first word's size only. Use each line's tallest run.
- [ ] (B) **Superscripts and subscripts** (footnote marks, H₂O): the rise isn't read, so they come back on the baseline. Read each letter's offset from its line's baseline and keep it.
- [ ] (B) **Underline and strikethrough** are drawn lines, not part of the font: they stay where they were while the words move. Find rules that sit under or through words, and move or redraw them with their words.
- [ ] (B) **Highlights and other text markups** over a rewritten paragraph are left where they were. Move them as links are moved now.
- [ ] (B) **When a style choice is made for the whole paragraph** (font, bold, italic or colour from the bar), every per-word style is dropped. Apply the choice on top of the per-word styles instead (a new colour only replaces the main colour; a new font keeps bold and italic words bold and italic).
- [ ] (B) In the editor, a colour picked for the whole paragraph doesn't show on words that had their own colour until the change is applied.
- [ ] **Right-to-left paragraphs** fall back to one style (the older path). Belongs with Phase 5.

**Fidelity of the re-set**
- [ ] (B) A paragraph in several styles is always taken out and written again whole; a paragraph in one style can change only the letters that changed. Give styled paragraphs the same in-place path, so a one-word change moves nothing else (this is Task 3.3 brought forward for the common case).
- [ ] (B) Word spacing and letter spacing are measured once for the paragraph and applied to every run. A bold run with different spacing in the original gets the paragraph's. Measure per run.
- [ ] Justified lines stretch only the spaces; the original may also have stretched letter spacing. Match what the original did.

**The editor**
- [ ] (B) Typing at the edge of a styled word: the server decides the new letters' style from the old text, and the editor decides it from where the caret is. They agree in the tests, but the editor's choice should always win; send the editor's runs for every paragraph edit, not only after Ctrl+B or Ctrl+I.
- [ ] (B) One-line edits (click, not Shift+click) still use a plain box; a bold word in the line shows plain while editing. Use the rich field for a line too.
- [ ] Undo inside the field after Ctrl+B relies on the browser's own undo. Check it on WebView2 in the Windows run.

**Checks that need real documents (Windows run for 0.33)**
- A Word-made report with bold and italic words and a link; an invoice with a bold total; a brochure paragraph with a coloured phrase; a paragraph whose first word is bold (a run-in heading).
