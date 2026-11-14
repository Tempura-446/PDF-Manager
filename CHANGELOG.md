# Changelog

What changed in each version of ZPDF. The newest is first; each section is
also the notes of its release on
[ZPDF-releases](https://github.com/Tempura-446/ZPDF-releases/releases).

## 0.33.0

Rewriting a paragraph keeps each word's own style.

- **Bold, italic and coloured words stay as they are.** When you rewrite a
  paragraph in the reader (Edit text, Shift+click a line), words that were
  bold, italic or in a colour keep that look when the paragraph is set
  again; before, the whole paragraph took the look of its first word. A
  changed word keeps the style of the word it replaced, and new words take
  the style of the word before them, as in a word processor.
- **The paragraph editor shows the styles**, and **Ctrl+B** and **Ctrl+I**
  (or the B and I buttons) make just the chosen words bold or italic, in
  that face of their own font. With no words chosen in a paragraph of one
  style, they change the whole paragraph, as before. Pasting is plain text.
- **Links follow their words.** A link over words of a rewritten paragraph
  moves with them; when its words now run over two lines the link covers
  both, and a link whose words were all deleted is removed.
- **Paragraphs set without a gap between them** (as Word does by default
  in some styles) are told apart: Shift+click takes the one you clicked,
  not both as one.
- **Shift+click opens the paragraph** also when there's a caret or a
  selection on the page; before, it could select text instead.
- **No boxes where the spaces were.** In files whose fonts hold only the
  letters used (Word's), a rewritten line or paragraph could show a small
  box for every space: Word's fonts can list their "no such letter" box
  as a space beside the real one, and ZPDF wrote the box. It now writes
  the real space, and only in a font the page already draws spaces with;
  otherwise the words are set apart as the page's own are.
- A paragraph in one style is written exactly as in 0.32.

## 0.32.0

Drawings with layers. CAD programs export each drawing layer as a PDF
layer (optional content); ZPDF now shows them, prints with them and can
flatten them.

- **Layers tab in the reader.** For a PDF with layers, a **Layers** tab
  beside Pages, Bookmarks and Comments lists them in the file's order,
  nested as the file nests them (a named group's layers indented under its
  heading), each with a box to show or hide it, and
  **Show all** / **Hide all**. Locked layers can't be switched; in a radio
  group one layer stays on. The pages and thumbnails redraw at once, and
  each tab keeps its own choice when you switch tabs.
- **Measuring follows the layers.** Snapping uses only the lines of the
  layers shown, so a hidden dimension layer no longer pulls points off the
  walls. A hidden layer's words can't be selected either, and hiding a
  layer clears a selection on the page.
- **Printing follows the layers.** Print prints the layers shown.
- **Save layer visibility** makes the file open with the layers as they
  are now, in ZPDF and in Acrobat; it's a change like any other, with Undo.
- **Flatten layers** (in the reader's Layers tab, as a tool under Improve,
  and `pdfm flatten-layers`): the layers shown become part of the page,
  the hidden layers' lines, text and pictures are removed from the file,
  and the layers go, for sending a clean set.
- The page previews of a tool no longer stumble when the file is taken
  away while they load.
- Web page to PDF tries once more with a fresh browser when the browser stalls
  while starting, instead of failing after two minutes.

## 0.31.0

Mostly behind the scenes: groundwork for keeping ZPDF and what it installs
safe. The app works as before.

- **Packages checked against their hashes.** When a release carries a
  list of ZPDF's packages pinned with their hashes, the installer installs
  exactly those, and stops if a file doesn't match its hash, so a package
  replaced on PyPI can't slip in. Without the list, or on a Python other
  than the one it was made for, it installs as before. The list is made on
  Windows by `packaging/lock.py`, or by a workflow on GitHub.
- **Reporting a security problem.** `SECURITY.md` says how to report one
  privately, through GitHub, what counts, and that fixes go into the latest
  release.
- **The name and the licence.** Every source file now starts with a
  copyright line, and a test makes sure new ones do too. `TRADEMARKS.md`
  says the ZPDF name and icon are for this software only, not for other
  software or changed copies, and the README says how copies that break
  the licence are reported to GitHub.
- **Checks that run by themselves on GitHub:** Dependabot proposes
  updates to ZPDF's packages and to the workflows' actions each week;
  CodeQL scans ZPDF's Python and JavaScript for security mistakes; and
  `pip-audit` checks ZPDF's packages for known vulnerabilities each week.
- Switching tools quickly while a page preview was loading for the first time
  could leave an error behind; it no longer does.

## 0.30.0


Two ways to get work done without opening the tools first.

- **Right-click actions in File Explorer.** Right-click PDFs for a
  **ZPDF** submenu: **Combine in ZPDF**, **Make searchable (OCR)**, **Make
  smaller** and **Open in ZPDF**; right-click pictures (JPG, PNG, TIFF) for
  **Make a PDF with ZPDF**. Select several files and choose the action
  once: they all go to the one ZPDF window, as one job. The result opens as
  a new tab that isn't saved yet, and Save asks where, starting in the
  files' folder. Combine puts the files in name order ("Scan 2" before
  "Scan 10"). On Windows 11 the actions are under *Show more options*.
  The installer adds them for you only (`-NoContextMenu` leaves them out),
  uninstalling takes them away, and **Settings → Show ZPDF in the
  right-click menu** turns them off and on.
- **Watch folders in the app.** **Settings → Watch folders**: add a
  folder, choose what to do with the PDFs put in it (Make searchable, Make
  smaller, Archive as PDF/A, or a saved recipe), where the results go (a
  Done folder inside it, or next to the original with " (searchable)" and
  the like on the name) and whether the originals stay or move to an
  Originals folder. It runs while ZPDF is open, with the same engine as
  `pdfm watch`: each file is done once, also after a restart, a file still
  downloading or being copied is left until it's complete, and a note says
  when a file is done or why it couldn't be. Pause, resume or remove a
  folder from its line in Settings.
- `pdfm watch` leaves alone files still being downloaded (`.crdownload`,
  `.part` and the like beside them), Office lock files and hidden files.

Still to come: starting ZPDF with Windows, minimised, so watch folders run
without opening it.
- A file in a watch folder that is still open in another program waits and is done
  once it is closed; after a minute ZPDF says so once, instead of failing for good.
- The card for a right-click action stays 20 seconds after it finishes, and while
  you point at it.

## 0.29.0

Mostly behind the scenes: the reader now keeps each open document's state
in one place, and everything it shows about saving (the dot on a tab and
by the title, the Save button, closing a tab, Ctrl+S) follows from it, so
they can no longer disagree. The reader's code is also split into parts
(tabs, annotating, Sign, find, the text editor box, measuring). Things
that were wrong and are now right:

- **Undo after Black out** brought the words back, but Save still made a
  "(redacted)" copy and called itself "Save a redacted copy". It saves as
  usual again.
- **Undo after saving**: with annotations saved, turning, deleting or
  moving a page and then undoing it left the unsaved dot and a red Save,
  although the document was as saved.
- **Ctrl+S straight after saving** saved the file again (and offered Undo
  again) although nothing had changed; it now says "No changes to save",
  as the Save button already showed.
- **Undo of "Saved over"** put the file back, but a page change undone after
  that could show the document as saved when it wasn't.
- **Closing ZPDF or updating it** now warns about a tool's result that was
  never saved anywhere (its tab has the dot, and closing the tab already
  asked).
- **OCR with Tesseract, with both OCRmyPDF and the Tesseract program
  missing**, asked only for OCRmyPDF; it now names both, with where to get
  each. (The OCR tests also no longer depend on whether OCRmyPDF is
  installed on the computer running them.)
- **Redo after undoing a note** (or a shape, a stamp, a link, a text or
  page change): Ctrl+Z took it away, but Redo stayed off and Ctrl+Y did
  nothing. Redo now puts it back (with the dot), and Undo and Redo pick
  the same change whether from the buttons or the keys.
- **Ctrl+Z and Ctrl+Y with Select on** (for instance after Esc from Draw)
  did nothing, although the Undo and Redo buttons worked. They now do
  what the buttons do, with any tool on (not while typing in a box).
- **Closing ZPDF with the window's X** didn't warn about unsaved work (the
  warning only worked in a browser). It now asks "Close ZPDF? Your changes
  to … haven't been saved." with Yes and No (No keeps it open). Restarting
  after an update, which asks itself, doesn't ask again.
- **The unsaved dot while drawing** came only once the drawing was put
  down (Select or Esc), although Save turned red as soon as the stroke was
  drawn. The dot now shows with it.
- **The Measurements list** stayed open over the next tab after switching
  away from the measured one. It now goes with its document, and is there
  again on coming back.

## 0.28.0

Safety nets, mostly behind the scenes: checks that catch a kind of mistake
before it reaches a release.

- **When a program ZPDF can use isn't there** (Microsoft Word, LibreOffice,
  Edge or Chrome, Tesseract, Ghostscript, the built-in OCR engine, Windows
  Image Acquisition), each tool that uses it now either works another way
  or says what's missing and what to do, and the tests check every one:
  - Web page to PDF from a typed web address (no saved page added) stopped
    with "Something went wrong" before it printed anything. It prints the
    page now, and without a browser says to install Edge, Chrome or
    Chromium.
  - PDF to Word: "Microsoft Word only" without Word, and "Exact layout"
    without LibreOffice, say which converter to choose instead.
  - OCR without an engine, or with an engine that isn't installed, says to
    reinstall ZPDF or install Tesseract, instead of only a pip command; with
    Tesseract it names just what is missing (OCRmyPDF, the Tesseract
    program, or Ghostscript when OCRmyPDF wants it).
  - A program that can't start says so in words, not "[Errno 2] ...".
  - Office to PDF says which program converted the file (Word, LibreOffice
    or ZPDF itself), and says what to install when there's none.
  - Scan: without the piece of ZPDF that reaches Windows Image Acquisition,
    the Scanner choice says to reinstall ZPDF, and that photos still work.
- An import check: every part of ZPDF must load on its own, and two parts
  may not start depending on each other (a mistake that only shows when
  the app starts). Four such pairs were taken apart; five remain, listed in
  `tests/test_imports.py`.
- A weekly sweep on GitHub (`.github/workflows/sweep.yml`): every tool on a
  small made-up corpus of about 60 valid and damaged PDFs
  (`make_corpus.py --quick`), with the report kept for two weeks. It fails
  only on a crash, a hang or running out of memory, and never blocks a
  pull request.

## 0.27.0

A maturity pass: every tool was run on about 400 real-world and tricky
PDFs (pypdf's collection of sample files, documents made with LibreOffice,
Word and PowerPoint files, scans, and files damaged on purpose), and what
broke was fixed. Kinds of files that work better now:

- **Files cut short or with a broken index of objects** (a download that
  stopped, a file copied with the wrong line endings): each tool mends
  them first, as Repair does, and says so. Repair's own result now opens in
  every tool.
- **Very large pages** (posters, plans a metre or more across): Convert to
  images, PowerPoint, Word, the web page, Compare, Redact and Add form fields
  draw them with fewer pixels instead of running out of memory. A picture
  made this way still says how big the page is.
- **Pages whose crop box lies outside the page**: shown whole, as other
  viewers show them, instead of failing in PowerPoint, images, Compare,
  Trim margins, Clean up and previews.
- **A page count that is wrong in the file** (5 for one page, 0 or -1): put
  right in what every tool writes, and when signing; the reader shows such
  a file as damaged, with Repair, instead of an empty window.
- **Document details that are really another part of the file**: Sanitize
  and Document details no longer remove the pages along with the title.
- OCR with Tesseract keeps form fields, and a portfolio keeps its own
  pages: OCRmyPDF's PDF/A step (through Ghostscript) dropped the fields and
  printed the attached files instead. OCR now writes a plain PDF; PDF/A
  is its own tool.
- To Excel: a table with a lone dash or currency sign in a cell (common in
  financial reports) no longer stops it.
- To Word: control characters in a PDF's title or links no longer make a
  document Word refuses to open.
- Word, the web page, Accessibility tags and Excel: a page the table reader
  can't parse no longer stops them; Word and the web page convert the text
  without the tables.
- Sign: a page tree pyHanko trips over is signed on a mended copy (a page
  without its type), or refused in plain words.
- The PDF/A check and conversion read pages with small pictures written
  into the page itself (inline images).
- Compress and Colour cope with a picture in a compression they don't know.
- A page turned by an angle that isn't a quarter turn counts as not turned,
  as viewers show it (Sign stopped on it).
- Redact: a page of small print with thousands of matches takes seconds,
  not minutes.
- Copy text says when a file has no text (and points to OCR) instead of
  giving an empty text file; Remove password says when a file has none.
- For checking: `tests/sweep` makes the corpus and runs the sweep (see
  Tests in the README).
- Web page to PDF can no longer stall for two minutes when the browser writes a
  lot of warnings while it prints, or when it crashed and left its lock behind.

## 0.26.0

Scan: paper to a searchable PDF, in one place.

- **Scan paper** on the start screen (and Scan under Convert, or in the
  search box) opens a page of its own. The pages come from a scanner or
  from photos taken with a phone.
- On Windows, ZPDF lists the scanners Windows knows and scans from them
  through Windows Image Acquisition, which comes with Windows: no other
  program, and no PowerShell, so it works with Smart App Control. Colour,
  greyscale or black and white; 150, 200 or 300 dpi (300 greyscale to
  start with); the glass, one page each press of "Scan another page", or
  every page in the document feeder, arriving one by one. An empty
  feeder, a busy, offline or jammed scanner are said in plain words, and
  Cancel stops between pages and keeps the pages scanned so far. Where
  scanning can't work (not Windows, or no Windows Image Acquisition) the
  Scanner choice is off and says why; Photos works everywhere.
- Photos: the page's four edges are found in each photo and the page is
  flattened, cropped and straightened. A clean look evens out shadows
  and makes the paper white and the ink dark; "Keep colours" keeps
  coloured ink and stamps, "As taken" changes nothing. When the edges
  aren't clear, the whole photo is kept (only straightened), and its
  page says so.
- The pages gather in a strip as they arrive: drag them into another
  order (or Alt+arrow keys), turn them, take them out (with Undo), or scan
  one again.
- Make PDF straightens crooked pages, takes out blank ones, turns
  sideways pages upright, reads the text (the built-in OCR engine, or
  Tesseract), sizes each page from the scan's resolution (photos go on
  A4 or Letter, whichever fits their shape), compresses it as Compress
  does scans, and opens it in the reader as a new tab, not saved yet.
- From the command line: `pdfm scan` (with `--list`, `--scanner`,
  `--feeder`, `--mode`, `--dpi`) and `pdfm photos-to-pdf`.

## 0.25.0

A design pass: the same look, made consistent. The tools work as before.

- One set of type sizes everywhere, in the app and the reader: six, from
  12px to 24px. The small labels, keys and file facts that were 11px are
  12px now, easier to read, and the labels in capitals share one spacing.
  The rail's buttons are a little wider for their labels.
- One set of corners and shadows. The odd ones (a corner of 6, 10 or 12
  pixels here and there, a shadow made up on the spot) use the shared ones.
- What a search found shows in a plain banner; green is kept for a job
  done. A folder tool's preview, which hasn't changed anything yet, is
  plain too.
- A tool's description is about 70 letters a line instead of 95.
- Redact: Match upper/lower case and Whole words only sit beside the words
  they apply to, and the kinds of numbers to black out have a row of their
  own.
- After Esc closes a group of tools, the rail button under the pointer no
  longer looks like a second current group.

## 0.24.4

Small fixes. The look is the same.

- With Select, a single click on a signature or a drawing picks it, ready
  to move, resize or delete, also after you switch to another tab and
  back. (Before, it took a double-click.) Esc after picking one goes back
  to Select instead of leaving its tool on. Links, form fields and
  selecting text work as before.
- Switching tabs while a long file is still being laid out can't leave an
  error behind any more.
- Signing no longer waits several seconds before the signature appears
  when the reader is slow to switch to the picture tool.
- The automatic checks run on current versions of GitHub's actions.

## 0.24.3

Two small things from a check of 0.24.2 on Windows.

- A file you have already signed with Sign (a signature, drawing or typed
  name on its signature line) is no longer offered "Sign it" again.
- A selected picture or signature has handles at its corners only. The
  side handles squashed it out of shape, and on a small signature they
  covered the middle, where a press should move it.

## 0.24.2

Small fixes after a check of 0.24.1 on Windows. The look is the same.

- A signature put on its line stays drawn there when you sign again and
  the new one is selected, and when you zoom or turn the page. It is now
  made where it goes and at its size, instead of being made in the middle
  of the page and moved there.
- After signing, Select and Esc turn the picture tool off again.
- Signatures, drawings and other annotations added with the annotate
  tools and not saved yet can still be picked, moved, resized and deleted after you
  switch to another tab and back: the tool you were using comes back with
  the tab. The tab still shows it isn't saved, and Save writes each one
  once. (Undo can't take back what was done before the switch; delete it
  instead.)
- With Select, double-clicking a signature picks it, ready to move or
  delete.
- In the Sign dialog the name box has the focus as soon as it opens, and
  no longer offers saved names to fill in, so typing straight away keeps
  every letter.

## 0.24.1

Small fixes after a check of 0.24.0 on Windows. The look is the same.

- "Sign it" puts your signature on the file's signature line: at the
  start of the line, standing on it, about as tall as handwriting next to
  the label and never longer than the line. It still moves and resizes
  like any signature. Sign without a known line places it about 2 inches
  wide in the middle of what you see, no longer large across the page.
- Undoing your only change (or every change since the last save) takes
  away the unsaved dot and quiets Save again; Redo brings them back.
- Opening the find bar while annotating, then closing it, brings the
  annotate bar back with its tool, and the file panel stays aside. The
  same the other way round.
- In the keyboard shortcuts list, the descriptions start at the same
  place in every section.
- The delete button on a selected picture or signature shows its icon
  in the button again, not far below it.
- The corner handles of a selected picture, signature or note are larger
  and easier to catch with the mouse.
- "Sign it" straight after opening a file also finds the line (the first
  time, or in a window that had been minimized, it could put the
  signature in the middle of the view instead).
- "Use saved signature" places it at the same moderate size as a new one,
  not about 5 inches wide.
- Pressing R straight after clicking another tab turns that tab, not the
  one you left.
- Picking a font in a text edit takes its normal width: "DejaVu Sans"
  bold could come out as DejaVu Sans Condensed bold.
- Turning a page just after switching tabs turns it in the tab you
  switched to, and no longer logs an error. Page changes asked for while
  a tab is still being shown wait for it, instead of being made from the
  other tab's pages.

## 0.24.0

Sign means signing, a damaged file has a way out, and the page gets more
room, after a second review of the interface and a check on Windows. The
look is the same.

- Sign, everywhere, is your own signature: drawn, typed or a picture of
  it, placed on the page. The file panel's Sign and its "Sign it"
  suggestion open that, no longer the certificate form. Signing with a
  certificate (.pfx or .p12) is now called Digital ID, in the tool list,
  the annotate bar's More tools and Ctrl+K.
- "Sign it" is also suggested for a file with a printed place to sign: a
  "Signature" or "Sign here" line on one of its first pages.
- A file the reader can't show no longer flashes a message and leaves an
  empty reader. Its name stays where the pages would be, with "ZPDF can't
  show this file — it may be damaged.", Try to repair it (the repaired copy
  opens in a new tab, not saved yet), Remove from Recent and Close. Recent
  says "can't be opened" in red until the file changes, and a file that
  wouldn't open no longer jumps to the top of Recent.
- While the annotate bar or the find bar is open, the file panel steps
  aside so the page has the room, and comes back when they close (open it
  by hand meanwhile and it stays). Find and annotate are one row at a
  time. The note box and the text look bar stay in sight instead of
  running under the panel or the bars, and a tool's tip goes away with the
  tool.
- With one document open, the dot for unsaved changes is beside its name
  too. Ctrl+S while typing a note adds the note, then saves.
- After blacking out areas in the reader, Save writes a copy beside the
  original, "name (redacted).pdf". Replace original is there too, and asks
  first, since the original text can't be recovered. The reader's button
  says "Black out 3 areas", like the Redact page.
- Closing a tab with unsaved changes offers Save as well as Close without
  saving.
- Files kept when you switch tools are said: "2 files kept from Compress
  · Clear". The same file is never in the list twice. Redact says "Choose
  what to black out" and waits until you have; an error after a run
  starts with a capital and shows by the Run button too.
- Make it smaller is no longer suggested for a file Compress just made, or
  for one that is already small for its pages.
- Search PDFs is called Search a folder everywhere. Its results head each
  folder by the part below the folder you searched, and a copy elsewhere
  names the other copy and its folder. What you typed is still there when
  you come back to it, until ZPDF is closed.
- Tool descriptions in the rail's lists take two lines instead of being
  cut off.
- Ctrl+K knows other words: shrink, join, combine, lock, protect, unlock,
  sign. "Keyboard shortcuts" there, or ?, lists the keys ZPDF answers to.
- File names are set in Source Serif 4, which is wider and easier to read
  than before; its 1 no longer looks like l.

## 0.23.0

Clearer about saving, and easier to find your way, after a review of the
whole interface. The look is the same.

- After a tool runs in the reader's file panel, its message has Save… (the
  folder of the original is offered first), then Show in folder. A tab
  that isn't saved anywhere yet has a dot.
- Saving over a file (Ctrl+S) can be undone for 10 seconds: "Saved over
  report.pdf — Undo" puts back what the file held. (Not for files over
  64 MB.)
- Questions come up in ZPDF's own window, with buttons that say what they
  do: "Replace file", "Save as a copy", "Cancel". Cancel and Escape never
  change anything. This replaces the browser's plain boxes everywhere.
- Save is red, like Run: the one main button. Once there is a result to
  save, Run steps back.
- The annotate bar keeps its words in ordinary windows: Highlight, Note,
  Add text, Draw, Sign and Save. The rest (Underline, Strike out, Shapes,
  Stamp, Add picture, Edit text, Edit pictures, Links, Digital ID,
  Redact) are under More tools, which shows the one in use. "Picture" is
  now "Add picture" and "Pictures" "Edit pictures".
- Single keys while the annotate bar is open: H highlight, C note, A add
  text, D draw, S sign (press again to put the tool down). They are in
  each button's tooltip and in Ctrl+K.
- The rail is grouped by what you want to do: Read, Pages, Improve,
  Convert, Protect. Improve has Compress, Make searchable (OCR), Repair,
  Clean up scans, Convert colours, Flatten and the accessibility tools.
  The folder tools (Search PDFs, Organize, Rename, Find duplicates) and
  Recipes are on the start screen under "Start from several files" and
  its More, and under All. A long tool list shows that it scrolls.
- Search PDFs: once results come, the form folds into one line ("how much
  do I owe" in Documents · Edit) and the results sit under the title,
  grouped by folder, with only the matching words marked. The marks are
  easy to read in dark mode too.
- One or two files on a tool page show as a compact strip instead of a
  large, mostly empty card.
- "can't be read" is shown in red on the start screen and in the file
  panel.
- Redact's button says "Black out" ("Black out 3 matches" once they're
  found), Delete pages' "Delete pages", Remove hidden data's and
  Flatten's their own name, instead of Run.
- When a file couldn't be opened and was left out, the result says so by
  name. The command palette has one Redact, and "Redact this document" in
  the reader.

## 0.22.1

Easier to use with the keyboard and easier to see, after a review against
common accessibility guidelines. The look is unchanged.

- Text boxes and lists have borders you can see, and whatever has the
  keyboard's focus shows a clear outline, including search results and
  menus. The bar with Run at the bottom of a tool no longer hides it.
- The annotate toolbar fits the window, with Save and Close always in
  view; the Settings dialog keeps its buttons on screen in a small window.
- After searching a folder, the results come into view.
- A PDF that can't be opened is said to be damaged, in plain words, with
  a way to Repair it; it no longer counts as ready to run.
- "Skip to main content" for the keyboard; Escape closes the annotate
  bar and puts the focus back where it was after the find bar and
  password box; small buttons are easier to hit.
- Removing a file from Recent can be undone; such messages stay at least
  10 seconds, longer while the pointer is on them. The password box has a
  label and Show/Hide, and ZPDF offers to remember a PDF's password and
  fill it in next time. A missing setting is pointed out next to the field.
- "Drop files here" can be reached and opened with the keyboard.
- Windows' high contrast mode shows which tool, tab and document are
  active, and lists keep their arrows.
- The "looks like a form" suggestion no longer appears for cover pages
  with drawn artwork, or for boxes inside photos and screenshots.
- Opening a file that is already open switches to it.

## 0.22.0

- Editing text really replaces it. The old words are taken out of the
  page and the new ones written in their place, instead of being painted
  over: search finds what you wrote and not what was there, gradients,
  highlights and underlines behind the words survive, outlined and
  see-through text keeps its look, and a right-aligned number keeps its
  right edge. A change to one line changes that line only. Letters the
  document's font lacks use a matching font (serif for serif), and any
  that no font can draw are named. Every edit is checked afterwards; one
  that can't be done safely in place falls back to the old way and says
  so.
- PDF to Word follows designed pages better: a pull quote or picture
  inside a column no longer splits the columns around it, footnote marks,
  superscripts and subscripts (1st, m², CO₂) stay in their sentence, the
  labels of charts and diagrams come out as tidy lines of text, numbered
  headings are told from numbered clauses, and Chinese, Japanese and
  Korean lines join without stray spaces.
- OCR reads three-column pages column by column, keeps the rows of a
  table on a crooked scan together, keeps the spaces between words on
  grey paper, puts back spaces where Chinese meets English, and no longer
  reads the blanks of a form as text.
- The reader opens files faster (a 27 MB scan shows its first page in
  about half the time) and big files are read in pieces while the first
  page is drawn. Page thumbnails are made only when the Pages panel is
  open, and only for the pages in view. Opening a file no longer sends
  it back to ZPDF's own server for checks.
- Enter presses the main button of a reader dialog: it used to press the
  first, which cancelled the password prompt and deleted a link in the
  link editor. Enter after typing a password now opens the file.
- A big file that is moved, deleted or changed by another program while
  it is open no longer stops the reader: ZPDF says what happened and the
  tab can be closed.

## 0.21.0

- Search a folder by meaning. Ask "how much do I owe" and Search PDFs
  finds the page that says "balance due": pages are ranked by what they
  are about as well as by the words they share with your question, the
  closest first, each with the part of the page that matched. In a test
  of 44 questions worded differently from the documents, the right
  document came first 30 times (matching words alone: 19; the exact
  phrase: never).
- A name, a number or a code (an order or invoice number) finds the pages
  that have it and nothing else. "These exact words" searches for a
  phrase as before. A second copy of the same file is named beside the
  first instead of doubling every result.
- Questions are understood in English. Chinese (and Japanese and Korean)
  is matched by its characters, so searching in Chinese finds the pages
  with those words, in the best order.
- It runs on your computer: a 4 MB table of word meanings comes with
  ZPDF, nothing is sent anywhere, and a folder is prepared once and then
  searched in moments. On the command line: `pdfm search FOLDER "…" --best`.

## 0.20.0

- The panel beside an open document suggests more: "Make it fillable" for
  a form you can't type in yet, and "Sign it" for a file with an empty
  signature field.
- Tools that start from several files (Merge, Compare, Interleave, and
  the tools that turn pictures, Office files and web pages into PDFs)
  open on a large drop area that says what to add; their options appear
  once the files are there.
- One list of recent files, the same on the start screen and in the
  reader: each file with what it is, and two files of the same name told
  apart by their folder.
- Movement where it shows what happened: panels and the search box open
  softly, a running tool's bar shimmers, results and messages arrive and
  leave gently. None of it with Windows' "reduce motion" on.
- Frosted surfaces read properly in the dark theme; saving a result and
  removing a recent file say so in a short message.
- Opening a PDF while ZPDF is running brings its window to the front more
  reliably. After "Add a password", the note names the protected copy.

## 0.19.0

- A new look and a new way in. ZPDF opens on a start screen: drop or open
  a PDF, or pick up a recent file. Each recent file says what it is at a
  glance: its pages and size, and whether it is scanned, searchable,
  protected, signed or a form.
- With a document open, a panel beside it shows what the file is and what
  fits it: a scan with no text is offered "Make it searchable", a large
  file "Make it smaller", a signed one "Check its signatures". Common
  tools are one click away, with their options in the panel.
- A tool run from the panel works on the open file and opens its result
  as a new tab beside the original; nothing is uploaded again, and the
  original stays as it was. T shows and hides the panel.
- The long list of tools at the side is now a slim rail with five groups:
  Read, Pages, Convert, Protect and All. Every tool is still there, and
  the search box at the top (Ctrl+K, or /) finds any of them.
- Layered surfaces, softer corners and new type, in light and dark. The
  theme choice has moved into Settings.
- PDF → Word's built-in converter reads pages the way the other exports
  do. Columns of text come out one after the other where they were read
  straight across, headings are headings, tables are real Word tables,
  lists keep their items, and bold, italic and links are kept. Documents
  made of text, tables and scans convert well; heavily designed pages
  (brochures with panels, illustrations made of many small drawings) are
  better than before but not yet right everywhere.
- Scanned pages that have been made searchable convert properly: the
  built-in converter added stray letters to their words ("committee e
  met on Tuesday y") and flattened their tables.
- In the HTML export too, an ordinary line of a scanned page is no longer
  taken for a heading because its recognised size differs a little.
- Designed pages read better in the Word and HTML exports: a grid of
  features (a heading over a paragraph, side by side) is read one feature
  at a time, two columns of sentences aren't turned into a table, the
  labels inside a screenshot stay in the picture, page numbers are left
  out, and a bold sentence or a number is no longer a heading.

## 0.17.0

- PDF/A without Ghostscript. ZPDF now converts to PDF/A-1b, -2b and -3b
  itself: it adds what the standard asks for (the PDF/A mark, an sRGB
  colour profile, printing links), takes out what it forbids (scripts,
  protection, attachments in parts 1 and 2) and leaves the pages as they
  are. Scans and files made by browsers and Office need nothing else.
- Fonts that aren't in the file (Helvetica, Times, Courier in older PDFs)
  are embedded from the computer's own: Arial, Times New Roman and
  Courier New have the same letter widths, so no line moves. A font
  without such a stand-in stops the conversion with a message naming it.
- Ghostscript, when installed, is still used for a file ZPDF can't put
  right alone, and `pdfm pdfa --engine ghostscript` asks for it by name.
- Every converted file is checked with ZPDF's own PDF/A checks before it
  is handed over.

## 0.16.0

- Compress keeps scanned text sharp. At Medium and High, a scanned page is
  stored as a black-and-white text shape at the scan's full resolution
  over a low-resolution background, with the ink's colour kept, instead
  of being blurred down as a whole. The file is about as small as before
  and the text stays as sharp as the scan. On by default; "Keep scanned
  text sharp" in Compress, or `--no-sharp-scans`, turns it off.
- When something goes wrong, the message carries a short error ID, and
  ZPDF keeps a log of what happened (the tools run, how long they took, and
  errors), with document names and your user folder left out. Settings ›
  About › Copy diagnostics copies the details needed to report a problem;
  Open log folder shows the log.
- Damaged and unusual PDFs (cut short, broken cross-references, looping
  page trees, bad pictures and fonts, no pages, empty or oddly rotated
  pages) now get a plain message from every tool instead of an unexpected
  error, and messages name your file rather than ZPDF's working folder.
  Every tool is tested on a set of such files.
- Security: converting Word, Excel and PowerPoint files never runs their
  macros; a saved web page can't pull other files from your PC into the
  PDF it's printed to; Ghostscript runs in its safe mode; the file catalog
  can't turn a document title into a spreadsheet formula; Settings only
  accepts ZPDF's own update repositories; Windows programs are started by
  their full path.
- Performance limits: checks that memory follows a file's size, not its
  page count, for merging, compressing and OCR, and that a 200 MB file
  reaches the reader in pieces. They run when a version is released.
- PDF → Word with "Best available": if Microsoft Word fails or doesn't
  finish (on some PCs it never does when another program drives it), ZPDF's
  own converter is used instead and the result says so, rather than a wait
  of five minutes and an error. A Word that doesn't answer is closed, not
  left running out of sight.
- Web page → PDF no longer fails when Edge hands the printing to a browser
  process of its own and that one finishes a moment later.

## 0.15.1

- Updating or installing on a PC whose newest Python is one the built-in OCR
  engine has no version for yet (Python 3.15 when it came out) failed. The
  installer now picks Python 3.10 to 3.14, installing Python 3.13 for ZPDF
  if there's none, and moves an existing ZPDF onto it. If the OCR engine
  still can't be installed, ZPDF is installed or updated without it and
  says so, rather than not at all.
- Publishing a release checks first that GitHub's gh is installed and
  signed in and that git has a name and email for the version tag, and says
  plainly if the release went out but its tag didn't.

## 0.15.0

- Releases for friends: each version is published on ZPDF-releases as one
  zip with the installer, its SHA-256 beside it. No GitHub account is needed
  to download or to update.
- The Update button follows those releases and checks the download's
  checksum before installing it. No token to set up any more (a development
  copy can still follow the private repository with one).
- Licence: ZPDF is shared under the PolyForm Internal Use License 1.0.0,
  with personal use added: use it at home or at work and change it for your
  own use, but don't pass it on. It comes as is, without warranty.
- Settings has an About section with the licence and the notices of the
  software by others that ZPDF includes or installs.
- `packaging/release.py` checks a version (the tests, the app build and its
  self-test), makes the release files and publishes them.

## 0.14.0

- OCR is built in: PaddleOCR's PP-OCR models, run with ONNX Runtime, read
  scans with nothing else to install. On a hard set of test scans the mean
  character error rate is 0.3 %, against 35.6 % for Tesseract with its
  English data.
- Sideways and upside-down pages are turned upright, two-column pages are
  read column by column, and table rows stay together.
- Russian, other Cyrillic, Korean, Arabic, Persian, Hindi, Thai, Greek,
  Tamil and Telugu: the reading model for the script is fetched once and
  checked against its published checksum.
- The text layer works in every reader and keeps files fit for PDF/A.
- Clean up scans can turn pages upright without Tesseract.
- Tesseract stays as a second engine; the installer offers it and
  Ghostscript as extras.

## 0.13.0

- True redaction: what's under the boxes is removed, the rest of the page
  stays text. Also on scanned pages with OCR text.
- Accessibility: tag PDFs for screen readers and check them, like Acrobat's
  accessibility check.
- PDF to PowerPoint and PDF to HTML, with tables, lists and columns.
- Password permissions, attached files, page labels, repair and fast web view.
- Stamps in the reader, a comment summary, FDF and XFDF export and import,
  and web page to PDF.
- Form formats, totals and checks, with form scripts running in the reader.
- Text editing flows within its paragraph, with a font picker.

## 0.12.0

- Make a printed form fillable: ZPDF finds its fields by itself.
- Radio buttons, dropdowns and list boxes.
- PDF/A check.

## 0.11.0

- Edit pictures and links in the reader.
- Sign with a digital ID and check signatures.
- PDF to Excel.

## 0.10.0

- Measuring: rectangles, cut-outs, counts and angles, with names, groups
  and colours, kept with the file, as CSV, or saved into the PDF.
- Compare drawing revisions in the reader.

## 0.9.0

- Save in place (Ctrl+S) and Save as in the file's folder; big files open
  in pieces.
- Notes, comments, underline, strikeout and shapes in the reader.
- Page tools: blank pages, split at bookmarks or by size, booklets, trim
  white margins.
- Drawing scales stored in the PDF are read.

## 0.8.0 and earlier

- 0.8: measuring with path lengths, a list with totals and units, undo and
  redo; safer reading of broken files.
- 0.7: live previews, favourite tools and clearer tool pages.
- 0.6: drawing scales, true paper size, a measure tool that snaps to the
  drawing's lines, and text edits that stay in place.
- 0.5 and before: the tools, the reader, the desktop app and the Windows
  installer.
