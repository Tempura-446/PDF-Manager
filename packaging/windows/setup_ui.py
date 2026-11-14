# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""ZPDF's setup window on Windows.

install.ps1 starts this once it has a Python, instead of asking its
questions in the console:

    python -I setup_ui.py --source <the ZPDF folder> --installer <install.ps1> [--no-desktop ...]

It shows setup.html in a window of its own (Microsoft Edge in app mode,
which every Windows 10 and 11 has: nothing is installed yet to draw a
window with), and does the installation by running install.ps1 again with
-Engine, which asks nothing and marks its steps on lines that start
with "@@". The page and this program talk over a local address only this
PC can reach, guarded by a one-time token.

Exit codes: 0 installed, 1 the installation failed, 2 closed before
installing, 3 no window could be shown (install.ps1 then carries on in the
console). Only the standard library is used: ZPDF's own packages aren't
installed yet.
"""
import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
OK, FAILED, CANCELLED, NO_WINDOW = 0, 1, 2, 3
LOG_LINES = 400
# The steps install.ps1 marks, in order: (key, what the window calls it).
STEPS = (("python", "Python"), ("app", "ZPDF and its packages"), ("shortcuts", "Shortcuts and menus"),
         ("extras", "Tesseract and Ghostscript"))


def find_edge():
    """Microsoft Edge's program, or None."""
    for var in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(var)
        if root:
            exe = Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if exe.is_file():
                return str(exe)
    return shutil.which("msedge")


def version_of(source):
    try:
        text = (Path(source) / "pdfmanager" / "__init__.py").read_text(encoding="utf-8")
        found = re.search(r'__version__\s*=\s*"([^"]+)"', text)
        return found.group(1) if found else ""
    except OSError:
        return ""


def brand_mark(source):
    """The app's own mark (the Z on a page), taken from its page so there is one copy of it."""
    try:
        html = (Path(source) / "pdfmanager" / "gui.html").read_text(encoding="utf-8")
        found = re.search(r'<span class="brand-mark">(<svg.*?</svg>)</span>', html, re.S)
        return found.group(1) if found else ""
    except OSError:
        return ""


def app_dir():
    return str(Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "PDF Manager" / "app")


class Setup:
    """The installation as the window sees it: its choices, steps and log."""

    def __init__(self, source, installer, options, engine=None):
        self.source, self.installer = str(source), str(installer)
        self.defaults = dict(options)
        self.engine = engine or self._engine  # (tests give their own)
        self.lock = threading.Lock()
        self.phase = "welcome"   # welcome, installing, done, failed
        self.options = dict(options)
        self.steps, self.detail, self.error, self.log, self.extras = [], "", "", [], []
        self.process = None
        self.finished = threading.Event()  # the window is no longer needed
        self.launch = False

    # -- what the page asks for --------------------------------------------------------
    def config(self, token):
        return {"token": token, "version": version_of(self.source),
                "updating": (Path(app_dir()) / "venv").is_dir(), "appDir": app_dir(),
                "desktop": self.defaults["desktop"], "openWith": self.defaults["openWith"],
                "contextMenu": self.defaults["contextMenu"], "extras": self.defaults["extras"],
                "mark": brand_mark(self.source)}

    def state(self):
        with self.lock:
            return {"phase": self.phase, "steps": [dict(s) for s in self.steps], "detail": self.detail,
                    "error": self.error, "log": list(self.log), "extras": list(self.extras),
                    "options": dict(self.options)}

    def start(self, options):
        """Begin (or begin again after a failure). Only yes or no is taken
        from the page for each choice: nothing it sends reaches a command."""
        with self.lock:
            if self.phase == "installing":
                return
            self.options = {key: bool(options.get(key, self.defaults[key])) for key in self.defaults}
            self.phase, self.detail, self.error, self.log, self.extras = "installing", "", "", [], []
            self.steps = [{"key": key, "title": title, "state": "pending", "note": ""}
                          for key, title in STEPS if key != "extras" or self.options["extras"]]
        threading.Thread(target=self._run, daemon=True).start()

    # -- running install.ps1 ------------------------------------------------------------
    def _engine(self, options):
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", self.installer,
                   "-Engine", "-NoLaunch"]
        if not options["desktop"]:
            command.append("-NoDesktop")
        if not options["openWith"]:
            command.append("-NoOpenWith")
        if not options["contextMenu"]:
            command.append("-NoContextMenu")
        command.append("-WithExtras" if options["extras"] else "-NoExtras")
        return command

    def _mark(self, key, state, note=None):
        for step in self.steps:
            if step["key"] == key:
                step["state"] = state
                if note is not None:
                    step["note"] = note

    def _line(self, line):
        """One line the installer wrote: a marker ("@@step app", "@@note
        ...", "@@extra Tesseract|1"), or something for the log."""
        with self.lock:
            if line.startswith("@@step "):
                key = line[7:].split("|", 1)[0].strip()
                for step in self.steps:
                    if step["state"] == "now":
                        step["state"] = "done"
                self._mark(key, "now")
                self.detail = ""
            elif line.startswith("@@note "):
                key, _, note = line[7:].partition("|")
                self._mark(key.strip(), next((s["state"] for s in self.steps if s["key"] == key.strip()), "pending"), note.strip())
            elif line.startswith("@@extra "):
                name, _, ready = line[8:].partition("|")
                self.extras.append({"name": name.strip(), "ready": ready.strip() == "1"})
            elif line.startswith("@@"):
                pass  # a marker from a newer installer: nothing to show
            elif line.strip():
                self.detail = line.strip()[:200]
                self.log.append(line.rstrip()[:500])
                del self.log[:-LOG_LINES]

    def _run(self):
        code, problem = 1, ""
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.process = subprocess.Popen(self.engine(self.options), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                            creationflags=flags, cwd=self.source)
            for line in self.process.stdout:
                self._line(line.rstrip("\r\n"))
            code = self.process.wait()
        except OSError as exc:
            problem = f"The installer could not be started ({exc})."
        with self.lock:
            if code == 0:
                for step in self.steps:
                    if step["state"] in ("now", "pending"):
                        step["state"] = "done" if step["state"] == "now" else "skipped"
                self.phase = "done"
            else:
                for step in self.steps:
                    if step["state"] == "now":
                        step["state"] = "failed"
                self.error = problem or _reason(self.log)
                self.phase = "failed"
            self.detail = ""

    # -- the end -------------------------------------------------------------------------
    def open_app(self):
        """Start the installed ZPDF (as install.ps1 does at its end)."""
        pythonw = Path(app_dir()) / "venv" / "Scripts" / "pythonw.exe"
        if self.phase == "done" and pythonw.is_file():
            subprocess.Popen([str(pythonw), "-I", "-m", "pdfmanager", "gui"], cwd=str(Path.home()),
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        self.finished.set()

    def exit_code(self):
        return {"done": OK, "failed": FAILED, "welcome": CANCELLED}.get(self.phase, FAILED)


def _reason(log):
    """The line of the installer's output that says what went wrong: the
    message PowerShell prints for a stopped script, else the last line."""
    for line in reversed(log):
        text = line.strip()
        if text and not text.startswith(("At ", "+", "~", "CategoryInfo", "FullyQualifiedErrorId")) \
                and "CategoryInfo" not in text and "FullyQualifiedErrorId" not in text:
            text = re.sub(r"^.*?install\.ps1\s*:\s*", "", text)
            return re.sub(r"\s*\(see above\)", "", text)[:400]  # ("above" is the console's; here it's in the details)
    return "The installer stopped without saying why."


def make_server(setup):
    """(server, the address the window opens, the token)"""
    token = secrets.token_urlsafe(24)
    page = (HERE / "setup.html").read_text(encoding="utf-8")
    # The app's fonts, read once: a request picks one by its name from this
    # list, and no path is ever made from what a request says.
    folder = Path(setup.source) / "pdfmanager" / "static" / "fonts"
    try:
        fonts = {font.name: font.read_bytes() for font in sorted(folder.glob("*.woff2"))}
    except OSError:
        fonts = {}

    class Handler(BaseHTTPRequestHandler):
        server_version = "ZPDFSetup"

        def log_message(self, *args):  # quiet
            pass

        def _send(self, status, body=b"", kind="application/json"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if kind.startswith("text/html"):
                self.send_header("Content-Security-Policy",
                                 "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                 "font-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
                                 "form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _local(self):
            """Only this PC, by its own name for itself (no other site's page may ask)."""
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            return host in ("127.0.0.1", "localhost")

        def _allowed(self):
            return self._local() and secrets.compare_digest(self.headers.get("X-Setup-Token") or "", token)

        def do_GET(self):  # noqa: N802 - the base class's name
            path, _, query = self.path.partition("?")
            if not self._local():
                return self._send(HTTPStatus.FORBIDDEN, {"error": "not this PC"})
            if path == "/":
                if not secrets.compare_digest(query, "t=" + token):
                    return self._send(HTTPStatus.FORBIDDEN, {"error": "missing or wrong token"})
                config = json.dumps(setup.config(token)).replace("</", "<\\/")
                body = re.sub(r"/\*CONFIG\*/.*?/\*END\*/", lambda m: config, page, count=1, flags=re.S)
                return self._send(HTTPStatus.OK, body.encode("utf-8"), "text/html; charset=utf-8")
            if path.startswith("/fonts/") and path[7:] in fonts:
                return self._send(HTTPStatus.OK, fonts[path[7:]], "font/woff2")
            if path == "/api/state":
                if not self._allowed():
                    return self._send(HTTPStatus.FORBIDDEN, {"error": "missing or wrong token"})
                return self._send(HTTPStatus.OK, setup.state())
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if not self._allowed():
                return self._send(HTTPStatus.FORBIDDEN, {"error": "missing or wrong token"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if 0 <= length <= 4096 else None
            except ValueError:
                body = None
            if not isinstance(body, dict):
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not understood"})
            if self.path == "/api/start":
                setup.start(body)
                return self._send(HTTPStatus.OK, setup.state())
            if self.path == "/api/open":
                setup.open_app()
                return self._send(HTTPStatus.OK, {"ok": True})
            if self.path == "/api/quit":
                if setup.phase != "installing":
                    setup.finished.set()
                return self._send(HTTPStatus.OK, {"ok": True})
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    return server, f"http://127.0.0.1:{server.server_address[1]}/?t={token}", token


def main(argv=None):
    parser = argparse.ArgumentParser(description="ZPDF's setup window.")
    parser.add_argument("--source", required=True, help="the unzipped ZPDF folder")
    parser.add_argument("--installer", required=True, help="install.ps1")
    parser.add_argument("--no-desktop", action="store_true")
    parser.add_argument("--no-open-with", action="store_true")
    parser.add_argument("--no-context-menu", action="store_true")
    parser.add_argument("--with-extras", action="store_true")
    args = parser.parse_args(argv)

    edge = find_edge()
    if not edge or not (HERE / "setup.html").is_file():
        return NO_WINDOW
    setup = Setup(args.source, args.installer, {"desktop": not args.no_desktop, "openWith": not args.no_open_with,
                                                "contextMenu": not args.no_context_menu, "extras": args.with_extras})
    try:
        server, url, _ = make_server(setup)
    except OSError:
        return NO_WINDOW
    threading.Thread(target=server.serve_forever, daemon=True).start()
    profile = tempfile.mkdtemp(prefix="zpdf-setup-")
    try:
        try:
            # A profile of its own: the window is then this process's, and
            # its closing is seen; nothing of the user's Edge is touched.
            window = subprocess.Popen([edge, f"--app={url}", f"--user-data-dir={profile}", "--window-size=760,640",
                                       "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                                       "--disable-sync", "--disable-features=Translate"])
        except OSError:
            return NO_WINDOW
        while window.poll() is None and not setup.finished.is_set():
            time.sleep(0.2)
        # The window was closed while installing: the installation goes on to its end.
        while setup.phase == "installing":
            time.sleep(0.2)
        if window.poll() is None:
            window.terminate()
        return setup.exit_code()
    finally:
        server.shutdown()
        server.server_close()
        time.sleep(0.3)
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
