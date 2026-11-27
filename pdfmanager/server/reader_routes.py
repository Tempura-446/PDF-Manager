# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Reader HTTP route handlers (file streaming, save/undo, layers, text edit, OCR fix, markups, comments)."""

import logging
import json
import tempfile
import urllib.parse
from http import HTTPStatus
from pathlib import Path

from pdfmanager import filefacts
from pdfmanager.server.common import (
    MARKUPS_IN_HEADER, RecentFiles, SavedOver, UserError,
    _bool, _byte_range, _download_headers, _file_stamp, _parse_multipart,
    _replace_file, _safe_name, gui_hook, lazy_module, reveal_in_file_manager,
)

comments = lazy_module("pdfmanager.comments")
core = lazy_module("pdfmanager.core")
crop = lazy_module("pdfmanager.crop")
layers = lazy_module("pdfmanager.layers")
markup = lazy_module("pdfmanager.markup")
outline = lazy_module("pdfmanager.outline")
pageimages = lazy_module("pdfmanager.pageimages")
textedit = lazy_module("pdfmanager.textedit")
viewports = lazy_module("pdfmanager.viewports")


import collections
import threading

_SAFE_OPEN_CACHE = collections.OrderedDict()
_SAFE_OPEN_LOCK = threading.Lock()
_SAFE_OPEN_MAX = 4


def _get_safe_open_cached(cache_key, path):
    from pdfmanager import risks

    with _SAFE_OPEN_LOCK:
        hit = _SAFE_OPEN_CACHE.get(cache_key)
        if hit is not None:
            _SAFE_OPEN_CACHE.move_to_end(cache_key)
            return hit
    with tempfile.TemporaryDirectory(prefix="pdfm-safe-") as tmp:
        safe_path = Path(tmp) / "safe.pdf"
        try:
            risks.make_safe(path, safe_path)
        except Exception as exc:  # noqa: BLE001 - fail closed when policy requires Make Safe
            return exc
        safe_bytes = safe_path.read_bytes()
    with _SAFE_OPEN_LOCK:
        _SAFE_OPEN_CACHE[cache_key] = safe_bytes
        _SAFE_OPEN_CACHE.move_to_end(cache_key)
        while len(_SAFE_OPEN_CACHE) > _SAFE_OPEN_MAX:
            _SAFE_OPEN_CACHE.popitem(last=False)
    return safe_bytes


_CBZ_OPEN_CACHE = collections.OrderedDict()
_CBZ_OPEN_LOCK = threading.Lock()
_CBZ_OPEN_MAX = 8
_CBZ_OPEN_MAX_BYTES = 150 * 1024 * 1024  # 150 MB total cache cap
_BOOK_OPEN_EXTS = {".cbz", ".cbr", ".epub"}


def _get_cbz_open_cached(cache_key, path):
    from pdfmanager import cbz, epub

    with _CBZ_OPEN_LOCK:
        hit = _CBZ_OPEN_CACHE.get(cache_key)
        if hit is not None:
            _CBZ_OPEN_CACHE.move_to_end(cache_key)
            return hit
    ext = Path(path).suffix.lower()
    with tempfile.TemporaryDirectory(prefix="pdfm-book-") as tmp:
        out_path = Path(tmp) / "book.pdf"
        try:
            if ext == ".epub":
                epub.epub_to_pdf(path, out_path)
            elif ext == ".cbr":
                cbz.cbr_to_pdf(path, out_path)
            else:
                cbz.cbz_to_pdf(path, out_path)
        except Exception as exc:  # noqa: BLE001 - report conversion failure cleanly
            return exc
        pdf_bytes = out_path.read_bytes()
    with _CBZ_OPEN_LOCK:
        _CBZ_OPEN_CACHE[cache_key] = pdf_bytes
        _CBZ_OPEN_CACHE.move_to_end(cache_key)
        while len(_CBZ_OPEN_CACHE) > _CBZ_OPEN_MAX or (
            len(_CBZ_OPEN_CACHE) > 1 and sum(len(v) for v in _CBZ_OPEN_CACHE.values()) > _CBZ_OPEN_MAX_BYTES
        ):
            _CBZ_OPEN_CACHE.popitem(last=False)
    return pdf_bytes


class ReaderRoutesMixin:
    """Reader, annotation, layer, text-edit, OCR-correction, and markup endpoints."""

    def _open_file(self, key):
        """A file opened from this computer. Big files are read in pieces
        (HTTP Range): the reader shows the first pages without waiting for
        the whole file."""
        from pdfmanager import policy, risks

        path = self.opened.get(key) if self.opened is not None else None
        if path is None or not path.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "that file is no longer available")
        # The path is percent-encoded like the name: a folder called
        # "Документы" would otherwise be unsendable.
        is_book = path.suffix.lower() in _BOOK_OPEN_EXTS
        display_name = f"{path.stem}.pdf" if is_book else path.name
        headers = {"X-Pdfm-Filename": gui_hook("_download_headers", _download_headers)(display_name)["X-Pdfm-Filename"],
                   "X-Pdfm-Path": urllib.parse.quote(str(path), safe=""),
                   "X-Pdfm-Stamp": _file_stamp(path), "Accept-Ranges": "bytes"}

        if is_book:
            cache_key = (str(path), headers["X-Pdfm-Stamp"])
            cbz_bytes = _get_cbz_open_cached(cache_key, path)
            if isinstance(cbz_bytes, Exception):
                return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, f"couldn't open {path.suffix.upper()} file: {cbz_bytes}")
            size = len(cbz_bytes)
            wanted = self.headers.get("Range")
            span = _byte_range(wanted, size) if wanted else (0, size - 1)
            if span is None:
                return self._send(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"", "application/pdf",
                                  {**headers, "Content-Range": f"bytes */{size}"})
            start, end = span
            if start == 0:
                RecentFiles.add(path)
            if wanted:
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            self._start(HTTPStatus.PARTIAL_CONTENT if wanted else HTTPStatus.OK, "application/pdf",
                        end - start + 1, headers)
            self.wfile.write(cbz_bytes[start:end + 1])
            return

        if policy.requires_make_safe_on_open():
            # Administrator policy requires Make Safe sanitization before opening.
            # Cache sanitized bytes by (path, stamp) so multi-range pdf.js loads don't re-run make_safe per chunk.
            cache_key = (str(path), headers["X-Pdfm-Stamp"])
            safe_bytes = _get_safe_open_cached(cache_key, path)
            if isinstance(safe_bytes, Exception):
                return self._error(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    f"cannot open file: administrator policy requires Make Safe sanitization ({safe_bytes})",
                )
            headers["X-Pdfm-Made-Safe"] = "1"
            size = len(safe_bytes)
            wanted = self.headers.get("Range")
            span = _byte_range(wanted, size) if wanted else (0, size - 1)
            if span is None:
                return self._send(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"", "application/pdf",
                                  {**headers, "Content-Range": f"bytes */{size}"})
            start, end = span
            if start == 0:
                RecentFiles.add(path)
            if wanted:
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            self._start(HTTPStatus.PARTIAL_CONTENT if wanted else HTTPStatus.OK, "application/pdf",
                        end - start + 1, headers)
            self.wfile.write(safe_bytes[start:end + 1])
            return

        size = path.stat().st_size
        wanted = self.headers.get("Range")
        span = _byte_range(wanted, size) if wanted else (0, size - 1)
        if span is None:
            return self._send(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"", "application/pdf",
                              {**headers, "Content-Range": f"bytes */{size}"})
        start, end = span
        if start == 0:
            RecentFiles.add(path)
        if wanted:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        self._start(HTTPStatus.PARTIAL_CONTENT if wanted else HTTPStatus.OK, "application/pdf",
                    end - start + 1, headers)
        # The file is open only while a piece is read, never while it's being
        # sent: on Windows an open file can't be replaced (Save) or deleted,
        # and the reader may act on it as soon as the last byte arrives.
        at, left = start, end - start + 1
        while left > 0:
            with path.open("rb") as f:
                f.seek(at)
                chunk = f.read(min(left, 1 << 20))
            if not chunk:
                break
            self.wfile.write(chunk)
            at += len(chunk)
            left -= len(chunk)

    def _layers(self, body):
        """The layers of a PDF, for the reader's Layers tab (see layers.info):
        of a file opened from this computer (field "id") or of an uploaded copy."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        password = fields.get("password") or None
        if fields.get("id"):
            return self._send(HTTPStatus.OK, layers.info(self._opened_path(fields["id"]), password))
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = Path(tmp) / "file.pdf"
            path.write_bytes(uploads[0][2])
            return self._send(HTTPStatus.OK, layers.info(path, password))

    def _layers_change(self, how, body):
        """The reader's choice of layers ("hidden": a JSON list of pdf.js
        ids), saved as the way the file opens ("save") or made part of the
        page ("flatten"); sends back the changed PDF."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        try:
            hidden = json.loads(fields.get("hidden") or "[]")
        except ValueError:
            hidden = None
        if not isinstance(hidden, list) or not all(isinstance(h, str) for h in hidden):
            raise UserError("hidden must be a list of layers")
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "layers.pdf"
            src.write_bytes(uploads[0][2])
            if how == "save":
                layers.save_visibility(src, out, hidden, password)
            else:
                layers.flatten_layers(src, out, hidden=hidden, password=password)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)  # stays protected
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf")

    def _scales(self, body):
        """The drawing scales stored in a PDF (see viewports.py): of a file
        opened from this computer (field "id") or of an uploaded copy."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        password = fields.get("password") or None
        if fields.get("id"):
            path = self.opened.get(fields["id"]) if self.opened is not None else None
            if path is None:
                raise UserError("that file is no longer available")
            return self._send(HTTPStatus.OK, {"pages": viewports.stored_scales(path, password)})
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = Path(tmp) / "file.pdf"
            path.write_bytes(uploads[0][2])
            return self._send(HTTPStatus.OK, {"pages": viewports.stored_scales(path, password)})

    def _save_in_place(self, key, body):
        """Write the reader's copy of an opened file over the file itself:
        to a temporary file next to it first, then swapped in, so a failure
        halfway never leaves a broken file. If another program changed the
        file since it was opened, the answer is 409 unless X-Pdfm-Force."""
        path = self.opened.get(key) if self.opened is not None else None
        if path is None:
            raise UserError("this file can't be saved in place; use Save as")
        if path.suffix.lower() != ".pdf":
            raise UserError(f"{path.name} is not a PDF archive; use Save as to export your edits as a PDF")
        if b"%PDF" not in body[:1024]:
            raise UserError("not a PDF")
        expected = self.headers.get("X-Pdfm-Stamp", "")
        if (path.exists() and expected and _file_stamp(path) != expected
                and self.headers.get("X-Pdfm-Force") != "1"):
            return self._send(HTTPStatus.CONFLICT, {"error": f"{path.name} was changed by another "
                                                             "program", "changed": True})
        # What the file held, for Undo (not for big files: kept in memory).
        before = None
        try:
            if path.stat().st_size <= SavedOver.LIMIT:
                before = path.read_bytes()
        except OSError:
            pass
        _replace_file(path, body)
        RecentFiles.add(path)
        stamp = _file_stamp(path)
        undo = self.saved_over.add(path, before, stamp) if self.saved_over is not None else None
        return self._send(HTTPStatus.OK, {"path": str(path), "stamp": stamp, "undo": undo})

    def _unsave(self, data):
        """Put back what a file held before Save wrote over it (the "undo"
        that save gave). Refused when the file has changed since."""
        item = self.saved_over.pop(str(data.get("undo", ""))) if self.saved_over is not None else None
        if item is None:
            raise UserError("that can no longer be undone")
        path, before, stamp = item
        if not path.exists() or _file_stamp(path) != stamp:
            raise UserError(f"{path.name} has changed since it was saved, so it was left as it is")
        _replace_file(path, before)
        return self._send(HTTPStatus.OK, {"path": str(path), "stamp": _file_stamp(path)})

    def _reveal(self, data):
        """Show a file in Explorer / Finder / the file manager: only a file
        the app has open or has saved, never any path the page names."""
        known = None
        if self.opened is not None:
            if data.get("id"):
                known = self.opened.get(str(data["id"]))
            elif data.get("path"):
                known = self.opened.find(data["path"])
        if known is None:
            return self._error(HTTPStatus.FORBIDDEN, "not a file ZPDF has open")
        if not known.exists():
            raise UserError("that file is no longer there")
        gui_hook("reveal_in_file_manager", reveal_in_file_manager)(known)
        return self._send(HTTPStatus.OK, {"ok": True})

    def _recent(self):
        """Recently opened files that still exist, each with an id the
        reader can open."""
        files = []
        for item in RecentFiles.load():
            path = Path(item["path"])
            if path.is_file() and self.opened is not None:
                files.append({"id": self.opened.add(path), "name": path.name, "path": str(path),
                              "folder": str(path.parent), "opened": item.get("opened"),
                              "facts": filefacts.remembered(path), "failed": RecentFiles.is_failed(item)})
        return self._send(HTTPStatus.OK, {"files": files})

    def _outline(self, body):
        """The bookmarks of an uploaded PDF, or (detect=1) suggestions from
        its headings."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = Path(tmp) / "file.pdf"
            path.write_bytes(uploads[0][2])
            password = fields.get("password") or None
            pages = len(core.open_pdf(path, password).pages)
            items = (outline.detect_headings(path, password) if _bool(fields.get("detect"))
                     else outline.get_outline(path, password))
        return self._send(HTTPStatus.OK, {"items": items, "pages": pages})

    def _comments(self, what, body):
        """The reader's comment summary (PDF), XFDF export, or XFDF import
        (the changed PDF)."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "file.pdf"
            src.write_bytes(uploads[0][2])
            out = Path(tmp) / "out"
            name = fields.get("name") or "document.pdf"
            if what == "summary":
                comments.summary_pdf(comments.collect(src, password), out, name)
                return self._send(HTTPStatus.OK, out.read_bytes(), "application/pdf")
            if what == "markdown":
                group_by = fields.get("group_by") or "page"
                if group_by not in comments.GROUP_MODES:
                    raise UserError(f"unknown grouping {group_by!r}")
                found = comments.filter_comments(
                    comments.collect(src, password),
                    author=fields.get("author") or None,
                    kind=fields.get("kind") or None,
                )
                md = comments.to_markdown(found, title=name, group_by=group_by)
                return self._send(HTTPStatus.OK, md.encode("utf-8"), "text/markdown; charset=utf-8",
                                  {"X-Pdfm-Count": str(comments.count(found))})
            if what == "export":
                n = comments.export_xfdf(src, out, password)
                return self._send(HTTPStatus.OK, out.read_bytes(), "application/vnd.adobe.xfdf",
                                  {"X-Pdfm-Count": str(n)})
            xfdf = next((data for key, _, data in uploads if key == "xfdf"), None)
            if xfdf is None:
                raise UserError("choose the comments file (.xfdf) to add")
            (Path(tmp) / "in.xfdf").write_bytes(xfdf)
            added, skipped = comments.import_xfdf(src, Path(tmp) / "in.xfdf", out, password)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)  # stays protected
            return self._send(HTTPStatus.OK, out.read_bytes(), "application/pdf",
                              {"X-Pdfm-Added": str(added), "X-Pdfm-Skipped": ",".join(skipped)})

    def _drawing_open(self, body):
        """Convert an uploaded .dxf or .dwg drawing to a layered, scaled PDF for the reader."""
        from pdfmanager import dxf

        raw_name = self.headers.get("X-Pdfm-Filename") or "drawing.dxf"
        try:
            raw_name = urllib.parse.unquote(raw_name)
        except Exception as _exc:
            logging.getLogger("zpdf").debug("reader_routes.ReaderRoutesMixin._drawing_open: %s", _exc)
        name = Path(raw_name).name or "drawing.dxf"
        ext = Path(name).suffix.lower()
        if ext == ".dwg":
            temp_name = "input.dwg"
        elif ext == ".dxf":
            temp_name = "input.dxf"
        else:
            raise UserError("choose a .dxf or .dwg drawing")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / temp_name
            out = Path(tmp) / "output.pdf"
            src.write_bytes(body)
            dxf.drawing_to_pdf(src, out)
            pdf_bytes = out.read_bytes()
        return self._send(HTTPStatus.OK, pdf_bytes, "application/pdf",
                          {"X-Pdfm-Filename": urllib.parse.quote(f"{Path(name).stem}.pdf")})

    def _cbz_open(self, body):
        """Convert an uploaded .cbz comic/manga archive to a PDF for the reader."""
        from pdfmanager import cbz

        raw_name = self.headers.get("X-Pdfm-Filename") or "comic.cbz"
        try:
            raw_name = urllib.parse.unquote(raw_name)
        except Exception as _exc:
            logging.getLogger("zpdf").debug("reader_routes.ReaderRoutesMixin._cbz_open: %s", _exc)
        name = Path(raw_name).name or "comic.cbz"
        if Path(name).suffix.lower() != ".cbz":
            raise UserError("choose a .cbz comic or manga archive")
        with tempfile.TemporaryDirectory(prefix="pdfm-cbz-") as tmp:
            src = Path(tmp) / "input.cbz"
            out = Path(tmp) / "output.pdf"
            src.write_bytes(body)
            cbz.cbz_to_pdf(src, out)
            pdf_bytes = out.read_bytes()
        return self._send(HTTPStatus.OK, pdf_bytes, "application/pdf",
                          {"X-Pdfm-Filename": urllib.parse.quote(f"{Path(name).stem}.pdf")})

    def _epub_open(self, body):
        """Convert an uploaded .epub e-book to a PDF for the reader."""
        from pdfmanager import epub

        raw_name = self.headers.get("X-Pdfm-Filename") or "book.epub"
        try:
            raw_name = urllib.parse.unquote(raw_name)
        except Exception as _exc:
            logging.getLogger("zpdf").debug("reader_routes._epub_open: %s", _exc)
        name = Path(raw_name).name or "book.epub"
        if Path(name).suffix.lower() != ".epub":
            raise UserError("choose an .epub e-book")
        with tempfile.TemporaryDirectory(prefix="pdfm-epub-") as tmp:
            src = Path(tmp) / "input.epub"
            out = Path(tmp) / "output.pdf"
            src.write_bytes(body)
            epub.epub_to_pdf(src, out)
            pdf_bytes = out.read_bytes()
        return self._send(HTTPStatus.OK, pdf_bytes, "application/pdf",
                          {"X-Pdfm-Filename": urllib.parse.quote(f"{Path(name).stem}.pdf")})

    def _cbr_open(self, body):
        """Convert an uploaded .cbr comic archive to a PDF for the reader."""
        from pdfmanager import cbz

        raw_name = self.headers.get("X-Pdfm-Filename") or "comic.cbr"
        try:
            raw_name = urllib.parse.unquote(raw_name)
        except Exception as _exc:
            logging.getLogger("zpdf").debug("reader_routes._cbr_open: %s", _exc)
        name = Path(raw_name).name or "comic.cbr"
        if Path(name).suffix.lower() != ".cbr":
            raise UserError("choose a .cbr comic or manga archive")
        with tempfile.TemporaryDirectory(prefix="pdfm-cbr-") as tmp:
            src = Path(tmp) / "input.cbr"
            out = Path(tmp) / "output.pdf"
            src.write_bytes(body)
            cbz.cbr_to_pdf(src, out)
            pdf_bytes = out.read_bytes()
        return self._send(HTTPStatus.OK, pdf_bytes, "application/pdf",
                          {"X-Pdfm-Filename": urllib.parse.quote(f"{Path(name).stem}.pdf")})

    def _images_open(self, body):
        """Convert one or more uploaded images (or an image folder drop) into a single PDF."""
        from pdfmanager import cbz

        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no images uploaded")
        items = [(fname or f"page_{i:03d}.png", data) for i, (_field, fname, data) in enumerate(uploads, 1)]
        title = (fields.get("title") or "").strip() or Path(items[0][0]).stem or "images"
        rtl = _bool(fields.get("rtl"))
        with tempfile.TemporaryDirectory(prefix="pdfm-imgs-") as tmp:
            out = Path(tmp) / "images.pdf"
            cbz.images_to_pdf(items, out, rtl_viewer=rtl)
            pdf_bytes = out.read_bytes()
        return self._send(HTTPStatus.OK, pdf_bytes, "application/pdf",
                          {"X-Pdfm-Filename": urllib.parse.quote(f"{_safe_name(title)}.pdf")})

    def _library_list(self, query=""):
        from pdfmanager import bookshelf

        return self._send(HTTPStatus.OK, bookshelf.scan_library(query=query))

    def _library_folder(self, body):
        from pdfmanager import bookshelf

        data = json.loads(body or b"{}")
        action = str(data.get("action") or "add").strip().lower()
        folder = str(data.get("path") or "").strip()
        if action == "remove":
            bookshelf.remove_folder(folder)
        else:
            if not folder:
                raise UserError("enter a folder path")
            bookshelf.add_folder(folder)
        return self._send(HTTPStatus.OK, bookshelf.scan_library())

    def _library_thumb(self, book_id):
        from pdfmanager import bookshelf

        path = bookshelf.find_by_id(book_id)
        if path is None:
            return self._error(HTTPStatus.NOT_FOUND, "book not found")
        raw = bookshelf.cover_thumbnail(path)
        if not raw:
            return self._error(HTTPStatus.NOT_FOUND, "thumbnail unavailable")
        return self._send(HTTPStatus.OK, raw, "image/jpeg", {"Cache-Control": "private, max-age=300"})

    def _library_open(self, body):
        from pdfmanager import bookshelf

        data = json.loads(body or b"{}")
        book_id = str(data.get("id") or "").strip()
        if not book_id:
            raise UserError("no book id")
        p = bookshelf.find_by_id(book_id)
        if p is None or not p.is_file():
            raise UserError("file not found")
        key = self.opened.add(p)
        return self._send(HTTPStatus.OK, {"id": key, "name": p.name, "path": str(p)})

    def _library_neighbors(self, body):
        from pdfmanager import bookshelf

        data = json.loads(body or b"{}")
        file_id = str(data.get("id") or "").strip()
        path = self.opened.get(file_id) if (file_id and self.opened is not None) else None
        if path is None or not path.is_file():
            return self._send(HTTPStatus.OK, {"prev": None, "next": None, "index": 0, "total": 0})
        info = bookshelf.sibling_chapters(path)
        want = str(data.get("open") or "").strip().lower()
        opened_neighbor = None
        if want in ("prev", "next") and info.get(want):
            np = Path(info[want])
            if np.is_file():
                nkey = self.opened.add(np)
                opened_neighbor = {"id": nkey, "name": np.name, "path": str(np)}
        return self._send(HTTPStatus.OK, {**info, "opened": opened_neighbor})

    def _text_edit(self, body):
        """Change words on the reader's pages; sends back the changed PDF."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        try:
            edits = json.loads(fields.get("edits") or "[]")
        except ValueError:
            raise UserError("the changes are damaged; make them again") from None
        password = fields.get("password") or None
        require_permanent = str(fields.get("require_permanent") or "").lower() in ("1", "true", "yes")
        verify_visual = str(fields.get("verify_visual") or "").lower() in ("1", "true", "yes")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "edited.pdf"
            src.write_bytes(uploads[0][2])
            report = []
            visual_report = []
            textedit.edit_text(
                src, out, edits, password, report=report,
                require_permanent=require_permanent, verify_visual=verify_visual,
                visual_report=visual_report,
            )
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)  # stays protected
            data = out.read_bytes()
        summary = textedit.summarize_report(report)
        headers = {
            "X-Pdfm-Fonts": json.dumps(report, ensure_ascii=True),
            "X-Pdfm-Summary": json.dumps(summary, ensure_ascii=True),
        }
        if visual_report:
            headers["X-Pdfm-Visual"] = json.dumps(visual_report, ensure_ascii=True)
        # Which font each change got, plus permanent-vs-cover summary & visual verification.
        return self._send(HTTPStatus.OK, data, "application/pdf", headers)

    def _text_fonts(self, body):
        """The fonts the reader's text editor offers: the document's own
        first, then installed ones, then the standard PDF fonts."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "file.pdf"
            src.write_bytes(uploads[0][2])
            choices = textedit.font_choices(src, fields.get("password") or None)
        return self._send(HTTPStatus.OK, choices)

    def _text_runs(self, body):
        """How a paragraph's words look (bold, italic, colour), for the
        reader's paragraph editor (see textedit.paragraph_runs)."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        try:
            page, rect, text = int(fields.get("page", "")), json.loads(fields.get("rect") or "null"), fields.get("text", "")
            if not isinstance(rect, list) or len(rect) != 4:
                raise ValueError
        except ValueError:
            raise UserError("a paragraph needs a page and the box around its words") from None
        if len(text) > 20000:
            raise UserError("that paragraph is too long to edit at once")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "file.pdf"
            src.write_bytes(uploads[0][2])
            runs = textedit.paragraph_runs(src, page, rect, text, fields.get("password") or None)
        return self._send(HTTPStatus.OK, {"runs": runs})

    def _ocr_unsure(self, body):
        """Return unsure OCR words stored in the PDF's private /PieceInfo keys."""
        from pdfmanager import textlayer

        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "file.pdf"
            src.write_bytes(uploads[0][2])
            found = textlayer.read_unsure_words(src, fields.get("password") or None)
        return self._send(HTTPStatus.OK, found)

    def _ocr_fix(self, body):
        """Correct (or accept) OCR words in the invisible text layer; sends back the updated PDF."""
        from pdfmanager import textlayer

        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        try:
            edits = json.loads(fields.get("edits") or "[]")
        except ValueError:
            raise UserError("the OCR corrections are damaged; try again") from None
        if not isinstance(edits, list):
            raise UserError("the OCR corrections must be a list")
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "fixed.pdf"
            src.write_bytes(uploads[0][2])
            textlayer.correct_words(src, out, edits, password=password)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)  # stays protected
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf",
                          {"X-Pdfm-Changed": str(len(edits))})

    def _ocr_words(self, body):
        """Return all invisible OCR words (high and low confidence) for in-place editing."""
        from pdfmanager import textlayer

        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        page_str = (fields.get("page") or "").strip()
        page_num = int(page_str) if page_str.isdigit() else None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "file.pdf"
            src.write_bytes(uploads[0][2])
            found = textlayer.read_all_ocr_words(src, fields.get("password") or None, page=page_num)
        return self._send(HTTPStatus.OK, found)

    def _ocr_replace(self, body):
        """Batch find-and-replace across invisible OCR words; sends back the updated PDF."""
        from pdfmanager import textlayer

        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        find_text = fields.get("find") or ""
        if not find_text:
            raise UserError("enter text to find in the OCR layer")
        replace_text = fields.get("replace") or ""
        case_sensitive = _bool(fields.get("case_sensitive"))
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "replaced.pdf"
            src.write_bytes(uploads[0][2])
            res = textlayer.replace_ocr_text(
                src, out, find_text, replace_text,
                password=password, case_sensitive=case_sensitive,
            )
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf",
                          {"X-Pdfm-Changed": str(res["replaced"])})

    def _sessions_list(self):
        """Return saved workspace sessions with fresh OpenFiles IDs for existing files."""
        from pdfmanager import sessions

        out = []
        for s in sessions.list_sessions():
            tabs_out = []
            for tab in s.get("tabs") or []:
                p = Path(tab.get("path") or "")
                exists = p.is_file()
                file_id = self.opened.add(p) if (exists and self.opened is not None) else None
                tabs_out.append({
                    "id": file_id,
                    "name": tab.get("name") or p.name,
                    "page": tab.get("page", 1),
                    "scale": tab.get("scale", "auto"),
                    "rotation": tab.get("rotation", 0),
                    "missing": not exists,
                })
            out.append({
                "id": s.get("id"),
                "name": s.get("name"),
                "saved_at": s.get("saved_at"),
                "active_index": s.get("active_index", 0),
                "tabs": tabs_out,
                "split": s.get("split") or {"open": False},
            })
        return self._send(HTTPStatus.OK, {"sessions": out})

    def _sessions_save(self, body):
        """Save a named workspace session. Only accepts fileIds registered in self.opened."""
        from pdfmanager import sessions

        data = self._json(body)
        name = str(data.get("name") or "").strip()
        raw_tabs = data.get("tabs") or []
        if not isinstance(raw_tabs, list):
            raise UserError("tabs must be a list")
        resolved = []
        for t in raw_tabs:
            if not isinstance(t, dict):
                continue
            fid = str(t.get("fileId") or "").strip()
            known = self.opened.get(fid) if (fid and self.opened is not None) else None
            if known is None or not known.is_file():
                continue
            resolved.append({
                "path": str(known),
                "name": str(t.get("name") or known.name),
                "page": int(t.get("page") or 1),
                "scale": t.get("scale", "auto"),
                "rotation": int(t.get("rotation") or 0),
            })
        if not resolved:
            raise UserError("open at least one saved PDF from disk before saving a workspace session")
        try:
            saved = sessions.save_session(
                name=name,
                tabs=resolved,
                active_index=int(data.get("active_index") or 0),
                split=data.get("split") if isinstance(data.get("split"), dict) else None,
            )
        except ValueError as exc:
            raise UserError(str(exc)) from None
        return self._send(HTTPStatus.OK, {"session": saved})

    def _sessions_delete(self, body):
        """Delete a saved workspace session by ID."""
        from pdfmanager import sessions

        data = self._json(body)
        deleted = sessions.delete_session(str(data.get("id") or ""))
        return self._send(HTTPStatus.OK, {"deleted": deleted})

    def _ai_ask(self, body):
        """Ask a question about the uploaded PDF using the configured local LLM."""
        from pdfmanager import assistant

        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        question = (fields.get("question") or "").strip()
        if not question:
            raise UserError("enter a question to ask about this PDF")
        max_pages = max(1, min(16, int(fields.get("max_pages") or 6)))
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "file.pdf"
            src.write_bytes(uploads[0][2])
            res = assistant.ask_pdf(
                src, question,
                password=fields.get("password") or None,
                max_pages=max_pages,
            )
        return self._send(HTTPStatus.OK, res)

    def _ai_summarize(self, body):
        """Summarize the uploaded PDF (or a page range) using the configured local LLM."""
        from pdfmanager import assistant

        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "file.pdf"
            src.write_bytes(uploads[0][2])
            res = assistant.summarize_pdf(
                src,
                password=fields.get("password") or None,
                pages_spec=(fields.get("pages") or "").strip() or None,
                style=(fields.get("style") or "bullets").strip(),
            )
        return self._send(HTTPStatus.OK, res)

    def _ai_extract(self, body):
        """Extract structured key-value pairs or tables to JSON/CSV using the local LLM."""
        from pdfmanager import assistant

        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "file.pdf"
            src.write_bytes(uploads[0][2])
            res = assistant.extract_structured(
                src,
                schema=(fields.get("schema") or "key_values").strip(),
                fields=(fields.get("fields") or "").strip() or None,
                fmt=(fields.get("format") or "json").strip(),
                pages_spec=(fields.get("pages") or "").strip() or None,
                password=fields.get("password") or None,
            )
        return self._send(HTTPStatus.OK, res)

    def _ai_vlm_ocr(self, body):
        """Run Vision-LLM OCR or direct visual page translation on a single PDF/CBZ page."""
        from pdfmanager import cbz, vlm

        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        file_id = (fields.get("id") or "").strip()
        opened_path = self.opened.get(file_id) if (file_id and self.opened is not None) else None
        if not uploads and (opened_path is None or not opened_path.is_file()):
            raise UserError("no file")
        page_num = int(fields.get("page") or 1)
        translate_to = (fields.get("translate_to") or "").strip()
        prompt = (fields.get("prompt") or "").strip() or None
        model = (fields.get("model") or "").strip() or None
        password = fields.get("password") or None

        with tempfile.TemporaryDirectory(prefix="pdfm-vlm-") as tmp:
            if opened_path is not None and opened_path.is_file() and not uploads:
                if opened_path.suffix.lower() == ".cbz":
                    pdf_path = Path(tmp) / "comic.pdf"
                    cbz.cbz_to_pdf(opened_path, pdf_path, rtl_viewer="auto")
                else:
                    pdf_path = opened_path
            else:
                pdf_path = Path(tmp) / "file.pdf"
                pdf_path.write_bytes(uploads[0][2])
            res = vlm.vlm_ocr_pdf_page(
                pdf_path, page_num, password=password,
                translate_to=translate_to, prompt=prompt, model=model,
            )
        return self._send(HTTPStatus.OK, res)

    def _images_list(self, body):
        """The pictures on the reader's pages (see pageimages.py)."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "file.pdf"
            src.write_bytes(uploads[0][2])
            found = pageimages.list_images(src, fields.get("password") or None)
        return self._send(HTTPStatus.OK, {"pages": found})

    def _images_edit(self, body):
        """Move, resize, replace or delete pictures; sends back the changed PDF.
        The first upload is the document, "picture" uploads are new pictures
        (by their field position: picture0, picture1, ...)."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        try:
            changes = json.loads(fields.get("changes") or "[]")
        except ValueError:
            raise UserError("the changes are damaged; make them again") from None
        if not isinstance(changes, list) or not all(isinstance(c, dict) for c in changes):
            raise UserError("the changes are damaged; make them again")
        pictures = {name: bytes(data) for name, _, data in uploads[1:] if name}
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "edited.pdf"
            src.write_bytes(uploads[0][2])
            pageimages.edit_images(src, out, changes, password, pictures)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)  # stays protected
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf")

    def _markup(self, body):
        """Add notes, underlines, strikeouts and shapes, or change, answer or
        remove comments (see markup.py); sends back the changed PDF."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        try:
            changes = json.loads(fields.get("changes") or "[]")
        except ValueError:
            raise UserError("the changes are damaged; make them again") from None
        if not isinstance(changes, list) or not all(isinstance(c, dict) for c in changes):
            raise UserError("the changes are damaged; make them again")
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "marked.pdf"
            src.write_bytes(uploads[0][2])
            added = markup.apply(src, out, changes, password, fields.get("author"))
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)  # stays protected
            data = out.read_bytes()
            # How its changeable markups are now, for the reader (which then doesn't send the
            # whole file again to ask); left out when it's too much for a header.
            headers = {"X-Pdfm-Added": json.dumps(added)}
            try:
                now = json.dumps(markup.describe_all(out, password), ensure_ascii=True, separators=(",", ":"))
                if len(now) <= gui_hook("MARKUPS_IN_HEADER", MARKUPS_IN_HEADER):
                    headers["X-Pdfm-Markups"] = now
            except Exception as _exc:  # noqa: BLE001 - the reader asks for them the usual way
                logging.getLogger("zpdf").debug("reader_routes.ReaderRoutesMixin._markup: %s", _exc)
        return self._send(HTTPStatus.OK, data, "application/pdf", headers)

    def _quantities(self, body):
        """The reader's Measurements panel: one schedule of what's measured in
        the open document (as it is now, unsaved changes included) and what's
        still only in the reader's list; sends back a workbook or a CSV file."""
        from pdfmanager import quantities

        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        try:
            extra = json.loads(fields.get("measures") or "[]")
        except ValueError:
            raise UserError("the measurements are damaged; try again") from None
        as_csv = fields.get("format") == "csv"
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "file.pdf"
            src.write_bytes(uploads[0][2])
            found = quantities.schedule(src, fields.get("password") or None, extra)
            if not found["items"]:
                raise UserError("there's nothing measured yet: measure with the Scale bar, or place a dimension")
            out = Path(tmp) / ("q.csv" if as_csv else "q.xlsx")
            (quantities.write_csv(found, out) if as_csv
             else quantities.write_xlsx(found, out, str(fields.get("name") or "")[:120]))
            data = out.read_bytes()
        kind = "text/csv; charset=utf-8" if as_csv else \
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return self._send(HTTPStatus.OK, data, kind, {"X-Pdfm-Said": json.dumps(quantities.said(found))})

    def _markup_describe(self, body):
        """The markups of the open document that can be changed after placing
        (markup.describe_all), for the reader to select, move and reshape."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "file.pdf"
            src.write_bytes(uploads[0][2])
            found = markup.describe_all(src, fields.get("password") or None)
        return self._send(HTTPStatus.OK, {"pages": {str(n): shapes for n, shapes in found.items()}})

    def _arrange(self, body):
        """Reorder, turn, delete, copy or insert the reader's pages; sends back
        the new PDF. The first upload is the document, the others are PDFs
        whose pages go in."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        try:
            plan = json.loads(fields.get("plan") or "[]")
        except ValueError:
            raise UserError("the page order is damaged; try again") from None
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            paths = []
            for n, (_, _, data) in enumerate(uploads):
                path = Path(tmp) / f"{n}.pdf"
                path.write_bytes(data)
                paths.append(path)
            out = Path(tmp) / "arranged.pdf"
            core.arrange(paths[0], out, plan, inserts=paths[1:], password=password)
            if password and core.open_pdf(paths[0], password).is_encrypted:
                core.encrypt(out, out, password)  # stays protected
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf")

    def _crop(self, body):
        """Apply an interactive visual crop rectangle to selected pages and return the PDF."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        try:
            rect = json.loads(fields.get("rect") or "[]")
            if not isinstance(rect, list) or len(rect) != 4:
                raise ValueError("rect must have 4 coordinates")
        except ValueError as exc:
            raise UserError(f"invalid crop rectangle: {exc}") from None
        password = fields.get("password") or None
        scope = fields.get("scope") or "page"
        page_num = int(fields.get("page") or 1)
        pages_spec = fields.get("pages") or None
        trim_permanently = _bool(fields.get("trim_permanently"))
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "in.pdf"
            out = Path(tmp) / "cropped.pdf"
            src.write_bytes(uploads[0][2])
            crop.crop_pages(
                src,
                out,
                rect,
                scope=scope,
                page_num=page_num,
                pages_spec=pages_spec,
                password=password,
                trim_permanently=trim_permanently,
            )
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf")

    def _reader_compare(self, body):
        """Compute a structured JSON diff summary for the Reader's interactive split-view diff mode."""
        from pdfmanager import compare

        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        by_field = {}
        for field_name, filename, data in uploads:
            by_field[field_name] = (filename, data)

        old_id = (fields.get("old_id") or "").strip()
        new_id = (fields.get("new_id") or "").strip()
        old_opened = self.opened.get(old_id) if (old_id and self.opened is not None) else None
        new_opened = self.opened.get(new_id) if (new_id and self.opened is not None) else None

        mode = (fields.get("mode") or "both").strip().lower()
        if mode not in compare.MODES:
            raise UserError(f"unknown compare mode: {mode!r}")
        try:
            dpi = max(36, min(150, int(fields.get("dpi") or compare.DPI)))
        except ValueError:
            dpi = compare.DPI
        old_pw = fields.get("old_password") or None
        new_pw = fields.get("new_password") or None

        with tempfile.TemporaryDirectory(prefix="pdfm-cmp-") as tmp:
            tmp_dir = Path(tmp)
            if "file_old" in by_field:
                old_path = tmp_dir / "old.pdf"
                old_path.write_bytes(by_field["file_old"][1])
            elif old_opened is not None and old_opened.is_file():
                old_path = old_opened
            else:
                raise UserError("missing first PDF to compare")

            if "file_new" in by_field:
                new_path = tmp_dir / "new.pdf"
                new_path.write_bytes(by_field["file_new"][1])
            elif new_opened is not None and new_opened.is_file():
                new_path = new_opened
            else:
                raise UserError("missing second PDF to compare")

            summary = compare.compare_summary(
                old_path,
                new_path,
                old_password=old_pw,
                new_password=new_pw,
                all_pages=True,
                dpi=dpi,
                mode=mode,
            )
        return self._send(HTTPStatus.OK, summary)

    def _attachments_update(self, body):
        """Add or remove embedded attachments on the open PDF and return updated PDF bytes."""
        from pdfmanager import filetools

        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        pdf_upload = None
        attach_uploads = []
        for field_name, filename, data in uploads:
            if field_name == "file" and pdf_upload is None:
                pdf_upload = (filename, data)
            elif field_name in ("attach", "attachment", "attachments"):
                attach_uploads.append((filename, data))
        if pdf_upload is None:
            raise UserError("no file")
        try:
            remove = json.loads(fields.get("remove") or "[]")
            if not isinstance(remove, list):
                remove = []
        except ValueError:
            raise UserError("invalid remove list") from None
        remove_all = _bool(fields.get("remove_all"))
        description = str(fields.get("description") or "").strip()
        password = fields.get("password") or None

        with tempfile.TemporaryDirectory(prefix="pdfm-att-") as tmp:
            tmp_dir = Path(tmp)
            src = tmp_dir / "in.pdf"
            out = tmp_dir / "out.pdf"
            src.write_bytes(pdf_upload[1])
            att_dir = tmp_dir / "files"
            att_dir.mkdir()
            add_paths = []
            for idx, (fname, data) in enumerate(attach_uploads):
                safe = _safe_name(fname) or f"attachment_{idx + 1}.bin"
                target = att_dir / safe
                if target.exists():
                    target = att_dir / f"{target.stem}_{idx + 1}{target.suffix}"
                target.write_bytes(data)
                add_paths.append(target)
            filetools.attach(
                src,
                out,
                files=add_paths,
                remove=[str(x) for x in remove],
                remove_all=remove_all,
                password=password,
                description=description,
            )
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)
            result_bytes = out.read_bytes()
        return self._send(HTTPStatus.OK, result_bytes, "application/pdf")
