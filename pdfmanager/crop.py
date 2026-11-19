# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Interactive visual page cropping with rotation-aware coordinates and scope selection.

Supports non-destructive cropping (updating /CropBox) and optional permanent
trimming (clipping the content stream and shrinking /MediaBox + /CropBox).
"""

from pathlib import Path

from pypdf import PageObject, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, FloatObject, NameObject

from pdfmanager import core


def visual_to_media_rect(visual_rect, media_box, rotation=0):
    """Map a visual-space rectangle ``(vx0, vy0, vx1, vy1)`` (bottom-left origin
    in the displayed/rotated orientation, in PDF points) to unrotated PDF
    ``/MediaBox`` coordinates ``(ux0, uy0, ux1, uy1)``, clamped to ``media_box``."""
    mx0, my0, mx1, my1 = (float(v) for v in media_box)
    w, h = mx1 - mx0, my1 - my0
    vx0, vy0, vx1, vy1 = (float(v) for v in visual_rect)
    vx0, vx1 = min(vx0, vx1), max(vx0, vx1)
    vy0, vy1 = min(vy0, vy1), max(vy0, vy1)
    rot = int(rotation or 0) % 360

    def to_unrotated(vx, vy):
        if rot == 90:
            return mx0 + (w - vy), my0 + vx
        if rot == 180:
            return mx0 + (w - vx), my0 + (h - vy)
        if rot == 270:
            return mx0 + vy, my0 + (h - vx)
        return mx0 + vx, my0 + vy

    corners = [to_unrotated(x, y) for x in (vx0, vx1) for y in (vy0, vy1)]
    ux0 = max(mx0, min(mx1, min(p[0] for p in corners)))
    uy0 = max(my0, min(my1, min(p[1] for p in corners)))
    ux1 = max(mx0, min(mx1, max(p[0] for p in corners)))
    uy1 = max(my0, min(my1, max(p[1] for p in corners)))
    if ux1 - ux0 < 4.0 or uy1 - uy0 < 4.0:
        raise core.PdfManagerError("crop rectangle is too small")
    return (round(ux0, 3), round(uy0, 3), round(ux1, 3), round(uy1, 3))


def resolve_scope_pages(total_pages, scope="page", page_num=1, pages_spec=None):
    """Return sorted 1-based page numbers selected by ``scope``."""
    scope = (scope or "page").strip().lower()
    if scope == "all":
        return list(range(1, total_pages + 1))
    if scope == "odd":
        return [p for p in range(1, total_pages + 1) if p % 2 == 1]
    if scope == "even":
        return [p for p in range(1, total_pages + 1) if p % 2 == 0]
    if scope == "range":
        if not pages_spec or not str(pages_spec).strip():
            raise core.PdfManagerError("specify a page range (e.g. 1-3, 5)")
        idxs = core.parse_pages(str(pages_spec), total_pages)
        return sorted({i + 1 for i in idxs})
    # default: single page
    n = max(1, min(total_pages, int(page_num or 1)))
    return [n]


def _clip_page_permanently(page, rect):
    """Wrap page content stream with a clipping path for ``rect`` and shrink boxes."""
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    clip_prefix = f"q {x0:.3f} {y0:.3f} {w:.3f} {h:.3f} re W n\n".encode("ascii")
    clip_suffix = b"\nQ\n"
    existing = page.get_contents()
    if existing is not None:
        raw = existing.get_data()
        stream = DecodedStreamObject()
        stream.set_data(clip_prefix + raw + clip_suffix)
        page[NameObject("/Contents")] = stream
    box = ArrayObject([FloatObject(v) for v in rect])
    page[NameObject("/MediaBox")] = box
    page[NameObject("/CropBox")] = box
    if "/TrimBox" in page:
        page[NameObject("/TrimBox")] = box
    if "/BleedBox" in page:
        page[NameObject("/BleedBox")] = box


def crop_pages(
    input_path,
    output_path,
    rect,
    *,
    scope="page",
    page_num=1,
    pages_spec=None,
    password=None,
    trim_permanently=False,
):
    """Crop selected pages of ``input_path`` to visual-space ``rect`` ``[x0, y0, x1, y1]``
    and write the result to ``output_path``. Returns the list of 1-based cropped pages."""
    src = Path(input_path)
    dst = Path(output_path)
    reader = core.open_pdf(src, password=password)
    total = len(reader.pages)
    if total == 0:
        raise core.PdfManagerError("document has no pages")
    targets = set(resolve_scope_pages(total, scope=scope, page_num=page_num, pages_spec=pages_spec))

    writer = PdfWriter()
    for idx, page in enumerate(reader.pages, start=1):
        out_page = writer.add_page(page)
        if idx in targets:
            mb = (
                float(out_page.mediabox.left),
                float(out_page.mediabox.bottom),
                float(out_page.mediabox.right),
                float(out_page.mediabox.top),
            )
            rot = int(out_page.get("/Rotate", 0) or 0)
            media_rect = visual_to_media_rect(rect, mb, rotation=rot)
            if trim_permanently:
                _clip_page_permanently(out_page, media_rect)
            else:
                box = ArrayObject([FloatObject(v) for v in media_rect])
                out_page[NameObject("/CropBox")] = box
                if "/TrimBox" in out_page:
                    out_page[NameObject("/TrimBox")] = box

    with open(dst, "wb") as fh:
        writer.write(fh)
    return sorted(targets)
