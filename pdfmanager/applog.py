# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""ZPDF's log, error ids, and the diagnostics a friend can send.

The app keeps a small log in its data folder (logs/zpdf.log, 1 MB, with
three older files): when it started and stopped, its version and the
computer's, each tool run (its name, how many files, their size, how long
it took and how it ended) and the full details of anything that went
wrong unexpectedly. Every line is scrubbed on its way to the file: the
home folder becomes "~" and anything that looks like a document's name
becomes "<file>", so the log can be sent to someone without showing what
was worked on.

An unexpected error gets a short id, written to the log with its details
and shown to the user in a plain sentence; "Copy diagnostics" in Settings
then gives the id's context.
"""
import logging
import logging.handlers
import os
import platform
import re
import secrets
import subprocess
import sys
from pathlib import Path

from pdfmanager import __version__
from pdfmanager.appdata import data_dir

log = logging.getLogger("zpdf")
MAX_BYTES = 1_000_000
BACKUPS = 3
DIAGNOSTIC_LINES = 200

_EXTENSIONS = (r"pdf|png|jpe?g|tiff?|bmp|gif|webp|docx?|xlsx?|pptx?|odt|ods|odp|rtf|html?|txt|csv|fdf|xfdf|xml|"
               r"json|p12|pfx|icc|icm")
# A document's name: from the last separator, quote or bracket (names can
# have spaces, "Salary 2026.pdf") up to its extension.
_DOCUMENT = re.compile(rf"""(?:(?<=[\\/'"\[(\s])|^)[^\\/'"\[\]()\n]*?\.(?:{_EXTENSIONS})\b""", re.I | re.M)


def scrub(text):
    """``text`` without the home folder or document names in it."""
    home = str(Path.home())
    if home and home not in ("/", "\\"):
        for form in {home, home.replace("\\", "\\\\"), home.replace("\\", "/")}:
            text = text.replace(form, "~")
    return _DOCUMENT.sub("<file>", text)


class _ScrubbingFormatter(logging.Formatter):
    def format(self, record):
        return scrub(super().format(record))


def log_dir():
    return data_dir() / "logs"


def setup(max_bytes=MAX_BYTES):
    """Write the log to the data folder (again, if that folder changed)."""
    target = log_dir() / "zpdf.log"
    for handler in list(log.handlers):
        if getattr(handler, "baseFilename", None) == str(target):
            return
        log.removeHandler(handler)
        handler.close()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(target, maxBytes=max_bytes, backupCount=BACKUPS,
                                                       encoding="utf-8", delay=True)
    except OSError:
        return  # a read-only or full disk must not stop the app
    handler.setFormatter(_ScrubbingFormatter("%(asctime)s %(levelname)s [%(process)d] %(message)s",
                                             "%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    log.setLevel(logging.DEBUG if os.environ.get("PDFM_LOG") else logging.INFO)
    log.propagate = False


def shutdown():
    for handler in list(log.handlers):
        log.removeHandler(handler)
        handler.close()


def install():
    """Report unexpected errors in background jobs through this module."""
    from pdfmanager import progress

    progress.report_unexpected = unexpected


def unexpected(exc):
    """Log ``exc`` with its details under a new id; the sentence to show."""
    error_id = secrets.token_hex(2).upper()
    log.error("error %s: %s: %s", error_id, type(exc).__name__, exc, exc_info=(type(exc), exc, exc.__traceback__))
    return (f"Something went wrong (error {error_id}). Settings › About › Copy diagnostics "
            "helps report it.")


def tool_run(op, files, seconds, outcome):
    sizes = 0
    for path in files:
        try:
            sizes += Path(path).stat().st_size
        except OSError:
            pass
    count = len(files)
    log.info("tool %s: %d file%s, %.1f MB, %.1f s, %s", op, count, "" if count == 1 else "s",
             sizes / 1e6, seconds, outcome)


def started(how):
    log.info("ZPDF %s started (%s) on %s, Python %s", __version__, how, platform.platform(), platform.python_version())


def _engines():
    lines = []
    try:
        from pdfmanager import ocr

        models = sorted(p.stem for p in ocr.models_dir().glob("*.onnx")) if ocr.models_dir().exists() else []
        lines.append(f"Built-in OCR: {'yes' if ocr.builtin_available() else 'no'}"
                     + (f" (reading models: {', '.join(models)})" if models else ""))
        lines.append(f"Tesseract: {'yes' if ocr.tesseract_available() else 'no'}")
    except Exception as exc:  # diagnostics must never fail
        lines.append(f"Built-in OCR: unknown ({type(exc).__name__})")
    try:
        from pdfmanager import archive

        lines.append(f"Ghostscript: {'yes' if archive.find_ghostscript() else 'no'}")
    except Exception as exc:
        lines.append(f"Ghostscript: unknown ({type(exc).__name__})")
    try:
        from pdfmanager import office

        found = office.available()
        names = [name for name, there in found.items() if there]
        lines.append(f"Office: {', '.join(names) if names else 'none'}")
    except Exception as exc:
        lines.append(f"Office: unknown ({type(exc).__name__})")
    return lines


def _recent_lines(count=DIAGNOSTIC_LINES):
    target = log_dir() / "zpdf.log"
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ["(no log yet)"]
    if len(lines) < count:
        older = target.with_name("zpdf.log.1")
        try:
            lines = older.read_text(encoding="utf-8", errors="replace").splitlines() + lines
        except OSError:
            pass
    return lines[-count:]


def diagnostics():
    """What helps to understand a problem, without anything private: the
    versions, how ZPDF was installed, which engines it can use, its
    settings (never the token) and the end of the log."""
    from pdfmanager import update

    system = "Windows" if sys.platform == "win32" else "Platform"
    try:
        settings = update.public_settings()
    except Exception as exc:
        settings = {"unknown": type(exc).__name__}
    parts = [
        f"ZPDF {__version__}, installed as: {settings.get('how', '?')}",
        f"{system}: {platform.platform()} ({platform.machine()})",
        f"Python {platform.python_version()} ({platform.architecture()[0]})",
        *_engines(),
        "Updates: " + ", ".join(f"{k}={v}" for k, v in settings.items() if k != "how"),
        f"Data folder: {data_dir()}",
        "",
        f"Last {DIAGNOSTIC_LINES} lines of the log:",
        *_recent_lines(),
    ]
    return scrub("\n".join(str(p) for p in parts)) + "\n"


def open_folder():
    """Show the log folder in the computer's file manager."""
    folder = log_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(folder)  # noqa: S606 (a folder, not a program)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])
