"""Comments as a list: a summary to read or print, a spreadsheet, and
XFDF, the XML file Acrobat (and most PDF editors) exports and imports
comments with, so comments can travel without the whole document.

    found = collect(path)                       # [{page, kind, author, date, text, marked, replies}]
    summary_pdf(found, out, title="report.pdf")
    to_csv(found, out)
    export_xfdf(path, out)
    import_xfdf(path, xfdf, out)                # -> how many were added
"""

import csv
import re
import secrets
import xml.etree.ElementTree as ET
from pathlib import Path

from pypdf.generic import (ArrayObject, BooleanObject, DictionaryObject, FloatObject, NameObject,
                           NumberObject, TextStringObject)

from pdfmanager import core, markup

XFDF_NS = "http://ns.adobe.com/xfdf/"

# Annotation types that are comments (not links, form fields or popups).
KINDS = {"/Text": "Note", "/FreeText": "Text box", "/Highlight": "Highlight",
         "/Underline": "Underline", "/StrikeOut": "Strike-out", "/Squiggly": "Squiggly underline",
         "/Square": "Rectangle", "/Circle": "Ellipse", "/Line": "Line", "/Polygon": "Polygon",
         "/PolyLine": "Polyline", "/Ink": "Drawing", "/Stamp": "Stamp", "/Caret": "Insert text",
         "/FileAttachment": "Attached file", "/Sound": "Sound"}
MARKED = ("/Highlight", "/Underline", "/StrikeOut", "/Squiggly")
# XFDF element <-> PDF subtype
ELEMENTS = {"text": "/Text", "freetext": "/FreeText", "highlight": "/Highlight",
            "underline": "/Underline", "strikeout": "/StrikeOut", "squiggly": "/Squiggly",
            "square": "/Square", "circle": "/Circle", "line": "/Line", "polygon": "/Polygon",
            "polyline": "/PolyLine", "ink": "/Ink", "stamp": "/Stamp", "caret": "/Caret"}
SUBTYPES = {v: k for k, v in ELEMENTS.items()}
FLAG_NAMES = ((1, "invisible"), (2, "hidden"), (4, "print"), (8, "nozoom"), (16, "norotate"),
              (32, "noview"), (64, "readonly"), (128, "locked"), (256, "togglenoview"))


def _str(value):
    return str(value) if value is not None else ""


def _nums(value):
    try:
        return [float(v) for v in value]
    except (TypeError, ValueError):
        return []


def _date_text(raw):
    """D:20261109140200+01'00' -> 2026-11-09 14:02."""
    m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?", _str(raw))
    if not m:
        return ""
    y, mo, d, h, mi = m.groups()
    return f"{y}-{mo or '01'}-{d or '01'}" + (f" {h}:{mi or '00'}" if h else "")


# --------------------------------------------------------------------------
# Reading the comments
# --------------------------------------------------------------------------

def collect(path, password=None):
    """Every comment in the PDF, in page order (top to bottom), each with
    its replies: [{"page", "kind", "author", "date", "text", "marked",
    "subject", "replies": [...]}]. ``marked`` is the text under a
    highlight, underline or strike-out."""
    import pypdfium2 as pdfium

    reader = core.open_pdf(path, password)
    found, by_ref, replies = [], {}, []
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path), password=password)
        try:
            for number, page in enumerate(reader.pages, 1):
                textpage = None
                for ref in page.get("/Annots") or []:
                    annot = ref.get_object()
                    if not isinstance(annot, DictionaryObject) or annot.get("/Subtype") not in KINDS:
                        continue
                    item = {"page": number, "kind": KINDS[annot["/Subtype"]],
                            "author": _str(annot.get("/T")), "date": _date_text(annot.get("/M") or annot.get("/CreationDate")),
                            "text": _str(annot.get("/Contents")).strip(), "subject": _str(annot.get("/Subj")),
                            "marked": "", "replies": [],
                            "top": max(_nums(annot.get("/Rect"))[1::2] or [0])}
                    effect = annot.get("/BE")
                    if annot["/Subtype"] == "/Polygon" and isinstance(effect, DictionaryObject) \
                            and effect.get("/S") == "/C":
                        item["kind"] = "Cloud"
                    if annot["/Subtype"] == "/Line" and any("Arrow" in _str(e) for e in annot.get("/LE") or []):
                        item["kind"] = "Arrow"
                    if annot["/Subtype"] in MARKED:
                        if textpage is None:
                            textpage = doc[number - 1].get_textpage()
                        item["marked"] = _marked_text(textpage, annot)
                    if annot["/Subtype"] == "/Stamp" and not item["text"]:
                        item["text"] = item["subject"] or _str(annot.get("/Name")).lstrip("/")
                    key = getattr(ref, "idnum", None)
                    if key is not None:
                        by_ref[key] = item
                    irt = annot.get("/IRT")
                    if irt is not None and getattr(irt, "idnum", None) is not None:
                        replies.append((irt.idnum, item))
                    else:
                        found.append(item)
                if textpage is not None:
                    textpage.close()
        finally:
            doc.close()
    for parent, item in replies:
        (by_ref[parent]["replies"] if parent in by_ref else found).append(item)
    found.sort(key=lambda c: (c["page"], -c["top"]))
    for item in found:
        item.pop("top", None)
        for reply in item["replies"]:
            reply.pop("top", None)
    return found


def _marked_text(textpage, annot):
    """The words under a text markup's quadrilaterals."""
    quads = _nums(annot.get("/QuadPoints"))
    parts = []
    for i in range(0, len(quads) - 7, 8):
        xs, ys = quads[i:i + 8:2], quads[i + 1:i + 8:2]
        try:
            text = textpage.get_text_bounded(min(xs), min(ys), max(xs), max(ys))
        except Exception:  # an odd box
            continue
        if text.strip():
            parts.append(" ".join(text.split()))
    return " ".join(parts)


def count(found):
    return sum(1 + len(c["replies"]) for c in found)


# --------------------------------------------------------------------------
# Summary and spreadsheet
# --------------------------------------------------------------------------

def summary_pdf(found, output, title="Comments"):
    """A PDF listing the comments page by page (like Acrobat's "Summarize
    comments")."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from xml.sax.saxutils import escape

    from pdfmanager import fonts

    every = " ".join(" ".join([c["text"], c["marked"], c["author"]] +
                              [r["text"] + r["author"] for r in c["replies"]]) for c in found)
    regular, bold = fonts.font_for(every + title, "sans"), fonts.font_for(every + title, "sans", bold=True)
    styles = {
        "title": ParagraphStyle("t", fontName=bold, fontSize=16, leading=20, spaceAfter=4),
        "sub": ParagraphStyle("s", fontName=regular, fontSize=9, leading=12, textColor=HexColor("#666666"),
                              spaceAfter=10),
        "page": ParagraphStyle("p", fontName=bold, fontSize=11, leading=14, spaceBefore=10, spaceAfter=4,
                               textColor=HexColor("#1f4fd8")),
        "head": ParagraphStyle("h", fontName=bold, fontSize=9.5, leading=12),
        "body": ParagraphStyle("b", fontName=regular, fontSize=9.5, leading=12.5, leftIndent=8),
        "quote": ParagraphStyle("q", fontName=regular, fontSize=9, leading=12, leftIndent=8,
                                textColor=HexColor("#444444")),
        "reply": ParagraphStyle("r", fontName=regular, fontSize=9, leading=12, leftIndent=22),
    }
    total = count(found)
    story = [Paragraph(escape(f"Comments in {title}"), styles["title"]),
             Paragraph(escape(f"{total} comment{'s' if total != 1 else ''}" if total else "No comments"),
                       styles["sub"])]
    page = None
    for c in found:
        if c["page"] != page:
            page = c["page"]
            story.append(Paragraph(f"Page {page}", styles["page"]))
        who = " · ".join(bit for bit in (c["author"], c["date"]) if bit)
        story.append(Paragraph(escape(c["kind"]) + (f" <font color='#666666'>— {escape(who)}</font>" if who else ""),
                               styles["head"]))
        if c["marked"]:
            story.append(Paragraph("“" + escape(c["marked"]) + "”", styles["quote"]))
        if c["text"]:
            story.append(Paragraph(escape(c["text"]).replace("\n", "<br/>"), styles["body"]))
        for r in c["replies"]:
            who = " · ".join(bit for bit in (r["author"], r["date"]) if bit)
            story.append(Paragraph(f"<b>Reply</b>{(' — ' + escape(who)) if who else ''}: "
                                   + escape(r["text"]).replace("\n", "<br/>"), styles["reply"]))
        story.append(Spacer(1, 5))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(output), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title=f"Comments in {title}")
    from reportlab import rl_config

    rl_config.useA85 = 0
    doc.build(story)
    return output


def to_csv(found, output):
    """One row per comment and reply (Excel opens it as it is)."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Page", "Type", "Author", "Date", "Marked text", "Comment", "Reply to"])
        safe = lambda v: ("'" + v) if v[:1] in ("=", "+", "-", "@") else v  # not a formula
        for c in found:
            w.writerow([c["page"], c["kind"], safe(c["author"]), c["date"], safe(c["marked"]),
                        safe(c["text"]), ""])
            for r in c["replies"]:
                w.writerow([r["page"], "Reply", safe(r["author"]), r["date"], "", safe(r["text"]),
                            c["text"][:60] or c["kind"]])
    return output


# --------------------------------------------------------------------------
# XFDF
# --------------------------------------------------------------------------

def _fmt_nums(values):
    return ",".join(markup._fmt(v) for v in values)


def _color_hex(color):
    vals = _nums(color)
    if len(vals) == 3:
        return "#" + "".join(f"{round(max(0, min(1, v)) * 255):02X}" for v in vals)
    if len(vals) == 1:
        g = round(max(0, min(1, vals[0])) * 255)
        return f"#{g:02X}{g:02X}{g:02X}"
    return None


def export_xfdf(path, output, password=None):
    """The PDF's comments as an XFDF file. Returns how many."""
    reader = core.open_pdf(path, password)
    root = ET.Element("xfdf", {"xmlns": XFDF_NS, "xml:space": "preserve"})
    annots = ET.SubElement(root, "annots")
    names, n = {}, 0
    pending = []
    for index, page in enumerate(reader.pages):
        for ref in page.get("/Annots") or []:
            annot = ref.get_object()
            if not isinstance(annot, DictionaryObject) or annot.get("/Subtype") not in SUBTYPES:
                continue
            name = _str(annot.get("/NM")) or ("zpdf-" + secrets.token_hex(8))
            if getattr(ref, "idnum", None) is not None:
                names[ref.idnum] = name
            pending.append((index, annot, name))
    for index, annot, name in pending:
        tag = SUBTYPES[annot["/Subtype"]]
        attrs = {"page": str(index), "name": name, "rect": _fmt_nums(_nums(annot.get("/Rect")))}
        flags = int(annot.get("/F", 0) or 0)
        if flags:
            attrs["flags"] = ",".join(word for bit, word in FLAG_NAMES if flags & bit)
        for key, attr in (("/T", "title"), ("/Subj", "subject")):
            if annot.get(key) is not None:
                attrs[attr] = _str(annot[key])
        for key, attr in (("/M", "date"), ("/CreationDate", "creationdate")):
            if annot.get(key) is not None:
                attrs[attr] = _str(annot[key])
        color = _color_hex(annot.get("/C"))
        if color:
            attrs["color"] = color
        interior = _color_hex(annot.get("/IC"))
        if interior:
            attrs["interior-color"] = interior
        if annot.get("/CA") is not None:
            attrs["opacity"] = markup._fmt(float(annot["/CA"]))
        border = annot.get("/BS")
        if isinstance(border, DictionaryObject) and border.get("/W") is not None:
            attrs["width"] = markup._fmt(float(border["/W"]))
        irt = annot.get("/IRT")
        if irt is not None and names.get(getattr(irt, "idnum", None)):
            attrs["inreplyto"] = names[irt.idnum]
            if annot.get("/RT") == "/Group":
                attrs["replyType"] = "group"
        if tag in ("highlight", "underline", "strikeout", "squiggly"):
            attrs["coords"] = _fmt_nums(_nums(annot.get("/QuadPoints")))
        if tag == "text":
            attrs["icon"] = _str(annot.get("/Name")).lstrip("/") or "Comment"
        if tag == "stamp":
            attrs["icon"] = _str(annot.get("/Name")).lstrip("/") or "Draft"
        if tag == "line":
            x0, y0, x1, y1 = (_nums(annot.get("/L")) + [0, 0, 0, 0])[:4]
            attrs["start"], attrs["end"] = _fmt_nums([x0, y0]), _fmt_nums([x1, y1])
            ends = [_str(e).lstrip("/") for e in (annot.get("/LE") or [])]
            if len(ends) == 2:
                attrs["head"], attrs["tail"] = ends
        el = ET.SubElement(annots, tag, attrs)
        if tag in ("polygon", "polyline"):
            ET.SubElement(el, "vertices").text = _fmt_nums(_nums(annot.get("/Vertices")))
            effect = annot.get("/BE")
            if isinstance(effect, DictionaryObject) and effect.get("/S") == "/C":
                el.set("style", "cloudy")
                el.set("intensity", markup._fmt(float(effect.get("/I", 1))))
        if tag == "ink":
            inklist = ET.SubElement(el, "inklist")
            for stroke in annot.get("/InkList") or []:
                pts = _nums(stroke)
                ET.SubElement(inklist, "gesture").text = ";".join(
                    f"{markup._fmt(pts[i])},{markup._fmt(pts[i + 1])}" for i in range(0, len(pts) - 1, 2))
        if annot.get("/Contents") is not None:
            ET.SubElement(el, "contents").text = _str(annot["/Contents"])
        if tag == "freetext" and annot.get("/DA") is not None:
            ET.SubElement(el, "defaultappearance").text = _str(annot["/DA"])
        n += 1
    source = Path(str(path)).name
    ET.SubElement(root, "f", {"href": source})
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output, encoding="UTF-8", xml_declaration=True)
    return n


def _attr_nums(value):
    return [float(v) for v in re.split(r"[,\s;]+", value.strip()) if v] if value else []


def _hex_color(value):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", (value or "").strip())
    if not m:
        return None
    h = m.group(1)
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def import_xfdf(path, xfdf, output, password=None):
    """Add the comments of an XFDF file to the PDF (each with its own look,
    drawn here); comments the PDF already has (same name) are skipped.
    Returns (added, skipped kinds)."""
    try:
        tree = ET.parse(xfdf)
    except (ET.ParseError, OSError) as exc:
        raise core.PdfManagerError(f"the comments file can't be read ({exc})") from None
    root = tree.getroot()
    if root.tag.rsplit("}", 1)[-1] != "xfdf":
        raise core.PdfManagerError("that isn't an XFDF comments file")
    reader = core.open_pdf(path, password)
    writer = core.clone_document(reader)
    existing = set()
    for page in writer.pages:
        for ref in page.get("/Annots") or []:
            nm = ref.get_object().get("/NM") if isinstance(ref.get_object(), DictionaryObject) else None
            if nm is not None:
                existing.add(_str(nm))
    by_name, added, skipped, pending_replies = {}, 0, [], []
    annots_el = next((el for el in root if el.tag.rsplit("}", 1)[-1] == "annots"), None)
    for el in list(annots_el) if annots_el is not None else []:
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ELEMENTS:
            skipped.append(tag)
            continue
        name = el.get("name") or ("zpdf-" + secrets.token_hex(8))
        if name in existing:
            continue
        try:
            index = int(el.get("page", "0"))
        except ValueError:
            index = 0
        if not 0 <= index < len(writer.pages):
            skipped.append(tag)
            continue
        page = writer.pages[index]
        annot = _annotation(writer, page, el, tag, name)
        if annot is None:
            skipped.append(tag)
            continue
        ref = writer._add_object(annot)
        if "/Annots" not in page:
            page[NameObject("/Annots")] = ArrayObject()
        page["/Annots"].get_object().append(ref)
        by_name[name] = ref
        if el.get("inreplyto"):
            pending_replies.append((annot, el.get("inreplyto"), el.get("replyType")))
        added += 1
    for annot, parent, kind in pending_replies:
        target = by_name.get(parent)
        if target is None:  # a reply to a comment already in the PDF
            for page in writer.pages:
                for ref in page.get("/Annots") or []:
                    obj = ref.get_object()
                    if isinstance(obj, DictionaryObject) and _str(obj.get("/NM")) == parent:
                        target = ref
        if target is not None:
            annot[NameObject("/IRT")] = target
            if kind == "group":
                annot[NameObject("/RT")] = NameObject("/Group")
    core._write(writer, output)
    return added, sorted(set(skipped))


def _contents(el):
    for child in el:
        if child.tag.rsplit("}", 1)[-1] == "contents":
            return child.text or ""
    for child in el:  # rich text: its words
        if child.tag.rsplit("}", 1)[-1] == "contents-richtext":
            return " ".join("".join(child.itertext()).split())
    return ""


def _child_text(el, name):
    for child in el.iter():
        if child.tag.rsplit("}", 1)[-1] == name:
            return child.text or ""
    return ""


def _annotation(writer, page, el, tag, name):
    """A PDF annotation (with an appearance) for an XFDF element."""
    rect = _attr_nums(el.get("rect"))
    if len(rect) != 4:
        return None
    color = _hex_color(el.get("color")) or {"highlight": [1, 0.9, 0], "text": [0.96, 0.77, 0]}.get(tag, [0.84, 0.17, 0.12])
    width = float(el.get("width") or 1)
    subtype = ELEMENTS[tag]
    annot = markup._base(writer, page.indirect_reference, subtype, rect, color,
                         el.get("title") or "", _contents(el))
    annot[NameObject("/NM")] = TextStringObject(name)
    for attr, key in (("date", "/M"), ("creationdate", "/CreationDate")):
        if el.get(attr):
            annot[NameObject(key)] = TextStringObject(el.get(attr))
    if el.get("subject"):
        annot[NameObject("/Subj")] = TextStringObject(el.get("subject"))
    if el.get("flags") is not None:
        words = set(el.get("flags").replace(" ", "").split(","))
        annot[NameObject("/F")] = NumberObject(sum(bit for bit, word in FLAG_NAMES if word in words))
    opacity = el.get("opacity")
    if opacity:
        annot[NameObject("/CA")] = FloatObject(float(opacity))
    x0, y0, x1, y1 = rect
    f, rgb = markup._fmt, markup._rgb
    content, resources = "", None
    if tag in ("highlight", "underline", "strikeout", "squiggly"):
        quads = _attr_nums(el.get("coords"))
        if len(quads) < 8:
            quads = [x0, y1, x1, y1, x0, y0, x1, y0]
        annot[NameObject("/QuadPoints")] = ArrayObject(FloatObject(v) for v in quads)
        parts = []
        for i in range(0, len(quads) - 7, 8):
            xs, ys = quads[i:i + 8:2], quads[i + 1:i + 8:2]
            a, b, lo, hi = min(xs), max(xs), min(ys), max(ys)
            h = hi - lo
            if tag == "highlight":
                parts.append(f"{f(a)} {f(lo)} {f(b - a)} {f(h)} re f")
            elif tag == "squiggly":
                step, wave = max(2.0, h / 6), max(0.8, h / 14)
                pts = [f"{f(a)} {f(lo + wave)} m"]
                xx, up = a, False
                while xx < b:
                    xx = min(b, xx + step)
                    pts.append(f"{f(xx)} {f(lo + (wave * 2 if up else 0))} l")
                    up = not up
                parts.append(" ".join(pts) + " S")
            else:
                at = lo + h * (0.42 if tag == "strikeout" else 0.1)
                parts.append(f"{f(max(0.5, h / 14))} w {f(a)} {f(at)} m {f(b)} {f(at)} l S")
        if tag == "highlight":
            resources = DictionaryObject({NameObject("/ExtGState"): DictionaryObject({
                NameObject("/GSm"): DictionaryObject({NameObject("/BM"): NameObject("/Multiply"),
                                                      NameObject("/ca"): FloatObject(float(opacity or 1))})})})
            content = f"/GSm gs {rgb(color, False)} " + " ".join(parts)
        else:
            content = f"{rgb(color)} " + " ".join(parts)
    elif tag == "text":
        annot[NameObject("/Name")] = NameObject("/" + re.sub(r"\W", "", el.get("icon") or "Comment"))
        annot[NameObject("/Open")] = BooleanObject(False)
        content = markup._note_icon(x0, y1, color)
    elif tag in ("square", "circle"):
        interior = _hex_color(el.get("interior-color"))
        border = DictionaryObject({NameObject("/W"): FloatObject(width), NameObject("/S"): NameObject("/S")})
        annot[NameObject("/BS")] = border
        half = width / 2
        path = (f"{f(x0 + half)} {f(y0 + half)} {f(x1 - x0 - width)} {f(y1 - y0 - width)} re" if tag == "square"
                else markup._ellipse_path(x0 + half, y0 + half, x1 - half, y1 - half))
        if interior:
            annot[NameObject("/IC")] = ArrayObject(FloatObject(c) for c in interior)
        content = f"{rgb(color)} {f(width)} w " + (f"{rgb(interior, False)} {path} B" if interior else f"{path} S")
    elif tag == "line":
        start, end = _attr_nums(el.get("start")), _attr_nums(el.get("end"))
        if len(start) != 2 or len(end) != 2:
            return None
        annot[NameObject("/L")] = ArrayObject(FloatObject(v) for v in start + end)
        annot[NameObject("/BS")] = DictionaryObject({NameObject("/W"): FloatObject(width)})
        head, tail = el.get("head") or "None", el.get("tail") or "None"
        annot[NameObject("/LE")] = ArrayObject([NameObject("/" + head), NameObject("/" + tail)])
        content = f"{rgb(color)} {f(width)} w 1 J {f(start[0])} {f(start[1])} m {f(end[0])} {f(end[1])} l S"
        for (px, py), (qx, qy), kind in (((end[0], end[1]), (start[0], start[1]), head),
                                         ((start[0], start[1]), (end[0], end[1]), tail)):
            if "Arrow" in kind:  # a head at (qx, qy) pointing away from (px, py)
                import math

                length = math.hypot(qx - px, qy - py) or 1
                ux, uy = (qx - px) / length, (qy - py) / length
                size = max(6.0, width * 4)
                bx, by = qx - ux * size, qy - uy * size
                wing = size * 0.45
                content += (f" {rgb(color, False)} {f(qx)} {f(qy)} m {f(bx - uy * wing)} {f(by + ux * wing)} l "
                            f"{f(bx + uy * wing)} {f(by - ux * wing)} l h " + ("f" if "Closed" in kind else "S"))
    elif tag in ("polygon", "polyline"):
        pts = _attr_nums(_child_text(el, "vertices"))
        if len(pts) < 4:
            return None
        annot[NameObject("/Vertices")] = ArrayObject(FloatObject(v) for v in pts)
        annot[NameObject("/BS")] = DictionaryObject({NameObject("/W"): FloatObject(width)})
        path = f"{f(pts[0])} {f(pts[1])} m " + " ".join(f"{f(pts[i])} {f(pts[i + 1])} l" for i in range(2, len(pts) - 1, 2))
        content = f"{rgb(color)} {f(width)} w 1 j {path}" + (" h S" if tag == "polygon" else " S")
        if tag == "polygon" and el.get("style") == "cloudy":  # a revision cloud round its box
            xs, ys = pts[0::2], pts[1::2]
            annot[NameObject("/BE")] = DictionaryObject({NameObject("/S"): NameObject("/C"),
                                                         NameObject("/I"): NumberObject(1)})
            content = (f"{rgb(color)} {f(width)} w 1 j "
                       + markup._cloud_path(min(xs), min(ys), max(xs), max(ys), max(10.0, width * 6)) + " S")
    elif tag == "ink":
        strokes = []
        for gesture in el.iter():
            if gesture.tag.rsplit("}", 1)[-1] == "gesture" and gesture.text:
                pts = _attr_nums(gesture.text.replace(";", ","))
                if len(pts) >= 4:
                    strokes.append(pts)
        if not strokes:
            return None
        annot[NameObject("/InkList")] = ArrayObject(ArrayObject(FloatObject(v) for v in s) for s in strokes)
        annot[NameObject("/BS")] = DictionaryObject({NameObject("/W"): FloatObject(width)})
        content = f"{rgb(color)} {f(width)} w 1 J 1 j " + " ".join(
            f"{f(s[0])} {f(s[1])} m " + " ".join(f"{f(s[i])} {f(s[i + 1])} l" for i in range(2, len(s) - 1, 2)) + " S"
            for s in strokes)
    elif tag == "freetext":
        da = _child_text(el, "defaultappearance") or "0 0 0 rg /Helv 10 Tf"
        annot[NameObject("/DA")] = TextStringObject(da)
        m = re.search(r"/\w+\s+([\d.]+)\s+Tf", da)
        size = float(m.group(1)) if m and float(m.group(1)) > 0 else 10
        text = _contents(el)
        lines, y = [], y1 - size - 2
        for raw in text.splitlines() or [""]:
            words, line = raw.split(), ""
            for word in words:
                trial = (line + " " + word).strip()
                if markup._text_width(trial, size) > (x1 - x0 - 4) and line:
                    lines.append(line)
                    line = word
                else:
                    line = trial
            lines.append(line)
        body = []
        for line in lines:
            if y < y0:
                break
            body.append(f"1 0 0 1 {f(x0 + 2)} {f(y)} Tm {markup._pdf_text(line)} Tj")
            y -= size * 1.2
        resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})})})
        content = f"BT 0 g /Helv {f(size)} Tf " + " ".join(body) + " ET"
    elif tag == "stamp":
        label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", el.get("icon") or el.get("subject") or "Draft")
        stamp = markup.stamp_annotation(writer, page, page.indirect_reference,
                                        {"rect": rect, "label": label, "text": _contents(el)},
                                        el.get("title") or "")
        for key in ("/Name", "/Subj", "/AP"):
            annot[NameObject(key)] = stamp[key]
        return annot
    elif tag == "caret":
        mx = (x0 + x1) / 2
        content = f"{rgb(color, False)} {f(x0)} {f(y0)} m {f(mx)} {f(y1)} l {f(x1)} {f(y0)} l h f"
    annot[NameObject("/AP")] = markup._appearance(writer, rect, content, resources)
    return annot
