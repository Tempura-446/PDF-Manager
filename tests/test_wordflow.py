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


# -- found on real documents (Windows check of 0.18.0) -----------------------------------

GRID = [("Work offline", ["Downloading the Desktop app unlocks the tools", "offline. Documents are kept locally on your",
                          "computer and never leave it."]),
        ("No document limits", ["Subscribing to the Premium package means no", "limits on the amount of documents you can",
                                "process in one task."]),
        ("Faster processing", ["Files are processed on your own machine, so", "large documents finish sooner than they",
                               "would over a slow connection."]),
        ("Create multiple tabs", ["Open several documents side by side and", "switch between them with a click, as in",
                                  "a web browser."])]


def brochure(path, footer=True):
    """A designed page: a title, then a 2 x 2 grid of features, each a bold
    heading over a short paragraph, the two columns level with each other."""
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 24)
    c.drawString(60, 760, "Features to boost your productivity")
    for n, (head, lines) in enumerate(GRID):
        x, y = (60, 320)[n % 2], 690 - 150 * (n // 2)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(x, y, head)
        c.setFont("Helvetica", 10)
        for i, line in enumerate(lines):
            c.drawString(x, y - 22 - 14 * i, line)
    if footer:
        c.setFont("Helvetica", 9)
        c.drawString(292, 30, "2")
    c.save()
    return Path(path)


def test_a_grid_of_features_is_read_one_feature_at_a_time(tmp_path):
    paragraphs, tables, _ = read(office.basic_docx(brochure(tmp_path / "a.pdf"), tmp_path / "a.docx"))
    assert not tables
    texts = [t for _, t in paragraphs]
    joined = " | ".join(texts)
    for head, lines in GRID:
        assert head in texts, f"{head!r} isn't a paragraph of its own: {joined[:300]}"
        body = next(t for t in texts if t.startswith(lines[0][:20]))
        assert body.endswith(lines[-1][-12:]), f"{head}: its text runs into another feature: {body}"
    assert "Work offline No document limits" not in joined


def test_feature_headings_are_headings_and_the_page_number_isnt(tmp_path):
    paragraphs, _, _ = read(office.basic_docx(brochure(tmp_path / "a.pdf"), tmp_path / "a.docx"))
    styles = dict((t, s) for s, t in paragraphs)
    assert styles["Features to boost your productivity"] == "Heading1"
    assert all(styles[head].startswith("Heading") for head, _ in GRID)
    assert "2" not in styles, "the page number is left out"
    assert not any(s.startswith("Heading") for t, s in styles.items() if t[:1].islower() or len(t.split()) > 9)


def test_a_bold_sentence_is_not_a_heading(tmp_path):
    c = canvas.Canvas(str(tmp_path / "a.pdf"), pagesize=A4)
    c.setFont("Helvetica", 10)
    c.drawString(60, 760, "The app has four tabs, described below in the order they appear on screen.")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(60, 730, "The Recents tab will show you the documents you opened and edited most recently")
    c.drawString(60, 700, "Easily manage your documents and keep them in the folders you already use")
    c.save()
    paragraphs, _, _ = read(office.basic_docx(tmp_path / "a.pdf", tmp_path / "a.docx"))
    assert [s for s, _ in paragraphs] == ["", "", ""], paragraphs


def test_two_columns_of_sentences_are_not_a_table():
    from pdfmanager import webpage

    prose = {"kind": "aligned", "rows": [
        ["Merge several documents into one file and", "Reduce the size of a document while keeping"],
        ["put their pages in the order you want.", "its pictures clear enough to read on screen."],
        ["Split one document into several.", "Repair a document that won't open."]]}
    figures = {"kind": "aligned", "rows": [["Item", "Qty", "Amount"], ["Copper tubing", "14", "52.50"],
                                           ["Brass fittings", "6", "74.40"]]}
    assert not webpage._table_like(prose) and webpage._table_like(figures)
    assert webpage._table_like({**prose, "kind": "ruled"}), "a table with drawn lines is a table whatever it holds"


def test_the_words_inside_a_screenshot_stay_in_the_picture(tmp_path):
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    c = canvas.Canvas(str(tmp_path / "a.pdf"), pagesize=A4)
    c.setFont("Helvetica", 11)
    c.drawString(60, 780, "The Recents tab shows the documents you opened last.")
    shot = Image.new("RGB", (800, 500), (225, 230, 240))
    c.drawImage(ImageReader(shot), 60, 400, 400, 250)
    c.setFont("Helvetica", 7)
    for i in range(12):                      # the labels of the app in the screenshot
        c.drawString(70 + 120 * (i % 3), 630 - 40 * (i // 3), f"Menu label {i + 1}")
    c.setFont("Helvetica", 11)
    c.drawString(60, 370, "Figure 1: the Recents tab.")
    c.save()
    paragraphs, _, xml = read(office.basic_docx(tmp_path / "a.pdf", tmp_path / "a.docx"))
    texts = [t for _, t in paragraphs]
    assert texts == ["The Recents tab shows the documents you opened last.", "Figure 1: the Recents tab."], texts
    assert "<w:drawing>" in xml, "the screenshot itself is kept"
