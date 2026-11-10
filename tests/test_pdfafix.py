"""The built-in PDF/A engine: PDF/A without Ghostscript."""
import io
import sys
from pathlib import Path

import pikepdf
import pytest
from PIL import Image
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).parent))

from pdfmanager import archive, core, fonts, pdfacheck, pdfafix  # noqa: E402


def passes(path, level="2"):
    result = pdfacheck.check(path, level=f"{level}b", engine="builtin")
    assert result["passed"], [i["message"] for i in result["issues"]]
    return True


def picture_pdf(path, colour=(200, 60, 40)):
    """A page that is one RGB picture, as a scan is: no fonts."""
    buf = io.BytesIO()
    Image.new("RGB", (400, 560), colour).save(buf, "JPEG")
    pdf = pikepdf.new()
    name = pikepdf.Name
    picture = pikepdf.Stream(pdf, buf.getvalue(), Type=name.XObject, Subtype=name.Image, Width=400, Height=560,
                             ColorSpace=name.DeviceRGB, BitsPerComponent=8, Filter=name.DCTDecode)
    page = pdf.add_blank_page(page_size=(400, 560))
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im=picture))
    page.Contents = pdf.make_stream(b"q 400 0 0 560 0 0 cm /Im Do Q")
    pdf.save(path)
    return Path(path)


def text_pdf(path, font="Helvetica", text="Invoice 42: total 176.16", **info):
    c = canvas.Canvas(str(path))
    for key, value in info.items():
        getattr(c, f"set{key}")(value)
    c.setFont(font, 14)
    c.drawString(72, 720, text)
    c.save()
    return Path(path)


@pytest.fixture(autouse=True)
def no_ghostscript(monkeypatch):
    """Nothing here may lean on Ghostscript."""
    monkeypatch.setattr(archive, "find_ghostscript", lambda: None)


@pytest.mark.parametrize("level", ["1", "2", "3"])
def test_a_scan_becomes_pdfa_without_ghostscript(tmp_path, level):
    out = pdfafix.convert(picture_pdf(tmp_path / "scan.pdf"), tmp_path / "out.pdf", level=level)
    assert passes(out, level) and archive.pdfa_level(out) == f"{level}B"
    assert core.open_pdf(out).pages[0].images, "the picture is still there"


def test_title_author_and_dates_carry_over_and_agree(tmp_path):
    src = text_pdf(tmp_path / "a.pdf", "Courier", Title="Résumé – 2024", Author="J. Smith", Subject="Pay")
    out = pdfafix.convert(src, tmp_path / "out.pdf")
    info = core.open_pdf(out).metadata
    assert info["/Title"] == "Résumé – 2024" and info["/Author"] == "J. Smith"
    assert passes(out)


def test_links_are_set_to_print_and_nothing_is_hidden(tmp_path):
    src = picture_pdf(tmp_path / "a.pdf")
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        name = pikepdf.Name
        pdf.pages[0].Annots = [pdf.make_indirect(pikepdf.Dictionary(
            Type=name.Annot, Subtype=name.Link, Rect=[10, 10, 100, 40], F=2,
            A=pikepdf.Dictionary(S=name.URI, URI=pikepdf.String("https://example.com"))))]
        pdf.save()
    out = pdfafix.convert(src, tmp_path / "out.pdf")
    with pikepdf.open(out) as pdf:
        link = pdf.pages[0].Annots[0]
        assert int(link.F) == 4 and str(link.A.URI) == "https://example.com"
    assert passes(out)


def test_scripts_and_attachments_go(tmp_path):
    src = picture_pdf(tmp_path / "a.pdf")
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        pdf.Root.OpenAction = pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String("app.alert(1)"))
        pdf.attachments["note.txt"] = b"hello"
        pdf.save()
    notes = []
    out = pdfafix.convert(src, tmp_path / "out.pdf", notes=notes)
    with pikepdf.open(out) as pdf:
        assert "/OpenAction" not in pdf.Root and not pdf.attachments
    assert passes(out) and any("attach" in n for n in notes) and any("script" in n.lower() for n in notes)


def test_a_protected_file_comes_out_unprotected_pdfa(tmp_path):
    src = picture_pdf(tmp_path / "a.pdf")
    core.encrypt(src, tmp_path / "locked.pdf", "secret")
    out = pdfafix.convert(tmp_path / "locked.pdf", tmp_path / "out.pdf", password="secret")
    assert not core.open_pdf(out).is_encrypted and passes(out)


def test_cmyk_colours_are_brought_to_the_rgb_profile(tmp_path):
    c = canvas.Canvas(str(tmp_path / "a.pdf"))
    c.setFillColorCMYK(0.2, 0.5, 0, 0)
    c.rect(72, 500, 100, 50, fill=1)
    c.save()
    out = pdfafix.convert(tmp_path / "a.pdf", tmp_path / "out.pdf")
    assert passes(out)


def test_lzw_and_smoothing_are_put_right(tmp_path):
    src = picture_pdf(tmp_path / "a.pdf")
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        pdf.pages[0].Resources.XObject.Im.Interpolate = True
        # "Hi" in LZW (clear, H, i, end), as old programs wrote content.
        pdf.pages[0].Contents = pikepdf.Stream(pdf, bytes.fromhex("80120d2010"), Filter=pikepdf.Name.LZWDecode)
        pdf.save()
    out = pdfafix.convert(src, tmp_path / "out.pdf")
    assert passes(out)


def test_an_embedded_font_needs_nothing_more(tmp_path):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    pdfmetrics.registerFont(TTFont("Vera", "Vera.ttf"))
    out = pdfafix.convert(text_pdf(tmp_path / "a.pdf", "Vera"), tmp_path / "out.pdf")
    assert passes(out) and "Invoice 42" in core.extract_text(out)


# -- fonts that aren't in the file -----------------------------------------------------

has_like = {kind: pdfafix.stand_in(name) is not None
            for kind, name in (("sans", "Helvetica"), ("serif", "Times-Bold"), ("mono", "Courier"))}


@pytest.mark.skipif(not all(has_like.values()), reason="needs Arial/Times New Roman/Courier New or Liberation")
@pytest.mark.parametrize("font", ["Helvetica", "Helvetica-BoldOblique", "Times-Roman", "Times-Bold", "Courier"])
def test_a_standard_font_is_embedded_from_the_computers_own(tmp_path, font):
    text = "Invoice 42: total 176.16 (fi fl) - \xa3 \xe9"
    src = text_pdf(tmp_path / "a.pdf", font, text)
    notes = []
    out = pdfafix.convert(src, tmp_path / "out.pdf", notes=notes)
    assert passes(out)
    assert core.extract_text(out).strip() == core.extract_text(src).strip()
    assert any("embedded" in n and font.split("-")[0] in n for n in notes), notes
    # The page looks the same: letters where they were (the stand-in has the same widths).
    import numpy as np
    import pypdfium2 as pdfium

    def render(path):
        doc = pdfium.PdfDocument(str(path))
        try:
            return np.asarray(doc[0].render(scale=2).to_pil().convert("L"), int)
        finally:
            doc.close()

    a, b = render(src), render(out)
    assert np.abs(a - b).mean() < 1.5
    ink_a, ink_b = np.argwhere(a < 128), np.argwhere(b < 128)
    assert abs(ink_a[:, 1].max() - ink_b[:, 1].max()) <= 3, "the line ends where it did"


def test_a_font_with_no_stand_in_stops_the_conversion_and_says_which(tmp_path, monkeypatch):
    monkeypatch.setattr(pdfafix, "stand_in", lambda name: None)
    with pytest.raises(pdfafix.CannotConvert, match="Helvetica.*isn't embedded") as caught:
        pdfafix.convert(text_pdf(tmp_path / "a.pdf"), tmp_path / "out.pdf")
    assert caught.value.reasons and not (tmp_path / "out.pdf").exists()


def test_a_stand_in_of_other_widths_is_not_used(tmp_path, monkeypatch):
    import reportlab

    vera = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"   # wider than Helvetica
    monkeypatch.setattr(pdfafix, "stand_in", lambda name: (str(vera), 0))
    with pytest.raises(pdfafix.CannotConvert, match="Helvetica"):
        pdfafix.convert(text_pdf(tmp_path / "a.pdf"), tmp_path / "out.pdf")


def test_transparency_in_pdfa_1_says_to_use_2(tmp_path):
    c = canvas.Canvas(str(tmp_path / "a.pdf"))
    c.setFillAlpha(0.5)
    c.rect(72, 500, 100, 50, fill=1)
    c.save()
    with pytest.raises(pdfafix.CannotConvert, match="PDF/A-2"):
        pdfafix.convert(tmp_path / "a.pdf", tmp_path / "out.pdf", level="1")
    assert passes(pdfafix.convert(tmp_path / "a.pdf", tmp_path / "out2.pdf", level="2"))


# -- the tool ---------------------------------------------------------------------------

def test_to_pdfa_needs_no_ghostscript_and_says_how(tmp_path):
    out, engine = archive.to_pdfa(picture_pdf(tmp_path / "a.pdf"), tmp_path / "out.pdf", with_engine=True)
    assert engine == "builtin" and passes(out)


def test_to_pdfa_turns_to_ghostscript_for_what_zpdf_cant_fix(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(pdfafix, "stand_in", lambda name: None)
    monkeypatch.setattr(archive, "find_ghostscript", lambda: "gs")
    monkeypatch.setattr(archive, "_ghostscript_pdfa", lambda *a, **k: called.append(a) or Path(a[1]))
    archive.to_pdfa(text_pdf(tmp_path / "a.pdf"), tmp_path / "out.pdf")
    assert len(called) == 1
    # Without it, the message says what stood in the way and what would help.
    monkeypatch.setattr(archive, "find_ghostscript", lambda: None)
    with pytest.raises(core.PdfManagerError, match="Helvetica.*Ghostscript"):
        archive.to_pdfa(text_pdf(tmp_path / "b.pdf"), tmp_path / "out2.pdf")


def test_the_app_and_pdfm_convert_without_ghostscript(tmp_path):
    from pdfmanager import cli, gui

    src = picture_pdf(tmp_path / "a.pdf")
    notes = []
    (tmp_path / "work").mkdir()
    name, data, _ = gui.run_operation("pdfa", [src], {"level": "2"}, None, {}, tmp_path / "work", notes)
    (tmp_path / "app.pdf").write_bytes(data)
    assert passes(tmp_path / "app.pdf") and any("passes" in n.lower() or "checked" in n for n in notes)
    assert cli.main(["pdfa", str(src), "-o", str(tmp_path / "cli.pdf")]) in (0, None)
    assert passes(tmp_path / "cli.pdf")
    op = next(o for o in gui.OPERATIONS if o["id"] == "pdfa")
    assert "Needs Ghostscript" not in op["help"]
