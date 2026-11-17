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

from pypdf.generic import (ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NumberObject, TextStringObject)

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


def _widgets(pages):
    """(1-based page, place among the page's fields, widget, its field) for
    every form field shown on ``pages``."""
    for number, page in enumerate(pages, 1):
        index = 0
        for ref in page.get("/Annots") or []:
            annot = ref.get_object()
            if not isinstance(annot, DictionaryObject):
                continue  # a null or missing annotation: viewers skip it too
            if annot.get("/Subtype") != "/Widget" or "/Rect" not in annot:
                continue
            yield number, index, annot, _field_of(annot)
            index += 1


def _rect_of(annot):
    x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
    return [round(min(x0, x1), 1), round(min(y0, y1), 1),
            round(max(x0, x1), 1), round(max(y0, y1), 1)]


def existing_fields(path, password=None):
    """The form fields already in a PDF: {1-based page: [{"rect", "name",
    "type"}]}, with rectangles in PDF points (origin bottom left)."""
    reader = core.open_pdf(path, password)
    found = {}
    for number, _, annot, field in _widgets(reader.pages):
        found.setdefault(number, []).append({
            "rect": _rect_of(annot), "name": _qualified_name(field), "type": _kind(field)})
    return found


_SIMPLE_CALC = re.compile(r"\s*AFSimple_Calculate\(\s*(\"[^\"]*\")\s*,\s*new Array\s*\((.*)\)\s*\)\s*;?\s*", re.S)


def _calc_of(field):
    """(operation, [names]) of a field worked out with AFSimple_Calculate
    (the way this app and Acrobat's "value is the sum of" write it), else
    None."""
    try:
        script = field["/AA"]["/C"]["/JS"]
    except (KeyError, TypeError):
        return None
    match = _SIMPLE_CALC.fullmatch(str(script)) if isinstance(script, str) else None
    if not match:
        return None
    try:
        names = json.loads("[" + match.group(2) + "]")
    except ValueError:
        return None
    if not all(isinstance(n, str) for n in names):
        return None
    return json.loads(match.group(1)), names


def _set_calc(field, op, names):
    field["/AA"]["/C"][NameObject("/JS")] = TextStringObject(
        f"AFSimple_Calculate({_js_string(op)}, new Array("
        + ", ".join(_js_string(n) for n in names) + "));")


_NUMBER = re.compile(r"\s*AFNumber_Format\(\s*(\d+)\s*,\s*(\d+)\s*,\s*\d+\s*,\s*\d+\s*,\s*(\"(?:[^\"\\]|\\.)*\")\s*,\s*(true|false)\s*\)\s*;?\s*")
_PERCENT = re.compile(r"\s*AFPercent_Format\(\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*(?:true|false)\s*)?\)\s*;?\s*")
_DATE = re.compile(r"\s*AFDate_FormatEx\(\s*(\"(?:[^\"\\]|\\.)*\")\s*\)\s*;?\s*")
_SPECIAL = re.compile(r"\s*AFSpecial_Format\(\s*(\d+)\s*\)\s*;?\s*")
_RANGE = re.compile(r"\s*AFRange_Validate\(\s*(true|false)\s*,\s*(-?[\d.eE+-]+)\s*,\s*(true|false)\s*,\s*(-?[\d.eE+-]+)\s*\)\s*;?\s*")
_OPERATIONS = {code: name for name, code in CALCULATIONS.items()}


def _script(field, key):
    """The script a field runs for ``key`` ("/F" to show its value, "/V" to
    check it, "/C" to work it out), or None."""
    try:
        action = field["/AA"][key].get_object()
        script = action["/JS"].get_object()
    except (KeyError, TypeError, AttributeError):
        return None
    if isinstance(script, str):
        return str(script)
    try:
        return script.get_data().decode("latin-1")  # (a script kept as a stream)
    except Exception:  # noqa: BLE001 - not a script that can be read
        return ""


def _format_of(field):
    """What a text field takes, read back from Acrobat's form scripts into
    the choices ``add_fields`` offers: {"format": "number" | "amount" |
    "currency" | "percent" | "date" | "phone" | "zip", ...} with "decimals",
    "separators", "currency", "currency_after" or "date" where they apply;
    {"format": "custom"} for a script that is none of these; None without one."""
    script = _script(field, "/F")
    if script is None:
        return None
    try:
        match = _NUMBER.fullmatch(script)
        if match:
            decimals, sep, symbol = int(match.group(1)), int(match.group(2)), json.loads(match.group(3))
            out = {"format": "currency" if symbol.strip() else "amount" if decimals else "number",
                   "decimals": decimals, "separators": "1.234,56" if sep in (2, 3) else "1,234.56"}
            if symbol.strip():
                out.update(currency=symbol, currency_after=match.group(4) == "false")
            return out
        match = _PERCENT.fullmatch(script)
        if match:
            return {"format": "percent", "decimals": int(match.group(1)),
                    "separators": "1.234,56" if int(match.group(2)) in (2, 3) else "1,234.56"}
        match = _DATE.fullmatch(script)
        if match:
            return {"format": "date", "date": json.loads(match.group(1))}
        match = _SPECIAL.fullmatch(script)
        if match and int(match.group(1)) in (0, 2):
            return {"format": "zip" if int(match.group(1)) == 0 else "phone"}
    except ValueError:
        pass
    return {"format": "custom"}


def _range_of(field):
    """(smallest, largest) a field checks its number against with
    AFRange_Validate, each None when not set."""
    match = _RANGE.fullmatch(_script(field, "/V") or "")
    if not match:
        return None, None
    try:
        return (float(match.group(2)) if match.group(1) == "true" else None,
                float(match.group(4)) if match.group(3) == "true" else None)
    except ValueError:
        return None, None


def _total_of(field):
    """How a field is worked out: {"op": "sum" | "average" | "product" |
    "min" | "max", "fields": [names]}, "custom" for a script of its own,
    None when it's typed."""
    if _script(field, "/C") is None:
        return None
    calc = _calc_of(field)
    if not calc or calc[0] not in _OPERATIONS:
        return "custom"
    return {"op": _OPERATIONS[calc[0]], "fields": list(calc[1])}


def _value_of(field, kind):
    value = _inherited(field, "/V")
    value = value.get_object() if value is not None else None
    if value is None or kind == "signature":
        return ""
    if isinstance(value, NameObject):
        return "" if value == "/Off" else str(value)[1:]
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value) if isinstance(value, str) else ""


def _choices_of(field):
    out = []
    for option in _inherited(field, "/Opt") or []:
        option = option.get_object()
        if isinstance(option, (list, tuple)) and option:
            option = option[-1].get_object()  # [what's saved, what's shown]
        out.append(str(option))
    return out


def _key(obj):
    return id(obj)


def field_details(path, password=None):
    """Like ``existing_fields``, with what the form tool needs to change a
    field: its "index" (its place among the page's fields, which
    ``edit_fields`` takes), "tooltip", "required", "readonly", "value",
    "options" (a dropdown's or list's), "widgets" (how many places the field
    is shown: a radio group's buttons, or one box repeated), "calculated",
    "format", "min", "max" and "calculate" (what a text box takes and how it
    is worked out, read back from Acrobat's form scripts: see ``_format_of``,
    ``_range_of`` and ``_total_of``), "used_by" (the fields worked out from
    it) and "signed"."""
    reader = core.open_pdf(path, password)
    rows = list(_widgets(reader.pages))
    shown, users = {}, {}
    seen = set()
    for _, _, _, field in rows:
        shown[_key(field)] = shown.get(_key(field), 0) + 1
        if _key(field) in seen:
            continue
        seen.add(_key(field))
        calc = _calc_of(field)
        for used in calc[1] if calc else ():
            users.setdefault(used, []).append(_qualified_name(field))
    found = {}
    for number, index, annot, field in rows:
        kind, name = _kind(field), _qualified_name(field)
        flags = int(_inherited(field, "/Ff", 0))
        tooltip = _inherited(field, "/TU")
        found.setdefault(number, []).append({
            "index": index, "rect": _rect_of(annot), "name": name, "type": kind,
            "tooltip": str(tooltip) if tooltip is not None else "",
            "required": bool(flags & REQUIRED), "readonly": bool(flags & READ_ONLY),
            "value": _value_of(field, kind), "options": _choices_of(field),
            "widgets": shown[_key(field)],
            "calculated": "/C" in (field.get("/AA") or {}),
            "format": _format_of(field), "min": _range_of(field)[0], "max": _range_of(field)[1],
            "calculate": _total_of(field),
            "used_by": users.get(name, []),
            "signed": kind == "signature" and _inherited(field, "/V") is not None})
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


def _paint(colour, stroke):
    """The operator setting a /MK colour (grey, RGB or CMYK), or ""."""
    try:
        parts = [float(v) for v in colour or []]
    except (TypeError, ValueError):
        return ""
    op = {1: "g", 3: "rg", 4: "k"}.get(len(parts))
    if not op:
        return ""
    return " ".join(f"{v:g}" for v in parts) + " " + (op.upper() if stroke else op)


def _frame_of(annot, width, height):
    """A widget's own background and border, drawn at a new size."""
    mk = annot.get("/MK") or {}
    out = ""
    fill = _paint(mk.get("/BG"), False)
    if fill:
        out += f"q {fill} 0 0 {width:.2f} {height:.2f} re f Q\n"
    line = _paint(mk.get("/BC"), True)
    if line:
        try:
            thick = float((annot.get("/BS") or {}).get("/W", 1))
        except (TypeError, ValueError):
            thick = 1.0
        if thick > 0:
            out += (f"q {line} {thick:g} w {thick / 2:g} {thick / 2:g} "
                    f"{width - thick:.2f} {height - thick:.2f} re S Q\n")
    return out


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


# -- changing the fields a form already has -----------------------------------------------------

def _drop(array, obj):
    """Take every reference to ``obj`` out of ``array``."""
    for i in range(len(array) - 1, -1, -1):
        if array[i].get_object() is obj:
            del array[i]


def _retype(writer, acroform, field, shown, kind, wanted, change, number):
    """Turn a field that holds words into another kind that does: a box to
    type in (text, multiline) into a list to choose from (dropdown, list) or
    back, or one kind of list into the other. It keeps its name, its place
    and what goes with them; what it is set to is kept when the new kind can
    hold it. Returns a note for the user, or None."""
    name, note = _qualified_name(field), None
    value = _inherited(field, "/V")
    if isinstance(value, ArrayObject):        # (a list with several chosen: the first)
        value = value[0] if len(value) else None
    value = None if value is None else str(value)
    flags = int(_inherited(field, "/Ff", 0)) & (REQUIRED | READ_ONLY | 1 << 2)   # what any field can be
    if wanted in ("dropdown", "list"):
        options = _options(change) if change.get("options") is not None else [str(o[1] if isinstance(o, ArrayObject) else o)
                                                                              for o in _inherited(field, "/Opt") or []]
        if not options and value:
            options = [value]
        if not options:
            raise core.PdfManagerError(f"{name} on page {number} needs choices to become a {wanted}: "
                                       "add them, separated by commas")
        field[NameObject("/FT")] = NameObject("/Ch")
        field[NameObject("/Opt")] = ArrayObject(TextStringObject(o) for o in options)
        flags |= COMBO if wanted == "dropdown" else 0
        if value is not None and value not in options:
            note, value = f"{name} was set to \"{value}\", which isn't one of its choices: it's now empty.", None
        for key in ("/MaxLen", "/I"):
            if key in field:
                del field[key]
        actions = field["/AA"].get_object() if "/AA" in field else None
        if actions is not None:               # (what could be typed: a number, a date... doesn't apply to a list)
            for key in ("/F", "/K", "/V"):
                if key in actions:
                    del actions[key]
            if not len(actions):
                del field["/AA"]
    else:
        field[NameObject("/FT")] = NameObject("/Tx")
        for key in ("/Opt", "/I", "/TI"):
            if key in field:
                del field[key]
        flags |= MULTILINE if wanted == "multiline" else 0
    field[NameObject("/Ff")] = NumberObject(flags)
    for key in ("/V", "/DV"):
        if key in field:
            del field[key]
    if value is not None:
        field[NameObject("/V")] = TextStringObject(value)
    for annot in shown:                        # drawn again as what it now is; readers fill in the words
        x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
        _remake(writer, acroform, annot, field, wanted, abs(x1 - x0), abs(y1 - y0))
    return note


def _unset(field, *keys):
    """Take actions out of a field's /AA (and /AA itself when empty)."""
    actions = field.get("/AA")
    actions = actions.get_object() if actions is not None else None
    if actions is None:
        return
    for key in keys:
        if key in actions:
            del actions[key]
    if not len(actions):
        del field["/AA"]


def _rules(field, change, taken, calculated):
    """Set what a text box takes and how it's worked out, from a change's
    "format", "min"/"max" and "calculate" (as ``add_fields`` takes them; ""
    takes each away). Returns notes for the user."""
    notes, name = [], _qualified_name(field)
    if "/AA" in field:   # (its own, not one shared with another field)
        field[NameObject("/AA")] = DictionaryObject(field["/AA"].get_object())

    def actions():
        return _child(field, "/AA", DictionaryObject)

    if change.get("format") is not None:
        if str(change["format"]).strip().lower() in ("", "none", "text"):
            _unset(field, "/F", "/K")
        else:
            shows, typed = _format_scripts(change)
            actions()[NameObject("/F")], actions()[NameObject("/K")] = _js(shows), _js(typed)
    if change.get("min") is not None or change.get("max") is not None:
        low, high = _range_of(field)
        check = _validation({"min": change.get("min", low) if change.get("min") is not None else low,
                             "max": change.get("max", high) if change.get("max") is not None else high})
        if check:
            actions()[NameObject("/V")] = _js(check)
        else:
            _unset(field, "/V")
    if change.get("calculate") is not None:
        flags = int(_inherited(field, "/Ff", 0))
        if not change["calculate"] or (isinstance(change["calculate"], str) and not change["calculate"].strip()):
            if _script(field, "/C") is not None:
                _unset(field, "/C")
                field[NameObject("/Ff")] = NumberObject(flags & ~READ_ONLY)  # typed again
                calculated.append(field)
        else:
            op, sources = _calculation({**change, "name": name})
            if name in sources:
                raise core.PdfManagerError(f"{name} can't be worked out from itself")
            missing = [n for n in sources if n not in taken]
            if missing:
                notes.append(f"{name} is worked out from {', '.join(missing)}, which this form "
                             "doesn't have: check the name" + ("s." if len(missing) > 1 else "."))
            actions()[NameObject("/C")] = _js("")
            _set_calc(field, op, sources)
            field[NameObject("/Ff")] = NumberObject(flags | READ_ONLY)  # worked out, not typed
            calculated.append(field)
    return notes


def _reorder(acroform, fields, touched):
    """/CO after totals were set or taken away: every calculated field once,
    each after the ones it's worked out from; the order the form had is kept
    where that allows."""
    co = _child(acroform, "/CO", ArrayObject)
    listed = [ref.get_object() for ref in co]
    for field in touched:
        if _script(field, "/C") is None:
            listed = [f for f in listed if f is not field]
        elif not any(f is field for f in listed):
            listed.append(field)
    by_name = {_qualified_name(f): f for f in listed}
    done, order = [], []

    def visit(field, path=()):
        if any(f is field for f in done) or any(f is field for f in path):
            return
        calc = _calc_of(field)
        for used in calc[1] if calc else ():
            if used in by_name:
                visit(by_name[used], path + (field,))
        done.append(field)
        order.append(field)

    for field in listed:
        visit(field)
    co.clear()
    for field in order:
        co.append(field.indirect_reference if field.indirect_reference else field)
    if not len(co):
        del acroform["/CO"]


def _to_page(writer, annot, number, target):
    """Take a field's widget from page ``number`` to page ``target`` (both
    1-based): it leaves one page's list for the end of the other's (so it
    comes last when tabbing through that page) and names its new page. Its
    place on the paper stays, unless that isn't on the new page: then it is
    moved just far enough to be on it. Returns True if it had to be."""
    source, page = writer.pages[number - 1], writer.pages[target - 1]
    ref = next((r for r in source.get("/Annots") or [] if r.get_object() is annot), None)
    if ref is None:
        return False  # (asked for twice: it has gone already)
    _drop(source["/Annots"], annot)
    if "/Annots" not in page:
        page[NameObject("/Annots")] = ArrayObject()
    page["/Annots"].get_object().append(ref)
    annot[NameObject("/P")] = page.indirect_reference
    box = [float(v) for v in page.mediabox]
    left, bottom, right, top = min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])
    x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
    x0, y0, x1, y1 = min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
    dx = max(left - x0, 0) or min(right - x1, 0)
    dy = max(bottom - y0, 0) or min(top - y1, 0)
    if not dx and not dy:
        return False
    annot[NameObject("/Rect")] = ArrayObject(_num(v) for v in (x0 + dx, y0 + dy, x1 + dx, y1 + dy))
    return True


def _remake(writer, acroform, annot, field, kind, width, height):
    """Draw a resized widget's appearance again at its new size, with its
    own border and background. Returns True if readers should redraw the
    value (text was showing). Kinds this app doesn't draw (push buttons, a
    signed signature) keep their look, which readers stretch to fit."""
    rotation = core.quarter_turn(int((annot.get("/MK") or {}).get("/R", 0) or 0))
    if rotation in (90, 270):
        width, height = height, width
    frame = _frame_of(annot, width, height)

    def look(content, resources=None):
        return _stream(writer, width, height, content, rotation, resources)

    if kind in ("text", "multiline", "dropdown", "list"):
        annot[NameObject("/AP")] = DictionaryObject({NameObject("/N"): look(frame + "/Tx BMC EMC\n")})
        return _inherited(field, "/V") is not None
    if kind == "signature":
        if _inherited(field, "/V") is None:
            annot[NameObject("/AP")] = DictionaryObject({NameObject("/N"): look(frame)})
        return False
    if kind not in ("checkbox", "radio"):
        return False
    try:
        states = [k for k in annot["/AP"]["/N"].keys() if k != "/Off"]
    except (KeyError, AttributeError, TypeError):
        states = []
    states = states or ["/Yes"]
    glyph = str((annot.get("/MK") or {}).get("/CA", DOT if kind == "radio" else TICK))[:1] or TICK
    resources = None
    if kind == "radio" and glyph == DOT:
        edged = bool(_paint((annot.get("/MK") or {}).get("/BC"), True))
        off, on = _radio_look(width, height, False)
        ring = ""
        if edged:
            cx, cy, r = width / 2, height / 2, max(1.0, min(width, height) / 2 - 0.75)
            ring = (f"q {_paint(annot['/MK']['/BC'], True)} 1 w " + _circle(cx, cy, r, "S") + "Q\n")
        off, on = ring + off, ring + on
    else:
        fonts = _fonts(writer, acroform)
        resources = DictionaryObject({NameObject("/Font"): DictionaryObject(
            {NameObject("/ZaDb"): fonts["/ZaDb"]})})
        size = min(width, height) * 0.8
        tx, ty = (width - 0.846 * size) / 2, (height - 0.7 * size) / 2
        mark = glyph.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        off = frame
        on = frame + f"q BT 0 g /ZaDb {size:.2f} Tf {tx:.2f} {ty:.2f} Td ({mark}) Tj ET Q\n"
    normal = DictionaryObject({NameObject("/Off"): look(off)})
    for state in states:
        normal[NameObject(state)] = look(on, resources)
    annot[NameObject("/AP")] = DictionaryObject({NameObject("/N"): normal})
    return False


def edit_fields(path, output, changes, password=None):
    """Change form fields a PDF already has. Each change names a field by
    its "page" (1-based) and "index" (its place among that page's fields, as
    ``field_details`` lists them) and carries any of: "rect" [x0, y0, x1,
    y1] in PDF points (move or resize), "to_page" (the 1-based page it goes
    to, keeping its place on the paper unless "rect" says another), "name",
    "tooltip", "required", "readonly", "type" ("text", "multiline",
    "dropdown" or "list": a field that holds words can become another kind
    that does), "options" (a dropdown's or list's choices) and "delete". A
    text box also takes what ``add_fields`` does for one: "format" (with
    "decimals", "separators", "currency", "currency_after", "date"; "" or
    "none" for any text), "min" and "max" ("" for none), and "calculate"
    ({"op", "fields"} or "sum: Price, Tax"; "" to have it typed again).

    A name is kept unique, and the calculations that used the old name use
    the new one. A deleted field is taken out of the calculations that used
    it. Returns {"changed", "deleted", "names": {old: new}, "notes"}."""
    reader = core.open_pdf(path, password)
    root = reader.trailer["/Root"]
    if "/AcroForm" in root and "/XFA" in root["/AcroForm"].get_object():
        raise core.PdfManagerError(
            "this PDF is an XFA form (made with Adobe LiveCycle); its fields can't be changed")
    changes = [c for c in changes or [] if isinstance(c, dict)]
    if not changes:
        raise core.PdfManagerError("nothing to change: move, rename or delete a field first")

    writer = core.clone_document(reader)
    acroform = _child(writer._root_object, "/AcroForm", DictionaryObject)
    field_list = _child(acroform, "/Fields", ArrayObject)
    rows = list(_widgets(writer.pages))
    where = {(number, index): (annot, field) for number, index, annot, field in rows}
    fields = {}  # every field shown on a page, once
    for _, _, _, field in rows:
        fields.setdefault(_key(field), field)
    first_names = {key: _qualified_name(field) for key, field in fields.items()}
    taken = set(first_names.values())
    notes, redraw = [], False
    if any(_kind(f) == "signature" and _inherited(f, "/V") is not None for f in fields.values()):
        notes.append("This file is signed: changing its fields makes the signature show as "
                     "no longer valid.")

    targets = []
    for change in changes:
        try:
            spot = (int(change.get("page", 1)), int(change.get("index", -1)))
        except (TypeError, ValueError):
            spot = None
        if spot not in where:
            raise core.PdfManagerError(
                f"page {change.get('page', 1)} has no field number {change.get('index')}")
        targets.append((spot[0], *where[spot], change))

    deleted, changed = [], 0
    calculated = []  # fields whose total was set or taken away
    for number, annot, field, change in targets:
        if change.get("delete"):
            deleted.append((number, annot, field))
            continue
        kind, touched = _kind(field), False

        if change.get("rect") is not None:
            try:
                x0, y0, x1, y1 = (float(v) for v in change["rect"])
            except (TypeError, ValueError):
                raise core.PdfManagerError(f"a field on page {number} has no usable place") from None
            rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            width, height = rect[2] - rect[0], rect[3] - rect[1]
            if width < MIN_SIZE or height < MIN_SIZE:
                raise core.PdfManagerError(f"a field on page {number} is too small")
            old = _rect_of(annot)
            resized = (abs((old[2] - old[0]) - width) > 0.05 or abs((old[3] - old[1]) - height) > 0.05)
            annot[NameObject("/Rect")] = ArrayObject(_num(v) for v in rect)
            if resized:
                redraw = _remake(writer, acroform, annot, field, kind, width, height) or redraw
            touched = True

        if change.get("to_page") is not None:
            try:
                target = int(change["to_page"])
            except (TypeError, ValueError):
                target = 0
            if not 1 <= target <= len(writer.pages):
                count = len(writer.pages)
                raise core.PdfManagerError(
                    f"{_qualified_name(field)} can't go to page {change['to_page']}: this PDF has "
                    f"{count} page{'' if count == 1 else 's'}")
            if target != number:
                moved = _to_page(writer, annot, number, target)
                if moved:
                    notes.append(f"{_qualified_name(field)} didn't fit page {target} where it was: "
                                 "it was moved onto the page.")
                touched = True

        if change.get("name") is not None:
            current = _qualified_name(field)
            prefix = current[:-len(str(field.get("/T", "")))] if "/T" in field else ""
            asked = " ".join(str(change["name"]).replace(".", " ").split())
            if asked and prefix + asked != current:
                taken.discard(current)
                candidate, n = asked, 2
                while prefix + candidate in taken:
                    candidate, n = f"{asked} {n}", n + 1
                taken.add(prefix + candidate)
                short = str(field.get("/T", ""))
                field[NameObject("/T")] = TextStringObject(candidate)
                if str(field.get("/TU", "")) in (short, current) and "/TU" in field:
                    field[NameObject("/TU")] = TextStringObject(candidate)
                for other in fields.values():  # the sums that added it up
                    calc = _calc_of(other)
                    if calc and current in calc[1]:
                        _set_calc(other, calc[0], [prefix + candidate if n == current else n
                                                   for n in calc[1]])
                touched = True

        if change.get("tooltip") is not None:
            tip = " ".join(str(change["tooltip"]).split())
            if tip:
                field[NameObject("/TU")] = TextStringObject(tip)
            elif "/TU" in field:
                del field["/TU"]
            touched = True

        flags = before = int(_inherited(field, "/Ff", 0))
        for key, bit in (("required", REQUIRED), ("readonly", READ_ONLY)):
            if change.get(key) is not None:
                flags = flags | bit if change[key] else flags & ~bit

        wanted = change.get("type")
        if wanted is not None and str(wanted).lower() != kind:
            wanted = str(wanted).lower()
            typed = ("text", "multiline", "dropdown", "list")   # the kinds that hold words
            if kind not in typed or wanted not in typed:
                raise core.PdfManagerError(
                    f"a {kind} field can't be turned into a {wanted} field: delete it and draw "
                    "a new one")
            if (kind in ("dropdown", "list")) != (wanted in ("dropdown", "list")) or wanted in ("dropdown", "list"):
                # Between a box to type in and a list to choose from (or one kind of list and the other).
                shown = [a for _, _, a, f in rows if f is field]
                said = _retype(writer, acroform, field, shown, kind, wanted, change, number)
                if said:
                    notes.append(said)
                flags = int(_inherited(field, "/Ff", 0))
                for key, bit in (("required", REQUIRED), ("readonly", READ_ONLY)):
                    if change.get(key) is not None:
                        flags = flags | bit if change[key] else flags & ~bit
                before, redraw, touched = int(_inherited(field, "/Ff", 0)), True, True
                # (Several lines: the size of its letters is seen to just below, as for a text box.)
                kind, wanted = ("text", wanted) if wanted == "multiline" else (wanted, None)
                change = {k: v for k, v in change.items() if k != "options"}   # (its choices are set)
        if wanted is not None and wanted != kind:
            flags = flags | MULTILINE if wanted == "multiline" else flags & ~MULTILINE
            if wanted == "multiline":
                # Text fitted to the box (size 0) would shrink as lines are added.
                holder = annot if "/DA" in annot else field
                style = str(_inherited(holder, "/DA", acroform.get("/DA", "/Helv 0 Tf 0 g")))
                holder[NameObject("/DA")] = TextStringObject(
                    re.sub(r"(\s)0(\.0+)?(\s+Tf)", r"\g<1>10\3", style))
            redraw = redraw or _inherited(field, "/V") is not None
        if flags != before:
            field[NameObject("/Ff")] = NumberObject(flags)
            touched = True

        if any(change.get(k) is not None for k in ("format", "min", "max", "calculate")):
            if kind not in ("text", "multiline"):
                raise core.PdfManagerError(
                    f"{_qualified_name(field)} on page {number} is a {kind} field: only a text box "
                    "has a format, a smallest and largest value, or a total")
            said = _rules(field, change, taken, calculated)
            notes.extend(said)
            redraw = touched = True

        if change.get("options") is not None and kind in ("dropdown", "list"):
            options = _options(change)
            if not options and not flags & EDIT:
                raise core.PdfManagerError(
                    f"{_qualified_name(field)} on page {number} has no choices: add them, "
                    "separated by commas")
            field[NameObject("/Opt")] = ArrayObject(TextStringObject(o) for o in options)
            value = _inherited(field, "/V")
            if value is not None and str(value) not in options and not flags & EDIT:
                for key in ("/V", "/DV", "/I"):
                    if key in field:
                        del field[key]
                notes.append(f"{_qualified_name(field)} was set to \"{value}\", which is no "
                             "longer a choice: it's now empty.")
                redraw = True
            touched = True
        changed += touched

    gone = []  # names of fields deleted altogether
    order = acroform["/CO"] if "/CO" in acroform else None
    for number, annot, field in deleted:
        if not any(ref.get_object() is annot for ref in writer.pages[number - 1].get("/Annots") or []):
            continue  # asked for twice
        _drop(writer.pages[number - 1]["/Annots"], annot)
        if _kind(field) == "radio" and field is not annot and annot.get("/AS", "/Off") != "/Off":
            field[NameObject("/V")] = NameObject("/Off")
        node = annot
        for _ in range(64):  # up the tree, taking the branches left empty
            if order is not None:
                _drop(order, node)
            if node is field:
                gone.append(_qualified_name(field))
                fields.pop(_key(field), None)
            parent = node["/Parent"].get_object() if "/Parent" in node else None
            if parent is None:
                _drop(field_list, node)
                break
            if "/Kids" in parent:
                _drop(parent["/Kids"], node)
            if "/Kids" in parent and len(parent["/Kids"]):
                break
            node = parent
    for name in gone:
        for other in fields.values():
            calc = _calc_of(other)
            if not calc or name not in calc[1]:
                continue
            left = [n for n in calc[1] if n != name]
            notes.append(f"{_qualified_name(other)} was worked out from {name}, which is "
                         "deleted" + ("." if left else ": it's now an ordinary box to type in."))
            if left:
                _set_calc(other, calc[0], left)
                continue
            del other["/AA"]["/C"]
            if not len(other["/AA"]):
                del other["/AA"]
            other[NameObject("/Ff")] = NumberObject(int(_inherited(other, "/Ff", 0)) & ~READ_ONLY)
            if order is not None:
                _drop(order, other)

    if calculated:
        _reorder(acroform, fields, calculated)
    if redraw:
        acroform[NameObject("/NeedAppearances")] = BooleanObject(True)
    names = {}
    for key, field in fields.items():
        if _qualified_name(field) != first_names[key]:
            names[first_names[key]] = _qualified_name(field)
    core._write(writer, output)
    return {"changed": changed, "deleted": len(deleted), "names": names, "notes": notes}
