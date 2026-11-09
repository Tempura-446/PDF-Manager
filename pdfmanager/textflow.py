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

The paragraph that was rewritten is taken out of the drawing the same way
(its words replaced by an empty move of the same width, so whatever comes
after on the line stays put). When that isn't possible for sure (its words
share a piece of text with others, sit inside a form XObject, or use a
font whose letter widths can't be read), the old way is kept: the words
are covered with the colour around them, and the text below only moves
down, never up under the cover.

When the text below would be pushed past the bottom margin (or into
something that stays), nothing moves and the change says so: no words are
ever pushed off the page. The last paragraph of a column runs on instead
at the top of the next column, if that one's text can move down to make
room, as linked text boxes do.
"""

import math
import statistics

from pypdf.generic import ArrayObject, ContentStream, DecodedStreamObject, FloatObject, NameObject

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
PAINT = {b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"n"}
FILLS = {b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"}
BUILD = {b"m", b"l", b"c", b"v", b"y", b"h", b"re"}
TEXT_OPS = {b"Tj", b"TJ", b"'", b'"'}
RULE = 3.0      # points: a path no taller than this (and wider than tall) is a rule
COVER_PAD = 2.5  # points: how far an earlier change's cover box reaches past its words
NEAR = 1.0      # points: a letter pdfium reads and one worked out here are the same letter
MARGIN = 72.0   # points: the bottom margin of a page whose text ends higher up


def _mul(m, n):
    """``m`` then ``n`` (PDF matrices, row vectors)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


def _at(m, x, y):
    return x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5]


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

    def __init__(self, page, pdf):
        contents = page.get_contents()
        self.ops = ContentStream(contents, pdf).operations if contents is not None else []
        resources = page.get("/Resources")
        resources = resources.get_object() if resources is not None else {}
        fonts = resources.get("/Font")
        self.fonts = fonts.get_object() if fonts is not None else {}
        xobjects = resources.get("/XObject")
        self.xobjects = xobjects.get_object() if xobjects is not None else {}
        self._info = {}
        self.shows = []   # {i, op, glyphs: [(x, y, space)], rise, m (Tm x CTM), fs, th, tx, sure}
        self.paths = []   # {start, end, box, fill, segments, rect, ctm}
        self.things = []  # pictures and forms: their boxes
        self._walk()

    def _font(self, name):
        if name not in self._info:
            self._info[name] = _FontInfo(self.fonts.get(name))
        return self._info[name]

    def _walk(self):
        ctm, stack = IDENTITY, []
        ts = {"Tc": 0.0, "Tw": 0.0, "Th": 1.0, "TL": 0.0, "font": None, "fs": 0.0, "rise": 0.0}
        tm = tlm = IDENTITY
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
            elif op in (b"Tc", b"Tw", b"Tz", b"TL", b"Ts"):
                v = _nums(operands)
                if v:
                    key = {b"Tc": "Tc", b"Tw": "Tw", b"Tz": "Th", b"TL": "TL", b"Ts": "rise"}[op]
                    ts[key] = v[0] / 100 if op == b"Tz" else v[0]
            elif op == b"Tf" and len(operands) == 2:
                ts["font"] = operands[0]
                v = _nums(operands[1:])
                ts["fs"] = v[0] if v else 0.0
            elif op in (b"Td", b"TD"):
                v = _nums(operands)
                if v and len(v) == 2:
                    if op == b"TD":
                        ts["TL"] = -v[1]
                    tm = tlm = _mul((1, 0, 0, 1, v[0], v[1]), tlm)
            elif op == b"Tm":
                v = _nums(operands)
                if v and len(v) == 6:
                    tm = tlm = tuple(v)
            elif op == b"T*":
                tm = tlm = _mul((1, 0, 0, 1, 0, -ts["TL"]), tlm)
            elif op in TEXT_OPS:
                if op in (b"'", b'"'):
                    if op == b'"':
                        v = _nums(operands[:2])
                        if v and len(v) == 2:
                            ts["Tw"], ts["Tc"] = v
                    tm = tlm = _mul((1, 0, 0, 1, 0, -ts["TL"]), tlm)
                tm = self._show(i, op, operands, ts, tm, ctm)
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

    def _show(self, i, op, operands, ts, tm, ctm):
        """Follow the pen through one piece of text; the text matrix after it."""
        info = self._font(ts["font"])
        fs, th = ts["fs"], ts["Th"]
        items = operands[-1] if op in (b"'", b'"', b"Tj") else operands[0] if operands else []
        if op != b"TJ":
            items = [items]
        start = _mul(tm, ctm)
        glyphs, tx = [], 0.0
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
                tx += w * th
        self.shows.append({"i": i, "op": op, "glyphs": glyphs, "rise": ts["rise"], "m": start, "fs": fs,
                           "th": th, "tx": tx, "sure": info.sure, "operands": operands})
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
        self.removed, self.dropped = set(), set()  # show op indices; path starts
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

    # -- taking the rewritten paragraph out -------------------------------------------------
    def take_out(self, rows):
        """Take the paragraph's old words (``rows`` of textedit letters) out
        of the drawing. False, and nothing changed, unless every one of its
        letters is found in pieces of text that hold nothing else."""
        content = self.content
        if not content:
            return False
        boxes = []
        for row in rows:
            size = max(L["style"]["size"] for L in row)
            boxes.append((min(L["loose"][0] for L in row) - NEAR, min(L["loose"][1] for L in row) - NEAR,
                          max(L["loose"][2] for L in row) + size * 0.6, max(L["loose"][3] for L in row) + NEAR))

        def inside(x, y):
            return any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in boxes)

        taken, origins = [], []
        for show in content.shows:
            marks = [g for g in show["glyphs"] if not g[2]]
            if not marks:
                continue
            ins = [inside(g[0], g[1]) for g in marks]
            if not any(ins):
                continue
            if not all(ins) or not show["sure"] or show["fs"] == 0 or show["th"] == 0:
                return False  # shares its piece of text with other words, or widths unknown
            taken.append(show)
            origins += [(g[0], g[1]) for g in marks]
        for L in (L for row in rows for L in row):
            ox, oy = L["origin"]
            if not any(abs(ox - x) < NEAR and abs(oy - y) < NEAR for x, y in origins):
                return False  # drawn somewhere this can't change (a form XObject)
        if not taken:
            return False
        self.removed.update(show["i"] for show in taken)
        # The boxes earlier changes drew over words in it go too, or the
        # lines moving up would be hidden under them.
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
        return bool(self.removed or self.dropped or self.rises or self.moves)

    def write(self, writer, page):
        """Put the changes into the page's drawing (and move the links and
        highlights on the lines that moved)."""
        if not self.changed():
            return
        content = self.content
        shows = {show["i"]: show for show in content.shows}
        ends = {path["start"]: path["end"] for path in content.paths}
        out, skip_to, open_q = [], -1, None
        for i, (operands, op) in enumerate(content.ops):
            if i <= skip_to:
                continue
            if i in self.dropped:
                skip_to = ends[i]
                continue
            if i in self.moves and open_q is None:
                path = next(p for p in content.paths if p["start"] == i)
                a, b, c, d = path["ctm"][:4]
                det = a * d - b * c
                if abs(det) > 1e-12:
                    dy = self.moves[i]
                    out += [([], b"q"), ([FloatObject(1), FloatObject(0), FloatObject(0), FloatObject(1),
                                          FloatObject(-dy * c / det), FloatObject(dy * a / det)], b"cm")]
                    open_q = ends[i]
            if i in self.removed:
                out += _blank(shows[i])
            elif i in self.rises:
                show = shows[i]
                k = self.rises[i] / show["m"][3]
                out += [([FloatObject(show["rise"] + k)], b"Ts"), (operands, op), ([FloatObject(show["rise"])], b"Ts")]
            else:
                out.append((operands, op))
            if open_q is not None and i == open_q:
                out.append(([], b"Q"))
                open_q = None
        stream = ContentStream(None, writer)
        stream.operations = out
        data = DecodedStreamObject()
        data.set_data(stream.get_data())
        page[NameObject("/Contents")] = writer._add_object(data)
        self._move_annotations(page)

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

