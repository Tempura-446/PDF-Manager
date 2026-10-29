"""Archiving and privacy: flatten forms and comments into the page, remove
hidden data, and convert to PDF/A for long-term archiving."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

from pdfmanager import core

# Annotation flags (PDF 32000-1, 12.5.3).
HIDDEN, NO_VIEW = 2, 32


# --------------------------------------------------------------------------
# Flatten
# --------------------------------------------------------------------------

def _appearance(annot):
    """The normal appearance stream of an annotation, or None."""
    ap = annot.get("/AP")
    if ap is None:
        return None
    normal = ap.get_object().get("/N")
    if normal is None:
        return None
    normal = normal.get_object()
    if "/BBox" not in normal:  # a dictionary of states, e.g. a check box's /On and /Off
        state = annot.get("/AS")
        normal = normal.get(state).get_object() if state is not None and state in normal else None
    return normal


def _placement(form, rect):
    """Matrix that maps the appearance's (transformed) bounding box onto the
    annotation rectangle (PDF 32000-1, 12.5.5)."""
    x0, y0, x1, y1 = (float(v) for v in form["/BBox"])
    a, b, c, d, e, f = (float(v) for v in form.get("/Matrix", [1, 0, 0, 1, 0, 0]))
    corners = [(a * x + c * y + e, b * x + d * y + f) for x in (x0, x1) for y in (y0, y1)]
    bx0, by0 = min(p[0] for p in corners), min(p[1] for p in corners)
    bx1, by1 = max(p[0] for p in corners), max(p[1] for p in corners)
    rx0, ry0, rx1, ry1 = (float(v) for v in rect)
    rx0, rx1 = sorted((rx0, rx1))
    ry0, ry1 = sorted((ry0, ry1))
    sx = (rx1 - rx0) / (bx1 - bx0) if bx1 > bx0 else 1
    sy = (ry1 - ry0) / (by1 - by0) if by1 > by0 else 1
    return sx, 0, 0, sy, rx0 - bx0 * sx, ry0 - by0 * sy


def _text_values(reader, all_fields):
    """{field name: value} of the filled-in text fields, or with
    ``all_fields`` False only of those whose widget has nothing to draw."""
    from pdfmanager.forms import _field_of, _inherited, _qualified_name

    values = {}
    for page in reader.pages:
        for ref in page.get("/Annots") or []:
            annot = ref.get_object()
            if not isinstance(annot, DictionaryObject) or annot.get("/Subtype") != "/Widget":
                continue
            value = _inherited(annot, "/V")
            if _inherited(annot, "/FT") == "/Tx" and value and (all_fields or _appearance(annot) is None):
                values[_qualified_name(_field_of(annot))] = value
    return values


def _flatten_page(writer, page, forms, comments, number):
    """Draw the chosen annotations into the page content and remove them.
    Returns the ids of removed widget annotations."""
    annots = page.get("/Annots")
    if annots is None:
        return set()
    keep, drawn, removed_widgets = ArrayObject(), [], set()
    for ref in annots.get_object():
        annot = ref.get_object()
        if not isinstance(annot, DictionaryObject):
            continue  # a null or missing annotation: viewers skip it too
        subtype = annot.get("/Subtype")
        if subtype == "/Widget":
            if not forms:
                keep.append(ref)
                continue
            removed_widgets.add(getattr(ref, "idnum", id(annot)))
        elif subtype in ("/Link",) or not comments:
            keep.append(ref)
            continue
        if subtype == "/Popup":
            continue  # the pop-up note window of a comment; nothing to draw
        flags = int(annot.get("/F", 0))
        if flags & (HIDDEN | NO_VIEW):
            continue
        form = _appearance(annot)
        if form is None or "/Rect" not in annot:
            # Readers draw notes, shapes and highlights without an appearance
            # stream themselves; there is nothing to burn in, so keep them.
            if subtype != "/Widget":
                keep.append(ref)
            continue
        drawn.append((form, _placement(form, annot["/Rect"])))
    if not drawn and len(keep) == len(annots.get_object()):
        return removed_widgets
    if drawn:
        resources = page.get("/Resources")
        resources = resources.get_object() if resources is not None else DictionaryObject()
        page[NameObject("/Resources")] = resources
        xobjects = resources.get("/XObject")
        xobjects = xobjects.get_object() if xobjects is not None else DictionaryObject()
        resources[NameObject("/XObject")] = xobjects
        ops = [b"Q"]  # the page's own drawing is wrapped in q ... Q first
        for k, (form, (a, b, c, d, e, f)) in enumerate(drawn):
            name = f"/PdfmFlat{number}_{k}"
            xobjects[NameObject(name)] = form.indirect_reference or writer._add_object(form)
            ops.append(f"q {a:.6f} {b:.6f} {c:.6f} {d:.6f} {e:.4f} {f:.4f} cm {name} Do Q".encode())
        before, after = DecodedStreamObject(), DecodedStreamObject()
        before.set_data(b"q\n")
        after.set_data(b"\n" + b"\n".join(ops) + b"\n")
        streams = ArrayObject([writer._add_object(before)])
        existing = page.get("/Contents")
        if existing is not None:
            existing_obj = existing.get_object()
            streams.extend(existing_obj if isinstance(existing_obj, ArrayObject) else [existing])
        streams.append(writer._add_object(after))
        page[NameObject("/Contents")] = streams
    if keep:
        page[NameObject("/Annots")] = keep
    else:
        del page["/Annots"]
    return removed_widgets


def flatten(path, output, forms=True, comments=True, password=None):
    """Make form fields and/or comments (highlights, notes, drawings,
    stamps) part of the page, so they look the same everywhere and can no
    longer be changed. Links keep working."""
    reader = core.open_pdf(path, password)
    writer = core.clone_document(reader)
    if forms and "/AcroForm" in writer._root_object:
        # Give filled-in text fields an appearance first; some programs only
        # store the value and leave drawing it to the viewer. Fields that
        # have one keep it: pypdf's replacement would lose the border,
        # background and text placement the file's producer drew.
        try:
            acroform = writer._root_object["/AcroForm"].get_object()
            stale = bool(acroform.get("/NeedAppearances"))  # the viewer was to redraw them all
            writer.set_need_appearances_writer(False)
            values = _text_values(reader, all_fields=stale)
            if values:
                writer.update_page_form_field_values(None, values, auto_regenerate=False)
        except Exception:
            pass  # keep whatever appearances the file has
    for number, page in enumerate(writer.pages):
        _flatten_page(writer, page, forms, comments, number)
    if forms:
        for key in ("/AcroForm",):
            if key in writer._root_object:
                del writer._root_object[key]
    return core._write(writer, output)


# --------------------------------------------------------------------------
# Remove hidden data
# --------------------------------------------------------------------------

def _drop(obj, *keys):
    removed = 0
    for key in keys:
        if key in obj:
            del obj[key]
            removed += 1
    return removed


SCRIPT_ACTIONS = ("/JavaScript", "/Launch")


def _drop_scripts(holder, key, seen=None):
    """Remove the action at ``holder[key]`` if it runs a script or a
    program, or any such action chained to a harmless one there through
    /Next. Returns how many were removed."""
    action = holder[key] if isinstance(holder, ArrayObject) else holder.get(key)
    action = action.get_object() if action is not None else None
    if not isinstance(action, DictionaryObject):
        return 0
    if action.get("/S") in SCRIPT_ACTIONS:
        del holder[key]
        return 1
    seen = set() if seen is None else seen
    if id(action) in seen:  # a chain that loops back on itself
        return 0
    seen.add(id(action))
    chained = action.get("/Next")
    chained = chained.get_object() if chained is not None else None
    if isinstance(chained, ArrayObject):
        return sum(_drop_scripts(chained, i, seen) for i in range(len(chained) - 1, -1, -1))
    return _drop_scripts(action, "/Next", seen)


def _walk(start, *keys):
    """Every dictionary in a tree of bookmarks or form fields, once each,
    from ``start`` (a dictionary or an array of them) along ``keys``."""
    pending, seen = [start], set()
    while pending:
        item = pending.pop()
        item = item.get_object() if item is not None else None
        if isinstance(item, ArrayObject):
            pending.extend(item)
            continue
        if not isinstance(item, DictionaryObject) or id(item) in seen:
            continue
        seen.add(id(item))
        yield item
        pending.extend(item.get(key) for key in keys)


def sanitize(path, output, metadata=True, javascript=True, attachments=True, comments=False,
             password=None):
    """Remove hidden data. Returns a dict counting what was removed:
    metadata (document info and XMP), JavaScript and launch actions,
    embedded files, and optionally comments (annotations other than links
    and form fields)."""
    reader = core.open_pdf(path, password)
    writer = core.clone_document(reader)
    root = writer._root_object
    counts = {"metadata": 0, "javascript": 0, "attachments": 0, "comments": 0}

    if metadata:
        info = writer._info.get_object() if writer._info is not None else None
        if info is not None:
            counts["metadata"] += len([k for k in list(info) if k != "/Producer"])
            for key in list(info):
                del info[key]
        counts["metadata"] += _drop(root, "/Metadata", "/PieceInfo")
        for page in writer.pages:
            counts["metadata"] += _drop(page, "/Metadata", "/PieceInfo", "/Thumb")

    names = root.get("/Names")
    names = names.get_object() if names is not None else None
    if javascript:
        if names is not None:
            counts["javascript"] += _drop(names, "/JavaScript")
        counts["javascript"] += _drop_scripts(root, "/OpenAction")
        counts["javascript"] += _drop(root, "/AA")
        acroform = root.get("/AcroForm")
        if acroform is not None:
            acroform = acroform.get_object()
            _drop(acroform, "/XFA", "/CO")  # XFA forms can carry scripts; /CO orders calculations
            # Calculate, format and validate scripts sit on the fields, not
            # on their widgets on the pages.
            for field in _walk(acroform.get("/Fields"), "/Kids"):
                counts["javascript"] += _drop(field, "/AA")
        for item in _walk(root.get("/Outlines"), "/First", "/Next"):
            counts["javascript"] += _drop_scripts(item, "/A")
    if attachments:
        if names is not None:
            counts["attachments"] += _drop(names, "/EmbeddedFiles")
        counts["attachments"] += _drop(root, "/AF", "/Collection")

    for page in writer.pages:
        if javascript:
            counts["javascript"] += _drop(page, "/AA")
        annots = page.get("/Annots")
        if annots is None:
            continue
        keep = ArrayObject()
        for ref in annots.get_object():
            annot = ref.get_object()
            if not isinstance(annot, DictionaryObject):
                continue  # a null or missing annotation: viewers skip it too
            subtype = annot.get("/Subtype")
            if attachments and subtype == "/FileAttachment":
                counts["attachments"] += 1
                continue
            if comments and subtype not in ("/Link", "/Widget"):
                counts["comments"] += 1
                continue
            if javascript:
                counts["javascript"] += _drop_scripts(annot, "/A")
                counts["javascript"] += _drop(annot, "/AA")
            keep.append(ref)
        if keep:
            page[NameObject("/Annots")] = keep
        else:
            del page["/Annots"]
    if names is not None and not names:
        del root["/Names"]
    if metadata:
        writer._info = None
        writer.add_metadata({"/Producer": "ZPDF"})
    core._write(writer, output)
    return counts


# --------------------------------------------------------------------------
# PDF/A
# --------------------------------------------------------------------------

PDFA_LEVELS = ("1", "2", "3")


def find_ghostscript():
    for name in ("gs", "gswin64c", "gswin32c"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
            if base and (Path(base) / "gs").is_dir():
                for exe in sorted((Path(base) / "gs").glob("gs*/bin/gswin*c.exe"), reverse=True):
                    return str(exe)
    return None


def _ps_text(text):
    """``text`` as a PostScript hex string holding a PDF text string
    (UTF-16 with a byte-order mark), so any character survives."""
    return "<" + ("\ufeff" + text).encode("utf-16-be").hex() + ">"


def to_pdfa(path, output, level="2", password=None):
    """Convert to PDF/A-1b, -2b (default) or -3b for long-term archiving:
    fonts embedded, colours tied to an sRGB profile, no encryption or
    scripts. Needs Ghostscript."""
    if str(level) not in PDFA_LEVELS:
        raise core.PdfManagerError("PDF/A level must be 1, 2 or 3")
    gs = find_ghostscript()
    if not gs:
        raise core.PdfManagerError(
            "PDF/A needs Ghostscript (https://ghostscript.com/releases/gsdnld.html)")
    from PIL import ImageCms

    output = Path(output)
    with tempfile.TemporaryDirectory(prefix="pdfm-pdfa-") as tmp:
        tmp = Path(tmp)
        source = tmp / "in.pdf"
        reader = core.open_pdf(path, password)
        if reader.is_encrypted:
            core.decrypt(path, source, password or "")
        else:
            shutil.copyfile(path, source)
        # Scripts and attachments aren't allowed in PDF/A-1 and -2.
        sanitize(source, source, metadata=False, javascript=True,
                 attachments=str(level) != "3")
        # The sRGB profile goes into the PostScript as hex strings (a string
        # holds at most 64 KB) rather than by file name: Ghostscript reads
        # names as UTF-8, and the temp folder is under the user's name,
        # which can be in any alphabet.
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes().hex()
        chunks = [icc[i:i + 60000] for i in range(0, len(icc), 60000)]
        title = (reader.metadata or {}).get("/Title") or output.stem
        definition = tmp / "pdfa_def.ps"
        definition.write_text(
            "%!\n"
            f"[ /Title {_ps_text(str(title))} /DOCINFO pdfmark\n"
            "[/_objdef {icc_PDFA} /type /stream /OBJ pdfmark\n"
            "[{icc_PDFA} <</N 3>> /PUT pdfmark\n"
            + "".join(f"[{{icc_PDFA}} <{chunk}> /PUT pdfmark\n" for chunk in chunks)
            + "[/_objdef {OutputIntent_PDFA} /type /dict /OBJ pdfmark\n"
            "[{OutputIntent_PDFA} <</Type /OutputIntent /S /GTS_PDFA1 "
            "/DestOutputProfile {icc_PDFA} /OutputConditionIdentifier (sRGB)>> /PUT pdfmark\n"
            "[{Catalog} <</OutputIntents [ {OutputIntent_PDFA} ]>> /PUT pdfmark\n",
            encoding="ascii")
        result = tmp / "out.pdf"
        command = [gs, f"-dPDFA={level}", "-dBATCH", "-dNOPAUSE", "-dQUIET", "-dNOOUTERSAVE",
                   "-dPDFACompatibilityPolicy=1", "-sColorConversionStrategy=RGB",
                   "-sProcessColorModel=DeviceRGB", "-sDEVICE=pdfwrite",
                   f"--permit-file-read={tmp.as_posix()}/", f"-sOutputFile={result}",
                   str(definition), str(source)]
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=600,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise core.PdfManagerError(f"Ghostscript failed: {exc}") from None
        if run.returncode or not result.exists():
            detail = (run.stderr or run.stdout).strip().splitlines()[-3:]
            raise core.PdfManagerError("Ghostscript could not convert the file: "
                                       + " ".join(detail))
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(result), str(output))
    return output


def pdfa_level(path):
    """The PDF/A part claimed in a file's XMP metadata ("2B", ...), or None."""
    reader = core.open_pdf(path)
    xmp = reader.xmp_metadata
    if xmp is None:
        return None
    raw = xmp.stream.get_data().decode("utf-8", "replace")
    import re

    part = re.search(r"pdfaid:part(?:>|=[\"'])\s*(\d)", raw)
    conf = re.search(r"pdfaid:conformance(?:>|=[\"'])\s*([ABUabu])", raw)
    return (part.group(1) + (conf.group(1).upper() if conf else "")) if part else None
