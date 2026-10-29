"""The app's icon, drawn with Pillow: a brush-stroke Z with a spark (the same
mark as in the app's sidebar, whose SVG is ``Z_PATH`` and ``SPARK``)."""

from PIL import Image, ImageDraw

ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
TOP_LEFT, BOTTOM_RIGHT = (226, 94, 66), (158, 42, 28)  # the tile's gradient: the app's red, as before ZPDF
SPARK_COLOR = (255, 214, 138)

# On a 100-unit grid: hairline top stroke, heavy diagonal, a bottom stroke
# that ends in an upturned swash. ("M"/"L", point) or ("C", c1, c2, point).
Z = [
    ("M", (22, 32)),
    ("C", (34, 26), (52, 29), (66, 25)),
    ("L", (80, 24)),
    ("L", (43, 70)),
    ("C", (56, 71), (68, 70), (76, 64)),
    ("C", (79, 62), (83, 62), (82, 66)),
    ("C", (76, 76), (58, 78), (40, 77)),
    ("L", (20, 77)),
    ("L", (57, 31)),
    ("C", (45, 31), (33, 33), (25, 36)),
    ("C", (21, 37.5), (19, 34), (22, 32)),
]
SPARK = [(77, 9), (78.8, 14.2), (84, 16), (78.8, 17.8), (77, 23), (75.2, 17.8), (70, 16), (75.2, 14.2)]
Z_PATH = "".join(op + " ".join(f"{x:g} {y:g}" for x, y in pts) for op, *pts in Z) + "Z"


def _outline(scale, steps=24):
    points, here = [], None
    for op, *pts in Z:
        if op != "C":
            here = pts[0]
            points.append(here)
            continue
        c1, c2, end = pts
        for i in range(1, steps + 1):
            t = i / steps
            u = 1 - t
            points.append(tuple(u ** 3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t ** 3 * d
                                for a, b, c, d in zip(here, c1, c2, end)))
        here = end
    return [(x * scale, y * scale) for x, y in points]


def make_icon(size=1024):
    s = size * 4  # drawn large, then scaled down for smooth edges
    down = Image.linear_gradient("L")
    ramp = Image.blend(down, down.transpose(Image.Transpose.ROTATE_90).transpose(Image.Transpose.FLIP_TOP_BOTTOM), 0.5)
    ramp = ramp.resize((s, s), Image.BILINEAR)  # 0 at the top left, 255 at the bottom right
    tile = Image.composite(Image.new("RGBA", (s, s), BOTTOM_RIGHT + (255,)),
                           Image.new("RGBA", (s, s), TOP_LEFT + (255,)), ramp)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([s * 0.06, s * 0.06, s * 0.94, s * 0.94], radius=s * 0.22, fill=255)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    img.paste(tile, (0, 0), mask)
    d = ImageDraw.Draw(img)
    d.polygon(_outline(s / 100), fill=(255, 255, 255, 255))
    if size >= 24:  # too small to read below that
        d.polygon([(x * s / 100, y * s / 100) for x, y in SPARK], fill=SPARK_COLOR + (255,))
    return img.resize((size, size), Image.LANCZOS)


def save_ico(path):
    img = make_icon(256)
    img.save(path, sizes=ICO_SIZES, append_images=[make_icon(w) for w, _ in ICO_SIZES[:-1]])
    return path
