# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Watch an inbox folder and process PDFs as they arrive.

``watch()`` polls until stopped; ``once()`` makes a single pass (for a
scheduled task): it looks at the inbox, waits ``settle`` seconds so a file
still being written is seen to change, looks again and processes what held
still, then returns the poll state. The app's watch folders
(watchfolders.py) call ``poll()`` themselves, with a ``ProcessedFiles``
memory so a file is done once, also across restarts.

A file is taken once its size and time are the same at two looks; a browser
download still under way (``x.pdf`` beside ``x.pdf.crdownload``, ``.part``
and the like), an Office lock file (``~$x.pdf``) and a hidden file are left
alone.

With ``images`` the pictures a scanner saves (JPEG, PNG, TIFF with one or
many pages, BMP) are taken too: each becomes a PDF first, a page per
picture at the scan's own size. With ``batch`` (seconds), files that
arrive within that time of each other are one document: a scanner that
saves a file per page gives one PDF per stack of paper, in the order the
files are named. A batch is done once nothing new has come for that long.
"""

import dataclasses
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from pdfmanager import core, organize, recipes, scan


@dataclass
class WatchSettings:
    inbox: Path
    dest: Path
    by: str = None                # organize into sub-folders (see organize.GROUPINGS)
    rename: str = None            # rename pattern (see organize.format_name)
    compress: str = None          # compression level
    sharp_scans: bool = True      # when compressing, keep scanned text sharp (see core.compress)
    remove_blank: bool = False    # drop blank pages
    auto_rotate: bool = False     # turn pages upright (built-in OCR engine or Tesseract)
    ocr: bool = False
    ocr_language: str = "eng"     # Tesseract codes (see core.ocr)
    ocr_engine: str = "auto"      # auto, builtin or tesseract (see core.ocr)
    recipe: list = None           # steps of a recipe to run (see recipes.py)
    skip_duplicates: bool = False
    keep_originals: bool = True   # move originals to <inbox>/originals instead of deleting
    # What happens to an original once it's done: "move" (to <inbox>/<originals_folder>),
    # "delete" or "keep" (left where it is). None: from keep_originals.
    originals: str = None
    originals_folder: str = "originals"
    # Added to the result's name. With one, results can go into the inbox
    # itself (next to the originals): names ending in it aren't taken.
    suffix: str = ""
    images: bool = False          # take pictures too (what a scanner saves): each becomes a PDF first
    batch: float = 0              # seconds: files arriving within this of each other are one document

    def __post_init__(self):
        self.inbox, self.dest = Path(self.inbox), Path(self.dest)
        if self.originals is None:
            self.originals = "move" if self.keep_originals else "delete"
        if self.originals not in ("move", "delete", "keep"):
            raise core.PdfManagerError(f"unknown originals setting {self.originals!r}")
        if self.dest.resolve() == self.inbox.resolve() and not self.suffix:
            # The output would land back in the inbox and be processed again.
            raise core.PdfManagerError("the destination must be a different folder "
                                       "from the inbox")
        if self.by and self.by not in organize.GROUPINGS:
            raise core.PdfManagerError(
                f"unknown grouping {self.by!r}; use one of {', '.join(organize.GROUPINGS)}")


@dataclass
class _State:
    sizes: dict = field(default_factory=dict)   # path -> (size, mtime) at last poll
    failed: set = field(default_factory=set)    # (path, size, mtime) of files that failed
    busy: dict = field(default_factory=dict)    # path -> when it was first found open in another program
    told: set = field(default_factory=set)      # paths already reported as open in another program


IN_USE_NOTE = 60  # seconds a file may stay open in another program before that is said


class FileInUse(core.PdfManagerError):
    """A file another program holds open (Windows: a sharing violation), so
    it can't be read or moved yet. Not a failure: it is tried again."""


def _in_use(exc):
    """Whether ``exc`` means the file is open in another program (or can't
    be read just now) rather than that it is bad."""
    if not isinstance(exc, PermissionError):
        return False
    code = getattr(exc, "winerror", None)  # (None on Windows too when no Windows error came with it)
    return code is None or code in (5, 32, 33)


# Beside a download that isn't finished yet (Chrome, Edge, Firefox, Safari, others).
PARTIAL = (".crdownload", ".part", ".partial", ".download", ".tmp", ".opdownload")


IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")


def candidates(settings):
    """The files at the top level of the inbox that could be taken: PDFs,
    and with ``images`` the pictures a scanner saves."""
    found = organize.find_pdfs(settings.inbox)
    if settings.images:
        found += [p for p in settings.inbox.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_TYPES]
    return sorted(found)


def _picture_pages(path):
    """[(image, dpi)] for a picture file: every page of a TIFF; the dpi it
    was saved with, when that's a scanner's (else the page is fitted to
    paper)."""
    from PIL import Image, ImageSequence

    pages = []
    try:
        with Image.open(path) as im:
            for frame in ImageSequence.Iterator(im):
                dpi = frame.info.get("dpi") or im.info.get("dpi") or (0, 0)
                try:
                    dpi = float(dpi[0])
                except (TypeError, ValueError, IndexError):
                    dpi = 0
                page = frame.convert("RGB" if frame.mode not in ("1", "L", "RGB") else frame.mode)
                pages.append((page.copy(), round(dpi) if 50 <= dpi <= 1200 else None))
    except PermissionError:
        raise
    except Exception as exc:  # noqa: BLE001 - whatever Pillow says of a file that isn't a picture
        raise core.PdfManagerError(f"{path.name} couldn't be read as a picture ({type(exc).__name__})") from None
    if not pages:
        raise core.PdfManagerError(f"{path.name} has no pages")
    return pages


def _gather(paths, work):
    """One PDF at ``work`` from the files of a document, in order: pictures
    a page each, PDFs as they are."""
    from pdfmanager import scanpdf

    if len(paths) == 1 and paths[0].suffix.lower() == ".pdf":
        shutil.copy2(paths[0], work)
        return
    parts = []
    for n, path in enumerate(paths):
        if path.suffix.lower() == ".pdf":
            parts.append(path)
            continue
        part = work.with_name(f"part{n}.pdf")
        scanpdf._write_pdf(_picture_pages(path), part, "auto")
        parts.append(part)
    if len(parts) == 1:
        shutil.move(str(parts[0]), str(work))
    else:
        core.merge(parts, work, bookmarks=False)


def _skipped(path, settings):
    """Files never taken: hidden ones, Office lock files, a download still
    under way, and (with results next to the originals) results."""
    name = path.name
    if name.startswith((".", "~$")):
        return True
    if any(path.with_name(name + ext).exists() for ext in PARTIAL):
        return True
    return bool(settings.suffix) and path.parent.resolve() == settings.dest.resolve() \
        and path.stem.endswith(settings.suffix.rstrip())


class ProcessedFiles:
    """Which files of a folder were done (or failed), kept in a JSON file so
    a file is processed once, also after a restart. A file is known by its
    name, size and time: one replaced or changed since gets another go."""

    def __init__(self, path):
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.items = {k: v for k, v in data.items() if isinstance(v, list) and len(v) == 3}
        except (OSError, ValueError, AttributeError):
            self.items = {}

    @staticmethod
    def _signature(path):
        stat = path.stat()
        return [stat.st_size, stat.st_mtime_ns]

    def knows(self, path):
        try:
            item = self.items.get(path.name)
            return item is not None and item[:2] == self._signature(path)
        except OSError:
            return False

    def outcome(self, path):
        return self.items.get(path.name, [None, None, None])[2] if self.knows(path) else None

    def remember(self, path, outcome):
        try:
            self.items[path.name] = [*self._signature(path), outcome]
        except OSError:
            return  # gone already (moved to Originals): nothing to remember
        self._save(path.parent)

    def _save(self, folder):
        # Names no longer in the folder are forgotten (moved away, deleted).
        try:
            present = {p.name for p in Path(folder).iterdir()}
        except OSError:
            present = set(self.items)
        self.items = {k: v for k, v in self.items.items() if k in present}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.items), encoding="utf-8")
        os.replace(tmp, self.path)


def _ready(path, state):
    """A file is ready once its size and mtime are unchanged between two
    polls, so we don't grab a file that is still being copied in."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False
    signature = (stat.st_size, stat.st_mtime)
    previous = state.sizes.get(path)
    state.sizes[path] = signature
    return previous == signature and stat.st_size > 0


def process_file(path, settings, known_hashes=None):
    """Run the configured steps on one file. Returns the final path, or None
    if it was skipped as a duplicate."""
    return process_files([Path(path)], settings, known_hashes)


def process_files(paths, settings, known_hashes=None):
    """Run the configured steps on the files of one document (one file, or
    a batch that becomes one PDF, named after the first). Returns the final
    path, or None if it was skipped as a duplicate."""
    paths = [Path(p) for p in paths]
    path = paths[0]
    single_pdf = len(paths) == 1 and path.suffix.lower() == ".pdf"
    digest = organize.file_hash(path) if known_hashes is not None and single_pdf else None
    if settings.skip_duplicates and digest is not None and digest in known_hashes:
        _retire(path, settings)
        return None
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / (path.stem + ".pdf")
        _gather(paths, work)
        if settings.remove_blank or settings.auto_rotate:
            scan.clean(work, work, remove_blank=settings.remove_blank,
                       auto_rotate=settings.auto_rotate)
        if settings.ocr:
            core.ocr(work, work, language=settings.ocr_language, engine=settings.ocr_engine)
        if settings.recipe:
            recipes.run(settings.recipe, work, work)
        if settings.compress:
            core.compress(work, work, level=settings.compress, sharp_scans=settings.sharp_scans)
        record = organize.describe(work)
        if record["error"] and not record["locked"]:
            raise core.PdfManagerError(f"can't be read: {record['error']}")
        record["path"] = path  # {stem} refers to the original name
        # The date comes from the original: the processed copy was written just now.
        if single_pdf:
            record["date"] = organize.describe(path)["date"]
        name = (organize.format_name(record, settings.rename) if settings.rename
                else path.stem) + settings.suffix + ".pdf"
        folder = settings.dest / organize.group_name(record, settings.by) if settings.by \
            else settings.dest
        folder.mkdir(parents=True, exist_ok=True)
        target = organize._unique(folder / name, set())
        shutil.move(str(work), str(target))
    try:
        for each in paths:
            _retire(each, settings)
    except OSError:
        # The original is still in the inbox (locked, perhaps) and will be
        # picked up again, so don't leave a copy behind to be duplicated.
        target.unlink(missing_ok=True)
        raise
    if digest is not None:
        known_hashes.add(digest)
    return target


def _retire(path, settings):
    if settings.originals == "keep":
        return
    if settings.originals == "move":
        originals = settings.inbox / settings.originals_folder
        originals.mkdir(exist_ok=True)
        shutil.move(str(path), str(organize._unique(originals / path.name, set())))
    else:
        path.unlink()


def _documents(pending, settings, state, now):
    """The files to take now, as documents (lists of files). Without
    ``batch`` each ready file is its own. With it, files whose times are
    within ``batch`` seconds of the next one's belong together, and a
    document is taken only once nothing new has come for that long (and
    nothing in the folder is still being written)."""
    if not settings.batch:
        return [[p] for p in pending if _ready(p, state)]
    ready = [_ready(p, state) for p in pending]   # (every file looked at: each needs two looks)
    if not all(ready):
        return []
    ordered = sorted(pending, key=lambda p: (state.sizes[p][1], p.name))
    groups = []
    for path in ordered:
        if groups and state.sizes[path][1] - state.sizes[groups[-1][-1]][1] <= settings.batch:
            groups[-1].append(path)
        else:
            groups.append([path])
    closed = [g for g in groups if now - state.sizes[g[-1]][1] >= settings.batch]
    return [sorted(g, key=lambda p: p.name) for g in closed]   # (pages in the order they're named)


def poll(settings, state, known_hashes=None, log=print, memory=None, report=None, stop=None):
    """Process every ready file at the top level of the inbox once (PDFs,
    and pictures with ``settings.images``; a batch as one document with
    ``settings.batch``). ``memory``: a ProcessedFiles, to skip files done
    before and remember those done now. ``report(path, result, error)`` is
    told of each document done or failed (by its first file). ``stop()``:
    checked between documents; true stops the pass."""
    done = []
    pending = [p for p in candidates(settings)
               if not _skipped(p, settings) and not (memory is not None and memory.knows(p))]
    # A failure is remembered for that version of the file: a replaced or
    # rewritten file gets another go.
    documents = [d for d in _documents(pending, settings, state, time.time())
                 if not any((p, *state.sizes[p]) in state.failed for p in d)]
    for paths in documents:
        path = paths[0]
        said = path.name if len(paths) == 1 else f"{path.name} and {len(paths) - 1} more"
        if stop and stop():
            break
        try:
            result = process_files(paths, settings, known_hashes)
        except PermissionError as exc:
            if not _in_use(exc):
                raise
            # Open in another program: like a download still under way, it
            # waits and is tried again, and after a minute that is said once.
            since = state.busy.setdefault(path, time.monotonic())
            if time.monotonic() - since >= IN_USE_NOTE and path not in state.told:
                state.told.add(path)
                log(f"in use: {said}")
                if report:
                    report(path, None, FileInUse(f"{said} is open in another program; "
                                                 "ZPDF will do it once it's closed"))
            continue
        except Exception as exc:  # keep watching even if one file is bad
            for each in paths:
                state.failed.add((each, *state.sizes[each]))
                if memory is not None:
                    memory.remember(each, "failed")
            log(f"failed: {said}: {exc}")
            if report:
                report(path, None, exc)
            continue
        state.busy.pop(path, None)
        state.told.discard(path)
        log(f"skipped duplicate: {said}" if result is None else f"{said} -> {result}")
        for each in paths:
            state.sizes.pop(each, None)
            if memory is not None and each.exists():  # (kept where it was)
                memory.remember(each, "done")
        if memory is not None and result is not None and result.parent.resolve() == settings.inbox.resolve():
            memory.remember(result, "result")
        if report:
            report(path, result, None)
        done.append((path, result))
    # Forget failures of files that are gone or have changed since.
    state.failed = {f for f in state.failed if state.sizes.get(f[0]) == f[1:]}
    return done


def _known(settings):
    """Hashes of what is already in the destination, for --skip-duplicates."""
    return {organize.file_hash(p) for p in organize.find_pdfs(settings.dest, recursive=True)}


def watch(settings, interval=5.0, log=print, stop=None):
    """Poll the inbox every ``interval`` seconds until interrupted (or until
    ``stop()`` returns true)."""
    settings.inbox.mkdir(parents=True, exist_ok=True)
    settings.dest.mkdir(parents=True, exist_ok=True)
    known = _known(settings) if settings.skip_duplicates else None
    state = _State()
    log(f"watching {settings.inbox} (Ctrl+C to stop)")
    trouble = None
    while not (stop and stop()):
        try:
            poll(settings, state, known, log)
        except (core.PdfManagerError, OSError) as exc:
            # The inbox went away (drive unplugged, share dropped); wait for it.
            if str(exc) != trouble:
                trouble = str(exc)
                log(f"waiting: {exc}")
        else:
            if trouble:
                log(f"watching {settings.inbox} again")
                trouble = None
        time.sleep(interval)


def once(settings, skip_duplicates=False, settle=2.0, log=print):
    """Process what is in the inbox now and return the poll state. Files are
    looked at twice, ``settle`` seconds apart, so one still being written
    is left for the next run. ``skip_duplicates`` (or the same setting in
    ``settings``) leaves out files already in the destination."""
    if skip_duplicates:
        settings = dataclasses.replace(settings, skip_duplicates=True)
    settings.inbox.mkdir(parents=True, exist_ok=True)
    settings.dest.mkdir(parents=True, exist_ok=True)
    known = _known(settings) if settings.skip_duplicates else None
    state = _State()
    poll(settings, state, known, log)  # only records sizes
    if state.sizes:  # something is there: give a file still being written time to change
        time.sleep(settle)
    poll(settings, state, known, log)
    return state
