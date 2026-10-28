"""PDF operations. Every function reads from paths and writes to paths so it
can be used from the CLI or imported as a library."""

import io
import os
from pathlib import Path

from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import RectangleObject

from pdfmanager.pageranges import parse_page_ranges

POINTS_PER_INCH = 72
POINTS_PER_MM = 72 / 25.4

# Paper sizes in points (portrait orientation: width, height).
PAPER_SIZES = {
    "a0": (2384, 3370),
    "a1": (1684, 2384),
    "a2": (1191, 1684),
    "a3": (842, 1191),
    "a4": (595, 842),
    "a5": (420, 595),
    "a6": (298, 420),
    "b4": (709, 1001),
    "b5": (499, 709),
    "letter": (612, 792),
    "legal": (612, 1008),
    "tabloid": (792, 1224),
    "ledger": (1224, 792),
    "executive": (522, 756),
}


class PdfManagerError(Exception):
    pass


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def open_pdf(path, password=None):
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        if password is None:
            raise PdfManagerError(f"{path} is encrypted; supply a password")
        if not reader.decrypt(password):
            raise PdfManagerError(f"wrong password for {path}")
    return reader


def _write(writer, output):
    output = Path(output)
    if output.parent and not output.parent.exists():
        output.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temporary file first so an input can safely be overwritten.
    tmp = output.with_name(output.name + ".tmp")
    with open(tmp, "wb") as fh:
        writer.write(fh)
    os.replace(tmp, output)
    return output


def _writer_from_pages(reader, indices):
    writer = PdfWriter()
    for i in indices:
        writer.add_page(reader.pages[i])
    if reader.metadata:
        writer.add_metadata({k: v for k, v in reader.metadata.items() if isinstance(v, str)})
    return writer


def parse_paper_size(spec):
    """Parse 'a4', 'letter', 'a4-landscape', '210x297mm', '8.5x11in' or '600x800'
    (points) into (width, height) in points."""
    spec = spec.strip().lower()
    landscape = False
    for suffix in ("-landscape", ":landscape", "-l"):
        if spec.endswith(suffix):
            landscape, spec = True, spec[: -len(suffix)]
            break
    if spec in PAPER_SIZES:
        w, h = PAPER_SIZES[spec]
    else:
        unit = 1.0
        for suffix, factor in (("mm", POINTS_PER_MM), ("cm", POINTS_PER_MM * 10),
                               ("in", POINTS_PER_INCH), ("pt", 1.0)):
            if spec.endswith(suffix):
                unit, spec = factor, spec[: -len(suffix)]
                break
        try:
            w_str, h_str = spec.split("x")
            w, h = float(w_str) * unit, float(h_str) * unit
        except ValueError:
            known = ", ".join(sorted(PAPER_SIZES))
            raise PdfManagerError(
                f"unknown paper size {spec!r}; use one of {known} or WxH[mm|cm|in|pt]"
            ) from None
        if w <= 0 or h <= 0:
            raise PdfManagerError("paper dimensions must be positive")
    if landscape:
        w, h = max(w, h), min(w, h)
    return float(w), float(h)


def _page_size(page):
    box = page.mediabox
    w, h = float(box.width), float(box.height)
    if (page.rotation or 0) % 180 == 90:
        w, h = h, w
    return w, h


def _describe_size(w, h):
    for name, (pw, ph) in PAPER_SIZES.items():
        if abs(w - pw) < 3 and abs(h - ph) < 3:
            return name.upper() if name.startswith(("a", "b")) else name.capitalize()
        if abs(w - ph) < 3 and abs(h - pw) < 3:
            label = name.upper() if name.startswith(("a", "b")) else name.capitalize()
            return f"{label} landscape"
    return f"{w / POINTS_PER_MM:.0f}x{h / POINTS_PER_MM:.0f} mm"


# --------------------------------------------------------------------------
# Inspection
# --------------------------------------------------------------------------

def info(path, password=None):
    reader = open_pdf(path, password)
    meta = reader.metadata or {}
    sizes = {}
    for page in reader.pages:
        label = _describe_size(*_page_size(page))
        sizes[label] = sizes.get(label, 0) + 1
    return {
        "file": str(path),
        "file_size": os.path.getsize(path),
        "pages": len(reader.pages),
        "encrypted": reader.is_encrypted,
        "page_sizes": sizes,
        "title": meta.get("/Title"),
        "author": meta.get("/Author"),
        "subject": meta.get("/Subject"),
        "creator": meta.get("/Creator"),
        "producer": meta.get("/Producer"),
        "created": meta.get("/CreationDate"),
        "modified": meta.get("/ModDate"),
    }


def extract_text(path, output=None, pages=None, password=None):
    reader = open_pdf(path, password)
    indices = parse_page_ranges(pages, len(reader.pages)) if pages else range(len(reader.pages))
    text = "\n\f\n".join(reader.pages[i].extract_text() or "" for i in indices)
    if output:
        Path(output).write_text(text, encoding="utf-8")
    return text


# --------------------------------------------------------------------------
# Combining and splitting
# --------------------------------------------------------------------------

def merge(inputs, output, bookmarks=True, password=None):
    """Concatenate ``inputs`` into ``output``. Each input may be a path or a
    ``(path, page_range)`` tuple to include only some pages."""
    if not inputs:
        raise PdfManagerError("nothing to merge")
    writer = PdfWriter()
    for item in inputs:
        path, spec = item if isinstance(item, tuple) else (item, None)
        reader = open_pdf(path, password)
        indices = parse_page_ranges(spec, len(reader.pages)) if spec else range(len(reader.pages))
        first = len(writer.pages)
        for i in indices:
            writer.add_page(reader.pages[i])
        if bookmarks and len(writer.pages) > first:
            writer.add_outline_item(Path(path).stem, first)
    return _write(writer, output)


def split(path, outdir, every=None, ranges=None, prefix=None, password=None):
    """Split a PDF.

    - ``ranges``: list of range specs, one output file per spec.
    - ``every``: chunks of N pages.
    - neither: one file per page.
    Returns the list of written files.
    """
    reader = open_pdf(path, password)
    total = len(reader.pages)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    prefix = prefix or Path(path).stem

    if ranges:
        groups = [(spec, parse_page_ranges(spec, total)) for spec in ranges]
        labels = [spec.replace(",", "_").replace(" ", "") for spec, _ in groups]
        groups = [g for _, g in groups]
    else:
        n = every or 1
        if n < 1:
            raise PdfManagerError("--every must be at least 1")
        groups = [list(range(i, min(i + n, total))) for i in range(0, total, n)]
        width = len(str(total))
        labels = [
            f"{g[0] + 1:0{width}d}" if len(g) == 1 else f"{g[0] + 1:0{width}d}-{g[-1] + 1:0{width}d}"
            for g in groups
        ]

    written = []
    for label, indices in zip(labels, groups):
        written.append(_write(_writer_from_pages(reader, indices), outdir / f"{prefix}_{label}.pdf"))
    return written


def select_pages(path, output, pages, password=None):
    """Write only ``pages`` (a range spec, in the given order) to ``output``.
    Used for extracting, reordering and duplicating pages."""
    reader = open_pdf(path, password)
    indices = parse_page_ranges(pages, len(reader.pages))
    return _write(_writer_from_pages(reader, indices), output)


def delete_pages(path, output, pages, password=None):
    reader = open_pdf(path, password)
    total = len(reader.pages)
    remove = set(parse_page_ranges(pages, total))
    keep = [i for i in range(total) if i not in remove]
    if not keep:
        raise PdfManagerError("cannot delete every page")
    return _write(_writer_from_pages(reader, keep), output)


def reverse(path, output, password=None):
    reader = open_pdf(path, password)
    return _write(_writer_from_pages(reader, reversed(range(len(reader.pages)))), output)


def interleave(front, back, output, reverse_back=True, password=None):
    """Combine separately scanned front and back sides into one document.
    Back sides are reversed by default, as produced by a single-sided feeder."""
    fr, br = open_pdf(front, password), open_pdf(back, password)
    backs = list(br.pages)
    if reverse_back:
        backs.reverse()
    writer = PdfWriter()
    for i in range(max(len(fr.pages), len(backs))):
        if i < len(fr.pages):
            writer.add_page(fr.pages[i])
        if i < len(backs):
            writer.add_page(backs[i])
    return _write(writer, output)


# --------------------------------------------------------------------------
# Page geometry
# --------------------------------------------------------------------------

def rotate(path, output, degrees, pages=None, password=None):
    if degrees % 90:
        raise PdfManagerError("rotation must be a multiple of 90 degrees")
    reader = open_pdf(path, password)
    targets = set(parse_page_ranges(pages, len(reader.pages))) if pages else None
    writer = PdfWriter(clone_from=reader)
    for i, page in enumerate(writer.pages):
        if targets is None or i in targets:
            page.rotate(degrees)
    return _write(writer, output)


def resize(path, output, size=None, scale=None, fit="fit", pages=None,
           auto_orient=True, password=None):
    """Resize pages either to a paper ``size`` or by a ``scale`` factor.

    ``fit`` controls how content goes onto a new paper size:
      - "fit":     scale uniformly to fit inside the page, centred (default)
      - "fill":    scale uniformly to cover the page, centred (may crop)
      - "stretch": scale each axis independently to exactly fill the page
      - "none":    keep content at original size, centred (pads or crops)
    With ``auto_orient`` the target page is rotated to match each source
    page's orientation (landscape pages stay landscape).
    """
    if (size is None) == (scale is None):
        raise PdfManagerError("specify exactly one of size or scale")
    if fit not in ("fit", "fill", "stretch", "none"):
        raise PdfManagerError(f"unknown fit mode {fit!r}")
    if scale is not None and scale <= 0:
        raise PdfManagerError("scale must be positive")

    reader = open_pdf(path, password)
    targets = set(parse_page_ranges(pages, len(reader.pages))) if pages else None
    target = parse_paper_size(size) if size else None
    writer = PdfWriter(clone_from=reader)

    for i, page in enumerate(writer.pages):
        if targets is not None and i not in targets:
            continue
        # Bake any /Rotate into the content so the geometry below is simple.
        page.transfer_rotation_to_content()
        box = page.mediabox
        sw, sh = float(box.width), float(box.height)
        left, bottom = float(box.left), float(box.bottom)

        if scale is not None:
            tw, th = sw * scale, sh * scale
            sx = sy = scale
        else:
            tw, th = target
            if auto_orient and (sw > sh) != (tw > th) and sw != sh:
                tw, th = th, tw
            if fit == "fit":
                sx = sy = min(tw / sw, th / sh)
            elif fit == "fill":
                sx = sy = max(tw / sw, th / sh)
            elif fit == "stretch":
                sx, sy = tw / sw, th / sh
            else:
                sx = sy = 1.0

        dx = (tw - sw * sx) / 2
        dy = (th - sh * sy) / 2
        op = Transformation().translate(-left, -bottom).scale(sx, sy).translate(dx, dy)
        page.add_transformation(op)
        new_box = RectangleObject([0, 0, tw, th])
        page.mediabox = new_box
        for name in ("/CropBox", "/TrimBox", "/BleedBox", "/ArtBox"):
            if name in page:
                del page[name]

    return _write(writer, output)


def crop(path, output, margins, pages=None, password=None):
    """Trim ``margins`` (left, bottom, right, top) in points from each page."""
    left, bottom, right, top = margins
    reader = open_pdf(path, password)
    targets = set(parse_page_ranges(pages, len(reader.pages))) if pages else None
    writer = PdfWriter(clone_from=reader)
    for i, page in enumerate(writer.pages):
        if targets is not None and i not in targets:
            continue
        box = page.mediabox
        new = [float(box.left) + left, float(box.bottom) + bottom,
               float(box.right) - right, float(box.top) - top]
        if new[2] <= new[0] or new[3] <= new[1]:
            raise PdfManagerError(f"margins are larger than page {i + 1}")
        page.mediabox = RectangleObject(new)
        page.cropbox = RectangleObject(new)
    return _write(writer, output)


def n_up(path, output, per_sheet=2, size="a4", password=None):
    """Place 2, 4, 6, 8 or 9 pages on each sheet (handout style)."""
    layouts = {2: (2, 1), 4: (2, 2), 6: (3, 2), 8: (4, 2), 9: (3, 3)}
    if per_sheet not in layouts:
        raise PdfManagerError(f"pages per sheet must be one of {sorted(layouts)}")
    cols, rows = layouts[per_sheet]
    sw, sh = parse_paper_size(size)
    # Use landscape sheets for wide grids.
    if cols > rows:
        sw, sh = max(sw, sh), min(sw, sh)
    else:
        sw, sh = min(sw, sh), max(sw, sh)
    cell_w, cell_h = sw / cols, sh / rows

    # Work on a copy so pages can be normalised without touching the reader.
    source = PdfWriter(clone_from=open_pdf(path, password))
    writer = PdfWriter()
    sheet = None
    for i, src in enumerate(source.pages):
        slot = i % per_sheet
        if slot == 0:
            sheet = writer.add_blank_page(width=sw, height=sh)
        src.transfer_rotation_to_content()
        box = src.mediabox
        pw, ph = float(box.width), float(box.height)
        s = min(cell_w / pw, cell_h / ph) * 0.95
        col, row = slot % cols, slot // cols
        x = col * cell_w + (cell_w - pw * s) / 2
        y = sh - (row + 1) * cell_h + (cell_h - ph * s) / 2
        op = Transformation().translate(-float(box.left), -float(box.bottom)).scale(s, s).translate(x, y)
        sheet.merge_transformed_page(src, op)
    return _write(writer, output)


# --------------------------------------------------------------------------
# File size, security, metadata
# --------------------------------------------------------------------------

def compress(path, output, image_quality=None, password=None):
    """Losslessly compress content streams and deduplicate objects.
    Optionally re-encode images as JPEG at ``image_quality`` (1-95)."""
    reader = open_pdf(path, password)
    writer = PdfWriter(clone_from=reader)
    for page in writer.pages:
        page.compress_content_streams()
        if image_quality:
            for img in page.images:
                try:
                    img.replace(img.image, quality=image_quality)
                except Exception:
                    # Some image types (masks, odd colour spaces) can't be
                    # re-encoded; leave them untouched.
                    pass
    try:
        writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    except TypeError:  # pypdf < 6
        writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
    return _write(writer, output)


def encrypt(path, output, user_password, owner_password=None, password=None):
    reader = open_pdf(path, password)
    writer = PdfWriter(clone_from=reader)
    writer.encrypt(user_password=user_password,
                   owner_password=owner_password or user_password,
                   algorithm="AES-256")
    return _write(writer, output)


def decrypt(path, output, password):
    reader = open_pdf(path, password)
    writer = PdfWriter(clone_from=reader)
    return _write(writer, output)


def set_metadata(path, output, password=None, **fields):
    reader = open_pdf(path, password)
    writer = PdfWriter(clone_from=reader)
    updates = {f"/{k.capitalize()}": v for k, v in fields.items() if v is not None}
    if updates:
        writer.add_metadata(updates)
    return _write(writer, output)


# --------------------------------------------------------------------------
# Stamping
# --------------------------------------------------------------------------

def _overlay_page(width, height, draw):
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    draw(c, width, height)
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]


def watermark(path, output, text=None, stamp_pdf=None, opacity=0.15,
              font_size=60, angle=45, pages=None, below=False, password=None):
    """Stamp ``text`` diagonally across pages, or overlay the first page of
    ``stamp_pdf``. With ``below`` the stamp goes underneath the content."""
    if (text is None) == (stamp_pdf is None):
        raise PdfManagerError("specify exactly one of text or stamp_pdf")
    reader = open_pdf(path, password)
    targets = set(parse_page_ranges(pages, len(reader.pages))) if pages else None
    stamp_page = open_pdf(stamp_pdf).pages[0] if stamp_pdf else None
    writer = PdfWriter(clone_from=reader)

    for i, page in enumerate(writer.pages):
        if targets is not None and i not in targets:
            continue
        box = page.mediabox
        w, h = float(box.width), float(box.height)
        if stamp_page is None:
            def draw(c, w, h):
                c.setFillAlpha(opacity)
                c.setFont("Helvetica-Bold", font_size)
                c.translate(w / 2, h / 2)
                c.rotate(angle)
                c.drawCentredString(0, -font_size / 3, text)
            stamp = _overlay_page(w, h, draw)
        else:
            stamp = stamp_page
        op = Transformation().translate(float(box.left), float(box.bottom))
        page.merge_transformed_page(stamp, op, over=not below)
    return _write(writer, output)


def number_pages(path, output, fmt="{n} / {total}", position="bottom-center",
                 font_size=10, start=1, pages=None, password=None):
    """Add page numbers. ``fmt`` may use {n} and {total}."""
    positions = ("bottom-center", "bottom-right", "bottom-left",
                 "top-center", "top-right", "top-left")
    if position not in positions:
        raise PdfManagerError(f"position must be one of {', '.join(positions)}")
    reader = open_pdf(path, password)
    total = len(reader.pages)
    targets = set(parse_page_ranges(pages, total)) if pages else None
    writer = PdfWriter(clone_from=reader)
    vert, horiz = position.split("-")
    margin = 28

    n = start
    for i, page in enumerate(writer.pages):
        if targets is not None and i not in targets:
            continue
        label = fmt.format(n=n, total=total)
        n += 1
        box = page.mediabox
        w, h = float(box.width), float(box.height)

        def draw(c, w, h, label=label):
            c.setFont("Helvetica", font_size)
            y = margin if vert == "bottom" else h - margin
            if horiz == "center":
                c.drawCentredString(w / 2, y, label)
            elif horiz == "right":
                c.drawRightString(w - margin, y, label)
            else:
                c.drawString(margin, y, label)

        op = Transformation().translate(float(box.left), float(box.bottom))
        page.merge_transformed_page(_overlay_page(w, h, draw), op)
    return _write(writer, output)


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------

def images_to_pdf(images, output, size=None, margin=0):
    """Save one or more images as a PDF, one image per page.

    Without ``size`` each page matches its image (at the image's DPI, or 72).
    With ``size`` images are fitted onto that paper size, orientation chosen
    per image, with ``margin`` points of padding."""
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    if not images:
        raise PdfManagerError("no images given")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output))
    for img_path in images:
        with Image.open(img_path) as im:
            im.load()
            dpi = im.info.get("dpi", (72, 72))[0] or 72
            iw, ih = im.size[0] * 72 / dpi, im.size[1] * 72 / dpi
            if im.mode not in ("RGB", "L", "CMYK"):
                im = im.convert("RGB")
            if size:
                pw, ph = parse_paper_size(size)
                if (iw > ih) != (pw > ph):
                    pw, ph = ph, pw
                s = min((pw - 2 * margin) / iw, (ph - 2 * margin) / ih)
                dw, dh = iw * s, ih * s
                x, y = (pw - dw) / 2, (ph - dh) / 2
            else:
                pw, ph = iw + 2 * margin, ih + 2 * margin
                dw, dh, x, y = iw, ih, margin, margin
            c.setPageSize((pw, ph))
            c.drawImage(ImageReader(im), x, y, width=dw, height=dh)
            c.showPage()
    c.save()
    return output


def pdf_to_images(path, outdir, dpi=150, fmt="png", pages=None, password=None):
    """Render pages to image files. Requires the optional pypdfium2 package."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise PdfManagerError("rendering pages needs pypdfium2: pip install pypdfium2") from None
    doc = pdfium.PdfDocument(str(path), password=password)
    try:
        total = len(doc)
        indices = parse_page_ranges(pages, total) if pages else range(total)
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        width = len(str(total))
        written = []
        for i in indices:
            image = doc[i].render(scale=dpi / 72).to_pil()
            if fmt.lower() in ("jpg", "jpeg") and image.mode != "RGB":
                image = image.convert("RGB")
            target = outdir / f"{Path(path).stem}_{i + 1:0{width}d}.{fmt}"
            image.save(target)
            written.append(target)
        return written
    finally:
        doc.close()
