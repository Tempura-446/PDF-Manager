"""Tests for PDF → Excel: finding tables (tables.py), the workbook and CSV
files, the command line and the app's tool."""

import csv
import io
import zipfile

import pytest
from openpyxl import load_workbook
from PIL import Image, ImageDraw
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from pdfmanager import core, gui, tables
from pdfmanager.cli import main

STYLES = getSampleStyleSheet()
GRID = TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                   ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")])
PROSE = "This is an ordinary paragraph of running text with nothing tabular in it. " * 12

RULED_ROWS = [
    ["Item", "Amount", "Change", "Share", "Code"],
    ["Widgets", "$1,234.50", "(12)", "7.5%", "00123"],
    ["Gadgets with a rather long description that wraps", "€99", "3", "12%", "04567"],
    ["Total", "£1,333.50", "-9", "19.5%", ""],
]
ALIGNED_ROWS = [
    ["Name", "Department", "Salary", "Start"],
    ["Alice Smith", "Finance", "52,000", "2019"],
    ["Bob Jones", "Engineering", "61,500", "2020"],
    ["Carol White", "Sales", "48,250", "2018"],
    ["Dan Brown", "Support", "39,900", "2021"],
]


def build(path, story):
    SimpleDocTemplate(str(path), pagesize=A4).build(story)
    return path


def ruled_table(rows=RULED_ROWS):
    """The ruled table; the long cell is a paragraph, so it wraps."""
    cells = [[Paragraph(c, STYLES["Normal"]) if len(c) > 40 else c for c in row] for row in rows]
    return Table(cells, colWidths=[150, 80, 60, 60, 60], style=GRID)


def aligned_table(rows=ALIGNED_ROWS):
    """No lines at all: only the columns line up."""
    return Table(rows, colWidths=[120, 120, 80, 60],
                 style=TableStyle([("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                                   ("ALIGN", (2, 0), (2, -1), "RIGHT")]))


@pytest.fixture
def report(tmp_path):
    """Page 1: a ruled table, text, a borderless table. Page 2: only text."""
    return build(tmp_path / "report.pdf", [
        Paragraph("Quarterly report", STYLES["Title"]), ruled_table(),
        Paragraph(PROSE, STYLES["Normal"]), Spacer(1, 12), aligned_table(),
        PageBreak(), Paragraph(PROSE, STYLES["Normal"]), Paragraph(PROSE, STYLES["Normal"])])


def long_rows(n=70):
    return [["#", "Product", "Qty", "Price"]] + \
        [[str(i), f"Product {i}", str(i * 3), f"{i * 1.25:.2f}"] for i in range(1, n + 1)]


def continued_pdf(path, repeat_header):
    """A ruled table of 70 rows that runs onto page 2."""
    return build(path, [Paragraph("Inventory", STYLES["Title"]),
                        Table(long_rows(), colWidths=[40, 160, 60, 80], style=GRID,
                              repeatRows=1 if repeat_header else 0)])


def prose_pdf(path):
    return build(path, [Paragraph("Just text", STYLES["Title"]),
                        Paragraph(PROSE, STYLES["Normal"]), Paragraph(PROSE, STYLES["Normal"])])


def scanned_pdf(path):
    """A page that is only a picture of a table: no text."""
    image = Image.new("RGB", (600, 300), "white")
    draw = ImageDraw.Draw(image)
    for y in range(20, 300, 60):
        draw.line([(20, y), (580, y)], fill="black", width=2)
    draw.text((40, 40), "Amount 1,234", fill="black")
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawImage(ImageReader(image), 50, 400, width=500, height=250)
    c.showPage()
    c.save()
    return path


# -- finding tables ----------------------------------------------------------------------

def test_finds_ruled_and_borderless_tables(report):
    found = tables.find_tables(report)
    assert [(t["page"], t["kind"]) for t in found] == [(1, "ruled"), (1, "aligned")]
    # wrapped lines are joined with a space; empty cells stay ""
    assert found[0]["rows"] == RULED_ROWS
    assert found[1]["rows"] == ALIGNED_ROWS


def test_bbox_is_in_pdf_points_from_the_bottom_left(tmp_path):
    path = tmp_path / "placed.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    table = Table([["Name", "Score"], ["Ann", "91"], ["Ben", "78"]], style=GRID)
    width, height = table.wrapOn(c, 400, 400)
    table.drawOn(c, 100, 600)  # bottom-left corner at (100, 600)
    c.showPage()
    c.save()
    (found,) = tables.find_tables(path)
    x0, y0, x1, y1 = found["bbox"]
    assert found["rows"] == [["Name", "Score"], ["Ann", "91"], ["Ben", "78"]]
    assert abs(x0 - 100) < 1 and abs(y0 - 600) < 1
    assert abs(x1 - (100 + width)) < 1 and abs(y1 - (600 + height)) < 1


def test_bbox_on_a_rotated_page(tmp_path):
    """A /Rotate 90 page whose content reads upright: rows as seen, the box
    in the page's own (unrotated) coordinates."""
    path = tmp_path / "rotated.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setPageRotation(90)
    c.rotate(90)
    c.translate(0, -A4[0])
    table = Table([["Name", "Score"], ["Ann", "91"], ["Ben", "78"]], style=GRID)
    width, height = table.wrapOn(c, 400, 400)
    table.drawOn(c, 100, 400)
    c.showPage()
    c.save()
    (found,) = tables.find_tables(path)
    assert found["rows"] == [["Name", "Score"], ["Ann", "91"], ["Ben", "78"]]
    # (x, y) as drawn ends up at (A4 width - y, x) on the unrotated page
    x0, y0, x1, y1 = found["bbox"]
    assert abs(x0 - (A4[0] - 400 - height)) < 1 and abs(x1 - (A4[0] - 400)) < 1
    assert abs(y0 - 100) < 1 and abs(y1 - (100 + width)) < 1


@pytest.mark.parametrize("repeat_header", [False, True])
def test_a_table_continued_on_the_next_page_is_one_table(tmp_path, repeat_header):
    found = tables.find_tables(continued_pdf(tmp_path / "long.pdf", repeat_header))
    assert len(found) == 1
    assert found[0]["page"] == 1 and found[0]["pages"] == [1, 2]
    assert found[0]["rows"] == long_rows()  # the repeated header is dropped


def test_tables_of_the_same_shape_away_from_the_page_break_stay_apart(tmp_path):
    rows = [["Name", "Score"], ["Ann", "91"], ["Ben", "78"]]
    path = build(tmp_path / "two.pdf", [Table(rows, style=GRID), PageBreak(),
                                        Table(rows, style=GRID)])
    found = tables.find_tables(path)
    assert [t["pages"] for t in found] == [[1], [2]]


def test_no_tables_in_prose_or_columns_of_text(tmp_path):
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

    assert tables.find_tables(prose_pdf(tmp_path / "prose.pdf")) == []
    path = tmp_path / "columns.pdf"
    doc = BaseDocTemplate(str(path), pagesize=A4)
    w, h = A4
    doc.addPageTemplates([PageTemplate(frames=[Frame(40, 40, w / 2 - 50, h - 80),
                                               Frame(w / 2 + 10, 40, w / 2 - 50, h - 80)])])
    doc.build([Paragraph(PROSE, STYLES["Normal"]) for _ in range(4)])
    assert tables.find_tables(path) == []


def test_pages_limit_the_search(report):
    assert tables.find_tables(report, pages="2") == []
    assert len(tables.find_tables(report, pages="1")) == 2


def test_password_protected(tmp_path, report):
    locked = tmp_path / "locked.pdf"
    core.encrypt(report, locked, "s3cret")
    assert tables.find_tables(locked, password="s3cret")[0]["rows"] == RULED_ROWS
    with pytest.raises(core.PdfManagerError, match="wrong password"):
        tables.find_tables(locked, password="nope")


@pytest.mark.parametrize("text, value, fmt", [
    ("$1,234.50", 1234.5, '"$"#,##0.00'),
    ("(12)", -12, "0;(0)"),
    ("7.5%", 0.075, "0.0%"),
    ("-9", -9, "General"),
    ("2019", 2019, "General"),
    ("€99", 99, '"€"0'),
    ("12 €", 12, '"€"0'),
    ("£1,333.50", 1333.5, '"£"#,##0.00'),
    ("(2.7%)", -0.027, "0.0%;(0.0%)"),
    ("1.234,56", 1234.56, "#,##0.00"),
    ("0.50", 0.5, "0.00"),
    ("−3", -3, "General"),
])
def test_numbers(text, value, fmt):
    assert tables.parse_number(text) == (value, fmt)


@pytest.mark.parametrize("text", ["00123", "04567", "", "n/a", "12,5", "1,23", "3/4",
                                  "10-20", "1234567890123456", "27.083.460", "(X)", "$", "%"])
def test_not_numbers(text):
    assert tables.parse_number(text) is None


# -- the workbook and CSV files ------------------------------------------------------------

def test_workbook_sheet_per_table(tmp_path, report):
    made = tables.to_excel(report, tmp_path / "out.xlsx")
    assert made["tables"] == 2 and made["sheets"] == ["p1 table 1", "p1 table 2"]
    book = load_workbook(made["output"])
    assert book.sheetnames == ["Summary", "p1 table 1", "p1 table 2"]

    summary = [[c.value for c in row] for row in book["Summary"].iter_rows(min_row=3)]
    assert summary == [["Sheet", "Page", "Table", "Rows", "Columns"],
                       ["p1 table 1", 1, 1, 4, 5], ["p1 table 2", 1, 2, 5, 4]]

    sheet = book["p1 table 1"]
    assert [c.value for c in sheet[1]] == RULED_ROWS[0]
    assert all(c.font.b for c in sheet[1]) and not sheet["A2"].font.b
    assert sheet.freeze_panes == "A2"
    assert sheet["B2"].value == 1234.5 and sheet["B2"].number_format == '"$"#,##0.00'
    assert sheet["C2"].value == -12 and sheet["D2"].value == 0.075
    assert sheet["E2"].value == "00123" and sheet["E2"].data_type == "s"  # leading zero kept
    assert sheet["A3"].value == RULED_ROWS[2][0]
    assert sheet["C4"].value == -9 and sheet["E4"].value is None
    assert sheet.column_dimensions["A"].width > sheet.column_dimensions["C"].width

    staff = book["p1 table 2"]
    assert staff["C2"].value == 52000 and staff["D2"].value == 2019
    assert staff["A2"].value == "Alice Smith"


def test_workbook_one_sheet(tmp_path, report):
    made = tables.to_excel(report, tmp_path / "out.xlsx", mode="one-sheet")
    book = load_workbook(made["output"])
    assert book.sheetnames == ["Summary", "Tables"]
    values = [[c.value for c in row] for row in book["Tables"].iter_rows()]
    assert values[0][0] == "Page 1, table 1"
    assert values[1] == RULED_ROWS[0]
    second = values.index(["Page 1, table 2", None, None, None, None])
    assert values[second + 1][:4] == ALIGNED_ROWS[0]
    assert values[second + 2][:4] == ["Alice Smith", "Finance", 52000, 2019]


def test_continued_table_is_one_sheet(tmp_path):
    made = tables.to_excel(continued_pdf(tmp_path / "long.pdf", True), tmp_path / "long.xlsx")
    book = load_workbook(made["output"])
    assert book.sheetnames == ["Summary", "p1-2 table 1"]
    assert book["p1-2 table 1"].max_row == 71
    assert book["Summary"]["B4"].value == "1-2"


def test_text_that_looks_like_a_formula_stays_text(tmp_path):
    rows = [["Name", "Note"], ["Ann", "=1+1"], ["Ben", "@SUM(A1)"]]
    path = build(tmp_path / "f.pdf", [Table(rows, style=GRID)])
    sheet = load_workbook(tables.to_excel(path, tmp_path / "f.xlsx")["output"])["p1 table 1"]
    assert sheet["B2"].value == "=1+1" and sheet["B2"].data_type == "s"
    (written,) = tables.to_csv(path, tmp_path / "csv")["files"]
    assert list(csv.reader(io.StringIO(written.read_text(encoding="utf-8-sig"))))[1:] == \
        [["Ann", "'=1+1"], ["Ben", "'@SUM(A1)"]]


def test_csv_files(tmp_path, report):
    made = tables.to_csv(report, tmp_path / "csv")
    assert [p.name for p in made["files"]] == ["report_p1_table1.csv", "report_p1_table2.csv"]
    raw = made["files"][0].read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # a BOM, so Excel reads € and £
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert rows == RULED_ROWS


def test_no_tables(tmp_path):
    with pytest.raises(tables.NoTablesFound, match="^no tables were found$"):
        tables.to_excel(prose_pdf(tmp_path / "prose.pdf"), tmp_path / "x.xlsx")
    with pytest.raises(core.PdfManagerError, match="no text .* need OCR first"):
        tables.to_csv(scanned_pdf(tmp_path / "scan.pdf"), tmp_path / "csv")
    assert not (tmp_path / "x.xlsx").exists()


def test_bad_layout(tmp_path, report):
    with pytest.raises(core.PdfManagerError, match="layout"):
        tables.to_excel(report, tmp_path / "x.xlsx", mode="sideways")


# -- command line -------------------------------------------------------------------------

def test_cli_to_excel(tmp_path, report, capsys):
    assert main(["to-excel", str(report)]) == 0
    assert "2 tables" in capsys.readouterr().out
    assert load_workbook(tmp_path / "report.xlsx").sheetnames == \
        ["Summary", "p1 table 1", "p1 table 2"]

    assert main(["to-excel", str(report), "--one-sheet", "-o", str(tmp_path / "one.xlsx")]) == 0
    assert load_workbook(tmp_path / "one.xlsx").sheetnames == ["Summary", "Tables"]

    assert main(["to-excel", str(report), "--csv", "-p", "1"]) == 0
    assert sorted(p.name for p in (tmp_path / "report_tables").iterdir()) == \
        ["report_p1_table1.csv", "report_p1_table2.csv"]


def test_cli_to_excel_batch(tmp_path, report, capsys):
    prose = prose_pdf(tmp_path / "prose.pdf")
    assert main(["to-excel", str(report), str(prose), "-o", str(tmp_path / "out")]) == 1
    captured = capsys.readouterr()
    assert "no tables were found" in captured.err and "1 of 2 file(s) done" in captured.out
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["report.xlsx"]


def test_cli_to_excel_password_from_the_environment(tmp_path, report, monkeypatch):
    locked = tmp_path / "locked.pdf"
    core.encrypt(report, locked, "pw")
    monkeypatch.setenv("PDFM_PASSWORD", "pw")
    assert main(["to-excel", str(locked)]) == 0
    assert load_workbook(tmp_path / "locked.xlsx").sheetnames[0] == "Summary"


# -- the app ------------------------------------------------------------------------------

def test_tool_is_in_the_app():
    ids = [op["id"] for op in gui.OPERATIONS]
    assert ids[ids.index("to-word") + 1] == "to-excel"
    op = gui.OPS["to-excel"]
    assert op["group"] == "Convert" and op["label"] == "PDF → Excel"
    fields = {f["key"]: f for f in op["fields"]}
    assert set(fields) == {"pages", "layout", "format"}
    assert [c[0] for c in fields["layout"]["choices"]] == list(tables.LAYOUTS)
    assert [c[0] for c in fields["format"]["choices"]] == ["xlsx", "csv"]
    from pathlib import Path
    html = Path(gui.__file__).with_name("gui.html").read_text(encoding="utf-8")
    assert "\"to-excel\": '<" in html and '"to-excel": "' in html  # an icon and a tagline


def test_gui_to_excel(tmp_path, report):
    work = tmp_path / "w1"
    work.mkdir()
    notes = []
    name, data, ctype = gui.run_operation("to-excel", [report], {"layout": "one-sheet"},
                                          work=work, notes=notes)
    assert name == "report.xlsx" and ctype.endswith("spreadsheetml.sheet")
    assert load_workbook(io.BytesIO(data)).sheetnames == ["Summary", "Tables"]
    assert notes == ["report.pdf: 2 tables"]

    work = tmp_path / "w2"
    work.mkdir()
    name, data, ctype = gui.run_operation("to-excel", [report], {"format": "csv", "pages": "1"},
                                          work=work, notes=[])
    assert name == "report_tables.zip" and ctype == "application/zip"
    assert sorted(zipfile.ZipFile(io.BytesIO(data)).namelist()) == \
        ["report_tables/report_p1_table1.csv", "report_tables/report_p1_table2.csv"]


def test_gui_to_excel_without_tables(tmp_path, report):
    prose = prose_pdf(tmp_path / "prose.pdf")
    work = tmp_path / "w1"
    work.mkdir()
    notes = []
    # one file without tables in a batch: a note, the others still convert
    name, data, _ = gui.run_operation("to-excel", [report, prose, report], {}, work=work,
                                      notes=notes)
    assert name == "pdfm_to-excel.zip"
    assert sorted(zipfile.ZipFile(io.BytesIO(data)).namelist()) == ["report (2).xlsx",
                                                                    "report.xlsx"]
    assert "prose.pdf: no tables were found" in notes
    work = tmp_path / "w2"
    work.mkdir()
    with pytest.raises(gui.UserError, match="no tables were found"):
        gui.run_operation("to-excel", [prose], {}, work=work, notes=[])
