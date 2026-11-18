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


CALC_NAMES = {v: k for k, v in CALCULATIONS.items()}
_JS_STR = r'(?:"(?:[^"\\\n\r]|\\.)*"|\'(?:[^\'\\\n\r]|\\.)*\')'
_SIMPLE_CALC = re.compile(
    rf"\s*\ufeff*AFSimple_Calculate\(\s*({_JS_STR})\s*,\s*new\s+Array\s*\((.*?)\)\s*\)\s*;?\s*", re.S)
_NUMBER_FMT = re.compile(
    rf"\s*\ufeff*AFNumber_Format\(\s*(\d+)\s*,\s*(\d+)\s*,\s*\d+\s*,\s*\d+\s*,\s*({_JS_STR})\s*,\s*(true|false)\s*\)\s*;?\s*", re.S)
_PERCENT_FMT = re.compile(rf"\s*\ufeff*AFPercent_Format\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*;?\s*", re.S)
_DATE_FMT = re.compile(rf"\s*\ufeff*AFDate_Format(?:Ex)?\(\s*({_JS_STR})\s*\)\s*;?\s*", re.S)
_SPECIAL_FMT = re.compile(r"\s*\ufeff*AFSpecial_Format\(\s*(\d+)\s*\)\s*;?\s*", re.S)
_RANGE_VAL = re.compile(
    r"\s*\ufeff*AFRange_Validate\(\s*(true|false)\s*,\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*,"
    r"\s*(true|false)\s*,\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*\)\s*;?\s*", re.S)
_EVENT_CALC = re.compile(r"\s*\ufeff*event\.value\s*=\s*(.+?)\s*;?\s*", re.S)
_GET_FIELD = re.compile(
    rf"(?:Number\s*\(\s*)?this\.getField\(\s*({_JS_STR})\s*\)\.value(?:\s*\))?")
_FORMULA_TOKEN = re.compile(
    rf"\s*(?:(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|([+\-*/()])|\[([^\[\]\n\r]+)\]|({_JS_STR})|"
    r"([A-Za-z_][A-Za-z0-9_.]*(?: +[A-Za-z_][A-Za-z0-9_.]*)*))")


def _unquote_js(token):
    token = token.strip()
    if token.startswith("'") and token.endswith("'"):
        inner = token[1:-1].replace("\\'", "'").replace('"', '\\"')
        token = f'"{inner}"'
    return str(json.loads(token))


def _action_js(field, key):
    aa = _inherited(field, "/AA")
    aa = aa.get_object() if aa is not None else None
    if not isinstance(aa, DictionaryObject) or key not in aa:
        return None
    act = aa[key].get_object()
    js = act.get("/JS") if isinstance(act, DictionaryObject) else None
    js = js.get_object() if js is not None else None
    return str(js) if isinstance(js, str) else None


def _parse_formula(expr, rename=None):
    """Check a simple arithmetic formula over field names (e.g. ``Qty * Price``
    or ``([Net price] - Discount) * 1.2``) and return ``(clean_formula,
    field_names, js_code)``."""
    text = str(expr or "").strip()
    if not text:
        raise core.PdfManagerError("invalid calculation formula: give an expression such as Qty * Price")
    pos, tokens = 0, []
    while pos < len(text):
        m = _FORMULA_TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise core.PdfManagerError(f"invalid calculation formula {expr!r}")
        pos = m.end()
        num, op, bracketed, quoted, bare = m.groups()
        if num is not None:
            tokens.append(("num", num))
        elif op is not None:
            tokens.append(("op", op))
        elif bracketed is not None:
            name = " ".join(bracketed.split())
            if not name:
                raise core.PdfManagerError(f"invalid calculation formula {expr!r}")
            tokens.append(("field", name))
        elif quoted is not None:
            try:
                name = " ".join(_unquote_js(quoted).split())
            except ValueError:
                raise core.PdfManagerError(f"invalid calculation formula {expr!r}") from None
            if not name:
                raise core.PdfManagerError(f"invalid calculation formula {expr!r}")
            tokens.append(("field", name))
        elif bare is not None:
            tokens.append(("field", " ".join(bare.split())))
    want_operand, depth = True, 0
    fields, seen, js_parts, disp_parts = [], set(), [], []
    for i, (kind, val) in enumerate(tokens):
        if want_operand:
            if kind == "op" and val == "(":
                depth += 1
                js_parts.append("(")
                disp_parts.append("(")
            elif kind == "op" and val in ("+", "-"):
                js_parts.append(val)
                disp_parts.append(val)
            elif kind == "num":
                js_parts.append(val)
                disp_parts.append(val)
                want_operand = False
            elif kind == "field":
                if i + 1 < len(tokens) and tokens[i + 1] == ("op", "("):
                    raise core.PdfManagerError(f"invalid calculation formula {expr!r}")
                name = rename.get(val, val) if rename else val
                if name not in seen:
                    seen.add(name)
                    fields.append(name)
                js_parts.append(f"Number(this.getField({_js_string(name)}).value)")
                simple = bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name))
                disp_parts.append(name if simple else f"[{name}]")
                want_operand = False
            else:
                raise core.PdfManagerError(f"invalid calculation formula {expr!r}")
        else:
            if kind == "op" and val == ")":
                depth -= 1
                if depth < 0:
                    raise core.PdfManagerError(f"invalid calculation formula {expr!r}")
                js_parts.append(")")
                disp_parts.append(")")
            elif kind == "op" and val in ("+", "-", "*", "/"):
                js_parts.append(f" {val} ")
                disp_parts.append(f" {val} ")
                want_operand = True
            else:
                raise core.PdfManagerError(f"invalid calculation formula {expr!r}")
    if want_operand or depth != 0 or not fields:
        raise core.PdfManagerError(f"invalid calculation formula {expr!r}")
    return "".join(disp_parts), fields, "event.value = " + "".join(js_parts) + ";"


def _formula_of(script):
    """(formula, [names]) of an ``event.value = ...`` calculation over field
    names, else None."""
    match = _EVENT_CALC.fullmatch(script) if isinstance(script, str) else None
    if not match:
        return None
    body = match.group(1)
    outside = _GET_FIELD.sub("", body)
    if not re.fullmatch(r"[0-9.+\-*/() \t\r\n]*", outside):
        return None
    try:
        rebuilt = _GET_FIELD.sub(lambda m: f"[{_unquote_js(m.group(1))}]", body)
        formula, fields, _ = _parse_formula(rebuilt)
    except (core.PdfManagerError, ValueError):
        return None
    return formula, fields


def _calc_of(field):
    """(operation, [names]) of a field worked out with AFSimple_Calculate
    (the way this app and Acrobat's "value is the sum of" write it) or a
    simple formula over field names ("=formula"), else None."""
    script = _action_js(field, "/C")
    if not script:
        return None
    match = _SIMPLE_CALC.fullmatch(script)
    if match:
        try:
            op = _unquote_js(match.group(1))
            items = re.findall(_JS_STR, match.group(2))
            rest = re.sub(_JS_STR, "", match.group(2))
            if not re.fullmatch(r"[\s,]*", rest):
                return None
            names = [_unquote_js(s) for s in items]
        except ValueError:
            return None
        return (op, names) if names else None
    formula = _formula_of(script)
    if formula:
        return "=" + formula[0], formula[1]
    return None


def _calc_script(op, names):
    if op.startswith("="):
        return _parse_formula(op[1:])[2]
    return (f"AFSimple_Calculate({_js_string(op)}, new Array("
            + ", ".join(_js_string(n) for n in names) + "));")


def _set_calc(field, op, names):
    actions = _child(field, "/AA", DictionaryObject)
    actions[NameObject("/C")] = _js(_calc_script(op, names))


def _format_of(field):
    """The format of a text field as ``add_fields`` takes it ({"format":
    "amount", "decimals": 2, ...}), {"format": "custom"} for another script,
    or {} when it has none."""
    script = _action_js(field, "/F")
    if not script:
        return {}
    m = _NUMBER_FMT.fullmatch(script)
    if m:
        try:
            decimals, sep_code = int(m.group(1)), int(m.group(2))
            symbol = _unquote_js(m.group(3))
        except ValueError:
            return {"format": "custom"}
        sep = "1.234,56" if sep_code in (2, 3) else "1,234.56"
        before = m.group(4) == "true"
        if symbol:
            return {"format": "currency", "decimals": decimals, "separators": sep,
                    "currency": symbol, "currency_after": not before}
        return {"format": "amount" if decimals == 2 else "number",
                "decimals": decimals, "separators": sep}
    m = _PERCENT_FMT.fullmatch(script)
    if m:
        return {"format": "percent", "decimals": int(m.group(1)),
                "separators": "1.234,56" if int(m.group(2)) in (2, 3) else "1,234.56"}
    m = _DATE_FMT.fullmatch(script)
    if m:
        try:
            return {"format": "date", "date": _unquote_js(m.group(1))}
        except ValueError:
            return {"format": "custom"}
    m = _SPECIAL_FMT.fullmatch(script)
    if m and int(m.group(1)) in (0, 2):
        return {"format": "zip" if int(m.group(1)) == 0 else "phone"}
    return {"format": "custom"}


def _clean_number(text):
    val = float(text)
    return int(val) if val.is_integer() else val


def _range_of(field):
    """{"min": ..., "max": ...} of a field checked with AFRange_Validate,
    {"validate": "custom"} for another validation script, or {}."""
    script = _action_js(field, "/V")
    if not script:
        return {}
    m = _RANGE_VAL.fullmatch(script)
    if not m:
        return {"validate": "custom"}
    out = {}
    if m.group(1) == "true":
        out["min"] = _clean_number(m.group(2))
    if m.group(3) == "true":
        out["max"] = _clean_number(m.group(4))
    return out


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


def _calc_detail(field):
    calc = _calc_of(field)
    if calc:
        op, names = calc
        if op.startswith("="):
            return {"op": "formula", "formula": op[1:], "fields": names}
        return {"op": CALC_NAMES.get(op, op.lower()), "fields": names}
    if _action_js(field, "/C") is not None:
        return {"op": "custom", "fields": []}
    return None


def field_details(path, password=None):
    """Like ``existing_fields``, with what the form tool needs to change a
    field: its "index" (its place among the page's fields, which
    ``edit_fields`` takes), "tooltip", "required", "readonly", "value",
    "options" (a dropdown's or list's), "widgets" (how many places the field
    is shown: a radio group's buttons, or one box repeated), "format" (and
    its "decimals", "separators", "currency", "date"), "min", "max",
    "calculated", "calculate", "used_by" (the fields worked out from it) and
    "signed"."""
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
        fmt = _format_of(field) if kind in ("text", "multiline") else {}
        bounds = _range_of(field) if kind in ("text", "multiline") else {}
        calc_info = _calc_detail(field)
        info = {
            "index": index, "rect": _rect_of(annot), "name": name, "type": kind,
            "tooltip": str(tooltip) if tooltip is not None else "",
            "required": bool(flags & REQUIRED), "readonly": bool(flags & READ_ONLY),
            "value": _value_of(field, kind), "options": _choices_of(field),
            "widgets": shown[_key(field)],
            "format": fmt.get("format", ""),
            "calculated": calc_info is not None,
            "calculate": calc_info,
            "used_by": users.get(name, []),
            "signed": kind == "signature" and _inherited(field, "/V") is not None}
        for k in ("decimals", "separators", "currency", "currency_after", "date"):
            if k in fmt:
                info[k] = fmt[k]
        for k in ("min", "max", "validate"):
            if k in bounds:
                info[k] = bounds[k]
        found.setdefault(number, []).append(info)
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
    {"op": "sum", "fields": [...]}, "sum: Price, Tax", {"formula":
    "Qty * Price"}, or "Qty * Price"."""
    calc = field.get("calculate")
    if not calc:
        return None
    if isinstance(calc, dict) and ("formula" in calc or str(calc.get("op") or "").strip().lower() == "formula"):
        formula, names, _ = _parse_formula(calc.get("formula") if "formula" in calc else calc.get("fields", ""))
        return "=" + formula, names
    if isinstance(calc, str):
        text = calc.strip()
        if text.startswith("="):
            formula, names, _ = _parse_formula(text[1:])
            return "=" + formula, names
        if ":" in text:
            op, _, names = text.partition(":")
            if op.strip().lower() == "formula":
                formula, names, _ = _parse_formula(names)
                return "=" + formula, names
            calc = {"op": op, "fields": names}
        elif any(ch in text for ch in "+-*/"):
            formula, names, _ = _parse_formula(text)
            return "=" + formula, names
        else:
            calc = {"op": text, "fields": ""}
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
                    actions[NameObject("/C")] = _js(_calc_script(op, sources))
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


def _ref_of(annot, pages):
    if annot.indirect_reference is not None:
        return annot.indirect_reference
    return next((r for p in pages for r in p.get("/Annots") or [] if r.get_object() is annot), None)


def _retype(writer, acroform, field, shown, kind, wanted, change, number):
    """Turn a field that holds words or an unsigned signature into another
    kind in that family (text, multiline, dropdown, list, signature). It
    keeps its name, its place and what goes with them; what it is set to is
    kept when the new kind can hold it. Returns a note for the user, or None."""
    name, note = _qualified_name(field), None
    if kind == "signature" and _inherited(field, "/V") is not None:
        raise core.PdfManagerError(f"{name} is signed and can't be turned into a {wanted} field")
    value = None if kind == "signature" else _inherited(field, "/V")
    if isinstance(value, ArrayObject):        # (a list with several chosen: the first)
        value = value[0] if len(value) else None
    value = None if value is None else str(value)
    flags = int(_inherited(field, "/Ff", 0)) & (REQUIRED | READ_ONLY | 1 << 2)   # what any field can be
    if kind == "signature" and wanted != "signature":
        _fonts(writer, acroform)
        if "/DA" not in field and not any("/DA" in a for a in shown):
            field[NameObject("/DA")] = TextStringObject(f"/Helv {10 if wanted == 'multiline' else 0} Tf 0 g")
    if wanted == "signature":
        field[NameObject("/FT")] = NameObject("/Sig")
        acroform[NameObject("/SigFlags")] = NumberObject(int(acroform.get("/SigFlags", 0)) | 1)
        for key in ("/Opt", "/I", "/TI", "/MaxLen", "/AA"):
            if key in field:
                del field[key]
        if "/CO" in acroform:
            _drop(acroform["/CO"], field)
        if value:
            note, value = f"{name} was set to \"{value}\": a signature field starts empty.", None
        flags &= (REQUIRED | READ_ONLY)
    elif wanted in ("dropdown", "list"):
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


def _retype_button(writer, acroform, field_list, rows, annot, field, kind, wanted, change,
                   taken, fields, first_names):
    """Switch a tick box into a radio button (a group of one) or a radio
    group's buttons into separate tick boxes (named from their values when
    there are several). Keeps the name, place, tooltip, required flag and
    whether a button is chosen. Returns ``(new_field_for_annot,
    converted_widget_ids)``."""
    shown = [a for _, _, a, f in rows if f is field]
    base_flags = int(_inherited(field, "/Ff", 0)) & (REQUIRED | READ_ONLY)
    if wanted == "radio":
        flags = base_flags | RADIO | NO_TOGGLE_OFF
        cur_v = _inherited(field, "/V")
        checked = (cur_v is not None and str(cur_v) != "/Off") or annot.get("/AS", "/Off") != "/Off"
        old_state = next((str(k)[1:] for k in (annot.get("/AP", {}).get("/N") or {}) if k != "/Off"), "Yes")
        used = set()
        state = _state_name(change.get("value") or old_state, used)
        val_obj = NameObject("/" + state) if checked else NameObject("/Off")
        if field is annot:
            annot_ref = _ref_of(annot, writer.pages)
            parent = DictionaryObject({
                NameObject("/FT"): NameObject("/Btn"),
                NameObject("/Ff"): NumberObject(flags),
                NameObject("/T"): TextStringObject(str(field.get("/T", ""))),
                NameObject("/V"): val_obj,
                NameObject("/Kids"): ArrayObject([annot_ref]),
            })
            if "/TU" in field:
                parent[NameObject("/TU")] = TextStringObject(str(field["/TU"]))
            if "/Parent" in field:
                parent[NameObject("/Parent")] = field["/Parent"]
            parent_ref = writer._add_object(parent)
            holder = field["/Parent"].get_object()["/Kids"] if "/Parent" in field else field_list
            for idx, ref in enumerate(holder):
                if ref.get_object() is field:
                    holder[idx] = parent_ref
                    break
            else:
                holder.append(parent_ref)
            for k in ("/T", "/TU", "/FT", "/Ff", "/V", "/DV", "/DA"):
                if k in annot:
                    del annot[k]
            annot[NameObject("/Parent")] = parent_ref
            old_first = first_names.pop(_key(field), _qualified_name(parent))
            fields.pop(_key(field), None)
            fields[_key(parent)] = parent
            first_names[_key(parent)] = old_first
            field = parent
        else:
            field[NameObject("/FT")] = NameObject("/Btn")
            field[NameObject("/Ff")] = NumberObject(flags)
            field[NameObject("/V")] = val_obj
        for i, w in enumerate(shown):
            w_state = state if i == 0 else _state_name(
                next((str(k)[1:] for k in (w.get("/AP", {}).get("/N") or {}) if k != "/Off"), "Choice"), used)
            w_on = checked if len(shown) == 1 else (w.get("/AS", "/Off") != "/Off")
            w[NameObject("/AS")] = NameObject("/" + w_state) if w_on else NameObject("/Off")
            mk = _child(w, "/MK", DictionaryObject)
            mk[NameObject("/CA")] = TextStringObject(DOT)
            w[NameObject("/AP")] = DictionaryObject({NameObject("/N"): DictionaryObject({
                NameObject("/" + w_state): DictionaryObject(), NameObject("/Off"): DictionaryObject()})})
            x0, y0, x1, y1 = (float(v) for v in w["/Rect"])
            _remake(writer, acroform, w, field, "radio", abs(x1 - x0), abs(y1 - y0))
        return field, {id(w) for w in shown}

    full_name = _qualified_name(field)
    short_name = str(field.get("/T", ""))
    prefix = full_name[:-len(short_name)] if short_name else ""
    tooltip = str(field["/TU"]) if "/TU" in field else None
    group_val = _inherited(field, "/V")
    group_parent_ref = field.get("/Parent") if field is not annot else None
    holder = group_parent_ref.get_object()["/Kids"] if group_parent_ref is not None else field_list
    taken.discard(full_name)
    old_first = first_names.pop(_key(field), full_name)
    fields.pop(_key(field), None)
    _fonts(writer, acroform)
    for w in shown:
        state = next((str(k)[1:] for k in (w.get("/AP", {}).get("/N") or {}) if k != "/Off"), "Yes")
        is_on = (w.get("/AS") not in (None, "/Off")) or (group_val is not None and str(group_val) == "/" + state)
        if len(shown) == 1:
            asked = short_name or state
        elif w is annot and change.get("name"):
            asked = " ".join(str(change["name"]).replace(".", " ").split()) or state
        else:
            asked = " ".join(state.replace(".", " ").split()) or "Checkbox"
        candidate, n = asked, 2
        while prefix + candidate in taken:
            candidate, n = f"{asked} {n}", n + 1
        taken.add(prefix + candidate)
        if group_parent_ref is not None:
            w[NameObject("/Parent")] = group_parent_ref
        elif "/Parent" in w:
            del w["/Parent"]
        for k in ("/Kids", "/Opt", "/I", "/TI"):
            if k in w:
                del w[k]
        w[NameObject("/FT")] = NameObject("/Btn")
        w[NameObject("/Ff")] = NumberObject(base_flags)
        w[NameObject("/T")] = TextStringObject(candidate)
        w[NameObject("/TU")] = TextStringObject(
            candidate if tooltip in (None, short_name, full_name) else tooltip)
        w[NameObject("/DA")] = TextStringObject("/ZaDb 0 Tf 0 g")
        val_obj = NameObject("/Yes") if is_on else NameObject("/Off")
        w[NameObject("/V")] = val_obj
        w[NameObject("/AS")] = val_obj
        mk = _child(w, "/MK", DictionaryObject)
        mk[NameObject("/CA")] = TextStringObject(TICK)
        w[NameObject("/AP")] = DictionaryObject({NameObject("/N"): DictionaryObject({
            NameObject("/Yes"): DictionaryObject(), NameObject("/Off"): DictionaryObject()})})
        x0, y0, x1, y1 = (float(v) for v in w["/Rect"])
        _remake(writer, acroform, w, w, "checkbox", abs(x1 - x0), abs(y1 - y0))
        w_ref = _ref_of(w, writer.pages)
        if w is not field and w_ref is not None:
            holder.append(w_ref)
        fields[_key(w)] = w
        first_names[_key(w)] = old_first if len(shown) == 1 else prefix + candidate
    if field is not annot:
        _drop(holder, field)
    return annot, {id(w) for w in shown}


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
    "dropdown", "list", "signature", "checkbox" or "radio"), "options" (a
    dropdown's or list's choices), "format" (with "decimals", "separators",
    "currency", "date"), "min", "max", "calculate" and "delete".

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
    notes, redraw, calc_changed = [], False, False
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

    deleted, changed, retyped_buttons = [], 0, set()
    for number, annot, field, change in targets:
        field = _field_of(annot)
        if change.get("delete"):
            deleted.append((number, annot, field))
            continue
        kind, touched = _kind(field), id(annot) in retyped_buttons

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

        wanted = change.get("type")
        if wanted is not None and str(wanted).lower() != kind and kind in ("checkbox", "radio"):
            wanted_btn = str(wanted).lower()
            if wanted_btn not in ("checkbox", "radio"):
                raise core.PdfManagerError(
                    f"a {kind} field can't be turned into a {wanted_btn} field: delete it and draw "
                    "a new one")
            field, converted = _retype_button(
                writer, acroform, field_list, rows, annot, field, kind, wanted_btn, change,
                taken, fields, first_names)
            retyped_buttons.update(converted)
            rows = list(_widgets(writer.pages))
            kind, wanted, touched = wanted_btn, None, True

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
                for other in fields.values():  # the sums or formulas that used it
                    calc = _calc_of(other)
                    if calc and current in calc[1]:
                        if calc[0].startswith("="):
                            formula, updated, _ = _parse_formula(calc[0][1:], rename={current: prefix + candidate})
                            _set_calc(other, "=" + formula, updated)
                        else:
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

        if wanted is not None and str(wanted).lower() != kind:
            wanted = str(wanted).lower()
            typed = ("text", "multiline", "dropdown", "list", "signature")
            if kind not in typed or wanted not in typed:
                raise core.PdfManagerError(
                    f"a {kind} field can't be turned into a {wanted} field: delete it and draw "
                    "a new one")
            if ({kind, wanted} & {"dropdown", "list", "signature"}):
                # Between a box to type in, a list to choose from and an unsigned signature.
                shown = [a for _, _, a, f in rows if f is field]
                said = _retype(writer, acroform, field, shown, kind, wanted, change, number)
                if said:
                    notes.append(said)
                flags = int(_inherited(field, "/Ff", 0))
                for key, bit in (("required", REQUIRED), ("readonly", READ_ONLY)):
                    if change.get(key) is not None:
                        flags = flags | bit if change[key] else flags & ~bit
                before, redraw, touched = int(_inherited(field, "/Ff", 0)), wanted != "signature", True
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

        fmt_keys = ("format", "decimals", "separators", "currency", "currency_after", "date")
        if any(change.get(k) is not None for k in fmt_keys) and kind in ("text", "multiline"):
            if "format" in change and not change["format"]:
                if "/AA" in field:
                    aa = field["/AA"].get_object()
                    for k in ("/F", "/K"):
                        if k in aa:
                            del aa[k]
                    if not len(aa):
                        del field["/AA"]
                touched = True
            else:
                cur_fmt = _format_of(field)
                if cur_fmt.get("format") in ("", "custom"):
                    cur_fmt = {}
                elif change.get("format") and str(change["format"]).lower() != cur_fmt.get("format"):
                    cur_fmt = {k: v for k, v in cur_fmt.items() if k == "separators"}
                merged = {**cur_fmt, **{k: change[k] for k in fmt_keys if change.get(k) is not None}}
                if not merged.get("format"):
                    merged["format"] = ("currency" if merged.get("currency") else "date" if merged.get("date")
                                        else "amount" if merged.get("decimals") == 2 else "number")
                scripts = _format_scripts(merged)
                if scripts:
                    aa = _child(field, "/AA", DictionaryObject)
                    aa[NameObject("/F")], aa[NameObject("/K")] = _js(scripts[0]), _js(scripts[1])
                    touched = True

        if ("min" in change or "max" in change) and kind in ("text", "multiline"):
            cur_range = _range_of(field)
            spec = {"min": change["min"] if "min" in change else cur_range.get("min"),
                    "max": change["max"] if "max" in change else cur_range.get("max")}
            check = _validation(spec)
            if check:
                aa = _child(field, "/AA", DictionaryObject)
                aa[NameObject("/V")] = _js(check)
                touched = True
            elif "/AA" in field and "/V" in field["/AA"].get_object():
                del field["/AA"].get_object()["/V"]
                if not len(field["/AA"].get_object()):
                    del field["/AA"]
                touched = True

        if "calculate" in change and kind in ("text", "multiline"):
            if not change["calculate"]:
                if "/AA" in field and "/C" in field["/AA"].get_object():
                    del field["/AA"].get_object()["/C"]
                    if not len(field["/AA"].get_object()):
                        del field["/AA"]
                if change.get("readonly") is None:
                    flags &= ~READ_ONLY
                    field[NameObject("/Ff")] = NumberObject(flags)
                calc_changed = touched = True
            else:
                op, sources = _calculation({**change, "name": _qualified_name(field)})
                _set_calc(field, op, sources)
                if change.get("readonly") is None:
                    flags |= READ_ONLY
                    field[NameObject("/Ff")] = NumberObject(flags)
                calc_changed = touched = True
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
            left = [] if calc[0].startswith("=") else [n for n in calc[1] if n != name]
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

    names = {}
    for key, field in fields.items():
        if key in first_names and _qualified_name(field) != first_names[key]:
            names[first_names[key]] = _qualified_name(field)
    if calc_changed and names:
        for other in fields.values():
            calc = _calc_of(other)
            if calc and any(n in names for n in calc[1]):
                if calc[0].startswith("="):
                    formula, updated, _ = _parse_formula(calc[0][1:], rename=names)
                    _set_calc(other, "=" + formula, updated)
                else:
                    _set_calc(other, calc[0], [names.get(n, n) for n in calc[1]])
    if calc_changed:
        acroform[NameObject("/CO")] = ArrayObject()
        calc_fields = [(_qualified_name(f), (_calc_of(f) or ("", []))[1], f)
                       for f in fields.values() if _action_js(f, "/C") is not None]
        if calc_fields:
            _calculation_order(acroform, calc_fields, writer)
    if redraw:
        acroform[NameObject("/NeedAppearances")] = BooleanObject(True)
    core._write(writer, output)
    return {"changed": changed, "deleted": len(deleted), "names": names, "notes": notes}
