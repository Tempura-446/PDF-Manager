"""PDF operations. Every function reads from paths and writes to paths so it
can be used from the CLI or imported as a library."""

import io
import logging
import os
import re
import tempfile
import threading
from pathlib import Path

from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from pypdf.errors import PyPdfError
from pypdf.generic import (ArrayObject, ContentStream, DictionaryObject, FloatObject, IndirectObject,
                           NameObject, NumberObject, RectangleObject, StreamObject)

# pypdf notes that a page copied without its annotations has fewer links than
# the original; that's what _writer_from_pages asks for (see there).
logging.getLogger("pypdf.generic._link").addFilter(
    lambda record: "Annotation sizes differ" not in record.getMessage())

from pdfmanager import fonts, imaging, progress
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
    try:
        reader = PdfReader(str(path))
    except PyPdfError as exc:
        raise PdfManagerError(f"{path} is not a readable PDF ({exc})") from None
    if reader.is_encrypted:
        # A file with only an owner password (printing or copying restricted)
        # opens with an empty password, also when a password meant for another
        # file of the batch was given.
        for candidate in dict.fromkeys([password or "", ""]):
            if reader.decrypt(candidate):
                return reader
        if password is None:
            raise PdfManagerError(f"{path} is encrypted; supply a password")
        raise PdfManagerError(f"wrong password for {path}")
    return reader


def _write(writer, output):
    output = Path(output)
    if output.is_dir():
        raise PdfManagerError(f"{output} is a folder; give the name of the PDF to write")
    if output.parent and not output.parent.exists():
        output.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temporary file first so an input can safely be overwritten,
    # and nothing is left behind when writing fails.
    fd, tmp = tempfile.mkstemp(prefix=output.name + ".", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            writer.write(fh)
        os.replace(tmp, output)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return output


def _writer_from_pages(reader, indices):
    """A writer with the reader's pages ``indices``, in that order.

    Bookmarks, form fields and links between the kept pages come along;
    links to pages left out are dropped. (pypdf's add_page would copy a
    page's links as they are, and with them, unseen, every page they point
    to: "Delete pages" would then leave the deleted page in the file.)"""
    writer = PdfWriter()
    indices = list(indices)
    unique = list(dict.fromkeys(indices))  # each page once, in order of first use
    writer.append(reader, pages=unique)
    if len(unique) < len(indices):
        _reorder_pages(writer, [unique.index(i) for i in indices])  # the copies
    if reader.metadata:
        writer.add_metadata({k: v for k, v in reader.metadata.items() if isinstance(v, str)})
    return writer


def _reorder_pages(writer, order):
    """Put the writer's pages in ``order`` (indices as they are now); an
    index listed twice makes a copy of that page."""
    pages = list(writer.flattened_pages)
    kids, flat, seen = ArrayObject(), [], set()
    for i in order:
        page = pages[i]
        if i in seen:
            # Viewers reject a page listed twice: a copy is a new page
            # dictionary, sharing the content and annotations below it
            # (see _own_contents for when they're changed).
            copy = PageObject(writer)
            for key in page:
                if key != "/Parent":
                    copy[NameObject(key)] = page.raw_get(key)
            copy[NameObject("/Parent")] = writer._pages
            writer._add_object(copy)
            page = copy
        seen.add(i)
        kids.append(page.indirect_reference)
        flat.append(page)
    node = writer._pages.get_object()
    node[NameObject("/Kids")] = kids
    node[NameObject("/Count")] = NumberObject(len(flat))
    writer.flattened_pages = flat


def clone_document(reader):
    """A writer with everything in ``reader``, like ``PdfWriter(clone_from=
    reader)``, also for long documents whose pages link to each other:
    pypdf copies objects recursively, and a chain of "next page" links
    through hundreds of pages is deeper than Python allows. The pages'
    annotations are copied after the rest, when their target pages are
    already known."""
    detached = []
    for i, page in enumerate(reader.pages):
        stored = reader.get_object(page.indirect_reference)  # the object pypdf copies from
        if isinstance(stored, DictionaryObject) and "/Annots" in stored:
            detached.append((i, stored, stored.raw_get("/Annots")))
            del stored["/Annots"]
    try:
        writer = PdfWriter(clone_from=reader)
    finally:
        for _, stored, annots in detached:
            stored[NameObject("/Annots")] = annots
    for i, _, annots in detached:
        writer.pages[i][NameObject("/Annots")] = annots.clone(writer)
    return writer


def _content_refs(page):
    """References to the page's content stream(s)."""
    contents = page.get("/Contents")
    refs = [contents] if isinstance(contents, IndirectObject) else []
    array = contents.get_object() if contents is not None else None
    if isinstance(array, ArrayObject):
        refs.extend(c for c in array if isinstance(c, IndirectObject))
    return refs


def _annot_refs(page):
    annots = page.get("/Annots")
    refs = [annots] if isinstance(annots, IndirectObject) else []
    array = annots.get_object() if annots is not None else None
    if isinstance(array, ArrayObject):
        refs.extend(a for a in array if isinstance(a, IndirectObject))
    return refs


def _shared_objects(writer):
    """Ids of the content streams and annotations that two or more pages
    share: a duplicated page, or identical pages after Compress."""
    counts = {}
    for page in writer.pages:
        for ref in _content_refs(page) + _annot_refs(page):
            counts[ref.idnum] = counts.get(ref.idnum, 0) + 1
    return {idnum for idnum, n in counts.items() if n > 1}


def _own_contents(page, shared):
    """Before a page is changed: give it its own content stream and
    annotations if another page shares them (see ``_shared_objects``), so
    the change doesn't land on that page too."""
    if not shared:
        return
    pdf = page.indirect_reference.pdf
    if any(ref.idnum in shared for ref in _content_refs(page)):
        own = ContentStream(page["/Contents"], pdf)
        page[NameObject("/Contents")] = pdf._add_object(own)
    if any(ref.idnum in shared for ref in _annot_refs(page)):
        fresh = ArrayObject()
        for ref in page["/Annots"]:
            annot = ref.get_object()
            if isinstance(annot, DictionaryObject) and getattr(ref, "idnum", None) in shared:
                copy = DictionaryObject()
                for key, value in annot.items():
                    copy[NameObject(key)] = value
                ref = pdf._add_object(copy)
            fresh.append(ref)
        page[NameObject("/Annots")] = fresh


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


def _visible_box(page):
    """The part of the page viewers show: its crop box, within the media box."""
    media, crop = page.mediabox, page.cropbox
    left, bottom = max(float(media.left), float(crop.left)), max(float(media.bottom), float(crop.bottom))
    right, top = min(float(media.right), float(crop.right)), min(float(media.top), float(crop.top))
    if right <= left or top <= bottom:  # a crop box outside the page: ignore it
        left, bottom, right, top = (float(v) for v in (media.left, media.bottom, media.right, media.top))
    return RectangleObject([left, bottom, right, top])


def _page_size(page):
    box = _visible_box(page)
    w, h = float(box.width), float(box.height)
    if (page.rotation or 0) % 180 == 90:
        w, h = h, w
    return w, h


def _moved_points(values, op):
    points = []
    for i in range(0, len(values) - 1, 2):
        points.extend(op.apply_on((float(values[i]), float(values[i + 1]))))
    return ArrayObject(FloatObject(v) for v in points)


def _turn_appearances(annot, rotation):
    """Turn an annotation's appearance streams by ``rotation`` degrees.
    Viewers fit an appearance's box into /Rect without turning it, so a
    turned /Rect alone shows stamps, signatures and notes sideways."""
    ap = annot.get("/AP")
    ap = ap.get_object() if ap is not None else None
    if not isinstance(ap, DictionaryObject):
        return
    for kind in ("/N", "/R", "/D"):
        normal = ap.get(kind)
        normal = normal.get_object() if normal is not None else None
        if normal is None:
            continue
        states = [normal]
        if isinstance(normal, DictionaryObject) and not isinstance(normal, StreamObject):
            states = [s.get_object() for s in normal.values()]  # one per state (checkboxes)
        for stream in states:
            if not isinstance(stream, StreamObject):
                continue
            old = Transformation(tuple(float(v) for v in stream.get("/Matrix", (1, 0, 0, 1, 0, 0))))
            new = old.transform(Transformation().rotate(rotation))
            stream[NameObject("/Matrix")] = ArrayObject(FloatObject(v) for v in new.ctm)


def _transform_annotations(page, op, turn=0):
    """Move and resize the page's annotations (links, form fields, comments,
    drawings) by ``op`` so they stay on top of the content they belong to.
    With ``turn`` (degrees) their appearances are turned as well."""
    annots = page.get("/Annots")
    if annots is None:
        return
    for ref in annots.get_object():
        annot = ref.get_object()
        if not isinstance(annot, DictionaryObject):
            continue  # a null or missing annotation: viewers skip it too
        rect = annot.get("/Rect")
        if rect is not None and len(rect) == 4:
            x1, y1 = op.apply_on((float(rect[0]), float(rect[1])))
            x2, y2 = op.apply_on((float(rect[2]), float(rect[3])))
            annot[NameObject("/Rect")] = RectangleObject(
                [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)])
        # Highlights, lines, polygons and callout arrows keep their own points.
        for key in ("/QuadPoints", "/L", "/Vertices", "/CL"):
            points = annot.get(key)
            if points is not None:
                annot[NameObject(key)] = _moved_points(points.get_object(), op)
        ink = annot.get("/InkList")
        if ink is not None:
            annot[NameObject("/InkList")] = ArrayObject(
                _moved_points(path.get_object(), op) for path in ink.get_object())
        if turn:
            _turn_appearances(annot, turn)


def _transfer_rotation(page):
    """Like ``page.transfer_rotation_to_content()`` but also turns the
    page's annotations, appearances included."""
    rotation = quarter_turn(page.rotation)
    if not rotation:
        return
    box = page.mediabox
    corners = [(float(box.left), float(box.bottom)), (float(box.right), float(box.top))]
    rotate = Transformation().rotate(-rotation)
    turned = [rotate.apply_on(c) for c in corners]
    page.transfer_rotation_to_content()
    new = page.mediabox
    dx = float(new.left) - min(x for x, _ in turned)
    dy = float(new.bottom) - min(y for _, y in turned)
    _transform_annotations(page, rotate.translate(dx, dy), turn=-rotation)


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
        indices = parse_page_ranges(spec, len(reader.pages)) if spec else list(range(len(reader.pages)))
        if not indices:
            continue
        # With bookmarks, each file gets one, and its own bookmarks under it;
        # form fields and links between its kept pages come along too.
        writer.append(reader, outline_item=Path(path).stem if bookmarks else None, pages=indices,
                      import_outline=bookmarks)
    return _write(writer, output)


def split(path, outdir, every=None, ranges=None, prefix=None, password=None, bookmarks=None,
          max_size=None, notes=None):
    """Split a PDF.

    - ``ranges``: list of range specs, one output file per spec.
    - ``every``: chunks of N pages.
    - ``bookmarks``: 1 or 2: one file per bookmark of that level or above
      (1 = top-level bookmarks only), named after the bookmark.
    - ``max_size``: files of at most this many bytes, as many pages as fit.
    - none of these: one file per page.
    Messages for the user (a page too large for ``max_size`` on its own) are
    appended to ``notes``. Returns the list of written files.
    """
    reader = open_pdf(path, password)
    total = len(reader.pages)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    if bookmarks:
        groups = _bookmark_groups(path, total, int(bookmarks), password)
        width = max(2, len(str(len(groups))))
        first = 0 if groups[0][0] is None else 1  # 00 for the pages before the first bookmark
        taken, names = set(), []
        for n, (title, _) in enumerate(groups, first):
            label = f"{n:0{width}d} {_title_filename(title or 'Opening pages')}"
            name = f"{prefix}_{label}" if prefix else label
            names.append(_unique_filename(name, taken))
        written = []
        for n, (name, (_, indices)) in enumerate(zip(names, groups)):
            progress.report(n, len(groups), "file")
            written.append(_write(_writer_from_pages(reader, indices), outdir / name))
        return written

    prefix = prefix or Path(path).stem
    width = len(str(total))

    def label(g):
        return f"{g[0] + 1:0{width}d}" if len(g) == 1 else f"{g[0] + 1:0{width}d}-{g[-1] + 1:0{width}d}"

    if max_size is not None:
        written = []
        for indices, data in _size_chunks(path, reader, max_size, notes):
            written.append(_write(_Bytes(data), outdir / f"{prefix}_{label(indices)}.pdf"))
        return written

    if ranges:
        groups = [(spec, parse_page_ranges(spec, total)) for spec in ranges]
        labels = [spec.replace(",", "_").replace(" ", "") for spec, _ in groups]
        groups = [g for _, g in groups]
    else:
        n = every or 1
        if n < 1:
            raise PdfManagerError("--every must be at least 1")
        groups = [list(range(i, min(i + n, total))) for i in range(0, total, n)]
        labels = [label(g) for g in groups]

    written = []
    for label_, indices in zip(labels, groups):
        written.append(_write(_writer_from_pages(reader, indices), outdir / f"{prefix}_{label_}.pdf"))
    return written


def _bookmark_groups(path, total, level, password=None):
    """[(bookmark title, 0-based pages)] for splitting at the bookmarks of
    ``level`` or above (1 = top level). The pages before the first bookmark
    come first, with the title None."""
    from pdfmanager import outline  # outline uses this module

    if level not in (1, 2):
        raise PdfManagerError("the bookmark level must be 1 (top level) or 2")
    starts = {}  # first page -> title; of several bookmarks on one page, the first
    for item in sorted(outline.get_outline(path, password), key=lambda i: i["page"]):
        if item["level"] < level:
            starts.setdefault(item["page"] - 1, item["title"])
    if not starts:
        raise PdfManagerError(
            f"{Path(path).name} has no bookmarks to split at; split it another way, or add "
            "bookmarks first (the Bookmarks tool can create them from the headings)")
    firsts = sorted(starts)
    groups = [(None, list(range(firsts[0])))] if firsts[0] > 0 else []
    for k, first in enumerate(firsts):
        end = firsts[k + 1] if k + 1 < len(firsts) else total
        groups.append((starts[first], list(range(first, end))))
    return groups


def _title_filename(title, limit=80):
    """A bookmark title as (part of) a file name that works on every system."""
    text = re.sub(r"[\\/]+", "-", str(title))
    text = "".join("_" if c in '<>:"|?*' or ord(c) < 32 else c for c in text)
    text = " ".join(text.split())
    while len(text.encode("utf-8")) > limit:
        text = text[:-1]
    return text.strip(" .") or "Untitled"


def _unique_filename(stem, taken, ext=".pdf"):
    """``stem + ext``, or ``stem (2) + ext`` and so on if that name (in any
    upper/lower case, as on Windows and macOS) is already in ``taken``."""
    name, n = stem + ext, 1
    while name.lower() in taken:
        n += 1
        name = f"{stem} ({n}){ext}"
    taken.add(name.lower())
    return name


def _pages_bytes(reader, indices):
    buf = io.BytesIO()
    _writer_from_pages(reader, indices).write(buf)
    return buf.getvalue()


def _size_chunks(path, reader, max_size, notes=None):
    """Split for ``split(max_size=...)``: yields (0-based pages, the file's
    bytes) for runs of pages whose file is at most ``max_size`` bytes, each
    as long as possible. A page that is larger on its own gets a file of
    its own, and a note says so.

    Each file is really written (in memory) and measured; the number of
    pages to try is estimated from the sizes measured so far, so a long
    document takes a few tries per file, not one per page."""
    max_size = int(max_size)
    if max_size < 1024:
        raise PdfManagerError("the largest file size must be at least 1 KB")
    total = len(reader.pages)
    # Results made from a protected file are protected again afterwards,
    # which adds a little to each file.
    limit = int(max_size * 0.98) - 2048 if reader.is_encrypted else max_size
    per_page = max(1.0, os.path.getsize(path) / total)
    start = 0
    while start < total:
        progress.report(start, total, "page")
        left = total - start
        sizes, data = {}, {}  # pages tried -> file size, and the bytes of those that fit
        fits, too_big = 0, left + 1  # most pages known to fit; fewest known not to
        tries = min(left, max(1, int(limit * 0.97 / per_page)))
        while True:
            blob = _pages_bytes(reader, range(start, start + tries))
            sizes[tries] = len(blob)
            if len(blob) <= limit:
                fits, data[tries] = tries, blob
            else:
                too_big = tries
            if fits == left or too_big == fits + 1:
                break
            if too_big > left:  # everything tried fits: try more pages
                guess = int(tries * limit * 0.97 / max(1, len(blob)))
            elif fits == 0:  # nothing fits yet: try fewer
                guess = int(tries * limit * 0.97 / max(1, len(blob)))
            else:  # between the two, assuming size grows evenly with the pages
                low, high = sizes[fits], sizes[too_big]
                guess = fits + int((limit - low) * (too_big - fits) / max(1, high - low))
            tries = min(max(guess, fits + 1), too_big - 1, left)
        if fits:
            yield list(range(start, start + fits)), data[fits]
            per_page = max(1.0, sizes[fits] / fits)
            start += fits
        else:  # one page is larger than the limit by itself
            blob = _pages_bytes(reader, [start])
            if notes is not None:
                notes.append(f"page {start + 1} alone is {len(blob) / 1e6:.1f} MB, more than "
                             f"{max_size / 1e6:g} MB, so it is in a file of its own")
            yield [start], blob
            start += 1


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
    backs = list(range(len(br.pages)))
    if reverse_back:
        backs.reverse()
    writer = PdfWriter()
    writer.append(fr)  # whole files, so links and bookmarks within each stay
    writer.append(br)
    fronts = len(fr.pages)
    order = []
    for i in range(max(fronts, len(backs))):
        if i < fronts:
            order.append(i)
        if i < len(backs):
            order.append(fronts + backs[i])
    _reorder_pages(writer, order)
    return _write(writer, output)


# --------------------------------------------------------------------------
# Page geometry
# --------------------------------------------------------------------------

def arrange(path, output, plan, inserts=(), password=None):
    """Rebuild a document page by page, as the reader's page tools do.
    ``plan`` lists the new document's pages in order: {"page": 1-based page
    of ``path``, or "file": n (1-based, of ``inserts``) and "page", and
    "rotate": extra degrees; a "file" without "page" goes in whole}. Pages
    left out are deleted, a page listed
    twice is copied. Bookmarks, form fields and links between kept pages
    come along."""
    if not isinstance(plan, (list, tuple)) or not plan:
        raise PdfManagerError("a document needs at least one page")
    readers = [open_pdf(path, password)] + [open_pdf(p) for p in inserts]
    wanted = []
    for entry in plan:
        try:
            source = int(entry.get("file", 0))
            turn = int(entry.get("rotate", 0))
            # An inserted file without a page number goes in whole.
            pages = [int(entry["page"]) - 1] if "page" in entry or source == 0 else None
        except (AttributeError, KeyError, TypeError, ValueError):
            raise PdfManagerError("each page needs its page number") from None
        if not 0 <= source < len(readers):
            raise PdfManagerError(f"there's no file {source} to take pages from")
        for page in range(len(readers[source].pages)) if pages is None else pages:
            if not 0 <= page < len(readers[source].pages):
                raise PdfManagerError(f"page {page + 1} doesn't exist")
            if turn % 90:
                raise PdfManagerError("rotation must be a multiple of 90 degrees")
            wanted.append((source, page, turn % 360))
    if all(source == 0 for source, _, _ in wanted):
        writer = _writer_from_pages(readers[0], [page for _, page, _ in wanted])
    else:
        writer = PdfWriter()
        where = {}  # (file, page) -> index among the pages appended
        for source, reader in enumerate(readers):
            pages = list(dict.fromkeys(page for s, page, _ in wanted if s == source))
            if not pages:
                continue
            writer.append(reader, pages=pages, import_outline=source == 0)
            for page in pages:
                where[(source, page)] = len(where)
        _reorder_pages(writer, [where[(source, page)] for source, page, _ in wanted])
        if readers[0].metadata:
            writer.add_metadata({k: v for k, v in readers[0].metadata.items() if isinstance(v, str)})
    # A copied page is its own page dictionary: turning it leaves the other alone.
    for page, (_, _, turn) in zip(writer.pages, wanted):
        if turn:
            page.rotate(turn)
    return _write(writer, output)


def rotate(path, output, degrees, pages=None, password=None):
    if degrees % 90:
        raise PdfManagerError("rotation must be a multiple of 90 degrees")
    reader = open_pdf(path, password)
    targets = set(parse_page_ranges(pages, len(reader.pages))) if pages else None
    writer = clone_document(reader)
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
    writer = clone_document(reader)
    shared = _shared_objects(writer)

    for i, page in enumerate(writer.pages):
        if targets is not None and i not in targets:
            continue
        _own_contents(page, shared)
        # Bake any /Rotate into the content so the geometry below is simple.
        _transfer_rotation(page)
        # The new page holds what the old one showed (its crop box).
        box = _visible_box(page)
        sw, sh = float(box.width), float(box.height)
        left, bottom = float(box.left), float(box.bottom)

        if scale is not None:
            tw, th = sw * scale, sh * scale
            sx = sy = scale
        else:
            tw, th = target
            if auto_orient and (sw > sh) != (tw > th) and sw != sh:
                tw, th = th, tw
            if not sw or not sh:
                raise PdfManagerError(f"page {i + 1} has no size (its box is empty), so it can't be resized")
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
        _transform_annotations(page, op)
        page.mediabox = RectangleObject([0, 0, tw, th])
        for name in ("/CropBox", "/TrimBox", "/BleedBox", "/ArtBox"):
            if name in page:
                del page[name]

    return _write(writer, output)


def quarter_turn(rotation):
    """A page's /Rotate as 0, 90, 180 or 270. The value must be a multiple of
    90; anything else counts as 0, as viewers (pdf.js, Acrobat) show it."""
    try:
        rotation = int(rotation or 0) % 360
    except (TypeError, ValueError):
        return 0
    return rotation if rotation % 90 == 0 else 0


def _unturned(margins, rotation):
    """Margins as a page is shown (left, bottom, right, top) -> the same
    edges in the page's own coordinates, before its /Rotate."""
    left, bottom, right, top = margins
    return {0: (left, bottom, right, top), 90: (top, left, bottom, right),
            180: (right, top, left, bottom), 270: (bottom, right, top, left)}[quarter_turn(rotation)]


def crop(path, output, margins, pages=None, visual=True, password=None):
    """Trim ``margins`` (left, bottom, right, top) in points from what each
    page shows (its crop box). ``visual`` margins are as the page is shown,
    so "top" is the top of a page that is turned by /Rotate; otherwise they
    are in the page's own coordinates (what the page editor measures)."""
    reader = open_pdf(path, password)
    targets = set(parse_page_ranges(pages, len(reader.pages))) if pages else None
    writer = clone_document(reader)
    for i, page in enumerate(writer.pages):
        if targets is not None and i not in targets:
            continue
        left, bottom, right, top = _unturned(margins, page.rotation or 0) if visual else margins
        box = _visible_box(page)
        new = [float(box.left) + left, float(box.bottom) + bottom,
               float(box.right) - right, float(box.top) - top]
        if new[2] <= new[0] or new[3] <= new[1]:
            raise PdfManagerError(f"margins are larger than page {i + 1}")
        page.mediabox = RectangleObject(new)
        page.cropbox = RectangleObject(new)
        for name in ("/TrimBox", "/BleedBox", "/ArtBox"):
            if name in page:
                del page[name]
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
    total = len(open_pdf(path, password).pages)
    sheets = [list(range(i, min(i + per_sheet, total))) for i in range(0, total, per_sheet)]
    return _write(_impose(path, password, sheets, (sw, sh), cols, rows), output)


def _impose(path, password, sheets, sheet_size, cols, rows, fill=0.95):
    """A writer with sheets of ``sheet_size`` holding the pages of ``path``
    in a grid of ``cols`` x ``rows`` cells: ``sheets`` lists, per sheet, the
    0-based page for each cell (row by row from the top left; None leaves
    the cell empty). Each page is scaled to ``fill`` of its cell, centred.
    Links, form fields and comments move with their page; links to other
    pages point to the sheet that holds that page."""
    sw, sh = sheet_size
    cell_w, cell_h = sw / cols, sh / rows
    # Work on a copy so pages can be normalised without touching the reader.
    source = clone_document(open_pdf(path, password))
    shared = _shared_objects(source)
    page_numbers = {p.indirect_reference.idnum: i for i, p in enumerate(source.pages)}
    for page in source.pages:
        _own_contents(page, shared)
        _retarget_links(page, page_numbers)
    writer = PdfWriter()
    sheet_of = {}  # page -> the sheet it is on
    for n, cells in enumerate(sheets):
        progress.report(n, len(sheets), "sheet")
        sheet = writer.add_blank_page(width=sw, height=sh)
        for slot, i in enumerate(cells):
            if i is None:
                continue
            src = source.pages[i]
            sheet_of.setdefault(i, n)
            _transfer_rotation(src)
            box = _visible_box(src)  # what the page shows goes in the cell
            pw, ph = float(box.width), float(box.height)
            if not pw or not ph:
                raise PdfManagerError(f"page {i + 1} has no size (its box is empty), so it can't be placed")
            s = min(cell_w / pw, cell_h / ph) * fill
            col, row = slot % cols, slot // cols
            x = col * cell_w + (cell_w - pw * s) / 2
            y = sh - (row + 1) * cell_h + (cell_h - ph * s) / 2
            op = Transformation().translate(-float(box.left), -float(box.bottom)).scale(s, s).translate(x, y)
            sheet.merge_transformed_page(src, op)  # also moves the annotations
    _resolve_links(writer, sheet_of)
    return writer


def booklet(path, output, size=None, password=None):
    """Arrange pages for a booklet printed on both sides of the paper,
    folded in the middle and stapled on the fold (saddle stitch): two pages
    side by side on each landscape sheet, in the order that puts them in
    sequence once folded (n, 1 | 2, n-1 | n-2, 3 | ...). Blank pages are
    added at the end to make a multiple of four.

    ``size`` is the sheet (paper) size, e.g. "a4" or "letter", turned
    landscape; by default a sheet is twice the first page (A5 pages give
    A4 sheets, A4 pages A3 sheets). Print the result double-sided,
    flipping on the short edge."""
    reader = open_pdf(path, password)
    total = len(reader.pages)
    if size:
        w, h = parse_paper_size(size)
        sheet = (max(w, h), min(w, h))
    else:
        w, h = _page_size(reader.pages[0])
        sheet = (2 * w, h)
    padded = -(-total // 4) * 4
    sides = []
    for k in range(padded // 4):
        sides.append([padded - 1 - 2 * k, 2 * k])      # front of sheet k + 1
        sides.append([2 * k + 1, padded - 2 - 2 * k])  # its back
    sides = [[i if i < total else None for i in side] for side in sides]
    return _write(_impose(path, password, sides, sheet, 2, 1, fill=1.0), output)


_LINK_TARGET = NameObject("/PdfmTargetPage")


def _retarget_links(page, page_numbers):
    """Replace links to other pages with a placeholder page number, so
    merging doesn't drag whole pages along; ``_resolve_links`` points them
    at the right sheet afterwards. Named destinations can't be kept."""
    annots = page.get("/Annots")
    if annots is None:
        return
    keep = ArrayObject()
    for ref in annots.get_object():
        annot = ref.get_object()
        if not isinstance(annot, DictionaryObject):
            continue  # a null or missing annotation
        if annot.get("/Subtype") == "/Link":
            dest = annot.get("/Dest")
            action = annot.get("/A")
            if action is not None and action.get_object().get("/S") == "/GoTo":
                dest = action.get_object().get("/D")
            if dest is not None:
                dest = dest.get_object()
                target = None
                if isinstance(dest, ArrayObject) and dest and hasattr(dest[0], "idnum"):
                    target = page_numbers.get(dest[0].idnum)
                for key in ("/Dest", "/A"):
                    if key in annot:
                        del annot[key]
                if target is None:
                    continue  # named or broken destination: drop the link
                annot[_LINK_TARGET] = NumberObject(target)
        keep.append(ref)
    page[NameObject("/Annots")] = keep


def _resolve_links(writer, sheet_of):
    """Point the links ``_retarget_links`` marked at the sheet that holds
    their page (``sheet_of``: page -> sheet)."""
    for sheet in writer.pages:
        for ref in sheet.get("/Annots", ArrayObject()):
            annot = ref.get_object()
            if isinstance(annot, DictionaryObject) and _LINK_TARGET in annot:
                target = sheet_of.get(int(annot[_LINK_TARGET]))
                del annot[_LINK_TARGET]
                if target is not None:
                    annot[NameObject("/Dest")] = ArrayObject(
                        [writer.pages[target].indirect_reference, NameObject("/Fit")])


# --------------------------------------------------------------------------
# Blank pages and white margins
# --------------------------------------------------------------------------

def _insert_positions(after, total, at_start=False):
    """Where blank pages go: the number of pages before each place, from a
    page list ("3,7", "end", "all"; 0 or "start" = before the first page)."""
    positions = {0} if at_start else set()
    rest = []
    for token in str(after or "").split(","):
        token = token.strip().lower()
        if token in ("0", "start"):
            positions.add(0)
        elif token:
            rest.append(token)
    if rest:
        positions.update(i + 1 for i in parse_page_ranges(",".join(rest), total))
    if not positions:
        raise PdfManagerError("say after which pages the blank pages go")
    return sorted(positions)


def insert_blank_pages(path, output, after=None, count=1, at_start=False, password=None):
    """Insert ``count`` blank pages after each page in ``after`` (a page
    list such as "3,7" or "end"; "all" puts them after every page, e.g. to
    print with a sheet for notes beside each page; 0 means before the first
    page, as does ``at_start``). Each blank page has the size of the page
    before it (at the start, of the first page). Bookmarks, links and form
    fields are kept."""
    if not 1 <= int(count) <= 100:
        raise PdfManagerError("insert between 1 and 100 blank pages at each place")
    reader = open_pdf(path, password)
    positions = _insert_positions(after, len(reader.pages), at_start)
    writer = clone_document(reader)
    sizes = [_page_size(page) for page in writer.pages]
    for n, position in enumerate(reversed(positions)):  # from the end, so positions stay put
        progress.report(n, len(positions), "place")
        width, height = sizes[max(position - 1, 0)]
        for _ in range(int(count)):
            writer.insert_blank_page(width, height, position)
    return _write(writer, output)


def trim_margins(path, output, padding=10, same=False, pages=None, tolerance=24, dpi=50,
                 password=None):
    """Crop away the white margins around each page's content: the pages
    are drawn at ``dpi`` and everything lighter than ``tolerance`` below
    pure white counts as margin. ``padding`` (points) is kept around the
    content. With ``same``, every page gets the same crop, large enough
    for the content of all of them (good for a book whose page numbers or
    headings sit in different places); otherwise each page is trimmed on
    its own. Pages that are entirely blank are left as they are.

    Only the crop box changes (what viewers show and print); the media box
    stays, so the margins can be brought back.
    Returns {"trimmed": [...], "blank": [...]} with 1-based page numbers."""
    import pypdfium2 as pdfium

    if padding < 0:
        raise PdfManagerError("the space around the content can't be negative")
    reader = open_pdf(path, password)
    total = len(reader.pages)
    targets = sorted(set(parse_page_ranges(pages, total))) if pages else list(range(total))
    found, blank = {}, []  # page -> margins as fractions of the page as shown (l, b, r, t)
    white = 255 - max(0, min(254, int(tolerance)))
    with PDFIUM_LOCK:
        try:
            doc = pdfium.PdfDocument(str(path), password=password)
        except pdfium.PdfiumError as exc:
            raise PdfManagerError(f"{path} can't be rendered ({exc})") from None
        try:
            doc.init_forms()  # filled-in form fields count as content
            for n, i in enumerate(targets):
                progress.report(n, len(targets), "page")
                page = doc[i]
                try:
                    image = page.render(scale=dpi / 72, may_draw_forms=True).to_pil().convert("L")
                finally:
                    page.close()
                box = image.point(lambda v: 255 if v < white else 0).getbbox()
                if box is None:
                    blank.append(i)
                    continue
                w, h = image.size
                x0, y0, x1, y1 = box
                found[i] = (x0 / w, (h - y1) / h, (w - x1) / w, y0 / h)
        finally:
            doc.close()
    writer = clone_document(reader)
    margins = {}  # page -> margins to trim as the page is shown, in points
    for i, fractions in found.items():
        w, h = _page_size(writer.pages[i])
        margins[i] = [max(0.0, f * size - padding) for f, size in zip(fractions, (w, h, w, h))]
    if same and margins:
        common = [min(m[k] for m in margins.values()) for k in range(4)]
        margins = {i: common for i in margins}
    for i, shown in margins.items():
        page = writer.pages[i]
        left, bottom, right, top = _unturned(shown, page.rotation or 0)
        box = _visible_box(page)
        page.cropbox = RectangleObject([float(box.left) + left, float(box.bottom) + bottom,
                                        float(box.right) - right, float(box.top) - top])
        for name in ("/TrimBox", "/BleedBox", "/ArtBox"):
            if name in page:
                del page[name]
    _write(writer, output)
    return {"trimmed": [i + 1 for i in sorted(margins)], "blank": [i + 1 for i in blank]}


# --------------------------------------------------------------------------
# File size, security, metadata
# --------------------------------------------------------------------------

def _finish_compression(writer):
    imaging.unwrap_ascii(writer)
    for page in writer.pages:
        page.compress_content_streams()
    imaging.deflate_plain_streams(writer)
    try:
        writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    except TypeError:  # pypdf < 6
        writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)


def compress(path, output, level="medium", image_quality=None, max_dpi=None, password=None,
             sharp_scans=True, notes=None):
    """Reduce file size.

    ``level`` is a preset:
      - "low":    lossless only (compress streams, drop ASCII85/hex wrappers,
                  remove duplicate objects, pack objects into object
                  streams when pikepdf is installed)
      - "medium": also downsample images above 150 DPI, JPEG quality 75
      - "high":   also downsample images above 96 DPI, JPEG quality 50
                  ("large" is accepted as an alias)
    ``image_quality`` (1-95) and ``max_dpi`` override the preset's values.
    Images are only replaced when the new version is smaller.

    With ``sharp_scans`` (medium and high), a scanned page keeps its text
    at full resolution: the scan becomes a black-and-white text shape over
    a low-resolution background (see scanlayers.py), when that is no
    larger than the usual result. ``notes`` (a list) is told how many
    pages that was done for.
    """
    try:
        level = imaging.normalize_level(level)
    except ValueError as exc:
        raise PdfManagerError(str(exc)) from None
    preset_dpi, preset_quality = imaging.COMPRESSION_LEVELS[level]
    asked_dpi, asked_quality = max_dpi, image_quality
    max_dpi = preset_dpi if max_dpi is None else max_dpi
    image_quality = preset_quality if image_quality is None else image_quality
    if image_quality is not None and not 1 <= image_quality <= 95:
        raise PdfManagerError("image quality must be between 1 and 95")
    if max_dpi is not None and max_dpi < 1:
        raise PdfManagerError("maximum DPI must be at least 1")

    reader = open_pdf(path, password)
    writer = clone_document(reader)
    try:
        imaging.unwrap_ascii(writer)  # first: everything after reads the streams
        if max_dpi or image_quality:
            layers = imaging.SCAN_LAYERS.get(level) if sharp_scans else None
            if layers:
                layers = (asked_dpi or layers[0], asked_quality or layers[1], *layers[2:])
            sharp = imaging.downsample_images(writer, max_dpi, image_quality or 85, layers)[2]
            if sharp and notes is not None:
                notes.append(f"{sharp} scanned page{'s' if sharp != 1 else ''} kept sharp")
        _finish_compression(writer)
    except PyPdfError as exc:  # a stream pypdf can't rewrite
        raise PdfManagerError(f"can't rewrite {path} ({exc})") from None
    _write(writer, output)
    pack(output)
    return Path(output)


def pack(path):
    """Repack the PDF at ``path`` with qpdf (through pikepdf), in place: its
    many small objects (page dictionaries, fonts' descriptions, outlines)
    go into compressed object streams, which pypdf can't write. Nothing is
    decoded or re-encoded, so it is lossless; text-heavy files lose a
    third or more. Skipped, leaving the file as it is, when pikepdf isn't
    installed, can't read the file, or the result isn't smaller. Returns
    whether the file was repacked."""
    try:
        import pikepdf
    except ImportError:
        return False
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        try:
            with pikepdf.open(path) as pdf:
                pdf.save(tmp, object_stream_mode=pikepdf.ObjectStreamMode.generate,
                         compress_streams=True, recompress_flate=False)
        except (pikepdf.PdfError, OSError, RuntimeError):
            return False  # pypdf's file is still there, as good as before
        if os.path.getsize(tmp) >= path.stat().st_size:
            return False
        os.replace(tmp, path)
        return True
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def convert_color(path, output, target, icc_profile=None, password=None):
    """Convert a PDF's colours to ``target`` ("gray", "rgb" or "cmyk").

    Converts colours set in page content and form XObjects with the device,
    calibrated and ICC-based colour spaces, plus all images. With
    ``icc_profile`` images are converted through that profile and it is
    embedded as the document's output intent (e.g. for print shops).
    Gradients, patterns and spot colours are left unchanged."""
    if target not in imaging.COLOR_TARGETS:
        raise PdfManagerError(f"colour target must be one of {', '.join(imaging.COLOR_TARGETS)}")
    if icc_profile and not Path(icc_profile).is_file():
        raise PdfManagerError(f"ICC profile not found: {icc_profile}")
    reader = open_pdf(path, password)
    writer = clone_document(reader)
    from PIL import ImageCms

    try:
        imaging.convert_colors(writer, target, icc_profile)
        _finish_compression(writer)
    except (OSError, ImageCms.PyCMSError) as exc:  # unreadable or wrong-type ICC profile
        raise PdfManagerError(f"cannot use ICC profile {icc_profile}: {exc}") from None
    except PyPdfError as exc:  # a stream pypdf can't rewrite
        raise PdfManagerError(f"can't rewrite {path} ({exc})") from None
    return _write(writer, output)


class _Bytes:
    """Written data that ``_write`` can save like a writer."""

    def __init__(self, data):
        self.data = data

    def write(self, stream):
        stream.write(self.data)


def encrypt(path, output, user_password, owner_password=None, password=None, permissions=None):
    """Protect with AES-256. With pikepdf installed, qpdf does the protecting
    and packs the file into object streams on the way (see ``pack``), so a
    compressed file that is protected again keeps its packing; the
    unprotected document only passes through memory.

    ``permissions`` (filetools.permissions) limits what people with the
    open password (``user_password``, which may be empty: anyone can open
    it) can do; the permissions password (``owner_password``) lifts the
    limits, so it must differ from the open password."""
    from pdfmanager import filetools

    reader = open_pdf(path, password)
    restricted = filetools.restricts(permissions)
    if restricted and (not owner_password or owner_password == user_password):
        raise PdfManagerError("to limit printing, copying or changes, set a permissions "
                              "password different from the password to open it")
    if not user_password and not owner_password:
        raise PdfManagerError("enter a password to open it, or choose what to limit")
    owner_password = owner_password or user_password
    try:
        import pikepdf
    except ImportError:
        pikepdf = None
    if pikepdf is not None:
        plain = io.BytesIO()
        clone_document(reader).write(plain)
        plain.seek(0)
        try:
            with pikepdf.open(plain) as pdf:
                protected = io.BytesIO()
                allow = permissions if permissions is not None else pikepdf.Permissions(
                    modify_assembly=True)
                pdf.save(protected, encryption=pikepdf.Encryption(
                             user=user_password or "", owner=owner_password, aes=True, R=6,
                             allow=allow),
                         object_stream_mode=pikepdf.ObjectStreamMode.generate,
                         compress_streams=True, recompress_flate=False)
            return _write(_Bytes(protected.getvalue()), output)
        except (pikepdf.PdfError, RuntimeError):
            pass  # qpdf can't read pypdf's copy: protect it the pypdf way
    writer = clone_document(reader)
    extra = {"permissions_flag": filetools.pypdf_flags(permissions)} if permissions else {}
    writer.encrypt(user_password=user_password or "", owner_password=owner_password,
                   algorithm="AES-256", **extra)
    return _write(writer, output)


def decrypt(path, output, password):
    """Remove the protection. A PDF that limits printing, copying or
    changes needs its permissions password for this (as in Acrobat), not
    just the password to open it."""
    reader = open_pdf(path, password)
    try:
        import pikepdf
    except ImportError:
        pikepdf = None
    if pikepdf is not None and reader.is_encrypted:
        from pdfmanager import filetools

        try:
            with pikepdf.open(path, password=password or "") as pdf:
                if not pdf.owner_password_matched and filetools.restricts(pdf.allow):
                    raise PdfManagerError(
                        f"this PDF limits what can be done with it ({filetools.describe(pdf.allow)}):"
                        " removing its protection needs its permissions password")
        except pikepdf.PdfError:
            pass  # qpdf can't read it: pypdf already could
    writer = clone_document(reader)
    return _write(writer, output)


def set_metadata(path, output, password=None, **fields):
    reader = open_pdf(path, password)
    writer = clone_document(reader)
    updates = {f"/{k.capitalize()}": v for k, v in fields.items() if v is not None}
    if updates:
        writer.add_metadata(updates)
        _update_xmp(writer, updates)
    return _write(writer, output)


# Where viewers look first for the title and author: the XMP metadata, when
# a file has it (Word, LibreOffice, InDesign and Ghostscript all write it).
_XMP_FIELDS = {
    "/Title": ("dc:title", "http://purl.org/dc/elements/1.1/", "Alt"),
    "/Author": ("dc:creator", "http://purl.org/dc/elements/1.1/", "Seq"),
    "/Subject": ("dc:description", "http://purl.org/dc/elements/1.1/", "Alt"),
    "/Keywords": ("pdf:Keywords", "http://ns.adobe.com/pdf/1.3/", None),
}


def _update_xmp(writer, updates):
    """Keep the XMP metadata in step with the Info fields, so readers that
    prefer it (this app's own, Acrobat) show the new title and author."""
    from xml.sax.saxutils import escape

    ref = writer._root_object.get("/Metadata")
    stream = ref.get_object() if ref is not None else None
    if not isinstance(stream, StreamObject):
        return
    try:
        xml = stream.get_data().decode("utf-8")
    except (UnicodeDecodeError, PyPdfError):
        return
    for key, value in updates.items():
        if key not in _XMP_FIELDS:
            continue
        tag, namespace, kind = _XMP_FIELDS[key]
        prefix = tag.split(":")[0]
        text = escape(str(value))
        if kind == "Alt":
            inner = f'<rdf:Alt><rdf:li xml:lang="x-default">{text}</rdf:li></rdf:Alt>'
        elif kind == "Seq":
            inner = f"<rdf:Seq><rdf:li>{text}</rdf:li></rdf:Seq>"
        else:
            inner = text
        element = f'<{tag} xmlns:{prefix}="{namespace}">{inner}</{tag}>' if text else ""
        pattern = re.compile(rf"<{tag}\b[^>]*?(?:/>|>.*?</{tag}>)", re.DOTALL)
        if pattern.search(xml):
            xml = pattern.sub(lambda m: element, xml, count=1)
        elif element and "</rdf:Description>" in xml:
            xml = xml.replace("</rdf:Description>", element + "</rdf:Description>", 1)
    data = xml.encode("utf-8")
    for key in ("/Filter", "/DecodeParms"):
        if key in stream:
            del stream[key]
    stream._data = data
    stream[NameObject("/Length")] = NumberObject(len(data))
    if hasattr(stream, "decoded_self"):
        stream.decoded_self = None


# --------------------------------------------------------------------------
# Stamping
# --------------------------------------------------------------------------

def pdf_canvas(target, **kwargs):
    """A reportlab canvas writing to ``target`` (a path or file object).
    Its streams are written as plain binary: reportlab's default wraps them
    in ASCII85 text, 25% bigger, and without its optional C accelerator that
    encoding takes most of the time of writing a page picture."""
    from reportlab import rl_config
    from reportlab.pdfgen import canvas

    rl_config.useA85 = 0
    return canvas.Canvas(target, **kwargs)


def _overlay_page(width, height, draw):
    buf = io.BytesIO()
    c = pdf_canvas(buf, pagesize=(width, height))
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
    writer = clone_document(reader)
    shared = _shared_objects(writer)

    for i, page in enumerate(writer.pages):
        if targets is not None and i not in targets:
            continue
        _own_contents(page, shared)
        w, h, matrix = _upright(page)  # across what the page shows, the right way up
        if stamp_page is None:
            def draw(c, w, h):
                c.setFillAlpha(opacity)
                c.setFont(fonts.font_for(text, bold=True), font_size)
                c.translate(w / 2, h / 2)
                c.rotate(angle)
                c.drawCentredString(0, -font_size / 3, text)
            stamp = _overlay_page(w, h, draw)
        else:
            stamp = stamp_page
        page.merge_transformed_page(stamp, matrix, over=not below)
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
    writer = clone_document(reader)
    shared = _shared_objects(writer)
    vert, horiz = position.split("-")
    margin = 28
    if not _NUMBER_FORMAT.fullmatch(fmt):
        raise PdfManagerError("the format may use {n} and {total}, with other braces as {{ and }}")

    n = start
    for i, page in enumerate(writer.pages):
        if targets is not None and i not in targets:
            continue
        label = fmt.format(n=n, total=total)
        n += 1
        _own_contents(page, shared)
        w, h, matrix = _upright(page)  # inside what the page shows, the right way up

        def draw(c, w, h, label=label):
            c.setFont(fonts.font_for(label), font_size)
            y = margin if vert == "bottom" else h - margin
            if horiz == "center":
                c.drawCentredString(w / 2, y, label)
            elif horiz == "right":
                c.drawRightString(w - margin, y, label)
            else:
                c.drawString(margin, y, label)

        page.merge_transformed_page(_overlay_page(w, h, draw), matrix)
    return _write(writer, output)


# {n}, {total}, {{ and }} are fine in a page number format; anything else in
# braces would be a KeyError from str.format.
_NUMBER_FORMAT = re.compile(r"(?:[^{}]|\{\{|\}\}|\{(?:n|total)(?::[^{}]*)?\})*")


def _safe_filename(name, fallback, limit=120):
    name = Path(str(name).replace("\\", "/")).name
    name = "".join("_" if c in '<>:"|?*' or ord(c) < 32 else c for c in name).strip(" .")
    stem, suffix = Path(name).stem, Path(name).suffix[:16]
    while len((stem + suffix).encode("utf-8")) > limit and stem:  # file systems stop at 255 bytes
        stem = stem[:-1]
    return (stem + suffix).strip(" .") or fallback


def extract_media(path, outdir, images=True, attachments=True, min_size=16, password=None):
    """Save the pictures and/or attached files of a PDF into ``outdir``.
    Pictures are saved in their own format where possible (JPEG stays JPEG),
    each picture once even if it's used on several pages; pictures smaller
    than ``min_size`` pixels (lines, dots, tiny icons) are skipped.
    Returns {"images": [paths], "attachments": [paths]}."""
    import hashlib

    reader = open_pdf(path, password)
    outdir = Path(outdir)
    stem = Path(path).stem
    saved = {"images": [], "attachments": []}
    taken = set()

    def target(folder, name):
        folder.mkdir(parents=True, exist_ok=True)
        candidate, n = folder / name, 1
        while candidate.name.lower() in taken or candidate.exists():
            n += 1
            candidate = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        taken.add(candidate.name.lower())
        return candidate

    if images:
        seen = set()
        for number, page in enumerate(reader.pages, 1):
            progress.report(number - 1, len(reader.pages), "page")
            try:
                found = list(page.images)
            except Exception:
                continue  # a damaged image dictionary: skip the page's pictures
            for k, image in enumerate(found, 1):
                try:
                    data = image.data
                    width, height = image.image.size if image.image is not None else (0, 0)
                except Exception:
                    continue
                if width < min_size or height < min_size:
                    continue
                digest = hashlib.sha1(data).digest()
                if digest in seen:
                    continue
                seen.add(digest)
                ext = Path(image.name).suffix.lower() or ".png"
                if ext in (".jp2", ".jpx") and image.image is not None:
                    # A photo with a soft mask comes back as JPEG 2000, which
                    # few programs open; PNG keeps the transparency too.
                    buf = io.BytesIO()
                    image.image.save(buf, "PNG")
                    data, ext = buf.getvalue(), ".png"
                out = target(outdir, f"{stem}_page{number}_{k}{ext}")
                try:
                    out.write_bytes(data)
                except OSError:
                    saved.setdefault("skipped", []).append(out.name)
                    continue
                saved["images"].append(out)
    if attachments:
        files = []
        entries = getattr(reader, "attachment_list", None)
        if entries is not None:  # pypdf 5.4+: one entry per attached file, with its file name
            for entry in entries:
                try:
                    files.append((entry.alternative_name or entry.name, entry.content))
                except Exception:  # a damaged entry
                    continue
        else:
            for name, contents in (reader.attachments or {}).items():
                files.extend((name, data) for data in contents)
        for page in reader.pages:
            for ref in page.get("/Annots") or []:
                annot = ref.get_object()
                if not isinstance(annot, DictionaryObject):
                    continue
                if annot.get("/Subtype") != "/FileAttachment" or "/FS" not in annot:
                    continue
                spec = annot["/FS"].get_object()
                if not isinstance(spec, DictionaryObject):  # a bare file name: nothing embedded
                    continue
                try:
                    data = spec["/EF"].get_object()["/F"].get_object().get_data()
                except (KeyError, AttributeError, TypeError):
                    continue
                files.append((spec.get("/UF") or spec.get("/F") or "attachment", data))
        seen = set()  # the same file listed under two names is saved once
        for name, data in files:
            digest = hashlib.sha1(data).digest()
            if digest in seen:
                continue
            seen.add(digest)
            out = target(outdir / "attachments", _safe_filename(str(name), "attachment"))
            try:
                out.write_bytes(data)
            except OSError:  # a name the file system won't take: the rest is still saved
                saved.setdefault("skipped", []).append(out.name)
                continue
            saved["attachments"].append(out)
    return saved


HEADER_SPOTS = ("top_left", "top_center", "top_right", "bottom_left", "bottom_center",
                "bottom_right")


_HEADER_TOKEN = re.compile(r"\{(n|total|file|title|date|bates)\}")


def _upright(page):
    """(visual width, visual height, matrix) to draw on ``page`` as it's
    shown: inside its crop box, and after its /Rotate, so text comes out
    upright."""
    box = page.cropbox
    w, h, left, bottom = float(box.width), float(box.height), float(box.left), float(box.bottom)
    rotation = quarter_turn(page.rotation)
    matrix = {0: (1, 0, 0, 1, 0, 0), 90: (0, 1, -1, 0, w, 0),
              180: (-1, 0, 0, -1, w, h), 270: (0, -1, 1, 0, 0, h)}[rotation]
    a, b, c, d, e, f = matrix
    visual = (h, w) if rotation in (90, 270) else (w, h)
    return visual[0], visual[1], Transformation((a, b, c, d, e + left, f + bottom))


def headers_footers(path, output, texts, font_size=9, margin_mm=10, pages=None, start=1,
                    bates_prefix="", bates_start=1, bates_digits=6, date_format="%Y-%m-%d",
                    password=None):
    """Add headers and footers. ``texts`` maps spots (see HEADER_SPOTS, e.g.
    "top_right") to text, which may use {n} (page number, counting from
    ``start``), {total}, {file}, {title}, {date} and {bates} (Bates number:
    ``bates_prefix`` and a number from ``bates_start`` with ``bates_digits``
    digits, e.g. ABC000123). Returns the next Bates number, so numbering can
    continue in the next file."""
    import datetime

    texts = {k: v for k, v in (texts or {}).items() if v and v.strip()}
    unknown = set(texts) - set(HEADER_SPOTS)
    if unknown:
        raise PdfManagerError(f"unknown position {sorted(unknown)[0]!r}")
    if not texts:
        raise PdfManagerError("enter the text for at least one header or footer")
    reader = open_pdf(path, password)
    total = len(reader.pages)
    targets = set(parse_page_ranges(pages, total)) if pages else None
    title = (reader.metadata or {}).get("/Title") or ""
    writer = clone_document(reader)
    shared = _shared_objects(writer)
    margin = margin_mm * POINTS_PER_MM
    fields = {"total": total, "file": Path(path).stem, "title": str(title),
              "date": datetime.date.today().strftime(date_format)}
    n, bates = start, bates_start
    for i, page in enumerate(writer.pages):
        progress.report(i, total, "page")
        if targets is not None and i not in targets:
            continue
        _own_contents(page, shared)
        fields.update(n=n, bates=f"{bates_prefix}{bates:0{bates_digits}d}")
        # Only the known {names} are filled in; other braces stay as typed.
        lines = {spot: _HEADER_TOKEN.sub(lambda m: str(fields[m.group(1)]), text)
                 for spot, text in texts.items()}
        n += 1
        bates += 1
        w, h, matrix = _upright(page)

        def draw(c, w, h, lines=lines):
            for spot, text in lines.items():
                c.setFont(fonts.font_for(text), font_size)  # a font that has the letters
                vert, horiz = spot.split("_")
                y = h - margin - font_size * 0.8 if vert == "top" else margin
                if horiz == "center":
                    c.drawCentredString(w / 2, y, text)
                elif horiz == "right":
                    c.drawRightString(w - margin, y, text)
                else:
                    c.drawString(margin, y, text)

        page.merge_transformed_page(_overlay_page(w, h, draw), matrix)
    _write(writer, output)
    return bates


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

    if not images:
        raise PdfManagerError("no images given")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    c = pdf_canvas(str(output))
    for img_path in images:
        try:
            opened = Image.open(img_path)
        except (OSError, ValueError) as exc:  # not a picture, or a damaged one
            raise PdfManagerError(f"{img_path} is not a picture ({exc})") from None
        with opened as source:
            turn = _jpeg_turn(source)
            # A scanner's TIFF holds a page per frame. A camera's MPO also
            # has several, but they are other views of the same photo.
            frames = 1 if source.format == "MPO" else getattr(source, "n_frames", 1)
            for frame in range(frames):
                if frame:
                    source.seek(frame)
                # Pillow gives TIFF resolutions as fractions; the page size must be plain numbers.
                dpi_x, dpi_y = (float(v) if v and float(v) >= 1 else 72.0
                                for v in (source.info.get("dpi") or (72, 72))[:2])
                if turn is None:
                    source.load()
                    im = _page_image(source)
                    pixels = im.size
                else:  # a JPEG goes in as it is: no quality lost, and many times smaller
                    im = ImageReader(str(img_path))
                    pixels = source.size[::-1] if turn in (90, 270) else source.size
                    if turn in (90, 270):
                        dpi_x, dpi_y = dpi_y, dpi_x
                iw, ih = pixels[0] * 72 / dpi_x, pixels[1] * 72 / dpi_y
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
                if turn is None:
                    c.drawImage(ImageReader(im), x, y, width=dw, height=dh)
                else:
                    _draw_turned(c, im, x, y, dw, dh, turn)
                c.showPage()
    c.save()
    return output


# EXIF orientation -> degrees to turn the stored picture anticlockwise to
# show it upright (the mirrored orientations are left to Pillow).
_EXIF_TURNS = {1: 0, 3: 180, 6: 270, 8: 90}


def _jpeg_turn(source):
    """For a JPEG the PDF can show as it is (RGB or gray; Adobe CMYK
    JPEGs store their colours inverted), how far to turn it to be upright;
    None for any other picture."""
    if source.format != "JPEG" or source.mode not in ("RGB", "L"):
        return None
    return _EXIF_TURNS.get(source.getexif().get(0x0112, 1))


def _draw_turned(c, image, x, y, width, height, turn):
    """Draw ``image`` turned ``turn`` degrees anticlockwise to fill the
    ``width`` x ``height`` box at ``x``, ``y``."""
    c.saveState()
    c.translate(x + (width if turn in (90, 180) else 0), y + (height if turn in (180, 270) else 0))
    c.rotate(turn)
    sw, sh = (height, width) if turn in (90, 270) else (width, height)
    c.drawImage(image, 0, 0, width=sw, height=sh)
    c.restoreState()


def _page_image(im):
    """The picture as it should appear on the page: turned the way the
    camera recorded it, transparent parts on white, and 16-bit scans and
    palettes brought to 8 bits."""
    from PIL import Image, ImageOps

    im = ImageOps.exif_transpose(im) or im
    if im.mode.startswith("I"):  # 16-bit (or wider) grayscale: scale, don't clip
        im = im.convert("I").point(lambda v: v / 256).convert("L")
    elif im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:
        rgba = im.convert("RGBA")
        im = Image.new("RGB", rgba.size, "white")
        im.paste(rgba, mask=rgba.getchannel("A"))
    if im.mode not in ("RGB", "L", "CMYK"):
        im = im.convert("RGB")
    return im


def ocr(path, output, language="eng", force=False, deskew=False, password=None, engine="auto",
        rotate=True):
    """Add a searchable, selectable text layer to scanned pages.

    ``engine`` is "auto" (the built-in engine when it is installed, else
    Tesseract), "builtin" (PP-OCR through rapidocr; needs the ``ocr`` extra)
    or "tesseract" (OCRmyPDF and the Tesseract program; the ``tesseract``
    extra). Pages that already have text are left alone unless ``force`` is
    set, in which case they are rasterised and recognised again.
    ``language`` uses Tesseract's codes, joined with '+' for several (e.g.
    "eng+deu"); the built-in engine reads English, most European languages,
    Chinese and Japanese unasked, and fetches a reading model once for
    Russian, Korean, Arabic, Hindi and other scripts (see ocr.script_for).
    ``rotate`` turns sideways and upside-down pages upright."""
    from pdfmanager import ocr as _ocr  # it uses this module

    return _ocr.run(path, output, language=language, force=force, deskew=deskew,
                    password=password, engine=engine, rotate=rotate)


# pdfium is not thread-safe: every use of pypdfium2 must hold this lock
# (the GUI server renders thumbnails for several files at once).
PDFIUM_LOCK = threading.Lock()


def pdf_to_images(path, outdir, dpi=150, fmt="png", pages=None, password=None):
    """Render pages to image files with pypdfium2."""
    import pypdfium2 as pdfium

    outdir = Path(outdir)
    with PDFIUM_LOCK:
        open_pdf(path, password)  # a plain error for a damaged file or a wrong password
        try:
            doc = pdfium.PdfDocument(str(path), password=password)
        except pdfium.PdfiumError as exc:
            raise PdfManagerError(f"{path} can't be rendered ({exc})") from None
        try:
            total = len(doc)
            indices = parse_page_ranges(pages, total) if pages else range(total)
            outdir.mkdir(parents=True, exist_ok=True)
            width = len(str(total))
            written = []
            for n, i in enumerate(indices):
                progress.report(n, len(indices), "page")
                image = doc[i].render(scale=dpi / 72).to_pil()
                if fmt.lower() in ("jpg", "jpeg") and image.mode != "RGB":
                    image = image.convert("RGB")
                target = outdir / f"{Path(path).stem}_{i + 1:0{width}d}.{fmt}"
                image.save(target, dpi=(dpi, dpi))  # so the pictures keep their size
                written.append(target)
            return written
        finally:
            doc.close()
