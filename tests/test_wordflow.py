"""PDF → Word (built-in) reads pages the way the other converters do:
columns in order, headings, real tables, and the look of the words."""
import re
import sys
import zipfile
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).parent))
import ocrpages  # noqa: E402

from pdfmanager import core, ocr, office  # noqa: E402

LEFT = ["The committee met on Tuesday to review the", "quarterly accounts and the draft budget.",
        "Members agreed that the reserve should rise."]
RIGHT = ["The proposal to replace the boiler was", "deferred until a second quotation arrives.",
         "Correspondence from the council was noted."]


def read(path):
    """([(style, text)] of the paragraphs outside tables, [tables as rows of cell texts], the xml)."""
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    tables = []
    for table in re.findall(r"<w:tbl>.*?</w:tbl>", xml, re.S):
        tables.append([["".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", cell))
                        for cell in re.findall(r"<w:tc>.*?</w:tc>", row, re.S)]
                       for row in re.findall(r"<w:tr>.*?</w:tr>", table, re.S)])
    outside = re.sub(r"<w:tbl>.*?</w:tbl>", "", xml, flags=re.S)
    paragraphs = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", outside, re.S):
        style = re.search(r'<w:pStyle w:val="([^"]+)"', p)
        text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p))
        if text.strip():
            paragraphs.append((style.group(1) if style else "", text))
    return paragraphs, tables, xml


def report(path):
    """A heading, two columns of text, a sub-heading and a ruled table."""
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(72, 770, "Quarterly report")
    c.setFont("Helvetica", 10)
    for x, lines in ((72, LEFT), (320, RIGHT)):
        for i, line in enumerate(lines):
            c.drawString(x, 730 - 14 * i, line)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, 640, "Sales by item")
    rows = [("Item", "Qty", "Amount"), ("Copper tubing", "14", "52.50"), ("Brass fittings", "6", "74.40")]
    c.setFont("Helvetica", 10)
    for r, row in enumerate(rows):
        for k, cell in enumerate(row):
            c.drawString(78 + 150 * k, 606 - 20 * r, cell)
    c.grid([72, 222, 372, 522], [620, 600, 580, 560])
    c.save()
    return Path(path)


def test_columns_are_read_one_after_the_other(tmp_path):
    out = office.basic_docx(report(tmp_path / "a.pdf"), tmp_path / "a.docx")
    text = " ".join(t for _, t in read(out)[0])
    left, right = text.find("quarterly accounts"), text.find("replace the boiler")
    assert 0 <= text.find("committee met") < left < text.find("reserve should rise") < right
    assert "review the The proposal" not in text, "the columns were read straight across"


def test_headings_and_a_real_table(tmp_path):
    paragraphs, tables, _ = read(office.basic_docx(report(tmp_path / "a.pdf"), tmp_path / "a.docx"))
    assert ("Heading1", "Quarterly report") in paragraphs
    assert any(style.startswith("Heading") and text == "Sales by item" for style, text in paragraphs)
    assert tables == [[["Item", "Qty", "Amount"], ["Copper tubing", "14", "52.50"],
                       ["Brass fittings", "6", "74.40"]]]
    assert not any("Copper tubing" in text for _, text in paragraphs), "the table isn't repeated as text"


def test_bold_italic_and_links_keep_their_look(tmp_path):
    c = canvas.Canvas(str(tmp_path / "a.pdf"), pagesize=A4)
    c.setFont("Helvetica", 11)
    c.drawString(72, 700, "Payment is due")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(150, 700, "within 30 days")
    c.setFont("Helvetica-Oblique", 11)
    c.drawString(232, 700, "of the invoice date, see")
    c.setFont("Helvetica", 11)
    c.drawString(350, 700, "our terms")
    c.linkURL("https://example.com/terms", (350, 696, 400, 712))
    c.save()
    out = office.basic_docx(tmp_path / "a.pdf", tmp_path / "a.docx")
    _, _, xml = read(out)
    bold = re.search(r"<w:r><w:rPr>[^/]*<w:b/>.*?</w:rPr><w:t[^>]*>([^<]*)", xml)
    assert bold and "within 30 days" in bold.group(1)
    assert re.search(r"<w:i/>.*?of the invoice date", xml)
    rels = zipfile.ZipFile(out).read("word/_rels/document.xml.rels").decode("utf-8")
    assert 'Target="https://example.com/terms" TargetMode="External"' in rels
    assert re.search(r"<w:hyperlink r:id=\"[^\"]+\">.*?our terms", xml)


def test_a_list_keeps_its_items(tmp_path):
    c = canvas.Canvas(str(tmp_path / "a.pdf"), pagesize=A4)
    c.setFont("Helvetica", 11)
    for i, line in enumerate(["Things to bring:", "1. The signed form", "2. Proof of address", "3. A photograph"]):
        c.drawString(72, 700 - 18 * i, line)
    c.save()
    paragraphs, _, _ = read(office.basic_docx(tmp_path / "a.pdf", tmp_path / "a.docx"))
    assert [t for _, t in paragraphs] == ["Things to bring:", "1. The signed form", "2. Proof of address",
                                         "3. A photograph"]
    assert [s for s, _ in paragraphs][1:] == ["ListParagraph"] * 3


needs_ocr = pytest.mark.skipif(not ocr.builtin_available(), reason="needs the built-in OCR engine")


@needs_ocr
def test_a_scan_comes_out_as_its_words_without_stray_letters(tmp_path):
    src = ocrpages.scan_pdf(ocrpages.two_columns()[0], tmp_path / "scan.pdf")
    searchable = core.ocr(src, tmp_path / "ocr.pdf", engine="builtin")
    paragraphs, _, _ = read(office.basic_docx(searchable, tmp_path / "a.docx"))
    text = " ".join(t for _, t in paragraphs)
    assert "The committee met on Tuesday to review the quarterly accounts" in text
    assert not re.search(r"\b(\w+)(\w) \2\b", text.replace("a a", "")), text[:300]
    assert text.find("quarterly accounts") < text.find("until a second quotation")


@needs_ocr
def test_a_scanned_table_is_a_table_in_word(tmp_path):
    src = ocrpages.scan_pdf(ocrpages.ruled_table()[0], tmp_path / "scan.pdf")
    searchable = core.ocr(src, tmp_path / "ocr.pdf", engine="builtin")
    _, tables, _ = read(office.basic_docx(searchable, tmp_path / "a.docx"))
    assert len(tables) == 1 and tables[0][0] == ["Item", "Qty", "Unit price", "Amount"]
    assert ["Copper tubing", "14", "3.75", "52.50"] in tables[0]


@needs_ocr
def test_ordinary_lines_of_a_scan_are_not_taken_for_headings(tmp_path):
    """Recognised text varies a little in size from line to line; that isn't a heading."""
    from PIL import Image, ImageDraw

    page = Image.new("L", ocrpages.A4_300, 255)
    draw = ImageDraw.Draw(page)
    draw.text((250, 220), "Quarterly report", font=ocrpages._font(90), fill=0)
    words = (ocrpages.TEXT.replace("\n", " ") + " ") * 2
    y = 460
    for line in ocrpages._wrap(words.split(), ocrpages._font(38), 1900)[:16]:
        draw.text((250, y), line, font=ocrpages._font(38), fill=0)
        y += 60
    searchable = core.ocr(ocrpages.scan_pdf(page, tmp_path / "scan.pdf"), tmp_path / "ocr.pdf", engine="builtin")
    paragraphs, _, _ = read(office.basic_docx(searchable, tmp_path / "a.docx"))
    headings = [text for style, text in paragraphs if style.startswith("Heading")]
    assert headings == ["Quarterly report"], headings
