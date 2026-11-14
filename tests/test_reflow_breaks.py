# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.34.0 (textedit.py): a rewritten paragraph's lines are broken looking
at the whole paragraph, not line by line: no lone short word left on the
last line when the lines above can spare one, more even word gaps in
justified text, never more lines than breaking line by line would give."""
import random

from pdfmanager import textedit

GAP = 5.0


def measure_of(widths):
    """A measure for words named by their index ("0 1 2"): the widths plus a gap between words."""
    def measure(line):
        words = [w for w in line.split(" ") if w != ""]
        return sum(widths[int(w)] for w in words) + GAP * max(len(line.split(" ")) - 1, 0)
    return measure


def lines_of(widths, room, **how):
    text = " ".join(str(i) for i in range(len(widths)))
    return [[int(w) for w in line.split()] for line in textedit._wrap(text, room, measure_of(widths), **how)]


def test_no_lone_word_on_the_last_line():
    widths = [30, 30, 30, 30, 30, 30, 10]
    greedy = lines_of(widths, 100, greedy=True)
    assert greedy == [[0, 1, 2], [3, 4, 5], [6]]
    best = lines_of(widths, 100)
    assert len(best) == 3 and len(best[-1]) >= 2, best
    assert [w for line in best for w in line] == list(range(7))


def test_never_wider_than_the_room_or_more_lines_than_line_by_line():
    rnd = random.Random(4)
    for _ in range(200):
        widths = [rnd.choice((8, 12, 20, 26, 33, 41, 60)) for _ in range(rnd.randrange(1, 70))]
        room = rnd.choice((90, 120, 200, 310))
        for justify in (False, True):
            greedy, best = lines_of(widths, room, greedy=True), lines_of(widths, room, justify=justify)
            assert [w for line in best for w in line] == list(range(len(widths)))
            assert len(best) <= len(greedy), (widths, room)
            for line in best:
                natural = sum(widths[w] for w in line) + GAP * (len(line) - 1)
                assert natural <= room + 1e-6 or len(line) == 1, (line, natural, room)


def stretch(lines, widths, room):
    """How far the word gaps of the justified lines (all but the last) are
    pulled from their natural width: the sum of the squares."""
    return sum(((room - sum(widths[w] for w in line) - GAP * (len(line) - 1)) / (len(line) - 1)) ** 2
               for line in lines[:-1] if len(line) > 1)


def test_justified_gaps_are_more_even_than_line_by_line():
    """Better in most paragraphs, the same where line by line was already
    the best, and worse only where a lone last word was taken up."""
    rnd = random.Random(11)
    better = worse = 0
    for _ in range(300):
        widths = [rnd.choice((10, 14, 18, 22, 27, 35, 48)) for _ in range(rnd.randrange(40, 120))]
        room = rnd.choice((180, 240, 300))
        greedy, best = lines_of(widths, room, greedy=True), lines_of(widths, room, justify=True)
        a, b = stretch(greedy, widths, room), stretch(best, widths, room)
        better += b < a - 1e-9
        if b > a + 1e-9:
            worse += 1
            assert len(greedy[-1]) == 1 or len(best) < len(greedy) or \
                sum(widths[w] for w in greedy[-1]) < room * 0.12, (greedy, best)
    assert better >= 180 and worse <= 30, (better, worse)


def test_a_word_wider_than_the_room_gets_a_line_to_itself():
    assert lines_of([20, 150, 20, 20], 100) == [[0], [1], [2, 3]]


def test_one_word_and_no_words():
    assert textedit._wrap("7", 100, lambda line: 10.0) == ["7"]
    assert textedit._wrap("", 100, lambda line: 0.0) == [""]


def test_a_very_long_paragraph_is_broken_line_by_line():
    widths = [20] * 2500
    assert lines_of(widths, 100) == lines_of(widths, 100, greedy=True)


# -- dividing words ------------------------------------------------------------------------

def letters(line):
    """A measure by the letter: each one 1 wide, as is a space."""
    return float(len(line))


def test_a_justified_narrow_column_divides_a_long_word_where_the_patterns_say():
    text = "the committee met extraordinarily early on a cold morning"
    plain = textedit._wrap(text, 16, letters, justify=True)
    divided = textedit._wrap_marked(text, 16, letters, justify=True, divide=textedit._divisions)
    lines = [line for line, _ in divided]
    assert any(end == "soft" for _, end in divided), divided
    for (line, end), nxt in zip(divided, lines[1:]):
        assert len(line) <= 16, line
        if end == "soft":
            assert line.endswith("-")
            whole = line.split(" ")[-1][:-1] + nxt.split(" ")[0]
            assert whole == "extraordinarily" and line.split(" ")[-1][:-1] in ("ex", "extra", "extraor", "extraordi", "extraordinar")
    assert "".join(line[:-1] if end == "soft" else line + " " for line, end in divided).strip() == text
    assert len(lines) <= len(plain)


def test_a_word_with_its_own_hyphen_breaks_after_it_and_gets_no_second_one():
    text = "this is a well-known and long-established rule of the house"
    divided = textedit._wrap_marked(text, 14, letters, justify=True, divide=textedit._divisions)
    hard = [(line, end) for line, end in divided if end == "hard"]
    assert hard and all(line.endswith("-") and not line.endswith("--") for line, _ in hard), divided
    rebuilt = "".join(line if end == "hard" else line[:-1] if end == "soft" else line + " " for line, end in divided).strip()
    assert rebuilt == text
    assert textedit._divisions("well-known") == [(5, "hard")] and textedit._divisions("(well-known),") == [(6, "hard")]
    assert textedit._divisions("e-mail") == [] and textedit._divisions("2024-0117") == []


def test_ragged_text_is_only_divided_when_a_word_is_wider_than_the_column():
    text = "the committee met extraordinarily early on a cold morning"
    assert all(end is None for _, end in textedit._wrap_marked(text, 16, letters, divide=textedit._divisions))
    narrow = textedit._wrap_marked(text, 12, letters, divide=textedit._divisions)  # "extraordinarily" is 15
    assert any(end == "soft" for _, end in narrow) and all(len(line) <= 12 for line, _ in narrow), narrow


def test_the_last_word_is_never_divided_and_two_lines_running_rarely():
    text = "a b c d extraordinarily"
    assert all(end is None for _, end in textedit._wrap_marked(text, 12, letters, justify=True, divide=textedit._divisions))
    prose = ("The committee met on Tuesday to review the quarterly accounts and the draft budget for next year. "
             "Members agreed that the maintenance reserve should rise by four percent, and asked the treasurer "
             "to circulate revised figures before the next meeting. The proposal to replace the boiler in the "
             "east wing was deferred until a second quotation has been received from the contractor.")
    for room in (28, 34, 40, 52):
        ends = [end for _, end in textedit._wrap_marked(prose, room, letters, justify=True, divide=textedit._divisions)]
        assert not any(a == "soft" and b == "soft" for a, b in zip(ends, ends[1:])), (room, ends)
        assert ends.count("soft") <= len(ends) // 2, (room, ends)


def test_punctuation_stays_with_its_word():
    assert textedit._divisions("extraordinarily,") == [(2, "soft"), (5, "soft"), (7, "soft"), (9, "soft"), (12, "soft")]
    assert textedit._divisions('"table"') == [(3, "soft")]


# -- in a file -----------------------------------------------------------------------------

NARROW = ["The committee met early on a cold", "morning to review the accounts and", "the budget for next year."]
NEW = ("The committee met extraordinarily early on a cold morning to review the quarterly accounts, "
       "the maintenance responsibilities and the international budget for next year.")
NARROW_RECT = [70, 700 - 24 - 4, 250, 712]


def narrow(path, lang=None, bold=None):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 700
    for line in NARROW:
        x = 72
        for word in line.split(" "):
            font = "Helvetica-Bold" if word == bold else "Helvetica"
            c.setFont(font, 10)
            c.drawString(x, y, word)
            x += c.stringWidth(word + " ", font, 10)
        y -= 12
    c.save()
    if lang:
        import pikepdf
        with pikepdf.open(path, allow_overwriting_input=True) as pdf:
            pdf.Root.Lang = lang
            pdf.save()
    return path


def rows_of(path):
    """The page's lines as pdfium reads them: [(text, right edge)], top to
    bottom (a hyphen at a line end reads as U+FFFE: given as "-")."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(path))
    try:
        tp = doc[0].get_textpage()
        rows = []
        for i in range(tp.count_chars()):
            ch = tp.get_text_range(i, 1).replace("\ufffe", "-")
            if ch in ("\r", "\n"):
                continue
            _, bottom, right, _ = tp.get_charbox(i, loose=True)
            if not ch.strip():
                if rows:
                    rows[-1][1] += " "
                continue
            if not rows or abs(rows[-1][0] - bottom) > 4:
                rows.append([bottom, "", 0.0])
            rows[-1][1] += ch
            rows[-1][2] = max(rows[-1][2], tp.get_charbox(i)[2])
        return [(text.strip(), right) for _, text, right in rows]
    finally:
        doc.close()


def test_a_justified_paragraph_divides_words_in_the_file(tmp_path):
    src, out = narrow(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    textedit.edit_text(src, out, [{"page": 1, "rect": NARROW_RECT, "paragraph": True, "text": NEW, "align": "justify"}])
    rows = rows_of(out)
    divided = [text for text, _ in rows if text.endswith("-")]
    assert divided, rows
    # Put together again, the words are the ones typed.
    rebuilt = " ".join(text for text, _ in rows).replace("- ", "")
    assert rebuilt == NEW, rebuilt
    # Justified: every line but the last ends at the same place (a hyphen hangs no further).
    edges = [right for _, right in rows[:-1]]
    assert max(edges) - min(edges) < 0.6, rows
    # Turned off, or broken the old way: no word is divided.
    for off in ({"hyphens": False}, {"breaks": "simple"}):
        textedit.edit_text(src, out, [{"page": 1, "rect": NARROW_RECT, "paragraph": True, "text": NEW, "align": "justify", **off}])
        assert not [text for text, _ in rows_of(out) if text.endswith("-")], off


def test_a_ragged_paragraph_is_not_divided(tmp_path):
    src, out = narrow(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    textedit.edit_text(src, out, [{"page": 1, "rect": NARROW_RECT, "paragraph": True, "text": NEW}])
    assert not [text for text, _ in rows_of(out) if text.endswith("-")]


def test_another_language_is_not_divided(tmp_path):
    src, out = narrow(tmp_path / "in.pdf", lang="de-DE"), tmp_path / "out.pdf"
    textedit.edit_text(src, out, [{"page": 1, "rect": NARROW_RECT, "paragraph": True, "text": NEW, "align": "justify"}])
    assert not [text for text, _ in rows_of(out) if text.endswith("-")]
    src = narrow(tmp_path / "en.pdf", lang="en-GB")
    textedit.edit_text(src, out, [{"page": 1, "rect": NARROW_RECT, "paragraph": True, "text": NEW, "align": "justify"}])
    assert [text for text, _ in rows_of(out) if text.endswith("-")]


def test_a_divided_bold_word_stays_bold_on_both_lines(tmp_path):
    import ctypes

    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_raw
    src, out = narrow(tmp_path / "in.pdf", bold="committee"), tmp_path / "out.pdf"
    text = NEW.replace("extraordinarily", "committee-wide").replace("The committee", "The committee")
    text = ("The extraordinarily committee met early on a cold morning to review the quarterly accounts, the "
            "maintenance responsibilities and the international budget for next year.")
    textedit.edit_text(src, out, [{"page": 1, "rect": NARROW_RECT, "paragraph": True, "text": text, "align": "justify",
                                   "runs": [{"text": "The "}, {"text": "extraordinarily committee", "bold": True},
                                            {"text": text[len("The extraordinarily committee"):]}]}])
    doc = pdfium.PdfDocument(str(out))
    try:
        tp = doc[0].get_textpage()
        full = tp.get_text_range()
        bold = ""
        for i in range(tp.count_chars()):
            buf, flags = ctypes.create_string_buffer(256), ctypes.c_int()
            pdfium_raw.FPDFText_GetFontInfo(tp.raw, i, buf, 256, ctypes.byref(flags))
            if b"Bold" in buf.value and full[i].strip():
                bold += full[i]
    finally:
        doc.close()
    assert bold.replace("-", "") == "extraordinarilycommittee", bold
    assert " ".join(t for t, _ in rows_of(out)).replace("- ", "") == text
