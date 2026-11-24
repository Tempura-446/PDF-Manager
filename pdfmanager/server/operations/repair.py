# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Output protection, damaged-PDF repair-before-run, and error translation helpers."""

import logging
import re
import shutil
from pathlib import Path

from pdfmanager import applog, filefacts
from pdfmanager.server.common import EXPECTED_ERRORS, UserError, _bool, _file_mentions, _pages
from pdfmanager.server.operations._deps import core, filetools, organize, protection
from pdfmanager.server.operations.catalog import OPS

def _protect(outputs, password, op):
    """PDFs made from a password-protected file get the same password."""
    # A signed PDF keeps its password inside the signed file.
    # (An upgraded protection is already in place, with its two passwords.)
    if password and op not in ("decrypt", "encrypt", "pdfa", "media", "sign-cert", "upgrade-protection",
                               "encrypt-cert", "decrypt-cert"):
        for path in outputs:
            if path.suffix.lower() == ".pdf":
                core.encrypt(path, path, password)


def _was_protected(path, password):
    """Whether an input needed the typed password: only results made from
    such a file are protected again. The page sends the one password box
    with every run, also for the files in the batch that were never locked."""
    if not password or path.suffix.lower() != ".pdf":
        return False
    try:
        from pypdf import PdfReader
        return PdfReader(str(path)).is_encrypted
    except Exception:
        return False  # not a readable PDF: the tool itself reports that


def _flatten_outputs(outputs, out_dir):
    """Move per-input outputs into ``out_dir``, renaming on clashes."""
    taken, flat = set(), []
    for sub, path in outputs:
        target = organize._unique(out_dir / path.relative_to(sub), taken)
        target.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target)
        flat.append(target)
    return flat


def _damaged_errors():
    """The errors the PDF libraries raise for a file they can't make sense
    of: a damaged file is the file's problem, told plainly, not a crash."""
    import pikepdf
    import pypdfium2
    from pdfminer.psexceptions import PSException
    from pdfplumber.utils.exceptions import MalformedPDFException, PdfminerException
    from PIL import Image
    from pypdf.errors import PyPdfError

    return (PyPdfError, pikepdf.PdfError, pypdfium2.PdfiumError, Image.DecompressionBombError,
            PSException, PdfminerException, MalformedPDFException)


def _plainly(exc):
    from PIL import Image

    if isinstance(exc, Image.DecompressionBombError):
        return UserError("a page is too large to work with (it would make an image of hundreds of "
                         "millions of pixels)")
    reason = " ".join(str(exc).split())[:160]
    return UserError("the PDF is damaged or unusual and couldn't be processed"
                     + (f" ({reason})" if reason else ""))


def _rebuildable(exc):
    try:
        return str(type(exc)("x")) == "x"
    except Exception:
        return False


def _tidy(message, work):
    """``message`` naming files, not the app's working folder: the uploads
    sit in <work>/in/<n>/<name> and the results in <work>/out/..."""
    if not work:
        return message
    for folder in {str(Path(work)), str(Path(work).resolve())}:
        message = re.sub(re.escape(folder) + r"[\\/](?:in[\\/]o?\d+|out)[\\/]", "", message)
        message = message.replace(folder, "")
    return message


def _needs_mending(path, password):
    """Whether ``path`` is damaged in a way qpdf mends and pypdf or pdfium
    don't: qpdf had to rebuild its cross-reference table (a file cut short,
    a download that stopped, a wrong index of objects), and pypdf or pdfium
    can't open it, lose its trailer or count other pages. A file all three
    read alike is left as it is."""
    try:
        with core.open_pikepdf(path, password) as pdf:
            if not any("reconstruct cross-reference" in str(w) for w in pdf.get_warnings()):
                return False
            if "/Perms" in pdf.Root or _file_mentions(path, b"/ByteRange"):
                return False  # signed: rewriting it would break the signatures
            pages = len(pdf.pages)
    except Exception:  # not readable at all, or locked: the tool says so itself
        return False
    import pypdfium2 as pdfium

    try:
        reader = core.open_pdf(path, password)
        if "/Size" not in reader.trailer or len(reader.pages) != pages:
            return True
        with core.PDFIUM_LOCK:
            doc = pdfium.PdfDocument(str(path), password=password)
            try:
                return len(doc) != pages
            finally:
                doc.close()
    except Exception:  # noqa: BLE001 (pypdf or pdfium can't read it)
        return True


def _mend_inputs(op, files, password, work, notes):
    """``files``, each damaged one replaced by a copy mended as Repair does,
    with a note saying so (Repair itself mends the file as it is)."""
    if op == "repair" or not work or OPS[op].get("files") not in ("pdf", "pdf2"):
        return files

    mended = []
    for index, path in enumerate(files):
        path = Path(path)
        if path.suffix.lower() == ".pdf" and _needs_mending(path, password):
            target = Path(work) / "mended" / f"{index:03d}" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with core.open_pikepdf(path, password) as pdf:
                    core.keep_size(pdf).save(target, encryption=pdf.is_encrypted or None)  # (still locked if it was)
                notes.append(f"{path.name} was damaged; it was mended first, as Repair does")
                path = target
            except Exception as _exc:  # noqa: BLE001 (it couldn't be mended: the tool tries the file as it is)
                logging.getLogger("zpdf").debug("repair._mend_inputs: %s", _exc)
        mended.append(path)
    return mended


def _check_pages(op, files, password):
    """Each PDF has a page to work on (Repair is for files that don't open)."""
    if op in ("repair", "decrypt-cert") or OPS[op].get("files") not in ("pdf", "pdf2"):
        return  # (a file protected for people has no page to count until it's opened)
    for path in files:
        if Path(path).suffix.lower() == ".pdf" and not len(core.open_pdf(path, password).pages):
            raise UserError(f"{Path(path).name} has no pages")
