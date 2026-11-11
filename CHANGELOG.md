# Changelog

What changed in each version of ZPDF. The newest is first; each section is
also the notes of its release on
[ZPDF-releases](https://github.com/Tempura-446/ZPDF-releases/releases).

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
