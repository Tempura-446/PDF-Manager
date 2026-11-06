"""Drawing scales stored in a PDF.

CAD programs and markup tools such as Bluebeam write a page's scale into
the file: a viewport (``/VP``) covers part of the page and carries a
measure dictionary (``/Measure``, subtype ``/RL``) whose first number
format says how many real units one unit on the page stands for. That is
exact, unlike reading "1/4" = 1'-0"" off the title block.

A scale is reported like the reader's own (pdfmanager/static/measure.js):
``ratio`` is real length / length on the paper and ``units`` "ft" or "m".
"""

from pdfmanager import core

# Inches in one of the units a number format may name.
_INCHES = {"in": 1, "inch": 1, "inches": 1, '"': 1, "ft": 12, "foot": 12, "feet": 12, "'": 12,
           "yd": 36, "mi": 63360, "mm": 1 / 25.4, "cm": 10 / 25.4, "m": 1000 / 25.4,
           "km": 1e6 / 25.4}
_METRIC = {"mm", "cm", "m", "km"}


def _number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _viewport_scale(viewport, user_unit):
    """{ratio, units, label} for a viewport with a rectilinear measure, else None."""
    measure = viewport.get("/Measure")
    measure = measure.get_object() if measure is not None else None
    if not hasattr(measure, "get") or measure.get("/Subtype", "/RL") != "/RL":
        return None  # geospatial measures are not drawing scales
    formats = measure.get("/X") or measure.get("/D")
    try:
        first = formats[0].get_object()
    except (TypeError, IndexError, KeyError, AttributeError):
        return None
    unit = str(first.get("/U", "")).strip().lower().rstrip(".")
    factor, per_unit = _INCHES.get(unit), _number(first.get("/C"))
    if not factor or not per_unit or per_unit <= 0:
        return None
    # One unit of the page is user_unit/72 inch on paper and per_unit real units.
    ratio = per_unit * factor * 72 / user_unit
    if not 0.01 <= ratio <= 1e6:
        return None
    return {"ratio": ratio, "units": "m" if unit in _METRIC else "ft",
            "label": str(measure.get("/R", "")).strip()}  # (the reader names unlabelled ones)


def stored_scales(path, password=None):
    """{page number (1-based): [viewport, ...]} for the pages that have a
    stored scale; a viewport is {ratio, units, label, bbox: [x0, y0, x1, y1]}
    in the page's own coordinates, the biggest first. ``label`` is the
    scale as the PDF writes it ("1/4 in = 1 ft"), "" if it doesn't."""
    reader = core.open_pdf(path, password)
    found = {}
    for number, page in enumerate(reader.pages, 1):
        viewports = page.get("/VP")
        if viewports is None:
            continue
        viewports = viewports.get_object()
        user_unit = _number(page.get("/UserUnit"), 1.0) or 1.0
        items = []
        for entry in viewports if isinstance(viewports, list) else []:
            entry = entry.get_object()
            if not hasattr(entry, "get"):
                continue
            scale = _viewport_scale(entry, user_unit)
            if not scale:
                continue
            box = [_number(v, 0.0) for v in (entry.get("/BBox") or page.mediabox)][:4]
            if len(box) < 4:
                continue
            x0, y0, x1, y1 = box
            items.append({**scale, "bbox": [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]})
        if items:
            items.sort(key=lambda v: -(v["bbox"][2] - v["bbox"][0]) * (v["bbox"][3] - v["bbox"][1]))
            found[number] = items
    return found
