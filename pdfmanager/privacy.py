# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""What ZPDF leaves on this computer, and when it goes online.

Working files: while the app runs, everything a tool writes on the way to
its result goes into one folder of this run, under the app's own data
folder (not the shared temp folder). The folder is deleted when the app
closes, and folders left by a copy that crashed are swept at the next
start.

History: the Recent list, what was read from each file (file facts), the
text kept by Search a folder. ``clear_history`` empties them;
``remembering`` is the "Remember the files I open" setting, which keeps
them from being written at all when it's off.

``ONLINE`` is every occasion ZPDF connects to anything outside this
computer. There is no telemetry: nothing is sent about you or your files.
"""

import atexit
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

from pdfmanager import appdata

ONLINE = [
    {"what": "Checking for a new version",
     "when": "when ZPDF starts (at most twice a day), and when you press Check now",
     "sends": "nothing about you or your files: it reads the list of published versions on GitHub",
     "switch": "Settings → Check for new versions when ZPDF starts"},
    {"what": "Downloading an update",
     "when": "only when you press the Update button",
     "sends": "nothing: it downloads the new version from GitHub"},
    {"what": "Downloading a language or best-accuracy model for reading scans (OCR)",
     "when": "only when you choose a language or Best accuracy that isn't installed yet",
     "sends": "nothing: it downloads the model's data"},
    {"what": "Downloading a language pack or offline translation model",
     "when": "only when you open the catalog or press Download in Settings → Language (or Translate)",
     "sends": "nothing: it reads the catalog and downloads the pack from GitHub or the Argos Translate open-source index"},
    {"what": "Downloading optional Manga AI models (bubble detector, Baberu OCR v1.2 JA/ZH/EN, LaMa inpainting)",
     "when": "only when you enable Enhanced Manga AI in Translate PDF or Settings → Offline AI Models",
     "sends": "nothing: it downloads SHA-256 verified Apache-2.0 ONNX weights from Hugging Face"},
    {"what": "A timestamp when signing with a Digital ID",
     "when": "only when you give a timestamp server's address",
     "sends": "a fingerprint (hash) of the signature to that server, never the document"},
    {"what": "Turning a web page into a PDF",
     "when": "only for the address you type (a saved web page may fetch the pictures it links to)",
     "sends": "what any browser sends when it opens that page"},
    {"what": "Finding and using a network scanner",
     "when": "only on the Scan page: a question to your own network asking which scanners are there, and the "
             "scan itself from the one you choose",
     "sends": "nothing beyond your local network: the question and the scan settings go to the scanner only"},
    {"what": "Opening a link in a PDF",
     "when": "only when you click it (ZPDF asks first and shows the real address)",
     "sends": "nothing from ZPDF: your browser opens the page"},
    {"what": "Vision-LLM OCR & visual page translation",
     "when": "only when you explicitly run Vision-LLM OCR/translation using your configured local or BYOK endpoint",
     "sends": "the rendered image of the selected page to the endpoint you configured (defaults to localhost)"},
]

_lock = threading.Lock()
_run = None        # this run's folder, once begun
_before = None     # tempfile.tempdir before it


# ---- working files --------------------------------------------------------------------

def temp_root():
    """The folder that holds each running copy's working folder."""
    return appdata.data_dir() / "temp"


def _alive(pid):
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # (access denied: it's there, and not ours to ask)
        code = ctypes.c_ulong()
        got = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(got) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # (it's there, and someone else's)
    return True


def _remove(folder):
    """Delete a folder and all in it; what's still in use is left for the
    next sweep. Returns whether it's gone."""
    shutil.rmtree(folder, ignore_errors=True)
    return not Path(folder).exists()


def sweep():
    """Delete the working folders of copies that are no longer running (a
    crash, the power going). Returns how many were removed."""
    removed = 0
    try:
        folders = [f for f in temp_root().iterdir() if f.is_dir() and f.name.startswith("run-")]
    except OSError:
        return 0
    for folder in folders:
        try:
            pid = int(folder.name.split("-")[1])
        except (IndexError, ValueError):
            pid = 0
        if not pid or not _alive(pid):
            removed += _remove(folder)
    return removed


def begin_run():
    """From now on this copy's working files go into its own folder (made
    for this user only), and earlier copies' leftovers are swept. Returns
    the folder; calling it again changes nothing."""
    global _run, _before
    with _lock:
        if _run is not None:
            return _run
        try:
            sweep()
            folder = appdata._private_dir(temp_root()) / f"run-{os.getpid()}"
            if folder.exists():  # (a process number used again)
                _remove(folder)
            appdata._private_dir(folder)
            if not folder.is_dir():
                return None
        except OSError:
            return None  # (the shared temp folder, as before)
        _before, tempfile.tempdir = tempfile.tempdir, str(folder)
        _run = folder
        atexit.register(end_run)
        return folder


def end_run():
    """Delete this run's working folder (when the app closes)."""
    global _run, _before
    with _lock:
        if _run is None:
            return
        folder, _run = _run, None
        tempfile.tempdir, _before = _before, None
        _remove(folder)


def left_behind():
    """The working files there now, as paths (for tests and diagnostics)."""
    try:
        return sorted(str(p) for p in temp_root().rglob("*") if p.is_file())
    except OSError:
        return []


# ---- history --------------------------------------------------------------------------

def remembering():
    """Whether ZPDF keeps a history of the files it opened (the default)."""
    from pdfmanager import update

    return update.settings().get("remember_files", True) is not False


def set_remembering(on):
    """Turn the history on or off; turning it off also clears it."""
    from pdfmanager import update

    update.save_settings(remember_files=None if on else False)
    if not on:
        clear_history()


def history():
    """What's kept now: {"recent": files in the Recent list, "facts": files
    remembered, "search": whether Search a folder keeps text}."""
    import json

    folder = appdata.data_dir()

    def count(name):
        try:
            return len(json.loads((folder / name).read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return 0

    return {"recent": count("recent.json"), "facts": count("filefacts.json"),
            "search": (folder / "search.sqlite").exists()}


def clear_history():
    """Empty the Recent list, the remembered file facts and the text kept by
    Search a folder. Returns what was cleared, in words. The log is kept: it
    names no files (applog scrubs them). What the reader keeps in the window itself (where
    you were in each file) is cleared by the page."""
    from pdfmanager import search

    folder = appdata.data_dir()
    cleared = []
    for name, said in (("recent.json", "the Recent list"), ("filefacts.json", "what was read from your files")):
        target = folder / name
        if target.exists():
            try:
                target.unlink()
                cleared.append(said)
            except OSError:
                pass
    if (folder / "search.sqlite").exists():
        try:
            search.forget_index()
            cleared.append("the text kept by Search a folder")
        except OSError:
            pass
    return cleared
