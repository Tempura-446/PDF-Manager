# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for Better OCR Phase 4: ruled and unruled table detection on scans,
cell-by-cell reading, numeric-cell second pass (O/0, l/1, S/5), and exact
PDF-to-Excel export from OCR'd scanned tables."""
import types

import openpyxl
from PIL import Image, ImageDraw

from pdfmanager import core, ocr, ocrtables, tables
from pdfmanager.textlayer import Word


def _draw_ruled_table_image(rows_data, width=900, height=700):
    """Draw a clean ruled table on a white scan image; returns (image, xs, ys)."""
    im = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(im)
    xs = [60, 320, 560, 820]
    ys = [80 + r * 70 for r in range(len(rows_data) + 1)]
    for y in ys:
        draw.line([(xs[0], y), (xs[-1], y)], fill="black", width=3)
    for x in xs:
        draw.line([(x, ys[0]), (x, ys[-1])], fill="black", width=3)
    for r, row in enumerate(rows_data):
        for c, text in enumerate(row):
            if text:
                draw.text((xs[c] + 18, ys[r] + 24), text, fill="black")
    return im, xs, ys


def _make_line(text, x0, y0, x1, y1, conf=0.90):
    quad = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return ocr.Line(text, quad, conf, [Word(text, quad, conf)])


def test_detect_ruled_tables_finds_grid():
    rows_data = [
        ["Item", "Qty", "Amount"],
        ["Widgets", "12", "1,042.50"],
        ["Gadgets", "50", "500.00"],
        ["Totals", "62", "1,542.50"],
    ]
    im, xs, ys = _draw_ruled_table_image(rows_data)
    found = ocrtables.detect_ruled_tables(im)
    assert len(found) == 1
    tbl = found[0]
    assert len(tbl["cells"]) == 4
    assert len(tbl["cells"][0]) == 3
    assert abs(tbl["rows"][0] - ys[0]) <= 4
    assert abs(tbl["cols"][0] - xs[0]) <= 4


def test_unruled_table_numeric_normalisation_and_row_order():
    """Unruled table lines get numeric confusion fixes (O/0, l/1, S/5) on body
    rows without touching headers, and come out strictly row-by-row."""
    # Intentionally scramble input order to verify row-by-row cell ordering.
    raw = [
        _make_line("$l25.00", 560, 210, 680, 238, 0.88),
        _make_line("Item", 80, 100, 160, 128, 0.96),
        _make_line("S0%", 340, 210, 410, 238, 0.85),
        _make_line("Amount", 560, 100, 660, 128, 0.96),
        _make_line("Rate", 340, 100, 410, 128, 0.96),
        _make_line("Alpha", 80, 155, 170, 183, 0.95),
        _make_line("1,O42.50", 560, 155, 690, 183, 0.82),
        _make_line("25%", 340, 155, 410, 183, 0.94),
        _make_line("Beta", 80, 210, 160, 238, 0.95),
        _make_line("Gamma", 80, 265, 180, 293, 0.95),
        _make_line("O%", 340, 265, 390, 293, 0.80),
        _make_line("O.00", 560, 265, 640, 293, 0.81),
    ]
    refined = ocrtables.refine_tables(None, raw)
    texts = [l.text for l in refined]
    assert texts == [
        "Item", "Rate", "Amount",
        "Alpha", "25%", "1,042.50",
        "Beta", "50%", "$125.00",
        "Gamma", "0%", "0.00",
    ]


def test_ruled_table_cell_crop_recovers_missed_and_confused_cells():
    """On a ruled table, an empty cell with visible ink and a low-confidence
    numeric cell are re-read from their cropped cell images."""
    rows_data = [
        ["Item", "Qty", "Price"],
        ["Bolts", "100", "45.50"],
        ["Nuts", "250", "12.00"],
        ["Washers", "500", "8.75"],
    ]
    im, xs, ys = _draw_ruled_table_image(rows_data)
    # Full-page OCR missed cell (1, 1) ("100") and misread cell (2, 2) as "l2.OO" with low conf.
    initial = [
        _make_line("Item", xs[0] + 20, ys[0] + 20, xs[0] + 100, ys[0] + 50, 0.96),
        _make_line("Qty", xs[1] + 20, ys[0] + 20, xs[1] + 80, ys[0] + 50, 0.96),
        _make_line("Price", xs[2] + 20, ys[0] + 20, xs[2] + 100, ys[0] + 50, 0.96),
        _make_line("Bolts", xs[0] + 20, ys[1] + 20, xs[0] + 100, ys[1] + 50, 0.95),
        # cell (1, 1) intentionally omitted
        _make_line("45.50", xs[2] + 20, ys[1] + 20, xs[2] + 110, ys[1] + 50, 0.95),
        _make_line("Nuts", xs[0] + 20, ys[2] + 20, xs[0] + 100, ys[2] + 50, 0.95),
        _make_line("250", xs[1] + 20, ys[2] + 20, xs[1] + 90, ys[2] + 50, 0.95),
        _make_line("l2.OO", xs[2] + 20, ys[2] + 20, xs[2] + 110, ys[2] + 50, 0.65),
        _make_line("Washers", xs[0] + 20, ys[3] + 20, xs[0] + 140, ys[3] + 50, 0.95),
        _make_line("500", xs[1] + 20, ys[3] + 20, xs[1] + 90, ys[3] + 50, 0.95),
        _make_line("8.75", xs[2] + 20, ys[3] + 20, xs[2] + 100, ys[3] + 50, 0.95),
    ]

    crop_calls = []

    def fake_read_crop(crop):
        crop_calls.append(crop.size)
        if len(crop_calls) == 1:
            return [_make_line("100", 12, 12, 80, 40, 0.97)]
        return [_make_line("12.00", 12, 12, 90, 40, 0.96)]

    refined = ocrtables.refine_tables(im, initial, read_crop_fn=fake_read_crop)
    assert len(crop_calls) == 2
    assert [l.text for l in refined] == [
        "Item", "Qty", "Price",
        "Bolts", "100", "45.50",
        "Nuts", "250", "12.00",
        "Washers", "500", "8.75",
    ]


def test_scanned_numeric_table_exports_to_excel_exact(tmp_path, monkeypatch):
    """End-to-end: OCR a scanned numeric table with O/0, l/1, S/5 confusions,
    then export the OCR'd PDF to Excel via tables.to_excel() with every number exact."""
    rows_data = [
        ["Account", "Units", "Balance"],
        ["North", "100", "1,042.50"],
        ["South", "500", "2,150.00"],
        ["West", "20", "85.25"],
    ]
    im, xs, ys = _draw_ruled_table_image(rows_data)
    img_path = tmp_path / "table_scan.png"
    im.save(img_path)
    scan_pdf = core.images_to_pdf([img_path], tmp_path / "scan.pdf")
    ocr_pdf = tmp_path / "ocr.pdf"
    xlsx_path = tmp_path / "out.xlsx"

    # Simulate initial full-page OCR reading with classic digit confusions.
    confused = [
        ("Account", 0, 0, 0.97), ("Units", 0, 1, 0.97), ("Balance", 0, 2, 0.97),
        ("North", 1, 0, 0.96), ("lOO", 1, 1, 0.88), ("1,O42.50", 1, 2, 0.87),
        ("South", 2, 0, 0.96), ("S00", 2, 1, 0.88), ("2,l50.00", 2, 2, 0.89),
        ("West", 3, 0, 0.96), ("2O", 3, 1, 0.88), ("8S.25", 3, 2, 0.88),
    ]

    def fake_read(image, script=None, quality="standard"):
        # Only return full-page boxes on the full-page render (cell crops are short strips).
        if image.height < 800:
            return types.SimpleNamespace(txts=[], boxes=[], scores=[], word_results=[])
        sx = image.width / im.width
        sy = image.height / im.height
        txts, boxes, scores = [], [], []
        for text, r, c, conf in confused:
            x0 = (xs[c] + 18) * sx
            y0 = (ys[r] + 22) * sy
            x1 = (xs[c] + 18 + max(40, len(text) * 14)) * sx
            y1 = (ys[r] + 48) * sy
            txts.append(text)
            boxes.append([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
            scores.append(conf)
        return types.SimpleNamespace(txts=txts, boxes=boxes, scores=scores, word_results=None)

    monkeypatch.setattr(ocr, "_read", fake_read)
    ocr._run_builtin(scan_pdf, ocr_pdf, force=False, deskew=False, rotate=False)

    # Export to Excel via tables.to_excel() and verify every cell & number is exact!
    tables.to_excel(ocr_pdf, xlsx_path)
    wb = openpyxl.load_workbook(xlsx_path)
    assert wb.sheetnames == ["Summary", "p1 table 1"]
    ws = wb["p1 table 1"]
    exported = [[cell.value for cell in row] for row in ws.iter_rows()]
    assert exported == [
        ["Account", "Units", "Balance"],
        ["North", 100, 1042.5],
        ["South", 500, 2150.0],
        ["West", 20, 85.25],
    ]
