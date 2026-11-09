"""True redaction: take out what lies under the redaction boxes, keep the
rest of the page as it was.

A black box drawn over text hides nothing: the words are still in the
page's content and can be copied out. Turning the page into a picture
(redact.py's other method) is safe but makes the whole page unsearchable.
Like Acrobat, this module edits the page's own content instead:

- text: the letters under a box are dropped from the strings that draw
  them, and the letters after them are moved back where they were with TJ
  offsets, so everything else stays selectable and searchable;
- pictures: the covered pixels are painted black in a copy of the picture
  (a picture that is covered completely is dropped);
- drawings: shapes that lie completely under a box are dropped (lines that
  only cross a box stay; the box covers them);
- form XObjects (reusable bits of page) get the same treatment, in a copy.

Shared objects are never changed in place: anything edited is a new
object, so other pages that use the original keep it. Pages using
something this can't safely edit (Type3 fonts, vertical text, unusual
font encodings, damaged content, JPEG 2000 pictures) raise
:class:`Unsupported`; :func:`remove` then leaves them to the picture
method. After editing, :func:`remove` checks every page with pdfium: no
letter may remain under a box, the searched words must be gone and the
remaining letters must not have moved.
"""

import io
import math
import re
import zlib

import pikepdf
from pikepdf import Name

from pdfmanager import core, progress

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
MAX_DEPTH = 12  # forms inside forms
EPS = 1e-3


class Unsupported(Exception):
    """The page uses something that can't be edited safely; the message
    says what, in words for the user ("it uses a Type3 font")."""


# -- geometry --------------------------------------------------------------------------------

def _mul(m, n):
    """m then n (PDF row-vector convention: [x y 1] x m x n)."""
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (a * a2 + b * c2, a * b2 + b * d2, c * a2 + d * c2, c * b2 + d * d2,
            e * a2 + f * c2 + e2, e * b2 + f * d2 + f2)


def _apply(m, x, y):
    return x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5]


def _invert(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    return (d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det)


def _bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _overlaps(box, area):
    return box[0] < area[2] and box[2] > area[0] and box[1] < area[3] and box[3] > area[1]


def _inside(box, area, slack=0.01):
    return (box[0] >= area[0] - slack and box[1] >= area[1] - slack
            and box[2] <= area[2] + slack and box[3] <= area[3] + slack)


def _clip(poly, axis, limit, keep_above):
    """Sutherland-Hodgman: the part of ``poly`` with coordinate ``axis`` >=
    (or <=) ``limit``."""
    def ins(p):
        return p[axis] >= limit if keep_above else p[axis] <= limit

    result = []
    for i, cur in enumerate(poly):
        prev = poly[i - 1]
        if ins(cur):
            if not ins(prev):
                result.append(_cross(prev, cur, axis, limit))
            result.append(cur)
        elif ins(prev):
            result.append(_cross(prev, cur, axis, limit))
    return result


def _cross(p, q, axis, limit):
    t = (limit - p[axis]) / (q[axis] - p[axis])
    return (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]))


def _clip_rect(poly, rect):
    x0, y0, x1, y1 = rect
    for axis, limit, above in ((0, x0, True), (0, x1, False), (1, y0, True), (1, y1, False)):
        poly = _clip(poly, axis, limit, above)
        if not poly:
            break
    return poly


def _area(poly):
    return abs(sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(poly, poly[1:] + poly[:1]))) / 2


HIT_SHARE = 0.3  # how much of a letter must be under a box for it to go (its middle always does)


def _hits(quad, areas):
    """Whether a letter is to go: its middle or a good part of it is under
    a box. ``quad`` is the letter's box in page space (advance width by
    ascent and descent), with the sides pulled in a little: letters seldom
    reach the edges of their box, and the padding around found words
    should not take the full stop after them."""
    box = _bbox(quad)
    whole = _area(quad)
    cx = sum(p[0] for p in quad) / 4
    cy = sum(p[1] for p in quad) / 4
    for area in areas:
        if not (box[0] <= area[2] and box[2] >= area[0] and box[1] <= area[3] and box[3] >= area[1]):
            continue
        if area[0] <= cx <= area[2] and area[1] <= cy <= area[3]:
            return True
        if whole > 1e-9 and _area(_clip_rect(list(quad), area)) >= HIT_SHARE * whole:
            return True
    return False


# -- fonts ------------------------------------------------------------------------------------

_STD14_ALIASES = {
    "Arial": "Helvetica", "Arial,Bold": "Helvetica-Bold", "Arial,Italic": "Helvetica-Oblique",
    "Arial,BoldItalic": "Helvetica-BoldOblique", "Arial-BoldMT": "Helvetica-Bold",
    "ArialMT": "Helvetica", "Arial-ItalicMT": "Helvetica-Oblique",
    "Arial-BoldItalicMT": "Helvetica-BoldOblique",
    "TimesNewRoman": "Times-Roman", "TimesNewRoman,Bold": "Times-Bold",
    "TimesNewRoman,Italic": "Times-Italic", "TimesNewRoman,BoldItalic": "Times-BoldItalic",
    "TimesNewRomanPSMT": "Times-Roman", "TimesNewRomanPS-BoldMT": "Times-Bold",
    "TimesNewRomanPS-ItalicMT": "Times-Italic", "TimesNewRomanPS-BoldItalicMT": "Times-BoldItalic",
    "CourierNew": "Courier", "CourierNew,Bold": "Courier-Bold", "CourierNew,Italic": "Courier-Oblique",
    "CourierNew,BoldItalic": "Courier-BoldOblique", "CourierNewPSMT": "Courier",
}


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class _Font:
    """What redaction needs to know about a font: how its strings split
    into character codes, each code's width (in 1/1000 em) and how far
    letters reach above and below the baseline (in em)."""

    def __init__(self, font):
        subtype = str(font.get("/Subtype", ""))
        if subtype == "/Type3":
            raise Unsupported("it uses a Type3 font")
        descriptor = font.get("/FontDescriptor")
        self.two_byte = subtype == "/Type0"
        self.widths, self.ranges, self.missing = {}, [], 0.0
        base = str(font.get("/BaseFont", ""))[1:]
        base = base.split("+", 1)[1] if re.match(r"^[A-Z]{6}\+", base) else base
        if self.two_byte:
            encoding = font.get("/Encoding")
            name = str(encoding.get("/CMapName", "")) if isinstance(encoding, pikepdf.Stream) \
                else str(encoding)
            if name == "/Identity-V":
                raise Unsupported("it has vertical text")
            if name != "/Identity-H":
                raise Unsupported("a font uses an encoding that can't be edited")
            kids = font.get("/DescendantFonts")
            if not isinstance(kids, pikepdf.Array) or len(kids) != 1:
                raise Unsupported("a font is damaged")
            cid = kids[0]
            descriptor = cid.get("/FontDescriptor")
            self.missing = _num(cid.get("/DW"), 1000.0)
            self._read_w(cid.get("/W"))
        else:
            descriptor_missing = _num(descriptor.get("/MissingWidth")) if descriptor is not None else 0.0
            self.missing = descriptor_missing
            widths = font.get("/Widths")
            if isinstance(widths, pikepdf.Array):
                first = int(_num(font.get("/FirstChar")))
                for i, w in enumerate(widths):
                    self.widths[first + i] = _num(w)
            else:
                self._standard_widths(font, base)
        ascent = descent = 0.0
        if descriptor is not None:
            ascent, descent = _num(descriptor.get("/Ascent")), _num(descriptor.get("/Descent"))
        if not ascent or not descent:
            from reportlab.pdfbase import _fontdata

            std = _fontdata.ascent_descent.get(_STD14_ALIASES.get(base, base))
            if std and std[0]:
                ascent, descent = std
        self.ascent = min(max(ascent / 1000 if ascent else 0.8, 0.5), 1.2)
        self.descent = max(min(-abs(descent) / 1000 if descent else -0.2, 0.0), -0.5)

    def _read_w(self, w):
        if not isinstance(w, pikepdf.Array):
            return
        items = list(w)
        i = 0
        try:
            while i < len(items):
                first = int(items[i])
                nxt = items[i + 1]
                if isinstance(nxt, pikepdf.Array):
                    for k, value in enumerate(nxt):
                        self.widths[first + k] = _num(value)
                    i += 2
                else:
                    self.ranges.append((first, int(nxt), _num(items[i + 2])))
                    i += 3
        except (IndexError, TypeError, ValueError):
            raise Unsupported("a font is damaged") from None

    def _standard_widths(self, font, base):
        from reportlab.pdfbase import _fontdata

        name = _STD14_ALIASES.get(base, base)
        metrics = _fontdata.widthsByFontGlyph.get(name)
        if metrics is None:
            raise Unsupported("a font has no character widths")
        if name in ("Symbol", "ZapfDingbats"):
            names = list(_fontdata.encodings[name + "Encoding"])
        else:
            encoding = font.get("/Encoding")
            base_name = "StandardEncoding"
            differences = None
            if isinstance(encoding, pikepdf.Dictionary):
                base_name = str(encoding.get("/BaseEncoding", "/StandardEncoding"))[1:]
                differences = encoding.get("/Differences")
            elif encoding is not None:
                base_name = str(encoding)[1:]
            if base_name not in ("StandardEncoding", "WinAnsiEncoding", "MacRomanEncoding"):
                raise Unsupported("a font uses an encoding that can't be edited")
            names = list(_fontdata.encodings[base_name])
            if isinstance(differences, pikepdf.Array):
                code = 0
                for item in differences:
                    if isinstance(item, Name):
                        if 0 <= code < 256:
                            names[code] = str(item)[1:]
                        code += 1
                    else:
                        code = int(item)
        for code, glyph in enumerate(names):
            if glyph:
                self.widths[code] = metrics.get(glyph, 0)

    def codes(self, data):
        if not self.two_byte:
            return [(bytes([b]), b) for b in data]
        if len(data) % 2:
            raise Unsupported("a font's text is damaged")
        return [(data[i:i + 2], data[i] * 256 + data[i + 1]) for i in range(0, len(data), 2)]

    def width(self, code):
        w = self.widths.get(code)
        if w is not None:
            return w
        for first, last, value in self.ranges:
            if first <= code <= last:
                return value
        return self.missing


# -- pictures ---------------------------------------------------------------------------------

_LOSSLESS = {"/FlateDecode", "/LZWDecode", "/RunLengthDecode", "/ASCII85Decode",
             "/ASCIIHexDecode"}


def _filters(image):
    f = image.get("/Filter")
    if f is None:
        return []
    return [str(x) for x in f] if isinstance(f, pikepdf.Array) else [str(f)]


def _colorspace(cs, resources, depth=0):
    """(family, number of components, extra) of a colour space."""
    if depth > 4:
        raise Unsupported("a picture's colours can't be read")
    if isinstance(cs, Name):
        name = str(cs)
        known = {"/DeviceGray": ("gray", 1), "/G": ("gray", 1), "/CalGray": ("gray", 1),
                 "/DeviceRGB": ("rgb", 3), "/RGB": ("rgb", 3), "/DeviceCMYK": ("cmyk", 4),
                 "/CMYK": ("cmyk", 4)}
        if name in known:
            return known[name] + (None,)
        found = _resource(resources, "/ColorSpace", name)
        if found is None:
            raise Unsupported("a picture's colours can't be read")
        return _colorspace(found, resources, depth + 1)
    if isinstance(cs, pikepdf.Array) and len(cs):
        family = str(cs[0])
        if family in ("/CalGray",):
            return "gray", 1, None
        if family in ("/CalRGB",):
            return "rgb", 3, None
        if family == "/Lab":
            return "lab", 3, cs[1] if len(cs) > 1 else None
        if family == "/ICCBased":
            n = int(_num(cs[1].get("/N"), 0))
            return {1: "gray", 3: "rgb", 4: "cmyk"}.get(n, "other"), n, None
        if family in ("/Indexed", "/I"):
            return "indexed", 1, cs
        if family == "/Separation":
            return "tint", 1, None
        if family == "/DeviceN":
            return "tint", len(cs[1]), None
        if len(cs) == 1:
            return _colorspace(cs[0], resources, depth + 1)
    raise Unsupported("a picture's colours can't be read")


def _darkest(indexed, resources):
    """Index of the darkest colour in an /Indexed palette."""
    try:
        family, n, _ = _colorspace(indexed[1], resources)
        hival = int(indexed[2])
        lookup = indexed[3]
        table = lookup.read_bytes() if isinstance(lookup, pikepdf.Stream) else bytes(lookup)
    except (Unsupported, IndexError, TypeError, ValueError, pikepdf.PdfError):
        return 0
    best, best_light = 0, 2.0
    for i in range(hival + 1):
        entry = table[i * n:(i + 1) * n]
        if len(entry) < n:
            break
        v = [b / 255 for b in entry]
        if family == "gray":
            light = v[0]
        elif family == "rgb":
            light = 0.3 * v[0] + 0.59 * v[1] + 0.11 * v[2]
        elif family == "cmyk":
            light = 1 - min(1.0, 0.3 * v[0] + 0.59 * v[1] + 0.11 * v[2] + v[3])
        else:
            return 0
        if light < best_light:
            best, best_light = i, light
    return best


def _fill_samples(image, resources, purpose):
    """The sample values (one per component) that paint a pixel black in
    ``image`` (purpose "image"), opaque (an /SMask), painted (a /Mask) or
    not at all (a stencil mask, which draws in the current colour)."""
    bpc = int(_num(image.get("/BitsPerComponent"), 1 if image.get("/ImageMask") else 8))
    maxval = (1 << bpc) - 1
    if image.get("/ImageMask") is True or purpose in ("mask", "stencil"):
        targets, n, default = [1.0 if purpose == "stencil" else 0.0], 1, [0.0, 1.0]
    elif purpose == "smask":
        targets, n, default = [1.0], 1, [0.0, 1.0]
    else:
        family, n, extra = _colorspace(image.get("/ColorSpace"), resources)
        if family in ("gray", "rgb"):
            targets = [0.0] * n
        elif family in ("cmyk", "tint"):
            targets = [1.0] * n
        elif family == "lab":
            targets = [0.0, 0.0, 0.0]
        elif family == "indexed":
            targets = [float(_darkest(extra, resources))]
        else:
            raise Unsupported("a picture's colours can't be edited")
        if family == "indexed":
            default = [0.0, float(maxval)]
        elif family == "lab":
            rng = [(_num(v)) for v in extra.get("/Range", [-100, 100, -100, 100])] \
                if isinstance(extra, pikepdf.Dictionary) else [-100, 100, -100, 100]
            default = [0.0, 100.0] + rng
        else:
            default = [0.0, 1.0] * n
    decode = image.get("/Decode")
    decode = [_num(v) for v in decode] if isinstance(decode, pikepdf.Array) and \
        len(decode) >= 2 * n else default
    samples = []
    for i, target in enumerate(targets):
        lo, hi = decode[2 * i], decode[2 * i + 1]
        s = 0 if hi == lo else round((target - lo) / (hi - lo) * maxval)
        samples.append(min(max(s, 0), maxval))
    return samples, n, bpc


def _spans(matrix, width, height, areas):
    """{row: [(first, end) columns]} of the pixels of a ``width`` x
    ``height`` picture placed with ``matrix`` that any area touches."""
    inverse = _invert(matrix)
    spans = {}
    if inverse is None or width <= 0 or height <= 0:
        return spans
    for x0, y0, x1, y1 in areas:
        quad = []
        for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            u, v = _apply(inverse, x, y)
            quad.append((u * width, (1 - v) * height))
        quad = _clip_rect(quad, (0, 0, width, height))
        if len(quad) < 3 or _area(quad) < 1e-6:
            continue
        ys = [p[1] for p in quad]
        for row in range(max(0, math.floor(min(ys) + EPS)), min(height, math.ceil(max(ys) - EPS))):
            band = _clip(_clip(quad, 1, row, True), 1, row + 1, False)
            if len(band) < 2:
                continue
            xs = [p[0] for p in band]
            first = max(0, math.floor(min(xs) + EPS))
            end = min(width, math.ceil(max(xs) - EPS))
            if end > first:
                spans.setdefault(row, []).append((first, end))
    for row, found in spans.items():
        found.sort()
        merged = [list(found[0])]
        for first, end in found[1:]:
            if first <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([first, end])
        spans[row] = [tuple(m) for m in merged]
    return spans


def _covers_all(spans, width, height):
    return len(spans) == height and all(s == [(0, width)] for s in spans.values())


def _paint(data, spans, width, samples, bpc):
    """Set the pixels in ``spans`` of raw image ``data`` to ``samples``."""
    n = len(samples)
    pixel_bits = n * bpc
    stride = (width * pixel_bits + 7) // 8
    pixel = 0
    for s in samples:
        pixel = (pixel << bpc) | s
    data = bytearray(data)
    total = stride * 8
    for row, found in spans.items():
        start = row * stride
        value = int.from_bytes(data[start:start + stride], "big")
        for first, end in found:
            count = end - first
            pattern = pixel * (((1 << (count * pixel_bits)) - 1) // ((1 << pixel_bits) - 1))
            shift = total - end * pixel_bits
            mask = ((1 << (count * pixel_bits)) - 1) << shift
            value = (value & ~mask) | (pattern << shift)
        data[start:start + stride] = value.to_bytes(stride, "big")
    return bytes(data)


def _check_painted(data, spans, width, samples, bpc):
    """The pixels in ``spans`` hold ``samples`` (the picture was really edited)."""
    return _paint(data, spans, width, samples, bpc) == bytes(data)


def _predecode(data, filters):
    """Undo the text and zip encodings some files wrap around a JPEG."""
    import base64
    import binascii

    for name in filters:
        if name in ("/ASCII85Decode", "/A85"):
            body = re.sub(rb"\s+", b"", data)
            body = body[2:] if body.startswith(b"<~") else body
            data = base64.a85decode(body.split(b"~>")[0])
        elif name in ("/ASCIIHexDecode", "/AHx"):
            body = re.sub(rb"[^0-9A-Fa-f]", b"", data.split(b">")[0])
            data = binascii.unhexlify(body + b"0" * (len(body) % 2))
        elif name in ("/FlateDecode", "/Fl"):
            data = zlib.decompress(data)
        else:
            raise Unsupported("a picture is stored in a way that can't be edited")
    return data


class _Pictures:
    """Paints the covered parts of pictures black, in copies."""

    def __init__(self, pdf):
        self.pdf = pdf

    def edit(self, image, matrix, areas, resources):
        """None (untouched), "drop" (covered completely) or a new picture."""
        width = int(_num(image.get("/Width")))
        height = int(_num(image.get("/Height")))
        spans = _spans(matrix, width, height, areas)
        if not spans:
            return None
        if _covers_all(spans, width, height):
            return "drop"
        stencil = image.get("/ImageMask") is True
        new = self._painted(image, spans, width, height, resources, "stencil" if stencil else "image")
        for key, purpose in (("/SMask", "smask"), ("/Mask", "mask")):
            mask = image.get(key)
            if isinstance(mask, pikepdf.Stream):
                w, h = int(_num(mask.get("/Width"))), int(_num(mask.get("/Height")))
                mask_spans = _spans(matrix, w, h, areas)
                new[Name(key)] = self._painted(mask, mask_spans, w, h, resources, purpose) \
                    if mask_spans else mask
        return new

    def _painted(self, image, spans, width, height, resources, purpose):
        filters = _filters(image)
        if any(f in ("/JPXDecode",) for f in filters):
            raise Unsupported("it has a JPEG 2000 picture")
        if filters and filters[-1] in ("/DCTDecode", "/DCT"):
            return self._painted_jpeg(image, spans, width, height, resources, filters)
        if filters and filters[-1] in ("/CCITTFaxDecode", "/CCF", "/JBIG2Decode"):
            return self._painted_bilevel(image, spans, width, height, purpose)
        if not all(f in _LOSSLESS or f in ("/Fl", "/LZW", "/RL", "/A85", "/AHx") for f in filters):
            raise Unsupported("a picture is stored in a way that can't be edited")
        samples, n, bpc = _fill_samples(image, resources, purpose)
        if bpc not in (1, 2, 4, 8, 16):
            raise Unsupported("a picture is damaged")
        try:
            data = image.read_bytes(decode_level=pikepdf.StreamDecodeLevel.specialized)
        except pikepdf.PdfError:
            raise Unsupported("a picture is damaged") from None
        stride = (width * n * bpc + 7) // 8
        if len(data) < stride * height:
            raise Unsupported("a picture is damaged")
        data = _paint(data[:stride * height], spans, width, samples, bpc)
        new = self._copy(image, zlib.compress(data, 6), Name.FlateDecode)
        if not _check_painted(new.read_bytes(), spans, width, samples, bpc):
            raise Unsupported("a picture couldn't be blacked out")
        return new

    def _painted_jpeg(self, image, spans, width, height, resources, filters):
        from PIL import Image, JpegImagePlugin

        family, n, _ = _colorspace(image.get("/ColorSpace"), resources)
        if family not in ("gray", "rgb") or "/Decode" in image:
            raise Unsupported("a JPEG picture uses colours that can't be edited")
        try:
            picture = Image.open(io.BytesIO(_predecode(image.read_raw_bytes(), filters[:-1])))
            picture.load()
        except (OSError, ValueError, zlib.error, pikepdf.PdfError):
            raise Unsupported("a picture is damaged") from None
        if picture.size != (width, height) or picture.mode != ("L" if n == 1 else "RGB"):
            raise Unsupported("a JPEG picture uses colours that can't be edited")
        data = _paint(picture.tobytes(), spans, width, [0] * n, 8)
        painted = Image.frombytes(picture.mode, picture.size, data)
        options = {"qtables": picture.quantization} if getattr(picture, "quantization", None) \
            else {"quality": 90}
        sampling = JpegImagePlugin.get_sampling(picture)
        if sampling != -1 and n == 3:
            options["subsampling"] = sampling
        buf = io.BytesIO()
        painted.save(buf, "JPEG", **options)
        new = self._copy(image, buf.getvalue(), Name.DCTDecode)
        # JPEG is lossy: allow a little noise from the edges next to the black.
        check = Image.open(io.BytesIO(new.read_raw_bytes())).convert("L")
        dark = check.tobytes()
        worst = 0
        for row, found in spans.items():
            if 1 <= row < height - 1:
                for first, end in found:
                    if end - first > 4:
                        worst = max(worst, max(dark[row * width + first + 2:row * width + end - 2]))
        if worst > 96:
            raise Unsupported("a picture couldn't be blacked out")
        return new

    def _painted_bilevel(self, image, spans, width, height, purpose):
        """Fax (CCITT) and JBIG2 pictures: decoded through pikepdf, saved as
        plain black-and-white (1 = white, as the picture looked)."""
        if purpose != "image" or image.get("/ImageMask") is True:
            raise Unsupported("a picture is stored in a way that can't be edited")
        try:
            picture = pikepdf.PdfImage(image).as_pil_image()
        except Exception:  # noqa: BLE001 - pikepdf raises many kinds for what it can't decode
            raise Unsupported("a picture is stored in a way that can't be edited") from None
        if picture.mode != "1" or picture.size != (width, height):
            raise Unsupported("a picture is stored in a way that can't be edited")
        data = _paint(picture.tobytes(), spans, width, [0], 1)
        new = self._copy(image, zlib.compress(data, 6), Name.FlateDecode)
        new.ColorSpace = Name.DeviceGray
        new.BitsPerComponent = 1
        for key in ("/Decode", "/JBIG2Globals"):
            if key in new:
                del new[key]
        if not _check_painted(new.read_bytes(), spans, width, [0], 1):
            raise Unsupported("a picture couldn't be blacked out")
        return new

    def _copy(self, image, data, filter_name):
        new = pikepdf.Stream(self.pdf, b"")
        new.write(data, filter=filter_name)
        for key, value in image.items():
            if key not in ("/Length", "/Filter", "/DecodeParms", "/DP", "/F", "/Metadata",
                           "/Alternates", "/OPI", "/SMask", "/Mask"):
                new[Name(key)] = value
        mask = image.get("/Mask")
        if isinstance(mask, pikepdf.Array):
            new.Mask = mask
        new.Type = Name.XObject
        new.Subtype = Name.Image
        return self.pdf.make_indirect(new)


# -- content streams -------------------------------------------------------------------------

class _State:
    __slots__ = ("ctm", "font", "size", "tc", "tw", "tz", "tl", "ts", "lw")

    def __init__(self, ctm):
        self.ctm = ctm
        self.font = None
        self.size = 0.0
        self.tc = self.tw = self.tl = self.ts = 0.0
        self.tz = 100.0
        self.lw = 1.0

    def copy(self):
        new = _State(self.ctm)
        for key in self.__slots__:
            setattr(new, key, getattr(self, key))
        return new


def _resource(resources, category, name):
    if not isinstance(resources, pikepdf.Dictionary):
        return None
    group = resources.get(category)
    if not isinstance(group, pikepdf.Dictionary):
        return None
    return group.get(name)


def _op(operands, operator):
    return pikepdf.ContentStreamInstruction(operands, pikepdf.Operator(operator))


def _n(value):
    """A number for a content stream: short, but exact enough."""
    value = round(value, 4)
    return int(value) if value == int(value) else value


_PATH_OPS = {"m", "l", "c", "v", "y", "h", "re"}
_PAINT_OPS = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"}
_STROKES = {"S", "s", "B", "B*", "b", "b*"}
_TEXT_OPS = {"Tj", "TJ", "'", '"'}
_ALT_KEYS = ("/ActualText", "/Alt", "/E")


class Scrubber:
    """Removes what lies under ``areas`` (page space boxes) from one page."""

    def __init__(self, pdf, areas):
        self.pdf = pdf
        self.alt_text_dropped = False  # pdfium then reads other letters there: see _verify
        self.areas = [tuple(map(float, a)) for a in areas]
        self.fonts = {}
        self.pictures = _Pictures(pdf)
        self.active = []  # forms being edited, against loops
        self.changed = False

    # -- the page --
    def page(self, page):
        """Edit ``page`` (a pikepdf.Page) in place and draw the black boxes."""
        resources = self._page_resources(page)
        try:
            instructions = pikepdf.parse_content_stream(page)
        except pikepdf.PdfError:
            raise Unsupported("its content is damaged") from None
        out, resources, depth = self._run(instructions, resources, IDENTITY)
        body = pikepdf.unparse_content_stream(out)
        boxes = b"".join(b"%s %s %s %s re\n" % tuple(
            str(_n(v)).encode() for v in (a[0], a[1], a[2] - a[0], a[3] - a[1]))
            for a in self.areas)
        data = (b"q\n" + body + b"\n" + b"Q\n" * (depth + 1) + b"q 0 g 0 G\n" + boxes + b"f\nQ\n")
        stream = pikepdf.Stream(self.pdf, b"")
        stream.write(zlib.compress(data, 6), filter=Name.FlateDecode)
        page.obj.Contents = self.pdf.make_indirect(stream)
        page.obj.Resources = resources if resources is not None else pikepdf.Dictionary()
        # A thumbnail, an editing program's private copy (/PieceInfo) and
        # page metadata could all still show what was under the boxes.
        for key in ("/Thumb", "/PieceInfo", "/Metadata"):
            if key in page.obj:
                del page.obj[key]

    @staticmethod
    def _page_resources(page):
        node = page.obj
        for _ in range(64):
            if node is None:
                break
            if "/Resources" in node:
                return node.Resources
            node = node.get("/Parent")
        return pikepdf.Dictionary()

    # -- one content stream --
    def _run(self, instructions, resources, ctm, state=None):
        """(new instructions, resources to use with them, unclosed q's)."""
        out = []
        state = state.copy() if state is not None else _State(ctm)
        state.ctm = ctm
        stack = []
        tm = tlm = IDENTITY
        path = []        # [(instruction, [points in page space], starts subpath)]
        clip = None      # a W or W* waiting for the paint operator
        marked = []      # [index in out or None, touched]
        added = {}       # new XObject name -> object
        replaced = set()  # XObject names whose drawing was replaced or dropped
        for ins in instructions:
            if isinstance(ins, pikepdf.ContentStreamInlineImage):
                self._inline(ins, state, resources, out, added, marked)
                continue
            op = str(ins.operator)
            ops = ins.operands
            if op in _PATH_OPS:
                path.append((ins, self._path_points(op, ops, state.ctm), op in ("m", "re")))
                continue
            if op in ("W", "W*"):
                clip = ins
                continue
            if op in _PAINT_OPS:
                self._paint_path(path, clip, ins, op, state, out, marked)
                path, clip = [], None
                continue
            if path:  # an operator inside a path: keep the path as it was
                out.extend(p[0] for p in path)
                if clip is not None:
                    out.append(clip)
                path, clip = [], None
            if op == "q":
                stack.append(state.copy())
            elif op == "Q":
                if stack:
                    state = stack.pop()
            elif op == "cm" and len(ops) == 6:
                state.ctm = _mul(tuple(_num(v) for v in ops), state.ctm)
            elif op == "w" and ops:
                state.lw = _num(ops[0], 1.0)
            elif op == "gs" and ops:
                self._extgstate(_resource(resources, "/ExtGState", ops[0]), state)
            elif op == "BT":
                tm = tlm = IDENTITY
            elif op == "Tf" and len(ops) == 2:
                state.font = _resource(resources, "/Font", ops[0])
                if state.font is None:
                    state.font = ops[0]  # unknown: fails only if text is shown with it
                state.size = _num(ops[1])
            elif op == "Tc" and ops:
                state.tc = _num(ops[0])
            elif op == "Tw" and ops:
                state.tw = _num(ops[0])
            elif op == "Tz" and ops:
                state.tz = _num(ops[0], 100.0)
            elif op == "TL" and ops:
                state.tl = _num(ops[0])
            elif op == "Ts" and ops:
                state.ts = _num(ops[0])
            elif op in ("Td", "TD") and len(ops) == 2:
                tx, ty = _num(ops[0]), _num(ops[1])
                if op == "TD":
                    state.tl = -ty
                tm = tlm = _mul((1, 0, 0, 1, tx, ty), tlm)
            elif op == "Tm" and len(ops) == 6:
                tm = tlm = tuple(_num(v) for v in ops)
            elif op == "T*":
                tm = tlm = _mul((1, 0, 0, 1, 0, -state.tl), tlm)
            elif op in _TEXT_OPS:
                if op in ("'", '"'):
                    if op == '"' and len(ops) == 3:
                        state.tw, state.tc = _num(ops[0]), _num(ops[1])
                    tm = tlm = _mul((1, 0, 0, 1, 0, -state.tl), tlm)
                tm = self._text(ins, op, ops, state, tm, out, marked)
                continue
            elif op in ("BDC", "BMC"):
                out.append(ins)
                marked.append([len(out) - 1 if op == "BDC" else None, False])
                continue
            elif op == "EMC":
                if marked:
                    index, touched = marked.pop()
                    if touched and index is not None:
                        out[index] = self._without_alt_text(out[index], resources)
            elif op == "Do" and ops:
                if self._do(ops[0], state, resources, out, added, marked):
                    replaced.add(str(ops[0]))
                    continue
            out.append(ins)
        if path:
            out.extend(p[0] for p in path)
        for index, touched in marked:
            if touched and index is not None:
                out[index] = self._without_alt_text(out[index], resources)
        # Pictures and forms no longer drawn must leave the resources too,
        # or the originals would still sit in the file.
        used = {str(i.operands[0]) for i in out if isinstance(i, pikepdf.ContentStreamInstruction)
                and str(i.operator) == "Do" and i.operands}
        unused = replaced - used
        if added or unused:
            resources = self._with_xobjects(resources, added, unused)
        return out, resources, len(stack)

    def _touch(self, marked):
        self.changed = True
        for entry in marked:
            entry[1] = True

    def _without_alt_text(self, ins, resources):
        """A BDC whose words were partly removed: drop the replacement
        text it carries (/ActualText and the like repeat those words)."""
        tag, props = (list(ins.operands) + [None, None])[:2]
        if isinstance(props, Name):
            found = _resource(resources, "/Properties", props)
            if not isinstance(found, pikepdf.Dictionary) or not any(k in found for k in _ALT_KEYS):
                return ins
            props = found
        if not isinstance(props, pikepdf.Dictionary) or not any(k in props for k in _ALT_KEYS):
            return ins
        clean = pikepdf.Dictionary({k: v for k, v in props.items() if k not in _ALT_KEYS})
        self.alt_text_dropped = True
        return _op([tag, clean], "BDC")

    def _extgstate(self, gstate, state):
        if not isinstance(gstate, pikepdf.Dictionary):
            return
        if "/LW" in gstate:
            state.lw = _num(gstate.LW, state.lw)
        font = gstate.get("/Font")
        if isinstance(font, pikepdf.Array) and len(font) == 2:
            state.font, state.size = font[0], _num(font[1])

    @staticmethod
    def _with_xobjects(resources, added, unused=()):
        new = pikepdf.Dictionary({k: v for k, v in resources.items()}) \
            if isinstance(resources, pikepdf.Dictionary) else pikepdf.Dictionary()
        old = new.get("/XObject")
        xobjects = pikepdf.Dictionary({k: v for k, v in old.items()}) \
            if isinstance(old, pikepdf.Dictionary) else pikepdf.Dictionary()
        for name in unused:
            if name in xobjects:
                del xobjects[name]
        for name, obj in added.items():
            xobjects[name] = obj
        new.XObject = xobjects
        return new

    def _new_name(self, resources, added):
        existing = resources.get("/XObject") if isinstance(resources, pikepdf.Dictionary) else None
        i = len(added) + 1
        while True:
            name = f"/ZRed{i}"
            if name not in added and not (isinstance(existing, pikepdf.Dictionary) and name in existing):
                return name
            i += 1

    # -- drawings --
    @staticmethod
    def _path_points(op, ops, ctm):
        values = [_num(v) for v in ops]
        if op == "re" and len(values) == 4:
            x, y, w, h = values
            pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        else:
            pts = list(zip(values[0::2], values[1::2]))
        return [_apply(ctm, x, y) for x, y in pts]

    def _paint_path(self, path, clip, paint, op, state, out, marked):
        """Emit a path, leaving out subpaths that lie completely under a box."""
        if op == "n" or not path:
            out.extend(p[0] for p in path)
            if clip is not None:
                out.append(clip)
            out.append(paint)
            return
        subpaths = []
        for item in path:
            if item[2] or not subpaths:
                subpaths.append([])
            subpaths[-1].append(item)
        grow = 0.0
        if op in _STROKES:
            a, b, c, d = state.ctm[:4]
            grow = state.lw / 2 * max(math.hypot(a, b), math.hypot(c, d))
        keep, dropped = [], False
        for sub in subpaths:
            points = [p for item in sub for p in item[1]]
            if points:
                x0, y0, x1, y1 = _bbox(points)
                box = (x0 - grow, y0 - grow, x1 + grow, y1 + grow)
                if any(_inside(box, area) for area in self.areas):
                    dropped = True
                    continue
            keep.append(sub)
        if not dropped:
            out.extend(p[0] for p in path)
            if clip is not None:
                out.append(clip)
            out.append(paint)
            return
        self._touch(marked)
        if keep:
            out.extend(item[0] for sub in keep for item in sub)
            out.append(paint)
        if clip is not None:  # the clip still uses the whole path
            out.extend(p[0] for p in path)
            out.append(clip)
            out.append(_op([], "n"))

    # -- text --
    def _font(self, font):
        if not isinstance(font, pikepdf.Dictionary):
            raise Unsupported("a font is missing")
        key = font.objgen if font.objgen != (0, 0) else None
        if key is not None and key in self.fonts:
            return self.fonts[key]
        info = _Font(font)
        if key is not None:
            self.fonts[key] = info
        return info

    def _text(self, ins, op, ops, state, tm, out, marked):
        """Show text, leaving out letters under a box. Returns the new text matrix."""
        if op == "TJ":
            items = list(ops[0]) if ops and isinstance(ops[0], pikepdf.Array) else []
        else:
            items = [ops[-1]] if ops else []
        font = self._font(state.font)
        size, th = state.size, state.tz / 100
        if size == 0 or th == 0:
            raise Unsupported("it has text of size zero")
        trm_base = _mul(tm, state.ctm)
        x = 0.0
        result, buf, pending = [], b"", 0.0
        dropped = False
        for item in items:
            if isinstance(item, pikepdf.String):
                for raw, code in font.codes(bytes(item)):
                    w0 = font.width(code) / 1000
                    space = state.tw if (not font.two_byte and code == 32) else 0.0
                    advance = (w0 * size + state.tc + space) * th
                    glyph = (size * th, 0, 0, size, x, state.ts)
                    m = _mul(glyph, trm_base)
                    gw = w0 if abs(w0) > 0.05 else 0.05
                    inset = 0.1 * gw
                    quad = [_apply(m, gx, gy) for gx, gy in
                            ((inset, font.descent), (gw - inset, font.descent),
                             (gw - inset, font.ascent), (inset, font.ascent))]
                    if _hits(quad, self.areas):
                        dropped = True
                        if buf:
                            result.append(buf)
                            buf = b""
                        pending -= advance * 1000 / (size * th)
                    else:
                        if pending:
                            if buf:
                                result.append(buf)
                                buf = b""
                            result.append(pending)
                            pending = 0.0
                        buf += raw
                    x += advance
            else:
                n = _num(item)
                x -= n / 1000 * size * th
                if buf:
                    result.append(buf)
                    buf = b""
                pending += n
        if buf:
            result.append(buf)
        if pending:
            result.append(pending)
        if dropped:
            self._touch(marked)
            array = pikepdf.Array([pikepdf.String(r) if isinstance(r, bytes) else _n(r)
                                   for r in result])
            if op == '"' and len(ops) == 3:
                out.extend([_op([ops[0]], "Tw"), _op([ops[1]], "Tc")])
            if op in ("'", '"'):
                out.append(_op([], "T*"))
            out.append(_op([array], "TJ"))
        else:
            out.append(ins)
        return _mul((1, 0, 0, 1, x, 0), tm)

    # -- pictures and forms --
    def _do(self, name, state, resources, out, added, marked):
        """Handle ``/name Do``; True when it was replaced or dropped."""
        xobject = _resource(resources, "/XObject", name)
        if not isinstance(xobject, pikepdf.Stream):
            return False
        subtype = xobject.get("/Subtype")
        if subtype == Name.Image:
            new = self.pictures.edit(xobject, state.ctm, self.areas, resources)
        elif subtype == Name.Form:
            new = self._form(xobject, state, resources)
        else:
            return False
        if new is None:
            return False
        self._touch(marked)
        if new != "drop":
            fresh = self._new_name(resources, added)
            added[fresh] = new
            out.append(_op([Name(fresh)], "Do"))
        return True

    def _inline(self, ins, state, resources, out, added, marked):
        try:
            image = ins.iimage
            raw = image.read_raw_bytes()
            info = image.obj
            stream = pikepdf.Stream(self.pdf, b"")
            stream.write(raw, filter=info.get("/Filter"), decode_parms=info.get("/DecodeParms"))
            for key, value in info.items():
                if key not in ("/Filter", "/DecodeParms", "/Length"):
                    stream[Name(key)] = value
        except (pikepdf.PdfError, AttributeError, ValueError, TypeError):
            raise Unsupported("a picture is damaged") from None
        new = self.pictures.edit(stream, state.ctm, self.areas, resources)
        if new is None:
            out.append(ins)
            return
        self._touch(marked)
        if new != "drop":
            fresh = self._new_name(resources, added)
            added[fresh] = new
            out.append(_op([Name(fresh)], "Do"))

    def _form(self, form, state, resources):
        """A copy of a form XObject without what's under the boxes, or None."""
        matrix = form.get("/Matrix")
        matrix = tuple(_num(v) for v in matrix) if isinstance(matrix, pikepdf.Array) and \
            len(matrix) == 6 else IDENTITY
        ctm = _mul(matrix, state.ctm)
        bbox = form.get("/BBox")
        if isinstance(bbox, pikepdf.Array) and len(bbox) == 4:
            x0, y0, x1, y1 = (_num(v) for v in bbox)
            box = _bbox([_apply(ctm, x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))])
            if not any(_overlaps(box, area) for area in self.areas):
                return None
        if len(self.active) >= MAX_DEPTH or form.objgen in self.active:
            raise Unsupported("its drawings refer to themselves")
        own = form.get("/Resources")
        form_resources = own if isinstance(own, pikepdf.Dictionary) else resources
        try:
            instructions = pikepdf.parse_content_stream(form)
        except pikepdf.PdfError:
            raise Unsupported("its content is damaged") from None
        before = self.changed
        self.changed = False
        self.active.append(form.objgen)
        try:
            out, new_resources, _ = self._run(instructions, form_resources, ctm, state)
        finally:
            self.active.pop()
        changed, self.changed = self.changed, before or self.changed
        if not changed:
            return None
        new = pikepdf.Stream(self.pdf, b"")
        new.write(zlib.compress(pikepdf.unparse_content_stream(out), 6), filter=Name.FlateDecode)
        for key, value in form.items():
            if key not in ("/Length", "/Filter", "/DecodeParms", "/Metadata", "/PieceInfo",
                           "/LastModified", "/Resources"):
                new[Name(key)] = value
        if isinstance(new_resources, pikepdf.Dictionary):
            new.Resources = new_resources
        return self.pdf.make_indirect(new)


# -- the document --------------------------------------------------------------------------------

def remove(path, password, areas, regexes=()):
    """Remove what lies under ``areas`` ({0-based page: [(x0, y0, x1, y1)]}
    in PDF points) and draw the black boxes. Returns (PDF bytes, {page:
    reason} for pages this couldn't do, {page: text that was removed})."""
    try:
        pdf = pikepdf.open(str(path), password=password or "")
    except pikepdf.PasswordError:
        pdf = pikepdf.open(str(path), password="")
    failed, loose = {}, set()
    with pdf:
        for n, (number, boxes) in enumerate(sorted(areas.items())):
            progress.report(n, len(areas), "removing what's under the boxes")
            scrubber = Scrubber(pdf, boxes)
            try:
                scrubber.page(pdf.pages[number])
            except Unsupported as exc:
                failed[number] = str(exc)
            except (pikepdf.PdfError, ValueError, TypeError, KeyError, IndexError,
                    AttributeError, OverflowError, RecursionError, ZeroDivisionError):
                failed[number] = "its content couldn't be read"
            if scrubber.alt_text_dropped:
                loose.add(number)
        buf = io.BytesIO()
        pdf.save(buf)
    data = buf.getvalue()
    removed = {}
    checks = {n: b for n, b in areas.items() if n not in failed}
    for number, (reason, text) in _verify(path, password, data, checks, regexes, loose).items():
        if reason:
            failed[number] = reason
        removed[number] = text
    return data, failed, removed


def _chars(textpage):
    """[(character, (x0, y0, x1, y1))] of a pdfium text page, with None
    boxes for spaces and line breaks."""
    import pypdfium2.raw as pdfium_c

    chars = []
    for i in range(textpage.count_chars()):
        ch = chr(pdfium_c.FPDFText_GetUnicode(textpage.raw, i) or 32)
        if ch.isspace() or ch == "\x00" or pdfium_c.FPDFText_IsGenerated(textpage.raw, i) == 1:
            chars.append((" ", None))
        else:
            chars.append((ch, textpage.get_charbox(i)))
    return chars


def _center_in(box, areas, shrink=0.5):
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return any(a[0] + shrink < cx < a[2] - shrink and a[1] + shrink < cy < a[3] - shrink
               for a in areas)


def _verify(path, password, data, areas, regexes, loose=()):
    """Check the edited pages with pdfium, which reads text much like other
    programs do: {page: (why it failed or None, the text that was under
    the boxes)}. Pages in ``loose`` lost replacement text (/ActualText),
    which changes where pdfium puts letters, so they skip the check that
    nothing moved."""
    import pypdfium2 as pdfium

    results = {}
    if not areas:
        return results
    with core.PDFIUM_LOCK:
        old_doc = pdfium.PdfDocument(str(path), password=password)
        new_doc = pdfium.PdfDocument(data)
        try:
            for number, boxes in sorted(areas.items()):
                old_text, new_text = old_doc[number].get_textpage(), new_doc[number].get_textpage()
                old, new = _chars(old_text), _chars(new_text)
                full = new_text.get_text_range()
                old_text.close()
                new_text.close()
                gone = [box is not None and _center_in(box, boxes, 0) for _, box in old]
                removed = "".join(ch if hit else " " for (ch, _), hit in zip(old, gone))
                old = [c for c, hit in zip(old, gone) if c[1] is not None and not hit]
                new = [c for c in new if c[1] is not None]
                reason = None
                if any(_center_in(box, boxes) for _, box in new):
                    reason = "some text under a box couldn't be removed"
                elif any(r.search(full) for r in regexes):
                    reason = "the words you searched for could still be found"
                elif number not in loose and not _same_places(old, new, sum(gone)):
                    reason = "the rest of the text would have moved"
                results[number] = (reason, " ".join(removed.split()))
        finally:
            old_doc.close()
            new_doc.close()
    return results


def _same_places(old, new, removed=0):
    """Every letter left on the page is one that was there, in the same
    place. pdfium rounds character widths to whole units, so letters after
    removed ones may seem to move by up to a unit per removed letter
    (other programs place them exactly)."""
    where = {}
    for ch, box in old:
        where.setdefault(ch, []).append(box)
    for ch, box in new:
        size = min(max(box[3] - box[1], 1.0) / 0.6, 100.0)
        slack_x = 0.75 + removed * size / 1000
        if not any(abs(box[0] - o[0]) <= slack_x and abs(box[2] - o[2]) <= slack_x
                   and abs(box[1] - o[1]) <= 0.75 and abs(box[3] - o[3]) <= 0.75
                   for o in where.get(ch, ())):
            return False
    return True
