# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Page geometry, paper sizes, rotation, resize, crop, N-up, booklet imposition, and margin trim."""

import re
from pathlib import Path

from pypdf import PageObject, PdfWriter, Transformation
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, RectangleObject, StreamObject

from pdfmanager import progress
from pdfmanager.errors import PdfManagerError
from pdfmanager.pageranges import parse_page_ranges
from pdfmanager.core.io import (
    PAPER_SIZES, POINTS_PER_INCH, POINTS_PER_MM,
    _own_contents, _reorder_pages, _shared_objects, _write, _writer_from_pages,
    clone_document, open_pdf,
)

def parse_paper_size(spec):
    """Parse 'a4', 'letter', 'a4-landscape', '210x297mm', '8.5x11in' or '600x800'
    (points) into (width, height) in points."""
    spec = spec.strip().lower()
    landscape = False
    for suffix in ("-landscape", ":landscape", "-l"):
        if spec.endswith(suffix):
            landscape, spec = True, spec[: -len(suffix)]
            break
    if spec in PAPER_SIZES:
        w, h = PAPER_SIZES[spec]
    else:
        unit = 1.0
        for suffix, factor in (("mm", POINTS_PER_MM), ("cm", POINTS_PER_MM * 10),
                               ("in", POINTS_PER_INCH), ("pt", 1.0)):
            if spec.endswith(suffix):
                unit, spec = factor, spec[: -len(suffix)]
                break
        try:
            w_str, h_str = spec.split("x")
            w, h = float(w_str) * unit, float(h_str) * unit
        except ValueError:
            known = ", ".join(sorted(PAPER_SIZES))
            raise PdfManagerError(
                f"unknown paper size {spec!r}; use one of {known} or WxH[mm|cm|in|pt]"
            ) from None
        if w <= 0 or h <= 0:
            raise PdfManagerError("paper dimensions must be positive")
    if landscape:
        w, h = max(w, h), min(w, h)
    return float(w), float(h)


def _visible_box(page):
    """The part of the page viewers show: its crop box, within the media box."""
    media, crop = page.mediabox, page.cropbox
    left, bottom = max(float(media.left), float(crop.left)), max(float(media.bottom), float(crop.bottom))
    right, top = min(float(media.right), float(crop.right)), min(float(media.top), float(crop.top))
    if right <= left or top <= bottom:  # a crop box outside the page: ignore it
        left, bottom, right, top = (float(v) for v in (media.left, media.bottom, media.right, media.top))
    return RectangleObject([left, bottom, right, top])


def _page_size(page):
    box = _visible_box(page)
    w, h = float(box.width), float(box.height)
    if (page.rotation or 0) % 180 == 90:
        w, h = h, w
    return w, h


def _moved_points(values, op):
    points = []
    for i in range(0, len(values) - 1, 2):
        points.extend(op.apply_on((float(values[i]), float(values[i + 1]))))
    return ArrayObject(FloatObject(v) for v in points)


def _turn_appearances(annot, rotation):
    """Turn an annotation's appearance streams by ``rotation`` degrees.
    Viewers fit an appearance's box into /Rect without turning it, so a
    turned /Rect alone shows stamps, signatures and notes sideways."""
    ap = annot.get("/AP")
    ap = ap.get_object() if ap is not None else None
    if not isinstance(ap, DictionaryObject):
        return
    for kind in ("/N", "/R", "/D"):
        normal = ap.get(kind)
        normal = normal.get_object() if normal is not None else None
        if normal is None:
            continue
        states = [normal]
        if isinstance(normal, DictionaryObject) and not isinstance(normal, StreamObject):
            states = [s.get_object() for s in normal.values()]  # one per state (checkboxes)
        for stream in states:
            if not isinstance(stream, StreamObject):
                continue
            old = Transformation(tuple(float(v) for v in stream.get("/Matrix", (1, 0, 0, 1, 0, 0))))
            new = old.transform(Transformation().rotate(rotation))
            stream[NameObject("/Matrix")] = ArrayObject(FloatObject(v) for v in new.ctm)


def _transform_annotations(page, op, turn=0):
    """Move and resize the page's annotations (links, form fields, comments,
    drawings) by ``op`` so they stay on top of the content they belong to.
    With ``turn`` (degrees) their appearances are turned as well."""
    annots = page.get("/Annots")
    if annots is None:
        return
    for ref in annots.get_object():
        annot = ref.get_object()
        if not isinstance(annot, DictionaryObject):
            continue  # a null or missing annotation: viewers skip it too
        rect = annot.get("/Rect")
        if rect is not None and len(rect) == 4:
            x1, y1 = op.apply_on((float(rect[0]), float(rect[1])))
            x2, y2 = op.apply_on((float(rect[2]), float(rect[3])))
            annot[NameObject("/Rect")] = RectangleObject(
                [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)])
        # Highlights, lines, polygons and callout arrows keep their own points.
        for key in ("/QuadPoints", "/L", "/Vertices", "/CL"):
            points = annot.get(key)
            if points is not None:
                annot[NameObject(key)] = _moved_points(points.get_object(), op)
        ink = annot.get("/InkList")
        if ink is not None:
            annot[NameObject("/InkList")] = ArrayObject(
                _moved_points(path.get_object(), op) for path in ink.get_object())
        if turn:
            _turn_appearances(annot, turn)


def _transfer_rotation(page):
    """Like ``page.transfer_rotation_to_content()`` but also turns the
    page's annotations, appearances included."""
    rotation = quarter_turn(page.rotation)
    if not rotation:
        return
    box = page.mediabox
    corners = [(float(box.left), float(box.bottom)), (float(box.right), float(box.top))]
    rotate = Transformation().rotate(-rotation)
    turned = [rotate.apply_on(c) for c in corners]
    page.transfer_rotation_to_content()
    new = page.mediabox
    dx = float(new.left) - min(x for x, _ in turned)
    dy = float(new.bottom) - min(y for _, y in turned)
    _transform_annotations(page, rotate.translate(dx, dy), turn=-rotation)


def _describe_size(w, h):
    for name, (pw, ph) in PAPER_SIZES.items():
        if abs(w - pw) < 3 and abs(h - ph) < 3:
            return name.upper() if name.startswith(("a", "b")) else name.capitalize()
        if abs(w - ph) < 3 and abs(h - pw) < 3:
            label = name.upper() if name.startswith(("a", "b")) else name.capitalize()
            return f"{label} landscape"
    return f"{w / POINTS_PER_MM:.0f}x{h / POINTS_PER_MM:.0f} mm"


# --------------------------------------------------------------------------
# Page geometry
# --------------------------------------------------------------------------

def arrange(path, output, plan, inserts=(), password=None):
    """Rebuild a document page by page, as the reader's page tools do.
    ``plan`` lists the new document's pages in order: {"page": 1-based page
    of ``path``, or "file": n (1-based, of ``inserts``) and "page", and
    "rotate": extra degrees; a "file" without "page" goes in whole}. Pages
    left out are deleted, a page listed
    twice is copied. Bookmarks, form fields and links between kept pages
    come along."""
    if not isinstance(plan, (list, tuple)) or not plan:
        raise PdfManagerError("a document needs at least one page")
    readers = [open_pdf(path, password)] + [open_pdf(p) for p in inserts]
    wanted = []
    for entry in plan:
        try:
            source = int(entry.get("file", 0))
            turn = int(entry.get("rotate", 0))
            # An inserted file without a page number goes in whole.
            pages = [int(entry["page"]) - 1] if "page" in entry or source == 0 else None
        except (AttributeError, KeyError, TypeError, ValueError):
            raise PdfManagerError("each page needs its page number") from None
        if not 0 <= source < len(readers):
            raise PdfManagerError(f"there's no file {source} to take pages from")
        for page in range(len(readers[source].pages)) if pages is None else pages:
            if not 0 <= page < len(readers[source].pages):
                raise PdfManagerError(f"page {page + 1} doesn't exist")
            if turn % 90:
                raise PdfManagerError("rotation must be a multiple of 90 degrees")
            wanted.append((source, page, turn % 360))
    if all(source == 0 for source, _, _ in wanted):
        writer = _writer_from_pages(readers[0], [page for _, page, _ in wanted])
    else:
        writer = PdfWriter()
        where = {}  # (file, page) -> index among the pages appended
        for source, reader in enumerate(readers):
            pages = list(dict.fromkeys(page for s, page, _ in wanted if s == source))
            if not pages:
                continue
            writer.append(reader, pages=pages, import_outline=source == 0)
            for page in pages:
                where[(source, page)] = len(where)
        _reorder_pages(writer, [where[(source, page)] for source, page, _ in wanted])
        if readers[0].metadata:
            writer.add_metadata({k: v for k, v in readers[0].metadata.items() if isinstance(v, str)})
    # A copied page is its own page dictionary: turning it leaves the other alone.
    for page, (_, _, turn) in zip(writer.pages, wanted):
        if turn:
            page.rotate(turn)
    return _write(writer, output)


def rotate(path, output, degrees, pages=None, password=None):
    if degrees % 90:
        raise PdfManagerError("rotation must be a multiple of 90 degrees")
    reader = open_pdf(path, password)
    targets = set(parse_page_ranges(pages, len(reader.pages))) if pages else None
    writer = clone_document(reader)
    for i, page in enumerate(writer.pages):
        if targets is None or i in targets:
            page.rotate(degrees)
    return _write(writer, output)


def resize(path, output, size=None, scale=None, fit="fit", pages=None,
           auto_orient=True, password=None):
    """Resize pages either to a paper ``size`` or by a ``scale`` factor.

    ``fit`` controls how content goes onto a new paper size:
      - "fit":     scale uniformly to fit inside the page, centred (default)
      - "fill":    scale uniformly to cover the page, centred (may crop)
      - "stretch": scale each axis independently to exactly fill the page
      - "none":    keep content at original size, centred (pads or crops)
    With ``auto_orient`` the target page is rotated to match each source
    page's orientation (landscape pages stay landscape).
    """
    if (size is None) == (scale is None):
        raise PdfManagerError("specify exactly one of size or scale")
    if fit not in ("fit", "fill", "stretch", "none"):
        raise PdfManagerError(f"unknown fit mode {fit!r}")
    if scale is not None and scale <= 0:
        raise PdfManagerError("scale must be positive")

    reader = open_pdf(path, password)
    targets = set(parse_page_ranges(pages, len(reader.pages))) if pages else None
    target = parse_paper_size(size) if size else None
    writer = clone_document(reader)
    shared = _shared_objects(writer)

    for i, page in enumerate(writer.pages):
        if targets is not None and i not in targets:
            continue
        _own_contents(page, shared)
        # Bake any /Rotate into the content so the geometry below is simple.
        _transfer_rotation(page)
        # The new page holds what the old one showed (its crop box).
        box = _visible_box(page)
        sw, sh = float(box.width), float(box.height)
        left, bottom = float(box.left), float(box.bottom)

        if scale is not None:
            tw, th = sw * scale, sh * scale
            sx = sy = scale
        else:
            tw, th = target
            if auto_orient and (sw > sh) != (tw > th) and sw != sh:
                tw, th = th, tw
            if not sw or not sh:
                raise PdfManagerError(f"page {i + 1} has no size (its box is empty), so it can't be resized")
            if fit == "fit":
                sx = sy = min(tw / sw, th / sh)
            elif fit == "fill":
                sx = sy = max(tw / sw, th / sh)
            elif fit == "stretch":
                sx, sy = tw / sw, th / sh
            else:
                sx = sy = 1.0

        dx = (tw - sw * sx) / 2
        dy = (th - sh * sy) / 2
        op = Transformation().translate(-left, -bottom).scale(sx, sy).translate(dx, dy)
        page.add_transformation(op)
        _transform_annotations(page, op)
        page.mediabox = RectangleObject([0, 0, tw, th])
        for name in ("/CropBox", "/TrimBox", "/BleedBox", "/ArtBox"):
            if name in page:
                del page[name]

    return _write(writer, output)


def quarter_turn(rotation):
    """A page's /Rotate as 0, 90, 180 or 270. The value must be a multiple of
    90; anything else counts as 0, as viewers (pdf.js, Acrobat) show it."""
    try:
        rotation = int(rotation or 0) % 360
    except (TypeError, ValueError):
        return 0
    return rotation if rotation % 90 == 0 else 0


def _unturned(margins, rotation):
    """Margins as a page is shown (left, bottom, right, top) -> the same
    edges in the page's own coordinates, before its /Rotate."""
    left, bottom, right, top = margins
    return {0: (left, bottom, right, top), 90: (top, left, bottom, right),
            180: (right, top, left, bottom), 270: (bottom, right, top, left)}[quarter_turn(rotation)]


def crop(path, output, margins, pages=None, visual=True, password=None):
    """Trim ``margins`` (left, bottom, right, top) in points from what each
    page shows (its crop box). ``visual`` margins are as the page is shown,
    so "top" is the top of a page that is turned by /Rotate; otherwise they
    are in the page's own coordinates (what the page editor measures)."""
    reader = open_pdf(path, password)
    targets = set(parse_page_ranges(pages, len(reader.pages))) if pages else None
    writer = clone_document(reader)
    for i, page in enumerate(writer.pages):
        if targets is not None and i not in targets:
            continue
        left, bottom, right, top = _unturned(margins, page.rotation or 0) if visual else margins
        box = _visible_box(page)
        new = [float(box.left) + left, float(box.bottom) + bottom,
               float(box.right) - right, float(box.top) - top]
        if new[2] <= new[0] or new[3] <= new[1]:
            raise PdfManagerError(f"margins are larger than page {i + 1}")
        page.mediabox = RectangleObject(new)
        page.cropbox = RectangleObject(new)
        for name in ("/TrimBox", "/BleedBox", "/ArtBox"):
            if name in page:
                del page[name]
    return _write(writer, output)


def n_up(path, output, per_sheet=2, size="a4", password=None):
    """Place 2, 4, 6, 8 or 9 pages on each sheet (handout style)."""
    layouts = {2: (2, 1), 4: (2, 2), 6: (3, 2), 8: (4, 2), 9: (3, 3)}
    if per_sheet not in layouts:
        raise PdfManagerError(f"pages per sheet must be one of {sorted(layouts)}")
    cols, rows = layouts[per_sheet]
    sw, sh = parse_paper_size(size)
    # Use landscape sheets for wide grids.
    if cols > rows:
        sw, sh = max(sw, sh), min(sw, sh)
    else:
        sw, sh = min(sw, sh), max(sw, sh)
    total = len(open_pdf(path, password).pages)
    sheets = [list(range(i, min(i + per_sheet, total))) for i in range(0, total, per_sheet)]
    return _write(_impose(path, password, sheets, (sw, sh), cols, rows), output)


def _impose(path, password, sheets, sheet_size, cols, rows, fill=0.95):
    """A writer with sheets of ``sheet_size`` holding the pages of ``path``
    in a grid of ``cols`` x ``rows`` cells: ``sheets`` lists, per sheet, the
    0-based page for each cell (row by row from the top left; None leaves
    the cell empty). Each page is scaled to ``fill`` of its cell, centred.
    Links, form fields and comments move with their page; links to other
    pages point to the sheet that holds that page."""
    sw, sh = sheet_size
    cell_w, cell_h = sw / cols, sh / rows
    # Work on a copy so pages can be normalised without touching the reader.
    source = clone_document(open_pdf(path, password))
    shared = _shared_objects(source)
    page_numbers = {p.indirect_reference.idnum: i for i, p in enumerate(source.pages)}
    for page in source.pages:
        _own_contents(page, shared)
        _retarget_links(page, page_numbers)
    writer = PdfWriter()
    sheet_of = {}  # page -> the sheet it is on
    for n, cells in enumerate(sheets):
        progress.report(n, len(sheets), "sheet")
        sheet = writer.add_blank_page(width=sw, height=sh)
        for slot, i in enumerate(cells):
            if i is None:
                continue
            src = source.pages[i]
            sheet_of.setdefault(i, n)
            _transfer_rotation(src)
            box = _visible_box(src)  # what the page shows goes in the cell
            pw, ph = float(box.width), float(box.height)
            if not pw or not ph:
                raise PdfManagerError(f"page {i + 1} has no size (its box is empty), so it can't be placed")
            s = min(cell_w / pw, cell_h / ph) * fill
            col, row = slot % cols, slot // cols
            x = col * cell_w + (cell_w - pw * s) / 2
            y = sh - (row + 1) * cell_h + (cell_h - ph * s) / 2
            op = Transformation().translate(-float(box.left), -float(box.bottom)).scale(s, s).translate(x, y)
            sheet.merge_transformed_page(src, op)  # also moves the annotations
    _resolve_links(writer, sheet_of)
    return writer


def booklet(path, output, size=None, password=None):
    """Arrange pages for a booklet printed on both sides of the paper,
    folded in the middle and stapled on the fold (saddle stitch): two pages
    side by side on each landscape sheet, in the order that puts them in
    sequence once folded (n, 1 | 2, n-1 | n-2, 3 | ...). Blank pages are
    added at the end to make a multiple of four.

    ``size`` is the sheet (paper) size, e.g. "a4" or "letter", turned
    landscape; by default a sheet is twice the first page (A5 pages give
    A4 sheets, A4 pages A3 sheets). Print the result double-sided,
    flipping on the short edge."""
    reader = open_pdf(path, password)
    total = len(reader.pages)
    if size:
        w, h = parse_paper_size(size)
        sheet = (max(w, h), min(w, h))
    else:
        w, h = _page_size(reader.pages[0])
        sheet = (2 * w, h)
    padded = -(-total // 4) * 4
    sides = []
    for k in range(padded // 4):
        sides.append([padded - 1 - 2 * k, 2 * k])      # front of sheet k + 1
        sides.append([2 * k + 1, padded - 2 - 2 * k])  # its back
    sides = [[i if i < total else None for i in side] for side in sides]
    return _write(_impose(path, password, sides, sheet, 2, 1, fill=1.0), output)


_LINK_TARGET = NameObject("/PdfmTargetPage")


def _retarget_links(page, page_numbers):
    """Replace links to other pages with a placeholder page number, so
    merging doesn't drag whole pages along; ``_resolve_links`` points them
    at the right sheet afterwards. Named destinations can't be kept."""
    annots = page.get("/Annots")
    if annots is None:
        return
    keep = ArrayObject()
    for ref in annots.get_object():
        annot = ref.get_object()
        if not isinstance(annot, DictionaryObject):
            continue  # a null or missing annotation
        if annot.get("/Subtype") == "/Link":
            dest = annot.get("/Dest")
            action = annot.get("/A")
            if action is not None and action.get_object().get("/S") == "/GoTo":
                dest = action.get_object().get("/D")
            if dest is not None:
                dest = dest.get_object()
                target = None
                if isinstance(dest, ArrayObject) and dest and hasattr(dest[0], "idnum"):
                    target = page_numbers.get(dest[0].idnum)
                for key in ("/Dest", "/A"):
                    if key in annot:
                        del annot[key]
                if target is None:
                    continue  # named or broken destination: drop the link
                annot[_LINK_TARGET] = NumberObject(target)
        keep.append(ref)
    page[NameObject("/Annots")] = keep


def _resolve_links(writer, sheet_of):
    """Point the links ``_retarget_links`` marked at the sheet that holds
    their page (``sheet_of``: page -> sheet)."""
    for sheet in writer.pages:
        for ref in sheet.get("/Annots", ArrayObject()):
            annot = ref.get_object()
            if isinstance(annot, DictionaryObject) and _LINK_TARGET in annot:
                target = sheet_of.get(int(annot[_LINK_TARGET]))
                del annot[_LINK_TARGET]
                if target is not None:
                    annot[NameObject("/Dest")] = ArrayObject(
                        [writer.pages[target].indirect_reference, NameObject("/Fit")])


# --------------------------------------------------------------------------
# Blank pages and white margins
# --------------------------------------------------------------------------

def _insert_positions(after, total, at_start=False):
    """Where blank pages go: the number of pages before each place, from a
    page list ("3,7", "end", "all"; 0 or "start" = before the first page)."""
    positions = {0} if at_start else set()
    rest = []
    for token in str(after or "").split(","):
        token = token.strip().lower()
        if token in ("0", "start"):
            positions.add(0)
        elif token:
            rest.append(token)
    if rest:
        positions.update(i + 1 for i in parse_page_ranges(",".join(rest), total))
    if not positions:
        raise PdfManagerError("say after which pages the blank pages go")
    return sorted(positions)


def insert_blank_pages(path, output, after=None, count=1, at_start=False, password=None):
    """Insert ``count`` blank pages after each page in ``after`` (a page
    list such as "3,7" or "end"; "all" puts them after every page, e.g. to
    print with a sheet for notes beside each page; 0 means before the first
    page, as does ``at_start``). Each blank page has the size of the page
    before it (at the start, of the first page). Bookmarks, links and form
    fields are kept."""
    if not 1 <= int(count) <= 100:
        raise PdfManagerError("insert between 1 and 100 blank pages at each place")
    reader = open_pdf(path, password)
    positions = _insert_positions(after, len(reader.pages), at_start)
    writer = clone_document(reader)
    sizes = [_page_size(page) for page in writer.pages]
    for n, position in enumerate(reversed(positions)):  # from the end, so positions stay put
        progress.report(n, len(positions), "place")
        width, height = sizes[max(position - 1, 0)]
        for _ in range(int(count)):
            writer.insert_blank_page(width, height, position)
    return _write(writer, output)


def trim_margins(path, output, padding=10, same=False, pages=None, tolerance=24, dpi=50,
                 password=None):
    """Crop away the white margins around each page's content: the pages
    are drawn at ``dpi`` and everything lighter than ``tolerance`` below
    pure white counts as margin. ``padding`` (points) is kept around the
    content. With ``same``, every page gets the same crop, large enough
    for the content of all of them (good for a book whose page numbers or
    headings sit in different places); otherwise each page is trimmed on
    its own. Pages that are entirely blank are left as they are.

    Only the crop box changes (what viewers show and print); the media box
    stays, so the margins can be brought back.
    Returns {"trimmed": [...], "blank": [...]} with 1-based page numbers."""
    import pypdfium2 as pdfium
    import pdfmanager.core as _core
    PDFIUM_LOCK = _core.PDFIUM_LOCK
    render_scale = _core.render_scale

    if padding < 0:
        raise PdfManagerError("the space around the content can't be negative")
    reader = open_pdf(path, password)
    total = len(reader.pages)
    targets = sorted(set(parse_page_ranges(pages, total))) if pages else list(range(total))
    found, blank = {}, []  # page -> margins as fractions of the page as shown (l, b, r, t)
    white = 255 - max(0, min(254, int(tolerance)))
    with PDFIUM_LOCK:
        try:
            doc = pdfium.PdfDocument(str(path), password=password)
        except pdfium.PdfiumError as exc:
            raise PdfManagerError(f"{path} can't be rendered ({exc})") from None
        doc.init_forms()  # filled-in form fields count as content
    try:
        for n, i in enumerate(targets):
            progress.report(n, len(targets), "page")
            with PDFIUM_LOCK:
                page = doc[i]
                try:
                    image = page.render(scale=render_scale(page, dpi / 72), may_draw_forms=True).to_pil()
                finally:
                    page.close()
            gray = image.convert("L")
            box = gray.point(lambda v: 255 if v < white else 0).getbbox()
            if box is None:
                blank.append(i)
                continue
            w, h = gray.size
            x0, y0, x1, y1 = box
            found[i] = (x0 / w, (h - y1) / h, (w - x1) / w, y0 / h)
    finally:
        with PDFIUM_LOCK:
            doc.close()
    writer = clone_document(reader)
    margins = {}  # page -> margins to trim as the page is shown, in points
    for i, fractions in found.items():
        w, h = _page_size(writer.pages[i])
        margins[i] = [max(0.0, f * size - padding) for f, size in zip(fractions, (w, h, w, h))]
    if same and margins:
        common = [min(m[k] for m in margins.values()) for k in range(4)]
        margins = {i: common for i in margins}
    for i, shown in margins.items():
        page = writer.pages[i]
        left, bottom, right, top = _unturned(shown, page.rotation or 0)
        box = _visible_box(page)
        page.cropbox = RectangleObject([float(box.left) + left, float(box.bottom) + bottom,
                                        float(box.right) - right, float(box.top) - top])
        for name in ("/TrimBox", "/BleedBox", "/ArtBox"):
            if name in page:
                del page[name]
    _write(writer, output)
    return {"trimmed": [i + 1 for i in sorted(margins)], "blank": [i + 1 for i in blank]}
