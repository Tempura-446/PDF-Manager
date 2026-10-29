"""Fillable forms: add text boxes, checkboxes and signature fields to a PDF.

The fields are standard PDF form fields, so they can be filled in with any
PDF reader, including this app's. Each field gets its own appearance (a
thin border, and a tick in a checked box), so it looks the same everywhere,
and readers draw the text in as it's typed.
"""

from pypdf import PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject,
                           NameObject, NumberObject, TextStringObject)

from pdfmanager import core

TYPES = {"text": "Text", "multiline": "Text", "checkbox": "Checkbox", "signature": "Signature"}
MULTILINE, REQUIRED = 1 << 12, 1 << 1     # text field flags
RADIO, PUSHBUTTON = 1 << 15, 1 << 16      # button field flags
BORDER = (0.45, 0.45, 0.45)
TICK = "4"                                # ✔ in ZapfDingbats
MIN_SIZE = 4                              # points


def _num(v):
    return FloatObject(round(float(v), 3))


def _field_of(widget):
    """The field a widget belongs to (a widget may be its own field)."""
    while "/T" not in widget and "/Parent" in widget:
        widget = widget["/Parent"].get_object()
    return widget


def _inherited(field, key, default=None):
    while field is not None:
        if key in field:
            return field[key]
        field = field["/Parent"].get_object() if "/Parent" in field else None
    return default


def _qualified_name(field):
    parts = []
    while field is not None:
        if "/T" in field:
            parts.append(str(field["/T"]))
        field = field["/Parent"].get_object() if "/Parent" in field else None
    return ".".join(reversed(parts))


def _kind(field):
    ft, flags = _inherited(field, "/FT"), int(_inherited(field, "/Ff", 0))
    if ft == "/Tx":
        return "multiline" if flags & MULTILINE else "text"
    if ft == "/Btn":
        return "radio" if flags & RADIO else "button" if flags & PUSHBUTTON else "checkbox"
    if ft == "/Sig":
        return "signature"
    if ft == "/Ch":
        return "choice"
    return "other"


def existing_fields(path, password=None):
    """The form fields already in a PDF: {1-based page: [{"rect", "name",
    "type"}]}, with rectangles in PDF points (origin bottom left)."""
    reader = core.open_pdf(path, password)
    found = {}
    for number, page in enumerate(reader.pages, 1):
        for ref in page.get("/Annots") or []:
            annot = ref.get_object()
            if annot.get("/Subtype") != "/Widget" or "/Rect" not in annot:
                continue
            x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
            field = _field_of(annot)
            found.setdefault(number, []).append({
                "rect": [round(min(x0, x1), 1), round(min(y0, y1), 1),
                         round(max(x0, x1), 1), round(max(y0, y1), 1)],
                "name": _qualified_name(field), "type": _kind(field)})
    return found


def parse_field(spec):
    """'TYPE:PAGE:X0,Y0,X1,Y1[:NAME]' (from the command line) -> field dict."""
    try:
        kind, page, coords, *name = spec.split(":", 3)
        x0, y0, x1, y1 = (float(v) for v in coords.split(","))
        field = {"type": kind.strip().lower(), "page": int(page), "rect": [x0, y0, x1, y1]}
    except ValueError:
        raise core.PdfManagerError(
            f"invalid field {spec!r}; use TYPE:PAGE:X0,Y0,X1,Y1[:NAME], e.g. "
            "text:1:72,700,300,720:Name (points from the bottom left)") from None
    if name:
        field["name"] = name[0]
    return field


def _unique(name, taken, label):
    """``name``, or "Name 2", "Name 3"... if it's taken; without a name the
    next free "Text 1", "Text 2"..."""
    name = " ".join(str(name or "").replace(".", " ").split())  # a dot would nest the field
    if name:
        candidate, n = name, 2
        while candidate in taken:
            candidate, n = f"{name} {n}", n + 1
    else:
        n = 1
        while f"{label} {n}" in taken:
            n += 1
        candidate = f"{label} {n}"
    taken.add(candidate)
    return candidate


def _child(parent, key, make):
    """parent[key], resolved; added (from ``make()``) if missing."""
    if key not in parent:
        parent[NameObject(key)] = make()
    return parent[key].get_object()


def _stream(writer, width, height, content, rotation, resources=None):
    stream = DecodedStreamObject()
    stream.set_data(content.encode("latin-1"))
    stream.update({
        NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject([_num(0), _num(0), _num(width), _num(height)]),
    })
    if rotation:
        # Turn the appearance with the page, so it's upright on screen.
        cos, sin = {90: (0, 1), 180: (-1, 0), 270: (0, -1)}[rotation]
        stream[NameObject("/Matrix")] = ArrayObject(
            [NumberObject(cos), NumberObject(sin), NumberObject(-sin), NumberObject(cos),
             NumberObject(0), NumberObject(0)])
    if resources is not None:
        stream[NameObject("/Resources")] = resources
    return writer._add_object(stream)


def _border(width, height, border):
    if not border:
        return ""
    r, g, b = BORDER
    return f"q {r} {g} {b} RG 1 w 0.5 0.5 {width - 1:.2f} {height - 1:.2f} re S Q\n"


def _fonts(writer, acroform):
    """The form's default fonts (Helvetica for text, ZapfDingbats for ticks)."""
    fonts = _child(_child(acroform, "/DR", DictionaryObject), "/Font", DictionaryObject)
    for name, base, extra in (("/Helv", "/Helvetica", {"/Encoding": "/WinAnsiEncoding"}),
                              ("/ZaDb", "/ZapfDingbats", {})):
        if name not in fonts:
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                     NameObject("/Subtype"): NameObject("/Type1"),
                                     NameObject("/BaseFont"): NameObject(base)})
            for k, v in extra.items():
                font[NameObject(k)] = NameObject(v)
            fonts[NameObject(name)] = writer._add_object(font)
    if "/DA" not in acroform:
        acroform[NameObject("/DA")] = TextStringObject("/Helv 0 Tf 0 g")
    return fonts


def add_fields(path, output, fields, border=True, font_size=0, password=None):
    """Add form fields to a PDF. ``fields`` is a list of {"page" (1-based),
    "rect": [x0, y0, x1, y1] in PDF points, "type": text/multiline/
    checkbox/signature, "name" (optional), "required" (optional)}.
    ``font_size`` 0 fits single-line text to its box. Returns the names
    given to the new fields (made unique if a name was taken)."""
    reader = core.open_pdf(path, password)
    root = reader.trailer["/Root"]
    if "/AcroForm" in root and "/XFA" in root["/AcroForm"].get_object():
        raise core.PdfManagerError(
            "this PDF is an XFA form (made with Adobe LiveCycle); fields can't be added to it")
    taken = set()
    for page_fields in existing_fields(path, password).values():
        taken.update(f["name"] for f in page_fields)

    writer = PdfWriter(clone_from=reader)
    acroform = _child(writer._root_object, "/AcroForm", DictionaryObject)
    field_list = _child(acroform, "/Fields", ArrayObject)
    fonts = _fonts(writer, acroform)
    tick_resources = DictionaryObject({NameObject("/Font"): DictionaryObject(
        {NameObject("/ZaDb"): fonts["/ZaDb"]})})

    names, by_page = [], {}
    for field in fields:
        kind = str(field.get("type", "text")).lower()
        if kind not in TYPES:
            raise core.PdfManagerError(f"unknown field type {kind!r}; use one of {', '.join(TYPES)}")
        page = int(field.get("page", 1))
        if not 1 <= page <= len(writer.pages):
            raise core.PdfManagerError(f"page {page} doesn't exist")
        x0, y0, x1, y1 = (float(v) for v in field["rect"])
        rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        if rect[2] - rect[0] < MIN_SIZE or rect[3] - rect[1] < MIN_SIZE:
            raise core.PdfManagerError(f"a field on page {page} is too small")
        by_page.setdefault(page, []).append((kind, rect, field))
    if not by_page:
        raise core.PdfManagerError("no fields to add: draw boxes on the pages")

    for page_number in sorted(by_page):
        page = writer.pages[page_number - 1]
        rotation = page.rotation % 360
        annots = _child(page, "/Annots", ArrayObject)
        if "/Tabs" not in page:
            page[NameObject("/Tabs")] = NameObject("/R")  # Tab moves along rows
        # In reading order, so Tab goes from field to field down the page.
        for kind, rect, field in sorted(by_page[page_number], key=lambda f: (-f[1][3], f[1][0])):
            name = _unique(field.get("name"), taken, TYPES[kind])
            names.append(name)
            width, height = rect[2] - rect[0], rect[3] - rect[1]
            if rotation in (90, 270):
                width, height = height, width
            widget = DictionaryObject({
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Widget"),
                NameObject("/Rect"): ArrayObject(_num(v) for v in rect),
                NameObject("/F"): NumberObject(4),  # printed
                NameObject("/P"): page.indirect_reference,
                NameObject("/T"): TextStringObject(name),
            })
            mk = DictionaryObject()
            if border:
                mk[NameObject("/BC")] = ArrayObject(_num(c) for c in BORDER)
                widget[NameObject("/BS")] = DictionaryObject(
                    {NameObject("/W"): NumberObject(1), NameObject("/S"): NameObject("/S")})
            if rotation:
                mk[NameObject("/R")] = NumberObject(rotation)
            flags = REQUIRED if field.get("required") else 0
            frame = _border(width, height, border)
            if kind in ("text", "multiline"):
                size = font_size or (10 if kind == "multiline" else 0)
                flags |= MULTILINE if kind == "multiline" else 0
                widget.update({
                    NameObject("/FT"): NameObject("/Tx"),
                    NameObject("/DA"): TextStringObject(f"/Helv {size:g} Tf 0 g"),
                    NameObject("/AP"): DictionaryObject({NameObject("/N"): _stream(
                        writer, width, height, frame + "/Tx BMC EMC\n", rotation)}),
                })
            elif kind == "checkbox":
                size = min(width, height) * 0.8
                tx, ty = (width - 0.846 * size) / 2, (height - 0.7 * size) / 2
                tick = f"q BT 0 g /ZaDb {size:.2f} Tf {tx:.2f} {ty:.2f} Td ({TICK}) Tj ET Q\n"
                mk[NameObject("/CA")] = TextStringObject(TICK)
                widget.update({
                    NameObject("/FT"): NameObject("/Btn"),
                    NameObject("/DA"): TextStringObject("/ZaDb 0 Tf 0 g"),
                    NameObject("/V"): NameObject("/Off"),
                    NameObject("/AS"): NameObject("/Off"),
                    NameObject("/AP"): DictionaryObject({NameObject("/N"): DictionaryObject({
                        NameObject("/Yes"): _stream(writer, width, height, frame + tick, rotation,
                                                    tick_resources),
                        NameObject("/Off"): _stream(writer, width, height, frame, rotation),
                    })}),
                })
            else:  # signature
                widget.update({
                    NameObject("/FT"): NameObject("/Sig"),
                    NameObject("/AP"): DictionaryObject({NameObject("/N"): _stream(
                        writer, width, height, frame, rotation)}),
                })
                acroform[NameObject("/SigFlags")] = NumberObject(
                    int(acroform.get("/SigFlags", 0)) | 1)
            if flags:
                widget[NameObject("/Ff")] = NumberObject(flags)
            if mk:
                widget[NameObject("/MK")] = mk
            ref = writer._add_object(widget)
            annots.append(ref)
            field_list.append(ref)
    core._write(writer, output)
    return names
