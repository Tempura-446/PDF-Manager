"""Change words already on a page.

Text in a PDF isn't stored as editable paragraphs: it's drawn letter by
letter, usually with fonts that only contain the letters used. So, like
most PDF editors do for such files, a change covers the old words with a
box in the colour around them and writes the new words on top, in a
standard font that looks like the original, at the same size, colour and
angle. The old words are still in the file underneath (copying or
searching can find them); to remove text for good, use Redact.
"""

import ctypes
import math
import statistics
import unicodedata
from pathlib import Path

from pypdf import PdfWriter, Transformation

from pdfmanager import core

PAD = 0.6  # points added around the old words' box
BASE14 = {  # (family, bold, italic) -> standard PDF font
    ("sans", False, False): "Helvetica", ("sans", True, False): "Helvetica-Bold",
    ("sans", False, True): "Helvetica-Oblique", ("sans", True, True): "Helvetica-BoldOblique",
    ("serif", False, False): "Times-Roman", ("serif", True, False): "Times-Bold",
    ("serif", False, True): "Times-Italic", ("serif", True, True): "Times-BoldItalic",
    ("mono", False, False): "Courier", ("mono", True, False): "Courier-Bold",
    ("mono", False, True): "Courier-Oblique", ("mono", True, True): "Courier-BoldOblique",
}
SERIF_NAMES = ("times", "serif", "roman", "georgia", "garamond", "cambria", "minion", "book",
               "palatino", "baskerville", "caslon", "didot", "song", "ming", "batang")
MONO_NAMES = ("courier", "mono", "consol", "menlo", "inconsolata", "typewriter")
BOLD_NAMES = ("bold", "black", "heavy", "semibold", "demi")
# Fonts with many scripts, for words the standard fonts can't show. Only
# TrueType fonts work here (not the CFF-based .otf/.ttc ones).
UNICODE_FONTS = [
    r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\msgothic.ttc",
    r"C:\Windows\Fonts\malgun.ttf", r"C:\Windows\Fonts\Nirmala.ttf", r"C:\Windows\Fonts\seguisym.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
]
_loaded = {}


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


def _style(textpage, index):
    """Font, size, colour, angle and starting point of a letter."""
    raw = _raw()
    tp = textpage.raw
    buf, flags = ctypes.create_string_buffer(256), ctypes.c_int()
    raw.FPDFText_GetFontInfo(tp, index, buf, len(buf), ctypes.byref(flags))
    name = buf.value.decode("latin-1").split("+")[-1].lower()
    weight = raw.FPDFText_GetFontWeight(tp, index)
    rgba = [ctypes.c_uint() for _ in range(4)]
    color = ((0, 0, 0) if not raw.FPDFText_GetFillColor(tp, index, *map(ctypes.byref, rgba))
             else tuple(v.value / 255 for v in rgba[:3]))
    m = raw.FS_MATRIX()
    raw.FPDFText_GetMatrix(tp, index, ctypes.byref(m))
    scale = math.hypot(m.a, m.b) or 1.0
    x, y = ctypes.c_double(), ctypes.c_double()
    raw.FPDFText_GetCharOrigin(tp, index, ctypes.byref(x), ctypes.byref(y))
    if flags.value & 1 or any(n in name for n in MONO_NAMES):
        family = "mono"
    elif (flags.value & 2 or any(n in name for n in SERIF_NAMES)) and "sans" not in name:
        family = "serif"
    else:
        family = "sans"
    return {
        "family": family,
        "bold": weight >= 600 or bool(flags.value & (1 << 18)) or any(n in name for n in BOLD_NAMES),
        "italic": bool(flags.value & 64) or "italic" in name or "oblique" in name,
        "size": raw.FPDFText_GetFontSize(tp, index) * scale,
        "color": color, "angle": math.degrees(math.atan2(m.b, m.a)),
        "origin": (x.value, y.value),
    }


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


def _font(text, style):
    """A reportlab font name able to show ``text``, close to ``style``."""
    try:
        text.encode("cp1252")
        return BASE14[(style["family"], style["bold"], style["italic"])]
    except UnicodeEncodeError:
        pass
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    needed = {ord(ch) for ch in text if not ch.isspace()}
    for path in UNICODE_FONTS:
        if not Path(path).is_file():
            continue
        if path not in _loaded:
            name = "PdfmEdit" + str(len(_loaded))
            try:
                font = TTFont(name, path, subfontIndex=0) if path.lower().endswith(".ttc") \
                    else TTFont(name, path)
                pdfmetrics.registerFont(font)
                _loaded[path] = (name, set(font.face.charToGlyph))
            except Exception:  # not a TrueType font reportlab can use
                _loaded[path] = None
        if _loaded[path] and needed <= _loaded[path][1]:
            return _loaded[path][0]
    # Chinese, Japanese and Korean without a suitable font installed: fonts
    # that PDF readers supply themselves.
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    names = " ".join(unicodedata.name(ch, "") for ch in text)
    cid = ("HYSMyeongJo-Medium" if "HANGUL" in names else
           "HeiseiMin-W3" if "HIRAGANA" in names or "KATAKANA" in names else "STSong-Light")
    if cid not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(cid))
    return cid


def edit_text(path, output, edits, password=None):
    """Replace words on pages. ``edits`` is a list of {"page" (1-based),
    "rect": [x0, y0, x1, y1] around the old words in PDF points, "text":
    the new words (empty to just cover the old ones)}. Returns how many
    edits were made."""
    import pypdfium2 as pdfium

    reader = core.open_pdf(path, password)
    by_page = {}
    for edit in edits:
        page = int(edit.get("page", 0))
        if not 1 <= page <= len(reader.pages):
            raise core.PdfManagerError(f"page {page} doesn't exist")
        x0, y0, x1, y1 = (float(v) for v in edit["rect"])
        rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        by_page.setdefault(page, []).append((rect, str(edit.get("text") or "")))
    if not by_page:
        raise core.PdfManagerError("no changes to make")

    plans = {}  # page -> [(cover box, background, new text, style)]
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path), password=password)
        try:
            for number, page_edits in by_page.items():
                page = doc[number - 1]
                textpage = page.get_textpage()
                image = page.render(scale=1).to_pil().convert("RGB")
                plan = []
                for rect, text in page_edits:
                    chars = _chars(textpage, rect)
                    if chars:
                        boxes = [textpage.get_charbox(i, loose=True) for i in chars]
                        cover = (min(b[0] for b in boxes) - PAD, min(b[1] for b in boxes) - PAD,
                                 max(b[2] for b in boxes) + PAD, max(b[3] for b in boxes) + PAD)
                        style = _style(textpage, chars[0])
                    else:  # no text there (e.g. a scan): use the box as drawn
                        cover = rect
                        style = {"family": "sans", "bold": False, "italic": False,
                                 "size": (rect[3] - rect[1]) * 0.75, "color": (0, 0, 0),
                                 "angle": 0.0,
                                 "origin": (rect[0], rect[1] + (rect[3] - rect[1]) * 0.22)}
                    plan.append((cover, _background(page, image, cover), text, style))
                textpage.close()
                plans[number] = plan
        finally:
            doc.close()

    writer = PdfWriter(clone_from=reader)
    for number, plan in plans.items():
        page = writer.pages[number - 1]
        box = page.mediabox
        left, bottom = float(box.left), float(box.bottom)

        def draw(c, w, h, plan=plan):
            for (x0, y0, x1, y1), background, text, style in plan:
                c.setFillColorRGB(*background)
                c.rect(x0 - left, y0 - bottom, x1 - x0, y1 - y0, fill=1, stroke=0)
                if not text.strip():
                    continue
                c.saveState()
                c.translate(style["origin"][0] - left, style["origin"][1] - bottom)
                c.rotate(style["angle"])
                c.setFillColorRGB(*style["color"])
                c.setFont(_font(text, style), max(1.0, style["size"]))
                c.drawString(0, 0, text)
                c.restoreState()

        page.merge_transformed_page(core._overlay_page(float(box.width), float(box.height), draw),
                                    Transformation().translate(left, bottom))
    core._write(writer, output)
    return sum(len(p) for p in plans.values())
