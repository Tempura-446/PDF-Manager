"""Where PDF Manager keeps its own files: saved recipes, the browser profile
of the app window and a note about why the native window didn't open."""

import os
import sys
from pathlib import Path


def data_dir():
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "PDF Manager"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PDF Manager"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "pdf-manager"
