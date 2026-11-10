"""What a PDF is, at a glance: the facts the start screen shows beside a
recent file, and the suggestions for an open one start from.

Quick by design: it reads the file's structure and the first few pages'
instructions, and never draws a page. It never fails either: a file it
can't read says so in its facts.
"""
import json
import re
from pathlib import Path

from pdfmanager import appdata

SAMPLE = 10            # pages looked at for text and scans
BIG_PICTURE = 400_000  # pixels from which a picture can be a page's scan
REMEMBERED = 300       # files whose facts are kept
TEXT_SHOWING = {"Tj", "TJ", "'", '"'}


def _page_look(page, pikepdf):
    """(shows text, holds a picture big enough to be its scan)."""
    text = picture = False
    resources = page.get("/Resources")
    seen = set()

    def pictures(holder, depth=0):
        nonlocal picture
        xobjects = holder.get("/XObject") if isinstance(holder, pikepdf.Dictionary) else None
        if not isinstance(xobjects, pikepdf.Dictionary):
            return
        for xobject in xobjects.values():
            if not isinstance(xobject, pikepdf.Stream) or xobject.objgen in seen:
                continue
            seen.add(xobject.objgen)
            kind = xobject.get("/Subtype")
            if kind == "/Image":
                if int(xobject.get("/Width", 0)) * int(xobject.get("/Height", 0)) >= BIG_PICTURE:
                    picture = True
            elif kind == "/Form" and depth < 3:
                # A scan kept sharp by Compress is a form drawing its layers.
                pictures(xobject.get("/Resources"), depth + 1)

    pictures(resources)
    try:
        for instruction in pikepdf.parse_content_stream(page):
            if str(instruction.operator) in TEXT_SHOWING:
                text = True
                break
    except Exception:
        pass
    return text, picture


def _fields(pdf, pikepdf):
    """(fields to fill in, signatures made)."""
    form = pdf.Root.get("/AcroForm")
    fields = form.get("/Fields") if isinstance(form, pikepdf.Dictionary) else None
    filled = signed = 0
    stack = list(fields) if isinstance(fields, pikepdf.Array) else []
    seen = 0
    while stack and seen < 5000:
        seen += 1
        field = stack.pop()
        if not isinstance(field, pikepdf.Dictionary):
            continue
        kids = field.get("/Kids")
        named = [k for k in kids if isinstance(k, pikepdf.Dictionary) and "/T" in k] \
            if isinstance(kids, pikepdf.Array) else []
        if named:
            stack.extend(named)
        elif field.get("/FT") == "/Sig":
            signed += "/V" in field
        elif "/FT" in field or "/T" in field:
            filled += 1
    return filled, signed


def _pdfa(pdf, pikepdf):
    meta = pdf.Root.get("/Metadata")
    if not isinstance(meta, pikepdf.Stream):
        return None
    try:
        text = meta.read_bytes().decode("utf-8", "replace")
    except Exception:
        return None
    part = re.search(r"pdfaid:part(?:>|=[\"'])\s*(\d)", text)
    conformance = re.search(r"pdfaid:conformance(?:>|=[\"'])\s*([ABUabu])", text)
    return (part.group(1) + (conformance.group(1).upper() if conformance else "")) if part else None


def facts(path, password=None):
    """{"size", "pages", "protected", "scanned", "has_text", "form_fields",
    "signatures", "tagged", "pdfa"} of the PDF at ``path``. A file that
    needs a password it wasn't given has "protected" and no page count; one
    that can't be read has {"size", "pages": None, "error"}."""
    import pikepdf

    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        return {"size": 0, "pages": None, "error": "can't be read"}
    try:
        pdf = pikepdf.open(path, password=password or "")
    except pikepdf.PasswordError:
        return {"size": size, "pages": None, "protected": True}
    except Exception:
        return {"size": size, "pages": None, "error": "can't be read"}
    try:
        with pdf:
            count = len(pdf.pages)
            texts = pictures = sampled = 0
            for page in list(pdf.pages)[:SAMPLE]:
                sampled += 1
                text, picture = _page_look(page, pikepdf)
                texts += text
                pictures += picture
            filled, signed = _fields(pdf, pikepdf)
            marked = pdf.Root.get("/MarkInfo")
            return {
                "size": size, "pages": count, "protected": bool(pdf.is_encrypted),
                "scanned": bool(sampled) and pictures >= 0.6 * sampled,
                "has_text": texts > 0, "form_fields": filled, "signatures": signed,
                "tagged": isinstance(marked, pikepdf.Dictionary) and marked.get("/Marked") is True
                and "/StructTreeRoot" in pdf.Root,
                "pdfa": _pdfa(pdf, pikepdf),
            }
    except Exception:  # a file damaged past its first page
        return {"size": size, "pages": None, "error": "can't be read"}


def _store():
    return appdata.data_dir() / "filefacts.json"


def remembered(path):
    """facts(path), kept from one look to the next for as long as the file
    is the same (its size and the time it was last written)."""
    path = Path(path)
    try:
        stat = path.stat()
        stamp = [stat.st_size, stat.st_mtime_ns]
    except OSError:
        return facts(path)
    key = str(path)
    try:
        kept = json.loads(_store().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = {}
    entry = kept.get(key) if isinstance(kept, dict) else None
    if isinstance(entry, dict) and entry.get("stamp") == stamp:
        return entry["facts"]
    found = facts(path)
    if not isinstance(kept, dict):
        kept = {}
    kept.pop(key, None)
    kept[key] = {"stamp": stamp, "facts": found}
    for old in list(kept)[:-REMEMBERED]:
        del kept[old]
    try:
        appdata.write_private(_store(), json.dumps(kept))
    except OSError:
        pass
    return found
