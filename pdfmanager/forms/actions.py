# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Button actions (URL, page, reset, print, JS) and show/hide field actions."""

import json
import re

from pypdf.generic import (ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NumberObject, TextStringObject)

from ._base import _child
from .scripts import _js

def _action_of(annot, pages):
    """Read a button widget's ``/A`` action into a plain dict, or ``None``."""
    act = annot.get("/A")
    act = act.get_object() if act is not None else None
    if not isinstance(act, dict):
        return None
    s = str(act.get("/S", ""))
    if s == "/ResetForm":
        fields = [str(f) for f in (act.get("/Fields") or [])]
        return {"type": "reset", "fields": fields}
    if s == "/Named" and str(act.get("/N", "")) == "/Print":
        return {"type": "print"}
    if s == "/GoTo":
        dest = act.get("/D")
        dest = dest.get_object() if dest is not None else None
        if isinstance(dest, (list, tuple)) and dest:
            ref = getattr(dest[0], "indirect_reference", None) or dest[0]
            target = dest[0].get_object()
            for idx, pg in enumerate(pages, 1):
                if getattr(pg, "indirect_reference", None) == ref or pg.get_object() is target:
                    return {"type": "goto", "page": idx}
        return {"type": "goto", "page": 1}
    if s == "/URI":
        return {"type": "url", "url": str(act.get("/URI", ""))}
    if s == "/SubmitForm":
        f = act.get("/F")
        f = f.get_object() if f is not None else None
        uri = str(f.get("/F", "")) if isinstance(f, dict) else str(f or "")
        to, subject = "", ""
        if uri.lower().startswith("mailto:"):
            rest = uri[7:]
            if "?" in rest:
                to, qs = rest.split("?", 1)
                from urllib.parse import parse_qs
                subject = (parse_qs(qs).get("subject") or [""])[0]
            else:
                to = rest
        flags = int(act.get("/Flags", 0) or 0)
        return {"type": "email", "to": to, "subject": subject,
                "submit": "pdf" if (flags & 256) else "fdf"}
    return None


def _set_button_action(writer, annot, action):
    """Write or clear a button widget's ``/A`` action."""
    if not action or not action.get("type") or action.get("type") == "none":
        if "/A" in annot:
            del annot["/A"]
        return
    kind = action["type"]
    if kind == "reset":
        d = DictionaryObject({NameObject("/Type"): NameObject("/Action"),
                              NameObject("/S"): NameObject("/ResetForm")})
        fields = [str(x).strip() for x in (action.get("fields") or []) if str(x).strip()]
        if fields:
            d[NameObject("/Fields")] = ArrayObject([TextStringObject(f) for f in fields])
        annot[NameObject("/A")] = d
    elif kind == "print":
        annot[NameObject("/A")] = DictionaryObject({
            NameObject("/Type"): NameObject("/Action"),
            NameObject("/S"): NameObject("/Named"),
            NameObject("/N"): NameObject("/Print"),
        })
    elif kind == "goto":
        pno = max(1, min(len(writer.pages), int(action.get("page") or 1)))
        annot[NameObject("/A")] = DictionaryObject({
            NameObject("/Type"): NameObject("/Action"),
            NameObject("/S"): NameObject("/GoTo"),
            NameObject("/D"): ArrayObject([writer.pages[pno - 1].indirect_reference, NameObject("/Fit")]),
        })
    elif kind == "url":
        url = str(action.get("url") or "").strip()
        annot[NameObject("/A")] = DictionaryObject({
            NameObject("/Type"): NameObject("/Action"),
            NameObject("/S"): NameObject("/URI"),
            NameObject("/URI"): TextStringObject(url),
        })
    elif kind == "email":
        from urllib.parse import quote
        to = str(action.get("to") or "").strip()
        subject = str(action.get("subject") or "").strip()
        uri = f"mailto:{to}" + (f"?subject={quote(subject)}" if subject else "")
        submit_pdf = action.get("submit") == "pdf"
        annot[NameObject("/A")] = DictionaryObject({
            NameObject("/Type"): NameObject("/Action"),
            NameObject("/S"): NameObject("/SubmitForm"),
            NameObject("/F"): DictionaryObject({
                NameObject("/FS"): NameObject("/URL"),
                NameObject("/F"): TextStringObject(uri),
            }),
            NameObject("/Flags"): NumberObject(256 if submit_pdf else 0),
        })


_SHOW_HIDE_RE = re.compile(
    r"var _s = event\.target\.isBoxChecked\(0\) \? display\.visible : display\.hidden;\s*"
    r"(\[[^\]]*\])\.forEach"
)


def _show_hide_of(annot):
    """Return the list of field names shown/hidden by a checkbox, or ``[]``."""
    aa = annot.get("/AA")
    aa = aa.get_object() if aa is not None else None
    if not isinstance(aa, dict) or "/U" not in aa:
        return []
    u = aa["/U"].get_object()
    js = str(u.get("/JS", "")) if isinstance(u, dict) else ""
    m = _SHOW_HIDE_RE.search(js)
    if not m:
        return []
    try:
        items = json.loads(m.group(1))
        return [str(x) for x in items if str(x)]
    except (ValueError, TypeError):
        return []


def _set_show_hide(annot, field_names):
    """Write or clear a standard ``/AA /U`` JavaScript action on a checkbox
    that shows ``field_names`` when ticked and hides them when unticked."""
    names = [str(x).strip() for x in (field_names or []) if str(x).strip()]
    if not names:
        aa = annot.get("/AA")
        aa = aa.get_object() if aa is not None else None
        if isinstance(aa, dict) and "/U" in aa:
            del aa["/U"]
            if not len(aa):
                del annot["/AA"]
        return
    js = (
        f"var _s = event.target.isBoxChecked(0) ? display.visible : display.hidden;\n"
        f"{json.dumps(names)}.forEach(function(n){{ var f = this.getField(n); if (f) f.display = _s; }}, this);"
    )
    aa = _child(annot, "/AA", DictionaryObject)
    aa[NameObject("/U")] = _js(js)

