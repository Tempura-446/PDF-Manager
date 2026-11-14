# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""PDF → PowerPoint (slides.py) and PDF → HTML (webpage.py), and what they
share (layout.py)."""

import io
import re
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import pikepdf
import pytest
from PIL import Image, ImageChops
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from pdfmanager import core, gui, layout, slides, webpage
from pdfmanager.cli import main

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
EMU = 12700


def report_pdf(path, tmp_path):
    """Three pages: an A4 page with a coloured heading, a paragraph of four
    lines (one ends with a hyphenated word), a subheading, bold/italic/green
    words, a web link, a list, a ruled table, a picture and a link to page 3;
    an A4 page turned with /Rotate; a small landscape page."""
    Image.new("RGB", (300, 200), (30, 120, 200)).save(tmp_path / "pic.png")
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle("Sample Report")
    c.setFont("Helvetica-Bold", 24)
    c.setFillColorRGB(0.8, 0.1, 0.1)
    c.drawString(72, 770, "Quarterly Report")
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 11)
    for k, line in enumerate(["The first paragraph has several lines of ordinary body",
                              "text that wrap across the column, so that the inter-",
                              "national converter must join them back into one para-",
                              "graph again, without the hyphens."]):
        c.drawString(72, 740 - 14 * k, line)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 666, "Results & <Findings>")
    t = c.beginText(72, 642)
    for font, text, color in [("Helvetica", "Some ", 0), ("Helvetica-Bold", "bold", 0),
                              ("Helvetica", " and ", 0), ("Helvetica-Oblique", "italic", 0),
                              ("Helvetica", " words, and ", 0), ("Helvetica", "green text", 1),
                              ("Helvetica", ".", 0)]:
        t.setFont(font, 11)
        t.setFillColorRGB(0, 0.5 if color else 0, 0)
        t.textOut(text)
    c.drawText(t)
    c.setFont("Helvetica", 11)
    c.setFillColorRGB(0, 0, 1)
    c.drawString(72, 620, "Visit example.com")
    c.linkURL("https://example.com/a?b=1&c=2", (72, 617, 170, 631), relative=0)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(72, 596, "• First point")
    c.drawString(72, 582, "• Second point")
    rows = [["Item", "Qty", "Price"], ["Apples", "3", "1.20"], ["Pears", "5", "2.50"]]
    xs, top = [72, 200, 280, 360], 560
    for r, row in enumerate(rows):
        for k, cell in enumerate(row):
            c.drawString(xs[k] + 4, top - 14 - r * 20, cell)
    for r in range(len(rows) + 1):
        c.line(72, top - r * 20, 360, top - r * 20)
    for x in xs:
        c.line(x, top, x, top - len(rows) * 20)
    c.drawImage(str(tmp_path / "pic.png"), 72, 330, 225, 150)
    c.drawString(72, 300, "Go to the last page")
    c.linkAbsolute("", "last", (72, 297, 200, 312))
    c.showPage()
    c.setPageSize(A4)
    c.setPageRotation(90)  # reportlab turns the media box too: 842 x 595, shown upright
    c.setFont("Courier", 14)
    c.drawString(72, 500, "Rotated page heading")
    c.setFont("Helvetica", 11)
    c.drawString(72, 470, "Text on a page shown turned.")
    c.showPage()
    c.setPageSize((400, 300))
    c.setPageRotation(0)
    c.bookmarkPage("last")
    c.setFont("Helvetica", 11)
    c.drawString(50, 200, "A small last page.")
    c.showPage()
    c.save()
    return path


@pytest.fixture
def report(tmp_path):
    return report_pdf(tmp_path / "report.pdf", tmp_path)


def xml_part(zf, name):
    return ET.fromstring(zf.read(name))


def slide_texts(zf, number):
    """[(paragraph text, [run attributes and font and colour])] of a slide."""
    root = xml_part(zf, f"ppt/slides/slide{number}.xml")
    out = []
    for p in root.iter(A + "p"):
        runs = []
        for r in p.findall(A + "r"):
            props = r.find(A + "rPr")
            color = props.find(f"{A}solidFill/{A}srgbClr")
            runs.append({"text": r.find(A + "t").text, **props.attrib,
                         "font": props.find(A + "latin").get("typeface"),
                         "color": color.get("val"),
                         "hidden": color.find(A + "alpha") is not None,
                         "link": props.find(A + "hlinkClick")})
        if runs:
            out.append(("".join(r["text"] for r in runs), runs))
    return out


def rels(zf, name):
    root = xml_part(zf, name)
    return {r.get("Id"): r for r in root.findall(PR + "Relationship")}


# -- reading pages ---------------------------------------------------------------------

def test_font_names_map_to_common_fonts():
    assert layout.font_look("ABCDEF+Helvetica-BoldOblique") == ("Arial", True, True)
    assert layout.font_look("Times-Roman") == ("Times New Roman", False, False)
    assert layout.font_look("TimesNewRomanPS-BoldItalicMT") == ("Times New Roman", True, True)
    assert layout.font_look("Courier") == ("Courier New", False, False)
    assert layout.font_look("ArialMT") == ("Arial", False, False)
    assert layout.font_look("Arial,Bold") == ("Arial", True, False)
    assert layout.font_look("SegoeUI-Semibold") == ("Segoe UI", True, False)
    assert layout.font_look("MinionPro-It") == ("Minion Pro", False, True)
    assert layout.font_look("CMBX12") == ("Times New Roman", True, False)
    # A name that says nothing: the font's flags decide (fixed pitch, serif).
    assert layout.font_look("F1", flags=1) == ("Courier New", False, False)
    assert layout.font_look("T1_0", flags=2) == ("Times New Roman", False, False)
    assert layout.font_look("F2", weight=700) == ("Arial", True, False)


def test_pages_are_read_into_lines_and_paragraphs(report):
    with layout.Document(report) as doc:
        first, turned, small = (doc.page(i) for i in doc.indices)
    blocks = {b.lines[0].text: b for b in first.blocks}
    para = blocks["The first paragraph has several lines of ordinary body"]
    assert len(para.paragraphs) == 1 and len(para.lines) == 4 and para.align == "l"
    title = blocks["Quarterly Report"].lines[0]
    assert (title.family, title.chars[0].bold, title.chars[0].color) == ("Arial", True, (204, 26, 26))
    assert round(title.size) == 24 and abs(title.x0 - 72) < 0.5 and abs(title.base - (842 - 770)) < 1
    assert [(t, c.bold, c.italic) for t, c in blocks["Some bold and italic words, and green text."]
            .lines[0].runs()][:4] == [("Some ", False, False), ("bold ", True, False),
                                      ("and ", False, False), ("italic ", False, True)]
    assert {link.get("uri") or link.get("page") for link in first.links} == \
        {"https://example.com/a?b=1&c=2", 3}
    # The turned page: shown 595 wide and 842 high, its text running downwards.
    assert (round(turned.width), round(turned.height)) == (595, 842)
    assert {ln.angle for ln in turned.lines} == {90}
    assert (round(small.width), round(small.height)) == (400, 300)


def test_text_is_taken_out_of_the_background(report):
    with layout.Document(report, pages="1") as doc:
        page = doc.page(0)
        full = doc.render(0, 72)
        bare = doc.render(0, 72, without_text=True)
    diff = ImageChops.difference(full, bare).convert("L").point(lambda v: 255 if v > 40 else 0)
    # Where text was: changed. Everywhere else (lines, the picture): the same.
    title = [b for b in page.blocks if b.lines[0].text == "Quarterly Report"][0]
    box = tuple(int(v) for v in (title.x0, title.top, title.x1, title.bottom))
    assert diff.crop(box).getbbox() is not None
    text_mask = Image.new("L", full.size, 0)
    for line in page.lines:
        text_mask.paste(255, tuple(int(v) for v in layout.box_on_page(
            line.angle, line.x0 - 2, line.top - 2, line.x1 + 2, line.bottom + 2)))
    outside = ImageChops.subtract(diff, text_mask)
    assert outside.getbbox() is None
    picture = bare.crop((72, int(842 - 480), 72 + 225, int(842 - 330))).convert("RGB")
    assert picture.getpixel((100, 70)) == (30, 120, 200)


# -- PowerPoint -------------------------------------------------------------------------

def test_pptx_package_is_valid_office_xml(report, tmp_path):
    made = slides.pdf_to_pptx(report, tmp_path / "out.pptx")
    assert made["slides"] == 3 and made["text_boxes"] > 10
    with zipfile.ZipFile(made["output"]) as zf:
        names = set(zf.namelist())
        for name in names:
            if name.endswith((".xml", ".rels")):
                xml_part(zf, name)  # well-formed
        types = xml_part(zf, "[Content_Types].xml")
        overrides = {o.get("PartName") for o in types.findall(CT + "Override")}
        defaults = {d.get("Extension") for d in types.findall(CT + "Default")}
        for name in names:
            if name.endswith(".xml") and name != "[Content_Types].xml":
                assert "/" + name in overrides, name
            elif not name.endswith(".rels"):
                assert name.rsplit(".", 1)[1] in defaults, name
        # every internal relationship points at a part that is there
        for name in names:
            if name.endswith(".rels"):
                base = name.replace("_rels/", "").rsplit(".rels", 1)[0]
                folder = base.rsplit("/", 1)[0] + "/" if "/" in base else ""
                for rel in rels(zf, name).values():
                    if rel.get("TargetMode") == "External":
                        continue
                    target = (Path(folder) / rel.get("Target")).as_posix()
                    target = re.sub(r"[^/]+/\.\./", "", target)
                    assert target in names, (name, target)
        pres = xml_part(zf, "ppt/presentation.xml")
        assert len(pres.findall(f"{P}sldIdLst/{P}sldId")) == 3
        size = pres.find(P + "sldSz")
        # Two of the three pages are A4: the slides are A4.
        assert abs(int(size.get("cx")) - 595.28 * EMU) < EMU and \
            abs(int(size.get("cy")) - 841.89 * EMU) < EMU
        core_xml = zf.read("docProps/core.xml").decode()
        assert "<dc:title>Sample Report</dc:title>" in core_xml


def test_pptx_text_boxes_keep_the_look(report, tmp_path):
    out = slides.pdf_to_pptx(report, tmp_path / "out.pptx")["output"]
    with zipfile.ZipFile(out) as zf:
        texts = dict(slide_texts(zf, 1))
        title = texts["Quarterly Report"][0]
        assert (title["sz"], title["b"], title["font"], title["color"]) == ("2400", "1", "Arial", "CC1A1A")
        # The paragraph's lines are one paragraph again (its text rewraps when edited).
        assert ("The first paragraph has several lines of ordinary body text that wrap across "
                "the column, so that the inter-national converter must join them back into one "
                "para-graph again, without the hyphens.") in texts
        runs = texts["Some bold and italic words, and green text."]
        assert [(r["text"], r["b"], r["i"], r["color"]) for r in runs] == [
            ("Some ", "0", "0", "000000"), ("bold ", "1", "0", "000000"), ("and ", "0", "0", "000000"),
            ("italic ", "0", "1", "000000"), ("words, and ", "0", "0", "000000"),
            ("green text", "0", "0", "008000"), (".", "0", "0", "000000")]
        assert all(r["font"] == "Arial" and r["sz"] == "1100" for r in runs)
        # Links: a web address, and a jump to the slide of page 3.
        slide_rels = rels(zf, "ppt/slides/_rels/slide1.xml.rels")
        web = texts["Visit example.com"][0]["link"]
        assert slide_rels[web.get(R + "id")].get("Target") == "https://example.com/a?b=1&c=2"
        assert slide_rels[web.get(R + "id")].get("TargetMode") == "External"
        jump = texts["Go to the last page"][0]["link"]
        assert jump.get("action") == "ppaction://hlinksldjump"
        assert slide_rels[jump.get(R + "id")].get("Target") == "slide3.xml"
        # Text boxes sit where the text was: the title's box starts at x = 72 pt.
        root = xml_part(zf, "ppt/slides/slide1.xml")
        boxes = {"".join(t.text for t in sp.iter(A + "t")): sp for sp in root.iter(P + "sp")}
        off = boxes["Quarterly Report"].find(f"{P}spPr/{A}xfrm/{A}off")
        assert abs(int(off.get("x")) - 72 * EMU) < 0.5 * EMU
        assert 40 * EMU < int(off.get("y")) < 72 * EMU  # its baseline is 72 pt from the top
        # The page without its text is the slide's background.
        background = root.find(f"{P}cSld/{P}bg/{P}bgPr/{A}blipFill/{A}blip")
        image = slide_rels[background.get(R + "embed")].get("Target")
        assert image.startswith("../media/page1.")


def test_pptx_turned_page_and_other_sizes(report, tmp_path):
    out = slides.pdf_to_pptx(report, tmp_path / "out.pptx")["output"]
    with zipfile.ZipFile(out) as zf:
        root = xml_part(zf, "ppt/slides/slide2.xml")
        boxes = {"".join(t.text for t in sp.iter(A + "t")): sp for sp in root.iter(P + "sp")}
        assert boxes["Rotated page heading"].find(f"{P}spPr/{A}xfrm").get("rot") == "5400000"
        assert dict(slide_texts(zf, 2))["Rotated page heading"][0]["font"] == "Courier New"
        # The 400 x 300 page is scaled to the A4 slide's width, centred, as a picture.
        root = xml_part(zf, "ppt/slides/slide3.xml")
        pic = root.find(f"{P}cSld/{P}spTree/{P}pic")
        off, ext = pic.find(f"{P}spPr/{A}xfrm/{A}off"), pic.find(f"{P}spPr/{A}xfrm/{A}ext")
        scale = 595.28 / 400
        assert abs(int(ext.get("cx")) - 400 * scale * EMU) < EMU
        assert abs(int(off.get("y")) - (841.89 - 300 * scale) / 2 * EMU) < EMU
        assert dict(slide_texts(zf, 3))["A small last page."][0]["sz"] == str(round(11 * scale * 100))


def test_pptx_pictures_of_the_pages(report, tmp_path):
    made = slides.pdf_to_pptx(report, tmp_path / "pics.pptx", pages="3,1", mode="pictures")
    assert made["slides"] == 2 and made["text_boxes"] == 0
    with zipfile.ZipFile(made["output"]) as zf:
        for n in (1, 2):
            root = xml_part(zf, f"ppt/slides/slide{n}.xml")
            assert not list(root.iter(P + "sp"))
            assert len(list(root.iter(P + "pic"))) == 1
        # pages in the order asked: the small page first
        image = Image.open(io.BytesIO(zf.read(next(n for n in zf.namelist()
                                                   if n.startswith("ppt/media/page1.")))))
        assert image.width > image.height


def test_pptx_scanned_text_stays_invisible(tmp_path):
    path = tmp_path / "scan.pdf"
    Image.new("RGB", (600, 300), (240, 240, 230)).save(tmp_path / "scan.png")
    c = canvas.Canvas(str(path), pagesize=(300, 150))
    c.drawImage(str(tmp_path / "scan.png"), 0, 0, 300, 150)
    t = c.beginText(20, 100)
    t.setTextRenderMode(3)
    t.setFont("Helvetica", 12)
    t.textOut("Recognised words")
    c.drawText(t)
    c.save()
    out = slides.pdf_to_pptx(path, tmp_path / "scan.pptx")["output"]
    with zipfile.ZipFile(out) as zf:
        runs = dict(slide_texts(zf, 1))["Recognised words"]
        assert runs[0]["hidden"]
    html = webpage.pdf_to_html(path, tmp_path / "scan.html", mode="exact")["output"].read_text(encoding="utf-8")
    assert "color:transparent" in html and "Recognised words" in html
    # Flowing: the text is the content; the scan isn't repeated as a picture.
    html = webpage.pdf_to_html(path, tmp_path / "scan2.html")["output"].read_text(encoding="utf-8")
    assert "<p>Recognised words</p>" in html and "<img" not in html


@pytest.mark.skipif(not shutil.which("soffice"), reason="LibreOffice isn't installed")
def test_pptx_opens_in_libreoffice(report, tmp_path):
    out = slides.pdf_to_pptx(report, tmp_path / "deck.pptx")["output"]
    profile = (tmp_path / "profile").as_uri()
    subprocess.run(["soffice", f"-env:UserInstallation={profile}", "--headless", "--convert-to",
                    "pdf", "--outdir", str(tmp_path / "back"), str(out)],
                   check=True, capture_output=True, timeout=240)
    back = tmp_path / "back" / "deck.pdf"
    with layout.Document(back) as doc:
        pages = [doc.page(i) for i in doc.indices]
    with layout.Document(report) as doc:
        original = doc.page(0)
    assert len(pages) == 3
    lines = {ln.text: ln for ln in pages[0].lines}
    for line in original.lines:
        twin = lines[line.text]  # the same text, in the same place, the same size
        assert abs(twin.x0 - line.x0) < 2 and abs(twin.base - line.base) < 2, line.text
        assert abs(twin.size - line.size) < 0.2
    assert "Rotated page heading" in [ln.text for ln in pages[1].lines]
    assert [ln.text for ln in pages[2].lines] == ["A small last page."]


# -- HTML -------------------------------------------------------------------------------

class Collect(HTMLParser):
    """Tags (with attributes) and the text in each element, as parsed."""

    def __init__(self):
        super().__init__()
        self.tags, self.stack, self.texts = [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag not in ("img", "meta", "br", "hr"):
            self.stack.append([tag, ""])

    def handle_endtag(self, tag):
        while self.stack:
            name, text = self.stack.pop()
            self.texts.append((name, text))
            if self.stack:
                self.stack[-1][1] += text
            if name == tag:
                break

    def handle_data(self, data):
        if self.stack:
            self.stack[-1][1] += data

    def all(self, tag):
        return [" ".join(t.split()) for name, t in self.texts if name == tag]


def parse(path):
    parser = Collect()
    parser.feed(Path(path).read_text(encoding="utf-8"))
    parser.close()
    return parser


def test_html_flowing(report, tmp_path):
    out = webpage.pdf_to_html(report, tmp_path / "report.html")["output"]
    source = out.read_text(encoding="utf-8")
    doc = parse(out)
    assert source.startswith("<!DOCTYPE html>") and '<html lang="en">' in source
    assert doc.all("title") == ["Sample Report"]
    assert "<script" not in source.lower() and "javascript:" not in source.lower()
    assert doc.all("h1") == ["Quarterly Report"]
    assert "Results & <Findings>" in doc.all("h2")
    assert "Results &amp; &lt;Findings&gt;" in source  # escaped
    assert ("The first paragraph has several lines of ordinary body text that wrap across the "
            "column, so that the international converter must join them back into one "
            "paragraph again, without the hyphens.") in doc.all("p")
    assert "Some <strong>bold</strong> and <em>italic</em> words, and " \
           '<span style="color:#008000">green text</span>.' in source
    assert doc.all("li") == ["First point", "Second point"]
    assert doc.all("th") == ["Item", "Qty", "Price"] and doc.all("td")[:3] == ["Apples", "3", "1.20"]
    images = [a for tag, a in doc.tags if tag == "img"]
    assert len(images) == 1 and images[0]["src"].startswith("data:image/")
    picture = Image.open(io.BytesIO(__import__("base64").b64decode(images[0]["src"].split(",")[1])))
    assert picture.convert("RGB").getpixel((picture.width // 2, picture.height // 2)) == (30, 120, 200)
    links = {a["href"] for tag, a in doc.tags if tag == "a"}
    assert links == {"https://example.com/a?b=1&c=2", "#page-3"}
    ids = [a["id"] for tag, a in doc.tags if a.get("id")]
    assert ids == ["page-1", "page-2", "page-3"]
    # reading order: the table after the list, the picture after the table
    order = [tag for tag, _ in doc.tags if tag in ("h1", "h2", "ul", "table", "img")]
    assert order == ["h1", "h2", "ul", "table", "img", "h2"]
    # the turned page's text, in reading order
    assert source.index("Rotated page heading") < source.index("Text on a page shown turned.")


def two_column_pdf(path, pages=3):
    """Pages with a running header and footer, a title across the page and
    two columns of text; rows of "label: value" at the bottom."""
    c = canvas.Canvas(str(path), pagesize=A4)
    for n in range(pages):
        c.setFont("Helvetica", 9)
        c.drawString(72, 815, "ACME Annual Report")
        c.drawString(290, 25, f"Page {n + 1}")
        c.setFont("Helvetica-Bold", 20)
        c.drawString(72, 760, f"Chapter {n + 1}")
        c.setFont("Helvetica", 10)
        for col, x in enumerate((72, 320)):  # columns 200 pt wide, 48 pt apart
            for k in range(8):
                c.drawString(x, 720 - 13 * k, f"Column {col + 1} line {k + 1} has a longer text in it.")
        for k, (label, value) in enumerate([("Name:", "Jane Doe"), ("Phone:", "555 0100")]):
            c.drawString(72, 560 - 15 * k, label)
            c.drawString(200, 560 - 15 * k, value)
        c.showPage()
    c.save()
    return path


def test_html_columns_headers_and_forms(tmp_path):
    out = webpage.pdf_to_html(two_column_pdf(tmp_path / "cols.pdf"), tmp_path / "cols.html")
    doc = parse(out["output"])
    paragraphs = doc.all("p")
    assert paragraphs[:2] == [
        " ".join(f"Column 1 line {k} has a longer text in it." for k in range(1, 9)),
        " ".join(f"Column 2 line {k} has a longer text in it." for k in range(1, 9))]
    assert paragraphs[2:4] == ["Name: Jane Doe", "Phone: 555 0100"]
    assert doc.all("h1") == ["Chapter 1", "Chapter 2", "Chapter 3"]
    text = out["output"].read_text(encoding="utf-8")
    # the running header and the page numbers in the footer are left out
    assert "ACME Annual Report" not in text
    assert not re.search(r"<p>Page \d</p>", text)


def test_html_exact(report, tmp_path):
    out = webpage.pdf_to_html(report, tmp_path / "exact.html", mode="exact")["output"]
    source = out.read_text(encoding="utf-8")
    doc = parse(out)
    assert "<script" not in source.lower()
    sections = [a for tag, a in doc.tags if tag == "section"]
    assert [s["id"] for s in sections] == ["page-1", "page-2", "page-3"]
    assert "aspect-ratio:400/300" in sections[2]["style"]
    images = [a for tag, a in doc.tags if tag == "img"]
    assert len(images) == 3 and all(i["src"].startswith("data:image/") for i in images)
    spans = {}
    # Each line is a span placed in percent of the page, its size relative to the page width.
    for m in re.finditer(r'<span class="f\d+" style="left:([\d.]+)%;top:([\d.]+)%;font-size:'
                         r'([\d.]+)cqw[^"]*">(.*?)</span>\n', source):
        spans[re.sub(r"<[^>]+>", "", m.group(4))] = tuple(float(v) for v in m.groups()[:3])
    left, top, size = spans["Quarterly Report"]
    assert abs(left - 72 / 595.28 * 100) < 0.1
    assert abs(top - (72 - 0.85 * 24) / 841.89 * 100) < 0.2
    assert abs(size - 24 / 595.28 * 100) < 0.01
    assert "Results &amp; &lt;Findings&gt;" in spans
    assert "transform:rotate(90deg)" in source
    assert '<a href="https://example.com/a?b=1&amp;c=2">' in source
    assert '<a href="#page-3">' in source


def test_html_language_pages_and_bad_links(report, tmp_path):
    with pikepdf.open(report, allow_overwriting_input=True) as pdf:
        pdf.Root.Lang = pikepdf.String("de-DE")
        page = pdf.pages[0]
        page.obj.Annots.append(pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link, Rect=[72, 740, 300, 752],
            A=pikepdf.Dictionary(S=pikepdf.Name.URI, URI=pikepdf.String("javascript:alert(1)")))))
        pdf.save(report)
    out = webpage.pdf_to_html(report, tmp_path / "p.html", pages="3,1")["output"]
    source = out.read_text(encoding="utf-8")
    assert '<html lang="de-DE">' in source and "javascript" not in source
    assert [a["id"] for tag, a in parse(out).tags if a.get("id")] == ["page-3", "page-1"]
    # a link to a page that isn't in the file is dropped
    out = webpage.pdf_to_html(report, tmp_path / "q.html", pages="1-2")["output"]
    assert "#page-3" not in out.read_text(encoding="utf-8")


WORDS = ("Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor "
         "incididunt ut labore et dolore magna aliqua ut enim ad minim veniam quis nostrud "
         "exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat").split()


def article_pdf(path):
    """Two justified columns (the second paragraph goes on in the second
    column), a bulleted list with a hanging indent, and a small ruled table
    whose cells are close together."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (BaseDocTemplate, Frame, ListFlowable, ListItem,
                                    PageTemplate, Paragraph, Table, TableStyle)

    body = ParagraphStyle("body", fontName="Times-Roman", fontSize=10, leading=12,
                          alignment=TA_JUSTIFY, spaceAfter=6)
    words = WORDS
    doc = BaseDocTemplate(str(path), pagesize=A4)
    width = (A4[0] - 120) / 2
    doc.addPageTemplates([PageTemplate(frames=[
        Frame(50, 400, width, 380, id="left"), Frame(70 + width, 400, width, 380, id="right")])])
    table = Table([["Name", "Value"], ["Alpha", "1"]])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                               ("FONT", (0, 0), (-1, -1), "Helvetica", 10)]))
    story = [Paragraph(" ".join(words * 3) + ".", body),
             Paragraph(" ".join(words * 5) + " the end.", body),
             ListFlowable([ListItem(Paragraph(" ".join(words[:14]) + ".", body)),
                           ListItem(Paragraph("Second item.", body))], bulletType="bullet",
                          bulletFontSize=10), table]
    doc.build(story)
    return path


def test_tables_lists_and_columns(tmp_path):
    src = article_pdf(tmp_path / "article.pdf")
    out = slides.pdf_to_pptx(src, tmp_path / "a.pptx")["output"]
    with zipfile.ZipFile(out) as zf:
        texts = dict(slide_texts(zf, 1))
        root = xml_part(zf, "ppt/slides/slide1.xml")
    # The table's cells are text boxes of their own, though close together.
    assert {"Name", "Value", "Alpha", "1"} <= set(texts)
    # A list item: the bullet, a tab, the text, with a hanging indent.
    item = next(p for p in root.iter(A + "p")
                if "".join(t.text for t in p.iter(A + "t")).startswith("•\tLorem"))
    props = item.find(A + "pPr")
    assert int(props.get("indent")) < 0 < int(props.get("marL"))
    # Justified paragraphs stay justified.
    aligns = {p.find(A + "pPr").get("algn") for p in root.iter(A + "p")
              if len("".join(t.text for t in p.iter(A + "t"))) > 200}
    assert aligns == {"just"}

    html = webpage.pdf_to_html(src, tmp_path / "a.html")["output"]
    doc = parse(html)
    paragraphs = doc.all("p")
    # The paragraph that goes on in the second column is one paragraph.
    assert paragraphs[1].endswith("commodo consequat the end.") and len(paragraphs) == 2
    assert doc.all("li") == [" ".join(WORDS[:14]) + ".", "Second item."]
    assert doc.all("th") == ["Name", "Value"]

    exact = webpage.pdf_to_html(src, tmp_path / "x.html", mode="exact")["output"].read_text(encoding="utf-8")
    # bullet and text placed apart; justified lines get their spaces' width
    assert re.search(r'>•</span> </span>\n<span class="f\d+" style="left:[\d.]+%;top:[\d.]+%;'
                     r"font-size:[\d.]+cqw[^\"]*\">Lorem", exact)
    assert "word-spacing:" in exact


# -- passwords, errors, the command line and the app -------------------------------------

def test_encrypted_input(report, tmp_path):
    locked = tmp_path / "locked.pdf"
    core.encrypt(report, locked, "secret")
    with pytest.raises(core.PdfManagerError, match="encrypted"):
        slides.pdf_to_pptx(locked, tmp_path / "x.pptx")
    with pytest.raises(core.PdfManagerError, match="wrong password"):
        webpage.pdf_to_html(locked, tmp_path / "x.html", password="nope")
    made = slides.pdf_to_pptx(locked, tmp_path / "x.pptx", password="secret")
    with zipfile.ZipFile(made["output"]) as zf:
        assert "Quarterly Report" in dict(slide_texts(zf, 1))
        assert zf.read("ppt/slides/slide1.xml").count(b"<p:bg>") == 1  # its text could be taken out
    html = webpage.pdf_to_html(locked, tmp_path / "x.html", password="secret")["output"]
    assert "Quarterly Report" in html.read_text(encoding="utf-8")


def test_bad_options(report, tmp_path):
    with pytest.raises(core.PdfManagerError, match="mode must be one of editable, pictures"):
        slides.pdf_to_pptx(report, tmp_path / "x.pptx", mode="slides")
    with pytest.raises(core.PdfManagerError, match="mode must be one of flowing, exact"):
        webpage.pdf_to_html(report, tmp_path / "x.html", mode="fancy")
    with pytest.raises(ValueError, match="out of range"):
        slides.pdf_to_pptx(report, tmp_path / "x.pptx", pages="7")
    assert not (tmp_path / "x.pptx").exists() and not (tmp_path / "x.pptx.part").exists()


def test_cli(report, tmp_path, capsys):
    assert main(["to-pptx", str(report)]) == 0
    assert "(3 slides, editable)" in capsys.readouterr().out
    assert zipfile.ZipFile(tmp_path / "report.pptx").testzip() is None
    assert main(["to-pptx", str(report), "--pictures", "-p", "2", "-o",
                 str(tmp_path / "one.pptx")]) == 0
    assert "(1 slide, pictures)" in capsys.readouterr().out
    assert main(["to-html", str(report), "--exact", "-p", "1"]) == 0
    assert "(1 page, exact)" in capsys.readouterr().out
    assert 'id="page-1"' in (tmp_path / "report.html").read_text(encoding="utf-8")
    other = report_pdf(tmp_path / "other.pdf", tmp_path)
    assert main(["to-html", str(report), str(other), "-o", str(tmp_path / "web")]) == 0
    assert sorted(p.name for p in (tmp_path / "web").iterdir()) == ["other.html", "report.html"]


def test_tools_are_in_the_app(report, tmp_path):
    ids = [op["id"] for op in gui.OPERATIONS]
    assert ids[ids.index("to-excel") + 1:ids.index("to-excel") + 3] == ["to-pptx", "to-html"]
    for op_id, label, modes in (("to-pptx", "PDF → PowerPoint", slides.MODES),
                                ("to-html", "PDF → HTML", webpage.MODES)):
        op = gui.OPS[op_id]
        assert op["group"] == "Convert" and op["label"] == label
        fields = {f["key"]: f for f in op["fields"]}
        assert set(fields) == {"mode", "pages"}
        assert [c[0] for c in fields["mode"]["choices"]] == list(modes)
    html = Path(gui.__file__).with_name("gui.html").read_text(encoding="utf-8")
    for op_id in ("to-pptx", "to-html"):
        assert f"\"{op_id}\": '<" in html and f'"{op_id}": "' in html  # an icon and a tagline

    work = tmp_path / "w1"
    work.mkdir()
    notes = []
    name, data, ctype = gui.run_operation("to-pptx", [report], {"mode": "pictures", "pages": "1-2"},
                                          work=work, notes=notes)
    assert name == "report.pptx" and ctype.endswith("presentationml.presentation")
    assert notes == ["report.pdf: 2 slides"]
    assert zipfile.ZipFile(io.BytesIO(data)).read("ppt/slides/slide2.xml")
    work = tmp_path / "w2"
    work.mkdir()
    locked = tmp_path / "locked.pdf"
    core.encrypt(report, locked, "pw")
    name, data, ctype = gui.run_operation("to-html", [locked], {"mode": "exact"}, password="pw",
                                          work=work, notes=[])
    assert name == "locked.html" and ctype == "text/html; charset=utf-8"
    assert b"Quarterly Report" in data
    work = tmp_path / "w3"
    work.mkdir()
    with pytest.raises(gui.UserError, match="unknown layout"):
        gui.run_operation("to-html", [report], {"mode": "odd"}, work=work, notes=[])
