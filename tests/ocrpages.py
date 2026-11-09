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


def _font(size, serif=False):
    for name in (("DejaVuSerif.ttf", "DejaVuSans.ttf") if serif else ("DejaVuSans.ttf",)):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def render(text=TEXT, font_size=44, serif=False, size=A4_300):
    """The text as a clean 300 dpi scan (greyscale)."""
    im = Image.new("L", size, 255)
    draw = ImageDraw.Draw(im)
    font = _font(font_size, serif)
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
