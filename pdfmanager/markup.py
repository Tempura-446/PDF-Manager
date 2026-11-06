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

KINDS = ("note", "underline", "strikeout", "rect", "ellipse", "cloud", "line", "arrow")
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


def _appearance(writer, bbox, content):
    """An appearance stream (/AP /N) drawing ``content``, in page coordinates."""
    stream = DecodedStreamObject()
    stream.set_data(content.encode("latin-1"))
    stream.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                   NameObject("/BBox"): ArrayObject(FloatObject(v) for v in bbox),
                   NameObject("/Resources"): DictionaryObject()})
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
