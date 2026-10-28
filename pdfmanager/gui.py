"""A small local web app: ``pdfm gui`` serves a page on 127.0.0.1 and opens it
in the browser. Uses only the standard library.

Uploads are processed in a temporary folder and the result is sent back as
a download. Folder tools (organize, rename, duplicates) work on paths on
this computer. Every API call must carry the random token printed at
start-up, and only localhost Host headers are accepted, so other web pages
and other machines can't drive the server.
"""

import email.parser
import email.policy
import io
import json
import re
import secrets
import shutil
import tempfile
import threading
import webbrowser
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from pdfmanager import __version__, core, organize
from pdfmanager.pageranges import PageRangeError

MAX_UPLOAD = 1024 * 1024 * 1024  # 1 GB

PAPER = ["a4", "letter", "legal", "a3", "a5", "a6", "tabloid", "executive"]


def _f(key, label, kind="text", default=None, **extra):
    return {"key": key, "label": label, "kind": kind, "default": default, **extra}


# Operation specs. The page builds its forms from these.
#   files: "pdf" (one or more PDFs), "pdf1" (exactly one), "pdf2" (two),
#          "images", or None (folder tools)
OPERATIONS = [
    {"id": "merge", "group": "Combine", "label": "Merge", "files": "pdf", "min": 2,
     "help": "Combine PDFs in the order listed. Drag to reorder.",
     "fields": [_f("bookmarks", "Add a bookmark per file", "check", True)]},
    {"id": "split", "group": "Combine", "label": "Split", "files": "pdf",
     "help": "Split into several files (downloaded as a ZIP).",
     "fields": [_f("every", "Pages per file", "number", 1, min=1),
                _f("ranges", "…or ranges (one file each)", "text", "",
                   placeholder="1-3, 4-10, 11-", hint="separate ranges with spaces or ;")]},
    {"id": "interleave", "group": "Combine", "label": "Interleave scans", "files": "pdf2",
     "help": "Combine separately scanned front sides (first file) and back sides "
             "(second file).",
     "fields": [_f("reverse_back", "Back sides are in reverse order", "check", True)]},
    {"id": "extract", "group": "Pages", "label": "Extract pages", "files": "pdf",
     "fields": [_f("pages", "Pages", "text", "", placeholder="1-3,5", required=True)]},
    {"id": "delete", "group": "Pages", "label": "Delete pages", "files": "pdf",
     "fields": [_f("pages", "Pages", "text", "", placeholder="2,4-6", required=True)]},
    {"id": "reorder", "group": "Pages", "label": "Reorder pages", "files": "pdf",
     "fields": [_f("order", "New order", "text", "", placeholder="3,1,2,4-", required=True)]},
    {"id": "reverse", "group": "Pages", "label": "Reverse order", "files": "pdf", "fields": []},
    {"id": "rotate", "group": "Pages", "label": "Rotate", "files": "pdf",
     "fields": [_f("degrees", "Rotation", "select", "90",
                   choices=[["90", "90° clockwise"], ["180", "180°"],
                            ["270", "90° counter-clockwise"]]),
                _f("pages", "Pages (blank = all)", "text", "", placeholder="all")]},
    {"id": "resize", "group": "Layout", "label": "Resize", "files": "pdf",
     "fields": [_f("size", "Paper size", "select", "a4",
                   choices=[[p, p.upper() if p[0] == "a" else p.capitalize()] for p in PAPER]
                   + [["custom", "Custom…"], ["scale", "Scale by factor…"]]),
                _f("custom", "Custom size", "text", "", placeholder="210x297mm or 8.5x11in",
                   show_if={"size": "custom"}),
                _f("scale", "Scale factor", "number", 1.0, min=0.05, step=0.05,
                   show_if={"size": "scale"}),
                _f("fit", "Fit", "select", "fit",
                   choices=[["fit", "Fit (keep proportions)"], ["fill", "Fill (may crop)"],
                            ["stretch", "Stretch"], ["none", "Don't scale"]]),
                _f("auto_orient", "Keep landscape pages landscape", "check", True),
                _f("pages", "Pages (blank = all)", "text", "", placeholder="all")]},
    {"id": "crop", "group": "Layout", "label": "Crop margins", "files": "pdf",
     "fields": [_f("top", "Top (mm)", "number", 10, min=0),
                _f("right", "Right (mm)", "number", 10, min=0),
                _f("bottom", "Bottom (mm)", "number", 10, min=0),
                _f("left", "Left (mm)", "number", 10, min=0),
                _f("pages", "Pages (blank = all)", "text", "", placeholder="all")]},
    {"id": "nup", "group": "Layout", "label": "Pages per sheet", "files": "pdf",
     "fields": [_f("per_sheet", "Pages per sheet", "select", "2",
                   choices=[[str(n), str(n)] for n in (2, 4, 6, 8, 9)]),
                _f("size", "Sheet size", "select", "a4",
                   choices=[[p, p.upper() if p[0] == "a" else p.capitalize()] for p in PAPER])]},
    {"id": "compress", "group": "Optimize", "label": "Compress", "files": "pdf",
     "fields": [_f("level", "Compression", "select", "medium",
                   choices=[["low", "Low — lossless"],
                            ["medium", "Medium — recommended"],
                            ["high", "High — smallest file"]],
                   hint="Low keeps every image as is. Medium reduces images to 150 DPI "
                        "(good for screen and office printing); High to 96 DPI.")]},
    {"id": "color", "group": "Optimize", "label": "Convert colours", "files": "pdf",
     "fields": [_f("to", "Convert to", "select", "gray",
                   choices=[["gray", "Grayscale"], ["rgb", "RGB (screen)"],
                            ["cmyk", "CMYK (print)"]]),
                _f("icc", "ICC profile (optional)", "file", None, accept=".icc,.icm")]},
    {"id": "ocr", "group": "Optimize", "label": "Make searchable (OCR)", "files": "pdf",
     "fields": [_f("language", "Language(s)", "text", "eng", placeholder="eng+deu"),
                _f("deskew", "Straighten crooked scans", "check", False),
                _f("force", "Redo pages that already have text", "check", False)]},
    {"id": "watermark", "group": "Stamp", "label": "Watermark", "files": "pdf",
     "fields": [_f("text", "Text", "text", "CONFIDENTIAL", required=True),
                _f("opacity", "Opacity", "number", 0.15, min=0.01, max=1, step=0.05),
                _f("font_size", "Font size", "number", 60, min=4),
                _f("angle", "Angle", "number", 45),
                _f("below", "Put under the content", "check", False),
                _f("pages", "Pages (blank = all)", "text", "", placeholder="all")]},
    {"id": "number", "group": "Stamp", "label": "Page numbers", "files": "pdf",
     "fields": [_f("format", "Format", "text", "{n} / {total}",
                   hint="{n} = page number, {total} = page count"),
                _f("position", "Position", "select", "bottom-center",
                   choices=[[p, p.replace("-", " ").capitalize()] for p in
                            ("bottom-center", "bottom-right", "bottom-left",
                             "top-center", "top-right", "top-left")]),
                _f("start", "Start at", "number", 1),
                _f("font_size", "Font size", "number", 10, min=4)]},
    {"id": "metadata", "group": "Stamp", "label": "Title & author", "files": "pdf",
     "fields": [_f("title", "Title"), _f("author", "Author"), _f("subject", "Subject"),
                _f("keywords", "Keywords")]},
    {"id": "encrypt", "group": "Security", "label": "Add password", "files": "pdf",
     "fields": [_f("new_password", "New password", "password", "", required=True)]},
    {"id": "decrypt", "group": "Security", "label": "Remove password", "files": "pdf",
     "help": "Enter the current password in the box above the Run button.", "fields": []},
    {"id": "from-images", "group": "Convert", "label": "Images → PDF", "files": "images",
     "fields": [_f("size", "Page size", "select", "",
                   choices=[["", "Same as image"]] +
                   [[p, p.upper() if p[0] == "a" else p.capitalize()] for p in PAPER]),
                _f("margin", "Margin (mm)", "number", 0, min=0)]},
    {"id": "to-images", "group": "Convert", "label": "PDF → images", "files": "pdf",
     "fields": [_f("format", "Format", "select", "png", choices=[["png", "PNG"], ["jpg", "JPG"]]),
                _f("dpi", "Resolution (DPI)", "number", 150, min=36, max=600),
                _f("pages", "Pages (blank = all)", "text", "", placeholder="all")]},
    {"id": "text", "group": "Convert", "label": "PDF → text", "files": "pdf", "fields": []},
    {"id": "organize", "group": "Folders", "label": "Organize folder", "files": None,
     "help": "Sort the PDFs in a folder on this computer into sub-folders.",
     "fields": [_f("directory", "Folder", "text", "", placeholder="/Users/me/Downloads",
                   required=True),
                _f("dest", "Put sub-folders in (blank = same folder)", "text", ""),
                _f("by", "Group by", "select", "year",
                   choices=[[g, g.replace("-", " ").capitalize()] for g in organize.GROUPINGS]),
                _f("duplicates", "Identical files", "select", "keep",
                   choices=[["keep", "Organize anyway"], ["skip", "Skip"],
                            ["folder", "Put in a Duplicates folder"]]),
                _f("move", "Move instead of copy", "check", False),
                _f("recursive", "Include sub-folders", "check", False)]},
    {"id": "rename", "group": "Folders", "label": "Rename from metadata", "files": None,
     "fields": [_f("directory", "Folder", "text", "", required=True),
                _f("pattern", "Pattern", "text", "{date:%Y-%m-%d} {title}",
                   hint="{title} {author} {pages} {date:%Y-%m-%d} {stem}"),
                _f("recursive", "Include sub-folders", "check", False)]},
    {"id": "duplicates", "group": "Folders", "label": "Find duplicates", "files": None,
     "fields": [_f("directory", "Folder", "text", "", required=True),
                _f("recursive", "Include sub-folders", "check", False),
                _f("delete", "Delete duplicates (keeps the oldest copy)", "check", False)]},
]
OPS = {op["id"]: op for op in OPERATIONS}

SUFFIX = {"extract": "extract", "delete": "edited", "reorder": "reordered",
          "reverse": "reversed", "rotate": "rotated", "resize": "resized", "crop": "cropped",
          "nup": "nup", "compress": "compressed", "color": "color", "ocr": "ocr",
          "watermark": "watermarked", "number": "numbered", "metadata": "edited",
          "encrypt": "protected", "decrypt": "unlocked"}


class UserError(Exception):
    pass


# --------------------------------------------------------------------------
# Running operations
# --------------------------------------------------------------------------

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


def _run_file_op(op, src, out_dir, opts, password, extra_files):
    """Run a single-input operation. Returns a list of output paths."""
    out = out_dir / f"{src.stem}_{SUFFIX.get(op, op)}.pdf"
    if op == "extract":
        core.select_pages(src, out, opts["pages"], password)
    elif op == "delete":
        core.delete_pages(src, out, opts["pages"], password)
    elif op == "reorder":
        core.select_pages(src, out, opts["order"], password)
    elif op == "reverse":
        core.reverse(src, out, password)
    elif op == "rotate":
        core.rotate(src, out, _num(opts.get("degrees"), int, 90), _pages(opts), password)
    elif op == "resize":
        size = opts.get("size") or "a4"
        kwargs = {}
        if size == "scale":
            kwargs["scale"] = _num(opts.get("scale"), float, 1.0)
        else:
            kwargs["size"] = opts.get("custom") if size == "custom" else size
            if not kwargs["size"]:
                raise UserError("enter a custom size such as 210x297mm")
        core.resize(src, out, fit=opts.get("fit") or "fit", pages=_pages(opts),
                    auto_orient=_bool(opts.get("auto_orient", True)), password=password,
                    **kwargs)
    elif op == "crop":
        mm = [_num(opts.get(k), float, 0) * core.POINTS_PER_MM
              for k in ("left", "bottom", "right", "top")]
        core.crop(src, out, mm, _pages(opts), password)
    elif op == "nup":
        core.n_up(src, out, _num(opts.get("per_sheet"), int, 2), opts.get("size") or "a4",
                  password)
    elif op == "compress":
        core.compress(src, out, level=opts.get("level") or "medium", password=password)
    elif op == "color":
        icc = extra_files.get("icc")
        core.convert_color(src, out, opts.get("to") or "gray", icc, password)
    elif op == "ocr":
        core.ocr(src, out, language=opts.get("language") or "eng",
                 force=_bool(opts.get("force")), deskew=_bool(opts.get("deskew")),
                 password=password)
    elif op == "watermark":
        core.watermark(src, out, text=opts.get("text") or "CONFIDENTIAL",
                       opacity=_num(opts.get("opacity"), float, 0.15),
                       font_size=_num(opts.get("font_size"), float, 60),
                       angle=_num(opts.get("angle"), float, 45),
                       below=_bool(opts.get("below")), pages=_pages(opts), password=password)
    elif op == "number":
        core.number_pages(src, out, fmt=opts.get("format") or "{n} / {total}",
                          position=opts.get("position") or "bottom-center",
                          font_size=_num(opts.get("font_size"), float, 10),
                          start=_num(opts.get("start"), int, 1), password=password)
    elif op == "metadata":
        fields = {k: opts.get(k) or None for k in ("title", "author", "subject", "keywords")}
        core.set_metadata(src, out, password, **fields)
    elif op == "encrypt":
        if not opts.get("new_password"):
            raise UserError("enter a new password")
        core.encrypt(src, out, opts["new_password"], password=password)
    elif op == "decrypt":
        if not password:
            raise UserError("enter the PDF's current password")
        core.decrypt(src, out, password)
    elif op == "split":
        ranges = [r for r in re.split(r"[;\s]+", opts.get("ranges") or "") if r]
        folder = out_dir / f"{src.stem}_split"
        return core.split(src, folder, every=None if ranges else _num(opts.get("every"), int, 1),
                          ranges=ranges or None, password=password)
    elif op == "to-images":
        return core.pdf_to_images(src, out_dir / f"{src.stem}_images",
                                  _num(opts.get("dpi"), int, 150), opts.get("format") or "png",
                                  _pages(opts), password)
    elif op == "text":
        out = out.with_suffix(".txt")
        core.extract_text(src, out, None, password)
    else:
        raise UserError(f"unknown operation {op!r}")
    return [out]


def _protect(outputs, password, op):
    """PDFs made from a password-protected file get the same password."""
    if password and op not in ("decrypt", "encrypt"):
        for path in outputs:
            if path.suffix.lower() == ".pdf":
                core.encrypt(path, path, password)


def _flatten_outputs(outputs, out_dir):
    """Move per-input outputs into ``out_dir``, renaming on clashes."""
    taken, flat = set(), []
    for sub, path in outputs:
        target = organize._unique(out_dir / path.relative_to(sub), taken)
        target.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target)
        flat.append(target)
    return flat


def run_operation(op, files, opts, password=None, extra_files=None, work=None):
    """Run ``op`` on uploaded ``files`` (paths in the temporary folder
    ``work``). Returns (filename, bytes, content type) for the download."""
    spec = OPS.get(op)
    if spec is None:
        raise UserError(f"unknown operation {op!r}")
    for field in spec["fields"]:
        if field.get("required") and not str(opts.get(field["key"]) or "").strip():
            raise UserError(f"please fill in “{field['label']}”")
    if not files:
        raise UserError("add at least one file")
    if len(files) < spec.get("min", 1):
        raise UserError(f"add at least {spec['min']} files")
    out_dir = Path(work or files[0].parent) / "out"
    out_dir.mkdir()
    extra_files = extra_files or {}

    if op == "merge":
        out = out_dir / "merged.pdf"
        core.merge(files, out, bookmarks=_bool(opts.get("bookmarks", True)), password=password)
        outputs = [out]
    elif op == "interleave":
        if len(files) != 2:
            raise UserError("add exactly two files: front sides, then back sides")
        out = out_dir / f"{files[0].stem}_interleaved.pdf"
        core.interleave(files[0], files[1], out, _bool(opts.get("reverse_back", True)), password)
        outputs = [out]
    elif op == "from-images":
        out = out_dir / (files[0].stem + ".pdf" if len(files) == 1 else "images.pdf")
        core.images_to_pdf(files, out, opts.get("size") or None,
                           _num(opts.get("margin"), float, 0) * core.POINTS_PER_MM)
        outputs = [out]
    else:
        outputs = []
        for index, src in enumerate(files):
            # Each input gets its own output folder; flatten afterwards.
            sub = out_dir / f"{index:03d}"
            sub.mkdir()
            outputs.extend((sub, p) for p in _run_file_op(op, src, sub, opts, password,
                                                           extra_files))
        outputs = _flatten_outputs(outputs, out_dir)

    _protect(outputs, password, op)
    if len(outputs) == 1:
        path = outputs[0]
        ctype = "text/plain; charset=utf-8" if path.suffix == ".txt" else "application/pdf"
        return path.name, path.read_bytes(), ctype
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in outputs:
            zf.write(path, path.relative_to(out_dir))
    name = f"{files[0].stem}_{SUFFIX.get(op, op)}.zip" if len(files) == 1 else f"pdfm_{op}.zip"
    return name, buf.getvalue(), "application/zip"


def run_folder_operation(op, opts, apply):
    """Folder tools. With ``apply`` false nothing on disk changes."""
    directory = (opts.get("directory") or "").strip()
    if not directory:
        raise UserError("enter a folder")
    directory = Path(directory).expanduser()
    recursive = _bool(opts.get("recursive"))
    if op == "organize":
        plan = organize.organize(directory, opts.get("by") or "year",
                                 Path(opts["dest"]).expanduser() if opts.get("dest") else None,
                                 _bool(opts.get("move")), recursive, dry_run=not apply,
                                 duplicates=opts.get("duplicates") or "keep")
        rows = [[str(s), str(d) if d else "skipped (duplicate)"] for s, d in plan]
    elif op == "rename":
        plan = organize.rename(directory, opts.get("pattern") or "{date:%Y-%m-%d} {title}",
                               recursive, dry_run=not apply)
        rows = [[str(s), str(d)] for s, d in plan]
    elif op == "duplicates":
        rows = []
        for keep, *extra in organize.find_duplicates(directory, recursive):
            for path in extra:
                if apply and _bool(opts.get("delete")):
                    path.unlink()
                    rows.append([str(path), f"deleted (same as {keep.name})"])
                else:
                    rows.append([str(path), f"duplicate of {keep}"])
    else:
        raise UserError(f"unknown operation {op!r}")
    return {"rows": rows, "applied": apply}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def _parse_multipart(content_type, body):
    """Return (fields, files) where files is a list of (field name, filename, bytes)."""
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    if not message.is_multipart():
        raise UserError("expected a form upload")
    fields, files = {}, []
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        data = part.get_payload(decode=True) or b""
        if filename is not None:
            files.append((name, filename, data))
        else:
            fields[name] = data.decode("utf-8")
    return fields, files


def _safe_name(name):
    name = Path(name.replace("\\", "/")).name  # drop any path the browser sent
    return re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .") or "file"


class Handler(BaseHTTPRequestHandler):
    server_version = f"pdfm/{__version__}"
    token = ""
    allowed_hosts = set()

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    # -- helpers -----------------------------------------------------------

    def _send(self, status, body, ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        self._send(status, {"error": message})

    def _host_ok(self):
        return self.headers.get("Host", "") in self.allowed_hosts

    def _token_ok(self):
        return secrets.compare_digest(self.headers.get("X-Pdfm-Token", ""), self.token)

    # -- routes ------------------------------------------------------------

    def do_GET(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad host")
        path = self.path.split("?", 1)[0]
        if path == "/":
            page = resources.files("pdfmanager").joinpath("gui.html").read_text("utf-8")
            return self._send(HTTPStatus.OK, page, "text/html; charset=utf-8", {
                "Content-Security-Policy":
                    "default-src 'self'; style-src 'unsafe-inline'; "
                    "script-src 'unsafe-inline'; img-src 'self' data: blob:",
            })
        if path == "/api/operations":
            if not self._token_ok():
                return self._error(HTTPStatus.FORBIDDEN, "missing or wrong token")
            return self._send(HTTPStatus.OK, {"operations": OPERATIONS, "version": __version__})
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad host")
        if not self._token_ok():
            return self._error(HTTPStatus.FORBIDDEN, "missing or wrong token")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "upload too large (1 GB max)")
        body = self.rfile.read(length)
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/run":
                return self._run(body)
            if path == "/api/folder":
                data = json.loads(body or b"{}")
                result = run_folder_operation(data.get("op"), data.get("options") or {},
                                              bool(data.get("apply")))
                return self._send(HTTPStatus.OK, result)
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        except (UserError, core.PdfManagerError, PageRangeError, ValueError,
                FileNotFoundError, NotADirectoryError) as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # report, don't kill the server
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR,
                               f"{type(exc).__name__}: {exc}")

    def _run(self, body):
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        op = fields.pop("op", "")
        password = fields.pop("password", "") or None
        work = Path(tempfile.mkdtemp(prefix="pdfm-"))
        try:
            files, extra = [], {}
            for index, (name, filename, data) in enumerate(uploads):
                # One folder per upload keeps original names without clashes.
                folder = work / "in" / f"{index:03d}"
                folder.mkdir(parents=True)
                path = folder / _safe_name(filename)
                path.write_bytes(data)
                if name == "files":
                    files.append(path)
                elif data:  # extra file field, e.g. an ICC profile
                    extra[name] = path
            name, data, ctype = run_operation(op, files, fields, password, extra, work)
            quoted = name.replace('"', "")
            return self._send(HTTPStatus.OK, data, ctype, {
                "Content-Disposition": f'attachment; filename="{quoted}"',
                "X-Pdfm-Filename": quoted,
            })
        finally:
            shutil.rmtree(work, ignore_errors=True)


def make_server(port=0):
    """Create (but don't start) the server. Returns (server, url)."""
    token = secrets.token_urlsafe(24)
    handler = type("PdfmHandler", (Handler,), {"token": token})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    real_port = server.server_address[1]
    handler.allowed_hosts = {f"127.0.0.1:{real_port}", f"localhost:{real_port}"}
    return server, f"http://127.0.0.1:{real_port}/#token={token}"


def serve(port=0, open_browser=True):
    server, url = make_server(port)
    print(f"PDF Manager is running at {url}")
    print("Keep this window open while you use it; press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
