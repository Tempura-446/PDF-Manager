"""The app's icon, drawn with Pillow (the same mark as in the app's sidebar)."""

from PIL import Image, ImageDraw

ACCENT = (194, 65, 43)
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def make_icon(size=1024):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([size * 0.06, size * 0.06, size * 0.94, size * 0.94],
                        radius=size * 0.22, fill=ACCENT)
    white = (255, 255, 255)
    # The page glyph spans x 5..19, y 3..21 on its 24-unit grid; centre it
    # and make it 60% of the tile's height.
    u = size * 0.60 / 18
    w = round(size * 0.055)

    def p(x, y):
        return (size / 2 + (x - 12) * u, size / 2 + (y - 12) * u)

    def stroke(*points):
        pts = [p(*pt) for pt in points]
        d.line(pts, fill=white, width=w, joint="curve")
        for x, y in (pts[0], pts[-1]):  # round caps
            d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=white)

    stroke((7, 3), (14, 3), (19, 8), (19, 19), (17, 21), (7, 21), (5, 19), (5, 5), (7, 3))
    stroke((14, 3), (14, 8), (19, 8))
    stroke((9, 13), (15, 13))
    stroke((9, 17), (13, 17))
    return img


def save_ico(path):
    make_icon(512).save(path, sizes=ICO_SIZES)
    return path
