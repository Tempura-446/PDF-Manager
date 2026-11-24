# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Image-to-PDF, PDF-to-image rendering, render-scale safety limits, and OCR wrapper."""

import io
import sys
import threading
from pathlib import Path

from pypdf import PdfWriter

from pdfmanager import progress
from pdfmanager.errors import PdfManagerError
from pdfmanager.pageranges import parse_page_ranges
from pdfmanager.core.io import _write, open_pdf
from pdfmanager.core.geometry import parse_paper_size
from pdfmanager.core.stamp import pdf_canvas

# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------

def images_to_pdf(images, output, size=None, margin=0):
    """Save one or more images as a PDF, one image per page.

    Without ``size`` each page matches its image (at the image's DPI, or 72).
    With ``size`` images are fitted onto that paper size, orientation chosen
    per image, with ``margin`` points of padding."""
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    if not images:
        raise PdfManagerError("no images given")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    c = pdf_canvas(str(output))
    for img_path in images:
        try:
            opened = Image.open(img_path)
        except (OSError, ValueError) as exc:  # not a picture, or a damaged one
            raise PdfManagerError(f"{img_path} is not a picture ({exc})") from None
        with opened as source:
            turn = _jpeg_turn(source)
            # A scanner's TIFF holds a page per frame. A camera's MPO also
            # has several, but they are other views of the same photo.
            frames = 1 if source.format == "MPO" else getattr(source, "n_frames", 1)
            for frame in range(frames):
                if frame:
                    source.seek(frame)
                # Pillow gives TIFF resolutions as fractions; the page size must be plain numbers.
                dpi_x, dpi_y = (float(v) if v and float(v) >= 1 else 72.0
                                for v in (source.info.get("dpi") or (72, 72))[:2])
                if turn is None:
                    source.load()
                    im = _page_image(source)
                    pixels = im.size
                else:  # a JPEG goes in as it is: no quality lost, and many times smaller
                    im = ImageReader(str(img_path))
                    pixels = source.size[::-1] if turn in (90, 270) else source.size
                    if turn in (90, 270):
                        dpi_x, dpi_y = dpi_y, dpi_x
                iw, ih = pixels[0] * 72 / dpi_x, pixels[1] * 72 / dpi_y
                if size:
                    pw, ph = parse_paper_size(size)
                    if (iw > ih) != (pw > ph):
                        pw, ph = ph, pw
                    s = min((pw - 2 * margin) / iw, (ph - 2 * margin) / ih)
                    dw, dh = iw * s, ih * s
                    x, y = (pw - dw) / 2, (ph - dh) / 2
                else:
                    pw, ph = iw + 2 * margin, ih + 2 * margin
                    dw, dh, x, y = iw, ih, margin, margin
                c.setPageSize((pw, ph))
                if turn is None:
                    c.drawImage(ImageReader(im), x, y, width=dw, height=dh)
                else:
                    _draw_turned(c, im, x, y, dw, dh, turn)
                c.showPage()
    c.save()
    return output


# EXIF orientation -> degrees to turn the stored picture anticlockwise to
# show it upright (the mirrored orientations are left to Pillow).
_EXIF_TURNS = {1: 0, 3: 180, 6: 270, 8: 90}


def _jpeg_turn(source):
    """For a JPEG the PDF can show as it is (RGB or gray; Adobe CMYK
    JPEGs store their colours inverted), how far to turn it to be upright;
    None for any other picture."""
    if source.format != "JPEG" or source.mode not in ("RGB", "L"):
        return None
    return _EXIF_TURNS.get(source.getexif().get(0x0112, 1))


def _draw_turned(c, image, x, y, width, height, turn):
    """Draw ``image`` turned ``turn`` degrees anticlockwise to fill the
    ``width`` x ``height`` box at ``x``, ``y``."""
    c.saveState()
    c.translate(x + (width if turn in (90, 180) else 0), y + (height if turn in (180, 270) else 0))
    c.rotate(turn)
    sw, sh = (height, width) if turn in (90, 270) else (width, height)
    c.drawImage(image, 0, 0, width=sw, height=sh)
    c.restoreState()


def _page_image(im):
    """The picture as it should appear on the page: turned the way the
    camera recorded it, transparent parts on white, and 16-bit scans and
    palettes brought to 8 bits."""
    from PIL import Image, ImageOps

    im = ImageOps.exif_transpose(im) or im
    if im.mode.startswith("I"):  # 16-bit (or wider) grayscale: scale, don't clip
        im = im.convert("I").point(lambda v: v / 256).convert("L")
    elif im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:
        rgba = im.convert("RGBA")
        im = Image.new("RGB", rgba.size, "white")
        im.paste(rgba, mask=rgba.getchannel("A"))
    if im.mode not in ("RGB", "L", "CMYK"):
        im = im.convert("RGB")
    return im


def ocr(path, output, language="eng", force=False, deskew=False, password=None, engine="auto",
        rotate=True, quality=None, report=None, workers=None):
    """Add a searchable, selectable text layer to scanned pages.

    ``engine`` is "auto" (the built-in engine when it is installed, else
    Tesseract), "builtin" (PP-OCR through rapidocr; needs the ``ocr`` extra)
    or "tesseract" (OCRmyPDF and the Tesseract program; the ``tesseract``
    extra). Pages that already have text are left alone unless ``force`` is
    set, in which case they are rasterised and recognised again.
    ``language`` uses Tesseract's codes, joined with '+' for several (e.g.
    "eng+deu"); the built-in engine reads English, most European languages,
    Chinese and Japanese unasked, and fetches a reading model once for
    Russian, Korean, Arabic, Hindi and other scripts (see ocr.script_for).
    ``rotate`` turns sideways and upside-down pages upright.
    ``quality`` is "standard" (built-in, fast), "best" (larger models,
    fetched once, slower) or None (use the Settings default).
    ``report`` is an optional dict filled with per-page OCR stats and
    unsure-word counts.
    ``workers`` is the number of worker processes for parallel page OCR
    (None sizes the pool automatically to CPU cores and memory; 1 runs
    sequentially)."""
    from pdfmanager import ocr as _ocr  # it uses this module

    return _ocr.run(path, output, language=language, force=force, deskew=deskew,
                    password=password, engine=engine, rotate=rotate, quality=quality,
                    report=report, workers=workers)


# pdfium is not thread-safe: every use of pypdfium2 must hold this lock
# (the GUI server renders thumbnails for several files at once).
PDFIUM_LOCK = threading.Lock()

# The most pixels one page is drawn with: a poster or a drawing sheet of
# several metres would otherwise need gigabytes of memory at 150 dpi.
MAX_RENDER_PIXELS = 60_000_000


def render_scale(page, scale, limit=None):
    """``scale`` for drawing the pdfium ``page``, made smaller if the page
    would come out with more than MAX_RENDER_PIXELS pixels."""
    import math

    limit = limit or getattr(sys.modules.get("pdfmanager.core"), "MAX_RENDER_PIXELS", MAX_RENDER_PIXELS)
    w, h = show_whole_page(page).get_size()
    area = max(1.0, abs(w * h))
    if area * scale * scale > limit:
        scale = math.sqrt(limit / area)
    return scale


def show_whole_page(page):
    """A pdfium ``page`` whose crop box lies outside its media box has no
    size at all; such a page is shown whole (as pdf.js and Acrobat do)."""
    width, height = page.get_size()
    if width < 1 or height < 1:
        x0, y0, x1, y1 = page.get_mediabox()
        if abs(x1 - x0) >= 1 and abs(y1 - y0) >= 1:
            page.set_cropbox(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    return page


def pdf_to_images(path, outdir, dpi=150, fmt="png", pages=None, password=None):
    """Render pages to image files with pypdfium2."""
    import pypdfium2 as pdfium

    outdir = Path(outdir)
    open_pdf(path, password)  # a plain error for a damaged file or a wrong password
    with PDFIUM_LOCK:
        try:
            doc = pdfium.PdfDocument(str(path), password=password)
        except pdfium.PdfiumError as exc:
            raise PdfManagerError(f"{path} can't be rendered ({exc})") from None
        total = len(doc)
    try:
        indices = parse_page_ranges(pages, total) if pages else range(total)
        outdir.mkdir(parents=True, exist_ok=True)
        width = len(str(total))
        written, notes_said = [], []
        for n, i in enumerate(indices):
            progress.report(n, len(indices), "page")
            with PDFIUM_LOCK:
                page = show_whole_page(doc[i])
                try:
                    scale = getattr(sys.modules.get("pdfmanager.core"), "render_scale", render_scale)(page, dpi / 72)
                    image = page.render(scale=scale).to_pil()
                finally:
                    page.close()
            if scale < dpi / 72 and not notes_said:
                notes_said.append(i)
                progress.say(f"page {i + 1} is very large: drawn at {scale * 72:.0f} dpi")
            if fmt.lower() in ("jpg", "jpeg") and image.mode != "RGB":
                image = image.convert("RGB")
            target = outdir / f"{Path(path).stem}_{i + 1:0{width}d}.{fmt}"
            shown = round(scale * 72, 2)
            image.save(target, dpi=(shown, shown))  # so the pictures keep their size
            written.append(target)
        return written
    finally:
        with PDFIUM_LOCK:
            doc.close()
