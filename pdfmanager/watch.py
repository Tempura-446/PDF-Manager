"""Watch an inbox folder and process PDFs as they arrive."""

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
    remove_blank: bool = False    # drop blank pages
    auto_rotate: bool = False     # turn pages upright (needs Tesseract)
    ocr: bool = False
    ocr_language: str = "eng"
    recipe: list = None           # steps of a recipe to run (see recipes.py)
    skip_duplicates: bool = False
    keep_originals: bool = True   # move originals to <inbox>/originals instead of deleting

    def __post_init__(self):
        self.inbox, self.dest = Path(self.inbox), Path(self.dest)
        if self.by and self.by not in organize.GROUPINGS:
            raise core.PdfManagerError(
                f"unknown grouping {self.by!r}; use one of {', '.join(organize.GROUPINGS)}")


@dataclass
class _State:
    sizes: dict = field(default_factory=dict)   # path -> (size, mtime) at last poll
    failed: set = field(default_factory=set)


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
    if settings.skip_duplicates and known_hashes is not None:
        digest = organize.file_hash(path)
        if digest in known_hashes:
            _retire(path, settings)
            return None
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / path.name
        shutil.copy2(path, work)
        if settings.remove_blank or settings.auto_rotate:
            scan.clean(work, work, remove_blank=settings.remove_blank,
                       auto_rotate=settings.auto_rotate)
        if settings.ocr:
            core.ocr(work, work, language=settings.ocr_language)
        if settings.recipe:
            recipes.run(settings.recipe, work, work)
        if settings.compress:
            core.compress(work, work, level=settings.compress)
        record = organize.describe(work)
        record["path"] = path  # {stem} refers to the original name
        name = (organize.format_name(record, settings.rename) if settings.rename
                else path.stem) + ".pdf"
        folder = settings.dest / organize.group_name(record, settings.by) if settings.by \
            else settings.dest
        folder.mkdir(parents=True, exist_ok=True)
        target = organize._unique(folder / name, set())
        shutil.move(str(work), str(target))
    if known_hashes is not None:
        known_hashes.add(organize.file_hash(path))
    _retire(path, settings)
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
    for path in organize.find_pdfs(settings.inbox):
        if path in state.failed or not _ready(path, state):
            continue
        try:
            result = process_file(path, settings, known_hashes)
        except Exception as exc:  # keep watching even if one file is bad
            state.failed.add(path)
            log(f"failed: {path.name}: {exc}")
            continue
        state.sizes.pop(path, None)
        log(f"skipped duplicate: {path.name}" if result is None else f"{path.name} -> {result}")
        done.append((path, result))
    return done


def watch(settings, interval=5.0, log=print, stop=None):
    """Poll the inbox every ``interval`` seconds until interrupted (or until
    ``stop()`` returns true)."""
    settings.inbox.mkdir(parents=True, exist_ok=True)
    settings.dest.mkdir(parents=True, exist_ok=True)
    known = ({organize.file_hash(p) for p in organize.find_pdfs(settings.dest, recursive=True)}
             if settings.skip_duplicates else None)
    state = _State()
    log(f"watching {settings.inbox} (Ctrl+C to stop)")
    while not (stop and stop()):
        poll(settings, state, known, log)
        time.sleep(interval)
