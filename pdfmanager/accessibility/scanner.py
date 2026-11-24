# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""PDF font width measurement, content-stream scanner, and marked-content rewriter."""

import re
from collections import Counter

from pdfmanager.accessibility.geometry import (
    ARTIFACT, UNMARKED, _PATH_BUILD, _PATH_PAINT, _SHOW, _STD_FONTS,
    _apply, _bbox, _box_through, _inherited, _mul, _num, _union,
)



# --------------------------------------------------------------------------
# Following a content stream: where each drawing operator draws
# --------------------------------------------------------------------------

class _Font:
    """Glyph widths of a font, enough to know where a run of text ends."""

    def __init__(self, font):
        import pikepdf

        self.two_byte = False
        self.widths = {}
        self.default = 0.5
        self.scale = 0.001
        self.std = None
        try:
            subtype = str(font.get("/Subtype", ""))
            if subtype == "/Type0":
                self.two_byte = True
                desc = font.DescendantFonts[0]
                self.default = _num(desc.get("/DW", 1000), 1000) / 1000
                self._cid_widths(desc.get("/W"))
                self.widths = {k: v / 1000 for k, v in self.widths.items()}
                return
            first = int(_num(font.get("/FirstChar", 0)))
            widths = font.get("/Widths")
            if subtype == "/Type3":
                matrix = font.get("/FontMatrix")
                self.scale = _num(matrix[0], 0.001) if matrix is not None else 0.001
            if isinstance(widths, pikepdf.Array):
                self.widths = {first + i: _num(w) * self.scale for i, w in enumerate(widths)}
                descriptor = font.get("/FontDescriptor")
                if descriptor is not None and "/MissingWidth" in descriptor:
                    self.default = _num(descriptor.MissingWidth) * self.scale
            else:
                self.std = _standard_font(str(font.get("/BaseFont", "")))
        except (AttributeError, IndexError, TypeError, ValueError, pikepdf.PdfError):
            pass

    def _cid_widths(self, array):
        if array is None:
            return
        items = list(array)
        i = 0
        while i + 1 < len(items) and len(self.widths) < 70000:
            start = int(_num(items[i]))
            nxt = items[i + 1]
            if hasattr(nxt, "__len__") and not isinstance(nxt, (int, float)):
                for j, w in enumerate(nxt):
                    self.widths[start + j] = _num(w)
                i += 2
            elif i + 2 < len(items):
                end, w = int(_num(nxt)), _num(items[i + 2])
                for code in range(start, min(end, start + 70000) + 1):
                    self.widths[code] = w
                i += 3
            else:
                break

    def codes(self, data):
        if self.two_byte:
            return [data[i] << 8 | data[i + 1] for i in range(0, len(data) - 1, 2)]
        return list(data)

    def width(self, code):
        w = self.widths.get(code)
        if w is not None:
            return w
        if self.std:
            from reportlab.pdfbase import pdfmetrics

            ch = bytes([code]).decode("cp1252", "replace") if code < 256 else "?"
            try:
                w = pdfmetrics.stringWidth(ch, self.std, 1)
            except Exception:  # a character reportlab doesn't know
                w = 0.5
            self.widths[code] = w
            return w
        return self.default


def _standard_font(name):
    base = name.lstrip("/").split("+")[-1]
    key = re.sub(r"[^a-z]", "", base.lower().split(",")[0].split("-")[0])
    family = _STD_FONTS.get(key)
    if family is None:
        return None
    if family in ("Symbol", "ZapfDingbats"):
        return family
    lower = base.lower()
    bold = "bold" in lower or "black" in lower
    italic = "italic" in lower or "oblique" in lower
    if family == "Times-Roman":
        return {(0, 0): "Times-Roman", (1, 0): "Times-Bold", (0, 1): "Times-Italic",
                (1, 1): "Times-BoldItalic"}[(bold, italic)]
    suffix = {(0, 0): "", (1, 0): "-Bold", (0, 1): "-Oblique", (1, 1): "-BoldOblique"}[(bold, italic)]
    return family + suffix


class _Unit:
    """One thing a content stream draws: a run of text, a picture, a path,
    a form XObject or a shading; instructions ``start``..``end`` of
    ``stream`` (None: the page's own content; else a form XObject drawn on
    the page, tagged inside: see _single_use_forms)."""

    __slots__ = ("kind", "start", "end", "box", "blank", "in_text", "key", "stream", "xobj")

    def __init__(self, kind, start, end, box, blank=False, in_text=False, stream=None):
        self.kind, self.start, self.end, self.box = kind, start, end, box
        self.blank, self.in_text, self.key = blank, in_text, None
        self.stream, self.xobj = stream, None


class _Scanner:
    """Walks a content stream keeping the graphics and text state, and lists
    what it draws (``_Unit``s, boxes in the stream's own coordinates)."""

    def __init__(self, resources, fonts, forms, inside=()):
        self.resources = resources
        self.fonts = fonts      # cache: font object -> _Font
        self.forms = forms      # cache: form object -> has pictures
        self.inside = inside    # forms to tag inside rather than as a whole

    def _font(self, name):
        try:
            font = self.resources.Font[name]
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
        key = font.objgen if font.is_indirect else id(font)
        if key not in self.fonts:
            self.fonts[key] = _Font(font)
        return self.fonts[key]

    def _xobject(self, name):
        try:
            return self.resources.XObject[name]
        except (AttributeError, KeyError, TypeError, ValueError):
            return None

    def run(self, instructions, ctm=(1, 0, 0, 1, 0, 0), ts=None, stream=None):
        """The units ``instructions`` draw; a form's content is followed with
        the transformation and text state it is drawn with."""
        import pikepdf

        units = []
        ts = ts or {"font": None, "size": 0.0, "tc": 0.0, "tw": 0.0, "th": 1.0, "tl": 0.0,
                    "rise": 0.0}
        stack = []
        tm = tlm = (1, 0, 0, 1, 0, 0)
        in_text = False
        path_start, points = None, []
        for i, ins in enumerate(instructions):
            if isinstance(ins, pikepdf.ContentStreamInlineImage):
                units.append(_Unit("image", i, i, _box_through(ctm, (0, 0, 1, 1)), stream=stream))
                continue
            op = str(ins.operator)
            args = ins.operands
            try:
                if op in _PATH_BUILD:
                    if path_start is None:
                        path_start, points = i, []
                    nums = [_num(a) for a in args]
                    if op == "re" and len(nums) == 4:
                        x, y, w, h = nums
                        points += [_apply(ctm, x, y), _apply(ctm, x + w, y + h),
                                   _apply(ctm, x + w, y), _apply(ctm, x, y + h)]
                    else:
                        points += [_apply(ctm, nums[k], nums[k + 1]) for k in range(0, len(nums) - 1, 2)]
                    continue
                if op in ("W", "W*"):
                    continue
                if op in _PATH_PAINT or op == "n":
                    if op != "n":
                        start = path_start if path_start is not None else i
                        box = _bbox(points) if points else _box_through(ctm, (0, 0, 0, 0))
                        units.append(_Unit("path", start, i, box, stream=stream))
                    path_start, points = None, []
                    continue
                if path_start is not None:
                    path_start, points = None, []  # a broken path: forget it
                if op == "q":
                    stack.append((ctm, dict(ts)))
                elif op == "Q":
                    if stack:
                        ctm, ts = stack.pop()
                elif op == "cm" and len(args) == 6:
                    ctm = _mul(tuple(_num(a) for a in args), ctm)
                elif op == "BT":
                    in_text, tm, tlm = True, (1, 0, 0, 1, 0, 0), (1, 0, 0, 1, 0, 0)
                elif op == "ET":
                    in_text = False
                elif op == "Tf" and len(args) == 2:
                    ts["font"], ts["size"] = self._font(args[0]), _num(args[1])
                elif op == "Tc" and args:
                    ts["tc"] = _num(args[0])
                elif op == "Tw" and args:
                    ts["tw"] = _num(args[0])
                elif op == "Tz" and args:
                    ts["th"] = _num(args[0], 100) / 100
                elif op == "TL" and args:
                    ts["tl"] = _num(args[0])
                elif op == "Ts" and args:
                    ts["rise"] = _num(args[0])
                elif op in ("Td", "TD") and len(args) == 2:
                    tx, ty = _num(args[0]), _num(args[1])
                    if op == "TD":
                        ts["tl"] = -ty
                    tlm = _mul((1, 0, 0, 1, tx, ty), tlm)
                    tm = tlm
                elif op == "Tm" and len(args) == 6:
                    tlm = tm = tuple(_num(a) for a in args)
                elif op == "T*":
                    tlm = _mul((1, 0, 0, 1, 0, -ts["tl"]), tlm)
                    tm = tlm
                elif op in _SHOW:
                    if op in ("'", '"'):
                        if op == '"' and len(args) == 3:
                            ts["tw"], ts["tc"] = _num(args[0]), _num(args[1])
                        tlm = _mul((1, 0, 0, 1, 0, -ts["tl"]), tlm)
                        tm = tlm
                    pieces = args[0] if op == "TJ" and args else args[-1:] if args else []
                    box, tm, blank = self._show(pieces, ts, tm, ctm)
                    units.append(_Unit("text", i, i, box, blank, in_text, stream))
                elif op == "Do" and args:
                    xobj = self._xobject(args[0])
                    subtype = str(xobj.get("/Subtype", "")) if xobj is not None else ""
                    if subtype == "/Image":
                        units.append(_Unit("image", i, i, _box_through(ctm, (0, 0, 1, 1)), stream=stream))
                    elif subtype == "/Form":
                        matrix = xobj.get("/Matrix")
                        m = tuple(_num(v) for v in matrix) if matrix is not None and len(matrix) == 6 \
                            else (1, 0, 0, 1, 0, 0)
                        bbox = [_num(v) for v in xobj.get("/BBox", [0, 0, 0, 0])][:4]
                        if len(bbox) < 4:
                            bbox = [0, 0, 0, 0]
                        box = _box_through(_mul(m, ctm), bbox)
                        inner = None
                        if stream is None and xobj.is_indirect and xobj.objgen in self.inside:
                            try:
                                inner = pikepdf.parse_content_stream(xobj)
                            except (pikepdf.PdfError, ValueError, TypeError):
                                inner = None
                        if inner is not None:
                            unit = _Unit("tagged-form", i, i, box)
                            unit.xobj = xobj
                            units.append(unit)
                            sub = _Scanner(xobj.get("/Resources") or self.resources, self.fonts,
                                           self.forms)
                            units += sub.run(inner, _mul(m, ctm), dict(ts), xobj)
                            continue
                        kind = "form-image" if _form_has_images(xobj, self.forms) else "form"
                        units.append(_Unit(kind, i, i, box, stream=stream))
                    else:
                        units.append(_Unit("form", i, i, _box_through(ctm, (0, 0, 1, 1)), stream=stream))
                elif op == "sh":
                    units.append(_Unit("shading", i, i, None, stream=stream))
            except (TypeError, ValueError, IndexError, ZeroDivisionError, AttributeError):
                continue
        return units

    def _show(self, pieces, ts, tm, ctm):
        """Box of a run of text and the text matrix after it."""
        import pikepdf

        font = ts["font"] or _Font({})
        fs, tc, tw, th = ts["size"], ts["tc"], ts["tw"], ts["th"]
        x, blank = 0.0, True
        for piece in pieces:
            if isinstance(piece, pikepdf.String):
                data = bytes(piece)
                for code in font.codes(data):
                    x += (font.width(code) * fs + tc + (tw if not font.two_byte and code == 32 else 0)) * th
                if font.two_byte:
                    blank = blank and not data
                else:
                    blank = blank and all(b in (32, 9, 10, 13, 160) for b in data)
            else:
                x -= _num(piece) / 1000 * fs * th
        rise = ts["rise"]
        m = _mul(tm, ctm)
        box = _box_through(m, (min(0, x), rise - 0.25 * fs, max(0, x), rise + 0.8 * fs))
        return box, _mul((1, 0, 0, 1, x, 0), tm), blank


DO_NAMES = re.compile(rb"/([^\s/\[\]()<>{}%]+)\s+Do\b")


def _single_use_forms(pdf):
    """Form XObjects drawn exactly once, straight from a page: these are
    tagged inside (with their own /StructParents), so that a page drawn as
    one form, as some programs and page imports do, still gets headings,
    paragraphs and tables. Forms drawn more often are tagged as a whole
    where they're drawn: the same MCIDs can't belong to two places."""
    import pikepdf

    drawn, owners, from_page = Counter(), Counter(), set()
    seen = set()

    def scan(data, resources, page_level, depth):
        xobjects = resources.get("/XObject") if isinstance(resources, pikepdf.Dictionary) else None
        if not isinstance(xobjects, pikepdf.Dictionary):
            return
        for _, xobj in xobjects.items():
            if isinstance(xobj, pikepdf.Stream) and xobj.is_indirect:
                owners[xobj.objgen] += 1
        for name in DO_NAMES.findall(data):
            xobj = xobjects.get("/" + name.decode("latin-1"))
            if not isinstance(xobj, pikepdf.Stream) or str(xobj.get("/Subtype", "")) != "/Form" \
                    or not xobj.is_indirect:
                continue
            drawn[xobj.objgen] += 1
            if page_level:
                from_page.add(xobj.objgen)
            if xobj.objgen not in seen and depth < 8:
                seen.add(xobj.objgen)
                try:
                    scan(xobj.read_bytes(), xobj.get("/Resources"), False, depth + 1)
                except pikepdf.PdfError:
                    continue

    for page in pdf.pages:
        contents = page.obj.get("/Contents")
        streams = list(contents) if isinstance(contents, pikepdf.Array) else \
            [contents] if contents is not None else []
        try:
            data = b"\n".join(c.read_bytes() for c in streams if isinstance(c, pikepdf.Stream))
        except pikepdf.PdfError:
            continue
        scan(data, _inherited(page.obj, "/Resources"), True, 0)
    return {key for key in from_page if drawn[key] == 1 and owners[key] == 1}


def _form_has_images(xobj, cache, depth=0):
    """Whether a form XObject draws pictures (directly or in forms inside)."""
    import pikepdf

    key = xobj.objgen if xobj.is_indirect else id(xobj)
    if key in cache:
        return cache[key]
    cache[key] = False  # while looking: a form that draws itself
    found = False
    try:
        data = xobj.read_bytes()
        if b"BI" in data and re.search(rb"(^|\s)BI(\s|$)", data):
            found = True
        elif b"Do" in data and depth < 4:
            xobjects = (xobj.get("/Resources") or {}).get("/XObject") or {}
            for name in re.findall(rb"/([^\s/\[\]()<>{}%]+)\s+Do\b", data):
                child = xobjects.get("/" + name.decode("latin-1"))
                if child is None:
                    continue
                subtype = str(child.get("/Subtype", ""))
                if subtype == "/Image" or (subtype == "/Form" and
                                           _form_has_images(child, cache, depth + 1)):
                    found = True
                    break
    except (pikepdf.PdfError, AttributeError, TypeError, ValueError):
        found = False
    cache[key] = found
    return found



# --------------------------------------------------------------------------
# Writing marked content
# --------------------------------------------------------------------------

def _rewrite(instructions, units, open_mc, on_form=None):
    """The content stream with each unit wrapped in marked content for its
    key. Marked content that only tagged (anything but optional content,
    /OC) is dropped: the new tags replace it. ``open_mc(key)`` gives the
    BDC/BMC instruction for a key (and allocates its MCID). Marked content
    nests inside text objects and saved graphics states (BT/ET, q/Q). A
    form tagged inside (key UNMARKED) is drawn outside any marked content,
    after ``on_form(unit)`` has tagged its own content."""
    import pikepdf

    starts = {u.start: u for u in units}
    out, current, kept = [], None, []
    depth = opened_at = 0
    emc = pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC"))

    def close():
        nonlocal current
        if current is not None:
            out.append(emc)
            current = None

    i, n = 0, len(instructions)
    while i < n:
        unit = starts.get(i)
        if unit is not None and unit.key is UNMARKED:
            close()
            if on_form is not None:
                on_form(unit)
            out.extend(instructions[unit.start:unit.end + 1])
            i = unit.end + 1
            continue
        if unit is not None:
            key = unit.key if unit.key is not None else ARTIFACT
            if key is not current:
                close()
                out.append(open_mc(key))
                current, opened_at = key, depth
            out.extend(instructions[unit.start:unit.end + 1])
            i = unit.end + 1
            continue
        ins = instructions[i]
        i += 1
        if isinstance(ins, pikepdf.ContentStreamInlineImage):
            out.append(ins)
            continue
        op = str(ins.operator)
        if op in ("BT", "ET", "BX", "EX"):
            close()
            out.append(ins)
        elif op in ("BMC", "BDC"):
            close()
            keep = bool(ins.operands) and str(ins.operands[0]) == "/OC"
            kept.append(keep)
            if keep:
                out.append(ins)
        elif op == "EMC":
            close()
            if kept and kept.pop():
                out.append(ins)
        elif op in ("MP", "DP"):
            continue  # marked-content points tag nothing that's kept
        elif op == "q":
            depth += 1
            out.append(ins)
        elif op == "Q":
            if depth <= opened_at:
                close()
            depth = max(0, depth - 1)
            out.append(ins)
        else:
            out.append(ins)
    close()
    out.extend(emc for keep in kept if keep)
    return out


def _strip_marks(stream, done):
    """Remove tagging marked content from a form XObject (and the forms it
    draws): content drawn by a form is tagged where the page draws it, and
    tags left inside would point to nothing."""
    import pikepdf

    key = stream.objgen if stream.is_indirect else id(stream)
    if key in done:
        return
    done.add(key)
    try:
        data = stream.read_bytes()
    except pikepdf.PdfError:
        return
    if b"BMC" in data or b"BDC" in data:
        try:
            instructions = pikepdf.parse_content_stream(stream)
            new = _rewrite(instructions, [], None)
            stream.write(pikepdf.unparse_content_stream(new))
        except (pikepdf.PdfError, ValueError, TypeError):
            pass
    for name in ("/StructParents", "/StructParent"):
        if name in stream:
            del stream[name]
    xobjects = (stream.get("/Resources") or {}).get("/XObject") or {}
    _strip_xobjects(xobjects, done)


def _strip_xobjects(xobjects, done):
    import pikepdf

    try:
        items = list(xobjects.items())
    except (AttributeError, pikepdf.PdfError):
        return
    for _, xobj in items:
        if isinstance(xobj, pikepdf.Stream) and str(xobj.get("/Subtype", "")) == "/Form":
            _strip_marks(xobj, done)
