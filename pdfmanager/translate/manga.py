# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Offline Manga / Comic speech-bubble detection, RTL ordering, cleaning, and typesetting.

Uses pure Python + NumPy + Pillow + ReportLab (100% commercial-safe, zero OpenCV/GPL).
"""

import io
import logging

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from pdfmanager import fonts
from pdfmanager.translate.layout import _shape_rtl, _wrap_text
from pdfmanager.translate.vertical import normalize_vertical_punct


def order_manga_bubbles(bubbles):
    """Sort speech bubbles in manga reading order: top-to-bottom horizontal bands,
    right-to-left (``-cx``) within each band."""
    if not bubbles:
        return []
    ordered_top = sorted(bubbles, key=lambda b: (b["y0"], -b["cx"]))
    bands = []
    for b in ordered_top:
        placed = False
        for band in bands:
            band_y0 = min(item["y0"] for item in band)
            band_y1 = max(item["y1"] for item in band)
            band_h = sum(item["h"] for item in band) / len(band)
            # Same horizontal reading band if vertical centers overlap or tops are close
            if (band_y0 <= b["cy"] <= band_y1) or (abs(b["y0"] - band_y0) <= band_h * 0.45):
                band.append(b)
                placed = True
                break
        if not placed:
            bands.append([b])
    result = []
    for band in bands:
        band.sort(key=lambda item: -item["cx"])
        result.extend(band)
    return result


def detect_speech_bubbles(pil_img, *, max_dim=480):
    """Detect enclosed bright speech bubbles on a raster manga/comic page.

    Returns a list of bubble dicts sorted in manga reading order (RTL within bands):
    ``{"x0", "y0", "x1", "y1", "w", "h", "cx", "cy", "bg_rgb"}`` in full-resolution
    pixel coordinates of ``pil_img``.
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

    # Bright interior mask (speech bubble paper is typically white/near-white)
    bright = gray >= 232

    # Fast row-run connected-component labeling via Disjoint-Set Union
    parent = []
    # Component stats: [min_x, min_y, max_x, max_y, bright_pixels, touches_border]
    stats = []

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

    prev_runs = []  # list of (start_x, end_x_excl, run_id)
    for y in range(sh):
        row = bright[y]
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

            # Advance previous-row pointer to first run that could overlap [sx, ex)
            while p_idx < p_len and prev_runs[p_idx][1] <= sx:
                p_idx += 1
            j = p_idx
            while j < p_len and prev_runs[j][0] < ex:
                _union(rid, prev_runs[j][2])
                j += 1
            cur_runs.append((sx, ex, _find(rid)))
        prev_runs = cur_runs

    # Filter unique roots for enclosed speech-bubble candidates
    page_area = float(sw * sh)
    inv_sx = float(orig_w) / float(sw)
    inv_sy = float(orig_h) / float(sh)
    rgb_arr = np.asarray(small.convert("RGB"), dtype=np.uint8)

    candidates = []
    seen_roots = set()
    for rid in range(len(parent)):
        root = _find(rid)
        if root in seen_roots:
            continue
        seen_roots.add(root)
        min_x, min_y, max_x, max_y, bright_px, touches = stats[root]
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
        solidity = float(bright_px) / box_area
        if solidity < 0.40:
            continue

        # Check inner box for dark dialogue ink (rejects empty white panels & black blobs)
        pad_x = max(2, int(round(bw * 0.14)))
        pad_y = max(2, int(round(bh * 0.14)))
        ix0, ix1 = min_x + pad_x, max_x - pad_x + 1
        iy0, iy1 = min_y + pad_y, max_y - pad_y + 1
        if ix1 <= ix0 + 2 or iy1 <= iy0 + 2:
            continue
        inner_gray = gray[iy0:iy1, ix0:ix1]
        ink_ratio = float(np.mean(inner_gray < 140))
        if not (0.01 <= ink_ratio <= 0.45):
            continue

        # Sample bright interior RGB so off-white/cream manga paper blends cleanly
        inner_rgb = rgb_arr[iy0:iy1, ix0:ix1]
        bright_mask = inner_gray >= 232
        if bright_mask.any():
            med = np.median(inner_rgb[bright_mask], axis=0)
            bg_rgb = (float(med[0]) / 255.0, float(med[1]) / 255.0, float(med[2]) / 255.0)
        else:
            bg_rgb = (1.0, 1.0, 1.0)

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
        })

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

    return order_manga_bubbles(filtered)


def extract_bubble_text(pil_img, bubble, src_lang, *, ocr_fn=None):
    """Extract dialogue text from a detected speech bubble crop."""
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

    if ocr_fn is not None:
        raw = ocr_fn(crop, src_lang)
        return normalize_vertical_punct(str(raw or "")).strip()

    try:
        from pdfmanager import ocr

        script = ocr.script_for(src_lang) if src_lang and src_lang != "auto" else None
        lines = ocr.recognise(crop, script=script, quality="standard")
        raw = ocr.text_of(lines) if lines else ""
        return normalize_vertical_punct(raw).strip()
    except Exception as _exc:
        logging.getLogger("zpdf").debug("manga.extract_bubble_text: %s", _exc)
        return ""


def make_manga_overlay(width, height, img_w, img_h, bubbles, translated_texts, dst_lang):
    """Create a transparent PDF overlay page that cleans each speech bubble interior
    (preserving the artist's black outline & surrounding artwork) and typesets
    centered, auto-fitted translated dialogue inside each bubble."""
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

        # Clean strictly inside an inset ellipse so the black bubble border stays intact
        inset_x = pw * 0.08
        inset_y = ph * 0.08
        bg_r, bg_g, bg_b = bubble.get("bg_rgb", (1.0, 1.0, 1.0))
        c.saveState()
        c.setFillColorRGB(bg_r, bg_g, bg_b)
        c.ellipse(px0 + inset_x, py0 + inset_y, px1 - inset_x, py1 - inset_y, stroke=0, fill=1)
        c.restoreState()

        # Typeset centered dialogue inside the inscribed box of the ellipse
        inner_w = max(12.0, pw * 0.68)
        inner_h = max(12.0, ph * 0.66)
        chosen_font = fonts.font_for(display_text, family="sans", bold=True)

        max_sz = max(7.0, min(13.0, ph * 0.19))
        best_sz = 6.0
        best_lines = [display_text]
        for step in range(10):
            cand_sz = max(6.0, max_sz * (1.0 - step * 0.09))
            lines = _wrap_text(display_text, chosen_font, cand_sz, inner_w)
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
        c.setFillColorRGB(0.06, 0.06, 0.08)
        c.setFont(chosen_font, best_sz)
        for idx, line in enumerate(best_lines):
            ly = start_y - idx * leading
            if ly < py0 + inset_y:
                break
            # Clamp single-line width gently if a long word exceeds inner_w
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
