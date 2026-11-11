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


def test_the_lettering_of_a_drawing_stays_in_the_picture(tmp_path):
    def draw(c, _):
        y = wrap(c, SENTENCES, 72, 760, 450)
        diagram(c, y - 60)
        wrap(c, SENTENCES, 72, y - 100, 450)

    paragraphs, _, xml = convert(tmp_path, draw)
    assert [t[:20] for _, t in paragraphs] == [SENTENCES[:20]] * 2, paragraphs
    assert xml.count("<w:drawing>") == 1, "the diagram is a picture, its lettering on it"


def test_text_in_frames_is_not_a_drawing(tmp_path):
    def draw(c, _):
        for n in range(6):                                 # six framed notes, running text in each
            c.rect(72, 700 - 70 * n, 450, 60)
            wrap(c, f"Note {n + 1}. " + SENTENCES[:170], 80, 742 - 70 * n, 430)

    paragraphs, _, xml = convert(tmp_path, draw)
    assert len(paragraphs) == 6 and "<w:drawing>" not in xml, paragraphs


def test_headings_set_apart_without_larger_type(tmp_path):
    heads = [("SPEED IN THE FAIRWAY", "Helvetica"),
             ("What every skipper must do before leaving the berth at night", "Helvetica-Bold"),
             ("Opening hours of the harbour office:", "Helvetica-Bold"),
             ("3. Fees and charges", "Helvetica-Bold")]

    def draw(c, _):
        y = 780
        for text, font in heads:
            c.setFont(font, 10)
            c.drawString(64, y, text)
            y = wrap(c, SENTENCES[:300], 64, y - 16, 460) - 14

    paragraphs, _, _ = convert(tmp_path, draw)
    assert [t for s, t in paragraphs if s == "Heading3"] == [t for t, _ in heads], paragraphs
    assert len(paragraphs) == 8


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
        y = wrap(c, SENTENCES, 50, y - 64, 240)
        c.setFont("Helvetica", 11)                         # a little larger than the text, no more
        c.drawString(50, y - 10, "Spring programme")
        wrap(c, SENTENCES[:200], 50, y - 40, 240)

    paragraphs, _, _ = convert(tmp_path, draw)
    assert [(s, t) for s, t in paragraphs if s] == [("Heading1", "Three Seasons on the Estuary")], paragraphs
    texts = [t for _, t in paragraphs]
    assert "“We stopped waiting for the big ships and looked after the small ones.”" in texts
    assert "A year of small repairs and one very large crane has changed how the port works from day to day." in texts


def test_a_header_and_footer_on_a_single_page(tmp_path):
    def draw(c, _):
        c.setFont("Helvetica", 9)
        c.drawString(64, 802, "Annual Review 2026")
        c.drawRightString(531, 802, "Section 2: Finance")
        c.drawString(64, 34, "www.northfield.example")
        c.drawRightString(531, 34, "7")
        c.setFont("Helvetica-Bold", 15)
        c.drawString(64, 747, "Income and expenditure")
        wrap(c, SENTENCES * 2, 64, 725, 460)

    paragraphs, _, _ = convert(tmp_path, draw)
    assert [t[:22] for _, t in paragraphs] == ["Income and expenditure", SENTENCES[:22]], paragraphs


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


def test_running_titles_and_page_numbers_set_well_inside_the_page(tmp_path):
    def draw(c, number):
        c.setFont("Helvetica-Oblique", 10)
        c.drawCentredString(297, 746, "Harbour Notes")             # 11% down the page
        c.setFont("Helvetica", 11.5)
        y = wrap(c, f"Page {number} starts here. " + SENTENCES, 90, 700, 410, size=11.5, lead=15)
        wrap(c, SENTENCES, 90, y - 10, 410, size=11.5, lead=15)
        c.setFont("Helvetica", 10.5)
        c.drawCentredString(297, 100, str(11 + number))            # 12% up from the foot

    paragraphs, _, _ = convert(tmp_path, draw, pages=3)
    texts = [t for _, t in paragraphs]
    assert len(texts) == 6 and not [t for t in texts if "Harbour Notes" in t or t.strip().isdigit()], texts


def test_a_form_is_not_a_table_and_keeps_its_headings(tmp_path):
    def draw(c, _):
        y = 760
        for head, pairs in (("1. Applicant details", [("Full name", "Date of birth"), ("Address", "Postcode"),
                                                      ("Telephone", "Email address")]),
                            ("2. Vessel details", [("Vessel name", "Registration no."), ("Length (m)", "Beam (m)")])):
            c.setFont("Helvetica-Bold", 11.5)
            c.drawString(64, y, head)
            c.setFont("Helvetica", 10)
            for left, right in pairs:
                y -= 26
                c.drawString(64, y, left)
                c.line(156, y - 2, 290, y - 2)
                c.drawString(310, y, right)
                c.line(402, y - 2, 531, y - 2)
            y -= 34

    paragraphs, tables, _ = convert(tmp_path, draw)
    assert not tables
    assert [t for s, t in paragraphs if s.startswith("Heading")] == ["1. Applicant details", "2. Vessel details"]
    assert ("", "Full name Date of birth") in paragraphs and ("", "Length (m) Beam (m)") in paragraphs


def test_labels_that_line_up_with_their_values_are_not_a_table():
    from pdfmanager import webpage

    def aligned(rows):
        return webpage._table_like({"kind": "aligned", "rows": rows})

    assert not aligned([["Subtotal", "1,056.50"], ["VAT at 20%", "211.30"], ["Total due", "1,267.80"]])
    assert not aligned([["Northfield NF2 6QT", "Invoice no:", "2026-0417"], ["accounts@example.com", "Date:", "11 May"],
                        ["", "Due date:", "11 June"]])
    assert not aligned([["Tidewater Marine", "", "INVOICE"], ["14 Chandlers Row", "", ""], ["Northfield", "No", "17"],
                        ["", "Date", "11 May"]])
    assert aligned([["Item", "Price"], ["Boat hook", "14.25"], ["Signal horn", "9.90"], ["Fender", "19.50"]])
    assert aligned([["Item", "Qty", "Amount"], ["Copper tubing", "14", "52.50"], ["Brass fittings", "", "74.40"]])


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


def test_text_goes_on_after_a_picture_and_its_caption(tmp_path):
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
    assert any("Planners expect the waterfront path to be finished well ahead of the regatta." in t for t in texts), texts
    assert "<w:drawing>" in xml


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


def test_a_compound_broken_at_its_hyphen_keeps_it(tmp_path):
    lines = ["The harbourside community relies on up-", "to-date charts, a full-time pilot and round-the-",
             "clock monitoring, which is self-", "financing; the channel is under obser-",
             "vation by a well-", "equipped team."]

    def draw(c, _):
        c.setFont("Helvetica", 10)
        for n, line in enumerate(lines):
            c.drawString(64, 700 - 14 * n, line)
            if n < 5:  # set as full lines: the right edge of the column
                c.drawString(64 + 215 - c.stringWidth("-", "Helvetica", 10), 700 - 14 * n, " ")

    paragraphs, _, _ = convert(tmp_path, draw)
    text = " ".join(t for _, t in paragraphs)
    for word in ("up-to-date", "round-the-clock", "self-financing", "observation", "well-equipped", "full-time"):
        assert word in text, (word, text)


def test_a_two_line_heading_row_whose_one_line_cells_stand_on_the_lower_line():
    from pdfmanager import tables

    def line(top, cells):
        return [{"x0": x, "x1": x + 6 * len(t), "top": top, "bottom": top + 10, "text": t, "size": 10}
                for x, t in cells]

    words = (line(100, [(250, "Unit-price"), (340, "Quantity")])
             + line(111.5, [(64, "Item"), (250, "(EUR)"), (340, "in-stock"), (430, "Notes")]))
    for n, row in enumerate((("Mooring-warp", "28.00", "45", "Nylon"), ("Fender", "19.50", "120", "Large"),
                             ("Boat-hook", "14.25", "30", "Telescopic"))):
        words += line(131.5 + 20 * n, list(zip((64, 250, 340, 430), row)))
    runs = tables._runs(tables._lines(words))
    assert len(runs) == 1
    assert tables._aligned_table(runs[0])["rows"][:2] == [["Item", "Unit-price (EUR)", "Quantity in-stock", "Notes"],
                                                          ["Mooring-warp", "28.00", "45", "Nylon"]]
