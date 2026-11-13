"""Build the sweep corpus: a few hundred PDFs of every kind, written into a
folder outside the repository, with a manifest (manifest.csv) saying what
each file tests.

    python3 tests/sweep/make_corpus.py OUT_DIR [--samples DIR]

--samples: a folder of real-world PDFs to copy in as well (for example a
clone of pypdf's sample-files repository). They are only read, as data.

Sources: badpdfs.py (the robustness test's hostile files), LibreOffice
(documents written here and converted with soffice, when it is installed),
reportlab (fonts, page sizes and boxes, annotations, outlines, forms,
actions, attachments), pikepdf mutations of the files made here (object
streams, incremental updates, broken cross-references, encryption, filters,
images of every kind, layers) and Pillow images.

Each file is "valid" (a correct PDF that every tool should work on) or
"hostile" (damaged or deliberately odd: a tool may refuse it, with a
message). The sweep (run_sweep.py) reads that from the manifest.
"""
import argparse
import csv
import io
import math
import random
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pikepdf  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4, letter  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

FONTS = Path("/usr/share/fonts/truetype")
LOREM = ("The quick brown fox jumps over the lazy dog. Pack my box with five dozen liquor jugs. "
         "Invoice number 2024-0042 was paid on 3 March. Contact jane.doe@example.com or +1 555 0100. ")


class Corpus:
    def __init__(self, out):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.rows = []

    def path(self, name):
        return self.out / f"{name}.pdf"

    def add(self, name, source, what, kind="valid"):
        if self.path(name).exists() and self.path(name).stat().st_size >= 0:
            self.rows.append({"file": f"{name}.pdf", "source": source, "kind": kind, "what": what})

    def write(self, name, data, source, what, kind="valid"):
        self.path(name).write_bytes(data)
        self.add(name, source, what, kind)

    def save_manifest(self):
        with open(self.out / "manifest.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, ["file", "source", "kind", "what"])
            w.writeheader()
            w.writerows(self.rows)


def _try(label, func, *args):
    try:
        func(*args)
    except Exception as exc:  # one generator failing doesn't stop the rest
        print(f"  skipped {label}: {type(exc).__name__}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# reportlab
# ---------------------------------------------------------------------------

def _register_fonts():
    names = {}
    for name, rel in (("DejaVu", "dejavu/DejaVuSans.ttf"), ("Liberation", "liberation/LiberationSerif-Regular.ttf"),
                      ("FreeSans", "freefont/FreeSans.ttf"), ("WQY", "wqy/wqy-zenhei.ttc"),
                      ("FreeSerif", "freefont/FreeSerif.ttf")):
        path = FONTS / rel
        if path.exists():
            try:
                pdfmetrics.registerFont(TTFont(name, str(path)))
                names[name] = path
            except Exception:
                pass
    return names


def text_pages(path, pages=3, size=letter, font="Helvetica", lines=30, title=None, extra=None):
    c = canvas.Canvas(str(path), pagesize=size)
    if title:
        c.setTitle(title)
    w, h = size
    for n in range(pages):
        c.setFont(font, 11)
        y = h - 60
        c.drawString(50, y, f"Page {n + 1} of {pages}: {title or 'sample'}")
        for i in range(lines):
            y -= 16
            if y < 40:
                break
            c.drawString(50, y, (LOREM * 2)[i * 7 % 80: i * 7 % 80 + 90])
        if extra:
            extra(c, n, w, h)
        c.showPage()
    c.save()
    return path


def gen_reportlab(cp):
    fonts = _register_fonts()
    src = "reportlab"
    for f in ("Helvetica", "Times-Roman", "Courier", "Symbol", "ZapfDingbats", "Helvetica-BoldOblique"):
        text_pages(cp.path(f"rl_std14_{f}"), 2, font=f, title=f)
        cp.add(f"rl_std14_{f}", src, f"standard-14 font {f}")
    for name in fonts:
        text_pages(cp.path(f"rl_ttf_{name}"), 2, font=name, title=name)
        cp.add(f"rl_ttf_{name}", src, f"embedded TrueType subset {name}")
    if "WQY" in fonts:
        c = canvas.Canvas(str(cp.path("rl_cjk")))
        c.setFont("WQY", 14)
        for i, line in enumerate(["中文文本测试：快速的棕色狐狸跳过了懒狗。", "日本語のテキスト：こんにちは世界",
                                  "한국어 텍스트: 안녕하세요 세계", "繁體中文：發票號碼 2024-0042"]):
            c.drawString(50, 750 - i * 30, line)
        c.showPage()
        c.save()
        cp.add("rl_cjk", src, "CJK text in an embedded TrueType collection font")
    if "DejaVu" in fonts:
        c = canvas.Canvas(str(cp.path("rl_rtl")))
        c.setFont("DejaVu", 14)
        c.drawString(50, 750, "שלום עולם - Hebrew in logical order")
        c.drawString(50, 720, "مرحبا بالعالم - Arabic, unshaped")
        c.drawString(50, 690, "Ελληνικά, Кириллица, Türkçe ğüşiöç, ﬁ ligature")
        c.showPage()
        c.save()
        cp.add("rl_rtl", src, "Hebrew/Arabic/Greek/Cyrillic text")
    # Type 3 font (reportlab doesn't make one: written by hand, below in raw)
    # page sizes
    for label, size in (("tiny", (36, 36)), ("huge", (14400, 14400)), ("long_strip", (200, 14000)),
                        ("wide_banner", (14000, 200)), ("a0", (2384, 3370)), ("business_card", (252, 144))):
        text_pages(cp.path(f"rl_size_{label}"), 1, size=size, lines=5)
        cp.add(f"rl_size_{label}", src, f"page size {label} {size}")
    mixed = canvas.Canvas(str(cp.path("rl_mixed_sizes")))
    for i, size in enumerate([letter, A4, (A4[1], A4[0]), (300, 300), (1000, 400), letter]):
        mixed.setPageSize(size)
        mixed.drawString(20, size[1] - 30, f"Page {i + 1} size {size}")
        mixed.showPage()
    mixed.save()
    cp.add("rl_mixed_sizes", src, "every page a different size and orientation")
    # many pages
    c = canvas.Canvas(str(cp.path("rl_1200_pages")))
    for n in range(1200):
        c.drawString(72, 720, f"Page {n + 1} of 1200: a long report")
        c.drawString(72, 700, LOREM[: (n % 7 + 1) * 12])
        c.showPage()
    c.save()
    cp.add("rl_1200_pages", src, "1200 pages of text")
    # annotations of every kind (reportlab makes links, text notes, freetext-ish via raw)
    c = canvas.Canvas(str(cp.path("rl_links_outline")))
    for n in range(40):
        c.bookmarkPage(f"p{n}")
        level = min(n % 6, 5)
        c.addOutlineEntry(f"Heading {n} level {level}", f"p{n}", level=min(level, 0 if n == 0 else level))
        c.drawString(72, 720, f"Section {n}")
        c.linkAbsolute(f"to p{(n + 5) % 40}", f"p{(n + 5) % 40}", (70, 700, 300, 715))
        c.drawString(72, 703, f"go to section {(n + 5) % 40}")
        c.linkURL("https://example.com/x?y=1", (70, 680, 300, 695))
        c.showPage()
    c.showOutline()
    c.save()
    cp.add("rl_links_outline", src, "deep outline, internal and web links")
    # forms of every type
    c = canvas.Canvas(str(cp.path("rl_form_all")))
    f = c.acroForm
    c.drawString(50, 760, "Name:")
    f.textfield(name="name", x=120, y=750, width=200, height=20, value="Jane")
    f.textfield(name="notes", x=120, y=640, width=300, height=90, fieldFlags="multiline", value="Line 1\nLine 2")
    f.textfield(name="secret", x=120, y=610, width=150, height=20, fieldFlags="password")
    f.checkbox(name="agree", x=120, y=580, checked=True)
    f.radio(name="choice", value="a", x=120, y=550, selected=True)
    f.radio(name="choice", value="b", x=150, y=550)
    f.choice(name="country", x=120, y=510, width=150, height=20, options=["UK", "US", "FR"], value="US")
    f.listbox(name="many", x=120, y=420, width=150, height=60, options=["one", "two", "three", "four"],
              value="two")
    c.showPage()
    c.drawString(50, 760, "Page two with a field")
    f.textfield(name="page2", x=120, y=700, width=200, height=20)
    c.showPage()
    c.save()
    cp.add("rl_form_all", src, "AcroForm: text, multiline, password, checkbox, radio, combo, list")
    # images
    img = Image.new("RGB", (400, 300))
    d = ImageDraw.Draw(img)
    for i in range(0, 400, 20):
        d.rectangle([i, 0, i + 10, 300], fill=(i % 255, 100, 200))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    from reportlab.lib.utils import ImageReader

    c = canvas.Canvas(str(cp.path("rl_images_text")))
    c.drawImage(ImageReader(io.BytesIO(buf.getvalue())), 50, 400, 400, 300)
    c.drawString(50, 380, "Caption under an image")
    rgba = Image.new("RGBA", (200, 200), (255, 0, 0, 0))
    ImageDraw.Draw(rgba).ellipse([10, 10, 190, 190], fill=(0, 0, 255, 128))
    b2 = io.BytesIO()
    rgba.save(b2, "PNG")
    c.drawImage(ImageReader(io.BytesIO(b2.getvalue())), 300, 100, 200, 200, mask="auto")
    c.showPage()
    c.save()
    cp.add("rl_images_text", src, "RGB image, RGBA image with soft mask, text")
    # rotated and boxed pages
    for rot in (90, 180, 270, -90, 450):
        text_pages(cp.path(f"rl_rotate_{rot}"), 2)
        with pikepdf.open(cp.path(f"rl_rotate_{rot}"), allow_overwriting_input=True) as pdf:
            for p in pdf.pages:
                p.obj.Rotate = rot
            pdf.save(cp.path(f"rl_rotate_{rot}"))
        cp.add(f"rl_rotate_{rot}", src, f"/Rotate {rot}", "valid" if rot % 90 == 0 and rot in (90, 180, 270) else "hostile")
    # tables drawn with lines (for to-excel)
    c = canvas.Canvas(str(cp.path("rl_ruled_table")))
    x0, y0 = 50, 700
    for r in range(12):
        for col in range(5):
            c.rect(x0 + col * 100, y0 - r * 20, 100, 20)
            c.drawString(x0 + col * 100 + 4, y0 - r * 20 + 6, f"R{r}C{col}" if r else f"Head {col}")
    c.showPage()
    c.save()
    cp.add("rl_ruled_table", src, "a ruled table")
    # two columns
    c = canvas.Canvas(str(cp.path("rl_two_columns")))
    for col, x in enumerate((50, 320)):
        y = 750
        for i in range(40):
            c.drawString(x, y, (LOREM * 3)[i * 13 + col * 7: i * 13 + col * 7 + 40])
            y -= 16
    c.showPage()
    c.save()
    cp.add("rl_two_columns", src, "two text columns")
    # vector drawing only, no text
    c = canvas.Canvas(str(cp.path("rl_vector_only")))
    for i in range(300):
        c.setStrokeColorRGB(i / 300, 0.3, 1 - i / 300)
        c.circle(300, 400, i, stroke=1, fill=0)
    c.showPage()
    c.save()
    cp.add("rl_vector_only", src, "vector drawing, no text")
    # blank pages
    c = canvas.Canvas(str(cp.path("rl_blank")))
    for _ in range(3):
        c.showPage()
    c.save()
    cp.add("rl_blank", src, "three blank pages")
    # invisible text (render mode 3) over an image, like an OCRed scan
    c = canvas.Canvas(str(cp.path("rl_invisible_text")))
    c.drawImage(ImageReader(io.BytesIO(buf.getvalue())), 0, 0, 612, 792)
    t = c.beginText(72, 700)
    t.setTextRenderMode(3)
    t.textLine("Hidden OCR text layer here")
    c.drawText(t)
    c.showPage()
    c.save()
    cp.add("rl_invisible_text", src, "image page with invisible text (OCRed scan look)")
    # long words, many tiny letters, huge font
    c = canvas.Canvas(str(cp.path("rl_text_extremes")))
    c.setFont("Helvetica", 2)
    for i in range(300):
        c.drawString(10, 10 + i * 2.5, LOREM * 3)
    c.showPage()
    c.setFont("Helvetica", 400)
    c.drawString(10, 300, "Ab")
    c.showPage()
    c.setFont("Helvetica", 12)
    c.drawString(10, 400, "x" * 2000)
    c.showPage()
    c.save()
    cp.add("rl_text_extremes", src, "2pt text, 400pt text, a 2000-letter word")
    # text in transforms
    c = canvas.Canvas(str(cp.path("rl_rotated_text")))
    for ang in range(0, 360, 30):
        c.saveState()
        c.translate(300, 400)
        c.rotate(ang)
        c.drawString(20, 0, f"Rotated text {ang} degrees")
        c.restoreState()
    c.saveState()
    c.transform(1, 0, 0, -1, 0, 792)
    c.drawString(50, 50, "Mirrored text")
    c.restoreState()
    c.showPage()
    c.save()
    cp.add("rl_rotated_text", src, "text at every angle, mirrored text")


# ---------------------------------------------------------------------------
# raw PDFs, written byte by byte
# ---------------------------------------------------------------------------

def _raw(objects, root=1, version="1.7", trailer_extra=""):
    out = bytearray(f"%PDF-{version}\n%\xe2\xe3\xcf\xd3\n".encode("latin-1"))
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        body = body.encode("latin-1") if isinstance(body, str) else body
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root {root} 0 R {trailer_extra}>>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def _stream(d, data):
    return f"<< {d} /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"


def gen_raw(cp):
    src = "raw"
    # Type 3 font: glyphs drawn as boxes
    glyph = b"500 0 0 0 400 600 d1 0 0 400 600 re f"
    t3 = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /T3 5 0 R >> >> >>",
        _stream("", b"BT /T3 24 Tf 72 700 Td (ABCAB) Tj ET BT /T3 12 Tf 72 600 Td (CAB) Tj ET"),
        "<< /Type /Font /Subtype /Type3 /FontBBox [0 0 500 700] /FontMatrix [0.001 0 0 0.001 0 0] "
        "/CharProcs << /a 6 0 R /b 6 0 R /c 6 0 R >> /Encoding << /Type /Encoding /Differences [65 /a /b /c] >> "
        "/FirstChar 65 /LastChar 67 /Widths [500 500 500] /ToUnicode 7 0 R >>",
        _stream("", glyph),
        _stream("", b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /T3 def "
                    b"1 begincodespacerange <00> <FF> endcodespacerange 3 beginbfchar <41> <0041> <42> <0042> "
                    b"<43> <0043> endbfchar endcmap CMapName currentdict /CMap defineresource pop end end"),
    ]
    cp.write("raw_type3_font", _raw(t3), src, "Type 3 font with ToUnicode")
    t3b = list(t3)
    t3b[4] = t3b[4].replace(" /ToUnicode 7 0 R", "")
    cp.write("raw_type3_no_tounicode", _raw(t3b), src, "Type 3 font without ToUnicode")
    page = ("<< /Type /Page /Parent 2 0 R /MediaBox {mb} {extra} /Contents 4 0 R "
            "/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>")
    content = _stream("", b"BT /F1 24 Tf 72 700 Td (Hello boxes) Tj ET 0 0 m 612 792 l S")

    def one(mb, extra="", version="1.7", kind="valid"):
        return _raw(["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                     page.format(mb=mb, extra=extra), content], version=version)

    for name, mb, extra, kind, what in [
        ("offset_mediabox", "[100 100 712 892]", "", "valid", "MediaBox not at origin"),
        ("negative_mediabox", "[-306 -396 306 396]", "", "valid", "MediaBox centred on origin"),
        ("cropbox_smaller", "[0 0 612 792]", "/CropBox [50 50 562 742]", "valid", "CropBox inside MediaBox"),
        ("cropbox_outside", "[0 0 612 792]", "/CropBox [-100 -100 900 1000]", "hostile", "CropBox larger than MediaBox"),
        ("cropbox_disjoint", "[0 0 612 792]", "/CropBox [1000 1000 1200 1200]", "hostile", "CropBox outside MediaBox"),
        ("cropbox_rotate90", "[0 0 612 792]", "/CropBox [50 50 562 742] /Rotate 90", "valid", "CropBox + Rotate 90"),
        ("offset_rotate270", "[100 200 712 992]", "/Rotate 270", "valid", "offset MediaBox + Rotate 270"),
        ("all_boxes", "[0 0 612 792]", "/CropBox [10 10 602 782] /BleedBox [5 5 607 787] /TrimBox [20 20 592 772] "
         "/ArtBox [30 30 582 762]", "valid", "Crop/Bleed/Trim/Art boxes"),
        ("userunit", "[0 0 612 792]", "/UserUnit 2.5", "valid", "UserUnit 2.5"),
        ("float_mediabox", "[0.25 0.5 611.75 791.999]", "", "valid", "fractional MediaBox"),
        ("mediabox_refs", "[0 0 612 792.0]", "/Rotate -270", "hostile", "negative rotation"),
        ("mediabox_strings", "[(0) 0 612 792]", "", "hostile", "a string in the MediaBox"),
        ("mediabox_three", "[0 0 612]", "", "hostile", "MediaBox with three numbers"),
        ("no_mediabox", "[0 0 612 792] /MediaBox null", "", "hostile", "MediaBox null"),
    ]:
        cp.write(f"raw_{name}", one(mb, extra), src, what, kind)
    # inherited attributes in the page tree
    cp.write("raw_inherited", _raw([
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [5 0 R] /Count 2 /MediaBox [0 0 500 500] /Rotate 90 "
        "/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>",
        "<< /Type /Page /Parent 5 0 R /Contents 4 0 R >>",
        _stream("", b"BT /F1 24 Tf 72 300 Td (Inherited) Tj ET"),
        "<< /Type /Pages /Parent 2 0 R /Kids [3 0 R 6 0 R] /Count 2 /CropBox [10 10 490 490] >>",
        "<< /Type /Page /Parent 5 0 R /Contents 4 0 R /Rotate 0 >>",
    ]), src, "MediaBox/Rotate/Resources/CropBox inherited through a nested page tree")
    # contents as an array of streams, one empty, split mid-operator
    cp.write("raw_contents_array", _raw([
        "<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents [4 0 R 5 0 R 6 0 R] "
        "/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>",
        _stream("", b"BT /F1 24 Tf 72 700"), _stream("", b""), _stream("", b" Td (Split contents) Tj ET")]),
        src, "page contents split across streams mid-operator")
    # page without contents or resources
    cp.write("raw_bare_page", _raw(["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                                    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>"]),
             src, "page with no contents and no resources")
    # wrong /Count
    for cnt, kind in (("5", "hostile"), ("0", "hostile"), ("-1", "hostile")):
        cp.write(f"raw_wrong_count_{cnt.replace('-', 'm')}", _raw([
            "<< /Type /Catalog /Pages 2 0 R >>", f"<< /Type /Pages /Kids [3 0 R] /Count {cnt} >>",
            page.format(mb="[0 0 612 792]", extra=""), content]), src, f"page tree /Count {cnt} for one page", kind)
    # missing /Type /Page
    cp.write("raw_page_no_type", _raw([
        "<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        page.format(mb="[0 0 612 792]", extra="").replace("/Type /Page ", ""), content]),
        src, "a page without /Type", "hostile")
    # PDF 2.0 header, PDF 1.0 header, no header version
    cp.write("raw_pdf20", one("[0 0 612 792]", version="2.0"), src, "PDF 2.0 header")
    cp.write("raw_pdf10", one("[0 0 612 792]", version="1.0"), src, "PDF 1.0 header")
    data = one("[0 0 612 792]")
    cp.write("raw_junk_before_header", b"JUNK" * 200 + b"\n" + data, src, "800 bytes of junk before %PDF", "hostile")
    cp.write("raw_junk_after_eof", data + b"\x00" * 5000 + b"trailing garbage", src, "junk after %%EOF", "hostile")
    cp.write("raw_crlf", data.replace(b"\n", b"\r\n"), src, "CRLF line ends (xref offsets now wrong)", "hostile")
    cp.write("raw_cr_only", data.replace(b"\n", b"\r"), src, "CR line ends", "hostile")
    cp.write("raw_no_eof", data.replace(b"%%EOF\n", b""), src, "no %%EOF", "hostile")
    cp.write("raw_no_xref", data[:data.index(b"xref")] + b"trailer\n<< /Root 1 0 R >>\n%%EOF\n", src,
             "no cross-reference table", "hostile")
    # missing /Length, wrong /Length
    nolen = data.replace(b"/Length ", b"/Lxngth ")
    cp.write("raw_missing_length", nolen, src, "streams without /Length", "hostile")
    cp.write("raw_wrong_length", data.replace(b"/Length 56", b"/Length 9999"), src, "/Length too long", "hostile")
    cp.write("raw_length_indirect_missing", data.replace(b"/Length 56", b"/Length 99 0 R"), src,
             "/Length points to a missing object", "hostile")
    # name/string oddities and unicode metadata
    cp.write("raw_odd_info", _raw(["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                                   page.format(mb="[0 0 612 792]", extra=""), content,
                                   "<< /Title <FEFF00480069> /Author (\\330\\331 bad \\\\ octal) "
                                   "/CreationDate (D:20231399999999Z) /ModDate (yesterday) /Keywords 42 "
                                   "/Producer [/Array] >>"], trailer_extra="/Info 5 0 R"),
             src, "Info with UTF-16 title, bad dates, number and array values", "hostile")
    # Deeply nested arrays / dictionaries
    deep = "[" * 3000 + "]" * 3000
    cp.write("raw_deep_nesting", _raw(["<< /Type /Catalog /Pages 2 0 R /Deep " + deep + " >>",
                                       "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                                       page.format(mb="[0 0 612 792]", extra=""), content]),
             src, "3000-deep nested array in the catalog", "hostile")
    # outline loop
    cp.write("raw_outline_loop", _raw([
        "<< /Type /Catalog /Pages 2 0 R /Outlines 5 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        page.format(mb="[0 0 612 792]", extra=""), content,
        "<< /Type /Outlines /First 6 0 R /Last 7 0 R /Count 2 >>",
        "<< /Title (One) /Parent 5 0 R /Next 7 0 R /Dest [3 0 R /Fit] >>",
        "<< /Title (Two) /Parent 5 0 R /Next 6 0 R /Prev 6 0 R /Dest [3 0 R /XYZ null null null] >>"]),
        src, "outline whose /Next loops", "hostile")
    cp.write("raw_outline_bad_dests", _raw([
        "<< /Type /Catalog /Pages 2 0 R /Outlines 5 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        page.format(mb="[0 0 612 792]", extra=""), content,
        "<< /Type /Outlines /First 6 0 R /Last 8 0 R /Count 3 >>",
        "<< /Title (Missing page) /Parent 5 0 R /Next 7 0 R /Dest [99 0 R /Fit] >>",
        "<< /Title (Named dest nowhere) /Parent 5 0 R /Prev 6 0 R /Next 8 0 R /Dest (nowhere) >>",
        "<< /Title <FEFFD83DDE00> /Parent 5 0 R /Prev 7 0 R /A << /S /GoTo /D [7] >> >>"]),
        src, "outline items pointing at missing pages, missing names, numbers", "hostile")
    # annotations of every subtype
    subs = ["Text", "Link", "FreeText", "Line", "Square", "Circle", "Polygon", "PolyLine", "Highlight", "Underline",
            "Squiggly", "StrikeOut", "Stamp", "Caret", "Ink", "Popup", "FileAttachment", "Sound", "Movie", "Widget",
            "Screen", "PrinterMark", "TrapNet", "Watermark", "3D", "Redact", "Projection", "RichMedia", "Bogus"]
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>", None, content]
    annots = []
    for i, s in enumerate(subs):
        x, y = 30 + (i % 6) * 95, 650 - (i // 6) * 110
        extra = ""
        if s in ("Line",):
            extra = f"/L [{x} {y} {x + 60} {y + 60}]"
        elif s in ("Polygon", "PolyLine"):
            extra = f"/Vertices [{x} {y} {x + 50} {y} {x + 25} {y + 50}]"
        elif s in ("Highlight", "Underline", "Squiggly", "StrikeOut", "Redact"):
            extra = f"/QuadPoints [{x} {y + 20} {x + 80} {y + 20} {x} {y} {x + 80} {y}]"
        elif s == "Ink":
            extra = f"/InkList [[{x} {y} {x + 30} {y + 40} {x + 60} {y}]]"
        elif s == "Link":
            extra = "/A << /S /URI /URI (https://example.com) >>"
        elif s == "FileAttachment":
            extra = "/FS << /Type /Filespec /F (a.txt) /EF << /F 4 0 R >> >>"
        elif s == "Widget":
            extra = "/FT /Tx /T (orphan_widget) /V (x)"
        elif s == "FreeText":
            extra = "/DA (/Helv 10 Tf 0 g)"
        annots.append(f"<< /Type /Annot /Subtype /{s} /Rect [{x} {y} {x + 80} {y + 80}] /Contents (Note {s}) "
                      f"/T (Reviewer) /C [1 0 0] /F 4 {extra} >>")
    first = len(objs) + 1
    objs[2] = page.format(mb="[0 0 612 792]", extra="/Annots [" + " ".join(f"{first + i} 0 R" for i in range(len(annots))) + "]")
    cp.write("raw_annots_every_subtype", _raw(objs + annots), src, "one annotation of every subtype, some unknown",
             "hostile")
    # thousands of annotations
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>", None, content]
    n = 3000
    annots = [f"<< /Type /Annot /Subtype /{['Text', 'Square', 'Highlight', 'Link'][i % 4]} "
              f"/Rect [{i % 60 * 10} {i // 60 * 15} {i % 60 * 10 + 8} {i // 60 * 15 + 8}] /Contents (n{i}) "
              f"/QuadPoints [0 8 8 8 0 0 8 0] >>" for i in range(n)]
    objs[2] = page.format(mb="[0 0 612 792]", extra="/Annots [" + " ".join(f"{5 + i} 0 R" for i in range(n)) + "]")
    cp.write("raw_3000_annots", _raw(objs + annots), src, "3000 annotations on one page")
    # JavaScript everywhere, launch actions, submit form
    cp.write("raw_actions", _raw([
        "<< /Type /Catalog /Pages 2 0 R /OpenAction 5 0 R /AA << /WC 6 0 R >> /Names << /JavaScript 7 0 R >> >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        page.format(mb="[0 0 612 792]", extra="/AA << /O 6 0 R >> /Annots [9 0 R]"), content,
        "<< /S /JavaScript /JS (app.alert\\('open'\\); this.print\\(\\);) /Next 8 0 R >>",
        "<< /S /JavaScript /JS 10 0 R >>",
        "<< /Names [(a) 6 0 R] >>",
        "<< /S /Launch /F (cmd.exe) /Win << /F (calc.exe) >> >>",
        "<< /Type /Annot /Subtype /Link /Rect [0 0 100 100] /A << /S /SubmitForm /F (http://example.com/x) >> >>",
        _stream("", b"while(true){}"),
    ]), src, "JavaScript open action and names tree, launch, submit-form", "hostile")
    # Optional content (layers)
    cp.write("raw_layers", _raw([
        "<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [5 0 R 6 0 R] /D << /ON [5 0 R] /OFF [6 0 R] "
        "/Order [5 0 R 6 0 R] >> >> >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 << "
        "/Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> /Properties << /L1 5 0 R /L2 6 0 R >> >> >>",
        _stream("", b"/OC /L1 BDC BT /F1 20 Tf 72 700 Td (Visible layer) Tj ET EMC "
                    b"/OC /L2 BDC BT /F1 20 Tf 72 650 Td (Hidden layer text) Tj ET EMC"),
        "<< /Type /OCG /Name (Shown) >>", "<< /Type /OCG /Name (Hidden) >>"]),
        src, "optional content: one layer on, one off")
    # transparency group + soft mask in ExtGState
    cp.write("raw_transparency", _raw([
        "<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Group << /S /Transparency /CS "
        "/DeviceRGB /I true /K false >> /Resources << /ExtGState << /G1 5 0 R /G2 7 0 R >> /XObject << /Fm 6 0 R >> >> >>",
        _stream("", b"/G1 gs 1 0 0 rg 100 100 300 300 re f /G2 gs /Fm Do"),
        "<< /Type /ExtGState /ca 0.4 /CA 0.4 /BM /Multiply >>",
        _stream("/Type /XObject /Subtype /Form /BBox [0 0 612 792] /Group << /S /Transparency >>",
                b"0 0 1 rg 200 200 300 300 re f"),
        "<< /Type /ExtGState /SMask << /Type /Mask /S /Luminosity /G 6 0 R >> >>"]),
        src, "transparency groups, blend mode, luminosity soft mask")
    # shading and patterns
    cp.write("raw_shading", _raw([
        "<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Shading << /Sh 5 0 R >> >> >>",
        _stream("", b"q 0 0 612 792 re W n /Sh sh Q"),
        "<< /ShadingType 2 /ColorSpace /DeviceRGB /Coords [0 0 612 792] /Function << /FunctionType 2 /Domain [0 1] "
        "/C0 [1 0 0] /C1 [0 0 1] /N 1 >> /Extend [true true] >>"]), src, "axial shading fill")
    # inline images, odd ones
    cp.write("raw_inline_images", _raw([
        "<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        _stream("", b"q 100 0 0 100 50 600 cm BI /W 4 /H 4 /CS /G /BPC 8 ID \x00\x40\x80\xff\x00\x40\x80\xff"
                    b"\x00\x40\x80\xff\x00\x40\x80\xff EI Q q 100 0 0 100 200 600 cm BI /W 2 /H 2 /CS /RGB /BPC 8 "
                    b"/F /AHx ID 00ff00ff000000ff00ffffff> EI Q q 50 0 0 50 300 600 cm BI /W 8 /H 1 /IM true /D [1 0] ID \xaa EI Q")]),
        src, "inline images: gray, ASCIIHex RGB, image mask")


# ---------------------------------------------------------------------------
# images (Pillow) inside PDFs, through pikepdf
# ---------------------------------------------------------------------------

def _image_pdf(path, image_dict_fn, content=b"q 400 0 0 300 100 400 cm /Im0 Do Q BT /F1 14 Tf 100 380 Td (Image caption) Tj ET"):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    im = image_dict_fn(pdf)
    page.obj.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=im),
                                            Font=pikepdf.Dictionary(F1=pikepdf.Dictionary(
                                                Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name.Helvetica)))
    page.obj.Contents = pdf.make_stream(content)
    pdf.save(path)


def _photo(w=400, h=300, mode="RGB"):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for i in range(0, w, 8):
        d.line([(i, 0), (w - i, h)], fill=(i % 256, (i * 3) % 256, 255 - i % 256), width=4)
    d.text((10, 10), "Picture text 123", fill=(255, 255, 255))
    return img.convert(mode)


def gen_images(cp):
    src = "pikepdf-images"

    def stream(data, **kw):
        def fn(pdf):
            return pikepdf.Stream(pdf, data, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image, **kw)
        return fn

    def jpeg(img, **save):
        b = io.BytesIO()
        img.save(b, "JPEG", **save)
        return b.getvalue()

    photo = _photo()
    cases = []
    cases.append(("img_dct_rgb", stream(jpeg(photo), Width=400, Height=300, ColorSpace=pikepdf.Name.DeviceRGB,
                                        BitsPerComponent=8, Filter=pikepdf.Name.DCTDecode), "JPEG RGB", "valid"))
    cases.append(("img_dct_gray", stream(jpeg(photo.convert("L")), Width=400, Height=300,
                                         ColorSpace=pikepdf.Name.DeviceGray, BitsPerComponent=8,
                                         Filter=pikepdf.Name.DCTDecode), "JPEG gray", "valid"))
    cmyk = photo.convert("CMYK")
    cases.append(("img_dct_cmyk", stream(jpeg(cmyk), Width=400, Height=300, ColorSpace=pikepdf.Name.DeviceCMYK,
                                         BitsPerComponent=8, Filter=pikepdf.Name.DCTDecode), "JPEG CMYK", "valid"))
    cases.append(("img_dct_cmyk_inverted", stream(jpeg(cmyk), Width=400, Height=300,
                                                  ColorSpace=pikepdf.Name.DeviceCMYK, BitsPerComponent=8,
                                                  Decode=pikepdf.Array([1, 0] * 4), Filter=pikepdf.Name.DCTDecode),
                  "JPEG CMYK with inverted Decode (Adobe)", "valid"))
    cases.append(("img_dct_progressive", stream(jpeg(photo, progressive=True), Width=400, Height=300,
                                                ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8,
                                                Filter=pikepdf.Name.DCTDecode), "progressive JPEG", "valid"))
    raw = photo.tobytes()
    cases.append(("img_flate_rgb", stream(zlib.compress(raw), Width=400, Height=300,
                                          ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8,
                                          Filter=pikepdf.Name.FlateDecode), "Flate RGB", "valid"))
    # PNG predictor
    rows = b"".join(b"\x02" + raw[y * 1200:(y + 1) * 1200] for y in range(300))
    cases.append(("img_flate_predictor", stream(zlib.compress(rows), Width=400, Height=300,
                                                ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8,
                                                Filter=pikepdf.Name.FlateDecode,
                                                DecodeParms=pikepdf.Dictionary(Predictor=15, Colors=3, Columns=400,
                                                                               BitsPerComponent=8)),
                  "Flate with PNG predictor (rows unfiltered as Up)", "valid"))
    # 16-bit
    import numpy as np

    arr16 = (np.linspace(0, 65535, 400 * 300 * 3).reshape(300, 400, 3)).astype(">u2")
    cases.append(("img_16bit_rgb", stream(zlib.compress(arr16.tobytes()), Width=400, Height=300,
                                          ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=16,
                                          Filter=pikepdf.Name.FlateDecode), "16-bit RGB image", "valid"))
    # 1-bit, 2-bit, 4-bit
    for bpc in (1, 2, 4):
        row = (400 * bpc + 7) // 8
        data = bytes((i * 37) % 256 for i in range(row * 300))
        cases.append((f"img_gray_{bpc}bit", stream(zlib.compress(data), Width=400, Height=300,
                                                   ColorSpace=pikepdf.Name.DeviceGray, BitsPerComponent=bpc,
                                                   Filter=pikepdf.Name.FlateDecode), f"{bpc}-bit gray", "valid"))
    # indexed
    pal = photo.convert("P", palette=Image.ADAPTIVE, colors=16)
    palette = bytes(pal.getpalette()[:48])

    def indexed(pdf):
        return pikepdf.Stream(pdf, zlib.compress(pal.tobytes()), Type=pikepdf.Name.XObject,
                              Subtype=pikepdf.Name.Image, Width=400, Height=300, BitsPerComponent=8,
                              ColorSpace=pikepdf.Array([pikepdf.Name.Indexed, pikepdf.Name.DeviceRGB, 15,
                                                        pikepdf.String(palette)]),
                              Filter=pikepdf.Name.FlateDecode)
    cases.append(("img_indexed", indexed, "indexed (palette) image", "valid"))

    # ICC-based
    def iccbased(pdf):
        from PIL import ImageCms

        prof = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        icc = pikepdf.Stream(pdf, prof, N=3)
        return pikepdf.Stream(pdf, zlib.compress(raw), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                              Width=400, Height=300, BitsPerComponent=8,
                              ColorSpace=pikepdf.Array([pikepdf.Name.ICCBased, icc]), Filter=pikepdf.Name.FlateDecode)
    cases.append(("img_iccbased", iccbased, "ICCBased colour space", "valid"))

    # Lab / Separation
    def separation(pdf):
        fn = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 0.5, 1, 0], N=1)
        return pikepdf.Stream(pdf, zlib.compress(photo.convert("L").tobytes()), Type=pikepdf.Name.XObject,
                              Subtype=pikepdf.Name.Image, Width=400, Height=300, BitsPerComponent=8,
                              ColorSpace=pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/Spot Orange"),
                                                        pikepdf.Name.DeviceCMYK, fn]),
                              Filter=pikepdf.Name.FlateDecode)
    cases.append(("img_separation", separation, "Separation (spot colour) image", "valid"))

    def lab(pdf):
        return pikepdf.Stream(pdf, zlib.compress(raw), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                              Width=400, Height=300, BitsPerComponent=8,
                              ColorSpace=pikepdf.Array([pikepdf.Name.Lab, pikepdf.Dictionary(
                                  WhitePoint=[0.9505, 1, 1.089], Range=[-100, 100, -100, 100])]),
                              Filter=pikepdf.Name.FlateDecode)
    cases.append(("img_lab", lab, "Lab colour space image", "valid"))

    # soft mask
    def smask(pdf):
        mask = Image.radial_gradient("L").resize((400, 300))
        sm = pikepdf.Stream(pdf, zlib.compress(mask.tobytes()), Type=pikepdf.Name.XObject,
                            Subtype=pikepdf.Name.Image, Width=400, Height=300, BitsPerComponent=8,
                            ColorSpace=pikepdf.Name.DeviceGray, Filter=pikepdf.Name.FlateDecode)
        return pikepdf.Stream(pdf, zlib.compress(raw), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                              Width=400, Height=300, BitsPerComponent=8, ColorSpace=pikepdf.Name.DeviceRGB,
                              Filter=pikepdf.Name.FlateDecode, SMask=sm)
    cases.append(("img_smask", smask, "image with a soft mask", "valid"))

    def colorkey(pdf):
        return pikepdf.Stream(pdf, zlib.compress(raw), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                              Width=400, Height=300, BitsPerComponent=8, ColorSpace=pikepdf.Name.DeviceRGB,
                              Filter=pikepdf.Name.FlateDecode, Mask=pikepdf.Array([0, 60, 0, 60, 0, 255]))
    cases.append(("img_colorkey_mask", colorkey, "colour-key masked image", "valid"))

    def smask_size(pdf):
        mask = Image.radial_gradient("L").resize((50, 30))
        sm = pikepdf.Stream(pdf, zlib.compress(mask.tobytes()), Type=pikepdf.Name.XObject,
                            Subtype=pikepdf.Name.Image, Width=50, Height=30, BitsPerComponent=8,
                            ColorSpace=pikepdf.Name.DeviceGray, Filter=pikepdf.Name.FlateDecode)
        return pikepdf.Stream(pdf, zlib.compress(raw), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                              Width=400, Height=300, BitsPerComponent=8, ColorSpace=pikepdf.Name.DeviceRGB,
                              Filter=pikepdf.Name.FlateDecode, SMask=sm)
    cases.append(("img_smask_other_size", smask_size, "soft mask of another size than its image", "valid"))

    # JPX
    try:
        b = io.BytesIO()
        photo.save(b, "JPEG2000")
        cases.append(("img_jpx", stream(b.getvalue(), Width=400, Height=300, Filter=pikepdf.Name.JPXDecode),
                      "JPEG 2000 image", "valid"))
        b = io.BytesIO()
        photo.convert("L").save(b, "JPEG2000")
        cases.append(("img_jpx_gray", stream(b.getvalue(), Width=400, Height=300, ColorSpace=pikepdf.Name.DeviceGray,
                                             BitsPerComponent=8, Filter=pikepdf.Name.JPXDecode),
                      "JPEG 2000 gray image", "valid"))
    except Exception as exc:
        print("  no JPEG 2000:", exc, file=sys.stderr)
    # CCITT G4 via TIFF from Pillow
    try:
        bw = photo.convert("1")
        b = io.BytesIO()
        bw.save(b, "TIFF", compression="group4")
        tif = Image.open(io.BytesIO(b.getvalue()))
        # take the strip data out of the TIFF
        offsets, counts = tif.tag_v2[273], tif.tag_v2[279]
        tb = b.getvalue()
        g4 = b"".join(tb[o:o + c] for o, c in zip(offsets, counts))
        cases.append(("img_ccitt_g4", stream(g4, Width=400, Height=300, ColorSpace=pikepdf.Name.DeviceGray,
                                             BitsPerComponent=1, Filter=pikepdf.Name.CCITTFaxDecode,
                                             DecodeParms=pikepdf.Dictionary(K=-1, Columns=400, Rows=300,
                                                                            BlackIs1=False)),
                      "CCITT Group 4 image", "valid"))
    except Exception as exc:
        print("  no CCITT:", exc, file=sys.stderr)
    # filter chains
    import base64

    a85 = base64.a85encode(zlib.compress(raw), adobe=True)
    cases.append(("img_a85_flate", stream(a85, Width=400, Height=300, ColorSpace=pikepdf.Name.DeviceRGB,
                                          BitsPerComponent=8, Filter=pikepdf.Array([pikepdf.Name.ASCII85Decode,
                                                                                    pikepdf.Name.FlateDecode])),
                  "ASCII85 + Flate filter chain", "valid"))
    hexd = zlib.compress(raw).hex().encode() + b">"
    cases.append(("img_ahx_flate", stream(hexd, Width=400, Height=300, ColorSpace=pikepdf.Name.DeviceRGB,
                                          BitsPerComponent=8, Filter=pikepdf.Array([pikepdf.Name.ASCIIHexDecode,
                                                                                    pikepdf.Name.FlateDecode])),
                  "ASCIIHex + Flate filter chain", "valid"))
    # RunLength
    def rle(data):
        out = bytearray()
        for i in range(0, len(data), 128):
            chunk = data[i:i + 128]
            out.append(len(chunk) - 1)
            out += chunk
        out.append(128)
        return bytes(out)
    cases.append(("img_runlength", stream(rle(raw), Width=400, Height=300, ColorSpace=pikepdf.Name.DeviceRGB,
                                          BitsPerComponent=8, Filter=pikepdf.Name.RunLengthDecode),
                  "RunLength image", "valid"))
    # LZW (written with a tiny encoder)
    def lzw(data):
        table = {bytes([i]): i for i in range(256)}
        bits, out, buf, nbits, code = 9, bytearray(), 0, 0, 258

        def emit(v):
            nonlocal buf, nbits
            buf = (buf << bits) | v
            nbits += bits
            while nbits >= 8:
                nbits -= 8
                out.append((buf >> nbits) & 0xFF)
        emit(256)
        w = b""
        for ch in data:
            wc = w + bytes([ch])
            if wc in table:
                w = wc
                continue
            emit(table[w])
            table[wc] = code
            code += 1
            if code >= (1 << bits):
                if bits < 12:
                    bits += 1
                else:
                    emit(256)
                    table = {bytes([i]): i for i in range(256)}
                    code, bits = 258, 9
            w = bytes([ch])
        if w:
            emit(table[w])
        emit(257)
        if nbits:
            out.append((buf << (8 - nbits)) & 0xFF)
        return bytes(out)
    small = _photo(120, 90).tobytes()

    def lzwimg(pdf):
        return pikepdf.Stream(pdf, lzw(small), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image, Width=120,
                              Height=90, ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8,
                              Filter=pikepdf.Name.LZWDecode)
    cases.append(("img_lzw", lzwimg, "LZW image", "valid"))
    # odd/broken image dictionaries
    cases.append(("img_bad_bpc", stream(zlib.compress(raw), Width=400, Height=300, ColorSpace=pikepdf.Name.DeviceRGB,
                                        BitsPerComponent=7, Filter=pikepdf.Name.FlateDecode),
                  "image with 7 bits per component", "hostile"))
    cases.append(("img_short_data", stream(zlib.compress(raw[:1000]), Width=400, Height=300,
                                           ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8,
                                           Filter=pikepdf.Name.FlateDecode), "image data too short", "hostile"))
    cases.append(("img_no_colorspace", stream(zlib.compress(raw), Width=400, Height=300, BitsPerComponent=8,
                                              Filter=pikepdf.Name.FlateDecode), "image without ColorSpace",
                  "hostile"))
    cases.append(("img_unknown_filter", stream(raw, Width=400, Height=300, ColorSpace=pikepdf.Name.DeviceRGB,
                                               BitsPerComponent=8, Filter=pikepdf.Name.MadeUpDecode),
                  "image with an unknown filter", "hostile"))
    cases.append(("img_zero_width", stream(b"", Width=0, Height=300, ColorSpace=pikepdf.Name.DeviceRGB,
                                           BitsPerComponent=8), "image 0 pixels wide", "hostile"))
    cases.append(("img_crypt_identity", stream(zlib.compress(raw), Width=400, Height=300,
                                               ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8,
                                               Filter=pikepdf.Name.FlateDecode,
                                               Decode=pikepdf.Array([0, 1, 0, 1])),
                  "Decode array of the wrong length", "hostile"))
    for name, fn, what, kind in cases:
        _try(name, _image_pdf, cp.path(name), fn)
        cp.add(name, src, what, kind)
    # big image page, like a 600 dpi scan
    big = Image.new("L", (5100, 6600), 255)
    d = ImageDraw.Draw(big)
    for i in range(60):
        d.text((300, 300 + i * 100), "Scanned text line %d at 600 dpi" % i, fill=0)
    big.save(cp.path("img_600dpi_scan"), "PDF", resolution=600)
    cp.add("img_600dpi_scan", "pillow", "a 600 dpi grayscale page image")


# ---------------------------------------------------------------------------
# Pillow: images saved as PDF, and scanned-looking pages
# ---------------------------------------------------------------------------

def _font(size):
    for rel in ("dejavu/DejaVuSans.ttf", "liberation/LiberationSerif-Regular.ttf"):
        if (FONTS / rel).exists():
            return ImageFont.truetype(str(FONTS / rel), size)
    return ImageFont.load_default()


def scanned_page(text_lines, angle=0.0, noise=True, size=(1700, 2200), dark=False):
    img = Image.new("L", size, 235 if not dark else 180)
    d = ImageDraw.Draw(img)
    f = _font(34)
    y = 150
    for line in text_lines:
        d.text((150, y), line, fill=20, font=f)
        y += 52
    if noise:
        rnd = random.Random(1)
        for _ in range(4000):
            x, yy = rnd.randrange(size[0]), rnd.randrange(size[1])
            d.point((x, yy), fill=rnd.randrange(0, 120))
    if angle:
        img = img.rotate(angle, expand=False, fillcolor=235)
    return img.filter(ImageFilter.GaussianBlur(0.6))


def gen_pillow(cp):
    src = "pillow"
    lines = [(LOREM * 4)[i * 50:(i + 1) * 50] for i in range(30)]
    for name, kw, what in [("scan_straight", {}, "scanned page, straight"),
                           ("scan_skewed", {"angle": 3.5}, "scanned page, skewed 3.5 degrees"),
                           ("scan_upside_down", {"angle": 180}, "scanned page, upside down"),
                           ("scan_sideways", {"angle": 90}, "scanned page, sideways"),
                           ("scan_dark", {"dark": True}, "dark, low-contrast scan")]:
        img = scanned_page(lines, **kw)
        img.save(cp.path(name), "PDF", resolution=200)
        cp.add(name, src, what)
    pages = [scanned_page(lines[i:] + lines[:i]) for i in range(5)]
    pages.insert(2, Image.new("L", (1700, 2200), 240))
    pages[0].save(cp.path("scan_multipage_blank"), "PDF", resolution=200, save_all=True, append_images=pages[1:])
    cp.add("scan_multipage_blank", src, "six scanned pages, one blank")
    # bilevel scan
    scanned_page(lines).point(lambda v: 255 if v > 128 else 0).convert("1").save(cp.path("scan_bilevel"), "PDF",
                                                                                   resolution=300)
    cp.add("scan_bilevel", src, "black-and-white scan")
    # colour photo of a page
    photo = scanned_page(lines).convert("RGB")
    photo.save(cp.path("scan_color_jpeg"), "PDF", resolution=150, quality=60)
    cp.add("scan_color_jpeg", src, "colour JPEG page")
    # odd resolutions
    _photo().save(cp.path("pil_rgb_72dpi"), "PDF", resolution=72)
    cp.add("pil_rgb_72dpi", src, "photo at 72 dpi")
    _photo().save(cp.path("pil_rgb_1dpi"), "PDF", resolution=1)
    cp.add("pil_rgb_1dpi", src, "photo at 1 dpi (a page 400 inches wide)", "hostile")
    _photo(4, 4).save(cp.path("pil_tiny_image"), "PDF", resolution=300)
    cp.add("pil_tiny_image", src, "a 4x4 pixel image page")
    _photo().convert("CMYK").save(cp.path("pil_cmyk"), "PDF")
    cp.add("pil_cmyk", src, "CMYK image page")
    _photo().convert("P").save(cp.path("pil_palette"), "PDF")
    cp.add("pil_palette", src, "palette image page")
    rgba = _photo().convert("RGBA")
    rgba.putalpha(128)
    try:
        rgba.save(cp.path("pil_rgba"), "PDF")
        cp.add("pil_rgba", src, "RGBA image page")
    except Exception:
        pass
    seq = [_photo(300 + i * 50, 200 + (i % 3) * 120) for i in range(8)]
    seq[0].save(cp.path("pil_photo_album"), "PDF", save_all=True, append_images=seq[1:])
    cp.add("pil_photo_album", src, "8 photos of different sizes")


# ---------------------------------------------------------------------------
# LibreOffice
# ---------------------------------------------------------------------------

HTML_DOC = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:'Liberation Serif'}} table{{border-collapse:collapse}} td,th{{border:1px solid #000;padding:4px}}
.cols{{column-count:2}}</style></head><body>
<h1>{title}</h1><p>{lorem}</p>
<h2>A table</h2>
<table><tr><th>Item</th><th>Qty</th><th>Price</th><th>Total</th></tr>
{rows}
</table>
<h2>Columns</h2><div class="cols"><p>{lorem}{lorem}{lorem}</p></div>
<h2>Languages</h2>
<p lang="zh">中文：发票号码 2024-0042，总计 ¥1,234.00</p>
<p lang="ja">日本語：請求書番号</p>
<p dir="rtl" lang="he">שלום עולם, זה מסמך בדיקה</p>
<p dir="rtl" lang="ar">مرحبا بالعالم، هذا مستند اختبار</p>
<p>Emoji: 😀 🎉 ✅ — math: ∑ √ ∞ ≠ — ligatures: office efficient fjord</p>
<h2>A list</h2><ol>{items}</ol>
<p><img src="{image}" width="300"></p>
<p>Link: <a href="https://example.com">example.com</a></p>
</body></html>"""


def _soffice(src, outdir, filt="pdf"):
    soffice = shutil.which("soffice")
    if not soffice:
        raise RuntimeError("LibreOffice isn't installed")
    subprocess.run([soffice, "--headless", "--norestore", "-env:UserInstallation=file://" + str(outdir / ".lo"),
                    "--convert-to", filt, "--outdir", str(outdir), str(src)],
                   check=True, capture_output=True, timeout=240)


def gen_libreoffice(cp, work):
    src = "libreoffice"
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    img = work / "pic.png"
    _photo().save(img)
    rows = "\n".join(f"<tr><td>Item {i}</td><td>{i}</td><td>{i * 1.5:.2f}</td><td>{i * i * 1.5:.2f}</td></tr>"
                     for i in range(1, 30))
    items = "".join(f"<li>Point {i}: {LOREM[:60]}</li>" for i in range(10))
    html = HTML_DOC.format(title="Office document", lorem=LOREM, rows=rows, items=items, image=img.name)
    (work / "doc.html").write_text(html, encoding="utf-8")
    # docx with footnotes-ish content, headers, tables, columns
    import docx
    from docx.enum.section import WD_ORIENT
    from docx.shared import Inches

    d = docx.Document()
    d.core_properties.title = "Word document"
    d.add_heading("Word document with many parts", 0)
    for i in range(3):
        d.add_heading(f"Chapter {i + 1}", 1)
        d.add_paragraph(LOREM * 3)
        d.add_heading(f"Section {i + 1}.1", 2)
        p = d.add_paragraph("Bold, ", style="List Bullet")
        p.add_run("italic").italic = True
        t = d.add_table(rows=6, cols=4)
        t.style = "Table Grid"
        for r in range(6):
            for c in range(4):
                t.cell(r, c).text = f"r{r}c{c}" if r else f"Header {c}"
        d.add_picture(str(img), width=Inches(3))
    d.sections[0].header.paragraphs[0].text = "Running header"
    d.sections[0].footer.paragraphs[0].text = "Running footer"
    sec = d.add_section()
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = sec.page_height, sec.page_width
    d.add_paragraph("A landscape section. " + LOREM * 5)
    d.save(work / "word.docx")
    # xlsx
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["Region", "Q1", "Q2", "Q3", "Q4", "Total"])
    for r in range(2, 120):
        ws.append([f"Region {r}", r * 10, r * 11, r * 12.5, r * 9, f"=SUM(B{r}:E{r})"])
    ws2 = wb.create_sheet("Wide")
    for r in range(1, 40):
        ws2.append([f"c{r}x{c}" for c in range(1, 30)])
    wb.save(work / "sheet.xlsx")
    # pptx
    from pptx import Presentation
    from pptx.util import Inches as PI

    prs = Presentation()
    for i in range(5):
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = f"Slide {i + 1}"
        s.placeholders[1].text = "First point\nSecond point\n" + LOREM[:80]
        if i == 2:
            s.shapes.add_picture(str(img), PI(5), PI(3), PI(3))
            tb = s.shapes.add_table(4, 3, PI(0.5), PI(4.5), PI(4), PI(1.5)).table
            for r in range(4):
                for c in range(3):
                    tb.cell(r, c).text = f"{r}:{c}"
        s.notes_slide.notes_text_frame.text = f"Speaker notes {i}"
    prs.save(work / "slides.pptx")
    # odt with a form: flat ODF text with controls
    fodt = work / "form.fodt"
    fodt.write_text(FODT_FORM, encoding="utf-8")
    sources = [("lo_html", work / "doc.html", "HTML with table, columns, CJK, RTL, emoji, list, image, link"),
               ("lo_docx", work / "word.docx", "Word document: headings, tables, images, header/footer, landscape"),
               ("lo_xlsx", work / "sheet.xlsx", "spreadsheet: 2 sheets, formulas, wide sheet"),
               ("lo_pptx", work / "slides.pptx", "slides with notes, image, table"),
               ("lo_form", fodt, "Writer form controls exported as PDF form")]
    variants = [("", "pdf", "plain export"),
                ("_tagged", 'pdf:writer_pdf_Export:{"UseTaggedPDF":{"type":"boolean","value":"true"}}', "tagged PDF"),
                ("_pdfa2b", 'pdf:writer_pdf_Export:{"SelectPdfVersion":{"type":"long","value":"2"}}', "PDF/A-2b"),
                ("_pdfua", 'pdf:writer_pdf_Export:{"PDFUACompliance":{"type":"boolean","value":"true"}}', "PDF/UA"),
                ("_bookmarks_notes", 'pdf:writer_pdf_Export:{"ExportNotes":{"type":"boolean","value":"true"},'
                                     '"ExportBookmarks":{"type":"boolean","value":"true"},'
                                     '"ExportFormFields":{"type":"boolean","value":"true"}}', "notes + bookmarks")]
    for base, path, what in sources:
        for suffix, filt, vwhat in variants:
            if suffix in ("_pdfua", "_bookmarks_notes") and base not in ("lo_docx", "lo_html", "lo_form"):
                continue
            outdir = work / f"out{suffix or '_plain'}"
            outdir.mkdir(exist_ok=True)
            try:
                _soffice(path, outdir, filt)
                made = outdir / (path.stem + ".pdf")
                shutil.copy(made, cp.path(base + suffix))
                cp.add(base + suffix, src, f"{what}; {vwhat}")
            except Exception as exc:
                print(f"  soffice {base}{suffix}: {exc}", file=sys.stderr)


FODT_FORM = """<?xml version="1.0" encoding="UTF-8"?>
<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:form="urn:oasis:names:tc:opendocument:xmlns:form:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
 xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
 xmlns:xlink="http://www.w3.org/1999/xlink"
 office:version="1.2" office:mimetype="application/vnd.oasis.opendocument.text">
 <office:body><office:text>
  <office:forms>
   <form:form form:name="Form1">
    <form:text form:name="FullName" form:id="c1" form:value="Jane"/>
    <form:checkbox form:name="Agree" form:id="c2" form:current-state="checked"/>
    <form:listbox form:name="Country" form:id="c3" form:dropdown="true">
      <form:option form:label="UK"/><form:option form:label="US"/></form:listbox>
    <form:button form:name="Send" form:id="c4" form:label="Send"/>
   </form:form>
  </office:forms>
  <text:h text:outline-level="1">Application form</text:h>
  <text:p>Name: <draw:control draw:control="c1" svg:width="6cm" svg:height="0.7cm"/></text:p>
  <text:p>I agree: <draw:control draw:control="c2" svg:width="0.5cm" svg:height="0.5cm"/></text:p>
  <text:p>Country: <draw:control draw:control="c3" svg:width="4cm" svg:height="0.7cm"/></text:p>
  <text:p><draw:control draw:control="c4" svg:width="3cm" svg:height="0.8cm"/></text:p>
 </office:text></office:body></office:document>
"""


# ---------------------------------------------------------------------------
# pikepdf mutations of files already made
# ---------------------------------------------------------------------------

LIGHT = {"objstreams", "aes256_user", "aes128", "incremental", "cut90", "bitflips"}


def gen_mutations(cp, bases, light=False):
    src = "pikepdf-mutation"
    for base in bases:
        path = cp.path(base)
        if not path.exists():
            continue

        def save(suffix, what, kind="valid", **kw):
            name = f"{base}__{suffix}"
            if light and suffix not in LIGHT:
                return
            try:
                with pikepdf.open(path) as pdf:
                    pre = kw.pop("pre", None)
                    if pre:
                        pre(pdf)
                    pdf.save(cp.path(name), **kw)
                cp.add(name, src, f"{base}: {what}", kind)
            except Exception as exc:
                print(f"  mutation {name}: {exc}", file=sys.stderr)

        save("objstreams", "object streams and xref stream", object_stream_mode=pikepdf.ObjectStreamMode.generate)
        save("linearized", "linearized", linearize=True)
        save("qdf", "uncompressed QDF", qdf=True, compress_streams=False,
             stream_decode_level=pikepdf.StreamDecodeLevel.none)
        save("rc4_40", "RC4 40-bit, owner password only (restricted)", encryption=pikepdf.Encryption(
            owner="owner", user="", R=2, metadata=False, aes=False, allow=pikepdf.Permissions(extract=False, print_highres=False,
                                                                   modify_other=False)))
        save("rc4_128_user", "RC4 128 with user password 'user'", "hostile",
             encryption=pikepdf.Encryption(owner="owner", user="user", R=3, metadata=False, aes=False))
        save("aes128", "AES-128, owner password only, no copy", encryption=pikepdf.Encryption(
            owner="owner", user="", R=4, aes=True, allow=pikepdf.Permissions(extract=False)))
        save("aes256_user", "AES-256 user password 'user'", "hostile",
             encryption=pikepdf.Encryption(owner="owner", user="user", R=6))
        save("aes256_owner_only", "AES-256 owner password only, no printing/modify",
             encryption=pikepdf.Encryption(owner="owner", user="", R=6, allow=pikepdf.Permissions(
                 print_lowres=False, print_highres=False, modify_annotation=False, modify_form=False,
                 modify_other=False, modify_assembly=False)))
        save("aes256_meta_clear", "AES-256 owner only, metadata not encrypted",
             encryption=pikepdf.Encryption(owner="owner", user="", R=6, metadata=False))
        save("pdf20", "saved as PDF 2.0", force_version="2.0")

        def add_attach(pdf):
            pdf.attachments["notes.txt"] = pikepdf.AttachedFileSpec(pdf, b"attached text\n" * 100,
                                                                    mime_type="text/plain")
            pdf.attachments["data.pdf"] = pikepdf.AttachedFileSpec(pdf, cp.path("rl_blank").read_bytes()
                                                                   if cp.path("rl_blank").exists() else b"%PDF-1.4")
        save("attachments", "two attached files", pre=add_attach)

        def portfolio(pdf):
            add_attach(pdf)
            pdf.Root.Collection = pikepdf.Dictionary(Type=pikepdf.Name.Collection, View=pikepdf.Name.T)
        save("portfolio", "a portfolio (Collection) with attachments", pre=portfolio)

        def xmp(pdf):
            with pdf.open_metadata() as meta:
                meta["dc:title"] = "Titre ünïcødé 標題"
                meta["dc:creator"] = ["A. Author", "B. Author"]
            pdf.docinfo["/Title"] = "Different title in Info"
        save("xmp", "XMP metadata disagreeing with Info", pre=xmp)

        def labels(pdf):
            pdf.Root.PageLabels = pikepdf.Dictionary(Nums=[0, pikepdf.Dictionary(S=pikepdf.Name.r),
                                                           1, pikepdf.Dictionary(S=pikepdf.Name.D, St=5, P="A-")])
        save("pagelabels", "page labels i, A-5...", pre=labels)

        def drop_info(pdf):
            for k in list(pdf.trailer.keys()):
                if k == "/Info":
                    del pdf.trailer[k]
            pdf.Root.Lang = pikepdf.String("")
        save("noinfo", "no Info dictionary, empty /Lang", pre=drop_info)
        # incremental update, appended by hand: a changed page content
        try:
            name = f"{base}__incremental"
            # pikepdf can't append to a file; pypdf's incremental mode can
            from pypdf import PdfWriter

            w = PdfWriter(str(path), incremental=True)
            w.add_metadata({"/Title": "Changed in an incremental update"})
            w.add_blank_page()
            b = io.BytesIO()
            w.write(b)
            cp.write(name, b.getvalue(), src, f"{base}: incremental update adding a page and a title")
        except Exception as exc:
            print(f"  incremental {base}: {exc}", file=sys.stderr)
        # byte damage
        data = path.read_bytes()
        rnd = random.Random(zlib.crc32(base.encode()))  # the same damage every time
        if light:
            cp.write(f"{base}__cut90", data[: int(len(data) * 0.9)], src, f"{base}: last 10% cut off", "hostile")
            continue
        cp.write(f"{base}__cut90", data[: int(len(data) * 0.9)], src, f"{base}: last 10% cut off", "hostile")
        flipped = bytearray(data)
        for _ in range(30):
            i = rnd.randrange(len(flipped))
            flipped[i] = rnd.randrange(256)
        cp.write(f"{base}__bitflips", bytes(flipped), src, f"{base}: 30 random bytes changed", "hostile")
        if b"startxref" in data:
            i = data.rindex(b"startxref")
            cp.write(f"{base}__bad_startxref", data[:i] + b"startxref\n123\n%%EOF\n", src,
                     f"{base}: startxref points to the wrong place", "hostile")
        if b"xref" in data[-4096:] or b"/XRef" in data:
            zeroed = bytearray(data)
            j = data.rfind(b"xref")
            if j > 0:
                zeroed[j:j + 200] = b"\x00" * min(200, len(data) - j)
                cp.write(f"{base}__zeroed_xref", bytes(zeroed), src, f"{base}: xref section zeroed", "hostile")


def gen_badpdfs(cp):
    import badpdfs

    tmp = cp.out / "_bad"
    files = badpdfs.build(tmp)
    for name, p in files.items():
        shutil.copy(p, cp.path(f"bad_{name}"))
        cp.add(f"bad_{name}", "badpdfs", f"robustness test file: {name.replace('_', ' ')}",
               "valid" if name in ("many_pages", "huge_page") else "hostile")
    shutil.rmtree(tmp, ignore_errors=True)


def gen_samples(cp, folder):
    for p in sorted(Path(folder).rglob("*.pdf")):
        name = "sf_" + p.parent.name.split("-", 1)[0] + "_" + p.stem
        name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:80]
        shutil.copy(p, cp.path(name))
        kind = "hostile" if "password" in p.name or "unreadable" in p.name else "valid"
        cp.add(name, "py-pdf/sample-files", f"real-world sample {p.parent.name}/{p.name}", kind)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out")
    ap.add_argument("--samples", help="folder of real-world sample PDFs to include")
    args = ap.parse_args(argv)
    cp = Corpus(args.out)
    if args.samples:
        _try("samples", gen_samples, cp, args.samples)
    for label, fn in (("badpdfs", gen_badpdfs), ("reportlab", gen_reportlab), ("raw", gen_raw),
                      ("images", gen_images), ("pillow", gen_pillow)):
        print("making", label, file=sys.stderr)
        _try(label, fn, cp)
    print("making libreoffice", file=sys.stderr)
    _try("libreoffice", gen_libreoffice, cp, cp.out / "_lo")
    shutil.rmtree(cp.out / "_lo", ignore_errors=True)
    print("making mutations", file=sys.stderr)
    gen_mutations(cp, ["rl_std14_Helvetica", "rl_form_all", "rl_links_outline", "lo_docx_tagged", "scan_skewed",
                       "raw_layers"])
    gen_mutations(cp, ["rl_images_text", "rl_cjk", "lo_html", "img_dct_cmyk", "rl_ttf_DejaVu",
                       "raw_annots_every_subtype", "rl_mixed_sizes", "raw_type3_font", "lo_form", "rl_rotate_90"],
                  light=True)
    # a sample of the real-world files mutated too
    gen_mutations(cp, [r["file"][:-4] for r in cp.rows if r["source"] == "py-pdf/sample-files"
                       and r["kind"] == "valid"][:4], light=True)
    cp.save_manifest()
    print(f"{len(cp.rows)} files in {cp.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
