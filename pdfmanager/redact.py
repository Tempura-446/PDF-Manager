"""Redaction: permanently black out text and areas.

Simply drawing a black box over text leaves the text in the file, where it
can be copied out. Here every page with something to redact is replaced by
a picture of the page with the black boxes burned in, so the text, images
and comments on that page are gone for good. Pages without redactions are
left untouched. Redacted pages can be made searchable again with OCR (the
blacked-out parts stay black).
"""

import io
import re

from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

from pdfmanager import core, progress

PAD = 1.5  # points added around found text

# Ready-made patterns for things people often need to black out.
PRESETS = {
    "emails": r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
    "phones": r"(?<![\w.])\+?\(?\d[\d ()./-]{6,}\d(?![\w.])",
    "numbers": r"(?<!\w)\d(?:[ -]?\d){7,}(?!\w)",  # card, account and ID numbers
}


def find_text(path, terms=(), patterns=(), case_sensitive=False, whole_words=False,
              password=None):
    """Boxes around every match: {0-based page: [(x0, y0, x1, y1), ...]} in
    PDF points (origin bottom left, before the page's rotation). ``terms``
    are plain text, ``patterns`` regular expressions."""
    import pypdfium2 as pdfium

    regexes = []
    flags = 0 if case_sensitive else re.IGNORECASE
    for term in terms:
        term = term.strip()
        if term:
            # Any run of spaces in a term matches any whitespace (line breaks too).
            body = r"\s+".join(re.escape(part) for part in term.split())
            regexes.append(re.compile(rf"\b{body}\b" if whole_words else body, flags))
    for pattern in patterns:
        try:
            regexes.append(re.compile(pattern, flags))
        except re.error as exc:
            raise core.PdfManagerError(f"invalid pattern {pattern!r}: {exc}") from None
    found = {}
    if not regexes:
        return found
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path), password=password)
        try:
            for number, page in enumerate(doc):
                progress.report(number, len(doc), "searching page")
                textpage = page.get_textpage()
                text = textpage.get_text_range()
                boxes = []
                for regex in regexes:
                    for match in regex.finditer(text):
                        boxes.extend(_match_boxes(textpage, match.start(), match.end()))
                textpage.close()
                if boxes:
                    found[number] = _distinct(boxes)
        finally:
            doc.close()
    return found


def _distinct(boxes, slack=1.0):
    """Drop boxes that lie inside another (when two searches find the same text)."""
    def inside(a, b):
        return (a[0] >= b[0] - slack and a[1] >= b[1] - slack and a[2] <= b[2] + slack
                and a[3] <= b[3] + slack)
    unique = []
    for box in sorted(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True):
        if not any(inside(box, other) for other in unique):
            unique.append(box)
    return unique


def _match_boxes(textpage, start, end):
    """One box per line of a match."""
    lines = []
    for i in range(start, end):
        x0, y0, x1, y1 = textpage.get_charbox(i)
        if x1 - x0 <= 0 and y1 - y0 <= 0:
            continue  # spaces and line breaks pdfium adds itself
        line = next((ln for ln in lines if y0 < ln[3] and y1 > ln[1]), None)
        if line is None:
            lines.append([x0, y0, x1, y1])
        else:
            line[0], line[1] = min(line[0], x0), min(line[1], y0)
            line[2], line[3] = max(line[2], x1), max(line[3], y1)
    boxes = []
    for x0, y0, x1, y1 in lines:
        h = y1 - y0
        boxes.append((x0 - PAD, y0 - 0.25 * h - PAD, x1 + PAD, y1 + 0.1 * h + PAD))
    return boxes


def _parse_box(spec):
    try:
        page, coords = spec.split(":", 1)
        x0, y0, x1, y1 = (float(v) for v in coords.split(","))
        return int(page) - 1, (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    except ValueError:
        raise core.PdfManagerError(
            f"invalid box {spec!r}; use PAGE:X0,Y0,X1,Y1 in points from the bottom left") \
            from None


def redact(path, output, terms=(), patterns=(), boxes=None, case_sensitive=False,
           whole_words=False, dpi=200, password=None):
    """Black out every match of ``terms``/``patterns`` and the given
    ``boxes`` ({0-based page: [(x0, y0, x1, y1)]} in PDF points, or
    "PAGE:X0,Y0,X1,Y1" strings with 1-based pages). Returns
    {1-based page: number of areas blacked out}."""
    reader = core.open_pdf(path, password)
    areas = find_text(path, terms, patterns, case_sensitive, whole_words, password)
    if isinstance(boxes, (list, tuple)):
        parsed = {}
        for spec in boxes:
            page, box = _parse_box(spec)
            parsed.setdefault(page, []).append(box)
        boxes = parsed
    for page, page_boxes in (boxes or {}).items():
        if not 0 <= int(page) < len(reader.pages):
            raise core.PdfManagerError(f"page {int(page) + 1} doesn't exist")
        areas.setdefault(int(page), []).extend(tuple(map(float, b)) for b in page_boxes)
    writer = PdfWriter(clone_from=reader)
    if areas:
        images = _render_redacted(path, areas, dpi, password)
        removed_widgets = set()
        for number, (image, box) in images.items():
            removed_widgets |= _replace_page(writer, writer.pages[number], image, box)
        _prune_fields(writer, removed_widgets)
    core._write(writer, output)
    return {number + 1: len(found) for number, found in sorted(areas.items())}


def _render_redacted(path, areas, dpi, password):
    """{page: (PIL image with black boxes, crop box)} at ``dpi``."""
    import pypdfium2 as pdfium
    from PIL import ImageDraw

    scale = dpi / 72
    images = {}
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path), password=password)
        try:
            for n, (number, boxes) in enumerate(areas.items()):
                progress.report(n, len(areas), "blacking out page")
                page = doc[number]
                page.set_rotation(0)  # draw in the page's own coordinates
                left, bottom, right, top = page.get_cropbox()
                image = page.render(scale=scale, may_draw_forms=True).to_pil().convert("RGB")
                draw = ImageDraw.Draw(image)
                for x0, y0, x1, y1 in boxes:
                    draw.rectangle([(x0 - left) * scale, (top - y1) * scale,
                                    (x1 - left) * scale, (top - y0) * scale], fill=(0, 0, 0))
                images[number] = (image, (left, bottom, right, top))
        finally:
            doc.close()
    return images


def _image_page(image, width, height):
    """A one-page PDF showing ``image`` at width x height points."""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    if image.convert("L").convert("RGB").tobytes() == image.tobytes():
        image = image.convert("L")  # black and white or gray: a third of the size
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.drawImage(ImageReader(image), 0, 0, width, height)
    c.showPage()
    c.save()
    return PdfReader(buf).pages[0]


def _replace_page(writer, page, image, box):
    """Replace everything on ``page`` with ``image`` (keeping its size,
    rotation and links into it). Returns ids of removed form widgets."""
    left, bottom, right, top = box
    removed = set()
    for ref in page.get("/Annots") or []:
        if ref.get_object().get("/Subtype") == "/Widget":
            removed.add(getattr(ref, "idnum", None))
    for key in ("/Annots", "/Contents", "/Resources", "/Group", "/Thumb", "/PieceInfo",
                "/Metadata", "/AA"):
        if key in page:
            del page[key]
    empty = DecodedStreamObject()
    empty.set_data(b"")
    page[NameObject("/Contents")] = writer._add_object(empty)
    page[NameObject("/Resources")] = DictionaryObject()
    page.mediabox = page.cropbox  # nothing is left outside the visible area
    page.merge_transformed_page(_image_page(image, right - left, top - bottom),
                                Transformation().translate(left, bottom))
    return removed - {None}


def _prune_fields(writer, widget_ids):
    """Drop form fields whose widgets were on redacted pages."""
    if not widget_ids or "/AcroForm" not in writer._root_object:
        return
    acroform = writer._root_object["/AcroForm"].get_object()

    def keep(ref):
        field = ref.get_object()
        if getattr(ref, "idnum", None) in widget_ids:
            return False
        kids = field.get("/Kids")
        if kids is not None:
            remaining = ArrayObject(k for k in kids if keep(k))
            if not remaining:
                return False
            field[NameObject("/Kids")] = remaining
        return True

    acroform[NameObject("/Fields")] = ArrayObject(f for f in acroform.get("/Fields", []) if keep(f))
