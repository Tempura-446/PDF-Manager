# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Image measurement, background flattening, dewarping, low-confidence retry, and recognition."""

import logging
import math
import statistics

from pdfmanager.textlayer import Word
from pdfmanager.ocr._facade import _ocr_attr
from pdfmanager.ocr.engines import Line, MAX_RENDER_SIDE, MIN_CONFIDENCE
from pdfmanager.ocr.models import _call_lock, _directml_available, _engine, _engine_lock, _engines, default_gpu

def _array(image):
    import numpy

    return numpy.asarray(image.convert("RGB"))


GREY_PAPER = 190          # paper darker than this (of 255) is stretched to full contrast
FAINT_CONTRAST = 75       # paper-to-ink range below this (when ink exists) is faint print
UNEVEN_LIGHT = 35         # background 95th - 10th percentile above this triggers background flattening
SPECKLE_RATIO = 0.004     # share of isolated single-pixel spikes that triggers light denoising
MIN_DENOISE_STROKE = 2.2  # estimated stroke width (px) below which median denoising is skipped
SMALL_TEXT_PX = 20.0      # median line height (px) below which higher-resolution reading is tried
RETRY_CONFIDENCE = 0.78   # lines below this get a second reading from a cleaned/enlarged crop
MAX_LINE_RETRIES = 25     # cap per-page line retries so pure noise cannot stall


def _measure_image(image):
    """Measure paper brightness, ink contrast, lighting evenness, speckle and
    approximate stroke width on a fast copy of ``image``."""
    import numpy as np
    from PIL import ImageFilter

    grey = image.convert("L")
    w, h = grey.size
    if w == 0 or h == 0:
        return {"paper": 255.0, "contrast": 0.0, "uneven": 0.0, "speckle": 0.0, "stroke_width": 0.0}

    # Unblurred strided sample on interior (excluding 4% outer scan border) preserves true ink darkness.
    bx, by = int(w * 0.04), int(h * 0.04)
    inner = grey.crop((bx, by, w - bx, h - by)) if (w - 2 * bx >= 32 and h - 2 * by >= 32) else grey
    iw, ih = inner.size
    step = max(1, max(iw, ih) // 1000)
    full_arr = np.asarray(grey, np.float32)
    sample = np.asarray(inner, np.float32)[::step, ::step]
    paper = float(np.median(sample))
    p_ink = float(np.percentile(sample, 0.5))
    contrast = max(0.0, paper - p_ink)

    small = inner.copy()
    small.thumbnail((400, 400))
    bg_im = small.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(5))
    bg = np.asarray(bg_im, np.float32)
    uneven = float(np.percentile(bg, 95) - np.percentile(bg, 10))

    # Isolated 1-px speckle on a 1:1 centre patch: differs sharply from all 4 neighbours
    # (continuous text strokes always share a neighbour along the stroke, so they score 0).
    pw, ph = min(w, 1000), min(h, 1000)
    x0, y0 = (w - pw) // 2, (h - ph) // 2
    patch = full_arr[y0:y0 + ph, x0:x0 + pw]
    if patch.shape[0] >= 3 and patch.shape[1] >= 3:
        c = patch[1:-1, 1:-1]
        min_diff = np.minimum(
            np.minimum(np.abs(c - patch[:-2, 1:-1]), np.abs(c - patch[2:, 1:-1])),
            np.minimum(np.abs(c - patch[1:-1, :-2]), np.abs(c - patch[1:-1, 2:])),
        )
        speckle = float(np.mean(min_diff > 28.0))
    else:
        speckle = 0.0

    # Approximate stroke width after a 3x3 median so 1-2 px random noise clusters
    # vanish and only real print strokes are measured (thin 1-px strokes also vanish,
    # keeping stroke_width < MIN_DENOISE_STROKE to protect small/thin print).
    ink_thresh = paper - max(18.0, contrast * 0.35)
    stroke_width = 0.0
    for px0, py0 in ((x0, y0), (min(max(0, w - pw), w // 10), min(max(0, h - ph), h // 10))):
        sub = grey.crop((px0, py0, px0 + pw, py0 + ph)).filter(ImageFilter.MedianFilter(3))
        ink = np.asarray(sub, np.float32) < ink_thresh
        ink_count = int(ink.sum())
        if ink_count >= 20:
            cpad = np.pad(ink, 1, mode="constant", constant_values=False)
            interior = cpad[1:-1, 1:-1] & cpad[:-2, 1:-1] & cpad[2:, 1:-1] & cpad[1:-1, :-2] & cpad[1:-1, 2:]
            boundary = int((ink & ~interior).sum())
            stroke_width = 2.0 * ink_count / max(boundary, 1)
            break
    return {"paper": paper, "contrast": contrast, "uneven": uneven,
            "speckle": speckle, "stroke_width": float(stroke_width)}


def _flatten_background(image):
    """Even out shadows or uneven lighting for OCR without binarising."""
    import numpy as np
    from PIL import Image

    from pdfmanager import pagephoto

    rgb = image.convert("RGB")
    grey = rgb.convert("L")
    bg = np.maximum(pagephoto._background(grey), 24.0)
    arr = np.asarray(rgb, np.float32)
    norm = np.clip((arr / bg[..., None]) * 245.0, 0, 255).astype(np.uint8)
    return Image.fromarray(norm, "RGB")


def _clean_for_reading(image):
    """Clean ``image`` for OCR only (the PDF keeps its original picture),
    applying only the steps the page's measurements call for. Returns
    ``(cleaned_image, steps)`` where ``steps`` names what was applied."""
    from PIL import ImageFilter, ImageOps

    stats = _measure_image(image)
    out = image
    steps = []
    if stats["uneven"] >= UNEVEN_LIGHT and stats["paper"] < 245.0:
        out = _flatten_background(out)
        steps.append("background")
        # Re-check contrast after evening out the background.
        after = _measure_image(out)
        if after["paper"] < GREY_PAPER or (5.0 <= after["contrast"] < FAINT_CONTRAST):
            out = ImageOps.autocontrast(out.convert("RGB"), cutoff=1)
            steps.append("contrast")
    elif stats["paper"] < GREY_PAPER or (5.0 <= stats["contrast"] < FAINT_CONTRAST):
        out = ImageOps.autocontrast(out.convert("RGB"), cutoff=1)
        steps.append("contrast")

    if stats["speckle"] >= SPECKLE_RATIO and stats["stroke_width"] >= MIN_DENOISE_STROKE:
        out = out.filter(ImageFilter.MedianFilter(3))
        steps.append("denoise")
    return out, steps


def _clear(image):
    """``image`` cleaned for reading when its paper is dark, uneven, faint
    or speckled; returned untouched when it is already clean."""
    return _clean_for_reading(image)[0]


def _quad(points):
    return [(float(x), float(y)) for x, y in points]


def _map_quad(quad, matrix):
    """Project a 4-point quad through a 3x3 homography ``matrix``."""
    out = []
    for x, y in quad:
        w = matrix[2, 0] * x + matrix[2, 1] * y + matrix[2, 2]
        if abs(w) < 1e-9:
            w = 1.0
        px = (matrix[0, 0] * x + matrix[0, 1] * y + matrix[0, 2]) / w
        py = (matrix[1, 0] * x + matrix[1, 1] * y + matrix[1, 2]) / w
        out.append((float(px), float(py)))
    return out


def _straighten_photo(image):
    """If ``image`` is a phone photo of a page on a darker surface with
    noticeable perspective, return ``(flattened_image, to_photo_matrix)``;
    otherwise ``(None, None)``. Used for reading only: word boxes are mapped
    back to the original photo via ``to_photo_matrix``."""
    import numpy as np
    from PIL import Image

    from pdfmanager import pagephoto

    if min(image.size) < 300:
        return None, None
    found = pagephoto.find_page(image)
    if not found.found or found.confidence < 0.5 or not found.corners:
        return None, None
    w, h = image.size
    corners = [np.asarray(c, float) for c in found.corners]
    # Skip if the detected corners are already essentially the full image rectangle.
    img_corners = [np.array([0.0, 0.0]), np.array([float(w), 0.0]),
                   np.array([float(w), float(h)]), np.array([0.0, float(h)])]
    max_shift = max(float(np.linalg.norm(c - ic)) for c, ic in zip(corners, img_corners))
    if max_shift < 0.02 * max(w, h):
        return None, None
    ratio = pagephoto.snap_shape(pagephoto.page_shape(found.corners, image.size))
    height = max(np.linalg.norm(corners[3] - corners[0]), np.linalg.norm(corners[2] - corners[1]))
    width = height * ratio
    longest = max(width, height, 1.0)
    if longest > max(w, h):
        width, height = width * max(w, h) / longest, height * max(w, h) / longest
    fw, fh = max(1, round(width)), max(1, round(height))
    target = [(0.0, 0.0), (float(fw), 0.0), (float(fw), float(fh)), (0.0, float(fh))]
    to_photo = pagephoto._homography(target, corners)
    coeffs = (to_photo / to_photo[2, 2]).ravel()[:8]
    mode = image.mode if image.mode in ("RGB", "L") else "RGB"
    flat = image.convert(mode).transform((fw, fh), Image.PERSPECTIVE, tuple(coeffs), Image.BICUBIC)
    return flat, to_photo


def _dewarp_line_crop(crop):
    """Mildly flatten a curved baseline in a single-line crop by fitting a
    quadratic y(x) to dark-ink column centroids and shifting columns vertically.
    Returns ``crop`` untouched when the line is already straight."""
    import numpy as np
    from PIL import Image

    grey = np.asarray(crop.convert("L"), np.float32)
    h, w = grey.shape
    if w < 60 or h < 12:
        return crop
    paper = float(np.percentile(grey, 85))
    thresh = paper - max(20.0, (paper - float(np.percentile(grey, 5))) * 0.35)
    ink = grey < thresh
    ys = np.arange(h, dtype=np.float32)
    xs_fit, ys_fit = [], []
    step = max(1, w // 32)
    for x in range(0, w, step):
        col = ink[:, x:min(w, x + step)]
        if col.sum() >= 2:
            weights = col.sum(axis=1).astype(np.float32)
            xs_fit.append(x + step / 2.0)
            ys_fit.append(float((ys * weights).sum() / weights.sum()))
    if len(xs_fit) < 6:
        return crop
    xs_arr = np.asarray(xs_fit, np.float64)
    ys_arr = np.asarray(ys_fit, np.float64)
    coeffs = np.polyfit(xs_arr, ys_arr, 2)
    col_x = np.arange(w, dtype=np.float64)
    curve = np.polyval(coeffs, col_x)
    # Remove linear tilt component so we only measure curvature sag, then shift relative to median.
    sag = float(curve.max() - curve.min())
    if sag < 2.5 or sag > 0.35 * h:
        return crop
    shifts = np.round(curve - np.median(curve)).astype(int)
    rgb = np.asarray(crop.convert("RGB"))
    fill = int(round(paper))
    out = np.full_like(rgb, fill)
    for x in range(w):
        dy = int(shifts[x])
        if dy == 0:
            out[:, x] = rgb[:, x]
        elif dy > 0 and dy < h:
            out[:h - dy, x] = rgb[dy:, x]
        elif dy < 0 and -dy < h:
            out[-dy:, x] = rgb[:h + dy, x]
    return Image.fromarray(out, "RGB")


def _lines_score(lines):
    """Quality score for comparing two readings of the same page: confident characters."""
    return sum(w.confidence * len(w.text) for line in lines for w in line.words)


def _median_text_height(lines):
    """Median height (px) of recognised line boxes; 0.0 when empty."""
    from pdfmanager.ocr.layout import _box_size
    heights = [_box_size(line.quad)[1] for line in lines]
    return float(statistics.median(heights)) if heights else 0.0


def _scale_lines(lines, factor):
    """Scale every line and word quad by ``factor``."""
    out = []
    for line in lines:
        words = [Word(w.text, [(x * factor, y * factor) for x, y in w.quad], w.confidence) for w in line.words]
        out.append(Line(line.text, [(x * factor, y * factor) for x, y in line.quad], line.confidence, words))
    return out


def _map_lines(lines, matrix):
    """Project every line and word quad through a 3x3 homography ``matrix``."""
    out = []
    for line in lines:
        words = [Word(w.text, _map_quad(w.quad, matrix), w.confidence) for w in line.words]
        out.append(Line(line.text, _map_quad(line.quad, matrix), line.confidence, words))
    return out


def _adaptive_binarize_crop(crop):
    """Local-mean adaptive binarization for faint or unevenly shaded line crops."""
    import numpy as np
    from PIL import Image, ImageFilter

    grey = crop.convert("L")
    arr = np.asarray(grey, np.float32)
    bg = np.asarray(grey.filter(ImageFilter.GaussianBlur(radius=12)), np.float32)
    diff = bg - arr
    thresh = max(14.0, float(np.percentile(diff, 85)) * 0.65)
    out = np.where(diff > thresh, 0, 255).astype(np.uint8)
    return Image.fromarray(out, mode="L").convert("RGB")


def _retry_low_confidence(image, lines, script=None, quality="standard"):
    """Re-read low-confidence lines from a cleaned/enlarged crop and keep
    whichever reading has higher confidence (never both)."""
    from PIL import Image, ImageOps

    if not lines or not any(line.confidence < RETRY_CONFIDENCE for line in lines):
        return lines
    w_img, h_img = image.size
    engine = _ocr_attr("_engine", _engine)(script, quality)
    updated = []
    retried = 0
    for line in lines:
        if line.confidence >= RETRY_CONFIDENCE or retried >= MAX_LINE_RETRIES:
            updated.append(line)
            continue
        retried += 1
        from pdfmanager.ocr.layout import _bbox, _lines, _union
        x0, y0, x1, y1 = _bbox(line)
        lh = max(y1 - y0, 1.0)
        pad_x = max(8, int(round(lh * 0.35)))
        pad_y = max(6, int(round(lh * 0.35)))
        left = max(0, int(math.floor(x0)) - pad_x)
        top = max(0, int(math.floor(y0)) - pad_y)
        right = min(w_img, int(math.ceil(x1)) + pad_x)
        bottom = min(h_img, int(math.ceil(y1)) + pad_y)
        if right - left < 8 or bottom - top < 6:
            updated.append(line)
            continue
        from PIL import ImageFilter
        import numpy as np

        crop = image.crop((left, top, right, bottom))
        raw_median = float(np.median(np.asarray(crop.convert("L"), np.float32)))
        crop = ImageOps.autocontrast(crop.convert("RGB"), cutoff=1)
        crop = _dewarp_line_crop(crop)
        up = 2.0 if lh < 36.0 and max(crop.size) * 2 <= MAX_RENDER_SIDE else 1.0
        if up > 1.0:
            crop = crop.resize((max(1, round(crop.width * up)), max(1, round(crop.height * up))), Image.BICUBIC)
            crop = crop.filter(ImageFilter.UnsharpMask(radius=1.2, percent=110, threshold=2))
        with _call_lock:
            res = engine(_array(crop), use_det=True, use_cls=False, use_rec=True)
        cand_lines = _lines(res)
        # If crop has a dark background (e.g. white text on dark header/banner), also try inverted polarity.
        if raw_median < 95.0:
            inv = ImageOps.invert(crop.convert("RGB"))
            with _call_lock:
                inv_res = engine(_array(inv), use_det=True, use_cls=False, use_rec=True)
            inv_lines = _lines(inv_res)
            if inv_lines:
                inv_score = sum(cl.confidence * len(cl.text) for cl in inv_lines)
                cur_score = sum(cl.confidence * len(cl.text) for cl in cand_lines)
                if inv_score > cur_score:
                    cand_lines = inv_lines
        # If still low confidence on a light crop, try local adaptive binarization for faint/shaded ink.
        best_c = max((cl.confidence for cl in cand_lines), default=0.0)
        if raw_median >= 95.0 and best_c < RETRY_CONFIDENCE:
            bin_crop = _adaptive_binarize_crop(crop)
            with _call_lock:
                bin_res = engine(_array(bin_crop), use_det=True, use_cls=False, use_rec=True)
            bin_lines = _lines(bin_res)
            if bin_lines:
                bin_score = sum(cl.confidence * len(cl.text) for cl in bin_lines)
                cur_score = sum(cl.confidence * len(cl.text) for cl in cand_lines)
                if bin_score > cur_score:
                    cand_lines = bin_lines
        if not cand_lines:
            updated.append(line)
            continue
        # Combine candidate lines on this single-line crop into one replacement if strictly more confident.
        cand_words = []
        for cl in cand_lines:
            for cw in cl.words:
                mapped_quad = [(left + x / up, top + y / up) for x, y in cw.quad]
                cand_words.append(Word(cw.text, mapped_quad, cw.confidence))
        if not cand_words:
            updated.append(line)
            continue
        cand_conf = sum(cw.confidence * len(cw.text) for cw in cand_words) / max(sum(len(cw.text) for cw in cand_words), 1)
        if cand_conf > line.confidence:
            cand_text = " ".join(cw.text for cw in cand_words)
            cand_quad = _union([cw.quad for cw in cand_words]) if len(cand_words) > 1 else cand_words[0].quad
            updated.append(Line(cand_text, cand_quad, float(cand_conf), cand_words))
        else:
            updated.append(line)
    from pdfmanager.ocr.layout import reading_order
    return reading_order(updated)


def _with_quality(fn, image, script, quality):
    """Call ``fn(image, script)``, passing ``quality`` only when non-standard
    so 2-argument fakes in tests stay compatible."""
    return fn(image, script) if quality == "standard" else fn(image, script, quality=quality)


def _read(image, script=None, quality="standard"):
    """The model's raw result for an image. Every switch is named each
    time: an engine keeps the last call's settings, and skew_angle() runs
    the detector alone. The per-line flip check stays off: on short crops it
    guesses wrong (a lone "6" became "9", 7 pt print turned to nonsense), and
    whole pages are turned upright by page_orientation() instead."""
    import sys
    from pdfmanager.ocr import models as _models
    arr = _array(_clear(image))
    used_gpu = default_gpu() and _directml_available()
    engine = _ocr_attr("_engine", _engine)(script, quality)
    try:
        with _call_lock:
            return engine(arr, use_det=True, use_cls=False, use_rec=True)
    except Exception as exc:
        if not used_gpu:
            raise
        logging.getLogger(__name__).warning("DirectML OCR call failed (%s); falling back to CPU", exc)
        with _engine_lock:
            _models._directml_failed = True
            _pm = sys.modules.get("pdfmanager.ocr")
            if _pm is not None:
                _pm._directml_failed = True
            for k in [k for k in _engines if k[2]]:
                _engines.pop(k, None)
        engine = _ocr_attr("_engine", _engine)(script, quality, use_gpu=False)
        with _call_lock:
            return engine(arr, use_det=True, use_cls=False, use_rec=True)


def recognise(image, script=None, quality="standard"):
    """The lines of text in a PIL image, each with its words, in reading
    order. ``script``: see script_for(); ``quality``: 'standard' or 'best'."""
    from PIL import Image

    from pdfmanager.ocr.layout import _lines, reading_order
    lines = _lines(_with_quality(_ocr_attr("_read", _read), image, script, quality))
    # Phone photos: try reading the perspective-flattened page and map boxes back.
    flat, to_photo = _straighten_photo(image)
    if flat is not None:
        flat_lines = _lines(_with_quality(_ocr_attr("_read", _read), flat, script, quality))
        if _lines_score(flat_lines) > _lines_score(lines):
            lines = reading_order(_map_lines(flat_lines, to_photo))
    # Two readings, the better kept: re-read low-confidence lines from cleaned/enlarged crops.
    lines = _retry_low_confidence(image, lines, script, quality)
    # Small print on a standalone image: upscale 2x when median text height is tiny.
    med_h = _median_text_height(lines)
    if lines and 0 < med_h < SMALL_TEXT_PX and max(image.size) * 2 <= MAX_RENDER_SIDE:
        if any(line.confidence < 0.92 for line in lines):
            enlarged = image.resize((image.width * 2, image.height * 2), Image.BICUBIC)
            big_lines = _scale_lines(_lines(_with_quality(_ocr_attr("_read", _read), enlarged, script, quality)), 0.5)
            if _lines_score(big_lines) > _lines_score(lines):
                lines = reading_order(big_lines)
    from pdfmanager.ocr.runner import _refine_tables
    return _refine_tables(image, lines, script, quality)


_ORIG_READ = _read
_ORIG_RECOGNISE = recognise
