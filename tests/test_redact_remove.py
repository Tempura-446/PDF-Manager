# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""True redaction (redact.py method "remove", redactcontent.py): what lies
under the boxes is taken out of the page, the rest stays real text, in the
same place."""

import ctypes
import io
import json
import os
import threading
import urllib.request
import zlib

import pikepdf
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest
import reportlab
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, BooleanObject, DictionaryObject, NameObject, NumberObject,
                           TextStringObject)
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from pdfmanager import core, gui, redact, redactcontent
from pdfmanager.cli import main

VERA = os.path.join(os.path.dirname(reportlab.__file__), "fonts", "Vera.ttf")
pdfmetrics.registerFont(TTFont("Vera", VERA))


# -- helpers ------------------------------------------------------------------------------------

def chars(path, page=0, password=None):
    """[(character, box)] of a page as pdfium reads it, without spaces."""
    doc = pdfium.PdfDocument(str(path), password=password)
    try:
        tp = doc[page].get_textpage()
        found = []
        for i in range(tp.count_chars()):
            ch = chr(pdfium_c.FPDFText_GetUnicode(tp.raw, i))
            if not ch.isspace() and not pdfium_c.FPDFText_IsGenerated(tp.raw, i) == 1:
                found.append((ch, tp.get_charbox(i)))
        tp.close()
        return found
    finally:
        doc.close()


def text(path, page=0, password=None):
    doc = pdfium.PdfDocument(str(path), password=password)
    try:
        tp = doc[page].get_textpage()
        result = tp.get_text_range()
        tp.close()
        return result
    finally:
        doc.close()


def centre_in(box, area):
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return area[0] < cx < area[2] and area[1] < cy < area[3]


def assert_rest_in_place(before, after, areas, slack=0.25):
    """Every letter not under an area is still there, in the same place,
    and no letter is left under one. (pdfium rounds font widths to whole
    units, so letters after removed ones may seem to move a hair across.)"""
    for ch, box in after:
        assert not any(centre_in(box, a) for a in areas), f"{ch!r} left under a box"
    left = list(after)
    for ch, box in before:
        if any(centre_in(box, a) for a in areas):
            continue
        match = next((o for o in left if o[0] == ch and abs(o[1][0] - box[0]) < slack
                      and abs(o[1][2] - box[2]) < slack and abs(o[1][1] - box[1]) < 0.01
                      and abs(o[1][3] - box[3]) < 0.01), None)
        assert match is not None, f"{ch!r} at {box} moved or vanished"
        left.remove(match)


def everything_in(path):
    """All bytes of a PDF with every stream decoded, to look for leftovers."""
    pdf = pikepdf.open(str(path))
    parts = []
    for obj in pdf.objects:
        if isinstance(obj, pikepdf.Stream):
            try:
                parts.append(obj.read_bytes())
            except pikepdf.PdfError:
                parts.append(obj.read_raw_bytes())
        else:
            parts.append(bytes(str(obj), "utf-8", "replace"))
    return b"\n".join(parts)


def images_on(path, page=0):
    """{name: PIL image} of a page's picture XObjects (decoded)."""
    pdf = pikepdf.open(str(path))
    found = {}
    for name, obj in (pdf.pages[page].Resources.get("/XObject") or {}).items():
        if obj.get("/Subtype") == "/Image":
            found[name] = (pikepdf.PdfImage(obj).as_pil_image().convert("RGB"), str(obj.get("/Filter")))
    return found


def noise(width=60, height=40, seed=7):
    """A picture of bright, varied pixels (none of them dark)."""
    import random

    rnd = random.Random(seed)
    data = bytes(rnd.randrange(120, 256) for _ in range(width * height * 3))
    return Image.frombytes("RGB", (width, height), data)


def text_pdf(path, rotate=0):
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.setFont("Helvetica", 14)
    c.drawString(72, 700, "Call John Smith at 555-1234 or mail john@example.com.")
    c.setFont("Vera", 12)
    c.drawString(72, 650, "Subset TrueType: John Smith lives here.")
    t = c.beginText(72, 600)
    t.setFont("Times-Roman", 12)
    t.setCharSpace(0.8)
    t.setWordSpace(2.5)
    t.setHorizScale(90)
    t.setRise(1)
    t.textLine("Spaced out John Smith with care")
    c.drawText(t)
    c.drawString(72, 560, "A line with nothing secret at all.")
    c.showPage()
    c.setFont("Helvetica", 14)
    c.drawString(72, 700, "Page two mentions nobody.")
    c.showPage()
    c.save()
    if rotate:
        w = PdfWriter(clone_from=str(path))
        w.pages[0].rotate(rotate)
        w.write(str(path))
    return path


def cid_pdf(path):
    """Text in a Type0 (CID) font with Identity-H, as pdfium writes it."""
    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(612, 792)
    data = open(VERA, "rb").read()
    buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
    font = pdfium_c.FPDFText_LoadFont(pdf.raw, buf, len(data), pdfium_c.FPDF_FONT_TRUETYPE, True)
    for y, line in ((700, "Call John Smith at 555-1234 now."), (650, "Second line stays here")):
        obj = pdfium_c.FPDFPageObj_CreateTextObj(pdf.raw, font, 14.0)
        ws = (line + "\0").encode("utf-16-le")
        pdfium_c.FPDFText_SetText(obj, ctypes.cast(ctypes.c_char_p(ws), pdfium_c.FPDF_WIDESTRING))
        pdfium_c.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 72, y)
        pdfium_c.FPDFPage_InsertObject(page.raw, obj)
    pdfium_c.FPDFPage_GenerateContent(page.raw)
    pdf.save(str(path))
    pdf.close()
    return path


def raw_content_pdf(path, content, resources=None):
    """A one-page PDF with the given content stream and Helvetica as /F1."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name.Helvetica,
                                                Encoding=pikepdf.Name.WinAnsiEncoding))
    res = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    for key, value in (resources or {}).items():
        res[key] = value
    page = pdf.add_blank_page(page_size=(612, 792))
    page.obj.Resources = res
    page.obj.Contents = pdf.make_stream(content)
    pdf.save(str(path))
    return path


# -- text ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("rotate", [0, 90])
def test_text_is_removed_and_the_rest_stays_in_place(tmp_path, rotate):
    src = text_pdf(tmp_path / "s.pdf", rotate)
    out = tmp_path / "out.pdf"
    report = redact.redact(src, out, terms=["John Smith"], patterns=[redact.PRESETS["emails"]])
    assert report == {1: 4} and report.pictures == {} and report.method == "remove"
    left = text(out)
    assert "John Smith" not in left and "Smith" not in left and "example.com" not in left
    for kept in ("Call", "at 555-1234", "Subset TrueType:", "lives here.", "Spaced out",
                 "with care", "A line with nothing secret at all."):
        assert kept in left
    areas = redact.find_text(src, ["John Smith"], [redact.PRESETS["emails"]])[0]
    assert_rest_in_place(chars(src), chars(out), areas)
    assert "nobody" in text(out, 1)
    assert PdfReader(str(out)).pages[0].rotation == rotate
    assert b"Smith" not in everything_in(out)


def test_cid_font_text(tmp_path):
    src = cid_pdf(tmp_path / "cid.pdf")
    out = tmp_path / "out.pdf"
    report = redact.redact(src, out, terms=["John Smith"])
    assert report == {1: 1} and report.pictures == {}
    assert "Smith" not in text(out) and "Call" in text(out) and "Second line stays here" in text(out)
    assert_rest_in_place(chars(src), chars(out), redact.find_text(src, ["John Smith"])[0])


def test_tj_kerning_and_strings_split_mid_word(tmp_path):
    content = (b"BT /F1 14 Tf 72 700 Td [(Call J) -40 (ohn S) 120 (mi) -15.5 (th ) 30 (now, please.)] TJ "
               b"0 -20 Td (Next) Tj ( line) Tj ET")
    src = raw_content_pdf(tmp_path / "tj.pdf", content)
    out = tmp_path / "out.pdf"
    assert redact.redact(src, out, terms=["John Smith"]).pictures == {}
    assert "Smith" not in text(out) and "now, please." in text(out) and "Next line" in text(out)
    assert_rest_in_place(chars(src), chars(out), redact.find_text(src, ["John Smith"])[0])


def test_quote_operators(tmp_path):
    content = (b"BT /F1 12 Tf 14 TL 72 700 Td (First line) Tj (Second John Smith line) ' "
               b"2 1 (Third John Smith line) \" (Fourth line) ' ET")
    src = raw_content_pdf(tmp_path / "q.pdf", content)
    out = tmp_path / "out.pdf"
    assert redact.redact(src, out, terms=["John Smith"]).pictures == {}
    assert "Smith" not in text(out)
    assert_rest_in_place(chars(src), chars(out), redact.find_text(src, ["John Smith"])[0])


def test_a_box_over_part_of_a_word(tmp_path):
    src = text_pdf(tmp_path / "s.pdf")
    before = chars(src)
    smith = [box for ch, box in before if box[1] > 695 and box[3] < 715][8:13]  # "Smith"
    box = (smith[1][0] + 0.5, 695, smith[3][2] - 0.5, 715)  # over "mit"
    out = tmp_path / "out.pdf"
    assert redact.redact(src, out, boxes={0: [box]}).pictures == {}
    after = chars(out)
    assert_rest_in_place(before, after, [box])
    line = "".join(ch for ch, b in after if 695 < b[1] < 715)
    assert "JohnSh" in line  # S and h stay where they were


# -- pictures -----------------------------------------------------------------------------------

def picture_pdf(path, jpeg=False, twice=False):
    picture = noise()
    if jpeg:
        buf = io.BytesIO()
        picture.save(buf, "JPEG", quality=92)
        buf.seek(0)
        picture = buf  # reportlab keeps a JPEG file as it is
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.drawImage(ImageReader(picture), 72, 500, 120, 80)
    c.setFont("Helvetica", 12)
    c.drawString(72, 470, "Caption under the picture")
    c.showPage()
    if twice:
        if jpeg:
            picture.seek(0)
        c.drawImage(ImageReader(picture), 72, 500, 120, 80)  # the same picture object
        c.showPage()
    c.save()
    return path


@pytest.mark.parametrize("jpeg", [False, True])
def test_part_of_a_picture_is_painted_black(tmp_path, jpeg):
    src = picture_pdf(tmp_path / "p.pdf", jpeg=jpeg)
    out = tmp_path / "out.pdf"
    # The left half of the picture (72..132 of 72..192 points).
    assert redact.redact(src, out, boxes={0: [(60, 490, 132, 590)]}).pictures == {}
    [(picture, kind)] = images_on(out).values()
    assert ("DCTDecode" in kind) == jpeg
    original = list(images_on(src).values())[0][0]
    w, h = picture.size
    for x, y in ((2, 2), (w // 4, h // 2), (w // 2 - 2, h - 3)):
        assert max(picture.getpixel((x, y))) < (60 if jpeg else 1)
    for x, y in ((w // 2 + 3, 3), (w - 3, h // 2), (w * 3 // 4, h - 4)):
        assert max(abs(a - b) for a, b in zip(picture.getpixel((x, y)), original.getpixel((x, y)))) \
            < (40 if jpeg else 1)
    assert "Caption under the picture" in text(out)


def test_a_scan_with_hidden_ocr_text(tmp_path):
    """A scanned page: a JPEG of the page with invisible OCR words over it.
    The word goes from both; the rest stays searchable."""
    scan = Image.new("RGB", (850, 1100), "white")
    from PIL import ImageDraw

    ImageDraw.Draw(scan).rectangle([100, 120, 400, 160], fill=(40, 40, 40))  # "ink" of the word
    buf = io.BytesIO()
    scan.save(buf, "JPEG", quality=90)
    buf.seek(0)
    src = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    c.drawImage(ImageReader(buf), 0, 0, 612, 792)
    t = c.beginText(72, 680)
    t.setTextRenderMode(3)  # invisible, as OCR programs write it
    t.setFont("Helvetica", 20)
    t.textLine("Confidential Smith report")
    c.drawText(t)
    c.showPage()
    c.save()
    out = tmp_path / "out.pdf"
    report = redact.redact(src, out, terms=["Smith"])
    assert report.pictures == {}
    assert "Smith" not in text(out) and "Confidential" in text(out) and "report" in text(out)
    [(picture, kind)] = images_on(out).values()
    assert "DCTDecode" in kind
    [box] = redact.find_text(src, ["Smith"])[0]
    x, y = (box[0] + box[2]) / 2 * 850 / 612, (792 - (box[1] + box[3]) / 2) * 1100 / 792
    assert max(picture.getpixel((int(x), int(y)))) < 40
    assert min(picture.getpixel((700, 900))) > 230  # paper elsewhere stays white


def test_a_covered_picture_is_dropped(tmp_path):
    src = picture_pdf(tmp_path / "p.pdf")
    out = tmp_path / "out.pdf"
    redact.redact(src, out, boxes={0: [(70, 498, 194, 582)]})
    assert images_on(out) == {}
    assert noise().tobytes()[:60] not in everything_in(out)
    assert "Caption under the picture" in text(out)


def test_a_shared_picture_is_copied_first(tmp_path):
    src = picture_pdf(tmp_path / "p.pdf", twice=True)
    out = tmp_path / "out.pdf"
    redact.redact(src, out, boxes={0: [(60, 490, 132, 590)]})
    first = list(images_on(out, 0).values())[0][0]
    second = list(images_on(out, 1).values())[0][0]
    assert first.getpixel((2, 2)) == (0, 0, 0)
    assert second.tobytes() == list(images_on(src, 1).values())[0][0].tobytes()


def test_inline_picture_and_soft_mask(tmp_path):
    picture = noise(20, 10)
    data = zlib.compress(picture.tobytes())
    content = (b"q 100 0 0 50 72 500 cm BI /W 20 /H 10 /CS /RGB /BPC 8 /F /Fl ID\n" + data
               + b"\nEI Q BT /F1 12 Tf 72 470 Td (Inline caption) Tj ET")
    src = raw_content_pdf(tmp_path / "inline.pdf", content)
    out = tmp_path / "out.pdf"
    assert redact.redact(src, out, boxes={0: [(60, 490, 122, 560)]}).pictures == {}
    [(painted, _)] = images_on(out).values()
    assert painted.getpixel((1, 1)) == (0, 0, 0) and painted.getpixel((19, 9)) == picture.getpixel((19, 9))
    assert "Inline caption" in text(out)

    # A picture with transparency: the mask is made opaque under the box too.
    rgba = noise(40, 40).convert("RGBA")
    rgba.putalpha(Image.new("L", (40, 40), 90))
    c = canvas.Canvas(str(tmp_path / "alpha.pdf"), pagesize=(612, 792))
    c.drawImage(ImageReader(rgba), 72, 500, 80, 80, mask="auto")
    c.showPage()
    c.save()
    redact.redact(tmp_path / "alpha.pdf", out, boxes={0: [(60, 490, 112, 600)]})
    pdf = pikepdf.open(str(out))
    [image] = [o for o in pdf.pages[0].Resources.XObject.values() if o.Subtype == "/Image"]
    mask = pikepdf.PdfImage(image.SMask).as_pil_image()
    assert mask.getpixel((2, 2)) == 255 and mask.getpixel((38, 38)) == 90


# -- drawings and forms -------------------------------------------------------------------------

def test_shapes_inside_a_box_go_lines_across_stay(tmp_path):
    content = (b"q 1 0 0 rg 100 600 20 20 re f Q "        # inside the box: goes
               b"q 0 0 1 RG 2 w 50 610 m 300 610 l S Q "   # crosses it: stays
               b"q 0 1 0 rg 400 600 20 20 re f Q")         # elsewhere: stays
    src = raw_content_pdf(tmp_path / "shapes.pdf", content)
    out = tmp_path / "out.pdf"
    redact.redact(src, out, boxes={0: [(90, 590, 130, 630)]})
    with pikepdf.open(str(out)) as pdf:
        body = pdf.pages[0].Contents.read_bytes()
    assert b"100 600 20 20 re" not in body
    assert b"50 610 m" in body and b"400 600 20 20 re" in body


def test_forms_are_edited_in_a_copy(tmp_path):
    src = tmp_path / "form.pdf"
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    c.beginForm("stamp")
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "Stamp says John Smith approved")
    c.endForm()
    c.doForm("stamp")
    c.showPage()
    c.doForm("stamp")
    c.showPage()
    c.save()
    out = tmp_path / "out.pdf"
    assert redact.redact(src, out, terms=["John Smith"], boxes={}) .pictures == {}
    # Both pages had the words: both lose them.
    for page in (0, 1):
        assert "Smith" not in text(out, page) and "approved" in text(out, page)
    out2 = tmp_path / "out2.pdf"
    redact.redact(src, out2, boxes={0: [(150, 690, 210, 715)]})
    assert "Smith" not in text(out2, 0) and "John Smith" in text(out2, 1)


# -- comments, fields, tags, bookmarks ------------------------------------------------------------

def test_comments_and_fields_under_a_box_go_others_stay(tmp_path):
    src = tmp_path / "f.pdf"
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    c.setFont("Helvetica", 14)
    c.drawString(72, 700, "Call John Smith today.")
    c.acroForm.textfield(name="secret", value="SSNVALUE123", x=72, y=600, width=150, height=20)
    c.acroForm.textfield(name="other", value="keepme", x=72, y=400, width=150, height=20)
    c.linkURL("https://example.com/john", (100, 695, 180, 715))
    c.showPage()
    c.save()
    out = tmp_path / "out.pdf"
    redact.redact(src, out, terms=["John Smith"], boxes={0: [(70, 595, 230, 625)]})
    reader = PdfReader(str(out))
    assert set(reader.get_fields()) == {"other"}
    subtypes = [a.get_object()["/Subtype"] for a in reader.pages[0]["/Annots"]]
    assert subtypes == ["/Widget"]
    left = everything_in(out)
    assert b"SSNVALUE123" not in left and b"example.com/john" not in left and b"keepme" in left


def test_tags_lose_replacement_text_that_repeats_removed_words(tmp_path):
    src = tmp_path / "tagged.pdf"
    content = (b"BT /F1 14 Tf 72 700 Td /Span <</ActualText (Call TOPSECRETWORD now)>> BDC "
               b"(Call TOPSECRETWORD now) Tj EMC ET BT /F1 14 Tf 72 650 Td (Plain line) Tj ET")
    raw_content_pdf(src, content)
    w = PdfWriter(clone_from=str(src))
    page = w.pages[0]

    def element(text):
        return w._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/StructElem"), NameObject("/S"): NameObject("/P"),
            NameObject("/Pg"): page.indirect_reference, NameObject("/ActualText"): TextStringObject(text),
            NameObject("/K"): ArrayObject([NumberObject(0)])}))

    first, second = element("Call TOPSECRETWORD now"), element("Plain line")
    w._root_object[NameObject("/StructTreeRoot")] = w._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/StructTreeRoot"), NameObject("/K"): ArrayObject([first, second])}))
    w._root_object[NameObject("/MarkInfo")] = DictionaryObject({NameObject("/Marked"): BooleanObject(True)})
    w.add_outline_item("About TOPSECRETWORD", 0)
    w.add_outline_item("Contents", 0)
    w.write(str(src))
    out = tmp_path / "out.pdf"
    report = redact.redact(src, out, terms=["TOPSECRETWORD"])
    assert report.pictures == {} and report.bookmarks == 1
    assert b"TOPSECRETWORD" not in everything_in(out)
    reader = PdfReader(str(out))
    kids = reader.trailer["/Root"]["/StructTreeRoot"]["/K"]
    assert "/ActualText" not in kids[0] and kids[1]["/ActualText"] == "Plain line"
    assert [o.title for o in reader.outline] == ["About " + redact.BLACKED, "Contents"]
    assert "Call" in text(out) and "Plain line" in text(out)


# -- falling back, passwords, the choice of method ---------------------------------------------

def test_pages_that_cant_be_edited_become_pictures(tmp_path):
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
    src = tmp_path / "jp.pdf"
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    c.setFont("HeiseiMin-W3", 14)
    c.drawString(72, 700, "Name: John Smith")
    c.showPage()
    c.setFont("Helvetica", 14)
    c.drawString(72, 700, "Also John Smith here")
    c.showPage()
    c.save()
    out = tmp_path / "out.pdf"
    report = redact.redact(src, out, terms=["John Smith"])
    assert report == {1: 1, 2: 1}
    assert list(report.pictures) == [1] and "encoding" in report.pictures[1]
    assert text(out, 0).strip() == ""  # a picture now
    assert "Also" in text(out, 1) and "Smith" not in text(out, 1)


def test_a_failed_check_falls_back(tmp_path, monkeypatch):
    src = text_pdf(tmp_path / "s.pdf")
    monkeypatch.setattr(redactcontent, "_same_places", lambda old, new, removed=0: False)
    report = redact.redact(src, tmp_path / "out.pdf", terms=["John Smith"])
    assert report.pictures == {1: "the rest of the text would have moved"}
    assert text(tmp_path / "out.pdf").strip() == ""


def test_image_method_still_makes_pictures(tmp_path):
    src = text_pdf(tmp_path / "s.pdf")
    report = redact.redact(src, tmp_path / "out.pdf", terms=["John Smith"], method="image")
    assert report == {1: 3} and report.method == "image"
    assert text(tmp_path / "out.pdf").strip() == ""
    with pytest.raises(core.PdfManagerError):
        redact.redact(src, tmp_path / "x.pdf", terms=["x"], method="paint")


def test_encrypted_input(tmp_path):
    plain = text_pdf(tmp_path / "s.pdf")
    src = tmp_path / "locked.pdf"
    core.encrypt(plain, src, "pw")
    out = tmp_path / "out.pdf"
    report = redact.redact(src, out, terms=["John Smith"], password="pw")
    assert report.pictures == {}
    assert "Smith" not in text(out) and "Call" in text(out)


# -- the app, the reader and the command line -----------------------------------------------------

def test_gui_tool_notes_and_method(tmp_path, monkeypatch):
    src = text_pdf(tmp_path / "s.pdf")
    (tmp_path / "w1").mkdir()
    (tmp_path / "w2").mkdir()
    notes = []
    name, data, _ = gui.run_operation("redact", [src], {"terms": "John Smith"}, work=tmp_path / "w1",
                                      notes=notes)
    assert name == "s_redacted.pdf" and notes == ["s.pdf: blacked out 3 areas on page 1"]
    notes = []
    monkeypatch.setattr(redactcontent, "_same_places", lambda old, new, removed=0: False)
    gui.run_operation("redact", [src], {"terms": "John Smith", "method": "remove"},
                      work=tmp_path / "w2", notes=notes)
    assert notes == ["s.pdf: blacked out 3 areas on page 1; page 1 became a picture "
                     "(the rest of the text would have moved)"]
    tool = gui.OPS["redact"]
    assert [f for f in tool["fields"] if f["key"] == "method"][0]["default"] == "remove"


def test_cli_method(tmp_path, capsys):
    src = text_pdf(tmp_path / "s.pdf")
    assert main(["redact", str(src), "-t", "John Smith", "-o", str(tmp_path / "a.pdf")]) == 0
    assert "Smith" not in text(tmp_path / "a.pdf") and "Call" in text(tmp_path / "a.pdf")
    assert main(["redact", str(src), "-t", "John Smith", "--method", "image",
                 "-o", str(tmp_path / "b.pdf")]) == 0
    assert text(tmp_path / "b.pdf").strip() == ""
    capsys.readouterr()


@pytest.fixture
def app(tmp_path, monkeypatch):
    from pdfmanager import appdata, recipes

    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(gui, "data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(recipes, "data_dir", lambda: tmp_path / "appdata")
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    yield base, token
    srv.shutdown()
    srv.server_close()


def post_file(base, token, path, fields, data):
    boundary = "pdfmtestboundary"
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\n'
                 f'Content-Type: application/pdf\r\n\r\n'.encode() + data + b"\r\n")
    request = urllib.request.Request(base + path, data=b"".join(parts) + f"--{boundary}--\r\n".encode(),
                                     headers={"X-Pdfm-Token": token,
                                              "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def test_reader_apply_endpoint(app, tmp_path):
    base, token = app
    src = text_pdf(tmp_path / "s.pdf")
    locked = tmp_path / "locked.pdf"
    core.encrypt(src, locked, "pw")
    smith = redact.find_text(src, ["John Smith"])[0]
    boxes = {"1": [list(b) for b in smith]}
    status, headers, data = post_file(base, token, "/api/redact/apply",
                                      {"boxes": json.dumps(boxes), "password": "pw"}, locked.read_bytes())
    assert status == 200, data
    out = tmp_path / "out.pdf"
    out.write_bytes(data)
    assert PdfReader(str(out)).is_encrypted  # stays protected
    assert "Smith" not in text(out, password="pw") and "Call" in text(out, password="pw")
    assert json.loads(headers["X-Pdfm-Redacted"]) == {"areas": 3, "pages": [1], "pictures": {}}
    status, _, data = post_file(base, token, "/api/redact/apply", {"boxes": "{}"}, src.read_bytes())
    assert status == 400 and b"mark" in data
    status, _, data = post_file(base, token, "/api/redact/apply", {"boxes": "[oops"}, src.read_bytes())
    assert status == 400


# -- kinds of pictures ------------------------------------------------------------------------------

def raw_image_pdf(path, width, height, data, **info):
    """A page showing one picture XObject made from raw samples at 72,500 (160 x 100 pt)."""
    pdf = pikepdf.new()
    image = pdf.make_stream(zlib.compress(data), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                            Width=width, Height=height, Filter=pikepdf.Name.FlateDecode, **info)
    page = pdf.add_blank_page(page_size=(612, 792))
    page.obj.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=image))
    page.obj.Contents = pdf.make_stream(b"q 0 0 1 rg 160 0 0 100 72 500 cm /Im0 Do Q")
    pdf.save(str(path))
    return path


def samples_after(path):
    with pikepdf.open(str(path)) as pdf:
        [image] = list(pdf.pages[0].Resources.XObject.values())
        return image.read_bytes()


@pytest.mark.parametrize("kind", ["gray1", "gray16", "cmyk", "indexed4", "stencil", "decode"])
def test_kinds_of_pictures(tmp_path, kind):
    """The covered half of each kind of picture gets the samples that mean
    black (or, for a stencil mask, "paint nothing"); the other half stays."""
    w, h = 16, 4
    if kind == "gray1":
        info, row, black = dict(ColorSpace=pikepdf.Name.DeviceGray, BitsPerComponent=1), b"\xff\xff", "0"
    elif kind == "gray16":
        info, row, black = dict(ColorSpace=pikepdf.Name.DeviceGray, BitsPerComponent=16), b"\xab\xcd" * w, "0" * 16
    elif kind == "cmyk":
        info, row, black = dict(ColorSpace=pikepdf.Name.DeviceCMYK, BitsPerComponent=8), b"\x10\x20\x30\x00" * w, \
            "1" * 32
    elif kind == "indexed4":
        palette = pikepdf.String(bytes([255, 255, 255, 200, 0, 0, 0, 0, 0, 90, 90, 90]))
        info = dict(ColorSpace=pikepdf.Array([pikepdf.Name.Indexed, pikepdf.Name.DeviceRGB, 3, palette]),
                    BitsPerComponent=4)
        row, black = b"\x01" * (w // 2), "0010"  # index 2 is the black one
    elif kind == "stencil":
        info, row, black = dict(ImageMask=True), b"\x00\x00", "1"  # 0 = paint; 1 = leave alone
    else:  # an inverted grey picture: sample 255 is black
        info = dict(ColorSpace=pikepdf.Name.DeviceGray, BitsPerComponent=8, Decode=pikepdf.Array([1, 0]))
        row, black = b"\x10" * w, "1" * 8
    data = row * h
    src = raw_image_pdf(tmp_path / f"{kind}.pdf", w, h, data, **info)
    out = tmp_path / "out.pdf"
    # The left half of the picture: 72..152 points.
    assert redact.redact(src, out, boxes={0: [(60, 490, 152, 610)]}).pictures == {}
    new = samples_after(out)
    bits = "".join(f"{b:08b}" for b in new)
    old_bits = "".join(f"{b:08b}" for b in data)
    stride, half = len(row) * 8, len(black) * (w // 2)
    for y in range(h):
        line, old_line = bits[y * stride:(y + 1) * stride], old_bits[y * stride:(y + 1) * stride]
        assert line[:half] == black * (w // 2)
        assert line[half:2 * half] == old_line[half:2 * half]


def test_fax_pictures_are_blacked_out(tmp_path):
    """A black-and-white scan stored as CCITT fax (Group 4)."""
    w, h = 64, 32
    stripes = Image.frombytes("1", (w, h), b"\x00\xff" * (w // 16) * h)
    buf = io.BytesIO()
    stripes.save(buf, "TIFF", compression="group4")
    tiff = Image.open(io.BytesIO(buf.getvalue()))
    start, size = tiff.tag_v2[273][0], tiff.tag_v2[279][0]
    g4 = buf.getvalue()[start:start + size]
    fax = tmp_path / "fax.pdf"
    pdf = pikepdf.new()
    image = pikepdf.Stream(pdf, b"")
    image.write(g4, filter=pikepdf.Name.CCITTFaxDecode,
                decode_parms=pikepdf.Dictionary(K=-1, Columns=w, Rows=h,
                                                BlackIs1=tiff.tag_v2.get(262) == 1))
    image.Type, image.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    image.Width, image.Height, image.BitsPerComponent = w, h, 1
    image.ColorSpace = pikepdf.Name.DeviceGray
    page = pdf.add_blank_page(page_size=(612, 792))
    page.obj.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=pdf.make_indirect(image)))
    page.obj.Contents = pdf.make_stream(b"q 160 0 0 100 72 500 cm /Im0 Do Q")
    pdf.save(str(fax))
    assert list(images_on(fax).values())[0][0].convert("1").tobytes() == stripes.tobytes()
    out = tmp_path / "out.pdf"
    assert redact.redact(fax, out, boxes={0: [(60, 490, 152, 610)]}).pictures == {}
    [(picture, _)] = images_on(out).values()
    original = list(images_on(fax).values())[0][0]
    assert picture.getpixel((2, 2)) == (0, 0, 0) and picture.getpixel((w // 2 - 2, h - 2)) == (0, 0, 0)
    assert [picture.getpixel((x, 5)) for x in range(w // 2 + 2, w)] == \
        [original.getpixel((x, 5)) for x in range(w // 2 + 2, w)]
