# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Form diagnostics: flag missing labels, overlaps, tiny fields and tab order issues."""

import re

from pdfmanager import core
from .fields import field_details

def check_form(path, password=None):
    """Return a list of potential issues in the form's fields:
    - ``missing_tooltip``: field has no tooltip (``/TU``)
    - ``duplicate_type``: same name used by fields of different types
    - ``off_page``: field extends outside the page box
    - ``overlapping``: two fields on the same page overlap
    - ``required_readonly``: required field is also read-only
    - ``missing_calc_field``: calculation names a field that isn't in the form
    """
    reader = core.open_pdf(path, password)
    details = field_details(path, password)
    all_names = {f["name"] for flist in details.values() for f in flist if f["name"]}
    by_name_type = {}
    for pno, flist in details.items():
        for f in flist:
            bt = "text" if f["type"] == "multiline" else f["type"]
            by_name_type.setdefault(f["name"], set()).add(bt)

    issues = []
    for pno, flist in sorted(details.items()):
        pg = reader.pages[pno - 1]
        box = pg.cropbox or pg.mediabox
        px0, py0, px1, py1 = float(box.left), float(box.bottom), float(box.right), float(box.top)

        for i, f in enumerate(flist):
            name = f["name"] or f"Field {f['index'] + 1}"
            if not (f.get("tooltip") or "").strip():
                issues.append({
                    "kind": "missing_tooltip", "page": pno, "index": f["index"], "name": name,
                    "message": f"“{name}” on page {pno} has no tooltip.",
                })
            bt = "text" if f["type"] == "multiline" else f["type"]
            if f["name"] and len(by_name_type.get(f["name"], ())) > 1:
                issues.append({
                    "kind": "duplicate_type", "page": pno, "index": f["index"], "name": name,
                    "message": f"“{name}” is used for different field types ({bt}).",
                })
            x0, y0, x1, y1 = f["rect"]
            if x0 < px0 - 1 or y0 < py0 - 1 or x1 > px1 + 1 or y1 > py1 + 1:
                issues.append({
                    "kind": "off_page", "page": pno, "index": f["index"], "name": name,
                    "message": f"“{name}” on page {pno} extends off the page.",
                })
            if f.get("required") and f.get("readonly"):
                issues.append({
                    "kind": "required_readonly", "page": pno, "index": f["index"], "name": name,
                    "message": f"“{name}” on page {pno} is both required and read-only.",
                })
            calc = f.get("calculate")
            if calc:
                refs = list(calc.get("fields") or ())
                if not refs and calc.get("formula"):
                    refs = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", calc["formula"])
                missing = [r for r in refs if r not in all_names]
                if missing:
                    issues.append({
                        "kind": "missing_calc_field", "page": pno, "index": f["index"], "name": name,
                        "message": f"“{name}” calculates from missing field(s): {', '.join(missing)}.",
                    })
            for j in range(i + 1, len(flist)):
                g = flist[j]
                gx0, gy0, gx1, gy1 = g["rect"]
                ox = min(x1, gx1) - max(x0, gx0)
                oy = min(y1, gy1) - max(y0, gy0)
                if ox > 2 and oy > 2:
                    gname = g["name"] or f"Field {g['index'] + 1}"
                    issues.append({
                        "kind": "overlapping", "page": pno, "index": f["index"], "name": name,
                        "message": f"“{name}” and “{gname}” overlap on page {pno}.",
                    })
    return issues

