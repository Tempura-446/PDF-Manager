"""Watch folders in the app (Settings > Watch folders): PDFs put in a folder
are processed while ZPDF is open, with a saved recipe or one tool with its
default options, using the same engine as ``pdfm watch`` (watch.py).

Each folder has
  do:        {"tool": "ocr" | "compress" | "pdfa"} or {"recipe": name}
  results:   "done" (a Done folder inside it) or "beside" (next to the
             original, the name ending in a suffix such as " (searchable)")
  originals: "keep" (left where they are) or "move" (to an Originals folder
             inside it)
  paused:    whether it's paused

The folders are kept in watch-folders.json in the app's data folder, and
which files of each were done in watch-processed/<id>.json, so a restart
doesn't process them again (a file replaced or changed since gets another
go). One background thread looks at the folders every few seconds; a file
done or failed is news the page shows as a toast (``news_after``).

Only one running copy of ZPDF watches: the thread holds watch.lock in the
data folder while it runs.
"""

import itertools
import json
import secrets
import sys
import threading
import time
from pathlib import Path

from pdfmanager import core, watch
from pdfmanager.appdata import data_dir, write_private
from pdfmanager.pageranges import PageRangeError

TOOLS = {"ocr": "Make searchable (OCR)", "compress": "Make smaller", "pdfa": "Archive (PDF/A)"}
SUFFIXES = {"ocr": " (searchable)", "compress": " (smaller)", "pdfa": " (PDF-A)", "recipe": " (done)"}
DONE_FOLDER, ORIGINALS_FOLDER = "Done", "Originals"
INTERVAL = 3.0   # seconds between looks (a file must hold still for one)
NEWS_KEPT = 50
# Errors whose message is told as it is (the tools' own; see gui.EXPECTED_ERRORS).
PLAIN_ERRORS = (core.PdfManagerError, PageRangeError, ValueError, FileNotFoundError, NotADirectoryError)
# A tool's options as its form starts out: gui.default_options, handed in by
# gui when it loads (this module doesn't import gui, which imports it).
default_options = None


def _config_file():
    return data_dir() / "watch-folders.json"


def _memory_file(folder_id):
    return data_dir() / "watch-processed" / f"{folder_id}.json"


def load():
    try:
        data = json.loads(_config_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    folders = data.get("folders", []) if isinstance(data, dict) else []
    return [f for f in folders if isinstance(f, dict) and f.get("id") and f.get("folder")]


def _save(folders):
    write_private(_config_file(), json.dumps({"folders": folders}, indent=2, ensure_ascii=False))


def describe(entry):
    """What a folder does, in words: "Make smaller → Done folder, originals kept"."""
    do = entry["do"]
    what = TOOLS.get(do.get("tool"), "") if "tool" in do else f"Recipe “{do.get('recipe')}”"
    where = f"{DONE_FOLDER} folder" if entry["results"] == "done" else \
        f"next to the original (“…{suffix(entry)}.pdf”)"
    originals = "originals kept" if entry["originals"] == "keep" else f"originals moved to {ORIGINALS_FOLDER}"
    return f"{what} → {where}, {originals}"


def suffix(entry):
    return SUFFIXES[entry["do"].get("tool", "recipe")]


def check(folder, do, results, originals, existing=()):
    """The entry for a new watch folder, or PdfManagerError saying what's wrong."""
    from pdfmanager import recipes

    folder = Path(str(folder or "").strip().strip('"')).expanduser()
    if not str(folder).strip() or str(folder) == ".":
        raise core.PdfManagerError("choose a folder to watch")
    if not folder.is_absolute():
        raise core.PdfManagerError("give the whole path of the folder, e.g. C:\\Users\\you\\Scans")
    if not folder.is_dir():
        raise core.PdfManagerError(f"there's no folder {folder}")
    folder = folder.resolve()
    for other in existing:
        there = Path(other["folder"])
        if there == folder:
            raise core.PdfManagerError(f"{folder} is watched already")
        if there in folder.parents and folder.name in (DONE_FOLDER, ORIGINALS_FOLDER):
            raise core.PdfManagerError(f"{folder} is where another watch folder puts its files")
    if not isinstance(do, dict):
        raise core.PdfManagerError("choose what to do with the files")
    if "tool" in do:
        if do["tool"] not in TOOLS:
            raise core.PdfManagerError(f"{do['tool']!r} can't be used for a watch folder; "
                                       f"choose one of {', '.join(TOOLS.values())} or a recipe")
        do = {"tool": do["tool"]}
    elif "recipe" in do:
        recipes.get(str(do["recipe"]))  # says which recipes there are, if not
        do = {"recipe": str(do["recipe"])}
    else:
        raise core.PdfManagerError("choose what to do with the files")
    if results not in ("done", "beside"):
        raise core.PdfManagerError("choose where the results go")
    if originals not in ("keep", "move"):
        raise core.PdfManagerError("choose what happens to the originals")
    return {"id": secrets.token_hex(6), "folder": str(folder), "do": do, "results": results,
            "originals": originals, "paused": False}


def steps(entry):
    """The recipe steps a folder runs (a tool: one step with its default options)."""
    from pdfmanager import recipes

    do = entry["do"]
    if "tool" in do:
        return [{"op": do["tool"], "options": default_options(do["tool"]) if default_options else {}}]
    return recipes.get(do["recipe"])["steps"]


def settings_for(entry):
    folder = Path(entry["folder"])
    beside = entry["results"] == "beside"
    return watch.WatchSettings(
        inbox=folder, dest=folder if beside else folder / DONE_FOLDER, recipe=steps(entry),
        originals="keep" if entry["originals"] == "keep" else "move",
        originals_folder=ORIGINALS_FOLDER, suffix=suffix(entry) if beside else "")


class _OneWatcher:
    """watch.lock in the data folder, held by the copy of ZPDF that watches."""

    def __init__(self):
        self.fh = None

    def acquire(self):
        if self.fh is not None:
            return True
        try:
            fh = open(data_dir() / "watch.lock", "a+b")
        except OSError:
            return True  # can't tell: watch all the same
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self.fh = fh
        return True

    def release(self):
        fh, self.fh = self.fh, None
        if fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


class WatchFolders:
    """The app's watch folders and the thread that watches them."""

    def __init__(self, interval=None, log=None, thread=True):
        self.run_thread = thread  # False: poll_all() is called by hand (tests)
        self.interval = INTERVAL if interval is None else interval
        self.log = log or (lambda text: None)
        self.lock = threading.RLock()
        self.folders = load()
        self.status = {}      # id -> {"state", "detail", "done", "failed", "last"}
        self.states = {}      # id -> watch._State (sizes between looks)
        self.memories = {}    # id -> watch.ProcessedFiles
        self.news = []
        self._seq = itertools.count(1)
        self.last = 0
        self.thread = None
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self.owner = _OneWatcher()

    # -- the list Settings shows ----------------------------------------------------------

    def list(self):
        with self.lock:
            return [{**f, "describe": describe(f), "status": self._status(f)} for f in self.folders]

    def _status(self, entry):
        status = self.status.get(entry["id"], {})
        state = "paused" if entry["paused"] else status.get("state", "starting")
        return {"state": state, "detail": status.get("detail", ""), "done": status.get("done", 0),
                "failed": status.get("failed", 0), "last": status.get("last")}

    def add(self, folder, do, results="done", originals="keep"):
        with self.lock:
            entry = check(folder, do, results, originals, self.folders)
            self.folders.append(entry)
            _save(self.folders)
        self.start()
        self.wake.set()
        return entry

    def set_paused(self, folder_id, paused):
        with self.lock:
            entry = self._entry(folder_id)
            entry["paused"] = bool(paused)
            _save(self.folders)
        self.wake.set()
        return entry

    def remove(self, folder_id):
        with self.lock:
            entry = self._entry(folder_id)
            self.folders.remove(entry)
            _save(self.folders)
            for store in (self.status, self.states, self.memories):
                store.pop(folder_id, None)
        _memory_file(folder_id).unlink(missing_ok=True)
        return entry

    def _entry(self, folder_id):
        for entry in self.folders:
            if entry["id"] == folder_id:
                return entry
        raise core.PdfManagerError("that watch folder is no longer there")

    def news_after(self, after):
        with self.lock:
            return [n for n in self.news if n["n"] > after], self.last

    # -- watching --------------------------------------------------------------------------

    def start(self):
        """Start the thread (once there's a folder to watch)."""
        with self.lock:
            if self.thread is not None or not self.folders or not self.run_thread:
                return
            self.stopping.clear()
            self.thread = threading.Thread(target=self._run, name="watch-folders", daemon=True)
            self.thread.start()

    def stop(self, timeout=5.0):
        self.stopping.set()
        self.wake.set()
        thread, self.thread = self.thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self.owner.release()

    def _run(self):
        while not self.stopping.is_set():
            if self.owner.acquire():
                self.poll_all()
            else:
                with self.lock:
                    for entry in self.folders:
                        self._set(entry["id"], state="elsewhere",
                                  detail="Another ZPDF window is watching this folder.")
            self.wake.wait(self.interval)
            self.wake.clear()

    def poll_all(self):
        """One look at every folder that isn't paused (the thread calls it;
        tests call it straight)."""
        with self.lock:
            active = [dict(f) for f in self.folders if not f["paused"]]
        for entry in active:
            if self.stopping.is_set():
                return
            self._poll(entry)

    def _still_on(self, folder_id):
        with self.lock:
            return any(f["id"] == folder_id and not f["paused"] for f in self.folders)

    def _poll(self, entry):
        folder_id = entry["id"]
        folder = Path(entry["folder"])
        if not folder.is_dir():
            self._set(folder_id, state="missing", detail=f"Waiting for the folder: {folder} isn't there.")
            return
        try:
            settings = settings_for(entry)
        except core.PdfManagerError as exc:  # e.g. its recipe was deleted
            self._set(folder_id, state="error", detail=str(exc)[:1].upper() + str(exc)[1:])
            return
        with self.lock:
            state = self.states.setdefault(folder_id, watch._State())
            memory = self.memories.get(folder_id)
            if memory is None:
                memory = self.memories[folder_id] = watch.ProcessedFiles(_memory_file(folder_id))
        self._set(folder_id, state="watching", detail="")

        def report(path, result, error):
            self._report(entry, path, result, error)

        def stop():
            return self.stopping.is_set() or not self._still_on(folder_id)

        try:
            watch.poll(settings, state, None, log=lambda text: self.log(f"watch {folder.name}: {text}"),
                       memory=memory, report=report, stop=stop)
        except (core.PdfManagerError, OSError) as exc:
            self._set(folder_id, state="missing", detail=f"Waiting for the folder: {exc}")

    def _set(self, folder_id, **changes):
        with self.lock:
            self.status.setdefault(folder_id, {}).update(changes)

    def _report(self, entry, path, result, error):
        if isinstance(error, watch.FileInUse):
            # Not a failure (it is tried again once closed): just news.
            with self.lock:
                n = next(self._seq)
                self.news.append({"n": n, "folder": entry["folder"], "folder_id": entry["id"], "name": path.name,
                                  "ok": False, "waiting": True, "message": str(error), "result": None})
                self.news = self.news[-NEWS_KEPT:]
                self.last = n
            return
        ok = error is None
        if ok:
            message = (f"{path.name}: skipped, the same as one done before" if result is None else
                       f"{path.name} is done: {result.name}" +
                       (f" in {result.parent.name}" if result.parent != path.parent else ""))
        else:
            if isinstance(error, PLAIN_ERRORS):
                plain = str(error)
            else:
                from pdfmanager import applog

                plain = applog.unexpected(error)
            message = f"{path.name} couldn't be done: {plain}"
        with self.lock:
            status = self.status.setdefault(entry["id"], {})
            status["done" if ok else "failed"] = status.get("done" if ok else "failed", 0) + 1
            status["last"] = {"name": path.name, "ok": ok, "message": message, "at": time.time()}
            n = next(self._seq)
            self.news.append({"n": n, "folder": entry["folder"], "folder_id": entry["id"], "name": path.name,
                              "ok": ok, "message": message,
                              "result": str(result) if ok and result is not None else None})
            self.news = self.news[-NEWS_KEPT:]
            self.last = n
