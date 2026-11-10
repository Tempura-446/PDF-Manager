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
