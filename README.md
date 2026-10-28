# PDF-Manager

`pdfm` is a PDF toolkit you can use three ways: as a **desktop app**, from
the **command line**, or as a **Python library**.

It includes a full **PDF reader**, and it can merge, split, extract, reorder, rotate, resize, crop, n-up, compress
(three levels), convert colours (grayscale / RGB / CMYK, with ICC profiles),
OCR scans, encrypt, watermark, number pages, convert to and from images,
and organize, rename, de-duplicate and watch whole folders of PDFs.

## Install

### Easiest: the standalone app

Download **PDF-Manager-windows.zip** or **PDF-Manager-linux.zip** from the
latest run of the [Build app](../../actions/workflows/build-app.yml)
workflow (open the run, then *Artifacts*), unzip it, and double-click
**PDF Manager**. Nothing else to install (OCR still needs Tesseract, below).

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
Windows installer).

## Desktop app

Start it with **`pdfm-app`** (no terminal window; good for a desktop
shortcut or the Start menu) or `pdfm gui`. It opens in its own window;
without pywebview it opens in your web browser instead (`pdfm gui --browser`
forces that).

- Drop files anywhere on the window. Each file shows a **preview of its
  first page**, its page count and size; drag the cards to change the
  order (for merging, interleaving and images).
- Pick a tool on the left, or press **/** to search for one. Set the
  options and click **Run** (or Ctrl/⌘+Enter).
- The result appears with a preview. In the app window, **Save…** opens
  the normal save dialog and **Show in folder** reveals the file; in a
  browser, the result downloads. Several files come back as a ZIP.
- Folder tools (organize, rename, find duplicates) have **Browse…**
  buttons in the app window and always let you **Preview** before
  anything changes.
- Password-protected PDFs: enter the password once under the options;
  results stay protected with the same password.
- Light and dark themes follow your system, or pick one at the bottom of
  the sidebar.

Everything runs on your own computer. The app's built-in server only
listens on `127.0.0.1` and requires a random token that only the app
window (or the link `pdfm gui` prints) knows, so other websites and other
computers can't use it.

## PDF reader

Open **PDF reader** at the top of the sidebar, run `pdfm view report.pdf`,
or open a PDF with the standalone app ("Open with…" → PDF Manager, or drop
the file on the app). It is built on [pdf.js](https://mozilla.github.io/pdf.js/),
the engine behind Firefox's PDF viewer, bundled with the app so it works offline.

- **Reading:** continuous scrolling, page numbers (and the document's own
  page labels), thumbnails, bookmarks, single or two-page view.
- **Zoom:** automatic, fit page, fit width, actual size or 50–400%;
  Ctrl/⌘ + mouse wheel or pinch to zoom around the pointer.
- **Find** (Ctrl+F) with highlighting, match case and whole words.
- **Select and copy text**; links work (web links open in your browser).
- **Fill in forms** and save a copy with your entries.
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
| N or J, P or K, Home, End | next, previous, first, last page |
| R, Shift+R | rotate clockwise, counter-clockwise |
| Ctrl/⌘+P, Ctrl/⌘+S | print, save a copy |

The bundled pdf.js (Apache-2.0, see `pdfmanager/static/pdfjs/LICENSE`) is
updated with `python packaging/update_pdfjs.py <version>`.

## Command line

Every command that works on PDFs accepts **several files or wildcards**
(`pdfm compress *.pdf`). With one input, `-o` names the output file; with
several, `-o` is a folder and the original file names are kept. Without
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
| `split` | one file per page, every N pages, or per range | `pdfm split book.pdf -r 1-10 11-20 21-` |
| `extract` | copy selected pages to a new file | `pdfm extract doc.pdf -p 2,4-6` |
| `delete` | remove pages | `pdfm delete doc.pdf -p 1,end` |
| `reorder` | rearrange (or repeat) pages | `pdfm reorder doc.pdf --order 3,1,2,4-` |
| `reverse` | reverse page order | `pdfm reverse scan.pdf` |
| `interleave` | merge separately scanned fronts and backs | `pdfm interleave fronts.pdf backs.pdf -o book.pdf` |
| `rotate` | rotate all or some pages | `pdfm rotate *.pdf -d 90 -p 2-3` |
| `resize` | change paper size or scale pages | `pdfm resize doc.pdf -s a4` · `--scale 0.5` |
| `crop` | trim margins (mm, CSS-style order) | `pdfm crop doc.pdf -m 10` · `-m 10 5 10 5` |
| `nup` | 2/4/6/8/9 pages per sheet | `pdfm nup slides.pdf -n 4` |
| `compress` | shrink files: `low`, `medium`, `high` | `pdfm compress *.pdf -l high -o small/` |
| `color` | convert to grayscale, RGB or CMYK | `pdfm color doc.pdf --to cmyk --icc printer.icc` |
| `ocr` | make scans searchable | `pdfm ocr scan.pdf --language eng+deu` |
| `encrypt` / `decrypt` | AES-256 password protection | `pdfm encrypt doc.pdf` (prompts) |
| `metadata` | show or set title/author/subject/keywords | `pdfm metadata doc.pdf --title "Q3 Report"` |
| `watermark` | stamp text or another PDF | `pdfm watermark doc.pdf -t CONFIDENTIAL` |
| `number` | add page numbers | `pdfm number doc.pdf --format "Page {n} of {total}"` |
| `text` | extract text | `pdfm text doc.pdf -o doc.txt` |
| `from-images` | save images as a PDF | `pdfm from-images *.jpg -o photos.pdf -s a4 --margin 10` |
| `to-images` | render pages to PNG/JPG | `pdfm to-images doc.pdf --dpi 200 -p 1` |
| `organize` | sort a folder of PDFs into sub-folders | `pdfm organize ~/Downloads --by year --dest ~/PDFs --move` |
| `rename` | rename PDFs from their metadata | `pdfm rename ~/PDFs --pattern "{date:%Y-%m-%d} {title}" -n` |
| `duplicates` | find (and optionally delete) identical PDFs | `pdfm duplicates ~/PDFs -R` |
| `catalog` | CSV inventory of a folder | `pdfm catalog ~/PDFs -o inventory.csv -R` |
| `watch` | process new PDFs dropped into a folder | see below |

Run `pdfm <command> -h` for all options.

### Compression levels

| Level | What it does | Use for |
|---|---|---|
| `low` | lossless: recompresses page content and removes duplicate objects; images untouched | archiving, print masters |
| `medium` (default) | also reduces images above **150 DPI** and re-encodes them as JPEG quality 75 | everyday sharing, office printing |
| `high` (alias `large`) | images reduced to **96 DPI**, JPEG quality 50 | email, web, smallest files |

Image resolution is measured as each image is actually drawn on the page,
so a large photo shown small is reduced while small images are left alone.
An image is only replaced if the new version is smaller. `--image-quality`
and `--max-dpi` override a level's settings.

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

### Watching a folder

```bash
pdfm watch ~/Scans/inbox --dest ~/Documents/PDFs --by month \
    --rename "{date:%Y-%m-%d} {title}" --ocr --compress medium --skip-duplicates
```

Every PDF that lands in the inbox is OCR'd, compressed, renamed and filed
into the destination. A file is only picked up once it has stopped
changing, so large copies aren't grabbed half-written. Originals are moved
to `inbox/originals` (or deleted with `--delete-originals`). A file that
can't be processed is reported and skipped; watching continues. Use
`--once` to process what's there and exit, e.g. from a scheduled task.

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
plus an OCR job with Tesseract.
