# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Pictures on a page, edited where they are: moved, resized, replaced or
deleted, like Acrobat's Edit PDF does with images.

A picture is found by the order it's drawn in among the page's own
pictures (``index``, 0-based), the same order ``list_images`` reports.
Only the step that draws it changes; the rest of the page stays as it is:

- move / resize: the picture's box becomes ``box`` (page coordinates,
  points, [x0, y0, x1, y1]). The matrix it's drawn with is adjusted just
  for it. A picture clipped by the page's drawing (Word and browsers clip a
  picture to its own box) is drawn again at the end of the page instead,
  outside the clip, so it can't vanish when it moves.
- delete: the step that draws it is taken out (the image data stays in
  the file if something else uses it).
- replace: another picture (PNG, JPEG, ...) is drawn in its place, fitted
  inside its box without stretching.

Pictures inside form XObjects (drawn by a nested drawing) are listed as
not editable.
"""

import io
import math
import zlib

from pypdf.generic import (ContentStream, DecodedStreamObject, DictionaryObject, FloatObject, NameObject,
                           NumberObject, StreamObject)

from pdfmanager import core

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _mul(m, n):
    """m then n, as PDF matrices [a b c d e f] (row vectors: p' = p m)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D,
            e * A + f * C + E, e * B + f * D + F)


def _inverse(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        raise core.PdfManagerError("that picture is drawn with no size; it can't be changed")
    return (d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det)


def _box(ctm):
    """The box the unit square lands in under ``ctm``."""
    pts = [(x * ctm[0] + y * ctm[2] + ctm[4], x * ctm[1] + y * ctm[3] + ctm[5]) for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def _xobjects(page):
    resources = page.get("/Resources")
    resources = resources.get_object() if resources is not None else {}
    xobjs = resources.get("/XObject")
    return xobjs.get_object() if xobjs is not None else {}


def _walk(page, pdf):
    """The page's drawing steps, and for each picture drawn by the page
    itself: (operation index, name, matrix, clipped, form)."""
    ops = ContentStream(page.get_contents(), pdf).operations if page.get_contents() is not None else []
    xobjs = _xobjects(page)
    found, stack, ctm, clipped = [], [], IDENTITY, False
    for i, (operands, op) in enumerate(ops):
        if op == b"q":
            stack.append((ctm, clipped))
        elif op == b"Q" and stack:
            ctm, clipped = stack.pop()
        elif op == b"cm" and len(operands) == 6:
            try:
                ctm = _mul(tuple(float(v) for v in operands), ctm)
            except (TypeError, ValueError):
                pass
        elif op in (b"W", b"W*"):
            clipped = True
        elif op == b"Do" and operands:
            ref = xobjs.get(operands[0])
            obj = ref.get_object() if ref is not None else None
            if obj is None:
                continue
            if obj.get("/Subtype") == "/Image":
                found.append((i, operands[0], ctm, clipped, False, obj))
            elif obj.get("/Subtype") == "/Form":
                found.append((i, operands[0], ctm, clipped, True, obj))
    return ops, found


def list_images(path, password=None, pages=None):
    """{page number: [{index, box, width, height, editable}]} for the
    pictures each page draws (pictures inside a form XObject come with
    ``editable`` False and the form's box)."""
    reader = core.open_pdf(path, password)
    result = {}
    for number, page in enumerate(reader.pages, 1):
        if pages and number not in pages:
            continue
        try:
            _, found = _walk(page, reader)
        except Exception:  # a page whose drawing can't be read: nothing to offer on it
            continue
        items, index = [], 0
        for _, _, ctm, _, form, obj in found:
            if form:
                if _has_image(obj):
                    items.append({"index": None, "box": _form_box(obj, ctm), "editable": False})
                continue
            items.append({"index": index, "box": [round(v, 3) for v in _box(ctm)],
                          "width": int(obj.get("/Width", 0)), "height": int(obj.get("/Height", 0)), "editable": True})
            index += 1
        if items:
            result[number] = items
    return result


def _has_image(form, depth=0):
    resources = form.get("/Resources")
    xobjs = (resources.get_object().get("/XObject") if resources is not None else None) or {}
    for ref in xobjs.get_object().values() if hasattr(xobjs, "get_object") else xobjs.values():
        obj = ref.get_object()
        if obj.get("/Subtype") == "/Image" or (obj.get("/Subtype") == "/Form" and depth < 5 and _has_image(obj, depth + 1)):
            return True
    return False


def _form_box(form, ctm):
    bbox = [float(v) for v in form.get("/BBox", [0, 0, 1, 1])]
    matrix = tuple(float(v) for v in form.get("/Matrix", IDENTITY))
    m = _mul(matrix, ctm)
    pts = [(x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5]) for x in (bbox[0], bbox[2]) for y in (bbox[1], bbox[3])]
    return [round(min(p[0] for p in pts), 3), round(min(p[1] for p in pts), 3),
            round(max(p[0] for p in pts), 3), round(max(p[1] for p in pts), 3)]


def _fit(old, new):
    """The page-space matrix taking box ``old`` onto box ``new``."""
    ow, oh = old[2] - old[0], old[3] - old[1]
    nw, nh = new[2] - new[0], new[3] - new[1]
    if ow <= 0 or oh <= 0 or nw <= 0 or nh <= 0:
        raise core.PdfManagerError("a picture needs a width and a height")
    sx, sy = nw / ow, nh / oh
    return (sx, 0.0, 0.0, sy, new[0] - old[0] * sx, new[1] - old[1] * sy)


def _image_stream(data):
    """An image XObject for the picture file ``data``: a JPEG as it is, other
    pictures compressed losslessly (with their transparency as a soft mask)."""
    from PIL import Image, ImageOps

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        raise core.PdfManagerError("that file isn't a picture ZPDF can read (use PNG or JPEG)") from None
    upright = ImageOps.exif_transpose(image) or image
    stream = StreamObject()
    if image.format == "JPEG" and image.mode in ("RGB", "L") and upright is image:
        stream._data = data
        stream[NameObject("/Filter")] = NameObject("/DCTDecode")
        space = "/DeviceRGB" if image.mode == "RGB" else "/DeviceGray"
        smask = None
    else:
        image = upright
        alpha = image.convert("RGBA").getchannel("A") if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info else None
        rgb = image.convert("L") if image.mode in ("L", "LA", "1", "I", "I;16") else image.convert("RGB")
        stream._data = zlib.compress(rgb.tobytes(), 6)
        stream[NameObject("/Filter")] = NameObject("/FlateDecode")
        space = "/DeviceGray" if rgb.mode == "L" else "/DeviceRGB"
        smask = None
        if alpha is not None and alpha.getextrema() != (255, 255):
            smask = StreamObject()
            smask._data = zlib.compress(alpha.tobytes(), 6)
            smask.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                          NameObject("/Width"): NumberObject(alpha.width), NameObject("/Height"): NumberObject(alpha.height),
                          NameObject("/ColorSpace"): NameObject("/DeviceGray"), NameObject("/BitsPerComponent"): NumberObject(8),
                          NameObject("/Filter"): NameObject("/FlateDecode")})
    stream.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                   NameObject("/Width"): NumberObject(image.width), NameObject("/Height"): NumberObject(image.height),
                   NameObject("/ColorSpace"): NameObject(space), NameObject("/BitsPerComponent"): NumberObject(8)})
    return stream, smask, image.width, image.height


def _matrix_text(m):
    return " ".join(f"{v:.6f}".rstrip("0").rstrip(".") or "0" for v in m)


def edit_images(path, output, changes, password=None, pictures=None):
    """Make ``changes`` to the pictures: each {page, index, op: "move" |
    "delete" | "replace", box (for move/resize), picture (for replace: a key
    of ``pictures``, a dict of picture file bytes)}. Returns how many."""
    reader = core.open_pdf(path, password)
    writer = core.clone_document(reader)
    by_page = {}
    for change in changes:
        try:
            number, index = int(change.get("page")), int(change.get("index"))
        except (TypeError, ValueError):
            raise core.PdfManagerError("a picture change has no page or picture number") from None
        if not 1 <= number <= len(writer.pages):
            raise core.PdfManagerError(f"there is no page {number}")
        by_page.setdefault(number, []).append((index, change))
    done = 0
    for number, items in by_page.items():
        page = writer.pages[number - 1]
        ops, found = _walk(page, writer)
        images = [f for f in found if not f[4]]
        xobjs = None
        removed, appended = set(), []
        replaced = {}  # operation index -> new name
        adjusted = {}  # operation index -> matrix to put before it
        for index, change in items:
            if not 0 <= index < len(images):
                raise core.PdfManagerError(f"page {number} has no picture {index + 1}; reopen the document")
            at, name, ctm, clipped, _, obj = images[index]
            op = change.get("op")
            old = _box(ctm)
            if op == "delete":
                removed.add(at)
            elif op in ("move", "replace"):
                target = old
                if change.get("box") is not None:
                    try:
                        target = [float(v) for v in change["box"]][:4]
                    except (TypeError, ValueError):
                        raise core.PdfManagerError("a picture's new place isn't four numbers") from None
                    if len(target) != 4 or not all(math.isfinite(v) for v in target):
                        raise core.PdfManagerError("a picture's new place isn't four numbers")
                    target = [min(target[0], target[2]), min(target[1], target[3]), max(target[0], target[2]), max(target[1], target[3])]
                if op == "replace":
                    data = (pictures or {}).get(change.get("picture"))
                    if not data:
                        raise core.PdfManagerError("no picture was given to put in")
                    stream, smask, pw, ph = _image_stream(data)
                    if smask is not None:
                        stream[NameObject("/SMask")] = writer._add_object(smask)
                    if xobjs is None:
                        resources = page.get("/Resources")
                        if resources is None:
                            page[NameObject("/Resources")] = DictionaryObject()
                        resources = page["/Resources"].get_object()
                        if "/XObject" not in resources:
                            resources[NameObject("/XObject")] = DictionaryObject()
                        xobjs = resources["/XObject"].get_object()
                    new_name = NameObject(f"/ZImg{len(xobjs) + 1}")
                    while new_name in xobjs:
                        new_name = NameObject(new_name + "x")
                    xobjs[new_name] = writer._add_object(stream)
                    replaced[at] = new_name
                    name = new_name
                    # fitted inside the box without stretching
                    tw, th = target[2] - target[0], target[3] - target[1]
                    scale = min(tw / pw, th / ph)
                    fw, fh = pw * scale, ph * scale
                    target = [target[0] + (tw - fw) / 2, target[1] + (th - fh) / 2, target[0] + (tw + fw) / 2, target[1] + (th + fh) / 2]
                    final = (target[2] - target[0], 0.0, 0.0, target[3] - target[1], target[0], target[1])
                else:
                    final = _mul(ctm, _fit(old, target))
                if clipped:  # redrawn at the end, outside whatever clips it now
                    removed.add(at)
                    appended.append((final, name))
                else:
                    # new matrix = A x ctm, so A = final x ctm^-1
                    adjusted[at] = _mul(final, _inverse(ctm))
            else:
                raise core.PdfManagerError(f"unknown picture change {op!r}")
            done += 1
        new_ops = []
        for i, (operands, op) in enumerate(ops):
            if i in removed:
                continue
            if i in adjusted or i in replaced:
                name = replaced.get(i, operands[0])
                new_ops.append(([], b"q"))
                if i in adjusted:
                    new_ops.append(([FloatObject(v) for v in adjusted[i]], b"cm"))
                new_ops.append(([name], b"Do"))
                new_ops.append(([], b"Q"))
                continue
            new_ops.append((operands, op))
        if appended:
            # The page's own drawing in q ... Q, so it leaves no state behind.
            new_ops = [([], b"q")] + new_ops + [([], b"Q")]
            for matrix, name in appended:
                new_ops += [([], b"q"), ([FloatObject(v) for v in matrix], b"cm"), ([name], b"Do"), ([], b"Q")]
        content = ContentStream(None, writer)
        content.operations = new_ops
        stream = DecodedStreamObject()
        stream.set_data(content.get_data())
        page[NameObject("/Contents")] = writer._add_object(stream)
    core._write(writer, output)
    return done
