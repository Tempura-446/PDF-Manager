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
LOOK = 4              # raised when facts are worked out differently, so kept ones are looked at again
TEXT_SHOWING = {"Tj", "TJ", "'", '"'}
# A printed place to sign: the word near a rule or a run of underscores.
SIGN_WORDS = re.compile(r"\b(signature|signed|sign here|unterschrift|signé|firma)\b", re.I)
UNDERSCORES = re.compile(r"_{4,}|\.{8,}")
RULE = 72  # points: a line at least this long, and flat, can be written on


def _strings(operands):
    """The characters a text operator shows, as far as a simple font tells
    (an embedded CID font's codes don't read as letters: no matter here)."""
    out = []
    for operand in operands:
        try:
            items = list(operand) if not hasattr(operand, "__bytes__") else [operand]
        except TypeError:
            items = [operand]
        for item in items:
            if hasattr(item, "__bytes__"):
                try:
                    out.append(bytes(item).decode("latin-1"))
                except Exception:
                    pass
    return "".join(out)


def _page_look(page, pikepdf):
    """(shows text, shows text a reader can see, holds a picture big enough
    to be its scan, has a printed line to sign on). The text OCR puts on a
    scan is invisible (render mode 3); a designed page with large photos has
    text people read. The signature line: "Signature", "Signed" or "Sign
    here" in the page's text, and underscores or a drawn flat rule to write
    on; read from the instructions only, nothing drawn."""
    text = visible = picture = False
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
    words, rules, start = [], [], None
    ctm, matrices = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), []

    def at(x, y):  # a point in the page's own coordinates (through cm)
        a, b, c, d, e, f = ctm
        return a * x + c * y + e, b * x + d * y + f

    def flat(p, q, thick=0.0):  # a level stroke long enough to write on
        if abs(q[1] - p[1]) < 1 and abs(q[0] - p[0]) >= RULE and len(rules) < 200:
            rules.append((min(p[0], q[0]), (p[1] + q[1]) / 2, max(p[0], q[0]), thick))

    try:
        mode, saved = 0, []
        for operands, operator in pikepdf.parse_content_stream(page):
            operator = str(operator)
            if operator == "q":
                saved.append(mode)
                matrices.append(ctm)
            elif operator == "Q":
                mode = saved.pop() if saved else 0
                ctm = matrices.pop() if matrices else ctm
            elif operator == "cm" and len(operands) == 6:
                a, b, c, d, e, f = (float(v) for v in operands)
                A, B, C, D, E, F = ctm
                ctm = (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)
            elif operator == "Tr" and operands:
                mode = int(operands[0])
            elif operator in TEXT_SHOWING:
                text = True
                if mode != 3:
                    visible = True
                    if len(words) < 400:
                        words.append(_strings(operands))
            elif operator == "m" and len(operands) == 2:
                start = at(float(operands[0]), float(operands[1]))
            elif operator == "l" and len(operands) == 2 and start:
                end = at(float(operands[0]), float(operands[1]))
                flat(start, end)
                start = end
            elif operator == "re" and len(operands) == 4:
                x, y, w, h = (float(v) for v in operands)
                p, q = at(x, y), at(x + w, y + h)
                if abs(q[1] - p[1]) <= 2:
                    mid = (p[1] + q[1]) / 2
                    flat((p[0], mid), (q[0], mid), abs(q[1] - p[1]))
    except Exception:
        pass
    said = " ".join(words)
    sign = bool(SIGN_WORDS.search(said)) and (bool(rules) or bool(UNDERSCORES.search(said)))
    return text, visible, picture, sign, rules


def _sign_place(path, password, number, rules):
    """Where the printed line to sign on is on page ``number``: {"page",
    "rect" (the blank or the rule, in PDF points: x0, y0, x1, y1), "text"
    (the size of the label's letters, in points)}, or None when it can't be
    told. The label and the underscores are found with their places in the
    page's text (pdfium reads it; nothing is drawn); a drawn rule comes from
    the instructions (``rules``: (x0, y, x1, thickness)). Of the blanks near
    a label, one on its line and to its right wins; then one just above it
    (a label printed under the line)."""
    from pdfmanager import core

    with core.PDFIUM_LOCK:  # (pdfium isn't thread-safe: the server reads several files at once)
        return _sign_place_locked(path, password, number, rules)


def _sign_place_locked(path, password, number, rules):
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    try:
        doc = pdfium.PdfDocument(str(path), password=password or None)
    except Exception:
        return None
    try:
        textpage = doc[number - 1].get_textpage()
        said = "".join(chr(code) if 0 < code < 0x110000 else " "
                       for code in (raw.FPDFText_GetUnicode(textpage, i) for i in range(min(textpage.count_chars(), 20000))))

        def box(a, b):
            parts = [textpage.get_charbox(i) for i in range(a, b) if not said[i].isspace()]
            if not parts:
                return None
            return (min(p[0] for p in parts), min(p[1] for p in parts), max(p[2] for p in parts), max(p[3] for p in parts))

        blanks = [b for b in (box(m.start(), m.end()) for m in UNDERSCORES.finditer(said)) if b and b[2] - b[0] >= 20]
        blanks += [(x0, y - thick / 2, x1, y + thick / 2) for x0, y, x1, thick in rules]
        best = None
        for m in SIGN_WORDS.finditer(said):
            label = box(m.start(), m.end())
            if not label:
                continue
            size = raw.FPDFText_GetFontSize(textpage, m.start())
            if not 2 <= size <= 200:
                size = max(label[3] - label[1], 6)
            for blank in blanks:
                top = blank[3]
                if blank[2] <= label[0] or blank[0] > label[2] + 8 * size:
                    continue
                if label[1] - size <= top <= label[1] + 0.6 * size and blank[0] >= label[0] - 2:  # on its line
                    score = max(0.0, blank[0] - label[2])
                elif label[3] <= top <= label[3] + 3 * size:  # just above it
                    score = 1000 + top - label[3]
                else:
                    continue
                if best is None or score < best[0]:
                    best = (score, blank, size)
        if not best:
            return None
        return {"page": number, "rect": [round(v, 1) for v in best[1]], "text": round(best[2], 1)}
    except Exception:
        return None
    finally:
        doc.close()


def _fields(pdf, pikepdf):
    """(fields to fill in, signatures made, places to sign still empty)."""
    form = pdf.Root.get("/AcroForm")
    fields = form.get("/Fields") if isinstance(form, pikepdf.Dictionary) else None
    filled = signed = unsigned = 0
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
            unsigned += "/V" not in field
        elif "/FT" in field or "/T" in field:
            filled += 1
    return filled, signed, unsigned


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
    "signatures", "to_sign" (signature fields still empty), "tagged",
    "pdfa", "sign_line" (the first page with a printed line to sign on, 0
    for none), "sign_at" (where on it the line is, see _sign_place; None
    when it can't be told)} of the PDF at ``path``. A file that
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
            texts = pictures = sampled = sign_line = 0
            lines = []
            for page in list(pdf.pages)[:SAMPLE]:
                sampled += 1
                text, visible, picture, sign, rules = _page_look(page, pikepdf)
                if sign and not sign_line:
                    sign_line, lines = sampled, rules
                texts += text
                pictures += picture and not visible  # a page of its own picture, or one with OCR text
            filled, signed, unsigned = _fields(pdf, pikepdf)
            marked = pdf.Root.get("/MarkInfo")
            found = {
                "size": size, "pages": count, "protected": bool(pdf.is_encrypted),
                "scanned": bool(sampled) and pictures >= 0.6 * sampled,
                "has_text": texts > 0, "form_fields": filled, "signatures": signed, "to_sign": unsigned,
                "tagged": isinstance(marked, pikepdf.Dictionary) and marked.get("/Marked") is True
                and "/StructTreeRoot" in pdf.Root,
                "pdfa": _pdfa(pdf, pikepdf),
                "sign_line": sign_line,
            }
    except Exception:  # a file damaged past its first page
        return {"size": size, "pages": None, "error": "can't be read"}
    found["sign_at"] = _sign_place(path, password, sign_line, lines) if sign_line else None
    return found


BIG = 1_000_000  # bytes from which making a file smaller is worth offering


SMALL_PAGE = 200_000  # bytes a page: below this a file is already compact


def small_for_its_pages(found):
    """Already compact for its pages: little to gain from Compress."""
    pages = found.get("pages") or 0
    return bool(pages) and found.get("size", 0) < SMALL_PAGE * pages


def suggestions(found, made_by=None):
    """What to offer for a file with these facts, most useful first:
    [{"op": tool id, "title", "why"}], three at most. "sign" is the drawn
    or typed signature (the reader's Sign), not a tool; its "page" says
    where, and its "line" (facts' "sign_at", when known) where on it. ``made_by``: the tool that made this file, if one did (a file
    just compressed isn't offered Compress again)."""
    if found.get("error"):
        return [{"op": "repair", "title": "Repair it",
                 "why": "This file can't be read as it is; a repair often brings it back"}]
    if found.get("pages") is None:
        return []  # locked: nothing to offer before its password
    out = []
    if found.get("scanned") and not found.get("has_text"):
        out.append({"op": "ocr", "title": "Make it searchable",
                    "why": "Read the text so you can search and copy it"})
    if found.get("size", 0) >= BIG and made_by != "compress" and not small_for_its_pages(found):
        why = ("Scanned pages shrink a lot, and their text stays sharp" if found.get("scanned")
               else "Reduce its pictures for sending or storing")
        out.append({"op": "compress", "title": "Make it smaller", "why": why})
    if found.get("signatures"):
        out.append({"op": "verify-sig", "title": "Check its signatures",
                    "why": "See who signed it and whether it was changed afterwards"})
    if found.get("to_sign"):
        out.append({"op": "sign", "title": "Sign it",
                    "why": "It has a place for a signature that is still empty"})
    elif found.get("sign_line") and not found.get("signatures"):
        out.append({"op": "sign", "title": "Sign it", "page": found["sign_line"], "line": found.get("sign_at"),
                    "why": f"Page {found['sign_line']} has a line for your signature"})
    return out[:3]


def _store():
    return appdata.data_dir() / "filefacts.json"


def remembered(path):
    """facts(path), kept from one look to the next for as long as the file
    is the same (its size and the time it was last written)."""
    path = Path(path)
    try:
        stat = path.stat()
        stamp = [stat.st_size, stat.st_mtime_ns, LOOK]
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
