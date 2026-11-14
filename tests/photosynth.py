# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Made-up phone photos of a page, for the tests of pagephoto.py and Scan:
a printed page (a heading bar, a black square at its top left, lines of
"words"), turned, seen at an angle, on a grainy wooden desk, lit unevenly
with a soft shadow across it. The page's true corners come with it."""
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def camera_corners(size=(1600, 1200), turn=8.0, tilt=28.0, sideways=6.0, distance=0.52, focal=1.15,
                   shift=(0.01, 0.0), paper=(0.210, 0.297)):
    """Where an A4 page's corners (TL, TR, BR, BL) land in a photo of
    ``size`` taken by a pinhole camera ``distance`` metres away, its focal
    length ``focal`` times the photo's width: the page turned ``turn``
    degrees clockwise, tipped back ``tilt`` degrees and ``sideways``."""
    w, h = size
    f = focal * w
    pw, ph = paper
    pts = np.array([[-pw / 2, -ph / 2, 0], [pw / 2, -ph / 2, 0], [pw / 2, ph / 2, 0], [-pw / 2, ph / 2, 0]])
    a, b, c = (math.radians(v) for v in (turn, tilt, sideways))
    rz = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, math.cos(b), -math.sin(b)], [0, math.sin(b), math.cos(b)]])
    ry = np.array([[math.cos(c), 0, math.sin(c)], [0, 1, 0], [-math.sin(c), 0, math.cos(c)]])
    seen = (ry @ rx @ rz @ pts.T).T + np.array([shift[0], shift[1], distance])
    return [(float(f * x / z + w / 2), float(f * y / z + h / 2)) for x, y, z in seen]


# Where the page's corners land in the default 1600 x 1200 photo (TL, TR, BR, BL).
CORNERS = camera_corners()


def page_image(width=1000, height=1414, seed=1, colour=False):
    """The printed page itself."""
    rng = np.random.default_rng(seed)
    page = Image.new("RGB", (width, height), (246, 245, 240))
    draw = ImageDraw.Draw(page)
    m = width // 12
    draw.rectangle([m, m, m + width // 9, m + width // 9], fill=(20, 20, 20))           # the square, top left
    draw.rectangle([m + width // 6, m + 10, width - m, m + width // 18], fill=(30, 30, 30))  # the heading
    y = m + width // 6
    while y < height - m:
        x = m
        right = width - m - (rng.integers(0, width // 3) if rng.random() < 0.2 else 0)
        while x < right:
            w = int(rng.integers(width // 40, width // 9))
            draw.rectangle([x, y, min(x + w, right), y + height // 110], fill=(35, 35, 40))
            x += w + width // 70
        y += height // 38
    if colour:
        draw.rectangle([width - m - width // 5, height - m - width // 8, width - m, height - m], fill=(200, 30, 40))
    return page


def desk(size, seed=2):
    """A grainy, warm wooden desk."""
    w, h = size
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    grain = np.sin(x / 9.0 + 3 * np.sin(y / 70.0)) * 0.5 + 0.5
    noise = rng.normal(0, 1, (h // 8 + 1, w // 8 + 1)).astype(np.float32)
    noise = np.asarray(Image.fromarray(((noise - noise.min()) / np.ptp(noise) * 255).astype(np.uint8))
                       .resize((w, h), Image.BICUBIC), np.float32) / 255
    base = np.array([126, 86, 52], np.float32)
    shade = 0.75 + 0.18 * grain + 0.17 * noise
    arr = base[None, None, :] * shade[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def _coeffs(src, dst):
    """PIL's perspective coefficients that take points of ``dst`` (the
    output) back to ``src``."""
    rows, rhs = [], []
    for (x, y), (u, v) in zip(dst, src):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs += [u, v]
    return tuple(np.linalg.solve(np.array(rows, float), np.array(rhs, float)))


def photo(corners=CORNERS, size=(1600, 1200), seed=1, colour=False, light=True, background=None):
    """(the photo, the page's corners in it)."""
    page = page_image(seed=seed, colour=colour)
    pw, ph = page.size
    coeffs = _coeffs([(0, 0), (pw, 0), (pw, ph), (0, ph)], corners)
    on_desk = page.transform(size, Image.PERSPECTIVE, coeffs, Image.BICUBIC)
    mask = Image.new("L", page.size, 255).transform(size, Image.PERSPECTIVE, coeffs, Image.BILINEAR)
    shot = background if background is not None else desk(size, seed + 1)
    shot = Image.composite(on_desk, shot, mask)
    arr = np.asarray(shot, np.float32)
    if light:
        h, w = arr.shape[:2]
        y, x = np.mgrid[0:h, 0:w].astype(np.float32)
        lit = 0.62 + 0.38 * (x / w * 0.7 + (1 - y / h) * 0.3)         # darker to the left and bottom
        shadow = 1 - 0.28 * np.exp(-(((x - w * 0.62) / (w * 0.16)) ** 2 + ((y - h * 0.7) / (h * 0.3)) ** 2))
        arr = arr * (lit * shadow)[..., None]
    arr += np.random.default_rng(seed + 7).normal(0, 2.5, arr.shape)
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").filter(ImageFilter.GaussianBlur(0.6))
    return out, [tuple(c) for c in corners]
