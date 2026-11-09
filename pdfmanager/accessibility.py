"""Accessible PDFs: tags for screen readers, and a check of what stands in
their way (like Acrobat's Autotag and Accessibility Checker).

A *tagged* PDF carries, next to what's drawn on its pages, a structure tree
that says what each piece of content is (heading, paragraph, list, table,
figure, link) and in which order it reads. Screen readers, reflow on small
screens and conversions to Word or HTML rely on it; without it they guess
from the drawing and get columns, tables and running headers wrong. PDF/UA
(ISO 14289) is the standard for accessible PDFs.

    result = autotag(path, output)          # writes a tagged copy
    result = check(path)                    # what keeps it from being accessible
    print(report(result, path.name))

Tagging (``autotag``), page by page:

  * The layout is read from the page's text (pypdfium2: each character's
    box, size and weight) and its tables (pdfplumber, as for PDF → Excel).
    Text is put in reading order by cutting the page at its widest gaps of
    white space ("XY cut"): a gutter splits columns (left before right), a
    band across the page splits top from bottom.
  * Lines become paragraphs; larger or bold short ones become headings
    (H1-H6 by size, or by numbering such as "1.2"; levels never skip);
    lines that start with a bullet or a number become lists (Lbl + LBody);
    tables get rows and cells, with the first row as header cells (TH);
    pictures become figures, described by a caption next to them
    ("Figure 3: ...") or by the text given in ``alt``; text repeated at the
    top or bottom of the pages, and page numbers, become artifacts (not
    read out).
  * The page's drawing operators stay as they are and are wrapped in
    marked content: ``/P <</MCID 3>> BDC ... EMC`` around what belongs to an
    element, ``/Artifact BMC ... EMC`` around decoration (rules, borders,
    backgrounds, running headers). Each operator goes to the element whose
    text it overlaps most, found by following the page's graphics and text
    state (positions, fonts and their widths). Nothing drawn changes.
  * The structure tree, its ParentTree (from every MCID back to its
    element), /StructParents on pages and /StructParent on links, form
    fields and comments (tagged as Link, Form and Annot elements) are built
    with pikepdf, with MarkInfo /Marked, the language (/Lang), the title
    (shown in the window's title bar: DisplayDocTitle) and a tab order that
    follows the structure (/Tabs /S).

A form XObject drawn once (some programs draw each page as one) is tagged
inside, with its own /StructParents; one drawn several times is tagged as
a whole where it's drawn. Vector drawings (charts made of lines) are marked
as decoration, not figures. A file that is already tagged keeps its tags
(only the language, title and tab order are filled in) unless ``force`` is
given.

Checking (``check``) covers the basics of PDF/UA that Acrobat's checker
looks at (see RULES). Reading order and colour contrast can only be judged
by a person; the report says so.

``check`` result: {"file", "pages", "tagged", "lang", "title", "passed",
"issues": [{"rule", "message", "fix", "count", "pages"}], "note"}.
"""

import ctypes
import re
import statistics
from collections import Counter
from io import BytesIO
from pathlib import Path

from pdfmanager import core, progress

# A few common languages, recognised by their most frequent short words.
LANGUAGE_WORDS = {
    "en": "the and of to in is that for it with as was on are be this by from or not",
    "de": "der die und das ist nicht ein eine zu den mit von sich des auf für im dem auch",
    "fr": "le la les et des est une un du que pour dans pas qui sur au avec ce sont",
    "es": "el la los las que y en es un una por con para del se no al como",
    "it": "il la di che e è un una per non del della sono gli le con nel anche",
    "nl": "de het een en van is dat op te in niet zijn voor met die er ook",
    "pt": "o a os as de que e do da em um uma para com não é no na",
    "sv": "och att det som en på är av för med till den har inte jag",
    "da": "og at det som en på er af for med til den har ikke jeg",
    "pl": "i w na z do się nie że to jest jak o po ale",
}
LANGUAGE_WORDS = {lang: set(words.split()) for lang, words in LANGUAGE_WORDS.items()}
LANG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$")
DEFAULT_LANG = "en"

# What the checker looks for, in the order the report lists it:
# rule -> how to fix it.
RULES = {
    "security": "Remove the password (Remove password), or protect the file again with ZPDF, "
                "which lets screen readers in.",
    "tagged": "Use Make accessible (tag); on the command line, pdfm tag.",
    "lang": "Make accessible sets it; choose the language there if it guesses wrong.",
    "title": "Set a title (Title & author, or Make accessible's Title box).",
    "title-shown": "Make accessible turns this on.",
    "scanned": "Run OCR first (Make searchable), then Make accessible.",
    "unicode": "Make the PDF again from the original document with the fonts embedded, "
               "or OCR the pages.",
    "content": "Tag the file again with Make accessible, ticking Replace existing tags.",
    "structure": "Tag the file again with Make accessible, ticking Replace existing tags.",
    "figures": "Describe each picture (Acrobat: Set Alternate Text), or give the "
               "descriptions to pdfm tag --alt.",
    "tables": "Tag the file again, or fix the table's tags in Acrobat (Table Editor).",
    "headings": "Fix the heading tags (Acrobat: Tags panel), or tag the file again.",
    "lists": "Fix the list tags (Acrobat: Tags panel), or tag the file again.",
    "links": "Tag the file again with Make accessible.",
    "annotations": "Tag the file again with Make accessible.",
    "forms": "Give each field a tooltip (Acrobat: field Properties → Tooltip).",
    "tabs": "Make accessible sets the tab order to follow the structure.",
    "bookmarks": "Add bookmarks (Bookmarks → Find headings, or `pdfm bookmarks --detect`).",
}

MANUAL_NOTE = ("Two things only a person can check: that the reading order makes sense "
               "(read it with a screen reader, or in Acrobat's Reading Order tool), and "
               "that text stands out clearly enough from its background (colour contrast).")

BOOKMARK_PAGES = 20  # longer documents should have bookmarks

BULLETS = set("•◦▪▫‣⁃●○■□►▶➢➤→◆◇✓✔✗–—-*·∙")
NUMBER_MARK = re.compile(r"^(?:\(?(?:\d{1,3}|[a-z]|[ivx]{1,4})[.)]|\((?:\d{1,3}|[a-z]|[ivx]{1,4})\))$",
                         re.I)
NUMBER_START = re.compile(r"^(?:\(?(?:\d{1,3}|[a-z]|[ivx]{1,4})[.)]|\((?:\d{1,3}|[a-z]|[ivx]{1,4})\))\s",
                          re.I)
NUMBERED_HEADING = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){0,5})\.?\s+\S")
CAPTION = re.compile(
    r"^(?:figure|fig\.?|chart|graph|image|picture|photo|illustration|diagram|map|plate|"
    r"abbildung|abb\.|bild|grafik|figura|imagen|ilustración|gráfico|figuur|afbeelding|"
    r"immagine|illustrazione|tableau|tabelle|tabla|tabella|tabel|table)\s*[\dIVX]",
    re.I)
TABLE_CAPTION = re.compile(r"^(?:table|tableau|tabelle|tabla|tabella|tabel)\s*[\dIVX]", re.I)
PAGE_NUMBER = re.compile(
    r"^(?:[-–—]\s*)?(?:(?:page|p\.|pg\.?|seite|página|pagina|pag\.|blz\.?|sida|side|strona)\s*)?"
    r"(?:\d{1,4}|[ivxlcdm]{1,7})(?:\s*(?:/|of|von|de|di|van|sur|av|z|из)\s*\d{1,4})?(?:\s*[-–—])?$",
    re.I)

_PATH_BUILD = {"m", "l", "c", "v", "y", "h", "re"}
_PATH_PAINT = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*"}
_SHOW = {"Tj", "TJ", "'", '"'}

# The standard 14 fonts and common names for them, for text widths when a
# font gives none.
_STD_FONTS = {
    "helvetica": "Helvetica", "arial": "Helvetica", "arialmt": "Helvetica",
    "times": "Times-Roman", "timesnewroman": "Times-Roman", "timesnewromanpsmt": "Times-Roman",
    "courier": "Courier", "couriernew": "Courier", "symbol": "Symbol",
    "zapfdingbats": "ZapfDingbats",
}


# --------------------------------------------------------------------------
# Small geometry helpers. Boxes are (x0, y0, x1, y1), y upwards.
# --------------------------------------------------------------------------

def _mul(a, b):
    """The matrix ``a`` then ``b`` (PDF's [a b c d e f])."""
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5])


def _apply(m, x, y):
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def _bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _box_through(m, box):
    x0, y0, x1, y1 = box
    return _bbox([_apply(m, x0, y0), _apply(m, x1, y0), _apply(m, x0, y1), _apply(m, x1, y1)])


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _overlap(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _union(boxes):
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _distance(a, b):
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return (dx * dx + dy * dy) ** 0.5


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class _View:
    """From a page's own coordinates to what is seen: the page turned by its
    /Rotate, origin at the bottom left. Layout is read in this space, so
    "left column" and "top of the page" mean what they look like."""

    def __init__(self, mediabox, rotation):
        self.box = [float(v) for v in mediabox]
        x0, y0, x1, y1 = self.box
        self.box = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
        self.rotation = rotation % 360
        w, h = self.box[2] - self.box[0], self.box[3] - self.box[1]
        self.size = (h, w) if self.rotation in (90, 270) else (w, h)

    def point(self, x, y):
        bx0, by0, bx1, by1 = self.box
        if self.rotation == 90:
            return y - by0, bx1 - x
        if self.rotation == 180:
            return bx1 - x, by1 - y
        if self.rotation == 270:
            return by1 - y, x - bx0
        return x - bx0, y - by0

    def rect(self, box):
        x0, y0, x1, y1 = box
        return _bbox([self.point(x0, y0), self.point(x1, y1)])


def _inherited(page_obj, key):
    node, depth = page_obj, 0
    while node is not None and depth < 50:
        if key in node:
            return node[key]
        node = node.get("/Parent")
        depth += 1
    return None


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
# Reading the text: characters -> segments (runs of words on one line)
# --------------------------------------------------------------------------

class _Seg:
    __slots__ = ("x0", "y0", "x1", "y1", "base", "size", "bold", "text", "chars", "role",
                 "zone", "rights")

    def __init__(self, x0, base, size, bold):
        self.x0, self.x1, self.base, self.size, self.bold = x0, x0, base, size, bold
        self.y0, self.y1 = base - 0.25 * size, base + 0.8 * size
        self.text, self.chars, self.role, self.zone = "", 0, None, None
        self.rights = []  # right edge of each character (spaces left out)

    @property
    def box(self):
        return (self.x0, self.y0, self.x1, self.y1)


def _text_direction(textpage):
    """The turn (0, 90, 180, 270) that makes most of the page's text run
    left to right, or None for a page without text. Usually 0; text set
    sideways, e.g. on a landscape page, needs a turn to read in order. The
    page's /Rotate isn't used for this: it turns what's seen, and text is
    upright in the page's own coordinates as often as on screen."""
    import pypdfium2.raw as raw

    votes = Counter()
    x, y = ctypes.c_double(), ctypes.c_double()
    last = None
    for i in range(min(textpage.count_chars(), 1500)):
        code = raw.FPDFText_GetUnicode(textpage, i)
        if raw.FPDFText_IsGenerated(textpage, i) == 1 or not 32 < code < 0x110000 \
                or chr(code).isspace():
            last = None
            continue
        raw.FPDFText_GetCharOrigin(textpage, i, ctypes.byref(x), ctypes.byref(y))
        size = raw.FPDFText_GetFontSize(textpage, i) or 10
        if last is not None:
            dx, dy = x.value - last[0], y.value - last[1]
            if 0 < abs(dx) + abs(dy) < 2 * size:
                if abs(dx) >= abs(dy):
                    votes[0 if dx > 0 else 180] += 1
                else:
                    votes[90 if dy > 0 else 270] += 1
        last = (x.value, y.value)
    return votes.most_common(1)[0][0] if votes else None


def _page_segments(textpage, view):
    """The page's text as segments (words on one line with no wide gap),
    in seen coordinates, in the order the text was drawn."""
    import pypdfium2.raw as raw

    count = textpage.count_chars()
    buf = ctypes.create_string_buffer(128)
    flags = ctypes.c_int()
    ox, oy = ctypes.c_double(), ctypes.c_double()
    fonts = {}
    loose = raw.FS_RECTF()
    segs, cur, space = [], None, False
    for i in range(count):
        if raw.FPDFText_IsGenerated(textpage, i) == 1:
            space = True
            continue
        code = raw.FPDFText_GetUnicode(textpage, i)
        ch = chr(code) if 0 < code < 0x110000 else "�"
        if ch.isspace() or ch == "\x00":
            space = True
            continue
        # the loose box spans the character's advance: no gaps inside words
        if raw.FPDFText_GetLooseCharBox(textpage, i, ctypes.byref(loose)) and loose.right > loose.left:
            left, bottom, right, top = loose.left, loose.bottom, loose.right, loose.top
        else:
            left, bottom, right, top = textpage.get_charbox(i)
        raw.FPDFText_GetCharOrigin(textpage, i, ctypes.byref(ox), ctypes.byref(oy))
        size = raw.FPDFText_GetFontSize(textpage, i)
        glyph = max(top - bottom, right - left)
        if size <= 0 or (glyph > 0 and not 0.3 * glyph <= size <= 6 * glyph):
            size = glyph if glyph > 0 else 10.0
        raw.FPDFText_GetFontInfo(textpage, i, buf, 128, ctypes.byref(flags))
        name = buf.value
        bold = fonts.get((name, flags.value))
        if bold is None:
            lower = name.lower()
            bold = any(w in lower for w in (b"bold", b"black", b"heavy", b"semibold", b"demi")) \
                or bool(flags.value & (1 << 18))
            fonts[(name, flags.value)] = bold
        if not bold and raw.FPDFText_GetFontWeight(textpage, i) >= 600:
            bold = True
        # in seen coordinates
        (l, b, r, t) = view.rect((left, bottom, right, top))
        bx, by = view.point(ox.value, oy.value)
        if r <= l:
            r = l + size * 0.5
        same = (cur is not None and abs(by - cur.base) < 0.3 * max(size, cur.size)
                and -0.35 * size <= l - cur.x1 <= 0.8 * max(size, cur.size))
        if not same:
            cur = _Seg(min(l, bx), by, size, bold)
            segs.append(cur)
        elif space or l - cur.x1 > 0.15 * size:
            cur.text += " "
        cur.text += ch
        cur.x1 = max(cur.x1, r)
        cur.rights.append(r)
        cur.chars += 1
        cur.bold = cur.bold and bold
        if size > cur.size:
            cur.size = size
            cur.y0, cur.y1 = cur.base - 0.25 * size, cur.base + 0.8 * size
        space = False
    return segs


# --------------------------------------------------------------------------
# Running headers and footers, body text size, language
# --------------------------------------------------------------------------

def _normalise(text):
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", text.lower())).strip()


def _mark_page_furniture(pages):
    """Mark segments at the top or bottom of pages that repeat on other
    pages (running headers and footers) or are page numbers: artifacts."""
    seen = Counter()
    for page in pages:
        h = page["size"][1]
        for seg in page["segs"]:
            if seg.y0 >= h * 0.9:
                seg.zone = "top"
            elif seg.y1 <= h * 0.1:
                seg.zone = "bottom"
            if seg.zone:
                seen[(seg.zone, _normalise(seg.text))] += 1
    several = len(pages) > 1

    def furniture(seg):
        repeated = several and seen[(seg.zone, _normalise(seg.text))] >= 2
        return repeated or bool(PAGE_NUMBER.match(seg.text.strip()))

    for page in pages:
        # a line counts as a whole: "Best for: <different on every page>"
        # is content even though "Best for:" repeats
        rows = []
        for seg in sorted((s for s in page["segs"] if s.zone), key=lambda s: -s.base):
            if rows and abs(rows[-1][0].base - seg.base) < 0.5 * seg.size:
                rows[-1].append(seg)
            else:
                rows.append([seg])
        for row in rows:
            if all(furniture(seg) for seg in row):
                for seg in row:
                    seg.role = "header" if seg.zone == "top" else "footer"


def _body_size(pages):
    sizes = Counter()
    for page in pages:
        for seg in page["segs"]:
            if seg.role is None:
                sizes[round(seg.size * 2) / 2] += seg.chars
    return sizes.most_common(1)[0][0] if sizes else 10.0


def guess_language(text):
    """A language code ("en", "de", ...) from the commonest short words in
    ``text``; "en" when it can't tell."""
    words = re.findall(r"[^\W\d_]+", text.lower())[:20000]
    if len(words) < 5:
        return DEFAULT_LANG
    counts = Counter(words)
    scores = {lang: sum(counts[w] for w in common) for lang, common in LANGUAGE_WORDS.items()}
    lang, score = max(scores.items(), key=lambda item: item[1])
    return lang if score >= max(3, len(words) * 0.04) else DEFAULT_LANG


# --------------------------------------------------------------------------
# Reading order: XY cut
# --------------------------------------------------------------------------

class _Item:
    """Something placed in reading order: a segment, table, figure."""

    __slots__ = ("kind", "box", "obj", "size")

    def __init__(self, kind, box, obj, size):
        self.kind, self.box, self.obj, self.size = kind, box, obj, size


def _gaps(spans, min_gap):
    """Gaps between the merged spans: [(size, middle)]."""
    spans = sorted(spans)
    found = []
    hi = spans[0][1]
    for lo, top in spans[1:]:
        if lo - hi > min_gap:
            found.append((lo - hi, (lo + hi) / 2))
        hi = max(hi, top)
    return found


def _rows(group):
    """Items with no gap left between them: row by row, top down, each
    row left to right."""
    rows = []
    for it in sorted(group, key=lambda it: -(it.box[1] + it.box[3])):
        middle = (it.box[1] + it.box[3]) / 2
        if rows and abs(rows[-1][0] - middle) < 0.5 * max(it.size, rows[-1][1]):
            rows[-1][2].append(it)
        else:
            rows.append([middle, it.size, [it]])
    return [it for row in rows for it in sorted(row[2], key=lambda it: it.box[0])]


def _reading_order(items, size):
    """Items in reading order, each with its column path (items in
    different columns never join into one paragraph)."""
    out = []

    def cut(group, path, depth):
        if len(group) <= 1 or depth > 80:
            out.extend((it, path) for it in _rows(group))
            return
        ys = _gaps([(it.box[1], it.box[3]) for it in group], 0.5)
        xs = _gaps([(it.box[0], it.box[2]) for it in group], max(0.9 * size, 6.0))
        left_all = min(it.box[0] for it in group)
        right_all = max(it.box[2] for it in group)
        width = right_all - left_all
        valid = []
        for gap, at in xs:
            left = [it for it in group if it.box[2] <= at]
            right = [it for it in group if it.box[0] >= at]
            if not left or not right:
                continue
            lw = max(it.box[2] for it in left) - min(it.box[0] for it in left)
            rw = max(it.box[2] for it in right) - min(it.box[0] for it in right)
            # columns are wide, and more than one line deep (a wide gap inside
            # a single line doesn't make columns)
            deep = all(max(it.box[3] for it in side) - min(it.box[1] for it in side)
                       >= 1.8 * max(it.size for it in side) for side in (left, right))
            if deep and min(lw, rw) >= max(0.15 * width, 2.5 * size):
                valid.append((gap, at))
        by = max((g for g, _ in ys), default=0.0)
        bx = max((g for g, _ in valid), default=0.0)
        if not by and not bx:
            out.extend((it, path) for it in _rows(group))
            return
        if bx and bx >= by:
            cuts = sorted(at for g, at in valid if g >= bx * 0.75)
            parts = [[] for _ in range(len(cuts) + 1)]
            for it in group:
                parts[sum(1 for at in cuts if it.box[0] >= at)].append(it)
            for n, part in enumerate(parts):
                if part:
                    cut(part, path + ((depth, n),), depth + 1)
        else:
            cuts = sorted((at for g, at in ys if g >= by * 0.75), reverse=True)
            parts = [[] for _ in range(len(cuts) + 1)]
            for it in group:
                parts[sum(1 for at in cuts if it.box[1] < at)].append(it)
            for part in parts:
                if part:
                    cut(part, path, depth + 1)

    if items:
        cut(list(items), (), 0)
    return out


# --------------------------------------------------------------------------
# Lines, paragraphs, lists
# --------------------------------------------------------------------------

class _Line:
    def __init__(self, seg, path):
        self.segs, self.path = [seg], path

    def add(self, seg):
        self.segs.append(seg)

    def finish(self):
        self.segs.sort(key=lambda s: s.x0)
        main = max(self.segs, key=lambda s: s.chars)
        self.size, self.base = main.size, main.base
        self.x0 = min(s.x0 for s in self.segs)
        self.x1 = max(s.x1 for s in self.segs)
        self.y0 = min(s.y0 for s in self.segs)
        self.y1 = max(s.y1 for s in self.segs)
        self.bold = all(s.bold for s in self.segs)
        self.text = " ".join(s.text for s in self.segs)
        return self


def _lines(ordered):
    """Consecutive segments of one row (and column) as lines."""
    lines = []
    for seg, path in ordered:
        last = lines[-1] if lines else None
        if last and last.path == path:
            prev = last.segs[-1]
            middle = (seg.y0 + seg.y1) / 2
            if abs(middle - (prev.y0 + prev.y1) / 2) < 0.45 * max(seg.size, prev.size) \
                    and seg.x0 >= prev.x1 - 0.5 * seg.size:
                last.add(seg)
                continue
        lines.append(_Line(seg, path))
    return [line.finish() for line in lines]


def _marker(line):
    """("bullet"/"number", segment, characters) when a line starts a list
    item: the marker's own segment when it stands apart from the text,
    else None and how many characters of the first segment it is."""
    first = line.segs[0]
    text = first.text.strip()
    if len(line.segs) > 1:
        if len(text) == 1 and (text in BULLETS or (not text.isalnum() and text not in "([\"'“‘")):
            return "bullet", first, 0
        if NUMBER_MARK.match(text):
            return "number", first, 0
    if len(text) > 2 and text[0] in BULLETS and text[1] == " " and text[0] not in "-*":
        return "bullet", None, 1
    if len(text) > 2 and text[0] in "-*" and text[1] == " ":
        return "bullet", None, 1
    m = NUMBER_START.match(text)
    if m:
        return "number", None, len(m.group(0).replace(" ", ""))
    return None


class _Block:
    """A paragraph, heading candidate or list item, built from lines."""

    def __init__(self, kind, line, marker=None):
        self.kind = kind          # "p" or "li"
        self.lines = [line]
        self.marker = marker      # (kind, segment or None, characters) for "li"
        self.label_x = line.x0
        rest = [s for s in line.segs if marker is None or s is not marker[1]]
        self.text_x = min(s.x0 for s in rest) if rest else line.x0
        if marker and marker[1] is None and 0 < marker[2] <= len(line.segs[0].rights):
            first = line.segs[0]
            self.text_x = first.rights[marker[2] - 1] + 0.2 * first.size

    @property
    def size(self):
        return max(line.size for line in self.lines)

    @property
    def bold(self):
        return all(line.bold for line in self.lines)

    @property
    def text(self):
        out = ""
        for line in self.lines:
            if out.endswith("-") and line.text[:1].islower():
                out = out[:-1] + line.text
            else:
                out = (out + " " + line.text).strip()
        return out

    @property
    def box(self):
        return _union((line.x0, line.y0, line.x1, line.y1) for line in self.lines)


def _continues(block, line, right_edge):
    """Whether ``line`` goes on with ``block`` (rather than starting a new
    paragraph): same column and look, the usual line spacing, lined up."""
    last = block.lines[-1]
    size = max(last.size, line.size)
    if line.path != last.path:
        return False
    if abs(line.size - block.lines[0].size) > 0.15 * block.lines[0].size or line.bold != last.bold:
        return False
    pitch = last.base - line.base
    if pitch <= 0 or pitch > 1.7 * size:
        return False
    if len(block.lines) >= 2:
        usual = block.lines[-2].base - last.base
        if pitch > usual * 1.3 + 1:
            return False
    if line.x1 < block.box[0] - size or line.x0 > max(l.x1 for l in block.lines) + size:
        return False
    if block.kind == "li":
        # wrapped lines line up with the item's text, or (without a hanging
        # indent) with its number or bullet
        if not block.label_x - 0.6 * size <= line.x0 <= block.text_x + 3 * size:
            return False
    # a short line ending a sentence ends its paragraph
    if last.x1 < right_edge - max(2.5 * size, 0.12 * (right_edge - block.box[0])) \
            and re.search(r"[.!?:;\"”’)]$", last.text):
        return False
    if block.kind == "li":
        return True
    if line.x0 > last.x0 + 0.8 * size:
        return False  # an indented first line starts a paragraph
    if line.x0 < last.x0 - 0.8 * size and len(block.lines) >= 2:
        return False
    return True


def _text_blocks(lines, body):
    """Paragraphs and list items from lines in reading order."""
    right = {}
    for line in lines:
        right[line.path] = max(right.get(line.path, 0), line.x1)
    blocks = []
    for line in lines:
        marker = _marker(line)
        if marker and (line.size >= body * 1.12 or (line.bold and marker[0] == "number")):
            marker = None  # "2. Methods" set as a heading
        last = blocks[-1] if blocks else None
        if marker is None and last is not None and _continues(last, line, right[line.path]):
            last.lines.append(line)
            continue
        blocks.append(_Block("li" if marker else "p", line, marker))
    return blocks


# --------------------------------------------------------------------------
# Tables (pdfplumber) as grids of cells
# --------------------------------------------------------------------------

def _plumber_tables(ppage, view):
    """Tables on a pdfplumber page: [{"box", "rows", "plumber"}] in seen
    coordinates. Ruled tables come with the boxes of their cells, row by row
    (None where a merged cell covers one); borderless ones get theirs from
    the text later ("rows": None). "plumber": the box in pdfplumber's
    coordinates."""
    from pdfmanager import tables

    found = []
    try:
        settings = dict(tables.RULED, explicit_vertical_lines=tables._closing_edges(ppage))
        for table in ppage.find_tables(settings):
            raw_rows = table.extract()
            if tables._stacked(raw_rows) or not tables._tidy(raw_rows):
                continue
            rows = []
            for row in table.rows:
                rows.append([view.rect(tables._pdf_bbox(ppage, c)) if c else None for c in row.cells])
            found.append({"box": view.rect(tables._pdf_bbox(ppage, table.bbox)), "rows": rows,
                          "plumber": table.bbox})
        words = [w for w in ppage.extract_words(extra_attrs=["size"])
                 if w.get("upright", True)
                 and not any(tables._inside(w, t["plumber"]) for t in found)]
        for run in tables._runs(tables._lines(words)):
            table = tables._aligned_table(run)
            if table:
                box = view.rect(tables._pdf_bbox(ppage, table["bbox"]))
                if not any(_overlap(box, t["box"]) > 0.5 * _area(box) for t in found):
                    found.append({"box": box, "rows": None, "plumber": table["bbox"]})
    except Exception:  # pdfplumber trips on some damaged pages: no tables then
        return found
    return found


def _may_have_tables(segs, units):
    """Whether a page is worth searching for tables (pdfplumber is slow): it
    has ruled lines or boxes, or several lines with wide gaps in them."""
    if not segs:
        return False
    if sum(1 for u in units if u.kind == "path") >= 4:
        return True
    rows = Counter(round(s.base) for s in segs if s.role is None)
    return sum(1 for n in rows.values() if n >= 2) >= 3


def _ruled_grid(rows):
    """Cells with their column and row spans, from pdfplumber's cell boxes."""
    cells = [c for row in rows for c in row if c]
    if not cells:
        return []
    cols = sorted({round(c[0], 1) for c in cells})
    tops = sorted({round(c[3], 1) for c in cells}, reverse=True)
    grid = []
    for row in rows:
        out = []
        for c in row:
            if not c:
                continue
            colspan = max(1, sum(1 for x in cols if c[0] - 1 <= x < c[2] - 1))
            rowspan = max(1, sum(1 for y in tops if c[1] + 1 < y <= c[3] + 1))
            out.append({"box": c, "colspan": colspan, "rowspan": rowspan})
        if out:
            grid.append(out)
    return grid


def _aligned_grid(box, segs):
    """Rows and columns of a borderless table from the segments in it, and
    the segments used. Lines in a bigger font (a heading after the table)
    and lone lines at either end aren't part of it."""
    inside = sorted((s for s in segs if s.role is None and _overlap(s.box, box) > 0.5 * _area(s.box)),
                    key=lambda s: (-s.base, s.x0))
    rows = []
    for seg in inside:
        if rows and abs(rows[-1][0].base - seg.base) < 0.5 * seg.size:
            rows[-1].append(seg)
        else:
            rows.append([seg])
    if not rows:
        return [], []
    usual = statistics.median(s.size for s in inside)
    for n, row in enumerate(rows):
        if n and max(s.size for s in row) > usual * 1.25:
            rows = rows[:n]
            break
    while rows and len(rows[0]) < 2:
        rows.pop(0)
    while rows and len(rows[-1]) < 2:
        rows.pop()
    if len(rows) < 2:
        return [], []
    widest = max(rows, key=len)
    starts = sorted(s.x0 for s in widest)
    grid = []
    for row in rows:
        cells = [[] for _ in starts]
        for seg in row:
            col = max([i for i, x in enumerate(starts) if x <= seg.x0 + seg.size] or [0])
            cells[col].append(seg)
        grid.append([{"box": _union(s.box for s in c) if c else None, "colspan": 1, "rowspan": 1}
                     for c in cells])
    return grid, [s for row in rows for s in row]


# --------------------------------------------------------------------------
# The structure, as Python objects first
# --------------------------------------------------------------------------

class _Elem:
    """A structure element before it's written: type, page, kids (other
    elements, ("mcid", n) and ("objr", annotation)), attributes."""

    def __init__(self, S, page=None, inline=False):
        self.S, self.page, self.kids = S, page, []
        self.inline = inline     # kids sorted in content order (MCIDs and Links inside text)
        self.alt = None
        self.attrs = None
        self.style = None        # heading candidate: (size, bold)
        self.depth = None        # heading numbering depth ("1.2" -> 2)
        self.text = ""
        self.ref = None

    def add(self, kid):
        self.kids.append(kid)
        return kid


ARTIFACT = ("artifact", None)
ARTIFACT_HEADER = ("artifact", "Header")
ARTIFACT_FOOTER = ("artifact", "Footer")
ARTIFACT_BACKGROUND = ("artifact", "Background")
UNMARKED = ("unmarked", None)   # a form tagged inside: not wrapped where it's drawn


class _Targets:
    """Boxes of the elements that hold a page's content, to find the one
    a drawing operator belongs to; boxes are bucketed by height."""

    BAND = 24.0

    def __init__(self):
        self.items = []    # (box, key)
        self.bands = {}

    def add(self, box, key):
        if box is None or box[2] <= box[0] or box[3] <= box[1]:
            return
        index = len(self.items)
        self.items.append((box, key))
        for band in range(int(box[1] // self.BAND), int(box[3] // self.BAND) + 1):
            self.bands.setdefault(band, []).append(index)

    def best(self, box):
        """The key whose boxes overlap ``box`` most; else the nearest one."""
        if not self.items:
            return None
        scores = {}
        if box[3] >= box[1]:
            for band in range(int(box[1] // self.BAND), int(box[3] // self.BAND) + 1):
                for index in self.bands.get(band, ()):
                    tbox, key = self.items[index]
                    area = _overlap(box, tbox)
                    if area > 0:
                        scores[id(key)] = (scores.get(id(key), (0, key))[0] + area, key)
        if scores:
            return max(scores.values(), key=lambda v: v[0])[1]
        tbox, key = min(self.items, key=lambda item: _distance(box, item[0]))
        return key


# --------------------------------------------------------------------------
# Tagging one page
# --------------------------------------------------------------------------

def _annotations(page_obj):
    import pikepdf

    annots = page_obj.get("/Annots")
    if not isinstance(annots, pikepdf.Array):
        return []
    found = []
    for i, annot in enumerate(annots):
        if not isinstance(annot, pikepdf.Dictionary):
            continue
        found.append((i, annot))
    return found


def _annot_box(annot, view):
    try:
        rect = [float(v) for v in annot.Rect]
        return view.rect((min(rect[0], rect[2]), min(rect[1], rect[3]),
                          max(rect[0], rect[2]), max(rect[1], rect[3])))
    except (AttributeError, TypeError, ValueError, IndexError):
        return None


def _figures(units, segs, size, view):
    """Pictures on the page: figures, decoration and backgrounds. Pictures
    that touch are one figure. Returns [{"box", "units", "segs", "kind"}]
    with kind "figure", "decoration" or "background"."""
    w, h = size
    page_box = (0, 0, w, h)
    found = []
    for unit in units:
        if unit.kind not in ("image", "form-image") or unit.box is None:
            continue
        box = view.rect(unit.box)
        if unit.kind == "form-image" and any(
                s.role is None and _overlap(s.box, box) > 0.5 * _area(s.box) for s in segs):
            unit.kind = "form"  # a form with text in it (e.g. a whole page): not a picture
            continue
        clipped = (max(box[0], 0), max(box[1], 0), min(box[2], w), min(box[3], h))
        if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
            found.append({"box": box, "units": [unit], "segs": [], "kind": "decoration"})
            continue
        for fig in found:
            if fig["kind"] == "figure" and _distance(fig["box"], clipped) <= 2:
                fig["box"] = _union([fig["box"], clipped])
                fig["units"].append(unit)
                break
        else:
            found.append({"box": clipped, "units": [unit], "segs": [], "kind": "figure"})
    for fig in found:
        if fig["kind"] != "figure":
            continue
        box = fig["box"]
        if box[2] - box[0] < 16 or box[3] - box[1] < 16:
            fig["kind"] = "decoration"
            continue
        inside = [s for s in segs if s.role is None and _overlap(s.box, box) > 0.6 * _area(s.box)]
        words = sum(len(s.text.split()) for s in inside)
        text_area = sum(_area(s.box) for s in inside)
        if _area(box) >= 0.5 * _area(page_box) and inside:
            fig["kind"] = "background"
        elif words > 30 or text_area > 0.3 * _area(box):
            fig["kind"] = "background"
        else:
            fig["segs"] = inside
    return found


class _PageTagger:
    """Works out, for one page, the elements in reading order and which of
    them each drawing operator belongs to."""

    def __init__(self, number, page, view, segs, units, tables_found, body):
        self.number, self.page, self.view = number, page, view
        self.segs, self.units, self.tables = segs, units, tables_found
        self.body = body
        self.size = view.size
        self.targets = _Targets()
        self.elements = []        # top-level, in reading order
        self.figures = []         # figure elements, in reading order
        self.fallback = None
        self.links = []           # (index, annotation, box) of links
        self.link_texts = {}      # index -> the text under the link
        self.link_elems = {}      # index -> (annotation, Link element)
        self.annot_elems = []     # (index, annotation, Form or Annot element)

    def run(self, annots):
        figures = _figures(self.units, self.segs, self.size, self.view)
        absorbed = {id(s) for f in figures for s in f["segs"]}
        tables_kept = []
        for table in self.tables:
            if table["rows"]:
                table["grid"] = _ruled_grid(table["rows"])
            else:
                table["grid"], used = _aligned_grid(table["box"], self.segs)
                if used:
                    table["box"] = _union(s.box for s in used)
            if table["grid"]:
                tables_kept.append(table)
        self.tables = tables_kept
        table_boxes = [t["box"] for t in self.tables]
        items = []
        for seg in self.segs:
            if seg.role in ("header", "footer"):
                key = ARTIFACT_HEADER if seg.role == "header" else ARTIFACT_FOOTER
                self.targets.add(seg.box, key)
                continue
            if id(seg) in absorbed:
                continue
            if any(_overlap(seg.box, t) > 0.5 * _area(seg.box) for t in table_boxes):
                continue
            items.append(_Item("seg", seg.box, seg, seg.size))
        for table in self.tables:
            items.append(_Item("table", table["box"], table, self.body))
        for fig in figures:
            if fig["kind"] == "figure":
                items.append(_Item("figure", fig["box"], fig, self.body))
        ordered = _reading_order(items, self.body)
        self._build(ordered, figures)
        self._place_annotations(annots)
        self._assign()
        return self

    # -- elements in reading order ------------------------------------------

    def _build(self, ordered, figures):
        run = []
        blocks = []   # ("text", _Block) / ("table", t) / ("figure", f)

        def flush():
            if run:
                for block in _text_blocks(_lines(run), self.body):
                    blocks.append(("text", block))
                run.clear()

        for item, path in ordered:
            if item.kind == "seg":
                run.append((item.obj, path))
            else:
                flush()
                blocks.append((item.kind, item.obj))
        flush()
        captions = self._captions(blocks)
        lists = []    # the open lists (see _list_item)
        for i, (kind, obj) in enumerate(blocks):
            if kind != "text" or obj.kind != "li":
                lists = []
            if kind == "text":
                if obj.kind == "li":
                    self._list_item(obj, lists)
                    continue
                elem = _Elem("Caption" if i in captions else "P", self.number, inline=True)
                elem.text = obj.text
                if i not in captions:
                    elem.style = _heading_style(obj, self.body)
                    m = NUMBERED_HEADING.match(elem.text)
                    if m and elem.style:
                        elem.depth = m.group(1).count(".") + 1
                for line in obj.lines:
                    self.targets.add((line.x0, line.y0, line.x1, line.y1), elem)
                self.elements.append(elem)
            elif kind == "table":
                self.elements.append(self._table(obj))
            elif kind == "figure":
                elem = _Elem("Figure", self.number, inline=True)
                elem.attrs = {"BBox": obj["box"]}
                caption = captions.get(("figure", i))
                if caption is not None:
                    elem.alt = blocks[caption][1].text
                self.targets.add(obj["box"], elem)
                for unit in obj["units"]:
                    unit.key = elem
                self.figures.append(elem)
                self.elements.append(elem)
        for fig in figures:
            key = ARTIFACT_BACKGROUND if fig["kind"] == "background" else ARTIFACT
            if fig["kind"] != "figure":
                for unit in fig["units"]:
                    unit.key = key

    def _captions(self, blocks):
        """Text blocks that caption a figure or table next to them:
        {block index: True, ("figure", figure index): caption index}."""
        found = {}
        for i, (kind, obj) in enumerate(blocks):
            if kind not in ("figure", "table"):
                continue
            box = obj["box"]
            pattern = TABLE_CAPTION if kind == "table" else CAPTION
            for j in (i + 1, i - 1, i + 2, i - 2):
                if not 0 <= j < len(blocks) or j in found:
                    continue
                k, block = blocks[j]
                if k != "text" or block.kind != "p" or len(block.text) > 400:
                    continue
                bb = block.box
                near = _distance(bb, box) <= 3 * max(block.size, 8)
                if near and pattern.match(block.text):
                    found[j] = True
                    found[(kind, i)] = j
                    break
        return found

    def _list_item(self, block, lists):
        """Add a list item: to the open list at its indent, a new list inside
        the last item when it's indented further, or a new list. ``lists``:
        the open lists, outermost first: (L element, marker kind, label x,
        text x)."""
        size = block.size
        kind = block.marker[0]
        numbering = {"ListNumbering": "Decimal" if kind == "number" else "Disc"}
        while lists and block.label_x < lists[-1][2] - 0.5 * size:
            lists.pop()
        if lists and block.label_x >= lists[-1][3] - 0.5 * size and lists[-1][0].kids:
            # deeper than the open list's text: a list inside its last item
            sub = _Elem("L", self.number)
            sub.attrs = numbering
            lists[-1][0].kids[-1].kids[-1].add(sub)
            lists.append((sub, kind, block.label_x, block.text_x))
        elif not lists or abs(block.label_x - lists[-1][2]) > 0.5 * size or lists[-1][1] != kind:
            top = _Elem("L", self.number)
            top.attrs = numbering
            if len(lists) > 1:   # a list next to the previous one at the same depth
                lists.pop()
                lists[-1][0].kids[-1].kids[-1].add(top)
                lists.append((top, kind, block.label_x, block.text_x))
            else:
                self.elements.append(top)
                lists[:] = [(top, kind, block.label_x, block.text_x)]
        li = lists[-1][0].add(_Elem("LI", self.number))
        marker_seg, chars = block.marker[1], block.marker[2]
        first = block.lines[0].segs[0]
        split = None
        if marker_seg is None and 0 < chars <= len(first.rights):
            split = first.rights[chars - 1]
        if marker_seg is not None or split is not None:
            lbl = li.add(_Elem("Lbl", self.number, inline=True))
            self.targets.add(marker_seg.box if marker_seg is not None else
                             (first.x0, first.y0, split, first.y1), lbl)
        body = li.add(_Elem("LBody", self.number, inline=True))
        body.text = block.text
        for line in block.lines:
            for seg in line.segs:
                if seg is marker_seg:
                    continue
                if seg is first and split is not None:
                    self.targets.add((split, seg.y0, seg.x1, seg.y1), body)
                else:
                    self.targets.add(seg.box, body)

    def _table(self, table):
        elem = _Elem("Table", self.number)
        elem.attrs = {"BBox": table["box"]}
        grid = table["grid"]
        for r, row in enumerate(grid):
            tr = elem.add(_Elem("TR", self.number))
            for cell in row:
                td = tr.add(_Elem("TH" if r == 0 else "TD", self.number, inline=True))
                attrs = {}
                if r == 0:
                    attrs["Scope"] = "Column"
                if cell["colspan"] > 1:
                    attrs["ColSpan"] = cell["colspan"]
                if cell["rowspan"] > 1:
                    attrs["RowSpan"] = cell["rowspan"]
                td.attrs = attrs or None
                if cell["box"] is not None:
                    self.targets.add(cell["box"], td)
        return elem

    # -- annotations ----------------------------------------------------------

    def _place_annotations(self, annots):
        boxes = []
        for elem in self.elements:
            box = self._elem_box(elem)
            boxes.append(box)
        for index, annot in annots:
            subtype = str(annot.get("/Subtype", ""))
            flags = int(_num(annot.get("/F", 0)))
            if subtype in ("/Popup", "/PrinterMark", "/TrapNet") or flags & (2 | 32):
                continue
            box = _annot_box(annot, self.view)
            if box is None:
                continue
            if subtype == "/Link":
                self.links.append((index, annot, box))
                continue
            elem = _Elem("Form" if subtype == "/Widget" else "Annot", self.number)
            elem.add(("objr", index))
            self.annot_elems.append((index, annot, elem))
            # after the element it overlaps or sits nearest to
            at = len(self.elements)
            if boxes:
                scores = [(_overlap(box, b) if b else 0, -_distance(box, b) if b else -1e9)
                          for b in boxes]
                at = max(range(len(boxes)), key=lambda k: scores[k]) + 1
            self.elements.insert(at, elem)
            boxes.insert(at, box)

    def _elem_box(self, elem):
        boxes = [box for box, key in self.targets.items if key is elem]
        if boxes:
            return _union(boxes)
        for kid in elem.kids:
            if isinstance(kid, _Elem):
                box = self._elem_box(kid)
                if box:
                    return box
        return None

    # -- operators -> elements ----------------------------------------------

    def _assign(self):
        w, h = self.size
        view = self.view
        texts = []
        for unit in self.units:
            if unit.key is not None:
                continue
            if unit.kind == "text":
                texts.append(unit)
                if unit.blank:
                    continue
                unit.key = self.targets.best(view.rect(unit.box)) or self._fallback()
            elif unit.kind in ("path", "shading", "image", "form-image"):
                unit.key = ARTIFACT
                if unit.kind == "path" and unit.box is not None:
                    box = view.rect(unit.box)
                    for fig in self.figures:
                        fbox = fig.attrs["BBox"]
                        if _overlap(box, fbox) >= 0.9 * max(_area(box), 0.01) and _area(box) > 0:
                            unit.key = fig
                            break
            elif unit.kind == "tagged-form":
                unit.key = UNMARKED
            elif unit.kind == "form":
                box = view.rect(unit.box) if unit.box else (0, 0, w, h)
                key = self.targets.best(box)
                has_text = any(_overlap(seg.box, box) > 0 for seg in self.segs)
                unit.key = key if (key is not None and has_text) else ARTIFACT
        # blank text joins its neighbour in the same text object
        for k, unit in enumerate(texts):
            if unit.key is not None:
                continue
            near = [u for u in texts[max(0, k - 1):k] + texts[k + 1:k + 2] if u.key is not None
                    and u.in_text == unit.in_text]
            unit.key = near[0].key if near else ARTIFACT
        self._links(texts)

    def _fallback(self):
        if self.fallback is None:
            self.fallback = _Elem("P", self.number, inline=True)
            self.elements.append(self.fallback)
        return self.fallback

    def _links(self, texts):
        hosts = {}
        for index, annot, box in self.links:
            inside = [u for u in texts if u.key is not None and isinstance(u.key, _Elem)
                      and u.key.S != "Link" and _area(self.view.rect(u.box)) > 0
                      and _overlap(self.view.rect(u.box), _grow(box, 1))
                      >= 0.5 * _area(self.view.rect(u.box))]
            link = _Elem("Link", self.number, inline=True)
            link.text = self.link_texts.get(index, "")
            if inside:
                host = Counter(id(u.key) for u in inside).most_common(1)[0][0]
                host = next(u.key for u in inside if id(u.key) == host)
                for unit in inside:
                    if unit.key is host:
                        unit.key = link
            else:
                host = self.targets.best(box)
                if not isinstance(host, _Elem):
                    host = None
            link.add(("objr", index))
            if host is None:
                self.elements.append(link)
            else:
                host.add(link)
            hosts[index] = (annot, link)
        self.link_elems = hosts


def _grow(box, by):
    return (box[0] - by, box[1] - by, box[2] + by, box[3] + by)


def _heading_style(block, body):
    """(size, bold) when a block looks like a heading, else None."""
    text = block.text
    if len(text) > 200 or len(block.lines) > 3 or not re.search(r"[^\W\d_]", text):
        return None
    if PAGE_NUMBER.match(text):
        return None
    size = round(block.size * 2) / 2
    if block.size >= body * 1.12:
        return (size, block.bold)
    if block.bold and len(block.lines) <= 2 and len(text) <= 120 and block.size >= body * 0.9 \
            and not text.endswith((".", ",", ";")):
        return (size, True)
    return None


def _heading_levels(elements):
    """Turn heading candidates into H1-H6: bigger (and bolder) first,
    numbering ("1.2") wins, and no level is skipped."""
    styles = sorted({e.style for e in elements if e.style}, key=lambda s: (-s[0], not s[1]))
    rank = {style: i + 1 for i, style in enumerate(styles)}
    previous = 0
    for elem in elements:
        if not elem.style:
            continue
        level = elem.depth or rank[elem.style]
        level = max(1, min(level, previous + 1, 6))
        elem.S = f"H{level}"
        previous = level


def _walk(elements):
    for elem in elements:
        yield elem
        yield from _walk([k for k in elem.kids if isinstance(k, _Elem)])


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


# --------------------------------------------------------------------------
# Opening
# --------------------------------------------------------------------------

def _open_pikepdf(path, password, data=None):
    import pikepdf

    source = BytesIO(data) if data is not None else str(path)
    for candidate in dict.fromkeys([password or "", ""]):
        try:
            if isinstance(source, BytesIO):
                source.seek(0)
            return pikepdf.open(source, password=candidate)
        except pikepdf.PasswordError:
            continue
        except pikepdf.PdfError as exc:
            raise core.PdfManagerError(f"{path} can't be read ({exc})") from None
    if password:
        raise core.PdfManagerError(f"wrong password for {path}")
    raise core.PdfManagerError(f"{path} is encrypted; supply a password")


def _open_pdfium(path, password):
    import pypdfium2 as pdfium

    last = None
    for candidate in dict.fromkeys([password or None, None]):
        try:
            return pdfium.PdfDocument(str(path), password=candidate)
        except pdfium.PdfiumError as exc:
            last = exc
    raise core.PdfManagerError(f"{path} can't be read ({last})")


def _is_tagged(pdf):
    root = pdf.Root
    tree = root.get("/StructTreeRoot")
    return tree is not None and tree.get("/K") is not None


PLACEHOLDER_TITLES = {"", "(anonymous)", "anonymous", "untitled", "(untitled)", "title",
                      "document", "untitled document"}


def _title_of(pdf):
    """The title in the document's information or XMP metadata (not
    placeholders such as "untitled")."""
    title = ""
    try:
        info = pdf.docinfo
        if "/Title" in info:
            title = str(info.Title).strip()
    except Exception:
        title = ""
    if title.lower() in PLACEHOLDER_TITLES:
        title = ""
    if not title:
        try:
            meta = pdf.open_metadata()
            title = (meta.get("dc:title") or "").strip()
        except Exception:
            title = ""
    return "" if title.lower() in PLACEHOLDER_TITLES else title


def _set_title(pdf, title):
    import pikepdf

    pdf.docinfo["/Title"] = pikepdf.String(title)
    try:
        with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
            meta["dc:title"] = title
    except Exception:  # damaged XMP: the information dictionary still has it
        pass


def _finish_document(pdf, lang, title):
    """What a tagged document needs besides its tags: Marked, language,
    title shown in the window, tab order following the structure."""
    import pikepdf

    root = pdf.Root
    root.MarkInfo = pikepdf.Dictionary(Marked=True)
    root.Lang = pikepdf.String(lang)
    prefs = root.get("/ViewerPreferences")
    if not isinstance(prefs, pikepdf.Dictionary):
        prefs = pikepdf.Dictionary()
        root.ViewerPreferences = prefs
    prefs.DisplayDocTitle = True
    if title:
        _set_title(pdf, title)
    for page in pdf.pages:
        page.obj.Tabs = pikepdf.Name.S


def _parse_alt(alt):
    """{page: [texts]} with int pages, from the caller's or JSON's form."""
    out = {}
    for page, texts in (alt or {}).items():
        try:
            number = int(page)
        except (TypeError, ValueError):
            raise core.PdfManagerError(f"alt text: {page!r} isn't a page number") from None
        if isinstance(texts, str):
            texts = [texts]
        out[number] = [str(t) if t is not None else "" for t in texts]
    return out


# --------------------------------------------------------------------------
# Tagging
# --------------------------------------------------------------------------

def autotag(path, output, password=None, lang=None, title=None, alt=None, force=False):
    """Write a tagged copy of ``path`` to ``output`` (see the module
    docstring). ``lang``: a language code such as "en" or "de-CH" (None or
    "auto": guessed from the text); ``title``: the document's title (else
    the one it has, else its first heading); ``alt``: descriptions of the
    pictures, {page number: [text of the 1st figure, 2nd, ...]};
    ``force``: replace tags the file already has. The copy isn't encrypted.

    Returns {"pages", "tagged" (False when existing tags were kept),
    "elements": {type: count}, "lang", "title", "figures_without_alt":
    [page numbers], "untagged_pages": [pages whose content couldn't be read]}."""
    path, output = Path(path), Path(output)
    if lang and str(lang).strip().lower() not in ("", "auto"):
        lang = str(lang).strip()
        if not LANG_RE.match(lang):
            raise core.PdfManagerError(f"{lang!r} isn't a language code; use one like en, de, "
                                       "fr or pt-BR")
    else:
        lang = None
    alt = _parse_alt(alt)
    core.open_pdf(path, password)  # the usual messages about unreadable files and passwords
    data = path.read_bytes()
    pdf = _open_pikepdf(path, password, data)
    with pdf:
        result = {"file": str(path), "pages": len(pdf.pages), "tagged": True, "elements": {},
                  "figures_without_alt": [], "untagged_pages": []}
        if _is_tagged(pdf) and not force:
            lang = lang or str(pdf.Root.get("/Lang") or "").strip() \
                or guess_language(_sample_text(path, password, 30))
            title = title or _title_of(pdf) or path.stem
            _finish_document(pdf, lang, title)
            result.update(tagged=False, lang=lang, title=title)
            _save(pdf, output)
            return result
        _clear_tags(pdf)
        elements, pages_info, sample = _tag_pages(pdf, path, password, alt, result)
        lang = lang or str(pdf.Root.get("/Lang") or "").strip() or guess_language(sample)
        title = (title or _title_of(pdf) or next((e.text for e in _walk(elements)
                                                  if e.S == "H1" and e.text), "")
                 or path.stem)
        elements = _write_tree(pdf, elements, pages_info)
        _finish_document(pdf, lang, title[:300])
        counts = Counter(e.S for e in _walk(elements))
        result.update(lang=lang, title=title[:300], elements=dict(counts),
                      bookmarks=_add_bookmarks(pdf, elements))
        _save(pdf, output)
    return result


def _add_bookmarks(pdf, elements):
    """Bookmarks from the headings (H1-H3) of a long document that has none,
    as accessible documents should. Returns how many were added."""
    import pikepdf

    if len(pdf.pages) <= BOOKMARK_PAGES:
        return 0
    heads = [(int(e.S[1]), " ".join(e.text.split())[:200], e.page) for e in _walk(elements)
             if re.fullmatch(r"H[1-3]", e.S) and e.text.strip() and e.page is not None]
    if len(heads) < 2:
        return 0
    try:
        with pdf.open_outline() as outline:
            if outline.root:
                return 0
            stack = [(0, outline.root)]
            for level, title, page in heads:
                item = pikepdf.OutlineItem(title, page)
                while stack[-1][0] >= level:
                    stack.pop()
                stack[-1][1].append(item)
                stack.append((level, item.children))
    except (pikepdf.PdfError, pikepdf.OutlineStructureError):
        return 0
    return len(heads)


def _save(pdf, output):
    output = Path(output)
    if output.is_dir():
        raise core.PdfManagerError(f"{output} is a folder; give the name of the PDF to write")
    output.parent.mkdir(parents=True, exist_ok=True)
    buffer = BytesIO()
    pdf.save(buffer, min_version="1.7", encryption=False)
    output.write_bytes(buffer.getvalue())


def _sample_text(path, password, pages):
    with core.PDFIUM_LOCK:
        doc = _open_pdfium(path, password)
        try:
            parts = []
            for i in range(min(len(doc), pages)):
                textpage = doc[i].get_textpage()
                parts.append(textpage.get_text_range())
            return " ".join(parts)
        finally:
            doc.close()


def _clear_tags(pdf):
    """Take away the tags a file has (for ``force``), and the markings
    that only tagging uses, so new tags don't mix with old ones."""
    root = pdf.Root
    for key in ("/StructTreeRoot", "/MarkInfo"):
        if key in root:
            del root[key]
    for page in pdf.pages:
        obj = page.obj
        if "/StructParents" in obj:
            del obj["/StructParents"]
        for _, annot in _annotations(obj):
            if "/StructParent" in annot:
                del annot["/StructParent"]


def _step(done, pages, what):
    """Progress: reading the pages, then tagging them (two passes)."""
    progress.report(fraction_in_part=done / (2 * max(pages, 1)))
    progress.say(f"{what} page {done % max(pages, 1) + 1} of {pages}")


def _tag_pages(pdf, path, password, alt, result):
    """Read every page's layout and tag it. Returns (top-level elements in
    reading order, per-page writing info, text sample for the language)."""
    import pikepdf

    pages = []
    with core.PDFIUM_LOCK:
        doc = _open_pdfium(path, password)
        try:
            for i, page in enumerate(pdf.pages):
                _step(i, len(pdf.pages), "reading")
                obj = page.obj
                pdfium_page = doc[i]
                textpage = pdfium_page.get_textpage()
                turn = _text_direction(textpage)
                if turn is None:
                    turn = int(_num(_inherited(obj, "/Rotate"), 0))
                view = _View(_inherited(obj, "/MediaBox") or [0, 0, 612, 792], turn)
                links = {}
                for index, annot in _annotations(obj):
                    if str(annot.get("/Subtype", "")) == "/Link":
                        try:
                            r = [float(v) for v in annot.Rect]
                            text = textpage.get_text_bounded(min(r[0], r[2]), min(r[1], r[3]),
                                                             max(r[0], r[2]), max(r[1], r[3]))
                            links[index] = " ".join(text.split())
                        except Exception:  # a damaged rectangle: described by its address
                            pass
                pages.append({"view": view, "size": view.size, "links": links,
                              "segs": _page_segments(textpage, view)})
                textpage.close()
                pdfium_page.close()
        finally:
            doc.close()
    _mark_page_furniture(pages)
    body = _body_size(pages)
    sample = " ".join(s.text for p in pages[:30] for s in p["segs"] if s.role is None)

    from pdfmanager import tables

    fonts, forms = {}, {}
    inside = _single_use_forms(pdf)
    stripped = set(inside)  # their old marks go when they're tagged inside
    elements, infos = [], []
    try:
        plumber = tables._open(path, password)
    except core.PdfManagerError:
        plumber = None
    try:
        for i, page in enumerate(pdf.pages):
            _step(len(pdf.pages) + i, len(pdf.pages), "tagging")
            info = pages[i]
            obj = page.obj
            resources = _inherited(obj, "/Resources") or pikepdf.Dictionary()
            try:
                instructions = pikepdf.parse_content_stream(page)
            except (pikepdf.PdfError, ValueError, TypeError):
                result["untagged_pages"].append(i + 1)
                infos.append(None)
                continue
            units = _Scanner(resources, fonts, forms, inside).run(instructions)
            found = []
            if plumber is not None and _may_have_tables(info["segs"], units):
                try:
                    ppage = plumber.pages[i]
                    found = _plumber_tables(ppage, info["view"])
                    ppage.close()
                except Exception:  # pdfplumber trips on some damaged pages
                    found = []
            tagger = _PageTagger(i, page, info["view"], info["segs"], units, found, body)
            tagger.link_texts = info["links"]
            tagger.run(_annotations(obj))
            texts = alt.get(i + 1, [])
            for n, fig in enumerate(tagger.figures):
                if n < len(texts) and texts[n]:
                    fig.alt = texts[n]
                if not fig.alt:
                    result["figures_without_alt"].append(i + 1)
            elements.extend(tagger.elements)
            infos.append({"units": units, "tagger": tagger})
            _strip_xobjects((resources.get("/XObject") or {}), stripped)
            for unit in units:
                if unit.kind == "tagged-form":  # the forms these draw are tagged as a whole
                    _strip_xobjects((unit.xobj.get("/Resources") or {}).get("/XObject") or {},
                                    stripped)
    finally:
        if plumber is not None:
            plumber.close()
    _heading_levels([e for e in _walk(elements) if e.S == "P"])
    pages_info = {"infos": infos}
    return elements, pages_info, sample


def _write_tree(pdf, elements, pages_info):
    """Rewrite the pages' content with marked content and build the
    structure tree, ParentTree and /StructParent(s) entries. Returns the
    top-level elements that hold something."""
    import pikepdf

    Name, Dictionary, Array = pikepdf.Name, pikepdf.Dictionary, pikepdf.Array
    parent_tree = []          # [(key, [element of each MCID] or ("annot", element))]
    next_key = 0
    drawn = [0]               # the order content is drawn in, for ordering the kids
    for i, page in enumerate(pdf.pages):
        info = pages_info["infos"][i]
        if info is None:
            continue
        instructions = pikepdf.parse_content_stream(page)
        count = [0]    # MCIDs are numbered across the page and the forms tagged inside
        owners = {}    # stream (None: the page) -> {MCID: element}

        def opener(stream, count=count, owners=owners):
            mine = owners.setdefault(stream.objgen if stream is not None else None, {})

            def open_mc(key):
                if isinstance(key, _Elem):
                    mcid = count[0]
                    count[0] += 1
                    drawn[0] += 1
                    mine[mcid] = key
                    key.add(("mcid", mcid, drawn[0], stream))
                    return pikepdf.ContentStreamInstruction(
                        [Name("/" + key.S), Dictionary(MCID=mcid)], pikepdf.Operator("BDC"))
                if key[1] in ("Header", "Footer"):
                    props = Dictionary(Type=Name.Pagination, Subtype=Name("/" + key[1]))
                    return pikepdf.ContentStreamInstruction([Name.Artifact, props],
                                                            pikepdf.Operator("BDC"))
                if key[1] == "Background":
                    return pikepdf.ContentStreamInstruction(
                        [Name.Artifact, Dictionary(Type=Name.Background)], pikepdf.Operator("BDC"))
                return pikepdf.ContentStreamInstruction([Name.Artifact], pikepdf.Operator("BMC"))
            return open_mc

        inner = {}
        for unit in info["units"]:
            if unit.stream is not None:
                inner.setdefault(unit.stream.objgen, []).append(unit)
        forms = []

        def on_form(unit, inner=inner, opener=opener, forms=forms):
            form = unit.xobj
            new = _rewrite(pikepdf.parse_content_stream(form), inner.get(form.objgen, []),
                           opener(form))
            form.write(pikepdf.unparse_content_stream(new))
            forms.append(form)

        # (written now that headings have their levels: the tag names H1...)
        new = _rewrite(instructions, [u for u in info["units"] if u.stream is None],
                       opener(None), on_form)
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(new))
        # The page's entry also names what its forms hold (their MCIDs don't
        # clash with its own): pdf.js finds a page's tags from that entry only.
        owners["page"] = {m: e for found in owners.values() for m, e in found.items()}
        for stream, key in [(page.obj, "page")] + [(form, form.objgen) for form in forms]:
            if "/StructParents" in stream:
                del stream["/StructParents"]
            mine = owners.get(key)
            if mine:
                stream.StructParents = next_key
                parent_tree.append((next_key, [mine.get(m) for m in range(max(mine) + 1)]))
                next_key += 1
        tagger = info["tagger"]
        annots = page.obj.get("/Annots")
        made = [(index, elem) for index, _, elem in tagger.annot_elems] + \
            [(index, link) for index, (_, link) in tagger.link_elems.items()]
        for index, elem in made:
            annot = annots[index]
            if not annot.is_indirect:
                annot = pdf.make_indirect(annot)
                annots[index] = annot
            annot.StructParent = next_key
            if str(annot.get("/Subtype", "")) == "/Link" and not str(annot.get("/Contents", "")):
                annot.Contents = pikepdf.String(_link_text(elem, annot))
            parent_tree.append((next_key, ("annot", elem)))
            elem.kids = [("objr-ref", annot) if isinstance(k, tuple) and k[0] == "objr" else k
                         for k in elem.kids]
            next_key += 1

    root_elem = _Elem("Document")
    root_elem.kids = list(elements)
    tree = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    pages = list(pdf.pages)

    def build(elem, parent):
        d = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + elem.S), P=parent))
        elem.ref = d
        if elem.page is not None:
            d.Pg = pages[elem.page].obj
        if elem.alt:
            d.Alt = pikepdf.String(elem.alt)
        attrs = elem.attrs or {}
        layout = {}
        table = {}
        listing = {}
        for k, v in attrs.items():
            if k == "BBox":
                layout["BBox"] = _unview(pages_info, elem.page, v)
            elif k in ("Scope", "ColSpan", "RowSpan"):
                table[k] = v
            elif k == "ListNumbering":
                listing[k] = v
        attr_dicts = []
        if layout and layout["BBox"] is not None:
            attr_dicts.append(Dictionary(O=Name.Layout, BBox=Array(layout["BBox"])))
        if table:
            t = Dictionary(O=Name.Table)
            for k, v in table.items():
                t[Name("/" + k)] = Name("/" + v) if isinstance(v, str) else v
            attr_dicts.append(t)
        if listing:
            attr_dicts.append(Dictionary(O=Name.List, ListNumbering=Name("/" + listing["ListNumbering"])))
        if attr_dicts:
            d.A = attr_dicts[0] if len(attr_dicts) == 1 else Array(attr_dicts)
        kids = elem.kids
        if elem.inline:
            def order(kid):
                if isinstance(kid, tuple) and kid[0] == "mcid":
                    return (0, kid[2])
                if isinstance(kid, _Elem):
                    first = _first_drawn(kid)
                    return (0, first + 0.5) if first is not None else (1, 0)
                return (1, 0)
            kids = sorted(kids, key=order)
        out = Array()
        for kid in kids:
            if isinstance(kid, _Elem):
                out.append(build(kid, d))
            elif kid[0] == "mcid" and kid[3] is None:
                out.append(kid[1])
            elif kid[0] == "mcid":  # in a form XObject drawn on the page
                out.append(Dictionary(Type=Name.MCR, Pg=pages[elem.page].obj, Stm=kid[3],
                                      MCID=kid[1]))
            elif kid[0] == "objr-ref":
                out.append(Dictionary(Type=Name.OBJR, Obj=kid[1], Pg=pages[elem.page].obj))
        d.K = out
        return d

    # drop elements that ended up with nothing in them
    root_elem.kids = _prune(root_elem.kids)
    doc = build(root_elem, tree)
    tree.K = doc
    nums = Array()
    for key, value in parent_tree:
        nums.append(key)
        if isinstance(value, list):
            nums.append(Array([elem.ref if elem is not None and elem.ref is not None else None
                               for elem in value]))
        else:
            nums.append(value[1].ref if value[1].ref is not None else doc)
    tree.ParentTree = pdf.make_indirect(Dictionary(Nums=nums))
    tree.ParentTreeNextKey = next_key
    pdf.Root.StructTreeRoot = tree
    return root_elem.kids


def _first_drawn(elem):
    """When the first content of an element (or its kids) is drawn."""
    found = [k[2] for k in elem.kids if isinstance(k, tuple) and k[0] == "mcid"]
    for kid in elem.kids:
        if isinstance(kid, _Elem):
            sub = _first_drawn(kid)
            if sub is not None:
                found.append(sub)
    return min(found) if found else None


def _prune(kids):
    """Elements without content are left out (cells and annotations stay)."""
    out = []
    for kid in kids:
        if not isinstance(kid, _Elem):
            out.append(kid)
            continue
        kid.kids = _prune(kid.kids)
        if kid.kids or kid.S in ("TD", "TH", "TR", "Table"):
            out.append(kid)
    return out


def _unview(pages_info, page_index, box):
    """A box in seen coordinates back in the page's own."""
    info = pages_info["infos"][page_index] if page_index is not None else None
    if info is None:
        return None
    view = info["tagger"].view
    bx0, by0, bx1, by1 = view.box
    x0, y0, x1, y1 = box
    corners = []
    for vx, vy in ((x0, y0), (x1, y1)):
        if view.rotation == 90:
            corners.append((bx1 - vy, vx + by0))
        elif view.rotation == 180:
            corners.append((bx1 - vx, by1 - vy))
        elif view.rotation == 270:
            corners.append((vy + bx0, by1 - vx))
        else:
            corners.append((vx + bx0, vy + by0))
    return [round(v, 2) for v in _bbox(corners)]


def _link_text(elem, annot):
    """A description for a link: its text, else where it goes."""
    action = annot.get("/A")
    uri = ""
    try:
        if action is not None and "/URI" in action:
            uri = str(action.URI)
    except Exception:
        uri = ""
    return (elem.text or uri or "Link")[:300]


def tag_summary(result):
    """One line on what ``autotag`` did."""
    if not result.get("tagged"):
        return (f"already tagged: kept its tags; language {result['lang']}"
                + (f", title {result['title']!r}" if result.get("title") else ""))
    counts = result.get("elements", {})
    heads = sum(n for s, n in counts.items() if re.fullmatch(r"H\d", s))
    parts = []
    for n, word in ((heads, "heading"), (counts.get("P", 0), "paragraph"),
                    (counts.get("L", 0), "list"), (counts.get("Table", 0), "table"),
                    (counts.get("Figure", 0), "figure"), (counts.get("Link", 0), "link"),
                    (counts.get("Form", 0), "form field")):
        if n:
            parts.append(f"{n} {word}{'s' if n != 1 else ''}")
    line = "tagged " + (", ".join(parts) if parts else "the pages") + f"; language {result['lang']}"
    missing = result.get("figures_without_alt") or []
    if missing:
        pages = sorted(set(missing))
        line += (f"; {len(missing)} figure{'s' if len(missing) != 1 else ''} without a "
                 f"description (page{'s' if len(pages) != 1 else ''} "
                 f"{', '.join(map(str, pages[:10]))})")
    if result.get("bookmarks"):
        line += f"; added {result['bookmarks']} bookmarks"
    if result.get("untagged_pages"):
        line += f"; pages not tagged (unreadable content): {', '.join(map(str, result['untagged_pages']))}"
    return line


# --------------------------------------------------------------------------
# Checking
# --------------------------------------------------------------------------

def _plural(n, word, plural=None):
    return f"{n} {word if n == 1 else (plural or word + 's')}"


class _Found:
    """Problems found, per rule and kind, with the pages they're on."""

    def __init__(self):
        self.items = {}

    def add(self, rule, kind, page=None, count=1):
        item = self.items.setdefault((rule, kind), {"rule": rule, "message": kind,
                                                    "fix": RULES[rule], "count": 0,
                                                    "pages": set()})
        item["count"] += count
        if page:
            item["pages"].add(page)

    def list(self):
        order = list(RULES)
        return [{**i, "pages": sorted(i["pages"])}
                for i in sorted(self.items.values(), key=lambda i: order.index(i["rule"]))]


def _kids(elem):
    import pikepdf

    k = elem.get("/K")
    if k is None:
        return []
    return list(k) if isinstance(k, pikepdf.Array) else [k]


def _is_elem(obj):
    import pikepdf

    return isinstance(obj, pikepdf.Dictionary) and "/S" in obj and \
        str(obj.get("/Type", "/StructElem")) == "/StructElem"


def _attributes(elem):
    import pikepdf

    found = {}
    attrs = elem.get("/A")
    if attrs is None:
        return found
    for a in (list(attrs) if isinstance(attrs, pikepdf.Array) else [attrs]):
        if isinstance(a, pikepdf.Dictionary):
            for key, value in a.items():
                found[key] = value
    return found


class _TreeFacts:
    """What the structure tree says, read once: figures without alternate
    text, tables, headings, lists, and the annotations it tags."""

    def __init__(self, pdf, page_numbers):
        self.page_numbers = page_numbers
        self.figures = []          # pages of figures without alternate text
        self.tables_no_header = []
        self.tables_irregular = []
        self.headings = []         # (level, page) in document order
        self.lists = []            # pages of badly built lists
        self.link_objrs, self.form_objrs, self.objrs = set(), set(), set()
        tree = pdf.Root.StructTreeRoot
        roles = tree.get("/RoleMap")
        self.roles = {str(k): str(v) for k, v in roles.items()} if roles is not None else {}
        self.walk(tree)

    def std(self, name):
        name = str(name)
        for _ in range(10):
            if name not in self.roles or self.roles[name] == name:
                break
            name = self.roles[name]
        return name.lstrip("/")

    def page_of(self, elem, inherited):
        pg = elem.get("/Pg")
        if pg is not None:
            return self.page_numbers.get(pg.objgen, inherited)
        return inherited

    def walk(self, tree):
        import pikepdf

        seen = set()
        stack = [(k, None, None) for k in reversed(_kids(tree))]
        while stack:
            elem, page, parent = stack.pop()
            if not _is_elem(elem):
                continue
            key = elem.objgen if elem.is_indirect else id(elem)
            if key in seen or len(seen) > 500000:
                continue
            seen.add(key)
            S = self.std(elem.S)
            page = self.page_of(elem, page)
            kids = _kids(elem)
            kid_types = []
            for kid in kids:
                if _is_elem(kid):
                    kid_types.append(self.std(kid.S))
                elif isinstance(kid, pikepdf.Dictionary) and str(kid.get("/Type", "")) == "/OBJR":
                    target = kid.get("/Obj")
                    if target is not None and target.is_indirect:
                        self.objrs.add(target.objgen)
                        if S == "Link":
                            self.link_objrs.add(target.objgen)
                        elif S == "Form":
                            self.form_objrs.add(target.objgen)
                    if page is None and kid.get("/Pg") is not None:
                        page = self.page_numbers.get(kid.Pg.objgen)
            if S == "Figure":
                alt = str(elem.get("/Alt") or "").strip()
                actual = str(elem.get("/ActualText") or "").strip()
                if not alt and not actual:
                    self.figures.append(page)
            elif S == "Table":
                self.table(elem, page)
            elif re.fullmatch(r"H[1-6]", S):
                self.headings.append((int(S[1]), page))
            elif S == "L":
                if any(t not in ("LI", "L", "Caption", "Artifact", "NonStruct") for t in kid_types):
                    self.lists.append(page)
            elif S == "LI":
                if parent != "L" or any(t not in ("Lbl", "LBody") for t in kid_types):
                    self.lists.append(page)
            elif S in ("Lbl", "LBody") and parent != "LI":
                self.lists.append(page)
            for kid in reversed(kids):
                if _is_elem(kid):
                    stack.append((kid, page, S))

    def table(self, elem, page):
        rows, has_header = [], False
        groups = [elem]
        while groups:
            node = groups.pop(0)
            for kid in _kids(node):
                if not _is_elem(kid):
                    continue
                S = self.std(kid.S)
                if S == "TR":
                    rows.append(kid)
                elif S in ("THead", "TBody", "TFoot"):
                    groups.append(kid)
        widths, taken = [], {}
        for r, row in enumerate(rows):
            col = 0
            for kid in _kids(row):
                if not _is_elem(kid):
                    continue
                S = self.std(kid.S)
                if S not in ("TH", "TD"):
                    continue
                has_header = has_header or S == "TH"
                while taken.get((r, col)):
                    col += 1
                attrs = _attributes(kid)
                colspan = max(1, int(_num(attrs.get("/ColSpan", 1), 1)))
                rowspan = max(1, int(_num(attrs.get("/RowSpan", 1), 1)))
                for dr in range(rowspan):
                    for dc in range(colspan):
                        taken[(r + dr, col + dc)] = True
                col += colspan
            widths.append(max([c + 1 for (rr, c) in taken if rr == r], default=0))
        if not has_header:
            self.tables_no_header.append(page)
        if len(set(w for w in widths if w)) > 1:
            self.tables_irregular.append(page)


def _number_tree(node, out, depth=0):
    import pikepdf

    if not isinstance(node, pikepdf.Dictionary) or depth > 30:
        return out
    nums = node.get("/Nums")
    if isinstance(nums, pikepdf.Array):
        items = list(nums)
        for k in range(0, len(items) - 1, 2):
            try:
                out[int(items[k])] = items[k + 1]
            except (TypeError, ValueError):
                continue
    for kid in node.get("/Kids") or []:
        _number_tree(kid, out, depth + 1)
    return out


_PAINT = _SHOW | _PATH_PAINT | {"Do", "sh"}


def _content_marks(stream, resources, depth=0, seen=None):
    """(number of drawing operations outside marked content that tags them
    or marks them as artifacts, MCIDs found) for a page or form."""
    import pikepdf

    seen = set() if seen is None else seen
    try:
        instructions = pikepdf.parse_content_stream(stream)
    except (pikepdf.PdfError, ValueError, TypeError):
        return 0, set()
    props = resources.get("/Properties") if isinstance(resources, pikepdf.Dictionary) else None
    stack, untagged, mcids = [], 0, set()
    for ins in instructions:
        covered = "real" in stack or "artifact" in stack
        if isinstance(ins, pikepdf.ContentStreamInlineImage):
            untagged += 0 if covered else 1
            continue
        op = str(ins.operator)
        if op == "BMC":
            tag = str(ins.operands[0]) if ins.operands else ""
            stack.append("artifact" if tag == "/Artifact" else None)
        elif op == "BDC":
            tag = str(ins.operands[0]) if ins.operands else ""
            p = ins.operands[1] if len(ins.operands) > 1 else None
            if isinstance(p, pikepdf.Name) and props is not None:
                p = props.get(str(p))
            if tag == "/Artifact":
                stack.append("artifact")
            elif isinstance(p, pikepdf.Dictionary) and "/MCID" in p:
                stack.append("real")
                try:
                    mcids.add(int(p.MCID))
                except (TypeError, ValueError):
                    pass
            else:
                stack.append(None)
        elif op == "EMC":
            if stack:
                stack.pop()
        elif op in _PAINT and not covered:
            if op == "Do" and ins.operands:
                try:
                    xobj = resources.XObject[ins.operands[0]]
                except (AttributeError, KeyError, TypeError, ValueError):
                    xobj = None
                if xobj is not None and str(xobj.get("/Subtype", "")) == "/Form":
                    key = xobj.objgen if xobj.is_indirect else id(xobj)
                    if depth < 6 and key not in seen:
                        seen.add(key)
                        inner, _ = _content_marks(xobj, xobj.get("/Resources") or resources,
                                                  depth + 1, seen)
                        untagged += inner
                    continue
            untagged += 1
    return untagged, mcids


def _font_unmapped(font):
    """Whether a font has no way to say which characters it draws."""
    import pikepdf

    try:
        if "/ToUnicode" in font:
            return False
        subtype = str(font.get("/Subtype", ""))
        if subtype == "/Type0":
            encoding = str(font.get("/Encoding", ""))
            if encoding.startswith("/Uni"):
                return False
            info = font.DescendantFonts[0].get("/CIDSystemInfo")
            ordering = str(info.get("/Ordering", "")) if info is not None else ""
            return ordering not in ("Japan1", "GB1", "CNS1", "Korea1", "KR")
        encoding = font.get("/Encoding")
        if isinstance(encoding, pikepdf.Dictionary):
            names = [str(d) for d in encoding.get("/Differences", []) if isinstance(d, pikepdf.Name)]
            odd = [n for n in names
                   if re.fullmatch(r"/(?:g|G|glyph|index|cid|c|C|a)\d+|/[A-Z]{1,2}\d{1,3}", n)]
            if names and len(odd) > len(names) / 2:
                return True
        if subtype == "/Type3":
            return encoding is None
        if subtype == "/TrueType" and encoding is None:
            descriptor = font.get("/FontDescriptor")
            flags = int(_num(descriptor.get("/Flags", 0))) if descriptor is not None else 0
            return bool(flags & 4)  # symbolic: the codes are glyph positions
    except (AttributeError, IndexError, TypeError, ValueError, pikepdf.PdfError):
        return False
    return False


def _page_fonts(resources, depth=0, seen=None):
    """(name, font) of the fonts a page or form uses, with those of its forms."""
    import pikepdf

    seen = set() if seen is None else seen
    out = []
    if not isinstance(resources, pikepdf.Dictionary) or depth > 5:
        return out
    try:
        for name, font in (resources.get("/Font") or {}).items():
            if isinstance(font, pikepdf.Dictionary):
                out.append((str(font.get("/BaseFont", name)).lstrip("/").split("+")[-1], font))
        for _, xobj in (resources.get("/XObject") or {}).items():
            if isinstance(xobj, pikepdf.Stream) and str(xobj.get("/Subtype", "")) == "/Form":
                key = xobj.objgen if xobj.is_indirect else id(xobj)
                if key not in seen:
                    seen.add(key)
                    out += _page_fonts(xobj.get("/Resources"), depth + 1, seen)
    except (AttributeError, pikepdf.PdfError):
        pass
    return out


def _field_has_tooltip(widget):
    node, depth = widget, 0
    while node is not None and depth < 20:
        if str(node.get("/TU") or "").strip():
            return True
        node = node.get("/Parent")
        depth += 1
    return False


def _text_pages(path, password, count):
    """Per page: (characters, characters without a Unicode mapping, share of
    the page covered by pictures when it has no text), from pdfium."""
    import pypdfium2.raw as raw

    out = []
    has_error_check = hasattr(raw, "FPDFText_HasUnicodeMapError")
    with core.PDFIUM_LOCK:
        doc = _open_pdfium(path, password)
        try:
            for i in range(min(len(doc), count)):
                page = doc[i]
                textpage = page.get_textpage()
                chars = errors = 0
                for k in range(textpage.count_chars()):
                    if raw.FPDFText_IsGenerated(textpage, k) == 1:
                        continue
                    chars += 1
                    if has_error_check and raw.FPDFText_HasUnicodeMapError(textpage, k) == 1:
                        errors += 1
                covered = 0.0
                if chars == 0:
                    w, h = page.get_size()
                    boxes = []
                    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=3):
                        try:
                            boxes.append(obj.get_bounds())
                        except Exception:  # a picture pdfium can't place
                            continue
                    if boxes:
                        covered = min(1.0, _area(_union(boxes)) / max(w * h, 1))
                out.append((chars, errors, covered))
                textpage.close()
                page.close()
        finally:
            doc.close()
    return out, has_error_check


def check(path, password=None):
    """Check a PDF for what keeps it from being accessible (see RULES).
    Returns {"file", "pages", "tagged", "lang", "title", "passed",
    "issues": [{"rule", "message", "fix", "count", "pages"}], "note"}."""
    import pikepdf

    path = Path(path)
    core.open_pdf(path, password)  # the usual messages about unreadable files and passwords
    found = _Found()
    pdf = _open_pikepdf(path, password)
    with pdf:
        pages = list(pdf.pages)
        total = len(pages)
        numbers = {p.obj.objgen: i + 1 for i, p in enumerate(pages)}
        root = pdf.Root
        tagged = _is_tagged(pdf)
        mark = root.get("/MarkInfo")
        marked = bool(mark is not None and mark.get("/Marked", False))
        lang = str(root.get("/Lang") or "").strip()
        title = _title_of(pdf)
        prefs = root.get("/ViewerPreferences")
        shown = bool(prefs is not None and prefs.get("/DisplayDocTitle", False))

        if pdf.is_encrypted:
            try:
                blocked = not pdf.allow.accessibility
            except AttributeError:
                blocked = False
            if blocked:
                found.add("security", "security")
        if not tagged:
            found.add("tagged", "untagged")
        elif not marked:
            found.add("tagged", "unmarked")
        if not lang:
            found.add("lang", "lang")
        if not title:
            found.add("title", "title")
        if not shown:
            found.add("title-shown", "title-shown")

        facts = None
        parent_tree = {}
        if tagged:
            try:
                facts = _TreeFacts(pdf, numbers)
                parent_tree = _number_tree(root.StructTreeRoot.get("/ParentTree"), {})
            except (pikepdf.PdfError, AttributeError, TypeError, ValueError, RecursionError):
                found.add("structure", "unreadable")
                facts = None

        unmapped_fonts = {}
        for i, page in enumerate(pages):
            progress.report(i, total, "checking page")
            number = i + 1
            obj = page.obj
            resources = _inherited(obj, "/Resources")
            annots = [a for _, a in _annotations(obj)
                      if str(a.get("/Subtype", "")) not in ("/Popup", "/PrinterMark", "/TrapNet")
                      and not int(_num(a.get("/F", 0))) & 2]
            if annots and str(obj.get("/Tabs", "")) != "/S":
                found.add("tabs", "tabs", number)
            for annot in annots:
                subtype = str(annot.get("/Subtype", ""))
                key = annot.objgen if annot.is_indirect else None
                if subtype == "/Widget":
                    if not _field_has_tooltip(annot):
                        found.add("forms", "tooltip", number)
                    if facts is not None and key not in facts.form_objrs:
                        found.add("forms", "untagged", number)
                elif subtype == "/Link":
                    if facts is not None:
                        if key not in facts.link_objrs:
                            found.add("links", "untagged", number)
                        if not str(annot.get("/Contents") or "").strip():
                            found.add("links", "contents", number)
                elif facts is not None and key not in facts.objrs:
                    found.add("annotations", "untagged", number)
            for name, font in _page_fonts(resources):
                if _font_unmapped(font):
                    unmapped_fonts.setdefault(name, set()).add(number)
            if tagged:
                untagged, mcids = _content_marks(page, resources)
                if untagged:
                    found.add("content", "content", number, untagged)
                if mcids:
                    sp = obj.get("/StructParents")
                    entry = parent_tree.get(int(sp)) if sp is not None else None
                    ok = isinstance(entry, pikepdf.Array) and all(
                        m < len(entry) and isinstance(entry[m], pikepdf.Dictionary) for m in mcids)
                    if not ok:
                        found.add("structure", "structure", number)

        text, has_error_check = _text_pages(path, password, total)
        for i, (chars, errors, covered) in enumerate(text):
            if chars == 0 and covered >= 0.3:
                found.add("scanned", "scanned", i + 1)
            if errors and errors >= max(1, chars * 0.01):
                found.add("unicode", "unicode", i + 1)
        if not has_error_check:
            for where in unmapped_fonts.values():
                for number in where:
                    found.add("unicode", "unicode", number)
        unicode_pages = {p for item in found.items.values() if item["rule"] == "unicode"
                         for p in item["pages"]}
        fonts = sorted(name for name, where in unmapped_fonts.items() if where & unicode_pages)

        if facts is not None:
            for page in facts.figures:
                found.add("figures", "figures", page)
            for page in facts.tables_no_header:
                found.add("tables", "no-header", page)
            for page in facts.tables_irregular:
                found.add("tables", "irregular", page)
            previous = 0
            for level, page in facts.headings:
                if previous == 0 and level != 1:
                    found.add("headings", "first", page)
                elif level > previous + 1:
                    found.add("headings", "skip", page)
                previous = level
            for page in facts.lists:
                found.add("lists", "lists", page)
        outline = root.get("/Outlines")
        if total > BOOKMARK_PAGES and (outline is None or outline.get("/First") is None):
            found.add("bookmarks", "bookmarks")

    issues = [_worded(item, fonts, total) for item in found.list()]
    return {"file": str(path), "pages": total, "tagged": tagged, "lang": lang, "title": title,
            "passed": not issues, "issues": issues, "note": MANUAL_NOTE}


def _worded(item, fonts, total):
    """An issue with its message put into words, from what was counted."""
    n, pages = item["count"], item["pages"]
    on = _plural(len(pages), "page")
    are = "is" if n == 1 else "are"
    have = "has" if n == 1 else "have"
    messages = {
        "security": "The security settings stop screen readers from reading the text.",
        "untagged": "The document isn't tagged: screen readers can't tell its headings, "
                    "paragraphs, lists, tables and pictures apart, or the order they're read in.",
        "unmarked": "The document has tags but doesn't say so (MarkInfo), so some readers "
                    "ignore them.",
        "lang": "The document's language isn't set, so screen readers may read it with the "
                "wrong voice.",
        "title": "The document has no title.",
        "title-shown": "The window shows the file name instead of the document's title "
                       "(DisplayDocTitle is off).",
        "tabs": "On pages with links, fields or comments, the Tab key doesn't follow the "
                "document's structure.",
        "tooltip": f"{_plural(n, 'form field')} {have} no tooltip: the name a screen reader "
                   "reads out for the field.",
        "contents": f"{_plural(n, 'link')} {have} no description for screen readers (Contents).",
        "content": f"Some content on {on} is neither tagged nor marked as decoration "
                   f"(artifact), so screen readers skip it or read it out of order "
                   f"({_plural(n, 'drawing operation')}).",
        "structure": f"The tags don't match the page content on {on} (the structure tree is "
                     "damaged).",
        "unreadable": "The structure tree can't be read (it's damaged).",
        "scanned": f"{_plural(len(pages), 'page is a scanned picture', 'pages are scanned pictures')}"
                   " with no text: there's nothing for a screen reader to read.",
        "unicode": "Some text can't be turned into characters (its font has no Unicode "
                   "mapping), so screen readers read it as gibberish"
                   + (f" (fonts: {', '.join(fonts[:5])})" if fonts else "") + ".",
        "figures": f"{_plural(n, 'figure')} {have} no alternate text: a description read out "
                   "instead of the picture.",
        "no-header": f"{_plural(n, 'table')} {have} no header cells (TH), so the columns have "
                     "no names.",
        "irregular": f"{_plural(n, 'table')} {have} rows with different numbers of cells.",
        "first": "The first heading isn't a level 1 heading (H1).",
        "skip": f"Headings skip a level {_plural(n, 'time')} (e.g. H1 followed by H3).",
        "lists": f"{_plural(n, 'list')} {are}n't built right: a list (L) holds items (LI), each "
                 "with a label (Lbl) and a body (LBody).",
        "bookmarks": f"The document has {total} pages but no bookmarks to find your way around.",
    }
    if item["message"] == "untagged" and item["rule"] != "tagged":
        thing = {"forms": "form field", "links": "link",
                 "annotations": "comment or other annotation"}[item["rule"]]
        plural = {"annotations": "comments or other annotations"}.get(item["rule"])
        what = {"forms": " (as a Form element)", "links": " (as a Link element holding the link)",
                "annotations": ""}[item["rule"]]
        message = f"{_plural(n, thing, plural)} {are}n't tagged{what}."
    else:
        message = messages.get(item["message"], item["message"])
    return {**item, "message": message}


def summary(result):
    """One line: "no accessibility problems found" or "3 accessibility
    problems (...)"."""
    if result["passed"]:
        return "no accessibility problems found"
    n = len(result["issues"])
    first = result["issues"][0]["message"].split(":")[0].rstrip(".")
    return f"{n} accessibility problem{'s' if n != 1 else ''} ({first}…)"


def report(result, name):
    """A plain-text report."""
    lines = [f"Accessibility check: {name}", ""]
    lines.append(f"Pages: {result['pages']}")
    lines.append(f"Tagged: {'Yes' if result['tagged'] else 'No'}")
    lines.append(f"Language: {result['lang'] or '(not set)'}")
    lines.append(f"Title: {result['title'] or '(none)'}")
    n = len(result["issues"])
    lines.append(f"Result: {'no problems found' if result['passed'] else _plural(n, 'problem')}")
    if result["issues"]:
        lines += ["", "Problems:"]
        for item in result["issues"]:
            where = ""
            if item["pages"]:
                shown = ", ".join(str(p) for p in item["pages"][:10])
                where = f" (page{'s' if len(item['pages']) > 1 else ''} {shown}" \
                        f"{'…' if len(item['pages']) > 10 else ''})"
            lines.append(f"  - {item['message']}{where}")
            lines.append(f"    How to fix: {item['fix']}")
    if result.get("note"):
        lines += ["", result["note"]]
    return "\n".join(lines) + "\n"
