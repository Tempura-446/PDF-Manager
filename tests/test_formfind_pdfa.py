"""Finding the fields of a printed form, radio buttons and dropdowns, and
checking PDF/A (0.12.0)."""

import json
import warnings
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pdfmanager import archive, core, formdetect, forms, gui, pdfacheck
from pdfmanager.cli import main
from test_more import app, call, multipart  # noqa: F401  (app is a fixture)

SYMBOL_FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                r"C:\Windows\Fonts\seguisym.ttf", "/Library/Fonts/Arial Unicode.ttf",
                "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"]


def _symbol_font():
    """A font with ☐ and ○ in it, registered with reportlab, or None."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for path in SYMBOL_FONTS:
        if Path(path).is_file():
            pdfmetrics.registerFont(TTFont("Symbols", path))
            return "Symbols"
    return None


def make_form(path, symbols=False):
    """A printed form: lines, underscores, dots, boxes, tick boxes, a
    comment box, a table of data and a signature line."""
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 740, "Application Form")
    c.line(72, 730, 540, 730)  # a divider, not a field
    c.setFont("Helvetica", 11)
    c.drawString(72, 700, "Full name:")
    c.line(140, 698, 400, 698)
    c.drawString(72, 670, "Email:")
    c.drawString(110, 670, "_" * 40)
    c.drawString(72, 640, "City " + "." * 46)
    c.rect(72, 560, 200, 40)
    c.rect(272, 560, 200, 40)
    c.setFont("Helvetica", 7)
    c.drawString(76, 590, "Phone")
    c.drawString(276, 590, "Date of birth")
    c.setFont("Helvetica", 11)
    c.rect(72, 520, 10, 10)
    c.drawString(88, 521, "Yes, send me news")
    c.rect(220, 520, 10, 10)
    c.drawString(236, 521, "I agree")
    if symbols:
        c.setFont(symbols, 11)
        c.drawString(72, 495, "\u2610 Glyph box")
        c.drawString(250, 495, "Size: \u25cb Small  \u25cb Medium  \u25cb Large")
        c.setFont("Helvetica", 11)
    c.drawString(72, 470, "Comments")
    c.rect(72, 380, 400, 80)
    for i, (a, b) in enumerate([("Item", "Price"), ("Apple", "1.00"), ("Pear", "2.50")]):
        y = 330 - i * 18
        c.rect(72, y, 150, 18)
        c.rect(222, y, 150, 18)
        c.drawString(76, y + 5, a)
        c.drawString(226, y + 5, b)
    c.drawString(72, 200, "Signature:")
    c.line(140, 198, 340, 198)
    c.drawString(360, 200, "Date:")
    c.line(395, 198, 520, 198)
    c.showPage()
    c.save()
    return path


def _by_name(found):
    return {f["name"]: f for items in found.values() for f in items}


def _near(rect, expected, slack=3):
    return all(abs(a - b) <= slack for a, b in zip(rect, expected))


# --------------------------------------------------------------------------
# Finding fields
# --------------------------------------------------------------------------

def test_detect_finds_the_fields_of_a_printed_form(tmp_path):
    found = formdetect.detect(make_form(tmp_path / "form.pdf"))
    fields = _by_name(found)
    assert set(fields) == {"Full name", "Email", "City", "Phone", "Date of birth",
                           "Yes, send me news", "I agree", "Comments", "Signature", "Date"}
    assert _near(fields["Full name"]["rect"], [140, 699, 400, 718])     # above its line
    assert _near(fields["Email"]["rect"][:2], [110, 670])                # on the underscores
    assert fields["City"]["rect"][0] >= 90                               # after "City", on the dots
    assert _near(fields["Phone"]["rect"], [73, 561, 271, 588])           # under the caption
    assert fields["Yes, send me news"]["type"] == "checkbox"
    assert _near(fields["I agree"]["rect"], [220.5, 520.5, 229.5, 529.5], 1)
    assert fields["Comments"]["type"] == "multiline"
    assert fields["Signature"]["type"] == "signature"
    assert all(f["border"] is False for f in fields.values())
    # the table of data and the divider under the heading aren't fields
    assert not any(f["rect"][1] < 340 and f["rect"][3] > 290 for f in fields.values())
    assert not any(f["rect"][1] > 725 for f in fields.values())


def test_detect_reads_a_turned_page_and_a_slanted_scan(tmp_path):
    src = make_form(tmp_path / "form.pdf")
    upright = formdetect.detect(src)
    w = PdfWriter(clone_from=src)
    w.pages[0].rotate(90)
    w.write(tmp_path / "turned.pdf")
    assert formdetect.detect(tmp_path / "turned.pdf") == upright

    # A scan: a picture only (no text, so no names), slightly turned.
    import pypdfium2 as pdfium
    from PIL import Image

    doc = pdfium.PdfDocument(str(src))
    image = doc[0].render(scale=200 / 72, grayscale=True).to_pil()
    doc.close()
    image.rotate(0.6, fillcolor=255, resample=Image.BICUBIC).save(tmp_path / "scan.pdf",
                                                                  resolution=200)
    scanned = [f for items in formdetect.detect(tmp_path / "scan.pdf").values() for f in items]
    kinds = [f["type"] for f in scanned]
    assert kinds.count("checkbox") == 2 and kinds.count("multiline") == 1
    assert any(_near(f["rect"], [140, 699, 400, 718], 4) for f in scanned)  # the name line
    assert any(_near(f["rect"], [72, 561, 270, 587], 4) for f in scanned)   # the phone box
    assert all(f["name"].startswith(("Text", "Checkbox")) for f in scanned)


def test_detect_tick_boxes_and_round_buttons_drawn_as_characters(tmp_path):
    font = _symbol_font()
    if not font:
        pytest.skip("no font with ☐ and ○ here")
    fields = _by_name(formdetect.detect(make_form(tmp_path / "form.pdf", symbols=font)))
    assert fields["Glyph box"]["type"] == "checkbox"
    radios = [f for items in formdetect.detect(tmp_path / "form.pdf").values() for f in items
              if f["type"] == "radio"]
    assert [(f["name"], f["value"]) for f in radios] == [
        ("Size", "Small"), ("Size", "Medium"), ("Size", "Large")]


def test_detect_leaves_out_fields_already_there(tmp_path):
    src = make_form(tmp_path / "form.pdf")
    found = formdetect.detect(src)
    first = found[1][:3]
    forms.add_fields(src, tmp_path / "some.pdf", [{**f, "page": 1} for f in first])
    again = formdetect.detect(tmp_path / "some.pdf")
    names = {f["name"] for f in again[1]}
    assert not names & {f["name"] for f in first}
    assert len(again[1]) == len(found[1]) - 3
    assert formdetect.detect(tmp_path / "some.pdf", pages=[2]) == {}


def test_detect_on_a_page_without_a_form(tmp_path):
    c = canvas.Canvas(str(tmp_path / "letter.pdf"))
    c.drawString(72, 700, "Dear reader, this is a plain letter with no form in it.")
    c.save()
    assert formdetect.detect(tmp_path / "letter.pdf") == {}


# --------------------------------------------------------------------------
# Radio buttons and dropdowns
# --------------------------------------------------------------------------

def test_radio_buttons_and_dropdowns(tmp_path):
    src = make_form(tmp_path / "form.pdf")
    names = forms.add_fields(src, tmp_path / "out.pdf", [
        {"page": 1, "type": "radio", "name": "Size", "value": "Small", "rect": [280, 490, 290, 500]},
        {"page": 1, "type": "radio", "name": "Size", "value": "Large", "rect": [330, 490, 340, 500]},
        {"page": 1, "type": "radio", "name": "Size", "value": "Large", "rect": [380, 490, 390, 500]},
        {"page": 1, "type": "dropdown", "name": "Country", "options": "France, Spain\nItaly, Spain",
         "rect": [400, 640, 540, 660]},
        {"page": 1, "type": "dropdown", "name": "Other", "editable": True, "rect": [400, 610, 540, 630]},
    ])
    assert names == ["Country", "Other", "Size"]
    reader = PdfReader(tmp_path / "out.pdf")
    fields = reader.get_fields()
    size = fields["Size"]
    assert size["/FT"] == "/Btn" and size["/Ff"] & forms.RADIO and size["/V"] == "/Off"
    assert {"/Small", "/Large", "/Large 2"} <= set(size["/_States_"])
    assert fields["Country"]["/Opt"] == ["France", "Spain", "Italy"]
    assert fields["Country"]["/Ff"] & forms.COMBO and not fields["Country"]["/Ff"] & forms.EDIT
    assert fields["Other"]["/Ff"] & forms.EDIT
    kinds = [f["type"] for f in forms.existing_fields(tmp_path / "out.pdf")[1]]
    assert kinds.count("radio") == 3 and kinds.count("dropdown") == 2
    # Filling them in works.
    w = PdfWriter(clone_from=tmp_path / "out.pdf")
    w.update_page_form_field_values(w.pages[0], {"Size": "/Large", "Country": "Spain"},
                                    auto_regenerate=False)
    w.write(tmp_path / "filled.pdf")
    filled = PdfReader(tmp_path / "filled.pdf").get_fields()
    assert filled["Size"]["/V"] == "/Large" and filled["Country"]["/V"] == "Spain"


def test_radio_group_name_already_taken_and_parse_field(tmp_path):
    src = make_form(tmp_path / "form.pdf")
    forms.add_fields(src, tmp_path / "one.pdf", [{"page": 1, "type": "text", "name": "Size",
                                                  "rect": [72, 100, 200, 120]}])
    names = forms.add_fields(tmp_path / "one.pdf", tmp_path / "two.pdf", [
        {"page": 1, "type": "radio", "name": "Size", "value": "A", "rect": [280, 490, 290, 500]},
        {"page": 1, "type": "radio", "name": "Size", "value": "B", "rect": [300, 490, 310, 500]}])
    assert names == ["Size 2"]
    assert forms.parse_field("radio:1:1,2,3,4:size=Large") == {
        "type": "radio", "page": 1, "rect": [1, 2, 3, 4], "name": "size", "value": "Large"}
    assert forms.parse_field("dropdown:2:1,2,3,4:Country=France|Spain") == {
        "type": "dropdown", "page": 2, "rect": [1, 2, 3, 4], "name": "Country",
        "options": ["France", "Spain"]}


def test_form_tool_and_cli_find_fields(tmp_path, capsys, app):
    src = make_form(tmp_path / "form.pdf")
    assert main(["form", str(src), "--detect", "-f", "dropdown:1:420,600,540,620:Pick=A|B"]) == 0
    out = capsys.readouterr().out
    assert "11 fields" in out and "Pick" in out and "Full name" in out
    listed = main(["form", str(tmp_path / "form_form.pdf")])
    assert listed == 0 and "radio" not in capsys.readouterr().out

    base, token, _ = app
    body, headers = multipart({"password": ""}, [("file", src)])
    status, _, res = call(base, token, "/api/form/detect", body, headers)
    assert status == 200 and "Full name" in {f["name"] for f in json.loads(res)["fields"]["1"]}

    notes = []
    (tmp_path / "work").mkdir()
    name, data, _ = gui.run_operation("form", [src], {"detect": "true", "fields": "{}"},
                                      notes=notes, work=tmp_path / "work")
    assert name == "form_form.pdf" and "added 10 fields" in notes[0]
    blank = tmp_path / "blank.pdf"
    canvas.Canvas(str(blank)).save()
    (tmp_path / "work2").mkdir()
    with pytest.raises(gui.UserError, match="no fields were found"):
        gui.run_operation("form", [blank], {"detect": "true", "fields": "{}"},
                          work=tmp_path / "work2")


# --------------------------------------------------------------------------
# PDF/A
# --------------------------------------------------------------------------

def make_pdfa(path, part="1", conformance="B"):
    """A small file made to PDF/A rules without Ghostscript: an embedded
    font, an sRGB output intent and matching XMP metadata."""
    import pikepdf
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    pdfmetrics.registerFont(TTFont("Vera", "Vera.ttf"))
    base = path.with_name(path.stem + "_base.pdf")
    c = canvas.Canvas(str(base), initialFontName="Vera")
    c.setFont("Vera", 12)
    c.setFillColorRGB(0.2, 0.3, 0.8)
    c.drawString(72, 700, "Archive me")
    c.save()
    with pikepdf.open(base) as pdf:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
                meta.load_from_docinfo(pdf.docinfo)
                meta["pdfaid:part"] = part
                meta["pdfaid:conformance"] = conformance
        icc = pdf.make_stream(archive.srgb_v2_profile(), N=3)
        pdf.Root.OutputIntents = pikepdf.Array([pikepdf.Dictionary(
            Type=pikepdf.Name.OutputIntent, S=pikepdf.Name.GTS_PDFA1,
            OutputConditionIdentifier="sRGB", DestOutputProfile=icc)])
        pdf.save(path, object_stream_mode=pikepdf.ObjectStreamMode.disable)
    return path


def _messages(result):
    return [f"[{i['clause']}] {i['message']}" for i in result["issues"]]


def test_check_pdfa_passes_a_good_file_and_says_what_a_plain_one_lacks(tmp_path):
    good = pdfacheck.check(make_pdfa(tmp_path / "a1.pdf"), engine="builtin")
    assert good["passed"] and good["level"] == "1b" and good["claimed"] == "1B", _messages(good)
    assert good["engine"] == "ZPDF" and "veraPDF" in good["note"]
    assert pdfacheck.summary(good) == "passes PDF/A-1b (ZPDF's checks)"

    plain = pdfacheck.check(make_form(tmp_path / "form.pdf"), engine="builtin")
    assert not plain["passed"] and plain["level"] == "2b" and plain["claimed"] is None
    text = "\n".join(_messages(plain))
    assert "[6.2.11.4] The font Helvetica isn't embedded." in text
    assert "[6.6.4] The file has no XMP metadata" in text
    report = pdfacheck.report(plain, "form.pdf")
    assert "Result: FAILS" in report and "(page 1)" in report and "doesn't say it's PDF/A" in report
    # Against another level than the file claims.
    other = pdfacheck.check(tmp_path / "a1.pdf", level="2u", engine="builtin")
    assert any("says it's PDF/A-1b, not PDF/A-2u" in m for m in _messages(other))


def test_check_pdfa_finds_what_breaks_the_rules(tmp_path):
    import pikepdf

    src = make_pdfa(tmp_path / "a1.pdf")
    with pikepdf.open(src) as pdf:
        page = pdf.pages[0]
        page.Resources.ExtGState = pikepdf.Dictionary(GS1=pikepdf.Dictionary(ca=0.5))
        page.contents_add(pdf.make_stream(b"/GS1 gs 0 0 1 rg 10 10 50 50 re f"))
        pdf.Root.Names = pikepdf.Dictionary(JavaScript=pikepdf.Dictionary(Names=pikepdf.Array()))
        page.Annots = pdf.make_indirect(pikepdf.Array([
            pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Text,
                               Rect=[0, 0, 10, 10], F=2, Contents="hidden"),
            pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link,
                               Rect=[0, 20, 10, 30], F=4,
                               A=pikepdf.Dictionary(S=pikepdf.Name.Launch, F="calc.exe"))]))
        lzw = pikepdf.Stream(pdf, b"")
        lzw.write(b"\x80\x0b\x60\x50\x22\x0c\x0c\x85\x01", filter=pikepdf.Name.LZWDecode)
        pdf.Root.PieceInfo = pdf.make_indirect(lzw)
        pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array(), NeedAppearances=True)
        pdf.docinfo.Title = "Not the XMP title"
        pdf.save(tmp_path / "bad.pdf", compress_streams=False,
                 stream_decode_level=pikepdf.StreamDecodeLevel.none)
    result = pdfacheck.check(tmp_path / "bad.pdf", engine="builtin")
    text = "\n".join(_messages(result))
    for expected in ("[6.1.10] Part of the file is compressed with LZW",
                     "[6.4] The page uses transparency",
                     "[6.5.3] An annotation is hidden.",
                     "[6.5.3] An annotation (a comment, link or field) isn't set to print.",
                     "[6.6.1] A link or button starts a program.",
                     "[6.6.1] The document contains JavaScript.",
                     "[6.7.3] The document's title isn't the same",
                     "[6.9] The form asks readers to draw its fields themselves"):
        assert expected in text, text
    assert all(i["pages"] == [1] for i in result["issues"] if "annotation is hidden" in i["message"])

    # The same file as PDF/A-2: transparency is allowed, with an output intent.
    with pikepdf.open(tmp_path / "bad.pdf") as pdf:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
                meta["pdfaid:part"] = "2"
        pdf.save(tmp_path / "bad2.pdf", compress_streams=False,
                 stream_decode_level=pikepdf.StreamDecodeLevel.none)
    two = "\n".join(_messages(pdfacheck.check(tmp_path / "bad2.pdf", engine="builtin")))
    assert "transparency" not in two and "[6.1.7.2] Part of the file is compressed with LZW" in two


def test_check_pdfa_encrypted_and_unknown_level(tmp_path):
    src = make_pdfa(tmp_path / "a1.pdf")
    core.encrypt(src, tmp_path / "locked.pdf", "pw")
    result = pdfacheck.check(tmp_path / "locked.pdf", password="pw", engine="builtin")
    assert any("encrypted" in m for m in _messages(result))
    with pytest.raises(core.PdfManagerError, match="unknown PDF/A level"):
        pdfacheck.check(src, level="9z")
    with pytest.raises(core.PdfManagerError, match="unknown checker"):
        pdfacheck.check(src, engine="acrobat")


def test_srgb_v2_profile_is_a_working_version_2_profile():
    import io

    from PIL import Image, ImageCms

    data = archive.srgb_v2_profile()
    assert data[8] == 2 and data[12:16] == b"mntr" and data[36:40] == b"acsp"
    profile = ImageCms.ImageCmsProfile(io.BytesIO(data))
    to_srgb = ImageCms.buildTransform(profile, ImageCms.createProfile("sRGB"), "RGB", "RGB")
    pixel = ImageCms.applyTransform(Image.new("RGB", (1, 1), (200, 100, 50)), to_srgb).getpixel((0, 0))
    assert all(abs(a - b) <= 1 for a, b in zip(pixel, (200, 100, 50)))


@pytest.mark.skipif(not archive.find_ghostscript(), reason="needs Ghostscript")
@pytest.mark.parametrize("level", ["1", "2", "3"])
def test_converted_files_pass_the_check(tmp_path, level):
    out = archive.to_pdfa(make_form(tmp_path / "form.pdf"), tmp_path / "a.pdf", level=level)
    result = pdfacheck.check(out, engine="builtin")
    assert result["passed"] and result["level"] == f"{level}b", _messages(result)


VERAPDF_FAIL = """<?xml version="1.0" encoding="utf-8"?>
<report>
  <buildInformation><releaseDetails id="core" version="1.28.2" buildDate="x"/></buildInformation>
  <jobs><job>
    <item size="1000"><name>/tmp/x.pdf</name></item>
    <validationReport jobEndStatus="normal" profileName="PDF/A-2B validation profile"
        statement="PDF file is not compliant with Validation Profile requirements." isCompliant="false">
      <details passedRules="140" failedRules="1" passedChecks="5000" failedChecks="3">
        <rule specification="ISO 19005-2:2011" clause="6.2.11.4.1" testNumber="1" status="failed"
              failedChecks="3" tags="font">
          <description>The font programs for all fonts used for rendering within a conforming
            file shall be embedded within that file</description>
          <object>PDFont</object>
          <check status="failed"><context>root/document[0]/pages[0](3 0 obj PDPage)/contentStream[0]/operators[3]/font[0](Helvetica)</context></check>
          <check status="failed"><context>root/document[0]/pages[2](9 0 obj PDPage)/contentStream[0]/operators[3]/font[0](Helvetica)</context></check>
        </rule>
      </details>
    </validationReport>
  </job></jobs>
</report>"""


def test_verapdf_reports_are_read_and_used(tmp_path, monkeypatch):
    passed, issues, version, profile = pdfacheck.parse_verapdf(VERAPDF_FAIL)
    assert not passed and version == "1.28.2" and profile.startswith("PDF/A-2B")
    assert issues == [{"clause": "6.2.11.4.1", "count": 3, "pages": [1, 3], "message":
                       "The font programs for all fonts used for rendering within a conforming "
                       "file shall be embedded within that file"}]
    ok = VERAPDF_FAIL.replace('isCompliant="false"', 'isCompliant="true"')
    assert pdfacheck.parse_verapdf(ok)[0] is True
    with pytest.raises(core.PdfManagerError, match="couldn't check the file: Couldn't parse"):
        pdfacheck.parse_verapdf("<report><jobs><job><taskException><exceptionMessage>Couldn't parse"
                                "</exceptionMessage></taskException></job></jobs></report>")

    calls = []

    class Done:
        returncode, stdout, stderr = 1, VERAPDF_FAIL, ""

    def fake_run(command, **kw):
        calls.append(command)
        return Done()

    monkeypatch.setattr(pdfacheck, "find_verapdf", lambda: "verapdf")
    monkeypatch.setattr(pdfacheck.subprocess, "run", fake_run)
    src = make_pdfa(tmp_path / "a1.pdf")
    result = pdfacheck.check(src, level="2b")
    assert result["engine"] == "veraPDF 1.28.2" and not result["passed"]
    assert calls[0][:2] == ["verapdf", "--format"] and "--flavour" in calls[0] and "2b" in calls[0]
    # ZPDF's own checks when asked, or when veraPDF isn't there but was asked for.
    assert pdfacheck.check(src, engine="builtin")["engine"] == "ZPDF"
    monkeypatch.setattr(pdfacheck, "find_verapdf", lambda: None)
    with pytest.raises(core.PdfManagerError, match="veraPDF isn't installed"):
        pdfacheck.check(src, engine="verapdf")


def test_check_pdfa_cli_and_tool(tmp_path, capsys):
    good = make_pdfa(tmp_path / "a1.pdf")
    bad = make_form(tmp_path / "form.pdf")
    assert main(["check-pdfa", str(good), "--engine", "builtin"]) == 0
    assert "Result: PASSES" in capsys.readouterr().out
    assert main(["check-pdfa", str(good), str(bad), "--engine", "builtin", "--json"]) == 1
    records = json.loads(capsys.readouterr().out)
    assert [r["passed"] for r in records] == [True, False]

    notes = []
    (tmp_path / "work").mkdir()
    name, data, _ = gui.run_operation("pdfa-check", [bad], {"level": "", "engine": "builtin"},
                                      notes=notes, work=tmp_path / "work")
    assert notes[0].startswith("form.pdf: fails PDF/A-2b: 3 problems")
    assert name == "form_pdfa-check.txt" and b"Result: FAILS" in data


def test_pdf_and_xmp_dates_compare():
    same = pdfacheck._same_time
    assert same(pdfacheck._pdf_date("D:20240102030400+01'00'"),
                pdfacheck._xmp_date("2024-01-02T03:04+01:00"))
    assert same(pdfacheck._pdf_date("D:20240102020405Z"), pdfacheck._xmp_date("2024-01-02T02:04:05.5Z"))
    assert not same(pdfacheck._pdf_date("D:20240102"), pdfacheck._xmp_date("2024-01-03"))
    assert pdfacheck._xmp_date("soon") is None and pdfacheck._pdf_date("D:2024") is not None
