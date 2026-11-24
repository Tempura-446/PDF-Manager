# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Core PDF I/O, document cloning, inspection, text extraction, and bounded batch execution."""

import io
import logging
import os
import tempfile
import threading
from pathlib import Path

from pypdf import PageObject, PdfReader, PdfWriter
from pypdf.errors import PyPdfError
from pypdf.generic import ArrayObject, ContentStream, DictionaryObject, IndirectObject, NameObject, NumberObject

logging.getLogger("pypdf.generic._link").addFilter(
    lambda record: "Annotation sizes differ" not in record.getMessage())

from pdfmanager import progress
from pdfmanager.errors import PdfManagerError
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


from pdfmanager.errors import PdfManagerError  # noqa: F401 - re-exported for callers


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def open_pdf(path, password=None):
    try:
        reader = PdfReader(str(path))
    except NotImplementedError:
        # Encrypted in a way a password doesn't open: for people's certificates.
        from pdfmanager import forpeople

        if forpeople.is_for_people(path):
            raise PdfManagerError(f"{path} {forpeople.NOT_READABLE}") from None
        raise PdfManagerError(f"{path} is protected in a way ZPDF can't open") from None
    except PyPdfError as exc:
        reader = _mended(path, password)
        if reader is None:
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


def open_pikepdf(path, password=None, **kwargs):
    """Open a PDF with pikepdf without triggering a UserWarning when a
    password is supplied for an unencrypted file."""
    import pikepdf

    try:
        return pikepdf.open(str(path), **kwargs)
    except pikepdf.PasswordError:
        if not password:
            raise
        return pikepdf.open(str(path), password=password, **kwargs)


def keep_size(pdf):
    """Before qpdf (pikepdf) saves a file whose trailer was lost and rebuilt:
    qpdf only fills in /Size when the trailer has one, and pypdf can't copy
    a file without it."""
    if "/Size" not in pdf.trailer:
        pdf.trailer.Size = 0  # (qpdf writes the right number)
    return pdf


def _mended(path, password):
    """A reader on a copy of ``path`` mended by qpdf (as Repair does), for a
    file pypdf can't open: one cut short, or whose cross-reference table is
    damaged or missing. Viewers open such files, so the tools do too. None
    when qpdf can't make sense of it either."""
    try:
        with open_pikepdf(path, password) as pdf:
            if not len(pdf.pages):
                return None
            buf = io.BytesIO()
            keep_size(pdf).save(buf)  # (decrypted: the tools protect their results again, see gui._protect)
        reader = PdfReader(buf)
        len(reader.pages)
        return reader
    except Exception:  # noqa: BLE001 (any failure means it can't be mended here)
        return None


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
    _recount(writer)
    _own_info(writer)
    for i, _, annots in detached:
        writer.pages[i][NameObject("/Annots")] = annots.clone(writer)
    return writer


def _own_info(writer):
    """Some files' /Info (the title, author...) is another object of the
    file: the page tree, the catalog or a page. Changing or clearing the
    document details would then change or remove that object: such a file
    gets a document-details dictionary of its own (empty: what was there
    weren't details)."""
    info = getattr(writer, "_info_obj", None)
    if info is None:
        return
    target = info.get_object()
    if not isinstance(target, DictionaryObject) or target.get("/Type") in ("/Pages", "/Page", "/Catalog") \
            or "/Kids" in target or "/Pages" in target or target is writer.root_object:
        writer._info_obj = writer._add_object(DictionaryObject())


def _recount(writer):
    """Make every /Count in the page tree say how many pages are under it: a
    wrong one (5 for one page, or -1) makes pdfium and pdf.js look for pages
    that aren't there. Only the tree pypdf found pages in is kept."""
    seen = set()

    def walk(node):
        node = node.get_object()
        if id(node) in seen or not isinstance(node, DictionaryObject):
            return 0
        seen.add(id(node))
        if node.get("/Type") != "/Pages" and "/Kids" not in node:
            return 1
        count = sum(walk(kid) for kid in node.get("/Kids") or [])
        node[NameObject("/Count")] = NumberObject(count)
        return count

    try:
        root = writer.root_object["/Pages"]
        walk(root)
    except Exception as _exc:  # an odd tree pypdf read pages from all the same: leave it as it is
        logging.getLogger("zpdf").debug("io._recount: %s", _exc)


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


# --------------------------------------------------------------------------
# Inspection
# --------------------------------------------------------------------------

def info(path, password=None):
    from pdfmanager.core.geometry import _describe_size, _page_size
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
# Bounded concurrent batch execution
# --------------------------------------------------------------------------

DEFAULT_MAX_WORKERS = 4


def default_workers(max_workers=None):
    """Bounded worker count for batch operations.

    When ``max_workers`` is None, caps concurrency at ``min(4, cpu_count)``
    so batch jobs stay responsive without exhausting memory or file
    descriptors. Explicit values are clamped to at least 1.
    """
    if max_workers is None:
        return max(1, min(DEFAULT_MAX_WORKERS, os.cpu_count() or 1))
    return max(1, int(max_workers))


def batch_run(items, fn, *, max_workers=None, on_progress=None, processes=False):
    """Run ``fn(item)`` over ``items`` with bounded concurrency and error isolation.

    Returns a list of result dicts in the same order as ``items``:
      - ``{"index": i, "item": item, "ok": True, "result": value}`` on success
      - ``{"index": i, "item": item, "ok": False, "error": str(exc), "exception": exc}`` on failure

    Key properties:
      - Caps in-flight work to at most ``workers * 2`` tasks so large batches
        do not allocate unbounded futures or buffers.
      - Checks ``progress.check()`` before submitting and as tasks finish;
        cancels queued tasks on ``progress.Cancelled``.
      - Propagates ``progress.Cancelled`` immediately while isolating all
        other per-item exceptions so one bad document does not abort the batch.
      - When ``processes=True``, executes ``fn`` in a spawned
        ``ProcessPoolExecutor`` (``fn`` and ``items`` must be picklable) to
        bypass the Python GIL on CPU-bound PDF workloads.
    """
    from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
    import contextvars
    import multiprocessing

    item_list = list(items)
    total = len(item_list)
    if total == 0:
        return []

    workers = default_workers(max_workers)
    results = [None] * total

    def _run_one(item):
        progress.check()
        return fn(item)

    if workers == 1 or total == 1:
        for i, item in enumerate(item_list):
            progress.check()
            try:
                value = _run_one(item)
                results[i] = {"index": i, "item": item, "ok": True, "result": value}
            except progress.Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001 (per-item error isolation)
                results[i] = {"index": i, "item": item, "ok": False,
                              "error": str(exc), "exception": exc}
            if on_progress is not None:
                on_progress(i + 1, total)
        return results

    def _submit(executor, item):
        if processes:
            return executor.submit(fn, item)
        ctx = contextvars.copy_context()
        return executor.submit(ctx.run, _run_one, item)

    max_in_flight = workers * 2
    next_index = 0
    done_count = 0
    in_flight = {}

    if processes:
        executor = ProcessPoolExecutor(
            max_workers=workers,
            mp_context=multiprocessing.get_context("spawn"),
        )
    else:
        executor = ThreadPoolExecutor(max_workers=workers)
    try:
        while next_index < total and len(in_flight) < max_in_flight:
            progress.check()
            fut = _submit(executor, item_list[next_index])
            in_flight[fut] = next_index
            next_index += 1

        while in_flight:
            done_futs, _ = wait(in_flight.keys(), return_when=FIRST_COMPLETED)
            for fut in done_futs:
                idx = in_flight.pop(fut)
                item = item_list[idx]
                try:
                    value = fut.result()
                    results[idx] = {"index": idx, "item": item, "ok": True, "result": value}
                except progress.Cancelled:
                    for pending in in_flight:
                        pending.cancel()
                    raise
                except Exception as exc:  # noqa: BLE001 (per-item error isolation)
                    results[idx] = {"index": idx, "item": item, "ok": False,
                                    "error": str(exc), "exception": exc}
                done_count += 1
                if on_progress is not None:
                    on_progress(done_count, total)

            while next_index < total and len(in_flight) < max_in_flight:
                progress.check()
                fut = _submit(executor, item_list[next_index])
                in_flight[fut] = next_index
                next_index += 1
    except progress.Cancelled:
        for pending in in_flight:
            pending.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)
    return results

