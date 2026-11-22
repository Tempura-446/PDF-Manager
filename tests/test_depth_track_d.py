# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Track D depth tests: PDF -> Excel auto-OCR & header styling, plus Reader text-edit integrity headers."""

import shutil
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
from reportlab.lib import colors

from pdfmanager import tables
from pdfmanager.server import operations


def _make_table_pdf(path: Path) -> Path:
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    data = [
        ["Item", "Qty", "Price"],
        ["Widget A", "10", "$25.00"],
        ["Widget B", "5", "$40.00"],
    ]
    tbl = Table(data)
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    doc.build([tbl])
    return path


def _make_scanned_blank_pdf(path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    img = Image.new("L", (400, 500), 255)
    c.drawImage(ImageReader(img), 0, 0, letter[0], letter[1])
    img.close()
    c.showPage()
    c.save()
    return path


def test_to_excel_styles_header_fill_and_cell_borders(tmp_path):
    src = _make_table_pdf(tmp_path / "invoice.pdf")
    out = tmp_path / "invoice.xlsx"
    res = tables.to_excel(src, out)
    assert res["tables"] == 1

    wb = load_workbook(out)
    ws = wb[res["sheets"][0]]
    # Header row is bold with subtle fill and thin border
    assert ws["A1"].font.bold is True
    assert ws["A1"].fill.fill_type == "solid"
    assert ws["A1"].fill.fgColor.rgb.endswith("EEF2F7")
    assert ws["A1"].border.top.style == "thin"
    # Data row also has thin border
    assert ws["A2"].border.bottom.style == "thin"


def test_to_excel_auto_ocr_retries_on_scanned_pdf(tmp_path):
    scanned = _make_scanned_blank_pdf(tmp_path / "scanned.pdf")
    real_table = _make_table_pdf(tmp_path / "ocr_result.pdf")
    out = tmp_path / "out.xlsx"

    def fake_ocr(_src, dst, **_kwargs):
        shutil.copyfile(real_table, dst)
        return Path(dst)

    with mock.patch("pdfmanager.core.ocr", side_effect=fake_ocr) as mocked_ocr:
        res = tables.to_excel(scanned, out, auto_ocr=True)

    assert mocked_ocr.called
    assert res["tables"] == 1
    assert out.exists()


def test_gui_to_excel_has_auto_ocr_option():
    spec = operations.OPS["to-excel"]
    keys = [f["key"] for f in spec["fields"]]
    assert "auto_ocr" in keys
