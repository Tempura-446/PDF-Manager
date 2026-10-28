# PDF-Manager

`pdfm` is a PDF toolkit you can use three ways: as a **graphical app** in
your browser, from the **command line**, or as a **Python library**.

It can merge, split, extract, reorder, rotate, resize, crop, n-up, compress
(three levels), convert colours (grayscale / RGB / CMYK, with ICC profiles),
OCR scans, encrypt, watermark, number pages, convert to and from images,
and organize, rename, de-duplicate and watch whole folders of PDFs.

## Install

Requires Python 3.10 or newer.

```bash
pip install -e .            # core features + the `pdfm` command
pip install -e ".[all]"     # + page rendering (to-images) and OCR
```

OCR also needs the [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html)
program (`brew install tesseract`, `sudo apt install tesseract-ocr`, or the
Windows installer).

## Graphical app

```bash
pdfm gui
```

This opens PDF Manager in your web browser. Drop files in, pick a tool, set
the options and click **Run**; the result downloads automatically (several
files come back as a ZIP). The folder tools (organize, rename, find
duplicates) work on folders on your computer and always let you
**Preview** before anything changes.

The app runs only on your own computer (`127.0.0.1`) and is protected by a
random token in the link it prints, so other websites and other computers
can't use it. Keep the terminal open while you use it; press Ctrl+C to stop.

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
| `gui` | open the graphical app | `pdfm gui` |
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

GitHub Actions runs the suite on Linux, Windows and macOS, plus an OCR job
with Tesseract.
