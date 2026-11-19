# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Form field JavaScript scripts: formatting, calculation, validation and calc order."""

import json
import re

from pypdf.generic import (ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NumberObject, TextStringObject)

from pdfmanager import core
from ._base import CALCULATIONS, FORMATS, _child, _inherited

CALC_NAMES = {v: k for k, v in CALCULATIONS.items()}


_JS_STR = r'(?:"(?:[^"\\\n\r]|\\.)*"|\'(?:[^\'\\\n\r]|\\.)*\')'


_SIMPLE_CALC = re.compile(
    rf"\s*\ufeff*AFSimple_Calculate\(\s*({_JS_STR})\s*,\s*new\s+Array\s*\((.*?)\)\s*\)\s*;?\s*", re.S)


_NUMBER_FMT = re.compile(
    rf"\s*\ufeff*AFNumber_Format\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*\d+\s*,\s*({_JS_STR})\s*,\s*(true|false)\s*\)\s*;?\s*", re.S)


_PERCENT_FMT = re.compile(rf"\s*\ufeff*AFPercent_Format\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*;?\s*", re.S)


_DATE_FMT = re.compile(rf"\s*\ufeff*AFDate_Format(?:Ex)?\(\s*({_JS_STR})\s*\)\s*;?\s*", re.S)


_TIME_FMT = re.compile(rf"\s*\ufeff*AFTime_FormatEx\(\s*({_JS_STR})\s*\)\s*;?\s*", re.S)


_SPECIAL_FMT = re.compile(r"\s*\ufeff*AFSpecial_Format\(\s*(\d+)\s*\)\s*;?\s*", re.S)


_MASK_FMT = re.compile(rf"\s*\ufeff*/\*mask:({_JS_STR})\*/\s*AFSpecial_FormatEx\(\s*\1\s*\)\s*;?\s*", re.S)


_RANGE_VAL = re.compile(
    r"\s*\ufeff*AFRange_Validate\(\s*(true|false)\s*,\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*,"
    r"\s*(true|false)\s*,\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*\)\s*;?\s*", re.S)


_PATTERN_VAL = re.compile(
    rf"\s*\ufeff*/\*pattern:({_JS_STR})\*/\s*if\s*\(\s*event\.value\s*\)\s*\{{\s*event\.rc\s*=\s*new\s+RegExp\(", re.S)


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
            decimals, sep_code, neg_style = int(m.group(1)), int(m.group(2)), int(m.group(3))
            symbol = _unquote_js(m.group(4))
        except ValueError:
            return {"format": "custom"}
        sep = "1.234,56" if sep_code in (2, 3) else "1,234.56"
        before = m.group(5) == "true"
        if symbol:
            return {"format": "currency", "decimals": decimals, "separators": sep, "neg_style": neg_style,
                    "currency": symbol, "currency_after": not before}
        return {"format": "amount" if decimals == 2 else "number",
                "decimals": decimals, "separators": sep, "neg_style": neg_style}
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
    m = _TIME_FMT.fullmatch(script)
    if m:
        try:
            return {"format": "time", "time": _unquote_js(m.group(1))}
        except ValueError:
            return {"format": "custom"}
    m = _MASK_FMT.fullmatch(script)
    if m:
        try:
            return {"format": "mask", "mask": _unquote_js(m.group(1))}
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
    {"pattern": ...} for regex validation, {"validate": "custom"} for
    another validation script, or {}."""
    script = _action_js(field, "/V")
    if not script:
        return {}
    pm = _PATTERN_VAL.match(script)
    if pm:
        try:
            return {"pattern": _unquote_js(pm.group(1))}
        except ValueError:
            return {"validate": "custom"}
    m = _RANGE_VAL.fullmatch(script)
    if not m:
        return {"validate": "custom"}
    out = {}
    if m.group(1) == "true":
        out["min"] = _clean_number(m.group(2))
    if m.group(3) == "true":
        out["max"] = _clean_number(m.group(4))
    return out


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
        neg = max(0, min(3, int(field.get("neg_style", 0))))
        symbol = str(field.get("currency") or ("€" if kind == "currency" else "")) if kind == "currency" else ""
        before = "true" if not field.get("currency_after") else "false"
        args = f"{decimals}, {sep}, {neg}, 0, {_js_string(symbol)}, {before}"
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
    if kind == "time":
        pattern = str(field.get("time") or "HH:MM")
        if not re.fullmatch(r"[HhMmsStT: .]+", pattern):
            raise core.PdfManagerError(f"unknown time pattern {pattern!r}; e.g. HH:MM")
        return (f"AFTime_FormatEx({_js_string(pattern)});",
                f"AFTime_KeystrokeEx({_js_string(pattern)});")
    if kind == "mask":
        mask = str(field.get("mask") or "")
        if not mask:
            raise core.PdfManagerError("a custom mask needs a pattern (e.g. 999-99-9999)")
        q = _js_string(mask)
        return (f"/*mask:{q}*/ AFSpecial_FormatEx({q});",
                f"/*mask:{q}*/ AFSpecial_KeystrokeEx({q});")
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
    pattern = field.get("pattern")
    if pattern not in (None, ""):
        pat = str(pattern)
        try:
            re.compile(pat)
        except re.error:
            raise core.PdfManagerError(f"invalid validation pattern {pat!r}") from None
        q = _js_string(pat)
        anchored = _js_string(f"^(?:{pat})$")
        return f"/*pattern:{q}*/ if (event.value) {{ event.rc = new RegExp({anchored}).test(event.value); }}"
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

