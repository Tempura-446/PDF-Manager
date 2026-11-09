"""The invisible text layer OCR puts over a scanned page.

Each recognised word is set in text render mode 3 (invisible) exactly over
its place on the scan, followed by a zero-width space, so the page looks
unchanged while search, select, copy and PDF -> Excel work. The words use one "glyphless" font per file: a
Type0 font with Identity-H encoding where every character's CID is its own
UCS-2 code, a ToUnicode map that says so, and every CID drawn with glyph 0
of an embedded TrueType font. That makes any character extractable (pypdf,
pdfplumber and pdf.js all read it) with a single embedded font, which is
how Tesseract's own PDF output works too.

Word boxes come in image pixels of the rendered page (y down, as the
scanner saw it); ``image_to_page_matrix`` maps them back through the
page's /Rotate and crop box into PDF user space.
"""
import math
import zlib
from dataclasses import dataclass
from pathlib import Path

from pypdf import PageObject, Transformation
from pypdf.generic import (ArrayObject, DictionaryObject, NameObject,
                           NumberObject, StreamObject)

GLYPHLESS_FONT_RESOURCE = "/FZPDFOCR"
GLYPH_WIDTH = 500  # of 1000: every CID is half an em wide (/DW below)


@dataclass
class Word:
    """A recognised word. ``quad`` is its box in image pixels: four (x, y)
    points, top-left, top-right, bottom-right, bottom-left, y down."""
    text: str
    quad: list
    confidence: float = 1.0


def _font_file():
    import reportlab

    return (Path(reportlab.__file__).parent / "fonts" / "Vera.ttf").read_bytes()


def _stream(writer, data, **entries):
    obj = StreamObject()
    obj.set_data(zlib.compress(data))
    obj[NameObject("/Filter")] = NameObject("/FlateDecode")
    for key, value in entries.items():
        obj[NameObject("/" + key)] = value
    return writer._add_object(obj)


def _dict(**entries):
    return DictionaryObject({NameObject("/" + k): v for k, v in entries.items()})


def _glyphless_font(writer):
    """The file's one OCR font (an indirect reference), made on first use."""
    cached = getattr(writer, "_zpdf_ocr_font", None)
    if cached is not None:
        return cached
    ttf = _font_file()
    font_file = _stream(writer, ttf, Length1=NumberObject(len(ttf)))
    descriptor = writer._add_object(_dict(
        Type=NameObject("/FontDescriptor"), FontName=NameObject("/GlyphLessFont"),
        Flags=NumberObject(5), FontBBox=ArrayObject([NumberObject(0), NumberObject(0),
                                                     NumberObject(GLYPH_WIDTH), NumberObject(1000)]),
        ItalicAngle=NumberObject(0), Ascent=NumberObject(1000), Descent=NumberObject(0),
        CapHeight=NumberObject(1000), StemV=NumberObject(80), FontFile2=font_file))
    # Every CID draws glyph 0: 65536 two-byte entries of zero.
    cid_to_gid = _stream(writer, bytes(2 * 65536))
    descendant = writer._add_object(_dict(
        Type=NameObject("/Font"), Subtype=NameObject("/CIDFontType2"),
        BaseFont=NameObject("/GlyphLessFont"),
        CIDSystemInfo=_dict(Registry=_text("Adobe"), Ordering=_text("Identity"),
                            Supplement=NumberObject(0)),
        FontDescriptor=descriptor, DW=NumberObject(GLYPH_WIDTH), CIDToGIDMap=cid_to_gid,
        # A space takes no room: each word ends with one so that text
        # extraction always splits words there, whatever the gap between
        # their boxes on the page.
        W=ArrayObject([NumberObject(0x20), ArrayObject([NumberObject(0)])])))
    to_unicode = _stream(writer, b"\n".join([
        b"/CIDInit /ProcSet findresource begin",
        b"12 dict begin",
        b"begincmap",
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        b"/CMapName /Adobe-Identity-UCS def",
        b"/CMapType 2 def",
        b"1 begincodespacerange",
        b"<0000> <FFFF>",
        b"endcodespacerange",
        b"1 beginbfrange",
        b"<0000> <FFFF> <0000>",
        b"endbfrange",
        b"endcmap",
        b"CMapName currentdict /CMap defineresource pop",
        b"end",
        b"end",
    ]))
    font = writer._add_object(_dict(
        Type=NameObject("/Font"), Subtype=NameObject("/Type0"),
        BaseFont=NameObject("/GlyphLessFont"), Encoding=NameObject("/Identity-H"),
        DescendantFonts=ArrayObject([descendant]), ToUnicode=to_unicode))
    writer._zpdf_ocr_font = font
    return font


def _text(s):
    from pypdf.generic import TextStringObject

    return TextStringObject(s)


def _encode(text):
    """The CIDs of ``text``: UCS-2, with characters beyond the BMP replaced."""
    return "".join(ch if ord(ch) <= 0xFFFF else "�" for ch in text).encode("utf-16-be")


def _display_size(page):
    """(width, height) in points of the page as displayed: its crop box,
    turned by /Rotate."""
    box = page.cropbox
    w, h = float(box.width), float(box.height)
    return (h, w) if (page.rotation or 0) % 180 == 90 else (w, h)


def image_to_page_matrix(page, image_size, dpi):
    """The matrix taking display points (origin at the bottom-left of the
    rendered image, y up) to the page's user space: undoes /Rotate and
    moves to the crop box."""
    box = page.cropbox
    x0, y0 = float(box.left), float(box.bottom)
    w, h = float(box.width), float(box.height)
    rotation = (page.rotation or 0) % 360
    if rotation == 90:    # displayed turned clockwise: page bottom-left is at the top-left
        return (0, 1, -1, 0, x0 + w, y0)
    if rotation == 180:
        return (-1, 0, 0, -1, x0 + w, y0 + h)
    if rotation == 270:
        return (0, -1, 1, 0, x0, y0 + h)
    return (1, 0, 0, 1, x0, y0)


def _word_matrix(word, scale, display_h):
    """Text matrix putting a 1 pt glyphless word onto the word's box, in
    display points; None for a box with no size."""
    pts = [(x * scale, display_h - y * scale) for x, y in word.quad]
    (tlx, tly), _, (brx, bry), (blx, bly) = pts
    base_x, base_y = brx - blx, bry - bly       # along the bottom edge
    width = math.hypot(base_x, base_y)
    height = math.hypot(tlx - blx, tly - bly)
    if width < 0.01 or height < 0.01:
        return None
    angle = math.atan2(base_y, base_x)
    sx = width / (len(word.text) * GLYPH_WIDTH / 1000)
    cos, sin = math.cos(angle), math.sin(angle)
    return (sx * cos, sx * sin, -height * sin, height * cos, blx, bly)


def add_text_layer(writer, page, words, image_size, dpi):
    """Merge an invisible layer of ``words`` (boxes in pixels of the page
    rendered at ``dpi`` to ``image_size``) onto ``page``, a page of
    ``writer``. Returns how many words were written."""
    display_w, display_h = _display_size(page)
    scale = 72 / dpi
    parts = [b"BT 3 Tr " + GLYPHLESS_FONT_RESOURCE.encode() + b" 1 Tf"]
    written = 0
    for word in words:
        if not word.text:
            continue
        matrix = _word_matrix(word, scale, display_h)
        if matrix is None:
            continue
        parts.append(b" ".join(f"{v:.4f}".rstrip("0").rstrip(".").encode() for v in matrix)
                     + b" Tm <" + _encode(word.text + " ").hex().encode() + b"> Tj")
        written += 1
    if not written:
        return 0
    parts.append(b"ET")
    layer = PageObject.create_blank_page(width=display_w, height=display_h)
    content = StreamObject()
    content.set_data(b"\n".join(parts))
    layer[NameObject("/Contents")] = writer._add_object(content)
    layer[NameObject("/Resources")] = _dict(
        Font=_dict(**{GLYPHLESS_FONT_RESOURCE[1:]: _glyphless_font(writer)}))
    page.merge_transformed_page(layer, Transformation(image_to_page_matrix(page, image_size, dpi)))
    return written
