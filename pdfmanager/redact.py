# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Redaction: permanently black out text and areas.

Simply drawing a black box over text leaves the text in the file, where it
can be copied out. There are two ways to really get rid of it:

- "remove" (the default, like Acrobat): what lies under the boxes is taken
  out of the page's content (letters, the covered pixels of pictures,
  drawings, comments and form fields), and the rest of the page stays real
  text that can be searched and copied. See redactcontent.py. A page that
  can't be edited safely, or that fails the check afterwards, falls back
  to the second way, and the report says which pages and why.
- "image": every page with something to redact is replaced by a picture of
  the page with the black boxes burned in, so the text, images and
  comments on that page are gone for good. Such pages can be made
  searchable again with OCR (the blacked-out parts stay black).

Pages without redactions are left untouched either way. Bookmark titles
that contain the searched words have those words blacked out too (as
"███"), since they would otherwise give them away.
"""

import io
import re

from pypdf import PdfReader, Transformation
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, NameObject,
                           RectangleObject, TextStringObject)

from pdfmanager import core, progress

PAD = 1.5  # points added around found text
METHODS = ("remove", "image")
BLACKED = "███"  # what replaces the searched words in bookmark titles

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

    regexes = _regexes(terms, patterns, case_sensitive, whole_words)
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


def _regexes(terms=(), patterns=(), case_sensitive=False, whole_words=False):
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
    return regexes


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


class Report(dict):
    """{1-based page: number of areas blacked out}, plus what else happened:

    - ``method``: "remove" or "image";
    - ``pictures``: {1-based page: why} for the pages that were turned into
      pictures although the method was "remove" (empty for "image", where
      every redacted page becomes one);
    - ``bookmarks``: how many bookmark titles had words blacked out.
    """

    def __init__(self, counts=(), method="remove", pictures=None, bookmarks=0):
        super().__init__(counts)
        self.method = method
        self.pictures = dict(pictures or {})
        self.bookmarks = bookmarks


def redact(path, output, terms=(), patterns=(), boxes=None, case_sensitive=False,
           whole_words=False, dpi=200, password=None, method="remove"):
    """Black out every match of ``terms``/``patterns`` and the given
    ``boxes`` ({0-based page: [(x0, y0, x1, y1)]} in PDF points, or
    "PAGE:X0,Y0,X1,Y1" strings with 1-based pages). ``method`` "remove"
    takes out what's under the boxes and keeps the rest of the page as it
    was; "image" turns the redacted pages into pictures (``dpi``). Returns
    a :class:`Report`: {1-based page: number of areas blacked out}."""
    if method not in METHODS:
        raise core.PdfManagerError(f"unknown redaction method {method!r}; use "
                                   + " or ".join(METHODS))
    reader = core.open_pdf(path, password)
    regexes = _regexes(terms, patterns, case_sensitive, whole_words)
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
    writer = core.clone_document(reader)
    pictures, bookmarks = {}, 0
    if areas:
        removed_widgets = set()
        if method == "remove":
            from pdfmanager import redactcontent

            data, pictures, removed = redactcontent.remove(path, password, areas, regexes)
            writer = core.clone_document(PdfReader(io.BytesIO(data)))
            kept = {n: found for n, found in areas.items() if n not in pictures}
            for number, found in kept.items():
                removed_widgets |= _drop_annotations(writer.pages[number], found)
            _prune_alt_text(writer, {n: removed.get(n, "") for n in kept}, regexes)
            to_picture = {n: areas[n] for n in pictures}
        else:
            to_picture = areas
        if to_picture:
            images = _render_redacted(path, to_picture, dpi, password)
            for number, (image, box) in images.items():
                removed_widgets |= _replace_page(writer, writer.pages[number], image, box)
            _prune_structure(writer, [writer.pages[n] for n in images])
        _prune_fields(writer, removed_widgets)
        bookmarks = _scrub_outline(writer, regexes)
        # pypdf writes every object it holds, unlinked or not, so the old
        # content, pictures and fields would still sit in the file. A copy
        # made from the written result carries only what is reachable.
        buf = io.BytesIO()
        writer.write(buf)
        buf.seek(0)
        writer = core.clone_document(PdfReader(buf))
    core._write(writer, output)
    return Report({number + 1: len(found) for number, found in sorted(areas.items())}, method,
                  {n + 1: why for n, why in sorted(pictures.items())}, bookmarks)


def _drop_annotations(page, areas):
    """Remove the comments, links and form fields on ``page`` that touch
    one of ``areas`` (and the pop-ups of removed comments). Returns ids of
    removed form widgets."""
    annots = page.get("/Annots")
    annots = annots.get_object() if annots is not None else None
    if not isinstance(annots, ArrayObject):
        return set()
    gone, widgets, kept, broken = set(), set(), [], False
    for ref in annots:
        annot = ref.get_object() if ref is not None else None
        if not isinstance(annot, DictionaryObject):
            broken = True  # dangling or broken: nothing worth keeping
            continue
        try:
            x0, y0, x1, y1 = (float(v) for v in annot.get("/Rect", [0, 0, 0, 0]))
        except (TypeError, ValueError):
            x0 = y0 = x1 = y1 = 0.0
        box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        if any(box[0] < a[2] and box[2] > a[0] and box[1] < a[3] and box[3] > a[1] for a in areas):
            gone.add(getattr(ref, "idnum", id(annot)))
            if annot.get("/Subtype") == "/Widget":
                widgets.add(getattr(ref, "idnum", None))
            continue
        kept.append((ref, annot))
    if not gone and not broken:
        return set()
    result = ArrayObject()
    for ref, annot in kept:
        parent = annot.raw_get("/Parent") if "/Parent" in annot else None
        if annot.get("/Subtype") == "/Popup" and getattr(parent, "idnum", None) in gone:
            continue
        popup = annot.raw_get("/Popup") if "/Popup" in annot else None
        if getattr(popup, "idnum", None) in gone:
            del annot["/Popup"]
        result.append(ref)
    page[NameObject("/Annots")] = result
    return widgets - {None}


def _prune_alt_text(writer, removed, regexes):
    """Drop the structure tree's replacement texts (/ActualText, /Alt, /E)
    on pages in ``removed`` ({0-based page: the text taken off it}) that
    repeat a removed or searched word: a tagged PDF can carry the words
    there a second time."""
    tree = writer._root_object.get("/StructTreeRoot")
    if tree is None or not removed:
        return
    pages = {writer.pages[n].indirect_reference.idnum: n for n in removed}
    words = {n: {w.lower() for w in re.findall(r"\w[\w@.'-]*\w", text)}
             for n, text in removed.items()}

    def repeats(value, n):
        value = str(value)
        low = value.lower()
        return any(r.search(value) for r in regexes) or any(w in low for w in words[n])

    pending = [(tree.get_object(), None)]
    seen = set()
    while pending:
        node, page = pending.pop()
        if not isinstance(node, DictionaryObject) or id(node) in seen:
            continue
        seen.add(id(node))
        if "/Pg" in node:
            page = pages.get(getattr(node.raw_get("/Pg"), "idnum", None))
        if page is not None:
            for key in ("/ActualText", "/Alt", "/E"):
                if key in node and repeats(node[key], page):
                    del node[key]
        kids = node.get("/K")
        kids = kids.get_object() if kids is not None else None
        for kid in (kids if isinstance(kids, ArrayObject) else [kids]):
            kid = kid.get_object() if kid is not None else None
            if isinstance(kid, DictionaryObject):
                pending.append((kid, page))


def _scrub_outline(writer, regexes):
    """Black out the searched words in bookmark titles, which would give
    them away otherwise. Returns how many titles changed."""
    outlines = writer._root_object.get("/Outlines")
    if outlines is None or not regexes:
        return 0
    changed = 0
    pending = [outlines.get_object()]
    seen = set()
    while pending:
        node = pending.pop()
        if not isinstance(node, DictionaryObject) or id(node) in seen:
            continue
        seen.add(id(node))
        title = node.get("/Title")
        if title is not None:
            new = str(title)
            for regex in regexes:
                new = regex.sub(BLACKED, new)
            if new != str(title):
                node[NameObject("/Title")] = TextStringObject(new)
                changed += 1
        for key in ("/First", "/Next"):
            if node.get(key) is not None:
                pending.append(node[key].get_object())
    return changed


def _visible_box(page):
    """The part of the page a reader shows and pdfium renders: the crop box
    within the media box (a crop box reaching outside it is cut off)."""
    media = page.get_mediabox()
    crop = page.get_cropbox()
    box = (max(media[0], crop[0]), max(media[1], crop[1]), min(media[2], crop[2]),
           min(media[3], crop[3]))
    return box if box[2] > box[0] and box[3] > box[1] else media


def _render_redacted(path, areas, dpi, password):
    """{page: (PIL image with black boxes, visible box)} at ``dpi``."""
    import pypdfium2 as pdfium
    from PIL import ImageDraw

    scale = dpi / 72
    images = {}
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path), password=password)
        doc.init_forms()  # without this, filled-in fields are left out of the picture
        try:
            for n, (number, boxes) in enumerate(areas.items()):
                progress.report(n, len(areas), "blacking out page")
                page = doc[number]
                page.set_rotation(0)  # draw in the page's own coordinates
                left, bottom, right, top = _visible_box(page)
                scale = core.render_scale(page, dpi / 72)
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

    if image.convert("L").convert("RGB").tobytes() == image.tobytes():
        image = image.convert("L")  # black and white or gray: a third of the size
    buf = io.BytesIO()
    c = core.pdf_canvas(buf, pagesize=(width, height))
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
        annot = ref.get_object()
        if isinstance(annot, DictionaryObject) and annot.get("/Subtype") == "/Widget":
            removed.add(getattr(ref, "idnum", None))
    for key in ("/Annots", "/Contents", "/Resources", "/Group", "/Thumb", "/PieceInfo",
                "/Metadata", "/AA", "/StructParents"):
        if key in page:
            del page[key]
    empty = DecodedStreamObject()
    empty.set_data(b"")
    page[NameObject("/Contents")] = writer._add_object(empty)
    page[NameObject("/Resources")] = DictionaryObject()
    # Nothing is left outside the visible area, which is what the picture shows.
    page.mediabox = page.cropbox = RectangleObject(box)
    page.merge_transformed_page(_image_page(image, right - left, top - bottom),
                                Transformation().translate(left, bottom))
    return removed - {None}


def _prune_structure(writer, pages):
    """Drop the structure tree's entries for the redacted ``pages``: a
    tagged PDF repeats their words there (/ActualText, /Alt)."""
    root = writer._root_object
    tree = root.get("/StructTreeRoot")
    if tree is None:
        return
    tree = tree.get_object()
    gone = {page.indirect_reference.idnum for page in pages}

    def on_page(obj):
        return getattr(obj.get("/Pg"), "idnum", None) in gone

    def prune(items):
        kept = ArrayObject()
        for item in items:
            obj = item.get_object()
            if isinstance(obj, DictionaryObject):
                if on_page(obj):
                    continue
                kids = obj.get("/K")
                if kids is not None:
                    kids = kids.get_object()
                    obj[NameObject("/K")] = prune(
                        kids if isinstance(kids, ArrayObject) else ArrayObject([obj.raw_get("/K")]))
            kept.append(item)
        return kept

    kids = tree.get("/K")
    if kids is not None:
        kids = kids.get_object()
        tree[NameObject("/K")] = prune(kids if isinstance(kids, ArrayObject)
                                       else ArrayObject([tree.raw_get("/K")]))
    # The parent tree points at the same elements, by page.
    pending = [tree.get("/ParentTree")]
    while pending:
        node = pending.pop()
        node = node.get_object() if node is not None else None
        if not isinstance(node, DictionaryObject):
            continue
        pending.extend(node.get("/Kids", []))
        nums = node.get("/Nums")
        if nums is None:
            continue
        nums = nums.get_object()
        kept = ArrayObject()
        for key, value in zip(nums[0::2], nums[1::2]):
            entry = value.get_object()
            if isinstance(entry, ArrayObject):
                entry = prune(entry)
                if not entry:
                    continue
                value = entry
            elif isinstance(entry, DictionaryObject) and on_page(entry):
                continue
            kept.extend([key, value])
        node[NameObject("/Nums")] = kept


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
