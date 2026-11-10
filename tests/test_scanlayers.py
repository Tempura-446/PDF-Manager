"""The scan compression engine: a scanned page kept as a sharp text shape
over a low-resolution background, and Compress using it."""
import io
import sys
from pathlib import Path

import numpy as np
import pikepdf
import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageOps

sys.path.insert(0, str(Path(__file__).parent))
import ocrpages  # noqa: E402

from pdfmanager import scanlayers  # noqa: E402


# -- pages --------------------------------------------------------------------------

def colour_page():
    """Tinted paper, a red stamp, blue pen: a colour scan."""
    text = ocrpages.render().convert("RGB")
    paper = Image.new("RGB", text.size, (244, 238, 220))
    page = Image.fromarray(np.minimum(np.asarray(paper), np.asarray(text)))
    draw = ImageDraw.Draw(page)
    draw.ellipse((1500, 1500, 2100, 1900), outline=(200, 30, 30), width=14)
    draw.text((1600, 1650), "PAID", font=ocrpages._font(120), fill=(200, 30, 30))
    draw.text((300, 2050), "Approved - J. Smith 14/3", font=ocrpages._font(70, serif=True), fill=(20, 40, 170))
    return page.filter(ImageFilter.GaussianBlur(0.6))


def _photo(size):
    """A dark, soft picture (values 10 to 200), as a photograph scans."""
    noise = Image.effect_noise((size[0] // 20, size[1] // 20), 90).resize(size, Image.BICUBIC)
    return noise.filter(ImageFilter.GaussianBlur(6)).point(lambda v: 10 + v * 190 // 255)


def page_with_photo():
    page = ocrpages.render()
    page.paste(_photo((1000, 700)), (700, 2300))
    return page


def photo_only():
    return _photo(ocrpages.A4_300)


def _ink(layers):
    return 1 - np.asarray(layers.mask, np.uint8)


# -- the engine ---------------------------------------------------------------------

def test_text_goes_to_the_shape_and_paper_to_the_background():
    page = ocrpages.render()
    layers = scanlayers.split(page, 300, 100, 25)
    assert layers.mask.size == page.size and layers.mask.mode == "1"
    assert 0.005 < _ink(layers).mean() < 0.10
    assert layers.background.width == round(2480 * 100 / 300)
    assert np.asarray(layers.background.convert("L")).min() > 200, "ink is left in the paper"


def test_a_scan_above_300_dpi_gets_a_300_dpi_shape():
    page = ocrpages.render().resize((4960, 7016))
    assert scanlayers.split(page, 600, 100, 25).mask.size == (2480, 3508)


def test_speckles_stay_out_of_the_shape():
    noisy = ocrpages.degrade(ocrpages.render())
    layers = scanlayers.split(noisy, 300, 100, 25)
    assert len(scanlayers.group4(layers.mask)) < 40_000


def test_ink_keeps_its_colour():
    layers = scanlayers.split(colour_page(), 300, 100, 25)
    r, g, b = layers.ink.resize(layers.mask.size).getpixel((1505, 1700))  # on the stamp's ring
    assert r > 150 and g < 90 and b < 90
    r, g, b = layers.background.resize(layers.mask.size).getpixel((200, 200))
    assert (r, g, b) != (255, 255, 255) and r > b, "the paper's tint is kept"


def test_a_grey_scan_gets_grey_layers():
    layers = scanlayers.split(ocrpages.render(), 300, 100, 25)
    assert layers.background.mode == "L" and layers.ink.mode == "L"


def test_a_photograph_stays_out_of_the_shape():
    layers = scanlayers.split(page_with_photo(), 300, 100, 25)
    ink = _ink(layers)
    assert ink[2350:2950, 750:1650].mean() < 0.01
    assert ink[300:1300].mean() > 0.005, "the text above the photograph is still found"
    assert 0.03 < layers.picture_share < 0.30


def test_a_page_that_is_all_photograph_is_not_a_text_scan():
    assert scanlayers.split(photo_only(), 300, 100, 25) is None


def test_light_text_on_a_dark_page_is_not_a_text_scan():
    assert scanlayers.split(ImageOps.invert(ocrpages.render()), 300, 100, 25) is None


def test_a_blank_page_is_not_a_text_scan():
    assert scanlayers.split(Image.new("L", (1240, 1754), 250), 150, 100, 25) is None


def test_cmyk_comes_out_rgb_and_not_inverted():
    layers = scanlayers.split(ocrpages.render().convert("CMYK"), 300, 100, 25)
    assert layers.background.mode == "RGB" and np.asarray(layers.background).mean() > 200


def _mask_pdf(mask, data, path):
    """A page that paints ``data`` (Group 4) as a black stencil."""
    pdf = pikepdf.new()
    name = pikepdf.Name
    stencil = pikepdf.Stream(pdf, data, Type=name.XObject, Subtype=name.Image, Width=mask.width,
                             Height=mask.height, ImageMask=True, BitsPerComponent=1, Filter=name.CCITTFaxDecode,
                             DecodeParms=pikepdf.Dictionary(K=-1, Columns=mask.width, Rows=mask.height,
                                                            BlackIs1=False))
    w, h = mask.width * 72 / 300, mask.height * 72 / 300
    page = pdf.add_blank_page(page_size=(w, h))
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(M=stencil))
    page.Contents = pdf.make_stream(f"q 0 g {w} 0 0 {h} 0 0 cm /M Do Q".encode())
    pdf.save(path)
    return path


def render(path, dpi=300, page=0):
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        return doc[page].render(scale=dpi / 72).to_pil().convert("RGB")
    finally:
        doc.close()


def test_group4_paints_the_ink_as_a_stencil(tmp_path):
    mask = scanlayers.split(ocrpages.render(), 300, 100, 25).mask
    data = scanlayers.group4(mask)
    assert data and len(data) < 30_000
    drawn = render(_mask_pdf(mask, data, tmp_path / "mask.pdf")).convert("L").resize(mask.size)
    wanted = np.asarray(mask.convert("L"), int)
    assert np.abs(np.asarray(drawn, int) - wanted).mean() < 2


def test_group4_says_when_it_cant(monkeypatch):
    def broken(self, *args, **kwargs):
        raise OSError("encoder group4 not available")

    monkeypatch.setattr(Image.Image, "save", broken)
    assert scanlayers.group4(Image.new("1", (64, 64), 1)) is None


# -- Compress uses the engine -----------------------------------------------------------

from pdfmanager import core, imaging, ocr  # noqa: E402


def _jpeg(image, quality=85):
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _picture(pdf, image, quality=85, **extra):
    name = pikepdf.Name
    space = {"L": name.DeviceGray, "RGB": name.DeviceRGB, "CMYK": name.DeviceCMYK}[image.mode]
    if image.mode == "CMYK":
        extra["Decode"] = [1, 0] * 4  # Pillow writes CMYK JPEGs inverted, as Adobe does
    return pikepdf.Stream(pdf, _jpeg(image, quality), Type=name.XObject, Subtype=name.Image,
                          Width=image.width, Height=image.height, ColorSpace=space, BitsPerComponent=8,
                          Filter=name.DCTDecode, **extra)


def scan(image, path, dpi=300, quality=85, pages=1, content=None, rotate=0, shared=False, box=None):
    """A PDF as a scanner writes it: each page one JPEG filling it.
    ``content``: the page's drawing instead (the picture is /Im, the page
    w by h points); ``shared``: every page draws the same picture object;
    ``box``: where on the page (x, y, w, h in points) instead of all of it."""
    pdf = pikepdf.new()
    w, h = image.width * 72 / dpi, image.height * 72 / dpi
    if box:
        w, h = 595, 842
    one = _picture(pdf, image, quality) if shared else None
    for _ in range(pages):
        page = pdf.add_blank_page(page_size=(w, h))
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im=_picture(pdf, image, quality) if one is None else one))
        x, y, pw, ph = box or (0, 0, w, h)
        page.Contents = pdf.make_stream((content or f"q {pw} 0 0 {ph} {x} {y} cm /Im Do Q").encode())
        if rotate:
            page.Rotate = rotate
    pdf.save(path)
    return Path(path)


def pictures(path):
    """Every picture in the file: (filter, width, height), forms looked into."""
    found = []
    with pikepdf.open(path) as pdf:
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Stream) and obj.get("/Subtype") == "/Image":
                filt = obj.get("/Filter")
                found.append((str(filt[0] if isinstance(filt, pikepdf.Array) else filt),
                              int(obj.Width), int(obj.Height)))
    return sorted(found)


def filters(path):
    return [p[0] for p in pictures(path)]


def difference(a, b, dpi=100):
    return float(np.abs(np.asarray(render(a, dpi), int) - np.asarray(render(b, dpi), int)).mean())


PAGES = {"clean": ocrpages.render, "noisy": lambda: ocrpages.degrade(ocrpages.render()), "colour": colour_page}


@pytest.fixture(scope="module")
def scans(tmp_path_factory):
    folder = tmp_path_factory.mktemp("scans")
    made = {}
    for name, make in PAGES.items():
        src = scan(make(), folder / f"{name}.pdf")
        notes = []
        sharp = core.compress(src, folder / f"{name}-sharp.pdf", level="high", notes=notes)
        plain = core.compress(src, folder / f"{name}-plain.pdf", level="high", sharp_scans=False)
        made[name] = (src, sharp, plain, notes)
    return made


@pytest.mark.parametrize("name", list(PAGES))
def test_high_is_as_small_with_sharp_text(scans, name):
    src, sharp, plain, notes = scans[name]
    assert sharp.stat().st_size <= plain.stat().st_size * 1.10
    assert sharp.stat().st_size < src.stat().st_size / 4
    assert notes == ["1 scanned page kept sharp"]
    assert ("/CCITTFaxDecode", 2480, 3508) in pictures(sharp), "the text shape is at the scan's resolution"
    assert filters(plain) == ["/DCTDecode"]


@pytest.mark.parametrize("name", list(PAGES))
def test_the_page_still_looks_like_the_page(scans, name):
    src, sharp, _, _ = scans[name]
    assert difference(src, sharp) < 8


def test_a_red_stamp_stays_red(scans):
    r, g, b = render(scans["colour"][1], 300).getpixel((1505, 1700))
    assert r > 150 and g < 100 and b < 100


@pytest.mark.skipif(not ocr.builtin_available(), reason="needs the built-in OCR engine")
@pytest.mark.parametrize("name", list(PAGES))
def test_ocr_reads_it_as_well(scans, name):
    def cer(path):
        lines = ocr.recognise(render(path, 300))
        return ocrpages.cer(ocrpages.TEXT, ocr.text_of(ocr.reading_order(lines)))

    _, sharp, plain, _ = scans[name]
    assert cer(sharp) <= cer(plain) + 0.005


def test_several_pages_are_counted(tmp_path):
    src = scan(ocrpages.render(), tmp_path / "three.pdf", pages=3)
    notes = []
    out = core.compress(src, tmp_path / "out.pdf", level="medium", notes=notes)
    # (The three pages are the same picture, so the file keeps one copy of each layer.)
    assert notes == ["3 scanned pages kept sharp"] and "/CCITTFaxDecode" in filters(out)
    assert difference(src, out) < 8


def test_medium_keeps_more_of_the_background(tmp_path):
    src = scan(colour_page(), tmp_path / "scan.pdf")
    medium = core.compress(src, tmp_path / "m.pdf", level="medium")
    high = core.compress(src, tmp_path / "h.pdf", level="high")
    widths = lambda path: sorted(w for f, w, _ in pictures(path) if f == "/DCTDecode")  # noqa: E731
    # Each layer is the scan reduced a whole number of times: 300 dpi by 2 and 8, by 3 and 12.
    assert widths(medium) == [2480 // 8, 2480 // 2]
    assert widths(high) == [-(-2480 // 12), -(-2480 // 3)]


def test_low_and_the_switch_leave_scans_alone(tmp_path):
    src = scan(ocrpages.render(), tmp_path / "scan.pdf")
    notes = []
    low = core.compress(src, tmp_path / "low.pdf", level="low", notes=notes)
    off = core.compress(src, tmp_path / "off.pdf", level="medium", sharp_scans=False, notes=notes)
    assert filters(low) == ["/DCTDecode"] and filters(off) == ["/DCTDecode"] and notes == []


def _plain_and_sharp(src, tmp_path):
    sharp = core.compress(src, tmp_path / "sharp.pdf", level="high")
    plain = core.compress(src, tmp_path / "plain.pdf", level="high", sharp_scans=False)
    return sharp, plain


def _left_alone(src, tmp_path):
    sharp, plain = _plain_and_sharp(src, tmp_path)
    assert "/CCITTFaxDecode" not in filters(sharp)
    assert sharp.stat().st_size == plain.stat().st_size


def test_a_small_picture_on_a_page_is_not_a_scan(tmp_path):
    _left_alone(scan(ocrpages.render(), tmp_path / "s.pdf", box=(50, 300, 300, 424)), tmp_path)


def test_a_low_resolution_scan_has_no_sharpness_to_keep(tmp_path):
    page = ocrpages.render().resize((827, 1169))
    _left_alone(scan(page, tmp_path / "s.pdf", dpi=100), tmp_path)


def test_a_photograph_page_is_compressed_the_usual_way(tmp_path):
    _left_alone(scan(photo_only(), tmp_path / "s.pdf"), tmp_path)


def test_a_picture_shown_on_two_pages_is_left_alone(tmp_path):
    _left_alone(scan(ocrpages.render(), tmp_path / "s.pdf", pages=2, shared=True), tmp_path)


def test_a_picture_drawn_twice_on_a_page_is_left_alone(tmp_path):
    twice = "q 595.2 0 0 841.92 0 0 cm /Im Do Q q 595.2 0 0 841.92 0 0 cm /Im Do Q"
    _left_alone(scan(ocrpages.render(), tmp_path / "s.pdf", content=twice), tmp_path)


def test_a_picture_with_a_soft_mask_is_left_alone(tmp_path):
    src = scan(ocrpages.render(), tmp_path / "s.pdf")
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        picture = pdf.pages[0].Resources.XObject.Im
        picture.SMask = pikepdf.Stream(pdf, b"\xff" * 16, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                                       Width=4, Height=4, ColorSpace=pikepdf.Name.DeviceGray, BitsPerComponent=8)
        pdf.save()
    sharp = core.compress(src, tmp_path / "sharp.pdf", level="high")
    assert "/CCITTFaxDecode" not in filters(sharp)


def test_a_black_and_white_scan_is_left_as_it_is(tmp_path):
    mask = ocrpages.render().convert("1")
    src = _mask_pdf(mask, scanlayers.group4(mask), tmp_path / "fax.pdf")
    out = core.compress(src, tmp_path / "out.pdf", level="high")
    assert pictures(out) == [("/CCITTFaxDecode", 2480, 3508)]


@pytest.mark.parametrize("rotate, content", [
    (90, None),
    (0, "q -595.2 0 0 841.92 595.2 0 cm /Im Do Q"),       # mirrored
    (0, "q 0 595.2 -841.92 0 841.92 0 cm /Im Do Q"),      # turned by the page's drawing
])
def test_a_turned_or_mirrored_scan_draws_in_place(tmp_path, rotate, content):
    page = colour_page()
    if content and content.startswith("q 0"):
        src = scan(page, tmp_path / "s.pdf", content=content)
        with pikepdf.open(src, allow_overwriting_input=True) as pdf:
            pdf.pages[0].MediaBox = [0, 0, 841.92, 595.2]
            pdf.save()
    else:
        src = scan(page, tmp_path / "s.pdf", content=content, rotate=rotate)
    out = core.compress(src, tmp_path / "out.pdf", level="high")
    assert "/CCITTFaxDecode" in filters(out)
    assert difference(src, out) < 8


def test_a_cmyk_scan_keeps_its_colours(tmp_path):
    src = scan(colour_page().convert("CMYK"), tmp_path / "s.pdf")
    out = core.compress(src, tmp_path / "out.pdf", level="high")
    assert "/CCITTFaxDecode" in filters(out) and difference(src, out) < 12


def test_a_600_dpi_scan_gets_a_300_dpi_shape(tmp_path):
    src = scan(ocrpages.render().resize((4960, 7016)), tmp_path / "s.pdf", dpi=600)
    out = core.compress(src, tmp_path / "out.pdf", level="high")
    assert ("/CCITTFaxDecode", 2480, 3508) in pictures(out)


def test_the_rest_of_the_page_survives(tmp_path):
    from pypdf import PdfReader

    words = " BT 3 Tr /F1 12 Tf 72 700 Td (hidden words here) Tj ET"
    src = scan(ocrpages.render(), tmp_path / "s.pdf", content="q 595.2 0 0 841.92 0 0 cm /Im Do Q" + words)
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        name = pikepdf.Name
        page = pdf.pages[0]
        page.Resources.Font = pikepdf.Dictionary(F1=pikepdf.Dictionary(
            Type=name.Font, Subtype=name.Type1, BaseFont=name.Helvetica))
        page.Annots = pdf.make_indirect(pikepdf.Array([pikepdf.Dictionary(
            Type=name.Annot, Subtype=name.Link, Rect=[72, 72, 200, 100],
            A=pikepdf.Dictionary(S=name.URI, URI=pikepdf.String("https://example.com")))]))
        pdf.save()
    assert "hidden words here" in PdfReader(src).pages[0].extract_text()
    out = core.compress(src, tmp_path / "out.pdf", level="high")
    assert "/CCITTFaxDecode" in filters(out)
    page = PdfReader(out).pages[0]
    assert "hidden words here" in page.extract_text()
    assert [a.get_object()["/A"]["/URI"] for a in page["/Annots"]] == ["https://example.com"]


def test_an_engine_failure_falls_back(tmp_path, monkeypatch):
    src = scan(ocrpages.render(), tmp_path / "s.pdf")
    plain = core.compress(src, tmp_path / "plain.pdf", level="high", sharp_scans=False)
    monkeypatch.setattr(scanlayers, "split", lambda *a, **k: 1 / 0)
    notes = []
    out = core.compress(src, tmp_path / "out.pdf", level="high", notes=notes)
    assert out.stat().st_size == plain.stat().st_size and notes == []


def test_without_group4_the_result_is_still_no_larger(tmp_path, monkeypatch):
    src = scan(ocrpages.render(), tmp_path / "s.pdf")
    plain = core.compress(src, tmp_path / "plain.pdf", level="high", sharp_scans=False)
    monkeypatch.setattr(scanlayers, "group4", lambda mask: None)
    out = core.compress(src, tmp_path / "out.pdf", level="high")
    assert "/CCITTFaxDecode" not in filters(out)
    assert out.stat().st_size <= plain.stat().st_size * 1.10 and difference(src, out) < 8


def test_layers_larger_than_the_usual_way_are_not_used(tmp_path, monkeypatch):
    src = scan(ocrpages.render(), tmp_path / "s.pdf")
    plain = core.compress(src, tmp_path / "plain.pdf", level="high", sharp_scans=False)
    monkeypatch.setattr(scanlayers, "group4", lambda mask: b"x" * 200_000)
    out = core.compress(src, tmp_path / "out.pdf", level="high")
    assert out.stat().st_size == plain.stat().st_size
