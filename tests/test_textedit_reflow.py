"""Text editing like Acrobat's: a font picker for edited and new words, and
text that flows (paragraphs below a rewritten one move down or up)."""

import ctypes
import json

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdfmanager import core, fonts, textedit

LEFT = [["Alpha paragraph one has a few lines of text that wrap", "inside the left column and keep on going for a",
         "while longer until it stops here."],
        ["Second paragraph sits below the first one with", "a normal paragraph gap between them both."],
        ["Third paragraph is the last one in this column", "of the page."]]
LONGER = ("Alpha paragraph one now says a lot more than it did before, so that it takes quite a few more lines "
          "in the left column than it used to, pushing the rest of the column down the page.")
FIRST_RECT = [70, 700 - 24 - 4, 312, 710]  # around the first paragraph


def columns(path, right=True, picture=False, rule=True, size=10, lead=12):
    """Three paragraphs in a left column (a rule under them), and a right
    column of lines that must never move."""
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica", size)
    y = 700
    for para in LEFT:
        for line in para:
            c.drawString(72, y, line)
            y -= lead
        y -= 8
    if rule:
        c.setLineWidth(0.5)
        c.line(72, y + 4, 290, y + 4)
    if picture:
        c.drawImage(ImageReader(Image.new("RGB", (40, 30), (200, 30, 30))), 72, y - 120, 150, 100)
        c.drawImage(ImageReader(Image.new("RGB", (40, 30), (30, 30, 200))), 320, 400, 150, 100)
    if right:
        for n in range(12):
            c.drawString(320, 700 - 12 * n, f"Right column line {n} stays exactly where it is")
    c.save()
    return path


def letters(path, page=0):
    """[(letter, x, y)] of every letter pdfium reads, in reading order."""
    doc = pdfium.PdfDocument(str(path))
    try:
        textpage = doc[page].get_textpage()
        out = []
        for i in range(textpage.count_chars()):
            ch = textpage.get_text_range(i, 1)
            if ch.strip():
                x, y = textedit._origin(textpage, i)
                out.append((ch, x, y))
        return out
    finally:
        doc.close()


def where(path, words, page=0):
    """(x, y) of the first letter of ``words`` (spaces left out), or None."""
    found = letters(path, page)
    joined = "".join(c for c, *_ in found)
    k = joined.find(words.replace(" ", ""))
    return None if k < 0 else found[k][1:]


def text_of(path, page=0):
    return "".join(c for c, *_ in letters(path, page))


def edit(src, out, edit, **extra):
    report = []
    textedit.edit_text(src, out, [{"page": 1, **edit, **extra}], report=report)
    return report[0]


def drawn(path, word, page=0):
    """(font name, (r, g, b) 0-255) pdfium says ``word`` is drawn with."""
    doc = pdfium.PdfDocument(str(path))
    try:
        textpage = doc[page].get_textpage()
        start = textpage.get_text_range().find(word)
        assert start >= 0, word
        buf, flags = ctypes.create_string_buffer(256), ctypes.c_int()
        pdfium_raw.FPDFText_GetFontInfo(textpage.raw, start, buf, 256, ctypes.byref(flags))
        rgba = [ctypes.c_uint() for _ in range(4)]
        pdfium_raw.FPDFText_GetFillColor(textpage.raw, start, *map(ctypes.byref, rgba))
        size = pdfium_raw.FPDFText_GetFontSize(textpage.raw, start)
        return buf.value.decode(), tuple(v.value for v in rgba[:3]), size
    finally:
        doc.close()


def page_fonts(path, page=0):
    """{base font name: embedded?} of the fonts on a page (pypdf)."""
    out = {}
    for font in textedit._page_fonts(PdfReader(str(path)).pages[page]):
        font = font.get_object()
        descriptor = font.get("/FontDescriptor")
        if font.get("/Subtype") == "/Type0":
            descriptor = font["/DescendantFonts"][0].get_object().get("/FontDescriptor")
        descriptor = descriptor.get_object() if descriptor is not None else {}
        out[fonts.strip_subset(font["/BaseFont"])] = any(k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3"))
    return out


# -- text that flows -------------------------------------------------------------------------------

def test_a_longer_paragraph_pushes_the_ones_below_it_down(tmp_path):
    src, out = columns(tmp_path / "two.pdf"), tmp_path / "out.pdf"
    report = edit(src, out, {"rect": FIRST_RECT, "text": LONGER, "paragraph": True})
    assert report["kind"] == "same" and report["moved"] == 4 and "overflow" not in report
    # One more line (12 points): the two paragraphs below move down by just that.
    for words in ("Second paragraph", "a normal paragraph", "Third paragraph", "of the page."):
        (x0, y0), (x1, y1) = where(src, words), where(out, words)
        assert abs(x1 - x0) < 0.01 and abs((y0 - y1) - 12) < 0.01, words
    # The other column hasn't moved a hair.
    before = [L for L in letters(src) if L[1] >= 315]
    assert [L for L in letters(out) if L[1] >= 315] == before
    # The old words are gone (not just covered), the new ones are all there.
    text = text_of(out)
    assert "Alphaparagraphonehasafew" not in text and LONGER.replace(" ", "") in text
    # The rule under the column moved with it.
    assert _rule_y(out) == pytest.approx(_rule_y(src) - 12, abs=0.05)


def test_a_paragraph_in_the_right_column_moves_only_that_column(tmp_path):
    src, out = columns(tmp_path / "two.pdf"), tmp_path / "out.pdf"
    report = edit(src, out, {"rect": [318, 685, 540, 710], "paragraph": True,
                             "text": "Right column lines 0 and 1 are now one paragraph that is long enough to need "
                                     "three or four lines in this column."})
    assert report["moved"] == 10
    assert [L for L in letters(out) if L[1] < 315] == [L for L in letters(src) if L[1] < 315]
    grown = where(src, "Right column line 2")[1] - where(out, "Right column line 2")[1]
    assert grown > 11 and where(src, "Right column line 11")[1] - where(out, "Right column line 11")[1] == grown


def test_a_shorter_paragraph_pulls_the_ones_below_it_up(tmp_path):
    src, out = columns(tmp_path / "two.pdf"), tmp_path / "out.pdf"
    report = edit(src, out, {"rect": FIRST_RECT, "text": "Alpha is short now.", "paragraph": True})
    assert report["moved"] == 4
    for words in ("Second paragraph", "Third paragraph", "of the page."):
        assert where(out, words)[1] - where(src, words)[1] == pytest.approx(24, abs=0.01)  # two lines up
    assert [L for L in letters(out) if L[1] >= 315] == [L for L in letters(src) if L[1] >= 315]
    # Nothing left of the old paragraph shows or reads under the moved lines.
    assert "whilelonger" not in text_of(out)
    doc = pdfium.PdfDocument(str(out))
    image = doc[0].render(scale=1).to_pil().convert("L")
    doc.close()
    second = where(out, "Second paragraph")
    assert image.crop((72, int(792 - second[1] - 8), 200, int(792 - second[1] + 2))).getextrema()[0] < 100  # it shows


def test_taking_a_paragraph_away_closes_the_gap(tmp_path):
    src, out = columns(tmp_path / "two.pdf"), tmp_path / "out.pdf"
    edit(src, out, {"rect": FIRST_RECT, "text": "", "paragraph": True})
    assert where(out, "Second paragraph") == pytest.approx(where(src, "Alpha paragraph"), abs=0.01)
    assert "Alpha" not in text_of(out)


def _rule_y(path):
    """The y of the thin horizontal line on the page (pdfium)."""
    doc = pdfium.PdfDocument(str(path))
    try:
        page = doc[0]
        for i in range(pdfium_raw.FPDFPage_CountObjects(page.raw)):
            obj = pdfium_raw.FPDFPage_GetObject(page.raw, i)
            if pdfium_raw.FPDFPageObj_GetType(obj) != pdfium_raw.FPDF_PAGEOBJ_PATH:
                continue
            box = [ctypes.c_float() for _ in range(4)]
            pdfium_raw.FPDFPageObj_GetBounds(obj, *map(ctypes.byref, box))
            x0, y0, x1, y1 = (v.value for v in box)
            if x1 - x0 > 100 and y1 - y0 < 3:
                return (y0 + y1) / 2
    finally:
        doc.close()
    return None


def _images(path):
    """[(x0, y0, x1, y1)] of the pictures on page 1."""
    doc = pdfium.PdfDocument(str(path))
    try:
        page, found = doc[0], []
        for i in range(pdfium_raw.FPDFPage_CountObjects(page.raw)):
            obj = pdfium_raw.FPDFPage_GetObject(page.raw, i)
            if pdfium_raw.FPDFPageObj_GetType(obj) == pdfium_raw.FPDF_PAGEOBJ_IMAGE:
                box = [ctypes.c_float() for _ in range(4)]
                pdfium_raw.FPDFPageObj_GetBounds(obj, *map(ctypes.byref, box))
                found.append(tuple(round(v.value, 2) for v in box))
        return sorted(found)
    finally:
        doc.close()


def test_pictures_stay_and_text_stops_above_them(tmp_path):
    src, out = columns(tmp_path / "pic.pdf", picture=True), tmp_path / "out.pdf"
    report = edit(src, out, {"rect": FIRST_RECT, "text": LONGER, "paragraph": True})
    assert report["moved"] == 4
    assert _images(out) == _images(src)
    picture_top = max(b[3] for b in _images(src) if b[0] < 300)
    assert min(y for _, x, y in letters(out) if x < 300) > picture_top  # moved text stays clear of it
    # Grown far past what's left above the picture: nothing moves, and it says so.
    much = " ".join([LONGER] * 3)
    report = edit(src, out, {"rect": FIRST_RECT, "text": much, "paragraph": True})
    assert report.get("overflow") is True and "moved" not in report
    assert where(out, "Second paragraph") == where(src, "Second paragraph")
    assert much.replace(" ", "") in text_of(out)  # no word lost
    assert _images(out) == _images(src)


def test_text_that_would_run_off_the_page_isnt_moved_and_nothing_is_lost(tmp_path):
    src, out = tmp_path / "full.pdf", tmp_path / "out.pdf"
    c = canvas.Canvas(str(src), pagesize=letter)
    c.setFont("Helvetica", 10)
    lines = [f"Line {n} of a column that fills the page down to its bottom margin" for n in range(56)]
    for n, line in enumerate(lines):
        c.drawString(72, 740 - 12 * n - (8 if n >= 3 else 0), line)
    c.save()
    rect = [70, 740 - 24 - 4, 450, 750]
    before = text_of(src)
    report = edit(src, out, {"rect": rect, "text": " ".join(["A paragraph much longer than before."] * 12),
                             "paragraph": True})
    assert report["overflow"] is True and "moved" not in report
    after = text_of(out)
    for line in lines[3:]:
        assert line.replace(" ", "") in after  # every line still there, where it was
    assert where(out, "Line 55") == where(src, "Line 55")
    assert len(after) > len(before)
    # With "shrink", a paragraph a little too long comes down in size to fit.
    a_bit_more = " ".join(lines[:3]) + " plus more"
    report = edit(src, out, {"rect": rect, "text": a_bit_more, "paragraph": True})
    assert report["overflow"] is True
    report = edit(src, out, {"rect": rect, "text": a_bit_more, "paragraph": True, "fit": "shrink"})
    assert "overflow" not in report and 7.5 <= report["size"] < 10
    assert drawn(out, "plus more")[2] == pytest.approx(report["size"], abs=0.01)
    assert a_bit_more.replace(" ", "") in text_of(out)
    # Smaller, it ends a little higher up: the lines below follow it, none goes lower.
    assert 0 <= where(out, "Line 55")[1] - where(src, "Line 55")[1] < 4


def test_a_justified_paragraph_stays_justified_when_it_grows(tmp_path):
    src, out = tmp_path / "just.pdf", tmp_path / "out.pdf"
    words = ("Justified text fills each line to the right edge by stretching the gaps between words so both "
             "edges are straight and the paragraph looks neat on the page.").split()
    from reportlab.pdfbase.pdfmetrics import stringWidth

    c = canvas.Canvas(str(src), pagesize=letter)
    y, line, width = 700, [], 300
    rows = []
    for word in words:
        if line and stringWidth(" ".join(line + [word]), "Times-Roman", 11) > width:
            rows.append(line)
            line = []
        line.append(word)
    rows.append(line)
    for k, row in enumerate(rows):
        text = c.beginText(72, y)
        text.setFont("Times-Roman", 11)
        if k < len(rows) - 1:
            text.setWordSpace((width - stringWidth(" ".join(row), "Times-Roman", 11)) / (len(row) - 1))
        text.textLine(" ".join(row))
        c.drawText(text)
        y -= 13
    c.setFont("Times-Roman", 11)
    c.drawString(72, y - 6, "A last paragraph under it moves too.")
    c.save()
    rect = [70, 700 - 13 * (len(rows) - 1) - 4, 72 + width + 2, 711]
    new = " ".join(words) + " It now has a whole extra sentence, which needs at least one more line to fit in."
    report = edit(src, out, {"rect": rect, "text": new, "paragraph": True})
    assert report["moved"] == 1
    rows_out = _rows_of(out, x_max=400)
    body = [r for r in rows_out if r[0][2] > 680 - 13 * 6][:-2]  # the new paragraph but its last line
    assert len(body) >= 3
    for r in body:
        assert r[0][1] == pytest.approx(72, abs=0.6) and r[-1][3] == pytest.approx(72 + width, abs=1.0)
    assert where(src, "A last paragraph")[1] - where(out, "A last paragraph")[1] >= 12.9


def _rows_of(path, x_max):
    """Lines of letters: [[(letter, left, baseline, right)]], top to bottom."""
    doc = pdfium.PdfDocument(str(path))
    try:
        tp = doc[0].get_textpage()
        rows = {}
        for i in range(tp.count_chars()):
            ch = tp.get_text_range(i, 1)
            if not ch.strip():
                continue
            box = tp.get_charbox(i)
            x, y = textedit._origin(tp, i)
            if x < x_max:
                rows.setdefault(round(y, 1), []).append((ch, box[0], y, box[2]))
        return [sorted(r, key=lambda t: t[1]) for _, r in sorted(rows.items(), reverse=True)]
    finally:
        doc.close()


def test_lines_in_a_font_whose_byte_32_is_a_letter_move_too(tmp_path):
    """Office programs cut fonts down and number the letters anew: byte 32
    can be "S" (word spacing still applies to it). Such a line still moves,
    letter for letter."""
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NumberObject
    from reportlab.pdfbase.pdfmetrics import stringWidth

    widths = [stringWidth(chr(c), "Helvetica", 1000) for c in range(32, 127)]
    widths[0], widths[ord("S") - 32] = widths[ord("S") - 32], widths[0]  # "S" and the space swap codes
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    swapped = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/ABCDEF+Helvetica"),
        NameObject("/FirstChar"): NumberObject(32), NameObject("/LastChar"): NumberObject(126),
        NameObject("/Widths"): ArrayObject([FloatObject(w) for w in widths]),
        NameObject("/Encoding"): DictionaryObject({NameObject("/Differences"): ArrayObject(
            [NumberObject(32), NameObject("/S"), NumberObject(83), NameObject("/space")])})}))
    plain = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")}))
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject(
        {NameObject("/F1"): plain, NameObject("/F2"): swapped})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 10 Tf 72 700 Td (A first paragraph of two lines that is) Tj 0 -12 Td (about to grow.) Tj ET\n"
                    b"BT /F2 10 Tf 2 Tw 72 668 Td (\x20alesSgrewSinS\x20pringSandS\x20ummer) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    src, out = tmp_path / "swap.pdf", tmp_path / "out.pdf"
    writer.write(str(src))
    assert "SalesgrewinSpringandSummer" in text_of(src)
    report = edit(src, out, {"rect": [70, 684, 300, 710], "paragraph": True,
                             "text": "A first paragraph of two lines that is about to grow by a whole line or so."})
    assert report["moved"] == 1
    before = [L for L in letters(src) if L[2] < 680]
    after = [L for L in letters(out) if L[2] < 670]  # under the paragraph, now three lines
    assert [c for c, *_ in after] == [c for c, *_ in before]
    assert all(abs(a[1] - b[1]) < 0.01 and abs((b[2] - a[2]) - 12) < 0.01 for a, b in zip(after, before))


def test_a_page_flowed_once_flows_again(tmp_path):
    src, once, twice, third = columns(tmp_path / "two.pdf"), tmp_path / "1.pdf", tmp_path / "2.pdf", tmp_path / "3.pdf"
    edit(src, once, {"rect": FIRST_RECT, "text": LONGER, "paragraph": True})  # four lines now
    report = edit(once, twice, {"rect": [70, 660, 312, 710], "text": "Alpha back to one line.", "paragraph": True})
    assert report["moved"] == 4
    for words in ("Second paragraph", "of the page."):
        assert where(twice, words)[1] - where(src, words)[1] == pytest.approx(24, abs=0.01)
    assert _rule_y(twice) == pytest.approx(_rule_y(src) + 24, abs=0.05)
    assert "Alphaparagraphonenowsays" not in text_of(twice)  # the first rewrite is gone too
    second = where(twice, "Second paragraph")
    report = edit(twice, third, {"rect": [70, second[1] - 16, 312, second[1] + 10], "paragraph": True,
                                 "text": "Second paragraph is now longer than it was, so that it wraps onto three "
                                         "lines in the column and moves the third one."})
    assert report["moved"] == 2 and where(third, "Third paragraph")[1] == pytest.approx(where(twice, "Third paragraph")[1] - 12)
    assert [L for L in letters(third) if L[1] >= 315] == [L for L in letters(src) if L[1] >= 315]


def test_the_last_paragraph_of_a_column_runs_on_into_the_next(tmp_path):
    src, out = tmp_path / "link.pdf", tmp_path / "out.pdf"
    c = canvas.Canvas(str(src), pagesize=letter)
    c.setFont("Helvetica", 10)
    for n in range(49):
        c.drawString(72, 700 - 12 * n, f"Left column filler line number {n} of the long text")
    last = ["The last paragraph of the left column is here,", "and it ends right at the bottom margin of", "the page."]
    for n, line in enumerate(last):
        c.drawString(72, 104 - 12 * n, line)
    for n in range(10):
        c.drawString(320, 700 - 12 * n, f"Right column line {n} has room below it")
    c.save()
    text = " ".join(last) + (" It now goes on for much longer, so the words that don't fit any more continue at "
                             "the top of the next column, as in a linked box.")
    report = edit(src, out, {"rect": [70, 76, 300, 114], "text": text, "paragraph": True})
    assert report["linked"] == 3 and report["moved"] == 10 and "overflow" not in report
    assert text.replace(" ", "") in text_of(out)  # every word, in order
    # The paragraph stays above the margin; the rest starts where the next column did.
    assert min(y for _, x, y in letters(out) if x < 300) >= 72
    assert where(out, "linked box.")[0] > 315
    rest = [L for L in letters(out) if L[1] >= 315]
    assert max(rest, key=lambda L: (L[2], -L[1]))[1:] == (320.0, 700.0)
    # That column moved down by the lines put in it and half a line.
    assert where(src, "Right column line 0")[1] - where(out, "Right column line 0")[1] == pytest.approx(42, abs=0.01)
    assert where(out, "Right column line 9")[0] == where(src, "Right column line 9")[0]
    # With no room in the next column either, nothing moves, and it says so.
    report = edit(src, out, {"rect": [70, 76, 300, 114], "paragraph": True, "text": " ".join([text] * 14)})
    assert report["overflow"] is True and "linked" not in report
    assert where(out, "Right column line 0") == where(src, "Right column line 0")


def test_flow_can_be_turned_off(tmp_path):
    src, out = columns(tmp_path / "two.pdf"), tmp_path / "out.pdf"
    report = edit(src, out, {"rect": FIRST_RECT, "text": LONGER, "paragraph": True, "flow": False})
    assert report.get("taller") is True and "moved" not in report
    assert where(out, "Second paragraph") == where(src, "Second paragraph")


def test_a_one_line_change_still_keeps_every_letter_where_it_was(tmp_path):
    src, out = columns(tmp_path / "two.pdf"), tmp_path / "out.pdf"
    edit(src, out, {"rect": [70, 650, 300, 666], "text": "Second paragraph sits below the 1st one with",
                    "old": "Second paragraph sits below the first one with"})
    before, after = letters(src), letters(out)
    assert [L for L in after if L[1] >= 315] == [L for L in before if L[1] >= 315]
    assert where(out, "Third paragraph") == where(src, "Third paragraph")
    start = [L for L in after if abs(L[2] - 656) < 0.1]
    assert [L for L in start[:31]] == [L for L in before if abs(L[2] - 656) < 0.1][:31]  # "Secondparagraphsitsbelowthe"


# -- how the new words look ------------------------------------------------------------------------

def test_picking_an_installed_font_size_colour_and_alignment(tmp_path):
    common, more = fonts.installed_families()
    bold_faces = {fonts.family_key(e[7]) for e in fonts._font_index() if e[3] and not e[4]}
    family = next((f for f in common + more if fonts.family_key(f) in bold_faces
                   and fonts.installed_family(f, True, False, "Pickedwordsinachosenlook")), None)
    if family is None:
        pytest.skip("no TrueType font with a bold face installed")
    src, out = columns(tmp_path / "two.pdf"), tmp_path / "out.pdf"
    report = edit(src, out, {"rect": FIRST_RECT, "text": "Picked words in a chosen look", "paragraph": True,
                             "font": family, "size": 14, "bold": True, "color": [0.8, 0.1, 0.1], "align": "right"})
    assert report["asked"] == family and report["matched"] is True and report["kind"] == "installed"
    name, color, size = drawn(out, "Picked")
    assert fonts.family_key(name) == fonts.family_key(family) and fonts.font_style(name)[0]
    assert color == (204, 26, 26) and size == pytest.approx(14, abs=0.05)
    embedded = page_fonts(out)
    assert any(fonts.family_key(n) == fonts.family_key(family) and e for n, e in embedded.items())  # embedded
    # Set to the right: it ends where the paragraph's longest line did.
    old_right = max(r[-1][3] for r in _rows_of(src, 310)[:3])
    row = [r for r in _rows_of(out, 310) if r[0][0] == "P"][0]
    assert row[-1][3] == pytest.approx(old_right, abs=1.5) and row[0][1] > 100


def test_picking_a_standard_font_and_bold_italic(tmp_path):
    src, out = columns(tmp_path / "two.pdf"), tmp_path / "out.pdf"
    report = edit(src, out, {"rect": FIRST_RECT, "text": "Now in Times", "paragraph": True, "font": "Times",
                             "italic": True})
    assert report["kind"] == "standard" and report["font"] == "Times Italic" and report["matched"] is True
    assert drawn(out, "Now in Times")[0] == "Times-Italic"
    assert page_fonts(out)["Times-Italic"] is False  # a standard font: not embedded, readers have it
    # Bold alone: the document's own font family, bold.
    report = edit(src, out, {"rect": FIRST_RECT, "text": "Bold words", "paragraph": True, "bold": True})
    assert drawn(out, "Bold words")[0] == "Helvetica-Bold" and report["matched"] is True


def vera_doc(path, pdfa=False):
    """Text in an embedded TrueType font (Vera); optionally marked PDF/A."""
    import reportlab
    from pathlib import Path
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    pdfmetrics.registerFont(TTFont("Vera", str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf")))
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Vera", 12)
    c.drawString(72, 700, "Words in the document's own font: abcdefghijklmnopqrstuvwxyz")
    if not pdfa:  # a PDF/A file has every font embedded
        c.setFont("Helvetica", 12)
    c.drawString(72, 600, "Another line in Helvetica")
    c.save()
    if pdfa:
        writer = PdfWriter(clone_from=str(path))
        xmp = DecodedStreamObject()
        xmp.set_data(b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
                     b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
                     b'xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/" pdfaid:part="2" pdfaid:conformance="B"/>'
                     b'</rdf:RDF></x:xmpmeta><?xpacket end="w"?>')
        xmp.update({NameObject("/Type"): NameObject("/Metadata"), NameObject("/Subtype"): NameObject("/XML")})
        writer._root_object[NameObject("/Metadata")] = writer._add_object(xmp)
        writer.write(str(path))
    return path


def test_picking_a_family_the_document_uses(tmp_path):
    src, out = vera_doc(tmp_path / "vera.pdf"), tmp_path / "out.pdf"
    choices = textedit.font_choices(src)
    assert choices["document"] == ["Bitstream Vera Sans", "Helvetica"] and "Helvetica" not in choices["standard"]
    vera = drawn(src, "Words")[0]
    # The Helvetica line rewritten in the document's Vera, with letters its copy has.
    report = edit(src, out, {"rect": [70, 595, 300, 612], "text": "Another fine day", "font": "Bitstream Vera Sans"})
    assert report["kind"] == "same" and report["matched"] is True
    assert drawn(out, "Another fine day")[0] == vera
    # New words with a chosen font, size and colour.
    report = edit(src, out, {"at": [72, 500], "text": "Added later", "font": "Bitstream Vera Sans", "size": 20,
                             "color": [0, 0.5, 0]})
    name, color, size = drawn(out, "Added later")
    assert name == vera and color == (0, 128, 0) and size == pytest.approx(20, abs=0.05)
    # Centred in a given width, wrapped in it.
    report = edit(src, out, {"at": [72, 500], "text": "one two three four five six seven", "width": 120,
                             "align": "center", "font": "Helvetica"})
    rows = [r for r in _rows_of(out, 400) if r[0][2] < 500 and r[0][2] > 440]
    assert len(rows) >= 2
    for r in rows:
        assert (r[0][1] + r[-1][3]) / 2 == pytest.approx(72 + 60, abs=3)


def test_a_pdfa_file_keeps_its_fonts_embedded(tmp_path):
    src, out = vera_doc(tmp_path / "a.pdf", pdfa=True), tmp_path / "out.pdf"
    assert textedit.font_choices(src)["pdfa"] is True
    report = edit(src, out, {"rect": [70, 595, 300, 612], "text": "Standard font asked for", "font": "Helvetica"})
    assert report["matched"] is False and report["reason"] == "pdfa"
    name = drawn(out, "Standard font")[0]
    assert page_fonts(out)[fonts.strip_subset(name)] is True
    # Unpicked changes that would have used a standard font are embedded too.
    report = edit(src, out, {"rect": [70, 595, 300, 612], "text": "Straße über", "old": "Another line in Helvetica"})
    name = drawn(out, "Stra")[0]
    assert report["kind"] != "same" and page_fonts(out)[fonts.strip_subset(name)] is True


@pytest.mark.parametrize("bad", [{"size": "big"}, {"size": 0}, {"color": [2, 0, 0]}, {"color": "red"},
                                 {"align": "middle"}, {"fit": "squash"}, {"font": 5}, {"width": -3}])
def test_bad_choices_are_a_plain_error(tmp_path, bad):
    src = columns(tmp_path / "two.pdf")
    with pytest.raises(core.PdfManagerError):
        textedit.edit_text(src, tmp_path / "out.pdf", [{"page": 1, "rect": FIRST_RECT, "text": "x", **bad}])


# -- the reader's endpoints ----------------------------------------------------------------------

def test_endpoints_take_the_new_choices(tmp_path, monkeypatch):
    import threading
    import urllib.request

    from pdfmanager import appdata, gui, recipes

    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(gui, "data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(recipes, "data_dir", lambda: tmp_path / "appdata")
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    try:
        src = columns(tmp_path / "two.pdf")

        def post(path, fields):
            boundary = "zpdfreflowtest"
            parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                     for k, v in fields.items()]
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="f.pdf"\r\n'
                         f'Content-Type: application/pdf\r\n\r\n'.encode() + src.read_bytes() + b"\r\n")
            body = b"".join(parts) + f"--{boundary}--\r\n".encode()
            req = urllib.request.Request(base + path, data=body, headers={
                "X-Pdfm-Token": token, "Content-Type": f"multipart/form-data; boundary={boundary}"})
            try:
                with urllib.request.urlopen(req) as res:
                    return res.status, dict(res.headers), res.read()
            except urllib.error.HTTPError as err:
                return err.code, dict(err.headers), err.read()

        status, _, data = post("/api/text/fonts", {})
        choices = json.loads(data)
        assert status == 200 and choices["document"] == ["Helvetica"] and "Times" in choices["standard"]
        edits = [{"page": 1, "rect": FIRST_RECT, "text": LONGER, "paragraph": True, "font": "Times", "size": 12,
                  "color": [0, 0, 1], "align": "justify"}]
        status, headers, data = post("/api/text/edit", {"edits": json.dumps(edits)})
        assert status == 200
        (tmp_path / "e.pdf").write_bytes(data)
        report = json.loads(headers["X-Pdfm-Fonts"])
        assert report[0]["font"] == "Times Roman" and report[0]["matched"] is True and report[0]["moved"] == 4
        assert where(tmp_path / "e.pdf", "Second paragraph")[1] < where(src, "Second paragraph")[1] - 11
        assert drawn(tmp_path / "e.pdf", "Alpha")[1] == (0, 0, 255)
        bad = [{"page": 1, "rect": FIRST_RECT, "text": "x", "align": "sideways"}]
        status, _, data = post("/api/text/edit", {"edits": json.dumps(bad)})
        assert status == 400 and "alignment" in json.loads(data)["error"]
    finally:
        srv.shutdown()
        srv.server_close()

