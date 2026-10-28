"""Sort, rename and catalogue collections of PDF files."""

import csv
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
    record = {"path": path, "name": path.name, "bytes": path.stat().st_size,
              "pages": None, "title": None, "author": None, "date": None, "error": None}
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            # Many "encrypted" PDFs only have an owner password.
            reader.decrypt("")
        meta = reader.metadata or {}
        record["pages"] = len(reader.pages)
        record["title"] = _meaningful(meta.get("/Title"))
        record["author"] = _meaningful(meta.get("/Author"))
        record["date"] = _pdf_date(meta.get("/CreationDate")) or _pdf_date(meta.get("/ModDate"))
    except Exception as exc:  # unreadable/corrupt/password-protected
        record["error"] = str(exc) or exc.__class__.__name__
    if record["date"] is None:
        record["date"] = datetime.fromtimestamp(path.stat().st_mtime)
    return record


def _safe(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")
    return re.sub(r"\s+", " ", name)[:120] or "Untitled"


def group_name(record, by):
    if record["error"] and by in ("author", "pages", "title-letter"):
        return "Unreadable"
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


def _unique(target, taken):
    """Return ``target`` or "name (2).pdf", "name (3).pdf", ... so that it
    neither exists on disk nor is already in ``taken``; then reserve it."""
    candidate, i = target, 2
    while candidate.exists() or candidate in taken:
        candidate = target.with_name(f"{target.stem} ({i}){target.suffix}")
        i += 1
    taken.add(candidate)
    return candidate


def organize(directory, by="year", dest=None, move=False, recursive=False, dry_run=False):
    """Copy (or move) PDFs from ``directory`` into sub-folders of ``dest``
    grouped by ``by``. Returns a list of (source, destination) pairs."""
    directory = Path(directory)
    dest = Path(dest) if dest else directory
    plan = []
    planned = set()
    for path in find_pdfs(directory, recursive):
        if dest in path.parents and path.parent != directory:
            continue  # already organized by a previous run
        folder = dest / group_name(describe(path), by)
        target = folder / path.name
        if target.resolve() == path.resolve():
            continue
        plan.append((path, _unique(target, planned)))

    if not dry_run:
        for src, dst in plan:
            dst.parent.mkdir(parents=True, exist_ok=True)
            (shutil.move if move else shutil.copy2)(str(src), str(dst))
    return plan


def rename(directory, pattern="{date:%Y-%m-%d} {title}", recursive=False, dry_run=False):
    """Rename PDFs using their metadata. ``pattern`` may use {title},
    {author}, {pages}, {date:...} and {stem} (the current name)."""
    plan = []
    planned = set()
    for path in find_pdfs(directory, recursive):
        rec = describe(path)
        fields = {
            "title": rec["title"] or path.stem,
            "author": rec["author"] or "Unknown",
            "pages": rec["pages"] if rec["pages"] is not None else 0,
            "date": rec["date"],
            "stem": path.stem,
        }
        try:
            new_stem = _safe(pattern.format(**fields))
        except (KeyError, ValueError) as exc:
            raise PdfManagerError(f"bad rename pattern {pattern!r}: {exc}") from None
        target = path.with_name(new_stem + ".pdf")
        if target == path:
            continue
        plan.append((path, _unique(target, planned)))
    if not dry_run:
        for src, dst in plan:
            src.rename(dst)
    return plan


def catalog(directory, output, recursive=False):
    """Write a CSV inventory of the PDFs in ``directory``."""
    output = Path(output)
    rows = [describe(p) for p in find_pdfs(directory, recursive)]
    with open(output, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["file", "pages", "size_bytes", "title", "author", "date", "error"])
        for r in rows:
            writer.writerow([r["path"], r["pages"], r["bytes"], r["title"] or "",
                             r["author"] or "", f"{r['date']:%Y-%m-%d}", r["error"] or ""])
    return output, len(rows)
