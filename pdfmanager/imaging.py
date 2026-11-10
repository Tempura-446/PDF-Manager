"""Image and colour rewriting: compression levels and colour conversion.

Both work on a ``PdfWriter`` in place:

- ``downsample_images`` re-encodes images whose effective resolution (pixels
  per inch *as drawn on the page*) is above a target, and keeps the result
  only when it is smaller than the original.
- ``convert_colors`` converts page content, form XObjects and images to
  grayscale, RGB or CMYK, optionally through an ICC profile.
"""

import io
import math
import zlib

from PIL import Image
from pypdf.generic import (
    ArrayObject,
    ContentStream,
    DictionaryObject,
    FloatObject,
    IndirectObject,
    NameObject,
    NumberObject,
    StreamObject,
    TextStringObject,
)

from pdfmanager import progress

# Compression presets: (max image DPI, JPEG quality). None = leave images alone.
COMPRESSION_LEVELS = {
    "low": (None, None),     # lossless only
    "medium": (150, 75),     # good for reading on screen and office printing
    "high": (96, 50),        # smallest files, for email/web
}
LEVEL_ALIASES = {"large": "high", "max": "high", "small": "low", "lossless": "low"}

COLOR_TARGETS = ("gray", "rgb", "cmyk")

_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def normalize_level(level):
    level = (level or "low").lower()
    level = LEVEL_ALIASES.get(level, level)
    if level not in COMPRESSION_LEVELS:
        raise ValueError(f"compression level must be one of low, medium, high (got {level!r})")
    return level


# --------------------------------------------------------------------------
# Where are images drawn, and how big?
# --------------------------------------------------------------------------

def _mul(m, n):
    """Multiply two PDF matrices [a b c d e f] (m applied first)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D,
            e * A + f * C + E, e * B + f * D + F)


def _xobjects(resources):
    if resources is None:
        return {}
    resources = resources.get_object()
    xobjs = resources.get("/XObject")
    return xobjs.get_object() if xobjs is not None else {}


def _walk_images(content_owner, resources, pdf, ctm, sizes, depth=0, uses=None):
    """Record the largest drawn size (in points) of every image XObject,
    keyed by object number, and in ``uses`` how many times each is drawn.
    Recurses into form XObjects."""
    if depth > 10:
        return
    try:
        contents = content_owner.get_contents() if hasattr(content_owner, "get_contents") \
            else content_owner
        if contents is None:
            return
        ops = ContentStream(contents, pdf).operations
    except Exception:
        return
    xobjs = _xobjects(resources)
    stack = []
    for operands, op in ops:
        if op == b"q":
            stack.append(ctm)
        elif op == b"Q" and stack:
            ctm = stack.pop()
        elif op == b"cm" and len(operands) == 6:
            ctm = _mul(tuple(float(x) for x in operands), ctm)
        elif op == b"Do" and operands:
            ref = xobjs.get(operands[0])
            if ref is None:
                continue
            obj = ref.get_object()
            subtype = obj.get("/Subtype")
            if subtype == "/Image":
                a, b, c, d = ctm[:4]
                w, h = math.hypot(a, b), math.hypot(c, d)
                key = getattr(ref, "idnum", id(obj))
                old = sizes.get(key, (0, 0))
                sizes[key] = (max(old[0], w), max(old[1], h))
                if uses is not None:
                    uses[key] = uses.get(key, 0) + 1
            elif subtype == "/Form":
                matrix = tuple(float(x) for x in obj.get("/Matrix", _IDENTITY))
                _walk_images(obj, obj.get("/Resources", resources), pdf,
                             _mul(matrix, ctm), sizes, depth + 1, uses)


def image_display_sizes(page, pdf, uses=None):
    sizes = {}
    _walk_images(page, page.get("/Resources"), pdf, _IDENTITY, sizes, uses=uses)
    return sizes


def _page_images(page):
    """Yield (object number, image stream) for every image XObject on the
    page, including inside form XObjects."""
    seen = set()

    def visit(resources, depth):
        for ref in _xobjects(resources).values():
            obj = ref.get_object()
            key = getattr(ref, "idnum", id(obj))
            if key in seen:
                continue
            seen.add(key)
            if obj.get("/Subtype") == "/Image":
                yield key, obj
            elif obj.get("/Subtype") == "/Form" and depth < 10:
                yield from visit(obj.get("/Resources"), depth + 1)

    yield from visit(page.get("/Resources"), 0)


# --------------------------------------------------------------------------
# Image re-encoding
# --------------------------------------------------------------------------

_RAW_MODES = {1: "L", 3: "RGB", 4: "CMYK"}
_SPACE_COMPONENTS = {"/DeviceGray": 1, "/CalGray": 1, "/DeviceRGB": 3, "/CalRGB": 3, "/DeviceCMYK": 4}


def _plain_components(obj):
    """Components per pixel of an image whose samples can be read as they
    are (8 bits, a device, calibrated or ICC colour space, no /Decode), or
    None."""
    if obj.get("/BitsPerComponent", 8) != 8 or "/Decode" in obj:
        return None
    space = obj.get("/ColorSpace")
    space = space.get_object() if space is not None else None
    if isinstance(space, ArrayObject) and space:
        if space[0] == "/ICCBased":
            n = int(space[1].get_object().get("/N", 0))
            return n if n in _RAW_MODES else None
        space = space[0] if space[0] in ("/CalGray", "/CalRGB") else None
    return _SPACE_COMPONENTS.get(space)


def _decode(obj, size=None):
    """The image as Pillow sees it. Plain 8-bit samples and JPEGs are read
    directly; pypdf's general reader also re-encodes the picture as a PNG,
    which takes longer than everything else compress does. A JPEG that is
    going to be shrunk to ``size`` is decoded at the nearest scale above it
    (1/2, 1/4 or 1/8), which the decoder does far faster."""
    filters = [f for f in _filters(obj) if f is not None]
    n = _plain_components(obj)
    if n is not None:
        if filters == ["/DCTDecode"]:
            image = Image.open(io.BytesIO(obj._data))
            # Adobe CMYK JPEGs are stored inverted; leave those to pypdf.
            if image.mode == _RAW_MODES[n] and image.mode != "CMYK":
                if size:
                    image.draft(image.mode, size)
                image.load()
                return image
        elif all(f in ("/FlateDecode", "/Fl") for f in filters):
            width, height = int(obj["/Width"]), int(obj["/Height"])
            data = obj.get_data()
            if len(data) >= width * height * n:
                return Image.frombytes(_RAW_MODES[n], (width, height), data[:width * height * n])
    from pypdf.generic._image_xobject import _xobj_to_image

    _, _, image = _xobj_to_image(obj)
    return image


def _filters(obj):
    filters = obj.get("/Filter")
    return [filters] if not isinstance(filters, (list, ArrayObject)) else list(filters)


def _keyed(obj):
    """Transparent by colour key: the /Mask ranges name the original
    samples, so re-encoding the picture would paint the key colour."""
    mask = obj.get("/Mask")
    return isinstance(mask.get_object() if mask is not None else None, ArrayObject)


def _bilevel(obj):
    """Black-and-white already: stencil masks, 1-bit images, JBIG2/CCITT scans."""
    if obj.get("/ImageMask") or obj.get("/BitsPerComponent", 8) == 1:
        return True
    return any(f in ("/JBIG2Decode", "/CCITTFaxDecode") for f in _filters(obj))


def _skip(obj):
    # Black-and-white pictures compress best as they are.
    return _bilevel(obj) or _keyed(obj)


def _flatten(image):
    """Drop alpha (it lives in the PDF's /SMask) and normalise the mode."""
    if image.mode in ("RGBA", "LA", "PA"):
        image = image.convert(image.mode[:-1] if image.mode != "PA" else "RGB")
    if image.mode == "CMYK":
        return image
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    return image


def _encode(image, jpeg_quality):
    """Return (data, filter, colour space name, extra dict entries)."""
    spaces = {"L": "/DeviceGray", "RGB": "/DeviceRGB", "CMYK": "/DeviceCMYK"}
    extra = {}
    if jpeg_quality:
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=jpeg_quality, optimize=True)
        data, filt = buf.getvalue(), "/DCTDecode"
        if image.mode == "CMYK":
            # Pillow writes Adobe-style inverted CMYK JPEGs.
            extra["/Decode"] = ArrayObject([NumberObject(v) for v in (1, 0) * 4])
    else:
        data, filt = zlib.compress(image.tobytes(), 9), "/FlateDecode"
    return data, filt, spaces[image.mode], extra


def _store(obj, image, data, filt, colorspace, extra):
    for key in ("/DecodeParms", "/Decode", "/Intent"):
        if key in obj:
            del obj[key]
    obj[NameObject("/Filter")] = NameObject(filt)
    obj[NameObject("/ColorSpace")] = NameObject(colorspace)
    obj[NameObject("/BitsPerComponent")] = NumberObject(8)
    obj[NameObject("/Width")] = NumberObject(image.width)
    obj[NameObject("/Height")] = NumberObject(image.height)
    for key, value in extra.items():
        obj[NameObject(key)] = value
    obj._data = data
    obj[NameObject("/Length")] = NumberObject(len(data))
    if hasattr(obj, "decoded_self"):
        obj.decoded_self = None


def _resize_smask(obj, size):
    smask = obj.get("/SMask")
    if smask is None:
        return
    smask = smask.get_object()
    try:
        mask = _decode(smask).convert("L")
    except Exception:
        return  # a mask of a different size is allowed by the PDF spec
    if mask.size != size:
        mask = mask.resize(size, Image.LANCZOS)
        _store(smask, mask, zlib.compress(mask.tobytes(), 9), "/FlateDecode", "/DeviceGray", {})


# --------------------------------------------------------------------------
# Lossless clean-up of streams
# --------------------------------------------------------------------------

_ASCII_FILTERS = ("/ASCII85Decode", "/A85", "/ASCIIHexDecode", "/AHx")
_WHITESPACE = b" \t\r\n\x0c\x00"
_A85_DIGITS = bytes((c - 33) % 256 for c in range(256))
_A85_CHARS = bytes(range(33, 118))


def ascii85_decode(data):
    """Decode ASCII85 at C speed (the standard library's decoder loops in
    Python, a second or more per megabyte). Each group of five digits is
    laid in a 5-byte field of five big integers, one per digit position;
    multiplying them by powers of 85 and adding never carries from one
    field into the next, since a group is at most 85**5 - 1 < 2**40."""
    data = bytes(data).translate(None, _WHITESPACE)
    if data.startswith(b"<~"):
        data = data[2:]
    end = data.find(b"~>")
    if end >= 0:
        data = data[:end]
    data = data.replace(b"z", b"!!!!!")
    if not data:
        return b""
    if data.translate(None, _A85_CHARS):
        raise ValueError("not ASCII85 data")
    tail = -len(data) % 5  # a short last group is padded with the highest digit
    data += b"u" * tail
    groups = len(data) // 5
    digits = data.translate(_A85_DIGITS)
    total = 0
    for k in range(5):
        field = bytearray(5 * groups)
        field[4::5] = digits[k::5]
        total += int.from_bytes(field, "big") * 85 ** (4 - k)
    fields = total.to_bytes(5 * groups, "big")
    if any(fields[0::5]):
        raise ValueError("ASCII85 group out of range")
    out = bytearray(4 * groups)
    for k in range(4):
        out[k::4] = fields[k + 1::5]
    return bytes(out[:len(out) - tail])


def _use_fast_ascii85():
    """Have pypdf decode ASCII85 streams with ``ascii85_decode``: every tool
    that reads pictures (colours, extracting pictures, ...) gets it. Data the
    fast decoder can't read goes to pypdf's own, for its error handling."""
    from pypdf import filters

    original = filters.ASCII85Decode.decode
    if getattr(original, "_pdfm_fast", False):
        return

    def decode(data, decode_parms=None, **kwargs):
        try:
            return ascii85_decode(data.encode("latin-1") if isinstance(data, str) else data)
        except (ValueError, UnicodeEncodeError):
            return original(data, decode_parms, **kwargs)

    decode._pdfm_fast = True
    filters.ASCII85Decode.decode = staticmethod(decode)


_use_fast_ascii85()


def _ascii_decode(name, data):
    if name in ("/ASCIIHexDecode", "/AHx"):
        data = bytes(data).translate(None, _WHITESPACE)
        end = data.find(b">")
        data = data[:end] if end >= 0 else data
        return bytes.fromhex((data + b"0" * (len(data) % 2)).decode("ascii"))
    return ascii85_decode(data)


def unwrap_ascii(writer):
    """Remove the ASCII85/ASCIIHex layer from every stream. It only makes
    binary data safe to send as text, at 25% (ASCII85) or 100% (hex) more
    bytes, and decoding it is slow; the layers under it stay as they are.
    Returns the number of streams changed."""
    changed = 0
    for obj in writer._objects:
        if not isinstance(obj, StreamObject) or "/Filter" not in obj:
            continue
        filters = _filters(obj)
        if filters[0] not in _ASCII_FILTERS:
            continue
        try:
            data = _ascii_decode(filters[0], obj._data)
        except ValueError:
            continue  # damaged: leave it for the viewer to cope with
        rest = filters[1:]
        params = obj.get("/DecodeParms")
        params = params.get_object() if params is not None else None
        rest_params = list(params[1:]) if isinstance(params, ArrayObject) else []
        for key in ("/Filter", "/DecodeParms"):
            if key in obj:
                del obj[key]
        if rest:
            obj[NameObject("/Filter")] = rest[0] if len(rest) == 1 else ArrayObject(rest)
            if any(p is not None and p.get_object() is not None for p in rest_params):
                obj[NameObject("/DecodeParms")] = (
                    rest_params[0] if len(rest) == 1 else ArrayObject(rest_params))
        obj._data = data
        obj[NameObject("/Length")] = NumberObject(len(data))
        if hasattr(obj, "decoded_self"):
            obj.decoded_self = None
        changed += 1
    return changed


def deflate_plain_streams(writer, min_size=64):
    """Flate-compress streams stored with no compression at all (fonts,
    form XObjects, pictures), keeping the result only when it is smaller.
    XMP metadata stays readable as text, as PDF/A asks."""
    changed = 0
    for i, obj in enumerate(writer._objects):
        if (not isinstance(obj, StreamObject) or "/Filter" in obj or "/DecodeParms" in obj
                or obj.get("/Type") == "/Metadata" or len(obj._data) < min_size):
            continue
        packed = obj.flate_encode()
        if len(packed._data) >= len(obj._data):
            continue
        packed.indirect_reference = IndirectObject(i + 1, 0, writer)
        writer._objects[i] = packed
        changed += 1
    return changed


# Scanned pages (see scanlayers.py), by compression level: the background's
# resolution and JPEG quality, then the ink colour picture's.
SCAN_LAYERS = {"medium": (150, 50, 37, 40), "high": (100, 40, 25, 35)}
SCAN_COVERS = 0.80     # of its page, for a picture to be the page's scan
SCAN_MIN_DPI = 150     # below this there is no sharpness to keep
SCAN_ALLOWANCE = 1.10  # layers may be this much larger than the usual result
# The engine holds a page in memory several times over (about 25 bytes a
# pixel): a larger scan than this, as it would be worked on, is left to
# the usual way. 40 million is an A2 sheet at 300 dpi, about 1 GB.
SCAN_MAX_PIXELS = 40_000_000


def _scan_dpi(obj, drawn, page, uses):
    """The resolution a picture is shown at when it is its page's scan (it
    fills the page, is drawn once, and is a plain grey or colour picture),
    else None."""
    if uses != 1 or _skip(obj) or "/SMask" in obj or "/Mask" in obj:
        return None
    if obj.get("/BitsPerComponent", 8) != 8:
        return None
    width, height = int(obj.get("/Width", 0)), int(obj.get("/Height", 0))
    drawn_w, drawn_h = drawn
    if min(width, height, drawn_w, drawn_h) <= 0:
        return None
    had = "/CropBox" in page
    box = page.cropbox
    area = abs(float(box.width) * float(box.height))
    if not had and "/CropBox" in page:  # pypdf writes down the box it worked out
        del page["/CropBox"]
    if drawn_w * drawn_h < SCAN_COVERS * area:
        return None
    dpi = max(width / (drawn_w / 72), height / (drawn_h / 72))
    if dpi < SCAN_MIN_DPI:
        return None
    from pdfmanager import scanlayers

    worked = min(1.0, scanlayers.MASK_DPI / dpi)
    if width * height * worked * worked > SCAN_MAX_PIXELS:
        return None
    return dpi


def _jpeg(image, quality):
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _scan_layers(image, dpi, settings):
    """The layers of a scan, encoded: (layers, mask data, mask is Group 4,
    background JPEG, ink JPEG), or None when the page isn't a text scan."""
    from pdfmanager import scanlayers

    background_dpi, background_quality, ink_dpi, ink_quality = settings
    layers = scanlayers.split(image, dpi, background_dpi, ink_dpi)
    if layers is None:
        return None
    mask = scanlayers.group4(layers.mask)
    fax = mask is not None
    if not fax:
        mask = zlib.compress(layers.mask.tobytes(), 9)
    return layers, mask, fax, _jpeg(layers.background, background_quality), _jpeg(layers.ink, ink_quality)


def _picture_object(writer, image, data, **entries):
    stream = StreamObject()
    stream._data = data
    stream.update({
        NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
        NameObject("/Width"): NumberObject(image.width), NameObject("/Height"): NumberObject(image.height),
        NameObject("/Length"): NumberObject(len(data))})
    for key, value in entries.items():
        stream[NameObject("/" + key)] = value
    return writer._add_object(stream)


_IMAGE_KEYS = ("/Width", "/Height", "/ColorSpace", "/BitsPerComponent", "/Filter", "/DecodeParms", "/Decode",
               "/Intent", "/Interpolate", "/ImageMask", "/Mask", "/SMask", "/SMaskInData", "/Alternates",
               "/ID", "/OPI")  # not /OC, /StructParent, /Metadata, /Name: a form carries those too


def _store_layers(writer, obj, encoded):
    """Turn the scan picture ``obj`` into a form that draws its layers: the
    background, then the ink colour picture through the text shape. The
    object stays the one the page draws, so it lands where the scan did."""
    layers, mask, fax, background, ink = encoded
    eight, one = NumberObject(8), NumberObject(1)

    def space(image):
        return NameObject("/DeviceGray" if image.mode == "L" else "/DeviceRGB")

    if fax:
        packing = {"Filter": NameObject("/CCITTFaxDecode"),
                   "DecodeParms": DictionaryObject({NameObject("/K"): NumberObject(-1),
                                                    NameObject("/Columns"): NumberObject(layers.mask.width),
                                                    NameObject("/Rows"): NumberObject(layers.mask.height)})}
    else:
        packing = {"Filter": NameObject("/FlateDecode")}
    from pypdf.generic import BooleanObject

    shape = _picture_object(writer, layers.mask, mask, ImageMask=BooleanObject(True), BitsPerComponent=one,
                            **packing)
    paper = _picture_object(writer, layers.background, background, ColorSpace=space(layers.background),
                            BitsPerComponent=eight, Filter=NameObject("/DCTDecode"))
    colour = _picture_object(writer, layers.ink, ink, ColorSpace=space(layers.ink), BitsPerComponent=eight,
                             Filter=NameObject("/DCTDecode"), Mask=shape)
    for key in _IMAGE_KEYS:
        if key in obj:
            del obj[key]
    data = zlib.compress(b"q /Bg Do /Fg Do Q", 9)
    obj.update({
        NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject([NumberObject(v) for v in (0, 0, 1, 1)]),
        NameObject("/Resources"): DictionaryObject({NameObject("/XObject"): DictionaryObject({
            NameObject("/Bg"): paper, NameObject("/Fg"): colour})}),
        NameObject("/Filter"): NameObject("/FlateDecode"), NameObject("/Length"): NumberObject(len(data))})
    obj._data = data
    if hasattr(obj, "decoded_self"):
        obj.decoded_self = None


def downsample_images(writer, max_dpi, jpeg_quality, scan_layers=None):
    """Downsample/re-encode images. With ``scan_layers`` (a SCAN_LAYERS
    entry), a page's scan is kept as a sharp text shape over a small
    background when that is no larger. Returns (images changed, bytes
    saved, scanned pages kept sharp)."""
    changed = saved = sharp = 0
    done = set()
    # A picture used on several pages is resized for the largest of them.
    sizes, uses = {}, {}
    for page in writer.pages:
        for key, (w, h) in image_display_sizes(page, writer, uses).items():
            old = sizes.get(key, (0, 0))
            sizes[key] = (max(old[0], w), max(old[1], h))
    for number, page in enumerate(writer.pages):
        progress.report(number, len(writer.pages), "page")
        for key, obj in _page_images(page):
            if key in done or _skip(obj):
                continue
            done.add(key)
            width, height = int(obj.get("/Width", 0)), int(obj.get("/Height", 0))
            target = None
            drawn_w, drawn_h = sizes.get(key, (0, 0))
            if max_dpi and drawn_w > 0 and drawn_h > 0 and width > 0 and height > 0:
                dpi = max(width / (drawn_w / 72), height / (drawn_h / 72))
                if dpi > max_dpi * 1.1:
                    factor = max_dpi / dpi
                    target = (max(1, round(width * factor)), max(1, round(height * factor)))
            scan_dpi = _scan_dpi(obj, (drawn_w, drawn_h), page, uses.get(key, 0)) if scan_layers else None
            try:
                image = _flatten(_decode(obj, target))
            except Exception:
                continue
            original = len(obj._data)
            if target and image.size != target:
                # Halving first, then Lanczos for the rest: Pillow's own
                # shortcut, indistinguishable from Lanczos all the way.
                image = image.resize(target, Image.LANCZOS, reducing_gap=3.0)
            data, filt, space, extra = _encode(image, jpeg_quality)
            usual = min(len(data), original)
            if scan_dpi:
                try:
                    # Decoded again, whole: the text shape wants every pixel.
                    encoded = _scan_layers(_flatten(_decode(obj)), scan_dpi, scan_layers)
                    size = sum(len(part) for part in (encoded[1], encoded[3], encoded[4])) if encoded else None
                    if size is not None and size < original and size <= SCAN_ALLOWANCE * usual:
                        _store_layers(writer, obj, encoded)
                        changed, saved, sharp = changed + 1, saved + original - size, sharp + 1
                        continue
                except Exception:  # the engine never fails a compression
                    pass
            if len(data) >= original:
                continue
            _resize_smask(obj, image.size)
            _store(obj, image, data, filt, space, extra)
            changed += 1
            saved += original - len(data)
    return changed, saved, sharp


# --------------------------------------------------------------------------
# Colour conversion
# --------------------------------------------------------------------------

def _gray_from_rgb(r, g, b):
    return 0.299 * r + 0.587 * g + 0.114 * b


def _rgb_from_cmyk(c, m, y, k):
    return (1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)


def _cmyk_from_rgb(r, g, b):
    k = 1 - max(r, g, b)
    if k >= 1:
        return 0.0, 0.0, 0.0, 1.0
    return (1 - r - k) / (1 - k), (1 - g - k) / (1 - k), (1 - b - k) / (1 - k), k


def _convert_values(values, target):
    """Convert 1 (gray), 3 (rgb) or 4 (cmyk) colour components."""
    if len(values) == 1:
        rgb = (values[0],) * 3
    elif len(values) == 3:
        rgb = tuple(values)
    else:
        rgb = _rgb_from_cmyk(*values)
    rgb = tuple(min(1.0, max(0.0, v)) for v in rgb)
    if target == "gray":
        return (_gray_from_rgb(*rgb),)
    if target == "rgb":
        return (values[0],) if len(values) == 1 else rgb
    if len(values) == 1:
        return (0.0, 0.0, 0.0, 1 - values[0])
    return _cmyk_from_rgb(*rgb)


_SET_OPS = {
    # operator -> (number of operands, stroking?)
    b"g": (1, False), b"G": (1, True),
    b"rg": (3, False), b"RG": (3, True),
    b"k": (4, False), b"K": (4, True),
}
_TARGET_OPS = {
    "gray": (b"g", b"G", "/DeviceGray"),
    "rgb": (b"rg", b"RG", "/DeviceRGB"),
    "cmyk": (b"k", b"K", "/DeviceCMYK"),
}


def _components(colorspace, resources):
    """Number of components of a simple colour space, or None if it is one
    we don't convert (patterns, separations, indexed, ...)."""
    names = {"/DeviceGray": 1, "/CalGray": 1, "/DeviceRGB": 3, "/CalRGB": 3,
             "/DeviceCMYK": 4, "/Lab": None}
    if colorspace in names:
        return names[colorspace]
    spaces = resources.get("/ColorSpace") if resources is not None else None
    if spaces is None or colorspace not in spaces.get_object():
        return None
    cs = spaces.get_object()[colorspace].get_object()
    if isinstance(cs, (list, ArrayObject)) and cs:
        family = cs[0]
        if family == "/ICCBased":
            return int(cs[1].get_object().get("/N", 0)) or None
        if family in names:
            return names[family]
    return None


def _convert_content(operations, resources, target):
    fill_op, stroke_op, target_space = _TARGET_OPS[target]
    fill_n = stroke_n = 1  # components of the current fill/stroke colour space
    out = []
    for operands, op in operations:
        if op in _SET_OPS:
            n, stroking = _SET_OPS[op]
            values = _convert_values([float(v) for v in operands[:n]], target)
            new_op = stroke_op if stroking else fill_op
            if target == "rgb" and len(values) == 1:
                new_op = b"G" if stroking else b"g"
            out.append(([FloatObject(round(v, 4)) for v in values], new_op))
            continue
        if op in (b"cs", b"CS") and operands:
            n = _components(operands[0], resources)
            if op == b"cs":
                fill_n = n
            else:
                stroke_n = n
            if n:
                out.append(([NameObject(target_space)], op))
                continue
        elif op in (b"sc", b"scn", b"SC", b"SCN"):
            n = fill_n if op in (b"sc", b"scn") else stroke_n
            if n and len(operands) == n and all(not isinstance(v, NameObject) for v in operands):
                values = _convert_values([float(v) for v in operands], target)
                if target == "rgb" and len(values) == 1:
                    values = values * 3  # the space was set to /DeviceRGB above
                out.append(([FloatObject(round(v, 4)) for v in values], op))
                continue
        out.append((operands, op))
    return out


def _icc_transform(profile_path, target):
    from PIL import ImageCms

    srgb = ImageCms.createProfile("sRGB")
    dest = ImageCms.getOpenProfile(str(profile_path))
    out_mode = {"gray": "L", "rgb": "RGB", "cmyk": "CMYK"}[target]
    return ImageCms.buildTransform(srgb, dest, "RGB", out_mode)


def _convert_image(image, target, transform):
    image = _flatten(image)
    if transform is not None:
        if image.mode != "RGB":
            image = image.convert("RGB")
        return transform.apply(image)
    mode = {"gray": "L", "rgb": "RGB", "cmyk": "CMYK"}[target]
    if image.mode == mode:
        return image
    if image.mode == "CMYK" and mode == "L":
        image = image.convert("RGB")
    return image.convert(mode)


def _was_jpeg(obj):
    filters = obj.get("/Filter")
    filters = [filters] if not isinstance(filters, (list, ArrayObject)) else list(filters)
    return "/DCTDecode" in filters


def _forms(resources, seen, depth=0):
    for ref in _xobjects(resources).values():
        obj = ref.get_object()
        key = getattr(ref, "idnum", id(obj))
        if obj.get("/Subtype") == "/Form" and key not in seen and depth < 10:
            seen.add(key)
            yield obj
            yield from _forms(obj.get("/Resources"), seen, depth + 1)


def convert_colors(writer, target, icc_profile=None, images="all"):
    """Convert all simple colours and images to ``target``. With ``images``
    "cmyk", only CMYK pictures are converted and the others stay as they
    are, byte for byte. Returns the number of images converted."""
    only_cmyk = images == "cmyk"
    if target not in COLOR_TARGETS:
        raise ValueError(f"colour target must be one of {', '.join(COLOR_TARGETS)}")
    transform = _icc_transform(icc_profile, target) if icc_profile else None
    count = 0
    done_images, done_forms = set(), set()
    for number, page in enumerate(writer.pages):
        progress.report(number, len(writer.pages), "page")
        resources = page.get("/Resources")
        resources = resources.get_object() if resources is not None else None
        contents = page.get_contents()
        if contents is not None:
            cs = ContentStream(contents, writer)
            cs.operations = _convert_content(cs.operations, resources, target)
            page.replace_contents(cs)
        for form in _forms(resources, done_forms):
            form_res = form.get("/Resources")
            form_res = form_res.get_object() if form_res is not None else resources
            cs = ContentStream(form, writer)
            ops = _convert_content(cs.operations, form_res, target)
            cs.operations = ops
            # Stored afresh with Flate, whatever filters (ASCII85, LZW...) it had.
            form._data = zlib.compress(cs.get_data())
            form[NameObject("/Filter")] = NameObject("/FlateDecode")
            form[NameObject("/Length")] = NumberObject(len(form._data))
            if "/DecodeParms" in form:
                del form["/DecodeParms"]
            if hasattr(form, "decoded_self"):
                form.decoded_self = None
        for key, obj in _page_images(page):
            # Black-and-white pictures are gray already (and far smaller as
            # they are); colour-keyed ones can't be re-encoded.
            if key in done_images or _bilevel(obj) or _keyed(obj):
                continue
            done_images.add(key)
            try:
                image = _decode(obj)
                if only_cmyk and image.mode != "CMYK":
                    continue
                image = _convert_image(image, target, transform)
            except Exception:
                continue
            data, filt, space, extra = _encode(image, 90 if _was_jpeg(obj) else None)
            _store(obj, image, data, filt, space, extra)
            count += 1
    if icc_profile:
        _add_output_intent(writer, icc_profile, target)
    return count


def _add_output_intent(writer, profile_path, target):
    from PIL import ImageCms

    with open(profile_path, "rb") as fh:
        data = fh.read()
    try:
        name = ImageCms.getProfileDescription(ImageCms.getOpenProfile(str(profile_path))).strip()
    except Exception:
        name = "Custom"
    stream = StreamObject()
    stream._data = zlib.compress(data)
    stream[NameObject("/Filter")] = NameObject("/FlateDecode")
    stream[NameObject("/N")] = NumberObject({"gray": 1, "rgb": 3, "cmyk": 4}[target])
    intent = DictionaryObject({
        NameObject("/Type"): NameObject("/OutputIntent"),
        NameObject("/S"): NameObject("/GTS_PDFX" if target == "cmyk" else "/GTS_PDFA1"),
        NameObject("/OutputConditionIdentifier"): TextStringObject(name),
        NameObject("/Info"): TextStringObject(name),
        NameObject("/DestOutputProfile"): writer._add_object(stream),
    })
    writer._root_object[NameObject("/OutputIntents")] = ArrayObject([writer._add_object(intent)])
