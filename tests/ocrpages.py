# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Synthetic scans with known text, for the OCR tests: a clean A4 page, a
blurred, skewed and noisy "photocopy of a fax", and a low-resolution JPEG.
Also the two measures the tests judge recognition by."""
import difflib
import random
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

TEXT = """Invoice No. 2024-0117  Date: 14 March 2024
Bill to: Northwind Traders Ltd, 12 Harbour Street, Portsmouth PO1 3AX
Item            Qty   Unit price   Amount
Copper tubing    14      3.75       52.50
Brass fittings    6     12.40       74.40
Solder (500 g)    2      9.95       19.90
Subtotal                           146.80
VAT 20%                             29.36
Total due                          176.16
Payment within 30 days to account 20-45-77 / 83920114.
Thank you for your business. Questions? Call 023 9283 1100.
The quick brown fox jumps over the lazy dog; 0123456789 @ £ € %.
Zusammenfassung: Die Lieferung erfolgt frei Haus, Fälligkeit 14 Tage."""

A4_300 = (2480, 3508)


def _font(size, serif=False, font_path=None):
    """DejaVu where the computer has it (Linux), else reportlab's Vera, the
    font DejaVu Sans grew from, which ships with ZPDF everywhere: the pages,
    and so the accuracy figures the tests check, are alike on every
    computer. (Pillow's own fallback font reads differently.)"""
    import reportlab

    vera = str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf")
    names = [font_path] if font_path else (
        ["DejaVuSerif.ttf", "DejaVuSans.ttf", vera] if serif else ["DejaVuSans.ttf", vera])
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def cjk_font():
    """A font with Chinese glyphs, if the computer has one; else None."""
    candidates = [
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simsun.ttc", "C:/Windows/Fonts/simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    return next((c for c in candidates if Path(c).exists()), None)


def render(text=TEXT, font_size=44, serif=False, size=A4_300, font_path=None):
    """The text as a clean 300 dpi scan (greyscale)."""
    im = Image.new("L", size, 255)
    draw = ImageDraw.Draw(im)
    font = _font(font_size, serif, font_path)
    y = 300
    for line in text.splitlines():
        draw.text((250, y), line, font=font, fill=0)
        y += int(font_size * 1.6)
    return im


def degrade(im, angle=1.7, blur=1.6, noise=60000, seed=1):
    """A crooked, blurred, grey, speckled copy of ``im``."""
    rnd = random.Random(seed)
    out = im.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
    out = out.filter(ImageFilter.GaussianBlur(blur))
    out = out.point(lambda v: int(90 + v * 0.55))   # grey paper, grey ink
    px = out.load()
    w, h = out.size
    for _ in range(noise):
        px[rnd.randrange(w), rnd.randrange(h)] = rnd.choice((0, 0, 255))
    return out


def lowres(im, dpi=110):
    """``im`` (a 300 dpi page) scanned at ``dpi`` and saved as a rough JPEG."""
    small = im.resize((im.width * dpi // 300, im.height * dpi // 300), Image.BILINEAR)
    return small


def scan_pdf(image, path, dpi=300):
    """``image`` as a one-page PDF (no text), the page sized for ``dpi``."""
    from pdfmanager import core

    path = Path(path)
    picture = path.with_suffix(".jpg" if dpi < 200 else ".png")
    if picture.suffix == ".jpg":
        image.convert("L").save(picture, quality=35, dpi=(dpi, dpi))
    else:
        image.save(picture, dpi=(dpi, dpi))
    return core.images_to_pdf([picture], path)


def _norm(s):
    return re.sub(r"\s+", " ", s).strip()


def cer(truth, text):
    """Character error rate of ``text`` against ``truth`` (0.0 is perfect)."""
    a, b = _norm(truth), _norm(text)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    errors = 0
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op != "equal":
            errors += max(i2 - i1, j2 - j1)
    return errors / max(len(a), 1)


def missing_words(truth, text):
    """Fraction of the words of ``truth`` that are not in ``text`` at all,
    whatever the order."""
    want = _norm(truth).split()
    have = _norm(text).split()
    for word in have:
        if word in want:
            want.remove(word)
    return len(want) / max(len(_norm(truth).split()), 1)


# -- harder pages: the cases Acrobat is known for ------------------------------

BODY = ("The committee met on Tuesday to review the quarterly accounts and the draft budget for "
        "next year. Members agreed that the maintenance reserve should rise by four percent, and asked "
        "the treasurer to circulate revised figures before the next meeting on 18 March. The proposal to "
        "replace the boiler in the east wing was deferred until a second quotation has been received. "
        "Correspondence from the council regarding parking permits was noted, and the secretary will "
        "reply confirming that residents support the scheme in principle.").split()


def _wrap(words, font, width):
    lines, current = [], ""
    for word in words:
        trial = (current + " " + word).strip()
        if font.getlength(trial) > width and current:
            lines.append(current)
            current = word
        else:
            current = trial
    return lines + [current]


def _draw_lines(im, x, y, lines, font, gap=1.55):
    draw = ImageDraw.Draw(im)
    for line in lines:
        draw.text((x, y), line, font=font, fill=0)
        y += int(font.size * gap)
    return y


def letter():
    """A plain one-column letter: (image, true text)."""
    font = _font(42)
    lines = _wrap(BODY, font, 1950)
    im = Image.new("L", A4_300, 255)
    _draw_lines(im, 260, 300, lines, font)
    return im, "\n".join(lines)


def two_columns():
    """A heading over two columns of prose: (image, true text in reading order)."""
    font = _font(36, serif=True)
    left, right = _wrap(BODY[:45], font, 1000), _wrap(BODY[45:], font, 1000)
    im = Image.new("L", A4_300, 255)
    ImageDraw.Draw(im).text((260, 200), "Minutes of the Residents' Committee", font=_font(56), fill=0)
    _draw_lines(im, 220, 380, left, font)
    _draw_lines(im, 1300, 380, right, font)
    return im, "Minutes of the Residents' Committee\n" + "\n".join(left + right)


TABLE = [["Item", "Qty", "Unit price", "Amount"], ["Copper tubing", "14", "3.75", "52.50"],
         ["Brass fittings", "6", "12.40", "74.40"], ["Solder 500 g", "2", "9.95", "19.90"],
         ["PTFE tape", "10", "0.85", "8.50"], ["Pipe clips", "40", "0.22", "8.80"],
         ["Subtotal", "", "", "164.10"], ["VAT 20%", "", "", "32.82"], ["Total due", "", "", "196.92"]]


def ruled_table():
    """An invoice table with ruled cells: (image, true text, row by row)."""
    im = Image.new("L", A4_300, 255)
    draw, font = ImageDraw.Draw(im), _font(38)
    xs, top, height = [250, 1050, 1350, 1800, 2230], 400, 90
    for r, row in enumerate(TABLE):
        for c, cell in enumerate(row):
            draw.text((xs[c] + 20, top + r * height + 22), cell, font=font, fill=0)
    for r in range(len(TABLE) + 1):
        draw.line([(xs[0], top + r * height), (xs[-1], top + r * height)], fill=0, width=4)
    for x in xs:
        draw.line([(x, top), (x, top + len(TABLE) * height)], fill=0, width=4)
    return im, "\n".join(" ".join(c for c in row if c) for row in TABLE)


def small_print():
    """Two paragraphs of 7 pt print (29 px at 300 dpi): (image, true text)."""
    font = _font(29, serif=True)
    lines = _wrap(BODY * 2, font, 2000)
    im = Image.new("L", A4_300, 255)
    _draw_lines(im, 240, 300, lines, font, gap=1.35)
    return im, "\n".join(lines)
