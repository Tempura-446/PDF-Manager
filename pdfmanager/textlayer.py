# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
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
import base64
import math
import zlib
from dataclasses import dataclass

from pypdf import PageObject, Transformation
from pypdf.generic import (ArrayObject, DictionaryObject, NameObject,
                           NumberObject, StreamObject)

GLYPHLESS_FONT_RESOURCE = "/FZPDFOCR"
GLYPH_WIDTH = 500  # of 1000: the empty glyph is half an em wide (/DW below matches)

# Tesseract's pdf.ttf (Apache License 2.0): a TrueType font of two glyphs,
# .notdef and an empty glyph 1 that is half an em wide. 572 bytes.
GLYPHLESS_TTF = base64.b64decode("""AAEAAAAKAIAAAwAgT1MvMlbeyJQAAAEoAAAAYGNtYXAACgA0AAABkAAAAB5nbHlmFSJBJAAAAbgA
AAAYaGVhZAt48WUAAACsAAAANmhoZWEMAgQCAAAA5AAAACRobXR4BAAAAAAAAYgAAAAIbG9jYQAM
AAAAAAGwAAAABm1heHAABAAFAAABCAAAACBuYW1l8usW2gAAAdAAAABLcG9zdAABAAEAAAIcAAAA
IAABAAAAAQAAsJRxEF8PPPUEBwgAAAAAAM+a/G4AAAAA1MOn8gAAAAAEAAgAAAAAEAACAAAAAAAA
AAEAAAgA//8AAAQAAAAAAAQAAAEAAAAAAAAAAAAAAAAAAAACAAEAAAACAAQAAQAAAAAAAQAAAAAA
AAAAAAAAAAAAAAAAAwAAAZAABQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAUAAQABAAAAAAAAAAAA
AAAAAAAAAAAAAAAAR09PRwBAAAAAAAAB//8AAAABAAGAAAAAAAAAAAAAAAAAAAABAAAAAAAABAAA
AAAAAAIAAQAAAAAAFAADAAAAAAAUAAYACgAAAAAAAAAAAAAAAAAMAAAAAQAAAAAEAAgAAAMAADEh
ESEEAPwACAAAAAADACoAAAADAAAABQAWAAAAAQAAAAAABQALABYAAwABBAkABQAWAAAAVgBlAHIA
cwBpAG8AbgAgADEALgAwVmVyc2lvbiAxLjAAAAEAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAA
AAA=""")


@dataclass
class Word:
    """A recognised word. ``quad`` is its box in image pixels: four (x, y)
    points, top-left, top-right, bottom-right, bottom-left, y down."""
    text: str
    quad: list
    confidence: float = 1.0


def _turn(page):
    """The page's /Rotate as 0, 90, 180 or 270 (anything else shows as 0)."""
    try:
        rotation = int(page.rotation or 0) % 360
    except (TypeError, ValueError):
        return 0
    return rotation if rotation % 90 == 0 else 0


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
    ttf = GLYPHLESS_TTF
    font_file = _stream(writer, ttf, Length1=NumberObject(len(ttf)))
    descriptor = writer._add_object(_dict(
        Type=NameObject("/FontDescriptor"), FontName=NameObject("/GlyphLessFont"),
        Flags=NumberObject(5), FontBBox=ArrayObject([NumberObject(0), NumberObject(0),
                                                     NumberObject(GLYPH_WIDTH), NumberObject(1000)]),
        ItalicAngle=NumberObject(0), Ascent=NumberObject(1000), Descent=NumberObject(0),
        CapHeight=NumberObject(1000), StemV=NumberObject(80), FontFile2=font_file))
    # Every CID draws glyph 1, the empty one: 65536 two-byte entries.
    cid_to_gid = _stream(writer, b"\x00\x01" * 65536)
    descendant = writer._add_object(_dict(
        Type=NameObject("/Font"), Subtype=NameObject("/CIDFontType2"),
        BaseFont=NameObject("/GlyphLessFont"),
        CIDSystemInfo=_dict(Registry=_text("Adobe"), Ordering=_text("Identity"),
                            Supplement=NumberObject(0)),
        FontDescriptor=descriptor, DW=NumberObject(GLYPH_WIDTH), CIDToGIDMap=cid_to_gid))
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
    return (h, w) if _turn(page) % 180 == 90 else (w, h)


def image_to_page_matrix(page, image_size, dpi):
    """The matrix taking display points (origin at the bottom-left of the
    rendered image, y up) to the page's user space: undoes /Rotate and
    moves to the crop box."""
    box = page.cropbox
    x0, y0 = float(box.left), float(box.bottom)
    w, h = float(box.width), float(box.height)
    rotation = _turn(page)
    if rotation == 90:    # displayed turned clockwise: page bottom-left is at the top-left
        return (0, 1, -1, 0, x0 + w, y0)
    if rotation == 180:
        return (-1, 0, 0, -1, x0 + w, y0 + h)
    if rotation == 270:
        return (0, -1, 1, 0, x0, y0 + h)
    return (1, 0, 0, 1, x0, y0)


SPACE_SCALE = 0.01  # the space after a word is this fraction of a glyph wide


def _word_matrices(word, scale, display_h):
    """Two text matrices in display points: one putting the 1 pt glyphless
    word onto the word's box (the characters fill it), one putting the
    space that follows at the box's right edge, almost no width. None for
    a box with no size. The space is what makes every reader split the
    words, whatever the gap between their boxes; kept this narrow, it
    neither overhangs the next word (pypdf then drops it) nor leaves the
    word's own box short of its ink."""
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
    word_matrix = (sx * cos, sx * sin, -height * sin, height * cos, blx, bly)
    space_matrix = (sx * SPACE_SCALE * cos, sx * SPACE_SCALE * sin, -height * sin, height * cos, brx, bry)
    return word_matrix, space_matrix


def _tj(matrix, text):
    numbers = b" ".join(f"{v:.4f}".rstrip("0").rstrip(".").encode() for v in matrix)
    return numbers + b" Tm <" + _encode(text).hex().encode() + b"> Tj"


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
        matrices = _word_matrices(word, scale, display_h)
        if matrices is None:
            continue
        parts.append(_tj(matrices[0], word.text))
        parts.append(_tj(matrices[1], " "))
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
