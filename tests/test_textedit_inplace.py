"""Changing words in the page's own drawing: the old letters really go (no
box over them), the new ones are written where they were (same text object,
same tag, same colour and outline), and what's around them stays put."""

import ctypes

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, NumberObject
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from pdfmanager import fonts, textedit

FOX = "The quick brown fox jumps over the lazy dog."
STAYS = "A neighbouring line that must not move at all."


def std_font(name, encoding="/WinAnsiEncoding"):
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/" + name)})
    if encoding:
        font[NameObject("/Encoding")] = NameObject(encoding)
    return font


def raw_page(path, content, faces=None, more=None):
    """A page drawn by ``content`` (PDF operators) with the standard fonts
    ``faces`` ({"/F1": "Helvetica"}); ``more(writer, page)`` adds the rest."""
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    stream = DecodedStreamObject()
    stream.set_data(content.encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    font_dict = DictionaryObject({NameObject(k): writer._add_object(std_font(v))
                                  for k, v in (faces or {"/F1": "Helvetica"}).items()})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): font_dict})
    if more:
        more(writer, page)
    with open(path, "wb") as f:
        writer.write(f)
    return path


def plain(path):
    return raw_page(path, f"BT /F1 12 Tf 72 720 Td ({FOX}) Tj 0 -30 Td ({STAYS}) Tj ET")


def chars(path, page=0):
    """[(letter, x, y, right edge, render mode, fill rgba)] of the letters pdfium reads."""
    doc = pdfium.PdfDocument(str(path))
    try:
        textpage = doc[page].get_textpage()
        out = []
        for i in range(textpage.count_chars()):
            ch = textpage.get_text_range(i, 1)
            if not ch.strip():
                continue
            x, y = textedit._origin(textpage, i)
            rgba = [ctypes.c_uint() for _ in range(4)]
            pdfium_raw.FPDFText_GetFillColor(textpage.raw, i, *map(ctypes.byref, rgba))
            mode = pdfium_raw.FPDFTextObj_GetTextRenderMode(pdfium_raw.FPDFText_GetTextObject(textpage.raw, i))
            out.append((ch, x, y, textpage.get_charbox(i, loose=True)[2], mode, tuple(v.value for v in rgba)))
        return out
    finally:
        doc.close()


def found(path, phrase, page=0):
    """How many times a reader's Find finds ``phrase``."""
    doc = pdfium.PdfDocument(str(path))
    try:
        search, n = doc[page].get_textpage().search(phrase), 0
        while search.get_next():
            n += 1
        return n
    finally:
        doc.close()


def filled_boxes(path, page=0):
    """How many filled paths the page draws."""
    doc = pdfium.PdfDocument(str(path))
    try:
        pg, n = doc[page], 0
        for i in range(pdfium_raw.FPDFPage_CountObjects(pg.raw)):
            obj = pdfium_raw.FPDFPage_GetObject(pg.raw, i)
            if pdfium_raw.FPDFPageObj_GetType(obj) == pdfium_raw.FPDF_PAGEOBJ_PATH:
                fill, stroke = ctypes.c_int(), ctypes.c_int()
                n += bool(pdfium_raw.FPDFPath_GetDrawMode(obj, ctypes.byref(fill), ctypes.byref(stroke)) and fill.value)
        return n
    finally:
        doc.close()


def line_rect(path, words, page=0):
    """The box the reader sends for the line that has ``words``."""
    found_ = chars(path, page)
    joined = "".join(c[0] for c in found_)
    k = joined.find(words.replace(" ", ""))
    assert k >= 0, words
    x, y = found_[k][1], found_[k][2]
    doc = pdfium.PdfDocument(str(path))
    try:
        textpage = doc[page].get_textpage()
        boxes = sorted(textpage.get_charbox(i, loose=True) for i in range(textpage.count_chars())
                       if textpage.get_text_range(i, 1).strip() and abs(textedit._origin(textpage, i)[1] - y) < 3)
    finally:
        doc.close()
    # Like the reader: words far apart on a line (a column, a table cell) are lines of their own.
    pieces = [[boxes[0]]]
    for a, b in zip(boxes, boxes[1:]):
        if b[0] - a[2] > 1.2 * (a[3] - a[1]):
            pieces.append([])
        pieces[-1].append(b)
    boxes = next(p for p in pieces if p[0][0] - 0.5 <= x <= p[-1][2])
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def change(src, out, old, new, words=None, **extra):
    report = []
    textedit.edit_text(src, out, [{"page": 1, "rect": line_rect(src, words or old), "text": new, "old": old, **extra}],
                       report=report)
    return report[0]


def content(path, page=0):
    return PdfReader(str(path)).pages[page].get_contents().get_data().decode("latin-1")


def stays(src, out, words=STAYS):
    """The untouched line is still there, letter for letter, where it was."""
    key = words.replace(" ", "")
    before, after = chars(src), chars(out)
    a = "".join(c[0] for c in before).find(key)
    b = "".join(c[0] for c in after).find(key)
    assert a >= 0 and b >= 0
    return all(p[0] == q[0] and abs(p[1] - q[1]) < 0.01 and abs(p[2] - q[2]) < 0.01
               for p, q in zip(before[a:a + len(key)], after[b:b + len(key)]))


# -- 1. the old words really go ------------------------------------------------------------------

def test_a_changed_line_has_no_old_words_under_it_and_no_cover(tmp_path):
    src, out = plain(tmp_path / "a.pdf"), tmp_path / "out.pdf"
    report = change(src, out, FOX, FOX.replace("brown", "black"))
    assert report == {"font": "Helvetica", "kind": "same"}
    assert found(out, "brown") == 0 and found(out, "The quick black fox jumps over the lazy dog.") == 1
    assert filled_boxes(out) == filled_boxes(src) == 0
    assert len(chars(out)) == len(chars(src))  # nothing drawn twice
    assert stays(src, out)
    # Still one text object, the words written where the old ones were.
    assert content(out).count("BT") == 1 and content(out).index("TJ") < content(out).index("neighbouring")


def test_changing_a_line_again_and_again_doesnt_pile_up_letters(tmp_path):
    cur = plain(tmp_path / "a.pdf")
    line = FOX
    for n, (old, new) in enumerate([("brown", "black"), ("lazy", "idle"), ("black", "white"),
                                    ("quick", "speedy and quick"), ("white ", "")]):
        out = tmp_path / f"out{n}.pdf"
        after = line.replace(old, new, 1)
        change(cur, out, line, after, words="jumps over")
        cur, line = out, after
        assert "".join(c[0] for c in chars(cur)) == (line + STAYS).replace(" ", "")
        assert found(cur, line) == 1
    assert filled_boxes(cur) == 0 and stays(tmp_path / "a.pdf", cur)


def test_a_deleted_line_is_gone_from_the_file(tmp_path):
    src, out = plain(tmp_path / "a.pdf"), tmp_path / "out.pdf"
    change(src, out, FOX, "")
    assert found(out, "quick") == 0 and filled_boxes(out) == 0 and stays(src, out)


def test_words_sharing_a_piece_of_text_with_the_line_stay_where_they_are(tmp_path):
    src = raw_page(tmp_path / "shared.pdf",
                   "BT /F1 11 Tf 14 TL 72 720 Td [(First line of the paragraph is here) -9000 (Side note A)] TJ T* "
                   "[(and the second line of it ends here.) -9200 (Side note B)] TJ ET")
    out = tmp_path / "out.pdf"
    report = change(src, out, "First line of the paragraph is here", "First line of the statement is here")
    assert "covered" not in report
    assert found(out, "paragraph") == 0 and found(out, "First line of the statement is here") == 1
    assert stays(src, out, "Side note A") and stays(src, out, "Side note B") and stays(src, out, "and the second line")
    # A paragraph change too: the notes beside it aren't taken out with it.
    rect, second = line_rect(src, "First line"), line_rect(src, "and the second")
    rect = [rect[0], second[1], max(rect[2], second[2]), rect[3]]
    textedit.edit_text(src, out, [{"page": 1, "rect": rect, "text": "Short now.", "paragraph": True}])
    assert found(out, "second line") == 0 and found(out, "Short now.") == 1 and filled_boxes(out) == 0
    assert stays(src, out, "Side note A") and stays(src, out, "Side note B")


def test_text_inside_a_form_drawn_twice_changes_only_where_it_was_changed(tmp_path):
    def form(writer, page):
        xobj = DecodedStreamObject()
        xobj.set_data(f"BT /F1 12 Tf 0 0 Td ({FOX}) Tj ET".encode())
        xobj.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                     NameObject("/BBox"): ArrayObject([NumberObject(v) for v in (0, -5, 400, 15)]),
                     NameObject("/Resources"): DictionaryObject({NameObject("/Font"): page["/Resources"]["/Font"]})})
        page["/Resources"][NameObject("/XObject")] = DictionaryObject({NameObject("/Fm1"): writer._add_object(xobj)})

    src = raw_page(tmp_path / "form.pdf", "q 1 0 0 1 72 700 cm /Fm1 Do Q q 1 0 0 1 72 600 cm /Fm1 Do Q "
                   f"BT /F1 12 Tf 72 500 Td ({STAYS}) Tj ET", more=form)
    out = tmp_path / "out.pdf"
    assert found(src, "brown") == 2
    rect = [70, 695, 400, 714]
    report = []
    textedit.edit_text(src, out, [{"page": 1, "rect": rect, "text": FOX.replace("brown", "black"), "old": FOX}],
                       report=report)
    assert "covered" not in report[0] and filled_boxes(out) == 0
    assert found(out, "brown") == 1 and found(out, "quick black fox") == 1
    low = [c for c in chars(out) if abs(c[2] - 600) < 1]
    assert "".join(c[0] for c in low) == FOX.replace(" ", "")  # the other drawing of the form: as it was
    assert stays(src, out)


# -- 2. the new words are text, in their place ---------------------------------------------------------

def test_tagged_content_stays_inside_its_tag(tmp_path):
    def tags(writer, page):
        root = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/StructTreeRoot")}))
        writer._root_object[NameObject("/StructTreeRoot")] = root
        page[NameObject("/StructParents")] = NumberObject(0)

    src = raw_page(tmp_path / "tagged.pdf", f"/P <</MCID 0>> BDC BT /F1 12 Tf 72 720 Td ({FOX}) Tj ET EMC "
                   f"/P <</MCID 1>> BDC BT /F1 12 Tf 72 690 Td ({STAYS}) Tj ET EMC", more=tags)
    out = tmp_path / "out.pdf"
    change(src, out, FOX, FOX.replace("brown", "chestnut-coloured"))
    data = content(out)
    assert data.count("BDC") == 2 and data.count("EMC") == 2
    first = data[data.index("BDC"):data.index("EMC")]
    assert "TJ" in first and data.rstrip().endswith("EMC")  # nothing written after the tagged content
    assert "/StructTreeRoot" in PdfReader(str(out)).trailer["/Root"]
    assert found(out, "The quick chestnut-coloured fox") == 1 and found(out, "brown") == 0


def test_letters_the_font_lacks_are_written_as_text_in_a_font_put_in_the_file(tmp_path):
    src, out = plain(tmp_path / "a.pdf"), tmp_path / "out.pdf"
    new = FOX.replace("brown", "brőwn€")
    report = change(src, out, FOX, new)
    assert report["kind"] in ("similar", "installed")
    assert found(out, new) == 1  # one run of text, not letters set down one by one
    assert filled_boxes(out) == 0 and found(out, "brown") == 0 and stays(src, out)
    used = {str(f.get_object()["/BaseFont"]): f.get_object() for f in PdfReader(str(out)).pages[0]["/Resources"]["/Font"].values()}
    extra = [f for name, f in used.items() if name != "/Helvetica"]
    assert extra and all("/ToUnicode" in f for f in extra)


def test_words_in_a_mixed_line_are_found_as_one_phrase(tmp_path):
    src = raw_page(tmp_path / "mixed.pdf", "BT /F1 12 Tf 72 720 Td (Payment is ) Tj /F2 12 Tf (strictly required) Tj "
                   "/F1 12 Tf ( within thirty days.) Tj ET", {"/F1": "Helvetica", "/F2": "Helvetica-Bold"})
    out = tmp_path / "out.pdf"
    old = "Payment is strictly required within thirty days."
    change(src, out, old, old.replace("required", "expected"))
    assert found(out, "Payment is strictly expected within thirty days.") == 1
    # Words added after the bold ones are bold too, and the plain ones after them stay plain.
    change(src, out, old, old.replace("required", "required and enforced"))
    assert found(out, "Payment is strictly required and enforced within thirty days.") == 1
    doc = pdfium.PdfDocument(str(out))
    textpage = doc[0].get_textpage()
    text = textpage.get_text_range()
    buf, flags = ctypes.create_string_buffer(256), ctypes.c_int()

    def face(word):
        pdfium_raw.FPDFText_GetFontInfo(textpage.raw, text.index(word), buf, 256, ctypes.byref(flags))
        return buf.value.decode()

    assert face("enforced") == "Helvetica-Bold" and face("within") == "Helvetica" and face("Payment") == "Helvetica"
    doc.close()


# -- 3. lines keep their anchor ----------------------------------------------------------------------------

def aligned(path):
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(306, 720, "Annual Report 2025")
    c.setFont("Helvetica", 11)
    c.drawRightString(540, 690, "Page 3 of 12")
    c.drawRightString(540, 670, "Total due: 1,204.50")
    c.drawString(72, 640, "Left line one")
    c.drawString(72, 620, "Left line two is longer")
    c.save()
    return path


def edges(path, words):
    key = words.replace(" ", "")
    found_ = chars(path)
    k = "".join(c[0] for c in found_).find(key)
    assert k >= 0, words
    return found_[k][1], found_[k + len(key) - 1][3]


def test_a_right_aligned_line_keeps_its_right_edge(tmp_path):
    src, out = aligned(tmp_path / "a.pdf"), tmp_path / "out.pdf"
    report = change(src, out, "Total due: 1,204.50", "Total due: 11,204.50")
    assert "covered" not in report and "wider" not in report
    left, right = edges(out, "Total due: 11,204.50")
    assert right == pytest.approx(540, abs=0.05)
    assert left == pytest.approx(540 - stringWidth("Total due: 11,204.50", "Helvetica", 11), abs=0.05)
    assert edges(out, "Page 3 of 12") == edges(src, "Page 3 of 12")
    # Shorter: the same.
    change(src, out, "Page 3 of 12", "Page 3 of 9")
    assert edges(out, "Page 3 of 9")[1] == pytest.approx(540, abs=0.05)


def test_a_centred_line_keeps_its_middle_and_a_left_one_its_left(tmp_path):
    src, out = aligned(tmp_path / "a.pdf"), tmp_path / "out.pdf"
    change(src, out, "Annual Report 2025", "Annual Financial Report 2025")
    left, right = edges(out, "Annual Financial Report 2025")
    assert (left + right) / 2 == pytest.approx(306, abs=0.05)
    change(src, out, "Left line one", "Left line number one")
    assert edges(out, "Left line number one")[0] == pytest.approx(72, abs=0.01)


def paragraph_page(path, below=True):
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica", 10)
    lines = ["The committee reviewed the annual budget on Tuesday", "and agreed that the proposed figures were broadly",
             "acceptable, although several members asked for a", "clearer breakdown of travel costs."]
    for n, line in enumerate(lines):
        c.drawString(72, 700 - 12 * n, line)
    if below:
        c.drawString(72, 700 - 12 * 4 - 8, "A second paragraph follows the first one.")
    for n in range(6):
        c.drawString(360, 700 - 12 * n, f"Right column line {n} stays put.")
    c.save()
    return path, lines


def fonts_of(path, page=0):
    """[(letter, x, y, font name, size)] of the letters pdfium reads."""
    doc = pdfium.PdfDocument(str(path))
    try:
        textpage = doc[page].get_textpage()
        buf, flags, out = ctypes.create_string_buffer(256), ctypes.c_int(), []
        for i in range(textpage.count_chars()):
            ch = textpage.get_text_range(i, 1)
            if ch.strip():
                pdfium_raw.FPDFText_GetFontInfo(textpage.raw, i, buf, 256, ctypes.byref(flags))
                out.append((ch, *(round(v, 2) for v in textedit._origin(textpage, i)), buf.value.decode(),
                            round(pdfium_raw.FPDFText_GetFontSize(textpage.raw, i), 2)))
        return out
    finally:
        doc.close()


def off_line(path, y):
    return [c for c in fonts_of(path) if abs(c[2] - y) > 3]


def test_a_change_of_one_line_of_a_paragraph_changes_that_line_only(tmp_path):
    (src, lines), out = paragraph_page(tmp_path / "p.pdf"), tmp_path / "out.pdf"
    # Longer than the paragraph is wide: it stays one line (no wrapping of the
    # lines around it), and nothing else on the page moves or changes font.
    longer = lines[0].replace("annual", "comprehensive annual and quarterly")
    report = change(src, out, lines[0], longer)
    assert "reflowed" not in report and "moved" not in report and "covered" not in report
    assert report.get("wider") is True  # it now reaches the right column: said
    assert off_line(out, 700) == off_line(src, 700)
    assert found(out, longer) == 1
    # A little longer, with room: not "wider".
    report = change(src, out, lines[3], lines[3].replace("costs", "expenses"))
    assert "wider" not in report and off_line(out, 664) == off_line(src, 664)
    # A line taken away leaves its space: the lines below stay where they are.
    report = change(src, out, lines[1], "")
    assert report == {"font": "", "kind": "same"}
    assert off_line(out, 688) == off_line(src, 688) and found(out, "proposed figures") == 0
    assert not [c for c in fonts_of(out) if abs(c[2] - 688) <= 3 and c[1] < 350]


def test_three_separate_lines_of_like_length_stay_three_lines(tmp_path):
    rows = ["Invoice number 20417 was issued on Monday", "Delivery is expected within thirty days.",
            "Payment is due at the end of the month."]
    src = raw_page(tmp_path / "rows.pdf", "BT /F1 12 Tf 72 700 Td ({}) Tj 0 -18 Td ({}) Tj 0 -18 Td ({}) Tj ET".format(*rows))
    out = tmp_path / "out.pdf"
    for old, new, y in ((rows[0], rows[0] + " morning, very early indeed", 700), (rows[1], "", 682),
                        (rows[1], rows[1].replace("thirty", "Ωμέγα"), 682)):
        report = change(src, out, old, new)
        assert "reflowed" not in report and "moved" not in report
        assert off_line(out, y) == off_line(src, y)


def tagged_lines(path, first):
    return raw_page(path, f"/P <</MCID 0>> BDC BT /F1 12 Tf 72 700 Td {first} ET EMC\n"
                    f"/P <</MCID 1>> BDC BT /F1 12 Tf 72 682 Td ({STAYS}) Tj ET EMC\n"
                    "/Span BMC BT /F1 12 Tf 72 664 Td (Pack my box with five dozen liquor jugs.) Tj ET EMC")


def sequences(path):
    """The text operators of each marked-content sequence of the page, in order."""
    data, out = content(path), []
    while "EMC" in data:
        head, data = data.split("EMC", 1)
        if "BDC" in head or "BMC" in head:
            out.append(head[head.index("BDC") if "BDC" in head else head.index("BMC"):])
    return out


def test_a_tags_replacement_text_is_changed_with_its_line(tmp_path):
    src = tagged_lines(tmp_path / "t.pdf", f"/Span <</ActualText ({FOX}) /Lang (en-GB)>> BDC ({FOX}) Tj EMC")
    out = tmp_path / "out.pdf"
    new = FOX.replace("brown", "black")
    report = change(src, out, FOX, new)
    assert "covered" not in report
    data = content(out)
    assert f"/ActualText ({new})" in data.replace("\\056", ".") and "brown" not in data and "/Lang" in data
    assert data.count("BDC") == 3 and data.count("BMC") == 1 and data.count("EMC") == 4
    assert off_line(out, 700) == off_line(src, 700)
    # Each of the other lines is still drawn inside its own tag.
    parts = sequences(out)
    assert "neighbouring" in parts[-2] and "Pack my box" in parts[-1]
    # A longer change (written as a line of its own): the same.
    report = change(src, out, FOX, "Now something else entirely.")
    assert "covered" not in report and "/ActualText (Now something else entirely" in content(out)


def test_a_tag_whose_replacement_text_is_only_part_of_the_line_is_left_alone(tmp_path):
    src = tagged_lines(tmp_path / "t.pdf", "/Span <</ActualText (The quick brown fox)>> BDC (The quick brown fox) Tj EMC "
                       "( jumps over the lazy dog.) Tj")
    out = tmp_path / "out.pdf"
    report = change(src, out, FOX, FOX.replace("lazy", "idle"))
    assert report.get("covered") is True  # can't be changed for sure: covered, and said
    data = content(out)
    assert "/ActualText (The quick brown fox)" in data
    parts = sequences(out)
    assert any("neighbouring" in part for part in parts) and any("Pack my box" in part for part in parts)  # no tag emptied
    assert off_line(out, 700) == off_line(src, 700)
    assert found(out, STAYS) == 1 and found(out, "Pack my box with five dozen liquor jugs.") == 1


def test_a_paragraph_whose_lines_have_tags_of_their_own_isnt_emptied_into_one(tmp_path):
    src = tagged_lines(tmp_path / "t.pdf", f"({FOX}) Tj")
    out = tmp_path / "out.pdf"
    report = []
    textedit.edit_text(src, out, [{"page": 1, "rect": [70, 660, 340, 712], "paragraph": True,
                                   "text": "One paragraph written in place of the three lines."}], report=report)
    assert report[0].get("covered") is True
    parts = sequences(out)
    assert "quick brown" in parts[0] and "neighbouring" in parts[1] and "Pack my box" in parts[2]


def test_only_the_letters_the_font_lacks_get_another_font(tmp_path):
    src, out = plain(tmp_path / "a.pdf"), tmp_path / "out.pdf"
    report = change(src, out, FOX, FOX.replace("brown", "brőwn"))
    assert report["kind"] in ("similar", "installed") and "missing" not in report
    line = {c[0]: c[3] for c in fonts_of(out) if abs(c[2] - 720) < 1}
    assert line["ő"] != "Helvetica" and all(line[ch] == "Helvetica" for ch in "brwnTqfx")
    assert off_line(out, 720) == off_line(src, 720)  # the other line: same letters, places, font
    assert found(out, FOX.replace("brown", "brőwn")) == 1
    # Every font of the page is one a reader has (a standard one) or is in the file with its letters' meanings.
    for font in PdfReader(str(out)).pages[0]["/Resources"]["/Font"].values():
        font = font.get_object()
        assert font["/Subtype"] != "/Type0" or "/ToUnicode" in font
        if font["/BaseFont"] != "/Helvetica":
            descriptor = (font["/DescendantFonts"][0].get_object() if font["/Subtype"] == "/Type0" else font)["/FontDescriptor"]
            assert any(k in descriptor.get_object() for k in ("/FontFile", "/FontFile2", "/FontFile3"))


def test_letters_no_font_here_can_draw_are_named_not_written_blind(tmp_path):
    src, out = plain(tmp_path / "a.pdf"), tmp_path / "out.pdf"
    odd = "\ue123"  # a private-use letter: no font has it
    report = change(src, out, FOX, FOX.replace("brown", "br" + odd + "wn Ωμέγα"))
    assert report["missing"] == odd
    assert found(out, "The quick brwn Ωμέγα fox jumps over the lazy dog.") == 1
    assert off_line(out, 720) == off_line(src, 720)
    used = [f.get_object() for f in PdfReader(str(out)).pages[0]["/Resources"]["/Font"].values()]
    assert not any(str(f["/BaseFont"]).lstrip("/") in textedit.READER_FONTS for f in used)
    # A paragraph change says so too.
    report = []
    textedit.edit_text(src, out, [{"page": 1, "rect": [70, 715, 330, 732], "paragraph": True, "text": "New " + odd + " words"}],
                       report=report)
    assert report[0]["missing"] == odd and found(out, "New words") == 1


def test_a_change_that_moves_another_line_is_caught_and_only_that_change_is_covered(tmp_path, monkeypatch):
    rows = ["First line of three on the page.", "Second line of three on the page.", "Third line of three on the page."]
    src = raw_page(tmp_path / "rows.pdf", "BT /F1 12 Tf 72 700 Td ({}) Tj 0 -18 Td ({}) Tj 0 -18 Td ({}) Tj ET".format(*rows))
    out = tmp_path / "out.pdf"
    real = textedit._Page._in_place

    def faulty(self, *args, **kwargs):
        """A fault planted in the new way: a change whose words say FAULT nudges the page's last line."""
        done = real(self, *args, **kwargs)
        if done and "FAULT" in args[-1]:
            self.flow.rises[self.flow.content.shows[-1]["i"]] = 0.7
        return done

    monkeypatch.setattr(textedit._Page, "_in_place", faulty)
    report = []
    textedit.edit_text(src, out, [
        {"page": 1, "rect": line_rect(src, rows[0]), "text": rows[0].replace("First", "Opening"), "old": rows[0]},
        {"page": 1, "rect": line_rect(src, rows[1]), "text": rows[1].replace("Second", "FAULT"), "old": rows[1]},
    ], report=report)
    assert "covered" not in report[0] and report[1].get("covered") is True
    third = lambda path: [c for c in fonts_of(path) if abs(c[2] - 664) < 1]  # noqa: E731
    assert third(out) == third(src) and len(third(src)) > 20  # the third line: untouched
    assert found(out, "Opening line of three") == 1 and found(out, "First line") == 0
    assert found(out, "FAULT line of three") == 1
    # The check itself: a letter in another font, a letter gone, one too many, the new words unreadable.
    good = tmp_path / "good.pdf"
    monkeypatch.undo()
    _, expect = textedit._edit(src, good, [{"page": 1, "rect": line_rect(src, rows[0]), "text": "Opening words", "old": rows[0]}],
                               None, [], frozenset())
    assert textedit._intact(good, expect)
    letters_ = expect[0]["letters"]
    for broken in (dict(expect[0], letters=[(*letters_[0][:3], b"Courier", letters_[0][4])] + letters_[1:]),
                   dict(expect[0], letters=[(*letters_[0][:4], 11.0)] + letters_[1:]),
                   dict(expect[0], letters=letters_ + [("Z", 300.0, 500.0, b"Helvetica", 12.0)]),
                   dict(expect[0], letters=letters_[1:]),
                   dict(expect[0], letters=letters_[1:2] + letters_[:1] + letters_[2:]),
                   dict(expect[0], texts=[(0, "Opening words and more")])):
        assert not textedit._intact(good, {0: broken})


def test_a_lone_line_that_runs_into_its_neighbour_says_so(tmp_path):
    src = raw_page(tmp_path / "cells.pdf", "BT /F1 10 Tf 72 700 Td (Steel bracket) Tj 100 0 Td (7.25) Tj ET")
    out = tmp_path / "out.pdf"
    report = change(src, out, "Steel bracket", "Stainless steel wall bracket, large", words="Steel bracket")
    assert report.get("wider") is True
    report = change(src, out, "Steel bracket", "Steel brackets", words="Steel bracket")
    assert "wider" not in report


# -- 5. the look that isn't in the font: outline, opacity ---------------------------------------------

def test_outlined_and_see_through_words_stay_so(tmp_path):
    def state(writer, page):
        gs = DictionaryObject({NameObject("/ca"): FloatObject(0.5)})
        page["/Resources"][NameObject("/ExtGState")] = DictionaryObject({NameObject("/GS1"): gs})

    src = raw_page(tmp_path / "look.pdf", "BT /F1 12 Tf 2 Tr 0.5 w 1 0 0 RG 72 720 Td (Outlined stroke and fill text) Tj 0 Tr ET "
                   "q /GS1 gs 0 0.5 0 rg BT /F1 12 Tf 72 690 Td (Half transparent green text.) Tj ET Q", more=state)
    out = tmp_path / "out.pdf"
    change(src, out, "Outlined stroke and fill text", "Outlined strokes and fills of text")
    new = [c for c in chars(out) if abs(c[2] - 720) < 1]
    assert "".join(c[0] for c in new) == "Outlinedstrokesandfillsoftext" and {c[4] for c in new} == {2}
    assert "1 0 0 RG" in content(out)
    change(src, out, "Half transparent green text.", "Half transparent bright green text.")
    new = [c for c in chars(out) if abs(c[2] - 690) < 1]
    assert "".join(c[0] for c in new) == "Halftransparentbrightgreentext." and {c[5] for c in new} == {(0, 128, 0, 128)}


def test_a_highlight_and_an_underline_by_the_words_are_left_alone(tmp_path):
    src = raw_page(tmp_path / "marks.pdf", "1 1 0 rg 105 716 62 14 re f 0 g BT /F1 12 Tf 72 720 Td (Some highlighted words in a line.) Tj ET "
                   "0.6 w 90 688 m 185 688 l S BT /F1 12 Tf 72 690 Td (An underlined phrase in a line.) Tj ET")
    out = tmp_path / "out.pdf"

    def yellow(path):
        doc = pdfium.PdfDocument(str(path))
        image = doc[0].render(scale=2).to_pil().convert("RGB")
        doc.close()
        return sum(1 for x in range(200, 340) for y in range(132, 152) if image.getpixel((x, y)) == (255, 255, 0))

    change(src, out, "Some highlighted words in a line.", "Some highlighted words in one line.")
    assert filled_boxes(out) == 1 and yellow(out) > 0.8 * yellow(src)  # not painted over
    change(src, out, "An underlined phrase in a line.", "An underlined phrase in one line.")
    assert "90 688 m" in content(out) and filled_boxes(out) == 1


def test_an_underline_and_a_highlight_follow_their_words(tmp_path):
    src = raw_page(tmp_path / "marks.pdf", "1 1 0 rg 105 716 62 14 re f 0 g BT /F1 12 Tf 72 720 Td (Some highlighted words in a line.) Tj ET "
                   "0.6 w 90 688 m 185 688 l S BT /F1 12 Tf 72 690 Td (An underlined phrase in a line.) Tj ET "
                   "0.5 w 60 650 m 400 650 l S BT /F1 12 Tf 72 652 Td (Words above a table line.) Tj ET")
    out = tmp_path / "out.pdf"
    # Words put in the underlined phrase: the line under it grows with it.
    change(src, out, "An underlined phrase in a line.", "An underlined longer phrase in a line.")
    grown = stringWidth("longer ", "Helvetica", 12)
    data = content(out)
    assert "90 688 m" in data and "185 688 l" not in data
    end = float(data[data.index("90 688 m"):].split()[3])
    assert end == pytest.approx(185 + grown, abs=0.05)
    assert edges(out, "phrase")[1] == pytest.approx(edges(src, "phrase")[1] + grown, abs=0.05)
    # Words put in before the highlighted one: the highlight moves along with it.
    change(src, out, "Some highlighted words in a line.", "Some very highlighted words in a line.")
    moved = stringWidth("very ", "Helvetica", 12)
    data = content(out)
    x, _, w = (float(v) for v in data[data.index("rg") + 2:data.index("re")].split()[:3])
    assert x == pytest.approx(105 + moved, abs=0.05) and w == pytest.approx(62, abs=0.05)
    # A rule that doesn't run from letter to letter (a table's line) stays as it is.
    change(src, out, "Words above a table line.", "More words above a table line.")
    assert "60 650 m" in content(out) and "400 650 l" in content(out)


# -- 6. ragged or justified --------------------------------------------------------------------------------

def test_a_ragged_line_with_kerned_gaps_isnt_squeezed(tmp_path):
    src = raw_page(tmp_path / "kern.pdf",
                   "BT /F1 14 Tf 72 720 Td [(T) 80 (o) -10 (da) 15 (y) -278 (A) 74 (V) 60 (A) -278 (T) 111 (ea) -278 (W) 80 (a) 20 (v) 20 (e) "
                   "-278 (party) 55 (.)] TJ 0 -30 Td [(A neighbouring line ) -20 (that must not move at all.)] TJ ET",
                   {"/F1": "Times-Roman"})
    out = tmp_path / "out.pdf"
    old = "Today AVA Tea Wave party."
    report = change(src, out, old, old.replace("AVA", "AVATAR WAVY"))
    assert "wider" not in report
    gap = lambda path: edges(path, "Wave")[0] - edges(path, "Tea")[1]  # noqa: E731
    assert gap(out) == pytest.approx(gap(src), abs=0.01)
    assert edges(out, "party.")[0] - edges(out, "Tea")[0] == pytest.approx(edges(src, "party.")[0] - edges(src, "Tea")[0], abs=0.01)
    assert found(out, "Today AVATAR WAVY Tea Wave party.") == 1


def test_a_line_in_a_font_of_the_file_isnt_judged_by_another_fonts_space(tmp_path):
    src = raw_page(tmp_path / "ragged.pdf", "BT /F1 12 Tf 72 720 Td (one two three four five six) Tj 0 -16 Td (a shorter line) Tj ET",
                   {"/F1": "Times-Roman"})
    out = tmp_path / "out.pdf"
    old = "one two three four five six"
    report = change(src, out, old, old.replace("two", "twő"))  # the new word gets another font
    assert report["kind"] != "same" and "wider" not in report
    gap = lambda path: edges(path, "five")[0] - edges(path, "four")[1]  # noqa: E731
    assert gap(out) == pytest.approx(gap(src), abs=0.01)


# -- 7. a paragraph that grows where nothing can move ----------------------------------------------------

def test_a_paragraph_that_cant_push_what_is_below_is_fitted_not_drawn_over_it(tmp_path):
    src, out = tmp_path / "full.pdf", tmp_path / "out.pdf"
    c = canvas.Canvas(str(src), pagesize=letter)
    c.setFont("Helvetica", 10)
    lines = [f"Line {n} of a column that fills the page down to its bottom margin" for n in range(56)]
    for n, line in enumerate(lines):
        c.drawString(72, 740 - 12 * n - (8 if n >= 3 else 0), line)
    c.save()
    rect = [70, 740 - 24 - 4, 450, 750]
    report = []
    textedit.edit_text(src, out, [{"page": 1, "rect": rect, "paragraph": True,
                                   "text": " ".join(lines[:3]) + " and then one more sentence to make it longer."}], report=report)
    assert report[0]["overflow"] is True and "moved" not in report[0]  # the reader's note, as before
    assert 7 <= report[0]["size"] < 10
    after = chars(out)
    new = [c for c in after if c[2] > 740 - 24 - 8 - 0.5 + 9]
    below = min(c[2] for c in new)
    assert below > 740 - 36 - 8 + 7  # its last line ends above the next line's letters
    assert "onemoresentencetomakeitlonger." in "".join(c[0] for c in after)
    assert stays(src, out, lines[3]) and stays(src, out, lines[55])


# -- 8. fonts ---------------------------------------------------------------------------------------------------

def test_a_font_not_in_the_file_with_renamed_codes_is_used_again(tmp_path):
    letters_ = sorted(set(FOX + STAYS + "calk"))
    names = {" ": "space", ".": "period"}
    code = {ch: 0x80 + i for i, ch in enumerate(letters_)}

    def custom(writer, page):
        font = std_font("Times-Roman", None)
        font[NameObject("/Encoding")] = DictionaryObject({
            NameObject("/Type"): NameObject("/Encoding"),
            NameObject("/Differences"): ArrayObject([NumberObject(0x80)] + [NameObject("/" + names.get(ch, ch)) for ch in letters_])})
        font[NameObject("/FirstChar")] = NumberObject(0x80)
        font[NameObject("/LastChar")] = NumberObject(0x80 + len(letters_) - 1)
        font[NameObject("/Widths")] = ArrayObject([NumberObject(int(stringWidth(ch, "Times-Roman", 1000))) for ch in letters_])
        page["/Resources"]["/Font"][NameObject("/F1")] = writer._add_object(font)

    h = lambda s: "<" + "".join(f"{code[c]:02X}" for c in s) + ">"  # noqa: E731
    src = raw_page(tmp_path / "custom.pdf", f"BT /F1 12 Tf 72 720 Td {h(FOX)} Tj 0 -30 Td {h(STAYS)} Tj ET", more=custom)
    out = tmp_path / "out.pdf"
    report = change(src, out, FOX, FOX.replace("brown", "black"))
    assert report == {"font": "Times Roman", "kind": "same"}
    assert found(out, "The quick black fox") == 1 and stays(src, out)
    # A letter the font's widths don't cover isn't written with it.
    report = change(src, out, FOX, FOX.replace("brown", "red-brown"))
    assert report["kind"] != "same" and found(out, "red-brown") == 1


def test_a_serif_line_gets_a_serif_font_for_letters_it_lacks(tmp_path):
    if not any(fonts.installed_family(f, False, False, "ő") for f in fonts.EMBEDDABLE["serif"]):
        pytest.skip("no serif font with those letters installed")
    src = raw_page(tmp_path / "serif.pdf", f"BT /F1 12 Tf 72 720 Td ({FOX}) Tj 0 -30 Td ({STAYS}) Tj ET", {"/F1": "Times-Roman"})
    out = tmp_path / "out.pdf"
    report = change(src, out, FOX, FOX.replace("brown", "brőwn"))
    assert report["kind"] == "similar" and "serif" in report["font"].lower().replace(" ", "") or "times" in report["font"].lower()


# -- files changed the old way (words under a cover, new ones on top) ---------------------------------------

def test_a_line_covered_by_an_earlier_version_is_cleaned_up_when_changed_again(tmp_path, monkeypatch):
    src, old_way, out = plain(tmp_path / "a.pdf"), tmp_path / "old.pdf", tmp_path / "out.pdf"
    with monkeypatch.context() as patch:
        patch.setattr(textedit._Page, "_in_place", lambda *a, **k: False)
        report = change(src, old_way, FOX, FOX.replace("brown", "black"))
    assert report.get("covered") is True and filled_boxes(old_way) == 1 and found(old_way, "brown") == 1
    line = FOX.replace("brown", "black")
    report = change(old_way, out, line, line.replace("quick", "very quick"), words="jumps over")
    assert "covered" not in report
    assert filled_boxes(out) == 0 and found(out, "brown") == 0
    assert found(out, "The very quick black fox jumps over the lazy dog.") == 1
    # No letter twice: the old line's letters that were under the cover are gone.
    assert sorted(c[0] for c in chars(out)) == sorted((line.replace("quick", "very quick") + STAYS).replace(" ", ""))
    assert stays(src, out)


def test_a_change_that_would_lose_other_words_is_made_the_old_way_instead(tmp_path, monkeypatch):
    from pdfmanager import textflow

    src = raw_page(tmp_path / "shared.pdf",
                   "BT /F1 11 Tf 14 TL 72 720 Td [(First line of the paragraph is here) -9000 (Side note A)] TJ T* "
                   "[(and the second line of it ends here.) -9200 (Side note B)] TJ ET")
    out = tmp_path / "out.pdf"
    # A faulty rewrite of the piece of text, which drops the side note with the line...
    monkeypatch.setattr(textflow.Flow, "_rebuilt", lambda self, edit, names, dy, rise: [])
    report = change(src, out, "First line of the paragraph is here", "First line of the statement is here")
    # ... is caught when the result is read back, and the change made by covering instead.
    assert report.get("covered") is True
    assert stays(src, out, "Side note A") and stays(src, out, "Side note B") and found(out, "statement") == 1
    # So is one that fails outright.
    monkeypatch.setattr(textflow.Flow, "write", lambda self, writer, page: 1 / 0)
    report = change(src, out, "First line of the paragraph is here", "First line of the statement is here")
    assert report.get("covered") is True and stays(src, out, "Side note A") and found(out, "statement") == 1
    assert not list(tmp_path.glob("*.check"))  # nothing left behind


def test_right_to_left_lines_are_changed_as_before(tmp_path):
    name = fonts.font_for("שלום")
    if name in textedit.BASE14_NAMES:
        pytest.skip("no font with Hebrew letters installed")
    src, out = tmp_path / "rtl.pdf", tmp_path / "out.pdf"
    c = canvas.Canvas(str(src), pagesize=letter)
    c.setFont(name, 14)
    c.drawString(72, 700, "םלוע םולש")
    c.setFont("Helvetica", 12)
    c.drawString(72, 660, STAYS)
    c.save()
    report = []
    textedit.edit_text(src, out, [{"page": 1, "rect": [70, 695, 300, 716], "text": "שלום רב"}], report=report)
    assert report[0].get("covered") is True and stays(src, out)
