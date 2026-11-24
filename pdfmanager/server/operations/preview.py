# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Operation preview thumbnails and PDF inspection helpers."""

import logging
import base64
import io
import tempfile
from pathlib import Path

from pdfmanager.server.common import EXPECTED_ERRORS, UserError, _safe_name
from pdfmanager.server.operations._deps import certcrypt, core
from pdfmanager.server.operations.repair import _damaged_errors, _plainly
from pdfmanager.server.operations.runner import _run_operation, run_operation

# --------------------------------------------------------------------------
# Thumbnails and results
# --------------------------------------------------------------------------

THUMB_WIDTH = 240


# Tools whose result can be seen before running them: page 1 of it, redrawn
# as the options change.
PREVIEW_OPS = {"watermark", "number", "headers", "resize", "nup", "booklet"}
PREVIEW_PAGES = 40  # longer documents are previewed from their first pages only


def preview_operation(op, data, filename, opts, password=None, width=960):
    """See _preview_operation; a damaged file gives a plain message, not a crash."""
    try:
        return _preview_operation(op, data, filename, opts, password, width)
    except _damaged_errors() as exc:
        raise _plainly(exc) from None


def _preview_operation(op, data, filename, opts, password=None, width=960):
    """What ``op`` makes of the first page(s) of a PDF (``data``): page 1 of
    the result as PNG bytes, ``width`` pixels wide."""
    if op not in PREVIEW_OPS:
        raise UserError(f"no preview for {op!r}")
    import pypdfium2 as pdfium

    with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
        tmp = Path(tmp)
        (tmp / "in").mkdir()
        src = tmp / "in" / _safe_name(filename or "file.pdf")
        src.write_bytes(data)
        if len(core.open_pdf(src, password).pages) > PREVIEW_PAGES:
            (tmp / "first").mkdir()
            first = tmp / "first" / src.name
            core.select_pages(src, first, "1-9", password)  # enough for any sheet of pages
            src = first
        (tmp / "work").mkdir()
        _, result, _ = run_operation(op, [src], opts, password, {}, tmp / "work", [])
        with core.PDFIUM_LOCK:
            doc = pdfium.PdfDocument(result, password=password)
            try:
                page = core.show_whole_page(doc[0])
                image = page.render(scale=width / max(1.0, page.get_width()), draw_annots=True).to_pil()
                page.close()
            finally:
                doc.close()
    out = io.BytesIO()
    image.convert("RGB").save(out, "PNG", optimize=False)
    return out.getvalue()


def inspect_pdf(path, password=None):
    """See _inspect_pdf; a damaged file gives a plain message, not a crash."""
    try:
        return _inspect_pdf(path, password)
    except _damaged_errors() as exc:
        raise _plainly(exc) from None


def _inspect_pdf(path, password=None):
    """Page count, size label and a first-page thumbnail (data URL) for the
    file cards and the result preview."""
    try:
        reader = core.open_pdf(path, password)
    except core.PdfManagerError as exc:
        said = str(exc).replace(str(path), Path(path).name)  # the file's name, not where it's kept
        if certcrypt.is_for_people(path):
            # Not damaged and not asking for a password: it opens with a Digital ID.
            return {"pages": None, "locked": False, "error": said, "for_people": True}
        locked = "encrypted" in str(exc) or "password" in str(exc)
        return {"pages": None, "locked": locked, "error": None if locked else said}
    info = {"pages": len(reader.pages), "locked": False, "error": None, "thumb": None}
    try:
        import pypdfium2 as pdfium

        with core.PDFIUM_LOCK:
            doc = pdfium.PdfDocument(str(path), password=password)
            try:
                page = core.show_whole_page(doc[0])
                scale = THUMB_WIDTH / max(page.get_width(), 1)
                image = page.render(scale=min(scale, 2.0)).to_pil().convert("RGB")
            finally:
                doc.close()
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=82)
        info["thumb"] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as _exc:
        logging.getLogger("zpdf").debug("preview._inspect_pdf: %s", _exc)  # thumbnails are a nicety; never fail because of them
    return info
