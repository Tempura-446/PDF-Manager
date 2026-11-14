# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Small tools for the file itself: what a password allows, attached files,
page labels, repairing a damaged file and making it quick to open online.

    permissions(printing="low", changes="comments", copy=False)  # for core.encrypt
    attach(path, output, files, remove=(), remove_all=False)
    attachments(path)                    # [{"name", "size", "description"}]
    set_page_labels(path, output, parse_labels("1-4: i; 5-: 1"))
    repair(path, output, linearize=True) # {"warnings": [...], "linearized": bool}
"""

import functools
import mimetypes
import re
from pathlib import Path

from pdfmanager import core
from pdfmanager.pdfpermissions import CHANGES_TEXT, describe, pypdf_flags, restricts  # noqa: F401

# --------------------------------------------------------------------------
# What a password allows (Acrobat's "Permissions")
# --------------------------------------------------------------------------

PRINTING = ("high", "low", "none")
CHANGES = ("any", "comments", "forms", "pages", "none")


def permissions(printing="high", changes="any", copy=True):
    """pikepdf permissions for Acrobat's choices. Screen readers may always
    read the text (blocking them helps nobody)."""
    import pikepdf

    if printing not in PRINTING:
        raise core.PdfManagerError(f"printing must be one of {', '.join(PRINTING)}")
    if changes not in CHANGES:
        raise core.PdfManagerError(f"changes must be one of {', '.join(CHANGES)}")
    return pikepdf.Permissions(
        accessibility=True,
        extract=bool(copy),
        print_highres=printing == "high",
        print_lowres=printing in ("high", "low"),
        modify_other=changes == "any",
        modify_annotation=changes in ("any", "comments"),
        modify_form=changes in ("any", "comments", "forms"),
        modify_assembly=changes in ("any", "pages"),
    )


# --------------------------------------------------------------------------
# Attached files
# --------------------------------------------------------------------------

def attachments(path, password=None):
    """The files attached to a PDF (its EmbeddedFiles): [{"name", "size",
    "description"}]."""
    with _open(path, password) as pdf:
        out = []
        for name, spec in pdf.attachments.items():
            try:
                size = len(spec.get_file().read_bytes())
            except Exception:  # a damaged attachment still has a name
                size = None
            out.append({"name": name, "size": size, "description": spec.description or ""})
        return out


@functools.lru_cache(maxsize=1)
def _builtin_types():
    # Python's own table only: the module-level guess_type also reads the
    # Windows registry (where Excel registers .csv as application/vnd.ms-excel)
    # and /etc/mime.types, so the same file would be labelled per computer.
    return mimetypes.MimeTypes()


def _mime_type(name):
    return _builtin_types().guess_type(name)[0] or "application/octet-stream"


def attach(path, output, files=(), remove=(), remove_all=False, password=None, description=""):
    """Attach ``files`` to a PDF (a file of the same name is replaced), after
    taking away the attachments named in ``remove`` (or all of them).
    Returns (added names, removed names)."""
    import pikepdf

    with _open(path, password) as pdf:
        removed = []
        names = list(pdf.attachments.keys())
        wanted = {str(n).strip() for n in remove if str(n).strip()}
        missing = wanted - set(names)
        if missing:
            raise core.PdfManagerError(
                f"no attached file is called {', '.join(sorted(missing))}"
                + (f" (attached: {', '.join(names)})" if names else " (it has none)"))
        for name in names:
            if remove_all or name in wanted:
                del pdf.attachments[name]
                removed.append(name)
        added = []
        for file in files:
            file = Path(file)
            name = file.name
            mime = _mime_type(name)
            spec = pikepdf.AttachedFileSpec(pdf, file.read_bytes(), filename=name,
                                            description=description or "", mime_type=mime,
                                            relationship=pikepdf.Name.Unspecified)
            pdf.attachments[name] = spec
            added.append(name)
        if not added and not removed:
            raise core.PdfManagerError("choose files to attach, or attachments to remove")
        if added:
            # Open the attachments panel, so readers show what's attached.
            pdf.Root.PageMode = pikepdf.Name.UseAttachments
        elif not pdf.attachments and pdf.Root.get("/PageMode") == "/UseAttachments":
            del pdf.Root["/PageMode"]
        _save(pdf, output)
    return added, removed


# --------------------------------------------------------------------------
# Page labels (i, ii, iii… then 1, 2, 3)
# --------------------------------------------------------------------------

STYLES = {"1": "/D", "i": "/r", "I": "/R", "a": "/a", "A": "/A", "": None}
STYLE_NAMES = {"1": "1, 2, 3", "i": "i, ii, iii", "I": "I, II, III", "a": "a, b, c",
               "A": "A, B, C", "": "no number (prefix only)"}


def parse_labels(spec):
    """'1-4: i; 5-: 1; 30-: A-1' -> [(first page, style, prefix, start)].
    Each part: the page it starts on (a range's end is ignored: a label
    runs until the next one), then the first label: a number in its style,
    after an optional prefix ("A-1", "Appendix i")."""
    parts = []
    for raw in re.split(r"[;\n]+", spec or ""):
        raw = raw.strip()
        if not raw:
            continue
        m = re.fullmatch(r"(\d+)\s*(?:-\s*\d*)?\s*:\s*(.*)", raw)
        if not m:
            raise core.PdfManagerError(
                f"can't read the page label {raw!r}; write it like 1-4: i; 5-: 1; 30-: A-1")
        page, label = int(m.group(1)), m.group(2)
        parts.append((page, *_label_style(label)))
    return parts


_ROMAN = re.compile(r"(?i)^(m{0,3})(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")


def _roman_value(text):
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total, last = 0, 0
    for ch in reversed(text.lower()):
        value = values[ch]
        total += -value if value < last else value
        last = max(last, value)
    return total


def _label_style(label):
    """'A-1' -> ('1', 'A-', 1); 'iv' -> ('i', '', 4); 'c' -> ('a', '', 3)."""
    label = label.strip()
    if not label:
        return "", "", 1
    m = re.fullmatch(r"(.*?)(\d+)", label)
    if m:
        return "1", m.group(1), int(m.group(2))
    # A letter or numeral counts only on its own or after a separator
    # ("A-c", "Appendix iv"), so "Cover" stays a word.
    m = re.fullmatch(r"(|.*[^A-Za-z])([ivxlcdmIVXLCDM]+)", label)
    if m and _ROMAN.match(m.group(2)) and m.group(2) in (m.group(2).lower(), m.group(2).upper()):
        word = m.group(2)
        # "c", "d", "i" alone are letters as often as numerals; a single
        # letter other than i/I is read as a letter.
        if len(word) > 1 or word in "iI":
            return ("i" if word.islower() else "I"), m.group(1), _roman_value(word)
    m = re.fullmatch(r"(|.*[^A-Za-z])([a-zA-Z])", label)
    if m:
        letter = m.group(2)
        return ("a" if letter.islower() else "A"), m.group(1), ord(letter.lower()) - 96
    return "", label, 1  # a prefix only, e.g. "Cover"


def set_page_labels(path, output, labels, password=None):
    """Label pages: ``labels`` is [(first page (1-based), style, prefix,
    start)] with style one of STYLES. An empty list removes the labels."""
    from pypdf.generic import (ArrayObject, DictionaryObject, NameObject, NumberObject,
                               TextStringObject)

    reader = core.open_pdf(path, password)
    writer = core.clone_document(reader)
    total = len(writer.pages)
    if "/PageLabels" in writer._root_object:
        del writer._root_object["/PageLabels"]
    seen = set()
    nums = ArrayObject()
    for page, style, prefix, start in sorted(labels, key=lambda p: p[0]):
        if not 1 <= page <= total:
            raise core.PdfManagerError(f"page {page} doesn't exist (the PDF has {total})")
        if page in seen:
            raise core.PdfManagerError(f"page {page} has two labels")
        if style not in STYLES:
            raise core.PdfManagerError(f"unknown numbering style {style!r}")
        seen.add(page)
        entry = DictionaryObject()
        if STYLES[style]:
            entry[NameObject("/S")] = NameObject(STYLES[style])
        if prefix:
            entry[NameObject("/P")] = TextStringObject(prefix)
        if int(start) != 1:
            entry[NameObject("/St")] = NumberObject(max(1, int(start)))
        nums.extend([NumberObject(page - 1), entry])
    if nums:
        if nums[0] != 0:  # pages before the first label count normally
            nums[0:0] = [NumberObject(0), DictionaryObject({NameObject("/S"): NameObject("/D")})]
        writer._root_object[NameObject("/PageLabels")] = DictionaryObject(
            {NameObject("/Nums"): nums})
    return core._write(writer, output)


def page_labels(path, password=None):
    """Each page's label ("i", "ii", "1"…), or None when the PDF has none."""
    reader = core.open_pdf(path, password)
    if "/PageLabels" not in reader.trailer["/Root"]:
        return None
    return list(reader.page_labels)


# --------------------------------------------------------------------------
# Repair and fast web view
# --------------------------------------------------------------------------

def repair(path, output, linearize=True, password=None):
    """Rewrite a PDF with qpdf, which mends broken cross-reference tables,
    lengths and streams on the way; with ``linearize`` the first page comes
    first in the file, so it opens before the whole file has downloaded
    ("Fast web view"). Returns {"warnings": what qpdf mended, "linearized"}."""
    import pikepdf

    try:
        pdf = pikepdf.open(path, password=password or "")
    except pikepdf.PasswordError:
        raise core.PdfManagerError(f"{path} is password-protected: give its password") from None
    except pikepdf.PdfError as exc:
        raise core.PdfManagerError(f"{path} is too damaged to repair ({exc})") from None
    with pdf:
        warnings = [str(w) for w in pdf.get_warnings()]
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_name(output.name + ".tmp")
        # (Saved unprotected: the app and the command line protect the result
        # again with the password the file had.)
        core.keep_size(pdf).save(tmp, linearize=linearize,
                 object_stream_mode=pikepdf.ObjectStreamMode.generate if not linearize
                 else pikepdf.ObjectStreamMode.preserve,
                 compress_streams=True, recompress_flate=False)
        warnings += [str(w) for w in pdf.get_warnings() if str(w) not in warnings]
    tmp.replace(output)
    return {"warnings": warnings, "linearized": bool(linearize)}


def is_linearized(path):
    with open(path, "rb") as f:
        head = f.read(1024)
    return b"/Linearized" in head


# --------------------------------------------------------------------------

def _open(path, password):
    import pikepdf

    try:
        return pikepdf.open(path, password=password or "")
    except pikepdf.PasswordError:
        raise core.PdfManagerError(f"{path} is password-protected: give its password") from None
    except pikepdf.PdfError as exc:
        raise core.PdfManagerError(f"{path} can't be read ({exc})") from None


def _save(pdf, output):
    import pikepdf

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # (Saved unprotected: the app and the command line protect the result
    # again with the password the file had.)
    tmp = output.with_name(output.name + ".tmp")
    pdf.save(tmp, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    tmp.replace(output)
    return output
