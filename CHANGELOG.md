# Changelog

What changed in each version of ZPDF. The newest is first; each section is
also the notes of its release on
[ZPDF-releases](https://github.com/Tempura-446/ZPDF-releases/releases).

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
