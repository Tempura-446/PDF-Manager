"""Change words already on a page, rewrite a paragraph, or add words.

Text in a PDF isn't stored as editable paragraphs: it's drawn letter by
letter, usually with fonts that only contain the letters used. So, like
most PDF editors do, a change covers the old words with a box in the colour
around them and writes the words again on top. The old words are still in
the file underneath (copying or searching can find them); to remove text
for good, use Redact.

The words stay where they were. Each letter of a changed line that wasn't
changed is drawn again exactly where it was (with its own font, size and
colour, so a bold word stays bold); the new letters go where the old ones
began, spaced like the rest of the line (its letter and word spacing are
measured, not assumed); and the letters after them move along by exactly
the difference in width. A rewritten paragraph keeps its left edge, line
spacing and width, and stays justified if it was.

The new words keep the original look as far as the file allows:

1. the document's own font, when it has every letter needed (fonts cut
   down to the letters a document uses often do, for words made of them);
2. else the same font installed on this computer (Calibri, Times New
   Roman, ...), matched by name;
3. else a standard font of the same kind (serif, sans serif or monospace,
   bold, italic).

The reader can also choose the look: a font family (one of the
document's, an installed one, or a standard one), size, bold, italic,
colour and alignment, for the whole line, paragraph or new text. A font
from this computer is embedded, cut down to the letters used; in a PDF/A
file, which may not rely on a reader's own fonts, a standard font gives
way to an installed one that is embedded.

A rewritten paragraph that takes more or fewer lines moves the text below
it in its column (see textflow.py): text flows, as in a word processor.
"""

import ctypes
import math
import re
import statistics
import unicodedata

from pypdf import Transformation
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

from pdfmanager import core, fonts

PAD = 0.6  # points added around the old words' box
SAME, INSTALLED, SIMILAR = "same", "installed", "similar"  # which font the words got
STANDARD = "standard"  # a standard PDF font the reader picked
BASE14_NAMES = set(fonts.BASE14.values())
MOVED = 0.005  # points: letters after a change that move less than this stay as they are


def _raw():
    import pypdfium2.raw as raw
    return raw


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
    }
    return style


def _origin(textpage, index):
    x, y = ctypes.c_double(), ctypes.c_double()
    _raw().FPDFText_GetCharOrigin(textpage.raw, index, ctypes.byref(x), ctypes.byref(y))
    return x.value, y.value


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


def _widths(font):
    """A function giving a code's width in thousandths of the font size."""
    font = font.get_object()
    if font.get("/Subtype") == "/Type0":
        try:
            cid = font["/DescendantFonts"][0].get_object()
        except (KeyError, IndexError):
            return lambda code: 500
        default, table = float(cid.get("/DW", 1000)), {}
        w = list(cid.get("/W", []))
        i = 0
        while i + 1 < len(w):
            first = int(w[i])
            if isinstance(w[i + 1], list):
                for k, width in enumerate(w[i + 1]):
                    table[first + k] = float(width)
                i += 2
            elif i + 2 < len(w):
                for c in range(first, int(w[i + 1]) + 1):
                    table[c] = float(w[i + 2])
                i += 3
            else:
                break
        return lambda code: table.get(int.from_bytes(code, "big"), default)
    first = int(font.get("/FirstChar", 0))
    widths = [float(v) for v in font.get("/Widths", [])]
    descriptor = font.get("/FontDescriptor")
    missing = float(descriptor.get_object().get("/MissingWidth", 0) or 0) if descriptor is not None else 0.0
    if not widths:  # a standard font without widths in the file
        name = fonts.strip_subset(font.get("/BaseFont", "Helvetica"))
        try:
            from reportlab.pdfbase import pdfmetrics
            pdfmetrics.getFont(name)
            return lambda code: pdfmetrics.stringWidth(code.decode("cp1252", "replace"), name, 1000)
        except Exception:  # noqa: BLE001 - not a standard font
            return lambda code: 500
    return lambda code: widths[code[0] - first] if 0 <= code[0] - first < len(widths) else missing


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
        if space is not None and self.width_of(space) > 0:
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
            return own
    return None


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
    for key in ("bold", "italic", "flow"):
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
    if edit.get("fit") not in (None, ""):
        if edit["fit"] != "shrink":
            raise core.PdfManagerError('fit can only be "shrink"')
        opts["fit"] = "shrink"
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


def _wrap(text, room, measure):
    """Lines of one paragraph (no line breaks in it) no wider than ``room``."""
    lines, line = [], ""
    for word in text.split(" "):
        trial = word if not line else line + " " + word
        if line and measure(trial) > room:
            lines.append(line)
            line = word
        else:
            line = trial
    lines.append(line)
    return lines


def _box(letters, line=None, before=None, after=None):
    """The box covering ``letters`` (of the whole ``line`` across it), kept
    off the letters left as they are on either side."""
    boxes = [L["loose"] for L in letters]
    x0, y0 = min(b[0] for b in boxes) - PAD, min(b[1] for b in boxes) - PAD
    x1, y1 = max(b[2] for b in boxes) + PAD, max(b[3] for b in boxes) + PAD
    u = _unit(letters[0]["style"]["matrix"])
    if line and abs(u[1]) < 1e-6:  # across a level line: the line's full height
        y0 = min(y0, min(L["loose"][1] for L in line) - PAD)
        y1 = max(y1, max(L["loose"][3] for L in line) + PAD)
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


def _alignment(rows, left, right, word, natural, size):
    """How a paragraph's lines are set: "justify", "center", "right" or "left"."""
    tol = max(1.0, 0.1 * size)
    starts = [r[0]["s"] for r in rows]
    ends = [r[-1]["s"] + r[-1]["adv"] for r in rows]
    body = rows[:-1]
    aligned = bool(body) and all(abs(e - right) < tol for e in ends[:-1]) and all(abs(s - left) < tol for s in starts[:-1])
    if aligned and (len(body) >= 2 or (word is not None and word - natural > max(0.2, natural * 0.08))):
        return "justify"
    if len(rows) >= 2 and not all(abs(s - left) < tol for s in starts):
        middles = [(s + e) / 2 for s, e in zip(starts, ends)]
        if max(middles) - min(middles) < tol * 2:
            return "center"
        if all(abs(e - right) < tol for e in ends):
            return "right"
    return "left"


class _Page:
    """One page being changed: what pdfium reads of it and what gets drawn."""

    def __init__(self, pdf_page, page, doc=None, number=0, writer=None, pdfa=False):
        self.pdf_page, self.page = pdf_page, page
        self.doc, self.number, self.writer, self.pdfa = doc, number, writer, pdfa
        self.textpage = pdf_page.get_textpage()
        self.image = pdf_page.render(scale=1).to_pil().convert("RGB")
        self.cache = {}
        self.hidden = _Covers(pdf_page)
        self.covers = []  # (box, colour)
        self.notes = {}   # for the report on the change being made: wider, taller, moved, ...
        self.runs = []    # {style, own, name, entries: [(letter, x, y)]}
        self._flow, self._faces = None, {}
        self._segs = None
        self.spot = None    # where the change being made writes its new words in the drawing
        self.legacy = False  # the change being made is done the old way (covered and drawn on top)

    @property
    def flow(self):
        """The page's text as lines, for moving it (see textflow), or None."""
        if self._flow is None and self.writer is not None:
            from pdfmanager import textflow

            box = self.page.cropbox
            self._flow = textflow.Flow(self.page, self.writer, self.textpage, self.hidden,
                                       (float(box.left), float(box.bottom), float(box.right), float(box.top)))
        return self._flow

    def cover(self, box):
        self.covers.append((box, _background(self.pdf_page, self.image, box)))
        self.notes["covered"] = True  # the old words are still in the file, under the box

    # -- changing the page's own drawing ---------------------------------------------------
    def start(self):
        """Before each change."""
        self.notes, self.spot, self.legacy = {}, None, False
        self._first_run = len(self.runs)

    def take(self, rows, paragraph=False):
        """Take the old words (rows of letters) out of the page's drawing,
        and note where they were drawn, for the new ones to be written
        there. False (nothing changed) when that can't be done for sure:
        the old words are covered instead."""
        flow = self.flow
        if flow is None or self.legacy or not flow.content:
            return False
        everything = [L for row in rows for L in row]
        saved = flow.checkpoint()
        spot = flow.glyph(everything[0]) if flow.uncover(everything) else None
        if paragraph:
            done = flow.take_out(rows)
        else:
            done = spot is not None and flow.remove(everything)
        if not done:
            flow.rollback(saved)
            return False
        if spot is not None:
            self.spot = (spot, everything[0])
        return True

    def settle(self):
        """After each change: its new words go into the drawing where the
        old ones were (when those were taken out of it), not on top of the
        page."""
        if self.spot is None:
            return
        spot, first = self.spot
        left = []
        for run in self.runs[self._first_run:]:
            colour = None if _same_colour(run["style"]["color"], first["style"]["color"]) else run["style"]["color"]
            if not self.flow.insert(spot, True, {**run, "same": False, "color": colour}):
                left.append(run)
        self.runs[self._first_run:] = left

    def ready(self):
        """Fonts that aren't the document's own, for words written in the
        drawing: put in the file (cut down to the letters used), so the
        words are text like the rest: found by a search, copied in order."""
        if self._flow is None:
            return
        groups = {}
        for run in self._flow.pending():
            if run["own"] is None:
                groups.setdefault(run["name"], []).append(run)
        for name, runs in groups.items():
            letters = {c for run in runs for e in run["entries"] for c in e[0]}
            own = _embedded(self.writer, name, letters)
            for run in runs:
                if own is not None and own.can_write("".join(c for e in run["entries"] for c in e[0])):
                    run["own"] = own

    def run(self, style, font, entries):
        own, name = font[:2]
        entries = [e for e in entries if not e[0].isspace() or not (own and own.space)]
        if not any(not e[0].isspace() for e in entries):
            return
        last = self.runs[-1] if self.runs else None
        if last and last["style"] is style and last["own"] is not None and own is not None \
                and last["own"].font.get_object() is own.font.get_object() \
                and abs(_across(style, last["entries"][0], entries[0])) < 0.01:  # on the same line
            last["entries"] += entries  # one piece of text: copied, found and edited again as one
        else:
            self.runs.append({"style": style, "own": own, "name": name, "entries": entries})

    def close(self):
        self.textpage.close()
        self.pdf_page.close()

    # -- which font ----------------------------------------------------------------------
    def _choose(self, style, text):
        """:func:`_choose`, kept embedded in a PDF/A file."""
        return self._archival(_choose(self.page, style, text), style, text)

    def _font(self, style, base, text, opts):
        """The font for new words: the one asked for in ``opts`` (a family,
        or bold or italic other than ``base``, the old words' look), else
        like the old words."""
        letters = "".join(ch for ch in text if not ch.isspace())
        asked = opts.get("font")
        if asked is None and style["bold"] == base["bold"] and style["italic"] == base["italic"]:
            return self._choose(style, text)
        family = asked or fonts.family_name(base["font"]) or ""
        if family and fonts.family_key(family) == fonts.family_key(base["font"]):
            kind = style["family"]  # the old words' kind (a name like CMR10 doesn't tell)
        else:
            kind = fonts.kind_of(family)
        font = self._pick(family, kind, style, letters)
        font = self._archival(font, style, letters)
        if family:
            self.notes["asked"] = family
            self.notes["matched"] = font[2] in (SAME, INSTALLED, STANDARD) and "reason" not in self.notes
        return font

    def _pick(self, family, kind, style, letters):
        bold, italic = style["bold"], style["italic"]
        key = fonts.family_key(family) if family else ""
        if key:
            own = self._document_font(key, bold, italic, letters)
            if own:
                return own, None, SAME, fonts.display_name(own.font.get_object().get("/BaseFont", family))
            name = fonts.installed_family(family, bold, italic, letters)
            if name:
                return None, name, INSTALLED, fonts.display_name(fonts.file_name(name))
            if key in fonts.STANDARD_FAMILIES:
                name = fonts.font_for(letters or "a", fonts.STANDARD_FAMILIES[key], bold, italic)
                return None, name, STANDARD if name in BASE14_NAMES else SIMILAR, \
                    fonts.display_name(fonts.file_name(name))
        name = _similar(letters, kind, bold, italic)
        return None, name, SIMILAR, fonts.display_name(fonts.file_name(name))

    def _archival(self, font, style, letters):
        """In a PDF/A file every font is embedded: a standard font, which
        would be the reader's own copy, gives way to an installed one."""
        if not self.pdfa or font[0] or font[1] not in BASE14_NAMES:
            return font
        kind = "serif" if font[1].startswith("Times") else "mono" if font[1].startswith("Courier") else "sans"
        bold, italic = "Bold" in font[1], "Italic" in font[1] or "Oblique" in font[1]
        name = fonts.embedded_like(kind, bold, italic, "".join(ch for ch in letters if not ch.isspace()))
        if not name:
            return font
        self.notes["reason"] = "pdfa"
        return None, name, SIMILAR, fonts.display_name(fonts.file_name(name))

    def _document_font(self, key, bold, italic, letters):
        """One of the document's fonts of family ``key`` in that face, able to
        write ``letters`` (its copy may hold only some letters): an
        :class:`_OwnFont`, or None. This page's fonts first."""
        if self.writer is None:
            numbers = [self.number]
        else:
            numbers = [self.number] + [n for n in range(len(self.writer.pages)) if n != self.number][:40]
        seen = set()
        for number in numbers:
            page = self.page if number == self.number else self.writer.pages[number]
            for font in _page_fonts(page):
                obj = font.get_object()
                if id(obj) in seen or obj.get("/Subtype") == "/Type3":
                    continue
                seen.add(id(obj))
                name = fonts.strip_subset(obj.get("/BaseFont", ""))
                if fonts.family_key(name) != key or _face(obj) != (bool(bold), bool(italic)):
                    continue
                if self.pdfa and fonts.font_program(obj) is None:
                    continue  # the reader's own copy: not for a PDF/A file
                try:
                    own = _OwnFont(font, fonts.pdf_font_codes(font))
                except Exception:  # noqa: BLE001 - a font dictionary we can't read
                    continue
                if own.codes and own.can_write(letters) and self._draws(number, obj, letters):
                    return own
        return None

    def _draws(self, number, font, letters):
        """Whether the document's copy of ``font`` really has the letters'
        shapes (a copy cut down to some letters keeps empty ones for the
        rest), as pdfium sees it drawn on page ``number``."""
        name = str(font.get("/BaseFont", ""))
        if not letters:
            return True
        if number == self.number:
            textpage, close = self.textpage, None
        elif self.doc is not None:
            close = self.doc[number]
            textpage = close.get_textpage()
        else:
            return False
        try:
            faces = self._faces.get(number)
            if faces is None:
                faces = self._faces[number] = {}
                buf, flags = ctypes.create_string_buffer(256), ctypes.c_int()
                for i in range(textpage.count_chars()):
                    _raw().FPDFText_GetFontInfo(textpage.raw, i, buf, len(buf), ctypes.byref(flags))
                    faces.setdefault(buf.value.decode("latin-1").lstrip("/"), i)
            index = faces.get(name.lstrip("/"))
            if index is None:
                index = next((i for n, i in faces.items() if fonts.strip_subset(n) == fonts.strip_subset(name)), None)
            if index is None:  # not drawn there: only a whole font surely has the letters
                return fonts.strip_subset(name) == name.lstrip("/")
            handle = _font_handle(textpage, index)
            if handle and fonts.font_program(font) is None and not _is_embedded(handle):
                return True  # the reader's own copy of the font: every letter of its encoding
            return bool(handle) and all(_has_letter(handle, ch) for ch in set(letters))
        finally:
            if close is not None:
                textpage.close()
                close.close()

    # -- the three kinds of change --------------------------------------------------------
    def add(self, x, y, text, opts=None):
        """New words whose top left is at (x, y), looking like the nearest text
        unless ``opts`` say otherwise (and wrapped in its "width", if given)."""
        opts = opts or {}
        near = _nearest_char(self.textpage, x, y)
        style = dict(_style(self.textpage, near, self.cache)) if near is not None else \
            _default_style((x, y - 14, x + 100, y))
        k = style["size"] / style["raw_size"]
        style.update(matrix=(k, 0.0, 0.0, k), angle=0.0)
        base, style = style, _restyle(style, opts)
        font = self._font(style, base, text, opts)
        lines, ends, room = text.split("\n"), None, opts.get("width")
        if room:
            width, space = _metrics(font[0], font[1], style)
            lines, ends = [], set()
            for part in text.split("\n"):
                lines += [ln.strip() for ln in _wrap(part, room, lambda t: _place(t, 0.0, width, 0.0, space)[1])]
                ends.add(len(lines) - 1)
        self._set(lines, (x, y - style["size"] * 0.8), style, font, 0.0, None,
                  style["size"] * 1.2, opts.get("align"), room, ends)
        return font

    def line(self, rect, text, old, opts=None):
        """Change the words of one line."""
        opts = opts or {}
        letters = _letters(self.textpage, self._one_line(_chars(self.textpage, rect), rect, old), self.cache,
                           self.hidden)
        if letters is None:  # nothing there (e.g. a scan): the box as drawn
            base = _default_style(rect)
            style = _restyle(base, opts)
            self.cover(rect)
            font = self._font(style, base, text, opts)
            self._set(text.split("\n"), (rect[0], rect[1] + (rect[3] - rect[1]) * 0.22), style, font,
                      0.0, None, style["size"] * 1.2, opts.get("align"), rect[2] - rect[0])
            return font
        tc, word = _spacing([letters])
        if any(key in opts for key in LOOKS):  # a new look: the line written again in it
            return self._redo(letters, [letters], text, tc, word, opts)
        count = sum(len(L["char"]) for L in letters)
        before = old if old is not None and _nonspace(old) == count else _text_of(letters)
        if before == text:
            return (None, None, SAME, fonts.display_name(letters[0]["style"]["font"]))
        para = None if "\n" in text or _rtl(before + text) else self._paragraph_of(letters)
        if para and not text.strip():  # a line taken out of a paragraph: the rest closes up
            return self._reflow(para, text)
        if "\n" in text or not text.strip():
            return self._redo(letters, [letters], text, tc, word)
        try:
            return self._respell(letters, before, text, tc, word, para)
        except _Reflow:
            self.notes.pop("wider", None)
            return self._reflow(para, text)
        except _Unsure:
            return self._redo(letters, [letters], text, tc, word)

    def _segments(self):
        """Every level line of the page, cut where words are far apart (a
        column, a table cell): [{x0, x1, y, size, idx, letters}]."""
        if self._segs is None:
            from pdfmanager import textflow

            self._segs = []
            for line in textflow._lines([L for L in self.flow.letters if L[4]]):
                pieces = [[line[0]]]
                for a, b in zip(line, line[1:]):
                    if b[1][0] - a[1][2] > 1.2 * max(a[3], b[3]):
                        pieces.append([])
                    pieces[-1].append(b)
                for piece in pieces:
                    self._segs.append({"x0": piece[0][1][0], "x1": max(L[1][2] for L in piece), "y": piece[0][2][1],
                                       "size": statistics.median(L[3] for L in piece), "idx": {L[0] for L in piece},
                                       "letters": piece})
        return self._segs

    def _context(self, letters):
        """How the line sits among the others: "justify" (a line above or
        below starts and ends where it does), "right" or "center" (others
        end, or are centred, where it is, and none starts where it does),
        else "left"."""
        style = letters[0]["style"]
        if self.flow is None or self.legacy or abs(style["angle"]) >= 1:
            return "left"
        mine = {L["index"] for L in letters}
        size = max(L["style"]["size"] for L in letters)
        tol = max(0.75, 0.08 * size)
        x0, x1 = min(L["loose"][0] for L in letters), max(L["loose"][2] for L in letters)
        y = letters[0]["origin"][1]
        left = right = centre = both = 0
        for seg in self._segments():
            if seg["idx"] & mine:
                continue
            dl, dr = abs(seg["x0"] - x0), abs(seg["x1"] - x1)
            dm = abs((seg["x0"] + seg["x1"]) / 2 - (x0 + x1) / 2)
            near = abs(seg["y"] - y) < 9 * size
            if dl < tol and dr < tol:
                both += abs(seg["y"] - y) < 3.2 * size and abs(seg["size"] - size) < 0.5
            elif dl < tol:
                left += 1
            elif dr < tol and dl > 3 * tol:
                right += near
            elif dm < tol and dl > 3 * tol:
                centre += near
        if not left and right:
            return "right"
        if not left and centre:
            return "center"
        if both:
            return "justify"
        crop = self.flow.crop
        if not left and abs((x0 + x1) / 2 - (crop[0] + crop[2]) / 2) < 1.0 and x0 - crop[0] > 3 * size:
            return "center"  # alone on the page, in its middle
        return "left"

    def _beside(self, letters):
        """How far the line may reach: (left, right), the nearest words on
        either side of it on the page, else the page's edges."""
        x0, x1 = min(L["loose"][0] for L in letters), max(L["loose"][2] for L in letters)
        y0, y1 = min(L["loose"][1] for L in letters), max(L["loose"][3] for L in letters)
        mine = {L["index"] for L in letters}
        crop = self.flow.crop
        left, right = crop[0], crop[2]
        for i, loose, _, size, _ in self.flow.letters:
            if i in mine or loose[3] <= y0 + 0.2 * (y1 - y0) or loose[1] >= y1 - 0.2 * (y1 - y0):
                continue
            if loose[0] >= x1 - 0.5:
                right = min(right, loose[0] - 0.25 * size)
            elif loose[2] <= x0 + 0.5:
                left = max(left, loose[2] + 0.25 * size)
        return left, right

    def _paragraph_of(self, letters):
        """The paragraph a line is part of: (its lines as rows of letters,
        top to bottom, which of them the line is), or None when the line
        stands alone, or its paragraph isn't in one look (a bold word in
        it: writing it again would lose that)."""
        flow = self.flow
        if flow is None or flow.flowed or self.legacy or abs(letters[0]["style"]["angle"]) >= 1 or not flow.content:
            return None
        mine = {L["index"] for L in letters}
        segs = self._segments()
        me = next((seg for seg in segs if seg["idx"] & mine), None)
        x0, x1 = min(L["loose"][0] for L in letters), max(L["loose"][2] for L in letters)
        if me is None or abs(me["x0"] - x0) > 1 or abs(me["x1"] - x1) > 1:
            return None  # only part of a line
        size = me["size"]

        def first_word(seg):
            row = seg["letters"]
            end = row[0][1][2]
            for a, b in zip(row, row[1:]):
                if b[1][0] - a[1][2] > 0.18 * size:
                    break
                end = b[1][2]
            return end - row[0][1][0]

        def joins(a, b):
            """Whether ``b`` is the line right under ``a`` in one paragraph."""
            d = a["y"] - b["y"]
            return (abs(a["size"] - b["size"]) < 0.5 and 0.9 * size < d < 1.9 * size
                    and min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]) > 0.5 * min(a["x1"] - a["x0"], b["x1"] - b["x0"])
                    and b["x0"] - a["x0"] < 0.5 * size and a["x0"] - b["x0"] < 3 * size)

        def ended(a, b, right):
            """Whether the paragraph ends with ``a``: the first word of ``b`` would have fitted on it."""
            return a["x1"] + 0.25 * size + first_word(b) < right - 0.1 * size

        rows, lead = [me], None
        while True:  # down
            under = [seg for seg in segs if joins(rows[-1], seg)]
            if len(under) != 1:
                break
            d = rows[-1]["y"] - under[0]["y"]
            right = max([seg["x1"] for seg in rows] + [under[0]["x1"]])
            if (lead is not None and abs(d - lead) > 0.12 * lead) or ended(rows[-1], under[0], right):
                break
            lead = lead or d
            rows.append(under[0])
        while True:  # up
            over = [seg for seg in segs if joins(seg, rows[0])]
            if len(over) != 1:
                break
            d = over[0]["y"] - rows[0]["y"]
            right = max([seg["x1"] for seg in rows] + [over[0]["x1"]])
            if (lead is not None and abs(d - lead) > 0.12 * lead) or ended(over[0], rows[0], right):
                break
            lead = lead or d
            rows.insert(0, over[0])
        # Two lines don't show where a paragraph's right edge is, or that
        # they are one at all (two entries of a list look the same).
        if len(rows) < 3 or self._context(letters) not in ("left", "justify"):
            return None
        ends = sorted((seg["x1"] for seg in rows), reverse=True)
        if ends[0] - ends[1] > 4 * size:
            return None  # one line reaches far past the others: words of a column beside it, most likely
        found = [_letters(self.textpage, sorted(seg["idx"]), self.cache, self.hidden) for seg in rows]
        if any(row is None for row in found):
            return None
        _spacing(found)
        if any(_LIST_ITEM.match(_text_of(row)) for row in found[1:]):
            return None
        look = found[0][0]["style"]
        if any(L["style"] is not look for row in found for L in row):
            return None
        return found, rows.index(me)

    def _reflow(self, para, text):
        """A line's change that doesn't fit on its line: its paragraph is
        written again with it, wrapped, as a word processor would."""
        rows, at = para
        parts = [_text_of(row) for row in rows]
        parts[at] = text.strip()
        everything = [L for row in rows for L in row]
        rect = (min(L["loose"][0] for L in everything), min(L["loose"][1] for L in everything),
                max(L["loose"][2] for L in everything), max(L["loose"][3] for L in everything))
        self.notes["reflowed"] = True
        return self.paragraph(rect, " ".join(part for part in parts if part), {})

    def _one_line(self, chars, rect, old):
        """The letters of ``chars`` on the one line meant: a box drawn a
        little tall also catches letters of the line above or below (on a
        level page; lines at an angle are left as found)."""
        raw = _raw()
        m = raw.FS_MATRIX()
        for i in chars:
            raw.FPDFText_GetMatrix(self.textpage.raw, i, ctypes.byref(m))
            if abs(m.b) > abs(m.a) * 0.02 or m.a <= 0:
                return chars
        # Lines, not the raised and lowered letters within one (H2O, E=mc2):
        # those sit less than a line apart.
        rows = []
        for i in sorted(chars, key=lambda i: -_origin(self.textpage, i)[1]):
            y, size = _origin(self.textpage, i)[1], max(1.0, raw.FPDFText_GetFontSize(self.textpage.raw, i))
            if rows and rows[-1][0] - y < 0.7 * max(size, rows[-1][1]):
                rows[-1][2].append(i)
            else:
                rows.append([y, size, [i]])
            rows[-1][0], rows[-1][1] = y, max(size, rows[-1][1])
        if len(rows) < 2:
            return chars
        rows = [sorted(members) for _, _, members in rows]

        def count(row):
            return sum(1 for i in row if self.textpage.get_text_range(i, 1).strip()
                       and not self.hidden.hides(self.textpage, i, self.textpage.get_charbox(i, loose=True)))

        if old is not None:
            exact = [row for row in rows if count(row) == _nonspace(old)]
            if len(exact) == 1:
                return exact[0]
        middle = (rect[1] + rect[3]) / 2

        def off(row):
            boxes = [self.textpage.get_charbox(i, loose=True) for i in row]
            return abs((min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2 - middle)

        return min(rows, key=off)

    def paragraph(self, rect, text, opts=None):
        """Write a paragraph again, wrapped in the old one's width; the
        paragraphs below it in its column move up or down by the change in
        its height (see textflow), unless ``opts`` has "flow": False."""
        opts = opts or {}
        chars = _chars(self.textpage, rect, across=3.0)
        rows = [r for r in (_letters(self.textpage, row, self.cache, self.hidden) for row in _rows(self.textpage, chars)) if r]
        if not rows:
            return self.line(rect, text, None, opts)
        tc, word = _spacing(rows)
        everything = [L for row in rows for L in row]
        base = rows[0][0]["style"]
        if abs(base["angle"]) >= 1:
            return self._redo(rows[0], rows, text, tc, word, opts)
        u, down = _unit(base["matrix"]), _down(base["matrix"])
        first = rows[0][0]
        left = min(row[0]["s"] for row in rows)
        right = max(row[-1]["s"] + row[-1]["adv"] for row in rows)
        room = max(right - left, base["size"] * 4)
        lead = base["size"] * 1.2
        if len(rows) > 1:  # the old lines' spacing, measured down the page
            span = ((first["origin"][0] - rows[-1][0]["origin"][0]) * -down[0]
                    + (first["origin"][1] - rows[-1][0]["origin"][1]) * -down[1])
            lead = span / (len(rows) - 1) if span > 0 else lead
        flow = opts.get("flow", True) and self.flow is not None and not self.flow.flowed
        self.legacy = self.legacy or _rtl(text) or any(_rtl(L["char"]) for L in everything)
        taken = self.flow is not None and not self.flow.flowed and self.take(rows, paragraph=True)
        if not taken:
            self.cover(_box(everything))
        if not text.strip():  # taken away: what's below comes up into its place
            if taken:
                found = self.flow.column(rows, lead)
                if found["lines"] and self.flow.move(found, max(L["loose"][3] for L in rows[0]) - found["lines"][0][3]):
                    self.notes["moved"] = len(found["lines"])
            return None, None, SAME, fonts.display_name(base["font"])
        flow = flow and (taken or True)
        style = _restyle(base, opts)
        font = self._font(style, base, text, opts)
        plain = not any(key in opts for key in ("font", "bold", "italic"))  # the old words' font
        was = font if plain else _choose(self.page, base, text)
        natural = _metrics(was[0], was[1], base)[1] + 2 * tc
        align = opts.get("align") or _alignment(rows, left, right, word, natural, base["size"])
        top = max(L["loose"][3] for L in rows[0])
        ascent = (top - first["origin"][1]) if abs(down[1]) > 0.5 else base["size"] * 0.8
        old_span = ((rows[-1][0]["origin"][0] - first["origin"][0]) * down[0]
                    + (rows[-1][0]["origin"][1] - first["origin"][1]) * down[1])

        def layout(style, text=text, room=room, tight=1.0):
            """The new lines: ([(text, word gap, offset, ends a part)], letter width,
            letter spacing, line spacing, drop, growth)."""
            k = style["size"] / base["size"]
            tight = min(1.0, max(tight, style["size"] * 1.02 / (lead * k)))  # lines never closer than their size
            width, space = _metrics(font[0], font[1], style)
            tck = tc * k
            gap = space + 2 * tck if align == "justify" or word is None or not plain else word * k

            def measure(line):
                return _place(line, 0.0, width, tck, gap)[1] - tck

            lines = []
            for part in text.split("\n"):
                wrapped = _wrap(part, room, measure)
                for j, line in enumerate(wrapped):
                    line, g, off = line.strip(), gap, 0.0
                    spaces = line.count(" ")
                    if align == "justify" and j < len(wrapped) - 1 and spaces:
                        extra = (room - measure(line)) / spaces
                        if 0 <= extra <= style["size"] * 2:
                            g = gap + extra
                    elif align in ("center", "right"):
                        free = room - measure(line)
                        off = free / 2 if align == "center" else free
                    lines.append((line, g, off, j == len(wrapped) - 1))
            drop = (k - 1) * ascent  # a bigger size grows down from the old top
            grow = drop + (len(lines) - 1) * lead * k * tight - old_span + 0.22 * (style["size"] - base["size"])
            return lines, width, tck, lead * k * tight, drop, grow

        def squeeze(found, style, current):
            """Nothing below can move, so the paragraph has to end above
            what follows it: the smallest change that does (lines a little
            closer, then the size down a little at a time), or the most
            that's sane when even that isn't enough. (style, layout,
            whether it now fits)."""
            tops = [b[3] for b in found["lines"][:1]] + ([found["barrier"][3]] if found["barrier"] else [])
            allowed = found["bottom"] - max(tops + [found["limit"]]) - 0.05 * lead
            if current[5] <= max(allowed, 0.5):
                return style, current, True
            best = (style, current)
            for step in range(0, 13):  # down to seven tenths of the size
                smaller = style if step == 0 else _restyle(style, {"size": style["size"] * (1 - 0.025 * step)})
                for tight in ((1.0, 0.93) if step else (0.93,)):
                    best = (smaller, layout(smaller, tight=tight))
                    if best[1][5] <= allowed:
                        return *best, True
            return *best, False

        lines, width, tck, lead_k, drop, grow = layout(style)
        more, nxt = [], None  # lines that run on at the top of the next column
        if flow and abs(grow) > 0.5:
            found = self.flow.column(rows, lead)
            fits = grow <= 0 or found["low"] - grow >= found["limit"] - 0.01
            if not fits and not found["lines"] and len(lines) > 1:
                # The last paragraph of its column: what doesn't fit runs on
                # into the next column, whose text moves down to make room.
                nxt = self.flow.next_column(found, lead)
                for keep in range(len(lines) - 1, 0, -1) if nxt else ():
                    here = drop + (keep - 1) * lead_k - old_span + 0.22 * (style["size"] - base["size"])
                    if found["low"] - here < found["limit"] - 0.01:
                        continue
                    words = "".join(ln[0] + ("\n" if ln[3] else " ") for ln in lines[keep:]).rstrip()
                    more = layout(style, words, max(nxt["x1"] - nxt["x0"], base["size"] * 4))[0]
                    push = (len(more) + 0.5) * lead_k
                    if nxt["low"] - push >= nxt["limit"] - 0.01 and self.flow.move(nxt, -push):
                        lines, grow, fits = lines[:keep], here, True
                        self.notes["moved"] = len(nxt["lines"])
                        self.notes["linked"] = len(more)
                    else:
                        more = []
                    break
            if not fits and opts.get("fit") == "shrink":
                for step in range(1, 11):  # down to three quarters of the size, a little at a time
                    smaller = _restyle(style, {"size": style["size"] * (1 - 0.025 * step)})
                    attempt = layout(smaller)
                    if found["low"] - attempt[5] >= found["limit"] - 0.01:
                        style, (lines, width, tck, lead_k, drop, grow), fits = smaller, attempt, True
                        self.notes["size"] = round(smaller["size"], 2)
                        break
            if not fits:
                self.notes["overflow"] = True  # nothing moves: never pushed off the page
                # ... and never drawn over what's below it either.
                was = style["size"]
                style, (lines, width, tck, lead_k, drop, grow), _ = squeeze(found, style, (lines, width, tck, lead_k, drop, grow))
                if style["size"] != was:
                    self.notes["size"] = round(style["size"], 2)
            elif grow < 0 and not taken:
                pass  # the old words are covered: what's below can't come up under the cover
            elif self.flow.move(found, -grow):
                if found["lines"]:
                    self.notes["moved"] = self.notes.get("moved", 0) + len(found["lines"])
            elif grow > 0:
                self.notes["taller"] = True
        elif grow > 0.5:
            # Runs on below where it ended, and nothing moves: kept above what follows it.
            fitted = False
            if self.flow is not None and self.flow.content:
                was = style["size"]
                style, (lines, width, tck, lead_k, drop, grow), fitted = squeeze(
                    self.flow.column(rows, lead), style, (lines, width, tck, lead_k, drop, grow))
                if style["size"] != was:
                    self.notes["size"] = round(style["size"], 2)
            self.notes["taller"] = True
            del fitted  # said either way: it takes more lines than it did
        for n, (line, g, off, _) in enumerate(lines):
            placed, _ = _place(line, left + off, width, tck, g)
            d = drop + n * lead_k
            point = (first["origin"][0] + u[0] * (-first["s"]) + down[0] * d,
                     first["origin"][1] + u[1] * (-first["s"]) + down[1] * d)
            self.run(style, font, [(ch, point[0] + u[0] * s, point[1] + u[1] * s) for ch, s in placed])
        for n, (line, g, off, _) in enumerate(more):  # level text only: the paragraph is level
            placed, _ = _place(line, off, width, tck, g)
            self.run(style, font, [(ch, nxt["x0"] + s, nxt["baseline"] - n * lead_k) for ch, s in placed])
        return font

    # -- helpers ------------------------------------------------------------------------
    def _set(self, lines, origin, style, font, tc, word, lead, align=None, room=None, ends=None):
        """Lines of new words from ``origin`` (the first one's baseline start),
        set left, centred or right in ``room`` (else in the widest line), or
        justified but for the lines in ``ends``."""
        width, space = _metrics(font[0], font[1], style)
        gap = word if word is not None else space + 2 * tc
        u, down = _unit(style["matrix"]), _down(style["matrix"])

        def measure(line):
            return _place(line, 0.0, width, tc, gap)[1] - tc

        if align in ("center", "right", "justify") and not room:
            room = max((measure(line) for line in lines), default=0.0)
        for n, line in enumerate(lines):
            off, g = 0.0, gap
            if align in ("center", "right"):
                free = room - measure(line)
                off = free / 2 if align == "center" else free
            elif align == "justify" and ends is not None and n not in ends and line.count(" "):
                extra = (room - measure(line)) / line.count(" ")
                if 0 <= extra <= style["size"] * 2:
                    g = gap + extra
            placed, _ = _place(line, off, width, tc, g)
            x, y = origin[0] + down[0] * n * lead, origin[1] + down[1] * n * lead
            self.run(style, font, [(ch, x + u[0] * s, y + u[1] * s) for ch, s in placed])

    def _redo(self, letters, rows, text, tc, word, opts=None):
        """Cover the old words and set the new ones from where they began."""
        opts = opts or {}
        everything = [L for row in rows for L in row]
        self.legacy = self.legacy or _rtl(text) or any(_rtl(L["char"]) for L in everything)
        if not self.take(rows):
            self.cover(_box(everything, everything))
        base = letters[0]["style"]
        style = _restyle(base, opts)
        font = self._font(style, base, text, opts)
        k = style["size"] / base["size"]
        if text.strip():
            room = None
            if opts.get("align"):
                u = _unit(base["matrix"])
                room = max(L["s"] + L["adv"] for L in letters) - letters[0]["s"]
                room = room if room > 0 and abs(u[1]) < 1e-6 else None
            same = not any(key in opts for key in ("font", "bold", "italic"))
            self._set(text.split("\n"), letters[0]["origin"], style, font, tc * k,
                      word * k if word is not None and same else None, style["size"] * 1.2, opts.get("align"), room)
        return font

    def _natural(self, letters, tc):
        """The width of a space in the line's own font (with the line's
        letter spacing), or None when the font has none to measure."""
        style = letters[len(letters) // 2]["style"]
        if self.flow is not None and not self.legacy and self.flow.content:
            for L in (letters[len(letters) // 2], letters[0], letters[-1]):
                natural = self.flow.space_width(L)
                if natural is not None:
                    return natural + tc
        width = ctypes.c_float()
        try:
            if style["handle"] and _raw().FPDFFont_GetGlyphWidth(style["handle"], 32, style["raw_size"],
                                                                  ctypes.byref(width)) and width.value > 0:
                return width.value * (math.hypot(*style["matrix"][:2]) or 1.0) + 2 * tc
        except (AttributeError, TypeError, ValueError, OverflowError):
            pass
        return None

    def _respell(self, letters, before, after, tc, word, para=None):
        """Change a line, leaving what didn't change where it was: only the
        letters from the first change on are new, and those after the last
        change move by the difference in width."""
        p = 0
        while p < min(len(before), len(after)) and before[p] == after[p]:
            p += 1
        s = 0
        while s < min(len(before), len(after)) - p and before[-1 - s] == after[-1 - s]:
            s += 1
        # Words put in between two others: the space before them is theirs
        # ("required| and enforced| within", not "required |and enforced |within"),
        # so they look like the word they follow (bold after a bold word).
        at = _take(letters, _nonspace(before[:p]))
        if at and at < len(letters) and letters[at - 1]["style"] is not letters[at]["style"]:
            # ... when that word stands out from the line (bold, coloured) and
            # the one after doesn't: between a plain word and one that
            # stands out, they go with the one that stands out too.
            usual = max({id(L["style"]): L["style"] for L in letters}.values(),
                        key=lambda st: sum(1 for L in letters if L["style"] is st))
            if letters[at]["style"] is usual:
                while 0 < p == len(before) - s and before[p - 1].isspace() and len(after) - s > p \
                        and after[len(after) - s - 1].isspace() and _nonspace(before[:p]):
                    p, s = p - 1, s + 1
        count = sum(len(L["char"]) for L in letters)
        keep = _take(letters, _nonspace(before[:p]))
        tail = _take(letters, count - _nonspace(before[len(before) - s:]))
        if keep is None or tail is None or tail < keep:
            raise _Unsure
        u = _unit(letters[0]["style"]["matrix"])
        middle = after[p:len(after) - s]
        if p < len(before) and not before[p].isspace() and keep < len(letters):
            anchor = letters[keep]
            start = anchor["s"]
        elif keep > 0:
            anchor = letters[keep - 1]
            start = anchor["s"] + anchor["adv"] + tc
        else:
            anchor = letters[0]
            start = anchor["s"]
        style = anchor["style"]
        font = self._choose(style, middle)
        width, space = _metrics(font[0], font[1], style)
        gap = word if word is not None else space + 2 * tc
        placed, pen = _place(middle, start, width, tc, gap)
        shift = 0.0
        if tail < len(letters):
            b = len(before) - s
            if before[b].isspace() and tail > 0:
                was = letters[tail - 1]["s"] + letters[tail - 1]["adv"] + tc
            else:
                was = letters[tail]["s"]
            shift = pen - was
        # A justified line (word gaps wider than a space) keeps its right
        # edge: the gaps after the change take up the difference.
        moves = [shift] * (len(letters) - tail)
        # Justified: its word gaps are wider than a space of the line's own
        # font (not of the font the new words got), and those gaps are real
        # spaces stretched (word spacing), or a line above or below starts
        # and ends where this one does. Gaps set as moves between the words
        # of a ragged line don't make it justified.
        context = self._context(letters)
        natural = self._natural(letters, tc)
        own_space = natural if natural is not None else space + 2 * tc
        between = [L for L in letters[1:] if L.get("space_before")]
        real = bool(between) and sum(1 for L in between if L.get("explicit_space")) * 2 > len(between)
        if natural is None:
            stretched = word is not None and context == "justify"
        else:
            stretched = word is not None and word - natural > max(0.2, natural * 0.08) \
                and (real or context == "justify")
        old_end = letters[-1]["s"] + letters[-1]["adv"]
        growth = shift if tail < len(letters) else pen - tc - old_end
        gaps = [k for k, L in enumerate(letters[tail:]) if L.get("space_before")]
        if abs(shift) > MOVED and stretched and gaps:
            each = shift / len(gaps)
            narrowest = min(letters[tail + k]["s"] - letters[tail + k - 1]["s"] - letters[tail + k - 1]["adv"]
                            for k in gaps if tail + k > 0) if any(tail + k > 0 for k in gaps) else word
            # Words can come closer only so far (and spread only so far).
            each = min(each, max(0.0, narrowest - own_space * 0.7))
            each = max(each, word - own_space * 4)
            if shift - each * len(gaps) > 1.0:
                if para:
                    raise _Reflow  # past its right edge: the paragraph is wrapped again
                self.notes["wider"] = True  # still past its right edge: rewriting the paragraph re-wraps it
            if each:
                done = 0
                for k in range(len(moves)):
                    done += k in gaps
                    moves[k] = shift - each * done
        # A line set to the right keeps its right end, a centred one its
        # middle: the words before the change move left to make room.
        part = 0.0
        anchored = context in ("right", "center") and abs(growth) > MOVED and self.flow is not None
        if anchored:
            part = growth if context == "right" else growth / 2
        elif growth > 0.5 and not stretched and self.flow is not None and not self.legacy \
                and abs(letters[0]["style"]["angle"]) < 1:
            size = letters[0]["style"]["size"]
            if para:
                edge = max(row[-1]["s"] + row[-1]["adv"] for row in para[0])
                if old_end + growth > edge + 0.2 * size:
                    raise _Reflow  # longer than its paragraph is wide: wrapped again
            elif old_end + growth > self._beside(letters)[1]:
                self.notes["wider"] = True  # into the words beside it, or off the page
        ox, oy = anchor["origin"]

        def at(d):
            return ox + u[0] * (d - anchor["s"]), oy + u[1] * (d - anchor["s"])

        def entries(group, text, moves, partial=False):
            """The letters of ``group`` (spelling ``text``, or its start when
            ``partial``) as they are, each moved along by its ``moves``."""
            out, i, used = [], 0, 0
            for ch in text:
                if partial and i == len(group):
                    break
                if ch.isspace():
                    if out and not out[-1][0].isspace():
                        last = out[-1]
                        out.append((" ", last[1] + u[0] * (last[3] + tc), last[2] + u[1] * (last[3] + tc),
                                    0.0, last[4]))
                    continue
                if i >= len(group):
                    raise _Unsure
                used += 1
                if used == len(group[i]["char"]):
                    L, move = group[i], moves[i]
                    out.append((L["char"], L["origin"][0] + u[0] * move, L["origin"][1] + u[1] * move,
                                L["adv"], L["style"]))
                    i, used = i + 1, 0
            if i != len(group) or used:
                raise _Unsure
            return out

        def runs(items):
            """Consecutive letters drawn the same way: [(style, [entries], font)]."""
            out = []
            for item in items:
                if out and out[-1][0] is item[4]:
                    out[-1][1].append(item)
                else:
                    out.append((item[4], [item]))
            return [(st, group, self._choose(st, "".join(e[0] for e in group))) for st, group in out]

        # Drawn again with the change: the letters joined to it in the same
        # look (so the line stays one piece of text, for copying and the next
        # change) and every letter that moves. A piece in another look that
        # stays put is left alone, uncovered: drawn again exactly over
        # itself, a reader such as pdfium takes the copy for a duplicate.
        moved = abs(shift) > MOVED
        start = keep
        while start > 0 and letters[start - 1]["style"] is style:
            start -= 1
        end = len(letters) if moved else tail
        while not moved and end < len(letters) and letters[end]["style"] is style:
            end += 1
        head_runs = runs(entries(letters[start:keep], _skip(after[:p], sum(len(L["char"]) for L in letters[:start])),
                                 [0.0] * (keep - start)))
        rest_runs = runs(entries(letters[tail:end], after[len(after) - s:], moves, partial=True))
        if not all(f[0] for _, _, f in head_runs):  # not to be had in its own font: stays as it is
            start, head_runs = keep, []
        if not moved and not all(f[0] for _, _, f in rest_runs):
            end, rest_runs = tail, []
        new = [(ch, *at(d), 0.0, style) for ch, d in placed]
        writes = any(not ch.isspace() for ch in middle)
        same = (None, None, SAME, fonts.display_name(style["font"]))
        if not self.legacy and not _rtl(before + after) and self._in_place(
                letters, keep, tail, anchor, before, p, s, font, style,
                [(ch, *at(d - part)) for ch, d in placed] if writes else [], [m - part for m in moves], -part):
            if anchored and (letters[0]["s"] - part < self._beside(letters)[0]
                             or old_end + growth - part > self._beside(letters)[1]):
                self.notes["wider"] = True
            return font if writes else same
        covered = letters[start:end]
        if not covered and not writes:
            return font
        if covered:
            self.cover(_box(covered, letters, letters[start - 1] if start > 0 else None,
                            letters[end] if end < len(letters) else None))
        for st, group, f in head_runs:
            self.run(st, f, [e[:3] for e in group])
        if new:  # continues the run before it on the line, for copying
            self.run(style, font, [e[:3] for e in new])
        for st, group, f in rest_runs:
            self.run(st, f, [e[:3] for e in group])
        if writes:
            return font
        worst = next((f for _, _, f in rest_runs if not f[0]), None) if moved else None
        return worst or (None, None, SAME, fonts.display_name(style["font"]))


    def _in_place(self, letters, keep, tail, anchor, before, p, s, font, style, entries, moves, head=0.0):
        """Make a line's change in the page's own drawing: the letters
        that go are taken out of their pieces of text, the new ones written
        there, and the letters after them moved along. Nothing is covered
        and nothing drawn twice. False (nothing changed) when any of that
        can't be done for sure."""
        flow = self.flow
        if flow is None or not flow.content:
            return False
        own = font[0]
        if own is not None and own.space:
            entries = [e for e in entries if not e[0].isspace()]
        saved = flow.checkpoint()
        ok = flow.uncover(letters)
        spot = flow.glyph(anchor) if ok else None
        ok = spot is not None and flow.remove(letters[keep:tail])
        if ok and keep > 0 and tail < len(letters):
            # Spaces that went with the old words (not those kept before and after them).
            old = before[p:len(before) - s]
            lead = len(before[:p]) - len(before[:p].rstrip())
            trail = len(before[len(before) - s:]) - len(before[len(before) - s:].lstrip())
            flow.remove_spaces(letters[keep - 1], letters[tail], lead, trail, sum(ch.isspace() for ch in old))
        for L, move in list(zip(letters[tail:], moves)) + [(L, head) for L in letters[:keep]]:
            if ok and abs(move) > MOVED:
                ok = flow.shift(L, move)
        if ok and entries:
            ahead = anchor is letters[keep] if keep < len(letters) else False
            ok = flow.insert(spot, ahead, {"own": own, "name": font[1], "style": style, "entries": entries,
                                           "same": True, "color": None})
        if not ok:
            flow.rollback(saved)
        return ok


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


_LIST_ITEM = re.compile(r"\s*(?:[\u2022\u2023\u25aa\u25cf\u25e6\u2013\u2014*\-]\s|\(?\w{1,3}[.)]\s)")


class _Unsure(Exception):
    """The new words can't be matched up with the old letters."""


class _Reflow(Exception):
    """The changed line no longer fits on its line of the paragraph."""


def edit_text(path, output, edits, password=None, report=None):
    """Change words on pages. ``edits`` is a list of {"page" (1-based), and
    either "rect": [x0, y0, x1, y1] around the old words in PDF points (with
    "paragraph": true to rewrite the lines in it as one paragraph that
    wraps; else "old" may give the words as shown, to match the new ones
    against), or "at": [x, y] for new words whose top left is there (and
    "width" to wrap them in); and "text": the new words (empty to just cover
    the old ones)}.

    How the new words look can be chosen too: "font" (a family: one of the
    document's, an installed one, or Helvetica, Times or Courier), "size"
    (points), "bold", "italic", "color" ([r, g, b] from 0 to 1) and "align"
    ("left", "center", "right", "justify"). A rewritten paragraph that gets
    taller or shorter moves the text below it in its column ("flow": false
    to leave it); when that doesn't fit on the page nothing moves and the
    report says "overflow", unless "fit": "shrink" lets the paragraph's
    size come down a little to fit.

    Returns how many edits were made; ``report``, a list, gets for each the
    font used ("same", "installed", "standard" or "similar") and its name,
    with "asked" (the family asked for) and "matched" (whether it was the
    one used), "moved" (lines moved), "overflow", "size" (shrunk to),
    "wider" and "taller" when they apply."""
    import pypdfium2 as pdfium

    reader = core.open_pdf(path, password)
    by_page = _parse(edits, len(reader.pages))
    pdfa = _is_pdfa(reader)
    writer = core.clone_document(reader)
    # Pages can share one content stream (after Compress, or a duplicated
    # page): the change must land on this page only.
    shared = core._shared_objects(writer)
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path), password=password)
        try:
            for number, page_edits in by_page.items():
                page = writer.pages[number - 1]
                core._own_contents(page, shared)
                work = _Page(doc[number - 1], page, doc, number - 1, writer, pdfa)
                try:
                    for kind, rect, text, old, opts in page_edits:
                        work.start()
                        if kind == "add":
                            font = work.add(rect[0], rect[1], text, opts)
                        elif kind == "paragraph":
                            font = work.paragraph(rect, text, opts)
                        else:
                            font = work.line(rect, text, old, opts)
                        work.settle()
                        if report is not None:
                            drawn = any(not ch.isspace() for ch in text)
                            report.append({"font": font[3] if drawn else "", "kind": font[2] if drawn else SAME,
                                           **work.notes})
                    if work.flow is not None and work.flow.changed():
                        work.ready()
                        work.flow.write(writer, page)
                        work.runs += work.flow.orphans
                    _draw(writer, page, work.covers, work.runs)
                finally:
                    work.close()
        finally:
            doc.close()
    core._write(writer, output)
    return sum(len(p) for p in by_page.values())


def font_choices(path, password=None):
    """The fonts the reader offers for edited words: {"document": the
    families the document uses, "installed": common installed ones, "more":
    the other installed ones, "standard": the standard PDF fonts, "pdfa":
    whether the file is PDF/A}. A family is listed once, in the first group
    that has it (with the installed font's spelling, "DejaVu Sans")."""
    reader = core.open_pdf(path, password)
    found = {}
    for page in reader.pages[:200]:
        try:
            for font in _page_fonts(page):
                obj = font.get_object()
                if obj.get("/Subtype") != "/Type3":
                    name = fonts.family_name(obj.get("/BaseFont", ""))
                    if name and name.isprintable():
                        found.setdefault(fonts.family_key(name), name)
        except Exception:  # noqa: BLE001 - a page whose resources can't be read
            continue
    common, rest = fonts.installed_families()
    spelled = {fonts.family_key(n): n for n in common + rest}
    spelled.update({"helvetica": "Helvetica", "times": "Times", "courier": "Courier"})
    document = sorted((spelled.get(k, n) for k, n in found.items()), key=str.lower)

    def others(names):
        return [n for n in names if fonts.family_key(n) not in found]
    return {"document": document, "installed": others(common), "more": others(rest),
            "standard": others(["Helvetica", "Times", "Courier"]), "pdfa": _is_pdfa(reader)}


def _is_pdfa(reader):
    """Whether the file says it is PDF/A (its fonts must all be embedded)."""
    try:
        xmp = reader.xmp_metadata
        raw = xmp.stream.get_data().decode("utf-8", "replace") if xmp is not None else ""
    except Exception:  # noqa: BLE001 - unreadable metadata: not PDF/A as far as can be told
        return False
    return "pdfaid:part" in raw


# ---- drawing --------------------------------------------------------------------------------

def _draw(writer, page, covers, runs):
    box = page.mediabox
    left, bottom = float(box.left), float(box.bottom)
    others = [r for r in runs if not r["own"]]

    def draw(c, w, h):
        for (x0, y0, x1, y1), colour in covers:
            c.setFillColorRGB(*colour)
            c.rect(x0 - left, y0 - bottom, x1 - x0, y1 - y0, fill=1, stroke=0)
        for run in others:
            style = run["style"]
            a, b, cc, d = style["matrix"]
            for ch, x, y in run["entries"]:
                if ch.isspace():
                    continue
                c.saveState()
                c.transform(a, b, cc, d, x - left, y - bottom)
                c.setFillColorRGB(*style["color"])
                c.setFont(run["name"], style["raw_size"])
                c.drawString(0, 0, ch)
                c.restoreState()

    if covers or others:
        page.merge_transformed_page(core._overlay_page(float(box.width), float(box.height), draw),
                                    Transformation().translate(left, bottom))
    own = [r for r in runs if r["own"]]
    if own:
        _write_with_own_fonts(writer, page, own)


def _num(v):
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


def _write_with_own_fonts(writer, page, runs):
    """Add the words drawn with the document's fonts as one more content
    stream, after everything else on the page; each letter exactly where
    it was placed."""
    resources = page.get("/Resources")
    resources = DictionaryObject() if resources is None else resources.get_object()
    page[NameObject("/Resources")] = resources
    font_dict = resources.get("/Font")
    font_dict = DictionaryObject() if font_dict is None else font_dict.get_object()
    resources[NameObject("/Font")] = font_dict
    ops, names = ["q", "BT", "0 Tc", "0 Tw", "100 Tz", "0 Ts", "0 Tr"], {}
    for run in runs:
        own, style = run["own"], run["style"]
        key = id(own.font.get_object())
        if key not in names:
            n = 1
            while f"/ZPDF{n}" in font_dict:
                n += 1
            names[key] = f"/ZPDF{n}"
            font_dict[NameObject(names[key])] = own.font
        a, b, c, d = style["matrix"]
        scale = (math.hypot(a, b) or 1.0) * style["raw_size"]
        u = _unit(style["matrix"])
        entries = run["entries"]
        x0, y0 = entries[0][1], entries[0][2]
        ops += [" ".join(_num(v) for v in style["color"]) + " rg",
                f"{names[key]} {_num(style['raw_size'])} Tf",
                " ".join(_num(v) for v in (a, b, c, d, x0, y0)) + " Tm"]
        parts, pen = [], 0.0
        for ch, x, y in entries:
            if ch.isspace() and own.space:
                continue  # no space in the font: the next letter's place makes it
            s = (x - x0) * u[0] + (y - y0) * u[1]
            move = (s - pen) / scale * 1000  # in thousandths of the font size
            if abs(move) > 0.001:
                parts.append(_num(-move))
            code = b"".join(own.codes[c] for c in ch)
            parts.append("<" + code.hex() + ">")
            pen = s + sum(own.width_of(own.codes[c]) for c in ch) / 1000 * scale
        ops.append("[" + " ".join(parts) + "] TJ")
    ops += ["ET", "Q"]

    def stream(text):
        obj = DecodedStreamObject()
        obj.set_data(text.encode("latin-1"))
        return writer._add_object(obj)

    contents = page.get("/Contents")
    if contents is None:
        parts = []
    elif isinstance(contents.get_object(), ArrayObject):
        parts = list(contents.get_object())
    else:
        parts = [contents if hasattr(contents, "idnum") else writer._add_object(contents)]
    # The page's own drawing in q ... Q, so whatever it leaves set (a
    # transformation, a colour) doesn't move or tint the new words.
    page[NameObject("/Contents")] = ArrayObject([stream("q"), *parts, stream("Q"), stream("\n".join(ops))])
