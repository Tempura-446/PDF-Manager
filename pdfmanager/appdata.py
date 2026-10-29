"""Where ZPDF keeps its own files: saved recipes, the browser profile
of the app window and a note about why the native window didn't open."""

import os
import sys
from pathlib import Path


def data_dir():
    """The app's own folder, made for this user only (it holds secrets)."""
    return _private_dir(_data_dir())


def _private_dir(path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def write_private(path, text):
    """Write ``text`` to ``path`` so only this user can read it, replacing
    any earlier file in one step."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def touch_private(path):
    """Create ``path`` empty, readable by this user only, if it's missing."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        pass


def _data_dir():
    # The folders keep the app's first name (it was PDF Manager before
    # ZPDF), so recipes, settings and installed copies carry over.
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "PDF Manager"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PDF Manager"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "pdf-manager"
