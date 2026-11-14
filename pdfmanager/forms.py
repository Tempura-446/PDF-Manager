# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Fillable forms: add text boxes, checkboxes, radio buttons, dropdowns and
signature fields to a PDF.

The fields are standard PDF form fields, so they can be filled in with any
PDF reader, including this app's. Each field gets its own appearance (a
thin border, and a tick in a checked box), so it looks the same everywhere,
and readers draw the text in as it's typed.
"""

import json
import re

from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject,
                           NameObject, NumberObject, TextStringObject)

from pdfmanager import core

TYPES = {"text": "Text", "multiline": "Text", "checkbox": "Checkbox", "radio": "Group",
         "dropdown": "Dropdown", "signature": "Signature"}
MULTILINE, REQUIRED = 1 << 12, 1 << 1     # text field flags
NO_TOGGLE_OFF, RADIO, PUSHBUTTON = 1 << 14, 1 << 15, 1 << 16  # button field flags
COMBO, EDIT = 1 << 17, 1 << 18            # choice field flags
READ_ONLY = 1

# Formats for text fields, as Acrobat's form scripts (pdf.js runs them too).
# The number styles: separators "1,234.56" (0) or "1.234,56" (2).
FORMATS = ("number", "amount", "currency", "percent", "date", "phone", "zip")
CALCULATIONS = {"sum": "SUM", "average": "AVG", "product": "PRD", "min": "MIN", "max": "MAX"}
BORDER = (0.45, 0.45, 0.45)
TICK = "4"                                # ✔ in ZapfDingbats
DOT = "l"                                 # ● in ZapfDingbats
MIN_SIZE = 4                              # points


def _num(v):
    return FloatObject(round(float(v), 3))


def _walk_up(field):
    """``field``, its parent, grandparent... stopping at a loop (a damaged
    or crafted file can point a field at its own child)."""
    seen = set()
    while field is not None and len(seen) < 64:
        key = getattr(getattr(field, "indirect_reference", None), "idnum", None) or id(field)
        if key in seen:
            return
        seen.add(key)
        yield field
        field = field["/Parent"].get_object() if "/Parent" in field else None


def _field_of(widget):
    """The field a widget belongs to (a widget may be its own field)."""
    last = widget
    for field in _walk_up(widget):
        last = field
        if "/T" in field:
            return field
    return last


def _inherited(field, key, default=None):
    for ancestor in _walk_up(field):
        if key in ancestor:
            return ancestor[key]
    return default


def _qualified_name(field):
    parts = [str(f["/T"]) for f in _walk_up(field) if "/T" in f]
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
        return "dropdown" if flags & COMBO else "list"
    return "other"


def existing_fields(path, password=None):
    """The form fields already in a PDF: {1-based page: [{"rect", "name",
    "type"}]}, with rectangles in PDF points (origin bottom left)."""
    reader = core.open_pdf(path, password)
    found = {}
    for number, page in enumerate(reader.pages, 1):
        for ref in page.get("/Annots") or []:
            annot = ref.get_object()
            if not isinstance(annot, DictionaryObject):
                continue  # a null or missing annotation: viewers skip it too
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
    """'TYPE:PAGE:X0,Y0,X1,Y1[:NAME]' (from the command line) -> field dict.
    A radio button's NAME is GROUP=VALUE ("size=Large"); a dropdown's is
    NAME=CHOICE|CHOICE|... ("Country=France|Spain")."""
    try:
        kind, page, coords, *name = spec.split(":", 3)
        x0, y0, x1, y1 = (float(v) for v in coords.split(","))
        field = {"type": kind.strip().lower(), "page": int(page), "rect": [x0, y0, x1, y1]}
    except ValueError:
        raise core.PdfManagerError(
            f"invalid field {spec!r}; use TYPE:PAGE:X0,Y0,X1,Y1[:NAME], e.g. "
            "text:1:72,700,300,720:Name (points from the bottom left)") from None
    if name:
        name, sep, rest = name[0].partition("=")
        field["name"] = name
        if sep and field["type"] == "radio":
            field["value"] = rest
        elif sep and field["type"] == "dropdown":
            field["options"] = [o for o in rest.split("|") if o.strip()]
    return field


def _js(script):
    return DictionaryObject({NameObject("/S"): NameObject("/JavaScript"),
                             NameObject("/JS"): TextStringObject(script)})


def _js_string(text):
    return json.dumps(str(text))


def _format_scripts(field):
    """(format script, keystroke script) for a text field's "format"."""
    kind = str(field.get("format") or "").lower()
    if not kind:
        return None
    if kind not in FORMATS:
        raise core.PdfManagerError(f"unknown format {kind!r}; use one of {', '.join(FORMATS)}")
    sep = 2 if str(field.get("separators") or "") in ("1.234,56", "comma", "2") else 0
    if kind in ("number", "amount", "currency"):
        decimals = int(field.get("decimals", 0 if kind == "number" else 2))
        symbol = str(field.get("currency") or ("€" if kind == "currency" else "")) if kind == "currency" else ""
        before = "true" if not field.get("currency_after") else "false"
        args = f"{decimals}, {sep}, 0, 0, {_js_string(symbol)}, {before}"
        return f"AFNumber_Format({args});", f"AFNumber_Keystroke({args});"
    if kind == "percent":
        decimals = int(field.get("decimals", 0))
        return f"AFPercent_Format({decimals}, {sep});", f"AFPercent_Keystroke({decimals}, {sep});"
    if kind == "date":
        pattern = str(field.get("date") or "dd/mm/yyyy")
        if not re.fullmatch(r"[dmyHhMstT/.\-: ,]+", pattern):
            raise core.PdfManagerError(f"unknown date pattern {pattern!r}; e.g. dd/mm/yyyy")
        return (f"AFDate_FormatEx({_js_string(pattern)});",
                f"AFDate_KeystrokeEx({_js_string(pattern)});")
    special = {"zip": 0, "phone": 2}[kind]
    return f"AFSpecial_Format({special});", f"AFSpecial_Keystroke({special});"


def _calculation(field):
    """(operation, [field names]) of a calculated field, or None. Takes
    {"op": "sum", "fields": [...]} or "sum: Price, Tax"."""
    calc = field.get("calculate")
    if not calc:
        return None
    if isinstance(calc, str):
        op, _, names = calc.partition(":")
        calc = {"op": op, "fields": names}
    op = str(calc.get("op") or "sum").strip().lower()
    if op not in CALCULATIONS:
        raise core.PdfManagerError(f"unknown calculation {op!r}; use one of {', '.join(CALCULATIONS)}")
    names = calc.get("fields") or []
    if isinstance(names, str):
        names = names.split(",")
    names = [" ".join(str(n).split()) for n in names if str(n).strip()]
    if not names:
        raise core.PdfManagerError(f"say which fields {field.get('name') or 'a field'} adds up")
    return CALCULATIONS[op], names


def _validation(field):
    low, high = field.get("min"), field.get("max")
    if low in (None, "") and high in (None, ""):
        return None
    try:
        lo = float(low) if low not in (None, "") else 0
        hi = float(high) if high not in (None, "") else 0
    except (TypeError, ValueError):
        raise core.PdfManagerError("the smallest and largest values must be numbers") from None
    return (f"AFRange_Validate({'true' if low not in (None, '') else 'false'}, {lo:g}, "
            f"{'true' if high not in (None, '') else 'false'}, {hi:g});")


def _options(field):
    """A dropdown's choices: a list, or text with one per line or comma."""
    raw = field.get("options") or []
    if isinstance(raw, str):
        raw = re.split(r"[\n,]", raw)
    seen, out = set(), []
    for option in raw:
        option = " ".join(str(option).split())
        if option and option not in seen:
            seen.add(option)
            out.append(option)
    return out


def _state_name(value, taken):
    """A radio button's on-state name (its export value): the button's
    label, distinct within its group ("Off" is the off state)."""
    value = " ".join(str(value or "").split()) or "Choice"
    if value.lower() == "off":
        value = "Off choice"
    name, n = value, 2
    while name in taken:
        name, n = f"{value} {n}", n + 1
    taken.add(name)
    return name


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


def _circle(cx, cy, r, op):
    """A circle as four Bezier curves, then ``op`` (S strokes, f fills)."""
    k = 0.5523 * r
    return (f"{cx + r:.2f} {cy:.2f} m "
            f"{cx + r:.2f} {cy + k:.2f} {cx + k:.2f} {cy + r:.2f} {cx:.2f} {cy + r:.2f} c "
            f"{cx - k:.2f} {cy + r:.2f} {cx - r:.2f} {cy + k:.2f} {cx - r:.2f} {cy:.2f} c "
            f"{cx - r:.2f} {cy - k:.2f} {cx - k:.2f} {cy - r:.2f} {cx:.2f} {cy - r:.2f} c "
            f"{cx + k:.2f} {cy - r:.2f} {cx + r:.2f} {cy - k:.2f} {cx + r:.2f} {cy:.2f} c {op}\n")


def _radio_look(width, height, border):
    """(off, on) appearances of a round button."""
    cx, cy = width / 2, height / 2
    r = max(1.0, min(width, height) / 2 - 0.75)
    ring = ""
    if border:
        rr, gg, bb = BORDER
        ring = f"q {rr} {gg} {bb} RG 1 w " + _circle(cx, cy, r, "S") + "Q\n"
    dot = "q 0 g " + _circle(cx, cy, max(0.8, r * 0.45), "f") + "Q\n"
    return ring, ring + dot


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
    checkbox/radio/dropdown/signature, "name" (optional), "required"
    (optional)}. Radio buttons with the same name are one group, each with
    its own "value"; a dropdown has "options" (a list, or text with one per
    line) and "editable" (also takes typed text). ``font_size`` 0 fits
    single-line text to its box. Returns the names given to the new fields
    (made unique if a name was taken)."""
    reader = core.open_pdf(path, password)
    root = reader.trailer["/Root"]
    if "/AcroForm" in root and "/XFA" in root["/AcroForm"].get_object():
        raise core.PdfManagerError(
            "this PDF is an XFA form (made with Adobe LiveCycle); fields can't be added to it")
    taken = set()
    for page_fields in existing_fields(path, password).values():
        taken.update(f["name"] for f in page_fields)

    writer = core.clone_document(reader)
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
        if kind == "dropdown" and not _options(field) and not field.get("editable"):
            label = field.get("name") or "a dropdown"
            raise core.PdfManagerError(f"{label} on page {page} has no choices: add them, "
                                       "separated by commas")
        by_page.setdefault(page, []).append((kind, rect, field))
    if not by_page:
        raise core.PdfManagerError("no fields to add: draw boxes on the pages")

    groups = {}  # radio group as asked -> {"field": parent, "states": set}
    calculated = []  # (name, the fields it uses, widget): for the calculation order
    for page_number in sorted(by_page):
        page = writer.pages[page_number - 1]
        rotation = core.quarter_turn(page.rotation)
        annots = _child(page, "/Annots", ArrayObject)
        if "/Tabs" not in page:
            page[NameObject("/Tabs")] = NameObject("/R")  # Tab moves along rows
        # In reading order, so Tab goes from field to field down the page.
        for kind, rect, field in sorted(by_page[page_number], key=lambda f: (-f[1][3], f[1][0])):
            width, height = rect[2] - rect[0], rect[3] - rect[1]
            if rotation in (90, 270):
                width, height = height, width
            widget = DictionaryObject({
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Widget"),
                NameObject("/Rect"): ArrayObject(_num(v) for v in rect),
                NameObject("/F"): NumberObject(4),  # printed
                NameObject("/P"): page.indirect_reference,
            })
            mk = DictionaryObject()
            edged = field.get("border", border)  # (a found field sits on the form's own line)
            if edged:
                mk[NameObject("/BC")] = ArrayObject(_num(c) for c in BORDER)
                widget[NameObject("/BS")] = DictionaryObject(
                    {NameObject("/W"): NumberObject(1), NameObject("/S"): NameObject("/S")})
            if rotation:
                mk[NameObject("/R")] = NumberObject(rotation)
            flags = REQUIRED if field.get("required") else 0
            frame = _border(width, height, edged)
            target = widget  # where the field's own entries go (a radio group: its parent)
            if kind == "radio":
                asked = " ".join(str(field.get("name") or "").split())
                group = groups.get(asked)
                if group is None:
                    name = _unique(asked, taken, TYPES[kind])
                    names.append(name)
                    parent = DictionaryObject({
                        NameObject("/FT"): NameObject("/Btn"),
                        NameObject("/Ff"): NumberObject(RADIO | NO_TOGGLE_OFF | flags),
                        NameObject("/T"): TextStringObject(name),
                        NameObject("/TU"): TextStringObject(name),  # what screen readers say
                        NameObject("/V"): NameObject("/Off"),
                        NameObject("/Kids"): ArrayObject(),
                    })
                    group = groups[asked] = {"ref": writer._add_object(parent), "states": set()}
                    field_list.append(group["ref"])
                state = _state_name(field.get("value"), group["states"])
                off, on = _radio_look(width, height, edged)
                mk[NameObject("/CA")] = TextStringObject(DOT)
                widget.update({
                    NameObject("/Parent"): group["ref"],
                    NameObject("/AS"): NameObject("/Off"),
                    NameObject("/AP"): DictionaryObject({NameObject("/N"): DictionaryObject({
                        NameObject("/" + state): _stream(writer, width, height, on, rotation),
                        NameObject("/Off"): _stream(writer, width, height, off, rotation),
                    })}),
                })
                if mk:
                    widget[NameObject("/MK")] = mk
                ref = writer._add_object(widget)
                group["ref"].get_object()["/Kids"].append(ref)
                annots.append(ref)
                continue

            name = _unique(field.get("name"), taken, TYPES[kind])
            names.append(name)
            widget[NameObject("/T")] = TextStringObject(name)
            widget[NameObject("/TU")] = TextStringObject(name)  # what screen readers say
            if kind in ("text", "multiline"):
                size = font_size or (10 if kind == "multiline" else 0)
                flags |= MULTILINE if kind == "multiline" else 0
                actions = DictionaryObject()
                scripts = _format_scripts(field)
                if scripts:
                    actions[NameObject("/F")], actions[NameObject("/K")] = _js(scripts[0]), _js(scripts[1])
                check = _validation(field)
                if check:
                    actions[NameObject("/V")] = _js(check)
                calc = _calculation(field)
                if calc:
                    op, sources = calc
                    actions[NameObject("/C")] = _js(
                        f"AFSimple_Calculate({_js_string(op)}, new Array("
                        + ", ".join(_js_string(n) for n in sources) + "));")
                    flags |= READ_ONLY  # worked out, not typed
                    calculated.append((name, sources, widget))
                if actions:
                    widget[NameObject("/AA")] = actions
                widget.update({
                    NameObject("/FT"): NameObject("/Tx"),
                    NameObject("/DA"): TextStringObject(f"/Helv {size:g} Tf 0 g"),
                    NameObject("/AP"): DictionaryObject({NameObject("/N"): _stream(
                        writer, width, height, frame + "/Tx BMC EMC\n", rotation)}),
                })
            elif kind == "dropdown":
                flags |= COMBO | (EDIT if field.get("editable") else 0)
                widget.update({
                    NameObject("/FT"): NameObject("/Ch"),
                    NameObject("/Opt"): ArrayObject(TextStringObject(o) for o in _options(field)),
                    NameObject("/DA"): TextStringObject(f"/Helv {font_size or 0:g} Tf 0 g"),
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
                target[NameObject("/Ff")] = NumberObject(flags)
            if mk:
                widget[NameObject("/MK")] = mk
            ref = writer._add_object(widget)
            annots.append(ref)
            field_list.append(ref)
    if calculated:
        _calculation_order(acroform, calculated, writer)
    core._write(writer, output)
    return names


def _calculation_order(acroform, calculated, writer):
    """/CO: calculated fields, each after the ones it uses (a total of
    subtotals comes last); added to any order the form had."""
    known = {name for name, _, _ in calculated}
    done, order = set(), []

    def visit(name, path=()):
        if name in done or name in path:
            return
        entry = next(c for c in calculated if c[0] == name)
        for used in entry[1]:
            if used in known:
                visit(used, path + (name,))
        done.add(name)
        order.append(entry)

    for name, _, _ in calculated:
        visit(name)
    co = _child(acroform, "/CO", ArrayObject)
    for _, _, widget in order:
        co.append(widget.indirect_reference if widget.indirect_reference else widget)
