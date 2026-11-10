"""Scan compression engine: a scanned page as three small pictures.

A scan saved as one JPEG can only shrink by losing resolution, which
blurs its text. Split instead into

- a text shape: 1 bit a pixel at the scan's own resolution, marking the ink;
- a background: the paper with the ink taken out, at low resolution;
- an ink colour picture, at very low resolution: what colour the ink is
  (so a red stamp stays red),

the page is as small and its text stays sharp. This module does the
splitting and knows nothing of PDF; imaging.py puts the layers in the file.
"""
import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter

# Ink is darker than the paper around it by the larger of these.
INK_MARGIN = 30      # grey levels
INK_SHARE = 0.22     # of the paper's brightness
# An ink pixel needs this many ink pixels among the 8 around it: scanner
# speckle in the text shape costs more than all the text.
NEIGHBOURS = 4
# Photographs: looked for in cells this size. A cell is picture when more
# of it is "ink" than text ever covers, or its paper is dark.
CELL_INCH = 1 / 6
PICTURE_INK = 0.45
PICTURE_DARK = 110
PICTURE_PAGE = 0.60  # more picture than this: not a text scan
MAX_INK = 0.35       # more ink than this: not a text scan
MASK_DPI = 300       # the text shape is no finer than this


@dataclass
class Layers:
    mask: Image.Image         # mode "1", 0 where there is ink
    background: Image.Image   # "L" or "RGB"
    ink: Image.Image          # "L" or "RGB"
    picture_share: float      # of the page taken by photographs, 0 to 1


def _grown(flags, by=1):
    """Boolean array with every true cell spread to its neighbours."""
    padded = np.pad(flags, by)
    out = np.zeros_like(flags)
    h, w = flags.shape
    for dy in range(2 * by + 1):
        for dx in range(2 * by + 1):
            out |= padded[dy:dy + h, dx:dx + w]
    return out


def _neighbours(flags):
    """How many of the 8 pixels around each are set."""
    padded = np.pad(flags.astype(np.uint8), 1)
    h, w = flags.shape
    total = np.zeros((h, w), np.uint8)
    for dy in range(3):
        for dx in range(3):
            if (dy, dx) != (1, 1):
                total += padded[dy:dy + h, dx:dx + w]
    return total


def _cells(values, size, how):
    """``values`` (2-D) reduced over cells of ``size`` pixels with ``how``."""
    h, w = values.shape
    rows, cols = -(-h // size), -(-w // size)
    padded = np.full((rows * size, cols * size), np.nan, np.float32)
    padded[:h, :w] = values
    return how(padded.reshape(rows, size, cols, size), axis=(1, 3))


def _block_mean(pixels, keep, size):
    """The picture reduced ``size`` times, each block the mean of its
    pixels where ``keep`` is set; blocks with none are filled in from the
    blocks around them."""
    h, w = keep.shape
    rows, cols = -(-h // size), -(-w // size)
    bands = pixels.shape[2]
    values = np.zeros((rows * size, cols * size, bands), np.float32)
    values[:h, :w] = pixels
    weight = np.zeros((rows * size, cols * size), np.float32)
    weight[:h, :w] = keep
    total = (values * weight[..., None]).reshape(rows, size, cols, size, bands).sum((1, 3))
    count = weight.reshape(rows, size, cols, size).sum((1, 3))
    have = count > 0
    out = np.where(have[..., None], total / np.maximum(count, 1)[..., None], 0)
    if not have.any():
        out[:] = values[:h, :w].reshape(-1, bands).mean(0)
        have[:] = True
    while not have.all():
        padded = np.pad(out * have[..., None], ((1, 1), (1, 1), (0, 0)))
        known = np.pad(have, 1).astype(np.float32)
        total = np.zeros_like(out)
        near = np.zeros(have.shape, np.float32)
        for dy in range(3):
            for dx in range(3):
                total += padded[dy:dy + rows, dx:dx + cols]
                near += known[dy:dy + rows, dx:dx + cols]
        new = ~have & (near > 0)
        out[new] = total[new] / near[new][..., None]
        have |= new
    out = out.clip(0, 255).astype(np.uint8)
    return Image.fromarray(out[..., 0], "L") if bands == 1 else Image.fromarray(out, "RGB")


def split(image, dpi, background_dpi, ink_dpi):
    """The layers of a scanned page, or None when it isn't a page of text
    to keep sharp (mostly photograph, nothing dark on it, light on dark).

    ``image`` is the picture ("L", "RGB" or "CMYK") and ``dpi`` the
    resolution it is shown at; ``background_dpi`` and ``ink_dpi`` are the
    resolutions of those two layers."""
    if image.mode == "CMYK":
        image = image.convert("RGB")
    elif image.mode not in ("L", "RGB"):
        image = image.convert("RGB")
    if dpi > MASK_DPI:
        scale = MASK_DPI / dpi
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                             Image.LANCZOS, reducing_gap=3.0)
        dpi = MASK_DPI
    grey = image.convert("L")
    if grey.width < 16 or grey.height < 16:
        return None
    # The paper's brightness around each point: the brightest nearby, smoothed.
    small = grey.resize((max(1, grey.width // 8), max(1, grey.height // 8)), Image.BILINEAR)
    paper = small.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(4))
    paper = np.asarray(paper.resize(grey.size, Image.BILINEAR), np.float32)
    values = np.asarray(grey, np.float32)
    ink = values < paper - np.maximum(INK_MARGIN, INK_SHARE * paper)
    ink &= _neighbours(ink) >= NEIGHBOURS

    cell = max(4, round(dpi * CELL_INCH))
    picture = (_cells(ink, cell, np.nanmean) > PICTURE_INK) | (_cells(paper, cell, np.nanmean) < PICTURE_DARK)
    picture_share = float(picture.mean())
    if picture_share > PICTURE_PAGE:
        return None
    if picture.any():
        spread = np.repeat(np.repeat(_grown(picture), cell, 0), cell, 1)[:ink.shape[0], :ink.shape[1]]
        ink &= ~spread
    share = float(ink.mean())
    if share == 0 or share > MAX_INK:
        return None

    pixels = np.asarray(image, np.float32)
    if pixels.ndim == 2:
        pixels = pixels[..., None]
    background = _block_mean(pixels, ~_grown(ink, 2), max(1, round(dpi / background_dpi)))
    colour = _block_mean(pixels, ink, max(1, round(dpi / ink_dpi)))
    mask = Image.fromarray(np.where(ink, 0, 255).astype(np.uint8), "L").convert("1", dither=Image.NONE)
    return Layers(mask, background, colour, picture_share)


def _strip(mask):
    """(the Group 4 data of ``mask`` written as a one-strip TIFF, its photometric tag)."""
    buf = io.BytesIO()
    mask.save(buf, "TIFF", compression="group4", tiffinfo={278: mask.height})
    data = buf.getvalue()
    with Image.open(io.BytesIO(data)) as tiff:
        offsets, counts = tiff.tag_v2[273], tiff.tag_v2[279]
        photometric = tiff.tag_v2.get(262)
    if len(offsets) != 1:
        raise ValueError("more than one strip")
    return data[offsets[0]:offsets[0] + counts[0]], photometric


def group4(mask):
    """CCITT Group 4 data of ``mask`` (mode "1", 0 = ink) for a PDF
    /CCITTFaxDecode stencil (/K -1, /BlackIs1 false) that paints the ink.
    None when this Pillow can't write Group 4."""
    try:
        data, photometric = _strip(mask)
        if photometric == 1:
            # Written with 0 as black, so the coder saw the ink as its
            # white: turn the picture over to make the ink the coder's black.
            data, _ = _strip(mask.point(lambda v: 255 - v))
        return data
    except Exception:
        return None
