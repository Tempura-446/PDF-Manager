# ZPDF

`pdfm` is a PDF toolkit you can use three ways: as a **desktop app**, from
the **command line**, or as a **Python library**.

It includes a full **PDF reader** with tabs, where you can also annotate,
sign and change existing text, and it can merge, split (also at bookmarks or
into files of a maximum size), compare, extract, reorder, insert blank pages,
rotate, resize, crop (also trimming white margins automatically), n-up, make
**booklets** for folded printing, compress (three levels), convert colours
(grayscale / RGB / CMYK, with ICC profiles), OCR and clean up scans, redact,
flatten, remove hidden data, convert to PDF/A, Word and from Office files or
images, edit bookmarks, encrypt, watermark and number pages, add headers,
footers and **Bates numbers**, create **fillable forms**, pull out pictures
and attached files, run several of these in a row as a **recipe**, and
organize, rename, de-duplicate, **search** and watch whole folders of PDFs.

## Install

### Easiest on Windows: the installer

1. On GitHub, **Code → Download ZIP**, and unzip it (right-click → *Extract All*).
2. In the unzipped folder, double-click **Install ZPDF.cmd**. If
   Windows asks, choose *More info → Run anyway* (it's a plain text script:
   you can read what it does in `packaging/windows/install.ps1`).

It installs Python from python.org if you don't have it (with winget or the
official, signed installer), installs ZPDF in its own folder
(`%LOCALAPPDATA%\PDF Manager\app`), and adds **Start menu and desktop
shortcuts**, **ZPDF in "Open with"** (and in *Settings → Default
apps*) for PDFs, and an entry in **Settings → Apps** to uninstall it. No
administrator rights are needed, and you can delete the unzipped folder
afterwards. Running it again updates the installed copy (or use the
**Update** button, below).

At the end it asks whether to add **OCR and PDF/A support** (about 150 MB):
OCRmyPDF, plus the Tesseract and Ghostscript programs installed with
winget (`UB-Mannheim.TesseractOCR`, `ArtifexSoftware.GhostScript`; Windows
may ask for permission, as they install for all users). It doesn't ask when
they're already there, and skips the programs when winget is missing.
`Install ZPDF.cmd -WithOcr` adds them without asking, `-NoOcr` never does.
ZPDF finds both programs where their installers put them, also when they
aren't on the PATH.

The app runs with Python's own `pythonw.exe`, which is signed by the Python
Software Foundation, so Windows **Smart App Control** lets it start (it may
block the unsigned standalone app below). The script only uses plain
PowerShell commands, so it also works where Smart App Control limits scripts.

### The standalone app

Download **ZPDF-windows.zip** or **ZPDF-linux.zip** from the
latest run of the [Build app](../../actions/workflows/build-app.yml)
workflow (open the run, then *Artifacts*), unzip it, and double-click
**ZPDF**. Nothing else to install (OCR still needs Tesseract, below).

To build it yourself, including on a Mac:

```bash
pip install -e ".[build]"
python packaging/build_app.py      # result in dist/
```

### With Python

Requires Python 3.10 or newer.

```bash
pip install -e ".[app]"     # everything + the desktop window
pip install -e ".[all]"     # the same, plus OCR
pip install -e .            # without the desktop window (the app opens in your browser)
```

On Linux the desktop window also needs a web engine: `pip install "pywebview[qt]"`
(or the GTK packages, see the [pywebview docs](https://pywebview.flowrl.com/guide/installation.html)).
Without it, the app opens in your browser instead.

OCR also needs the [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html)
program (`brew install tesseract`, `sudo apt install tesseract-ocr`, or the
Windows installer). A few tools use other free programs if you have them:
turning scanned pages upright uses Tesseract, PDF/A uses
[Ghostscript](https://ghostscript.com/releases/gsdnld.html), and Office →
PDF uses Microsoft Office or [LibreOffice](https://www.libreoffice.org).

## Desktop app

Start it with **`pdfm-app`** (no terminal window; good for a desktop
shortcut or the Start menu), `pdfm gui`, or `pyw -m pdfmanager gui` on
Windows. It opens in its own window, the first way that works:

1. a native window (pywebview, installed with `.[app]`), with the normal
   Save and Open dialogs;
2. otherwise an app window of Microsoft Edge (part of Windows 10 and 11),
   Chrome, Chromium or Brave: no tabs or address bar, and results are
   saved like downloads;
3. otherwise a tab in your web browser (`pdfm gui --browser` forces that).

If the native window can't open, for example because Windows **Smart App
Control** blocks a library it needs, the reason is saved in
`%LOCALAPPDATA%\PDF Manager\window-error.txt` (`~/.local/share/pdf-manager`
on Linux) and the app uses the Edge window from then on. Delete that file
to try the native window again. The app stops by itself when its window is
closed.

- The app opens on a **Home** screen: open or drop PDFs to read them, go
  back to the tools you pinned or used recently, reopen a recent file, or
  pick any tool from the list with a line about each.
- **Pin** the tools you use most (the ☆ Pin button by a tool's name, or the
  star on its card on Home): they stay at the top of the sidebar and Home,
  above a few you used lately. In a narrower window the
  sidebar becomes a rail of icons; on a phone it's a menu.
- Drop files anywhere on the window. Each file shows a **preview of its
  first page**, its page count and size; drag the cards to change the
  order (for merging, interleaving and images).
- Pick a tool on the left, or press **/** to search for one. Set the
  options and click **Run** (or Ctrl/⌘+Enter). Run waits until there are
  enough files to work on.
- **Live preview:** Watermark, Page numbers, Headers & footers, Resize and
  Pages per sheet show page 1 as it will come out beside the options, and
  redraw it as you change them (watermark opacity and angle are sliders).
  Click the preview to see it larger.
- The result appears with a preview; **Run again** after changing options,
  or **Start over** to clear the files. In the app window, **Save…** opens
  the normal save dialog and **Show in folder** reveals the file; in a
  browser, the result downloads. Several files come back as a ZIP.
- The **page tools** show a preview of every page (with one PDF added):
  - **Reorder pages:** drag pages into the new order (or click a page and
    use Alt+arrow keys), duplicate or remove pages, undo with Ctrl+Z.
  - **Extract pages**, **Delete pages**, **Rotate:** click the pages to use
    (Shift+click for a range, or All / Odd / Even / Invert / Clear). Pages
    to delete are crossed out, and rotated pages are shown turned.
  - **Insert blank pages:** click the pages to put a blank page after
    (each the size of the page before it); *All* gives a page for notes
    after every page.
  - **Reverse order:** shows the pages in their new order.
  - **Split:** choose *every few pages*, *at the pages I choose* (click
    a page to start a new file there, or type the ranges), *at each
    bookmark* (one file per chapter, named after it; the previews show
    where each file starts), or *into files no larger than* a size, e.g.
    for email.

  The page box under Options updates as you go, and typing pages there
  updates the previews.
- **Crop margins** shows the page with a box to drag (the margins are as
  the page is shown, also for turned pages). **Trim white margins** finds
  the content of each page and cuts away the white around it (or the same
  for every page). **Booklet** puts two pages on each sheet in the order
  for a folded, stapled booklet: print it double-sided, flipping on the
  short edge. **Redact** lets you
  draw boxes to black out, and shows where the words you typed are found.
- **Bookmarks** has an editor: rename, indent and reorder bookmarks, or
  detect them from the document's headings.
- **Recipes** run several tools in a row (for example *Clean up scans →
  OCR → Compress*). Build one from steps, save it by name, and use it again
  in the app, with `pdfm recipe run`, or in a watch folder.
- Folder tools (organize, rename, find duplicates) have **Browse…**
  buttons in the app window and always let you **Preview** before
  anything changes.
- **Add form fields** makes a fillable form: pick a field type (text,
  multi-line text, checkbox, signature), then drag a box on the page or
  just click for a box of the usual size. Click a field to move, resize,
  rename or remove it. Fields already in the file are shown striped. The
  fields work in any PDF reader, including the one here.
- **Sign with a digital ID** signs PDFs with your certificate (a .pfx or
  .p12 file), like Acrobat: visible in a corner of a page (showing your
  name, the date and the reason) or invisible, optionally with a time from
  a timestamp server. **Check signatures** reports who signed each
  signature, when, and whether the document was changed afterwards. See
  [Digital ID signatures](#digital-id-signatures).
- **Headers & footers** adds text at the top or bottom of every page: a
  title, the date, *Page {n} of {total}*, the file name or **Bates
  numbers** (e.g. ABC000123), which continue from one file to the next.
- **Pictures & attachments** saves the pictures in a PDF and the files
  attached to it, as a ZIP.
- **PDF → Excel** puts the tables in PDFs into an Excel workbook (a sheet
  per table, or all on one sheet, after a Summary sheet) with numbers as
  real numbers, or into CSV files in a ZIP. See
  [Excel (tables)](#excel-tables).
- **Search PDFs** finds words in every PDF in a folder (and its
  sub-folders). Each result shows the words around the match; click one to
  open that page in the reader with the words highlighted. The text is
  kept in a small index, so searching the same folder again is quick.
- Long jobs show **progress** and can be **cancelled**.
- **Save…** opens the computer's own Save dialog, also in the Edge window.
- Password-protected PDFs: enter the password once under the options;
  results stay protected with the same password.
- Light and dark themes follow your system, or pick one at the bottom of
  the sidebar.
- Only **one copy** runs: opening a PDF from Explorer (or starting the app
  again) brings the running app to the front with the file in the reader
  (in the Edge window, the file opens in a new window of the running app).
- **Updates:** when there's a newer version on GitHub, an **Update** button
  appears in the sidebar; it downloads and installs it and starts the app
  again. The repository is private, so the check needs a GitHub token that
  can read it: in **Settings** (the gear at the bottom of the sidebar),
  paste a fine-grained token with *Contents: Read-only* access to this
  repository. It's kept on your computer only, and the check can be turned
  off there. (A copy running from a git checkout is updated with `git
  pull`, the standalone app by downloading it again.)

Everything runs on your own computer. The app's built-in server only
listens on `127.0.0.1` and requires a random token that only the app
window (or the link `pdfm gui` prints) knows, so other websites and other
computers can't use it.

## PDF reader

Open **PDF reader** at the top of the sidebar, run `pdfm view report.pdf`,
or open a PDF with the standalone app ("Open with…" → ZPDF, or drop
the file on the app). It is built on [pdf.js](https://mozilla.github.io/pdf.js/),
the engine behind Firefox's PDF viewer, bundled with the app so it works offline.

- **Distraction-free:** an open document gets the whole window. The toolbar
  keeps only the page number, zoom and the thumbnails button; **Tools** (or
  the T key) pulls out the rest (open, find, annotate and sign, rotate,
  print, save, more), and **All tools** in it slides out the app's tool list.
  The **Home** button (top left) goes back to the tools; your documents stay
  open in the reader.
- **Save** and **Undo** appear in the toolbar as soon as a document has
  changes not saved yet. **Save** (Ctrl+S) writes them into the file you
  opened (safely: to a new file next to it that then takes its place; if
  another program changed the file in the meantime you're asked first).
  **Save as** (Ctrl+Shift+S) starts in the file's own folder. A PDF that
  came from elsewhere (dropped from a browser, a tool's result) is saved
  as a copy.
- **Big files open fast:** a PDF of over 32 MB from your computer is read
  a piece at a time as you go, so the first pages show at once (a 277 MB
  drawing set: first page in about a second, using a few MB of memory).
- A zoom that fits the page (Automatic, Fit width, Fit page) fits it again
  when the window or the sidebar changes size.
- **Tabs:** open several PDFs at once (the + button, or drop more files),
  each with its own unsaved changes; a dot marks tabs with changes.
- **Recent files** on the start screen, to open them again with one click.
- **Reading:** continuous scrolling, page numbers (and the document's own
  page labels), thumbnails, bookmarks, single or two-page view.
- **Zoom:** automatic, fit page, fit width, actual size or 50–400%;
  Ctrl/⌘ + mouse wheel or pinch to zoom around the pointer.
- **Find** (Ctrl+F) with highlighting, match case and whole words.
- **Select and copy text**; links work (web links open in your browser).
- **Fill in forms** and save a copy with your entries.
- **Annotate and sign** (Tools → Annotate & sign): highlight, underline
  or strike out text, add **notes** (click, type your comment), add text,
  draw, draw **shapes** (rectangle, ellipse, revision cloud, arrow, line;
  hold Shift for squares, circles and 45° lines), add pictures, and sign
  by drawing, typing your name or using a picture of your signature
  (remembered on this computer if you like). Colours and sizes,
  undo/redo, and **Save** with your changes. Notes, underlines, strikeouts
  and shapes are standard PDF annotations with their own appearance, so
  Acrobat and other viewers show them the same, and they print. Use
  *Flatten forms & comments* afterwards to make them part of the page.
- **Pictures** (Tools → Annotate & sign → Pictures): every picture on the
  page is outlined; drag one to move it, drag a corner to resize it (it
  keeps its proportions; hold Shift to stretch it), or **Replace** or
  **Delete** it. Only the picture changes, the rest of the page stays as
  it is; a picture Word or a browser clipped to its box can be moved out
  of it.
- **Links** (Tools → Annotate & sign → Links): links are outlined; click
  one to change where it goes (a web or email address, or a page of the
  document) or delete it, or drag a box to add one.
- **Sign with a digital ID** (the badge button in Annotate & sign, or
  More): drag a box where the signature goes, choose your digital ID
  (.pfx or .p12) and its password. A signed document shows a bar saying
  who signed it and whether it has been changed since; **Details** lists
  every signature. ZPDF asks before changing a signed document, since
  any change shows up as made after signing.
- **Comments** (a tab in the sidebar): every note, highlight, shape and
  other markup in the document, page by page, with its author, date and
  comment and the replies under it. Click one to go to it; add or change
  its comment, reply, or delete it. Your name for comments is asked once.
- **Edit text** (E, or Tools → Annotate & sign → Edit text): click a line
  and retype it, **Shift+click** to rewrite its whole paragraph (it rewraps
  to the same width and line spacing, and stays justified if it was), or
  click an empty spot to add words that take the look of the text nearest
  to them. **Text stays where it was:** you type over the page's own words,
  in their font, size and colour; afterwards every letter you didn't change
  is exactly where it was (a bold word stays bold), the new letters start
  where the old ones did, spaced like the rest of the line, and the words
  after them move by exactly the difference in width (a justified line
  keeps its right edge, if its word gaps can take it up). The reader says
  when a line got too long for its column or a paragraph too long for its
  space. You can change the same line again and again. The new words keep the
  **document's own font** when its copy has every letter needed; otherwise
  the same font installed on your computer (found by name, e.g. Calibri),
  and only then a similar standard font; the reader tells you when it
  couldn't use the original. The old words are covered with the colour
  around them (they're still in the file underneath, so use **Redact** to
  remove text for good). Undo with Ctrl+Z.
- **Pages:** in the thumbnails, drag pages to reorder them, click with
  Ctrl or Shift to pick several, and turn, duplicate, delete or extract
  them (the buttons on a thumbnail, the bar above them, or right-click).
  **Insert PDF**, or drop a PDF between the thumbnails, puts its pages in.
  Every change can be undone (Ctrl+Z) and is kept with **Save**.
- **Right-click a page** for what you can do there: copy the selected
  words, edit this text, rewrite this paragraph, add text here, turn,
  duplicate, delete or extract the page, or insert a PDF after it.
- **Drawings to scale** (house plans, blueprints): the **Scale** button
  next to the zoom uses the exact scale CAD programs and Bluebeam store in
  the PDF when there is one, else reads each sheet's scale from its title block
  (`1/4" = 1'-0"`, `1" = 20'`, `1:100`, …), or you pick it, or **Set from a
  dimension**: click both ends of a line you know (say a 24'-0" wall) and
  type its length. The dialog shows what that line measures at the current
  scale, the scale your length makes and the **standard scale it matches**
  (pick that, or exactly as measured), and whether to use it for this sheet
  or **every sheet** of the document.
  - **True paper size** (in the zoom menu) shows the sheet exactly as big
    as it would print, once you've calibrated your screen (hold a ruler, in
    inches or centimetres, or a bank card to it, or type its diagonal; asked
    the first time). **Zoom to scale** zooms so the drawing looks as if
    printed at another scale, e.g. a 1/4" plan at 1/8" = 1'-0", and always
    shows what's in effect ("Not to scale (67%)" otherwise).
  - **Measure** (M): click two points for a length in feet and inches to
    the sixteenth (or metres to the millimetre). Clicks **snap to the
    drawing's own lines**, read from the file: a line's end, where two
    lines cross, a line's middle, the foot of the perpendicular from the
    last point (wall thicknesses), or a point on a line. A marker shows
    what the click will land on and the line it's on is highlighted, so a
    snapped measurement is exact to the drawing, not to the mouse.
  - **Lines close together:** when there's more than one thing to snap to
    under the pointer, a **magnifier** shows the lines there, the points to
    choose from and the one chosen; **Tab** (Shift+Tab) picks the next.
  - **Straight** keeps every line across or up and down (no slants); hold
    Shift for steps of 45°, Alt to place a point freely. **Area**: click a
    room's corners, then the first one again (or Enter), for its square feet
    (or m²) and perimeter.
  - **Path**: click along a run of lines (a wall run, a baseboard) for one
    total length, each segment's length shown beside it; Enter, a
    double-click or the Path button again finishes it.
  - **Rectangle**: two opposite corners for a rectangular room's area.
    **Cut-out**: click the corners of an opening (a stair, a shaft, a
    column) inside an area; it's taken off that area, which then shows its
    net size and how many cut-outs it has.
  - **Count**: click each outlet, door or fixture; each gets a number and
    the count shows by the first. Enter (or Count again) starts a new
    count, e.g. for another kind of fixture; Backspace takes back a mark.
  - **Angle**: a point on one line, the corner, a point on the other line.
  - **Names and groups:** name a measurement ("Living room", "North wall")
    and put it in a group ("Floors", "Outlets"); a group has its own
    colour on the page and in the list, and its subtotal. Pick the group
    new measurements go in at the top of the list.
  - **Move a measurement:** drag it (its line, edge or label) to move it, or
    drag one of its points to change it (the point snaps again). A click on
    it without dragging measures from there.
  - **Nudge:** the point last placed or moved is selected (shown filled);
    the arrow keys move it a pixel at a time (Shift: ten). Click a
    measurement in the list to select all of it. Delete removes the
    selected one, Esc lets go of it.
  - **Undo / redo** (Ctrl+Z, Ctrl+Y) while the scale bar is open: adding,
    moving, nudging, deleting and clearing measurements.
  - **List** shows every measurement with its name, value and page, by
    group with subtotals, **totals** of lengths (distances and paths),
    areas and counts, and **Show in**: feet and inches to 1/16", 1/8",
    1/4", 1/2" or the inch, decimal feet, inches, metres or millimetres.
    Labels sit on a white backing so the drawing's lines don't run through
    them, and hints appear at the top of the page, clear of the title block.
  - **Kept with the file:** measurements are remembered on this computer
    for each file and come back when it's opened again. **Copy** (to paste
    in a spreadsheet), **CSV** (group, name, page, kind, the value as shown
    and a plain number with its unit, for Excel or an estimate) and **Into
    PDF**: the measurements become measurement annotations in the document
    (with the drawing's scale), showing their values in Acrobat and other
    viewers and when printed, and listed under Comments.
  - **Compare revisions** (the layers button in the scale bar, or More →
    Compare with another revision): another version of the drawing, open
    in a tab or picked from a file, is drawn over this one page by page:
    what both have is black, what only that version has red, what only this
    one has blue, so a moved wall shows as a red line and a blue one.
    *Changes only* fades what's the same; you can also show either version
    alone, and if sheets moved, set the other version's page offset.
  - Backspace takes back the last point or measurement, Esc stops. Scanned
    sheets have no lines to snap to, and if a sheet's lines can't be read
    ZPDF says so: points then go exactly where you click, so zoom in close.
  - A scale read from the sheet is only as right as the sheet: measure one
    written dimension to check it before relying on the numbers.
- **Command palette (Ctrl+K):** type what you want (a tool, an action like
  "fit width" or "delete this page", a page number, or a zoom like "150%") and press Enter.
- Rotate the view, hand tool (drag to scroll), dark pages for night
  reading, full screen, print, document properties.
- Password-protected PDFs ask for their password.
- **Edit with…** sends the open document to any tool (compress, extract
  pages, OCR, …), and every finished result has **Open in reader**.
- Remembers the page and zoom of each document.

| Key | Action |
|---|---|
| Ctrl/⌘+O | open a PDF |
| Ctrl/⌘+F, Enter / Shift+Enter, Esc | find, next / previous match, close |
| Ctrl/⌘ + / − / 0 | zoom in / out / automatic |
| (zoom box) | type any percentage and press Enter; ↑ ↓ change it by 10% (Shift: 1%); the arrow beside it lists the presets |
| N or J, P or K, Home, End | next, previous, first, last page |
| R, Shift+R | rotate the view clockwise, counter-clockwise |
| Ctrl/⌘+K | command palette |
| E | Edit text on / off |
| M | measure on / off (Esc stops, Backspace undoes a point, Shift straight, Alt no snapping) |
| T | show / hide the tools |
| Ctrl/⌘+Z | undo a text or page change |
| Ctrl/⌘+P, Ctrl/⌘+S | print, save a copy |

The bundled pdf.js (Apache-2.0, see `pdfmanager/static/pdfjs/LICENSE`) is
updated with `python packaging/update_pdfjs.py <version>`.

## Command line

Every command that works on PDFs accepts **several files or wildcards**
(`pdfm compress *.pdf`). With one input, `-o` names the output file; with
several, `-o` is a folder and the original file names are kept (so is an
`-o` that ends with a slash or names an existing folder). Without
`-o`, results are written next to each input as `<name>_<command>.pdf`.
If one file in a batch fails, the rest are still processed.

Page ranges are 1-based: `1-3,5` · `7-` (7 to end) · `-3` (1 to 3) ·
`end` · `5-1` (reversed) · `odd` · `even` · `all`.

| Command | What it does | Example |
|---|---|---|
| `gui` | open the desktop app | `pdfm gui` |
| `view` | read PDFs in the reader | `pdfm view report.pdf` |
| `info` | page count, page sizes, metadata | `pdfm info *.pdf` |
| `merge` | combine files (optionally picking pages) | `pdfm merge a.pdf b.pdf:1-3 -o all.pdf` |
| `split` | one file per page, every N pages, per range, per bookmark, or files of at most N MB | `pdfm split book.pdf -r 1-10 11-20 21-` · `--bookmarks` · `--max-size 10` |
| `extract` | copy selected pages to a new file | `pdfm extract doc.pdf -p 2,4-6` |
| `delete` | remove pages | `pdfm delete doc.pdf -p 1,end` |
| `reorder` | rearrange (or repeat) pages | `pdfm reorder doc.pdf --order 3,1,2,4-` |
| `insert-blank` | insert blank pages (after pages, at the start, or after every page) | `pdfm insert-blank doc.pdf --after 3,7` · `--after all` |
| `reverse` | reverse page order | `pdfm reverse scan.pdf` |
| `interleave` | merge separately scanned fronts and backs | `pdfm interleave fronts.pdf backs.pdf -o book.pdf` |
| `rotate` | rotate all or some pages | `pdfm rotate *.pdf -d 90 -p 2-3` |
| `resize` | change paper size or scale pages | `pdfm resize doc.pdf -s a4` · `--scale 0.5` |
| `crop` | trim margins (mm, CSS-style order, as the page is shown), or the white margins | `pdfm crop doc.pdf -m 10` · `-m 10 5 10 5` · `--auto --padding 3` |
| `nup` | 2/4/6/8/9 pages per sheet | `pdfm nup slides.pdf -n 4` |
| `booklet` | arrange pages for a folded, saddle-stitched booklet | `pdfm booklet zine.pdf` · `-s letter` |
| `compress` | shrink files: `low`, `medium`, `high` | `pdfm compress *.pdf -l high -o small/` |
| `color` | convert to grayscale, RGB or CMYK | `pdfm color doc.pdf --to cmyk --icc printer.icc` |
| `ocr` | make scans searchable | `pdfm ocr scan.pdf --language eng+deu` |
| `encrypt` / `decrypt` | AES-256 password protection | `pdfm encrypt doc.pdf` (prompts) |
| `metadata` | show or set title/author/subject/keywords | `pdfm metadata doc.pdf --title "Q3 Report"` |
| `watermark` | stamp text or another PDF | `pdfm watermark doc.pdf -t CONFIDENTIAL` |
| `number` | add page numbers | `pdfm number doc.pdf --format "Page {n} of {total}"` |
| `headers` | headers, footers and Bates numbers | `pdfm headers *.pdf --bottom-right "{bates}" --bates ABC` |
| `form` | add fillable form fields (or list them) | `pdfm form doc.pdf -f text:1:72,700,300,720:Name` |
| `extract-media` | save the pictures and attached files | `pdfm extract-media doc.pdf` |
| `text` | extract text | `pdfm text doc.pdf -o doc.txt` |
| `clean` | remove blank pages, turn pages upright | `pdfm clean scan.pdf --rotate` |
| `compare` | report what changed between two versions | `pdfm compare v1.pdf v2.pdf` |
| `bookmarks` | list, set or detect bookmarks | `pdfm bookmarks doc.pdf --detect` |
| `redact` | black out text, numbers or areas for good | `pdfm redact doc.pdf -t "John Smith" --find emails` |
| `flatten` | make forms and comments part of the page | `pdfm flatten form.pdf` |
| `sanitize` | remove hidden data (details, scripts, attachments) | `pdfm sanitize doc.pdf` |
| `sign-cert` | sign with a digital ID (.pfx/.p12), visible or invisible | `pdfm sign-cert doc.pdf --pfx me.pfx --page last --reason "Approved"` |
| `verify` | check the digital signatures in PDFs | `pdfm verify signed.pdf` |
| `pdfa` | convert to PDF/A for archiving | `pdfm pdfa doc.pdf --level 2` |
| `to-word` | convert to an editable Word document | `pdfm to-word doc.pdf` |
| `to-excel` | put the tables into an Excel workbook (or CSV files) | `pdfm to-excel doc.pdf -p 2-5` · `--one-sheet` · `--csv` |
| `from-office` | convert Word, Excel, PowerPoint, … to PDF | `pdfm from-office report.docx` |
| `recipe` | save and run recipes (several tools in a row) | `pdfm recipe run "Scans" *.pdf` |
| `from-images` | save pictures as a PDF (photos the right way up, scanners' multi-page TIFFs) | `pdfm from-images *.jpg -o photos.pdf -s a4 --margin 10` |
| `to-images` | render pages to PNG/JPG | `pdfm to-images doc.pdf --dpi 200 -p 1` |
| `organize` | sort a folder of PDFs into sub-folders | `pdfm organize ~/Downloads --by year --dest ~/PDFs --move` |
| `rename` | rename PDFs from their metadata | `pdfm rename ~/PDFs --pattern "{date:%Y-%m-%d} {title}" -n` |
| `duplicates` | find (and optionally delete) identical PDFs | `pdfm duplicates ~/PDFs -R` |
| `catalog` | CSV inventory of a folder | `pdfm catalog ~/PDFs -o inventory.csv -R` |
| `search` | find words in every PDF in a folder | `pdfm search ~/PDFs "invoice 2024" -R` |
| `watch` | process new PDFs dropped into a folder | see below |
| `setup-windows` | Windows shortcuts, Open with, uninstall entry (`--remove`) | used by the installer |

Run `pdfm <command> -h` for all options.

### Splitting, blank pages, booklets and white margins

- `split --bookmarks` makes one file per top-level bookmark (`--level 2`
  also splits at the bookmarks under them), named `01 Title.pdf`,
  `02 …`; pages before the first bookmark go in `00 Opening pages.pdf`.
  `split --max-size 10` makes files of at most 10 MB (1 MB = 1,000,000
  bytes), each with as many pages as fit; a page larger than that on its
  own gets a file of its own, and a note says so.
- `insert-blank --after 3,7` puts a blank page after pages 3 and 7 (`end`
  after the last page, `0` or `--at-start` before the first, `all` after
  every page, e.g. to print slides with a page for notes); `--count 2`
  inserts two at each place. Each blank page has the size of the page
  before it.
- `booklet` adds blank pages to make a multiple of four and puts two pages
  side by side on each sheet in booklet order (8, 1 · 2, 7 · 6, 3 · 4, 5).
  By default a sheet is twice the page size (A5 pages on A4, A4 on A3);
  `-s a4` or `-s letter` scales them onto that paper instead. Print it
  double-sided, **flipping on the short edge**, fold the stack in the
  middle and staple it on the fold.
- `crop --auto` draws each page and cuts away the white around its content,
  keeping `--padding` mm (default 3.5); `--same` gives every page the same
  crop. Blank pages are left as they are, and only the crop box changes, so
  the margins are still in the file.

### Compression levels

| Level | What it does | Use for |
|---|---|---|
| `low` | lossless: recompresses page content, packs uncompressed fonts and pictures, drops the ASCII85/hex text wrapping some programs put around them (often 20% of the file), removes duplicate objects; images untouched | archiving, print masters |
| `medium` (default) | also reduces images above **150 DPI** and re-encodes them as JPEG quality 75 | everyday sharing, office printing |
| `high` (alias `large`) | images reduced to **96 DPI**, JPEG quality 50 | email, web, smallest files |

Image resolution is measured as each image is actually drawn on the page,
so a large photo shown small is reduced while small images are left alone.
An image is only replaced if the new version is smaller. `--image-quality`
and `--max-dpi` override a level's settings.

At every level the result is then repacked with [qpdf](https://qpdf.readthedocs.io)
(through pikepdf, installed with ZPDF): the many small objects a PDF
is made of go into compressed object streams. It's lossless and takes a
third or more off text-heavy documents; files that are mostly pictures
barely change.

### Colour conversion

`pdfm color --to gray|rgb|cmyk` converts page colours (text, lines, fills
and colours inside form XObjects) and images. With `--icc PROFILE.icc`
images are converted through that ICC profile and the profile is embedded
as the document's *output intent*, which is what print shops look for when
they ask for a PDF in their colour profile.

Without a profile, RGB↔CMYK uses the standard simple formula, so saturated
colours may shift; use your printer's profile for accurate colour.
Gradients (shadings), patterns, spot colours and inline images are left
unchanged.

### Passwords

When an input is encrypted, `pdfm` asks for the password. You can also set
the `PDFM_PASSWORD` environment variable (handy for batches) or pass
`--password`, though that ends up in your shell history. Files that only
have an owner password (printing/copying restrictions) open without
asking.

Results made from a password-protected PDF are **protected with the same
password**, so editing a protected file never silently produces an
unprotected one. Use `decrypt` to remove protection deliberately.

### Organizing folders

`organize --by` groups by `year`, `month` (from the PDF creation date, or
the file date), `author`, `pages`, `size` or `title-letter`. Files are
copied unless `--move` is given; `-n/--dry-run` previews the result.
Unreadable files go into an `Unreadable` folder rather than stopping the run.

`--duplicates` decides what happens to a file that is byte-for-byte
identical to one already organized: `keep` (default), `skip`, or `folder`
(put it in a `Duplicates` folder to review). `pdfm duplicates` lists
identical files and, with `--delete`, removes all but the oldest copy.

### Scan clean-up

`clean` removes blank pages (`--sensitivity low` keeps any page with a mark
on it, `high` also removes nearly empty ones) and, with `--rotate`, turns
sideways or upside-down pages upright using Tesseract's orientation
detection.

### Comparing

`compare old.pdf new.pdf` matches pages by their text (form answers
included), so an inserted or removed page doesn't make every later page
look different. The report PDF
starts with a summary, then shows each changed page side by side with the
changed areas outlined in red and the removed (−) and added (+) words.

### Bookmarks

`pdfm bookmarks doc.pdf` lists the bookmarks, one per line:

```
- Introduction (p. 1)
  - Background (p. 2)
- Results (p. 5)
```

Edit such a list and apply it with `--set list.txt`, create bookmarks from
the document's headings with `--detect` (`-n` to preview), or remove them
with `--clear`. Headings are found by font size and bold lines; running
headers and footers are ignored. Scans need OCR first.

### Redaction

`redact` blacks out text (`-t`, repeatable), regular expressions
(`--pattern`), all email addresses, phone numbers or long numbers
(`--find emails|phones|numbers`) and areas (`--box PAGE:X0,Y0,X1,Y1` in
points from the bottom left). Every page with a redaction is replaced by a
picture of the page with the black boxes burned in, so the text underneath
is really gone (other pages are untouched). Run OCR afterwards to make those
pages searchable again; metadata isn't changed, so use `sanitize` too.

### Archiving and privacy

- `flatten` draws filled-in form fields and comments (highlights, notes,
  drawings, signatures) into the page. Links keep working, and so do
  comments that have no drawing of their own (readers draw those themselves).
- `sanitize` removes the document details (author, title, dates, XMP),
  JavaScript and actions that launch programs, and attached files;
  `--comments` also removes comments. Form fields are kept.
- `pdfa` converts to PDF/A-1b, -2b (default) or -3b with Ghostscript:
  fonts embedded, colours tied to sRGB, no encryption or scripts.

### Digital ID signatures

A *digital ID* is a certificate with its private key, usually a `.pfx` or
`.p12` file protected by a password (exported from Acrobat, Windows'
certificate manager, or issued by a company or a certificate authority).
Signing with it proves who signed and that nothing changed since:

```bash
pdfm sign-cert contract.pdf --pfx me.pfx --page last --reason "I agree"   # bottom right of the last page
pdfm sign-cert contract.pdf --pfx me.pfx --page 2 --box 350,50,550,110    # a box, in points from the bottom left
pdfm sign-cert form.pdf --pfx me.pfx --field "Signature 1"                # an empty signature field
pdfm sign-cert contract.pdf --pfx me.pfx --timestamp http://timestamp.digicert.com
pdfm verify contract_signed.pdf                                            # exit code 0 if all valid
```

- `pdfm` asks for the digital ID's password (or set `PDFM_PFX_PASSWORD`,
  or pass `--pfx-password`). Without `--page` or `--field` the signature is
  invisible: it shows in a reader's signature panel only. `--position`
  (`bottom-right`, `bottom-left`, `top-right`, …) and `--size` (`small`,
  `medium`, `large`) place it on `--page` without `--box`.
- The visible signature shows the signer's name (from the certificate),
  the date and time, and the reason and location if given.
- The signature is **appended** to the file, so signatures already in it
  stay valid: several people can sign one after the other. Sign last:
  other tools (and the reader's edits) rewrite the file, which breaks the
  signatures in it. A password-protected PDF stays protected.
- With `--timestamp`, a timestamp server vouches for the time of signing
  (it needs the internet; if the server can't be reached nothing is signed).
- Expired digital IDs are refused: every reader would call their
  signatures invalid.

`verify` (and **Check signatures** in the app) reports, for each signature:
who signed, when (from the signer's clock or a timestamp), the reason and
location, and one of:

- **valid** — unchanged since signing, by a certificate you trust;
- **valid, but the signer's identity isn't verified** — unchanged, but the
  certificate isn't one you trust (ZPDF has no built-in list of trusted
  authorities, so this is the usual result; add `--trust company-root.cer`
  to trust a certificate);
- **modified** — the signed part is intact, but the document was changed
  afterwards in a way signatures don't allow (later signatures and, for
  some files, form filling are allowed; pyHanko, which does the checking,
  is stricter about later changes than Acrobat);
- **invalid** — the document was altered after signing, the signature
  doesn't match, or the certificate wasn't valid when it signed.

`--json` prints all the details.

### Word and Office

`to-word` converts with Microsoft Word when it's installed (the most
editable result), otherwise with the built-in converter (paragraphs,
headings and pictures in reading order; tables and columns become plain
paragraphs). `--engine layout` uses LibreOffice instead, which keeps the
exact look but puts every line in its own text box.

`from-office` converts Word, Excel, PowerPoint, OpenDocument, RTF and text
files to PDF with Microsoft Office or, without it, LibreOffice. Text files
always work.

### Excel (tables)

`to-excel` finds the tables in a PDF and writes `<name>.xlsx`: a
**Summary** sheet listing each table (page, rows × columns, a link to it),
then a sheet per table named like `p3 table 1` (`--one-sheet` stacks them
all on one sheet under a title each). The first row is the header (bold,
and frozen on its own sheet), columns are sized to fit, and numbers become
real numbers: `1,234.50`, `(12)` (negative), `7.5%`, `$ € £` amounts and
`1.234,50` keep their look as a number format. Codes with leading zeros
(`00123`, ZIP codes) and anything unclear stay text. `--csv` writes a CSV
file per table instead (UTF-8 with a byte-order mark, so Excel reads
accents and € right) into `<name>_tables/`.

- Ruled tables (with cell borders) come out best. Tables without lines are
  found from columns that line up with white space between them; dot
  leaders (`Total ....... 12`) count as space.
- A table that runs to the bottom of a page and goes on at the top of the
  next with the same columns becomes one table; a repeated header row is
  dropped.
- It needs text in the PDF: **scanned pages need OCR first** (*Make
  searchable*), otherwise it says that no tables were found.
- Limits: cells that span several columns come out in the first of them;
  headers on two lines become two rows; two tables side by side without
  lines can come out as one; text in a cell that looks like a formula
  stays text (in CSV files it starts with `'`).

### Recipes

A recipe is a list of tools run one after another. Save one with the steps
and their options (the same options as in the app):

```bash
pdfm recipe save "Scans" clean ocr language=eng compress level=high number "format=Page {n}"
pdfm recipe run "Scans" *.pdf -o done/
pdfm recipe list
```

Recipes are stored in `recipes.json` in the app's data folder
(`%LOCALAPPDATA%\PDF Manager` on Windows, `~/.local/share/pdf-manager` on
Linux), shared by the app, the command line and `pdfm watch --recipe NAME`.

### Watching a folder

```bash
pdfm watch ~/Scans/inbox --dest ~/Documents/PDFs --by month \
    --rename "{date:%Y-%m-%d} {title}" --ocr --compress medium --skip-duplicates
```

Every PDF that lands in the inbox is OCR'd, compressed, renamed and filed
into the destination. Add `--clean` to drop blank pages, `--rotate` to turn
pages upright and `--recipe NAME` to run a saved recipe. A file is only picked up once it has stopped
changing, so large copies aren't grabbed half-written. Originals are moved
to `inbox/originals` (or deleted with `--delete-originals`). A file that
can't be processed is reported and skipped; watching continues. Use
`--once` to process what's there and exit, e.g. from a scheduled task.

### Headers, footers and Bates numbers

```bash
pdfm headers report.pdf --top-right "{date}" --bottom-center "Page {n} of {total}"
pdfm headers exhibits/*.pdf --bottom-right "{bates}" --bates ABC --bates-start 1000 -o stamped/
```

Six spots (`--top-left` … `--bottom-right`) take text with `{n}`, `{total}`,
`{file}`, `{title}`, `{date}` and `{bates}`. Bates numbers continue from
file to file in the order given (ABC001000, ABC001001, …); the command
prints the range each file got.

### Form fields

```bash
pdfm form application.pdf -f text:1:72,700,300,720:Name -f checkbox:1:72,650,84,662:Agree
pdfm form application_form.pdf        # list the fields
```

Fields are `text`, `multiline`, `checkbox` or `signature`, with their page
and box in points from the bottom-left corner (`--fields FILE` reads a JSON
list). Names are made unique automatically. Drawing them in the app is
easier: **Add form fields**.

### Searching a folder

```bash
pdfm search ~/Documents "purchase order" -R --whole-words
```

Prints each page with a match and the words around it. The text of each
file is read once and kept in `search.sqlite` in the app's data folder, so
later searches only read new or changed files. Scanned PDFs need OCR first.

### Resizing

`--size` accepts `a0`–`a6`, `b4`, `b5`, `letter`, `legal`, `tabloid`,
`ledger`, `executive`, or custom sizes such as `210x297mm`, `8.5x11in`,
`600x800` (points). Append `-landscape` to force landscape. By default the
target size is turned to match each page's orientation; use
`--keep-orientation` to disable that. `--fit` chooses how content is placed:
`fit` (default, keep aspect ratio and pad), `fill` (keep aspect ratio and
crop), `stretch`, or `none` (no scaling).

Links, form fields and comments move with the page content when resizing,
rotating or placing several pages per sheet. In `nup` output, links to
other pages point to the sheet that now holds that page.

## Library use

```python
from pdfmanager import core, organize

core.merge(["a.pdf", ("b.pdf", "1-3")], "out.pdf")
core.resize("letter.pdf", "a4.pdf", size="a4")
core.compress("scan.pdf", "small.pdf", level="high")
core.convert_color("report.pdf", "print.pdf", "cmyk", icc_profile="printer.icc")
core.split("book.pdf", "chapters", ranges=["1-10", "11-"])
organize.organize("inbox", by="author", dest="library", move=True, duplicates="skip")
```

## Tests

```bash
pip install -e ".[test]"
pytest
```

GitHub Actions runs the suite on Linux (Python 3.10 and 3.13) and Windows,
plus a job with Tesseract, Ghostscript and LibreOffice for the tests that
need those programs, and a **Windows end-to-end** job: it installs the app
with `Install ZPDF.cmd` (with PowerShell limited the way Smart App
Control limits it), starts it like its Start menu shortcut, uses tools and
the reader in both kinds of window (its own window and the Edge window),
opens a second file from "outside", restarts it as after an update, closes
it and checks that nothing is left running, then uninstalls it.
