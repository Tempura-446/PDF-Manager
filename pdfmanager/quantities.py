# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""A quantity schedule (takeoff) from what was measured on a drawing.

``read`` collects the measurements saved in a PDF: the dimension lines
placed with the reader's annotate bar, the reader's measurements once
they're put in the PDF, and the measurement annotations other programs
write (Acrobat, Bluebeam, Revu…): ``/Line``, ``/PolyLine`` and
``/Polygon`` annotations that carry a ``/Measure`` dictionary or say what
they are (``/IT /LineDimension``, ``/PolyLineDimension``,
``/PolygonDimension``). Each quantity is worked out again from the
annotation's own points and its scale, not read from its words.

``schedule`` puts them in one table with the measurements still only in
the reader (sent by the page), and adds the totals per group and unit;
``write_csv`` and ``write_xlsx`` save it. In the workbook numbers are
numbers, with the unit in the next column, so they can be summed.
"""

import csv
import math
import re
from pathlib import Path

from pdfmanager import core, viewports

COLUMNS = ["Sheet", "Kind", "Group", "Name", "Quantity", "Unit", "Perimeter", "Perimeter unit",
           "Measured", "Scale", "Colour", "By", "When"]
KINDS = ("Length", "Area", "Count", "Angle")
DIMENSIONS = {"/LineDimension": "Length", "/PolyLineDimension": "Length", "/PolygonDimension": "Area"}
SHAPES = {"/Line": "Length", "/PolyLine": "Length", "/Polygon": "Area"}
_COUNT = re.compile(r"^(.*) \((\d+) of (\d+)\)$", re.S)


def _str(value):
    return str(value) if value is not None else ""


def _nums(value):
    try:
        return [float(v) for v in value]
    except (TypeError, ValueError):
        return []


def _pairs(values):
    return [(values[i], values[i + 1]) for i in range(0, len(values) - 1, 2)]


def _date(raw):
    m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?", _str(raw))
    if not m:
        return ""
    y, mo, d, h, mi = m.groups()
    return f"{y}-{mo or '01'}-{d or '01'}" + (f" {h}:{mi or '00'}" if h else "")


def _hex(color):
    vals = _nums(color)
    if len(vals) == 3:
        return "#" + "".join(f"{round(max(0, min(1, v)) * 255):02X}" for v in vals)
    return ""


def _length(points):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


def _area(points):
    """Of a closed outline (shoelace), always positive."""
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))) / 2


def _format(entry):
    """(factor, unit) of the first number format in a /Measure array."""
    try:
        first = entry[0].get_object()
        factor = float(first.get("/C", 1))
    except (TypeError, IndexError, KeyError, AttributeError, ValueError):
        return None, ""
    if not factor > 0 or not math.isfinite(factor):
        return None, ""
    return factor, _str(first.get("/U")).strip()


def _scale(measure):
    """How a /Measure dictionary (rectilinear) turns page units into real
    ones: {"per": real units per page unit, "unit", "area": factor from
    square units to the area unit, "area_unit", "label"}, or None."""
    measure = measure.get_object() if measure is not None else None
    if not hasattr(measure, "get") or measure.get("/Subtype", "/RL") != "/RL":
        return None
    per, unit = _format(measure.get("/X"))
    if not per:
        return None
    # /D: distances, as a factor on the /X unit (1 when it's the same unit).
    by, d_unit = _format(measure.get("/D"))
    if by and d_unit and math.isclose(by, per, rel_tol=1e-6):
        dist, dist_unit = per, d_unit  # (ZPDF before 0.43 repeated /X here)
    elif by and d_unit:
        dist, dist_unit = per * by, d_unit
    else:
        dist, dist_unit = per, unit
    area, area_unit = _format(measure.get("/A"))
    if not area:
        area, area_unit = 1.0, f"sq {unit}"
    elif math.isclose(area, per * per, rel_tol=1e-6):
        area = 1.0  # (ZPDF before 0.43 wrote the whole factor here)
    return {"per": per, "unit": unit, "dist": dist, "dist_unit": dist_unit, "area": area, "area_unit": area_unit,
            "label": _str(measure.get("/R")).strip()}


def _from_viewport(view):
    """The same, from a page's stored scale (viewports.stored_scales)."""
    metric = view["units"] == "m"
    per = view["ratio"] / 72 * 0.0254 if metric else view["ratio"] / 72 / 12
    unit = "m" if metric else "ft"
    return {"per": per, "unit": unit, "dist": per, "dist_unit": unit, "area": 1.0,
            "area_unit": "m²" if metric else "sq ft", "label": view.get("label") or ""}


def _scale_words(scale):
    if not scale:
        return ""
    if scale["label"]:
        return scale["label"]
    inches = {"m": 1000 / 25.4, "ft": 12, "in": 1, "mm": 1 / 25.4, "cm": 10 / 25.4}.get(scale["unit"].lower())
    return f"1:{round(scale['per'] * 72 * inches):,}".replace(",", " ") if inches else ""


def _names(annot, zpdf):
    """(group, name) of a measurement. ZPDF writes "group - name - value"
    in the words and the group as the subject; other programs' subject is
    their kind of measurement, and the words are the person's."""
    words, subject = _str(annot.get("/Contents")).strip(), _str(annot.get("/Subj")).strip()
    if not zpdf:
        return "", words if not re.fullmatch(r"[\d\s.,'\"/\-]*\S{0,6}", words) else ""
    words = _COUNT.sub(r"\1", words)
    words = re.sub(r" \([^()]*\)$", "", words)          # (an area's perimeter)
    parts = words.split(" - ")
    if subject and parts and parts[0] == subject:
        parts = parts[1:]
    return subject, " - ".join(parts[:-1])


def read(path, password=None):
    """The measurements saved in the PDF, in page order: a list of
    {"page", "sheet", "kind", "group", "name", "quantity", "unit",
    "perimeter", "perimeter_unit", "measured", "scale", "colour", "by",
    "when"}. ``quantity`` is None when the annotation has no scale and its
    page stores none (then ``measured`` still has its words)."""
    from pdfmanager import filetools

    reader = core.open_pdf(path, password)
    try:
        stored = viewports.stored_scales(path, password)
    except core.PdfManagerError:
        stored = {}
    try:
        labels = filetools.page_labels(path, password) or []
    except Exception:  # noqa: BLE001 - labels are a nicety
        labels = []
    items = []
    for number, page in enumerate(reader.pages, 1):
        sheet = labels[number - 1] if number - 1 < len(labels) and labels[number - 1] else str(number)
        counts = {}
        annots = page.get("/Annots")
        annots = annots.get_object() if annots is not None else []
        for ref in annots if isinstance(annots, list) else []:
            try:
                annot = ref.get_object()
                subtype = annot.get("/Subtype")
            except Exception:  # noqa: BLE001 - a damaged entry
                continue
            zpdf = _str(annot.get("/NM")).startswith("zpdf-")
            # (A markup drawn with Shapes says so: whatever its comment looks like, it isn't a count's mark or an angle.)
            drawn = "/ZPDFKind" in annot
            words = _str(annot.get("/Contents")).strip()
            base = {"page": number, "sheet": sheet, "colour": _hex(annot.get("/C")), "by": _str(annot.get("/T")),
                    "when": _date(annot.get("/M") or annot.get("/CreationDate")), "perimeter": None,
                    "perimeter_unit": ""}
            if subtype == "/Circle" and zpdf and not drawn and _COUNT.match(words):   # one mark of a count
                text, _, total = _COUNT.match(words).groups()
                group, name = _names(annot, True)
                entry = counts.setdefault(text, {**base, "kind": "Count", "group": group, "name": name, "quantity": 0,
                                                 "unit": "items", "measured": f"{total} items", "scale": ""})
                entry["quantity"] += 1
                continue
            intent, measure = annot.get("/IT"), annot.get("/Measure")
            if subtype not in SHAPES:
                continue
            points = _pairs(_nums(annot.get("/L") if subtype == "/Line" else annot.get("/Vertices")))
            # (The reader's angle: a three-point line with no intent; it needs no scale.)
            angle = zpdf and not drawn and subtype == "/PolyLine" and intent is None and len(points) == 3 \
                and "/LE" not in annot and "°" in words
            if len(points) < 2 or (measure is None and intent not in DIMENSIONS and not angle):
                continue
            scale = _scale(measure)
            if scale is None:
                x, y = points[0]
                view = next((v for v in stored.get(number, [])
                             if v["bbox"][0] <= x <= v["bbox"][2] and v["bbox"][1] <= y <= v["bbox"][3]), None)
                scale = _from_viewport(view) if view else None
            kind = DIMENSIONS.get(intent) or SHAPES[subtype]
            group, name = _names(annot, zpdf)
            item = {**base, "kind": kind, "group": group, "name": name, "measured": words, "scale": _scale_words(scale),
                    "quantity": None, "unit": ""}
            if angle:
                a, vertex, b = points
                turn = math.degrees(math.atan2(b[1] - vertex[1], b[0] - vertex[0])
                                    - math.atan2(a[1] - vertex[1], a[0] - vertex[0]))
                turn = abs((turn + 180) % 360 - 180)
                item.update(kind="Angle", quantity=round(turn, 2), unit="°", scale="")
            elif kind == "Area":
                if len(points) < 3:
                    continue
                if scale:
                    holes = annot.get("/ZPDFHoles")
                    cut = sum(_area(_pairs(_nums(h))) for h in holes) if isinstance(holes, list) else 0
                    item.update(quantity=max(_area(points) - cut, 0) * scale["per"] ** 2 * scale["area"],
                                unit=scale["area_unit"],
                                perimeter=_length(points + points[:1]) * scale["dist"], perimeter_unit=scale["dist_unit"])
            elif scale:
                item.update(quantity=_length(points) * scale["dist"], unit=scale["dist_unit"])
            items.append(item)
        items.extend(counts.values())
    return items


def from_reader(rows):
    """Measurements still only in the reader, as the page sends them
    ([{sheet, kind, group, name, quantity, unit, perimeter, perimeter_unit,
    measured, scale, colour}]), checked and made like read()'s."""
    items = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue

        def number(key):
            try:
                value = float(row.get(key))
            except (TypeError, ValueError):
                return None
            return value if math.isfinite(value) else None

        kind = row.get("kind") if row.get("kind") in KINDS else "Length"
        items.append({"page": int(number("page") or 0), "sheet": _str(row.get("sheet"))[:40], "kind": kind,
                      "group": _str(row.get("group"))[:60], "name": _str(row.get("name"))[:80],
                      "quantity": number("quantity"), "unit": _str(row.get("unit"))[:12],
                      "perimeter": number("perimeter"), "perimeter_unit": _str(row.get("perimeter_unit"))[:12],
                      "measured": _str(row.get("measured"))[:120], "scale": _str(row.get("scale"))[:80],
                      "colour": _str(row.get("colour"))[:9], "by": _str(row.get("by"))[:80], "when": ""})
    return items


def totals(items):
    """[{"group", "kind", "unit", "quantity", "items"}]: the sum per group,
    kind and unit (angles aren't summed), in the order they first appear."""
    found = {}
    for item in items:
        if item["quantity"] is None or item["kind"] == "Angle":
            continue
        key = (item["group"], item["kind"], item["unit"])
        entry = found.setdefault(key, {"group": item["group"], "kind": item["kind"], "unit": item["unit"],
                                       "quantity": 0.0, "items": 0})
        entry["quantity"] += item["quantity"]
        entry["items"] += 1
    return list(found.values())


def schedule(path=None, password=None, extra=None):
    """Everything measured: {"items": the file's measurements then the
    reader's, by page, "totals": totals(items)}."""
    items = (read(path, password) if path else []) + from_reader(extra)
    items.sort(key=lambda i: i["page"] or 10 ** 9)  # (stable: the order on each page stays; no page: last)
    return {"items": items, "totals": totals(items)}


def _round(value, kind):
    if value is None:
        return None
    return int(value) if kind == "Count" else round(value, 2 if kind == "Angle" else 4)


def _row(item):
    return [item["sheet"], item["kind"], item["group"], item["name"], _round(item["quantity"], item["kind"]), item["unit"],
            _round(item["perimeter"], "Length"), item["perimeter_unit"], item["measured"], item["scale"], item["colour"],
            item["by"], item["when"]]


def _total_rows(found):
    return [[t["group"] or "(no group)", t["kind"], _round(t["quantity"], t["kind"]), t["unit"], t["items"]] for t in found]


TOTAL_COLUMNS = ["Group", "Kind", "Total", "Unit", "Measurements"]


def _safe(value):
    """A cell's text, never read as a formula by a spreadsheet."""
    text = _str(value)
    return "'" + text if text and text[0] in "=+-@\t\r" else text


def write_csv(found, output):
    """The schedule as one CSV file: the items, an empty line, the totals."""
    output = Path(output)
    with open(output, "w", newline="", encoding="utf-8-sig") as fh:  # (the BOM: Excel reads it as UTF-8)
        out = csv.writer(fh)
        out.writerow(COLUMNS)
        for item in found["items"]:
            out.writerow(["" if v is None else v if isinstance(v, (int, float)) else _safe(v) for v in _row(item)])
        if found["totals"]:
            out.writerow([])
            out.writerow(["Totals"])
            out.writerow(TOTAL_COLUMNS)
            for row in _total_rows(found["totals"]):
                out.writerow([v if isinstance(v, (int, float)) else _safe(v) for v in row])
    return output


def write_xlsx(found, output, title=""):
    """The schedule as a workbook: a sheet of items and a sheet of totals,
    numbers as numbers."""
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    def fill(sheet, header, rows):
        widths = [len(h) for h in header]
        sheet.append(header)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for r, row in enumerate(rows, 2):
            for c, value in enumerate(row, 1):
                if value is None or value == "":
                    continue
                cell = sheet.cell(row=r, column=c)
                if isinstance(value, (int, float)):
                    cell.value = value
                else:
                    cell.value = ILLEGAL_CHARACTERS_RE.sub("", _str(value))
                    cell.data_type = "s"  # "=..." stays text
                widths[c - 1] = max(widths[c - 1], min(len(_str(value)), 50))
        for i, width in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(i)].width = width + 2
        sheet.freeze_panes = "A2"

    book = Workbook()
    items = book.active
    items.title = "Quantities"
    fill(items, COLUMNS, [_row(i) for i in found["items"]])
    fill(book.create_sheet("Totals"), TOTAL_COLUMNS, _total_rows(found["totals"]))
    if title:
        book.properties.title = title
    output = Path(output)
    book.save(output)
    return output


def said(found):
    """One sentence about a schedule, for a tool's notes."""
    n = len(found["items"])
    if not n:
        return "no measurements found: measure with the Scale bar or place dimensions, and put them in the PDF"
    parts = []
    for t in found["totals"][:6]:
        amount = f"{t['quantity']:,.0f}" if t["kind"] == "Count" else f"{t['quantity']:,.2f}"
        parts.append(f"{t['group'] or t['kind'].lower()}: {amount} {t['unit']}".strip())
    unscaled = sum(1 for i in found["items"] if i["quantity"] is None)
    return (f"{n} measurement{'s' if n != 1 else ''}" + (f" ({'; '.join(parts)})" if parts else "")
            + (f"; {unscaled} without a scale, listed with their words only" if unscaled else ""))
