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


# -- unsure OCR words (private /PieceInfo metadata) and in-place correction -----

UNSURE_CONFIDENCE = 0.6   # words with 0.3 <= confidence < 0.6 are kept and marked unsure
_PIECE_APP = "/ZPDF"
_PIECE_KEY = "/OCRUnsure"


def _page_unsure_list(page):
    """Read the list of unsure word dicts stored in ``page``'s /PieceInfo, or []."""
    import json

    piece = page.get("/PieceInfo")
    if piece is None:
        return []
    piece = piece.get_object()
    app = piece.get(_PIECE_APP)
    if app is None:
        return []
    app = app.get_object()
    priv = app.get("/Private")
    if priv is None:
        return []
    priv = priv.get_object()
    raw = priv.get(_PIECE_KEY)
    if raw is None:
        return []
    try:
        data = json.loads(str(raw.get_object()))
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def _write_page_unsure_list(page, items):
    """Store ``items`` in ``page``'s /PieceInfo /ZPDF /Private /OCRUnsure
    (or remove the key when ``items`` is empty)."""
    import datetime
    import json

    if not items:
        piece = page.get("/PieceInfo")
        if piece is not None:
            piece = piece.get_object()
            app = piece.get(_PIECE_APP)
            if app is not None:
                app = app.get_object()
                priv = app.get("/Private")
                if priv is not None:
                    priv = priv.get_object()
                    priv.pop(NameObject(_PIECE_KEY), None)
        return
    piece = page.get("/PieceInfo")
    if piece is None:
        piece = DictionaryObject()
        page[NameObject("/PieceInfo")] = piece
    else:
        piece = piece.get_object()
    app = piece.get(_PIECE_APP)
    if app is None:
        app = DictionaryObject()
        piece[NameObject(_PIECE_APP)] = app
    else:
        app = app.get_object()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("D:%Y%m%d%H%M%SZ")
    app[NameObject("/LastModified")] = _text(stamp)
    priv = app.get("/Private")
    if priv is None:
        priv = DictionaryObject()
        app[NameObject("/Private")] = priv
    else:
        priv = priv.get_object()
    priv[NameObject(_PIECE_KEY)] = _text(json.dumps(items, ensure_ascii=False, separators=(",", ":")))


def _count_ocr_words_in_page(page, writer):
    """How many glyphless-font words are already written on ``page``."""
    from pypdf.generic import ContentStream

    contents = page.get("/Contents")
    if contents is None:
        return 0
    streams = list(contents.get_object()) if isinstance(contents.get_object(), ArrayObject) else [contents]
    count = 0
    font_name = NameObject(GLYPHLESS_FONT_RESOURCE)
    for ref in streams:
        obj = ref.get_object()
        if GLYPHLESS_FONT_RESOURCE.encode() not in obj.get_data():
            continue
        cs = ContentStream(obj, writer)
        in_ocr = False
        for operands, operator in cs.operations:
            if operator == b"Tf":
                in_ocr = bool(operands) and operands[0] == font_name
            elif operator == b"ET":
                in_ocr = False
            elif operator == b"Tj" and in_ocr and operands:
                if _decode_tj(operands[0]) != " ":
                    count += 1
    return count


def _decode_tj(operand):
    raw = operand.original_bytes if hasattr(operand, "original_bytes") else bytes(operand)
    try:
        return raw.decode("utf-16-be")
    except UnicodeDecodeError:
        return ""


def record_unsure_words(writer, page, words, image_size, dpi, max_confidence=UNSURE_CONFIDENCE, base_index=0):
    """Record words whose confidence is below ``max_confidence`` in ``page``'s
    private /PieceInfo dictionary (invisible to other PDF viewers). Returns
    the number of unsure words recorded."""
    display_w, display_h = _display_size(page)
    scale = 72.0 / dpi
    existing = _page_unsure_list(page)
    items = list(existing)
    idx = base_index
    added = 0
    for word in words:
        if not word.text:
            continue
        if _word_matrices(word, scale, display_h) is None:
            continue
        if word.confidence < max_confidence:
            xs = [x * scale for x, _ in word.quad]
            ys = [y * scale for _, y in word.quad]
            items.append({
                "id": idx,
                "text": word.text,
                "confidence": round(float(word.confidence), 3),
                "box": [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)],
                "page_size": [round(display_w, 2), round(display_h, 2)],
            })
            added += 1
        idx += 1
    if added:
        _write_page_unsure_list(page, items)
    return added


def read_unsure_words(path, password=None):
    """Read all unsure OCR words stored in ``path``'s private /PieceInfo keys.
    Returns ``{"pages": {"1": [...], ...}, "total": N}``."""
    from pdfmanager import core

    reader = core.open_pdf(path, password)
    pages = {}
    total = 0
    for i, page in enumerate(reader.pages, start=1):
        items = _page_unsure_list(page)
        if items:
            pages[str(i)] = items
            total += len(items)
    return {"pages": pages, "total": total}


def correct_words(path, output, edits, password=None):
    """Apply corrections to invisible OCR words in ``path`` and save to
    ``output``. Only the invisible text layer and /PieceInfo metadata change;
    no image or drawn content is touched (pixel-identical pages).

    Each item in ``edits`` is a dict:
    ``{"page": 1, "id": 12, "text": "right"}`` or
    ``{"page": 1, "id": 12, "accept": True}`` (keeps the text, clears unsure).
    """
    from pathlib import Path
    from pypdf import PdfWriter
    from pypdf.generic import ByteStringObject, ContentStream, FloatObject

    from pdfmanager import core

    reader = core.open_pdf(path, password)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)

    by_page = {}
    for edit in edits or ():
        page_num = int(edit["page"])
        if not 1 <= page_num <= len(writer.pages):
            raise core.PdfManagerError(f"page {page_num} is out of range (1–{len(writer.pages)})")
        by_page.setdefault(page_num, {})[int(edit["id"])] = edit

    font_name = NameObject(GLYPHLESS_FONT_RESOURCE)
    for page_num, page_edits in by_page.items():
        page = writer.pages[page_num - 1]
        text_changes = {wid: str(ed["text"]) for wid, ed in page_edits.items()
                        if not ed.get("accept") and "text" in ed}
        if text_changes:
            contents = page.get("/Contents")
            if contents is not None:
                cobj = contents.get_object()
                streams = list(cobj) if isinstance(cobj, ArrayObject) else [contents]
                word_idx = 0
                for ref in streams:
                    stream_obj = ref.get_object()
                    if GLYPHLESS_FONT_RESOURCE.encode() not in stream_obj.get_data():
                        continue
                    cs = ContentStream(stream_obj, writer)
                    in_ocr = False
                    last_tm_pos = None
                    modified = False
                    for pos, (operands, operator) in enumerate(cs.operations):
                        if operator == b"Tf":
                            in_ocr = bool(operands) and operands[0] == font_name
                        elif operator == b"ET":
                            in_ocr = False
                            last_tm_pos = None
                        elif operator == b"Tm" and in_ocr:
                            last_tm_pos = pos
                        elif operator == b"Tj" and in_ocr and operands:
                            old_text = _decode_tj(operands[0])
                            if old_text == " ":
                                continue
                            if word_idx in text_changes:
                                new_text = text_changes[word_idx]
                                operands[0] = ByteStringObject(_encode(new_text))
                                if last_tm_pos is not None and new_text:
                                    tm_ops = cs.operations[last_tm_pos][0]
                                    if len(tm_ops) == 6:
                                        ratio = max(len(old_text), 1) / max(len(new_text), 1)
                                        tm_ops[0] = FloatObject(round(float(tm_ops[0]) * ratio, 4))
                                        tm_ops[1] = FloatObject(round(float(tm_ops[1]) * ratio, 4))
                                modified = True
                            word_idx += 1
                    if modified:
                        stream_obj.set_data(cs.get_data())

        # Remove corrected / accepted word IDs from the page's unsure list.
        remaining = [item for item in _page_unsure_list(page) if int(item.get("id", -1)) not in page_edits]
        _write_page_unsure_list(page, remaining)

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        writer.write(f)
    return out


def _tm_to_display_box(tm_ops, text: str, display_w: float, display_h: float):
    """Convert a /FZPDFOCR Tm matrix + decoded word into a top-left display box."""
    if not tm_ops or len(tm_ops) != 6:
        return [0.0, 0.0, 10.0, 10.0]
    a, b, c, d, e, f = (float(v) for v in tm_ops)
    span = max(len(text), 1) * (GLYPH_WIDTH / 1000.0)
    wx, wy = a * span, b * span
    pts = [
        (e, f),
        (e + wx, f + wy),
        (e + c, f + d),
        (e + wx + c, f + wy + d),
    ]
    xs = [max(0.0, min(display_w, p[0])) for p in pts]
    ys = [max(0.0, min(display_h, display_h - p[1])) for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if x1 <= x0:
        x1 = min(display_w, x0 + 4.0)
    if y1 <= y0:
        y1 = min(display_h, y0 + 4.0)
    return [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)]


def read_all_ocr_words(path, password=None, page=None):
    """Return all invisible /FZPDFOCR words with page-space boxes and unsure flags.

    Allows clicking and fixing *any* OCR word in place, even when confidence was
    high or /PieceInfo /OCRUnsure is empty.
    """
    from pypdf.generic import ContentStream

    from pdfmanager import core

    reader = core.open_pdf(path, password)
    font_name = NameObject(GLYPHLESS_FONT_RESOURCE)
    pages_out = {}
    total = 0

    page_indices = [int(page)] if page is not None else list(range(1, len(reader.pages) + 1))
    for page_num in page_indices:
        if not 1 <= page_num <= len(reader.pages):
            continue
        pg = reader.pages[page_num - 1]
        display_w, display_h = _display_size(pg)
        unsure_map = {int(it.get("id", -1)): it for it in _page_unsure_list(pg)}
        contents = pg.get("/Contents")
        if contents is None:
            continue
        cobj = contents.get_object()
        streams = list(cobj) if isinstance(cobj, ArrayObject) else [contents]
        word_idx = 0
        items = []
        for ref in streams:
            stream_obj = ref.get_object()
            if GLYPHLESS_FONT_RESOURCE.encode() not in stream_obj.get_data():
                continue
            cs = ContentStream(stream_obj, reader)
            in_ocr = False
            last_tm = None
            for operands, operator in cs.operations:
                if operator == b"Tf":
                    in_ocr = bool(operands) and operands[0] == font_name
                elif operator == b"ET":
                    in_ocr = False
                    last_tm = None
                elif operator == b"Tm" and in_ocr:
                    last_tm = operands
                elif operator == b"Tj" and in_ocr and operands:
                    text = _decode_tj(operands[0])
                    if text == " ":
                        continue
                    unsure_entry = unsure_map.get(word_idx)
                    box = (
                        unsure_entry["box"]
                        if unsure_entry and isinstance(unsure_entry.get("box"), list)
                        else _tm_to_display_box(last_tm, text, display_w, display_h)
                    )
                    items.append({
                        "id": word_idx,
                        "text": text,
                        "confidence": unsure_entry.get("confidence", 1.0) if unsure_entry else 1.0,
                        "unsure": unsure_entry is not None,
                        "box": box,
                        "page_size": [round(display_w, 2), round(display_h, 2)],
                    })
                    word_idx += 1
        if items:
            pages_out[str(page_num)] = items
            total += len(items)
    return {"pages": pages_out, "total": total}


def replace_ocr_text(path, output, find_text, replace_text, password=None, case_sensitive=False):
    """Batch find-and-replace across invisible /FZPDFOCR words in ``path``.

    Rescales each modified word's ``Tm`` matrix proportionally and leaves all
    drawn/image page content pixel-identical.
    """
    import re
    from pathlib import Path
    from pypdf import PdfWriter
    from pypdf.generic import ByteStringObject, ContentStream, FloatObject

    from pdfmanager import core

    needle = str(find_text or "")
    if not needle:
        raise core.PdfManagerError("find_text cannot be empty")
    repl = str(replace_text if replace_text is not None else "")

    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(re.escape(needle), flags)

    reader = core.open_pdf(path, password)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    font_name = NameObject(GLYPHLESS_FONT_RESOURCE)

    replaced = 0
    touched_pages = []

    for page_num, page in enumerate(writer.pages, start=1):
        contents = page.get("/Contents")
        if contents is None:
            continue
        cobj = contents.get_object()
        streams = list(cobj) if isinstance(cobj, ArrayObject) else [contents]
        page_hits = 0
        fixed_ids = set()
        word_idx = 0
        for ref in streams:
            stream_obj = ref.get_object()
            if GLYPHLESS_FONT_RESOURCE.encode() not in stream_obj.get_data():
                continue
            cs = ContentStream(stream_obj, writer)
            in_ocr = False
            last_tm_pos = None
            modified = False
            for pos, (operands, operator) in enumerate(cs.operations):
                if operator == b"Tf":
                    in_ocr = bool(operands) and operands[0] == font_name
                elif operator == b"ET":
                    in_ocr = False
                    last_tm_pos = None
                elif operator == b"Tm" and in_ocr:
                    last_tm_pos = pos
                elif operator == b"Tj" and in_ocr and operands:
                    old_text = _decode_tj(operands[0])
                    if old_text == " ":
                        continue
                    new_text, count = pattern.subn(lambda _m: repl, old_text)
                    if count > 0 and new_text != old_text:
                        operands[0] = ByteStringObject(_encode(new_text))
                        if last_tm_pos is not None and new_text:
                            tm_ops = cs.operations[last_tm_pos][0]
                            if len(tm_ops) == 6:
                                ratio = max(len(old_text), 1) / max(len(new_text), 1)
                                tm_ops[0] = FloatObject(round(float(tm_ops[0]) * ratio, 4))
                                tm_ops[1] = FloatObject(round(float(tm_ops[1]) * ratio, 4))
                        modified = True
                        page_hits += count
                        fixed_ids.add(word_idx)
                    word_idx += 1
            if modified:
                stream_obj.set_data(cs.get_data())
        if page_hits:
            replaced += page_hits
            touched_pages.append(page_num)
            if fixed_ids:
                remaining = [it for it in _page_unsure_list(page) if int(it.get("id", -1)) not in fixed_ids]
                _write_page_unsure_list(page, remaining)

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        writer.write(f)
    return {"replaced": replaced, "pages": touched_pages}

