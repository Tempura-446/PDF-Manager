"""The app's icon: a bold white Z on a red page with a folded corner.

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
CUT = 22     # how far the folded corner reaches in from the top right
# The Z's outline, drawn with a round-joined stroke of width 2 * Z_ROUND so its
# corners come out softly rounded: 11-unit bars, a matching diagonal.
Z = [(28, 34), (68, 34), (68, 41), (39, 63), (68, 63), (68, 70), (28, 70), (28, 63), (57, 41), (28, 41)]
Z_ROUND = 2


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
    white, w = (255, 255, 255, 255), Z_ROUND * k
    z = pts(Z)
    d.polygon(z, fill=white)
    d.line(z + z[:1], fill=white, width=round(2 * w), joint="curve")
    for x, y in z:  # round the corners the line joint leaves square
        d.ellipse([x - w, y - w, x + w, y + w], fill=white)
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
    z = " ".join(f"{x},{y}" for x, y in Z)
    hexc = "#{:02x}{:02x}{:02x}".format
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            f'<defs><linearGradient id="{gradient_id}" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{hexc(*TOP_LEFT)}"/><stop offset="1" stop-color="{hexc(*BOTTOM_RIGHT)}"/>'
            f'</linearGradient></defs>'
            f'<path d="{page}" fill="url(#{gradient_id})"/>'
            f'<path d="{fold}" fill="{hexc(*FOLD_COLOR)}"/>'
            f'<polygon points="{z}" fill="#fff" stroke="#fff" stroke-width="{2 * Z_ROUND}" stroke-linejoin="round"/>'
            f'</svg>')
