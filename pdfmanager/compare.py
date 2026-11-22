# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Compare two versions of a PDF.

Pages are matched up by their text, so an inserted or removed page doesn't
make every later page look different. Each matched pair is compared word by
word and pixel by pixel. The result is a report PDF: a summary page, then
the two versions side by side for each changed page with the changed areas
outlined in red, and the words that were removed and added.
"""

import collections
import difflib
import io
import re
from pathlib import Path

from PIL import Image, ImageChops
from pypdf.generic import DictionaryObject, NameObject

from pdfmanager import core, fonts, progress

CELL = 6            # pixels per cell when grouping changed pixels into areas
THRESHOLD = 48      # 0-255 difference for a pixel to count as changed
DPI = 90
HASH_DPI = 18       # tiny thumbnail scale for perceptual alignment hashes (~1 KB/page)
HASH_SIZE = 16      # 16x16 dHash = 256 bits
RENDER_CACHE = 4    # max full-res rendered pages kept in RAM per document
SIZE_TOL_PT = 1.5   # points tolerance when comparing physical page sizes


def _field_values(path, password):
    """{0-based page: "what is filled into the form fields on it"}."""
    from pdfmanager.forms import _inherited

    values = {}
    for number, page in enumerate(core.open_pdf(path, password).pages):
        words = []
        for ref in page.get("/Annots") or []:
            annot = ref.get_object()
            if isinstance(annot, DictionaryObject) and annot.get("/Subtype") == "/Widget":
                value = _inherited(annot, "/V")
                if isinstance(value, (str, NameObject)):
                    words.append(str(value))
        if words:
            values[number] = " ".join(words)
    return values


def _dhash(image, size=HASH_SIZE):
    """Compute a ``size*size``-bit horizontal difference hash + blank flag for ``image``."""
    gray = image.convert("L").resize((size + 1, size), Image.Resampling.BILINEAR)
    px = gray.tobytes()
    # A page is visually blank if almost all thumbnail pixels are near-white and low-contrast.
    dark = sum(1 for v in px if v < 240)
    blank = dark <= max(1, len(px) // 200)
    bits = 0
    for y in range(size):
        row = y * (size + 1)
        for x in range(size):
            bits = (bits << 1) | (1 if px[row + x] > px[row + x + 1] else 0)
    return bits, blank


def _hash_similarity(h1, h2, bits=HASH_SIZE * HASH_SIZE):
    """Normalized perceptual similarity in [0.0, 1.0] from two dHash integers."""
    if h1 is None or h2 is None:
        return 0.0
    return 1.0 - ((h1 ^ h2).bit_count() / float(bits))


def _analyze(path, password):
    """Stage 1 (C-002): Extract lightweight per-page fingerprints (text, physical
    size in points, 256-bit perceptual hash, blank flag) without retaining full
    rendered page images in memory."""
    import pypdfium2 as pdfium

    pages = []
    fields = _field_values(path, password)  # pdfium's text leaves out form fields
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path), password=password)
        doc.init_forms()
        try:
            total = len(doc)
            for number in range(total):
                progress.check()
                progress.report(number, max(1, total), "analyzing pages")
                page = doc[number]
                try:
                    pw, ph = page.get_size()
                    textpage = page.get_textpage()
                    try:
                        text = textpage.get_text_range() + " " + fields.get(number, "")
                    finally:
                        textpage.close()
                    thumb = page.render(scale=core.render_scale(page, HASH_DPI / 72)).to_pil().convert("RGB")
                    dhash, is_blank = _dhash(thumb)
                    norm_text = " ".join(text.split())
                    pages.append({
                        "text": norm_text,
                        "size": (round(float(pw), 2), round(float(ph), 2)),
                        "dhash": dhash,
                        "blank": is_blank and not norm_text,
                    })
                finally:
                    page.close()
        finally:
            doc.close()
    return pages


class _PageRenderer:
    """Stage 3 (C-002): On-demand page renderer with a small bounded LRU cache
    so peak memory stays O(1) regardless of document length."""

    def __init__(self, path, password, dpi, cache_size=RENDER_CACHE):
        self.path = str(path)
        self.password = password
        self.dpi = dpi
        self.cache_size = max(1, int(cache_size))
        self._doc = None
        self._cache = collections.OrderedDict()

    def __enter__(self):
        import pypdfium2 as pdfium

        with core.PDFIUM_LOCK:
            self._doc = pdfium.PdfDocument(self.path, password=self.password)
            self._doc.init_forms()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._cache.clear()
        if self._doc is not None:
            with core.PDFIUM_LOCK:
                self._doc.close()
            self._doc = None
        return False

    def get(self, index):
        if index in self._cache:
            self._cache.move_to_end(index)
            return self._cache[index]
        with core.PDFIUM_LOCK:
            page = self._doc[index]
            try:
                img = page.render(scale=core.render_scale(page, self.dpi / 72)).to_pil().convert("RGB")
            finally:
                page.close()
        self._cache[index] = img
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return img


def _load(path, password, dpi):
    """Legacy helper retained for backward compatibility: returns (text, PNG bytes)
    for every page. New code should use ``_analyze()`` + ``_PageRenderer``."""
    import pypdfium2 as pdfium

    pages = []
    fields = _field_values(path, password)
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path), password=password)
        doc.init_forms()
        try:
            for number, page in enumerate(doc):
                progress.report(number, len(doc), "page")
                textpage = page.get_textpage()
                text = textpage.get_text_range() + " " + fields.get(number, "")
                textpage.close()
                image = page.render(scale=core.render_scale(page, dpi / 72)).to_pil().convert("RGB")
                buf = io.BytesIO()
                image.save(buf, "PNG", compress_level=1)
                pages.append((" ".join(text.split()), buf.getvalue()))
        finally:
            doc.close()
    return pages


def _picture(png):
    return Image.open(io.BytesIO(png)).convert("RGB")


def _coerce_info(item):
    """Normalize either a ``_analyze()`` dict or legacy ``(text, png_bytes)`` tuple."""
    if isinstance(item, dict):
        return item
    text, png = item
    dhash, blank = (None, not bool(text))
    size = (612.0, 792.0)
    if png:
        img = _picture(png)
        size = (float(img.size[0]), float(img.size[1]))
        dhash, is_blank = _dhash(img)
        blank = is_blank and not bool(text)
    return {"text": str(text or ""), "size": size, "dhash": dhash, "blank": blank}


def _geom_similarity(size_a, size_b):
    wa, ha = max(1.0, size_a[0]), max(1.0, size_a[1])
    wb, hb = max(1.0, size_b[0]), max(1.0, size_b[1])
    ra, rb = wa / ha, wb / hb
    aspect = min(ra, rb) / max(ra, rb)
    scale = min(wa * ha, wb * hb) / max(wa * ha, wb * hb)
    return 0.7 * aspect + 0.3 * scale


def _text_similarity(ta, tb):
    if ta == tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    wa, wb = set(ta.lower().split()), set(tb.lower().split())
    jacc = len(wa & wb) / max(1, len(wa | wb))
    seq = difflib.SequenceMatcher(None, ta[:2000], tb[:2000], autojunk=False).quick_ratio()
    return 0.5 * jacc + 0.5 * seq


def _page_similarity(pa, pb):
    """Multi-signal similarity score in [0.0, 1.0] combining text, perceptual
    visual hash, and page geometry (C-001)."""
    geom = _geom_similarity(pa["size"], pb["size"])
    vis = _hash_similarity(pa.get("dhash"), pb.get("dhash")) if (pa.get("dhash") is not None and pb.get("dhash") is not None) else None
    ta, tb = pa["text"], pb["text"]
    has_text_a, has_text_b = len(ta) >= 12, len(tb) >= 12

    if pa.get("blank") or pb.get("blank"):
        if pa.get("blank") and pb.get("blank"):
            return 0.75 * geom
        return 0.0

    if has_text_a and has_text_b:
        tsim = _text_similarity(ta, tb)
        if vis is not None:
            return 0.65 * tsim + 0.25 * vis + 0.10 * geom
        return 0.85 * tsim + 0.15 * geom

    # Scanned / low-text page: rely on visual perceptual hash + geometry
    if vis is not None:
        if has_text_a != has_text_b:
            # One has text and the other doesn't (e.g. OCR'd vs raw scan): visual hash still bridges them
            return 0.80 * vis + 0.20 * geom
        return 0.85 * vis + 0.15 * geom

    # Fallback if no visual hash available
    return _text_similarity(ta, tb)


def _align_gap_dp(old_info, new_info, i1, i2, j1, j2, gap_penalty=0.52):
    """Needleman-Wunsch sequence alignment over a gap using multi-signal page
    similarity. Returns list of ``(i or None, j or None, confidence)``."""
    m, n = i2 - i1, j2 - j1
    if m == 0:
        return [(None, j, 0.0) for j in range(j1, j2)]
    if n == 0:
        return [(i, None, 0.0) for i in range(i1, i2)]

    # Guard against huge O(m*n) tables on completely unrelated giant documents
    if m * n > 40_000:
        k = min(m, n)
        out = []
        for d in range(k):
            sim = _page_similarity(old_info[i1 + d], new_info[j1 + d])
            if sim >= gap_penalty:
                out.append((i1 + d, j1 + d, round(sim, 3)))
            else:
                out.append((i1 + d, None, 0.0))
                out.append((None, j1 + d, 0.0))
        out.extend((i, None, 0.0) for i in range(i1 + k, i2))
        out.extend((None, j, 0.0) for j in range(j1 + k, j2))
        return out

    # DP table: dp[r][c] = best score aligning old[i1:i1+r] with new[j1:j1+c]
    # Score for pairing (r, c) = sim - gap_penalty (so sim < gap_penalty prefers insert+delete)
    dp = [[0.0] * (n + 1) for _ in range(m + 1)]
    bt = [[0] * (n + 1) for _ in range(m + 1)]  # 1=diag, 2=up(del), 3=left(ins)
    sims = [[0.0] * n for _ in range(m)]
    for r in range(1, m + 1):
        bt[r][0] = 2
    for c in range(1, n + 1):
        bt[0][c] = 3
    for r in range(1, m + 1):
        pa = old_info[i1 + r - 1]
        for c in range(1, n + 1):
            sim = _page_similarity(pa, new_info[j1 + c - 1])
            sims[r - 1][c - 1] = sim
            diag = dp[r - 1][c - 1] + (sim - gap_penalty)
            up = dp[r - 1][c]
            left = dp[r][c - 1]
            if diag >= up and diag >= left and sim >= gap_penalty:
                dp[r][c] = diag
                bt[r][c] = 1
            elif up >= left:
                dp[r][c] = up
                bt[r][c] = 2
            else:
                dp[r][c] = left
                bt[r][c] = 3

    out = []
    r, c = m, n
    while r > 0 or c > 0:
        move = bt[r][c]
        if move == 1:
            out.append((i1 + r - 1, j1 + c - 1, round(sims[r - 1][c - 1], 3)))
            r -= 1
            c -= 1
        elif move == 2:
            out.append((i1 + r - 1, None, 0.0))
            r -= 1
        else:
            out.append((None, j1 + c - 1, 0.0))
            c -= 1
    out.reverse()
    return out


def _align_scored(old, new):
    """Multi-signal page alignment returning ``[(old_idx_or_None, new_idx_or_None,
    confidence, reordered)]`` (C-001). Handles digital, scanned, mixed, inserted,
    deleted, duplicate, and reordered pages."""
    old_info = [_coerce_info(x) for x in old]
    new_info = [_coerce_info(x) for x in new]

    # Exact fingerprint anchor pass (fast O(N) for unchanged pages)
    key_old = [(p["text"], p["dhash"], p["size"]) for p in old_info]
    key_new = [(p["text"], p["dhash"], p["size"]) for p in new_info]
    matcher = difflib.SequenceMatcher(None, key_old, key_new, autojunk=False)

    raw_pairs = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                raw_pairs.append((i, j, 1.0))
        else:
            raw_pairs.extend(_align_gap_dp(old_info, new_info, i1, i2, j1, j2))

    # Reorder detection pass: match high-confidence unmatched removed & added pages
    removed_pos = [idx for idx, (i, j, _) in enumerate(raw_pairs) if i is not None and j is None]
    added_pos = [idx for idx, (i, j, _) in enumerate(raw_pairs) if i is None and j is not None]
    reordered_map = {}  # removed_pos_idx -> (added_pos_idx, sim)
    used_added = set()
    if removed_pos and added_pos and len(removed_pos) * len(added_pos) <= 10_000:
        candidates = []
        for rp in removed_pos:
            i = raw_pairs[rp][0]
            if old_info[i].get("blank"):
                continue
            for ap in added_pos:
                j = raw_pairs[ap][1]
                if new_info[j].get("blank"):
                    continue
                sim = _page_similarity(old_info[i], new_info[j])
                if sim >= 0.85:
                    candidates.append((sim, rp, ap))
        candidates.sort(key=lambda x: -x[0])
        used_removed = set()
        for sim, rp, ap in candidates:
            if rp not in used_removed and ap not in used_added:
                used_removed.add(rp)
                used_added.add(ap)
                reordered_map[rp] = (ap, round(sim, 3))

    final = []
    for idx, (i, j, conf) in enumerate(raw_pairs):
        if idx in used_added:
            continue
        if idx in reordered_map:
            ap, sim = reordered_map[idx]
            final.append((i, raw_pairs[ap][1], sim, True))
        else:
            final.append((i, j, conf, False))
    return final


def _align(old, new):
    """Pairs (old index or None, new index or None) in reading order."""
    return [(i, j) for i, j, _, _ in _align_scored(old, new)]


def _geometry_diff(size_a, size_b):
    """Compare physical page sizes (in PDF points) and orientations (C-003)."""
    wa, ha = size_a
    wb, hb = size_b
    size_changed = abs(wa - wb) > SIZE_TOL_PT or abs(ha - hb) > SIZE_TOL_PT
    orient_a = "landscape" if wa > ha + SIZE_TOL_PT else ("portrait" if ha > wa + SIZE_TOL_PT else "square")
    orient_b = "landscape" if wb > hb + SIZE_TOL_PT else ("portrait" if hb > wb + SIZE_TOL_PT else "square")
    orientation_changed = orient_a != orient_b
    return {
        "size_changed": size_changed,
        "orientation_changed": orientation_changed,
        "old_size": (round(wa, 1), round(ha, 1)),
        "new_size": (round(wb, 1), round(hb, 1)),
    }


def _changed_areas(a, b):
    """Rectangles (in shared physical-scale canvas pixels) where the two page
    images differ. Never stretches one page into another's aspect ratio (C-003):
    when sizes differ, both pages are placed on a shared white-padded canvas
    at their original 1:1 physical pixel scale."""
    if b.size != a.size:
        cw, ch = max(a.size[0], b.size[0]), max(a.size[1], b.size[1])
        ca = Image.new("RGB", (cw, ch), (255, 255, 255))
        cb = Image.new("RGB", (cw, ch), (255, 255, 255))
        ca.paste(a, (0, 0))
        cb.paste(b, (0, 0))
        a, b = ca, cb
    diff = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > THRESHOLD else 0)
    if not diff.getbbox():
        return []
    w, h = a.size
    cols, rows = (w + CELL - 1) // CELL, (h + CELL - 1) // CELL
    small = diff.resize((cols, rows), Image.Resampling.BOX)
    on = {(x, y) for y in range(rows) for x in range(cols) if small.getpixel((x, y)) > 8}
    areas = []
    while on:  # flood fill neighbouring cells (with a one-cell gap) into areas
        stack, cells = [on.pop()], []
        while stack:
            x, y = stack.pop()
            cells.append((x, y))
            for dx in (-2, -1, 0, 1, 2):
                for dy in (-2, -1, 0, 1, 2):
                    if (x + dx, y + dy) in on:
                        on.remove((x + dx, y + dy))
                        stack.append((x + dx, y + dy))
        xs, ys = [c[0] for c in cells], [c[1] for c in cells]
        areas.append((min(xs) * CELL, min(ys) * CELL, (max(xs) + 1) * CELL, (max(ys) + 1) * CELL))
    return areas


def _word_changes(a, b):
    """(removed words, added words) as lists of phrases."""
    wa, wb = a.split(), b.split()
    removed, added = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed.append(" ".join(wa[i1:i2]))
        if tag in ("replace", "insert"):
            added.append(" ".join(wb[j1:j2]))
    return removed, added


MODES = ("both", "text", "visual")
LOW_CONFIDENCE_THRESHOLD = 0.45


def _summarize_counts(rows):
    return {
        "changed": sum(1 for r in rows if r["kind"] == "changed"),
        "added": sum(1 for r in rows if r["kind"] == "added"),
        "removed": sum(1 for r in rows if r["kind"] == "removed"),
        "same": sum(1 for r in rows if r["kind"] == "same"),
        "reordered": sum(1 for r in rows if r.get("reordered")),
        "low_confidence": sum(1 for r in rows if r.get("low_confidence")),
    }


def format_summary(summary):
    """Human-readable one-line summary of comparison results for GUI and CLI."""
    counts = summary.get("counts") or _summarize_counts(summary.get("changes", []))
    total_diff = counts["changed"] + counts["added"] + counts["removed"]
    if total_diff == 0:
        return "No differences found."
    bits = []
    if counts["changed"]:
        bits.append(f"{counts['changed']} changed")
    if counts["added"]:
        bits.append(f"{counts['added']} added")
    if counts["removed"]:
        bits.append(f"{counts['removed']} removed")
    if counts["reordered"]:
        bits.append(f"{counts['reordered']} reordered")
    if counts["low_confidence"]:
        bits.append(f"{counts['low_confidence']} low-confidence alignment")
    return f"{total_diff} page{'s' if total_diff != 1 else ''} with differences ({', '.join(bits)})"


def _compute_rows(old, new, old_password, new_password, dpi, mode, render_a, render_b):
    if mode not in MODES:
        raise core.PdfManagerError(f"unknown compare mode {mode!r}; use {', '.join(MODES)}")
    with progress.section(0, 3, "original"):
        a = _analyze(old, old_password)
    with progress.section(1, 3, "changed version"):
        b = _analyze(new, new_password)
    rows = []
    scored_pairs = _align_scored(a, b)
    for n, (i, j, conf, reordered) in enumerate(scored_pairs):
        progress.check()
        progress.report(2 * len(scored_pairs) + n, max(1, 3 * len(scored_pairs)), message="comparing pages")
        if i is None or j is None:
            kind, removed, added, areas = ("added" if i is None else "removed"), [], [], []
            geom = {"size_changed": False, "orientation_changed": False}
            low_conf = False
        else:
            removed, added = ([], []) if mode == "visual" else _word_changes(a[i]["text"], b[j]["text"])
            geom = _geometry_diff(a[i]["size"], b[j]["size"])
            areas = [] if mode == "text" else _changed_areas(render_a.get(i), render_b.get(j))
            if mode == "text":
                kind = "changed" if (removed or added) else "same"
            elif mode == "visual":
                kind = "changed" if (areas or geom["size_changed"] or geom["orientation_changed"]) else "same"
            else:
                kind = "changed" if (removed or added or areas or geom["size_changed"] or geom["orientation_changed"]) else "same"
            low_conf = conf < LOW_CONFIDENCE_THRESHOLD
        row = {
            "old": None if i is None else i + 1,
            "new": None if j is None else j + 1,
            "kind": kind,
            "removed": removed,
            "added": added,
            "areas": areas,
            "confidence": round(float(conf), 3),
            "low_confidence": low_conf,
            "reordered": reordered,
            "size_changed": geom["size_changed"],
            "orientation_changed": geom["orientation_changed"],
        }
        row["label"] = _label(row)
        rows.append(row)
    return a, b, rows


def compare_summary(old, new, old_password=None, new_password=None, all_pages=True, dpi=DPI, mode="both"):
    """Compute a structured, JSON-serializable comparison summary without writing a PDF.

    Ideal for interactive side-by-side diff viewers and CLI ``--json`` output.
    """
    with _PageRenderer(old, old_password, dpi) as render_a, _PageRenderer(new, new_password, dpi) as render_b:
        a, b, rows = _compute_rows(old, new, old_password, new_password, dpi, mode, render_a, render_b)
    counts = _summarize_counts(rows)
    return {
        "old_name": Path(old).name,
        "new_name": Path(new).name,
        "old_pages": len(a),
        "new_pages": len(b),
        "mode": mode,
        "counts": counts,
        "changes": [r for r in rows if all_pages or r["kind"] != "same"],
    }


def compare(old, new, output, old_password=None, new_password=None, all_pages=False, dpi=DPI, mode="both"):
    """Write a comparison report of ``old`` and ``new`` to ``output``.
    Returns a summary: {"old_pages", "new_pages", "mode", "counts", "changes":
    [{"old", "new", "kind", "removed", "added", "areas", "confidence",
    "low_confidence", "reordered", "size_changed", "orientation_changed",
    "label"}]} with 1-based page numbers (None for a page only in one file);
    kind is "changed", "added", "removed" or "same"."""
    with _PageRenderer(old, old_password, dpi) as render_a, _PageRenderer(new, new_password, dpi) as render_b:
        a, b, rows = _compute_rows(old, new, old_password, new_password, dpi, mode, render_a, render_b)
        _report(Path(old).name, Path(new).name, len(a), len(b), render_a, render_b, rows, output, all_pages)
    counts = _summarize_counts(rows)
    return {
        "old_pages": len(a),
        "new_pages": len(b),
        "mode": mode,
        "counts": counts,
        "changes": [r for r in rows if all_pages or r["kind"] != "same"],
    }


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------

RED, GREEN, GRAY = (0.80, 0.16, 0.10), (0.12, 0.50, 0.25), (0.42, 0.41, 0.39)


def _write(c, x, y, text, size, width):
    """Draw ``text`` in a font that can show it (the documents can be in
    any alphabet), cut to ``width`` points."""
    from reportlab.pdfbase import pdfmetrics

    text = re.sub(r"\s+", " ", text)[:400]
    font = fonts.font_for(text)
    if pdfmetrics.stringWidth(text, font, size) > width:
        while text and pdfmetrics.stringWidth(text + "…", font, size) > width:
            text = text[:-1]
        text += "…"
    c.setFont(font, size)
    c.drawString(x, y, text)


def _label(row):
    if row["kind"] == "added":
        return f"Page {row['new']} added"
    if row["kind"] == "removed":
        return f"Page {row['old']} removed"
    same = row["old"] == row["new"]
    where = f"Page {row['old']}" if same else f"Page {row['old']} → page {row['new']}"
    suffix = " (reordered)" if row.get("reordered") else ""
    return where + (" changed" if row["kind"] == "changed" else " unchanged") + suffix


def _report(old_name, new_name, old_count, new_count, render_a, render_b, rows, output, all_pages):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.utils import ImageReader

    W, H = landscape(A4)
    margin = 36
    c = core.pdf_canvas(str(output), pagesize=(W, H))
    c.setTitle(f"Comparison of {old_name} and {new_name}")
    c.setCreator("ZPDF")

    # Summary page
    changed = [r for r in rows if r["kind"] != "same"]
    c.setFont("Helvetica-Bold", 20)
    c.drawString(margin, H - margin - 14, "Comparison")
    c.setFillColorRGB(*GRAY)
    _write(c, margin, H - margin - 36, f"Original: {old_name} ({old_count} pages)", 11, W - 2 * margin)
    _write(c, margin, H - margin - 52, f"Changed: {new_name} ({new_count} pages)", 11, W - 2 * margin)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 13)
    y = H - margin - 86
    c.drawString(margin, y, "No differences found." if not changed else
                 f"{len(changed)} page{'s' if len(changed) != 1 else ''} with differences:")
    c.setFont("Helvetica", 10.5)
    y -= 20
    for row in changed:
        if y < margin + 14:
            c.drawString(margin, y, f"… and {len(changed) - changed.index(row)} more")
            break
        bits = []
        if row.get("orientation_changed"):
            bits.append("orientation changed")
        elif row.get("size_changed"):
            bits.append("page size changed")
        if row["removed"]:
            bits.append("removed “" + " ".join(row["removed"])[:60] + "”")
        if row["added"]:
            bits.append("added “" + " ".join(row["added"])[:60] + "”")
        if row["kind"] == "changed" and not bits:
            bits.append("layout or pictures changed")
        _write(c, margin, y, _label(row) + (": " + "; ".join(bits) if bits else ""), 10.5,
               W - 2 * margin)
        y -= 15
    c.showPage()

    # One page per (changed) pair — rendered on demand with zero aspect distortion
    half = (W - 3 * margin) / 2
    text_space = 92
    for row in rows if all_pages else changed:
        progress.check()
        c.setFont("Helvetica-Bold", 13)
        c.drawString(margin, H - margin - 10, _label(row))
        top = H - margin - 30
        box_h = top - margin - text_space
        for side, (renderer, number) in enumerate(((render_a, row["old"]), (render_b, row["new"]))):
            x0 = margin + side * (half + margin)
            c.setFont("Helvetica", 9.5)
            c.setFillColorRGB(*GRAY)
            c.drawString(x0, top - 2, ("Original" if side == 0 else "Changed")
                         + (f" — page {number}" if number else " — (no page)"))
            c.setFillColorRGB(0, 0, 0)
            if not number:
                continue
            image = renderer.get(number - 1)
            iw, ih = image.size
            scale = min(half / iw, (box_h - 12) / ih)
            dw, dh = iw * scale, ih * scale
            ix, iy = x0 + (half - dw) / 2, top - 12 - dh
            c.drawImage(ImageReader(image), ix, iy, dw, dh)
            c.setStrokeColorRGB(0.8, 0.8, 0.78)
            c.rect(ix, iy, dw, dh, stroke=1, fill=0)
            # Outline the changed areas (computed in 1:1 physical pixels on the
            # shared canvas; clamp cleanly to this page's actual pixel bounds).
            c.setStrokeColorRGB(*RED)
            c.setFillColorRGB(*RED, alpha=0.12)
            c.setLineWidth(1.2)
            for x1, y1, x2, y2 in row["areas"]:
                cx1, cy1 = max(0, min(iw, x1)), max(0, min(ih, y1))
                cx2, cy2 = max(0, min(iw, x2)), max(0, min(ih, y2))
                if cx2 <= cx1 or cy2 <= cy1:
                    continue
                c.rect(ix + cx1 * scale - 2, iy + dh - cy2 * scale - 2,
                       (cx2 - cx1) * scale + 4, (cy2 - cy1) * scale + 4, stroke=1, fill=1)
            c.setFillColorRGB(0, 0, 0, alpha=1)
            c.setLineWidth(1)
        y = margin + text_space - 16
        if row.get("orientation_changed") or row.get("size_changed"):
            c.setFillColorRGB(*GRAY)
            c.setFont("Helvetica", 10)
            msg = "Page orientation changed." if row.get("orientation_changed") else "Page size changed."
            c.drawString(margin, y, msg)
            y -= 13
        for sign, colour, phrases in (("−", RED, row["removed"]), ("+", GREEN, row["added"])):
            for phrase in phrases[:3]:
                c.setFillColorRGB(*colour)
                _write(c, margin, y, f"{sign} {phrase}", 10, W - 2 * margin)
                y -= 13
        if row["kind"] == "changed" and not row["removed"] and not row["added"] and not row.get("size_changed") and not row.get("orientation_changed"):
            c.setFillColorRGB(*GRAY)
            c.setFont("Helvetica", 10)
            c.drawString(margin, y, "The text is the same; the layout or pictures changed "
                                    "(outlined in red).")
        c.setFillColorRGB(0, 0, 0)
        c.showPage()
    c.save()
    return output

