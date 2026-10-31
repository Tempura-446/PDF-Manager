"""The app's icon: a bold brush-stroke Z on a red page with a folded corner.

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
# The Z as one brush-painted outline: a rounded press where the brush lands,
# a pointed tip where it turns, a heavy diagonal, and a bottom stroke that
# sweeps out into a tail. ("M"/"L", point) or ("C", control1, control2, point).
Z = [
    ("M", (25, 34)),
    ("C", (39, 31.5), (57, 30.5), (72, 29.6)),
    ("C", (75, 29.4), (77.5, 29.2), (79.5, 29)),
    ("C", (77.5, 31.5), (76, 33.5), (74.5, 35.5)),
    ("C", (65, 45), (55, 54), (46.5, 62)),
    ("C", (58, 61), (70, 60), (84, 58)),
    ("C", (79, 67.5), (68, 75), (52, 76.4)),
    ("C", (40, 77.3), (27, 77.6), (15, 78)),
    ("C", (16.5, 75), (18, 71), (20, 68.5)),
    ("C", (28, 60.5), (36, 53), (44, 45.8)),
    ("C", (38, 46), (32.5, 46.6), (27.5, 47.5)),
    ("C", (22, 48.5), (19.5, 44.5), (20, 41)),
    ("C", (20.5, 37), (22.5, 34.5), (25, 34)),
]
# Dry-brush streaks: hairline gaps running out of the tail.
STREAKS = [
    [("M", (58, 66.2)), ("C", (67, 65.4), (75, 63.6), (84, 59.5)), ("C", (75, 64.8), (67, 66.8), (58, 66.2))],
    [("M", (62, 70.4)), ("C", (68, 69.6), (73, 68), (79, 65)), ("C", (73, 68.9), (68, 70.8), (62, 70.4))],
]
STREAKS_FROM = 48  # px; below this the streaks are too fine to see


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


def _flatten(path, steps=24):
    points, here = [], None
    for op, *p in path:
        if op != "C":
            here = p[0]
            points.append(here)
            continue
        c1, c2, end = p
        for i in range(1, steps + 1):
            t = i / steps
            u = 1 - t
            points.append(tuple(u ** 3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t ** 3 * e
                                for a, b, c, e in zip(here, c1, c2, end)))
        here = end
    return points


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
    d = ImageDraw.Draw(img)
    d.polygon(pts(_fold()), fill=FOLD_COLOR + (255,))
    z = Image.new("L", (s, s), 0)
    zd = ImageDraw.Draw(z)
    zd.polygon(pts(_flatten(Z)), fill=255)
    if size >= STREAKS_FROM:
        for streak in STREAKS:
            zd.polygon(pts(_flatten(streak)), fill=0)
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

    def d(path):
        return "".join(op + " ".join(f"{x:g} {y:g}" for x, y in p) for op, *p in path) + "Z"

    streaks = "".join(f'<path d="{d(st)}" fill="#000"/>' for st in STREAKS)
    hexc = "#{:02x}{:02x}{:02x}".format
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            f'<defs><linearGradient id="{gradient_id}" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{hexc(*TOP_LEFT)}"/><stop offset="1" stop-color="{hexc(*BOTTOM_RIGHT)}"/>'
            f'</linearGradient><mask id="{gradient_id}-z"><path d="{d(Z)}" fill="#fff"/>{streaks}</mask></defs>'
            f'<path d="{page}" fill="url(#{gradient_id})"/>'
            f'<path d="{fold}" fill="{hexc(*FOLD_COLOR)}"/>'
            f'<rect width="100" height="100" fill="#fff" mask="url(#{gradient_id}-z)"/>'
            f'</svg>')
