# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""PDF layout extraction, text stripping, RTL shaping, bilingual pages, and translated overlays."""

import logging
import io
import re
from pathlib import Path

from pypdf import PageObject, PdfWriter, Transformation
from pypdf.generic import NameObject, TextStringObject
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from pdfmanager import core, fonts, i18n
from pdfmanager.translate.engines import _BULLET_LINE_RE
from pdfmanager.translate.text import translate_texts

def _wrap_text(text, font_name, font_size, max_w):
    """Wrap ``text`` to ``max_w`` points, handling both space-delimited Latin
    text and spaceless CJK scripts (Chinese, Japanese, Korean)."""
    if not text:
        return [""]
    lines = []
    # Tokenize into CJK single chars, whitespace, or non-CJK words
    parts = re.findall(r"[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef\uac00-\ud7af]|\s+|[^\s\u3000-\u9fff\uf900-\ufaff\uff00-\uffef\uac00-\ud7af]+", text)
    cur = ""
    for part in parts:
        if not cur and part.isspace():
            continue
        cand = cur + part
        if pdfmetrics.stringWidth(cand, font_name, font_size) <= max_w:
            cur = cand
            continue
        if cur.strip():
            lines.append(cur.rstrip())
            cur = ""
        if part.isspace():
            continue
        if pdfmetrics.stringWidth(part, font_name, font_size) <= max_w:
            cur = part
        else:
            # Break an overlong token (e.g. URL) by character
            chunk = ""
            for ch in part:
                if chunk and pdfmetrics.stringWidth(chunk + ch, font_name, font_size) > max_w:
                    lines.append(chunk)
                    chunk = ch
                else:
                    chunk += ch
            cur = chunk
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [""]


def _make_text_page(width, height, text, font_name=None, font_size=10):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    margin = 36
    max_w = max(72, width - 2 * margin)
    avail_h = max(72, height - 2 * margin)
    chosen_font = font_name or fonts.font_for(text or "")
    paras = (text or "").split("\n\n")

    # Auto-fit font size down gently if a dense translated page would overflow
    size = float(font_size)
    wrapped = []
    for candidate_size in (size, min(size, 9.0), min(size, 8.0), min(size, 7.0)):
        leading = candidate_size * 1.4
        wrapped = [_wrap_text(p, chosen_font, candidate_size, max_w) for p in paras]
        needed_h = sum(len(lines) * leading + leading * 0.45 for lines in wrapped)
        size = candidate_size
        if needed_h <= avail_h:
            break

    y = height - margin
    leading = size * 1.4
    c.setFont(chosen_font, size)
    for lines in wrapped:
        for line in lines:
            if y < margin:
                break
            c.drawString(margin, y, line)
            y -= leading
        y -= leading * 0.45
    c.showPage()
    c.save()
    buf.seek(0)
    from pypdf import PdfReader
    return PdfReader(buf).pages[0]


def _translate_annotations_and_fields(writer, src_lang, dst_lang, translator, selected_pages=None):
    """Translate annotation /Contents and text form field values in-place on ``writer``."""
    targets = []
    for page_idx, page in enumerate(writer.pages):
        if selected_pages is not None and page_idx not in selected_pages:
            continue
        annots = page.get("/Annots")
        if hasattr(annots, "get_object"):
            annots = annots.get_object()
        for ref in annots or ():
            annot = ref.get_object() if hasattr(ref, "get_object") else ref
            if not hasattr(annot, "get"):
                continue
            subtype = str(annot.get("/Subtype", ""))
            if subtype == "/Widget":
                ft = str(annot.get("/FT", ""))
                val = annot.get("/V")
                if ft == "/Tx" and val and str(val).strip():
                    targets.append((annot, NameObject("/V"), str(val)))
            else:
                contents = annot.get("/Contents")
                if contents and str(contents).strip():
                    targets.append((annot, NameObject("/Contents"), str(contents)))
    if not targets:
        return 0
    translated = translate_texts([t[2] for t in targets], src_lang, dst_lang, translator=translator)
    for (obj, key, _), new_val in zip(targets, translated):
        obj[key] = TextStringObject(new_val)
    return len(targets)


def _translate_outlines(writer, src_lang, dst_lang, translator):
    """Translate PDF bookmark / outline titles (``/Outlines``) in-place."""
    root = writer.root_object
    outlines = root.get("/Outlines")
    if hasattr(outlines, "get_object"):
        outlines = outlines.get_object()
    if not outlines or not hasattr(outlines, "get"):
        return 0
    items = []
    visited = set()

    def _walk(node):
        while node is not None:
            obj = node.get_object() if hasattr(node, "get_object") else node
            if not hasattr(obj, "get") or id(obj) in visited:
                break
            visited.add(id(obj))
            title = obj.get("/Title")
            if title and str(title).strip():
                items.append((obj, str(title)))
            if "/First" in obj:
                _walk(obj["/First"])
            node = obj.get("/Next")

    if "/First" in outlines:
        _walk(outlines["/First"])
    if not items:
        return 0
    translated = translate_texts([t for _, t in items], src_lang, dst_lang, translator=translator)
    for (obj, _), new_title in zip(items, translated):
        obj[NameObject("/Title")] = TextStringObject(new_title)
    return len(items)


_MATH_SYMBOL_RE = re.compile(r"[∑∫∏√∞≈≠≤≥±×÷∂∇∈∉⊂⊃∪∩∀∃⇒⇔→←αβγδεζηθλμπρστφψωΔΣΩ]")
_CODE_SYNTAX_RE = re.compile(r"(?:[{}\[\];]|=>|->|==|!=|::|\b(?:def|class|return|import|const|let|var|func|SELECT|FROM|WHERE)\b)")
_EQUATION_RE = re.compile(r"(?:^|\s)[A-Za-z]\s*=\s*[-+0-9A-Za-z(]")
_RTL_RUN_RE = re.compile(r"([\u0590-\u08ff\ufb50-\ufdff\ufe70-\ufeff]+)")


def _is_code_or_math_block(blk):
    """Return True when ``blk`` is a monospace code snippet or mathematical
    formula that should pass through verbatim without machine translation."""
    text = str(blk.get("text") or "").strip()
    if not text:
        return False
    if blk.get("family") == "mono" and (_CODE_SYNTAX_RE.search(text) or text.count("_") >= 2):
        return True
    if len(_MATH_SYMBOL_RE.findall(text)) >= 2:
        return True
    if _MATH_SYMBOL_RE.search(text) and _EQUATION_RE.search(text) and len(text.split()) <= 10:
        return True
    return False


def _shape_rtl(text, dst_lang):
    """Apply BiDi visual ordering (and Arabic contextual glyph shaping when
    ``arabic_reshaper`` / ``python-bidi`` are installed) for RTL target languages."""
    if not text or i18n.language_dir(dst_lang) != "rtl":
        return text
    out = str(text)
    try:
        import arabic_reshaper  # type: ignore[import-not-found]
        out = arabic_reshaper.reshape(out)
    except Exception as _exc:  # noqa: BLE001
        logging.getLogger("zpdf").debug("layout._shape_rtl: %s", _exc)
    try:
        from bidi.algorithm import get_display  # type: ignore[import-not-found]
        return get_display(out)
    except Exception:  # noqa: BLE001
        # Pure-Python fallback: reverse token order for RTL display while keeping
        # embedded LTR runs (numbers, URLs, Latin words) reading left-to-right.
        tokens = re.findall(r"[\u0590-\u08ff\ufb50-\ufdff\ufe70-\ufeff]+|[^\u0590-\u08ff\ufb50-\ufdff\ufe70-\ufeff]+", out)
        visual = [tok[::-1] if _RTL_RUN_RE.fullmatch(tok) else tok for tok in reversed(tokens)]
        return "".join(visual)


def _detect_bold_prefix(words, block_bold):
    """Return True when a non-bold block starts with a short bold lead-in label
    ending in ``:`` or ``：`` (e.g. ``Note: Do not remove``)."""
    if block_bold or len(words) < 2:
        return False
    for idx, w in enumerate(words[:4]):
        fn = str(w.get("fontname") or "").lower()
        is_b = any(k in fn for k in ("bold", "heavy", "black", "demi", "semibold"))
        if not is_b:
            return False
        if str(w.get("text") or "").rstrip().endswith((":", "：")):
            return idx + 1 < len(words)
    return False


def _detect_rotation(words):
    """Infer block rotation angle (``0``, ``90``, or ``270``) from pdfplumber words."""
    non_upright = [w for w in words if w.get("upright") is False]
    if len(non_upright) <= len(words) // 2:
        return 0
    direction = str(non_upright[0].get("direction") or "").lower()
    return 270 if direction == "ttb" else 90


_STANDALONE_PAGENUM_RE = re.compile(r"^(?:[-–—]\s*)?(?:p\.?\s*)?(?:\d{1,5}|[ivxlcdm]{1,8})(?:\s*[-–—])?$", re.I)


def _detect_running_margin_texts(pages_blocks, pages_heights):
    """Identify repetitive book running headers/footers appearing in the top or
    bottom 8% margin band across 2+ pages so reflowed bilingual reading isn't
    interrupted by repeated chapter titles or page numbers."""
    from collections import Counter

    counts = Counter()
    for blks, h in zip(pages_blocks, pages_heights):
        if not h:
            continue
        seen_on_page = set()
        for blk in blks:
            top = float(blk.get("top") or 0.0)
            bottom = float(blk.get("bottom") or top)
            in_band = (bottom <= h * 0.09) or (top >= h * 0.91)
            if not in_band:
                continue
            txt = re.sub(r"\s+", " ", str(blk.get("text") or "")).strip()
            if txt and len(txt) <= 90:
                norm = re.sub(r"\d+", "#", txt).lower()
                seen_on_page.add(norm)
        for norm in seen_on_page:
            counts[norm] += 1
    return {k for k, v in counts.items() if v >= 2}


def _is_running_margin_block(blk, page_h, repeated_margin_texts):
    if not page_h:
        return False
    top = float(blk.get("top") or 0.0)
    bottom = float(blk.get("bottom") or top)
    in_band = (bottom <= page_h * 0.09) or (top >= page_h * 0.91)
    if not in_band:
        return False
    txt = re.sub(r"\s+", " ", str(blk.get("text") or "")).strip()
    if not txt:
        return True
    if _STANDALONE_PAGENUM_RE.match(txt):
        return True
    if len(txt) <= 90 and re.sub(r"\d+", "#", txt).lower() in repeated_margin_texts:
        return True
    return False


def _make_bilingual_pages(width, height, blocks, translated_texts, dst_lang, page_num=None):
    """Build clean book-styled paginated bilingual-stacked PDF pages where every
    original paragraph is followed directly underneath by its translation with a
    subtle left accent bar, preserved heading hierarchy, and quiet page reference."""
    from pypdf import PdfReader

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    margin_x = min(50.0, max(36.0, width * 0.08))
    margin_top = 44.0
    margin_bot = 42.0
    max_w = max(72.0, width - 2 * margin_x)
    trans_indent = 10.0
    trans_max_w = max(64.0, max_w - trans_indent)
    y = height - margin_top
    is_rtl = i18n.language_dir(dst_lang) == "rtl"

    def _draw_footer():
        if page_num is None:
            return
        c.saveState()
        c.setFillColorRGB(0.52, 0.54, 0.58)
        c.setFont("Helvetica", 8.0)
        c.drawCentredString(width / 2.0, 20.0, f"Original p. {page_num}")
        c.restoreState()

    def _new_page():
        nonlocal y
        _draw_footer()
        c.showPage()
        y = height - margin_top

    for blk, trans in zip(blocks, translated_texts):
        orig_text = str(blk.get("text") or "").strip()
        trans_text = str(trans or "").strip()
        if not orig_text and not trans_text:
            continue
        raw_size = float(blk.get("size") or 10.5)
        is_heading = bool(blk.get("bold", False)) or raw_size >= 13.5
        base_size = max(9.0, min(18.0 if is_heading else 13.0, raw_size))
        orig_font = fonts.font_for(orig_text, family=blk.get("family", "serif"), bold=bool(blk.get("bold", False)))
        orig_lines = _wrap_text(orig_text, orig_font, base_size, max_w)
        orig_leading = base_size * (1.28 if is_heading else 1.38)

        same_verbatim = (orig_text == trans_text) or not trans_text
        trans_display = _shape_rtl(trans_text, dst_lang) if not same_verbatim else ""
        trans_font = fonts.font_for(trans_display, bold=is_heading) if trans_display else orig_font
        trans_size = max(8.8, min(16.0 if is_heading else 12.2, base_size * 0.95))
        trans_lines = _wrap_text(trans_display, trans_font, trans_size, trans_max_w) if trans_display else []
        trans_leading = trans_size * (1.30 if is_heading else 1.40)

        if is_heading and y < height - margin_top - 8.0:
            y -= base_size * 0.45

        # Keep at least the heading/first lines of a pair together on a page
        min_block_h = orig_leading + (trans_leading if trans_lines else 0) + 8.0
        if y - min_block_h < margin_bot:
            _new_page()

        c.setFillColorRGB(0.10, 0.11, 0.13)
        c.setFont(orig_font, base_size)
        for line in orig_lines:
            if y - orig_leading < margin_bot:
                _new_page()
                c.setFillColorRGB(0.10, 0.11, 0.13)
                c.setFont(orig_font, base_size)
            c.drawString(margin_x, y, line)
            y -= orig_leading

        if trans_lines:
            y -= 3.0
            bar_top = y + trans_size * 0.85
            c.setFillColorRGB(0.13, 0.27, 0.62)  # crisp book-slate accent for translation
            c.setFont(trans_font, trans_size)
            seg_top = bar_top
            for line in trans_lines:
                if y - trans_leading < margin_bot:
                    # Close vertical accent bar on current page before page break
                    c.saveState()
                    c.setStrokeColorRGB(0.68, 0.76, 0.92)
                    c.setLineWidth(1.5)
                    bar_x = (width - margin_x - 3.0) if is_rtl else (margin_x + 2.5)
                    c.line(bar_x, seg_top, bar_x, y + trans_leading * 0.6)
                    c.restoreState()
                    _new_page()
                    seg_top = y + trans_size * 0.85
                    c.setFillColorRGB(0.13, 0.27, 0.62)
                    c.setFont(trans_font, trans_size)
                if is_rtl:
                    c.drawRightString(width - margin_x - trans_indent, y, line)
                else:
                    c.drawString(margin_x + trans_indent, y, line)
                y -= trans_leading
            c.saveState()
            c.setStrokeColorRGB(0.68, 0.76, 0.92)
            c.setLineWidth(1.5)
            bar_x = (width - margin_x - 3.0) if is_rtl else (margin_x + 2.5)
            c.line(bar_x, seg_top, bar_x, y + trans_leading * 0.7)
            c.restoreState()
        y -= base_size * (0.75 if is_heading else 0.70)

    _draw_footer()
    c.showPage()
    c.save()
    buf.seek(0)
    return list(PdfReader(buf).pages)


def _normalize_color(raw):
    """Convert a pdfplumber ``non_stroking_color`` value to an ``(r, g, b)`` 0..1 tuple."""
    if raw is None:
        return (0.0, 0.0, 0.0)
    if isinstance(raw, (int, float)):
        g = max(0.0, min(1.0, float(raw)))
        return (g, g, g)
    if isinstance(raw, (list, tuple)):
        if len(raw) == 1:
            g = max(0.0, min(1.0, float(raw[0])))
            return (g, g, g)
        if len(raw) == 3:
            return tuple(max(0.0, min(1.0, float(v))) for v in raw)
        if len(raw) == 4:
            c, m, y, k = (max(0.0, min(1.0, float(v))) for v in raw)
            return ((1.0 - c) * (1.0 - k), (1.0 - m) * (1.0 - k), (1.0 - y) * (1.0 - k))
    return (0.0, 0.0, 0.0)


def _luminance(rgb):
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _sample_bg_rgb(bg_image, x0, top, x1, bottom, page_w, page_h):
    """Sample median background RGB around a block's border from a rendered page image."""
    if bg_image is None or page_w <= 0 or page_h <= 0:
        return (1.0, 1.0, 1.0)
    iw, ih = bg_image.size
    sx, sy = iw / page_w, ih / page_h
    px0 = max(0, min(iw - 1, int(x0 * sx) - 2))
    px1 = max(0, min(iw - 1, int(x1 * sx) + 2))
    py0 = max(0, min(ih - 1, int(top * sy) - 2))
    py1 = max(0, min(ih - 1, int(bottom * sy) + 2))
    samples = []
    step_x = max(1, (px1 - px0) // 12)
    step_y = max(1, (py1 - py0) // 6)
    for x in range(px0, px1 + 1, step_x):
        samples.append(bg_image.getpixel((x, py0)))
        samples.append(bg_image.getpixel((x, py1)))
    for y in range(py0, py1 + 1, step_y):
        samples.append(bg_image.getpixel((px0, y)))
        samples.append(bg_image.getpixel((px1, y)))
    if not samples:
        return (1.0, 1.0, 1.0)
    rs = sorted(p[0] for p in samples)
    gs = sorted(p[1] for p in samples)
    bs = sorted(p[2] for p in samples)
    mid = len(samples) // 2
    return (rs[mid] / 255.0, gs[mid] / 255.0, bs[mid] / 255.0)


def _detect_font_style(words):
    """Infer ``(family, bold, italic)`` from pdfplumber word ``fontname`` attributes."""
    bold_hits = 0
    italic_hits = 0
    serif_hits = 0
    mono_hits = 0
    for w in words:
        fn = str(w.get("fontname") or "").lower()
        if any(k in fn for k in ("bold", "heavy", "black", "demi", "semibold", "medi")):
            bold_hits += 1
        if any(k in fn for k in ("italic", "oblique", "slant")):
            italic_hits += 1
        if any(k in fn for k in ("mono", "courier", "consolas", "menlo")):
            mono_hits += 1
        elif any(k in fn for k in ("times", "serif", "georgia", "garamond", "cambria", "mincho", "song")):
            serif_hits += 1
    half = max(1, len(words) // 2)
    bold = bold_hits >= half
    italic = italic_hits >= half
    if mono_hits >= half:
        family = "mono"
    elif serif_hits >= half:
        family = "serif"
    else:
        family = "sans"
    return family, bold, italic


def _detect_align(x0, x1, container_x0, container_x1, *, is_cell=False, lines=1, solo_row=True):
    """Detect ``left``, ``center``, or ``right`` alignment inside a cell or page."""
    span_w = max(1.0, container_x1 - container_x0)
    left_m = x0 - container_x0
    right_m = container_x1 - x1
    if is_cell:
        if left_m > 4.0 and right_m > 4.0 and abs(left_m - right_m) <= max(4.0, span_w * 0.08):
            return "center"
        if right_m <= max(16.0, span_w * 0.12) and left_m > right_m + 10.0:
            return "right"
        return "left"
    if lines == 1 and solo_row:
        cx = (x0 + x1) / 2.0
        page_cx = (container_x0 + container_x1) / 2.0
        if (
            abs(cx - page_cx) <= 12.0
            and abs(left_m - right_m) <= 24.0
            and left_m > span_w * 0.12
            and right_m > span_w * 0.12
        ):
            return "center"
    return "left"


def _extract_layout_blocks(plumber_page):
    """Extract positioned text blocks from a ``pdfplumber`` page, respecting
    real multi-row/multi-column table cells, columns, headings, font sizes,
    bold/italic styles, and left/center/right alignment."""
    words = plumber_page.extract_words(
        extra_attrs=["size", "fontname", "non_stroking_color"],
        keep_blank_chars=False,
        use_text_flow=False,
    )
    if not words:
        return []

    page_w = float(plumber_page.width or 612.0)
    page_h = float(plumber_page.height or 792.0)

    # 1. Detect genuine multi-row, multi-column tables (ignore 1x1 page/section border boxes)
    table_cells = []
    try:
        for tbl in plumber_page.find_tables() or ():
            if len(getattr(tbl, "rows", ()) or ()) < 2 or len(getattr(tbl, "columns", ()) or ()) < 2:
                continue
            for cell in tbl.cells or ():
                if cell and len(cell) == 4:
                    cx0, ctop, cx1, cbottom = (float(v) for v in cell)
                    cw, ch = cx1 - cx0, cbottom - ctop
                    if 8.0 <= cw <= page_w * 0.80 and 6.0 <= ch <= page_h * 0.25:
                        table_cells.append((cx0, ctop, cx1, cbottom))
    except Exception:  # noqa: BLE001
        table_cells = []

    cell_words = {}
    free_words = []
    for w in words:
        wx = (float(w["x0"]) + float(w["x1"])) / 2.0
        wy = (float(w["top"]) + float(w["bottom"])) / 2.0
        assigned = None
        for ci, (cx0, ctop, cx1, cbottom) in enumerate(table_cells):
            if cx0 - 1.0 <= wx <= cx1 + 1.0 and ctop - 1.0 <= wy <= cbottom + 1.0:
                assigned = ci
                break
        if assigned is not None:
            cell_words.setdefault(assigned, []).append(w)
        else:
            free_words.append(w)

    blocks = []

    # Build one strictly-bounded block per genuine homogeneous table cell;
    # if a cell contains mixed font sizes (e.g. heading + body) or large paragraph gaps,
    # return its words to free_words so every sub-block keeps its exact position.
    for ci, cwords in sorted(cell_words.items()):
        cx0, ctop, cx1, cbottom = table_cells[ci]
        cwords.sort(key=lambda w: (round(float(w["top"]) / 2.5), float(w["x0"])))
        sizes = [max(4.0, float(w.get("size") or 10.0)) for w in cwords]
        min_sz, max_sz = min(sizes), max(sizes)
        avg_size = sum(sizes) / len(sizes)
        if (max_sz - min_sz) > max(2.0, min_sz * 0.25):
            free_words.extend(cwords)
            continue
        # Check for large internal vertical gaps (multi-paragraph container box)
        has_big_gap = False
        prev_bot = float(cwords[0]["bottom"])
        for cw in cwords[1:]:
            if float(cw["top"]) - prev_bot > max(12.0, avg_size * 1.4):
                has_big_gap = True
                break
            prev_bot = max(prev_bot, float(cw["bottom"]))
        if has_big_gap:
            free_words.extend(cwords)
            continue

        family, bold, italic = _detect_font_style(cwords)
        wx0 = min(float(w["x0"]) for w in cwords)
        wx1 = max(float(w["x1"]) for w in cwords)
        wtop = min(float(w["top"]) for w in cwords)
        wbot = max(float(w["bottom"]) for w in cwords)
        line_count = max(1, int(round((wbot - wtop) / max(1.0, avg_size * 1.15))))
        align = _detect_align(wx0, wx1, cx0, cx1, is_cell=True, lines=line_count)
        blocks.append({
            "text": " ".join(w["text"] for w in cwords),
            "x0": max(cx0 + 2.0, wx0 if align == "left" else cx0 + 2.0),
            "top": max(ctop + 1.0, wtop),
            "x1": min(cx1 - 2.0, wx1 if align == "right" else cx1 - 2.0),
            "bottom": min(cbottom - 1.0, max(wbot, cbottom - 1.0)),
            "box_x0": cx0 + 2.0,
            "box_x1": cx1 - 2.0,
            "size": avg_size,
            "color": _normalize_color(cwords[0].get("non_stroking_color")),
            "family": family,
            "bold": bold,
            "italic": italic,
            "align": align,
            "lines": line_count,
            "in_table": True,
        })

    if not free_words:
        blocks.sort(key=lambda b: (b["top"], b["x0"]))
        return blocks

    # 2. Group non-table words into visual lines by vertical top coordinate
    free_words.sort(key=lambda w: (round(float(w["top"]) / 2.5), float(w["x0"])))
    raw_lines = []
    for w in free_words:
        w_top = float(w["top"])
        w_size = max(4.0, float(w.get("size") or 10.0))
        if raw_lines and abs(w_top - raw_lines[-1]["top"]) <= max(3.0, raw_lines[-1]["size"] * 0.45):
            raw_lines[-1]["words"].append(w)
        else:
            raw_lines.append({"top": w_top, "size": w_size, "words": [w]})

    # 3. Split each visual line on large horizontal gaps (separates columns)
    seg_objs = []
    for ln in raw_lines:
        lwords = sorted(ln["words"], key=lambda w: float(w["x0"]))
        line_segs = []
        cur = [lwords[0]]
        for w in lwords[1:]:
            prev = cur[-1]
            prev_size = max(4.0, float(prev.get("size") or 10.0))
            gap = float(w["x0"]) - float(prev["x1"])
            if gap > max(18.0, prev_size * 1.8):
                line_segs.append(cur)
                cur = [w]
            else:
                cur.append(w)
        line_segs.append(cur)
        solo_row = len(line_segs) == 1
        for seg in line_segs:
            sizes = [max(4.0, float(w.get("size") or 10.0)) for w in seg]
            avg_size = sum(sizes) / len(sizes)
            family, bold, italic = _detect_font_style(seg)
            sx0 = min(float(w["x0"]) for w in seg)
            sx1 = max(float(w["x1"]) for w in seg)
            seg_objs.append({
                "text": " ".join(w["text"] for w in seg),
                "x0": sx0,
                "top": min(float(w["top"]) for w in seg),
                "x1": sx1,
                "bottom": max(float(w["bottom"]) for w in seg),
                "box_x0": sx0,
                "box_x1": sx1,
                "size": avg_size,
                "color": _normalize_color(seg[0].get("non_stroking_color")),
                "family": family,
                "bold": bold,
                "italic": italic,
                "bold_prefix": _detect_bold_prefix(seg, bold),
                "rotation": _detect_rotation(seg),
                "align": _detect_align(sx0, sx1, 0.0, page_w, is_cell=False, lines=1, solo_row=solo_row),
                "lines": 1,
                "in_table": False,
            })

    # 4. Merge vertically adjacent line segments in the same column & style into blocks
    seg_objs.sort(key=lambda s: (s["top"], s["x0"]))
    free_blocks = []
    for seg in seg_objs:
        merged = False
        if not _BULLET_LINE_RE.match(seg["text"]) and not seg.get("rotation"):
            for blk in reversed(free_blocks):
                if blk.get("rotation"):
                    continue
                v_gap = seg["top"] - blk["bottom"]
                if v_gap > max(7.0, blk["size"] * 0.85):
                    continue
                if v_gap < -blk["size"] * 0.35:
                    continue
                same_size = abs(seg["size"] - blk["size"]) <= max(1.0, blk["size"] * 0.15)
                # Allow typical first-line paragraph indent (up to 36pt) on line 1 -> line 2
                left_tol = max(36.0, blk["size"] * 3.0) if blk["lines"] == 1 else max(18.0, blk["size"] * 2.0)
                same_left = abs(seg["x0"] - blk["x0"]) <= left_tol
                horiz_overlap = min(seg["x1"], blk["x1"]) > max(seg["x0"], blk["x0"])
                blk_w = blk["x1"] - blk["x0"]
                short_heading = blk["lines"] == 1 and blk_w < min(140.0, page_w * 0.28) and blk["text"][-1] not in ",;"
                if same_size and same_left and horiz_overlap and not short_heading:
                    prev_text = blk["text"]
                    nxt_text = seg["text"]
                    if prev_text.endswith("-") and len(prev_text) >= 2 and prev_text[-2].isalpha() and nxt_text[0].islower():
                        blk["text"] = prev_text[:-1] + nxt_text
                    else:
                        blk["text"] = prev_text + " " + nxt_text
                    blk["x0"] = min(blk["x0"], seg["x0"])
                    blk["x1"] = max(blk["x1"], seg["x1"])
                    blk["box_x0"] = blk["x0"]
                    blk["box_x1"] = blk["x1"]
                    blk["bottom"] = max(blk["bottom"], seg["bottom"])
                    blk["lines"] += 1
                    blk["align"] = "left"
                    merged = True
                    break
        if not merged:
            free_blocks.append(seg)

    blocks.extend(free_blocks)
    blocks.sort(key=lambda b: (b["top"], b["x0"]))
    return blocks


def _strip_bt_et_ops(instructions):
    """Filter out all PDF text blocks (``BT`` ... ``ET``) while preserving all
    images, shapes, tables, colors, gradients, and graphics state operators."""
    kept = []
    in_text = False
    for operands, operator in instructions:
        op = str(operator)
        if op == "BT":
            in_text = True
            continue
        if op == "ET":
            in_text = False
            continue
        if not in_text:
            kept.append((operands, operator))
    return kept


def _strip_text_from_pdf(src_path, page_indices, password=None):
    """Return a ``pypdf.PdfReader`` over a copy of ``src_path`` with all
    ``BT ... ET`` text operators removed from ``page_indices`` (while keeping
    all images, tables, borders, shadings, gradients, and forms intact), or
    ``None`` if stripping fails."""
    if not page_indices:
        return None
    try:
        import pikepdf
        from pypdf import PdfReader

        with core.open_pikepdf(src_path, password) as pdf:
            for idx in page_indices:
                if idx < 0 or idx >= len(pdf.pages):
                    continue
                page = pdf.pages[idx]
                ops = pikepdf.parse_content_stream(page)
                kept = _strip_bt_et_ops(ops)
                page.Contents = pdf.make_stream(pikepdf.unparse_content_stream(kept))
                # Also strip text inside non-shared Form XObjects on this page
                res = page.get("/Resources")
                xobjs = res.get("/XObject") if res and "/XObject" in res else None
                if xobjs:
                    new_xobjs = None
                    for name, ref in list(xobjs.items()):
                        if isinstance(ref, pikepdf.Stream) and ref.get("/Subtype") == "/Form":
                            try:
                                fops = pikepdf.parse_content_stream(ref)
                                fkept = _strip_bt_et_ops(fops)
                                if len(fkept) != len(fops):
                                    cloned = pdf.make_stream(pikepdf.unparse_content_stream(fkept))
                                    for k, v in ref.items():
                                        if k not in ("/Length", "/Filter", "/DecodeParms"):
                                            cloned[k] = v
                                    if new_xobjs is None:
                                        new_xobjs = pikepdf.Dictionary(xobjs)
                                    new_xobjs[name] = cloned
                            except Exception:  # noqa: BLE001
                                continue
                    if new_xobjs is not None:
                        new_res = pikepdf.Dictionary(res)
                        new_res["/XObject"] = new_xobjs
                        page["/Resources"] = new_res
            buf = io.BytesIO()
            pdf.save(buf)
        buf.seek(0)
        return PdfReader(buf)
    except Exception:  # noqa: BLE001
        return None


def _make_layout_overlay(
    width,
    height,
    blocks,
    translated_texts,
    bg_image=None,
    *,
    draw_bg=False,
    crop_x0=0.0,
    crop_y0=0.0,
    dst_lang="",
):
    """Build a transparent PDF overlay page that draws each translated block
    in-place with original font size, bold/italic style, bold lead-in labels,
    rotated text support (90°/270°), RTL BiDi shaping, color, alignment,
    smart vertical headroom, and CJK-aware auto-fit wrapping."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    if crop_x0 or crop_y0:
        c.translate(float(crop_x0), float(crop_y0))
    is_rtl = bool(dst_lang) and i18n.language_dir(dst_lang) == "rtl"

    for idx, (blk, trans) in enumerate(zip(blocks, translated_texts)):
        raw_text = (trans or "").strip()
        if not raw_text:
            continue
        text = _shape_rtl(raw_text, dst_lang) if is_rtl else raw_text
        x0, top, x1, bottom = blk["x0"], blk["top"], blk["x1"], blk["bottom"]
        box_x0 = blk.get("box_x0", x0)
        box_x1 = blk.get("box_x1", x1)
        orig_size = max(5.5, float(blk["size"]))
        pad = min(2.0, orig_size * 0.18)
        bg_rgb = _sample_bg_rgb(bg_image, x0, top, x1, bottom, width, height) if bg_image is not None else None

        if draw_bg:
            cover_rgb = bg_rgb if bg_rgb is not None else (1.0, 1.0, 1.0)
            rect_x = max(0.0, x0 - pad)
            rect_y = max(0.0, height - bottom - pad)
            rect_w = min(width - rect_x, (x1 - x0) + 2 * pad)
            rect_h = min(height - rect_y, (bottom - top) + 2 * pad)
            c.setFillColorRGB(*cover_rgb)
            c.rect(rect_x, rect_y, rect_w, rect_h, stroke=0, fill=1)

        fg_rgb = blk["color"]
        if bg_rgb is not None and abs(_luminance(fg_rgb) - _luminance(bg_rgb)) < 0.25:
            fg_rgb = (0.0, 0.0, 0.0) if _luminance(bg_rgb) > 0.5 else (1.0, 1.0, 1.0)

        chosen_font = fonts.font_for(
            text,
            family=blk.get("family", "sans"),
            bold=bool(blk.get("bold", False)),
            italic=bool(blk.get("italic", False)),
        )

        # Rotated text (90° or 270° margin labels / vertical table headers)
        rot = int(blk.get("rotation") or 0)
        if rot in (90, 270):
            avail_len = max(18.0, bottom - top)
            fit_size = orig_size
            min_size = max(5.5, orig_size * 0.55)
            while pdfmetrics.stringWidth(text, chosen_font, fit_size) > avail_len and fit_size > min_size:
                fit_size = max(min_size, fit_size * 0.9)
            c.saveState()
            c.setFillColorRGB(*fg_rgb)
            c.setFont(chosen_font, fit_size)
            if rot == 90:
                c.translate((x0 + x1) / 2.0 + fit_size * 0.35, height - bottom)
                c.rotate(90)
            else:
                c.translate((x0 + x1) / 2.0 - fit_size * 0.35, height - top)
                c.rotate(-90)
            c.drawString(0, 0, text)
            c.restoreState()
            continue

        # Compute available width: strictly cell-bounded inside tables;
        # outside tables, single-line labels/headings may expand into free space.
        avail_w = max(18.0, box_x1 - box_x0)
        align = blk.get("align", "left")
        if is_rtl and align == "left":
            align = "right"
        if not blk.get("in_table") and blk["lines"] == 1:
            if align == "center":
                half_span = min((x0 + x1) / 2.0 - 18.0, width - 18.0 - (x0 + x1) / 2.0)
                avail_w = max(avail_w, half_span * 2.0)
            elif align == "right":
                left_limit = 18.0
                for j, other in enumerate(blocks):
                    if j == idx:
                        continue
                    if other["top"] < bottom and other["bottom"] > top and other["x1"] <= x0 + 2.0:
                        left_limit = max(left_limit, other["x1"] + 6.0)
                avail_w = max(avail_w, x1 - left_limit)
            else:
                right_limit = width - 18.0
                for j, other in enumerate(blocks):
                    if j == idx:
                        continue
                    if other["top"] < bottom and other["bottom"] > top and other["x0"] >= x1 - 2.0:
                        right_limit = min(right_limit, other["x0"] - 6.0)
                avail_w = max(avail_w, right_limit - x0)

        # Smart vertical headroom: outside tables, measure free space down to the
        # next block in the same horizontal column so longer translations (e.g.
        # EN -> ES/DE/FR) don't get silently clipped when whitespace is available.
        max_bottom = bottom
        if not blk.get("in_table"):
            span_l = box_x0 if align != "right" else box_x1 - avail_w
            span_r = span_l + avail_w
            next_top = height - 18.0
            for j, other in enumerate(blocks):
                if j == idx:
                    continue
                if other["top"] >= bottom - 2.0:
                    o_l = other.get("box_x0", other["x0"])
                    o_r = other.get("box_x1", other["x1"])
                    if min(span_r, o_r) > max(span_l, o_l):
                        next_top = min(next_top, other["top"] - 4.0)
            if next_top > bottom:
                # Allow up to 2 extra lines of safe headroom into genuine whitespace
                max_bottom = min(next_top, bottom + orig_size * 2.8)

        avail_h = max(orig_size * 1.25, (max_bottom - top) + 2 * pad)

        # Auto-fit font size down gently until wrapped lines fit inside the block
        min_size = max(5.5, orig_size * 0.55)
        fit_size = orig_size
        wrapped = _wrap_text(text, chosen_font, fit_size, avail_w)
        while len(wrapped) * (fit_size * 1.2) > avail_h + 1.0 and fit_size > min_size:
            fit_size = max(min_size, fit_size * 0.9)
            wrapped = _wrap_text(text, chosen_font, fit_size, avail_w)

        c.setFillColorRGB(*fg_rgb)
        c.setFont(chosen_font, fit_size)
        leading = fit_size * 1.2
        y = height - top - fit_size * 0.88
        cx = (box_x0 + box_x1) / 2.0
        bold_prefix = bool(blk.get("bold_prefix")) and not is_rtl and align == "left"
        bold_font = (
            fonts.font_for(text, family=blk.get("family", "sans"), bold=True, italic=bool(blk.get("italic", False)))
            if bold_prefix
            else chosen_font
        )
        for line_i, line in enumerate(wrapped):
            if y < max(4.0, height - max_bottom - pad - leading * 0.35):
                break
            if line_i == 0 and bold_prefix and bold_font != chosen_font:
                m_col = re.match(r"^([^:：]{1,36}[:：])(.*)$", line)
                if m_col:
                    lead, rest = m_col.group(1), m_col.group(2)
                    c.setFont(bold_font, fit_size)
                    c.drawString(box_x0, y, lead)
                    lead_w = pdfmetrics.stringWidth(lead, bold_font, fit_size)
                    c.setFont(chosen_font, fit_size)
                    if rest:
                        c.drawString(box_x0 + lead_w, y, rest)
                    y -= leading
                    continue
            if align == "center":
                c.drawCentredString(cx, y, line)
            elif align == "right":
                c.drawRightString(box_x1, y, line)
            else:
                c.drawString(box_x0, y, line)
            y -= leading

    c.showPage()
    c.save()
    buf.seek(0)
    from pypdf import PdfReader
    return PdfReader(buf).pages[0]


def _render_page_bg(pdfium_doc, page_index):
    """Render a 72-dpi RGB PIL image for background-color sampling (or None)."""
    if pdfium_doc is None:
        return None
    try:
        with core.PDFIUM_LOCK:
            page = pdfium_doc[page_index]
            try:
                return page.render(scale=1.0).to_pil().convert("RGB")
            finally:
                page.close()
    except Exception:  # noqa: BLE001
        return None


def _page_crop_origin(page):
    """Return ``(crop_x0, crop_y0)`` offset between ``cropbox`` and ``mediabox``."""
    try:
        cb = page.cropbox
        mb = page.mediabox
        return (float(cb.left) - float(mb.left), float(cb.bottom) - float(mb.bottom))
    except Exception:  # noqa: BLE001
        return (0.0, 0.0)
