# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Form field formats, totals and checks (Acrobat's form scripts), 0.13.0."""

import pytest
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from pdfmanager import core, forms


def blank(path):
    c = canvas.Canvas(str(path))
    c.drawString(72, 700, "Invoice")
    c.save()
    return path


def scripts(path):
    out = {}
    for a in PdfReader(path).pages[0]["/Annots"]:
        o = a.get_object()
        out[str(o["/T"])] = ({k: v.get_object()["/JS"] for k, v in o.get("/AA", {}).items()},
                             int(o.get("/Ff", 0)))
    return out


def test_formats_totals_and_checks(tmp_path):
    src = blank(tmp_path / "a.pdf")
    forms.add_fields(src, tmp_path / "f.pdf", [
        {"page": 1, "type": "text", "name": "Price", "rect": [100, 600, 200, 620], "format": "currency",
         "currency": "€"},
        {"page": 1, "type": "text", "name": "Tax", "rect": [100, 570, 200, 590], "format": "amount",
         "separators": "1.234,56", "min": 0, "max": 1000},
        {"page": 1, "type": "text", "name": "Subtotal", "rect": [100, 540, 200, 560],
         "format": "number", "decimals": 1, "calculate": {"op": "sum", "fields": ["Price", "Tax"]}},
        {"page": 1, "type": "text", "name": "Total", "rect": [100, 510, 200, 530],
         "calculate": "max: Subtotal, Price"},
        {"page": 1, "type": "text", "name": "Rate", "rect": [100, 480, 200, 500], "format": "percent"},
        {"page": 1, "type": "text", "name": "When", "rect": [100, 450, 200, 470], "format": "date",
         "date": "yyyy-mm-dd"},
        {"page": 1, "type": "text", "name": "Phone", "rect": [100, 420, 200, 440], "format": "phone"},
        {"page": 1, "type": "text", "name": "ZIP", "rect": [100, 390, 200, 410], "format": "zip"},
    ])
    s = scripts(tmp_path / "f.pdf")
    assert s["Price"][0] == {"/F": 'AFNumber_Format(2, 0, 0, 0, "\\u20ac", true);',
                             "/K": 'AFNumber_Keystroke(2, 0, 0, 0, "\\u20ac", true);'}
    assert s["Tax"][0]["/F"] == 'AFNumber_Format(2, 2, 0, 0, "", true);'
    assert s["Tax"][0]["/V"] == "AFRange_Validate(true, 0, true, 1000);"
    assert s["Subtotal"][0]["/C"] == 'AFSimple_Calculate("SUM", new Array("Price", "Tax"));'
    assert s["Subtotal"][0]["/F"].startswith("AFNumber_Format(1, ")
    assert s["Subtotal"][1] & forms.READ_ONLY and not s["Price"][1] & forms.READ_ONLY
    assert s["Total"][0] == {"/C": 'AFSimple_Calculate("MAX", new Array("Subtotal", "Price"));'}
    assert s["Rate"][0]["/F"] == "AFPercent_Format(0, 0);"
    assert s["When"][0]["/K"] == 'AFDate_KeystrokeEx("yyyy-mm-dd");'
    assert s["Phone"][0]["/F"] == "AFSpecial_Format(2);" and s["ZIP"][0]["/F"] == "AFSpecial_Format(0);"
    # Calculated in order: the subtotal before the total that uses it.
    reader = PdfReader(tmp_path / "f.pdf")
    order = [str(r.get_object()["/T"]) for r in reader.trailer["/Root"]["/AcroForm"]["/CO"]]
    assert order == ["Subtotal", "Total"]


def test_format_mistakes(tmp_path):
    src = blank(tmp_path / "a.pdf")
    bad = [({"format": "roman"}, "unknown format"), ({"format": "date", "date": "<script>"}, "date pattern"),
           ({"calculate": "median: A"}, "unknown calculation"), ({"calculate": "sum:"}, "say which fields"),
           ({"min": "ten"}, "must be numbers")]
    for extra, message in bad:
        with pytest.raises(core.PdfManagerError, match=message):
            forms.add_fields(src, tmp_path / "x.pdf", [{"page": 1, "type": "text", "name": "A",
                                                        "rect": [100, 600, 200, 620], **extra}])
