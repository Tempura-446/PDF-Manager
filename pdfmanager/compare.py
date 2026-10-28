"""Compare two versions of a PDF.

Pages are matched up by their text, so an inserted or removed page doesn't
make every later page look different. Each matched pair is compared word by
word and pixel by pixel. The result is a report PDF: a summary page, then
the two versions side by side for each changed page with the changed areas
outlined in red, and the words that were removed and added.
"""

import difflib
import re
from pathlib import Path

from PIL import Image, ImageChops

from pdfmanager import core

CELL = 6          # pixels per cell when grouping changed pixels into areas
THRESHOLD = 48    # 0-255 difference for a pixel to count as changed
DPI = 90


def _load(path, password, dpi):
    """(text, image) for every page."""
    import pypdfium2 as pdfium

    pages = []
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path), password=password)
        try:
            for page in doc:
                textpage = page.get_textpage()
                text = textpage.get_text_range()
                textpage.close()
                image = page.render(scale=dpi / 72).to_pil().convert("RGB")
                pages.append((" ".join(text.split()), image))
        finally:
            doc.close()
    return pages


def _align(old, new):
    """Pairs (old index or None, new index or None) in reading order."""
    matcher = difflib.SequenceMatcher(None, [t for t, _ in old], [t for t, _ in new],
                                      autojunk=False)
    pairs = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            pairs.extend(zip(range(i1, i2), range(j1, j2)))
        else:  # replace, insert, delete: pair up what we can, the rest stand alone
            n = min(i2 - i1, j2 - j1)
            pairs.extend(zip(range(i1, i1 + n), range(j1, j1 + n)))
            pairs.extend((i, None) for i in range(i1 + n, i2))
            pairs.extend((None, j) for j in range(j1 + n, j2))
    return pairs


def _changed_areas(a, b):
    """Rectangles (in a's pixels) where the two page images differ."""
    if b.size != a.size:
        b = b.resize(a.size)
    diff = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > THRESHOLD else 0)
    if not diff.getbbox():
        return []
    w, h = a.size
    cols, rows = (w + CELL - 1) // CELL, (h + CELL - 1) // CELL
    small = diff.resize((cols, rows), Image.Resampling.BOX)
    on = {(x, y) for y in range(rows) for x in range(cols) if small.getpixel((x, y)) > 8}
    areas = []
    while on:  # flood fill neighbouring cells (with a one-cell gap) into areas
        stack, cells = [on.pop()], []
        while stack:
            x, y = stack.pop()
            cells.append((x, y))
            for dx in (-2, -1, 0, 1, 2):
                for dy in (-2, -1, 0, 1, 2):
                    if (x + dx, y + dy) in on:
                        on.remove((x + dx, y + dy))
                        stack.append((x + dx, y + dy))
        xs, ys = [c[0] for c in cells], [c[1] for c in cells]
        areas.append((min(xs) * CELL, min(ys) * CELL, (max(xs) + 1) * CELL, (max(ys) + 1) * CELL))
    return areas


def _word_changes(a, b):
    """(removed words, added words) as lists of phrases."""
    wa, wb = a.split(), b.split()
    removed, added = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed.append(" ".join(wa[i1:i2]))
        if tag in ("replace", "insert"):
            added.append(" ".join(wb[j1:j2]))
    return removed, added


def compare(old, new, output, old_password=None, new_password=None, all_pages=False, dpi=DPI):
    """Write a comparison report of ``old`` and ``new`` to ``output``.
    Returns a summary: {"old_pages", "new_pages", "changes": [{"old", "new",
    "kind", "removed", "added", "areas"}]} with 1-based page numbers (None
    for a page that is only in one file); kind is "changed", "added",
    "removed" or "same"."""
    a, b = _load(old, old_password, dpi), _load(new, new_password, dpi)
    rows = []
    for i, j in _align(a, b):
        if i is None or j is None:
            kind, removed, added, areas = ("added" if i is None else "removed"), [], [], []
        else:
            removed, added = _word_changes(a[i][0], b[j][0])
            areas = _changed_areas(a[i][1], b[j][1])
            kind = "changed" if removed or added or areas else "same"
        rows.append({"old": None if i is None else i + 1, "new": None if j is None else j + 1,
                     "kind": kind, "removed": removed, "added": added, "areas": areas})
    _report(Path(old).name, Path(new).name, a, b, rows, output, all_pages, dpi)
    return {"old_pages": len(a), "new_pages": len(b),
            "changes": [r for r in rows if all_pages or r["kind"] != "same"]}


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------

RED, GREEN, GRAY = (0.80, 0.16, 0.10), (0.12, 0.50, 0.25), (0.42, 0.41, 0.39)


def _clip(text, n):
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= n else text[: n - 1] + "…"


def _label(row):
    if row["kind"] == "added":
        return f"Page {row['new']} added"
    if row["kind"] == "removed":
        return f"Page {row['old']} removed"
    same = row["old"] == row["new"]
    where = f"Page {row['old']}" if same else f"Page {row['old']} → page {row['new']}"
    return where + (" changed" if row["kind"] == "changed" else " unchanged")


def _report(old_name, new_name, a, b, rows, output, all_pages, dpi):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    W, H = landscape(A4)
    margin = 36
    c = canvas.Canvas(str(output), pagesize=(W, H))
    c.setTitle(f"Comparison of {old_name} and {new_name}")
    c.setCreator("PDF Manager")

    # Summary page
    changed = [r for r in rows if r["kind"] != "same"]
    c.setFont("Helvetica-Bold", 20)
    c.drawString(margin, H - margin - 14, "Comparison")
    c.setFont("Helvetica", 11)
    c.setFillColorRGB(*GRAY)
    c.drawString(margin, H - margin - 36, _clip(f"Original: {old_name} ({len(a)} pages)", 120))
    c.drawString(margin, H - margin - 52, _clip(f"Changed: {new_name} ({len(b)} pages)", 120))
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 13)
    y = H - margin - 86
    c.drawString(margin, y, "No differences found." if not changed else
                 f"{len(changed)} page{'s' if len(changed) != 1 else ''} with differences:")
    c.setFont("Helvetica", 10.5)
    y -= 20
    for row in changed:
        if y < margin + 14:
            c.drawString(margin, y, f"… and {len(changed) - changed.index(row)} more")
            break
        bits = []
        if row["removed"]:
            bits.append("removed “" + _clip(" ".join(row["removed"]), 50) + "”")
        if row["added"]:
            bits.append("added “" + _clip(" ".join(row["added"]), 50) + "”")
        if row["kind"] == "changed" and not bits:
            bits.append("layout or pictures changed")
        c.drawString(margin, y, _clip(_label(row) + (": " + "; ".join(bits) if bits else ""), 150))
        y -= 15
    c.showPage()

    # One page per (changed) pair
    half = (W - 3 * margin) / 2
    text_space = 92
    for row in rows if all_pages else changed:
        c.setFont("Helvetica-Bold", 13)
        c.drawString(margin, H - margin - 10, _label(row))
        top = H - margin - 30
        box_h = top - margin - text_space
        for side, (pages, number) in enumerate(((a, row["old"]), (b, row["new"]))):
            x0 = margin + side * (half + margin)
            c.setFont("Helvetica", 9.5)
            c.setFillColorRGB(*GRAY)
            c.drawString(x0, top - 2, ("Original" if side == 0 else "Changed")
                         + (f" — page {number}" if number else " — (no page)"))
            c.setFillColorRGB(0, 0, 0)
            if not number:
                continue
            image = pages[number - 1][1]
            iw, ih = image.size
            scale = min(half / iw, (box_h - 12) / ih)
            dw, dh = iw * scale, ih * scale
            ix, iy = x0 + (half - dw) / 2, top - 12 - dh
            c.drawImage(ImageReader(image), ix, iy, dw, dh)
            c.setStrokeColorRGB(0.8, 0.8, 0.78)
            c.rect(ix, iy, dw, dh, stroke=1, fill=0)
            # Outline the changed areas (computed on the original's pixels;
            # scale them to this image if its size differs).
            ref = a[row["old"] - 1][1].size if row["old"] else (iw, ih)
            fx, fy = iw / ref[0], ih / ref[1]
            c.setStrokeColorRGB(*RED)
            c.setFillColorRGB(*RED, alpha=0.12)
            c.setLineWidth(1.2)
            for x1, y1, x2, y2 in row["areas"]:
                c.rect(ix + x1 * fx * scale - 2, iy + dh - y2 * fy * scale - 2,
                       (x2 - x1) * fx * scale + 4, (y2 - y1) * fy * scale + 4, stroke=1, fill=1)
            c.setFillColorRGB(0, 0, 0, alpha=1)
            c.setLineWidth(1)
        y = margin + text_space - 16
        for sign, colour, phrases in (("−", RED, row["removed"]), ("+", GREEN, row["added"])):
            for phrase in phrases[:3]:
                c.setFillColorRGB(*colour)
                c.setFont("Helvetica", 10)
                c.drawString(margin, y, _clip(f"{sign} {phrase}", 160))
                y -= 13
        if row["kind"] == "changed" and not row["removed"] and not row["added"]:
            c.setFillColorRGB(*GRAY)
            c.setFont("Helvetica", 10)
            c.drawString(margin, y, "The text is the same; the layout or pictures changed "
                                    "(outlined in red).")
        c.setFillColorRGB(0, 0, 0)
        c.showPage()
    c.save()
    return output
