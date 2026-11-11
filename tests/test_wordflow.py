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


# -- found with a benchmark of designed pages ---------------------------------------------

SENTENCES = ("The harbour authority approved the new dredging schedule after a long public consultation. "
             "Local traders reported steady growth in visitor numbers throughout the summer season. "
             "Engineers inspected the northern breakwater and found only minor surface damage. "
             "A second survey of the seabed will be carried out before the winter storms arrive. "
             "Fishing crews asked for longer opening hours at the ice plant during the herring run. "
             "The ferry operator agreed to trial an early morning sailing for commuters next spring. ")


def wrap(c, text, x, y, width, font="Helvetica", size=10, lead=14):
    """Draw ``text`` wrapped to ``width`` from the baseline ``y`` down; the baseline after it."""
    from reportlab.lib.utils import simpleSplit

    c.setFont(font, size)
    for line in simpleSplit(text, font, size, width):
        c.drawString(x, y, line)
        y -= lead
    return y


def convert(tmp_path, draw, pages=1):
    """([(style, text)], tables, xml) of the .docx made from the page(s) ``draw(canvas, number)`` draws."""
    c = canvas.Canvas(str(tmp_path / "a.pdf"), pagesize=A4)
    for number in range(1, pages + 1):
        draw(c, number)
        c.showPage()
    c.save()
    return read(office.basic_docx(tmp_path / "a.pdf", tmp_path / "a.docx"))


def test_columns_are_read_past_a_quotation_set_across_two_of_them(tmp_path):
    def draw(c, _):
        wrap(c, "First column. " + SENTENCES * 2, 50, 760, 155)
        y = wrap(c, "Second column starts. " + SENTENCES[:260], 220, 760, 155)
        wrap(c, "Third column starts. " + SENTENCES[:260], 390, 760, 155)
        c.setFont("Helvetica-Oblique", 14)                 # across columns two and three
        c.drawCentredString(382, y - 20, "“Nobody remembers the year the gates held.")
        c.drawCentredString(382, y - 39, "They remember the year they did not.”")
        wrap(c, "Second column goes on below. " + SENTENCES[:200], 220, y - 70, 155)

    paragraphs, _, _ = convert(tmp_path, draw)
    text = " ".join(t for _, t in paragraphs)
    order = [text.find(s) for s in ("First column.", "Second column starts.", "Second column goes on below.",
                                    "Third column starts.")]
    assert -1 not in order and order == sorted(order), order
    quote = [(s, t) for s, t in paragraphs if "Nobody remembers" in t]
    assert quote == [("", "“Nobody remembers the year the gates held. They remember the year they did not.”")], quote


def test_a_raised_footnote_mark_stays_in_its_line(tmp_path):
    def draw(c, _):
        c.setFont("Times-Roman", 10.5)
        c.drawString(72, 700, "Figures for cargo are taken from the port ledger.")
        x = 72 + c.stringWidth("Figures for cargo are taken from the port ledger.", "Times-Roman", 10.5)
        c.setFont("Times-Roman", 6.5)
        c.drawString(x, 705.3, "1")
        c.setFont("Times-Roman", 10.5)
        c.drawString(x + 6, 700, "Engineers inspected the northern breakwater last week.")
        c.setFont("Times-Roman", 5)
        c.drawString(72, 104, "1")
        c.setFont("Times-Roman", 8)
        c.drawString(76, 100, "Ledger entries before 2019 were kept on paper.")

    paragraphs, _, xml = convert(tmp_path, draw)
    assert [t for _, t in paragraphs] == [
        "Figures for cargo are taken from the port ledger.1 Engineers inspected the northern breakwater last week.",
        "1 Ledger entries before 2019 were kept on paper."], paragraphs
    assert len(re.findall(r'<w:vertAlign w:val="superscript"/></w:rPr><w:t[^>]*>1 ?</w:t>', xml)) == 2


def diagram(c, y=600):
    """Four boxes joined by arrows, a word in each."""
    c.setFont("Helvetica", 9)
    for n, label in enumerate(("Intake", "Sorting", "Quality check", "Dispatch")):
        x = 72 + n * 100
        c.roundRect(x, y, 82, 34, 5)
        c.drawCentredString(x + 41, y + 14, label)
        if n:
            c.line(x - 18, y + 17, x - 1, y + 17)
            c.line(x - 5, y + 20, x - 1, y + 17)
            c.line(x - 5, y + 14, x - 1, y + 17)


def test_a_drawing_is_a_picture_and_its_lettering_stays_as_plain_text(tmp_path):
    def draw(c, _):
        y = wrap(c, SENTENCES, 72, 760, 450)
        diagram(c, y - 60)
        wrap(c, SENTENCES, 72, y - 100, 450)

    paragraphs, _, xml = convert(tmp_path, draw)
    assert paragraphs[1:5] == [("", "Intake"), ("", "Sorting"), ("", "Quality check"), ("", "Dispatch")], paragraphs
    assert [t[:20] for _, t in paragraphs[::5]] == [SENTENCES[:20]] * 2 and len(paragraphs) == 6
    assert xml.count("<w:drawing>") == 1, "the diagram is there as a picture too"


def test_the_names_in_an_organisation_chart_are_all_kept(tmp_path):
    people = [("Managing Director", "Ruth Abara", 230, 700), ("Head of Finance", "Leon Whitcombe", 80, 620),
              ("Head of Operations", "Sunita Varma", 230, 620), ("Head of Sales", "Gareth Pryce", 380, 620)]

    def draw(c, _):
        for role, name, x, y in people:
            c.rect(x, y, 130, 40)
            c.setFont("Helvetica-Bold", 10)
            c.drawCentredString(x + 65, y + 24, role)
            c.setFont("Helvetica", 9)
            c.drawCentredString(x + 65, y + 10, name)
        c.line(295, 700, 295, 680)
        c.line(145, 680, 445, 680)
        for x in (145, 295, 445):
            c.line(x, 680, x, 660)
        wrap(c, SENTENCES, 72, 560, 450)

    paragraphs, _, _ = convert(tmp_path, draw)
    for role, name, _, _ in people:
        assert ("", role) in paragraphs and ("", name) in paragraphs, paragraphs


def test_text_in_frames_is_not_a_drawing(tmp_path):
    def draw(c, _):
        for n in range(6):                                 # six framed notes, running text in each
            c.rect(72, 700 - 70 * n, 450, 60)
            wrap(c, f"Note {n + 1}. " + SENTENCES[:170], 80, 742 - 70 * n, 430)

    paragraphs, _, xml = convert(tmp_path, draw)
    assert len(paragraphs) == 6 and "<w:drawing>" not in xml, paragraphs


def test_a_framed_checklist_is_text_not_a_picture(tmp_path):
    items = ["Spare wheel and jack present", "Tyres at correct pressure", "Service book in glovebox",
             "No new damage to bodywork", "Fuel above one quarter", "First aid kit sealed"]

    def draw(c, _):
        c.rect(64, 590, 300, 130)
        c.setFont("Helvetica", 10)
        for n, item in enumerate(items):
            c.rect(74, 698 - 18 * n, 9, 9)
            c.drawString(90, 700 - 18 * n, item)
        wrap(c, SENTENCES, 64, 560, 450)

    paragraphs, _, xml = convert(tmp_path, draw)
    assert [t for _, t in paragraphs[:6]] == items and "<w:drawing>" not in xml, paragraphs


def test_bold_headings_in_the_size_of_the_text(tmp_path):
    heads = ["What every skipper must do before leaving the berth at night", "3. Fees and charges",
             "Case study: the Harker boundary", "Survey results 2024-2025"]

    def draw(c, _):
        y = 780
        for text in heads:
            c.setFont("Helvetica-Bold", 10)
            c.drawString(64, y, text)
            y = wrap(c, SENTENCES[:300], 64, y - 16, 460) - 14

    paragraphs, _, _ = convert(tmp_path, draw)
    assert [t for s, t in paragraphs if s == "Heading3"] == heads, paragraphs
    assert len(paragraphs) == 8


def test_lines_over_text_that_are_not_headings(tmp_path):
    def draw(c, _):
        c.setFont("Times-Bold", 20)
        c.drawString(90, 760, "Kiln Room Notes")
        c.setFont("Times-Roman", 11.5)                     # a dateline, grey, a shade larger than the text
        c.setFillGray(0.47)
        c.drawString(90, 738, "Revised Thursday 5 February 2026")
        c.setFillGray(0)
        y = wrap(c, SENTENCES[:300], 90, 720, 410, "Times-Roman", 11) - 9
        c.drawString(90, y, "THIS KILN MUST COOL FOR TWELVE HOURS")       # a notice in capitals
        y = wrap(c, SENTENCES[:300], 90, y - 16, 410, "Times-Roman", 11) - 9
        c.setFont("Times-Bold", 11)
        c.drawString(90, y, "A word about the thermocouple:")              # a bold lead-in
        wrap(c, SENTENCES[:300], 90, y - 16, 410, "Times-Roman", 11)

    paragraphs, _, _ = convert(tmp_path, draw)
    assert [(s, t) for s, t in paragraphs if s] == [("Heading1", "Kiln Room Notes")], paragraphs
    assert len(paragraphs) == 7


def test_bold_lines_that_are_not_headings(tmp_path):
    def draw(c, _):
        y = wrap(c, SENTENCES[:300], 64, 780, 460) - 6
        c.setFont("Helvetica-Bold", 10)
        c.drawString(64, y, "Booking office: 01632 960 417")       # a label and its value
        c.drawString(64, y - 40, "Things to bring:")               # a bold line before a list
        c.setFont("Helvetica", 10)
        for n, item in enumerate(("1. The signed form", "2. Proof of address")):
            c.drawString(64, y - 56 - 14 * n, item)
        c.setFont("Helvetica-Bold", 10)
        for n, item in enumerate(("1. Moor the boat", "2. Report to the office")):   # a list in bold
            c.drawString(64, y - 120 - 14 * n, item)
        c.drawString(64, y - 180, "Total due")                      # a label, its value beside it
        c.drawRightString(400, y - 180, "1,267.80")

    paragraphs, _, _ = convert(tmp_path, draw)
    assert not [t for s, t in paragraphs if s.startswith("Heading")], paragraphs
    assert ("ListParagraph", "1. Moor the boat") in paragraphs and ("", "Total due 1,267.80") in paragraphs


def test_large_sentences_are_text_not_headings(tmp_path):
    def draw(c, _):
        c.setFont("Helvetica-Bold", 24)
        c.drawString(50, 780, "Three Seasons on the Estuary")
        c.setFont("Helvetica-Oblique", 13)                 # a standfirst
        c.drawString(50, 750, "A year of small repairs and one very large crane has changed how")
        c.drawString(50, 733, "the port works from day to day.")
        y = wrap(c, SENTENCES, 50, 700, 240)
        c.setFont("Helvetica-Oblique", 14)                 # a pull quote in the column, centred
        c.drawCentredString(170, y - 14, "“We stopped waiting for the big ships")
        c.drawCentredString(170, y - 32, "and looked after the small ones.”")
        wrap(c, SENTENCES, 50, y - 64, 240)

    paragraphs, _, _ = convert(tmp_path, draw)
    assert [(s, t) for s, t in paragraphs if s] == [("Heading1", "Three Seasons on the Estuary")], paragraphs
    texts = [t for _, t in paragraphs]
    assert "“We stopped waiting for the big ships and looked after the small ones.”" in texts
    assert "A year of small repairs and one very large crane has changed how the port works from day to day." in texts


def test_a_title_that_opens_with_a_quotation_and_a_plain_heading_a_little_larger(tmp_path):
    def draw(c, _):
        c.setFont("Times-Bold", 22)
        c.drawString(78, 740, "“Slow Is Smooth” and the Patient")
        c.drawString(78, 714, "Craft of the Dry-Stone Waller")
        y = wrap(c, SENTENCES, 78, 680, 450, "Times-Roman", 10, 13)
        c.setFont("Helvetica", 11)                         # 11 pt plain over 10 pt text
        c.drawString(78, y - 6, "Materials and tools")
        wrap(c, SENTENCES, 78, y - 22, 450, "Times-Roman", 10, 13)

    paragraphs, _, _ = convert(tmp_path, draw)
    assert [(s, t) for s, t in paragraphs if s] == [
        ("Heading1", "“Slow Is Smooth” and the Patient Craft of the Dry-Stone Waller"),
        ("Heading3", "Materials and tools")], paragraphs


def test_small_lines_at_the_edges_of_a_single_page_are_kept(tmp_path):
    def draw(c, _):
        c.setFont("Helvetica", 8)
        c.drawString(64, 802, "Our ref KT/2291/B")                 # a letter's reference
        c.drawString(64, 34, "Registered in England No 4471902")
        c.setFont("Helvetica-Bold", 15)
        c.drawString(64, 747, "Income and expenditure")
        y = wrap(c, SENTENCES * 2, 64, 725, 460)
        c.setFont("Helvetica", 8)
        c.drawString(64, 110, "1 Excludes agency staff")           # the only footnote
        c.setFont("Helvetica", 30)
        c.drawCentredString(297, y - 200, "7")                     # a chapter's numeral, mid-page

    texts = [t for _, t in convert(tmp_path, draw)[0]]
    for kept in ("Our ref KT/2291/B", "Registered in England No 4471902", "1 Excludes agency staff", "7"):
        assert kept in texts, (kept, texts)


def test_first_and_last_lines_in_the_texts_own_type_are_kept(tmp_path):
    def draw(c, _):
        c.setFont("Helvetica", 10)
        c.drawString(64, 800, "11 November 2026")                  # a letter: its date, then the text
        y = wrap(c, SENTENCES * 2, 64, 740, 460)
        c.drawString(64, 40, "Yours faithfully, the Harbourmaster")
        c.setFont("Helvetica", 8)
        c.drawString(64, y - 4, "A note in small type right under the text.")

    texts = [t for _, t in convert(tmp_path, draw)[0]]
    assert texts[0] == "11 November 2026" and texts[-1] == "Yours faithfully, the Harbourmaster", texts
    assert "A note in small type right under the text." in texts


def test_what_opens_and_closes_each_page_is_not_a_running_line(tmp_path):
    def draw(c, number):
        c.setFont("Helvetica", 8)
        c.drawString(64, 810, "Coast path guide")                  # the running header, and the page number
        c.drawCentredString(297, 30, str(number))
        c.setFont("Helvetica-Bold", 12)
        c.drawString(64, 790, f"Day {number}")                     # a heading at the top of each page
        y = wrap(c, f"The walk on day {number} starts here. " + SENTENCES, 64, 770, 460)
        c.setFont("Helvetica", 10)
        c.drawString(64, y - 10, f"Distance walked: {10 + number} km")
        c.drawString(64, 70, f"Distance walked: {10 + number} km")  # and a closing line low on the page

    paragraphs, _, _ = convert(tmp_path, draw, pages=3)
    texts = [t for _, t in paragraphs]
    assert [t for s, t in paragraphs if s.startswith("Heading")] == ["Day 1", "Day 2", "Day 3"], paragraphs
    assert texts.count("Distance walked: 12 km") == 2 and "Coast path guide" not in texts
    assert not [t for t in texts if t.strip().isdigit()], "the page numbers are left out"


def test_tables_without_lines_stay_tables(tmp_path):
    from pdfmanager import webpage

    def aligned(rows):
        return webpage._table_like({"kind": "aligned", "rows": rows})

    assert aligned([["Oak board", "14.50"], ["Pine board", "9.25"], ["Ash board", "17.00"]]), "two columns"
    assert aligned([["", "Mon", "Tue", "Wed"], ["Early", "Abara", "", ""], ["Late", "", "Pryce", ""],
                    ["Night", "", "", "Varma"], ["Cover", "Mbeki", "", ""]]), "many cells empty"
    assert aligned([["Kerf", "the width of the cut that the blade makes"], ["Arbor", "the shaft that carries the blade"],
                    ["Fence", "a straight guide for the work"], ["Riving knife", "a plate behind the blade"]])


def test_labels_ending_in_a_colon_before_their_values_are_not_a_table():
    from pdfmanager import webpage

    def aligned(rows):
        return webpage._table_like({"kind": "aligned", "rows": rows})

    assert not aligned([["Northfield NF2 6QT", "Invoice no:", "2026-0417"], ["accounts@example.com", "Date:", "11 May"],
                        ["", "Due date:", "11 June"]])
    assert aligned([["Item", "Qty", "Amount"], ["Copper tubing", "14", "52.50"], ["Brass fittings", "", "74.40"]])
    narrow = [["The harbour authority approved", "second survey of the seabed"], ["the new dredging schedule after", "will be carried out before the"],
              ["a long public consultation.", "winter storms arrive. Fishing"], ["Local traders reported steady", "crews asked for longer"],
              ["growth in visitor numbers.", "opening hours at the plant."]]
    assert not aligned(narrow), "narrow columns of running text"


def test_a_tables_bold_is_the_pages_own(tmp_path):
    def draw(c, _):
        rows = [("Berth", "Holder", "Vessel"), ("North quay", "Estuary Sailing Trust", "Kittiwake"),
                ("Total", "Two berths", "Two vessels")]
        for r, row in enumerate(rows):
            c.setFont("Helvetica-Bold" if r == 2 else "Helvetica", 10)
            for k, cell in enumerate(row):
                c.drawString(78 + 150 * k, 706 - 20 * r, cell)
        c.grid([72, 222, 372, 522], [720, 700, 680, 660])

    _, tables, xml = convert(tmp_path, draw)
    assert tables[0][0] == ["Berth", "Holder", "Vessel"]
    rows = re.findall(r"<w:tr>.*?</w:tr>", xml, re.S)
    assert ["<w:b/>" in row for row in rows] == [False, False, True], "only the row in bold on the page is bold"
    assert "<w:tblHeader/>" not in xml
    # ... and a row of words over rows of figures is a heading row, bold or not on the page
    assert "<w:tblHeader/>" in read(office.basic_docx(report(tmp_path / "r.pdf"), tmp_path / "r.docx"))[2]


def test_the_short_last_line_of_a_list_item_stays_with_it(tmp_path):
    def draw(c, _):
        c.setFont("Helvetica", 9)
        y = 700
        for lines in (["• Dogs are welcome on leads."], ["• Free entry for children under", "twelve."],
                      ["• Guided walks leave the pilot", "office at ten and two."]):
            for n, line in enumerate(lines):
                c.setFont("Helvetica", 10)                 # the bullet a size larger than its text
                c.drawString(402, y, "" if n else "•")
                c.setFont("Helvetica", 9)
                c.drawString(412, y, line.lstrip("• "))
                y -= 12
            y -= 4

    paragraphs, _, _ = convert(tmp_path, draw)
    assert paragraphs == [("ListParagraph", "• Dogs are welcome on leads."),
                          ("ListParagraph", "• Free entry for children under twelve."),
                          ("ListParagraph", "• Guided walks leave the pilot office at ten and two.")], paragraphs


def test_a_caption_in_italics_is_not_joined_to_the_text_after_it(tmp_path):
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    def draw(c, _):
        y = wrap(c, SENTENCES[:259] + " Planners expect the waterfront", 64, 760, 260)
        c.drawImage(ImageReader(Image.new("RGB", (300, 200), (120, 150, 180))), 342, y + 28, 189, 126)
        c.setFont("Helvetica-Oblique", 9)
        c.drawString(342, y + 14, "Figure 1: The restored east quay at low tide.")   # level with the text's last line
        wrap(c, "path to be finished well ahead of the regatta. " + SENTENCES[:176], 64, y - 5, 467)

    paragraphs, _, xml = convert(tmp_path, draw)
    texts = [t for _, t in paragraphs]
    assert "Figure 1: The restored east quay at low tide." in texts, texts
    assert "<w:drawing>" in xml


def test_paragraphs_on_both_sides_of_a_picture_are_not_joined(tmp_path):
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    def draw(c, _):
        c.setFont("Helvetica", 10)
        c.drawString(64, 760, "The first paragraph ends without a full stop, as a line of a list would")
        c.drawImage(ImageReader(Image.new("RGB", (300, 200), (120, 150, 180))), 64, 600, 189, 126)
        c.drawString(64, 570, "eBay and iPhone are names; this paragraph starts with a small letter.")

    texts = [t for _, t in convert(tmp_path, draw)[0]]
    assert texts == ["The first paragraph ends without a full stop, as a line of a list would",
                     "eBay and iPhone are names; this paragraph starts with a small letter."], texts


def test_a_masthead_is_not_one_line(tmp_path):
    def draw(c, _):
        c.setFont("Helvetica", 9.5)
        c.drawString(40, 780, "Issue 42")
        c.drawRightString(555, 780, "November 2026")
        c.setFont("Helvetica-Bold", 30)
        c.drawCentredString(297, 772, "The Harbour Bulletin")
        wrap(c, SENTENCES, 40, 720, 500)

    paragraphs, _, _ = convert(tmp_path, draw)
    assert [(s, t) for s, t in paragraphs[:3]] != [] and ("Heading1", "The Harbour Bulletin") in paragraphs
    assert ("", "Issue 42") in paragraphs and ("", "November 2026") in paragraphs, paragraphs


def test_wrapped_chinese_is_joined_without_a_space(tmp_path):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    lines = ["今年港口的货物吞吐量继续增长，东码头的扩建工程已经按期完成。港务局对北防波堤进行了全",
             "面检查，只发现少量表面损坏，计划在冬季风暴来临之前完成修复。"]

    def draw(c, _):
        c.setFont("STSong-Light", 11)
        for n, line in enumerate(lines):
            c.drawString(64, 700 - 16 * n, line)

    paragraphs, _, _ = convert(tmp_path, draw)
    assert [t for _, t in paragraphs] == ["".join(lines)], paragraphs


def test_only_a_plain_compound_broken_at_its_hyphen_keeps_it(tmp_path):
    lines = ["The harbourside community relies on up-to-", "date charts, a matter-of-", "fact pilot, his son-in-",
             "law and round-the-", "clock monitoring. It is non-", "sense to call this self-",
             "ishness: the long-estab-", "lished firm is under obser-", "vation."]

    def draw(c, _):
        c.setFont("Helvetica", 10)
        for n, line in enumerate(lines):
            c.drawString(64, 700 - 14 * n, line)
            if n < len(lines) - 1:  # set as full lines: the right edge of the column
                c.drawString(64 + 215 - c.stringWidth("-", "Helvetica", 10), 700 - 14 * n, " ")

    text = " ".join(t for _, t in convert(tmp_path, draw)[0])
    for word in ("up-to-date", "matter-of-fact", "son-in-law", "round-the-clock", "nonsense", "selfishness",
                 "long-established", "observation"):
        assert f" {word}" in text, (word, text)


def table_lines(heading_font, upper, lower, first_row):
    """The words of a table without lines: two lines at the top set close, then rows."""
    def line(top, cells, font="Helvetica", size=10):
        return [{"x0": x, "x1": x + 6 * len(t), "top": top, "bottom": top + size, "text": t, "size": size,
                 "fontname": font} for x, t in cells]

    xs = (64, 250, 340, 430)
    words = line(100, upper, heading_font) + line(111.5, lower, heading_font)
    for n, row in enumerate((first_row, ("Fender", "19.50", "120", "Large"), ("Boat-hook", "14.25", "30", "Telescopic"),
                             ("Signal-horn", "9.90", "64", "Brass"))):
        words += line(131.5 + 20 * n, list(zip(xs, row)))
    return words


def test_a_two_line_heading_row_whose_one_line_cells_stand_on_the_lower_line():
    from pdfmanager import tables

    words = table_lines("Helvetica-Bold", [(250, "Unit-price"), (340, "Quantity")],
                        [(64, "Item"), (250, "(EUR)"), (340, "in-stock"), (430, "Notes")],
                        ("Mooring-warp", "28.00", "45", "Nylon"))
    runs = tables._runs(tables._lines(words))
    assert len(runs) == 1
    assert tables._aligned_table(runs[0])["rows"][:2] == [["Item", "Unit-price (EUR)", "Quantity in-stock", "Notes"],
                                                          ["Mooring-warp", "28.00", "45", "Nylon"]]


def test_a_heading_row_is_not_merged_into_the_first_row_of_figures():
    from pdfmanager import tables

    # a timetable: the heading row has no first cell and stands close over the first row, all in one type
    words = table_lines("Helvetica", [(250, "Mon"), (340, "Tue"), (430, "Wed")],
                        [(64, "Ashby"), (250, "07:10"), (340, "07:15"), (430, "07:10")],
                        ("Dunmore", "07:40", "07:45", "07:40"))
    rows = tables._aligned_table(tables._runs(tables._lines(words))[0])["rows"]
    assert rows[:2] == [["", "Mon", "Tue", "Wed"], ["Ashby", "07:10", "07:15", "07:10"]], rows


def test_a_heading_row_in_larger_type_stays_in_its_table():
    from pdfmanager import tables

    words = [{"x0": x, "x1": x + 8 * len(t), "top": 100, "bottom": 114, "text": t, "size": 14, "fontname": "Helvetica-Bold"}
             for x, t in ((64, "Plant"), (250, "Q1"), (340, "Q2"), (430, "Q3"))]
    for n, row in enumerate((("Ashby", "12", "14", "9"), ("Dunmore", "7", "11", "13"), ("Eskfield", "5", "6", "8"))):
        words += [{"x0": x, "x1": x + 6 * len(t), "top": 124 + 18 * n, "bottom": 134 + 18 * n, "text": t, "size": 10,
                   "fontname": "Helvetica"} for x, t in zip((64, 250, 340, 430), row)]
    runs = tables._runs(tables._lines(words))
    assert len(runs) == 1 and tables._aligned_table(runs[0])["rows"][0] == ["Plant", "Q1", "Q2", "Q3"]


def test_centred_lines_with_an_inch_mark_are_not_run_together(tmp_path):
    lines = ["Overture to a Northern Spring", 'Fanfare for Brass and a 28" Tam-tam', "Interval of Twenty Minutes",
             "Suite in D for Small Orchestra and Solo Cello", "Evening Hymn"]

    def draw(c, _):
        c.setFont("Helvetica", 11)
        for n, line in enumerate(lines):
            c.drawCentredString(297, 700 - 16 * n, line)

    assert [t for _, t in convert(tmp_path, draw)[0]] == lines


def test_a_drop_capital_does_not_make_its_line_a_raised_mark(tmp_path):
    def draw(c, _):
        c.setFont("Times-Bold", 36)
        c.drawString(72, 676, "T")
        x = 72 + c.stringWidth("T", "Times-Bold", 36) + 2
        c.setFont("Times-Roman", 11)
        c.drawString(x, 692, "he harbour authority approved the new dredging schedule after a long")
        c.drawString(x, 678, "public consultation, and local traders reported steady growth in visitor")
        c.drawString(72, 664, "numbers throughout the summer season.")

    paragraphs, _, xml = convert(tmp_path, draw)
    text = " ".join(t for _, t in paragraphs)
    assert "he harbour authority approved the new dredging schedule" in text, text
    assert "vertAlign" not in xml


def test_lowered_figures_stay_in_their_line(tmp_path):
    def draw(c, _):
        x = 72
        for text, size, y in (("Carbon dioxide (CO", 11, 700), ("2", 7, 697.5), (") and water (H", 11, 700),
                              ("2", 7, 697.5), ("O) are given off.", 11, 700)):
            c.setFont("Helvetica", size)
            c.drawString(x, y, text)
            x += c.stringWidth(text, "Helvetica", size)

    paragraphs, _, xml = convert(tmp_path, draw)
    assert [t for _, t in paragraphs] == ["Carbon dioxide (CO2) and water (H2O) are given off."], paragraphs
    assert xml.count('<w:vertAlign w:val="subscript"/>') == 2

# -- found on a designed product brochure --------------------------------------------------

SPECS = ["Size: 600 x 600 mm", "Thickness: 10 mm", "Finish: matt", "Rectified edges",
         "Frost resistant", "Suitable for floors and walls"]


def product(c, x=320, y=700, dx=0.0, mode=None, white=False):
    """A product's heading over its list of specifications."""
    t = c.beginText()
    if mode is not None:
        t.setTextRenderMode(mode)
    if white:
        t.setFillColorRGB(1, 1, 1)
    t.setTextOrigin(x + dx, y)
    t.setFont("Helvetica-Bold", 20)
    t.textLine("HUDSON BEIGE - HB")
    t.setFont("Helvetica", 11)
    t.setLeading(16)
    t.moveCursor(0, 10)
    for spec in SPECS:
        t.textLine("-  " + spec)
    c.drawText(t)


def photo(c):
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    c.drawImage(ImageReader(Image.new("RGB", (200, 260), (150, 120, 90))), 50, 450, 230, 300)


def product_page(c, copy):
    """A photo, the product beside it, and a second copy of the product's
    text drawn the way ``copy`` says."""
    photo(c)
    if copy == "under the photo":
        product(c, x=60)
        photo(c)
    product(c)
    if copy == "at the same place":
        product(c)
    elif copy == "a little to the side":       # bold made by drawing the text twice
        product(c, dx=0.4)
    elif copy == "invisible":                  # render mode 3, over the photo
        product(c, x=60, mode=3)
    elif copy == "white on white":
        product(c, y=330, white=True)
    elif copy == "off the page":
        product(c, x=A4[0] + 40)
    elif copy == "clipped away":
        c.saveState()
        path = c.beginPath()
        path.rect(0, 0, 10, 10)
        c.clipPath(path, stroke=0, fill=0)
        product(c, x=60)
        c.restoreState()
    elif copy == "in a hidden layer":
        c._code.append("/OC /Hidden BDC")
        product(c, x=60)
        c._code.append("EMC")


@pytest.mark.parametrize("copy", ["at the same place", "a little to the side", "under the photo", "invisible",
                                  "white on white", "off the page", "clipped away", "in a hidden layer"])
def test_text_drawn_twice_or_a_copy_that_does_not_show_is_read_once(tmp_path, copy):
    import pikepdf

    c = canvas.Canvas(str(tmp_path / "a.pdf"), pagesize=A4)
    product_page(c, copy)
    c.save()
    if copy == "in a hidden layer":
        with pikepdf.open(tmp_path / "a.pdf", allow_overwriting_input=True) as pdf:
            layer = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name="Old text"))
            pdf.Root.OCProperties = pikepdf.Dictionary(OCGs=[layer], D=pikepdf.Dictionary(ON=[], OFF=[layer]))
            pdf.pages[0].Resources.Properties = pikepdf.Dictionary(Hidden=layer)
            pdf.save(tmp_path / "a.pdf")
    paragraphs, tables, xml = read(office.basic_docx(tmp_path / "a.pdf", tmp_path / "a.docx"))
    assert not tables, tables
    texts = [t for _, t in paragraphs]
    assert texts.count("HUDSON BEIGE - HB") == 1 and ("Heading1", "HUDSON BEIGE - HB") in paragraphs, paragraphs
    for spec in SPECS:
        assert sum(spec in t for t in texts) == 1, (spec, texts)
    assert "<w:drawing>" in xml


def test_text_that_does_not_show_and_is_no_copy_is_kept(tmp_path):
    def draw(c, _):
        photo(c)
        product(c)
        t = c.beginText()
        t.setTextRenderMode(3)
        t.setTextOrigin(60, 600)
        t.setFont("Helvetica", 11)
        t.textLine("Keywords for searching: porcelain floor tile")
        c.drawText(t)

    texts = [t for _, t in convert(tmp_path, draw)[0]]
    assert "Keywords for searching: porcelain floor tile" in texts, texts


def footer(c, number, url_size=9, over=20, rise=0.0, folio_first=True):
    """A web address at the foot of the page with the page number in
    other type set over it."""
    def folio():
        c.setFont("Helvetica-Bold", 9)
        c.drawString(72 + over, 30 + rise, str(number))

    if folio_first:
        folio()
    c.setFont("Helvetica", url_size)
    c.drawString(72, 30, "www.proselectdesign.com")
    if not folio_first:
        folio()


@pytest.mark.parametrize("folio_first, rise", [(True, 0.0), (False, 0.0), (True, 0.5)])
def test_a_page_number_set_over_the_footer_is_not_mixed_into_it(tmp_path, folio_first, rise):
    import pdfplumber

    from pdfmanager import tables

    def draw(c, number):
        y = wrap(c, SENTENCES * 2, 72, 760, 450)
        footer(c, number, rise=rise, folio_first=folio_first)

    paragraphs, found, _ = convert(tmp_path, draw, pages=3)
    texts = [t for _, t in paragraphs] + [cell for table in found for row in table for cell in row]
    assert not [t for t in texts if "proselectdesign" in t and re.search(r"\d", t)], texts
    with pdfplumber.open(str(tmp_path / "a.pdf")) as pdf:
        words = [w["text"] for w in tables._words(pdf.pages[0]) if w["top"] > 780]
    assert sorted(words) == ["1", "www.proselectdesign.com"], words


def test_running_lines_with_page_numbers_and_in_larger_type_are_not_headings(tmp_path):
    def draw(c, number):
        c.setFont("Helvetica", 12)
        c.drawString(64, 812, f"Desktop Welcome Guide {number}/4")   # larger than the text, 30 pt over it
        c.drawString(64, 24, "www.proselectdesign.com")
        c.setFont("Helvetica-Bold", 16)
        c.drawString(64, 760, f"Step {number}: {['Install', 'Open a file', 'Edit', 'Save'][number - 1]}")
        wrap(c, SENTENCES, 64, 738, 460)

    paragraphs, _, _ = convert(tmp_path, draw, pages=4)
    texts = [t for _, t in paragraphs]
    assert not [t for t in texts if "Desktop Welcome Guide" in t or "proselectdesign" in t], paragraphs
    assert [t for s, t in paragraphs if s.startswith("Heading")] == [
        "Step 1: Install", "Step 2: Open a file", "Step 3: Edit", "Step 4: Save"], paragraphs


def test_a_running_line_kept_for_the_text_right_under_it_is_not_a_heading(tmp_path):
    def draw(c, number):
        c.setFont("Helvetica-Bold", 12)
        c.drawString(64, 800, f"Desktop Welcome Guide {number} of 3")
        wrap(c, SENTENCES, 64, 786, 460)

    paragraphs, _, _ = convert(tmp_path, draw, pages=3)
    kept = [(s, t) for s, t in paragraphs if t.startswith("Desktop Welcome Guide")]
    assert kept and not [s for s, _ in kept if s.startswith("Heading")], paragraphs


def test_fragments_and_codes_in_large_type_are_not_headings(tmp_path):
    def draw(c, _):
        c.setFont("Helvetica-Bold", 22)
        c.drawString(64, 780, "Colours and finishes")
        wrap(c, SENTENCES, 64, 750, 460)
        c.setFont("Helvetica-Bold", 16)
        for k, code in enumerate(["BLU", "LG", "WT", "SL", "HB"]):     # beside their swatches
            c.drawString(80 + 90 * k, 600, code)
        c.drawString(64, 520, "w")                                      # cut off by the page's edge
        c.drawString(-60, 480, "www.proselectdesign.com")
        wrap(c, SENTENCES, 64, 450, 460)

    paragraphs, _, _ = convert(tmp_path, draw)
    headings = [t for s, t in paragraphs if s.startswith("Heading")]
    assert headings == ["Colours and finishes"], paragraphs
    text = " ".join(t for _, t in paragraphs)
    assert all(code in text for code in ["BLU", "LG", "WT", "SL", "HB"]), "the codes are kept as text"


def test_the_converter_menus_hint_says_what_the_built_in_converter_keeps():
    from pdfmanager import gui

    (op,) = [op for op in gui.OPERATIONS if op["id"] == "to-word"]
    (field,) = [f for f in op["fields"] if f.get("hint")]
    assert "plain paragraphs" not in field["hint"]
    assert all(word in field["hint"] for word in ("headings", "lists", "tables", "pictures"))
