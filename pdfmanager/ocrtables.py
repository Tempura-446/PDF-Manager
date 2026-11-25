# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Table detection and numeric-cell refinement for scanned pages (OCR Phase 4).

Finds ruled tables directly from the scanned page image (horizontal and
vertical grid lines) and unruled tables from aligned columns of OCR lines,
reads or refines cells individually so columns stay aligned, applies a
digits-focused second pass for numeric cells (O/0, l/1, I/1, S/5, B/8, Z/2),
and outputs lines in strict row-by-row, left-to-right cell order so Copy and
PDF-to-Excel (tables.py) work cleanly on scanned tables.
"""
import re
import statistics

from pdfmanager.textlayer import Word

# Common OCR letter-for-digit confusions inside numeric table cells.
_DIGIT_MAP = str.maketrans({
    "O": "0", "o": "0",
    "l": "1", "I": "1", "|": "1",
    "S": "5", "s": "5",
    "B": "8",
    "Z": "2", "z": "2",
})
_PURE_NUM_RE = re.compile(r"^[+-]?[$€£¥]?[+-]?\(?\d[\d,\.\s]*\d?\)?%?$")
_HAS_DIGIT_RE = re.compile(r"\d")
_CONFUSABLE_LETTER_RE = re.compile(r"[OolI|SsBZz]")

MIN_RULE_CLUSTER_GAP = 6   # px; adjacent dark pixels within this distance belong to one rule line
CELL_INSET = 3             # px; trim grid borders from cell crops
CELL_PAD = 10              # px; white border added around cell crops for cleaner OCR
PROSE_CELL_CHARS = 32      # cells longer than this on average read like prose, not a table


def is_pure_number(text):
    """Whether ``text`` is already a clean number (optional currency, sign,
    commas, decimal point, parentheses, or percent sign)."""
    s = (text or "").strip()
    if not s or not _HAS_DIGIT_RE.search(s):
        return False
    return bool(_PURE_NUM_RE.match(s))


def looks_numeric(text, strict_column=False):
    """Whether ``text`` looks like a numeric cell (possibly with OCR
    letter-for-digit confusions such as O/0, l/1, S/5)."""
    s = (text or "").strip()
    if not s:
        return False
    if is_pure_number(s):
        return True
    mapped = s.translate(_DIGIT_MAP)
    if not _PURE_NUM_RE.match(mapped) or not _HAS_DIGIT_RE.search(mapped):
        return False
    # Outside a known numeric column, require at least one genuine digit so
    # short words like "SO", "IS", "B" or "I" are never mistaken for numbers.
    if not strict_column and not _HAS_DIGIT_RE.search(s):
        return False
    return True


def normalise_numeric_token(token, strict_column=False):
    """Fix OCR letter-for-digit confusions (O/0, l/1, I/1, S/5, B/8, Z/2)
    in ``token`` when it looks numeric; otherwise return ``token`` unchanged."""
    s = (token or "").strip()
    if not s or is_pure_number(s):
        return token
    if not looks_numeric(s, strict_column=strict_column):
        return token
    if not _CONFUSABLE_LETTER_RE.search(s):
        return token
    return s.translate(_DIGIT_MAP)


# -- ruled table detection on scanned page images ------------------------------

def _find_1d_runs(mask, min_len):
    """For a 2D boolean mask, yield ``(row_idx, start, end)`` for every
    contiguous True run of length >= ``min_len`` along axis 1."""
    import numpy as np

    rows, cols = mask.shape
    if cols < min_len:
        return []
    padded = np.zeros((rows, cols + 2), dtype=np.int8)
    padded[:, 1:-1] = mask.astype(np.int8)
    diff = np.diff(padded, axis=1)
    starts = np.argwhere(diff == 1)
    ends = np.argwhere(diff == -1)
    out = []
    for (r, s), (_r2, e) in zip(starts, ends):
        if e - s >= min_len:
            out.append((int(r), int(s), int(e)))
    return out


def _cluster_segments(runs, gap=MIN_RULE_CLUSTER_GAP):
    """Merge parallel line runs whose primary coordinate is within ``gap`` px
    and whose spans overlap into ``[(coord, start, end), ...]``."""
    if not runs:
        return []
    runs = sorted(runs)
    groups = []
    for coord, s, e in runs:
        placed = False
        for g in groups:
            if abs(coord - g["coord"]) <= gap and s <= g["e"] + gap and e >= g["s"] - gap:
                g["coords"].append(coord)
                g["coord"] = statistics.median(g["coords"])
                g["s"] = min(g["s"], s)
                g["e"] = max(g["e"], e)
                placed = True
                break
        if not placed:
            groups.append({"coord": float(coord), "coords": [coord], "s": s, "e": e})
    return [(int(round(g["coord"])), int(g["s"]), int(g["e"])) for g in groups]


def _segments_intersect(h_seg, v_seg, tol=8):
    hy, hx0, hx1 = h_seg
    vx, vy0, vy1 = v_seg
    return (hx0 - tol <= vx <= hx1 + tol) and (vy0 - tol <= hy <= vy1 + tol)


def detect_ruled_tables(image):
    """Detect ruled tables (grid of >= 3 horizontal and >= 3 vertical rules)
    directly from a scanned PIL ``image``.

    Returns a list of dicts:
    ``{"box": (x0, y0, x1, y1), "rows": [y0, ...], "cols": [x0, ...],
       "cells": [[(cx0, cy0, cx1, cy1), ...], ...]}``.
    """
    import numpy as np

    grey = image.convert("L")
    w, h = grey.size
    if w < 120 or h < 80:
        return []
    arr = np.asarray(grey, dtype=np.int16)
    med = int(np.median(arr[::4, ::4]))
    thresh = min(200, max(40, med - 25))
    dark = arr < thresh

    min_h_len = max(80, int(w * 0.12))
    min_v_len = max(50, int(h * 0.06))
    h_segs = _cluster_segments(_find_1d_runs(dark, min_h_len))
    v_segs = _cluster_segments(_find_1d_runs(dark.T, min_v_len))
    if len(h_segs) < 3 or len(v_segs) < 3:
        return []

    # Connected components of intersecting horizontal and vertical segments.
    nh, nv = len(h_segs), len(v_segs)
    adj_h = [[] for _ in range(nh)]
    adj_v = [[] for _ in range(nv)]
    for hi, hs in enumerate(h_segs):
        for vi, vs in enumerate(v_segs):
            if _segments_intersect(hs, vs):
                adj_h[hi].append(vi)
                adj_v[vi].append(hi)

    seen_h, seen_v = set(), set()
    tables = []
    for hi in range(nh):
        if hi in seen_h or not adj_h[hi]:
            continue
        comp_h, comp_v = set(), set()
        stack = [("h", hi)]
        while stack:
            kind, idx = stack.pop()
            if kind == "h":
                if idx in comp_h:
                    continue
                comp_h.add(idx)
                seen_h.add(idx)
                for vi in adj_h[idx]:
                    if vi not in comp_v:
                        stack.append(("v", vi))
            else:
                if idx in comp_v:
                    continue
                comp_v.add(idx)
                seen_v.add(idx)
                for hj in adj_v[idx]:
                    if hj not in comp_h:
                        stack.append(("h", hj))

        ys = sorted({h_segs[i][0] for i in comp_h})
        xs = sorted({v_segs[i][0] for i in comp_v})
        # Merge coordinates that are very close (double borders / thick rules).
        ys = _dedup_coords(ys, min_gap=10)
        xs = _dedup_coords(xs, min_gap=10)
        if len(ys) < 3 or len(xs) < 3:
            continue

        cells = []
        for r in range(len(ys) - 1):
            row_cells = []
            for c in range(len(xs) - 1):
                cx0 = min(xs[c + 1], xs[c] + CELL_INSET)
                cy0 = min(ys[r + 1], ys[r] + CELL_INSET)
                cx1 = max(cx0 + 1, xs[c + 1] - CELL_INSET)
                cy1 = max(cy0 + 1, ys[r + 1] - CELL_INSET)
                row_cells.append((cx0, cy0, cx1, cy1))
            cells.append(row_cells)
        tables.append({
            "box": (xs[0], ys[0], xs[-1], ys[-1]),
            "rows": ys,
            "cols": xs,
            "cells": cells,
        })

    tables.sort(key=lambda t: (t["box"][1], t["box"][0]))
    return tables


def _dedup_coords(coords, min_gap=10):
    out = []
    for c in coords:
        if not out or c - out[-1] >= min_gap:
            out.append(c)
    return out


# -- unruled table detection from OCR lines ------------------------------------

def _line_bbox(line):
    xs = [x for x, _ in line.quad]
    ys = [y for _, y in line.quad]
    return min(xs), min(ys), max(xs), max(ys)


def _group_rows(lines):
    """Group OCR lines into horizontal rows (top to bottom, each row left to right)."""
    rows = []
    for line in sorted(lines, key=lambda l: (_line_bbox(l)[1] + _line_bbox(l)[3]) / 2):
        x0, y0, x1, y1 = _line_bbox(line)
        mid = (y0 + y1) / 2
        for row in rows:
            if row["top"] <= mid <= row["bottom"]:
                row["lines"].append(line)
                row["top"] = min(row["top"], y0)
                row["bottom"] = max(row["bottom"], y1)
                break
        else:
            rows.append({"top": y0, "bottom": y1, "lines": [line]})
    return [sorted(r["lines"], key=lambda l: _line_bbox(l)[0]) for r in rows]


def _x_columns(rows):
    """Find column x-intervals [(cx0, cx1), ...] separated by white-space rivers
    across ``rows``, or [] if fewer than 2 columns exist."""
    spans = []
    for row in rows:
        for line in row:
            x0, _, x1, _ = _line_bbox(line)
            spans.append((x0, x1))
    if not spans:
        return []
    spans.sort()
    merged = [list(spans[0])]
    for x0, x1 in spans[1:]:
        if x0 <= merged[-1][1] + 4:
            merged[-1][1] = max(merged[-1][1], x1)
        else:
            merged.append([x0, x1])
    return [(a, b) for a, b in merged] if len(merged) >= 2 else []


def detect_unruled_tables(lines, exclude_boxes=()):
    """Detect borderless tables from ``lines`` (outside ``exclude_boxes``):
    runs of >= 3 consecutive rows sharing >= 2 aligned columns with short cells."""
    def inside_excluded(line):
        x0, y0, x1, y1 = _line_bbox(line)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        return any(bx0 <= cx <= bx1 and by0 <= cy <= by1 for bx0, by0, bx1, by1 in exclude_boxes)

    free = [l for l in lines if not inside_excluded(l)]
    if len(free) < 6:
        return []

    rows = _group_rows(free)
    tables = []
    run = []

    def flush():
        if len(run) < 3:
            run.clear()
            return
        cols = _x_columns(run)
        if len(cols) >= 2:
            avg_len = statistics.median(len(l.text) for row in run for l in row)
            if avg_len <= PROSE_CELL_CHARS:
                all_boxes = [_line_bbox(l) for row in run for l in row]
                box = (min(b[0] for b in all_boxes), min(b[1] for b in all_boxes),
                       max(b[2] for b in all_boxes), max(b[3] for b in all_boxes))
                tables.append({"box": box, "cols": cols, "rows": list(run)})
        run.clear()

    for row in rows:
        if len(row) >= 2:
            if run:
                prev_bot = max(_line_bbox(l)[3] for l in run[-1])
                cur_top = min(_line_bbox(l)[1] for l in row)
                row_h = max(10.0, statistics.median(_line_bbox(l)[3] - _line_bbox(l)[1] for l in row))
                if cur_top - prev_bot > row_h * 2.5:
                    flush()
            run.append(row)
        else:
            flush()
    flush()
    return tables


# -- cell-by-cell reading & numeric second pass --------------------------------

def _cell_has_ink(crop):
    import numpy as np

    grey = crop.convert("L")
    w, h = grey.size
    if w < 4 or h < 4:
        return False
    arr = np.asarray(grey, dtype=np.int16)
    med = int(np.median(arr))
    return int(np.count_nonzero(np.abs(arr - med) >= 24)) >= 8


def _crop_cell(image, box):
    """Crop ``box`` from ``image``, pad with white border, and upscale small
    cells so single digits and narrow numbers read cleanly."""
    from PIL import Image, ImageOps

    cx0, cy0, cx1, cy1 = box
    crop = image.crop((max(0, cx0), max(0, cy0), min(image.width, cx1), min(image.height, cy1)))
    scale = 2.0 if crop.height < 40 and crop.width * 2 <= 1600 else 1.0
    if scale > 1.0:
        crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))), Image.BICUBIC)
    padded = ImageOps.expand(crop, border=CELL_PAD, fill="white")
    return padded, scale


def _map_crop_lines(lines, box, scale):
    """Map lines recognised on a padded/scaled cell crop back to page coordinates."""
    cx0, cy0, cx1, cy1 = box
    mapped = []
    for line in lines:
        quad = [
            (min(cx1, max(cx0, cx0 + (x - CELL_PAD) / scale)),
             min(cy1, max(cy0, cy0 + (y - CELL_PAD) / scale)))
            for x, y in line.quad
        ]
        words = [
            Word(
                w.text,
                [(min(cx1, max(cx0, cx0 + (x - CELL_PAD) / scale)),
                  min(cy1, max(cy0, cy0 + (y - CELL_PAD) / scale)))
                 for x, y in w.quad],
                w.confidence,
            )
            for w in line.words
        ]
        if words:
            mapped.append(type(line)(line.text, quad, line.confidence, words))
    return mapped


def _normalise_line_numbers(line, strict_column=False):
    """Apply numeric-cell confusion fixes to words in ``line``."""
    changed = False
    new_words = []
    for w in line.words:
        fixed = normalise_numeric_token(w.text, strict_column=strict_column)
        if fixed != w.text:
            changed = True
            new_words.append(Word(fixed, w.quad, w.confidence))
        else:
            new_words.append(w)
    if not changed:
        return line
    return type(line)(" ".join(w.text for w in new_words), line.quad, line.confidence, new_words)


def _column_is_numeric(cell_texts):
    """Whether a table column's non-header cells are predominantly numeric (>= 60%)."""
    body = [t.strip() for t in cell_texts[1:] if t and t.strip()]
    if not body:
        return False
    hits = sum(1 for t in body if looks_numeric(t, strict_column=True))
    return hits / len(body) >= 0.6


def refine_tables(image, lines, read_crop_fn=None, max_cell_retries=48):
    """Detect ruled and unruled tables on a scanned page, read/refine cells
    individually (including a digits-focused second reading for numeric cells),
    and return all page lines with table cells in strict row-by-row order."""
    from pdfmanager import progress

    ruled = detect_ruled_tables(image) if image is not None else []
    ruled_boxes = [t["box"] for t in ruled]

    # Partition lines into ruled-table lines and outside lines.
    used_ids = set()
    blocks = []  # [(sort_y, [Line, ...])]
    cell_retries = 0

    for tbl in ruled:
        nrows = len(tbl["cells"])
        ncols = len(tbl["cells"][0]) if nrows else 0
        grid = [[[] for _ in range(ncols)] for _ in range(nrows)]
        bx0, by0, bx1, by1 = tbl["box"]

        # Assign existing full-page lines whose centres fall inside each cell.
        for line in lines:
            if id(line) in used_ids:
                continue
            lx0, ly0, lx1, ly1 = _line_bbox(line)
            cx, cy = (lx0 + lx1) / 2, (ly0 + ly1) / 2
            if not (bx0 <= cx <= bx1 and by0 <= cy <= by1):
                continue
            placed = False
            for r in range(nrows):
                ry0, ry1 = tbl["rows"][r], tbl["rows"][r + 1]
                if not (ry0 <= cy <= ry1):
                    continue
                for c in range(ncols):
                    cx0, cx1 = tbl["cols"][c], tbl["cols"][c + 1]
                    if cx0 <= cx <= cx1:
                        grid[r][c].append(line)
                        used_ids.add(id(line))
                        placed = True
                        break
                if placed:
                    break

        # Cell-by-cell reading for empty-with-ink, low-confidence, or confused numeric cells.
        if read_crop_fn is not None and image is not None:
            for r in range(nrows):
                if cell_retries >= max_cell_retries:
                    break
                for c in range(ncols):
                    if cell_retries >= max_cell_retries:
                        break
                    cell_lines = grid[r][c]
                    cell_box = tbl["cells"][r][c]
                    cell_text = " ".join(l.text for l in cell_lines).strip()
                    need_reread = False
                    if not cell_lines:
                        crop_check = image.crop((max(0, cell_box[0]), max(0, cell_box[1]),
                                                 min(image.width, cell_box[2]), min(image.height, cell_box[3])))
                        need_reread = _cell_has_ink(crop_check)
                    else:
                        min_conf = min(l.confidence for l in cell_lines)
                        has_confusion = looks_numeric(cell_text) and bool(_CONFUSABLE_LETTER_RE.search(cell_text))
                        need_reread = min_conf < 0.85 or has_confusion
                    if need_reread:
                        progress.check()
                        cell_retries += 1
                        padded, scale = _crop_cell(image, cell_box)
                        try:
                            retry_raw = read_crop_fn(padded)
                        except progress.Cancelled:
                            raise
                        except Exception:
                            retry_raw = []
                        retry_lines = _map_crop_lines(retry_raw, cell_box, scale)
                        if retry_lines:
                            if not cell_lines:
                                grid[r][c] = retry_lines
                            else:
                                old_score = sum(l.confidence * len(l.text) for l in cell_lines)
                                new_score = sum(l.confidence * len(l.text) for l in retry_lines)
                                new_text = " ".join(l.text for l in retry_lines).strip()
                                # Prefer the cell crop if it is cleaner pure digits or higher confidence.
                                if (is_pure_number(new_text) and not is_pure_number(cell_text)) or new_score > old_score:
                                    grid[r][c] = retry_lines

        # Identify numeric columns and apply numeric normalisation on non-header rows.
        num_cols = set()
        for c in range(ncols):
            col_texts = [" ".join(l.text for l in grid[r][c]).strip() for r in range(nrows)]
            if _column_is_numeric(col_texts):
                num_cols.add(c)

        ordered_tbl_lines = []
        for r in range(nrows):
            for c in range(ncols):
                strict = (r > 0) and (c in num_cols)
                cell_lines = sorted(grid[r][c], key=lambda l: (_line_bbox(l)[1], _line_bbox(l)[0]))
                for line in cell_lines:
                    if r > 0:
                        line = _normalise_line_numbers(line, strict_column=strict)
                    ordered_tbl_lines.append(line)
        if ordered_tbl_lines:
            blocks.append((by0, ordered_tbl_lines))

    # Now detect unruled tables among lines outside ruled tables.
    remaining = [l for l in lines if id(l) not in used_ids]
    unruled = detect_unruled_tables(remaining, exclude_boxes=ruled_boxes)
    for utbl in unruled:
        cols = utbl["cols"]
        ncols = len(cols)
        grid_rows = []
        for row in utbl["rows"]:
            buckets = [[] for _ in range(ncols)]
            for line in row:
                used_ids.add(id(line))
                lx0, _, lx1, _ = _line_bbox(line)
                cx = (lx0 + lx1) / 2
                best_c = min(range(ncols), key=lambda idx: abs(cx - (cols[idx][0] + cols[idx][1]) / 2))
                buckets[best_c].append(line)
            grid_rows.append(buckets)

        num_cols = set()
        for c in range(ncols):
            col_texts = [" ".join(l.text for l in row[c]).strip() for row in grid_rows]
            if _column_is_numeric(col_texts):
                num_cols.add(c)

        ordered_u = []
        for r, row in enumerate(grid_rows):
            for c in range(ncols):
                strict = (r > 0) and (c in num_cols)
                for line in sorted(row[c], key=lambda l: _line_bbox(l)[0]):
                    if r > 0:
                        line = _normalise_line_numbers(line, strict_column=strict)
                    ordered_u.append(line)
        if ordered_u:
            blocks.append((utbl["box"][1], ordered_u))

    if not blocks:
        return lines

    # Add remaining non-table lines at their vertical positions.
    for line in lines:
        if id(line) not in used_ids:
            blocks.append((_line_bbox(line)[1], [line]))
    blocks.sort(key=lambda item: item[0])
    return [line for _y, group in blocks for line in group]
