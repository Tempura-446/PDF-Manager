"""Sort, rename and catalogue collections of PDF files."""

import csv
import hashlib
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

from pdfmanager.core import PdfManagerError

GROUPINGS = ("year", "month", "author", "pages", "size", "title-letter")


def find_pdfs(directory, recursive=False):
    directory = Path(directory)
    if not directory.is_dir():
        raise PdfManagerError(f"{directory} is not a directory")
    pattern = "**/*" if recursive else "*"
    return sorted(p for p in directory.glob(pattern) if p.is_file() and p.suffix.lower() == ".pdf")


def _pdf_date(value):
    """Parse a PDF date string like D:20240131120000+01'00'."""
    if not value:
        return None
    match = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?", str(value))
    if not match:
        return None
    year, month, day = match.group(1), match.group(2) or "01", match.group(3) or "01"
    try:
        return datetime(int(year), int(month), int(day))
    except ValueError:
        return None


# Placeholder values some PDF producers write instead of leaving fields empty.
_PLACEHOLDERS = {"untitled", "anonymous", "unknown", "unspecified"}


def _meaningful(value):
    value = str(value or "").strip()
    return value if value and value.lower() not in _PLACEHOLDERS else None


def describe(path):
    """Collect the metadata used for grouping and renaming."""
    path = Path(path)
    record = {"path": path, "name": path.name, "bytes": path.stat().st_size, "pages": None,
              "title": None, "author": None, "date": None, "error": None, "locked": False}
    try:
        reader = PdfReader(str(path))
        # Many "encrypted" PDFs only have an owner password and open with "".
        if reader.is_encrypted and not reader.decrypt(""):
            record.update(error="password protected", locked=True)
        else:
            meta = reader.metadata or {}
            record["pages"] = len(reader.pages)
            record["title"] = _meaningful(meta.get("/Title"))
            record["author"] = _meaningful(meta.get("/Author"))
            record["date"] = (_pdf_date(meta.get("/CreationDate"))
                              or _pdf_date(meta.get("/ModDate")))
    except Exception as exc:  # unreadable/corrupt
        record["error"] = str(exc) or exc.__class__.__name__
    if record["date"] is None:
        record["date"] = datetime.fromtimestamp(path.stat().st_mtime)
    return record


# Names Windows treats as devices whatever the extension (CON.pdf included).
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?=\.|$)", re.IGNORECASE)


def _safe(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")
    name = _RESERVED.sub(r"\1_", name)
    return re.sub(r"\s+", " ", name)[:120] or "Untitled"


def group_name(record, by):
    if record["error"] and by in ("author", "pages", "title-letter"):
        return "Password protected" if record.get("locked") else "Unreadable"
    if by == "year":
        return f"{record['date']:%Y}"
    if by == "month":
        return f"{record['date']:%Y-%m}"
    if by == "author":
        return _safe(record["author"] or "Unknown author")
    if by == "pages":
        n = record["pages"]
        if n <= 1:
            return "1 page"
        low = 2
        for high in (5, 10, 25, 50, 100, 250):
            if n <= high:
                return f"{low}-{high} pages"
            low = high + 1
        return "over 250 pages"
    if by == "size":
        mb = record["bytes"] / (1024 * 1024)
        if mb < 1:
            return "under 1 MB"
        if mb < 10:
            return "1-10 MB"
        if mb < 50:
            return "10-50 MB"
        return "over 50 MB"
    if by == "title-letter":
        first = (record["title"] or record["path"].stem).strip()[:1].upper()
        return first if first.isalpha() else "#"
    raise PdfManagerError(f"unknown grouping {by!r}; use one of {', '.join(GROUPINGS)}")


def _same_file(a, b):
    try:
        return b is not None and a.samefile(b)
    except OSError:
        return False


def _unique(target, taken, source=None):
    """Return ``target`` or "name (2).pdf", "name (3).pdf", ... so that it
    neither exists on disk nor is already in ``taken``; then reserve it.
    ``taken`` is compared the way the file system compares names, so on
    Windows two names differing only in case count as one. A candidate that
    is ``source`` itself (the file being renamed) is free to use."""
    candidate, i = target, 2
    while ((candidate.exists() and not _same_file(candidate, source))
           or os.path.normcase(str(candidate)) in taken):
        candidate = target.with_name(f"{target.stem} ({i}){target.suffix}")
        i += 1
    taken.add(os.path.normcase(str(candidate)))
    return candidate


DUPLICATE_MODES = ("keep", "skip", "folder")


def file_hash(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def find_duplicates(directory, recursive=False):
    """Return groups (lists of paths) of byte-identical PDFs, each group
    sorted so the oldest file comes first."""
    by_size = {}
    for path in find_pdfs(directory, recursive):
        if path.is_symlink():
            continue  # a link is not a copy; deleting its target would lose the data
        try:
            by_size.setdefault(path.stat().st_size, []).append(path)
        except FileNotFoundError:
            pass  # removed while we were listing
    groups = {}
    for paths in by_size.values():
        if len(paths) < 2:
            continue  # a file with a unique size can't have a duplicate
        for path in paths:
            try:
                groups.setdefault(file_hash(path), []).append(path)
            except FileNotFoundError:
                pass
    return [sorted(g, key=lambda p: (p.stat().st_mtime, str(p)))
            for g in groups.values() if len(g) > 1]


def organize(directory, by="year", dest=None, move=False, recursive=False, dry_run=False,
             duplicates="keep"):
    """Copy (or move) PDFs from ``directory`` into sub-folders of ``dest``
    grouped by ``by``. Returns a list of (source, destination) pairs; a
    destination of ``None`` means the file was skipped as a duplicate.

    ``duplicates`` decides what happens to a file identical to one already
    in ``dest`` or earlier in this run: "keep" (organize it anyway),
    "skip" (leave it where it is) or "folder" (put it in a "Duplicates"
    sub-folder for review)."""
    if duplicates not in DUPLICATE_MODES:
        raise PdfManagerError(f"duplicates must be one of {', '.join(DUPLICATE_MODES)}")
    directory = Path(directory)
    dest = Path(dest) if dest else directory
    if dest == directory:
        # Organizing in place: files already in their group folder are
        # recognised below, so only the review folder is left alone.
        sources = [p for p in find_pdfs(directory, recursive) if p.parent != dest / "Duplicates"]
    else:
        sources = [p for p in find_pdfs(directory, recursive)
                   if not (dest in p.parents and p.parent != directory)]  # organized earlier

    seen = set()
    if duplicates != "keep":
        if dest.is_dir() and dest != directory:
            seen = {file_hash(p) for p in find_pdfs(dest, recursive=True)}
        elif dest == directory:
            seen = {file_hash(p) for p in find_pdfs(dest, recursive=True) if p not in sources}

    plan = []
    planned = set()
    for path in sources:
        folder = None
        try:
            if duplicates != "keep":
                digest = file_hash(path)
                if digest in seen:
                    if duplicates == "skip":
                        plan.append((path, None))
                        continue
                    folder = dest / "Duplicates"
                seen.add(digest)
            if folder is None:
                folder = dest / group_name(describe(path), by)
        except FileNotFoundError:
            continue  # removed while we were working; nothing to organize
        target = folder / path.name
        if target.resolve() == path.resolve():
            continue
        plan.append((path, _unique(target, planned)))

    if not dry_run:
        for src, dst in plan:
            if dst is None:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            (shutil.move if move else shutil.copy2)(str(src), str(dst))
    return plan


def format_name(record, pattern):
    """Build a file name (without .pdf) from a ``describe`` record. ``pattern``
    may use {title}, {author}, {pages}, {date:...} and {stem}."""
    path = record["path"]
    fields = {
        "title": record["title"] or path.stem,
        "author": record["author"] or "Unknown",
        "pages": record["pages"] if record["pages"] is not None else 0,
        "date": record["date"],
        "stem": path.stem,
    }
    try:
        return _safe(pattern.format(**fields))
    except (KeyError, ValueError, IndexError) as exc:
        raise PdfManagerError(f"bad rename pattern {pattern!r}: {exc}") from None


def rename(directory, pattern="{date:%Y-%m-%d} {title}", recursive=False, dry_run=False):
    """Rename PDFs using their metadata (see ``format_name``)."""
    plan = []
    planned = set()
    for path in find_pdfs(directory, recursive):
        try:
            target = path.with_name(format_name(describe(path), pattern) + ".pdf")
        except FileNotFoundError:
            continue  # removed while we were working
        # A file that already carries the name (or a "(2)" variant of it) stays
        # put; a change of letter case only is a plain rename of the file itself
        # (Windows paths compare equal regardless of case, hence the strings).
        target = _unique(target, planned, source=path)
        if str(target) == str(path):
            continue
        plan.append((path, target))
    if not dry_run:
        for src, dst in plan:
            src.rename(dst)
    return plan


def catalog(directory, output, recursive=False):
    """Write a CSV inventory of the PDFs in ``directory``."""
    output = Path(output)
    rows = [describe(p) for p in find_pdfs(directory, recursive)]
    # With a byte-order mark, so Excel reads accented names right.
    with open(output, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["file", "pages", "size_bytes", "title", "author", "date", "error"])
        for r in rows:
            writer.writerow([r["path"], r["pages"], r["bytes"], r["title"] or "",
                             r["author"] or "", f"{r['date']:%Y-%m-%d}", r["error"] or ""])
    return output, len(rows)
