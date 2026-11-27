# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Combining, splitting, bookmark/size chunking, page selection, deletion, reverse, and interleave."""

import io
import os
import re
import sys
from pathlib import Path

from pypdf import PdfWriter

from pdfmanager import progress
from pdfmanager.errors import PdfManagerError
from pdfmanager.pageranges import parse_page_ranges
from pdfmanager.core.security import _Bytes
from pdfmanager.core.io import (
    _reorder_pages, _write, _writer_from_pages, clone_document, open_pdf,
)

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

    if prefix:
        prefix = _title_filename(str(prefix).replace("/", "_").replace("\\", "_").replace("..", "_"))

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

    prefix = prefix or _title_filename(Path(path).stem) or "page"
    width = len(str(max(1, total)))

    def label(g):
        return f"{g[0] + 1:0{width}d}" if len(g) == 1 else f"{g[0] + 1:0{width}d}-{g[-1] + 1:0{width}d}"

    if max_size is not None:
        if total == 0:
            raise PdfManagerError("PDF has no pages to split")
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


def outline_items(reader):
    """The bookmarks of an open document: [{"title", "page" (1-based),
    "level" (0 = top)}] (outline.get_outline opens the file for it)."""
    items = []

    def walk(entries, level):
        for entry in entries:
            if isinstance(entry, list):
                walk(entry, level + 1)
                continue
            try:
                page = reader.get_destination_page_number(entry)
            except Exception:
                page = None
            if page is None or page < 0:
                continue  # points outside the document (e.g. a web link)
            items.append({"title": str(entry.title or ""), "page": page + 1, "level": level})

    walk(reader.outline, 0)
    return items


def _bookmark_groups(path, total, level, password=None):
    """[(bookmark title, 0-based pages)] for splitting at the bookmarks of
    ``level`` or above (1 = top level). The pages before the first bookmark
    come first, with the title None."""
    if level not in (1, 2):
        raise PdfManagerError("the bookmark level must be 1 (top level) or 2")
    starts = {}  # first page -> title; of several bookmarks on one page, the first
    for item in sorted(outline_items(open_pdf(path, password)), key=lambda i: i["page"]):
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
        sizes = {}  # pages tried -> file size
        best_blob = None
        fits, too_big = 0, left + 1  # most pages known to fit; fewest known not to
        tries = min(left, max(1, int(limit * 0.97 / per_page)))
        while True:
            blob = getattr(sys.modules.get("pdfmanager.core"), "_pages_bytes", _pages_bytes)(reader, range(start, start + tries))
            sizes[tries] = len(blob)
            if len(blob) <= limit:
                fits, best_blob = tries, blob
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
            yield list(range(start, start + fits)), best_blob
            per_page = max(1.0, sizes[fits] / fits)
            start += fits
        else:  # one page is larger than the limit by itself
            blob = getattr(sys.modules.get("pdfmanager.core"), "_pages_bytes", _pages_bytes)(reader, [start])
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
