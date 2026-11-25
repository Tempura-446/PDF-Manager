# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Shared HTTP server helpers, state stores, and HandlerBase for ZPDF."""

import hashlib
import hmac
import importlib
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from importlib import resources
from pathlib import Path

from pdfmanager import __version__, applog, privacy, progress, quickactions, update
from pdfmanager.appdata import data_dir, write_private
from pdfmanager.errors import PdfManagerError
from pdfmanager.pageranges import PageRangeError

# One run's uploads. Parsing a form upload takes several times its size in
# memory, so this stays well inside what a laptop has.
MAX_UPLOAD = 1024 * 1024 * 1024

MARKUPS_IN_HEADER = 16000   # characters of markups sent along with a change (more: the reader asks)

_STATIC_CACHE = {}  # static path -> (bytes, ETag)
STATIC_TYPES = {
    ".mjs": "text/javascript", ".js": "text/javascript", ".css": "text/css",
    ".svg": "image/svg+xml", ".gif": "image/gif", ".png": "image/png",
    ".wasm": "application/wasm", ".json": "application/json",
    ".webmanifest": "application/manifest+json",
    ".woff2": "font/woff2",  # the interface's fonts (static/fonts)
}

_PARAM = re.compile(r';\s*([\w*-]+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^;\s]*))')


def _is_lan_host(host: str) -> bool:
    """Return True if ``host`` (optional ``:port``) is a loopback, RFC-1918
    private LAN IP, or ``.local`` mDNS hostname."""
    import ipaddress

    raw = (host or "").strip()
    if not raw:
        return False
    if raw.startswith("["):
        name = raw[1:].split("]", 1)[0]
    else:
        name = raw.rsplit(":", 1)[0]
    low = name.lower()
    if low in ("localhost", "127.0.0.1", "::1") or (low.endswith(".local") and len(low) > 6 and ".." not in low):
        return True
    try:
        ip = ipaddress.ip_address(low)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


class _LazyModule:
    """Defer importing a heavy submodule until its first attribute access.

    Transparently forwards attribute lookups, monkeypatches (``setattr`` /
    ``delattr``), and ``dir()`` to the underlying module once loaded.
    """

    __slots__ = ("_mod_name", "_mod_obj")

    def __init__(self, mod_name):
        object.__setattr__(self, "_mod_name", mod_name)
        object.__setattr__(self, "_mod_obj", None)

    def _load(self):
        mod = object.__getattribute__(self, "_mod_obj")
        if mod is None:
            name = object.__getattribute__(self, "_mod_name")
            mod = importlib.import_module(name)
            object.__setattr__(self, "_mod_obj", mod)
        return mod

    def __getattr__(self, item):
        return getattr(self._load(), item)

    def __setattr__(self, item, value):
        setattr(self._load(), item, value)

    def __delattr__(self, item):
        delattr(self._load(), item)

    def __dir__(self):
        return dir(self._load())

    def __repr__(self):
        name = object.__getattribute__(self, "_mod_name")
        loaded = object.__getattribute__(self, "_mod_obj") is not None
        return f"<_LazyModule {name!r} ({'loaded' if loaded else 'unloaded'})>"


def lazy_module(mod_name):
    return _LazyModule(mod_name)


core = lazy_module("pdfmanager.core")


def gui_hook(name, default):
    """Look up ``name`` on ``pdfmanager.gui`` if loaded so tests that
    monkeypatch attributes on ``gui`` continue to take effect without creating
    a circular import."""
    mod = sys.modules.get("pdfmanager.gui")
    return getattr(mod, name, default) if mod is not None else default


class UserError(Exception):
    pass


# Errors whose message is shown to the user as it is.
EXPECTED_ERRORS = (UserError, PdfManagerError, PageRangeError, ValueError,
                   FileNotFoundError, NotADirectoryError)


def _bool(v):
    return str(v).lower() in ("1", "true", "on", "yes")


def _num(v, cast=float, default=None):
    if v in (None, ""):
        return default
    try:
        return cast(v)
    except ValueError:
        raise UserError(f"not a number: {v!r}") from None


def _pages(opts, key="pages"):
    return (opts.get(key) or "").strip() or None


class ResultCache:
    """Keeps the last few results so the desktop window can save them with a
    native Save dialog (browsers download them directly instead)."""

    def __init__(self, keep=5, max_bytes=256 * 1024 * 1024):
        self.keep = keep
        self.max_bytes = max_bytes
        self.items = {}
        self.lock = threading.Lock()

    def add(self, name, data):
        key = secrets.token_urlsafe(12)
        with self.lock:
            self.items[key] = (name, data)
            # Old results go when there are too many or they add up to too
            # much memory (a few big merges would otherwise stay resident
            # for the whole session); the newest one is always kept.
            while len(self.items) > 1 and (
                    len(self.items) > self.keep
                    or sum(len(d) for _, d in self.items.values()) > self.max_bytes):
                self.items.pop(next(iter(self.items)))
        return key

    def get(self, key):
        with self.lock:
            return self.items.get(key)


class WorkFolders:
    """The temporary folders of runs in progress (the uploads and partial
    results of a job). A run removes its own folder when it ends; the ones
    still here when the app closes belong to jobs cut short, and are
    removed then rather than left in the temporary folder."""

    def __init__(self):
        self.items = set()
        self.lock = threading.Lock()

    def make(self):
        path = Path(tempfile.mkdtemp(prefix="pdfm-"))
        with self.lock:
            self.items.add(path)
        return path

    def remove(self, path):
        with self.lock:
            self.items.discard(path)
        shutil.rmtree(path, ignore_errors=True)

    def clear(self):
        with self.lock:
            left = list(self.items)
        for path in left:
            self.remove(path)


class OpenFiles:
    """Files on this computer the reader may load: ones given on the command
    line ("Open with…") or picked in the desktop Open dialog. The page can
    only ask for these by id, never for an arbitrary path."""

    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()

    def add(self, path):
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"no such file: {path}")
        key = secrets.token_urlsafe(9)
        with self.lock:
            self.items[key] = path
        return key

    def get(self, key):
        with self.lock:
            return self.items.get(key)

    def find(self, path):
        """Return the stored resolved Path matching ``path``, or None."""
        try:
            wanted = Path(path).expanduser().resolve()
        except (OSError, ValueError, TypeError):
            return None
        with self.lock:
            for known in self.items.values():
                if known == wanted:
                    return known
        return None

    def knows(self, path):
        """Whether ``path`` is a file the app has open or has saved: only
        those may be shown in the file manager."""
        return self.find(path) is not None


class UnsavedWork:
    """What the app's pages say is unsaved: the name of each reader tab with
    the dot (changes not saved to a file, or a tool's result not saved
    anywhere). Kept here so closing the desktop window can ask without
    asking the page (see may_close): from pywebview's closing event the
    page can't be asked (evaluate_js there can hang the window on Windows)."""

    MAX = 200

    def __init__(self):
        self._lock = threading.Lock()
        self._pages = {}  # page id -> names

    def said(self, page, names):
        names = [str(n)[:260] for n in names][:self.MAX]
        with self._lock:
            if names:
                self._pages[str(page)] = names
            else:
                self._pages.pop(str(page), None)

    def gone(self, page):
        """The page was closed or reloaded."""
        with self._lock:
            self._pages.pop(str(page), None)

    def names(self):
        with self._lock:
            return [name for names in self._pages.values() for name in names]


class SavedOver:
    """What a file held before Save wrote over it, for Undo: the last few,
    in memory, each up to LIMIT bytes (bigger files get no Undo)."""

    LIMIT = 64 * 2**20
    KEEP = 4

    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()

    def add(self, path, data, stamp):
        """Keep ``data`` (what ``path`` held); ``stamp`` is the file's stamp
        after the save, so Undo can tell whether it has changed since."""
        if data is None or len(data) > self.LIMIT:
            return None
        key = secrets.token_urlsafe(9)
        with self.lock:
            self.items[key] = (Path(path), data, stamp)
            while len(self.items) > self.KEEP:
                self.items.pop(next(iter(self.items)))
        return key

    def pop(self, key):
        with self.lock:
            return self.items.pop(key, None)


class Events:
    """Messages for the app's pages, numbered; pages ask for the ones after
    the last they've seen and wait for new ones."""

    MAX_ITEMS = 500

    def __init__(self):
        self.items = []
        self._offset = 0
        self.cond = threading.Condition()

    def push(self, event):
        with self.cond:
            self.items.append(event)
            if len(self.items) > self.MAX_ITEMS:
                drop = len(self.items) - self.MAX_ITEMS
                del self.items[:drop]
                self._offset += drop
            self.cond.notify_all()

    def wait(self, after, timeout=20):
        with self.cond:
            total = self._offset + len(self.items)
            if total <= after:
                self.cond.wait(timeout)
                total = self._offset + len(self.items)
            start = max(0, after - self._offset)
            return self.items[start:], total


class RecentFiles:
    """PDFs recently opened in the reader from this computer, newest first,
    kept in recent.json in the app's data folder."""

    LIMIT = 30
    lock = threading.Lock()

    @staticmethod
    def _file():
        return gui_hook("data_dir", data_dir)() / "recent.json"

    @classmethod
    def load(cls):
        if not privacy.remembering():
            return []
        try:
            items = json.loads(cls._file().read_text(encoding="utf-8"))
            return [i for i in items if isinstance(i, dict) and i.get("path")]
        except (OSError, ValueError):
            return []

    @classmethod
    def _save(cls, items):
        if not privacy.remembering():
            return
        try:
            write_private(cls._file(), json.dumps(items[:cls.LIMIT], indent=1))
        except OSError:
            pass  # a recent-files list isn't worth failing over

    @staticmethod
    def _stamp(path):
        try:
            st = Path(path).stat()
            return [st.st_size, st.st_mtime_ns]
        except OSError:
            return None

    @classmethod
    def add(cls, path):
        """To the top of the list: opened now. A file the reader couldn't
        open last time, unchanged since, stays where it is (it may open
        now; see failed())."""
        path = str(Path(path).resolve())
        with cls.lock:
            items = cls.load()
            had = next((i for i in items if os.path.normcase(i["path"]) == os.path.normcase(path)), None)
            if had and had.get("failed") and had["failed"] == cls._stamp(path):
                return
            items = [i for i in items if i is not had]
            items.insert(0, {"path": path, "opened": time.time(), "before": had.get("opened") if had else 0})
            cls._save(items)

    @classmethod
    def failed(cls, path):
        """The reader couldn't open the file: say so in Recent (until the
        file changes) and put it back where it was before this open."""
        path = str(Path(path).resolve())
        with cls.lock:
            items = cls.load()
            had = next((i for i in items if os.path.normcase(i["path"]) == os.path.normcase(path)), None)
            if had is None:
                had = {"path": path, "before": 0}
                items.append(had)
            had["failed"] = cls._stamp(path)
            if "before" in had:
                had["opened"] = had.pop("before") or 0
            items.sort(key=lambda i: i.get("opened") or 0, reverse=True)
            cls._save(items)

    @classmethod
    def is_failed(cls, item):
        return bool(item.get("failed")) and item["failed"] == cls._stamp(item["path"])

    @classmethod
    def forget(cls, path=None):
        """Drop one file (or all of them); returns the entries dropped, for restore()."""
        with cls.lock:
            items = cls.load()
            cls._save([] if path is None else [i for i in items if i["path"] != path])
            return items if path is None else [i for i in items if i["path"] == path]

    @classmethod
    def restore(cls, entries):
        """Put entries that forget() dropped back, in their places (newest first)."""
        good = [{"path": str(e["path"]), "opened": float(e.get("opened") or 0)}
                for e in entries if isinstance(e, dict) and e.get("path")]
        with cls.lock:
            items = cls.load()
            have = {os.path.normcase(i["path"]) for i in items}
            items += [e for e in good if os.path.normcase(e["path"]) not in have]
            items.sort(key=lambda i: i.get("opened") or 0, reverse=True)
            cls._save(items)


def _page_csp(allow_eval):
    """Content-Security-Policy for the app page. pdf.js needs workers,
    WebAssembly (JPEG 2000 / JBIG2 decoders) and fonts built from data;
    pywebview's bridge additionally needs eval() in the desktop window."""
    scripts = "'self' 'unsafe-inline' 'wasm-unsafe-eval'" + (" 'unsafe-eval'" if allow_eval else "")
    return ("default-src 'self'; "
            f"script-src {scripts}; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; "
            "worker-src 'self' blob:; connect-src 'self' data: blob:; "
            "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def _parse_multipart(content_type, body):
    """Return (fields, files) where files is a list of (field name, filename, bytes).

    A direct split of the browser's multipart/form-data body: the email
    parser took seconds and many times the upload's size in memory (5.6 s
    and 2.4 GB for a 277 MB PDF). Uploaded files come back as read-only
    views of ``body``, so they cost no copy."""
    match = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', content_type or "")
    if not content_type.lower().startswith("multipart/") or not match:
        raise UserError("expected a form upload")
    delimiter = b"--" + (match.group(1) or match.group(2)).encode("latin-1")
    view = memoryview(body).toreadonly()
    fields, files = {}, []
    start = body.find(delimiter)
    if start < 0:
        raise UserError("expected a form upload")
    while True:
        start += len(delimiter)
        if body[start:start + 2] == b"--":  # the closing delimiter
            break
        head_end = body.find(b"\r\n\r\n", start)
        end = body.find(b"\r\n" + delimiter, head_end + 4 if head_end >= 0 else start)
        if head_end < 0 or end < 0:
            raise UserError("the upload was cut short; try again")
        headers = {}
        for line in body[start:head_end].decode("utf-8", "replace").split("\r\n"):
            key, sep, value = line.partition(":")
            if sep:
                headers[key.strip().lower()] = value.strip()
        params = {}
        for key, quoted, plain in _PARAM.findall(";" + headers.get("content-disposition", "").partition(";")[2]):
            value = re.sub(r"\\(.)", r"\1", quoted) if quoted else plain
            # Browsers write ", CR and LF in names as %22, %0D and %0A.
            params[key.lower()] = re.sub(r"%(22|0D|0A)", lambda m: chr(int(m.group(1), 16)), value,
                                         flags=re.IGNORECASE)
        if "filename*" in params and "'" in params["filename*"]:  # RFC 5987: UTF-8''name
            charset, _, encoded = params["filename*"].partition("'")
            params["filename"] = urllib.parse.unquote(encoded.partition("'")[2],
                                                      encoding=charset or "utf-8", errors="replace")
        data = view[head_end + 4:end]
        if "filename" in params:
            files.append((params.get("name"), params["filename"], data))
        else:
            fields[params.get("name")] = bytes(data).decode("utf-8")
        start = end + 2
    return fields, files


def _file_stamp(path):
    """Changes whenever the file does (its time and size)."""
    info = Path(path).stat()
    return f"{info.st_mtime_ns}-{info.st_size}"


def _byte_range(header, size):
    """(first, last) byte of a one-range ``Range`` header for a file of
    ``size`` bytes; None if the range is outside the file or not one we serve."""
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not match or not (match.group(1) or match.group(2)) or size == 0:
        return None
    if match.group(1):
        start = int(match.group(1))
        end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
    else:  # the last n bytes
        start, end = max(0, size - int(match.group(2))), size - 1
    return (start, end) if start <= end and start < size else None


def _file_mentions(path, needle):
    """Whether the bytes ``needle`` occur in the file (read in pieces, so a
    big file isn't held in memory)."""
    keep = len(needle) - 1
    tail = b""
    try:
        with Path(path).open("rb") as f:
            while True:
                chunk = f.read(1 << 22)
                if not chunk:
                    return False
                if needle in chunk or needle in tail + chunk[:keep]:
                    return True
                tail = chunk[-keep:] if keep else b""
    except OSError:
        return False


def _safe_name(name):
    name = Path(name.replace("\\", "/")).name  # drop any path the browser sent
    return re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .") or "file"


def _download_headers(name):
    """Content-Disposition and X-Pdfm-Filename for a file called ``name``.
    HTTP headers only carry latin-1, and names like "John’s report.pdf"
    or "Отчёт.pdf" don't fit: the real name goes percent-encoded (the
    filename*= form of RFC 6266; the page decodes X-Pdfm-Filename with
    decodeURIComponent) next to a plain ASCII stand-in for old clients."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    plain = re.sub(r'[\\"\x00-\x1f]', "", plain).strip()
    if not plain or plain.startswith("."):  # nothing of the name survived
        plain = "file" + Path(name).suffix
    encoded = urllib.parse.quote(name, safe="")
    return {"Content-Disposition": f'attachment; filename="{plain}"; filename*=UTF-8\'\'{encoded}',
            "X-Pdfm-Filename": encoded}


def _log(message):
    """What the app does around starting and closing, in its log (see
    applog.py; names of files are scrubbed out there)."""
    applog.log.info(message)


def _thread_stacks():
    import traceback

    names = {t.ident: t.name for t in threading.enumerate()}
    return "\n".join(f"--- {names.get(ident, ident)}\n" + "".join(traceback.format_stack(frame))
                     for ident, frame in sys._current_frames().items())


def _proof(secret, purpose, nonce):
    """HMAC proof that a side knows the instance secret."""
    return hmac.new(secret.encode(), f"{purpose} {nonce}".encode(), hashlib.sha256).hexdigest()


def _replace_file(path, data):
    """Write ``data`` over ``path``: to a temporary file next to it first,
    then swapped in, so a failure halfway never leaves a broken file."""
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.zpdf-saving")
    try:
        temp.write_bytes(data)
        try:
            shutil.copymode(path, temp)
        except OSError:
            pass
        os.replace(temp, path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise UserError(f"could not save {path.name}: it may be open in another program, "
                        f"or read-only ({exc.strerror or exc})") from None


def reveal_in_file_manager(path):
    import subprocess
    import sys

    path = Path(path)
    if sys.platform == "win32":
        explorer = str(Path(os.environ.get("SystemRoot") or r"C:\Windows") / "explorer.exe")
        subprocess.Popen([explorer, "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if path.is_file() else path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class HandlerBase(BaseHTTPRequestHandler):
    server_version = f"pdfm/{__version__}"
    protocol_version = "HTTP/1.1"  # keep connections open between requests
    token = ""
    allowed_hosts = set()
    results = None  # ResultCache
    jobs = None  # progress.JobStore
    pages = None  # open pages: id -> when last heard from
    events = None  # Events for the pages
    instance_secret = None  # see forward_to_running_app
    instance_mode = None
    instance_extra = None
    bring_to_front = None
    quit_app = None  # closes the pywebview window (to restart after an update)
    quit_requested = False  # makes wait_until_closed return
    background = None  # background.InBackground: the window hidden, ZPDF in the notification area
    unsaved = None  # UnsavedWork: what closing the desktop window asks about
    opened = None  # OpenFiles
    saved_over = None  # SavedOver: Undo after Save wrote over a file
    scans = None  # scanpdf.ScanBoxes: the pages of each Scan page under way
    work = None  # WorkFolders
    # pywebview's JavaScript bridge needs eval(); only allowed in the desktop window.
    allow_eval = False


    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _send(self, status, body, ctype="application/json", headers=None, cache=False):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self._start(status, ctype, len(body), headers, cache)
        self.wfile.write(body)

    def _start(self, status, ctype, length, headers=None, cache=False):
        """The status line and headers of an answer ``length`` bytes long."""
        # Headers can only carry latin-1 (see _download_headers): check them
        # before the status line is written, so a bad one can't leave a
        # half-sent answer that a second status line then gets appended to.
        for k, v in (headers or {}).items():
            try:
                f"{k}: {v}".encode("latin-1")
            except UnicodeEncodeError:
                raise RuntimeError(f"the {k} header can't be sent as it is") from None
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        # Static files may be cached but are checked (cheaply, by ETag) on every
        # load, so an updated app never runs old code.
        self.send_header("Cache-Control", "no-cache" if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _error(self, status, message):
        # The request body may not have been read; don't reuse the connection.
        self.close_connection = True
        self._send(status, {"error": message})

    def _host_ok(self):
        host = self.headers.get("Host", "")
        if host in self.allowed_hosts:
            return True
        return bool(getattr(self, "lan_enabled", False)) and _is_lan_host(host)


    def _token_ok(self):
        ok = secrets.compare_digest(self.headers.get("X-Pdfm-Token", ""), self.token)
        if ok:
            now = gui_hook("time", time).monotonic()
            type(self).last_seen = now  # a page is still open
            page = self.headers.get("X-Pdfm-Page", "")[:64]
            if page and self.pages is not None:
                self.pages[page] = now
        return ok

    def _static(self, rel):
        """Serve the app's own static files (the reader and pdf.js)."""
        parts = rel.split("/")
        if any(p in ("", ".", "..") or "\\" in p or ":" in p for p in parts):
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        cached = _STATIC_CACHE.get(rel)
        if cached is None:
            target = resources.files("pdfmanager").joinpath("static", *parts)
            if not target.is_file():
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            data = target.read_bytes()
            etag = '"%s"' % hashlib.blake2b(data, digest_size=8).hexdigest()
            gz = None
            ext = Path(rel).suffix.lower()
            if len(data) >= 4096 and ext in (".js", ".mjs", ".css", ".svg", ".json"):
                import gzip
                gz_bytes = gzip.compress(data, compresslevel=6)
                if len(gz_bytes) < len(data) * 0.9:
                    gz = gz_bytes
            cached = _STATIC_CACHE[rel] = (data, etag, gz)
        if len(cached) == 2:  # backwards compat if tests inject 2-tuples
            data, etag = cached
            gz = None
        else:
            data, etag, gz = cached
        if etag in self.headers.get("If-None-Match", ""):
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return None
        ctype = STATIC_TYPES.get(Path(rel).suffix.lower(), "application/octet-stream")
        if gz is not None and "gzip" in self.headers.get("Accept-Encoding", "").lower():
            return self._send(
                HTTPStatus.OK, gz, ctype,
                {"ETag": etag, "Content-Encoding": "gzip", "Vary": "Accept-Encoding"},
                cache=True,
            )
        return self._send(HTTPStatus.OK, data, ctype, {"ETag": etag}, cache=True)

    def _opened_path(self, key):
        """The file opened from this computer under id ``key`` (see
        OpenFiles): what a request works on when the page names the file
        (field "id") instead of sending it back."""
        path = self.opened.get(key) if self.opened is not None else None
        if path is None or not path.is_file():
            raise UserError("that file is no longer available")
        return path

    def _job_or_now(self, func):
        if self.headers.get("X-Pdfm-Async") == "1":
            job = progress.Job()
            job.expected = EXPECTED_ERRORS
            self.jobs.add(job).start(func)
            return self._send(HTTPStatus.OK, {"job": job.id})
        return self._send_result(func())

    def _send_result(self, result):
        if "json" in result:
            return self._send(HTTPStatus.OK, result["json"])
        name, data, ctype, notes, keep = result["file"]
        headers = gui_hook("_download_headers", _download_headers)(name)
        if notes:
            headers["X-Pdfm-Note"] = urllib.parse.quote("\n".join(notes))
        if keep and self.results is not None:
            headers["X-Pdfm-Result-Id"] = self.results.add(name, data)
        return self._send(HTTPStatus.OK, data, ctype, headers)

    def _job(self, path, method):
        """GET /api/job/<id>, GET /api/job/<id>/result, POST /api/job/<id>/cancel."""
        job_id, _, action = path[len("/api/job/"):].partition("/")
        job = self.jobs.get(job_id)
        if job is None:
            return self._error(HTTPStatus.NOT_FOUND, "that job is no longer available")
        if method == "POST" and action == "cancel":
            job.cancel()
            return self._send(HTTPStatus.OK, job.snapshot())
        if method == "GET" and action == "result":
            if job.state == "failed":
                self.jobs.pop(job_id)
                return self._error(HTTPStatus.BAD_REQUEST, job.error)
            if job.state != "done":
                return self._error(HTTPStatus.CONFLICT, f"the job is {job.state}")
            # Forgotten only once the page has the file: if sending it
            # fails (the window was closed halfway), the page can ask again.
            self._send_result(job.result)
            self.jobs.pop(job_id)
            return None
        if method == "GET" and not action:
            return self._send(HTTPStatus.OK, job.snapshot())
        return self._error(HTTPStatus.NOT_FOUND, "not found")

    def _keep(self, body):
        """Hold a file (e.g. the reader's copy with filled-in forms) so the
        desktop window can save it with the native Save dialog."""
        _, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads or self.results is None:
            raise UserError("no file")
        _, filename, data = uploads[0]
        return self._send(HTTPStatus.OK, {"id": self.results.add(_safe_name(filename), bytes(data))})

    def _unsaved(self, body):
        """{"page": id, "names": [...]}: this page's unsaved tabs now."""
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            data = None
        names = data.get("names") if isinstance(data, dict) else None
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            return self._error(HTTPStatus.BAD_REQUEST, "names must be a list of file names")
        page = str(data.get("page") or self.headers.get("X-Pdfm-Page") or "")
        self.unsaved.said(page, names)
        return self._send(HTTPStatus.OK, {"ok": True})

    def _bye(self):
        """The page is being closed (or reloaded). Sent with
        navigator.sendBeacon, which can't add headers, so the token comes
        in the query string; this request can do nothing else."""
        try:
            length = max(0, int(self.headers.get("Content-Length") or 0))
        except ValueError:
            length = 0
        if length:
            self.rfile.read(min(length, 65536))
        query = urllib.parse.parse_qs(self.path.partition("?")[2])
        if not secrets.compare_digest(query.get("token", [""])[0], self.token):
            return self._error(HTTPStatus.FORBIDDEN, "missing or wrong token")
        if self.pages is not None:
            self.pages.pop(query.get("page", [""])[0], None)
        if self.unsaved is not None:
            self.unsaved.gone(query.get("page", [""])[0])
        type(self).closed_at = gui_hook("time", time).monotonic()
        return self._send(HTTPStatus.OK, {"ok": True})

    def _instance(self, body):
        """Another launch of the app hands over the files it was asked to
        open (see forward_to_running_app). It proves it's the same user
        with the secret from instance.json in the app's data folder."""
        data = json.loads(body or b"{}")
        nonce = str(data.get("nonce", ""))
        if not self.instance_secret or not nonce or not secrets.compare_digest(
                str(data.get("proof", "")), _proof(self.instance_secret, "open", nonce)):
            return self._error(HTTPStatus.FORBIDDEN, "not allowed")
        proof = _proof(self.instance_secret, "answer", nonce)
        if data.get("quiet"):  # "are you running?": yes, and nothing more
            return self._send(HTTPStatus.OK, {"handled": True, "proof": proof})
        action = data.get("action")
        if action is not None:
            return self._instance_action(action, data.get("paths") or [], proof)
        ids = []
        for path in data.get("paths") or []:
            try:
                ids.append(self.opened.add(path))
            except (OSError, ValueError):
                continue
        if self.instance_mode == "desktop":
            self.events.push({"open": ids})
            raise_window = type(self).bring_to_front  # a plain function, not a method
            if raise_window:
                # Not before answering: pywebview waits for the window's own
                # thread, and the other launch only waits a few seconds
                # before opening a second copy instead.
                threading.Thread(target=raise_window, daemon=True).start()
            return self._send(HTTPStatus.OK, {"handled": True, "proof": proof})
        url = f"http://127.0.0.1:{self.server.server_address[1]}/#token={self.token}"
        if ids:
            url += "&open=" + ",".join(ids)
        return self._send(HTTPStatus.OK, {"url": url, "mode": self.instance_mode, "proof": proof})

    def _instance_action(self, action, paths, proof):
        """Files for a right-click action (see quickactions.py): they join the
        batch of that action still gathering files, or start one. A new batch
        is shown by the app's own window (an event), else by a new window on
        it; files joining a batch need nothing more."""
        if action not in quickactions.ACTIONS or not isinstance(paths, list):
            return self._error(HTTPStatus.BAD_REQUEST, "bad request")
        batch, new = self.actions.add(action, [Path(str(p)) for p in paths])
        gui_hook("_log", _log)(f"action {action}: {len(paths)} file(s), batch {batch.id}{' (new)' if new else ''}")
        if self.instance_mode == "desktop":
            if new:
                self.events.push({"action": batch.id})
                raise_window = type(self).bring_to_front
                if raise_window:
                    threading.Thread(target=raise_window, daemon=True).start()
            return self._send(HTTPStatus.OK, {"handled": True, "proof": proof})
        if not new:
            return self._send(HTTPStatus.OK, {"handled": True, "proof": proof})
        url = f"http://127.0.0.1:{self.server.server_address[1]}/#token={self.token}&action={batch.id}"
        return self._send(HTTPStatus.OK, {"url": url, "mode": self.instance_mode, "proof": proof})

    def _action(self, rest):
        """GET /api/action/<id>: how a right-click batch is getting on;
        /api/action/<id>/file/<n>: one of its results (given once)."""
        batch_id, _, more = rest.partition("/")
        actions = getattr(self, "actions", None)
        batch = actions.get(batch_id) if actions is not None else None
        if batch is None:
            return self._error(HTTPStatus.NOT_FOUND, "that action is no longer available")
        if not more:
            return self._send(HTTPStatus.OK, batch.snapshot())
        kind, _, n = more.partition("/")
        item = batch.outputs.pop(int(n), None) if kind == "file" and n.isdigit() else None
        if item is None:
            return self._error(HTTPStatus.NOT_FOUND, "that result is no longer available")
        name, data = item
        return self._send(HTTPStatus.OK, data, "application/pdf", gui_hook("_download_headers", _download_headers)(name))

    def _events(self):
        """Long poll: events for the page (files handed over by another
        launch), after the one numbered ``after``."""
        query = urllib.parse.parse_qs(self.path.partition("?")[2])
        try:
            after = int(query.get("after", ["0"])[0])
        except ValueError:
            after = 0
        mode = type(self).instance_mode
        if mode is not None and mode != "desktop":
            # Only the app's own window gets events; other kinds open a new
            # window instead. Answer at once so the page stops asking (a
            # browser allows few connections per site).
            return self._send(HTTPStatus.OK, {"events": [], "last": after, "none": True})
        events, last = self.events.wait(after, timeout=20)
        return self._send(HTTPStatus.OK, {"events": events, "last": last})

    def _can_restart(self):
        """Can this copy close itself and start again (after an update)?"""
        mode = type(self).instance_mode
        return mode in ("app-window", "browser") or (mode == "desktop" and type(self).quit_app is not None)

    def _restart(self):
        """Start the app again: a helper waits for this copy to end."""
        cls = type(self)
        if not self._can_restart():
            raise UserError("close ZPDF and open it again to use the new version")
        update.restart_after(os.getpid())
        cls.quit_requested = True
        gui_hook("_log", _log)(f"restart asked for; mode {cls.instance_mode}")
        if cls.instance_mode == "desktop":
            threading.Timer(0.5, cls.quit_app).start()  # once this answer has been sent
        return self._send(HTTPStatus.OK, {"restarting": True})
