# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Text extraction, character geometry, font inspection, cover sampling and style helpers."""

import ctypes
import difflib
import math
import re
import statistics
import unicodedata

from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject

from pdfmanager import core, fonts
from pdfmanager.glyphs import _origin, _raw, _widths  # noqa: F401 - textedit._origin etc.

PAD = 0.6  # points added around the old words' box


SAME, INSTALLED, SIMILAR = "same", "installed", "similar"  # which font the words got


STANDARD = "standard"  # a standard PDF font the reader picked


BASE14_NAMES = set(fonts.BASE14.values())


MOVED = 0.005  # points: letters after a change that move less than this stay as they are


# Fonts a reader is supposed to have for Chinese, Japanese and Korean: not in
# the file and not to be put in it, so nothing is written with them here.
READER_FONTS = {"HYSMyeongJo-Medium", "HeiseiMin-W3", "STSong-Light"}


def _chars(textpage, rect, across=None):
    """Indices of the letters whose middle is inside ``rect``."""
    x0, y0, x1, y1 = rect
    # Up and down a little give (the box comes from the screen); along the
    # line hardly any, or a full stop of the next words would come too. A
    # paragraph's box gets ``across`` points: a fifth of its height would
    # reach into the lines above and below it.
    slack_x, slack_y = 1.0, max(1.0, (y1 - y0) * 0.2)
    if y1 - y0 > x1 - x0:  # a line running up or down the page
        slack_x, slack_y = max(1.0, (x1 - x0) * 0.2), 1.0
    if across is not None:
        slack_y = min(slack_y, across)
    found = []
    for i in range(textpage.count_chars()):
        left, bottom, right, top = textpage.get_charbox(i, loose=True)
        if right <= left or top <= bottom:
            continue  # spaces and line breaks added by pdfium
        cx, cy = (left + right) / 2, (bottom + top) / 2
        if x0 - slack_x <= cx <= x1 + slack_x and y0 - slack_y <= cy <= y1 + slack_y:
            found.append(i)
    return found


def _nearest_char(textpage, x, y):
    best, distance = None, None
    for i in range(textpage.count_chars()):
        left, bottom, right, top = textpage.get_charbox(i, loose=True)
        if right <= left or top <= bottom:
            continue
        dx = max(left - x, 0, x - right)
        dy = max(bottom - y, 0, y - top)
        d = dx * dx + dy * dy * 4  # the line it's on counts more than the column
        if distance is None or d < distance:
            best, distance = i, d
    return best


def _font_handle(textpage, index):
    """pdfium's handle on the font the letter is drawn with, or None."""
    raw = _raw()
    try:
        obj = raw.FPDFText_GetTextObject(textpage.raw, index)
        return raw.FPDFTextObj_GetFont(obj) if obj else None
    except AttributeError:  # an older pdfium
        return None


def _has_letter(font, letter):
    """Whether the font really draws ``letter``: fonts cut down to the
    letters a document uses keep empty (or placeholder, zero-width) glyphs
    for the others."""
    raw = _raw()
    try:
        path = raw.FPDFFont_GetGlyphPath(font, ord(letter), 10.0)
        if not path or raw.FPDFGlyphPath_CountGlyphSegments(path) <= 0:
            return False
        width = ctypes.c_float()
        return bool(raw.FPDFFont_GetGlyphWidth(font, ord(letter), 10.0, ctypes.byref(width))) and width.value > 0
    except (AttributeError, ValueError, OverflowError):
        return False


def _space_fonts(textpage):
    """The fonts the page draws a space with (pdfium's handles, as
    addresses). A font cut down to a document's letters may name a space
    and have a placeholder (a box) for it: Word draws every space in a
    one-letter font of its own, and sets the main font's words apart
    without one. Only a font seen drawing a space is trusted to."""
    raw = _raw()
    found = set()
    try:
        for i in range(textpage.count_chars()):
            if raw.FPDFText_GetUnicode(textpage.raw, i) != 32 or raw.FPDFText_IsGenerated(textpage.raw, i) == 1:
                continue
            handle = _font_handle(textpage, i)
            if handle:
                found.add(ctypes.cast(handle, ctypes.c_void_p).value)
    except AttributeError:  # an older pdfium
        return None
    return found


def _draws_space(style):
    """Whether the page draws spaces in the font of ``style`` (see _space_fonts)."""
    fonts_seen = style.get("spaces")
    if fonts_seen is None or not style.get("handle"):
        return True
    return ctypes.cast(style["handle"], ctypes.c_void_p).value in fonts_seen


def _font_data(font):
    """The font program (to tell apart cut-down copies of one font), or None."""
    raw = _raw()
    try:
        if not font or not raw.FPDFFont_GetIsEmbedded(font):
            return None
        size = ctypes.c_size_t()
        raw.FPDFFont_GetFontData(font, None, 0, ctypes.byref(size))
        if not size.value:
            return None
        buf = (ctypes.c_uint8 * size.value)()
        raw.FPDFFont_GetFontData(font, buf, size.value, ctypes.byref(size))
        return bytes(buf)
    except (AttributeError, OSError):  # an older pdfium
        return None


def _style(textpage, index, cache):
    """Font, size, colour, angle and starting point of a letter. Letters
    drawn the same way share one style (``cache`` keeps them), so runs of
    them can be told apart by identity."""
    raw = _raw()
    tp = textpage.raw
    buf, flags = ctypes.create_string_buffer(256), ctypes.c_int()
    raw.FPDFText_GetFontInfo(tp, index, buf, len(buf), ctypes.byref(flags))
    font_name = buf.value.decode("latin-1")
    rgba = [ctypes.c_uint() for _ in range(4)]
    color = ((0, 0, 0) if not raw.FPDFText_GetFillColor(tp, index, *map(ctypes.byref, rgba))
             else tuple(v.value / 255 for v in rgba[:3]))
    m = raw.FS_MATRIX()
    raw.FPDFText_GetMatrix(tp, index, ctypes.byref(m))
    matrix = (m.a, m.b, m.c, m.d)
    if not any(matrix):
        matrix = (1.0, 0.0, 0.0, 1.0)
    raw_size = raw.FPDFText_GetFontSize(tp, index)
    handle = _font_handle(textpage, index)
    address = ctypes.cast(handle, ctypes.c_void_p).value if handle else None
    key = (font_name, address, round(raw_size, 4), tuple(round(v, 4) for v in color),
           tuple(round(v, 5) for v in matrix))
    if key in cache:
        return cache[key]
    name = font_name.split("+")[-1].lower()
    weight = raw.FPDFText_GetFontWeight(tp, index)
    if flags.value & 1 or any(n in name for n in fonts.MONO_NAMES):
        family = "mono"
    elif (flags.value & 2 or any(n in name for n in fonts.SERIF_NAMES)) and "sans" not in name:
        family = "serif"
    else:
        family = "sans"
    if not raw_size or raw_size < 0:
        raw_size = abs(raw_size) or 1.0
    data = cache.setdefault(("data", address), _font_data(handle)) if address else None
    style = cache[key] = {
        "font": font_name, "handle": handle, "data": data, "family": family,
        "bold": weight >= 600 or bool(flags.value & (1 << 18)) or any(n in name for n in fonts.BOLD_NAMES),
        "italic": bool(flags.value & 64) or "italic" in name or "oblique" in name,
        "raw_size": raw_size, "matrix": matrix,
        "size": raw_size * (math.hypot(m.a, m.b) or 1.0),
        "color": color, "angle": math.degrees(math.atan2(matrix[1], matrix[0])),
        "spaces": cache["spaces"] if "spaces" in cache else cache.setdefault("spaces", _space_fonts(textpage)),
    }
    return style


def _default_style(rect):
    size = (rect[3] - rect[1]) * 0.75
    return {"font": "", "handle": None, "data": None, "family": "sans", "bold": False, "italic": False,
            "raw_size": size, "matrix": (1.0, 0.0, 0.0, 1.0), "size": size, "color": (0, 0, 0),
            "angle": 0.0}


def _background(page, image, box):
    """The colour around ``box`` (PDF points) on the page drawn in ``image``."""
    raw = _raw()
    width, height = image.size
    x0, y0, x1, y1 = box
    corners = []
    for px, py in ((x0, y0), (x1, y1), (x0, y1), (x1, y0)):
        dx, dy = ctypes.c_int(), ctypes.c_int()
        raw.FPDF_PageToDevice(page.raw, 0, 0, width, height, 0, px, py, ctypes.byref(dx),
                              ctypes.byref(dy))
        corners.append((dx.value, dy.value))
    left, right = min(c[0] for c in corners) - 2, max(c[0] for c in corners) + 2
    top, bottom = min(c[1] for c in corners) - 2, max(c[1] for c in corners) + 2
    samples = []
    for x in range(left, right + 1, max(1, (right - left) // 40)):
        samples += [(x, top), (x, bottom)]
    for y in range(top, bottom + 1, max(1, (bottom - top) // 10)):
        samples += [(left, y), (right, y)]
    pixels = [image.getpixel((x, y)) for x, y in samples if 0 <= x < width and 0 <= y < height]
    if not pixels:
        return (1, 1, 1)
    return tuple(statistics.median(p[i] for p in pixels) / 255 for i in range(3))


def _rows(textpage, chars):
    """The letters grouped into lines, top to bottom: [[index]]."""
    rows = []
    for i in chars:
        y = _origin(textpage, i)[1]
        size = max(1.0, _raw().FPDFText_GetFontSize(textpage.raw, i))
        for row in rows:
            if abs(row[0] - y) < size * 0.35:
                row[1].append(i)
                break
        else:
            rows.append([y, [i]])
    return [members for _, members in sorted(rows, key=lambda r: -r[0])]


def _rows_with_marks(textpage, chars):
    """:func:`_rows`, with small letters set above or below a line (a
    footnote mark, the 2 of H2O) counted with that line, not as a line of
    their own."""
    raw = _raw()
    found = []
    for members in _rows(textpage, chars):
        ys = sorted(_origin(textpage, i)[1] for i in members)
        sizes = sorted(max(1.0, raw.FPDFText_GetFontSize(textpage.raw, i)) for i in members)
        found.append([ys[len(ys) // 2], sizes[len(sizes) // 2], list(members)])
    for row in sorted(found, key=lambda r: r[1]):  # the smallest first
        hosts = [h for h in found if h is not row and h[2] and row[1] <= h[1] * 0.85 and abs(row[0] - h[0]) < h[1] * 0.6]
        if hosts:
            host = min(hosts, key=lambda h: abs(row[0] - h[0]))
            host[2] += row[2]
            row[2] = []
    return [members for _, _, members in found if members]


def _set_rises(rows):
    """Give each letter of the rows (of :func:`_letters`) its "rise": how
    far above (below: negative) its line's baseline it is set, 0 for the
    line's own letters."""
    for row in rows:
        down = _down(row[0]["style"]["matrix"])
        heights = [-(L["origin"][0] * down[0] + L["origin"][1] * down[1]) for L in row]
        usual = max({round(L["style"]["size"], 2) for L in row},
                    key=lambda size: sum(len(L["char"]) for L in row if round(L["style"]["size"], 2) == size))
        base = statistics.median(h for h, L in zip(heights, row) if round(L["style"]["size"], 2) == usual)
        for h, L in zip(heights, row):
            rise = h - base
            L["rise"] = round(rise, 2) if abs(rise) > 0.06 * L["style"]["size"] else 0.0


# ---- where the letters are ---------------------------------------------------------------

class _Covers:
    """Opaque boxes drawn over text, as an earlier change does over the old
    words (they stay in the file underneath): letters under one are hidden,
    and a change works on the words that show."""

    LIMIT = 20000  # page objects: past this (a detailed drawing) nothing is looked for

    def __init__(self, pdf_page):
        self.order, self.boxes = {}, []  # text object → place in the drawing order; [(place, box)]
        raw = _raw()
        try:
            count = raw.FPDFPage_CountObjects(pdf_page.raw)
            if count > self.LIMIT:
                return
            for i in range(count):
                self._walk(raw, raw.FPDFPage_GetObject(pdf_page.raw, i), i, 0)
        except (AttributeError, OSError, ValueError):  # an older pdfium: nothing hidden
            self.order, self.boxes = {}, []

    def _walk(self, raw, obj, place, depth):
        kind = raw.FPDFPageObj_GetType(obj)
        if kind == raw.FPDF_PAGEOBJ_TEXT:
            self.order[ctypes.cast(obj, ctypes.c_void_p).value] = place
        elif kind == raw.FPDF_PAGEOBJ_FORM and depth < 6:
            for j in range(raw.FPDFFormObj_CountObjects(obj)):
                self._walk(raw, raw.FPDFFormObj_GetObject(obj, j), place, depth + 1)
        elif kind == raw.FPDF_PAGEOBJ_PATH:
            fill, stroke = ctypes.c_int(), ctypes.c_int()
            if not raw.FPDFPath_GetDrawMode(obj, ctypes.byref(fill), ctypes.byref(stroke)) or not fill.value:
                return
            rgba = [ctypes.c_uint() for _ in range(4)]
            if not raw.FPDFPageObj_GetFillColor(obj, *map(ctypes.byref, rgba)) or rgba[3].value < 255:
                return
            if not 1 <= raw.FPDFPath_CountSegments(obj) <= 6:  # a plain box, not a drawing
                return
            box = [ctypes.c_float() for _ in range(4)]
            if raw.FPDFPageObj_GetBounds(obj, *map(ctypes.byref, box)):
                self.boxes.append((place, tuple(v.value for v in box)))

    def hides(self, textpage, index, box):
        """Whether the letter (its box given) is under a box drawn after it."""
        if not self.boxes:
            return False
        obj = _raw().FPDFText_GetTextObject(textpage.raw, index)
        place = self.order.get(ctypes.cast(obj, ctypes.c_void_p).value) if obj else None
        if place is None:
            return False
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        return any(p > place and b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for p, b in self.boxes)


def _across(style, a, b):
    """How far entry ``b`` is from the line through entry ``a``, across it."""
    u = _unit(style["matrix"])
    return (b[2] - a[2]) * u[0] - (b[1] - a[1]) * u[1]


def _unit(matrix):
    a, b = matrix[:2]
    n = math.hypot(a, b) or 1.0
    return a / n, b / n


def _down(matrix):
    """The direction from a line to the next one below it."""
    c, d = matrix[2:]
    n = math.hypot(c, d) or 1.0
    return -c / n, -d / n


def _advance(style, char, loose, u):
    """How far the letter moves the pen along the line, in points, without
    any extra letter spacing."""
    if abs(u[1]) < 1e-6 and u[0] > 0:
        return loose[2] - loose[0]  # pdfium's loose box is the letter's advance
    return _font_advance(style, char)


def _font_advance(style, char):
    width = ctypes.c_float()
    try:
        if style["handle"] and _raw().FPDFFont_GetGlyphWidth(style["handle"], ord(char[0]), style["raw_size"],
                                                              ctypes.byref(width)) and width.value > 0:
            return width.value * (math.hypot(*style["matrix"][:2]) or 1.0)
    except (AttributeError, TypeError, ValueError, OverflowError):
        pass
    return style["size"] * 0.5


def _letters(textpage, chars, cache, covers=None):
    """The letters among ``chars`` that show (spaces left out), in reading
    order along the line: [{char, origin, s, adv, loose, tight, style,
    space_before}], with ``s`` each one's distance along the line. Or None."""
    letters, spaces = [], []
    for i in chars:
        char = textpage.get_text_range(i, 1)
        loose = textpage.get_charbox(i, loose=True)
        if covers is not None and covers.hides(textpage, i, loose):
            continue  # covered by an earlier change
        if not char or char.isspace():
            spaces.append(_origin(textpage, i))
            continue
        letters.append({"index": i, "char": char, "origin": _origin(textpage, i),
                        "loose": loose, "tight": textpage.get_charbox(i),
                        "style": _style(textpage, i, cache)})
    if not letters:
        return None
    u = _unit(letters[0]["style"]["matrix"])
    for letter in letters:
        letter["s"] = letter["origin"][0] * u[0] + letter["origin"][1] * u[1]
        letter["adv"] = _advance(letter["style"], letter["char"], letter["loose"], u)
    letters.sort(key=lambda L: L["s"])
    merged = []  # two letters at one spot: one glyph read as two (a ligature, "ﬁ" → "fi")
    for letter in letters:
        if merged and abs(letter["s"] - merged[-1]["s"]) < 0.01 and letter["loose"] == merged[-1]["loose"]:
            merged[-1]["char"] += letter["char"]
        else:
            merged.append(letter)
    letters = merged
    space_s = [x * u[0] + y * u[1] for x, y in spaces]
    for a, b in zip(letters, letters[1:]):
        b["explicit_space"] = any(a["s"] < s < b["s"] for s in space_s)
    letters[0]["explicit_space"] = False
    return letters


def _spacing(rows):
    """The letter spacing and word spacing of lines of letters, in points:
    (between letters, between words or None). Measured: what's left between
    one letter's advance and the next letter."""
    pairs = [(a, b) for line in rows for a, b in zip(line, line[1:])]
    gaps = [b["s"] - (a["s"] + a["adv"]) for a, b in pairs]
    plain = [g for g, (_, b) in zip(gaps, pairs) if not b["explicit_space"]]
    typical = statistics.median(plain) if plain else 0.0
    words, between = [], []
    for g, (a, b) in zip(gaps, pairs):
        b["space_before"] = b["explicit_space"] or g > typical + 0.2 * a["style"]["size"]
        (words if b["space_before"] else between).append(g)
    for line in rows:
        line[0]["space_before"] = False
    size = statistics.median(L["style"]["size"] for line in rows for L in line)
    tc = statistics.median(between) if between else 0.0
    tc = min(max(tc, -0.15 * size), 0.8 * size)
    word = statistics.median(words) if words else None
    return tc, word


def _text_of(letters):
    """The line as text, a space between words."""
    return "".join((" " if L.get("space_before") else "") + L["char"] for L in letters)


def _nonspace(text):
    return sum(1 for ch in text if not ch.isspace())


def _take(letters, count):
    """How many letters hold the first ``count`` characters (spaces left
    out), or None when that ends inside one letter (a ligature)."""
    n = 0
    for k, letter in enumerate(letters):
        if n == count:
            return k
        n += len(letter["char"])
        if n > count:
            return None
    return len(letters) if n == count else None


# ---- the document's own fonts ----------------------------------------------------------

def _page_fonts(page):
    """Every font the page's text can be drawn with: [(font dictionary)],
    with those of the forms (XObjects) it draws."""
    found, seen = [], set()

    def walk(resources, depth=0):
        if resources is None or depth > 6:
            return
        resources = resources.get_object()
        for font in (resources.get("/Font") or {}).values():
            ref = getattr(font, "idnum", None)
            if ref is None or ref not in seen:
                seen.add(ref)
                found.append(font)
        for xobject in (resources.get("/XObject") or {}).values():
            obj = xobject.get_object()
            if obj.get("/Subtype") == "/Form" and getattr(xobject, "idnum", None) not in seen:
                seen.add(getattr(xobject, "idnum", None))
                walk(obj.get("/Resources"), depth + 1)

    walk(page.get("/Resources"))
    return found


def _names_of(font):
    font = font.get_object()
    names = {fonts.strip_subset(font.get("/BaseFont", ""))}
    if font.get("/Subtype") == "/Type0":
        base = fonts.strip_subset(font.get("/BaseFont", ""))
        names.add(base.rsplit("-Identity", 1)[0])
        try:
            names.add(fonts.strip_subset(font["/DescendantFonts"][0].get_object().get("/BaseFont", "")))
        except (KeyError, IndexError):
            pass
    return names


class _OwnFont:
    """Writing with one of the document's fonts."""

    def __init__(self, font, codes):
        obj = font.get_object()
        if obj.get("/Subtype") != "/Type0" and obj.get("/Widths") is not None:
            # A code outside the font's table of widths has no width a reader
            # can be counted on to agree about: its letter isn't written.
            first, count = int(obj.get("/FirstChar", 0)), len(obj["/Widths"])
            codes = {letter: code for letter, code in codes.items() if first <= code[0] < first + count}
        self.font, self.codes, self.width_of = font, codes, _widths(font)
        space = codes.get(" ")
        if space is not None and any(space) and self.width_of(space) > 0:
            self.space = None  # the font has a space
        else:
            mono = fonts.strip_subset(font.get_object().get("/BaseFont", "")).lower()
            self.space = 600 if any(n in mono for n in fonts.MONO_NAMES) else 250

    def can_write(self, text):
        return all(ch in self.codes or ch == " " for ch in text)

    def width(self, text):
        """In thousandths of the font size."""
        return sum(self.space if ch == " " and self.space else self.width_of(self.codes[ch]) for ch in text)

    def operands(self, text):
        """The array for TJ."""
        parts, run = [], b""
        for ch in text:
            if ch == " " and self.space:
                if run:
                    parts.append("<" + run.hex() + ">")
                    run = b""
                parts.append(str(-self.space))
            else:
                run += self.codes[ch]
        if run:
            parts.append("<" + run.hex() + ">")
        return "[" + " ".join(parts) + "]"


def _own_font(page, style, text):
    """The document's font the words were drawn with, if it can write ``text``."""
    if not style["font"] or not style["handle"]:
        return None
    wanted = fonts.strip_subset(style["font"])
    matches = [f for f in _page_fonts(page) if wanted in _names_of(f)]
    if style["data"] and len(matches) > 1:  # copies cut down differently: the one drawn
        exact = [f for f in matches if fonts.font_program(f) == style["data"]]
        matches = exact + [f for f in matches if f not in exact]
    for font in matches:
        try:
            own = _OwnFont(font, fonts.pdf_font_codes(font))
        except Exception:  # noqa: BLE001 - a font dictionary we can't read
            continue
        if not (own.codes and own.can_write(text)):
            continue
        # A font that isn't in the file is drawn with the reader's own copy,
        # which has every letter its encoding names; one in the file may be
        # cut down to the letters used, so each is checked for a shape.
        whole = fonts.font_program(font) is None and not _is_embedded(style["handle"])
        if whole or all(_has_letter(style["handle"], ch) for ch in set(text) if ch != " "):
            if not whole and own.space is None and not _draws_space(style):
                # No space to draw in this font: the words are set apart by moving the pen.
                own.space = own.width_of(own.codes[" "]) or 250
            return own
    return None


def _private(ch):
    """Whether ``ch`` is a private-use code point: it means whatever one
    font's maker chose (an icon in one, a logo in another), so only the
    document's own font can stand for it."""
    code = ord(ch)
    return 0xE000 <= code <= 0xF8FF or code >= 0xF0000


def _can_draw(font, ch):
    """Whether a chosen font (the document's own, or one known to
    reportlab by name) has the letter."""
    own, name = font[:2]
    if own:
        return ch in own.codes
    if _private(ch):
        return False
    if name in READER_FONTS:
        return False
    if name in BASE14_NAMES:
        try:
            ch.encode("cp1252")
            return True
        except UnicodeEncodeError:
            return False
    try:
        from reportlab.pdfbase import pdfmetrics

        return ord(ch) in pdfmetrics.getFont(name).face.charToGlyph
    except Exception:  # noqa: BLE001 - a font reportlab can't be asked about
        return True


def _is_embedded(handle):
    try:
        return bool(_raw().FPDFFont_GetIsEmbedded(handle))
    except (AttributeError, OSError):
        return True


def _similar(letters, kind, bold, italic):
    """A font like the old words' for letters their own font lacks: a
    standard one when it has them; else an installed one of the same kind
    and weight (serif for serif, monospace for monospace, bold for bold),
    before any font that merely has the letters."""
    name = fonts.font_for(letters or "a", kind, bold, italic)
    if name in BASE14_NAMES or (kind == "sans" and not bold and not italic):
        return name
    for family in fonts.EMBEDDABLE.get(kind, ()):
        like = fonts.installed_family(family, bold, italic, letters)
        if like:
            return like
    if bold or italic:  # the right kind at least, if not the weight
        for family in fonts.EMBEDDABLE.get(kind, ()) if kind != "sans" else ():
            like = fonts.installed_family(family, False, False, letters)
            if like:
                return like
    return name


# ---- reading the edits ---------------------------------------------------------------------

def _parse(edits, pages):
    if not isinstance(edits, (list, tuple)):
        raise core.PdfManagerError("the changes should be a list")
    by_page = {}
    for edit in edits:
        try:
            page = int(edit.get("page", 0))
            if "at" in edit:  # new words at a point
                x, y = (float(v) for v in edit["at"])
                rect, kind = (x, y, x, y), "add"
            else:
                x0, y0, x1, y1 = (float(v) for v in edit["rect"])
                if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
                    raise core.PdfManagerError("the box around the words isn't on the page")
                rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                kind = "paragraph" if edit.get("paragraph") else "line"
            text = edit.get("text")
            old = edit.get("old")
        except (AttributeError, KeyError, TypeError, ValueError):
            raise core.PdfManagerError("a change needs a page, the box around the words "
                                       "(four numbers) and the new words") from None
        if not all(math.isfinite(v) for v in rect):
            raise core.PdfManagerError("the box around the words isn't on the page")
        if not 1 <= page <= pages:
            raise core.PdfManagerError(f"page {page} doesn't exist")
        by_page.setdefault(page, []).append(
            (kind, rect, "" if text is None else str(text), old if isinstance(old, str) else None, _options(edit)))
    if not by_page:
        raise core.PdfManagerError("no changes to make")
    return by_page


ALIGNS = ("left", "center", "right", "justify")


LOOKS = ("font", "size", "bold", "italic", "color", "align")  # the reader's choices of how words look


def _options(edit):
    """The reader's choices for one change, checked: {font, size, bold,
    italic, color, align, width, fit, flow}, only those given."""
    opts = {}
    font = edit.get("font")
    if font not in (None, ""):
        if not isinstance(font, str) or len(font) > 120 or not font.strip().isprintable():
            raise core.PdfManagerError("the font's name isn't one that can be used")
        opts["font"] = font.strip()
    for key, low, high in (("size", 1.0, 500.0), ("width", 1.0, 20000.0)):
        if edit.get(key) is not None:
            try:
                value = float(edit[key])
            except (TypeError, ValueError):
                raise core.PdfManagerError(f"the {key} should be a number") from None
            if not low <= value <= high:
                raise core.PdfManagerError(f"the {key} should be between {low:g} and {high:g}")
            opts[key] = value
    for key in ("bold", "italic", "flow", "styles", "hyphens", "indent", "keep_unchanged", "wrap_pictures"):
        if edit.get(key) is not None:
            opts[key] = bool(edit[key])
    if edit.get("color") is not None:
        try:
            color = tuple(float(v) for v in edit["color"])
        except (TypeError, ValueError):
            color = ()
        if len(color) != 3 or not all(0 <= v <= 1 for v in color):
            raise core.PdfManagerError("the colour should be three numbers from 0 to 1")
        opts["color"] = color
    if edit.get("align") not in (None, ""):
        if edit["align"] not in ALIGNS:
            raise core.PdfManagerError("the alignment should be left, center, right or justify")
        opts["align"] = edit["align"]
    if edit.get("runs") is not None:
        runs, text = edit["runs"], edit.get("text")
        if not isinstance(runs, list) or len(runs) > 5000 or not all(
                isinstance(r, dict) and isinstance(r.get("text"), str) for r in runs):
            raise core.PdfManagerError("the words' styles are damaged; make the change again")
        if "".join(r["text"] for r in runs) != (text if isinstance(text, str) else ""):
            raise core.PdfManagerError("the words' styles don't match the words; make the change again")
        opts["runs"] = [(bool(r.get("bold")), bool(r.get("italic"))) for r in runs for _ in r["text"]]
    if edit.get("breaks") not in (None, ""):
        if edit["breaks"] != "simple":
            raise core.PdfManagerError('breaks can only be "simple"')
        opts["breaks"] = "simple"
    if edit.get("fit") not in (None, ""):
        if edit["fit"] not in ("shrink", "next_page", "add_page"):
            raise core.PdfManagerError('fit can only be "shrink", "next_page" or "add_page"')
        opts["fit"] = edit["fit"]
    return opts


def _restyle(style, opts):
    """A copy of ``style`` with the reader's size, colour, bold and italic."""
    style = dict(style)
    if opts.get("size"):
        style["raw_size"] = opts["size"] / (math.hypot(*style["matrix"][:2]) or 1.0)
        style["size"] = opts["size"]
    if "color" in opts:
        style["color"] = tuple(opts["color"])
    for key in ("bold", "italic"):
        if key in opts:
            style[key] = opts[key]
    return style


# ---- laying out the words -----------------------------------------------------------------

def _choose(page, style, text):
    """The font to write ``text`` with: (the document's own font or None,
    else a font name for reportlab), which kind it is and its name to show."""
    letters = "".join(ch for ch in text if not ch.isspace())
    own = _own_font(page, style, letters)
    if own:
        return own, None, SAME, fonts.display_name(style["font"])
    letters = "".join(ch for ch in letters if not _private(ch))  # no other font may stand in for those
    name = fonts.installed_font(style["font"], style["bold"], style["italic"], letters) \
        if style["font"] else None
    if name:
        return None, name, INSTALLED, fonts.display_name(style["font"])
    name = _similar(letters, style["family"], style["bold"], style["italic"])
    return None, name, SIMILAR, fonts.display_name(fonts.file_name(name))


def _metrics(own, name, style):
    """(width of a letter, width of a space), in points along the line."""
    scale = (math.hypot(*style["matrix"][:2]) or 1.0) * style["raw_size"]
    if own:
        def width(ch):
            return sum(own.width_of(own.codes[c]) for c in ch) / 1000 * scale
        return width, (own.space or own.width_of(own.codes[" "])) / 1000 * scale
    from reportlab.pdfbase import pdfmetrics

    def width(ch):
        return pdfmetrics.stringWidth(ch, name, 1000) / 1000 * scale
    return width, width(" ")


def _place(text, start, width, tc, gap):
    """Where the letters of ``text`` go, set from ``start`` along the line:
    ([(letter, s)], where the pen ends). ``tc`` is the extra space after
    each letter, ``gap`` the space between words (spaces are in the list
    too, for copying the words; nothing is drawn for them)."""
    pen, placed = start, []
    for ch in text:
        if ch.isspace():
            placed.append((" ", pen))
            pen += gap - tc
        else:
            placed.append((ch, pen))
            pen += width(ch) + tc
    return placed, pen


FIT_LIMIT = 2000  # words: a longer paragraph is broken line by line


def _fit(widths, gap, room, justify=False, joins=None):
    """Where to break a paragraph's pieces into lines no wider than ``room``,
    looking at the whole paragraph (as a typesetter's "total fit" does):
    [(first piece, one past the last)] per line. A piece is a word, or part
    of one: ``joins[i]`` says what follows piece i, None for a space, or
    ("soft", the width of the hyphen a break there adds) inside a word that
    may be divided, or ("hard", 0) after a hyphen the word has itself.
    Never more lines than breaking line by line gives; among those, the
    most even: in justified text the word gaps stretch as little and as
    alike as they can, in ragged text the line ends are close to each
    other, the last line isn't left with a lone or very short word when
    the lines above can spare one, and words are divided sparingly (never
    on two lines running when it can be helped). A piece wider than the
    room gets a line to itself."""
    n = len(widths)
    if n == 0:
        return [(0, 0)]
    joins = joins or [None] * n
    varies = callable(room)
    best = [(0, 0.0, -1)] + [None] * n  # for the first j pieces: (lines, demerits, where the last line starts)
    for j in range(1, n + 1):
        end = joins[j - 1] if j < n else None  # how this line would end
        natural, spaces = (end[1] if end else 0.0), 0
        for i in range(j - 1, -1, -1):
            natural += widths[i]
            if i < j - 1 and joins[i] is None:
                natural += gap
                spaces += 1
            if best[i] is None:
                if not varies and natural > room + 1e-9 and i < j - 1:
                    break
                continue
            r = room(best[i][0]) if varies else room
            if natural > r + 1e-9 and i < j - 1:
                if not varies:
                    break
                continue
            slack, last = max(r - natural, 0.0), j == n
            if last:
                cost = 0.0
                if n > 1 and (j - i == 1 or natural < r * 0.12):
                    cost = 2.0  # a lone or very short word left over
            elif not spaces:
                # One word (or part of one) alone on a line that isn't the last:
                # right when it nearly fills the line, never to tidy the others.
                cost = 10.0 + 1e4 * (slack / r)
            elif justify:
                cost = (slack / spaces / gap) ** 2 if gap > 0 else 10.0 + (slack / r) ** 2
            else:
                cost = (slack / r) ** 2
            if end:
                cost += 0.6 if end[0] == "soft" else 0.2
                if i > 0 and joins[i - 1] and end[0] == "soft" and joins[i - 1][0] == "soft":
                    cost += 3.0  # the line above ends in a divided word too
            trial = (best[i][0] + 1, best[i][1] + cost, i)
            if best[j] is None or trial[:2] < best[j][:2]:
                best[j] = trial
    lines, j = [], n
    while j > 0:
        i = best[j][2]
        lines.append((i, j))
        j = i
    return lines[::-1]


_CORE = re.compile(r"(\W*)(.*?)(\W*)\Z", re.S)


def _look(style):
    """What a reader sees of a style: two letters with the same look are
    written the same way."""
    return (style["font"].split("+")[-1], round(style["size"], 2), tuple(round(v, 3) for v in style["color"]),
            style["bold"], style["italic"])


def _old_words(rows, link_of=None):
    """The old paragraph's words, each a list of (letter, (style, link,
    rise)), in order; ``link_of(letter)`` is the link a letter is in (None:
    none), the rise how far above its line's baseline it is set."""
    words = []
    for row in rows:
        word = []
        for a, b in zip([None] + row[:-1], row):
            if a is not None and (b.get("explicit_space") or b["s"] - (a["s"] + a["adv"]) > 0.2 * b["style"]["size"]):
                words.append(word)
                word = []
            attr = (b["style"], link_of(b) if link_of else None, b.get("rise", 0.0))
            word += [(ch, attr) for ch in b["char"]]
        if word:
            words.append(word)
    return words


def _attr_look(attr):
    return _look(attr[0]) + (round(attr[2], 1), attr[1])


def _char_styles(rows, text, link_of=None):
    """The (style, link) of each character of ``text``, the paragraph's new
    words, taken from the old words as a word processor would keep them:
    words that are still there keep their own style (and link), a changed
    word keeps the style of the letters it replaced, and new words take
    the style of the word before them. None when every letter would look
    the same (one style throughout, and no links)."""
    old = _old_words(rows, link_of)
    if len({_attr_look(st) for word in old for _, st in word}) < 2:
        return None
    spans, i = [], 0  # the new words: (start, end) in text
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        j = i
        while j < len(text) and not text[j].isspace():
            j += 1
        spans.append((i, j))
        i = j
    new = [text[a:b] for a, b in spans]
    out = [None] * len(text)
    matcher = difflib.SequenceMatcher(None, ["".join(ch for ch, _ in w) for w in old], new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for w, (a, _) in zip(old[i1:i2], spans[j1:j2]):
                for n, (_, st) in enumerate(w):
                    out[a + n] = st
        elif tag == "replace":
            was = [pair for w in old[i1:i2] for pair in w]
            chars = [(k, text[k]) for a, b in spans[j1:j2] for k in range(a, b)]
            inner = difflib.SequenceMatcher(None, "".join(ch for ch, _ in was), "".join(ch for _, ch in chars),
                                            autojunk=False)
            for t, a1, a2, b1, b2 in inner.get_opcodes():
                for n in range(b2 - b1):
                    if t == "equal":
                        st = was[a1 + n][1]
                    elif a2 > a1:  # in place of other letters: theirs, in proportion
                        st = was[min(a2 - 1, a1 + n * (a2 - a1) // (b2 - b1))][1]
                    else:  # typed in: the letter before's
                        st = None
                    out[chars[b1 + n][0]] = st
    first = old[0][0][1] if old and old[0] else None
    last = None
    for k, st in enumerate(out):  # new words, spaces and line breaks: the style of what's before them
        if st is None:
            out[k] = last or first
        last = out[k] if not text[k].isspace() or last is None else last
    for k in range(len(out) - 2, -1, -1):  # a space after a raised mark is the line's, not the mark's
        if text[k].isspace() and out[k][2] and not out[k + 1][2]:
            out[k] = out[k + 1]
    if len({_attr_look(st) for st, ch in zip(out, text) if not ch.isspace()}) < 2 and not any(
            a[1] is not None for word in old for _, a in word):
        return None
    return out


MARKUPS = ("/Link", "/Highlight", "/Underline", "/StrikeOut", "/Squiggly")  # annotations that go with words


def _link_at(links, letter):
    """Which of ``links`` ([(annotation, its boxes)]: links and text
    markups) a letter's middle is in: their indices as a tuple, or None."""
    l, b, r, t = letter["tight"]
    x, y = (l + r) / 2, (b + t) / 2
    found = tuple(n for n, (_, boxes) in enumerate(links)
                  if any(x0 - 0.5 <= x <= x1 + 0.5 and y0 - 0.5 <= y <= y1 + 0.5 for x0, y0, x1, y1 in boxes))
    return found or None


def _box(letters, line=None, before=None, after=None):
    """The box covering ``letters``, kept off the letters left as they are
    on either side."""
    boxes = [L["loose"] for L in letters]
    x0, y0 = min(b[0] for b in boxes) - PAD, min(b[1] for b in boxes) - PAD
    x1, y1 = max(b[2] for b in boxes) + PAD, max(b[3] for b in boxes) + PAD
    u = _unit(letters[0]["style"]["matrix"])
    # No taller than the letters' own shapes (their loose boxes are the
    # font's full height): lines set close above and below aren't clipped.
    shapes = [L["tight"] for L in letters]
    if abs(u[1]) < 1e-6 and all(t[2] > t[0] and t[3] > t[1] for t in shapes):
        y0 = max(y0, min(t[1] for t in shapes) - PAD)
        y1 = min(y1, max(t[3] for t in shapes) + PAD)
        x0 = min(x0, min(t[0] for t in shapes) - PAD)
        x1 = max(x1, max(t[2] for t in shapes) + PAD)
    if abs(u[1]) < 1e-6 and u[0] > 0:
        if before and before["tight"][2] > before["tight"][0]:
            x0 = max(x0, min(before["tight"][2], letters[0]["loose"][0]))
        if after and after["tight"][2] > after["tight"][0]:
            x1 = min(x1, max(after["tight"][0], letters[-1]["loose"][2]))
    return (x0, y0, x1, y1)


def _face(font):
    """(bold, italic) of a PDF font, from its name and its descriptor."""
    bold, italic = fonts.font_style(font.get("/BaseFont", ""))
    try:
        descriptor = font["/DescendantFonts"][0].get_object().get("/FontDescriptor") \
            if font.get("/Subtype") == "/Type0" else font.get("/FontDescriptor")
        descriptor = descriptor.get_object() if descriptor is not None else {}
        flags = int(descriptor.get("/Flags", 0))
        bold = bold or float(descriptor.get("/FontWeight", 400)) >= 600 or bool(flags & (1 << 18))
        italic = italic or float(descriptor.get("/ItalicAngle", 0)) != 0 or bool(flags & 64)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        pass
    return bold, italic


def _same_colour(a, b):
    return all(abs(x - y) < 0.004 for x, y in zip(a, b))


def _rtl(text):
    """Whether any of the letters is written right to left (Hebrew, Arabic)."""
    return any(unicodedata.bidirectional(ch) in ("R", "AL", "AN") for ch in text)


def _embedded(writer, name, letters):
    """A font registered with reportlab (a standard one, or one installed
    on this computer) as a font of the document being written, holding
    ``letters``: an :class:`_OwnFont`, or None."""
    wanted = "".join(sorted(ch for ch in letters if not ch.isspace()))
    if not wanted:
        return None
    try:
        def draw(c, w, h):
            c.setFont(name, 10)
            c.drawString(10, 10, wanted + " ")

        made = core._overlay_page(200, 200, draw)
        found = made["/Resources"]["/Font"]
        for font in found.values():
            base = str(font.get_object().get("/BaseFont", "")).lstrip("/")
            if base == "Helvetica" and name != "Helvetica":
                continue  # reportlab's page always lists it
            codes = fonts.pdf_font_codes(font)
            if all(ch in codes for ch in wanted):
                ref = font.clone(writer)
                if getattr(ref, "idnum", None) is None:
                    ref = writer._add_object(ref)
                return _OwnFont(ref, fonts.pdf_font_codes(ref))
    except Exception:  # noqa: BLE001 - not a font that can be put in the file this way
        return None
    return None


def _skip(text, count):
    """``text`` after its first ``count`` letters (spaces not counted)."""
    for k, ch in enumerate(text):
        if count == 0:
            return text[k:]
        count -= not ch.isspace()
    return ""


class _Unsure(Exception):
    """The new words can't be matched up with the old letters."""


def _is_pdfa(reader):
    """Whether the file says it is PDF/A (its fonts must all be embedded)."""
    try:
        xmp = reader.xmp_metadata
        raw = xmp.stream.get_data().decode("utf-8", "replace") if xmp is not None else ""
    except Exception:  # noqa: BLE001 - unreadable metadata: not PDF/A as far as can be told
        return False
    return "pdfaid:part" in raw

