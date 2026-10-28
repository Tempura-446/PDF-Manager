# PDF-Manager

`pdfm` is a command-line tool (and Python library) for everyday PDF work:
merge, split, extract, reorder, rotate, resize, crop, compress, encrypt,
watermark, number pages, convert to/from images, and organize whole folders
of PDFs.

## Install

```bash
pip install -e .            # installs the `pdfm` command
pip install -e ".[render]"  # + pypdfium2 for rendering pages to images
```

Or without installing: `pip install -r requirements.txt` and run
`python -m pdfmanager ...`.

## Page ranges

Commands that take pages use 1-based ranges:
`1-3,5` · `7-` (7 to end) · `-3` (1 to 3) · `end` · `5-1` (reversed) · `odd` · `even` · `all`.

## Commands

| Command | What it does | Example |
|---|---|---|
| `info` | page count, page sizes, metadata | `pdfm info report.pdf` |
| `merge` | combine files (optionally picking pages) | `pdfm merge a.pdf b.pdf:1-3 -o all.pdf` |
| `split` | one file per page, every N pages, or per range | `pdfm split book.pdf -r 1-10 11-20 21-` |
| `extract` | copy selected pages to a new file | `pdfm extract doc.pdf 2,4-6 -o part.pdf` |
| `delete` | remove pages | `pdfm delete doc.pdf 1,end` |
| `reorder` | rearrange (or repeat) pages | `pdfm reorder doc.pdf 3,1,2,4-` |
| `reverse` | reverse page order | `pdfm reverse scan.pdf` |
| `interleave` | merge separately scanned fronts and backs | `pdfm interleave fronts.pdf backs.pdf -o book.pdf` |
| `rotate` | rotate all or some pages | `pdfm rotate doc.pdf 90 -p 2-3` |
| `resize` | change paper size or scale pages | `pdfm resize doc.pdf -s a4` · `pdfm resize doc.pdf --scale 0.5` |
| `crop` | trim margins (mm, CSS-style order) | `pdfm crop doc.pdf 10` · `pdfm crop doc.pdf 10 5 10 5` |
| `nup` | 2/4/6/8/9 pages per sheet | `pdfm nup slides.pdf -n 4` |
| `compress` | shrink file size (optionally lossy images) | `pdfm compress big.pdf -q 60` |
| `encrypt` / `decrypt` | AES-256 password protection | `pdfm encrypt doc.pdf` (prompts for password) |
| `metadata` | show or set title/author/subject/keywords | `pdfm metadata doc.pdf --title "Q3 Report"` |
| `watermark` | stamp text or another PDF | `pdfm watermark doc.pdf -t CONFIDENTIAL` |
| `number` | add page numbers | `pdfm number doc.pdf --format "Page {n} of {total}" --position bottom-right` |
| `text` | extract text | `pdfm text doc.pdf -o doc.txt` |
| `from-images` | save images as a PDF | `pdfm from-images *.jpg -o photos.pdf -s a4 --margin 10` |
| `to-images` | render pages to PNG/JPG | `pdfm to-images doc.pdf --dpi 200 -p 1` |
| `organize` | sort a folder of PDFs into sub-folders | `pdfm organize ~/Downloads --by year --dest ~/PDFs --move` |
| `rename` | rename PDFs from their metadata | `pdfm rename ~/PDFs --pattern "{date:%Y-%m-%d} {title}" -n` |
| `catalog` | CSV inventory of a folder | `pdfm catalog ~/PDFs -o inventory.csv -R` |

Run `pdfm <command> -h` for all options.

### Output files

Single-file commands write `<input>_<command>.pdf` next to the input unless
`-o` is given. `-o` may point at the input itself to modify it in place
(writes go to a temporary file first, so this is safe).

### Resizing

`--size` accepts `a0`–`a6`, `b4`, `b5`, `letter`, `legal`, `tabloid`,
`ledger`, `executive`, or custom sizes such as `210x297mm`, `8.5x11in`,
`600x800` (points). Append `-landscape` to force landscape. By default the
target size is turned to match each page's orientation; use
`--keep-orientation` to disable that. `--fit` chooses how content is placed:
`fit` (default, keep aspect ratio and pad), `fill` (keep aspect ratio and
crop), `stretch`, or `none` (no scaling).

### Organizing

`organize --by` groups by `year`, `month` (from the PDF creation date, or
the file date), `author`, `pages`, `size` or `title-letter`. Files are
copied unless `--move` is given; `-n/--dry-run` previews the result.
Unreadable files go into an `Unreadable` folder rather than stopping the run.

## Library use

```python
from pdfmanager import core, organize

core.merge(["a.pdf", ("b.pdf", "1-3")], "out.pdf")
core.resize("letter.pdf", "a4.pdf", size="a4")
core.split("book.pdf", "chapters", ranges=["1-10", "11-"])
organize.organize("inbox", by="author", dest="library", move=True)
```

## Tests

```bash
pip install -e ".[test,render]"
pytest
```
