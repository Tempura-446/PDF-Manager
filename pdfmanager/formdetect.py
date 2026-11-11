"""Find where a flat form should get fillable fields.

A form meant for a pen shows where to write: a line to write on
("Name ________"), an empty box or table cell, a small square to tick.
Each page is drawn as a picture and its lines are traced from the pixels,
so drawn lines, boxes, rows of underscores and scanned paper forms are all
found the same way. The page's text names the fields ("Name:" before a
line gives a field called "Name") and tells signature lines apart.

    found = detect(path)    # {1-based page: [{"rect", "type", "name"}, ...]}

Rectangles are PDF points (origin bottom left, before the page's
rotation), as forms.add_fields takes them; "border" is False because the
form already shows where to write. A field already in the file isn't
found again.
"""

import bisect
import re

from pdfmanager import core, forms, progress

SCALE = 2           # pixels per point while tracing (144 dpi)
LINE = 190          # a pixel this dark (0-255) or darker can be part of a line
INK = 160           # ...and this dark counts as something written or printed
MIN_LINE = 36       # points: the shortest line to write on
MAX_THICK = 4       # points: thicker than this is a bar, not a line
MAX_PAGES = 300
BOX_GLYPHS = set("☐□▢❏❐❑❒⬜")
CIRCLE_GLYPHS = set("○◯❍⚪")
SIGNATURE = re.compile(r"\b(signature|signed|sign here|unterschrift|firma)\b", re.I)


# --------------------------------------------------------------------------
# Lines from pixels
# --------------------------------------------------------------------------

_RUN = re.compile(rb"\x00+(?:[^\x00]\x00+)*")  # dark pixels, single-pixel gaps bridged


def _runs(data, width, height, min_len):
    """Horizontal runs of dark pixels: [(row, x0, x1)] (x1 exclusive)."""
    out = []
    for y in range(height):
        row = data[y * width:(y + 1) * width]
        if b"\x00" not in row:
            continue
        for m in _RUN.finditer(row):
            if m.end() - m.start() >= min_len:
                out.append((y, m.start(), m.end()))
    return out


def _segments(runs, slack=2):
    """Runs in neighbouring rows joined into lines: [(y, x0, x1, thickness,
    top, bottom)] with y the line's middle row and top/bottom its first and
    last rows. A slightly slanted line (a scan) comes in pieces a row
    apart; they're joined end to end."""
    done, prev, prev_keys, cur, row = [], [], [], [], None
    for y, x0, x1 in runs:  # (runs come row by row, left to right)
        if y != row:
            if row is not None and y != row + 1:
                cur, done = [], done + cur
            done += [seg for seg in prev if seg[4] != row]  # not carried on
            prev = cur if row is not None and y == row + 1 else []
            prev_keys = [seg[1] for seg in prev]
            cur, row = [], y
        carried = None
        for seg in prev[bisect.bisect_left(prev_keys, x0 - slack):]:
            if seg[1] > x0 + slack:
                break
            if seg[4] == y - 1 and abs(seg[2] - x1) <= slack:
                carried = seg
                break
        if carried is None:
            cur.append([y, x0, x1, 0, y])  # first row, x0, x1, (unused), last row
        else:
            carried[1], carried[2], carried[4] = min(carried[1], x0), max(carried[2], x1), y
            cur.append(carried)
    done += [seg for seg in prev if seg[4] != row] + cur
    # Pieces of one slanted line: end to end (or overlapping a little), the
    # next one a row or two up or down from where the last one ended.
    joined, by_row = [], {}
    for top, x0, x1, _, bottom in sorted(done, key=lambda s: (s[1], s[0])):
        y = (top + bottom) / 2
        match = None
        for r in range(int(y) - 3, int(y) + 4):
            for j in by_row.get(r, ()):
                if abs(j[4] - y) <= 2.5 and x1 > j[2] \
                        and j[2] - max(slack, (x1 - x0) // 2) <= x0 <= j[2] + slack * 2:
                    match = j
                    break
            if match:
                break
        if match is None:
            match = [y, x0, x1, bottom - top + 1, y, top, bottom, x1 - x0]
            joined.append(match)
        else:
            by_row[int(match[4])].remove(match)
            weight = match[7] + (x1 - x0)
            match[0] = (match[0] * match[7] + y * (x1 - x0)) / weight
            match[2], match[3], match[4] = x1, max(match[3], bottom - top + 1), y
            match[5], match[6], match[7] = min(match[5], top), max(match[6], bottom), weight
        by_row.setdefault(int(match[4]), []).append(match)
    # A line drawn over two rows of pixels (anti-aliased, or slanted) can
    # come out twice, side by side: keep one.
    kept, near = [], {}
    for line in sorted(((j[0], j[1], j[2], j[3], j[5], j[6]) for j in joined),
                       key=lambda s: -(s[2] - s[1])):
        y, x0, x1 = line[:3]
        twin = next((k for r in range(int(y) - 3, int(y) + 4) for k in near.get(r, ())
                     if abs(k[0] - y) <= 2.5 and min(k[2], x1) - max(k[1], x0) >= 0.8 * (x1 - x0)),
                    None)
        if twin is None:
            twin = list(line)
            kept.append(twin)
            near.setdefault(int(y), []).append(twin)
        else:
            twin[3] = max(twin[3], line[3])
            twin[4], twin[5] = min(twin[4], line[4]), max(twin[5], line[5])
    return sorted(tuple(k) for k in kept)


def _lines(image, min_len):
    """Horizontal and vertical lines in a page image (mode "L"):
    ([(y, x0, x1, t, top, bottom)], [(x, y0, y1, t, left, right)]) in pixels."""
    from PIL import Image

    mask = image.point(lambda v: 0 if v < LINE else 255)
    w, h = mask.size
    flat = _segments(_runs(mask.tobytes(), w, h, min_len))
    turned = mask.transpose(Image.Transpose.TRANSPOSE)  # columns become rows
    upright = _segments(_runs(turned.tobytes(), h, w, min_len))
    limit = MAX_THICK * SCALE
    return ([s for s in flat if s[3] <= limit], [s for s in upright if s[3] <= limit])


# --------------------------------------------------------------------------
# Boxes from lines
# --------------------------------------------------------------------------

class _Sides:
    """Lines sorted by their position across, to find the ones along a
    stretch quickly."""

    def __init__(self, lines):
        self.lines = sorted(lines, key=lambda s: s[4])
        self.tops = [s[4] for s in self.lines]
        self.reach = max((s[5] - s[4] for s in self.lines), default=0)

    def along(self, at, a, b, tol):
        """Whether a line runs at ``at`` along most of a..b."""
        i = bisect.bisect_left(self.tops, at - tol - self.reach)
        for _y, s, e, _t, ltop, lbottom in self.lines[i:]:
            if ltop > at + tol:
                break
            if lbottom + tol >= at and min(e, b) - max(s, a) >= 0.75 * (b - a):
                return True
        return False


def _cells(flat, upright, tol=3):
    """The smallest closed boxes the lines make (like table cells):
    [(x0, y0, x1, y1)] in pixels, each side a line along most of its length
    (so "C" next to "o" isn't a box)."""
    by_x = sorted(range(len(upright)), key=lambda i: upright[i][0])
    xs = [upright[i][0] for i in by_x]
    points = {}
    for hi, (y, x0, x1, _ht, htop, hbottom) in enumerate(flat):
        lean = (hbottom - htop) / 2  # a slanted line is higher at one end than the other
        start = bisect.bisect_left(xs, x0 - tol - MAX_THICK * SCALE)
        for k in range(start, len(by_x)):
            vi = by_x[k]
            x, y0, y1, _vt, vleft, vright = upright[vi]
            if x > x1 + tol + MAX_THICK * SCALE:
                break
            if x0 - tol - (vright - vleft) / 2 <= x <= x1 + tol + (vright - vleft) / 2 \
                    and y0 - tol - lean <= y <= y1 + tol + lean:
                hs, vs = points.setdefault((round(x), round(y)), (set(), set()))
                hs.add(hi)
                vs.add(vi)
    on_h, on_v, grid = {}, {}, {}
    for p, (hs, vs) in points.items():
        for h in hs:
            on_h.setdefault(h, []).append(p)
        for v in vs:
            on_v.setdefault(v, []).append(p)
        grid.setdefault((p[0] // 8, p[1] // 8), []).append(p)

    def corner(x, y):
        for gx in (x // 8 - 1, x // 8, x // 8 + 1):
            for gy in (y // 8 - 1, y // 8, y // 8 + 1):
                for q in grid.get((gx, gy), ()):
                    if abs(q[0] - x) <= tol and abs(q[1] - y) <= tol:
                        yield q

    flat_sides, upright_sides = _Sides(flat), _Sides(upright)
    cells = []
    for (px, py), (hs, vs) in points.items():
        below = sorted({q for v in vs for q in on_v[v] if q[1] > py + tol}, key=lambda q: q[1])
        right = sorted({q for h in hs for q in on_h[h] if q[0] > px + tol}, key=lambda q: q[0])
        found = None
        for b in below[:12]:
            for r in right[:12]:
                for c in corner(r[0], b[1]):
                    if points[c][0] & points[b][0] and points[c][1] & points[r][1]:
                        found = (px, py, c[0], c[1])
                        break
                if found:
                    break
            if found:
                break
        if found:
            x0, y0, x1, y1 = found
            if x1 > x0 and y1 > y0 and flat_sides.along(y0, x0, x1, tol) \
                    and flat_sides.along(y1, x0, x1, tol) and upright_sides.along(x0, y0, y1, tol) \
                    and upright_sides.along(x1, y0, y1, tol):
                cells.append(found)
    return sorted(set(cells), key=lambda c: (c[1], c[0]))


# --------------------------------------------------------------------------
# Empty space
# --------------------------------------------------------------------------

class _Ink:
    """How much is written where, in a page image."""

    def __init__(self, image):
        self.mask = image.point(lambda v: 0 if v < INK else 255)
        self.w, self.h = self.mask.size

    def _crop(self, x0, y0, x1, y1):
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(self.w, int(x1)), min(self.h, int(y1))
        if x1 <= x0 or y1 <= y0:
            return None
        return self.mask.crop((x0, y0, x1, y1))

    def count(self, box):
        crop = self._crop(*box)
        return 0 if crop is None else crop.histogram()[0]

    def empty(self, box, specks=2):
        crop = self._crop(*box)
        return crop is not None and crop.histogram()[0] <= specks

    def rows(self, box):
        """Dark pixels in each row of ``box``."""
        crop = self._crop(*box)
        if crop is None:
            return []
        w, data = crop.size[0], crop.tobytes()
        return [data[i:i + w].count(0) for i in range(0, len(data), w)]

    def columns(self, box):
        from PIL import Image

        crop = self._crop(*box)
        if crop is None:
            return []
        turned = crop.transpose(Image.Transpose.TRANSPOSE)
        w, data = turned.size[0], turned.tobytes()
        return [data[i:i + w].count(0) for i in range(0, len(data), w)]


def _empty_runs(counts, min_len, specks=0):
    """(start, end) of the runs of near-empty entries at least min_len long."""
    runs, start = [], None
    for i, c in enumerate(counts + [specks + 1]):
        if c <= specks:
            if start is None:
                start = i
        elif start is not None:
            if i - start >= min_len:
                runs.append((start, i))
            start = None
    return runs


# --------------------------------------------------------------------------
# Words, for names
# --------------------------------------------------------------------------

def _chars(textpage):
    """The page's characters with their boxes in points: [(char, box)]."""
    text = textpage.get_text_range()
    out = []
    for i in range(min(len(text), textpage.count_chars())):
        out.append((text[i], textpage.get_charbox(i)))
    return out


def _words(chars, to_px):
    """Words on the page with their boxes in pixels: [x0, y0, x1, y1, text,
    [box of each character]]."""
    words, cur = [], None
    for ch, (x0, y0, x1, y1) in chars:
        if not ch or ch.isspace() or (x1 - x0 <= 0 and y1 - y0 <= 0):
            cur = None
            continue
        ax, ay = to_px(x0, y0)
        bx, by = to_px(x1, y1)
        box = [min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)]
        height = box[3] - box[1]
        overlap = min(cur[3], box[3]) - max(cur[1], box[1]) if cur else 0
        if cur and overlap > 0 and -2 <= box[0] - cur[2] < max(height, cur[3] - cur[1]) * 0.35:
            cur[0], cur[1] = min(cur[0], box[0]), min(cur[1], box[1])
            cur[2], cur[3] = max(cur[2], box[2]), max(cur[3], box[3])
            cur[4] += ch
            cur[5].append(box)
        else:
            cur = [*box, ch, [box]]
            words.append(cur)
    return words


def _clean(label):
    label = re.sub(r"[_.…]{2,}", " ", label)
    label = re.sub(r"\s+", " ", label).strip(" :*#()[]-–—_.\t")
    return label[:40].strip()


def _row_words(words, top, bottom):
    mid = lambda w: (w[1] + w[3]) / 2
    return [w for w in words if top <= mid(w) <= bottom]


def _join_leftwards(row, edge, max_gap):
    """The words (or the parts of words) that end just left of ``edge``,
    read left to right: "Name:" of "Name:_____" when the field starts at
    the underscores."""
    parts = []
    for w in row:
        if w[0] >= edge - 2:
            continue
        chars = [(ch, box) for ch, box in zip(w[4], w[5]) if box[2] <= edge + 2]
        if chars:
            parts.append((chars[0][1][0], chars[-1][1][2], "".join(ch for ch, _ in chars)))
    picked, x = [], edge
    for x0, x1, text in sorted(parts, key=lambda p: -p[1]):
        if x - x1 > max_gap or len(picked) >= 6:
            break
        picked.append(text)
        x = x0
    return " ".join(reversed(picked))


def _join_rightwards(row, edge, max_gap):
    picked, x = [], edge
    for w in sorted((w for w in row if w[0] >= edge - 2), key=lambda w: w[0]):
        if w[0] - x > max_gap or len(picked) >= 5 or set(w[4]) & (BOX_GLYPHS | CIRCLE_GLYPHS):
            break
        picked.append(w)
        x = w[2]
    return " ".join(w[4] for w in picked)


def _label(words, box, kind, pt):
    """A name for a field from the text around it."""
    x0, y0, x1, y1 = box
    h = y1 - y0
    row = _row_words(words, y0 - 2 * pt, y1 + 6 * pt)
    if kind in ("checkbox", "radio"):
        text = _join_rightwards(row, x1, 24 * pt) or _join_leftwards(row, x0, 24 * pt)
    else:
        text = _join_leftwards(row, x0, 40 * pt)
        if not _clean(text):
            # Above it: a caption over the line or in the top of its box.
            above = [w for w in words if y0 - 18 * pt <= w[3] <= y0 + 2 * pt
                     and w[2] > x0 - 4 * pt and w[0] < x1]
            if above:
                last = max(w[3] for w in above)
                text = " ".join(w[4] for w in sorted(
                    (w for w in above if w[3] >= last - h), key=lambda w: w[0]))[:80]
    return _clean(text)


# --------------------------------------------------------------------------
# A page
# --------------------------------------------------------------------------

def _transform(page, turn):
    """Point (PDF) <-> pixel (top left origin) for a page shown turned
    ``turn`` degrees clockwise."""
    x0, y0, x1, y1 = page.get_cropbox()
    s = SCALE

    def to_px(x, y):
        if turn == 90:
            return (y - y0) * s, (x - x0) * s
        if turn == 180:
            return (x1 - x) * s, (y - y0) * s
        if turn == 270:
            return (y1 - y) * s, (x1 - x) * s
        return (x - x0) * s, (y1 - y) * s

    def to_pdf(px, py):
        px, py = px / s, py / s
        if turn == 90:
            return x0 + py, y0 + px
        if turn == 180:
            return x1 - px, y0 + py
        if turn == 270:
            return x1 - py, y1 - px
        return x0 + px, y1 - py

    return to_px, to_pdf


def _dot_leaders(chars, pt):
    """Rows of eight or more dots, as lines at the top of the dots."""
    runs, cur = [], None
    for box, ch in chars:
        if ch in ".…·" and cur and abs(box[1] - cur[1]) <= 2 * pt and 0 <= box[0] - cur[2] <= 5 * pt:
            cur[2], cur[1] = box[2], min(cur[1], box[1])
            cur[3] += 1 if ch != "…" else 3
        elif ch in ".…·":
            cur = [box[0], box[1], box[2], 1 if ch != "…" else 3]
            runs.append(cur)
        else:
            cur = None
    return [(y, x0, x1, 0, y, y) for x0, y, x1, n in runs if n >= 8]


def overlap(a, b):
    """The share of the smaller box that the two boxes have in common."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return w * h / smaller if smaller > 0 else 0.0


def _glyph_boxes(words, glyphs):
    """Boxes of the characters in ``glyphs`` (☐, ○...), made square."""
    out = []
    for w in words:
        for ch, box in zip(w[4], w[5]):
            if ch in glyphs:
                bx0, by0, bx1, by1 = box
                side = max(bx1 - bx0, by1 - by0)
                cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
                out.append((cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2))
    return out


def _reading_turn(page, chars):
    """The way to turn the page so its text reads across: the page's own
    turn, unless its text runs up or down the page as shown."""
    own = page.get_rotation() % 360
    best, best_score = own, None
    for turn in dict.fromkeys((own, 0, 90, 180, 270)):
        words = _words(chars, _transform(page, turn)[0])
        score = sum(len(w[4]) for w in words if len(w[4]) >= 3)
        if best_score is None or score > best_score * 1.5 + 10:
            best, best_score = turn, score
    return best


# The reader's quick look at a file it has just opened (nobody asked): pages
# that can't be a form to fill in aren't traced at all. A drawing sheet is
# far bigger than any form, and a page with this many straight strokes is a
# drawing or a dense table of figures; tracing either takes a minute or more.
QUICK_MAX_SIDE = 1500      # points: just over the long side of A3 and Tabloid
QUICK_MAX_STROKES = 12000


def _blank_pictures(page, image, to_px):
    """Paint the page's photos and screenshots white before lines are
    looked for: the edges in a picture are not boxes to write in (found on
    Windows: a brochure and a software guide were offered "Make it
    fillable" for boxes inside their pictures). A picture filling most of
    the page is its scan and is left alone."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    from PIL import ImageDraw

    width, height = image.size
    draw = ImageDraw.Draw(image)
    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=3):
        try:
            left, bottom, right, top = obj.get_bounds()
        except pdfium.PdfiumError:
            continue
        xs, ys = zip(to_px(left, bottom), to_px(right, top))
        box = (max(min(xs), 0), max(min(ys), 0), min(max(xs), width), min(max(ys), height))
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        if (box[2] - box[0]) * (box[3] - box[1]) >= 0.6 * width * height:
            continue  # the page's own scan
        draw.rectangle(box, fill=255)


def _detect_page(page, textpage, quick=False):
    if quick and max(page.get_size()) > QUICK_MAX_SIDE:
        return []
    chars = _chars(textpage)
    turn = _reading_turn(page, chars)
    to_px, to_pdf = _transform(page, turn)
    pt = SCALE
    extra = (turn - page.get_rotation()) % 360
    image = page.render(scale=SCALE, grayscale=True, rotation=extra).to_pil().convert("L")
    page_w, page_h = image.size
    if quick:
        _blank_pictures(page, image, to_px)
    ink = _Ink(image)
    words = _words(chars, to_px)
    chars = [(box, ch) for w in words for ch, box in zip(w[4], w[5])]
    flat, upright = _lines(image, 5 * pt)
    if quick and len(flat) + len(upright) > QUICK_MAX_STROKES:
        return []
    # How slanted the page is (a scan): rise per pixel of the long lines.
    slants = sorted((s[5] - s[4] + 1 - s[3]) / (s[2] - s[1]) for s in flat if s[2] - s[1] > 100 * pt)
    slant = slants[len(slants) // 2] if slants else 0

    found = []  # (box in pixels, kind)

    # Closed boxes: checkboxes and boxes or table cells to write in.
    used = set()
    for cell in _cells(flat, upright):
        x0, y0, x1, y1 = cell
        w, h = (x1 - x0) / pt, (y1 - y0) / pt
        if w < 5 or h < 5 or (w * h) * pt * pt > 0.4 * page_w * page_h:
            continue
        for i, (y, a, b, _t, ltop, lbottom) in enumerate(flat):
            if (ltop - 4 <= y0 <= lbottom + 4 or ltop - 4 <= y1 <= lbottom + 4) and a <= x1 and b >= x0:
                used.add(i)
        dx, dy = 3 + slant * (y1 - y0), 3 + slant * (x1 - x0)  # clear of its edges
        inner = (x0 + dx, y0 + dy, x1 - dx, y1 - dy)
        if w <= 24 and h <= 24 and 0.75 <= w / h <= 1.33:
            if w < 6 or h < 6:
                continue
            # A tick box, unless it's a letter shaped like one (口).
            inside = [ch for box, ch in chars if overlap(box, cell) > 0.5]
            if any(ch.isalnum() for ch in inside):
                continue
            if ink.empty(inner, specks=1):
                found.append(((x0 + 1, y0 + 1, x1 - 1, y1 - 1), "checkbox"))
            continue
        if h < 9:
            continue
        if ink.empty(inner, specks=3):
            kind = "multiline" if h > 40 and w > 60 else "text"
            found.append(((x0 + 2, y0 + 2, x1 - 2, y1 - 2), kind))
            continue
        # Writing on part of it: a caption at the top or on the left.
        rows = ink.rows(inner)
        free_below = next((i for i, c in enumerate(reversed(rows)) if c > 1), len(rows))
        cols = ink.columns(inner)
        free_right = next((i for i, c in enumerate(reversed(cols)) if c > 1), len(cols))
        below = (x0 + 2, y1 - dy - free_below + 2 * pt, x1 - 2, y1 - 2)
        right = (x1 - dx - free_right + 3 * pt, y0 + 2, x1 - 2, y1 - 2)
        options = []
        if free_below >= 11 * pt:
            options.append((below, "multiline" if free_below > 40 * pt and w > 60 else "text"))
        caption = " ".join(w[4] for w in words if overlap(w[:4], inner) > 0.5).strip()
        if free_right >= 40 * pt and h >= 9 and caption.endswith(":"):
            options.append((right, "multiline" if h > 40 and free_right > 60 * pt else "text"))
        if options:
            found.append(max(options, key=lambda o: (o[0][2] - o[0][0]) * (o[0][3] - o[0][1])))

    # Lines to write on: the space above a line that isn't part of a box.
    dotted = _dot_leaders(chars, pt)  # "Name ........" counts as a line too
    lines = list(flat) + dotted
    for i, (y, x0, x1, t, ltop, _lbottom) in enumerate(lines):
        if i in used or x1 - x0 < MIN_LINE * pt:
            continue
        if x1 - x0 > 0.7 * page_w and not _clean(_join_leftwards(
                _row_words(words, y - 10 * pt, y + 4 * pt), x0 + 2 * pt, 40 * pt)):
            # A long line with no "Label:" before it is a divider, unless
            # it's one of several lines to write on (a "Comments" area).
            if not any(8 * pt <= abs(o[0] - y) <= 40 * pt and abs(o[1] - x0) <= 6 * pt
                       and abs(o[2] - x1) <= 6 * pt for o in lines):
                continue
        top = ltop - 1
        band = (x0, top - 14 * pt, x1, top)
        for a, b in _empty_runs(ink.columns(band), 30 * pt, specks=1):
            fx0, fx1 = x0 + a, x0 + b
            rows = ink.rows((fx0, top - 22 * pt, fx1, top))
            clear = next((n for n, c in enumerate(reversed(rows)) if c > 1), len(rows))
            if clear < 9 * pt:
                continue
            fx0 = fx0 + 2 * pt if a else fx0
            found.append(((fx0, top - min(clear, 20 * pt) + pt, fx1 - (2 * pt if b < x1 - x0 else 0),
                           top), "text"))

    # Tick boxes and round buttons drawn as characters.
    for box in _glyph_boxes(words, BOX_GLYPHS):
        found.append((box, "checkbox"))
    circles = _glyph_boxes(words, CIRCLE_GLYPHS)

    # Fewest overlaps: smaller ones first (a box inside a frame wins).
    keep = []
    for box, kind in sorted(found, key=lambda f: (f[0][2] - f[0][0]) * (f[0][3] - f[0][1])):
        if (box[2] - box[0]) < 4 * pt or (box[3] - box[1]) < 4 * pt:
            continue
        if any(overlap(box, k[0]) > 0.3 for k in keep):
            continue
        keep.append((box, kind))

    fields = []
    for box, kind in keep:
        name = _label(words, box, kind, pt)
        if kind in ("text", "multiline") and SIGNATURE.search(name) and box[2] - box[0] >= 60 * pt:
            kind = "signature"
        fields.append({"box": box, "type": kind, "name": name})
    fields += _radio_groups(circles, words, pt, keep)

    out = []
    for f in sorted(fields, key=lambda f: (round(f["box"][1] / (6 * pt)), f["box"][0])):
        ax, ay = to_pdf(f["box"][0], f["box"][1])
        bx, by = to_pdf(f["box"][2], f["box"][3])
        item = {"rect": [round(min(ax, bx), 1), round(min(ay, by), 1),
                         round(max(ax, bx), 1), round(max(ay, by), 1)],
                "type": f["type"], "name": f["name"], "border": False}
        for key in ("value", "group"):
            if key in f:
                item[key] = f[key]
        out.append(item)
    return out


def _radio_groups(circles, words, pt, taken):
    """Round buttons in a row (or a column) become one group of choices."""
    circles = [c for c in circles if not any(overlap(c, k[0]) > 0.3 for k in taken)]
    groups = []
    for c in sorted(circles, key=lambda c: (c[1], c[0])):
        for g in groups:
            last = g[-1]
            same_row = abs(last[1] - c[1]) <= 4 * pt
            same_col = abs(last[0] - c[0]) <= 4 * pt and c[1] - last[3] <= 30 * pt
            if same_row or same_col:
                g.append(c)
                break
        else:
            groups.append([c])
    out = []
    for gi, g in enumerate(groups):
        first = min(g, key=lambda c: (c[1], c[0]))
        row = _row_words(words, first[1] - 2 * pt, first[3] + 4 * pt)
        question = _clean(_join_leftwards(row, first[0], 60 * pt))
        for n, c in enumerate(g, 1):
            value = _label(words, c, "radio", pt) or f"Choice {n}"
            if len(g) == 1:
                out.append({"box": c, "type": "checkbox", "name": value})
            else:
                out.append({"box": c, "type": "radio", "name": question, "value": value,
                            "group": gi})
    return out


def _named(fields, taken):
    """Give every field a name, distinct from the others (radio buttons of
    one group share theirs)."""
    labels = {"text": "Text", "multiline": "Text", "checkbox": "Checkbox",
              "signature": "Signature", "radio": "Group"}
    groups = {}
    for page in sorted(fields):
        for f in fields[page]:
            if f["type"] == "radio":
                key = (page, f.pop("group", None), f["name"])
                if key not in groups:
                    groups[key] = forms._unique(f["name"], taken, labels["radio"])
                f["name"] = groups[key]
            else:
                f["name"] = forms._unique(f["name"], taken, labels[f["type"]])
    return fields


def detect(path, password=None, pages=None, quick=False):
    """Fields a flat form should have: {1-based page: [{"rect": [x0, y0, x1, y1],
    "type": text/multiline/checkbox/signature/radio, "name", "value" (radio
    buttons)}]}. ``pages`` limits it to some pages (1-based numbers);
    ``quick`` leaves out pages that would take long and can't be forms."""
    import pypdfium2 as pdfium

    if not core.open_pdf(path, password).pages:
        return {}
    existing = forms.existing_fields(path, password)
    taken = {f["name"] for items in existing.values() for f in items}
    found = {}
    with core.PDFIUM_LOCK:
        try:
            doc = pdfium.PdfDocument(str(path), password=password)
        except pdfium.PdfiumError as exc:
            raise core.PdfManagerError(f"{path} can't be read ({exc})") from None
        try:
            numbers = [n for n in (pages or range(1, len(doc) + 1)) if 1 <= n <= len(doc)]
            for done, number in enumerate(numbers[:MAX_PAGES]):
                progress.report(done, min(len(numbers), MAX_PAGES), "looking for fields on page")
                page = doc[number - 1]
                textpage = page.get_textpage()
                try:
                    fields = _detect_page(page, textpage, quick)
                finally:
                    textpage.close()
                    page.close()
                old = [f["rect"] for f in existing.get(number, [])]
                fields = [f for f in fields if not any(overlap(f["rect"], r) > 0.3 for r in old)]
                if fields:
                    found[number] = fields
        finally:
            doc.close()
    return _named(found, taken)
