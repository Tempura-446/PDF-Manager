# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Inspecting, adding, editing, copying, tabulating and reordering form fields."""

import re

from pypdf.generic import (ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NumberObject, TextStringObject)

from pdfmanager import core
from ._base import (
    BORDER, COMB, COMBO, DO_NOT_SCROLL, DOT, EDIT, FORMATS, MIN_SIZE, MULTILINE,
    MULTI_SELECT, NO_TOGGLE_OFF, PASSWORD, PUSHBUTTON, RADIO, READ_ONLY, REQUIRED,
    TICK, TICK_NAMES, TICK_STYLES, TYPES, _STANDARD_MAP,
    _child, _choices_detail, _choices_of, _clone_pdf_obj, _da_info, _drop,
    _export_value_of, _field_of, _hex_of_colour, _inherited, _key, _kind, _num,
    _qualified_name, _rect_of, _ref_of, _rgb_of_hex, _unique, _value_of,
    _visibility_of, _widgets, existing_fields,
)
from .actions import _action_of, _set_button_action, _set_show_hide, _show_hide_of
from .scripts import (
    _action_js, _calc_detail, _calc_of, _calc_script, _calculation,
    _calculation_order, _format_of, _format_scripts, _js, _parse_formula,
    _range_of, _set_calc, _validation,
)

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
    acroform = reader.trailer["/Root"].get("/AcroForm")
    acroform = acroform.get_object() if acroform is not None else {}
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
    root = reader.trailer["/Root"]
    mark = root.get("/MarkInfo")
    mark = mark.get_object() if mark is not None else {}
    tagged = bool(root.get("/StructTreeRoot") and mark.get("/Marked"))
    page_tabs = {}
    for pno, pg in enumerate(reader.pages, 1):
        t = str(pg.get("/Tabs") or "")
        if t == "/C":
            page_tabs[pno] = "cols"
        elif t == "/A":
            page_tabs[pno] = "custom"
        elif t == "/S" or (not t and tagged):
            page_tabs[pno] = "structure"
        else:
            page_tabs[pno] = "rows"
    found = {}
    for number, index, annot, field in rows:
        kind, name = _kind(field), _qualified_name(field)
        flags = int(_inherited(field, "/Ff", 0))
        tooltip = _inherited(field, "/TU")
        fmt = _format_of(field) if kind in ("text", "multiline") else {}
        bounds = _range_of(field) if kind in ("text", "multiline") else {}
        calc_info = _calc_detail(field)
        font_name, font_size, text_color = _da_info(annot, field, acroform)
        mk = annot.get("/MK") or {}
        fill_color = _hex_of_colour(mk.get("/BG"), "")
        border_color = _hex_of_colour(mk.get("/BC"), "")
        try:
            border_width = float((annot.get("/BS") or {}).get("/W", 1 if border_color else 0))
        except (TypeError, ValueError):
            border_width = 1 if border_color else 0
        if border_width.is_integer() if isinstance(border_width, float) else False:
            border_width = int(border_width)
        q_val = int(_inherited(annot, "/Q", _inherited(field, "/Q", 0)) or 0)
        align = {0: "left", 1: "center", 2: "right"}.get(q_val, "left")
        dv = _inherited(field, "/DV")
        dv_obj = dv.get_object() if dv is not None else None
        if isinstance(dv_obj, NameObject):
            dv_str = "" if dv_obj == "/Off" else str(dv_obj)[1:]
        elif isinstance(dv_obj, (list, tuple)):
            dv_str = [str(v) for v in dv_obj]
        elif dv_obj is not None:
            dv_str = str(dv_obj)
        else:
            dv_str = ""
        info = {
            "index": index, "rect": _rect_of(annot), "name": name, "type": kind,
            "tab_order": page_tabs.get(number, "rows"),
            "tooltip": str(tooltip) if tooltip is not None else "",
            "required": bool(flags & REQUIRED), "readonly": bool(flags & READ_ONLY),
            "visibility": _visibility_of(annot),
            "font": font_name, "font_size": font_size, "text_color": text_color,
            "fill_color": fill_color, "border_color": border_color, "border_width": border_width,
            "align": align,
            "value": _value_of(field, kind), "options": _choices_of(field),
            "widgets": shown[_key(field)],
            "format": fmt.get("format", ""),
            "calculated": calc_info is not None,
            "calculate": calc_info,
            "used_by": users.get(name, []),
            "signed": kind == "signature" and _inherited(field, "/V") is not None}
        if kind in ("text", "multiline"):
            ml = _inherited(field, "/MaxLen")
            info["default_value"] = dv_str if isinstance(dv_str, str) else ""
            info["max_length"] = int(ml) if ml is not None else 0
            info["comb"] = bool(flags & COMB)
            info["password"] = bool(flags & PASSWORD)
            info["scroll"] = not bool(flags & DO_NOT_SCROLL)
        elif kind in ("dropdown", "list"):
            info["choices"] = _choices_detail(field)
            info["default_choice"] = dv_str
            info["allow_custom"] = bool(flags & EDIT)
            info["multiselect"] = bool(flags & MULTI_SELECT)
        elif kind in ("checkbox", "radio"):
            ca = str(mk.get("/CA", DOT if kind == "radio" else TICK))[:1]
            exp = _export_value_of(annot)
            info["tick_style"] = TICK_NAMES.get(ca, "dot" if kind == "radio" else "check")
            info["export_value"] = exp
            info["default_checked"] = (dv_str == exp) if dv is not None else (
                str(annot.get("/AS", "/Off")) == "/" + exp)
            if kind == "radio":
                info["group"] = name
            elif kind == "checkbox":
                sh = _show_hide_of(annot)
                if sh:
                    info["show_hide"] = sh
        elif kind == "button":
            info["label"] = str(mk.get("/CA") or name)
            act = _action_of(annot, reader.pages)
            if act:
                info["action"] = act
        for k in ("decimals", "separators", "neg_style", "currency", "currency_after", "date", "time", "mask"):
            if k in fmt:
                info[k] = fmt[k]
        for k in ("min", "max", "pattern", "validate"):
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
            elif kind in ("dropdown", "list"):
                if kind == "dropdown":
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
                if field.get("show_hide"):
                    _set_show_hide(widget, field["show_hide"])
            elif kind == "button":
                flags |= PUSHBUTTON
                label = str(field.get("label") or name)
                mk[NameObject("/CA")] = TextStringObject(label)
                mk[NameObject("/BG")] = ArrayObject(_num(c) for c in (0.93, 0.93, 0.93))
                size = min(font_size or 10, max(6.0, height * 0.55))
                cap = label.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                tx = max(4.0, (width - len(label) * size * 0.48) / 2)
                ty = max(3.0, (height - size * 0.72) / 2)
                bg = f"q 0.93 g 0 0 {width:.2f} {height:.2f} re f Q\n"
                txt = f"q BT 0 g /Helv {size:.2f} Tf {tx:.2f} {ty:.2f} Td ({cap}) Tj ET Q\n"
                btn_res = DictionaryObject({NameObject("/Font"): DictionaryObject(
                    {NameObject("/Helv"): fonts["/Helv"]})})
                widget.update({
                    NameObject("/FT"): NameObject("/Btn"),
                    NameObject("/H"): NameObject("/P"),
                    NameObject("/DA"): TextStringObject(f"/Helv {size:g} Tf 0 g"),
                    NameObject("/AP"): DictionaryObject({NameObject("/N"): _stream(
                        writer, width, height, bg + frame + txt, rotation, btn_res)}),
                })
                if field.get("action"):
                    _set_button_action(writer, widget, field["action"])
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
    if kind == "button":
        mk = annot.get("/MK") or {}
        label = str(mk.get("/CA") or _qualified_name(field) or "")
        fonts = _fonts(writer, acroform)
        da = str(_inherited(field, "/DA", "") or annot.get("/DA") or "")
        size_m = re.search(r"([0-9.]+)\s+Tf", da)
        size = float(size_m.group(1)) if size_m else 0.0
        if size <= 0:
            size = min(12.0, max(6.0, height * 0.55))
        tc = _da_info(annot, field, acroform)[2] or "#000000"
        r, g, b = _rgb_of_hex(tc) or (0.0, 0.0, 0.0)
        cap = label.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        tx = max(4.0, (width - len(label) * size * 0.48) / 2)
        ty = max(3.0, (height - size * 0.72) / 2)
        txt = f"q BT {r:.3f} {g:.3f} {b:.3f} rg /Helv {size:.2f} Tf {tx:.2f} {ty:.2f} Td ({cap}) Tj ET Q\n"
        btn_res = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): fonts["/Helv"]})})
        annot[NameObject("/AP")] = DictionaryObject({NameObject("/N"): look(frame + txt, btn_res)})
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


def _ensure_font_alias(writer, acroform, font_name):
    """Ensure ``font_name`` has a /Font entry in /AcroForm /DR /Font and
    return its resource name (e.g. '/Helv' or '/TiRo')."""
    fonts = _fonts(writer, acroform)
    key = str(font_name or "Helvetica").strip().lower()
    if key in _STANDARD_MAP:
        alias, base = _STANDARD_MAP[key]
        if alias not in fonts:
            fobj = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject(base),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            })
            fonts[NameObject(alias)] = writer._add_object(fobj)
        return alias
    # Reuse a matching document font if already present in /DR /Font or any page.
    for existing_alias, ref in list(fonts.items()):
        obj = ref.get_object()
        base = str(obj.get("/BaseFont", ""))[1:].split("+", 1)[-1].lower()
        if base == key or base.replace("-", " ") == key.replace("-", " "):
            return str(existing_alias)
    for page in writer.pages[:50]:
        res = page.get("/Resources")
        pfonts = (res.get_object().get("/Font") if res is not None else None) or {}
        if hasattr(pfonts, "get_object"):
            pfonts = pfonts.get_object()
        for _, ref in (pfonts.items() if isinstance(pfonts, DictionaryObject) else ()):
            obj = ref.get_object()
            base = str(obj.get("/BaseFont", ""))[1:].split("+", 1)[-1].lower()
            if base == key or base.replace("-", " ") == key.replace("-", " "):
                alias = "/F" + re.sub(r"[^A-Za-z0-9]", "", font_name)[:16]
                if alias not in fonts:
                    fonts[NameObject(alias)] = ref
                return alias
    # Fall back to Helvetica if an unknown/unembedded font is requested.
    return "/Helv"


def _set_visibility(annot, visibility):
    vis = str(visibility or "visible").lower()
    f = int(annot.get("/F", 4) or 0)
    f &= ~(2 | 4 | 32)
    if vis == "hidden":
        f |= 2
    elif vis == "noprint":
        pass
    elif vis == "printonly":
        f |= 4 | 32
    else:
        f |= 4
    annot[NameObject("/F")] = NumberObject(f)


def _options_array(items):
    out = ArrayObject()
    for item in items:
        if isinstance(item, dict):
            lbl = str(item.get("label") or item.get("value") or "")
            val = str(item.get("value") if item.get("value") is not None else lbl)
            if val == lbl:
                out.append(TextStringObject(lbl))
            else:
                out.append(ArrayObject([TextStringObject(val), TextStringObject(lbl)]))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            val, lbl = str(item[0]), str(item[1])
            if val == lbl:
                out.append(TextStringObject(lbl))
            else:
                out.append(ArrayObject([TextStringObject(val), TextStringObject(lbl)]))
        else:
            out.append(TextStringObject(str(item)))
    return out


def _rename_export_state(annot, field, new_state):
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(new_state or "Yes").strip()) or "Yes"
    old_state = _export_value_of(annot)
    if clean == old_state:
        return clean
    try:
        normal = annot["/AP"]["/N"].get_object()
        if "/" + old_state in normal:
            normal[NameObject("/" + clean)] = normal.pop("/" + old_state)
    except (KeyError, AttributeError, TypeError):
        pass
    if annot.get("/AS") == "/" + old_state:
        annot[NameObject("/AS")] = NameObject("/" + clean)
    if _inherited(field, "/V") == "/" + old_state:
        field[NameObject("/V")] = NameObject("/" + clean)
    if _inherited(field, "/DV") == "/" + old_state:
        field[NameObject("/DV")] = NameObject("/" + clean)
    return clean


def _move_radio_group(writer, field_list, rows, annot, old_field, group_name, taken, fields, first_names):
    """Move a radio widget ``annot`` to a radio group named ``group_name``."""
    target_name = " ".join(str(group_name or "").replace(".", " ").split())
    if not target_name or target_name == _qualified_name(old_field):
        return old_field
    annot_ref = _ref_of(annot, writer.pages)
    target_field = next((f for f in fields.values()
                         if _kind(f) == "radio" and _qualified_name(f) == target_name), None)
    if old_field is not annot and "/Kids" in old_field:
        _drop(old_field["/Kids"], annot)
        if not len(old_field["/Kids"]):
            old_parent = old_field["/Parent"].get_object() if "/Parent" in old_field else None
            _drop(old_parent["/Kids"] if old_parent and "/Kids" in old_parent else field_list, old_field)
            old_q = _qualified_name(old_field)
            taken.discard(old_q)
            fields.pop(_key(old_field), None)
            first_names.pop(_key(old_field), None)
    if target_field is None:
        parent = DictionaryObject({
            NameObject("/FT"): NameObject("/Btn"),
            NameObject("/Ff"): NumberObject(int(_inherited(old_field, "/Ff", RADIO | NO_TOGGLE_OFF))),
            NameObject("/T"): TextStringObject(target_name),
            NameObject("/TU"): TextStringObject(target_name),
            NameObject("/V"): NameObject("/Off"),
            NameObject("/Kids"): ArrayObject([annot_ref] if annot_ref is not None else []),
        })
        parent_ref = writer._add_object(parent)
        field_list.append(parent_ref)
        taken.add(target_name)
        fields[_key(parent)] = parent
        first_names[_key(parent)] = target_name
        target_field = parent
    else:
        parent_ref = _ref_of(target_field, writer.pages) or target_field.indirect_reference
        if annot_ref is not None:
            _child(target_field, "/Kids", ArrayObject).append(annot_ref)
    if parent_ref is not None:
        annot[NameObject("/Parent")] = parent_ref
    return target_field


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

        if change.get("visibility") is not None:
            _set_visibility(annot, change["visibility"])
            touched = True

        if any(change.get(k) is not None for k in ("font", "font_size", "text_color")) and kind != "signature":
            cur_font, cur_size, cur_color = _da_info(annot, field, acroform)
            new_font = change["font"] if change.get("font") is not None else cur_font
            new_size = float(change["font_size"]) if change.get("font_size") is not None else float(cur_size)
            new_color = change["text_color"] if change.get("text_color") is not None else cur_color
            alias = _ensure_font_alias(writer, acroform, new_font)
            rgb = _rgb_of_hex(new_color) or [0, 0, 0]
            color_op = "0 g" if all(c == 0 for c in rgb) else f"{rgb[0]:g} {rgb[1]:g} {rgb[2]:g} rg"
            size_str = f"{int(new_size) if new_size.is_integer() else new_size:g}"
            holder = annot if "/DA" in annot else field
            holder[NameObject("/DA")] = TextStringObject(f"{alias} {size_str} Tf {color_op}")
            redraw = True
            touched = True

        look_remake = False
        if "fill_color" in change and change["fill_color"] is not None:
            mk = _child(annot, "/MK", DictionaryObject)
            rgb = _rgb_of_hex(change["fill_color"])
            if rgb is None:
                if "/BG" in mk:
                    del mk["/BG"]
            else:
                mk[NameObject("/BG")] = ArrayObject(_num(c) for c in rgb)
            look_remake = True
            touched = True
        if "border_color" in change and change["border_color"] is not None:
            mk = _child(annot, "/MK", DictionaryObject)
            rgb = _rgb_of_hex(change["border_color"])
            if rgb is None:
                if "/BC" in mk:
                    del mk["/BC"]
            else:
                mk[NameObject("/BC")] = ArrayObject(_num(c) for c in rgb)
            look_remake = True
            touched = True
        if "border_width" in change and change["border_width"] is not None:
            bw = max(0.0, float(change["border_width"]))
            bs = _child(annot, "/BS", DictionaryObject)
            bs[NameObject("/W")] = _num(bw)
            if "/S" not in bs:
                bs[NameObject("/S")] = NameObject("/S")
            look_remake = True
            touched = True
        if look_remake:
            x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
            redraw = _remake(writer, acroform, annot, field, kind, abs(x1 - x0), abs(y1 - y0)) or redraw

        if change.get("align") is not None and kind in ("text", "multiline", "dropdown", "list"):
            q_map = {"left": 0, "center": 1, "right": 2}
            q_val = q_map.get(str(change["align"]).lower(), 0)
            field[NameObject("/Q")] = NumberObject(q_val)
            if "/Q" in annot and annot is not field:
                del annot["/Q"]
            redraw = True
            touched = True

        if kind in ("text", "multiline"):
            tflags = tbefore = int(_inherited(field, "/Ff", 0))
            if "default_value" in change and change["default_value"] is not None:
                dv_txt = str(change["default_value"])
                if dv_txt:
                    field[NameObject("/DV")] = TextStringObject(dv_txt)
                    if not _inherited(field, "/V"):
                        field[NameObject("/V")] = TextStringObject(dv_txt)
                else:
                    if "/DV" in field:
                        del field["/DV"]
                redraw = True
                touched = True
            if "max_length" in change:
                ml = int(change["max_length"] or 0)
                if ml > 0:
                    field[NameObject("/MaxLen")] = NumberObject(ml)
                elif "/MaxLen" in field:
                    del field["/MaxLen"]
                if ml <= 0:
                    tflags &= ~COMB
                touched = True
            if "password" in change and change["password"] is not None:
                tflags = tflags | PASSWORD if change["password"] else tflags & ~PASSWORD
            if "scroll" in change and change["scroll"] is not None:
                tflags = tflags & ~DO_NOT_SCROLL if change["scroll"] else tflags | DO_NOT_SCROLL
            if "comb" in change and change["comb"] is not None:
                if change["comb"]:
                    tflags = (tflags | COMB) & ~(MULTILINE | PASSWORD)
                    if "/MaxLen" not in field or int(field["/MaxLen"]) <= 0:
                        field[NameObject("/MaxLen")] = NumberObject(10)
                    kind = "text"
                else:
                    tflags &= ~COMB
            if tflags != tbefore:
                field[NameObject("/Ff")] = NumberObject(tflags)
                flags = tflags
                redraw = True
                touched = True

        if kind in ("dropdown", "list"):
            cflags = cbefore = int(_inherited(field, "/Ff", 0))
            if "allow_custom" in change and change["allow_custom"] is not None and kind == "dropdown":
                cflags = cflags | EDIT if change["allow_custom"] else cflags & ~EDIT
            if "multiselect" in change and change["multiselect"] is not None and kind == "list":
                cflags = cflags | MULTI_SELECT if change["multiselect"] else cflags & ~MULTI_SELECT
            if cflags != cbefore:
                field[NameObject("/Ff")] = NumberObject(cflags)
                flags = cflags
                touched = True
            if change.get("choices") is not None or change.get("options") is not None:
                raw_items = change.get("choices") if change.get("choices") is not None else _options(change)
                if not raw_items and not flags & EDIT:
                    raise core.PdfManagerError(
                        f"{_qualified_name(field)} on page {number} has no choices: add them, "
                        "separated by commas")
                opt_arr = _options_array(raw_items)
                field[NameObject("/Opt")] = opt_arr
                valid_vals = {str(o[0].get_object()) if isinstance(o, ArrayObject) else str(o) for o in opt_arr}
                valid_lbls = {str(o[-1].get_object()) if isinstance(o, ArrayObject) else str(o) for o in opt_arr}
                value = _inherited(field, "/V")
                if value is not None and str(value) not in (valid_vals | valid_lbls) and not flags & EDIT:
                    for key in ("/V", "/DV", "/I"):
                        if key in field:
                            del field[key]
                    notes.append(f"{_qualified_name(field)} was set to \"{value}\", which is no "
                                 "longer a choice: it's now empty.")
                    redraw = True
                touched = True
            if "default_choice" in change and change["default_choice"] is not None:
                dc = change["default_choice"]
                if isinstance(dc, (list, tuple)):
                    arr = ArrayObject(TextStringObject(str(v)) for v in dc if str(v) != "")
                    if len(arr):
                        field[NameObject("/DV")] = arr
                        if not _inherited(field, "/V"):
                            field[NameObject("/V")] = arr
                    elif "/DV" in field:
                        del field["/DV"]
                elif str(dc):
                    field[NameObject("/DV")] = TextStringObject(str(dc))
                    if not _inherited(field, "/V"):
                        field[NameObject("/V")] = TextStringObject(str(dc))
                else:
                    if "/DV" in field:
                        del field["/DV"]
                redraw = True
                touched = True

        if kind in ("checkbox", "radio"):
            btn_remake = False
            if change.get("group") is not None and kind == "radio":
                field = _move_radio_group(writer, field_list, rows, annot, field, change["group"],
                                          taken, fields, first_names)
                rows = list(_widgets(writer.pages))
                touched = True
            if change.get("export_value") is not None:
                _rename_export_state(annot, field, change["export_value"])
                btn_remake = True
                touched = True
            if change.get("tick_style") is not None:
                ts = str(change["tick_style"]).lower()
                glyph = TICK_STYLES.get(ts, TICK if kind == "checkbox" else DOT)
                mk = _child(annot, "/MK", DictionaryObject)
                mk[NameObject("/CA")] = TextStringObject(glyph)
                btn_remake = True
                touched = True
            if "default_checked" in change and change["default_checked"] is not None:
                exp = _export_value_of(annot)
                if change["default_checked"]:
                    val_obj = NameObject("/" + exp)
                    field[NameObject("/DV")] = val_obj
                    field[NameObject("/V")] = val_obj
                    annot[NameObject("/AS")] = val_obj
                    if kind == "radio" and "/Kids" in field:
                        for kid_ref in field["/Kids"]:
                            kid = kid_ref.get_object()
                            if kid is not annot:
                                kid[NameObject("/AS")] = NameObject("/Off")
                else:
                    if str(_inherited(field, "/DV", "")) == "/" + exp or kind == "checkbox":
                        field[NameObject("/DV")] = NameObject("/Off")
                    if str(_inherited(field, "/V", "")) == "/" + exp or kind == "checkbox":
                        field[NameObject("/V")] = NameObject("/Off")
                    annot[NameObject("/AS")] = NameObject("/Off")
                touched = True
            if btn_remake:
                x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
                _remake(writer, acroform, annot, field, kind, abs(x1 - x0), abs(y1 - y0))

        fmt_keys = ("format", "decimals", "separators", "neg_style", "currency", "currency_after", "date", "time", "mask")
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
                                        else "time" if merged.get("time") else "mask" if merged.get("mask")
                                        else "amount" if merged.get("decimals") == 2 else "number")
                scripts = _format_scripts(merged)
                if scripts:
                    aa = _child(field, "/AA", DictionaryObject)
                    aa[NameObject("/F")], aa[NameObject("/K")] = _js(scripts[0]), _js(scripts[1])
                    touched = True

        if ("min" in change or "max" in change or "pattern" in change) and kind in ("text", "multiline"):
            cur_range = _range_of(field)
            if "pattern" in change:
                spec = {"pattern": change["pattern"]}
            else:
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
        if kind == "button":
            if "label" in change and change["label"] is not None:
                mk = _child(annot, "/MK", DictionaryObject)
                mk[NameObject("/CA")] = TextStringObject(str(change["label"]))
                x0, y0, x1, y1 = _rect_of(annot)
                _remake(writer, acroform, annot, field, kind, max(1.0, x1 - x0), max(1.0, y1 - y0))
                touched = True
            if "action" in change:
                _set_button_action(writer, annot, change["action"])
                touched = True
        if kind == "checkbox" and "show_hide" in change:
            _set_show_hide(annot, change["show_hide"])
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


def _parse_pages_spec(spec, total):
    if isinstance(spec, (list, tuple)):
        return sorted({int(p) for p in spec if 1 <= int(p) <= total})
    text = str(spec or "").strip().lower()
    if not text or text == "all":
        return list(range(1, total + 1))
    out = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                lo, hi = int(a.strip()), int(b.strip())
            except ValueError:
                raise core.PdfManagerError(f"invalid page range {part!r}; use e.g. 2-5 or 2,4,6") from None
            for p in range(max(1, min(lo, hi)), min(total, max(lo, hi)) + 1):
                out.add(p)
        else:
            try:
                p = int(part)
            except ValueError:
                raise core.PdfManagerError(f"invalid page number {part!r}") from None
            if 1 <= p <= total:
                out.add(p)
    return sorted(out)


def copy_to_pages(path, output, page, index, pages, same_name=True, password=None):
    """Copy a field from ``page`` (1-based) and ``index`` to ``pages`` (e.g.
    '2-5', '1,3,5', or 'all'). When ``same_name`` is True, all copies share
    the same field (one value everywhere); when False, each copy gets a
    numbered name."""
    reader = core.open_pdf(path, password)
    writer = core.clone_document(reader)
    acroform = _child(writer._root_object, "/AcroForm", DictionaryObject)
    field_list = _child(acroform, "/Fields", ArrayObject)
    rows = list(_widgets(writer.pages))
    where = {(n, i): (a, f) for n, i, a, f in rows}
    spot = (int(page), int(index))
    if spot not in where:
        raise core.PdfManagerError(f"page {page} has no field number {index}")
    orig_annot, field = where[spot]
    targets = [p for p in _parse_pages_spec(pages, len(writer.pages)) if p != spot[0]]
    if not targets:
        raise core.PdfManagerError("no other pages in that range to copy to")
    taken = {_qualified_name(f) for _, _, _, f in rows}
    base_name = _qualified_name(field)
    created_names = []

    if same_name:
        if field is orig_annot:
            orig_ref = _ref_of(orig_annot, writer.pages)
            parent = DictionaryObject({NameObject("/Kids"): ArrayObject([orig_ref])})
            for k in ("/FT", "/Ff", "/T", "/TU", "/V", "/DV", "/DA", "/Q", "/Opt", "/AA", "/MaxLen"):
                if k in orig_annot:
                    parent[NameObject(k)] = orig_annot.pop(k)
            parent_ref = writer._add_object(parent)
            orig_annot[NameObject("/Parent")] = parent_ref
            for idx_fl, ref in enumerate(field_list):
                if ref.get_object() is orig_annot:
                    field_list[idx_fl] = parent_ref
                    break
            else:
                field_list.append(parent_ref)
            field = parent
        parent_ref = _ref_of(field, writer.pages) or field.indirect_reference
        if parent_ref is None:
            parent_ref = writer._add_object(field)
            field_list.append(parent_ref)
        for p in targets:
            pg = writer.pages[p - 1]
            clone = _clone_pdf_obj(orig_annot)
            for k in ("/FT", "/Ff", "/T", "/TU", "/V", "/DV", "/Opt", "/AA", "/MaxLen"):
                if k in clone:
                    del clone[k]
            clone[NameObject("/Parent")] = parent_ref
            clone[NameObject("/P")] = pg.indirect_reference
            ref = writer._add_object(clone)
            _child(field, "/Kids", ArrayObject).append(ref)
            if "/Annots" not in pg:
                pg[NameObject("/Annots")] = ArrayObject()
            pg["/Annots"].get_object().append(ref)
            created_names.append(base_name)
    else:
        for p in targets:
            pg = writer.pages[p - 1]
            clone = _clone_pdf_obj(orig_annot)
            for k in ("/FT", "/Ff", "/T", "/TU", "/V", "/DV", "/DA", "/Q", "/Opt", "/AA", "/MaxLen"):
                if k not in clone and k in field:
                    clone[NameObject(k)] = _clone_pdf_obj(field[k])
            new_name = _unique(f"{base_name} {p}", taken, base_name)
            clone[NameObject("/T")] = TextStringObject(new_name)
            if "/TU" in clone:
                clone[NameObject("/TU")] = TextStringObject(new_name)
            clone[NameObject("/P")] = pg.indirect_reference
            ref = writer._add_object(clone)
            field_list.append(ref)
            if "/Annots" not in pg:
                pg[NameObject("/Annots")] = ArrayObject()
            pg["/Annots"].get_object().append(ref)
            created_names.append(new_name)

    core._write(writer, output)
    return created_names


def make_table(path, output, page, index, rows_count, cols_count, row_gap=4, col_gap=4, password=None):
    """Turn a selected field into a ``rows_count`` x ``cols_count`` grid of
    fields with numbered names (``Name 1``, ``Name 2``, ...)."""
    rows_n = max(1, min(50, int(rows_count)))
    cols_n = max(1, min(20, int(cols_count)))
    if rows_n == 1 and cols_n == 1:
        raise core.PdfManagerError("a table needs at least 2 cells")
    reader = core.open_pdf(path, password)
    writer = core.clone_document(reader)
    acroform = _child(writer._root_object, "/AcroForm", DictionaryObject)
    field_list = _child(acroform, "/Fields", ArrayObject)
    rows = list(_widgets(writer.pages))
    where = {(n, i): (a, f) for n, i, a, f in rows}
    spot = (int(page), int(index))
    if spot not in where:
        raise core.PdfManagerError(f"page {page} has no field number {index}")
    orig_annot, field = where[spot]
    pg = writer.pages[spot[0] - 1]
    if "/Annots" not in pg:
        pg[NameObject("/Annots")] = ArrayObject()
    annots = pg["/Annots"].get_object()
    x0, y0, x1, y1 = _rect_of(orig_annot)
    w, h = x1 - x0, y1 - y0
    rg, cg = float(row_gap), float(col_gap)
    taken = {_qualified_name(f) for _, _, _, f in rows}
    base = _qualified_name(field)
    taken.discard(base)
    first_name = _unique(f"{base} 1", taken, "Field")
    field[NameObject("/T")] = TextStringObject(first_name)
    created_names = [first_name]
    cell = 1
    for r in range(rows_n):
        for c in range(cols_n):
            if r == 0 and c == 0:
                continue
            cell += 1
            nx0 = x0 + c * (w + cg)
            ny1 = y1 - r * (h + rg)
            nx1 = nx0 + w
            ny0 = ny1 - h
            clone = _clone_pdf_obj(orig_annot)
            for k in ("/FT", "/Ff", "/T", "/TU", "/V", "/DV", "/DA", "/Q", "/Opt", "/AA", "/MaxLen"):
                if k not in clone and k in field:
                    clone[NameObject(k)] = _clone_pdf_obj(field[k])
            cname = _unique(f"{base} {cell}", taken, "Field")
            clone[NameObject("/T")] = TextStringObject(cname)
            if "/TU" in clone:
                clone[NameObject("/TU")] = TextStringObject(cname)
            clone[NameObject("/Rect")] = ArrayObject(_num(v) for v in (nx0, ny0, nx1, ny1))
            clone[NameObject("/P")] = pg.indirect_reference
            ref = writer._add_object(clone)
            field_list.append(ref)
            annots.append(ref)
            created_names.append(cname)
    core._write(writer, output)
    return created_names


def _sort_widgets_reading(items, by="rows"):
    """Sort ``(slot, ref, rect, name)`` tuples by rows (top-to-bottom,
    left-to-right) or by columns (left-to-right, top-to-bottom)."""
    if by == "cols":
        by_x = sorted(items, key=lambda it: (it[2][0], -it[2][3]))
        groups, cur = [], []
        for it in by_x:
            if not cur:
                cur = [it]
            else:
                col_x1 = max(g[2][2] for g in cur)
                if it[2][0] < col_x1 - 2:
                    cur.append(it)
                else:
                    groups.append(cur)
                    cur = [it]
        if cur:
            groups.append(cur)
        out = []
        for g in groups:
            g.sort(key=lambda it: (-it[2][3], it[2][0]))
            out.extend(g)
        return out
    by_y = sorted(items, key=lambda it: (-it[2][3], it[2][0]))
    groups, cur = [], []
    for it in by_y:
        if not cur:
            cur = [it]
        else:
            row_y0 = min(g[2][1] for g in cur)
            if it[2][3] > row_y0 + 2:
                cur.append(it)
            else:
                groups.append(cur)
                cur = [it]
    if cur:
        groups.append(cur)
    out = []
    for g in groups:
        g.sort(key=lambda it: (it[2][0], -it[2][3]))
        out.extend(g)
    return out


def reorder_fields(path, output, page, order=None, mode="custom", password=None):
    """Reorder widget annotations on 1-based ``page`` and set ``/Tabs``.

    ``mode`` is ``"custom"`` (with ``order`` as a permutation of 0-based
    widget indices), ``"rows"`` (``/Tabs /R``), or ``"cols"`` (``/Tabs /C``).
    Returns the new list of field names in order.
    """
    reader = core.open_pdf(path, password)
    writer = core.clone_document(reader)
    pno = int(page)
    if pno < 1 or pno > len(writer.pages):
        raise core.PdfManagerError(f"page {pno} is out of range")
    pg = writer.pages[pno - 1]
    if "/Annots" not in pg:
        core._write(writer, output)
        return []
    annots = pg["/Annots"].get_object()
    widget_slots = []
    for slot, ref in enumerate(annots):
        obj = ref.get_object()
        if obj.get("/Subtype") == "/Widget":
            field = _field_of(obj)
            widget_slots.append((slot, ref, _rect_of(obj), _qualified_name(field)))
    if not widget_slots:
        core._write(writer, output)
        return []

    if mode == "rows":
        ordered = _sort_widgets_reading(widget_slots, "rows")
        pg[NameObject("/Tabs")] = NameObject("/R")
    elif mode == "cols":
        ordered = _sort_widgets_reading(widget_slots, "cols")
        pg[NameObject("/Tabs")] = NameObject("/C")
    else:
        n = len(widget_slots)
        if not isinstance(order, (list, tuple)) or len(order) != n or set(order) != set(range(n)):
            raise core.PdfManagerError("order must list every field on the page once")
        ordered = [widget_slots[i] for i in order]
        pg[NameObject("/Tabs")] = NameObject("/A")

    slots = [w[0] for w in widget_slots]
    for slot, item in zip(slots, ordered):
        annots[slot] = item[1]
    core._write(writer, output)
    return [item[3] for item in ordered]


def keep_defaults(path, output, password=None):
    """Copy each field's current value (``/V``) into its default value
    (``/DV``) so values entered during a preview become the form's defaults."""
    reader = core.open_pdf(path, password)
    writer = core.clone_document(reader)
    count = 0
    seen = set()
    for _, _, annot, field in _widgets(writer.pages):
        fid = _key(field)
        if fid in seen:
            continue
        seen.add(fid)
        v = field.get("/V")
        if v is not None:
            field[NameObject("/DV")] = _clone_pdf_obj(v)
            count += 1
        elif "/DV" in field:
            del field[NameObject("/DV")]
    core._write(writer, output)
    return count

