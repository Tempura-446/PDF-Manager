"""Fixes from the code audit: page operations, page boxes, annotations,
stamps on shared pages, output files."""

import re

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject,
                           NullObject, NumberObject, RectangleObject)
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pdfmanager import core, forms


def toc_pdf(path, chapters=5):
    """Page 1 is a table of contents linking to each chapter page; every
    chapter has a bookmark."""
    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(1, chapters + 1):
        c.drawString(72, 700 - i * 20, f"Chapter {i}")
        c.linkAbsolute("", f"ch{i}", (72, 690 - i * 20, 200, 705 - i * 20))
    c.showPage()
    for i in range(1, chapters + 1):
        c.bookmarkPage(f"ch{i}")
        c.addOutlineEntry(f"Chapter {i}", f"ch{i}")
        c.drawString(72, 700, f"Chapter {i} body")
        c.showPage()
    c.save()
    return path


def page_objects(path):
    """How many page objects the file holds (pypdf writes them in the open)."""
    return len(re.findall(rb"/Type\s*/Page[^s]", path.read_bytes()))


def texts(path):
    return [p.extract_text().strip() for p in PdfReader(str(path)).pages]


def rect(path, page=0, index=0):
    annots = PdfReader(str(path)).pages[page]["/Annots"]
    return [round(float(v)) for v in annots[index].get_object()["/Rect"]]


# -- page lists keep only the listed pages -------------------------------------------------------

def test_deleting_a_page_leaves_nothing_of_it_in_the_file(tmp_path):
    src = toc_pdf(tmp_path / "toc.pdf")
    out = core.delete_pages(src, tmp_path / "no3.pdf", "4")  # chapter 3
    assert len(PdfReader(str(out)).pages) == 5
    assert page_objects(out) == 5  # not a sixth, unlisted, reachable from a link
    assert out.stat().st_size < src.stat().st_size


def test_links_and_bookmarks_follow_the_kept_pages(tmp_path):
    src = toc_pdf(tmp_path / "toc.pdf")
    out = core.delete_pages(src, tmp_path / "no3.pdf", "4")
    reader = PdfReader(str(out))
    links = [a.get_object() for a in reader.pages[0]["/Annots"]]
    assert len(links) == 4  # the link to the deleted chapter is gone
    targets = {reader.get_destination_page_number(reader.outline[i]) for i in range(len(reader.outline))}
    assert [o.title for o in reader.outline] == ["Chapter 1", "Chapter 2", "Chapter 4", "Chapter 5"]
    assert targets == {1, 2, 3, 4}
    # links point at the pages as they are now
    dest_pages = sorted(reader.get_page_number(link["/Dest"][0].get_object()) for link in links)
    assert dest_pages == [1, 2, 3, 4]


def test_extracting_the_contents_page_alone_is_small(tmp_path):
    src = toc_pdf(tmp_path / "toc.pdf")
    out = core.select_pages(src, tmp_path / "toc_only.pdf", "1")
    assert page_objects(out) == 1
    parts = core.split(src, tmp_path / "parts", every=1)
    assert [page_objects(p) for p in parts] == [1] * 6


def test_reversing_and_reordering_keep_bookmarks(tmp_path):
    src = toc_pdf(tmp_path / "toc.pdf", chapters=2)
    out = core.reverse(src, tmp_path / "rev.pdf")
    reader = PdfReader(str(out))
    assert texts(out)[0] == "Chapter 2 body"
    assert {o.title for o in reader.outline} == {"Chapter 1", "Chapter 2"}
    assert reader.get_destination_page_number(reader.outline[1]) == 0  # Chapter 2 is first now


def test_duplicated_pages_are_separate_pages(tmp_path):
    src = toc_pdf(tmp_path / "toc.pdf", chapters=2)
    out = core.select_pages(src, tmp_path / "dup.pdf", "2,2,3")
    assert texts(out) == ["Chapter 1 body", "Chapter 1 body", "Chapter 2 body"]
    assert {o.title for o in PdfReader(str(out)).outline} == {"Chapter 1", "Chapter 2"}


def form_pdf(path, tmp_path):
    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(3):
        c.drawString(72, 700, f"Form page {i + 1}")
        c.showPage()
    c.save()
    forms.add_fields(path, path, [{"page": 1, "rect": [72, 600, 300, 624], "type": "text", "name": "Name"},
                                  {"page": 3, "rect": [72, 600, 92, 620], "type": "checkbox", "name": "Agree"}])
    return path


def test_page_operations_keep_the_form(tmp_path):
    src = form_pdf(tmp_path / "form.pdf", tmp_path)
    assert set(PdfReader(str(src)).get_fields()) == {"Name", "Agree"}
    for name, out in [("select", core.select_pages(src, tmp_path / "s.pdf", "1,3")),
                      ("delete", core.delete_pages(src, tmp_path / "d.pdf", "2")),
                      ("reverse", core.reverse(src, tmp_path / "r.pdf")),
                      ("merge", core.merge([src, toc_pdf(tmp_path / "toc.pdf", 1)], tmp_path / "m.pdf"))]:
        assert set(PdfReader(str(out)).get_fields()) == {"Name", "Agree"}, name
    only = core.select_pages(src, tmp_path / "one.pdf", "1")
    assert set(PdfReader(str(only)).get_fields()) == {"Name"}  # the field of the page left out is gone too


def test_merge_bookmarks_nest_each_files_own(tmp_path):
    a = toc_pdf(tmp_path / "a.pdf", chapters=2)
    b = toc_pdf(tmp_path / "b.pdf", chapters=1)
    out = core.merge([a, (b, "2")], tmp_path / "m.pdf")
    reader = PdfReader(str(out))
    assert len(reader.pages) == 4
    assert reader.outline[0].title == "a" and reader.outline[2].title == "b"
    assert [o.title for o in reader.outline[1]] == ["Chapter 1", "Chapter 2"]
    plain = core.merge([a, b], tmp_path / "p.pdf", bookmarks=False)
    assert PdfReader(str(plain)).outline == []


def test_interleave_keeps_links(tmp_path):
    front = toc_pdf(tmp_path / "f.pdf", chapters=2)
    back = toc_pdf(tmp_path / "b.pdf", chapters=2)
    out = core.interleave(front, back, tmp_path / "i.pdf", reverse_back=False)
    reader = PdfReader(str(out))
    assert len(reader.pages) == 6 and page_objects(out) == 6
    assert texts(out) == ["Chapter 1\nChapter 2"] * 2 + ["Chapter 1 body"] * 2 + ["Chapter 2 body"] * 2
    # The contents pages still link to their own chapters, now interleaved.
    for n in (0, 1):
        links = [a.get_object() for a in reader.pages[n]["/Annots"]]
        assert sorted(reader.get_page_number(link["/Dest"][0].get_object()) for link in links) == [2 + n, 4 + n]
    assert {o.title for o in reader.outline} == {"Chapter 1", "Chapter 2"}


# -- pages that share their content ---------------------------------------------------------------

def test_resizing_a_duplicated_page_scales_each_copy_once(tmp_path):
    src = toc_pdf(tmp_path / "toc.pdf", chapters=1)
    dup = core.select_pages(src, tmp_path / "dup.pdf", "1,1,2")
    out = core.resize(dup, tmp_path / "half.pdf", scale=0.5)
    reader = PdfReader(str(out))
    for page in reader.pages:
        assert page.get_contents().get_data().count(b"0.5 0.0 0.0 0.5 0.0 0.0 cm") == 1
    assert rect(out, 0) == rect(out, 1) == [36, 335, 100, 342]  # the link, at half size, once
    # Resizing one copy leaves the other alone.
    out = core.resize(dup, tmp_path / "one.pdf", scale=0.5, pages="2")
    reader = PdfReader(str(out))
    assert b"0.5 0.0 0.0 0.5" not in reader.pages[0].get_contents().get_data()
    assert reader.pages[0].mediabox.width == 612 and reader.pages[1].mediabox.width == 306


def test_numbering_pages_that_share_one_content_stream(tmp_path):
    c = canvas.Canvas(str(tmp_path / "same.pdf"), pagesize=letter)
    for _ in range(3):
        c.drawString(72, 700, "Same text on every page")
        c.showPage()
    c.save()
    small = core.compress(tmp_path / "same.pdf", tmp_path / "small.pdf", level="low")
    pages = PdfReader(str(small)).pages
    assert pages[0]["/Contents"].indirect_reference == pages[1]["/Contents"].indirect_reference
    out = core.number_pages(small, tmp_path / "numbered.pdf", fmt="{n}/{total}")
    assert [t.splitlines()[-1] for t in texts(out)] == ["1/3", "2/3", "3/3"]
    out = core.watermark(small, tmp_path / "wm.pdf", text="DRAFT", pages="2")
    assert [("DRAFT" in t) for t in texts(out)] == [False, True, False]


# -- what the page shows: crop box and rotation ---------------------------------------------------

def cropped_pdf(path, rotate=0):
    """A letter page whose crop box is [100 100 512 692], with a link at the
    crop box's lower left corner."""
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(120, 120, "inside")
    c.linkURL("https://example.com", (100, 100, 160, 130))
    c.showPage()
    c.save()
    w = PdfWriter(clone_from=str(path))
    w.pages[0].cropbox = RectangleObject([100, 100, 512, 692])
    if rotate:
        w.pages[0].rotate(rotate)
    w.write(str(path))
    return path


def test_resize_keeps_what_the_page_shows(tmp_path):
    src = cropped_pdf(tmp_path / "crop.pdf")
    assert core.info(src)["page_sizes"] == {"145x209 mm": 1}
    out = core.resize(src, tmp_path / "same.pdf", scale=1.0)
    page = PdfReader(str(out)).pages[0]
    assert [round(float(v)) for v in page.mediabox] == [0, 0, 412, 592]
    assert rect(out) == [0, 0, 60, 30]
    out = core.resize(src, tmp_path / "a4.pdf", size="a4")
    assert rect(out)[:2] == [5, 0]  # the shown part, centred, fills the new page


def test_crop_margins_are_taken_from_the_shown_part(tmp_path):
    src = cropped_pdf(tmp_path / "crop.pdf")
    out = core.crop(src, tmp_path / "c.pdf", (0, 0, 0, 0))
    page = PdfReader(str(out)).pages[0]
    assert [round(float(v)) for v in page.mediabox] == [100, 100, 512, 692]
    out = core.crop(src, tmp_path / "c2.pdf", (10, 20, 30, 40))
    assert [round(float(v)) for v in PdfReader(str(out)).pages[0].cropbox] == [110, 120, 482, 652]


@pytest.mark.parametrize("rotate,expected", [
    (90, [200, 0, 612, 792]),   # the shown top is the page's own left
    (180, [0, 200, 612, 792]),  # ... its bottom
    (270, [0, 0, 412, 792]),    # ... its right
])
def test_crop_top_is_the_top_as_shown(tmp_path, rotate, expected):
    c = canvas.Canvas(str(tmp_path / "r.pdf"), pagesize=letter)
    c.showPage()
    c.save()
    w = PdfWriter(clone_from=str(tmp_path / "r.pdf"))
    w.pages[0].rotate(rotate)
    w.write(str(tmp_path / "r.pdf"))
    out = core.crop(tmp_path / "r.pdf", tmp_path / "c.pdf", (0, 0, 0, 200))
    assert [round(float(v)) for v in PdfReader(str(out)).pages[0].mediabox] == expected
    # The page editor measures in the page's own coordinates.
    out = core.crop(tmp_path / "r.pdf", tmp_path / "c2.pdf", (0, 0, 0, 200), visual=False)
    assert [round(float(v)) for v in PdfReader(str(out)).pages[0].mediabox] == [0, 0, 612, 592]


def test_n_up_fills_each_cell_with_what_the_page_shows(tmp_path):
    src = cropped_pdf(tmp_path / "crop.pdf")
    out = core.n_up(src, tmp_path / "n.pdf", per_sheet=2)
    # The 412x592 shown part fills its 421x595 cell (less 5%): the corner
    # link sits at the cell's edge, not 70pt in as the whole page would.
    x0, y0, x1, y1 = rect(out)
    assert 10 <= x0 <= 18 and 0 <= y0 <= 20 and x1 - x0 == 57


# -- annotations -----------------------------------------------------------------------------------

def stamped_pdf(path, rotate=90):
    c = canvas.Canvas(str(path), pagesize=letter)
    c.showPage()
    c.save()
    w = PdfWriter(clone_from=str(path))
    page = w.pages[0]
    arrow = DecodedStreamObject()
    arrow.set_data(b"0 0 m 200 50 l 0 100 l h f")
    arrow[NameObject("/Type")] = NameObject("/XObject")
    arrow[NameObject("/Subtype")] = NameObject("/Form")
    arrow[NameObject("/BBox")] = RectangleObject([0, 0, 200, 100])
    stamp = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Stamp"),
        NameObject("/Rect"): RectangleObject([100, 100, 300, 200]),
        NameObject("/AP"): DictionaryObject({NameObject("/N"): w._add_object(arrow)}),
    })
    ink = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Ink"),
        NameObject("/Rect"): RectangleObject([100, 400, 300, 500]),
        NameObject("/InkList"): ArrayObject([ArrayObject([FloatObject(v) for v in (110, 410, 200, 490, 290, 410)])]),
    })
    line = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Line"),
        NameObject("/Rect"): RectangleObject([100, 600, 500, 620]),
        NameObject("/L"): ArrayObject([FloatObject(v) for v in (100, 610, 500, 610)]),
    })
    page[NameObject("/Annots")] = ArrayObject([w._add_object(stamp), w._add_object(ink), w._add_object(line),
                                               NullObject()])
    if rotate:
        page.rotate(rotate)
    w.write(str(path))
    return path


def test_appearances_turn_with_the_page(tmp_path):
    src = stamped_pdf(tmp_path / "stamp.pdf", rotate=90)
    out = core.resize(src, tmp_path / "out.pdf", scale=1.0)
    stamp = PdfReader(str(out)).pages[0]["/Annots"][0].get_object()
    assert [round(float(v)) for v in stamp["/Rect"]] == [100, 312, 200, 512]  # turned with the page
    matrix = [round(float(v), 3) for v in stamp["/AP"]["/N"]["/Matrix"]]
    assert matrix[:4] == [0, -1, 1, 0]  # ... and so is its drawing


def test_drawings_and_lines_move_with_the_page(tmp_path):
    src = stamped_pdf(tmp_path / "stamp.pdf", rotate=0)
    out = core.resize(src, tmp_path / "half.pdf", scale=0.5)
    annots = [a.get_object() for a in PdfReader(str(out)).pages[0]["/Annots"]]
    assert [round(float(v)) for v in annots[1]["/InkList"][0]] == [55, 205, 100, 245, 145, 205]
    assert [round(float(v)) for v in annots[2]["/L"]] == [50, 305, 250, 305]


def test_a_null_annotation_is_skipped(tmp_path):
    src = stamped_pdf(tmp_path / "stamp.pdf", rotate=0)
    assert isinstance(PdfReader(str(src)).pages[0]["/Annots"][3].get_object(), NullObject)
    core.resize(src, tmp_path / "a4.pdf", size="a4")
    core.n_up(src, tmp_path / "n.pdf", per_sheet=2)


# -- opening and writing files ---------------------------------------------------------------------

def test_owner_password_only_files_open_with_any_password(tmp_path):
    src = toc_pdf(tmp_path / "toc.pdf", chapters=1)
    w = PdfWriter(clone_from=str(src))
    w.encrypt(user_password="", owner_password="owner-secret")
    w.write(str(tmp_path / "locked.pdf"))
    for password in (None, "", "something-else"):
        assert len(core.open_pdf(tmp_path / "locked.pdf", password).pages) == 2


def test_a_failed_write_leaves_no_temporary_file(tmp_path, monkeypatch):
    src = toc_pdf(tmp_path / "toc.pdf", chapters=1)
    folder = tmp_path / "folder"
    folder.mkdir()
    with pytest.raises(core.PdfManagerError, match="is a folder"):
        core.rotate(src, folder, 90)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["folder", "toc.pdf"]

    def boom(self, stream):
        raise OSError("disk full")

    monkeypatch.setattr(PdfWriter, "write", boom)
    with pytest.raises(OSError):
        core.rotate(src, tmp_path / "out.pdf", 90)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["folder", "toc.pdf"]


def test_page_number_format_is_checked(tmp_path):
    src = toc_pdf(tmp_path / "toc.pdf", chapters=1)
    with pytest.raises(core.PdfManagerError, match="format"):
        core.number_pages(src, tmp_path / "n.pdf", fmt="Page {page}")
    out = core.number_pages(src, tmp_path / "n.pdf", fmt="{{{n:02d} of {total}}}")
    assert texts(out)[0].endswith("{01 of 2}")


def test_a_long_chain_of_next_page_links_is_copied(tmp_path):
    # Every page links to the next: copying objects recursively would go
    # as deep as the document is long.
    c = canvas.Canvas(str(tmp_path / "chain.pdf"), pagesize=letter)
    pages = 400
    for i in range(pages):
        c.bookmarkPage(f"p{i}")
        c.drawString(72, 700, f"Page {i + 1}")
        c.linkAbsolute("", f"p{(i + 1) % pages}", (72, 690, 200, 705))
        c.showPage()
    c.save()
    out = core.rotate(tmp_path / "chain.pdf", tmp_path / "turned.pdf", 90)
    reader = PdfReader(str(out))
    assert len(reader.pages) == pages and reader.pages[0].rotation == 90
    link = reader.pages[5]["/Annots"][0].get_object()
    assert reader.get_page_number(link["/Dest"][0].get_object()) == 6
    core.watermark(out, tmp_path / "wm.pdf", text="DRAFT", pages="1")


# -- pictures in and out ---------------------------------------------------------------------------

def render(path, page=0, scale=1.0):
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        return doc[page].render(scale=scale).to_pil().convert("RGB")
    finally:
        doc.close()


def test_images_to_pdf_takes_scans_as_they_are(tmp_path):
    from PIL import Image

    # A4 at 300 dpi: Pillow reads the TIFF resolution back as a fraction.
    Image.new("L", (2480, 3508), 200).save(tmp_path / "scan.tif", dpi=(300, 300))
    out = core.images_to_pdf([tmp_path / "scan.tif"], tmp_path / "scan.pdf")
    width, height = (float(v) for v in PdfReader(str(out)).pages[0].mediabox[2:])
    assert (round(width), round(height)) == (595, 842)
    assert core.info(out)["page_sizes"] == {"A4": 1}
    # A scanner's multi-page TIFF: a page per frame.
    frames = [Image.new("L", (200, 300), v) for v in (40, 120, 220)]
    frames[0].save(tmp_path / "pages.tif", save_all=True, append_images=frames[1:], dpi=(72, 72))
    out = core.images_to_pdf([tmp_path / "pages.tif"], tmp_path / "pages.pdf")
    assert len(PdfReader(str(out)).pages) == 3
    assert render(out, 2).getpixel((100, 150))[0] > 200


def test_images_to_pdf_keeps_phone_photos_upright_and_transparency_white(tmp_path):
    from PIL import Image

    photo = Image.new("RGB", (400, 300), (10, 120, 220))
    exif = Image.Exif()
    exif[0x0112] = 6  # stored sideways: turn 90° clockwise to show
    photo.save(tmp_path / "photo.jpg", exif=exif)
    out = core.images_to_pdf([tmp_path / "photo.jpg"], tmp_path / "photo.pdf")
    width, height = (float(v) for v in PdfReader(str(out)).pages[0].mediabox[2:])
    assert (round(width), round(height)) == (300, 400)
    logo = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    logo.paste((200, 30, 30, 255), (25, 25, 75, 75))
    logo.save(tmp_path / "logo.png")
    out = core.images_to_pdf([tmp_path / "logo.png"], tmp_path / "logo.pdf")
    picture = render(out)
    assert picture.getpixel((5, 5)) == (255, 255, 255) and picture.getpixel((50, 50))[0] > 150
    deep = Image.new("I;16", (50, 50), 32768)  # a 16-bit mid-gray scan
    deep.save(tmp_path / "deep.png")
    out = core.images_to_pdf([tmp_path / "deep.png"], tmp_path / "deep.pdf")
    assert 100 < render(out).getpixel((25, 25))[0] < 160
    with pytest.raises(core.PdfManagerError, match="not a picture"):
        core.images_to_pdf([toc_pdf(tmp_path / "toc.pdf", 1)], tmp_path / "x.pdf")


def test_rendered_pages_keep_their_resolution(tmp_path):
    from PIL import Image

    src = toc_pdf(tmp_path / "toc.pdf", chapters=1)
    files = core.pdf_to_images(src, tmp_path / "imgs", dpi=150, pages="1")
    with Image.open(files[0]) as im:
        assert tuple(round(v) for v in im.info["dpi"]) == (150, 150)
    back = core.images_to_pdf(files, tmp_path / "back.pdf")
    assert core.info(back)["page_sizes"] == {"Letter": 1}
    (tmp_path / "bad.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    with pytest.raises(core.PdfManagerError):
        core.pdf_to_images(tmp_path / "bad.pdf", tmp_path / "x")


# -- colours and compression -----------------------------------------------------------------------

def content_pdf(path, content, resources=None, xobjects=None):
    """A letter page with the given content stream (and XObjects)."""
    from pypdf.generic import DecodedStreamObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    stream = DecodedStreamObject()
    stream.set_data(content)
    page[NameObject("/Contents")] = writer._add_object(stream)
    res = DictionaryObject(resources or {})
    if xobjects:
        res[NameObject("/XObject")] = DictionaryObject({NameObject(k): writer._add_object(v) for k, v in xobjects.items()})
    page[NameObject("/Resources")] = res
    writer.write(str(path))
    return path


def test_gray_fills_become_rgb_triples(tmp_path):
    src = content_pdf(tmp_path / "gray.pdf", b"/DeviceGray cs 0.5 sc 100 100 200 200 re f")
    out = core.convert_color(src, tmp_path / "rgb.pdf", "rgb")
    data = PdfReader(str(out)).pages[0].get_contents().get_data()
    assert b"/DeviceRGB cs" in data and b"0.5 0.5 0.5 sc" in data
    assert render(out).getpixel((200, 792 - 200)) == (128, 128, 128)


def test_colour_conversion_rewrites_forms_with_other_filters(tmp_path):
    import base64
    import zlib
    from pypdf.generic import EncodedStreamObject

    form = EncodedStreamObject()
    form[NameObject("/Type")] = NameObject("/XObject")
    form[NameObject("/Subtype")] = NameObject("/Form")
    form[NameObject("/BBox")] = RectangleObject([0, 0, 200, 200])
    form[NameObject("/Filter")] = ArrayObject([NameObject("/ASCII85Decode"), NameObject("/FlateDecode")])
    form._data = base64.a85encode(zlib.compress(b"1 0 0 rg 0 0 200 200 re f"), adobe=True)
    src = content_pdf(tmp_path / "form.pdf", b"q 1 0 0 1 100 100 cm /Fx Do Q", xobjects={"/Fx": form})
    out = core.convert_color(src, tmp_path / "gray.pdf", "gray")
    pixel = render(out).getpixel((200, 792 - 200))
    assert pixel[0] == pixel[1] == pixel[2] and 60 < pixel[0] < 100  # red, as gray


def test_black_and_white_and_keyed_pictures_are_left_alone(tmp_path):
    import zlib
    from pypdf.generic import DecodedStreamObject

    scan = DecodedStreamObject()
    scan.set_data(b"\xaa" * 8)
    scan.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                 NameObject("/Width"): NumberObject(8), NameObject("/Height"): NumberObject(8),
                 NameObject("/ColorSpace"): NameObject("/DeviceGray"), NameObject("/BitsPerComponent"): NumberObject(1)})
    keyed = DecodedStreamObject()
    keyed.set_data(bytes([0, 1] * 32))
    keyed.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                  NameObject("/Width"): NumberObject(8), NameObject("/Height"): NumberObject(8),
                  NameObject("/ColorSpace"): ArrayObject([NameObject("/Indexed"), NameObject("/DeviceRGB"), NumberObject(1),
                                                          __import__("pypdf").generic.ByteStringObject(b"\xff\x00\xff\x00\x00\xff")]),
                  NameObject("/BitsPerComponent"): NumberObject(8),
                  NameObject("/Mask"): ArrayObject([NumberObject(0), NumberObject(0)])})
    src = content_pdf(tmp_path / "pics.pdf", b"q 300 0 0 300 50 50 cm /Scan Do Q q 100 0 0 100 400 400 cm /Logo Do Q",
                      xobjects={"/Scan": scan, "/Logo": keyed})
    for out in (core.convert_color(src, tmp_path / "gray.pdf", "gray"), core.compress(src, tmp_path / "small.pdf", "high")):
        xobjects = PdfReader(str(out)).pages[0]["/Resources"]["/XObject"]
        assert xobjects["/Scan"].get_object()["/BitsPerComponent"] == 1
        assert "/Mask" in xobjects["/Logo"].get_object() and xobjects["/Logo"].get_object()["/ColorSpace"][0] == "/Indexed"


def test_compress_sizes_a_shared_picture_for_its_largest_use(tmp_path):
    from PIL import Image

    Image.effect_noise((1600, 1200), 80).convert("RGB").save(tmp_path / "photo.jpg", quality=95)
    c = canvas.Canvas(str(tmp_path / "album.pdf"), pagesize=letter)
    c.drawImage(str(tmp_path / "photo.jpg"), 72, 600, width=72, height=54)  # a thumbnail
    c.showPage()
    c.drawImage(str(tmp_path / "photo.jpg"), 36, 200, width=540, height=405)  # the same picture, large
    c.showPage()
    c.save()
    out = core.compress(tmp_path / "album.pdf", tmp_path / "small.pdf", "medium")
    widths = {int(x.get_object()["/Width"]) for page in PdfReader(str(out)).pages
              for x in page["/Resources"]["/XObject"].values()}
    assert len(widths) == 1 and widths.pop() >= 1000  # 150 dpi over 7.5 inches, not over 1 inch


def test_metadata_reaches_the_xmp_too(tmp_path):
    from pypdf.generic import DecodedStreamObject

    src = toc_pdf(tmp_path / "toc.pdf", chapters=1)
    w = PdfWriter(clone_from=str(src))
    xmp = DecodedStreamObject()
    xmp.set_data(b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
                 b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description rdf:about="" '
                 b'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title><rdf:Alt><rdf:li xml:lang="x-default">Draft</rdf:li>'
                 b'</rdf:Alt></dc:title></rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>')
    xmp.update({NameObject("/Type"): NameObject("/Metadata"), NameObject("/Subtype"): NameObject("/XML")})
    w._root_object[NameObject("/Metadata")] = w._add_object(xmp)
    w.write(str(src))
    out = core.set_metadata(src, tmp_path / "titled.pdf", title="Q3 Report", author="Ann & Bo")
    reader = PdfReader(str(out))
    assert reader.metadata.title == "Q3 Report"
    assert reader.xmp_metadata.dc_title == {"x-default": "Q3 Report"} and reader.xmp_metadata.dc_creator == ["Ann & Bo"]


def test_extracted_pictures_and_attachments_have_usable_names(tmp_path):
    from PIL import Image
    from pypdf.generic import DecodedStreamObject

    Image.new("RGBA", (64, 64), (200, 40, 40, 128)).save(tmp_path / "soft.png")
    c = canvas.Canvas(str(tmp_path / "pics.pdf"), pagesize=letter)
    c.drawImage(str(tmp_path / "soft.png"), 72, 500, width=144, height=144, mask="auto")
    c.showPage()
    c.save()
    w = PdfWriter(clone_from=str(tmp_path / "pics.pdf"))
    w.add_attachment("x" * 300 + ".txt", b"a very long name")
    w.write(str(tmp_path / "pics.pdf"))
    saved = core.extract_media(tmp_path / "pics.pdf", tmp_path / "out")
    assert saved["images"] and all(p.suffix in (".png", ".jpg", ".jpeg") for p in saved["images"])
    assert len(saved["attachments"]) == 1 and len(saved["attachments"][0].name) <= 120
    assert saved["attachments"][0].suffix == ".txt" and saved["attachments"][0].read_bytes() == b"a very long name"


# -- compression: speed without changing the result ------------------------------------------------

def test_ascii85_decoder_matches_the_standard_one():
    import base64
    import random

    from pdfmanager import imaging

    rng = random.Random(7)
    for size in [0, 1, 2, 3, 4, 5, 6, 9, 64, 1001]:
        for _ in range(20):
            raw = bytes(rng.choice([0, 0, rng.randrange(256)]) for _ in range(size))
            for encoded in (base64.a85encode(raw, adobe=True, wrapcol=rng.choice([0, 76])),
                            base64.a85encode(raw) + b"~>"):
                assert imaging.ascii85_decode(encoded) == raw
    with pytest.raises(ValueError):
        imaging.ascii85_decode(b"ab{cd~>")
    with pytest.raises(ValueError):
        imaging.ascii85_decode(b"uuuuu~>")  # more than 32 bits


def ascii_wrapped_pdf(path):
    """Pictures and page content the way reportlab writes them: ASCII85 over
    Flate or JPEG, and a font program with no compression at all."""
    import base64
    import io
    import zlib
    from PIL import Image
    from pypdf.generic import DecodedStreamObject, EncodedStreamObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=400)
    photo = io.BytesIO()
    Image.effect_noise((300, 200), 60).convert("RGB").save(photo, "JPEG", quality=90)
    jpeg = EncodedStreamObject()
    jpeg.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                 NameObject("/Width"): NumberObject(300), NameObject("/Height"): NumberObject(200),
                 NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8),
                 NameObject("/Filter"): ArrayObject([NameObject("/ASCII85Decode"), NameObject("/DCTDecode")])})
    jpeg._data = base64.a85encode(photo.getvalue(), wrapcol=72) + b"~>"  # as PDF writes it: no <~
    gray = EncodedStreamObject()
    gray.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                 NameObject("/Width"): NumberObject(64), NameObject("/Height"): NumberObject(32),
                 NameObject("/ColorSpace"): NameObject("/DeviceGray"), NameObject("/BitsPerComponent"): NumberObject(8),
                 NameObject("/Filter"): ArrayObject([NameObject("/ASCIIHexDecode"), NameObject("/FlateDecode")])})
    gray._data = zlib.compress(bytes(range(256)) * 8).hex().encode() + b">"
    font = DecodedStreamObject()
    font.set_data(b"%!FontType1 fake font program " * 200)
    content = DecodedStreamObject()
    content.set_data(b"q 300 0 0 200 0 0 cm /Im1 Do Q q 64 0 0 32 400 300 cm /Im2 Do Q")
    page[NameObject("/Contents")] = writer._add_object(content)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({
        NameObject("/Im1"): writer._add_object(jpeg), NameObject("/Im2"): writer._add_object(gray)})})
    page[NameObject("/PieceInfo")] = writer._add_object(font)
    writer.write(str(path))
    return path


def test_lossless_compression_drops_text_wrappers_and_packs_plain_streams(tmp_path):
    from PIL import ImageChops

    src = ascii_wrapped_pdf(tmp_path / "wrapped.pdf")
    out = core.compress(src, tmp_path / "low.pdf", "low")
    assert out.stat().st_size < 0.85 * src.stat().st_size
    reader = PdfReader(str(out))
    xobjects = reader.pages[0]["/Resources"]["/XObject"]
    assert xobjects["/Im1"].get_object()["/Filter"] == "/DCTDecode"
    assert xobjects["/Im2"].get_object()["/Filter"] == "/FlateDecode"
    assert reader.pages[0]["/PieceInfo"].get_object()["/Filter"] == "/FlateDecode"
    before, after = PdfReader(str(src)), reader
    for a, b in zip(before.pages[0].images, after.pages[0].images):
        assert ImageChops.difference(a.image.convert("RGB"), b.image.convert("RGB")).getbbox() is None
    assert render(out).tobytes() == render(src).tobytes()


def test_pictures_are_read_the_same_way_pypdf_reads_them(tmp_path):
    import io
    from PIL import Image, ImageChops
    from pypdf.generic import DecodedStreamObject, EncodedStreamObject
    from pypdf.generic._image_xobject import _xobj_to_image

    from pdfmanager import imaging

    writer = PdfWriter()
    icc = DecodedStreamObject()
    icc.set_data(b"not really a profile")
    icc[NameObject("/N")] = NumberObject(3)
    cases = []
    for mode, space in (("L", NameObject("/DeviceGray")), ("RGB", NameObject("/DeviceRGB")),
                        ("CMYK", NameObject("/DeviceCMYK")),
                        ("RGB", ArrayObject([NameObject("/ICCBased"), writer._add_object(icc)]))):
        picture = Image.effect_noise((40, 30), 50).convert(mode)
        obj = DecodedStreamObject()
        obj.set_data(picture.tobytes())
        obj.update({NameObject("/Subtype"): NameObject("/Image"), NameObject("/Width"): NumberObject(40),
                    NameObject("/Height"): NumberObject(30), NameObject("/ColorSpace"): space,
                    NameObject("/BitsPerComponent"): NumberObject(8)})
        cases.append(obj.flate_encode())
    photo = io.BytesIO()
    Image.effect_noise((80, 60), 50).convert("RGB").save(photo, "JPEG")
    jpeg = EncodedStreamObject()
    jpeg.update({NameObject("/Subtype"): NameObject("/Image"), NameObject("/Width"): NumberObject(80),
                 NameObject("/Height"): NumberObject(60), NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                 NameObject("/BitsPerComponent"): NumberObject(8), NameObject("/Filter"): NameObject("/DCTDecode")})
    jpeg._data = photo.getvalue()
    for obj in cases:
        ours, theirs = imaging._decode(obj), _xobj_to_image(obj)[2]
        assert ours.mode == theirs.mode and ours.size == theirs.size
        assert ImageChops.difference(ours, theirs).getbbox() is None
    # The JPEG is the original one, decoded once (pypdf's reader saves it
    # again as a JPEG first, losing a little more of the picture).
    original = Image.open(io.BytesIO(photo.getvalue()))
    assert ImageChops.difference(imaging._decode(jpeg), original.convert("RGB")).getbbox() is None
    # A JPEG shrunk anyway is decoded at a smaller scale.
    assert imaging._decode(jpeg, (20, 15)).size == (20, 15)
