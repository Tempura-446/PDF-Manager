# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""The app's icon: a Z written with a Chinese brush, white on a red page with a
folded corner, like the white characters of a carved seal.

The Z is built the way a brush writes it: each stroke follows a curved spine
and swells or thins with the brush's pressure, starting with a pressed,
angled entry (起笔), pausing in a pressed knot at each turn (顿笔), and leaving
in a sharp tapered exit (出锋). Dry-brush gaps (飞白) run along the fast parts.

One set of shapes on a 100-unit grid feeds both the Pillow drawing (the
Windows .ico and the packaged app's icon) and the SVG in the app's sidebar
and tab (``svg()``), so the two can't drift apart.
"""

import math

from PIL import Image, ImageDraw

ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
TOP_LEFT, BOTTOM_RIGHT = (244, 86, 64), (186, 30, 42)  # the page's red gradient
FOLD_COLOR = (255, 208, 198)

RADIUS = 20  # the page's rounded corners
CUT = 20     # how far the folded corner reaches in from the top right
STREAKS_FROM = 48  # px; in the .ico the dry-brush gaps only show from here up

# Strokes: a cubic spine (start, control 1, control 2, end) and the brush's
# width along it, as (t, width) points, linearly interpolated.
STROKES = [
    # 横: the top stroke, rising slightly, thinning as the brush moves
    (((25, 39), (40, 35.5), (58, 34.5), (73, 32.5)), [(0, 13), (0.2, 11.5), (0.6, 10), (1, 12)]),
    # 撇: the diagonal, pulled down fast from the corner
    (((75, 35), (62, 46), (46, 57), (31, 67)), [(0, 14), (0.4, 12.5), (0.8, 12.5), (1, 14)]),
    # 横 ending in 出锋: the bottom stroke, pressing near the end, then lifting off
    (((26, 71), (45, 72.5), (62, 70.5), (85, 63.5)), [(0, 14), (0.4, 11.5), (0.72, 13.5), (1, 0.4)]),
]
# Pressed knots, as rotated ellipses (centre, radius x, radius y, angle):
KNOTS = [
    ((26, 38.8), 8.6, 5.6, 42),    # 起笔: the brush lands at an angle, its tip to the top left
    ((74.6, 34), 8.4, 5.8, 48),    # 顿笔: the pause at the top right turn, a shoulder
    ((28.3, 70.6), 8.6, 5.9, -32), # 顿笔: the press at the bottom left turn
]
# 飞白, dry-brush gaps: (stroke, t from, t to, offset across the stroke at the
# start and end, from -1 to 1, widest gap). A gap that drifts past ±1 breaks
# through the stroke's edge, splitting off a few bristles.
GAPS = [
    (1, 0.3, 0.85, -0.3, -0.15, 1.0),
    (2, 0.5, 0.97, -0.2, 0.05, 0.9),
    (2, 0.62, 1.0, 0.45, 1.2, 0.9),
]
SAMPLES = 32


def _arc(cx, cy, r, start, end, steps=16):
    return [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
            for a in (start + (end - start) * i / steps for i in range(steps + 1))]


def _page():
    """The page: a rounded square with its top right corner cut off."""
    lo, hi, r = 6, 94, RADIUS
    return ([(hi - CUT, lo), (hi, lo + CUT)] + _arc(hi - r, hi - r, r, 0, 90)
            + _arc(lo + r, hi - r, r, 90, 180) + _arc(lo + r, lo + r, r, 180, 270))


def _fold():
    """The folded-over corner, with a small curve where it bends."""
    x, y = 94 - CUT, 6 + CUT
    return [(x, 6)] + _arc(x + 4, y - 4, 4, 180, 90) + [(94, y)]


def _spine(curve, t):
    """The point on a cubic spine at t, and the unit normal there."""
    (ax, ay), (bx, by), (cx, cy), (dx, dy) = curve
    u = 1 - t
    x = u ** 3 * ax + 3 * u * u * t * bx + 3 * u * t * t * cx + t ** 3 * dx
    y = u ** 3 * ay + 3 * u * u * t * by + 3 * u * t * t * cy + t ** 3 * dy
    tx = 3 * u * u * (bx - ax) + 6 * u * t * (cx - bx) + 3 * t * t * (dx - cx)
    ty = 3 * u * u * (by - ay) + 6 * u * t * (cy - by) + 3 * t * t * (dy - cy)
    n = math.hypot(tx, ty) or 1
    return x, y, -ty / n, tx / n


def _width(profile, t, stroke):
    for (t0, w0), (t1, w1) in zip(profile, profile[1:]):
        if t <= t1:
            w = w0 + (w1 - w0) * (t - t0) / (t1 - t0)
            break
    # a slight unevenness, as a real brush leaves
    return w * (1 + 0.025 * math.sin(t * 23 + stroke * 2.1))


def _stroke(i):
    curve, profile = STROKES[i]
    left, right = [], []
    for k in range(SAMPLES + 1):
        t = k / SAMPLES
        x, y, nx, ny = _spine(curve, t)
        h = _width(profile, t, i) / 2
        left.append((x + nx * h, y + ny * h))
        right.append((x - nx * h, y - ny * h))
    return left + right[::-1]


def _knot(centre, rx, ry, angle, steps=28):
    (cx, cy), a = centre, math.radians(angle)
    return [(cx + rx * math.cos(s) * math.cos(a) - ry * math.sin(s) * math.sin(a),
             cy + rx * math.cos(s) * math.sin(a) + ry * math.sin(s) * math.cos(a))
            for s in (2 * math.pi * k / steps for k in range(steps))]


def _gap(i, t0, t1, o0, o1, widest):
    curve, profile = STROKES[i]
    left, right = [], []
    steps = max(6, round(SAMPLES * (t1 - t0)))
    for k in range(steps + 1):
        f = k / steps
        t = t0 + (t1 - t0) * f
        x, y, nx, ny = _spine(curve, t)
        h = _width(profile, t, i) / 2
        o = (o0 + (o1 - o0) * f) * h
        g = widest * math.sin(math.pi * f) ** 0.8 / 2
        left.append((x + nx * (o + g), y + ny * (o + g)))
        right.append((x + nx * (o - g), y + ny * (o - g)))
    return left + right[::-1]


def _ink():
    return [_stroke(i) for i in range(len(STROKES))] + [_knot(*k) for k in KNOTS]


def _gaps():
    return [_gap(*g) for g in GAPS]


def make_icon(size=1024):
    s = size * 4  # drawn large, then scaled down for smooth edges
    k = s / 100

    def pts(points):
        return [(x * k, y * k) for x, y in points]

    down = Image.linear_gradient("L")
    ramp = Image.blend(down, down.transpose(Image.Transpose.ROTATE_90).transpose(Image.Transpose.FLIP_TOP_BOTTOM), 0.5)
    ramp = ramp.resize((s, s), Image.BILINEAR)  # 0 at the top left, 255 at the bottom right
    tile = Image.composite(Image.new("RGBA", (s, s), BOTTOM_RIGHT + (255,)),
                           Image.new("RGBA", (s, s), TOP_LEFT + (255,)), ramp)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).polygon(pts(_page()), fill=255)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    img.paste(tile, (0, 0), mask)
    ImageDraw.Draw(img).polygon(pts(_fold()), fill=FOLD_COLOR + (255,))
    z = Image.new("L", (s, s), 0)
    zd = ImageDraw.Draw(z)
    for shape in _ink():
        zd.polygon(pts(shape), fill=255)
    if size >= STREAKS_FROM:
        for shape in _gaps():
            zd.polygon(pts(shape), fill=0)
    img.paste((255, 255, 255, 255), (0, 0), z)
    return img.resize((size, size), Image.LANCZOS)


def save_ico(path):
    img = make_icon(256)
    img.save(path, sizes=ICO_SIZES, append_images=[make_icon(w) for w, _ in ICO_SIZES[:-1]])
    return path


def svg(gradient_id="zpdf-g"):
    """The icon as an SVG, for the app's sidebar and its browser tab."""
    lo, hi, r, c = 6, 94, RADIUS, 94 - CUT
    page = (f"M{lo + r} {lo}H{c}L{hi} {lo + CUT}V{hi - r}A{r} {r} 0 0 1 {hi - r} {hi}"
            f"H{lo + r}A{r} {r} 0 0 1 {lo} {hi - r}V{lo + r}A{r} {r} 0 0 1 {lo + r} {lo}Z")
    fold = f"M{c} {lo}V{lo + CUT - 4}A4 4 0 0 0 {c + 4} {lo + CUT}H{hi}Z"

    def paths(shapes, fill):
        # one path per shape: overlapping shapes in a single path could cancel out
        return "".join(f'<path d="M{"L".join(f"{x:.1f} {y:.1f}" for x, y in shape)}Z" fill="{fill}"/>'
                       for shape in shapes)

    hexc = "#{:02x}{:02x}{:02x}".format
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            f'<defs><linearGradient id="{gradient_id}" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{hexc(*TOP_LEFT)}"/><stop offset="1" stop-color="{hexc(*BOTTOM_RIGHT)}"/>'
            f'</linearGradient><mask id="{gradient_id}-z">'
            f'{paths(_ink(), "#fff")}{paths(_gaps(), "#000")}</mask></defs>'
            f'<path d="{page}" fill="url(#{gradient_id})"/>'
            f'<path d="{fold}" fill="{hexc(*FOLD_COLOR)}"/>'
            f'<rect width="100" height="100" fill="#fff" mask="url(#{gradient_id}-z)"/>'
            f'</svg>')
