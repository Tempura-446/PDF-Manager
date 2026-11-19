# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Form filling and Prepare Form HTTP route handlers."""

import json
import tempfile
from http import HTTPStatus
from pathlib import Path

from pdfmanager import core, filefacts, formdetect, forms
from pdfmanager.server.common import UserError, _bool, _parse_multipart


class FormRoutesMixin:
    """Form inspection, detection, authoring, checking, and import/export endpoints."""

    def _form_fields(self, body):
        """The form fields already in a PDF, for the Add form fields editor."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = Path(tmp) / "file.pdf"
            path.write_bytes(uploads[0][2])
            # "details": also what the editor needs to change them.
            listing = forms.field_details if _bool(fields.get("details")) else forms.existing_fields
            found = listing(path, fields.get("password") or None)
        return self._send(HTTPStatus.OK, {"fields": {str(p): f for p, f in found.items()}})

    def _form_detect(self, body):
        """The fields a flat form should have, for the Add form fields editor:
        of a file opened from this computer (field "id") or of an uploaded copy."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        opened = self._opened_path(fields["id"]) if fields.get("id") else None
        if opened is None and not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = opened
            if path is None:
                path = Path(tmp) / "file.pdf"
                path.write_bytes(uploads[0][2])
            pages = None
            if fields.get("pages"):  # e.g. "1-2": the reader's quick look at the first pages
                try:
                    first, _, last = fields["pages"].partition("-")
                    pages = range(int(first), int(last or first) + 1)
                except ValueError:
                    raise UserError("pages should look like 1-2") from None
            if pages is not None:
                # The quick look, unasked: a scan isn't offered as a form, with
                # or without its text recognised (a contents page's dot leaders
                # look like one, and a scanned form is filled in on paper).
                password = fields.get("password") or None
                seen = filefacts.remembered(path) if opened is not None and not password else filefacts.facts(path, password)
                if seen.get("scanned"):
                    return self._send(HTTPStatus.OK, {"fields": {}})
            found = formdetect.detect(path, fields.get("password") or None, pages=pages, quick=pages is not None)
        return self._send(HTTPStatus.OK, {"fields": {str(p): f for p, f in found.items()}})

    def _form_fillable(self, body):
        """The reader's "Make it fillable": the fields of a printed form added
        to it; sends back the changed PDF (X-Pdfm-Fields: how many)."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "fillable.pdf"
            src.write_bytes(uploads[0][2])
            found = formdetect.detect(src, password)
            if not found:
                raise UserError("no fields found: this page has no lines, boxes or tick boxes to "
                                "fill in. Add form fields lets you draw them")
            forms.add_fields(src, out, [{**f, "page": page} for page, items in found.items()
                                        for f in items], password=password)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)  # stays protected
            data = out.read_bytes()
        places = sum(len(items) for items in found.values())  # (a radio group: each button)
        return self._send(HTTPStatus.OK, data, "application/pdf", {"X-Pdfm-Fields": str(places)})

    def _form_add(self, body):
        """Add form fields to a PDF from the reader's Prepare form mode."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        try:
            items = json.loads(fields.get("fields") or "[]")
        except ValueError:
            raise UserError("invalid fields") from None
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "added.pdf"
            src.write_bytes(uploads[0][2])
            names = forms.add_fields(src, out, items, password=password)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf",
                          {"X-Pdfm-Names": json.dumps(names, ensure_ascii=True)})

    def _form_edit(self, body):
        """Move, resize, rename or delete form fields from the reader's Prepare form mode."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        try:
            changes = json.loads(fields.get("changes") or "[]")
        except ValueError:
            raise UserError("invalid changes") from None
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "edited.pdf"
            src.write_bytes(uploads[0][2])
            result = forms.edit_fields(src, out, changes, password=password)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf",
                          {"X-Pdfm-Result": json.dumps(result, ensure_ascii=True)})

    def _form_copy(self, body):
        """Copy a form field to other pages from Prepare form mode."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        password = fields.get("password") or None
        same_name = str(fields.get("same_name", "1")).lower() not in ("0", "false", "no")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "copied.pdf"
            src.write_bytes(uploads[0][2])
            names = forms.copy_to_pages(
                src, out, int(fields.get("page", 1)), int(fields.get("index", 0)),
                fields.get("pages", "all"), same_name=same_name, password=password)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf",
                          {"X-Pdfm-Names": json.dumps(names, ensure_ascii=True)})

    def _form_table(self, body):
        """Turn a form field into a grid of numbered fields."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "table.pdf"
            src.write_bytes(uploads[0][2])
            names = forms.make_table(
                src, out, int(fields.get("page", 1)), int(fields.get("index", 0)),
                int(fields.get("rows", 2)), int(fields.get("cols", 2)),
                float(fields.get("row_gap", 4)), float(fields.get("col_gap", 4)),
                password=password)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf",
                          {"X-Pdfm-Names": json.dumps(names, ensure_ascii=True)})

    def _form_reorder(self, body):
        """Reorder form fields on a page (tab order) and set /Tabs."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        password = fields.get("password") or None
        mode = str(fields.get("mode", "custom"))
        order = json.loads(fields.get("order") or "null")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "reordered.pdf"
            src.write_bytes(uploads[0][2])
            names = forms.reorder_fields(
                src, out, int(fields.get("page", 1)), order=order, mode=mode, password=password)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf",
                          {"X-Pdfm-Names": json.dumps(names, ensure_ascii=True)})

    def _form_keep_defaults(self, body):
        """Promote current form values (/V) to default values (/DV)."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "defaults.pdf"
            src.write_bytes(uploads[0][2])
            count = forms.keep_defaults(src, out, password=password)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf",
                          {"X-Pdfm-Fields": str(count)})

    def _form_check(self, body):
        """Run pre-finish checks on form fields."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "file.pdf"
            src.write_bytes(uploads[0][2])
            issues = forms.check_form(src, password=password)
        return self._send(HTTPStatus.OK, json.dumps({"issues": issues}, ensure_ascii=False), "application/json")

    def _form_clear(self, body):
        """Reset all form fields to their default values (or clear them)."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "cleared.pdf"
            src.write_bytes(uploads[0][2])
            count = forms.clear_form(src, out, password=password)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf",
                          {"X-Pdfm-Fields": str(count)})

    def _form_export(self, body):
        """Export filled form values as FDF, XFDF, CSV, or JSON."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        password = fields.get("password") or None
        fmt = (fields.get("format") or "json").lower().lstrip(".")
        mimes = {
            "fdf": "application/vnd.fdf",
            "xfdf": "application/vnd.adobe.xfdf",
            "csv": "text/csv; charset=utf-8",
            "json": "application/json; charset=utf-8",
        }
        if fmt not in mimes:
            raise UserError(f"unsupported data format: {fmt}")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / f"data.{fmt}"
            src.write_bytes(uploads[0][2])
            forms.export_data(src, out, fmt=fmt, password=password)
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, mimes[fmt])

    def _form_import(self, body):
        """Fill form fields from an uploaded FDF, XFDF, CSV, or JSON file."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        pdf_up = next((u for u in uploads if u[0] == "file"), uploads[0] if uploads else None)
        data_up = next((u for u in uploads if u[0] == "data"), uploads[1] if len(uploads) > 1 else None)
        if not pdf_up or not data_up:
            raise UserError("choose a data file to import")
        password = fields.get("password") or None
        data_name = Path(data_up[1] or "data.json").name
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "file.pdf"
            dpath = Path(tmp) / data_name
            out = Path(tmp) / "filled.pdf"
            src.write_bytes(pdf_up[2])
            dpath.write_bytes(data_up[2])
            count = forms.import_data(src, dpath, out, password=password)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf",
                          {"X-Pdfm-Fields": str(count)})
