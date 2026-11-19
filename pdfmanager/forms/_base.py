# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Low-level AcroForm constants and PDF field/widget inspection helpers."""

import re

from pypdf.generic import (ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NumberObject, TextStringObject)

from pdfmanager import core

TYPES = {"text": "Text", "multiline": "Text", "checkbox": "Checkbox", "radio": "Group",
         "dropdown": "Dropdown", "list": "List", "signature": "Signature", "button": "Button"}


MULTILINE, REQUIRED = 1 << 12, 1 << 1     # text field flags


PASSWORD = 1 << 13                        # text field: mask characters


DO_NOT_SCROLL = 1 << 23                   # text field: no scrolling


COMB = 1 << 24                            # text field: one box per character


NO_TOGGLE_OFF, RADIO, PUSHBUTTON = 1 << 14, 1 << 15, 1 << 16  # button field flags


COMBO, EDIT, MULTI_SELECT = 1 << 17, 1 << 18, 1 << 21         # choice field flags


READ_ONLY = 1


# Formats for text fields, as Acrobat's form scripts (pdf.js runs them too).
# The number styles: separators "1,234.56" (0) or "1.234,56" (2).
FORMATS = ("number", "amount", "currency", "percent", "date", "time", "phone", "zip", "mask")


CALCULATIONS = {"sum": "SUM", "average": "AVG", "product": "PRD", "min": "MIN", "max": "MAX"}


TICK_STYLES = {"check": "4", "cross": "8", "dot": "l", "square": "n"}


TICK_NAMES = {v: k for k, v in TICK_STYLES.items()}


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


def _choices_detail(field):
    out = []
    for option in _inherited(field, "/Opt") or []:
        option = option.get_object()
        if isinstance(option, (list, tuple)) and option:
            val = str(option[0].get_object())
            lbl = str(option[-1].get_object())
            out.append({"label": lbl, "value": val})
        else:
            s = str(option)
            out.append({"label": s, "value": s})
    return out


def _hex_of_colour(colour, default=""):
    try:
        parts = [float(v) for v in colour or []]
    except (TypeError, ValueError):
        return default
    if len(parts) == 1:
        g = max(0, min(255, round(parts[0] * 255)))
        return f"#{g:02x}{g:02x}{g:02x}"
    if len(parts) == 3:
        r, g, b = (max(0, min(255, round(v * 255))) for v in parts)
        return f"#{r:02x}{g:02x}{b:02x}"
    if len(parts) == 4:
        c, m, y, k = parts
        r = max(0, min(255, round(255 * (1 - c) * (1 - k))))
        g = max(0, min(255, round(255 * (1 - m) * (1 - k))))
        b = max(0, min(255, round(255 * (1 - y) * (1 - k))))
        return f"#{r:02x}{g:02x}{b:02x}"
    return default


def _rgb_of_hex(color):
    s = str(color or "").strip()
    if not s:
        return None
    if s.startswith("#"):
        s = s[1:]
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise core.PdfManagerError(f"invalid colour {color!r}; use #rrggbb")
    try:
        return [int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except ValueError:
        raise core.PdfManagerError(f"invalid colour {color!r}; use #rrggbb") from None


_STANDARD_REV = {
    "Helv": "Helvetica", "Helvetica": "Helvetica",
    "HeBo": "Helvetica-Bold", "Helvetica-Bold": "Helvetica-Bold",
    "HeOb": "Helvetica-Oblique", "Helvetica-Oblique": "Helvetica-Oblique",
    "TiRo": "Times", "Times-Roman": "Times", "Times": "Times",
    "TiBo": "Times-Bold", "Times-Bold": "Times-Bold",
    "TiIt": "Times-Italic", "Times-Italic": "Times-Italic",
    "Cour": "Courier", "Courier": "Courier",
    "CoBo": "Courier-Bold", "Courier-Bold": "Courier-Bold",
    "CoOb": "Courier-Oblique", "Courier-Oblique": "Courier-Oblique",
    "ZaDb": "ZapfDingbats", "ZapfDingbats": "ZapfDingbats",
}


_STANDARD_MAP = {
    "helvetica": ("/Helv", "/Helvetica"),
    "helvetica-bold": ("/HeBo", "/Helvetica-Bold"),
    "helvetica-oblique": ("/HeOb", "/Helvetica-Oblique"),
    "times": ("/TiRo", "/Times-Roman"),
    "times-roman": ("/TiRo", "/Times-Roman"),
    "times-bold": ("/TiBo", "/Times-Bold"),
    "times-italic": ("/TiIt", "/Times-Italic"),
    "courier": ("/Cour", "/Courier"),
    "courier-bold": ("/CoBo", "/Courier-Bold"),
    "courier-oblique": ("/CoOb", "/Courier-Oblique"),
}


def _da_info(annot, field, acroform):
    da = str(_inherited(annot, "/DA") or _inherited(field, "/DA")
             or (acroform.get("/DA") if acroform else "") or "/Helv 0 Tf 0 g")
    font_name, font_size, text_color = "Helvetica", 0, "#000000"
    tf = re.search(r"/([^\s/]+)\s+([0-9.]+)\s+Tf", da)
    if tf:
        alias = tf.group(1)
        try:
            sz = float(tf.group(2))
            font_size = int(sz) if sz.is_integer() else sz
        except ValueError:
            font_size = 0
        if alias in _STANDARD_REV:
            font_name = _STANDARD_REV[alias]
        else:
            dr_fonts = ((acroform.get("/DR") or {}).get("/Font") or {}) if acroform else {}
            fobj = dr_fonts.get("/" + alias)
            fobj = fobj.get_object() if fobj is not None else None
            base = str(fobj.get("/BaseFont", ""))[1:] if isinstance(fobj, DictionaryObject) and "/BaseFont" in fobj else alias
            if "+" in base:
                base = base.split("+", 1)[1]
            font_name = _STANDARD_REV.get(base, base or "Helvetica")
    rg = re.search(r"([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+rg\b", da)
    g = re.search(r"([0-9.]+)\s+g\b", da)
    k = re.search(r"([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+k\b", da)
    if rg:
        text_color = _hex_of_colour([rg.group(1), rg.group(2), rg.group(3)], "#000000")
    elif k:
        text_color = _hex_of_colour([k.group(1), k.group(2), k.group(3), k.group(4)], "#000000")
    elif g:
        text_color = _hex_of_colour([g.group(1)], "#000000")
    return font_name, font_size, text_color


def _visibility_of(annot):
    f = int(annot.get("/F", 4) or 0)
    if f & 2:
        return "hidden"
    if f & 32:
        return "printonly"
    if not (f & 4):
        return "noprint"
    return "visible"


def _export_value_of(annot):
    try:
        states = [str(k)[1:] for k in annot["/AP"]["/N"].keys() if k != "/Off"]
        if states:
            return states[0]
    except (KeyError, AttributeError, TypeError):
        pass
    return "Yes"


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


def _clone_pdf_obj(obj):
    if hasattr(obj, "get_object"):
        obj = obj.get_object()
    if isinstance(obj, DecodedStreamObject):
        s = DecodedStreamObject()
        for k, v in obj.items():
            s[NameObject(k)] = _clone_pdf_obj(v)
        s.set_data(obj.get_data())
        return s
    if isinstance(obj, DictionaryObject):
        d = DictionaryObject()
        for k, v in obj.items():
            if k in ("/Parent", "/P", "/Kids"):
                continue
            d[NameObject(k)] = _clone_pdf_obj(v)
        return d
    if isinstance(obj, ArrayObject):
        return ArrayObject(_clone_pdf_obj(v) for v in obj)
    return obj

