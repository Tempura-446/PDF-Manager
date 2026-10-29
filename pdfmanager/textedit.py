"""Change words already on a page, rewrite a paragraph, or add words.

Text in a PDF isn't stored as editable paragraphs: it's drawn letter by
letter, usually with fonts that only contain the letters used. So, like
most PDF editors do, a change covers the old words with a box in the colour
around them and writes the new words on top, at the same size, colour and
angle. The old words are still in the file underneath (copying or searching
can find them); to remove text for good, use Redact.

The new words keep the original look as far as the file allows:

1. the document's own font, when it has every letter needed (fonts cut
   down to the letters a document uses often do, for words made of them);
2. else the same font installed on this computer (Calibri, Times New
   Roman, ...), matched by name;
3. else a standard font of the same kind (serif, sans serif or monospace,
   bold, italic).
"""

import ctypes
import math
import statistics

from pypdf import Transformation
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

from pdfmanager import core, fonts

PAD = 0.6  # points added around the old words' box
SAME, INSTALLED, SIMILAR = "same", "installed", "similar"  # which font the words got


def _raw():
    import pypdfium2.raw as raw
    return raw


def _chars(textpage, rect):
    """Indices of the letters whose middle is inside ``rect``."""
    x0, y0, x1, y1 = rect
    slack = max(1.0, (y1 - y0) * 0.2)
    found = []
    for i in range(textpage.count_chars()):
        left, bottom, right, top = textpage.get_charbox(i, loose=True)
        if right <= left or top <= bottom:
            continue  # spaces and line breaks added by pdfium
        cx, cy = (left + right) / 2, (bottom + top) / 2
        if x0 - slack <= cx <= x1 + slack and y0 - slack <= cy <= y1 + slack:
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


def _style(textpage, index):
    """Font, size, colour, angle and starting point of a letter."""
    raw = _raw()
    tp = textpage.raw
    buf, flags = ctypes.create_string_buffer(256), ctypes.c_int()
    raw.FPDFText_GetFontInfo(tp, index, buf, len(buf), ctypes.byref(flags))
    font_name = buf.value.decode("latin-1")
    name = font_name.split("+")[-1].lower()
    weight = raw.FPDFText_GetFontWeight(tp, index)
    rgba = [ctypes.c_uint() for _ in range(4)]
    color = ((0, 0, 0) if not raw.FPDFText_GetFillColor(tp, index, *map(ctypes.byref, rgba))
             else tuple(v.value / 255 for v in rgba[:3]))
    m = raw.FS_MATRIX()
    raw.FPDFText_GetMatrix(tp, index, ctypes.byref(m))
    scale = math.hypot(m.a, m.b) or 1.0
    x, y = ctypes.c_double(), ctypes.c_double()
    raw.FPDFText_GetCharOrigin(tp, index, ctypes.byref(x), ctypes.byref(y))
    if flags.value & 1 or any(n in name for n in fonts.MONO_NAMES):
        family = "mono"
    elif (flags.value & 2 or any(n in name for n in fonts.SERIF_NAMES)) and "sans" not in name:
        family = "serif"
    else:
        family = "sans"
    raw_size = raw.FPDFText_GetFontSize(tp, index)
    matrix = (m.a, m.b, m.c, m.d)
    if not any(matrix):
        matrix = (1.0, 0.0, 0.0, 1.0)
    return {
        "font": font_name, "handle": (handle := _font_handle(textpage, index)), "data": _font_data(handle),
        "family": family,
        "bold": weight >= 600 or bool(flags.value & (1 << 18)) or any(n in name for n in fonts.BOLD_NAMES),
        "italic": bool(flags.value & 64) or "italic" in name or "oblique" in name,
        "raw_size": raw_size, "matrix": matrix,
        "size": raw_size * scale,
        "color": color, "angle": math.degrees(math.atan2(m.b, m.a)),
        "origin": (x.value, y.value),
    }


def _default_style(rect):
    size = (rect[3] - rect[1]) * 0.75
    return {"font": "", "handle": None, "data": None, "family": "sans", "bold": False, "italic": False,
            "raw_size": size, "matrix": (1.0, 0.0, 0.0, 1.0), "size": size, "color": (0, 0, 0),
            "angle": 0.0, "origin": (rect[0], rect[1] + (rect[3] - rect[1]) * 0.22)}


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


def _lines(textpage, chars):
    """The letters grouped into lines, top to bottom: [(baseline y, [index])]."""
    raw = _raw()
    rows = []
    for i in chars:
        x, y = ctypes.c_double(), ctypes.c_double()
        raw.FPDFText_GetCharOrigin(textpage.raw, i, ctypes.byref(x), ctypes.byref(y))
        size = max(1.0, raw.FPDFText_GetFontSize(textpage.raw, i))
        for row in rows:
            if abs(row[0] - y.value) < size * 0.35:
                row[1].append(i)
                break
        else:
            rows.append([y.value, [i]])
    return sorted(((y, members) for y, members in rows), key=lambda r: -r[0])


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
        except (AttributeError, KeyError, TypeError, ValueError):
            raise core.PdfManagerError("a change needs a page, the box around the words "
                                       "(four numbers) and the new words") from None
        if not all(math.isfinite(v) for v in rect):
            raise core.PdfManagerError("the box around the words isn't on the page")
        if not 1 <= page <= pages:
            raise core.PdfManagerError(f"page {page} doesn't exist")
        by_page.setdefault(page, []).append((kind, rect, "" if text is None else str(text)))
    if not by_page:
        raise core.PdfManagerError("no changes to make")
    return by_page


def _plan(doc, by_page, keep):
    """What to draw on each page: {page: [{cover, background, text, style, ...}]},
    measured with pdfium. The pages read go in ``keep``, open: the font
    handles in the styles belong to them."""
    plans = {}
    for number, page_edits in by_page.items():
        page = doc[number - 1]
        textpage = page.get_textpage()
        image = page.render(scale=1).to_pil().convert("RGB")
        plan = []
        for kind, rect, text in page_edits:
            item = {"kind": kind, "text": text, "cover": None, "background": None}
            if kind == "add":
                near = _nearest_char(textpage, rect[0], rect[1])
                style = _style(textpage, near) if near is not None else _default_style(
                    (rect[0], rect[1] - 14, rect[0] + 100, rect[1]))
                style["angle"], style["matrix"] = 0.0, (style["size"] / (style["raw_size"] or 1), 0.0,
                                                        0.0, style["size"] / (style["raw_size"] or 1))
                # The click marks the top left of the new words.
                style["origin"] = (rect[0], rect[1] - style["size"] * 0.8)
                item.update(style=style, leading=style["size"] * 1.2, width=None)
                plan.append(item)
                continue
            chars = _chars(textpage, rect)
            if chars:
                boxes = [textpage.get_charbox(i, loose=True) for i in chars]
                cover = (min(b[0] for b in boxes) - PAD, min(b[1] for b in boxes) - PAD,
                         max(b[2] for b in boxes) + PAD, max(b[3] for b in boxes) + PAD)
                letters = [i for i in chars if not textpage.get_text_range(i, 1).isspace()] or chars
                style = _style(textpage, letters[0])
            else:  # no text there (e.g. a scan): use the box as drawn
                cover, style = rect, _default_style(rect)
            item.update(cover=cover, background=_background(page, image, cover), style=style,
                        leading=style["size"] * 1.2, width=None)
            if kind == "paragraph" and chars and abs(style["angle"]) < 1:
                rows = _lines(textpage, chars)
                firsts = [min(members, key=lambda i: textpage.get_charbox(i, loose=True)[0])
                          for _, members in rows]
                left = min(textpage.get_charbox(i, loose=True)[0] for i in firsts)
                style["origin"] = (left, rows[0][0])
                if len(rows) > 1:
                    item["leading"] = (rows[0][0] - rows[-1][0]) / (len(rows) - 1)
                item["width"] = max(cover[2] - PAD - left, style["size"] * 4)
            plan.append(item)
        keep.append((page, textpage))
        plans[number] = plan
    return plans


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
        if own.codes and own.can_write(text) and all(
                _has_letter(style["handle"], ch) for ch in set(text) if ch != " "):
            return own
    return None


# ---- laying out the words -----------------------------------------------------------------

def _wrap(text, width, measure):
    """Lines of ``text`` no wider than ``width`` (``measure`` gives widths)."""
    lines = []
    for paragraph in text.split("\n"):
        words, line = paragraph.split(" "), ""
        for word in words:
            trial = word if not line else line + " " + word
            if line and measure(trial) > width:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
    return lines


def edit_text(path, output, edits, password=None, report=None):
    """Change words on pages. ``edits`` is a list of {"page" (1-based), and
    either "rect": [x0, y0, x1, y1] around the old words in PDF points (with
    "paragraph": true to rewrite the lines in it as one paragraph that
    wraps), or "at": [x, y] for new words whose top left is there; and
    "text": the new words (empty to just cover the old ones)}. Returns how
    many edits were made; ``report``, a list, gets which font each one used
    ("same", "installed" or "similar") and its name."""
    import pypdfium2 as pdfium

    reader = core.open_pdf(path, password)
    by_page = _parse(edits, len(reader.pages))
    with core.PDFIUM_LOCK:
        doc, keep = pdfium.PdfDocument(str(path), password=password), []
        try:
            plans = _plan(doc, by_page, keep)
            _apply(reader, plans, output, report)
        finally:
            for page, textpage in keep:
                textpage.close()
                page.close()
            doc.close()
    return sum(len(p) for p in plans.values())


def _apply(reader, plans, output, report):
    writer = core.clone_document(reader)
    # Pages can share one content stream (after Compress, or a duplicated
    # page): the change must land on this page only.
    shared = core._shared_objects(writer)
    for number, plan in plans.items():
        page = writer.pages[number - 1]
        core._own_contents(page, shared)
        box = page.mediabox
        left, bottom = float(box.left), float(box.bottom)
        own_text = []  # (font, operator lines) drawn after the overlay
        drawn = []     # (item, reportlab font, lines)
        for item in plan:
            text, style = item["text"], item["style"]
            if not text.strip():
                if report is not None:
                    report.append({"font": "", "kind": SAME})
                continue
            scale = math.hypot(*style["matrix"][:2]) or 1.0
            own = _own_font(page, style, text.replace("\n", ""))
            if own:
                width = item["width"] / scale / style["raw_size"] * 1000 if item["width"] else None
                lines = _wrap(text, width, own.width) if width else text.split("\n")
                own_text.append((own, item, lines))
                if report is not None:
                    report.append({"font": fonts.display_name(style["font"]), "kind": SAME})
                continue
            name = fonts.installed_font(style["font"], style["bold"], style["italic"], text) \
                if style["font"] else None
            kind = INSTALLED if name else SIMILAR
            name = name or fonts.font_for(text, style["family"], style["bold"], style["italic"])
            if item["width"]:
                from reportlab.pdfbase import pdfmetrics
                lines = _wrap(text, item["width"],
                              lambda t, n=name, s=style: pdfmetrics.stringWidth(t, n, max(1.0, s["size"])))
            else:
                lines = text.split("\n")
            drawn.append((item, name, lines))
            if report is not None:
                report.append({"font": fonts.display_name(style["font"] if kind == INSTALLED else fonts.file_name(name)),
                               "kind": kind})

        def draw(c, w, h, plan=plan, drawn=drawn):
            for item in plan:
                if item["cover"] is None:
                    continue
                x0, y0, x1, y1 = item["cover"]
                c.setFillColorRGB(*item["background"])
                c.rect(x0 - left, y0 - bottom, x1 - x0, y1 - y0, fill=1, stroke=0)
            for item, name, lines in drawn:
                style = item["style"]
                c.saveState()
                c.translate(style["origin"][0] - left, style["origin"][1] - bottom)
                c.rotate(style["angle"])
                c.setFillColorRGB(*style["color"])
                c.setFont(name, max(1.0, style["size"]))
                for n, line in enumerate(lines):
                    c.drawString(0, -n * item["leading"], line)
                c.restoreState()

        if any(item["cover"] is not None for item in plan) or drawn:
            page.merge_transformed_page(core._overlay_page(float(box.width), float(box.height), draw),
                                        Transformation().translate(left, bottom))
        if own_text:
            _write_with_own_fonts(writer, page, own_text)
    core._write(writer, output)


def _num(v):
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


def _write_with_own_fonts(writer, page, own_text):
    """Add the words drawn with the document's fonts as one more content
    stream, after everything else on the page."""
    resources = page.get("/Resources")
    resources = DictionaryObject() if resources is None else resources.get_object()
    page[NameObject("/Resources")] = resources
    font_dict = resources.get("/Font")
    font_dict = DictionaryObject() if font_dict is None else font_dict.get_object()
    resources[NameObject("/Font")] = font_dict
    ops, names = ["q"], {}
    for own, item, lines in own_text:
        key = id(own.font.get_object())
        if key not in names:
            n = 1
            while f"/ZPDF{n}" in font_dict:
                n += 1
            names[key] = f"/ZPDF{n}"
            font_dict[NameObject(names[key])] = own.font
        style = item["style"]
        a, b, c, d = style["matrix"]
        x, y = style["origin"]
        # Moving down a line goes along the text's own downward direction.
        down = (-c / (math.hypot(c, d) or 1.0), -d / (math.hypot(c, d) or 1.0))
        ops += ["BT", "0 Tr", " ".join(_num(v) for v in style["color"]) + " rg",
                f"{names[key]} {_num(style['raw_size'])} Tf"]
        for n, line in enumerate(lines):
            if not line:
                continue
            dx, dy = down[0] * n * item["leading"], down[1] * n * item["leading"]
            ops.append(" ".join(_num(v) for v in (a, b, c, d, x + dx, y + dy)) + " Tm")
            ops.append(own.operands(line) + " TJ")
        ops.append("ET")
    ops.append("Q")

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
