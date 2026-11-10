"""Watch an inbox folder and process PDFs as they arrive.

``watch()`` polls until stopped; ``once()`` makes a single pass (for a
scheduled task): it looks at the inbox, waits ``settle`` seconds so a file
still being written is seen to change, looks again and processes what held
still, then returns the poll state.
"""

import dataclasses
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

    def __post_init__(self):
        self.inbox, self.dest = Path(self.inbox), Path(self.dest)
        if self.dest.resolve() == self.inbox.resolve():
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
    """Run the configured steps on one PDF. Returns the final path, or None
    if it was skipped as a duplicate."""
    path = Path(path)
    digest = organize.file_hash(path) if known_hashes is not None else None
    if settings.skip_duplicates and digest is not None and digest in known_hashes:
        _retire(path, settings)
        return None
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / path.name
        shutil.copy2(path, work)
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
        record["date"] = organize.describe(path)["date"]
        name = (organize.format_name(record, settings.rename) if settings.rename
                else path.stem) + ".pdf"
        folder = settings.dest / organize.group_name(record, settings.by) if settings.by \
            else settings.dest
        folder.mkdir(parents=True, exist_ok=True)
        target = organize._unique(folder / name, set())
        shutil.move(str(work), str(target))
    try:
        _retire(path, settings)
    except OSError:
        # The original is still in the inbox (locked, perhaps) and will be
        # picked up again, so don't leave a copy behind to be duplicated.
        target.unlink(missing_ok=True)
        raise
    if digest is not None:
        known_hashes.add(digest)
    return target


def _retire(path, settings):
    if settings.keep_originals:
        originals = settings.inbox / "originals"
        originals.mkdir(exist_ok=True)
        shutil.move(str(path), str(organize._unique(originals / path.name, set())))
    else:
        path.unlink()


def poll(settings, state, known_hashes=None, log=print):
    """Process every ready PDF at the top level of the inbox once."""
    done = []
    present = organize.find_pdfs(settings.inbox)
    for path in present:
        if not _ready(path, state):
            continue
        # A failure is remembered for that version of the file: a replaced
        # or rewritten file gets another go.
        if (path, *state.sizes[path]) in state.failed:
            continue
        try:
            result = process_file(path, settings, known_hashes)
        except Exception as exc:  # keep watching even if one file is bad
            state.failed.add((path, *state.sizes[path]))
            log(f"failed: {path.name}: {exc}")
            continue
        state.sizes.pop(path, None)
        log(f"skipped duplicate: {path.name}" if result is None else f"{path.name} -> {result}")
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
