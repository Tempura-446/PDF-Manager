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


def _walk_images(content_owner, resources, pdf, ctm, sizes, depth=0):
    """Record the largest drawn size (in points) of every image XObject,
    keyed by object number. Recurses into form XObjects."""
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
            elif subtype == "/Form":
                matrix = tuple(float(x) for x in obj.get("/Matrix", _IDENTITY))
                _walk_images(obj, obj.get("/Resources", resources), pdf,
                             _mul(matrix, ctm), sizes, depth + 1)


def image_display_sizes(page, pdf):
    sizes = {}
    _walk_images(page, page.get("/Resources"), pdf, _IDENTITY, sizes)
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

def _decode(obj):
    from pypdf.generic._image_xobject import _xobj_to_image

    _, _, image = _xobj_to_image(obj)
    return image


def _skip(obj):
    # Stencil masks and 1-bit images compress best as they are; JBIG2/CCITT
    # scans are already tiny.
    if obj.get("/ImageMask") or obj.get("/BitsPerComponent", 8) == 1:
        return True
    filters = obj.get("/Filter")
    filters = [filters] if not isinstance(filters, (list, ArrayObject)) else list(filters)
    return any(f in ("/JBIG2Decode", "/CCITTFaxDecode") for f in filters)


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


def downsample_images(writer, max_dpi, jpeg_quality):
    """Downsample/re-encode images. Returns (images changed, bytes saved)."""
    changed = saved = 0
    done = set()
    for number, page in enumerate(writer.pages):
        progress.report(number, len(writer.pages), "page")
        sizes = image_display_sizes(page, writer)
        for key, obj in _page_images(page):
            if key in done or _skip(obj):
                continue
            done.add(key)
            try:
                image = _flatten(_decode(obj))
            except Exception:
                continue
            original = len(obj._data)
            drawn_w, drawn_h = sizes.get(key, (0, 0))
            if max_dpi and drawn_w > 0 and drawn_h > 0:
                dpi = max(image.width / (drawn_w / 72), image.height / (drawn_h / 72))
                if dpi > max_dpi * 1.1:
                    factor = max_dpi / dpi
                    image = image.resize((max(1, round(image.width * factor)),
                                          max(1, round(image.height * factor))), Image.LANCZOS)
            data, filt, space, extra = _encode(image, jpeg_quality)
            if len(data) >= original:
                continue
            _resize_smask(obj, image.size)
            _store(obj, image, data, filt, space, extra)
            changed += 1
            saved += original - len(data)
    return changed, saved


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


def convert_colors(writer, target, icc_profile=None):
    """Convert all simple colours and images to ``target``. Returns the
    number of images converted."""
    if target not in COLOR_TARGETS:
        raise ValueError(f"colour target must be one of {', '.join(COLOR_TARGETS)}")
    transform = _icc_transform(icc_profile, target) if icc_profile else None
    images = 0
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
            form.set_data(cs.get_data())
        for key, obj in _page_images(page):
            if key in done_images or obj.get("/ImageMask"):
                continue
            done_images.add(key)
            try:
                image = _convert_image(_decode(obj), target, transform)
            except Exception:
                continue
            data, filt, space, extra = _encode(image, 90 if _was_jpeg(obj) else None)
            _store(obj, image, data, filt, space, extra)
            images += 1
    if icc_profile:
        _add_output_intent(writer, icc_profile, target)
    return images


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
