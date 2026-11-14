# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Paper to a searchable PDF, in one go: the pages from a scanner
(scanner.py) or from phone photos (pagephoto.py) become a PDF.

    pages → clean-up (straighten, drop blank pages, turn pages upright)
          → a PDF at the right page size (the scan's dpi, or A4 / Letter for photos)
          → OCR (the built-in engine when it's installed, else Tesseract)
          → scan compression (core.compress: sharp text over a light background)

    scanpdf.make_pdf([Page(image, dpi=300), ...], "out.pdf", notes=notes)

The app keeps the pages of a scan that's under way in a ScanBox (a folder
of pictures), so they can be put in order, turned, taken out and scanned
again before the PDF is made.
"""

import io
import secrets
import shutil
import tempfile
import threading
import time
from pathlib import Path

from pdfmanager import core, ocr, pagephoto, progress, scan

LOOKS = ("document", "colour", "photo")   # what photos are made to look like
PAPERS = ("auto", "a4", "letter")
THUMB_SIDE = 360


class Page:
    """One page to go in the PDF. ``image``: a PIL image or a path;
    ``dpi``: the scan's resolution (None for a photo, which is fitted to
    the paper size); ``turn``: degrees clockwise to turn it (0, 90, 180,
    270); ``photo``: whether it came from a camera (then it gets the look)."""

    def __init__(self, image, dpi=None, turn=0, photo=False):
        self.image = image
        self.dpi = dpi
        self.turn = int(turn) % 360
        self.photo = photo

    def load(self):
        from PIL import Image

        if isinstance(self.image, (str, Path)):
            with Image.open(self.image) as im:
                im.load()
                return im.copy()
        return self.image


def photo_page(source, straighten=True):
    """A Page from a phone photo (a path or a PIL image): the page found in
    it and flattened (see pagephoto.flatten_photo). Returns (Page, Found)."""
    from PIL import Image

    image = Image.open(source) if isinstance(source, (str, Path)) else source
    try:
        flat, found = pagephoto.flatten_photo(image, straighten_page=straighten)
    finally:
        if image is not source:
            image.close()
    return Page(flat, None, photo=True), found


def _paper_for(size, paper):
    """The paper (width, height in points) a photo of ``size`` goes on: A4
    or Letter as asked, or for "auto" the one whose shape is closer."""
    if paper == "auto":
        tall = max(size) / max(1, min(size))
        paper = min(("a4", "letter"), key=lambda p: abs(pagephoto.PAPERS[p] - tall))
    w, h = core.PAPER_SIZES[paper]
    return (h, w) if size[0] > size[1] else (w, h)


def _look(image, look):
    if look == "photo":
        return image
    return pagephoto.document_look(image, keep_colours=look == "colour")


def _skew(image):
    """How far to turn ``image`` anticlockwise to straighten its text: the
    built-in OCR engine's text boxes when it's installed (ocr.skew_angle),
    else the rows of dark pixels (pagephoto.skew_angle)."""
    if ocr.builtin_available():
        try:
            return ocr.skew_angle(image.convert("RGB"))
        except Exception:  # engine trouble: the plain way
            pass
    return pagephoto.skew_angle(image)


def _prepared(page, straighten, look):
    image = page.load()
    if image.mode not in ("1", "L", "RGB"):
        image = image.convert("RGB")
    if page.turn:
        image = image.rotate(-page.turn, expand=True)
    if page.photo:
        image = _look(image, look)
    if straighten and not page.photo:  # (photos were straightened when they were flattened)
        angle = _skew(image)
        if angle:
            image = pagephoto.straighten(image, angle)
    return image


def _write_pdf(images, output, paper):
    """One page per (image, dpi): at the scan's size when the dpi is known,
    else fitted to the paper and centred."""
    from reportlab.lib.utils import ImageReader

    c = core.pdf_canvas(str(output))
    for image, dpi in images:
        if image.mode == "1":
            image = image.convert("L")  # (compression turns black-and-white pages back into one bit a pixel)
        if dpi:
            w, h = image.width * 72 / dpi, image.height * 72 / dpi
            pw, ph, x, y = w, h, 0, 0
        else:
            pw, ph = _paper_for(image.size, paper)
            s = min(pw / image.width, ph / image.height)
            w, h = image.width * s, image.height * s
            x, y = (pw - w) / 2, (ph - h) / 2
        c.setPageSize((pw, ph))
        c.drawImage(ImageReader(image), x, y, width=w, height=h)
        c.showPage()
    c.save()


def _clean(source, output, remove_blank, upright, notes):
    """scan.clean, or None when it changed nothing. Turning pages upright
    that fails (Tesseract without its orientation data) is said and skipped."""
    try:
        return scan.clean(source, output, remove_blank=remove_blank, auto_rotate=upright)
    except core.PdfManagerError as exc:
        if "every page looks blank" in str(exc):
            notes.append("Every page looks blank, so none were taken out")
            return _clean(source, output, False, upright, notes) if upright else None
        if not upright:
            raise
        notes.append(f"Pages weren't turned upright ({str(exc).rstrip('.')})")
        return _clean(source, output, remove_blank, False, notes) if remove_blank else None


def ocr_engine():
    """The OCR engine Make PDF uses ("builtin" or "tesseract"), or None."""
    try:
        return ocr.choose_engine("auto")
    except core.PdfManagerError:
        return None


def can_turn_upright():
    return bool(ocr.builtin_available() or scan.find_tesseract())


def make_pdf(pages, output, straighten=True, remove_blank=True, upright=True, recognise=True,
             look="document", paper="auto", language="eng", compress=True, notes=None):
    """Make the PDF at ``output`` from ``pages`` (Page objects, in order).
    ``look`` is for photos: "document" (grey, white paper), "colour"
    (white paper, colours kept) or "photo" (as taken). ``paper``: "auto",
    "a4" or "letter", for photos. ``notes`` (a list) is told what was done
    and what couldn't be. Returns the output path."""
    if not pages:
        raise core.PdfManagerError("there are no pages yet: scan or add some first")
    if look not in LOOKS:
        raise core.PdfManagerError(f"choose a look: {', '.join(LOOKS)}")
    if paper not in PAPERS:
        raise core.PdfManagerError(f"choose a paper size: {', '.join(PAPERS)}")
    notes = notes if notes is not None else []
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    steps = ["pages"]
    if remove_blank or upright:
        steps.append("clean")
    engine = ocr_engine() if recognise else None
    if recognise and engine is None:
        notes.append("Not made searchable: no OCR engine is installed (see the OCR tool)")
    if engine:
        steps.append("ocr")
    if compress:
        steps.append("compress")
    if upright and not can_turn_upright():
        notes.append("Pages weren't turned upright: that needs the built-in OCR engine or Tesseract")
        upright = False
    work = Path(tempfile.mkdtemp(prefix="pdfm-scanpdf-"))
    try:
        current = work / "pages.pdf"
        with progress.section(steps.index("pages"), len(steps), "preparing pages"):
            images = []
            for n, page in enumerate(pages):
                progress.report(n, len(pages), "page")
                images.append((_prepared(page, straighten, look), page.dpi))
            _write_pdf(images, current, paper)
            del images
        if "clean" in steps and (remove_blank or upright):
            with progress.section(steps.index("clean"), len(steps), "cleaning up"):
                cleaned = work / "clean.pdf"
                report = _clean(current, cleaned, remove_blank, upright, notes)
                if report is None:
                    cleaned = current
                if report:
                    if report["removed"]:
                        n = len(report["removed"])
                        notes.append(f"{n} blank page{'s' if n != 1 else ''} taken out")
                    if report["rotated"]:
                        n = len(report["rotated"])
                        notes.append(f"{n} page{'s' if n != 1 else ''} turned upright")
                current = cleaned
        if engine:
            with progress.section(steps.index("ocr"), len(steps), "recognising text"):
                searchable = work / "ocr.pdf"
                core.ocr(current, searchable, language=language, engine=engine, rotate=False, deskew=False)
                current = searchable
                notes.append("Made searchable" + (" with Tesseract" if engine == "tesseract" else ""))
        if compress:
            with progress.section(steps.index("compress"), len(steps), "making it smaller"):
                small = work / "small.pdf"
                core.compress(current, small, level="medium", sharp_scans=True)
                if small.stat().st_size < current.stat().st_size:
                    current = small
        shutil.copyfile(current, output)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return output


# -- the pages of a scan under way (the app) -----------------------------------------------

class ScanBox:
    """The pages scanned or photographed so far in one Scan page of the app,
    kept as pictures in a folder of their own until the PDF is made."""

    def __init__(self):
        self.id = secrets.token_urlsafe(9)
        self.folder = Path(tempfile.mkdtemp(prefix="pdfm-scanbox-"))
        self.pages = {}  # id -> {"path", "dpi", "photo", "found", "width", "height"}
        self.lock = threading.Lock()
        self.used = time.monotonic()
        self.thumbs = {}

    def add(self, image, dpi=None, photo=False, found=None):
        """Keep a page; returns its description (see describe)."""
        key = secrets.token_hex(6)
        path = self.folder / f"{key}.png"
        image.save(path, "PNG", compress_level=1, dpi=(dpi, dpi) if dpi else None)
        with self.lock:
            self.pages[key] = {"path": path, "dpi": dpi, "photo": photo, "found": found,
                               "width": image.width, "height": image.height, "added": time.time()}
            self.used = time.monotonic()
        return self.describe(key)

    def describe(self, key):
        p = self.pages[key]
        return {"id": key, "dpi": p["dpi"], "photo": p["photo"], "found": p["found"],
                "width": p["width"], "height": p["height"]}

    def list(self):
        with self.lock:
            self.used = time.monotonic()
            return [self.describe(k) for k in self.pages]

    def page(self, key, turn=0):
        with self.lock:
            p = self.pages.get(key)
            if p is None:
                raise core.PdfManagerError("that page is no longer there: scan or add it again")
            self.used = time.monotonic()
        return Page(p["path"], p["dpi"], turn, p["photo"])

    def thumbnail(self, key, look="document"):
        """A JPEG of the page, small, as it will look (photos with their look)."""
        from PIL import Image

        cache = (key, look)
        if cache in self.thumbs:
            return self.thumbs[cache]
        page = self.page(key)
        with Image.open(page.image) as im:
            im.draft("RGB", (THUMB_SIDE * 2, THUMB_SIDE * 2))
            small = im.convert("RGB") if im.mode not in ("L", "RGB") else im.copy()
        small.thumbnail((THUMB_SIDE * 2, THUMB_SIDE * 2))
        if page.photo:
            small = _look(small, look)
        small.thumbnail((THUMB_SIDE, THUMB_SIDE))
        buf = io.BytesIO()
        small.convert("RGB" if small.mode not in ("L", "RGB") else small.mode).save(buf, "JPEG", quality=82)
        data = buf.getvalue()
        with self.lock:
            if len(self.thumbs) > 400:
                self.thumbs.clear()
            self.thumbs[cache] = data
        return data

    def close(self):
        shutil.rmtree(self.folder, ignore_errors=True)


class ScanBoxes:
    """The app's ScanBoxes by id; ones not used for a day go."""

    KEEP = 24 * 3600

    def __init__(self):
        self.boxes = {}
        self.lock = threading.Lock()

    def get(self, key=None, create=True):
        with self.lock:
            now = time.monotonic()
            for k in [k for k, b in self.boxes.items() if now - b.used > self.KEEP]:
                self.boxes.pop(k).close()
            box = self.boxes.get(key or "")
            if box is None:
                if not create:
                    raise core.PdfManagerError("those pages are no longer there: start the scan again")
                box = ScanBox()
                self.boxes[box.id] = box
            return box

    def drop(self, key):
        with self.lock:
            box = self.boxes.pop(key, None)
        if box is not None:
            box.close()

    def clear(self):
        with self.lock:
            boxes, self.boxes = list(self.boxes.values()), {}
        for box in boxes:
            box.close()
