"""Comments and markup on pages: sticky notes, underline, strikeout and
shapes (rectangle, ellipse, cloud, line, arrow), and the comments on any
annotation (change, reply, delete).

They are written as standard PDF annotations with their own appearance
streams, so every viewer, printer and pdfium-based tool (flatten, redact)
shows them the same as the reader did. Coordinates are in the page's own
PDF space (points, origin at the bottom left of the media box).

A change is a dict:

    {"op": "add", "page": 1, "kind": "note", "at": [x, y], "text": "..."}
    {"op": "add", "page": 1, "kind": "underline" | "strikeout", "rects": [[x0, y0, x1, y1], ...]}
    {"op": "add", "page": 1, "kind": "rect" | "ellipse" | "cloud", "rect": [x0, y0, x1, y1]}
    {"op": "add", "page": 1, "kind": "line" | "arrow", "points": [[x0, y0], [x1, y1]]}
    {"op": "text", "page": 1, "id": "12R", "text": "..."}     (the comment of an annotation)
    {"op": "reply", "page": 1, "id": "12R", "text": "..."}
    {"op": "delete", "page": 1, "id": "12R"}                  (with its popup and replies)
    {"op": "add", "page": 1, "kind": "link", "rect": [...], "url": "https://..." | "to_page": 3}
    {"op": "link", "page": 1, "id": "12R", "url": "..." | "to_page": 3}   (where a link goes)
    {"op": "add", "page": 1, "kind": "stamp", "rect": [...], "label": "APPROVED",
     "dated": true}                     (a rubber stamp; dated adds "by NAME, DATE")

``add`` also takes "color" ("#rrggbb"), "width" (points, shapes) and
"text" (the comment). ``id`` is pdf.js's id of an annotation in the file
given: its object number followed by "R" (and the generation, if not 0).
"""

import getpass
import math
import re
import secrets
import time

from pypdf.generic import (ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NumberObject, TextStringObject)

from pdfmanager import core

KINDS = ("note", "underline", "strikeout", "rect", "ellipse", "cloud", "line", "arrow", "measure", "link",
         "stamp")
DEFAULT_COLORS = {"note": "#f5c400", "underline": "#1f4fd8", "strikeout": "#d52b1e"}
PRINT = 4  # annotation flag: printed with the page
NOTE_SIZE = 20


def default_author():
    try:
        return getpass.getuser()
    except Exception:  # no user name available (some services, containers)
        return "ZPDF"


def _pdf_date(now=None):
    stamp = time.localtime(now)
    offset = -time.timezone if not stamp.tm_isdst else -time.altzone
    sign = "+" if offset >= 0 else "-"
    hours, minutes = divmod(abs(offset) // 60, 60)
    return time.strftime("D:%Y%m%d%H%M%S", stamp) + f"{sign}{hours:02d}'{minutes:02d}'"


def _color(value, default):
    text = value if isinstance(value, str) and re.fullmatch(r"#?[0-9a-fA-F]{6}", value or "") else default
    text = text.lstrip("#")
    return [int(text[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise core.PdfManagerError("a position in the change is not a number") from None
    if not math.isfinite(number):
        raise core.PdfManagerError("a position in the change is not a number")
    return number


def _rect(values):
    try:
        x0, y0, x1, y1 = (_num(v) for v in values)
    except (TypeError, ValueError):
        raise core.PdfManagerError("a box needs four numbers") from None
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


def _fmt(value):
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def _rgb(color, stroke=True):
    return " ".join(_fmt(c) for c in color) + (" RG" if stroke else " rg")


def _appearance(writer, bbox, content, resources=None):
    """An appearance stream (/AP /N) drawing ``content``, in page coordinates."""
    stream = DecodedStreamObject()
    stream.set_data(content.encode("latin-1"))
    stream.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                   NameObject("/BBox"): ArrayObject(FloatObject(v) for v in bbox),
                   NameObject("/Resources"): resources if resources is not None else DictionaryObject()})
    return DictionaryObject({NameObject("/N"): writer._add_object(stream)})


def _ellipse_path(x0, y0, x1, y1):
    k = 0.5523
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    f = _fmt
    return (f"{f(cx + rx)} {f(cy)} m "
            f"{f(cx + rx)} {f(cy + k * ry)} {f(cx + k * rx)} {f(cy + ry)} {f(cx)} {f(cy + ry)} c "
            f"{f(cx - k * rx)} {f(cy + ry)} {f(cx - rx)} {f(cy + k * ry)} {f(cx - rx)} {f(cy)} c "
            f"{f(cx - rx)} {f(cy - k * ry)} {f(cx - k * rx)} {f(cy - ry)} {f(cx)} {f(cy - ry)} c "
            f"{f(cx + k * rx)} {f(cy - ry)} {f(cx + rx)} {f(cy - k * ry)} {f(cx + rx)} {f(cy)} c")


def _cloud_path(x0, y0, x1, y1, bump):
    """A cloud's outline around the box: half-circle bumps along each side,
    bulging outwards, each about ``bump`` points across."""
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]  # anticlockwise: outward is to the right
    parts, start = [], True
    for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1]):
        length = math.hypot(bx - ax, by - ay)
        count = max(1, round(length / bump))
        nx, ny = (by - ay) / length, -(bx - ax) / length  # outward normal
        for i in range(count):
            px, py = ax + (bx - ax) * i / count, ay + (by - ay) * i / count
            qx, qy = ax + (bx - ax) * (i + 1) / count, ay + (by - ay) * (i + 1) / count
            h = length / count / 2 * 4 / 3  # a half circle as one Bézier curve
            if start:
                parts.append(f"{_fmt(px)} {_fmt(py)} m")
                start = False
            parts.append(f"{_fmt(px + nx * h)} {_fmt(py + ny * h)} {_fmt(qx + nx * h)} {_fmt(qy + ny * h)} "
                         f"{_fmt(qx)} {_fmt(qy)} c")
    return " ".join(parts) + " h"


def _note_icon(x, y, color):
    """A speech bubble NOTE_SIZE points square with its top left at x, y."""
    s, f = NOTE_SIZE, _fmt
    left, bottom, right, top = x + 1, y - s + 5, x + s - 1, y - 1
    lines = " ".join(f"{f(left + 4)} {f(top - 5 - 4 * i)} m {f(right - 4)} {f(top - 5 - 4 * i)} l"
                     for i in range(3))
    return (f"{_rgb(color, False)} 0.25 0.25 0.25 RG 0.8 w "
            f"{f(left)} {f(bottom)} m {f(left + 4)} {f(bottom)} l {f(left + 2)} {f(y - s + 1)} l "
            f"{f(left + 9)} {f(bottom)} l {f(right)} {f(bottom)} l {f(right)} {f(top)} l "
            f"{f(left)} {f(top)} l h B 0.5 w {lines} S")


def _base(writer, page_ref, subtype, rect, color, author, text):
    now = _pdf_date()
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject(subtype),
        NameObject("/Rect"): ArrayObject(FloatObject(v) for v in rect),
        NameObject("/C"): ArrayObject(FloatObject(c) for c in color),
        NameObject("/T"): TextStringObject(author), NameObject("/M"): TextStringObject(now),
        NameObject("/CreationDate"): TextStringObject(now),
        NameObject("/NM"): TextStringObject("zpdf-" + secrets.token_hex(8)),
        NameObject("/F"): NumberObject(PRINT), NameObject("/P"): page_ref,
    })
    if text:
        annot[NameObject("/Contents")] = TextStringObject(text)
    return annot


def _add(writer, page, page_ref, change, author):
    kind = change.get("kind")
    if kind not in KINDS:
        raise core.PdfManagerError(f"unknown kind of annotation: {kind!r}")
    if kind == "link":
        annot = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Link"),
            NameObject("/Rect"): ArrayObject(FloatObject(v) for v in _rect(change.get("rect") or ())),
            NameObject("/Border"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(0)]),
            NameObject("/F"): NumberObject(PRINT), NameObject("/P"): page_ref})
        _set_target(writer, annot, change)
        ref = writer._add_object(annot)
        annots = page.get("/Annots")
        if annots is None:
            page[NameObject("/Annots")] = ArrayObject()
            annots = page["/Annots"]
        annots.get_object().append(ref)
        return ref
    if kind == "stamp":
        added = [_stamp(writer, page, page_ref, change, author)]
        annots = page.get("/Annots")
        if annots is None:
            page[NameObject("/Annots")] = ArrayObject()
            annots = page["/Annots"]
        annots.get_object().extend(added)
        return added[0]
    if kind == "measure":
        added = _measure(writer, page_ref, change, author)
        annots = page.get("/Annots")
        if annots is None:
            page[NameObject("/Annots")] = ArrayObject()
            annots = page["/Annots"]
        annots.get_object().extend(added)
        return added[0]
    color = _color(change.get("color"), DEFAULT_COLORS.get(kind, "#d52b1e"))
    text = str(change.get("text") or "")
    width = max(0.25, min(_num(change.get("width", 2)), 50))
    added = []
    if kind == "note":
        x, y = (_num(v) for v in change.get("at", ())[:2])
        rect = [x, y - NOTE_SIZE, x + NOTE_SIZE, y]
        annot = _base(writer, page_ref, "/Text", rect, color, author, text)
        annot.update({NameObject("/Name"): NameObject("/Comment"),
                      NameObject("/Open"): BooleanObject(False),
                      NameObject("/AP"): _appearance(writer, rect, _note_icon(x, y, color))})
        ref = writer._add_object(annot)
        popup = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Popup"),
            NameObject("/Rect"): ArrayObject(FloatObject(v) for v in [x + NOTE_SIZE, y - 120,
                                                                     x + NOTE_SIZE + 200, y]),
            NameObject("/Parent"): ref, NameObject("/Open"): BooleanObject(False), NameObject("/F"): NumberObject(0)})
        annot[NameObject("/Popup")] = writer._add_object(popup)
        added = [ref, annot["/Popup"]]
    elif kind in ("underline", "strikeout"):
        boxes = [_rect(r) for r in change.get("rects") or []]
        boxes = [b for b in boxes if b[2] > b[0] and b[3] > b[1]]
        if not boxes:
            raise core.PdfManagerError("select some text first")
        quads, strokes = [], []
        for x0, y0, x1, y1 in boxes:
            quads += [x0, y1, x1, y1, x0, y0, x1, y0]
            h = y1 - y0
            thick = max(0.5, h / 14)
            at = y0 + h * (0.42 if kind == "strikeout" else 0.1)
            strokes.append(f"{_fmt(thick)} w {_fmt(x0)} {_fmt(at)} m {_fmt(x1)} {_fmt(at)} l S")
        rect = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                max(b[3] for b in boxes)]
        annot = _base(writer, page_ref, "/Underline" if kind == "underline" else "/StrikeOut", rect,
                      color, author, text)
        annot[NameObject("/QuadPoints")] = ArrayObject(FloatObject(v) for v in quads)
        annot[NameObject("/AP")] = _appearance(writer, rect, f"{_rgb(color)} " + " ".join(strokes))
        added = [writer._add_object(annot)]
    elif kind in ("rect", "ellipse", "cloud"):
        x0, y0, x1, y1 = _rect(change.get("rect") or ())
        if x1 - x0 < 1 or y1 - y0 < 1:
            raise core.PdfManagerError("draw the shape bigger")
        border = DictionaryObject({NameObject("/W"): FloatObject(width), NameObject("/S"): NameObject("/S")})
        if kind == "cloud":
            bump = max(10.0, width * 6)
            pad = bump / 2 * 4 / 3 * 0.75 + width  # how far the bumps reach out
            rect = [x0 - pad, y0 - pad, x1 + pad, y1 + pad]
            annot = _base(writer, page_ref, "/Polygon", rect, color, author, text)
            annot[NameObject("/Vertices")] = ArrayObject(FloatObject(v) for v in [x0, y0, x1, y0, x1, y1, x0, y1])
            annot[NameObject("/BE")] = DictionaryObject({NameObject("/S"): NameObject("/C"),
                                                         NameObject("/I"): NumberObject(1)})
            path = _cloud_path(x0, y0, x1, y1, bump)
        else:
            half = width / 2
            rect = [x0 - half, y0 - half, x1 + half, y1 + half]
            annot = _base(writer, page_ref, "/Square" if kind == "rect" else "/Circle", rect, color, author, text)
            annot[NameObject("/RD")] = ArrayObject(FloatObject(half) for _ in range(4))
            path = (f"{_fmt(x0)} {_fmt(y0)} {_fmt(x1 - x0)} {_fmt(y1 - y0)} re" if kind == "rect"
                    else _ellipse_path(x0, y0, x1, y1))
        annot[NameObject("/BS")] = border
        annot[NameObject("/AP")] = _appearance(writer, rect, f"{_rgb(color)} {_fmt(width)} w 1 j {path} S")
        added = [writer._add_object(annot)]
    else:  # line, arrow
        try:
            (ax, ay), (bx, by) = [(_num(p[0]), _num(p[1])) for p in change.get("points")[:2]]
        except (TypeError, ValueError, IndexError):
            raise core.PdfManagerError("a line needs two points") from None
        length = math.hypot(bx - ax, by - ay)
        if length < 1:
            raise core.PdfManagerError("draw the line longer")
        head = max(9.0, width * 4) if kind == "arrow" else 0
        pad = head + width
        rect = [min(ax, bx) - pad, min(ay, by) - pad, max(ax, bx) + pad, max(ay, by) + pad]
        annot = _base(writer, page_ref, "/Line", rect, color, author, text)
        annot[NameObject("/L")] = ArrayObject(FloatObject(v) for v in [ax, ay, bx, by])
        annot[NameObject("/BS")] = DictionaryObject({NameObject("/W"): FloatObject(width)})
        content = f"{_rgb(color)} {_fmt(width)} w 1 J "
        if kind == "arrow":
            annot[NameObject("/LE")] = ArrayObject([NameObject("/None"), NameObject("/ClosedArrow")])
            annot[NameObject("/IC")] = ArrayObject(FloatObject(c) for c in color)
            ux, uy = (bx - ax) / length, (by - ay) / length
            baseX, baseY = bx - ux * head, by - uy * head  # the shaft ends where the head begins
            wing = head * 0.45
            content += (f"{_fmt(ax)} {_fmt(ay)} m {_fmt(baseX)} {_fmt(baseY)} l S {_rgb(color, False)} "
                        f"{_fmt(bx)} {_fmt(by)} m {_fmt(baseX - uy * wing)} {_fmt(baseY + ux * wing)} l "
                        f"{_fmt(baseX + uy * wing)} {_fmt(baseY - ux * wing)} l h f")
        else:
            content += f"{_fmt(ax)} {_fmt(ay)} m {_fmt(bx)} {_fmt(by)} l S"
        annot[NameObject("/AP")] = _appearance(writer, rect, content)
        added = [writer._add_object(annot)]
    annots = page.get("/Annots")
    if annots is None:
        page[NameObject("/Annots")] = ArrayObject()
        annots = page["/Annots"]
    annots = annots.get_object()
    annots.extend(added)
    return added[0]


# ---- rubber stamps ----------------------------------------------------------------

# Acrobat's names for its standard stamps, so other readers recognise them.
STAMP_NAMES = {"APPROVED": "/Approved", "NOT APPROVED": "/NotApproved", "DRAFT": "/Draft",
               "FINAL": "/Final", "CONFIDENTIAL": "/Confidential", "FOR COMMENT": "/ForComment",
               "EXPERIMENTAL": "/Experimental", "EXPIRED": "/Expired", "AS IS": "/AsIs",
               "SOLD": "/Sold", "TOP SECRET": "/TopSecret", "DEPARTMENTAL": "/Departmental",
               "NOT FOR PUBLIC RELEASE": "/NotForPublicRelease",
               "FOR PUBLIC RELEASE": "/ForPublicRelease"}
STAMP_COLORS = {"APPROVED": "#1f8a3b", "FINAL": "#1f8a3b", "RECEIVED": "#1f4fd8",
                "REVIEWED": "#1f4fd8", "FOR COMMENT": "#1f4fd8", "DRAFT": "#d52b1e",
                "NOT APPROVED": "#d52b1e", "CONFIDENTIAL": "#d52b1e", "VOID": "#d52b1e"}


def _stamp(writer, page, page_ref, change, author):
    return writer._add_object(stamp_annotation(writer, page, page_ref, change, author))


def stamp_annotation(writer, page, page_ref, change, author):
    """A rubber stamp: the label in bold capitals in a rounded frame, with
    "by NAME, DATE" under it when ``dated``. Drawn upright as the page is
    shown, whatever the page's /Rotate."""
    label = " ".join(str(change.get("label") or "").split()).upper()[:60]
    if not label:
        raise core.PdfManagerError("a stamp needs words, e.g. APPROVED")
    x0, y0, x1, y1 = _rect(change.get("rect") or ())
    if x1 - x0 < 8 or y1 - y0 < 8:
        raise core.PdfManagerError("draw the stamp bigger")
    color = _color(change.get("color"), STAMP_COLORS.get(label, "#d52b1e"))
    turn = int(page.get("/Rotate", 0) or 0) % 360
    w, h = (y1 - y0, x1 - x0) if turn in (90, 270) else (x1 - x0, y1 - y0)
    # Local drawing space: (0, 0)-(w, h) upright on screen -> the page.
    matrix = {0: (1, 0, 0, 1, x0, y0), 90: (0, 1, -1, 0, x1, y0), 180: (-1, 0, 0, -1, x1, y1),
              270: (0, -1, 1, 0, x0, y1)}[turn]
    sub = ""
    if change.get("dated"):
        when = time.strftime("%d %b %Y, %H:%M")
        sub = f"by {author}, {when}" if author else when
    line = max(1.0, min(w, h) * 0.05)
    inset = line / 2 + 0.5
    r = min(w, h) * 0.18
    f = _fmt
    frame = (f"{f(inset + r)} {f(inset)} m {f(w - inset - r)} {f(inset)} l "
             f"{f(w - inset)} {f(inset)} {f(w - inset)} {f(inset + r)} v "
             f"{f(w - inset)} {f(h - inset - r)} l {f(w - inset)} {f(h - inset)} {f(w - inset - r)} {f(h - inset)} y "
             f"{f(inset + r)} {f(h - inset)} l {f(inset)} {f(h - inset)} {f(inset)} {f(h - inset - r)} v "
             f"{f(inset)} {f(inset + r)} l {f(inset)} {f(inset)} {f(inset + r)} {f(inset)} y h")
    room = w - 2 * (inset + line * 2)
    main_size = min((h * (0.5 if sub else 0.62)), room / max(1.0, _text_width(label, 1, "Helvetica-Bold")))
    sub_size = min(h * 0.17, room / max(1.0, _text_width(sub, 1))) if sub else 0
    block = main_size * 0.72 + (sub_size * 1.25 if sub else 0)
    base = (h - block) / 2 + (sub_size * 1.25 if sub else 0)
    parts = [f"q {' '.join(_fmt(v) for v in matrix)} cm {_rgb(color)} {_rgb(color, False)} {f(line)} w",
             frame + " S",
             f"BT /HelvB {f(main_size)} Tf 1 0 0 1 "
             f"{f((w - _text_width(label, main_size, 'Helvetica-Bold')) / 2)} {f(base)} Tm "
             f"{_pdf_text(label)} Tj ET"]
    if sub:
        parts.append(f"BT /Helv {f(sub_size)} Tf 1 0 0 1 {f((w - _text_width(sub, sub_size)) / 2)} "
                     f"{f(base - sub_size * 1.25)} Tm {_pdf_text(sub)} Tj ET")
    parts.append("Q")
    fonts = DictionaryObject({NameObject("/Font"): DictionaryObject({
        NameObject(name): DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject(base_font),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})
        for name, base_font in (("/Helv", "/Helvetica"), ("/HelvB", "/Helvetica-Bold"))})})
    rect = [x0, y0, x1, y1]
    annot = _base(writer, page_ref, "/Stamp", rect, color, author, str(change.get("text") or ""))
    annot[NameObject("/Name")] = NameObject(STAMP_NAMES.get(label, "/" + re.sub(r"\W+", "", label.title()) or "/Draft"))
    annot[NameObject("/Subj")] = TextStringObject(label.title())
    annot[NameObject("/AP")] = _appearance(writer, rect, " ".join(parts), fonts)
    return annot


# ---- measurements (the reader's Scale bar), as measurement annotations ----------

LABEL_SIZE = 9  # points
MEASURE_SHAPES = {"distance": "/Line", "path": "/PolyLine", "area": "/Polygon", "angle": "/PolyLine", "count": "/Circle"}


def _point(value):
    try:
        x, y = value
    except (TypeError, ValueError):
        raise core.PdfManagerError("a measurement point is not a pair of numbers") from None
    return [_num(x), _num(y)]


def _pdf_text(text):
    """``text`` as a PDF string for Helvetica (WinAnsi): (…) with escapes."""
    raw = str(text).encode("cp1252", "replace")
    return "(" + "".join(chr(b) if b not in (0x28, 0x29, 0x5C) else "\\" + chr(b) for b in raw) + ")"


def _text_width(text, size, font="Helvetica"):
    from reportlab.pdfbase.pdfmetrics import stringWidth

    return stringWidth(str(text).encode("cp1252", "replace").decode("cp1252"), font, size)


def _label(x, y, lines, color, size=LABEL_SIZE):
    """A label centred on x, y (on a white backing, so the drawing's lines
    don't run through it): (content, its box)."""
    lines = [line for line in lines if line]
    width = max(_text_width(line, size) for line in lines) + 8
    height = len(lines) * size * 1.2 + 4
    x0, y0 = x - width / 2, y - height / 2
    f = _fmt
    out = [f"q 1 1 1 rg {_rgb(color)} 0.6 w {f(x0)} {f(y0)} {f(width)} {f(height)} re B Q",
           f"q 0.1 0.1 0.1 rg BT /Helv {f(size)} Tf"]
    for i, line in enumerate(lines):
        tx = x - _text_width(line, size) / 2
        ty = y0 + height - 2 - size * 1.2 * (i + 1) + size * 0.25
        out.append(f"1 0 0 1 {f(tx)} {f(ty)} Tm {_pdf_text(line)} Tj")
    out.append("ET Q")
    return " ".join(out), [x0, y0, x0 + width, y0 + height]


def _measure_dict(scale):
    """A /Measure dictionary (rectilinear) for a drawing ``scale`` {ratio,
    units ("ft" or "m"), label}: how much one unit of the page is really."""
    try:
        ratio = float(scale["ratio"])
    except (TypeError, KeyError, ValueError):
        return None
    if not ratio > 0 or not math.isfinite(ratio):
        return None
    metric = scale.get("units") == "m"
    per_point = ratio / 72 * 0.0254 if metric else ratio / 72 / 12  # metres or feet per point

    def fmt(unit, factor, places):
        return DictionaryObject({NameObject("/Type"): NameObject("/NumberFormat"), NameObject("/U"): TextStringObject(unit),
                                 NameObject("/C"): FloatObject(factor), NameObject("/D"): NumberObject(places)})

    length = [fmt("m", per_point, 1000)] if metric else [fmt("ft", per_point, 1), fmt("in", 12, 16)]
    area = [fmt("m²" if metric else "sq ft", per_point * per_point, 100)]
    return DictionaryObject({
        NameObject("/Type"): NameObject("/Measure"), NameObject("/Subtype"): NameObject("/RL"),
        NameObject("/R"): TextStringObject(str(scale.get("label") or "")[:80]),
        NameObject("/X"): ArrayObject(length), NameObject("/D"): ArrayObject(length), NameObject("/A"): ArrayObject(area),
        NameObject("/O"): ArrayObject([FloatObject(0), FloatObject(0)])})


def _measure(writer, page_ref, change, author):
    """One measurement from the reader as annotation(s); returns their refs."""
    shape = change.get("shape")
    if shape not in MEASURE_SHAPES:
        raise core.PdfManagerError(f"unknown kind of measurement: {shape!r}")
    pts = [_point(p) for p in change.get("points") or []]
    holes = [[_point(p) for p in hole] for hole in change.get("holes") or [] if len(hole) >= 3]
    least = {"area": 3, "angle": 3, "count": 1}.get(shape, 2)
    if len(pts) < least:
        raise core.PdfManagerError("a measurement has too few points")
    color = _color(change.get("color"), "#1f4fd8")
    name, group = str(change.get("label") or "")[:80], str(change.get("group") or "")[:60]
    value, extra = str(change.get("value") or "")[:80], str(change.get("sub") or "")[:120]
    contents = " - ".join(t for t in (group, name, value) if t) + (f" ({extra})" if extra else "")
    fonts = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})})})
    measure = _measure_dict(change.get("scale")) if isinstance(change.get("scale"), dict) else None
    f, width = _fmt, 1.5
    lines = [name, value] if name else [value]

    def build(subtype, content, boxes, extra_keys, text=contents):
        xs = [b[0] for b in boxes] + [b[2] for b in boxes]
        ys = [b[1] for b in boxes] + [b[3] for b in boxes]
        rect = [min(xs) - 2, min(ys) - 2, max(xs) + 2, max(ys) + 2]
        annot = _base(writer, page_ref, subtype, rect, color, author, text)
        annot.update(extra_keys)
        annot[NameObject("/BS")] = DictionaryObject({NameObject("/W"): FloatObject(width)})
        if measure is not None and subtype != "/Circle":
            annot[NameObject("/Measure")] = measure
        if group:
            annot[NameObject("/Subj")] = TextStringObject(group)
        annot[NameObject("/AP")] = _appearance(writer, rect, content, fonts)
        return writer._add_object(annot)

    def bounds(points):
        return [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]

    def path(points, close=False):
        return " ".join(f"{f(x)} {f(y)} {'m' if i == 0 else 'l'}" for i, (x, y) in enumerate(points)) + (" h" if close else "")

    vertices = lambda points: ArrayObject(FloatObject(v) for p in points for v in p)  # noqa: E731
    if shape == "count":
        refs = []
        for i, (x, y) in enumerate(pts):
            r = 7
            num = str(i + 1)
            content = (f"q {_rgb(color, False)} {_ellipse_path(x - r, y - r, x + r, y + r)} f Q "
                       f"q 1 1 1 rg BT /Helv 8 Tf 1 0 0 1 {f(x - _text_width(num, 8) / 2)} {f(y - 2.8)} Tm {_pdf_text(num)} Tj ET Q")
            boxes = [[x - r, y - r, x + r, y + r]]
            if i == 0:  # the count by the first mark
                text, box = _label(x, y + r + 12, lines, color)
                content += " " + text
                boxes.append(box)
            refs.append(build("/Circle", content, boxes, {NameObject("/IC"): ArrayObject(FloatObject(c) for c in color)},
                              text=f"{contents} ({i + 1} of {len(pts)})"))
        return refs
    if shape == "angle":
        a, vertex, b = pts[:3]
        a1, a2 = math.atan2(a[1] - vertex[1], a[0] - vertex[0]), math.atan2(b[1] - vertex[1], b[0] - vertex[0])
        span = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
        r, steps = 18, 12
        arc = [(vertex[0] + r * math.cos(a1 + span * k / steps), vertex[1] + r * math.sin(a1 + span * k / steps)) for k in range(steps + 1)]
        mid = a1 + span / 2
        text, box = _label(vertex[0] + 34 * math.cos(mid), vertex[1] + 34 * math.sin(mid), lines, color)
        content = f"{_rgb(color)} {f(width)} w 1 J 1 j {path([a, vertex, b])} S 0.8 w {path(arc)} S {text}"
        return [build("/PolyLine", content, [bounds(pts), box], {NameObject("/Vertices"): vertices([a, vertex, b])})]
    if shape == "area":
        cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        text, box = _label(cx, cy, lines + ([extra] if extra else []), color)
        rings = " ".join(path(ring, close=True) for ring in [pts] + holes)
        dashed = " ".join(path(h, close=True) for h in holes)
        content = (f"q /G0 gs {_rgb(color, False)} {rings} f* Q {_rgb(color)} {f(width)} w 1 j {path(pts, close=True)} S "
                   + (f"q [3 2] 0 d 0.8 w {dashed} S Q " if holes else "") + text)
        fonts[NameObject("/ExtGState")] = DictionaryObject({NameObject("/G0"): DictionaryObject({
            NameObject("/Type"): NameObject("/ExtGState"), NameObject("/ca"): FloatObject(0.15)})})
        return [build("/Polygon", content, [bounds(pts), box], {
            NameObject("/Vertices"): vertices(pts), NameObject("/IT"): NameObject("/PolygonDimension")})]
    # distance, path: the line(s), ticks at the ends, the length by the middle (or the end of a path)
    ends = [(pts[0], pts[1]), (pts[-1], pts[-2])]
    ticks = []
    for (x, y), (ox, oy) in ends:
        length = math.hypot(x - ox, y - oy) or 1
        nx, ny = -(y - oy) / length * 4, (x - ox) / length * 4
        ticks.append(f"{f(x - nx)} {f(y - ny)} m {f(x + nx)} {f(y + ny)} l")
    if shape == "distance":
        (ax, ay), (bx, by) = pts[0], pts[1]
        length = math.hypot(bx - ax, by - ay) or 1
        nx, ny = -(by - ay) / length, (bx - ax) / length
        if ny < 0:
            nx, ny = -nx, -ny
        text, box = _label((ax + bx) / 2 + nx * 10, (ay + by) / 2 + ny * 10, lines, color)
        keys = {NameObject("/L"): vertices(pts[:2]), NameObject("/IT"): NameObject("/LineDimension")}
        subtype = "/Line"
    else:
        (ex, ey), (px, py) = pts[-1], pts[-2]
        length = math.hypot(ex - px, ey - py) or 1
        text, box = _label(ex + (ex - px) / length * 26, ey + (ey - py) / length * 14, lines, color)
        keys = {NameObject("/Vertices"): vertices(pts), NameObject("/IT"): NameObject("/PolyLineDimension")}
        subtype = "/PolyLine"
    content = f"{_rgb(color)} {f(width)} w 1 J 1 j {path(pts)} S {' '.join(ticks)} S {text}"
    return [build(subtype, content, [bounds(pts), box], keys)]


def _set_target(writer, annot, change):
    """Where a link goes: a web address (or email) or a page of this document."""
    url, to_page = str(change.get("url") or "").strip(), change.get("to_page")
    for key in ("/A", "/Dest"):
        if key in annot:
            del annot[key]
    if url:
        if re.match(r"(?i)\s*(javascript|vbscript|file|data):", url):
            raise core.PdfManagerError("a link can go to a web address, an email address or a page")
        if re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", url):
            url = "mailto:" + url
        elif not re.match(r"(?i)(https?|mailto|ftp|tel):", url):
            url = "https://" + url  # "example.com" means the web site
        annot[NameObject("/A")] = DictionaryObject({NameObject("/S"): NameObject("/URI"),
                                                    NameObject("/URI"): TextStringObject(url[:2000])})
        return
    try:
        number = int(to_page)
    except (TypeError, ValueError):
        raise core.PdfManagerError("say where the link goes: a web address or a page number") from None
    if not 1 <= number <= len(writer.pages):
        raise core.PdfManagerError(f"there is no page {number} to link to")
    annot[NameObject("/Dest")] = ArrayObject([writer.pages[number - 1].indirect_reference, NameObject("/Fit")])


def _find(reader, writer, number, ident):
    """The annotation pdf.js calls ``ident`` on page ``number``: its index
    in the page's /Annots, the same in the reader's and the copy's pages."""
    match = re.fullmatch(r"(\d+)R(\d*)", str(ident or ""))
    if not match:
        raise core.PdfManagerError("that annotation can't be found; reopen the document")
    num, gen = int(match.group(1)), int(match.group(2) or 0)
    refs = reader.pages[number - 1].get("/Annots")
    refs = refs.get_object() if refs is not None else []
    for index, ref in enumerate(refs):
        if getattr(ref, "idnum", None) == num and getattr(ref, "generation", 0) == gen:
            copy_refs = writer.pages[number - 1]["/Annots"].get_object()
            return index, copy_refs[index]
    raise core.PdfManagerError("that annotation can't be found; reopen the document")


def _key(ref):
    """The same for an annotation however it's reached (a reference to it or itself)."""
    return ("ref", ref.idnum) if hasattr(ref, "idnum") else ("obj", id(ref.get_object()))


def apply(path, output, changes, password=None, author=None):
    """Make ``changes`` (see above) to the PDF at ``path``, writing ``output``.
    Returns the pdf.js-style ids of what was added, in the copy written."""
    reader = core.open_pdf(path, password)
    writer = core.clone_document(reader)
    author = (author or "").strip() or default_author()
    added, doomed = [], {}
    for change in changes:
        try:
            number = int(change.get("page"))
        except (TypeError, ValueError):
            raise core.PdfManagerError("a change has no page") from None
        if not 1 <= number <= len(writer.pages):
            raise core.PdfManagerError(f"there is no page {number}")
        page = writer.pages[number - 1]
        op = change.get("op")
        if op == "add":
            added.append(_add(writer, page, page.indirect_reference, change, author))
            continue
        index, ref = _find(reader, writer, number, change.get("id"))
        annot = ref.get_object()
        if op == "text":
            text = str(change.get("text") or "")
            if text:
                annot[NameObject("/Contents")] = TextStringObject(text)
            elif "/Contents" in annot:
                del annot["/Contents"]
            annot[NameObject("/M")] = TextStringObject(_pdf_date())
        elif op == "reply":
            reply = _base(writer, page.indirect_reference, "/Text",
                          [float(v) for v in annot.get("/Rect", [0, 0, 0, 0])],
                          [float(c) for c in annot.get("/C", [1, 0.8, 0])][:3] or [1, 0.8, 0],
                          author, str(change.get("text") or ""))
            rect = reply["/Rect"]
            # Shown in comment lists, not as a second icon over the first.
            reply.update({NameObject("/IRT"): ref, NameObject("/RT"): NameObject("/R"),
                          NameObject("/Name"): NameObject("/Comment"),
                          NameObject("/AP"): _appearance(writer, [float(v) for v in rect], "")})
            new = writer._add_object(reply)
            page["/Annots"].get_object().append(new)
            added.append(new)
        elif op == "link":
            if annot.get("/Subtype") != "/Link":
                raise core.PdfManagerError("that isn't a link")
            _set_target(writer, annot, change)
        elif op == "delete":
            doomed.setdefault(number, set()).add(_key(ref))
        else:
            raise core.PdfManagerError(f"unknown change {op!r}")
    for number, ids in doomed.items():
        page = writer.pages[number - 1]
        refs = list(page["/Annots"].get_object())
        gone, grew = set(ids), True
        while grew:  # popups and replies (and their replies) go too
            grew = False
            for ref in refs:
                obj = ref.get_object()
                key = "/Parent" if obj.get("/Subtype") == "/Popup" else "/IRT"
                link = obj.raw_get(key) if key in obj else None
                if _key(ref) not in gone and link is not None and _key(link) in gone:
                    gone.add(_key(ref))
                    grew = True
        page[NameObject("/Annots")] = ArrayObject(ref for ref in refs if _key(ref) not in gone)
    core._write(writer, output)
    return [f"{ref.idnum}R" + (str(ref.generation) if ref.generation else "") for ref in added]
