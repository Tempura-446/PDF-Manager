# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""How well OCR reads: a fixed set of made pages with known text, each
degraded the way real scans are, and a score per kind of page. A report,
not a test: every change to OCR is judged against these numbers
(docs/superpowers/plans/2026-11-14-ocr-improvements.md).

    python tests/ocrscore.py [--out report.md] [--keep FOLDER] [--engine builtin|tesseract]

Per page: characters wrong (CER) and words wrong (WER) against the true
text in reading order, the share of its numbers read exactly, and seconds.
The last two rows go through the whole tool (ocr.run) on PDFs: a scan
whose text layer is rubbish, and a page of real text with a scanned
figure of text on it.
"""
import argparse
import difflib
import random
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import ocrpages  # noqa: E402


def _tokens(s):
    return re.sub(r"\s+", " ", s).strip().split(" ")


def wer(truth, text):
    """Word error rate (0.0 is perfect), words in order."""
    a, b = _tokens(truth), _tokens(text)
    errors = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2
                 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op != "equal")
    return errors / max(len(a), 1)


def numbers(truth, text):
    """The share of the numbers in ``truth`` (tokens with a digit) that are in ``text`` exactly; None if it has none."""
    want = [t for t in _tokens(truth) if any(c.isdigit() for c in t)]
    if not want:
        return None
    have = _tokens(text)
    hit = 0
    for t in want:
        if t in have:
            have.remove(t)
            hit += 1
    return hit / len(want)


# -- the pages: (image, true text) ------------------------------------------------------

def faint(im):
    """Pale grey print on white: a tired toner cartridge."""
    return im.point(lambda v: int(205 + v * 50 / 255))


def skewed(im, angle=3.0):
    return im.rotate(angle, resample=Image.BICUBIC, fillcolor=255)


def half_resolution(im):
    return im.resize((im.width // 2, im.height // 2), Image.BILINEAR)


def phone_photo(im, seed=5):
    """The page photographed at a slant, with a shadow across it and soft focus."""
    w, h = im.size
    # Corners pulled in unevenly (perspective), on a grey table.
    quad = (0.03 * w, 0.02 * h, -0.02 * w, 0.99 * h, 1.03 * w, 1.02 * h, 0.96 * w, 0.01 * h)
    out = im.transform((w, h), Image.QUAD, quad, resample=Image.BICUBIC, fillcolor=120)
    shade = Image.linear_gradient("L").rotate(35, expand=False).resize((w, h))
    out = Image.composite(out, out.point(lambda v: int(v * 0.6)), shade.point(lambda v: 255 if v > 110 else int(v * 2.3)))
    out = out.filter(ImageFilter.GaussianBlur(1.2))
    rnd = random.Random(seed)
    px = out.load()
    for _ in range(15000):
        px[rnd.randrange(w), rnd.randrange(h)] = rnd.randrange(60, 200)
    return out


def very_faint(im, seed=9):
    """Print barely darker than the paper, with photocopier speckle."""
    out = im.point(lambda v: int(228 + v * 22 / 255))
    rnd = random.Random(seed)
    px = out.load()
    for _ in range(40000):
        px[rnd.randrange(out.width), rnd.randrange(out.height)] = rnd.randrange(150, 255)
    return out


def fax(im):
    """About 100 dpi, black and white only, as a fax or an old scanner gives."""
    small = im.resize((im.width // 3, im.height // 3), Image.BILINEAR)
    return small.point(lambda v: 255 if v > 150 else 0)


def blurred_photo(im):
    """A phone photo out of focus, in poor light."""
    out = phone_photo(im).filter(ImageFilter.GaussianBlur(2.6))
    return out.point(lambda v: int(40 + v * 0.5))


def coloured_paper(im):
    """Black print on a mid-grey sheet (a coloured form), with a stamp across it."""
    out = im.point(lambda v: int(v * 0.5))
    draw = ImageDraw.Draw(out)
    draw.text((500, 700), "RECEIVED", font=ocrpages._font(260), fill=60)
    return out


def three_columns():
    font = ocrpages._font(32, serif=True)
    words = ocrpages.BODY * 2
    third = len(words) // 3
    cols = [ocrpages._wrap(words[k * third:(k + 1) * third if k < 2 else None], font, 640) for k in range(3)]
    im = Image.new("L", ocrpages.A4_300, 255)
    for k, lines in enumerate(cols):
        ocrpages._draw_lines(im, 200 + k * 740, 300, lines, font)
    return im, "\n".join(line for col in cols for line in col)


FORM = [("Full name", "Margaret Ellison"), ("Date of birth", "07/11/1968"), ("Policy number", "HX-204-77815"),
        ("Postcode", "PO1 3AX"), ("Telephone", "023 9283 1100"), ("Amount claimed", "1,482.60")]


def form():
    """Labels with the answers written on ruled lines, and two tick boxes."""
    im = Image.new("L", ocrpages.A4_300, 255)
    draw, label, entry = ImageDraw.Draw(im), ocrpages._font(40), ocrpages._font(44, serif=True)
    y, rows = 320, ["Claim form"]
    draw.text((250, 200), "Claim form", font=ocrpages._font(60), fill=0)
    for name, value in FORM:
        draw.text((250, y), name + ":", font=label, fill=0)
        draw.text((900, y - 4), value, font=entry, fill=0)
        draw.line([(880, y + 56), (2200, y + 56)], fill=0, width=3)
        rows.append(f"{name}: {value}")
        y += 150
    for k, choice in enumerate(("Theft", "Water damage")):
        draw.rectangle([250 + k * 700, y, 300 + k * 700, y + 50], outline=0, width=4)
        draw.text((320 + k * 700, y), choice, font=label, fill=0)
    rows.append("Theft Water damage")
    return im, "\n".join(rows)


CJK_TEXT = "季度报告显示各地区稳步增长\n北方办事处新客户数量创历史新高\n成本保持不变 合计 176.16 元\n请于 30 天内付款 谢谢合作"


def cjk():
    path = ocrpages.cjk_font()
    return (ocrpages.render(CJK_TEXT, font_size=56, font_path=path), CJK_TEXT) if path else None


def pages():
    """[(kind, image, true text)]"""
    letter, text = ocrpages.letter()
    out = [("Clean, 300 dpi", letter, text), ("150 dpi", half_resolution(letter), text),
           ("Faint print", faint(letter), text), ("Skewed 3 degrees", skewed(letter), text),
           ("Noisy photocopy", ocrpages.degrade(letter), text), ("Phone photo", phone_photo(letter), text),
           ("Very faint, speckled", very_faint(letter), text), ("Fax (100 dpi, black and white)", fax(letter), text),
           ("Blurred phone photo", blurred_photo(letter), text), ("Grey paper with a stamp across", coloured_paper(letter), text),
           ("Small print (7 pt)", *ocrpages.small_print()),
           ("Small print at 150 dpi", half_resolution(ocrpages.small_print()[0]), ocrpages.small_print()[1]),
           ("Two columns", *ocrpages.two_columns()),
           ("Three columns", *three_columns()), ("Table of numbers", *ocrpages.ruled_table()), ("Form", *form())]
    made = cjk()
    if made:
        out.append(("Chinese", *made))
    return out


# -- through the whole tool ---------------------------------------------------------------

def garbage_layer(folder):
    """A clean scan that an old program gave a rubbish text layer: (path, true text)."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas
    image, text = ocrpages.letter()
    scan = ocrpages.scan_pdf(image, folder / "garbage-scan.pdf")
    layer = folder / "garbage-layer.pdf"
    c = canvas.Canvas(str(layer), pagesize=(595.2, 841.92))
    t = c.beginText(60, 760)
    t.setTextRenderMode(3)  # invisible, as OCR layers are
    t.setFont("Helvetica", 9)
    rnd = random.Random(7)
    for _ in range(22):
        t.textLine("".join(rnd.choice("~^|\\{}#@*lI1!¬¦`") for _ in range(70)))
    c.drawText(t)
    c.save()
    writer = PdfWriter(clone_from=PdfReader(str(scan)))
    writer.pages[0].merge_page(PdfReader(str(layer)).pages[0])
    out = folder / "garbage.pdf"
    writer.write(str(out))
    return out, text


FIGURE = "Figure 3: boiler pressure 2.4 bar\nrelief valve opens at 3.0 bar\ninspected 18 March by R. Patel"


def mixed(folder):
    """A page of real text with a scanned figure of text on it: (path, the figure's text)."""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    figure = ocrpages.render(FIGURE, font_size=48, size=(1500, 700))
    figure = figure.crop((150, 220, 1500, 620))
    out = folder / "mixed.pdf"
    c = canvas.Canvas(str(out), pagesize=(595.2, 841.92))
    c.setFont("Helvetica", 11)
    y = 780
    for line in ocrpages._wrap(ocrpages.BODY, ocrpages._font(11), 78):
        c.drawString(60, y, line)
        y -= 15
    c.drawImage(ImageReader(figure), 60, 380, 324, 96)
    c.save()
    return out, FIGURE


def pdf_text(path):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(path))
    try:
        return doc[0].get_textpage().get_text_range()
    finally:
        doc.close()


def found_words(truth, text):
    """The share of the words of ``truth`` in ``text``, whatever the order."""
    return 1 - ocrpages.missing_words(truth, text)


def main():
    from pdfmanager import core, ocr
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", help="write the table here as well (Markdown)")
    parser.add_argument("--keep", help="keep the pages in this folder")
    parser.add_argument("--engine", default="builtin", choices=("builtin", "tesseract"))
    args = parser.parse_args()
    if args.engine == "builtin" and not ocr.builtin_available():
        sys.exit("The built-in engine isn't installed (pip install rapidocr onnxruntime).")
    folder = Path(args.keep or tempfile.mkdtemp(prefix="zpdf-ocr-"))
    folder.mkdir(parents=True, exist_ok=True)

    lines = ["| Page | Characters wrong | Words wrong | Numbers exact | Seconds |", "|---|---|---|---|---|"]
    if args.engine == "builtin":
        ocr.recognise(ocrpages.render("warm up"))  # the model's loading isn't any page's time
    for kind, image, truth in pages():
        if args.keep:
            image.save(folder / (re.sub(r"\W+", "-", kind.lower()).strip("-") + ".png"))
        t0 = time.perf_counter()
        if args.engine == "builtin":
            text = ocr.text_of(ocr.recognise(image))
        else:
            src = ocrpages.scan_pdf(image, folder / "page.pdf")
            ocr.run(src, folder / "page-out.pdf", engine="tesseract")
            text = pdf_text(folder / "page-out.pdf")
        seconds = time.perf_counter() - t0
        exact = numbers(truth, text)
        lines.append(f"| {kind} | {ocrpages.cer(truth, text):.1%} | {wer(truth, text):.1%} | "
                     f"{'' if exact is None else format(exact, '.0%')} | {seconds:.1f} |")

    lines += ["", "| Through the tool | Its words found afterwards | Note |", "|---|---|---|"]
    for kind, (src, truth) in (("Scan with a rubbish text layer", garbage_layer(folder)),
                               ("Real text with a scanned figure", mixed(folder))):
        out = folder / (src.stem + "-out.pdf")
        try:
            ocr.run(src, out, engine=args.engine)
            note = ""
        except core.PdfManagerError as exc:
            shutil.copyfile(src, out)
            note = str(exc)[:80]
        lines.append(f"| {kind} | {found_words(truth, pdf_text(out)):.0%} | {note} |")

    table = "\n".join(lines)
    print(table)
    if args.out:
        Path(args.out).write_text(table + "\n", encoding="utf-8")
    if not args.keep:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
