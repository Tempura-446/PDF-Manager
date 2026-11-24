# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Reader HTTP route handlers (file streaming, save/undo, layers, text edit, OCR fix, markups, comments)."""

import json
import tempfile
import urllib.parse
from http import HTTPStatus
from pathlib import Path

from pdfmanager import core, filefacts
from pdfmanager.server.common import (
    MARKUPS_IN_HEADER, RecentFiles, SavedOver, UserError,
    _bool, _byte_range, _download_headers, _file_stamp, _parse_multipart,
    _replace_file, gui_hook, lazy_module, reveal_in_file_manager,
)

comments = lazy_module("pdfmanager.comments")
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
        headers = {"X-Pdfm-Filename": gui_hook("_download_headers", _download_headers)(path.name)["X-Pdfm-Filename"],
                   "X-Pdfm-Path": urllib.parse.quote(str(path), safe=""),
                   "X-Pdfm-Stamp": _file_stamp(path), "Accept-Ranges": "bytes"}

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
        except Exception:
            pass
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
            except Exception:  # noqa: BLE001 - the reader asks for them the usual way
                pass
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
