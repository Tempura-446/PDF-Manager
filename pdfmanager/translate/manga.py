# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Offline Manga / Comic speech-bubble detection, RTL/LTR ordering, cleaning, and typesetting (v2).

Supports light and dark/inverted bubbles, shape classification (rect, oval, polygon),
pixel-accurate eroded interior masks for spiky/irregular bubbles, digital-PDF zero-OCR
extraction, OCR pre-processing, and balanced comic lettering.
100% commercial-safe pure Python + NumPy + Pillow + ReportLab (zero OpenCV/GPL).
"""

import io
import logging
from collections import deque

from PIL import Image, ImageFilter, ImageOps
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from pdfmanager import fonts
from pdfmanager.translate.layout import _shape_rtl, _wrap_text
from pdfmanager.translate.vertical import _is_cjk_char, normalize_vertical_punct

_RTL_COMIC_LANGS = {"", "auto", "ja", "zh", "zh-hans", "zh-hant", "ko"}


def default_reading_dir(src_lang):
    """Return ``'rtl'`` for East Asian manga languages and ``'ltr'`` for Western comics."""
    code = str(src_lang or "").strip().lower()
    return "rtl" if code in _RTL_COMIC_LANGS else "ltr"


def order_manga_bubbles(bubbles, *, reading_dir="rtl"):
    """Sort speech bubbles in comic reading order: top-to-bottom horizontal bands,
    right-to-left (``reading_dir='rtl'``) or left-to-right (``'ltr'``) within each band."""
    if not bubbles:
        return []
    is_ltr = str(reading_dir or "rtl").strip().lower() == "ltr"
    ordered_top = sorted(bubbles, key=lambda b: (b["y0"], b["cx"] if is_ltr else -b["cx"]))
    bands = []
    for b in ordered_top:
        placed = False
        for band in bands:
            band_y0 = min(item["y0"] for item in band)
            band_y1 = max(item["y1"] for item in band)
            band_h = sum(item["h"] for item in band) / len(band)
            if (band_y0 <= b["cy"] <= band_y1) or (abs(b["y0"] - band_y0) <= band_h * 0.45):
                band.append(b)
                placed = True
                break
        if not placed:
            bands.append([b])
    result = []
    for band in bands:
        band.sort(key=lambda item: item["cx"] if is_ltr else -item["cx"])
        result.extend(band)
    return result


def _fill_binary_holes(mask):
    """Fill interior holes (e.g. text ink inside a speech bubble) in a 2D bool mask
    using a pure-Python/NumPy exterior flood fill."""
    import numpy as np

    h, w = mask.shape
    padded = np.zeros((h + 2, w + 2), dtype=bool)
    padded[1:-1, 1:-1] = mask
    visited = np.zeros_like(padded)
    q = deque([(0, 0)])
    visited[0, 0] = True
    while q:
        y, x = q.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < h + 2 and 0 <= nx < w + 2 and not visited[ny, nx] and not padded[ny, nx]:
                visited[ny, nx] = True
                q.append((ny, nx))
    return ~visited[1:-1, 1:-1]


def _classify_bubble_shape(filled_mask):
    """Classify a hole-filled bubble mask as ``'rect'``, ``'oval'``, or ``'polygon'``."""
    h, w = filled_mask.shape
    fill_ratio = float(filled_mask.mean())
    ch = max(1, int(round(h * 0.15)))
    cw = max(1, int(round(w * 0.15)))
    corners = (
        filled_mask[:ch, :cw],
        filled_mask[:ch, w - cw:],
        filled_mask[h - ch:, :cw],
        filled_mask[h - ch:, w - cw:],
    )
    corner_occ = sum(float(c.mean()) for c in corners) / 4.0
    if fill_ratio >= 0.88 and corner_occ >= 0.65:
        return "rect"
    if fill_ratio >= 0.68:
        return "oval"
    return "polygon"


def _eroded_mask_image(filled_mask):
    """Return a Pillow ``'L'`` image of ``filled_mask`` eroded inward to preserve bubble outlines."""
    import numpy as np

    img = Image.fromarray((filled_mask.astype(np.uint8) * 255), mode="L")
    return img.filter(ImageFilter.MinFilter(size=5))


def _scan_polarity_components(gray, rgb_arr, mask, *, inverted, orig_w, orig_h):
    """Find enclosed speech-bubble candidates for one polarity (light or dark/inverted)."""
    import numpy as np

    sh, sw = gray.shape
    parent = []
    # [min_x, min_y, max_x, max_y, px_count, touches_border]
    stats = []
    label_img = np.full((sh, sw), -1, dtype=np.int32)

    def _find(idx):
        root = idx
        while parent[root] != root:
            root = parent[root]
        while parent[idx] != root:
            nxt = parent[idx]
            parent[idx] = root
            idx = nxt
        return root

    def _union(a, b):
        ra, rb = _find(a), _find(b)
        if ra == rb:
            return ra
        if ra > rb:
            ra, rb = rb, ra
        parent[rb] = ra
        sa, sb = stats[ra], stats[rb]
        if sb[0] < sa[0]:
            sa[0] = sb[0]
        if sb[1] < sa[1]:
            sa[1] = sb[1]
        if sb[2] > sa[2]:
            sa[2] = sb[2]
        if sb[3] > sa[3]:
            sa[3] = sb[3]
        sa[4] += sb[4]
        sa[5] = sa[5] or sb[5]
        return ra

    prev_runs = []
    for y in range(sh):
        row = mask[y]
        if not row.any():
            prev_runs = []
            continue
        padded = np.empty(sw + 2, dtype=np.uint8)
        padded[0] = 0
        padded[1:-1] = row
        padded[-1] = 0
        diff = np.diff(padded.astype(np.int8))
        starts = np.flatnonzero(diff == 1)
        ends = np.flatnonzero(diff == -1)

        cur_runs = []
        p_idx = 0
        p_len = len(prev_runs)
        for sx, ex in zip(starts.tolist(), ends.tolist()):
            rid = len(parent)
            parent.append(rid)
            touches = (y == 0) or (y == sh - 1) or (sx == 0) or (ex == sw)
            stats.append([sx, y, ex - 1, y, ex - sx, touches])
            label_img[y, sx:ex] = rid

            while p_idx < p_len and prev_runs[p_idx][1] <= sx:
                p_idx += 1
            j = p_idx
            while j < p_len and prev_runs[j][0] < ex:
                _union(rid, prev_runs[j][2])
                j += 1
            cur_runs.append((sx, ex, _find(rid)))
        prev_runs = cur_runs

    if not parent:
        return []

    # Resolve root lookup table for fast component mask slicing
    root_lut = np.array([_find(i) for i in range(len(parent))], dtype=np.int32)
    valid_mask = label_img >= 0
    label_img[valid_mask] = root_lut[label_img[valid_mask]]

    page_area = float(sw * sh)
    inv_sx = float(orig_w) / float(sw)
    inv_sy = float(orig_h) / float(sh)

    candidates = []
    seen_roots = set()
    for rid in range(len(parent)):
        root = int(root_lut[rid])
        if root in seen_roots:
            continue
        seen_roots.add(root)
        min_x, min_y, max_x, max_y, bg_px, touches = stats[root]
        if touches:
            continue
        bw = max_x - min_x + 1
        bh = max_y - min_y + 1
        if bw < 12 or bh < 12:
            continue
        box_area = float(bw * bh)
        area_ratio = box_area / page_area
        if not (0.0025 <= area_ratio <= 0.25):
            continue
        aspect = float(bw) / float(bh)
        if not (0.25 <= aspect <= 4.0):
            continue
        raw_solidity = float(bg_px) / box_area
        if raw_solidity < 0.30:
            continue

        # Check inner box for contrasting dialogue ink
        pad_x = max(2, int(round(bw * 0.14)))
        pad_y = max(2, int(round(bh * 0.14)))
        ix0, ix1 = min_x + pad_x, max_x - pad_x + 1
        iy0, iy1 = min_y + pad_y, max_y - pad_y + 1
        if ix1 <= ix0 + 2 or iy1 <= iy0 + 2:
            continue
        inner_gray = gray[iy0:iy1, ix0:ix1]
        if inverted:
            ink_ratio = float(np.mean(inner_gray >= 190))
        else:
            ink_ratio = float(np.mean(inner_gray < 140))
        if not (0.01 <= ink_ratio <= 0.45):
            continue

        comp_mask = label_img[min_y:max_y + 1, min_x:max_x + 1] == root
        filled = _fill_binary_holes(comp_mask)
        if float(filled.mean()) < 0.35:
            continue
        shape = _classify_bubble_shape(filled)
        poly_mask = _eroded_mask_image(filled) if shape == "polygon" else None

        # Sample interior background RGB
        inner_rgb = rgb_arr[iy0:iy1, ix0:ix1]
        bg_sel = (inner_gray <= 45) if inverted else (inner_gray >= 232)
        if bg_sel.any():
            med = np.median(inner_rgb[bg_sel], axis=0)
            bg_rgb = (float(med[0]) / 255.0, float(med[1]) / 255.0, float(med[2]) / 255.0)
        else:
            bg_rgb = (0.07, 0.07, 0.08) if inverted else (1.0, 1.0, 1.0)
        text_rgb = (0.96, 0.96, 0.98) if inverted else (0.06, 0.06, 0.08)

        ox0 = int(round(min_x * inv_sx))
        oy0 = int(round(min_y * inv_sy))
        ox1 = int(round((max_x + 1) * inv_sx))
        oy1 = int(round((max_y + 1) * inv_sy))
        candidates.append({
            "x0": ox0,
            "y0": oy0,
            "x1": ox1,
            "y1": oy1,
            "w": ox1 - ox0,
            "h": oy1 - oy0,
            "cx": (ox0 + ox1) / 2.0,
            "cy": (oy0 + oy1) / 2.0,
            "bg_rgb": bg_rgb,
            "text_rgb": text_rgb,
            "inverted": inverted,
            "shape": shape,
            "mask": poly_mask,
        })
    return candidates


def detect_speech_bubbles(pil_img, *, max_dim=480, reading_dir="rtl", detect_inverted=True):
    """Detect enclosed speech bubbles (both light and dark/inverted) on a raster manga/comic page.

    Returns a list of bubble dicts sorted in reading order:
    ``{"x0", "y0", "x1", "y1", "w", "h", "cx", "cy", "bg_rgb", "text_rgb",
       "inverted", "shape", "mask"}``.
    """
    if pil_img is None:
        return []
    orig_w, orig_h = pil_img.size
    if orig_w < 32 or orig_h < 32:
        return []

    import numpy as np

    scale = min(1.0, float(max_dim) / float(max(orig_w, orig_h)))
    sw = max(16, int(round(orig_w * scale)))
    sh = max(16, int(round(orig_h * scale)))
    small = pil_img.resize((sw, sh)) if scale < 1.0 else pil_img
    gray = np.asarray(small.convert("L"), dtype=np.uint8)
    rgb_arr = np.asarray(small.convert("RGB"), dtype=np.uint8)

    candidates = _scan_polarity_components(
        gray, rgb_arr, gray >= 232, inverted=False, orig_w=orig_w, orig_h=orig_h,
    )
    if detect_inverted:
        candidates.extend(
            _scan_polarity_components(
                gray, rgb_arr, gray <= 45, inverted=True, orig_w=orig_w, orig_h=orig_h,
            )
        )

    # Drop smaller nested boxes that sit >70% inside a larger bubble
    candidates.sort(key=lambda b: -(b["w"] * b["h"]))
    filtered = []
    for cand in candidates:
        c_area = max(1.0, float(cand["w"] * cand["h"]))
        nested = False
        for kept in filtered:
            ix0 = max(cand["x0"], kept["x0"])
            iy0 = max(cand["y0"], kept["y0"])
            ix1 = min(cand["x1"], kept["x1"])
            iy1 = min(cand["y1"], kept["y1"])
            if ix1 > ix0 and iy1 > iy0:
                inter = float((ix1 - ix0) * (iy1 - iy0))
                if inter / c_area >= 0.70:
                    nested = True
                    break
        if not nested:
            filtered.append(cand)

    return order_manga_bubbles(filtered, reading_dir=reading_dir)


def preprocess_bubble_for_ocr(crop, *, inverted=False):
    """Prepare a cropped speech bubble for offline OCR: invert dark bubbles,
    upscale small crops (min side < 160 px), normalize contrast, and add white margin."""
    img = crop.convert("RGB")
    if inverted:
        img = ImageOps.invert(img)
    w, h = img.size
    min_side = min(w, h)
    if 0 < min_side < 160:
        scale = 160.0 / float(min_side)
        img = img.resize(
            (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
            Image.Resampling.LANCZOS,
        )
    img = ImageOps.autocontrast(img, cutoff=1)
    return ImageOps.expand(img, border=12, fill=(255, 255, 255))


def extract_digital_bubble_text(plumber_page, bubble, img_w, img_h):
    """Extract selectable PDF text inside ``bubble`` directly without OCR (zero-OCR fast path).

    Handles both vertical CJK columns (with furigana stripping & RTL column ordering)
    and horizontal dialogue. Returns ``""`` when no selectable text exists inside the bubble.
    """
    if plumber_page is None:
        return ""
    try:
        raw_chars = plumber_page.chars or []
    except Exception as _exc:
        logging.getLogger("zpdf").debug("manga.extract_digital_bubble_text: %s", _exc)
        return ""
    if not raw_chars:
        return ""

    page_w = float(plumber_page.width or 1.0)
    page_h = float(plumber_page.height or 1.0)
    sx = page_w / max(1.0, float(img_w))
    sy = page_h / max(1.0, float(img_h))

    bx0 = bubble["x0"] * sx
    bx1 = bubble["x1"] * sx
    btop = bubble["y0"] * sy
    bbot = bubble["y1"] * sy

    inside = []
    for ch in raw_chars:
        txt = str(ch.get("text") or "")
        if not txt or txt.isspace():
            continue
        x0 = float(ch.get("x0", 0.0))
        x1 = float(ch.get("x1", x0))
        top = float(ch.get("top", 0.0))
        bot = float(ch.get("bottom", top))
        cx = (x0 + x1) / 2.0
        cy = (top + bot) / 2.0
        if bx0 <= cx <= bx1 and btop <= cy <= bbot:
            size = max(4.0, float(ch.get("size") or (bot - top) or 10.0))
            inside.append({"text": txt, "x0": x0, "x1": x1, "top": top, "bottom": bot, "cx": cx, "cy": cy, "size": size})
    if not inside:
        return ""

    # Strip furigana / ruby chars inside CJK bubbles
    sizes = sorted(c["size"] for c in inside)
    body_size = sizes[min(len(sizes) - 1, int(len(sizes) * 0.60))]
    cjk_count = sum(1 for c in inside if _is_cjk_char(c["text"]))
    if cjk_count >= 2:
        main_chars = [c for c in inside if c["size"] > body_size * 0.62] or inside
        span_x = max(c["x1"] for c in main_chars) - min(c["x0"] for c in main_chars)
        span_y = max(c["bottom"] for c in main_chars) - min(c["top"] for c in main_chars)
        if span_y > span_x * 1.15:
            # Vertical CJK column grouping (top-to-bottom, right-to-left)
            main_chars.sort(key=lambda c: (-c["cx"], c["top"]))
            cols = []
            for ch in main_chars:
                placed = False
                for col in cols:
                    if abs(ch["cx"] - col["cx"]) <= max(4.0, body_size * 0.55):
                        col["chars"].append(ch)
                        n = len(col["chars"])
                        col["cx"] = ((col["cx"] * (n - 1)) + ch["cx"]) / n
                        placed = True
                        break
                if not placed:
                    cols.append({"cx": ch["cx"], "chars": [ch]})
            cols.sort(key=lambda col: -col["cx"])
            text = "".join(
                "".join(c["text"] for c in sorted(col["chars"], key=lambda c: c["top"]))
                for col in cols
            )
            return normalize_vertical_punct(text).strip()

    # Horizontal ordering (top-to-bottom rows, left-to-right)
    row_bin = max(3.0, body_size * 0.55)
    inside.sort(key=lambda c: (round(c["cy"] / row_bin), c["x0"]))
    out = []
    prev = None
    for ch in inside:
        if prev is not None:
            same_row = abs(ch["cy"] - prev["cy"]) <= row_bin
            gap = ch["x0"] - prev["x1"]
            if not same_row or (gap > body_size * 0.28 and not _is_cjk_char(ch["text"])):
                out.append(" ")
        out.append(ch["text"])
        prev = ch
    return normalize_vertical_punct("".join(out)).strip()


def extract_bubble_text(pil_img, bubble, src_lang, *, ocr_fn=None, plumber_page=None):
    """Extract dialogue text from a detected speech bubble (digital PDF fast path, else OCR)."""
    if plumber_page is not None:
        digital = extract_digital_bubble_text(plumber_page, bubble, pil_img.width, pil_img.height)
        if digital:
            return digital

    bw, bh = bubble["w"], bubble["h"]
    pad_x = max(2, int(round(bw * 0.08)))
    pad_y = max(2, int(round(bh * 0.08)))
    cx0 = max(0, bubble["x0"] + pad_x)
    cy0 = max(0, bubble["y0"] + pad_y)
    cx1 = min(pil_img.width, bubble["x1"] - pad_x)
    cy1 = min(pil_img.height, bubble["y1"] - pad_y)
    if cx1 <= cx0 + 4 or cy1 <= cy0 + 4:
        return ""
    crop = pil_img.crop((cx0, cy0, cx1, cy1))
    pre = preprocess_bubble_for_ocr(crop, inverted=bool(bubble.get("inverted", False)))

    if ocr_fn is not None:
        raw = ocr_fn(pre, src_lang)
        return normalize_vertical_punct(str(raw or "")).strip()

    try:
        from pdfmanager import ocr

        script = ocr.script_for(src_lang) if src_lang and src_lang != "auto" else None
        lines = ocr.recognise(pre, script=script, quality="standard")
        raw = ocr.text_of(lines) if lines else ""
        return normalize_vertical_punct(raw).strip()
    except Exception as _exc:
        logging.getLogger("zpdf").debug("manga.extract_bubble_text: %s", _exc)
        return ""


def _wrap_balanced(text, font_name, font_size, max_w):
    """Wrap comic dialogue into balanced lines, avoiding single-word orphans when
    a slightly narrower wrap achieves the same line count."""
    base = _wrap_text(text, font_name, font_size, max_w)
    if len(base) < 2 or " " not in (text or ""):
        return base

    target_lines = len(base)
    best = base
    best_min_w = min(pdfmetrics.stringWidth(ln, font_name, font_size) for ln in base)
    for frac in (0.92, 0.85, 0.78, 0.72, 0.66, 0.60):
        cand = _wrap_text(text, font_name, font_size, max_w * frac)
        if len(cand) == target_lines:
            cand_min_w = min(pdfmetrics.stringWidth(ln, font_name, font_size) for ln in cand)
            if cand_min_w > best_min_w:
                best = cand
                best_min_w = cand_min_w
    return best


def make_manga_overlay(width, height, img_w, img_h, bubbles, translated_texts, dst_lang):
    """Create a transparent PDF overlay page that cleans each speech bubble interior
    according to its shape (``rect``, ``oval``, or pixel-accurate ``polygon`` mask)
    and typesets centered, balanced, auto-fitted dialogue inside each bubble."""
    from pypdf import PdfReader

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    sx = float(width) / max(1.0, float(img_w))
    sy = float(height) / max(1.0, float(img_h))

    for bubble, trans in zip(bubbles, translated_texts):
        text = str(trans or "").strip()
        if not text:
            continue
        display_text = _shape_rtl(text, dst_lang)

        px0 = bubble["x0"] * sx
        px1 = bubble["x1"] * sx
        py0 = height - (bubble["y1"] * sy)
        py1 = height - (bubble["y0"] * sy)
        pw = max(10.0, px1 - px0)
        ph = max(10.0, py1 - py0)

        bg_r, bg_g, bg_b = bubble.get("bg_rgb", (1.0, 1.0, 1.0))
        txt_r, txt_g, txt_b = bubble.get("text_rgb", (0.06, 0.06, 0.08))
        shape = bubble.get("shape", "oval")
        poly_mask = bubble.get("mask")

        c.saveState()
        if shape == "polygon" and poly_mask is not None:
            # Pixel-accurate RGBA mask patch preserves irregular/spiky bubble borders
            mw, mh = poly_mask.size
            rgba = Image.new(
                "RGBA",
                (mw, mh),
                (int(round(bg_r * 255)), int(round(bg_g * 255)), int(round(bg_b * 255)), 255),
            )
            rgba.putalpha(poly_mask)
            pbuf = io.BytesIO()
            rgba.save(pbuf, format="PNG")
            pbuf.seek(0)
            c.drawImage(ImageReader(pbuf), px0, py0, width=pw, height=ph, mask="auto")
            inset_y = ph * 0.10
        elif shape == "rect":
            inset_x = pw * 0.05
            inset_y = ph * 0.05
            c.setFillColorRGB(bg_r, bg_g, bg_b)
            c.roundRect(
                px0 + inset_x, py0 + inset_y,
                max(2.0, pw - 2.0 * inset_x), max(2.0, ph - 2.0 * inset_y),
                radius=min(pw, ph) * 0.06, stroke=0, fill=1,
            )
        else:
            inset_x = pw * 0.08
            inset_y = ph * 0.08
            c.setFillColorRGB(bg_r, bg_g, bg_b)
            c.ellipse(px0 + inset_x, py0 + inset_y, px1 - inset_x, py1 - inset_y, stroke=0, fill=1)
        c.restoreState()

        # Inscribed dialogue box tailored to bubble shape
        w_frac, h_frac = (0.84, 0.80) if shape == "rect" else (0.68, 0.66)
        inner_w = max(12.0, pw * w_frac)
        inner_h = max(12.0, ph * h_frac)
        chosen_font = fonts.font_for(display_text, family="sans", bold=True)

        max_sz = max(7.0, min(13.0, ph * 0.19))
        best_sz = 6.0
        best_lines = [display_text]
        for step in range(10):
            cand_sz = max(6.0, max_sz * (1.0 - step * 0.09))
            lines = _wrap_balanced(display_text, chosen_font, cand_sz, inner_w)
            leading = cand_sz * 1.22
            if len(lines) * leading <= inner_h:
                best_sz = cand_sz
                best_lines = lines
                break
            best_sz = cand_sz
            best_lines = lines

        leading = best_sz * 1.22
        total_h = len(best_lines) * leading
        center_x = (px0 + px1) / 2.0
        center_y = (py0 + py1) / 2.0
        start_y = center_y + (total_h / 2.0) - best_sz * 0.85

        c.saveState()
        c.setFillColorRGB(txt_r, txt_g, txt_b)
        c.setFont(chosen_font, best_sz)
        for idx, line in enumerate(best_lines):
            ly = start_y - idx * leading
            if ly < py0 + inset_y:
                break
            lw = pdfmetrics.stringWidth(line, chosen_font, best_sz)
            if lw > inner_w and lw > 0:
                fit_sz = max(5.5, best_sz * (inner_w / lw))
                c.setFont(chosen_font, fit_sz)
                c.drawCentredString(center_x, ly, line)
                c.setFont(chosen_font, best_sz)
            else:
                c.drawCentredString(center_x, ly, line)
        c.restoreState()

    c.showPage()
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]
