# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tool execution, redaction, signing, accessibility, and inspection HTTP route handlers."""

import json
import re
import shutil
import tempfile
import time
from http import HTTPStatus
from pathlib import Path

from pdfmanager import applog, filefacts, progress
from pdfmanager.server.common import (
    EXPECTED_ERRORS, UserError, _bool, _file_mentions, _parse_multipart,
    _safe_name, gui_hook, lazy_module,
)

accessibility = lazy_module("pdfmanager.accessibility")
certsign = lazy_module("pdfmanager.certsign")
core = lazy_module("pdfmanager.core")
preflight = lazy_module("pdfmanager.preflight")
redact = lazy_module("pdfmanager.redact")
from pdfmanager.server.operations import (
    _redact_boxes, _redact_request, inspect_pdf, run_operation,
)


class ToolRoutesMixin:
    """Tool runner, redaction, digital signature, accessibility, and inspect endpoints."""

    def _run(self, body):
        """Run a tool. With "X-Pdfm-Async: 1" the tool runs as a background
        job: the answer is {"job": id}; /api/job/<id> tells how far it is,
        /api/job/<id>/result gives the file and /api/job/<id>/cancel stops it."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        op = fields.pop("op", "")
        password = fields.pop("password", "") or None
        keep = self.headers.get("X-Pdfm-Keep") == "1"
        # "ids": files that are already open (see OpenFiles), to work on
        # without the page sending them back. They come before any uploads.
        opened = []
        for key in [k for k in fields.pop("ids", "").split(",") if k.strip()] if "ids" in fields else []:
            path = self.opened.get(key.strip()) if self.opened is not None else None
            if path is None or not path.is_file():
                return self._error(HTTPStatus.BAD_REQUEST, "that file is no longer available; open it again")
            opened.append(path)

        def work_on():
            work = self.work.make()
            try:
                files, extra = [], {}
                for index, source in enumerate(opened):
                    # A copy: tools never touch the file the reader has open.
                    folder = work / "in" / f"o{index:03d}"
                    folder.mkdir(parents=True)
                    files.append(Path(shutil.copy2(source, folder / _safe_name(source.name))))
                for index, (name, filename, data) in enumerate(uploads):
                    # One folder per upload keeps original names without clashes.
                    folder = work / "in" / f"{index:03d}"
                    folder.mkdir(parents=True)
                    path = folder / _safe_name(filename)
                    path.write_bytes(data)
                    if name == "files":
                        files.append(path)
                    elif data:  # extra file field, e.g. an ICC profile
                        extra.setdefault(name, path)
                        extra.setdefault(name + "[]", []).append(path)  # all of them
                uploads.clear()
                notes = []
                started, outcome = gui_hook("time", time).monotonic(), "failed"
                try:
                    name, data, ctype = gui_hook("run_operation", run_operation)(op, files, fields, password, extra, work, notes)
                    outcome = "done"
                except progress.Cancelled:
                    outcome = "cancelled"
                    raise
                except EXPECTED_ERRORS as exc:
                    outcome = f"stopped: {exc}"
                    raise
                finally:
                    applog.tool_run(op, files, gui_hook("time", time).monotonic() - started, outcome)
                return {"file": (name, data, ctype, notes, keep)}
            finally:
                self.work.remove(work)

        return self._job_or_now(work_on)

    def _redact_find(self, body):
        """Where the Redact tool's words and kinds of numbers are found, so
        the page editor can show them before anything is blacked out."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        terms, patterns, _ = gui_hook("_redact_request", _redact_request)({k: v for k, v in fields.items() if k != "boxes"})
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = Path(tmp) / "file.pdf"
            path.write_bytes(uploads[0][2])
            found = redact.find_text(path, terms, patterns,
                                     case_sensitive=_bool(fields.get("case_sensitive")),
                                     whole_words=_bool(fields.get("whole_words")),
                                     password=fields.get("password") or None)
        return self._send(HTTPStatus.OK, {"boxes": {str(p + 1): [list(b) for b in boxes]
                                                    for p, boxes in found.items()}})

    def _redact_apply(self, body):
        """The reader's Redact tool: remove what's under the marked boxes
        ("boxes": {"1": [[x0, y0, x1, y1]]} in PDF points from the bottom
        left of each page); sends back the redacted PDF, and in
        X-Pdfm-Redacted how many areas, on which pages, and which pages had
        to become pictures (and why)."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        boxes = gui_hook("_redact_boxes", _redact_boxes)(fields.get("boxes"))
        if not boxes:
            raise UserError("mark something to redact first")
        method = fields.get("method") or "remove"
        if method not in redact.METHODS:
            raise UserError("choose how to redact: remove what's under the boxes, or pictures")
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "redacted.pdf"
            src.write_bytes(uploads[0][2])
            report = redact.redact(src, out, boxes=boxes, password=password, method=method)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)  # stays protected
            data = out.read_bytes()
        summary = {"areas": sum(report.values()), "pages": list(report),
                   "pictures": {str(k): v for k, v in report.pictures.items()}}
        return self._send(HTTPStatus.OK, data, "application/pdf",
                          {"X-Pdfm-Redacted": json.dumps(summary, ensure_ascii=True)})

    def _sign_cert(self, body):
        """Sign the reader's PDF with a digital ID; sends back the signed PDF,
        and what certsign.sign() said in X-Pdfm-Signed (JSON). A missing or
        wrong digital ID password answers 400 with "code": "pfx_password"."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        named = {name: data for name, _, data in uploads}
        if "file" not in named:
            raise UserError("no file")
        if not len(named.get("pfx") or b""):
            raise UserError("choose your digital ID: a .pfx or .p12 file")
        password = fields.get("password") or None
        box = None
        if (fields.get("box") or "").strip():
            try:
                box = certsign.parse_box(json.loads(fields["box"]))
            except ValueError:
                raise UserError("the signature box is damaged; draw it again") from None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "signed.pdf"
            src.write_bytes(named["file"])
            page = (fields.get("page") or "").strip() or None
            if page is not None and box is None and not fields.get("field"):
                page, box = certsign.place(src, page, fields.get("position") or "bottom-right",
                                           fields.get("size") or "medium", password)
            try:
                result = certsign.sign(
                    src, out, bytes(named["pfx"]), fields.get("pfx_password") or None,
                    page=page, box=box, field=(fields.get("field") or "").strip() or None,
                    reason=(fields.get("reason") or "").strip() or None,
                    location=(fields.get("location") or "").strip() or None,
                    contact=(fields.get("contact") or "").strip() or None,
                    timestamp_url=(fields.get("timestamp_url") or "").strip() or None,
                    password=password)
            except certsign.IdPasswordError as exc:
                self.close_connection = True
                return self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc),
                                                           "code": "pfx_password"})
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf",
                          {"X-Pdfm-Signed": json.dumps(result, ensure_ascii=True)})

    def _sign_verify(self, body):
        """The digital signatures in a PDF, checked: {"signatures": [...]}
        (see certsign.verify), of a file opened from this computer (field
        "id") or of an uploaded copy. With "quick", a file that can't hold a
        signature (every one has a /ByteRange) is answered without being read
        as a PDF: what the reader asks about every file it opens."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        named = {name: data for name, _, data in uploads}
        opened = self._opened_path(fields["id"]) if fields.get("id") else None
        if opened is None and "file" not in named:
            raise UserError("no file")
        if fields.get("quick") and not (_file_mentions(opened, b"/ByteRange") if opened is not None
                                        else re.search(rb"/ByteRange", named["file"])):
            return self._send(HTTPStatus.OK, {"signatures": []})
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = opened
            if path is None:
                path = Path(tmp) / "file.pdf"
                path.write_bytes(named["file"])
            trust = None
            if len(named.get("trust") or b""):
                trust = [Path(tmp) / "trusted.cer"]
                trust[0].write_bytes(named["trust"])
            try:
                found = certsign.verify(path, fields.get("password") or None, trust)
            except EXPECTED_ERRORS as exc:
                if not fields.get("quick"):
                    raise
                # Signed, but the signatures couldn't be checked: the reader says so.
                return self._send(HTTPStatus.OK, {"signatures": [], "error": str(exc)})
        return self._send(HTTPStatus.OK, {"signatures": found})

    def _access_check(self, body):
        """The reader's Check accessibility: accessibility.check's result."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = Path(tmp) / "file.pdf"
            path.write_bytes(uploads[0][2])
            result = accessibility.check(path, fields.get("password") or None)
        result.pop("file", None)
        return self._send(HTTPStatus.OK, result)

    def _preflight_check(self, body):
        """Print preflight check: returns preflight.check() JSON report."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        file_id = (fields.get("id") or "").strip()
        opened_path = self.opened.get(file_id) if (file_id and self.opened is not None) else None
        if not uploads and (opened_path is None or not opened_path.is_file()):
            raise UserError("no file")
        min_dpi = float(fields.get("min_dpi") or 300)
        error_dpi = float(fields.get("error_dpi") or 150)
        min_bleed = float(fields.get("min_bleed_mm") or 3.0)
        max_tac = float(fields.get("max_tac") or 300.0)
        profile = (fields.get("profile") or "standard").strip().lower()
        if not uploads and opened_path is not None:
            result = preflight.check(
                opened_path,
                fields.get("password") or None,
                min_dpi=min_dpi,
                error_dpi=error_dpi,
                min_bleed_mm=min_bleed,
                max_tac=max_tac,
                profile=profile,
            )
            return self._send(HTTPStatus.OK, result)
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = Path(tmp) / "file.pdf"
            path.write_bytes(uploads[0][2])
            result = preflight.check(
                path,
                fields.get("password") or None,
                min_dpi=min_dpi,
                error_dpi=error_dpi,
                min_bleed_mm=min_bleed,
                max_tac=max_tac,
                profile=profile,
            )
        return self._send(HTTPStatus.OK, result)

    def _preflight_fix(self, body):
        """Apply safe print preflight fixes and return the updated PDF bytes."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "in.pdf"
            dst = Path(tmp) / "out.pdf"
            src.write_bytes(uploads[0][2])
            preflight.fix(
                src,
                dst,
                password=fields.get("password") or None,
                normalize_size=(fields.get("normalize_size") == "true"),
                add_bleed_mm=float(fields.get("add_bleed_mm") or 0.0),
                add_crop_marks=(fields.get("add_crop_marks") == "true"),
                color_strategy=(fields.get("color_strategy") or None),
                flatten_transparency=(fields.get("flatten_transparency") == "true"),
                fix_white_overprint=(fields.get("fix_white_overprint") == "true"),
                add_pdfx_intent=(fields.get("add_pdfx_intent") == "true"),
            )
            return self._send(
                HTTPStatus.OK,
                dst.read_bytes(),
                "application/pdf",
                {"Content-Disposition": 'attachment; filename="preflight-fixed.pdf"'},
            )

    def _preflight_preview(self, body):
        """Render a true CMYK plate separation ('c', 'm', 'y', 'k', 'all') or TAC heatmap ('tac') PNG."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        file_id = (fields.get("id") or "").strip()
        opened_path = self.opened.get(file_id) if (file_id and self.opened is not None) else None
        if not uploads and (opened_path is None or not opened_path.is_file()):
            raise UserError("no file")
        page = max(1, int(fields.get("page") or 1))
        plate = (fields.get("plate") or "all").strip().lower()
        max_tac = float(fields.get("max_tac") or 300.0)
        dpi = max(48, min(180, int(fields.get("dpi") or 100)))
        password = fields.get("password") or None

        if not uploads and opened_path is not None:
            png_bytes, meta = preflight.render_plate_preview(
                opened_path, password=password, page=page, plate=plate, max_tac=max_tac, dpi=dpi,
            )
        else:
            with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
                path = Path(tmp) / "file.pdf"
                path.write_bytes(uploads[0][2])
                png_bytes, meta = preflight.render_plate_preview(
                    path, password=password, page=page, plate=plate, max_tac=max_tac, dpi=dpi,
                )
        headers = {"X-ZPDF-Plate": str(meta.get("plate", plate))}
        if "max_tac_pct" in meta:
            headers["X-ZPDF-Max-TAC"] = str(meta["max_tac_pct"])
            headers["X-ZPDF-Over-TAC-Pct"] = str(meta.get("over_limit_pct", 0.0))
        return self._send(HTTPStatus.OK, png_bytes, "image/png", headers)

    def _translate_page(self, body):
        """Translate a single page of a PDF without modifying the document."""
        from pdfmanager import translate

        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        file_id = (fields.get("id") or "").strip()
        opened_path = self.opened.get(file_id) if (file_id and self.opened is not None) else None
        if not uploads and (opened_path is None or not opened_path.is_file()):
            raise UserError("no file")
        page_num = int(fields.get("page") or 1)
        src_lang = fields.get("from") or "en"
        dst_lang = fields.get("to") or ""
        if not dst_lang:
            raise UserError("choose a target language")
        if opened_path is not None and opened_path.is_file() and not uploads:
            result = translate.translate_page(
                opened_path, page_num, src_lang, dst_lang, password=fields.get("password") or None,
            )
            return self._send(HTTPStatus.OK, result)
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = Path(tmp) / "file.pdf"
            path.write_bytes(uploads[0][2])
            result = translate.translate_page(
                path, page_num, src_lang, dst_lang, password=fields.get("password") or None,
            )
        return self._send(HTTPStatus.OK, result)

    def _facts(self, body):
        """What a file the page holds is (see filefacts), and what to offer for it."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = Path(tmp) / "file.pdf"
            path.write_bytes(uploads[0][2])
            found = filefacts.facts(path, fields.get("password") or None)
        return self._send(HTTPStatus.OK, {**found, "suggestions": filefacts.suggestions(found, fields.get("made_by") or None)})

    def _inspect(self, body):
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = Path(tmp) / "file.pdf"
            path.write_bytes(uploads[0][2])
            info = gui_hook("inspect_pdf", inspect_pdf)(path, fields.get("password") or None)
        return self._send(HTTPStatus.OK, info)
