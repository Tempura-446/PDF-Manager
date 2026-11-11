"""Text that flows: when a rewritten paragraph takes more (or fewer) lines,
the paragraphs below it in its column move down (or up) to make room.

A PDF has no text boxes to flow in, only letters drawn at places. So the
column is worked out from the page as drawn (pdfium's letters): the lines
below the paragraph, between the gaps that separate it from the columns
beside it, down to a big gap or to something that isn't text (a picture,
a box, a table's lines, a heading across the columns). Those lines, and
the thin rules between them, move; everything else stays.

Moving is done in the page's own drawing, not by covering and drawing the
words again: each piece of text that moves gets a text rise (``Ts``, the
PDF's own way to raise or lower words, which every reader supports) for
just that piece, so the letters keep their font, spacing and everything
else exactly, and nothing is left behind underneath. A rule moves by
drawing it in a shifted copy of the coordinates (``q ... cm ... Q``).

The words that were changed are taken out of the drawing itself, glyph by
glyph: a piece of text (one Tj or TJ) they share with other words is
written again without them, every other glyph at its place and the pen
left where it was, so what follows on the page stays put. New words are
written at that place in the drawing (see :class:`Flow`), so they keep the
old ones' colour, outline, clipping and tag. Text inside a form XObject is
changed in a copy of the form made for that one drawing of it. When any
of this isn't possible for sure (a font whose letter widths can't be read,
letters drawn twice at one spot), nothing is changed here and the caller
covers the old words instead; the text below then only moves down, never
up under the cover.

When the text below would be pushed past the bottom margin (or into
something that stays), nothing moves and the change says so: no words are
ever pushed off the page. The last paragraph of a column runs on instead
at the top of the next column, if that one's text can move down to make
room, as linked text boxes do.
"""

import math
import statistics

from pypdf.generic import (ArrayObject, ByteStringObject, ContentStream, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject)

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
PAINT = {b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"n"}
FILLS = {b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"}
BUILD = {b"m", b"l", b"c", b"v", b"y", b"h", b"re"}
TEXT_OPS = {b"Tj", b"TJ", b"'", b'"'}
RULE = 3.0      # points: a path no taller than this (and wider than tall) is a rule
COVER_PAD = 2.5  # points: how far an earlier change's cover box reaches past its words
NEAR = 1.0      # points: a letter pdfium reads and one worked out here are the same letter
SAME_GLYPH = 0.6  # points: the same, when single letters (not whole lines) are matched up
TWICE = 0.35    # points: two letters drawn this close are one drawn twice (a shadow, a false bold)
FORMS = 60      # form XObjects on a page: past this their text isn't looked into
MARGIN = 72.0   # points: the bottom margin of a page whose text ends higher up


def _mul(m, n):
    """``m`` then ``n`` (PDF matrices, row vectors)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


def _at(m, x, y):
    return x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5]


def _inv(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    return (d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det)


def _nums(operands):
    try:
        return [float(v) for v in operands]
    except (TypeError, ValueError):
        return None


# ---- the fonts, for how far each piece of text moves the pen ----------------------------

class _FontInfo:
    """Widths and code lengths of one font, to follow the pen along a line."""

    def __init__(self, font):
        from pdfmanager import fonts
        from pdfmanager.textedit import _widths

        self.sure = True
        font = font.get_object() if font is not None else None
        if font is None:
            self.width, self.size, self.spaces, self.sure = (lambda code: 500), 1, {b" "}, False
            return
        subtype = font.get("/Subtype")
        width = _widths(font)
        self.size = 1
        if subtype == "/Type0":
            encoding = font.get("/Encoding")
            encoding = encoding.get_object() if encoding is not None else None
            if isinstance(encoding, NameObject):
                self.size = 2
                self.sure = any(k in str(encoding) for k in ("Identity", "UCS2", "UTF16"))
            elif encoding is not None:
                self.size, self.sure = self._codespace(encoding)
            else:
                self.size, self.sure = 2, False
        elif subtype == "/Type3":
            try:
                scale = float(font.get("/FontMatrix", [0.001])[0]) * 1000
            except (TypeError, ValueError, IndexError):
                scale, self.sure = 1.0, False
            plain = width
            width = (lambda code: plain(code) * scale)  # noqa: E731 - glyph space to thousandths
        self.width = width
        try:
            codes = fonts.pdf_font_codes(font)
        except Exception:  # noqa: BLE001 - a font dictionary we can't read
            codes = {}
        # Spaces as the font maps them: in a font cut down for a PDF, byte 32
        # is often a letter (word spacing still applies to it, as PDF says).
        letters = {code for letter, code in codes.items() if not letter.isspace()}
        self.spaces = {code for letter, code in codes.items() if letter.isspace() and code not in letters}
        if self.size == 1 and not codes:
            self.spaces.add(b" ")

    @staticmethod
    def _codespace(encoding):
        """(bytes per code, sure) from an embedded CMap's code space."""
        import re

        try:
            data = encoding.get_data()
        except Exception:  # noqa: BLE001
            return 2, False
        sizes = set()
        for block in re.findall(rb"begincodespacerange(.*?)endcodespacerange", data, re.S):
            for lo in re.findall(rb"<([0-9A-Fa-f]+)>\s*<[0-9A-Fa-f]+>", block):
                sizes.add(len(lo) // 2)
        return (sizes.pop(), True) if len(sizes) == 1 else (2, False)

    def codes(self, data):
        n = self.size
        return [data[k:k + n] for k in range(0, len(data) - len(data) % n, n)]


# ---- the page's drawing, read step by step --------------------------------------------------

class Content:
    """The page's own drawing (not inside form XObjects), read operator by
    operator: where each piece of text, path and picture is on the page."""

    def __init__(self, page, pdf, form=None, ctm=IDENTITY, do=None):
        """The page's drawing, or that of ``form`` (a form XObject the page
        draws at operator ``do[0]`` under the name ``do[1]``, with ``ctm``
        the transformation its drawing starts in)."""
        self.form, self.do, self.base = form, do, ctm
        resources = page.get("/Resources")
        if form is None:
            contents = page.get_contents()
            self.ops = ContentStream(contents, pdf).operations if contents is not None else []
        else:
            self.ops = ContentStream(form, pdf).operations
            if form.get("/Resources") is not None:
                resources = form.get("/Resources")
        resources = resources.get_object() if resources is not None else {}
        self.resources = resources
        fonts = resources.get("/Font")
        self.fonts = fonts.get_object() if fonts is not None else {}
        xobjects = resources.get("/XObject")
        self.xobjects = xobjects.get_object() if xobjects is not None else {}
        self._info = {}
        self.shows = []   # {i, op, glyphs: [(x, y, space)], rise, m (Tm x CTM), fs, th, tx, sure}
        self.paths = []   # {start, end, box, fill, segments, rect, ctm}
        self.things = []  # pictures and forms: their boxes
        self.forms = []   # (operator index, name, the form, the transformation it's drawn in)
        self._walk()

    def _font(self, name):
        if name not in self._info:
            self._info[name] = _FontInfo(self.fonts.get(name))
        return self._info[name]

    def _walk(self):
        ctm, stack = self.base, []
        ts = {"Tc": 0.0, "Tw": 0.0, "Th": 1.0, "TL": 0.0, "font": None, "fs": 0.0, "rise": 0.0, "fill": []}
        tm = tlm = IDENTITY
        lx = 0.0  # how far the pen is along the line from where the line began (text space)
        path, path_start, segments, rects, clip = [], None, 0, 0, False
        for i, (operands, op) in enumerate(self.ops):
            if op == b"q":
                stack.append((ctm, dict(ts)))
            elif op == b"Q":
                if stack:
                    ctm, ts = stack.pop()
            elif op == b"cm":
                m = _nums(operands)
                if m and len(m) == 6:
                    ctm = _mul(tuple(m), ctm)
            elif op == b"BT":
                tm = tlm = IDENTITY
                lx = 0.0
            elif op in (b"g", b"rg", b"k", b"cs"):
                ts["fill"] = [(operands, op)]
            elif op in (b"sc", b"scn"):
                ts["fill"] = [f for f in ts["fill"] if f[1] == b"cs"] + [(operands, op)]
            elif op in (b"Tc", b"Tw", b"Tz", b"TL", b"Ts"):
                v = _nums(operands)
                if v:
                    key = {b"Tc": "Tc", b"Tw": "Tw", b"Tz": "Th", b"TL": "TL", b"Ts": "rise"}[op]
                    ts[key] = v[0] / 100 if op == b"Tz" else v[0]
            elif op == b"Tf" and len(operands) == 2:
                ts["font"] = operands[0]
                v = _nums(operands[1:])
                ts["fs"] = v[0] if v else 0.0
            elif op == b"Tf" and len(operands) == 1 and _nums(operands):
                ts["fs"] = _nums(operands)[0]  # no font named: readers take the size alone
            elif op in (b"Td", b"TD"):
                v = _nums(operands)
                if v and len(v) == 2:
                    if op == b"TD":
                        ts["TL"] = -v[1]
                    tm = tlm = _mul((1, 0, 0, 1, v[0], v[1]), tlm)
                    lx = 0.0
            elif op == b"Tm":
                v = _nums(operands)
                if v and len(v) == 6:
                    tm = tlm = tuple(v)
                    lx = 0.0
            elif op == b"T*":
                tm = tlm = _mul((1, 0, 0, 1, 0, -ts["TL"]), tlm)
                lx = 0.0
            elif op in TEXT_OPS:
                if op in (b"'", b'"'):
                    if op == b'"':
                        v = _nums(operands[:2])
                        if v and len(v) == 2:
                            ts["Tw"], ts["Tc"] = v
                    tm = tlm = _mul((1, 0, 0, 1, 0, -ts["TL"]), tlm)
                    lx = 0.0
                tm = self._show(i, op, operands, ts, tm, ctm, tlm, lx)
                lx += self.shows[-1]["tx"]
            elif op in BUILD:
                if path_start is None:
                    path_start, path, segments, rects, clip = i, [], 0, 0, False
                v = _nums(operands) or []
                if op == b"re" and len(v) == 4:
                    x, y, w, h = v
                    path += [_at(ctm, x, y), _at(ctm, x + w, y), _at(ctm, x, y + h), _at(ctm, x + w, y + h)]
                    rects += 1
                    segments += 4
                else:
                    path += [_at(ctm, v[k], v[k + 1]) for k in range(0, len(v) - 1, 2)]
                    segments += op != b"m"
            elif op in (b"W", b"W*"):
                clip = True
            elif op in PAINT:
                if path_start is not None and path:
                    xs, ys = [p[0] for p in path], [p[1] for p in path]
                    self.paths.append({"start": path_start, "end": i, "box": (min(xs), min(ys), max(xs), max(ys)),
                                       "fill": op in FILLS, "paint": op != b"n", "clip": clip,
                                       "segments": segments, "rects": rects, "ctm": ctm})
                path_start, path = None, []
            elif op == b"Do" and operands:
                ref = self.xobjects.get(operands[0])
                obj = ref.get_object() if ref is not None else None
                m = ctm
                if obj is not None and obj.get("/Subtype") == "/Form":
                    bbox = _nums(obj.get("/BBox", [0, 0, 1, 1])) or [0, 0, 1, 1]
                    m = _mul(tuple(_nums(obj.get("/Matrix", IDENTITY)) or IDENTITY), ctm)
                    self.forms.append((i, operands[0], obj, m))
                    corners = [_at(m, x, y) for x in (bbox[0], bbox[2]) for y in (bbox[1], bbox[3])]
                else:
                    corners = [_at(m, x, y) for x in (0, 1) for y in (0, 1)]
                xs, ys = [p[0] for p in corners], [p[1] for p in corners]
                self.things.append((min(xs), min(ys), max(xs), max(ys)))
            elif op == b"INLINE IMAGE":
                corners = [_at(ctm, x, y) for x in (0, 1) for y in (0, 1)]
                xs, ys = [p[0] for p in corners], [p[1] for p in corners]
                self.things.append((min(xs), min(ys), max(xs), max(ys)))
            elif op == b"sh":
                self.things.append((-1e9, -1e9, 1e9, 1e9))  # a shading fills what's clipped: assume all

    def _show(self, i, op, operands, ts, tm, ctm, tlm=IDENTITY, lx=0.0):
        """Follow the pen through one piece of text; the text matrix after it."""
        info = self._font(ts["font"])
        fs, th = ts["fs"], ts["Th"]
        items = operands[-1] if op in (b"'", b'"', b"Tj") else operands[0] if operands else []
        if op != b"TJ":
            items = [items]
        start = _mul(tm, ctm)
        glyphs, tx = [], 0.0
        codes, txs, advs = [], [], []
        for item in items:
            if isinstance(item, (int, float)):
                tx += -float(item) / 1000 * fs * th
                continue
            data = getattr(item, "original_bytes", None)
            if data is None:
                data = bytes(item) if isinstance(item, (bytes, bytearray)) else str(item).encode("latin-1", "replace")
            for code in info.codes(data):
                x, y = _at(start, tx, ts["rise"])
                space = code in info.spaces
                glyphs.append((x, y, space))
                w = info.width(code) / 1000 * fs + ts["Tc"] + (ts["Tw"] if info.size == 1 and code == b" " else 0)
                codes.append(code)
                txs.append(tx)
                advs.append(w * th)
                tx += w * th
        self.shows.append({"i": i, "op": op, "glyphs": glyphs, "rise": ts["rise"], "m": start, "fs": fs,
                           "th": th, "tx": tx, "sure": info.sure, "operands": operands,
                           "codes": codes, "txs": txs, "advs": advs, "tlm": tlm, "lx": lx, "ctm": ctm,
                           "font": ts["font"], "Tc": ts["Tc"], "Tw": ts["Tw"], "fill": ts["fill"],
                           "info": info, "layer": self})
        return _mul((1, 0, 0, 1, tx, 0), tm)


# ---- what pdfium sees: the letters that show, as lines ----------------------------------------

def _shown_letters(textpage, covers):
    """Every letter that shows: [(index, loose box, origin, size, level)]."""
    import ctypes

    from pdfmanager.textedit import _origin, _raw

    raw = _raw()
    found = []
    for i in range(textpage.count_chars()):
        loose = textpage.get_charbox(i, loose=True)
        if loose[2] <= loose[0] or loose[3] <= loose[1]:
            continue
        ch = textpage.get_text_range(i, 1)
        if not ch or ch.isspace() or covers.hides(textpage, i, loose):
            continue
        m = raw.FS_MATRIX()
        raw.FPDFText_GetMatrix(textpage.raw, i, ctypes.byref(m))
        scale = math.hypot(m.a, m.b) or 1.0
        size = abs(raw.FPDFText_GetFontSize(textpage.raw, i)) * scale or (loose[3] - loose[1])
        level = abs(m.b) <= abs(m.a) * 0.02 and abs(m.c) <= abs(m.d) * 0.02 and m.a > 0 and m.d > 0
        found.append((i, loose, _origin(textpage, i), size, level))
    return found


def _lines(letters):
    """Letters grouped by baseline, top to bottom: [[letter]]."""
    rows = []
    for letter in sorted(letters, key=lambda L: -L[2][1]):
        y, size = letter[2][1], max(1.0, letter[3])
        if rows and abs(rows[-1][0] - y) < size * 0.35:
            rows[-1][1].append(letter)
        else:
            rows.append([y, [letter]])
    return [sorted(members, key=lambda L: L[1][0]) for _, members in rows]


def _box(letters):
    return (min(L[1][0] for L in letters), min(L[1][1] for L in letters),
            max(L[1][2] for L in letters), max(L[1][3] for L in letters))


def _overlaps(a0, a1, b0, b1):
    return min(a1, b1) - max(a0, b0) > 0


class Flow:
    """One page's text as lines, and the changes to its drawing that move
    some of them."""

    BIG_GAP = 3.0  # lines: a gap this tall (in the paragraph's line spacing) ends the column

    def __init__(self, page, writer, textpage, covers, crop):
        self.page, self.writer, self.textpage, self.covers = page, writer, textpage, covers
        self.crop = crop  # the page's visible box, (x0, y0, x1, y1)
        self._letters = self._content = None
        self._forms, self._index, self._hidden = None, {}, None
        self.edits = {}       # (layer, show op index) -> {show, els, shift, gone}: letters changed in place
        self.dropped = set()  # path starts
        self.orphans = []     # new words that couldn't be written in place: drawn after everything else
        self.reshaped = {}    # operator index -> new operands: an underline or highlight that follows its words
        self.rises, self.moves = {}, {}  # show op index -> page dy; path start -> page dy
        self.annots = []  # (box, dy)
        self.moved_letters = {}  # pdfium letter index -> page dy
        self.flowed = False  # the page's text has been moved once (by one paragraph)

    @property
    def letters(self):
        if self._letters is None:
            self._letters = _shown_letters(self.textpage, self.covers)
        return self._letters

    @property
    def content(self):
        if self._content is None:
            try:
                self._content = Content(self.page, self.writer)
            except Exception:  # noqa: BLE001 - a drawing pypdf can't read: nothing moves
                self._content = False
        return self._content

    # -- changing letters where they are drawn ---------------------------------------------
    #
    # A piece of text (one Tj or TJ) is a row of glyphs, each at a known
    # place along the line. A change keeps that row and says what becomes
    # of each glyph: it stays, goes, or moves along the line; and new glyphs
    # (in the same font) or whole new runs (in another) come in between.
    # The piece is then written again as one TJ that puts every glyph at its
    # place and ends with the pen exactly where it ended before, so nothing
    # after it on the page moves. Because the new words are written where
    # the old ones were, they keep everything that applied to those:
    # colour, outline or see-through drawing, clipping, and the tag of a
    # tagged PDF.

    def form_layers(self):
        """The drawings of the form XObjects the page draws: [Content]."""
        if self._forms is None:
            self._forms = []
            content = self.content
            if content and len(content.forms) <= FORMS:
                for i, name, form, m in content.forms:
                    try:
                        self._forms.append(Content(self.page, self.writer, form=form, ctm=m, do=(i, name)))
                    except Exception:  # noqa: BLE001 - a form pypdf can't read: its text stays as it is
                        continue
        return self._forms

    def _glyphs_of(self, layer):
        index = self._index.get(id(layer))
        if index is None:
            index = self._index[id(layer)] = {}
            for show in layer.shows:
                for k, g in enumerate(show["glyphs"]):
                    if not g[2]:
                        index.setdefault(math.floor(g[1]), []).append((g[0], g[1], show, k))
        return index

    def _edit(self, show):
        key = (id(show["layer"]), show["i"])
        if key not in self.edits:
            self.edits[key] = {"show": show, "els": [("g", k) for k in range(len(show["glyphs"]))],
                               "shift": {}, "gone": set()}
        return self.edits[key]

    def _is_gone(self, show, k):
        edit = self.edits.get((id(show["layer"]), show["i"]))
        return edit is not None and k in edit["gone"]

    @staticmethod
    def _editable(show):
        return bool(show["sure"] and show["fs"] and show["th"] and math.hypot(show["m"][0], show["m"][1]))

    def _near(self, layers, ox, oy):
        near = []
        for layer in layers:
            index = self._glyphs_of(layer)
            row = math.floor(oy)
            for bucket in (row - 1, row, row + 1):
                for x, y, show, k in index.get(bucket, ()):
                    d = math.hypot(x - ox, y - oy)
                    if d < SAME_GLYPH and not self._is_gone(show, k):
                        near.append((d, show, k))
        near.sort(key=lambda t: t[0])
        return near

    def glyph(self, letter):
        """Where the letter (one of textedit's) is drawn: (piece of text,
        place in it), or None when it isn't found, or isn't found for sure
        (drawn twice, as for a shadow)."""
        content = self.content
        if not content:
            return None
        ox, oy = letter["origin"]
        near = self._near([content], ox, oy) or self._near(self.form_layers(), ox, oy)
        if not near or (len(near) > 1 and near[1][0] < max(TWICE, near[0][0] + 0.2)):
            return None
        return near[0][1], near[0][2]

    def space_width(self, letter):
        """The width of a space where the letter is drawn, in points along
        the line: the font's own space with the letter spacing, without any
        word spacing. None when the font has no space."""
        found = self.glyph(letter)
        if found is None or not self._editable(found[0]):
            return None
        show = found[0]
        info = show["info"]
        code = next((c for c in show["codes"] if c in info.spaces), None) or next(iter(sorted(info.spaces)), None)
        if code is None or info.width(code) <= 0:
            return None
        return (info.width(code) / 1000 * show["fs"] + show["Tc"]) * show["th"] * math.hypot(show["m"][0], show["m"][1])

    def follow(self, letters, spot, delta):
        """An underline or highlight drawn by the words of a changed line
        (a thin rule under or through them, or a filled box behind them,
        from the start of one letter to the end of another) goes with them:
        ``delta(x, tolerance, right)`` says how far the letter that starts
        (or, with ``right``, ends) at ``x`` moved (None when that isn't
        sure, and the mark is left as it is)."""
        content = self.content
        if not content or spot[0]["layer"] is not content:
            return
        size = max(L["style"]["size"] for L in letters)
        tol = 0.3 * size
        x0, x1 = min(L["loose"][0] for L in letters), max(L["loose"][2] for L in letters)
        y0, y1 = min(L["loose"][1] for L in letters), max(L["loose"][3] for L in letters)
        for path in content.paths:
            b, c = path["box"], path["ctm"]
            if not path["paint"] or path["clip"] or abs(c[1]) > 1e-6 or abs(c[2]) > 1e-6 or c[0] <= 0:
                continue
            if b[0] < x0 - tol or b[2] > x1 + tol or b[2] - b[0] < size:
                continue
            high = b[3] - b[1]
            rule = high <= RULE and b[1] >= y0 - 0.15 * size and b[3] <= y1
            behind = (path["fill"] and path["rects"] == 1 and high >= 0.6 * size and b[1] >= y0 - tol
                      and b[3] <= y1 + tol and path["start"] < spot[0]["i"])
            if not (rule or behind):
                continue
            if not any(abs(L["loose"][0] - b[0]) <= tol for L in letters) \
                    or not any(abs(L["loose"][2] - b[2]) <= tol for L in letters):
                continue  # not from letter to letter: a frame or a table's line, not a mark on words
            da, db = delta(b[0], tol), delta(b[2], tol, True)
            if da is None or db is None or (abs(da) < 0.005 and abs(db) < 0.005):
                continue
            span = range(path["start"], path["end"])
            kinds = [content.ops[i][1] for i in span]
            values = [_nums(content.ops[i][0]) for i in span]
            if kinds == [b"re"] and values[0] and len(values[0]) == 4 and values[0][2] > 0:
                x, y, w, h = values[0]
                self.reshaped[path["start"]] = [FloatObject(v) for v in (x + da / c[0], y, w + (db - da) / c[0], h)]
            elif kinds == [b"m", b"l"] and all(v and len(v) == 2 for v in values) and values[0][0] != values[1][0]:
                first = values[0][0] < values[1][0]
                for i, (x, y), left in zip(span, values, (first, not first)):
                    self.reshaped[i] = [FloatObject(x + (da if left else db) / c[0]), FloatObject(y)]

    def checkpoint(self):
        """The changes so far, to come back to with :meth:`rollback`."""
        return ({key: {"show": e["show"], "els": list(e["els"]), "shift": dict(e["shift"]), "gone": set(e["gone"])}
                 for key, e in self.edits.items()}, set(self.dropped))

    def rollback(self, saved):
        self.edits, self.dropped = saved

    def _drop(self, show, k):
        edit = self._edit(show)
        if k not in edit["gone"]:
            edit["gone"].add(k)
            edit["els"][edit["els"].index(("g", k))] = ("x", k)

    def _tidy_spaces(self, show):
        """Spaces left with no word on either side go too."""
        edit = self._edit(show)
        glyphs, gone = show["glyphs"], edit["gone"]
        marks = [k for k, g in enumerate(glyphs) if not g[2]]
        for k, g in enumerate(glyphs):
            if not g[2] or k in gone:
                continue
            before = [m for m in marks if m < k]
            after = [m for m in marks if m > k]
            b_gone = not before or before[-1] in gone
            a_gone = not after or after[0] in gone
            if not ((before and before[-1] in gone) or (after and after[0] in gone)):
                continue
            if b_gone and a_gone:
                self._drop(show, k)

    def remove(self, letters):
        """Take letters out of the drawing; what's left of their pieces of
        text stays where it is. False (and nothing changed) unless every one
        is found."""
        found = [self.glyph(L) for L in letters]
        if any(g is None or not self._editable(g[0]) for g in found):
            return False
        for show, k in found:
            self._drop(show, k)
        for show in {id(g[0]): g[0] for g in found}.values():
            self._tidy_spaces(show)
        return True

    def remove_spaces(self, a, b, lead, trail, inner):
        """Between letters ``a`` and ``b`` (kept, in one piece of text) words
        were taken out with ``inner`` spaces among them: those spaces go
        too, but not the first ``lead`` and last ``trail`` (kept, like the
        letters). Only when the spaces drawn there are exactly those."""
        ga, gb = self.glyph(a), self.glyph(b)
        if ga is None or gb is None or ga[0] is not gb[0] or not inner:
            return
        show = ga[0]
        between = [k for k in range(ga[1] + 1, gb[1]) if show["glyphs"][k][2] and not self._is_gone(show, k)]
        if len(between) != lead + inner + trail:
            return
        for k in between[lead:len(between) - trail]:
            self._drop(show, k)

    def shift(self, letter, ds):
        """Move a letter ``ds`` points along its line."""
        found = self.glyph(letter)
        if found is None or not self._editable(found[0]):
            return False
        show, k = found
        m = show["m"]
        scale = math.hypot(m[0], m[1])
        ux, uy = _unit_of(letter)
        if (m[0] * ux + m[1] * uy) / scale < 0.999:
            return False  # drawn mirrored or at another angle than it reads
        edit = self._edit(show)
        edit["shift"][k] = edit["shift"].get(k, 0.0) + ds / scale
        return True

    def insert(self, spot, before, run):
        """Write new words (``run``: {own, name, style, entries: [(letter,
        x, y)], same, color}) just before or after the glyph at ``spot``
        (from :meth:`glyph`, asked for before anything was removed). In the
        piece of text itself when they are in its font and on its line,
        else as a piece of their own at that place in the drawing."""
        show, k = spot
        if not self._editable(show) or not run["entries"]:
            return False
        edit = self._edit(show)
        els = edit["els"]
        at = next((n for n, el in enumerate(els) if el[0] in ("g", "x") and el[1] == k), None)
        if at is None:
            return False
        tag = ("b" if before else "a", k)
        pos = at
        if not before:
            pos = at + 1
            while pos < len(els) and els[pos][0] in ("c", "f") and els[pos][-1] == tag:
                pos += 1
        new = self._spliced(show, run, tag)
        if new is None:
            if _inv(show["ctm"]) is None:
                return False
            if run.get("color") is not None and not show["fill"] and show["layer"].form is not None:
                return False  # the colour to go back to is set outside the form: not known here
            new = [("f", run, tag)]
        els[pos:pos] = new
        return True

    def _font_of(self, show):
        font = show["layer"].fonts.get(show["font"]) if show["font"] is not None else None
        return font.get_object() if font is not None else None

    def _spliced(self, show, run, tag):
        """The run as glyphs of the piece of text itself, or None."""
        own = run["own"]
        if own is None or not run.get("same") or run.get("color") is not None \
                or self._font_of(show) is not own.font.get_object():
            return None
        inverse = _inv(show["m"])
        if inverse is None:
            return None
        info, fs, th = show["info"], show["fs"], show["th"]
        scale_y = math.hypot(show["m"][2], show["m"][3]) or 1.0
        new = []
        for ch, x, y in run["entries"]:
            tx, ty = _at(inverse, x, y)
            if abs(ty - show["rise"]) * scale_y > 0.05:
                return None  # not on this piece's line
            if ch.isspace() and own.space:
                continue  # the font has no space: the place of the next letter makes it
            try:
                codes = [own.codes[c] for c in ch]
            except KeyError:
                return None
            adv = sum((info.width(c) / 1000 * fs + show["Tc"] + (show["Tw"] if info.size == 1 and c == b" " else 0))
                      * th for c in codes)
            new.append(("c", b"".join(codes), tx, adv, tag))
        return new or None

    def _hidden_origins(self):
        """Where letters covered by an earlier change's box start."""
        if self._hidden is None:
            self._hidden = []
            if getattr(self.covers, "boxes", None):
                from pdfmanager.textedit import _origin

                for i in range(self.textpage.count_chars()):
                    loose = self.textpage.get_charbox(i, loose=True)
                    if loose[2] <= loose[0] or loose[3] <= loose[1]:
                        continue
                    ch = self.textpage.get_text_range(i, 1)
                    if ch and not ch.isspace() and self.covers.hides(self.textpage, i, loose):
                        self._hidden.append(_origin(self.textpage, i))
        return self._hidden

    def uncover(self, letters):
        """An earlier version covered old words with a box and drew the new
        ones after everything else. Before a change in place on this line:
        the letters hidden under such boxes go, and the boxes with them (or
        they would hide what's written in the old letters' place). False if
        that can't be done for sure."""
        content = self.content
        if not content:
            return False
        if not self._hidden_origins():
            return True
        # Boxes on this line: no taller than it, and reaching into it (the
        # old words may have been longer than the line is now).
        area = _grow((min(L["loose"][0] for L in letters), min(L["loose"][1] for L in letters),
                      max(L["loose"][2] for L in letters), max(L["loose"][3] for L in letters)), COVER_PAD)
        paths = [p for p in content.paths if p["start"] not in self.dropped and p["paint"] and self._cover_like(p)
                 and p["box"][1] >= area[1] and p["box"][3] <= area[3] and _overlaps(p["box"][0], p["box"][2], area[0], area[2])]
        hidden = [o for o in self._hidden_origins()
                  if any(p["box"][0] - 1 <= o[0] <= p["box"][2] + 1 and p["box"][1] - 1 <= o[1] <= p["box"][3] + 1 for p in paths)]
        if not hidden:
            # A box of the page's own (a highlight) may lie on the line: it stays.
            return True
        used, shows = set(), {}
        for ox, oy in hidden:
            over = [p for p in paths if p["box"][0] - 1 <= ox <= p["box"][2] + 1 and p["box"][1] - 1 <= oy <= p["box"][3] + 1]
            if not over:
                return False
            last = max(p["start"] for p in over)
            under = [(show, k) for _, show, k in self._near([content], ox, oy) if show["i"] < last]
            if not under or not all(self._editable(show) for show, _ in under):
                return False
            for show, k in under:
                self._drop(show, k)
                shows[id(show)] = show
            used.update(p["start"] for p in over)
        for show in shows.values():
            self._tidy_spaces(show)
        self.dropped.update(used)
        return True

    # -- taking the rewritten paragraph out -------------------------------------------------
    def take_out(self, rows):
        """Take the paragraph's old words (``rows`` of textedit letters) out
        of the drawing. Other words drawn in the same pieces of text stay
        where they are. False, and nothing changed, unless every one of its
        letters is found."""
        content = self.content
        if not content:
            return False
        boxes = []
        for row in rows:
            size = max(L["style"]["size"] for L in row)
            boxes.append((min(L["loose"][0] for L in row) - NEAR, min(L["loose"][1] for L in row) - NEAR,
                          max(L["loose"][2] for L in row) + size * 0.6, max(L["loose"][3] for L in row) + NEAR))
        # Only as far right as the row's own letters: a piece of text shared
        # with a neighbour on the line is cut there.
        tight = [(b[0], b[1], max(L["origin"][0] for L in row) + NEAR, b[3]) for b, row in zip(boxes, rows)]

        def inside(x, y):
            return any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in tight)

        saved = self.checkpoint()
        taken, origins = [], []
        for show in content.shows:
            marks = [(k, g) for k, g in enumerate(show["glyphs"]) if not g[2] and not self._is_gone(show, k)]
            ins = [(k, g) for k, g in marks if inside(g[0], g[1])]
            if not ins:
                continue
            if not self._editable(show):
                self.rollback(saved)
                return False  # the widths of its letters can't be read
            for k, g in ins:
                self._drop(show, k)
                origins.append((g[0], g[1]))
            taken.append(show)
        rest = [L for row in rows for L in row
                if not any(abs(L["origin"][0] - x) < NEAR and abs(L["origin"][1] - y) < NEAR for x, y in origins)]
        if rest and not self.remove(rest):  # drawn in a form XObject, or nowhere this can change
            self.rollback(saved)
            return False
        if not taken and not rest:
            self.rollback(saved)
            return False
        for show in taken:
            self._tidy_spaces(show)
        # The boxes earlier changes drew over words in it go too, or the
        # lines moving up would be hidden under them.
        if taken:
            first = min(show["i"] for show in taken)
            area = (min(b[0] for b in boxes) - COVER_PAD, min(b[1] for b in boxes) - COVER_PAD,
                    max(b[2] for b in boxes) + COVER_PAD, max(b[3] for b in boxes) + COVER_PAD)
            for path in content.paths:
                if path["start"] > first and self._cover_like(path) and _within(path["box"], area):
                    self.dropped.add(path["start"])
        return True

    @staticmethod
    def _cover_like(path):
        return path["fill"] and not path["clip"] and 1 <= path["segments"] <= 6

    # -- the column below a paragraph -------------------------------------------------------
    def column(self, rows, lead):
        """What would move with the paragraph ``rows``: {"lines": [box],
        "letters", "rules", "covers": what moves, "limit": the lowest a
        line may go, "low": the lowest it goes now, "left", "right": the
        column's gutters}."""
        own = {L["index"] for row in rows for L in row}
        everything = [L for row in rows for L in row]
        top = max(L["loose"][3] for L in everything)
        bottom = min(L["loose"][1] for L in everything)
        left = min(L["loose"][0] for L in everything)
        right = max(L["loose"][2] for L in everything)
        size = max(L["style"]["size"] for L in everything)
        letters = [L for L in self.letters if L[0] not in own]
        # The gutters: the nearest words beside the paragraph, on its lines.
        beside = [L for L in letters if L[1][1] < top and L[1][3] > bottom]
        gutter_r = min([L[1][0] for L in beside if L[1][0] >= right - 0.5] + [self.crop[2]])
        gutter_l = max([L[1][2] for L in beside if L[1][2] <= left + 0.5] + [self.crop[0]])
        below = [L for L in letters if L[2][1] < min(L2["origin"][1] for L2 in everything) - size * 0.3
                 and L[1][3] <= bottom + size * 0.5]
        found = self._down(below, bottom, gutter_l, gutter_r, max(left - gutter_l, 1.0), max(gutter_r - right, 1.0),
                           lead)
        found["start"], found["edge"] = left, right  # the paragraph's left edge, where its longest line ends
        return found

    def next_column(self, found, lead):
        """The column to the right of the one ``found`` is in, for a
        paragraph to run on into: what's in it, as :meth:`column` gives,
        with "top" (its first line's top), "baseline" (that line's) and
        "x0", "x1" (its width); or None. Columns are the strips of the page
        the letters cover, with empty gutters between them (a heading
        across two columns makes them one: nothing runs on then)."""
        level = [L for L in self.letters if L[4]]
        if not level:
            return None
        size = statistics.median(L[3] for L in level)
        strips = []
        for a, b in sorted((L[1][0], L[1][2]) for L in level):
            if strips and a <= strips[-1][1] + size:
                strips[-1][1] = max(strips[-1][1], b)
            else:
                strips.append([a, b])
        at = next((k for k, (a, b) in enumerate(strips) if a - 1 <= found["start"] <= b), None)
        if at is None or at + 1 >= len(strips):
            return None
        x0, x1 = strips[at + 1]
        gutter = strips[at + 2][0] if at + 2 < len(strips) else self.crop[2]
        mine = [L for L in level if x0 - 0.5 < (L[1][0] + L[1][2]) / 2 < gutter]
        first = _lines(mine)[0]
        top, baseline = max(L[1][3] for L in first), first[0][2][1]
        below = [L for L in self.letters if L[2][1] <= baseline + 0.01]  # with the other columns', to see what spans
        nxt = self._down(below, top + 0.5, x0 - 0.5, gutter, max(x0 - strips[at][1], 1.0), max(gutter - x1, 1.0), lead)
        if not nxt["lines"] or abs(nxt["lines"][0][3] - top) > 0.5:
            return None  # its first line doesn't move (a heading across the columns, a picture)
        nxt.update(top=top, baseline=baseline, x0=x0, x1=x1)
        return nxt

    def _down(self, below, bottom, gutter_l, gutter_r, gap_l, gap_r, lead):
        """Walk down a column from ``bottom`` between the gutters: the lines
        (of ``below``) and rules that would move, until a big gap or
        something that stays."""
        # The bottom margin: as low as the page's text already goes, or an
        # inch from the bottom on a page whose text ends higher up.
        page_low = min([L[1][1] for L in self.letters] + [self.crop[1] + MARGIN])
        margin = self.crop[1] + max(0.0, min(MARGIN, page_low - self.crop[1]))
        found = {"lines": [], "letters": [], "rules": [], "covers": [], "limit": margin,
                 "bottom": bottom, "left": gutter_l, "right": gutter_r, "barrier": None}
        items = []  # (top, kind, thing)
        for line in _lines(below):
            mine = [L for L in line if gutter_l < (L[1][0] + L[1][2]) / 2 < gutter_r]
            if not mine:
                continue  # another column
            low_x, high_x = min(M[1][0] for M in mine), max(M[1][2] for M in mine)
            spans = any(L[1][0] - high_x < gap_r * 0.5 if (L[1][0] + L[1][2]) / 2 >= gutter_r
                        else low_x - L[1][2] < gap_l * 0.5 for L in line if L not in mine)
            box = _box(mine)
            kind = "barrier" if spans or not all(L[4] for L in mine) else "line"
            items.append((box[3], kind, (box, mine)))
        texts = [t[2][0] for t in items if t[1] == "line"]
        content = self.content
        if content:
            for path in content.paths:
                b = path["box"]
                if not path["paint"] or b[3] > bottom + 0.5 or not _overlaps(b[0], b[2], gutter_l, gutter_r):
                    continue
                thin = b[3] - b[1] <= RULE and b[2] - b[0] > b[3] - b[1]
                if thin and not path["clip"] and b[0] >= gutter_l - 2 and b[2] <= gutter_r + 2:
                    items.append((b[3], "rule", path))
                elif self._cover_like(path) and any(_within(b, _grow(t, COVER_PAD)) for t in texts):
                    items.append((b[3], "cover", path))  # an earlier change's box over a line: goes with it
                else:
                    items.append((b[3], "barrier", (b, None)))
            for b in content.things:
                if b[3] <= bottom + 0.5 and _overlaps(b[0], b[2], gutter_l, gutter_r):
                    items.append((b[3], "barrier", (b, None)))
        items.sort(key=lambda t: -t[0])
        cursor = bottom
        for top_y, kind, thing in items:
            if kind == "cover":
                continue  # moves with the line it covers, if that moves
            box = thing["box"] if kind == "rule" else thing[0]
            if kind == "barrier" or cursor - top_y > self.BIG_GAP * lead:
                found["barrier"] = box
                found["limit"] = max(margin, box[3] + min(max(cursor - box[3], 0.0), 0.25 * lead))
                break
            if kind == "rule":
                found["rules"].append(thing)
            else:
                found["lines"].append(box)
                found["letters"] += thing[1]
            cursor = min(cursor, box[1])
        found["covers"] = [t[2] for t in items if t[1] == "cover"
                           and any(_within(t[2]["box"], _grow(b, COVER_PAD)) for b in found["lines"])]
        found["low"] = cursor
        return found

    def move(self, found, dy):
        """Move what ``column`` found by ``dy`` points (negative: down the
        page). False, and nothing moved, unless every line's words could be
        found in the drawing, each in pieces of text of its own."""
        content = self.content
        if not content:
            return False
        boxes = [_grow(b, NEAR) for b in found["lines"]]
        rises, origins = {}, []
        for show in content.shows:
            marks = [g for g in show["glyphs"] if not g[2]]
            if not marks:
                continue
            first = next((k for k, b in enumerate(boxes) if b[0] <= marks[0][0] <= b[2]
                          and b[1] <= marks[0][1] <= b[3]), None)
            if first is None:
                continue
            b = found["lines"][first]
            size = show["fs"] * math.hypot(show["m"][2], show["m"][3]) or 1.0
            if any(not (b[0] - NEAR <= g[0] <= b[2] + size and b[1] - NEAR <= g[1] <= b[3] + NEAR) for g in marks):
                return False  # one piece of text runs on into something that stays
            m = show["m"]
            if abs(m[2]) > abs(m[3]) * 0.02 or not m[3]:
                return False
            rises[show["i"]] = dy
            origins += [(g[0], g[1]) for g in marks]
        for letter in found["letters"]:
            ox, oy = letter[2]
            if not any(abs(ox - x) < NEAR and abs(oy - y) < NEAR for x, y in origins):
                return False  # drawn somewhere this can't change (a form XObject)
        self.rises.update({i: self.rises.get(i, 0.0) + dy for i in rises})
        for path in found["rules"] + found["covers"]:
            self.moves[path["start"]] = self.moves.get(path["start"], 0.0) + dy
        for letter in found["letters"]:
            self.moved_letters[letter[0]] = dy
        self.annots += [(b, dy) for b in found["lines"]]
        self.flowed = True
        return True

    # -- writing the changed drawing --------------------------------------------------------
    def changed(self):
        return bool(self.edits or self.dropped or self.rises or self.moves or self.reshaped)

    def pending(self):
        """The new runs to be written in place, for their fonts to be made ready."""
        return [el[1] for edit in self.edits.values() for el in edit["els"] if el[0] == "f"]

    def write(self, writer, page):
        """Put the changes into the page's drawing (and move the links and
        highlights on the lines that moved)."""
        if not self.changed():
            return
        content = self.content
        renames = {}
        for layer in self._forms or []:
            if not any(e["show"]["layer"] is layer for e in self.edits.values()):
                continue
            # The form may be drawn elsewhere too (twice on this page, or on
            # other pages): this drawing of it gets a copy of its own.
            names = _FontNames(layer.fonts)
            data = self._stream(writer, self._ops(layer, names, {}))
            form = DecodedStreamObject()
            for key, value in layer.form.items():
                if key not in ("/Length", "/Filter", "/DecodeParms"):
                    form[NameObject(key)] = value
            if names.added:
                resources = DictionaryObject(layer.resources)
                resources[NameObject("/Font")] = DictionaryObject({**layer.fonts, **names.added})
                form[NameObject("/Resources")] = resources
            form.set_data(data)
            n = 1
            while f"/ZPDFFm{n}" in content.xobjects:
                n += 1
            content.xobjects[NameObject(f"/ZPDFFm{n}")] = writer._add_object(form)
            renames[layer.do[0]] = NameObject(f"/ZPDFFm{n}")
        names = _FontNames(content.fonts)
        data = self._stream(writer, self._ops(content, names, renames))
        if names.added:
            resources = page.get("/Resources")
            resources = DictionaryObject() if resources is None else resources.get_object()
            page[NameObject("/Resources")] = resources
            font_dict = resources.get("/Font")
            font_dict = DictionaryObject() if font_dict is None else font_dict.get_object()
            resources[NameObject("/Font")] = font_dict
            font_dict.update(names.added)
        stream = DecodedStreamObject()
        stream.set_data(data)
        page[NameObject("/Contents")] = writer._add_object(stream)
        self._move_annotations(page)

    @staticmethod
    def _stream(writer, ops):
        stream = ContentStream(None, writer)
        stream.operations = ops
        return stream.get_data()

    def _ops(self, layer, names, renames):
        """The layer's drawing with the changes made."""
        top = layer.form is None  # lines and rules only move in the page's own drawing
        shows = {show["i"]: show for show in layer.shows}
        ends = {path["start"]: path["end"] for path in layer.paths}
        out, skip_to, open_q = [], -1, None
        for i, (operands, op) in enumerate(layer.ops):
            if i <= skip_to:
                continue
            if top and i in self.dropped:
                skip_to = ends[i]
                continue
            if top and i in self.moves and open_q is None:
                path = next(p for p in layer.paths if p["start"] == i)
                a, b, c, d = path["ctm"][:4]
                det = a * d - b * c
                if abs(det) > 1e-12:
                    dy = self.moves[i]
                    out += [([], b"q"), ([FloatObject(1), FloatObject(0), FloatObject(0), FloatObject(1),
                                          FloatObject(-dy * c / det), FloatObject(dy * a / det)], b"cm")]
                    open_q = ends[i]
            edit = self.edits.get((id(layer), i))
            dy = self.rises.get(i) if top else None
            if dy is not None:
                show = shows[i]
                risen = show["rise"] + dy / show["m"][3]
                out.append(([FloatObject(risen)], b"Ts"))
                out += self._rebuilt(edit, names, dy, risen) if edit else [(operands, op)]
                out.append(([FloatObject(show["rise"])], b"Ts"))
            elif edit:
                out += self._rebuilt(edit, names, 0.0, edit["show"]["rise"])
            elif i in renames:
                out.append(([renames[i]], op))
            elif top and i in self.reshaped:
                out.append((self.reshaped[i], op))
            else:
                out.append((operands, op))
            if open_q is not None and i == open_q:
                out.append(([], b"Q"))
                open_q = None
        return out

    def _rebuilt(self, edit, names, dy, rise):
        """One piece of text written again with its changes: the glyphs
        that stay each at their place, the new ones between them, and the
        pen left where it was left before."""
        show = edit["show"]
        fs, th, glyphs = show["fs"], show["th"], show["glyphs"]
        els, shift = edit["els"], edit["shift"]
        unit = fs * th / 1000.0
        out, arr, pen = [], [], 0.0
        op, operands = show["op"], show["operands"]
        if op == b'"' and len(operands) >= 2:
            out += [([operands[0]], b"Tw"), ([operands[1]], b"Tc")]
        if op in (b"'", b'"'):
            out.append(([], b"T*"))

        def moved(n, k):
            """How far glyph ``k`` (element ``n``) moves: a space goes with
            the word right after it, else the one right before."""
            if k in shift or not glyphs[k][2]:
                return shift.get(k, 0.0)
            for step in (1, -1):
                j = n + step
                while 0 <= j < len(els) and els[j][0] == "g" and glyphs[els[j][1]][2]:
                    j += step
                if 0 <= j < len(els) and els[j][0] == "g":
                    return shift.get(els[j][1], 0.0)
            return 0.0

        def kern(to):
            nonlocal pen
            d = to - pen
            if abs(d) > abs(unit) * 0.25:
                n = -d / unit
                if arr and isinstance(arr[-1], FloatObject):
                    arr[-1] = FloatObject(float(arr[-1]) + n)
                else:
                    arr.append(FloatObject(n))
                pen = to

        def put(code, adv):
            nonlocal pen
            if arr and isinstance(arr[-1], ByteStringObject):
                arr[-1] = ByteStringObject(bytes(arr[-1]) + code)
            else:
                arr.append(ByteStringObject(code))
            pen += adv

        def flush():
            nonlocal arr
            if arr:
                out.append(([ArrayObject(arr)], b"TJ"))
                arr = []

        inverse = _inv(show["m"])
        follow = None  # where a space right after new words goes: straight after them
        for n, el in enumerate(els):
            if el[0] == "g":
                k = el[1]
                if glyphs[k][2] and follow is not None:
                    kern(follow)
                    put(show["codes"][k], show["advs"][k])
                    follow = pen
                    continue
                follow = None
                kern(show["txs"][k] + moved(n, k))
                put(show["codes"][k], show["advs"][k])
            elif el[0] == "c":
                kern(el[2])
                put(el[1], el[3])
                follow = pen
            elif el[0] == "f":
                run = el[1]
                if run["own"] is None:
                    self.orphans.append(run)  # its font couldn't be put in the file: drawn the old way
                    continue
                flush()
                ops, end = _foreign(show, run, names.of(run["own"].font), dy)
                out += ops
                follow = _at(inverse, end[0], end[1])[0] if inverse else None
                # Back to the piece's own font and spacing, the pen where its line began.
                if show["font"] is not None:
                    out.append(([show["font"], FloatObject(fs)], b"Tf"))
                out += [([FloatObject(show["Tc"])], b"Tc"), ([FloatObject(show["Tw"])], b"Tw"),
                        ([FloatObject(th * 100)], b"Tz"), ([FloatObject(rise)], b"Ts")]
                if run.get("color") is not None:
                    out += show["fill"] or [([FloatObject(0)], b"g")]
                out.append(([FloatObject(v) for v in show["tlm"]], b"Tm"))
                pen = -show["lx"]
        kern(show["tx"])
        flush()
        return out

    def _move_annotations(self, page):
        """Links and text highlights on lines that moved go with them."""
        if not self.annots or "/Annots" not in page:
            return
        for ref in page["/Annots"].get_object():
            annot = ref.get_object()
            if annot.get("/Subtype") not in ("/Link", "/Highlight", "/Underline", "/StrikeOut", "/Squiggly"):
                continue
            rect = _nums(annot.get("/Rect", [])) or []
            if len(rect) != 4:
                continue
            rect = (min(rect[0], rect[2]), min(rect[1], rect[3]), max(rect[0], rect[2]), max(rect[1], rect[3]))
            dy = next((d for b, d in self.annots if _within(rect, _grow(b, COVER_PAD + 1))), None)
            if dy is None:
                continue
            annot[NameObject("/Rect")] = ArrayObject([FloatObject(rect[0]), FloatObject(rect[1] + dy),
                                                      FloatObject(rect[2]), FloatObject(rect[3] + dy)])
            quads = _nums(annot.get("/QuadPoints", [])) or []
            if quads:
                annot[NameObject("/QuadPoints")] = ArrayObject(
                    [FloatObject(v + dy if k % 2 else v) for k, v in enumerate(quads)])


class _FontNames:
    """The names fonts go by in one drawing's resources; new ones as needed."""

    def __init__(self, fonts):
        self.fonts, self.added = fonts, {}

    def of(self, font):
        obj = font.get_object()
        for name, value in list(self.fonts.items()) + list(self.added.items()):
            if value.get_object() is obj:
                return NameObject(name)
        n = 1
        while f"/ZPDF{n}" in self.fonts or f"/ZPDF{n}" in self.added:
            n += 1
        self.added[NameObject(f"/ZPDF{n}")] = font
        return NameObject(f"/ZPDF{n}")


def _unit_of(letter):
    a, b = letter["style"]["matrix"][:2]
    n = math.hypot(a, b) or 1.0
    return a / n, b / n


def _foreign(show, run, name, dy):
    """A run of new words in a font or look of its own, written at this
    place in the drawing: everything not set here (colour, outline,
    clipping, tags) is as for the words around it."""
    style, entries, own = run["style"], run["entries"], run["own"]
    a, b, c, d = style["matrix"]
    x0, y0 = entries[0][1], entries[0][2]
    tm = _mul((a, b, c, d, x0, y0 + dy), _inv(show["ctm"]))
    ops = []
    if run.get("color") is not None:
        ops.append(([FloatObject(round(v, 4)) for v in run["color"]], b"rg"))
    ops += [([name, FloatObject(style["raw_size"])], b"Tf"), ([FloatObject(0)], b"Tc"), ([FloatObject(0)], b"Tw"),
            ([FloatObject(100)], b"Tz"), ([FloatObject(0)], b"Ts"), ([FloatObject(v) for v in tm], b"Tm")]
    n = math.hypot(a, b) or 1.0
    ux, uy = a / n, b / n
    scale = n * style["raw_size"]

    def wide(ch):
        return sum(own.width_of(own.codes[c]) for c in ch) / 1000 * scale

    # Letters set apart evenly (letter spacing): said once, as spacing, not
    # as a move before each letter (which reads as a space between them).
    along = [((x - x0) * ux + (y - y0) * uy, ch) for ch, x, y in entries]
    gaps = [s2 - s1 - wide(c1) for (s1, c1), (s2, c2) in zip(along, along[1:]) if not c1.isspace() and not c2.isspace()]
    tc = statistics.median(gaps) if len(gaps) >= 2 else 0.0
    if abs(tc) < 0.02 or sum(abs(g - tc) < 0.02 for g in gaps) < len(gaps) * 0.6:
        tc = 0.0
    if tc:
        ops[-5] = ([FloatObject(round(tc / n, 4))], b"Tc")  # in place of the "0 Tc" above
    arr, pen = [], 0.0
    for ch, x, y in entries:
        s = (x - x0) * ux + (y - y0) * uy
        move = (s - pen) / scale * 1000  # in thousandths of the font size
        if abs(move) > 0.25:
            if arr and isinstance(arr[-1], FloatObject):
                arr[-1] = FloatObject(float(arr[-1]) - move)
            else:
                arr.append(FloatObject(-move))
            pen = s
        if ch.isspace() and own.space:
            continue
        code = b"".join(own.codes[c] for c in ch)
        if arr and isinstance(arr[-1], ByteStringObject):
            arr[-1] = ByteStringObject(bytes(arr[-1]) + code)
        else:
            arr.append(ByteStringObject(code))
        pen += wide(ch) + tc * len(ch)
    ops.append(([ArrayObject(arr)], b"TJ"))
    return ops, (x0 + ux * pen, y0 + uy * pen)


def _blank(show):
    """A piece of text taken out: the same move of the pen, nothing drawn."""
    fs, th = show["fs"], show["th"]
    out = []
    op, operands = show["op"], show["operands"]
    if op == b'"' and len(operands) >= 2:
        out += [([operands[0]], b"Tw"), ([operands[1]], b"Tc")]
    if op in (b"'", b'"'):
        out.append(([], b"T*"))
    if fs and th and abs(show["tx"]) > 1e-9:
        out.append(([ArrayObject([FloatObject(-show["tx"] / (fs * th) * 1000)])], b"TJ"))
    return out


def _within(a, b):
    return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]


def _grow(b, d):
    return (b[0] - d, b[1] - d, b[2] + d, b[3] + d)

