"""Scan clean-up: drop blank pages and turn sideways or upside-down pages
the right way up.

Blank pages are found by rendering each page small and measuring how much
of it is dark ("ink"), ignoring a thin border where scanners often leave
shadows. Page orientation comes from Tesseract's orientation detection
(``tesseract --psm 0``), so auto-rotate needs the Tesseract program.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pdfmanager import core, progress

# Fraction of dark pixels below which a page counts as blank.
BLANK_THRESHOLDS = {"low": 0.000005, "medium": 0.001, "high": 0.004}
DARK = 150         # 0-255; lighter pixels (paper, bleed-through, dust) don't count
# Pages that look blank are checked again, closer: at 40 dpi the thin strokes
# of small or faded text blur to light grey, and light pencil is lighter than
# DARK. Show-through from the other side of the paper stays lighter than this.
CHECK_DPI, CHECK_DARK = 100, 170
BORDER = 0.04      # ignore this fraction of the page on each side
MIN_CONFIDENCE = 2.0  # Tesseract orientation confidence needed to rotate


def ink_coverage(path, password=None, dpi=40, pages=None, dark=DARK):
    """Fraction of pixels darker than ``dark`` on each page (0.0 for an
    empty page), or on the pages with the 0-based indices in ``pages``."""
    import pypdfium2 as pdfium

    coverage = []
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path), password=password)
        doc.init_forms()  # filled-in fields count as ink too
        try:
            indices = range(len(doc)) if pages is None else list(pages)
            for number, index in enumerate(indices):
                progress.report(number, len(indices), "page")
                page = doc[index]
                image = page.render(scale=dpi / 72, may_draw_forms=True).to_pil().convert("L")
                page.close()
                w, h = image.size
                bx, by = int(w * BORDER), int(h * BORDER)
                image = image.crop((bx, by, max(bx + 1, w - bx), max(by + 1, h - by)))
                hist = image.histogram()
                total = sum(hist) or 1
                coverage.append(sum(hist[:dark]) / total)
        finally:
            doc.close()
    return coverage


def find_blank_pages(path, password=None, sensitivity="medium"):
    """0-based indices of pages that look blank. ``sensitivity`` is "low"
    (only completely empty pages), "medium" or "high" (also pages with a
    few specks, a page number or a scanner mark)."""
    try:
        threshold = BLANK_THRESHOLDS[sensitivity]
    except KeyError:
        raise core.PdfManagerError(f"sensitivity must be one of {', '.join(BLANK_THRESHOLDS)}") \
            from None
    maybe = [i for i, ink in enumerate(ink_coverage(path, password)) if ink < threshold]
    if not maybe:
        return []
    closer = ink_coverage(path, password, dpi=CHECK_DPI, pages=maybe, dark=CHECK_DARK)
    return [i for i, ink in zip(maybe, closer) if ink < threshold]


def find_tesseract():
    found = shutil.which("tesseract")
    if found:
        return found
    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                     os.environ.get("LOCALAPPDATA")):
            if base and (Path(base) / "Tesseract-OCR" / "tesseract.exe").is_file():
                return str(Path(base) / "Tesseract-OCR" / "tesseract.exe")
    return None


def detect_rotation(path, password=None, pages=None, dpi=150):
    """{page index: degrees clockwise to turn it upright} for pages that are
    not upright. Pages without enough text to tell are left out."""
    import pypdfium2 as pdfium

    tesseract = find_tesseract()
    if not tesseract:
        raise core.PdfManagerError(
            "auto-rotate needs the Tesseract program "
            "(https://tesseract-ocr.github.io/tessdoc/Installation.html)")
    turns = {}
    with tempfile.TemporaryDirectory(prefix="pdfm-osd-") as tmp:
        images = []
        with core.PDFIUM_LOCK:
            doc = pdfium.PdfDocument(str(path), password=password)
            doc.init_forms()
            try:
                for i, page in enumerate(doc):
                    if pages is not None and i not in pages:
                        continue
                    target = Path(tmp) / f"{i}.png"
                    # Rendered as the page is shown (with its /Rotate), so the
                    # answer is relative to what the reader sees.
                    page.render(scale=dpi / 72).to_pil().convert("L").save(target)
                    images.append((i, target))
            finally:
                doc.close()
        for n, (i, image) in enumerate(images):
            progress.report(n, len(images), "checking which way up, page")
            angle, confidence = _osd(tesseract, image)
            if angle and confidence >= MIN_CONFIDENCE:
                turns[i] = angle
    return turns


def _osd(tesseract, image):
    """(degrees, confidence) from Tesseract; (0, 0.0) when it can't tell."""
    try:
        result = subprocess.run([tesseract, str(image), "stdout", "--psm", "0"],
                                capture_output=True, text=True, timeout=120,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise core.PdfManagerError(f"Tesseract could not check which way up the pages are: {exc}") \
            from None
    out = result.stdout + result.stderr
    if "Too few characters" in out:
        return 0, 0.0  # a picture or an empty page
    rotate = re.search(r"Rotate:\s*(\d+)", out)
    if result.returncode or not rotate:
        # A missing osd.traineddata (often a TESSDATA_PREFIX left by another
        # program) must not pass for "the page is upright".
        detail = " ".join(line.strip() for line in out.strip().splitlines()[-3:])
        raise core.PdfManagerError(
            "Tesseract could not check which way up the pages are: " + (detail or "no answer"))
    confidence = re.search(r"Orientation confidence:\s*([\d.]+)", out)
    return int(rotate.group(1)) % 360, float(confidence.group(1)) if confidence else 0.0


def clean(path, output, remove_blank=True, sensitivity="medium", auto_rotate=False,
          password=None):
    """Remove blank pages and/or turn pages upright. Returns
    ``{"removed": [page numbers], "rotated": {page number: degrees}}``
    (1-based page numbers of the input)."""
    if not remove_blank and not auto_rotate:
        raise core.PdfManagerError("choose at least one of: remove blank pages, auto-rotate")
    reader = core.open_pdf(path, password)
    with progress.section(0, 2 if auto_rotate else 1):
        blank = set(find_blank_pages(path, password, sensitivity)) if remove_blank else set()
    if blank and len(blank) == len(reader.pages):
        raise core.PdfManagerError("every page looks blank; nothing would be left")
    keep = [i for i in range(len(reader.pages)) if i not in blank]
    turns = {}
    if auto_rotate:
        with progress.section(1, 2):
            turns = detect_rotation(path, password, set(keep))
    writer = core.clone_document(reader)
    for i, page in enumerate(writer.pages):
        if i in turns:
            page.rotate(turns[i])
    for i in sorted(blank, reverse=True):
        writer.remove_page(i)
    core._write(writer, output)
    return {"removed": [i + 1 for i in sorted(blank)],
            "rotated": {i + 1: deg for i, deg in sorted(turns.items())}}
