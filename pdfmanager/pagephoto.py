# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Photos of paper pages, made into clean pages: find the page's four edges
in a phone photo, flatten it (undo the perspective), crop it, straighten it,
and give it a "document" look (even lighting, white paper, dark ink), with
or without its colours. NumPy and Pillow only.

    flat, found = pagephoto.flatten_photo(photo)   # geometry: edges, flatten, straighten
    page = pagephoto.document_look(flat, keep_colours=False)

Finding the page: on a small copy of the photo, the paper is what is light
and colourless (light minus saturation), split from the rest at a threshold
(Otsu's, and two either side of it for uneven light); the part under the
middle of the photo, with its holes (the print) filled, is the page. Its
outline is cut down to the four corners that keep most of its area, each
edge is fitted to the outline as a straight line, and then moved onto the
strongest light-to-dark step along it in the full-size photo. A page whose
outline isn't a clear four-sided shape (no page, a page cut off by the
edge of the photo, a cluttered desk) is not trusted: the photo is then
kept as it is, only straightened.
"""

import math

import numpy as np
from PIL import Image, ImageFilter, ImageOps

DETECT_SIDE = 640        # pixels: the long side of the copy the page is looked for in
MIN_AREA = 0.10          # of the photo: a smaller light shape is not the page
MAX_AREA = 0.985         # of the photo: a larger one is the whole photo, no edges in it
MIN_FILL = 0.94          # of its four-sided outline the light shape must fill
MIN_ANGLE, MAX_ANGLE = 50.0, 130.0   # degrees: a corner outside these isn't a photographed page corner
MIN_CONTRAST = 18.0      # grey levels between the page and what's around it
REFINE_REACH = 0.012     # of the photo's long side: how far an edge may move when refined
MIN_SKEW = 0.3           # degrees: smaller tilts are left alone
MAX_SKEW = 12.0          # degrees: the largest tilt straightening looks for

# Paper shapes a flattened page snaps to when it is this close (height / width).
PAPERS = {"a4": 297 / 210, "letter": 11 / 8.5}
SNAP = 0.045


class Found:
    """Where the page is in a photo: ``corners`` (top-left, top-right,
    bottom-right, bottom-left; full-size pixels), how sure (0-1), and
    ``found`` when it is trusted."""

    def __init__(self, corners=None, confidence=0.0, found=False, why=""):
        self.corners = corners
        self.confidence = confidence
        self.found = found
        self.why = why

    def __repr__(self):
        return f"Found(found={self.found}, confidence={self.confidence:.2f}, corners={self.corners}, why={self.why!r})"


# -- small image operations on boolean arrays ---------------------------------------

def _shift_or(mask, steps=1):
    """3x3 dilation, ``steps`` times."""
    out = mask.copy()
    for _ in range(steps):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def _erode(mask, steps=1):
    return ~_shift_or(~mask, steps)


def _spread_rows(mask, reached):
    """Every run of True along each row of ``mask`` that holds a reached
    pixel, reached."""
    h, w = mask.shape
    padded = np.zeros((h, w + 1), bool)
    padded[:, :w] = mask
    flat = padded.ravel()
    starts = flat & ~np.concatenate(([False], flat[:-1]))
    run = np.cumsum(starts) * flat  # 0 outside runs
    hit = np.zeros(int(run.max()) + 1, bool)
    r = np.zeros((h, w + 1), bool)
    r[:, :w] = reached
    hit[run[r.ravel() & flat]] = True
    hit[0] = False
    return hit[run].reshape(h, w + 1)[:, :w]


def _reach(mask, seeds):
    """The parts of ``mask`` connected (4-way) to ``seeds``."""
    reached = mask & seeds
    if not reached.any():
        return reached
    for _ in range(200):
        grown = _spread_rows(mask, reached)
        grown = _spread_rows(mask.T, grown.T).T
        if (grown == reached).all():
            break
        reached = grown
    return reached


def _otsu(values):
    """Otsu's threshold of uint8 values."""
    hist = np.bincount(values.ravel(), minlength=256).astype(float)
    total = hist.sum()
    if not total:
        return 128
    levels = np.arange(256)
    weight = np.cumsum(hist)
    mean = np.cumsum(hist * levels)
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mean[-1] * weight / total - mean) ** 2 / (weight * (total - weight))
    between = np.nan_to_num(between)
    return int(np.argmax(between))


# -- geometry ---------------------------------------------------------------------

def _hull(points):
    """Convex hull (monotone chain) of (x, y) points, anticlockwise in maths
    terms, as a list of tuples."""
    pts = sorted(set(map(tuple, points)))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _area(poly):
    x = np.array([p[0] for p in poly], float)
    y = np.array([p[1] for p in poly], float)
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _four_corners(hull):
    """The hull cut down to four corners: the corner whose loss costs the
    least area goes, again and again."""
    poly = list(hull)
    while len(poly) > 4:
        n = len(poly)
        costs = []
        for i in range(n):
            a, b, c = poly[i - 1], poly[i], poly[(i + 1) % n]
            costs.append(abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])))
        poly.pop(int(np.argmin(costs)))
    return poly


def _fit_line(points):
    """Total least squares line through ``points``: (point on it, unit direction)."""
    pts = np.asarray(points, float)
    centre = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - centre, full_matrices=False)
    return centre, vt[0]


def _meet(line_a, line_b):
    (p, d), (q, e) = line_a, line_b
    m = np.array([[d[0], -e[0]], [d[1], -e[1]]])
    if abs(np.linalg.det(m)) < 1e-9:
        return None
    t = np.linalg.solve(m, q - p)
    return p + t[0] * d


def _order(corners):
    """Top-left, top-right, bottom-right, bottom-left (image y points down):
    clockwise round the middle, starting from the one up and to the left."""
    pts = [np.asarray(c, float) for c in corners]
    cx, cy = np.mean(pts, axis=0)
    pts.sort(key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    start = min(range(4), key=lambda i: abs(math.remainder(math.atan2(pts[i][1] - cy, pts[i][0] - cx) + 3 * math.pi / 4, 2 * math.pi)))
    return pts[start:] + pts[:start]


def _angles(quad):
    out = []
    for i in range(4):
        a, b, c = quad[i - 1], quad[i], quad[(i + 1) % 4]
        u, v = a - b, c - b
        cos = np.dot(u, v) / (np.linalg.norm(u) * np.linalg.norm(v) + 1e-9)
        out.append(math.degrees(math.acos(max(-1.0, min(1.0, cos)))))
    return out


# -- finding the page ---------------------------------------------------------------

def _paper_score(small):
    """How much each pixel looks like paper: light and colourless (uint8)."""
    rgb = np.asarray(small, np.float32)
    light = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    score = np.clip(light - 1.2 * chroma, 0, 255).astype(np.uint8)
    # A little blur: the grain of a desk or a carpet is not an edge.
    return np.asarray(Image.fromarray(score).filter(ImageFilter.GaussianBlur(1.2)))


def _candidate(score, threshold):
    """The page's shape at one threshold, and its four corners (small-copy
    pixels), or (None, why)."""
    h, w = score.shape
    mask = score > threshold
    mask = _shift_or(_erode(mask, 2), 2)  # cut thin bridges to light things beside the page
    seeds = np.zeros_like(mask)
    seeds[int(h * 0.35):int(h * 0.65), int(w * 0.35):int(w * 0.65)] = True
    page = _reach(mask, seeds)
    if not page.any():
        return None, "nothing light in the middle of the photo"
    border = np.zeros_like(mask)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    outside = _reach(~page, border)
    page = ~outside  # the print inside the page filled in
    area = page.mean()
    if not MIN_AREA <= area <= MAX_AREA:
        return None, "the light part is too small or fills the photo"
    edge = page & ~_erode(page, 1)
    ys, xs = np.nonzero(edge)
    hull = _hull(np.column_stack([xs, ys]))
    if len(hull) < 4:
        return None, "no outline"
    quad = _order([np.array(p, float) for p in _four_corners(hull)])
    # Each edge as a straight line through the outline's points near it.
    pts = np.column_stack([xs, ys]).astype(float)
    lines = []
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        d = b - a
        length = np.linalg.norm(d)
        if length < 0.15 * min(w, h):
            return None, "a side is too short"
        d /= length
        normal = np.array([-d[1], d[0]])
        rel = pts - a
        along = rel @ d
        off = np.abs(rel @ normal)
        near = (off < 2.5) & (along > 0.08 * length) & (along < 0.92 * length)
        if near.sum() < 10:
            return None, "a side isn't straight"
        lines.append(_fit_line(pts[near]))
    corners = []
    for i in range(4):
        p = _meet(lines[i - 1], lines[i])
        if p is None:
            return None, "two sides are parallel"
        corners.append(p)
    corners = _order(corners)
    return (page, corners), ""


def _judge(score, page, corners):
    """How sure (0-1) that ``corners`` are a photographed page's, and why not."""
    h, w = score.shape
    poly = [tuple(c) for c in corners]
    quad_area = _area(poly)
    fill = page.sum() / max(quad_area, 1.0)
    angles = _angles(corners)
    on_border = sum(1 for x, y in corners if x < 2 or y < 2 or x > w - 3 or y > h - 3)
    outside = sum(1 for x, y in corners if x < -0.02 * w or y < -0.02 * h or x > 1.02 * w or y > 1.02 * h)
    inner = _erode(page, 3)
    ring = _shift_or(page, 6) & ~_shift_or(page, 2)
    contrast = float(np.median(score[inner]) - np.median(score[ring])) if inner.any() and ring.any() else 0.0
    if outside or on_border >= 2:
        return 0.0, "the page runs off the edge of the photo"
    if not MIN_FILL <= fill <= 1.04:
        return 0.0, "the light shape isn't four-sided"
    if min(angles) < MIN_ANGLE or max(angles) > MAX_ANGLE:
        return 0.0, "the corners aren't a page's"
    if contrast < MIN_CONTRAST:
        return 0.0, "the page doesn't stand out from what's around it"
    sure = min(1.0, (fill - MIN_FILL) / (1 - MIN_FILL) * 0.5 + 0.5) * min(1.0, contrast / 60)
    sure *= 1.0 - max(0.0, max(abs(a - 90) for a in angles) - 25) / 40
    return max(0.0, min(1.0, sure)), ""


def _refine(grey, corners, reach):
    """Each edge moved onto the strongest step from light (the page) to dark
    along it in the full-size grey photo, then the corners where the edges
    meet. The edges stay where they were when the steps don't agree."""
    h, w = grey.shape
    centre = np.mean(corners, axis=0)
    lines = []
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        d = b - a
        length = np.linalg.norm(d)
        d = d / length
        normal = np.array([-d[1], d[0]])
        if np.dot((a + b) / 2 - centre, normal) < 0:
            normal = -normal  # pointing out of the page
        ts = np.linspace(0.1, 0.9, 48)
        offsets = np.arange(-reach, reach + 1, dtype=float)
        base = a[None, :] + ts[:, None] * (b - a)[None, :]
        samples = base[:, None, :] + offsets[None, :, None] * normal[None, None, :]
        xs = np.clip(samples[..., 0], 0, w - 1.001)
        ys = np.clip(samples[..., 1], 0, h - 1.001)
        x0, y0 = np.floor(xs).astype(int), np.floor(ys).astype(int)
        fx, fy = xs - x0, ys - y0
        v = (grey[y0, x0] * (1 - fx) * (1 - fy) + grey[y0, x0 + 1] * fx * (1 - fy)
             + grey[y0 + 1, x0] * (1 - fx) * fy + grey[y0 + 1, x0 + 1] * fx * fy)
        step = v[:, :-2] - v[:, 2:]  # light inside, dark outside: positive
        best = np.argmax(step, axis=1)
        strength = step[np.arange(len(ts)), best]
        # Where between the samples the step is, to a fraction of a pixel.
        sub = np.zeros(len(ts))
        inside = (best > 0) & (best < step.shape[1] - 1)
        k = np.nonzero(inside)[0]
        if len(k):
            l, c, r = step[k, best[k] - 1], step[k, best[k]], step[k, best[k] + 1]
            denom = l - 2 * c + r
            sub[k] = np.where(np.abs(denom) > 1e-6, 0.5 * (l - r) / np.where(np.abs(denom) > 1e-6, denom, 1), 0)
        moved = offsets[best + 1] + np.clip(sub, -0.5, 0.5)
        good = strength > max(8.0, 0.4 * np.median(strength))
        if good.sum() < 12:
            lines.append((a, d))
            continue
        pts = base[good] + moved[good, None] * normal[None, :]
        # Two rounds: fit, drop the points far from the line, fit again.
        line = _fit_line(pts)
        resid = np.abs((pts - line[0]) @ np.array([-line[1][1], line[1][0]]))
        keep = resid <= max(1.0, 2.5 * np.median(resid))
        if keep.sum() >= 10:
            line = _fit_line(pts[keep])
        # A line that moved further than the search reach is a wrong step: keep the first.
        shift = abs(np.dot(line[0] - a, normal))
        lines.append(line if shift <= reach else (a, d))
    out = []
    for i in range(4):
        p = _meet(lines[i - 1], lines[i])
        out.append(p if p is not None else corners[i])
    return out


def find_page(photo):
    """Where the page is in ``photo`` (a PIL image): a Found."""
    rgb = photo.convert("RGB")
    scale = DETECT_SIDE / max(rgb.size)
    small = rgb.resize((max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))), Image.BILINEAR) \
        if scale < 1 else rgb
    scale = small.width / rgb.width
    score = _paper_score(small)
    otsu = _otsu(score)
    low, high = float(np.percentile(score, 5)), float(np.percentile(score, 95))
    best = Found(why="no page edges found")
    for threshold in (otsu, (otsu + low) / 2, (otsu + high) / 2):
        made, why = _candidate(score, threshold)
        if made is None:
            if not best.found and best.confidence == 0:
                best.why = why
            continue
        page, corners = made
        sure, why = _judge(score, page, corners)
        if sure > best.confidence:
            best = Found([tuple(map(float, c / scale)) for c in corners], sure, True)
        elif not best.found:
            best.why = why
    if not best.found:
        return best
    grey = np.asarray(rgb.convert("L").filter(ImageFilter.GaussianBlur(1.0)), np.float32)
    reach = max(3, round(REFINE_REACH * max(rgb.size)))
    corners = _refine(grey, [np.array(c) for c in best.corners], reach)
    best.corners = [tuple(map(float, c)) for c in _order(corners)]
    return best


# -- flattening -----------------------------------------------------------------------

def _homography(src, dst):
    """The 3x3 matrix taking each point of ``src`` to ``dst`` (four each)."""
    rows, rhs = [], []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs += [u, v]
    h = np.linalg.solve(np.array(rows, float), np.array(rhs, float))
    return np.append(h, 1.0).reshape(3, 3)


def page_shape(corners, size):
    """Width / height of the page whose photo has these corners (TL, TR,
    BR, BL), allowing for the perspective: the camera's focal length is
    worked out from the corners (Zhang and He, "Whiteboard scanning", 2007);
    when the shape says nothing about it (the photo was taken straight on),
    from the lengths of the sides."""
    tl, tr, br, bl = (np.asarray(c, float) for c in corners)
    widths = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    heights = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    plain = widths / heights
    u0, v0 = size[0] / 2, size[1] / 2
    m1, m2, m3, m4 = (np.array([p[0] - u0, p[1] - v0, 1.0]) for p in (tl, tr, bl, br))
    try:
        k2 = np.dot(np.cross(m1, m4), m3) / np.dot(np.cross(m2, m4), m3)
        k3 = np.dot(np.cross(m1, m4), m2) / np.dot(np.cross(m3, m4), m2)
    except FloatingPointError:
        return plain
    n2, n3 = k2 * m2 - m1, k3 * m3 - m1
    if abs(n2[2]) < 1e-6 or abs(n3[2]) < 1e-6:
        return plain
    f2 = -(n2[0] * n3[0] + n2[1] * n3[1]) / (n2[2] * n3[2])
    longest = max(size)
    if not (0.25 * longest) ** 2 <= f2 <= (8 * longest) ** 2:
        return plain  # nearly straight on: the focal length can't be told
    ratio = math.sqrt((n2[0] ** 2 / f2 + n2[1] ** 2 / f2 + n2[2] ** 2)
                      / (n3[0] ** 2 / f2 + n3[1] ** 2 / f2 + n3[2] ** 2))
    if not 0.5 * plain <= ratio <= 2 * plain:
        return plain
    return ratio


def snap_shape(ratio):
    """``ratio`` (width / height), or that of A4 or Letter when it's close."""
    tall = 1 / ratio if ratio < 1 else ratio
    for paper in PAPERS.values():
        if abs(tall - paper) / paper <= SNAP:
            return 1 / paper if ratio < 1 else paper
    return ratio


def flatten(photo, corners, inset=0.006):
    """The page at ``corners`` (TL, TR, BR, BL) cut out of ``photo`` as a
    flat rectangle, its shape from page_shape (snapped to A4 or Letter when
    close), a sliver trimmed off each side."""
    tl, tr, br, bl = (np.asarray(c, float) for c in corners)
    ratio = snap_shape(page_shape(corners, photo.size))
    height = max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))
    width = height * ratio
    # No bigger than the photo's own pixels allow.
    longest = max(width, height)
    if longest > max(photo.size):
        width, height = width * max(photo.size) / longest, height * max(photo.size) / longest
    w, h = max(1, round(width)), max(1, round(height))
    dx, dy = inset * w, inset * h
    target = [(-dx, -dy), (w + dx, -dy), (w + dx, h + dy), (-dx, h + dy)]
    to_photo = _homography(target, [tl, tr, br, bl])
    coeffs = (to_photo / to_photo[2, 2]).ravel()[:8]
    mode = photo.mode if photo.mode in ("RGB", "L") else "RGB"
    return photo.convert(mode).transform((w, h), Image.PERSPECTIVE, tuple(coeffs), Image.BICUBIC)


# -- straightening ---------------------------------------------------------------------

def skew_angle(image):
    """How far to turn ``image`` anticlockwise (degrees, as PIL's rotate
    takes them) for its lines of text to run level; 0.0 when they already
    do, or there is too little text to tell. From how sharply the dark
    pixels bunch into rows at each angle (a projection profile)."""
    grey = image.convert("L")
    grey.thumbnail((1200, 1200))
    arr = np.asarray(grey)
    h, w = arr.shape
    arr = arr[int(h * 0.04):int(h * 0.96), int(w * 0.04):int(w * 0.96)]
    threshold = min(_otsu(arr), int(np.median(arr)) - 40)
    ys, xs = np.nonzero(arr < threshold)
    if len(xs) < 200 or len(xs) > 0.3 * arr.size:
        return 0.0
    if len(xs) > 60000:
        pick = np.random.default_rng(0).choice(len(xs), 60000, replace=False)
        xs, ys = xs[pick], ys[pick]
    xs = xs.astype(float) - xs.mean()
    ys = ys.astype(float)

    def sharpness(degrees):
        t = math.radians(degrees)
        rows = np.round(ys * math.cos(t) + xs * math.sin(t)).astype(int)
        counts = np.bincount(rows - rows.min())
        return float(np.dot(counts, counts))

    coarse = np.arange(-MAX_SKEW, MAX_SKEW + 0.01, 0.5)
    scores = [sharpness(a) for a in coarse]
    best = float(coarse[int(np.argmax(scores))])
    fine = np.arange(best - 0.5, best + 0.51, 0.05)
    best = float(fine[int(np.argmax([sharpness(a) for a in fine]))])
    if max(scores) < 1.03 * sharpness(0.0) or abs(best) < MIN_SKEW:
        return 0.0
    return round(-best, 2)


def straighten(image, angle=None):
    """``image`` turned level (see skew_angle); the corners it uncovers are white."""
    angle = skew_angle(image) if angle is None else angle
    if not angle:
        return image
    mode = image.mode
    work = image.convert("L") if mode == "1" else image
    fill = 255 if work.mode == "L" else (255,) * len(work.getbands())
    turned = work.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=fill)
    return turned.convert("1") if mode == "1" else turned


def flatten_photo(photo, straighten_page=True):
    """A phone photo of a page made flat: (the page, the Found). When no
    page edges are found confidently, the photo as it is, only straightened."""
    photo = ImageOps.exif_transpose(photo) or photo
    if photo.mode not in ("RGB", "L"):
        photo = photo.convert("RGB")
    found = find_page(photo)
    page = flatten(photo, found.corners) if found.found else photo
    if straighten_page:
        page = straighten(page)
    return page, found


# -- the document look -----------------------------------------------------------------

def _background(grey):
    """The paper's brightness everywhere (the light falling on it), with the
    print taken out: a small copy, spread to its lightest nearby value,
    smoothed, and brought back to full size. A coarser copy keeps big dark
    things (a logo, a photo, a heading block) from passing for shadow."""
    w, h = grey.size

    def spread(side, size, smooth):
        factor = max(1, round(max(w, h) / side))
        small = grey.reduce(factor) if factor > 1 else grey
        small = small.filter(ImageFilter.MaxFilter(size)).filter(ImageFilter.MedianFilter(size))
        return np.asarray(small.filter(ImageFilter.GaussianBlur(smooth)).resize((w, h), Image.BILINEAR), np.float32)

    return np.maximum(spread(400, 7, 4), 0.82 * spread(60, 5, 1.5))


def document_look(page, keep_colours=False, white=0.86, black=0.30, gamma=1.25):
    """``page`` as a scanner would give it: the light evened out (shadows
    and a darker side gone), the paper white and the ink dark. Grey unless
    ``keep_colours`` (then coloured ink, stamps and highlighter keep theirs)."""
    rgb = page.convert("RGB")
    grey = rgb.convert("L")
    light = np.maximum(_background(grey), 24.0)

    def levels(values, light, black, gamma):
        norm = values / light
        out = np.clip((norm - black) / (white - black), 0, 1) ** gamma
        return (out * 255 + 0.5).astype(np.uint8)

    if not keep_colours:
        return Image.fromarray(levels(np.asarray(grey, np.float32), light, black, gamma), "L")
    # Colours: a gentler curve, so a red stamp stays red rather than turning pure.
    return Image.fromarray(levels(np.asarray(rgb, np.float32), light[..., None], black * 0.4, 1.1), "RGB")
