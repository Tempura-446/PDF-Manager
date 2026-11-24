# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Page text segmentation, reading order (XY-cut), block/list grouping, and table grids."""

import ctypes
import re
import statistics
from collections import Counter

from pdfmanager import core
from pdfmanager.accessibility.geometry import (
    BULLETS, CAPTION, DEFAULT_LANG, LANG_RE, LANGUAGE_WORDS,
    NUMBER_MARK, NUMBER_START, NUMBERED_HEADING, PAGE_NUMBER, TABLE_CAPTION,
    _View, _area, _distance, _overlap, _union,
)



# --------------------------------------------------------------------------
# Reading the text: characters -> segments (runs of words on one line)
# --------------------------------------------------------------------------

class _Seg:
    __slots__ = ("x0", "y0", "x1", "y1", "base", "size", "bold", "text", "chars", "role",
                 "zone", "rights")

    def __init__(self, x0, base, size, bold):
        self.x0, self.x1, self.base, self.size, self.bold = x0, x0, base, size, bold
        self.y0, self.y1 = base - 0.25 * size, base + 0.8 * size
        self.text, self.chars, self.role, self.zone = "", 0, None, None
        self.rights = []  # right edge of each character (spaces left out)

    @property
    def box(self):
        return (self.x0, self.y0, self.x1, self.y1)


def _text_direction(textpage):
    """The turn (0, 90, 180, 270) that makes most of the page's text run
    left to right, or None for a page without text. Usually 0; text set
    sideways, e.g. on a landscape page, needs a turn to read in order. The
    page's /Rotate isn't used for this: it turns what's seen, and text is
    upright in the page's own coordinates as often as on screen."""
    import pypdfium2.raw as raw

    votes = Counter()
    x, y = ctypes.c_double(), ctypes.c_double()
    last = None
    for i in range(min(textpage.count_chars(), 1500)):
        code = raw.FPDFText_GetUnicode(textpage, i)
        if raw.FPDFText_IsGenerated(textpage, i) == 1 or not 32 < code < 0x110000 \
                or chr(code).isspace():
            last = None
            continue
        raw.FPDFText_GetCharOrigin(textpage, i, ctypes.byref(x), ctypes.byref(y))
        size = raw.FPDFText_GetFontSize(textpage, i) or 10
        if last is not None:
            dx, dy = x.value - last[0], y.value - last[1]
            if 0 < abs(dx) + abs(dy) < 2 * size:
                if abs(dx) >= abs(dy):
                    votes[0 if dx > 0 else 180] += 1
                else:
                    votes[90 if dy > 0 else 270] += 1
        last = (x.value, y.value)
    return votes.most_common(1)[0][0] if votes else None


def _page_segments(textpage, view):
    """The page's text as segments (words on one line with no wide gap),
    in seen coordinates, in the order the text was drawn."""
    import pypdfium2.raw as raw

    count = textpage.count_chars()
    buf = ctypes.create_string_buffer(128)
    flags = ctypes.c_int()
    ox, oy = ctypes.c_double(), ctypes.c_double()
    fonts = {}
    loose = raw.FS_RECTF()
    segs, cur, space = [], None, False
    for i in range(count):
        if raw.FPDFText_IsGenerated(textpage, i) == 1:
            space = True
            continue
        code = raw.FPDFText_GetUnicode(textpage, i)
        ch = chr(code) if 0 < code < 0x110000 else "�"
        if ch.isspace() or ch == "\x00":
            space = True
            continue
        # the loose box spans the character's advance: no gaps inside words
        if raw.FPDFText_GetLooseCharBox(textpage, i, ctypes.byref(loose)) and loose.right > loose.left:
            left, bottom, right, top = loose.left, loose.bottom, loose.right, loose.top
        else:
            left, bottom, right, top = textpage.get_charbox(i)
        raw.FPDFText_GetCharOrigin(textpage, i, ctypes.byref(ox), ctypes.byref(oy))
        size = raw.FPDFText_GetFontSize(textpage, i)
        glyph = max(top - bottom, right - left)
        if size <= 0 or (glyph > 0 and not 0.3 * glyph <= size <= 6 * glyph):
            size = glyph if glyph > 0 else 10.0
        raw.FPDFText_GetFontInfo(textpage, i, buf, 128, ctypes.byref(flags))
        name = buf.value
        bold = fonts.get((name, flags.value))
        if bold is None:
            lower = name.lower()
            bold = any(w in lower for w in (b"bold", b"black", b"heavy", b"semibold", b"demi")) \
                or bool(flags.value & (1 << 18))
            fonts[(name, flags.value)] = bold
        if not bold and raw.FPDFText_GetFontWeight(textpage, i) >= 600:
            bold = True
        # in seen coordinates
        (l, b, r, t) = view.rect((left, bottom, right, top))
        bx, by = view.point(ox.value, oy.value)
        if r <= l:
            r = l + size * 0.5
        same = (cur is not None and abs(by - cur.base) < 0.3 * max(size, cur.size)
                and -0.35 * size <= l - cur.x1 <= 0.8 * max(size, cur.size))
        if not same:
            cur = _Seg(min(l, bx), by, size, bold)
            segs.append(cur)
        elif space or l - cur.x1 > 0.15 * size:
            cur.text += " "
        cur.text += ch
        cur.x1 = max(cur.x1, r)
        cur.rights.append(r)
        cur.chars += 1
        cur.bold = cur.bold and bold
        if size > cur.size:
            cur.size = size
            cur.y0, cur.y1 = cur.base - 0.25 * size, cur.base + 0.8 * size
        space = False
    return segs


# --------------------------------------------------------------------------
# Running headers and footers, body text size, language
# --------------------------------------------------------------------------

def _normalise(text):
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", text.lower())).strip()


def _mark_page_furniture(pages):
    """Mark segments at the top or bottom of pages that repeat on other
    pages (running headers and footers) or are page numbers: artifacts."""
    seen = Counter()
    for page in pages:
        h = page["size"][1]
        for seg in page["segs"]:
            if seg.y0 >= h * 0.9:
                seg.zone = "top"
            elif seg.y1 <= h * 0.1:
                seg.zone = "bottom"
            if seg.zone:
                seen[(seg.zone, _normalise(seg.text))] += 1
    several = len(pages) > 1

    def furniture(seg):
        repeated = several and seen[(seg.zone, _normalise(seg.text))] >= 2
        return repeated or bool(PAGE_NUMBER.match(seg.text.strip()))

    for page in pages:
        # a line counts as a whole: "Best for: <different on every page>"
        # is content even though "Best for:" repeats
        rows = []
        for seg in sorted((s for s in page["segs"] if s.zone), key=lambda s: -s.base):
            if rows and abs(rows[-1][0].base - seg.base) < 0.5 * seg.size:
                rows[-1].append(seg)
            else:
                rows.append([seg])
        for row in rows:
            if all(furniture(seg) for seg in row):
                for seg in row:
                    seg.role = "header" if seg.zone == "top" else "footer"


def _body_size(pages):
    sizes = Counter()
    for page in pages:
        for seg in page["segs"]:
            if seg.role is None:
                sizes[round(seg.size * 2) / 2] += seg.chars
    return sizes.most_common(1)[0][0] if sizes else 10.0


def guess_language(text):
    """A language code ("en", "de", ...) from the commonest short words in
    ``text``; "en" when it can't tell."""
    words = re.findall(r"[^\W\d_]+", text.lower())[:20000]
    if len(words) < 5:
        return DEFAULT_LANG
    counts = Counter(words)
    scores = {lang: sum(counts[w] for w in common) for lang, common in LANGUAGE_WORDS.items()}
    lang, score = max(scores.items(), key=lambda item: item[1])
    return lang if score >= max(3, len(words) * 0.04) else DEFAULT_LANG


# --------------------------------------------------------------------------
# Reading order: XY cut
# --------------------------------------------------------------------------

class _Item:
    """Something placed in reading order: a segment, table, figure."""

    __slots__ = ("kind", "box", "obj", "size")

    def __init__(self, kind, box, obj, size):
        self.kind, self.box, self.obj, self.size = kind, box, obj, size


def _gaps(spans, min_gap):
    """Gaps between the merged spans: [(size, middle)]."""
    spans = sorted(spans)
    found = []
    hi = spans[0][1]
    for lo, top in spans[1:]:
        if lo - hi > min_gap:
            found.append((lo - hi, (lo + hi) / 2))
        hi = max(hi, top)
    return found


def _rows(group):
    """Items with no gap left between them: row by row, top down, each
    row left to right."""
    rows = []
    for it in sorted(group, key=lambda it: -(it.box[1] + it.box[3])):
        middle = (it.box[1] + it.box[3]) / 2
        if rows and abs(rows[-1][0] - middle) < 0.5 * max(it.size, rows[-1][1]):
            rows[-1][2].append(it)
        else:
            rows.append([middle, it.size, [it]])
    return [it for row in rows for it in sorted(row[2], key=lambda it: it.box[0])]


def _reading_order(items, size):
    """Items in reading order, each with its column path (items in
    different columns never join into one paragraph)."""
    out = []

    def cut(group, path, depth):
        if len(group) <= 1 or depth > 80:
            out.extend((it, path) for it in _rows(group))
            return
        ys = _gaps([(it.box[1], it.box[3]) for it in group], 0.5)
        xs = _gaps([(it.box[0], it.box[2]) for it in group], max(0.9 * size, 6.0))
        left_all = min(it.box[0] for it in group)
        right_all = max(it.box[2] for it in group)
        width = right_all - left_all
        valid = []
        for gap, at in xs:
            left = [it for it in group if it.box[2] <= at]
            right = [it for it in group if it.box[0] >= at]
            if not left or not right:
                continue
            lw = max(it.box[2] for it in left) - min(it.box[0] for it in left)
            rw = max(it.box[2] for it in right) - min(it.box[0] for it in right)
            # columns are wide, and more than one line deep (a wide gap inside
            # a single line doesn't make columns)
            deep = all(max(it.box[3] for it in side) - min(it.box[1] for it in side)
                       >= 1.8 * max(it.size for it in side) for side in (left, right))
            if deep and min(lw, rw) >= max(0.15 * width, 2.5 * size):
                valid.append((gap, at))
        by = max((g for g, _ in ys), default=0.0)
        bx = max((g for g, _ in valid), default=0.0)
        if not by and not bx:
            out.extend((it, path) for it in _rows(group))
            return
        if bx and bx >= by:
            cuts = sorted(at for g, at in valid if g >= bx * 0.75)
            parts = [[] for _ in range(len(cuts) + 1)]
            for it in group:
                parts[sum(1 for at in cuts if it.box[0] >= at)].append(it)
            for n, part in enumerate(parts):
                if part:
                    cut(part, path + ((depth, n),), depth + 1)
        else:
            cuts = sorted((at for g, at in ys if g >= by * 0.75), reverse=True)
            parts = [[] for _ in range(len(cuts) + 1)]
            for it in group:
                parts[sum(1 for at in cuts if it.box[1] < at)].append(it)
            for part in parts:
                if part:
                    cut(part, path, depth + 1)

    if items:
        cut(list(items), (), 0)
    return out


# --------------------------------------------------------------------------
# Lines, paragraphs, lists
# --------------------------------------------------------------------------

class _Line:
    def __init__(self, seg, path):
        self.segs, self.path = [seg], path

    def add(self, seg):
        self.segs.append(seg)

    def finish(self):
        self.segs.sort(key=lambda s: s.x0)
        main = max(self.segs, key=lambda s: s.chars)
        self.size, self.base = main.size, main.base
        self.x0 = min(s.x0 for s in self.segs)
        self.x1 = max(s.x1 for s in self.segs)
        self.y0 = min(s.y0 for s in self.segs)
        self.y1 = max(s.y1 for s in self.segs)
        self.bold = all(s.bold for s in self.segs)
        self.text = " ".join(s.text for s in self.segs)
        return self


def _lines(ordered):
    """Consecutive segments of one row (and column) as lines."""
    lines = []
    for seg, path in ordered:
        last = lines[-1] if lines else None
        if last and last.path == path:
            prev = last.segs[-1]
            middle = (seg.y0 + seg.y1) / 2
            if abs(middle - (prev.y0 + prev.y1) / 2) < 0.45 * max(seg.size, prev.size) \
                    and seg.x0 >= prev.x1 - 0.5 * seg.size:
                last.add(seg)
                continue
        lines.append(_Line(seg, path))
    return [line.finish() for line in lines]


def _marker(line):
    """("bullet"/"number", segment, characters) when a line starts a list
    item: the marker's own segment when it stands apart from the text,
    else None and how many characters of the first segment it is."""
    first = line.segs[0]
    text = first.text.strip()
    if len(line.segs) > 1:
        if len(text) == 1 and (text in BULLETS or (not text.isalnum() and text not in "([\"'“‘")):
            return "bullet", first, 0
        if NUMBER_MARK.match(text):
            return "number", first, 0
    if len(text) > 2 and text[0] in BULLETS and text[1] == " " and text[0] not in "-*":
        return "bullet", None, 1
    if len(text) > 2 and text[0] in "-*" and text[1] == " ":
        return "bullet", None, 1
    m = NUMBER_START.match(text)
    if m:
        return "number", None, len(m.group(0).replace(" ", ""))
    return None


class _Block:
    """A paragraph, heading candidate or list item, built from lines."""

    def __init__(self, kind, line, marker=None):
        self.kind = kind          # "p" or "li"
        self.lines = [line]
        self.marker = marker      # (kind, segment or None, characters) for "li"
        self.label_x = line.x0
        rest = [s for s in line.segs if marker is None or s is not marker[1]]
        self.text_x = min(s.x0 for s in rest) if rest else line.x0
        if marker and marker[1] is None and 0 < marker[2] <= len(line.segs[0].rights):
            first = line.segs[0]
            self.text_x = first.rights[marker[2] - 1] + 0.2 * first.size

    @property
    def size(self):
        return max(line.size for line in self.lines)

    @property
    def bold(self):
        return all(line.bold for line in self.lines)

    @property
    def text(self):
        out = ""
        for line in self.lines:
            if out.endswith("-") and line.text[:1].islower():
                out = out[:-1] + line.text
            else:
                out = (out + " " + line.text).strip()
        return out

    @property
    def box(self):
        return _union((line.x0, line.y0, line.x1, line.y1) for line in self.lines)


def _continues(block, line, right_edge):
    """Whether ``line`` goes on with ``block`` (rather than starting a new
    paragraph): same column and look, the usual line spacing, lined up."""
    last = block.lines[-1]
    size = max(last.size, line.size)
    if line.path != last.path:
        return False
    if abs(line.size - block.lines[0].size) > 0.15 * block.lines[0].size or line.bold != last.bold:
        return False
    pitch = last.base - line.base
    if pitch <= 0 or pitch > 1.7 * size:
        return False
    if len(block.lines) >= 2:
        usual = block.lines[-2].base - last.base
        if pitch > usual * 1.3 + 1:
            return False
    if line.x1 < block.box[0] - size or line.x0 > max(l.x1 for l in block.lines) + size:
        return False
    if block.kind == "li":
        # wrapped lines line up with the item's text, or (without a hanging
        # indent) with its number or bullet
        if not block.label_x - 0.6 * size <= line.x0 <= block.text_x + 3 * size:
            return False
    # a short line ending a sentence ends its paragraph
    if last.x1 < right_edge - max(2.5 * size, 0.12 * (right_edge - block.box[0])) \
            and re.search(r"[.!?:;\"”’)]$", last.text):
        return False
    if block.kind == "li":
        return True
    if line.x0 > last.x0 + 0.8 * size:
        return False  # an indented first line starts a paragraph
    if line.x0 < last.x0 - 0.8 * size and len(block.lines) >= 2:
        return False
    return True


def _text_blocks(lines, body):
    """Paragraphs and list items from lines in reading order."""
    right = {}
    for line in lines:
        right[line.path] = max(right.get(line.path, 0), line.x1)
    blocks = []
    for line in lines:
        marker = _marker(line)
        if marker and (line.size >= body * 1.12 or (line.bold and marker[0] == "number")):
            marker = None  # "2. Methods" set as a heading
        last = blocks[-1] if blocks else None
        if marker is None and last is not None and _continues(last, line, right[line.path]):
            last.lines.append(line)
            continue
        blocks.append(_Block("li" if marker else "p", line, marker))
    return blocks


# --------------------------------------------------------------------------
# Tables (pdfplumber) as grids of cells
# --------------------------------------------------------------------------

def _plumber_tables(ppage, view):
    """Tables on a pdfplumber page: [{"box", "rows", "plumber"}] in seen
    coordinates. Ruled tables come with the boxes of their cells, row by row
    (None where a merged cell covers one); borderless ones get theirs from
    the text later ("rows": None). "plumber": the box in pdfplumber's
    coordinates."""
    from pdfmanager import tables

    found = []
    try:
        settings = dict(tables.RULED, explicit_vertical_lines=tables._closing_edges(ppage))
        for table in ppage.find_tables(settings):
            raw_rows = table.extract()
            if tables._stacked(raw_rows) or not tables._tidy(raw_rows):
                continue
            rows = []
            for row in table.rows:
                rows.append([view.rect(tables._pdf_bbox(ppage, c)) if c else None for c in row.cells])
            found.append({"box": view.rect(tables._pdf_bbox(ppage, table.bbox)), "rows": rows,
                          "plumber": table.bbox})
        words = [w for w in ppage.extract_words(extra_attrs=["size"])
                 if w.get("upright", True)
                 and not any(tables._inside(w, t["plumber"]) for t in found)]
        for run in tables._runs(tables._lines(words)):
            table = tables._aligned_table(run)
            if table:
                box = view.rect(tables._pdf_bbox(ppage, table["bbox"]))
                if not any(_overlap(box, t["box"]) > 0.5 * _area(box) for t in found):
                    found.append({"box": box, "rows": None, "plumber": table["bbox"]})
    except Exception:  # pdfplumber trips on some damaged pages: no tables then
        return found
    return found


def _may_have_tables(segs, units):
    """Whether a page is worth searching for tables (pdfplumber is slow): it
    has ruled lines or boxes, or several lines with wide gaps in them."""
    if not segs:
        return False
    if sum(1 for u in units if u.kind == "path") >= 4:
        return True
    rows = Counter(round(s.base) for s in segs if s.role is None)
    return sum(1 for n in rows.values() if n >= 2) >= 3


def _ruled_grid(rows):
    """Cells with their column and row spans, from pdfplumber's cell boxes."""
    cells = [c for row in rows for c in row if c]
    if not cells:
        return []
    cols = sorted({round(c[0], 1) for c in cells})
    tops = sorted({round(c[3], 1) for c in cells}, reverse=True)
    grid = []
    for row in rows:
        out = []
        for c in row:
            if not c:
                continue
            colspan = max(1, sum(1 for x in cols if c[0] - 1 <= x < c[2] - 1))
            rowspan = max(1, sum(1 for y in tops if c[1] + 1 < y <= c[3] + 1))
            out.append({"box": c, "colspan": colspan, "rowspan": rowspan})
        if out:
            grid.append(out)
    return grid


def _aligned_grid(box, segs):
    """Rows and columns of a borderless table from the segments in it, and
    the segments used. Lines in a bigger font (a heading after the table)
    and lone lines at either end aren't part of it."""
    inside = sorted((s for s in segs if s.role is None and _overlap(s.box, box) > 0.5 * _area(s.box)),
                    key=lambda s: (-s.base, s.x0))
    rows = []
    for seg in inside:
        if rows and abs(rows[-1][0].base - seg.base) < 0.5 * seg.size:
            rows[-1].append(seg)
        else:
            rows.append([seg])
    if not rows:
        return [], []
    usual = statistics.median(s.size for s in inside)
    for n, row in enumerate(rows):
        if n and max(s.size for s in row) > usual * 1.25:
            rows = rows[:n]
            break
    while rows and len(rows[0]) < 2:
        rows.pop(0)
    while rows and len(rows[-1]) < 2:
        rows.pop()
    if len(rows) < 2:
        return [], []
    widest = max(rows, key=len)
    starts = sorted(s.x0 for s in widest)
    grid = []
    for row in rows:
        cells = [[] for _ in starts]
        for seg in row:
            col = max([i for i, x in enumerate(starts) if x <= seg.x0 + seg.size] or [0])
            cells[col].append(seg)
        grid.append([{"box": _union(s.box for s in c) if c else None, "colspan": 1, "rowspan": 1}
                     for c in cells])
    return grid, [s for row in rows for s in row]
