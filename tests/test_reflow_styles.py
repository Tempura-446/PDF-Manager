# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.33.0 (textedit.py): rewriting a paragraph keeps each word's own style.
Bold, italic and coloured words stay bold, italic and coloured when other
words of the paragraph change, and new words take the style of the word
they're typed after, as in a word processor."""
import ctypes
import os

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


# Two paragraphs set without a gap; the first ends short of the right edge.
TWO = [[("Our workshop finished the cabinet order this week, and", PLAIN, None)],
       [("the doors were given two coats of hard wax oil for anyone", PLAIN, None)],
       [("who wants them.", PLAIN, None)],
       [("Note:", BOLD, None), (" please keep the doors away from direct heat for", PLAIN, None)],
       [("two weeks while the oil cures.", PLAIN, None)]]


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


def linked(path, words_rect_of="northern"):
    """The styled paragraph with a link over the word ``northern``."""
    styled(path)
    from pypdf import PdfReader, PdfWriter
    from pypdf.annotations import Link
    x = 72 + pdfium_width("region, and the ")
    w = PdfWriter(clone_from=PdfReader(str(path)))
    w.add_annotation(0, Link(rect=(x, 686, x + pdfium_width("northern", ITALIC), 697), url="https://example.com/n"))
    w.write(str(path))
    return path


def pdfium_width(text, font=PLAIN):
    from reportlab.pdfbase.pdfmetrics import stringWidth
    return stringWidth(text, font, 10)


def boxes_of(path, word):
    """The tight boxes of the letters of ``word`` (first time it appears)."""
    doc = pdfium.PdfDocument(str(path))
    try:
        tp = doc[0].get_textpage()
        text = tp.get_text_range()
        at = text.index(word)
        return [tp.get_charbox(i) for i in range(at, at + len(word))]
    finally:
        doc.close()


def link_rects(path):
    from pypdf import PdfReader
    page = PdfReader(str(path)).pages[0]
    out = []
    for ref in page.get("/Annots") or []:
        a = ref.get_object()
        if a.get("/Subtype") == "/Link":
            out.append(([float(v) for v in a["/Rect"]], [float(v) for v in a.get("/QuadPoints", [])]))
    return out


def inside(box, rect, slack=1.0):
    l, b, r, t = box
    return rect[0] - slack <= l and r <= rect[2] + slack and rect[1] - slack <= b and t <= rect[3] + slack


def test_a_link_follows_its_words(tmp_path):
    src, out = linked(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    text = ("The quarterly report shows strong growth across absolutely every region, and the northern office "
            "led the way with a record year for new customers.")
    before = boxes_of(src, "northern")
    textedit.edit_text(src, out, [{"page": 1, "rect": RECT, "paragraph": True, "text": text}])
    after = boxes_of(out, "northern")
    assert abs(after[0][0] - before[0][0]) > 20 or abs(after[0][1] - before[0][1]) > 5  # it moved
    (rect, _), = link_rects(out)
    assert all(inside(b, rect) for b in after), (rect, after)
    assert look_of(words(out), "northern")[2]  # still italic


def test_a_link_whose_words_wrap_gets_a_box_per_line(tmp_path):
    src, out = linked(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    # Words typed right after the linked word join its link (as in a word processor),
    # and here they run over a line end.
    text = ("The quarterly report shows strong growth in every region, and the very much enlarged northern "
            "and eastern office led the way with a record year for new customers.")
    textedit.edit_text(src, out, [{"page": 1, "rect": RECT, "paragraph": True, "text": text}])
    (rect, quads), = link_rects(out)
    first, last = boxes_of(out, "northern"), boxes_of(out, "eastern")
    assert abs(first[0][1] - last[0][1]) > 5, "the linked words should be on two lines"
    assert len(quads) == 16, quads
    boxes = [quads[i:i + 8] for i in (0, 8)]
    as_rect = [(q[0], q[5], q[2], q[1]) for q in boxes]
    assert all(inside(b, as_rect[0]) for b in first), (as_rect, first)
    assert all(inside(b, as_rect[1]) for b in last), (as_rect, last)
    assert all(inside(b, rect) for b in first + last)


def test_a_link_whose_words_are_gone_goes(tmp_path):
    src, out = linked(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    text = "The quarterly report shows strong growth in every region, and a record year for new customers."
    textedit.edit_text(src, out, [{"page": 1, "rect": RECT, "paragraph": True, "text": text}])
    assert link_rects(out) == []


TEXT = ("The quarterly report shows strong growth in every region, and the northern office led the way "
        "with a record year for new customers.")


def test_the_runs_the_editor_shows(tmp_path):
    src = styled(tmp_path / "in.pdf")
    runs = textedit.paragraph_runs(src, 1, RECT, TEXT)
    assert "".join(r["text"] for r in runs) == TEXT
    styled_runs = [(r["text"], r["bold"], r["italic"], tuple(round(v, 1) for v in r["color"])) for r in runs]
    assert ("strong", True, False, (0, 0, 0)) in styled_runs, styled_runs
    assert ("northern", False, True, (0, 0, 0)) in styled_runs, styled_runs
    assert ("record", False, False, (0.8, 0.1, 0.1)) in styled_runs, styled_runs
    assert len(runs) == 7, styled_runs  # plain, strong, plain, northern, plain, record, plain


def test_runs_of_a_paragraph_in_one_style(tmp_path):
    one = [[(t, PLAIN, None) for t, _, _ in line] for line in LINES]
    src = styled(tmp_path / "in.pdf", one)
    runs = textedit.paragraph_runs(src, 1, RECT, TEXT)
    assert len(runs) == 1 and runs[0]["text"] == TEXT and not runs[0]["bold"]


def test_words_made_bold_and_italic_in_the_editor(tmp_path):
    """The editor says which letters are bold and italic; the colour stays the page's."""
    src, out = styled(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    marks = {"strong": (False, False), "growth": (True, True), "northern": (False, True)}  # bold off; made bold italic
    runs, at = [], 0
    for word, (bold, italic) in marks.items():
        k = TEXT.index(word)
        runs += [{"text": TEXT[at:k], "bold": False, "italic": False}, {"text": word, "bold": bold, "italic": italic}]
        at = k + len(word)
    runs.append({"text": TEXT[at:], "bold": False, "italic": False})
    textedit.edit_text(src, out, [{"page": 1, "rect": RECT, "paragraph": True, "text": TEXT, "runs": runs}])
    found = words(out)
    assert look_of(found, "strong")[1:3] == (False, False), found
    assert look_of(found, "growth")[1:3] == (True, True), found
    assert look_of(found, "northern")[1:3] == (False, True), found
    assert look_of(found, "record")[1:] == (False, False, (0.8, 0.1, 0.1)), found
    assert look_of(found, "office")[1:3] == (False, False), found


def test_runs_that_dont_match_the_text_are_refused(tmp_path):
    import pytest
    from pdfmanager import core
    src, out = styled(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    with pytest.raises(core.PdfManagerError):
        textedit.edit_text(src, out, [{"page": 1, "rect": RECT, "paragraph": True, "text": TEXT,
                                       "runs": [{"text": "Other words", "bold": True}]}])


def test_the_runs_endpoint(tmp_path, monkeypatch):
    import json
    import threading
    import urllib.error
    import urllib.request

    from pdfmanager import appdata, gui, recipes

    for mod in (appdata, gui, recipes):
        monkeypatch.setattr(mod, "data_dir", lambda: tmp_path / "appdata")
    src = styled(tmp_path / "in.pdf")
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")

    def post(fields):
        boundary = "zpdfrunstest"
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                 for k, v in fields.items()]
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="f.pdf"\r\n'
                     f'Content-Type: application/pdf\r\n\r\n'.encode() + src.read_bytes() + b"\r\n")
        req = urllib.request.Request(base + "/api/text/runs", data=b"".join(parts) + f"--{boundary}--\r\n".encode(),
                                     headers={"X-Pdfm-Token": token,
                                              "Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    try:
        status, data = post({"page": "1", "rect": json.dumps(RECT), "text": TEXT})
        assert status == 200 and "".join(r["text"] for r in data["runs"]) == TEXT
        assert any(r["text"] == "strong" and r["bold"] for r in data["runs"]), data
        status, data = post({"page": "1", "rect": "[1, 2]", "text": TEXT})
        assert status == 400 and "box" in data["error"]
        status, data = post({"page": "9", "rect": json.dumps(RECT), "text": TEXT})
        assert status == 400 and "doesn't exist" in data["error"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_paragraph_editor_in_a_browser(tmp_path):
    import base64
    import json
    import shutil
    import subprocess
    import threading
    from pathlib import Path

    import pytest

    from pdfmanager import gui

    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        opened = srv.RequestHandlerClass.opened
        cfg = {"a": opened.add(styled(tmp_path / "Styled.pdf")), "b": opened.add(styled(tmp_path / "Two.pdf", TWO)),
               "shots": os.environ.get("FIX33_SHOTS")}
        run = subprocess.run([node, str(Path(__file__).with_name("fix33.js")), url, json.dumps(cfg)],
                             capture_output=True, text=True, encoding="utf-8", timeout=600)
    finally:
        srv.shutdown()
        srv.server_close()
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]
    assert not out["native"], out["native"]
    # The editor shows the paragraph's words as they are.
    assert out["value"] == TEXT
    shown = {text.strip(): (bold, italic, colour) for text, bold, italic, colour in out["shown"]}
    assert shown["strong"][:2] == (True, False) and shown["northern"][:2] == (False, True), out["shown"]
    assert shown["record"] == (False, False, "rgb(204, 26, 26)"), out["shown"]
    assert out["picked"] == {"plain": "rgb(0, 0, 255)", "red": "rgb(204, 26, 26)"}, out["picked"]
    # A plain word typed over: the styled words stay as they were.
    (tmp_path / "typed.pdf").write_bytes(base64.b64decode(out["typed"]))
    found = words(tmp_path / "typed.pdf")
    assert "returning" in [w[0] for w in found] and "new" not in [w[0] for w in found]
    assert look_of(found, "strong")[1] and look_of(found, "northern")[2]
    assert look_of(found, "record")[3] == (0.8, 0.1, 0.1) and not look_of(found, "returning")[1]
    # Ctrl+B on one chosen word: that word only.
    (tmp_path / "bolded.pdf").write_bytes(base64.b64decode(out["bolded"]))
    found = words(tmp_path / "bolded.pdf")
    assert look_of(found, "growth")[1:3] == (True, False), found
    assert look_of(found, "strong")[1] and look_of(found, "northern")[1:3] == (False, True), found
    for plain in ("quarterly", "office", "returning"):
        assert look_of(found, plain)[1:3] == (False, False), (plain, found)
    assert out["undone"]
    # Two paragraphs with no gap between them: each opens by itself, also with a caret on the page.
    assert out["caret"] == "Caret"
    assert out["noteLook"] == {"weight": 400, "pressed": "false", "first": 700}, out["noteLook"]
    assert out["second"] == "Note: please keep the doors away from direct heat for two weeks while the oil cures.", out["second"]
    assert out["first"] == ("Our workshop finished the cabinet order this week, and the doors were given two "
                            "coats of hard wax oil for anyone who wants them."), out["first"]


def embedded(path):
    """A paragraph in a font that is in the file (cut down to its letters),
    one word bold, set as Word sets it: each word drawn by itself, no space
    drawn in the words' font on this page (page 2 draws one, so the font
    does name a space)."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    pdfmetrics.registerFont(TTFont("Vera", "Vera.ttf"))
    pdfmetrics.registerFont(TTFont("VeraBd", "VeraBd.ttf"))
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 700
    for pieces in [[("The cabinet doors were cut from ", "Vera"), ("solid", "VeraBd"), (" oak and", "Vera")],
                   [("given two coats of hard wax oil this week.", "Vera")]]:
        x = 72
        for text, font in pieces:
            c.setFont(font, 10)
            for word in text.split(" "):
                c.drawString(x, y, word)
                x += c.stringWidth(word + " ", font, 10)
            x -= c.stringWidth(" ", font, 10)
        y -= 12
    c.showPage()
    c.setFont("Vera", 10)
    c.drawString(72, 700, "Page two")
    c.save()
    return path


def test_no_space_is_drawn_in_a_font_the_page_draws_none_with(tmp_path):
    """Word draws every space in a one-letter font of its own; the main
    font names a space but has a box there. New words are set apart as the
    page's are, by moving the pen, never with that glyph."""
    from pypdf import PdfReader

    from pdfmanager import fonts as zfonts
    src, out = embedded(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    text = "The cabinet fronts were cut from solid oak and given two coats of hard wax oil this week."
    rect = [70, 700 - 12 - 4, 400, 712]
    page_fonts = PdfReader(str(src)).pages[0]["/Resources"]["/Font"]
    assert any(" " in zfonts.pdf_font_codes(f) for f in page_fonts.values())  # the font names a space

    def drawn_spaces(path):
        doc = pdfium.PdfDocument(str(path))
        try:
            tp = doc[0].get_textpage()
            return sum(1 for i in range(tp.count_chars())
                       if tp.get_text_range(i, 1) == " " and not pdfium_raw.FPDFText_IsGenerated(tp.raw, i))
        finally:
            doc.close()

    assert drawn_spaces(src) == 0
    textedit.edit_text(src, out, [{"page": 1, "rect": rect, "paragraph": True, "text": text}])
    found = words(out)
    assert [w[0] for w in found] == text.split(), found
    assert look_of(found, "solid")[1] and not look_of(found, "fronts")[1]
    assert drawn_spaces(out) == 0
    # One line changed, too.
    textedit.edit_text(src, out, [{"page": 1, "rect": [70, 684, 400, 698], "old": "given two coats of hard wax oil this week.",
                                   "text": "given three coats of hard wax oil this week."}])
    assert "three" in [w[0] for w in words(out)] and drawn_spaces(out) == 0


def _type0(to_unicode):
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject
    cmap = DecodedStreamObject()
    cmap.set_data(to_unicode)
    cid = DictionaryObject({NameObject("/Subtype"): NameObject("/CIDFontType2"), NameObject("/DW"): NumberObject(500),
                            NameObject("/W"): ArrayObject([NumberObject(4), ArrayObject([NumberObject(226)])])})
    return DictionaryObject({NameObject("/Subtype"): NameObject("/Type0"), NameObject("/BaseFont"): NameObject("/BCDEEE+Calibri"),
                             NameObject("/Encoding"): NameObject("/Identity-H"),
                             NameObject("/DescendantFonts"): ArrayObject([cid]), NameObject("/ToUnicode"): cmap})


def test_code_zero_is_never_a_letter():
    """Code 0 is a font's "no such letter" box. Word's maps can call it a
    space beside the real one (0004 here): the real one is the space, and
    a font whose only "space" is code 0 has none to write."""
    from pdfmanager import fonts as zfonts
    both = _type0(b"2 beginbfchar\n<0000> <0020>\n<0004> <0020>\nendbfchar\n1 beginbfrange\n<0024> <0025> <0041>\nendbfrange")
    codes = zfonts.pdf_font_codes(both)
    assert codes[" "] == b"\x00\x04" and codes["A"] == b"\x00\x24"
    own = textedit._OwnFont(both, codes)
    assert own.space is None and own.operands("A A") == "[<002400040024>]"
    only_zero = _type0(b"1 beginbfchar\n<0000> <0020>\nendbfchar\n1 beginbfrange\n<0024> <0025> <0041>\nendbfrange")
    codes = zfonts.pdf_font_codes(only_zero)
    assert " " not in codes
    own = textedit._OwnFont(only_zero, codes)
    assert own.space == 250 and own.operands("A A") == "[<0024> -250 <0024>]"
    # A range that starts at code 0 names no letter for it either.
    ranged = _type0(b"1 beginbfrange\n<0000> <0004> <0020>\nendbfrange")
    assert b"\x00\x00" not in zfonts.pdf_font_codes(ranged).values()


def test_a_paragraph_rewritten_by_covering_shows_its_styles_again(tmp_path):
    """Where the old words can only be covered (tagged files), they stay
    underneath: the editor's styles, and a second change, are about the
    words that show."""
    src, once, twice = styled(tmp_path / "in.pdf"), tmp_path / "once.pdf", tmp_path / "twice.pdf"
    first = TEXT.replace("new", "returning")
    textedit._edit(src, once, [{"page": 1, "rect": RECT, "paragraph": True, "text": first}], None, None, True)
    runs = textedit.paragraph_runs(once, 1, RECT, first)
    assert "".join(r["text"] for r in runs) == first
    looks = {r["text"].strip(): (r["bold"], r["italic"], tuple(round(v, 1) for v in r["color"])) for r in runs}
    assert looks["strong"] == (True, False, (0, 0, 0)) and looks["northern"][:2] == (False, True), runs
    assert looks["record"] == (False, False, (0.8, 0.1, 0.1)), runs
    second = first.replace("office", "branch")
    textedit.edit_text(once, twice, [{"page": 1, "rect": RECT, "paragraph": True, "text": second}])
    found = [w for w in words(twice)]
    shown = [w for w in found if w[0].strip(".,") in ("strong", "northern", "record", "branch")]
    assert any(w[0] == "strong" and w[2] for w in shown) and any(w[0] == "northern" and w[3] for w in shown), shown
    assert any(w[0] == "record" and w[4] == (0.8, 0.1, 0.1) for w in shown), shown


def word_like(path):
    """A paragraph tagged as Word tags one with a link in it: the words
    before the link, the link's words and the words after are each in a
    marked-content sequence of their own, over two lines."""
    import pikepdf
    from pikepdf import Array, Dictionary, Name
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    content = b"\n".join([
        b"/P <</MCID 0>> BDC BT /F1 10 Tf 72 700 Td (For care instructions see ) Tj ET EMC",
        b"/Link <</MCID 1>> BDC BT /F1 10 Tf 0 0 0.8 rg 190.4 700 Td (our website) Tj ET EMC",
        b"/P <</MCID 2>> BDC BT /F1 10 Tf 0 g 244.9 700 Td ( or ask in the) Tj ET EMC",
        b"/P <</MCID 3>> BDC BT /F1 10 Tf 72 688 Td (shop, where we keep printed copies.) Tj ET EMC",
    ])
    page = pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792],
                                   Resources=Dictionary(Font=Dictionary(F1=font)), Contents=pdf.make_stream(content)))
    pdf.pages.append(page)
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.save(str(path))
    return path


WORD_RECT = [70, 684, 330, 712]


def page_text(path):
    doc = pdfium.PdfDocument(str(path))
    try:
        return " ".join(doc[0].get_textpage().get_text_range().split())
    finally:
        doc.close()


def test_a_paragraph_in_several_tags_leaves_no_old_words_underneath(tmp_path):
    """Word's files: a rewritten paragraph was covered, so Copy and Find
    gave the old words as well as the new."""
    src, out = word_like(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    assert page_text(src) == "For care instructions see our website or ask in the shop, where we keep printed copies."
    text = "For full care instructions see our website or ask in the shop, where we keep spare printed copies."
    report = []
    textedit.edit_text(src, out, [{"page": 1, "rect": WORD_RECT, "paragraph": True, "text": text}], report=report)
    assert not report[0].get("covered"), report
    assert page_text(out) == text
    assert look_of(words(out), "website")[3] == (0.0, 0.0, 0.8)  # the link's colour stays on its words


def test_one_line_in_several_tags_leaves_no_old_words_underneath(tmp_path):
    src, out = word_like(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    old, new = "For care instructions see our website or ask in the", "For care advice see our website or ask in the"
    textedit.edit_text(src, out, [{"page": 1, "rect": [70, 697, 330, 711], "old": old, "text": new}])
    assert page_text(out) == new + " shop, where we keep printed copies."


def test_a_colour_for_the_whole_paragraph_leaves_coloured_words_their_own(tmp_path):
    src, out = styled(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    textedit.edit_text(src, out, [{"page": 1, "rect": RECT, "paragraph": True, "text": TEXT, "color": [0, 0, 1]}])
    found = words(out)
    assert look_of(found, "quarterly")[3] == (0.0, 0.0, 1.0) and look_of(found, "office")[3] == (0.0, 0.0, 1.0)
    assert look_of(found, "record")[3] == (0.8, 0.1, 0.1)                       # its own colour stays
    assert look_of(found, "strong")[1:] == (True, False, (0.0, 0.0, 1.0))      # bold stays, in the new colour
    assert look_of(found, "northern")[2]


def test_a_font_for_the_whole_paragraph_keeps_bold_and_italic_words(tmp_path):
    src, out = styled(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    textedit.edit_text(src, out, [{"page": 1, "rect": RECT, "paragraph": True, "text": TEXT, "font": "Times"}])
    found = words(out)
    assert look_of(found, "quarterly")[:3] == ("Times-Roman", False, False), found
    assert look_of(found, "strong")[0] == "Times-Bold" and look_of(found, "northern")[0] == "Times-Italic", found
    assert look_of(found, "record")[3] == (0.8, 0.1, 0.1)


def test_bold_for_the_whole_paragraph_adds_to_the_words_own_styles(tmp_path):
    src, out = styled(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    textedit.edit_text(src, out, [{"page": 1, "rect": RECT, "paragraph": True, "text": TEXT, "bold": True}])
    found = words(out)
    assert all(w[2] for w in found if w[0] not in ("Elsewhere", "on", "the", "page")), found
    assert look_of(found, "northern")[1:3] == (True, True) and look_of(found, "record")[3] == (0.8, 0.1, 0.1)


def test_a_size_for_the_whole_paragraph_scales_every_word(tmp_path):
    import pypdfium2.raw as raw
    src, out = styled(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    textedit.edit_text(src, out, [{"page": 1, "rect": RECT, "paragraph": True, "text": TEXT, "size": 13}])
    doc = pdfium.PdfDocument(str(out))
    try:
        tp = doc[0].get_textpage()
        full = tp.get_text_range()
        sizes = {round(raw.FPDFText_GetFontSize(tp.raw, i), 1) for i in range(full.index("The"), full.index("customers") + 9)
                 if full[i].strip()}
    finally:
        doc.close()
    assert sizes == {13.0}, sizes
    assert look_of(words(out), "strong")[1]


def highlighted(path, subtype="/Highlight"):
    """The styled paragraph with a highlight (or other text markup) over "northern office"."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject
    styled(path)
    x0 = 72 + pdfium_width("region, and the ")
    x1 = x0 + pdfium_width("northern", ITALIC) + pdfium_width(" office")
    w = PdfWriter(clone_from=PdfReader(str(path)))
    quad = [x0, 697, x1, 697, x0, 686, x1, 686]
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject(subtype),
        NameObject("/Rect"): ArrayObject([FloatObject(v) for v in (x0, 686, x1, 697)]),
        NameObject("/QuadPoints"): ArrayObject([FloatObject(v) for v in quad]),
        NameObject("/C"): ArrayObject([FloatObject(v) for v in (1, 1, 0)]),
        NameObject("/AP"): DictionaryObject()})  # (an old picture of it: must not outlive the move)
    w.pages[0][NameObject("/Annots")] = ArrayObject([w._add_object(annot)])
    w.write(str(path))
    return path


def markups(path):
    from pypdf import PdfReader
    out = []
    for ref in PdfReader(str(path)).pages[0].get("/Annots") or []:
        a = ref.get_object()
        out.append((str(a["/Subtype"]), [float(v) for v in a["/Rect"]], [float(v) for v in a.get("/QuadPoints", [])], "/AP" in a))
    return out


def test_a_highlight_follows_its_words(tmp_path):
    for subtype in ("/Highlight", "/Underline", "/StrikeOut"):
        src, out = highlighted(tmp_path / "in.pdf", subtype), tmp_path / "out.pdf"
        text = TEXT.replace("shows strong", "shows quite remarkably strong")
        textedit.edit_text(src, out, [{"page": 1, "rect": RECT, "paragraph": True, "text": text}])
        (kind, rect, quads, has_ap), = markups(out)
        assert kind == subtype and not has_ap and len(quads) % 8 == 0 and quads
        lines = [(min(q[0::2]), min(q[1::2]), max(q[0::2]), max(q[1::2])) for q in (quads[i:i + 8] for i in range(0, len(quads), 8))]
        for word in ("northern", "office"):
            assert all(any(inside(b, line) for line in lines) for b in boxes_of(out, word)), (subtype, word, lines)
            assert all(inside(b, rect) for b in boxes_of(out, word))
        for word in ("region", "led"):  # its neighbours aren't under it
            assert not any(inside(b, line) for line in lines for b in boxes_of(out, word)), (subtype, word, lines)


def test_a_highlight_over_wrapped_words_has_a_box_per_line_and_goes_with_its_words(tmp_path):
    src, out = highlighted(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    text = TEXT.replace("region, and the", "region and area, and also the")
    textedit.edit_text(src, out, [{"page": 1, "rect": RECT, "paragraph": True, "text": text}])
    (_, rect, quads, _), = markups(out)
    north, office = boxes_of(out, "northern"), boxes_of(out, "office")
    if abs(north[0][1] - office[0][1]) > 5:  # now on two lines
        assert len(quads) == 16
    assert all(inside(b, rect) for b in north + office)
    # Its words deleted: the highlight goes.
    textedit.edit_text(src, out, [{"page": 1, "rect": RECT, "paragraph": True, "text": TEXT.replace(" northern office", "")}])
    assert markups(out) == []


def test_a_link_and_a_highlight_on_the_same_words_both_follow(tmp_path):
    from pypdf import PdfReader, PdfWriter
    from pypdf.annotations import Link
    src, out = highlighted(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    x = 72 + pdfium_width("region, and the ")
    w = PdfWriter(clone_from=PdfReader(str(src)))
    w.add_annotation(0, Link(rect=(x, 686, x + pdfium_width("northern", ITALIC), 697), url="https://example.com/n"))
    w.write(str(src))
    text = TEXT.replace("shows strong", "shows quite remarkably strong")
    textedit.edit_text(src, out, [{"page": 1, "rect": RECT, "paragraph": True, "text": text}])
    found = {kind: rect for kind, rect, _, _ in markups(out)}
    assert set(found) == {"/Highlight", "/Link"}
    north = boxes_of(out, "northern")
    assert all(inside(b, found["/Link"]) for b in north) and all(inside(b, found["/Highlight"]) for b in north)
    assert not all(inside(b, found["/Link"]) for b in boxes_of(out, "office"))


def raised(path):
    """A paragraph with a footnote mark above the line (a small "2") and a subscript below it."""
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont(PLAIN, 10)
    c.drawString(72, 700, "The pressure in the boiler")
    x = 72 + c.stringWidth("The pressure in the boiler", PLAIN, 10)
    c.setFont(PLAIN, 6.5)
    c.drawString(x, 703.5, "2")
    x += c.stringWidth("2", PLAIN, 6.5)
    c.setFont(PLAIN, 10)
    c.drawString(x, 700, " rose while H")
    x += c.stringWidth(" rose while H", PLAIN, 10)
    c.setFont(PLAIN, 6.5)
    c.drawString(x, 698, "2")
    x += c.stringWidth("2", PLAIN, 6.5)
    c.setFont(PLAIN, 10)
    c.drawString(x, 700, "O was")
    c.drawString(72, 688, "drained from the east wing overnight.")
    c.save()
    return path


def small_marks(path):
    """[(letter, size, height above its line's baseline)] of the letters set smaller than the text."""
    import pypdfium2.raw as raw
    doc = pdfium.PdfDocument(str(path))
    try:
        tp = doc[0].get_textpage()
        letters_, out = [], []
        for i in range(tp.count_chars()):
            ch = tp.get_text_range(i, 1)
            if ch.strip():
                x, y = ctypes.c_double(), ctypes.c_double()
                raw.FPDFText_GetCharOrigin(tp.raw, i, ctypes.byref(x), ctypes.byref(y))
                letters_.append((ch, round(raw.FPDFText_GetFontSize(tp.raw, i), 1), x.value, y.value))
        for ch, size, x, y in letters_:
            if size < 9:
                base = min((b for b in letters_ if b[1] >= 9), key=lambda b: abs(b[3] - y) * 50 + abs(b[2] - x))
                out.append((ch, size, round(y - base[3], 1)))
        return out
    finally:
        doc.close()


def test_marks_above_and_below_the_line_stay_there(tmp_path):
    src, out = raised(tmp_path / "in.pdf"), tmp_path / "out.pdf"
    assert small_marks(src) == [("2", 6.5, 3.5), ("2", 6.5, -2.0)]
    text = "The steam pressure in the boiler2 rose sharply while H2O was drained from the east wing overnight."
    textedit.edit_text(src, out, [{"page": 1, "rect": [70, 684, 330, 712], "paragraph": True, "text": text}])
    assert small_marks(out) == [("2", 6.5, 3.5), ("2", 6.5, -2.0)]
    assert page_text(out).replace(" ", "") == text.replace(" ", "")


def test_a_big_first_word_doesnt_set_the_paragraphs_size_or_spacing(tmp_path):
    """A paragraph that opens with a larger word (a run-in heading): the
    rest keeps its own size, and the lines stay as far apart as they were."""
    import pypdfium2.raw as raw
    path, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont(BOLD, 14)
    c.drawString(72, 700, "Warning")
    c.setFont(PLAIN, 10)
    c.drawString(72 + c.stringWidth("Warning ", BOLD, 14), 700, "keep the doors away from direct heat")
    c.drawString(72, 688, "for the first two weeks while the oil cures, and")
    c.drawString(72, 676, "do not stack anything on the shelves.")
    c.save()
    text = ("Warning keep the cabinet doors away from direct heat for the first two weeks while the oil cures, "
            "and do not stack anything on the shelves.")
    textedit.edit_text(path, out, [{"page": 1, "rect": [70, 672, 330, 714], "paragraph": True, "text": text}])
    doc = pdfium.PdfDocument(str(out))
    try:
        tp = doc[0].get_textpage()
        full = tp.get_text_range()
        sizes = {full[i]: round(raw.FPDFText_GetFontSize(tp.raw, i), 1) for i in (full.index("Warning"), full.index("cabinet"), full.index("shelves"))}
        ys = set()
        for i in range(tp.count_chars()):
            if full[i].strip():
                x, y = ctypes.c_double(), ctypes.c_double()
                raw.FPDFText_GetCharOrigin(tp.raw, i, ctypes.byref(x), ctypes.byref(y))
                ys.add(round(y.value, 1))
        ys = sorted(ys, reverse=True)
    finally:
        doc.close()
    assert sizes == {"W": 14.0, "c": 10.0, "s": 10.0}, sizes
    assert ys == [700.0, 688.0, 676.0], ys
    assert look_of(words(out), "Warning")[1] and not look_of(words(out), "cabinet")[1]
