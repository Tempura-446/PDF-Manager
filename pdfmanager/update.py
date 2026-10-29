"""Updates: see whether a newer PDF Manager is on GitHub and install it.

The newest version is the one in pdfmanager/__init__.py on the
repository's default branch. A private repository needs a GitHub token
that can read it (a fine-grained token with read access to its contents),
entered in the app's Settings. It's kept in settings.json in the app's
data folder.

Installing downloads that branch and installs it with pip into the Python
the app runs with, so it works for the Windows installer's copy (and any
other pip install). A copy running from a git checkout is updated with
git, and the standalone app by downloading it again.
"""

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from pdfmanager import __version__, core, progress
from pdfmanager.appdata import data_dir, write_private

REPO = "Tempura-446/PDF-Manager"
API = "https://api.github.com"
CHECK_EVERY = 12 * 3600  # seconds
TIMEOUT = 20


# ---- settings -------------------------------------------------------------------------

def _settings_file():
    return data_dir() / "settings.json"


def settings():
    try:
        data = json.loads(_settings_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(**changes):
    data = settings()
    for key, value in changes.items():
        if value is None or value == "":
            data.pop(key, None)
        else:
            data[key] = value
    write_private(_settings_file(), json.dumps(data, indent=2))  # holds the token
    return data


def public_settings():
    """Settings for the page (never the token itself)."""
    data = settings()
    return {"repo": data.get("update_repo") or REPO, "token_set": bool(data.get("github_token")),
            "check": data.get("check_updates", True), "how": how_installed()}


# ---- checking -------------------------------------------------------------------------

def parse_version(text):
    return tuple(int(n) for n in re.findall(r"\d+", text)[:4])


def how_installed():
    """'pip' (can update itself), 'source' (a git checkout) or 'frozen'
    (the standalone app)."""
    if getattr(sys, "frozen", False):
        return "frozen"
    here = Path(__file__).resolve().parent.parent
    if (here / "pyproject.toml").exists() or (here / ".git").exists():
        return "source"
    return "pip"


def _request(path, raw=False):
    data = settings()
    req = urllib.request.Request(path if path.startswith("http") else API + path)
    req.add_header("Accept", "application/vnd.github.raw" if raw else "application/vnd.github+json")
    req.add_header("User-Agent", f"pdf-manager/{__version__}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data.get("github_token"):
        # Not passed on when GitHub redirects a download to its file servers.
        req.add_unredirected_header("Authorization", f"Bearer {data['github_token']}")
    return req


def _open(req):
    try:
        return urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            token = bool(settings().get("github_token"))
            raise core.PdfManagerError(
                "the token can't read the repository; check it in Settings" if token else
                "the repository is private: add a GitHub token in Settings to check for updates"
            ) from None
        raise core.PdfManagerError(f"GitHub answered {exc.code} {exc.reason}") from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise core.PdfManagerError(f"can't reach GitHub ({reason})") from None


def _repo():
    return settings().get("update_repo") or REPO


def latest_version():
    """(version, branch) on GitHub."""
    repo = _repo()
    with _open(_request(f"/repos/{repo}")) as res:
        branch = json.load(res).get("default_branch") or "main"
    with _open(_request(f"/repos/{repo}/contents/pdfmanager/__init__.py?ref={branch}", raw=True)) as res:
        text = res.read().decode("utf-8", "replace")
    found = re.search(r"""__version__\s*=\s*["']([^"']+)["']""", text)
    if not found:
        raise core.PdfManagerError("can't find the version number on GitHub")
    return found.group(1), branch


def _cache_file():
    return data_dir() / "update.json"


def check(force=False):
    """{"current", "latest", "available", "error", "how", "checked"}. Asks
    GitHub at most every CHECK_EVERY seconds unless ``force``."""
    result = {"current": __version__, "how": how_installed()}
    try:
        cached = json.loads(_cache_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    fresh = (cached.get("repo") == _repo() and cached.get("for") == __version__
             and time.time() - cached.get("checked", 0) < CHECK_EVERY)
    if not force and (fresh or not settings().get("check_updates", True)):
        if not fresh:
            return {**result, "latest": None, "available": False, "error": None, "checked": None}
        latest, error, checked = cached.get("latest"), cached.get("error"), cached.get("checked")
    else:
        latest, error, checked = None, None, time.time()
        try:
            latest = latest_version()[0]
        except core.PdfManagerError as exc:
            error = str(exc)
        try:
            _cache_file().parent.mkdir(parents=True, exist_ok=True)
            _cache_file().write_text(json.dumps({"latest": latest, "error": error, "checked": checked,
                                                 "repo": _repo(), "for": __version__}),
                                     encoding="utf-8")
        except OSError:
            pass
    available = bool(latest) and parse_version(latest) > parse_version(__version__)
    return {**result, "latest": latest, "available": available, "error": error, "checked": checked}


# ---- installing -----------------------------------------------------------------------

def _extract(data, target):
    """Unzip GitHub's download into ``target``; returns the project folder."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        root = Path(target).resolve()
        for member in zf.infolist():
            dest = (root / member.filename).resolve()
            if root not in dest.parents and dest != root:
                raise core.PdfManagerError("the download contains unsafe file names")
        zf.extractall(root)
    projects = [p.parent for p in Path(target).glob("*/pyproject.toml")]
    if len(projects) != 1:
        raise core.PdfManagerError("the download isn't a PDF Manager source folder")
    return projects[0]


def install(python=None):
    """Download the newest version and install it with pip. Returns the
    version installed. The running app keeps the old version until it's
    started again."""
    how = how_installed()
    if how == "source":
        raise core.PdfManagerError("this copy runs from a source folder; update it with git pull")
    if how == "frozen":
        raise core.PdfManagerError("download the new standalone app from GitHub to update it")
    progress.say("Finding the newest version…")
    version, branch = latest_version()
    progress.say(f"Downloading version {version}…")
    progress.check()
    with _open(_request(f"/repos/{_repo()}/zipball/{branch}")) as res:
        data = res.read()
    progress.check()
    with tempfile.TemporaryDirectory(prefix="pdfm-update-") as tmp:
        project = _extract(data, tmp)
        progress.say(f"Installing version {version}…")
        extra = "[app]" if _has_pywebview() else ""
        proc = subprocess.run(
            [python or sys.executable, "-I", "-m", "pip", "install", "--upgrade", "--disable-pip-version-check",
             "--no-input", f"{project}{extra}"],
            capture_output=True, text=True, timeout=900,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode != 0:
            tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-6:])
            raise core.PdfManagerError(f"installing the update failed:\n{tail}")
    try:
        _cache_file().unlink()
    except OSError:
        pass
    return version


def _has_pywebview():
    import importlib.util
    return importlib.util.find_spec("webview") is not None


# ---- starting again -------------------------------------------------------------------

def _wait_for_exit(pid, timeout=60):
    deadline = time.monotonic() + timeout
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if handle:
            ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout * 1000))
            ctypes.windll.kernel32.CloseHandle(handle)
        return
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)


def restart_after(pid):
    """Start a helper that opens the app again once process ``pid`` (the
    running app) has ended."""
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    exe = windowless if windowless.exists() else exe
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    return subprocess.Popen([str(exe), "-I", "-m", "pdfmanager.update", "--relaunch-after", str(pid)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True,
                            start_new_session=os.name == "posix", cwd=str(Path.home()))


def _relaunch(pid):
    _wait_for_exit(pid)
    from pdfmanager import gui

    gui.launch()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--relaunch-after":
        _relaunch(int(sys.argv[2]))
