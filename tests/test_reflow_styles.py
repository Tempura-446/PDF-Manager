# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.33.0 (textedit.py): rewriting a paragraph keeps each word's own style.
Bold, italic and coloured words stay bold, italic and coloured when other
words of the paragraph change, and new words take the style of the word
they're typed after, as in a word processor."""
import ctypes

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pdfmanager import textedit

# Three lines; (text, font, colour) pieces per line.
PLAIN, BOLD, ITALIC = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"
RED = (0.8, 0.1, 0.1)
LINES = [
    [("The quarterly report shows ", PLAIN, None), ("strong", BOLD, None), (" growth in every", PLAIN, None)],
    [("region, and the ", PLAIN, None), ("northern", ITALIC, None), (" office led the way with", PLAIN, None)],
    [("a ", PLAIN, None), ("record", PLAIN, RED), (" year for new customers.", PLAIN, None)],
]
RECT = [70, 700 - 24 - 4, 330, 712]


def styled(path, lines=LINES):
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 700
    for pieces in lines:
        x = 72
        for text, font, colour in pieces:
            c.setFont(font, 10)
            c.setFillColorRGB(*(colour or (0, 0, 0)))
            c.drawString(x, y, text)
            x += c.stringWidth(text, font, 10)
        y -= 12
    c.setFont(PLAIN, 10)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(320, 500, "Elsewhere on the page")
    c.save()
    return path


def words(path):
    """[(word, font name, bold, italic, (r, g, b))] as pdfium reads them, a
    word's look taken from its first letter."""
    doc = pdfium.PdfDocument(str(path))
    try:
        tp = doc[0].get_textpage()
        out, word, look = [], "", None
        for i in range(tp.count_chars()):
            ch = tp.get_text_range(i, 1)
            if not ch.strip():
                if word:
                    out.append((word, *look))
                word, look = "", None
                continue
            if not word:
                buf, flags = ctypes.create_string_buffer(256), ctypes.c_int()
                pdfium_raw.FPDFText_GetFontInfo(tp.raw, i, buf, 256, ctypes.byref(flags))
                name = buf.value.decode("latin-1").split("+")[-1]
                rgba = [ctypes.c_uint() for _ in range(4)]
                pdfium_raw.FPDFText_GetFillColor(tp.raw, i, *map(ctypes.byref, rgba))
                bold = "bold" in name.lower() or pdfium_raw.FPDFText_GetFontWeight(tp.raw, i) >= 600
                italic = "oblique" in name.lower() or "italic" in name.lower()
                look = (name, bold, italic, tuple(round(v.value / 255, 1) for v in rgba[:3]))
            word += ch
        if word:
            out.append((word, *look))
        return out
    finally:
        doc.close()


def look_of(found, word):
    hits = [w for w in found if w[0].strip(".,") == word]
    assert hits, (word, [w[0] for w in found])
    return hits[0][1:]


def test_one_word_changed_next_to_styled_words(tmp_path):
    src, out = styled(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    text = ("The quarterly report shows strong growth in every region, and the northern office led the way "
            "with a record year for returning customers.")
    textedit.edit_text(src, out, [{"page": 1, "rect": RECT, "paragraph": True, "text": text}])
    found = words(out)
    assert " ".join(w[0] for w in found).startswith(text), [w[0] for w in found]
    _, bold, italic, colour = look_of(found, "strong")
    assert bold and not italic and colour == (0, 0, 0), found
    _, bold, italic, colour = look_of(found, "northern")
    assert italic and not bold, found
    _, bold, italic, colour = look_of(found, "record")
    assert colour == (0.8, 0.1, 0.1) and not bold, found
    for plain in ("quarterly", "growth", "office", "returning", "customers"):
        _, bold, italic, colour = look_of(found, plain)
        assert not bold and not italic and colour == (0, 0, 0), (plain, found)
    assert look_of(found, "Elsewhere")[1:] == (False, False, (0, 0, 0))


def test_typing_inside_a_bold_word_stays_bold(tmp_path):
    src, out = styled(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    text = ("The quarterly report shows very strong growth in every region, and the northern office led the way "
            "with a record year for new customers.")
    textedit.edit_text(src, out, [{"page": 1, "rect": RECT, "paragraph": True, "text": text}])
    found = words(out)
    # "very" is typed after a plain space before "strong": it follows the word before it (plain).
    assert not look_of(found, "very")[1], found
    assert look_of(found, "strong")[1], found


def test_a_replaced_bold_word_stays_bold(tmp_path):
    src, out = styled(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    text = ("The quarterly report shows remarkable growth in every region, and the northern office led the way "
            "with a record year for new customers.")
    textedit.edit_text(src, out, [{"page": 1, "rect": RECT, "paragraph": True, "text": text}])
    assert look_of(words(out), "remarkable")[1]


def test_a_paragraph_in_one_style_is_written_as_before(tmp_path):
    """One style throughout: the same path as 0.32, byte for byte."""
    one = [[(t, PLAIN, None) for t, _, _ in line] for line in LINES]
    src = styled(tmp_path / "in.pdf", one)
    text = "The quarterly report shows strong growth in every region, and more."
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    textedit.edit_text(src, a, [{"page": 1, "rect": RECT, "paragraph": True, "text": text}])
    textedit.edit_text(src, b, [{"page": 1, "rect": RECT, "paragraph": True, "text": text, "styles": False}])
    assert a.read_bytes() == b.read_bytes()


def test_a_bold_first_word_doesnt_make_the_paragraph_bold(tmp_path):
    lines = [[("Note:", BOLD, None), (" the quarterly report shows growth in", PLAIN, None)],
             [("every region, and the office led the way.", PLAIN, None)]]
    src, out = styled(tmp_path / "in.pdf", lines), tmp_path / "out.pdf"
    text = "Note: the quarterly report shows good growth in every region, and the office led the way."
    textedit.edit_text(src, out, [{"page": 1, "rect": [70, 700 - 12 - 4, 330, 712], "paragraph": True, "text": text}])
    found = words(out)
    assert look_of(found, "Note:")[1], found
    for plain in ("the", "good", "growth", "office"):
        assert not look_of(found, plain)[1], (plain, found)
