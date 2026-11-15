# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.43.0 (quantities.py): a quantity schedule from what was measured on a
drawing: the reader's dimensions and measurements in the PDF, other
programs' measurement annotations, and the measurements still in the
reader; as CSV and as a workbook with totals."""

import csv

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject

from pdfmanager import markup, quantities

QUARTER = {"ratio": 48, "units": "ft", "label": "1/4\" = 1'-0\""}   # 1/4 inch on paper is a foot


def sheet(path, width=1224, height=792):
    w = PdfWriter()
    w.add_blank_page(width, height)
    w.write(str(path))
    return path


def marked(tmp_path):
    """A sheet at 1/4" = 1'-0" with two dimensions, an area with a cut-out,
    a count and an angle, all saved in the PDF by the reader's own tools."""
    out = tmp_path / "plan.pdf"
    markup.apply(sheet(tmp_path / "blank.pdf"), out, [
        # 144 pt = 2 in of paper = 8 ft; 216 pt = 12 ft
        {"op": "add", "page": 1, "kind": "dimension", "points": [[100, 700], [244, 700]], "value": "8'-0\"", "scale": QUARTER},
        {"op": "add", "page": 1, "kind": "measure", "shape": "distance", "points": [[100, 650], [316, 650]],
         "value": "12' 0\"", "scale": QUARTER, "label": "North wall", "group": "Walls"},
        # 360 x 180 pt = 20 x 10 ft = 200 sq ft, less a 36 x 36 pt (2 x 2 ft) cut-out = 196
        {"op": "add", "page": 1, "kind": "measure", "shape": "area", "value": "196 sq ft", "sub": "perimeter 60' 0\"",
         "scale": QUARTER, "label": "Living room", "group": "Flooring",
         "points": [[100, 100], [460, 100], [460, 280], [100, 280]], "holes": [[[200, 150], [236, 150], [236, 186], [200, 186]]]},
        {"op": "add", "page": 1, "kind": "measure", "shape": "count", "points": [[600, 300], [650, 300], [700, 300]],
         "value": "3 items", "label": "Outlets"},
        {"op": "add", "page": 1, "kind": "measure", "shape": "angle", "points": [[800, 300], [800, 100], [1000, 100]], "value": "90°"},
    ], author="Pat")
    return out


def test_the_readers_own_measurements_are_worked_out_again_from_the_file(tmp_path):
    items = quantities.read(marked(tmp_path))
    by = {(i["kind"], i["name"]): i for i in items}
    assert len(items) == 5 and {i["kind"] for i in items} == {"Length", "Area", "Count", "Angle"}
    dim = by[("Length", "")]
    assert dim["quantity"] == pytest.approx(8) and dim["unit"] == "ft" and dim["measured"] == "8'-0\""
    assert dim["scale"] == "1/4\" = 1'-0\"" and dim["by"] == "Pat" and dim["sheet"] == "1" and dim["when"]
    wall = by[("Length", "North wall")]
    assert wall["quantity"] == pytest.approx(12) and wall["group"] == "Walls"
    room = by[("Area", "Living room")]
    assert room["quantity"] == pytest.approx(196) and room["unit"] == "sq ft" and room["group"] == "Flooring"
    assert room["perimeter"] == pytest.approx(60) and room["perimeter_unit"] == "ft"
    outlets = by[("Count", "Outlets")]
    assert outlets["quantity"] == 3 and outlets["unit"] == "items"
    angle = by[("Angle", "")]
    assert angle["quantity"] == pytest.approx(90) and angle["unit"] == "°"


def foreign(tmp_path, measure=True):
    """A file marked up in another program: a metric line dimension and a
    polygon area, with a /Measure the way ISO 32000 writes one (1:100,
    distances in m, areas in m²)."""
    w = PdfWriter()
    page = w.add_blank_page(842, 595)
    per = 100 / 72 * 0.0254   # metres per point at 1:100

    def fmt(unit, c):
        return DictionaryObject({NameObject("/Type"): NameObject("/NumberFormat"), NameObject("/U"): TextStringObject(unit),
                                 NameObject("/C"): FloatObject(c), NameObject("/D"): NumberObject(100)})

    scale = DictionaryObject({NameObject("/Type"): NameObject("/Measure"), NameObject("/Subtype"): NameObject("/RL"),
                              NameObject("/R"): TextStringObject("1 cm = 1 m"), NameObject("/X"): ArrayObject([fmt("m", per)]),
                              NameObject("/D"): ArrayObject([fmt("m", 1)]), NameObject("/A"): ArrayObject([fmt("sq m", 1)])})

    def annot(subtype, intent, key, values, words):
        a = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject(subtype),
                              NameObject("/Rect"): ArrayObject(FloatObject(v) for v in (0, 0, 600, 500)),
                              NameObject("/IT"): NameObject(intent), NameObject(key): ArrayObject(FloatObject(v) for v in values),
                              NameObject("/Contents"): TextStringObject(words), NameObject("/T"): TextStringObject("Sam"),
                              NameObject("/Subj"): TextStringObject("Length Measurement"),
                              NameObject("/NM"): TextStringObject("ABCDEF-1")})
        if measure:
            a[NameObject("/Measure")] = scale
        return w._add_object(a)

    # 283.4646 pt = 10 cm of paper = 10 m; a 5 m x 4 m room
    cm = 72 / 2.54
    page[NameObject("/Annots")] = ArrayObject([
        annot("/Line", "/LineDimension", "/L", [100, 400, 100 + 10 * cm, 400], "10.00 m"),
        annot("/Polygon", "/PolygonDimension", "/Vertices", [100, 100, 100 + 5 * cm, 100, 100 + 5 * cm, 100 + 4 * cm, 100, 100 + 4 * cm],
              "Bedroom")])
    out = tmp_path / ("foreign.pdf" if measure else "unscaled.pdf")
    w.write(str(out))
    return out


def test_another_programs_measurement_annotations_are_read_too(tmp_path):
    line, room = quantities.read(foreign(tmp_path))
    assert line["kind"] == "Length" and line["quantity"] == pytest.approx(10, rel=1e-4) and line["unit"] == "m"
    assert line["name"] == "" and line["measured"] == "10.00 m" and line["group"] == "" and line["by"] == "Sam"
    assert room["kind"] == "Area" and room["quantity"] == pytest.approx(20, rel=1e-4) and room["unit"] == "sq m"
    assert room["name"] == "Bedroom" and room["perimeter"] == pytest.approx(18, rel=1e-4) and room["scale"] == "1 cm = 1 m"
    # without a scale: listed with its words, no number made up
    line, room = quantities.read(foreign(tmp_path, measure=False))
    assert line["quantity"] is None and line["measured"] == "10.00 m" and room["quantity"] is None


def test_the_schedule_adds_the_readers_list_and_the_totals(tmp_path):
    extra = [{"page": 1, "sheet": "1", "kind": "Length", "group": "Walls", "name": "South wall", "quantity": 20.5,
              "unit": "ft", "measured": "20' 6\"", "scale": "1/4\" = 1'-0\"", "colour": "#1f4fd8"},
             {"page": 2, "sheet": "A-2", "kind": "Count", "group": "", "name": "Doors", "quantity": 4, "unit": "items"},
             "nonsense", {"kind": "Area", "quantity": "=cmd()", "name": "=HYPERLINK(1)"}]
    found = quantities.schedule(marked(tmp_path), extra=extra)
    assert len(found["items"]) == 8 and found["items"][-2]["sheet"] == "A-2"
    totals = {(t["group"], t["kind"], t["unit"]): t for t in found["totals"]}
    assert totals[("Walls", "Length", "ft")]["quantity"] == pytest.approx(32.5) and totals[("Walls", "Length", "ft")]["items"] == 2
    assert totals[("Flooring", "Area", "sq ft")]["quantity"] == pytest.approx(196)
    assert totals[("", "Count", "items")]["quantity"] == 7             # 3 outlets and 4 doors
    assert not any(t["kind"] == "Angle" for t in found["totals"])     # angles aren't summed
    assert "8 measurements" in quantities.said(found) and "Walls: 32.50 ft" in quantities.said(found)
    assert "no measurements" in quantities.said(quantities.schedule(sheet(tmp_path / "empty.pdf")))


def test_the_workbook_holds_numbers_and_the_csv_the_same_rows(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    found = quantities.schedule(marked(tmp_path), extra=[{"kind": "Length", "name": "=HYPERLINK(1)", "quantity": 2, "unit": "ft"}])
    book = openpyxl.load_workbook(quantities.write_xlsx(found, tmp_path / "takeoff.xlsx", "plan.pdf"))
    assert book.sheetnames == ["Quantities", "Totals"]
    rows = list(book["Quantities"].iter_rows(values_only=True))
    assert list(rows[0]) == quantities.COLUMNS and len(rows) == 7
    wall = next(r for r in rows if r[3] == "North wall")
    assert wall[4] == pytest.approx(12) and isinstance(wall[4], (int, float)) and wall[5] == "ft" and wall[2] == "Walls"
    room = next(r for r in rows if r[3] == "Living room")
    assert room[4] == pytest.approx(196) and room[6] == pytest.approx(60) and room[7] == "ft"
    assert next(r for r in rows if r[3] == "Outlets")[4] == 3
    cell = next(c for row in book["Quantities"].iter_rows() for c in row if c.value == "=HYPERLINK(1)")
    assert cell.data_type == "s"                                       # text, never a formula
    totals = {(r[0], r[1]): r for r in book["Totals"].iter_rows(min_row=2, values_only=True)}
    assert totals[("Walls", "Length")][2] == pytest.approx(12) and totals[("Flooring", "Area")][3] == "sq ft"
    assert totals[("(no group)", "Length")][2] == pytest.approx(10)   # the dimension and the reader's 2 ft

    with open(quantities.write_csv(found, tmp_path / "takeoff.csv"), newline="", encoding="utf-8-sig") as fh:
        lines = list(csv.reader(fh))
    assert lines[0] == quantities.COLUMNS and ["Totals"] in lines
    assert any(r[3:6] == ["North wall", "12.0", "ft"] for r in lines)
    assert any(r[3] == "'=HYPERLINK(1)" for r in lines)


def test_files_made_before_0_43_still_read_right(tmp_path):
    """Earlier versions wrote the whole factor in /D and /A too."""
    src = marked(tmp_path)
    reader = PdfReader(str(src))
    w = PdfWriter()
    w.append(reader)
    for ref in w.pages[0]["/Annots"]:
        measure = ref.get_object().get("/Measure")
        if measure is not None:
            per = float(measure["/X"][0]["/C"])
            measure["/D"][0][NameObject("/C")] = FloatObject(per)
            measure["/A"][0][NameObject("/C")] = FloatObject(per * per)
    w.write(str(tmp_path / "old.pdf"))
    by = {(i["kind"], i["name"]): i for i in quantities.read(tmp_path / "old.pdf")}
    assert by[("Length", "North wall")]["quantity"] == pytest.approx(12)
    assert by[("Area", "Living room")]["quantity"] == pytest.approx(196)


def test_the_tool_and_the_command_line(tmp_path, capsys):
    pytest.importorskip("openpyxl")
    from pdfmanager import gui
    from pdfmanager.cli import main

    src = marked(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    notes = []
    name, data, _ = gui.run_operation("quantities", [src], {}, None, {}, work, notes)
    assert name == "plan quantities.xlsx" and data[:2] == b"PK" and "5 measurements" in notes[0]
    work2 = tmp_path / "work2"
    work2.mkdir()
    name, data, _ = gui.run_operation("quantities", [src], {"format": "csv"}, None, {}, work2, [])
    assert name == "plan quantities.csv" and b"North wall" in data

    assert main(["quantities", str(src), "-o", str(tmp_path / "takeoff.xlsx")]) == 0
    assert "5 measurements" in capsys.readouterr().out and (tmp_path / "takeoff.xlsx").read_bytes()[:2] == b"PK"
    assert main(["quantities", str(src), "--csv"]) == 0
    assert "North wall" in (tmp_path / "plan_quantities.csv").read_text(encoding="utf-8-sig")
    assert main(["quantities", str(sheet(tmp_path / "none.pdf"))]) != 0     # nothing measured: said, no empty file
    assert "no measurements" in capsys.readouterr().err and not (tmp_path / "none_quantities.xlsx").exists()
