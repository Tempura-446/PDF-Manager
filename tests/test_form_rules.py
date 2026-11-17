# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.56.0 (forms.py): what a text box a form already has takes (a number,
money, a date, a smallest and largest value) and how it's worked out (a
total of other fields) is read back from Acrobat's form scripts, and can be
changed. Checked on forms ZPDF made and on scripts written the way other
programs write them."""

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from pdfmanager import core, forms


def blank(path):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawString(72, 800, "Order")
    c.showPage()
    c.save()
    return path


def made(tmp_path, extra=()):
    out = tmp_path / "form.pdf"
    forms.add_fields(blank(tmp_path / "blank.pdf"), out, [
        {"page": 1, "rect": [72, 700, 200, 720], "type": "text", "name": "Price", "format": "amount"},
        {"page": 1, "rect": [72, 670, 200, 690], "type": "text", "name": "Tax", "format": "currency",
         "currency": "$", "min": 0, "max": 500},
        {"page": 1, "rect": [72, 640, 200, 660], "type": "text", "name": "Total",
         "calculate": "sum: Price, Tax"},
        {"page": 1, "rect": [72, 610, 200, 630], "type": "text", "name": "When", "format": "date",
         "date": "yyyy-mm-dd"},
        {"page": 1, "rect": [72, 580, 200, 600], "type": "text", "name": "Notes"},
        {"page": 1, "rect": [72, 550, 86, 564], "type": "checkbox", "name": "Agree"},
        *extra,
    ])
    return out


def details(path):
    return {f["name"]: f for f in forms.field_details(path)[1]}


def field(path, name):
    for annot in PdfReader(str(path)).pages[0]["/Annots"]:
        annot = annot.get_object()
        if annot.get("/T") == name:
            return annot
    raise AssertionError(name)


def script(path, name, key):
    return str(field(path, name)["/AA"][key]["/JS"])


def order(path):
    form = PdfReader(str(path)).trailer["/Root"]["/AcroForm"]
    return [str(ref.get_object()["/T"]) for ref in form.get("/CO", [])]


def test_what_a_form_made_here_takes_is_read_back(tmp_path):
    d = details(made(tmp_path))
    assert d["Price"]["format"] == {"format": "amount", "decimals": 2, "separators": "1,234.56"}
    assert d["Tax"]["format"] == {"format": "currency", "decimals": 2, "separators": "1,234.56",
                                  "currency": "$", "currency_after": False}
    assert (d["Tax"]["min"], d["Tax"]["max"]) == (0, 500)
    assert d["When"]["format"] == {"format": "date", "date": "yyyy-mm-dd"}
    assert d["Notes"]["format"] is None and d["Notes"]["calculate"] is None
    assert (d["Notes"]["min"], d["Notes"]["max"]) == (None, None)
    assert d["Total"]["calculate"] == {"op": "sum", "fields": ["Price", "Tax"]}
    assert d["Agree"]["format"] is None and d["Agree"]["calculate"] is None


@pytest.mark.parametrize("written, read", [
    ('AFNumber_Format(0, 0, 0, 0, "", true);', {"format": "number", "decimals": 0, "separators": "1,234.56"}),
    ('AFNumber_Format(3,2,1,0,"",true)', {"format": "amount", "decimals": 3, "separators": "1.234,56"}),
    ('AFNumber_Format(2, 0, 0, 0, " \\u20ac", false);',
     {"format": "currency", "decimals": 2, "separators": "1,234.56", "currency": " €", "currency_after": True}),
    ("AFPercent_Format(1, 2);", {"format": "percent", "decimals": 1, "separators": "1.234,56"}),
    ("AFPercent_Format(0, 0, false);", {"format": "percent", "decimals": 0, "separators": "1,234.56"}),
    ('AFDate_FormatEx("d mmm yyyy");', {"format": "date", "date": "d mmm yyyy"}),
    ("AFSpecial_Format(0);", {"format": "zip"}),
    ("AFSpecial_Format(2);", {"format": "phone"}),
    ("AFSpecial_Format(3);", {"format": "custom"}),                      # (a social security number)
    ("event.value = util.printf('%.2f', event.value);", {"format": "custom"}),
])
def test_formats_other_programs_write(tmp_path, written, read):
    source = made(tmp_path)
    writer = PdfWriter(clone_from=str(source))
    for annot in writer.pages[0]["/Annots"]:
        annot = annot.get_object()
        if annot.get("/T") == "Notes":
            annot[NameObject("/AA")] = DictionaryObject({NameObject("/F"): DictionaryObject({
                NameObject("/S"): NameObject("/JavaScript"), NameObject("/JS"): TextStringObject(written)})})
    out = tmp_path / "other.pdf"
    writer.write(str(out))
    assert details(out)["Notes"]["format"] == read


def test_a_total_of_its_own_script_is_custom(tmp_path):
    source = made(tmp_path)
    writer = PdfWriter(clone_from=str(source))
    for annot in writer.pages[0]["/Annots"]:
        annot = annot.get_object()
        if annot.get("/T") == "Total":
            annot["/AA"]["/C"][NameObject("/JS")] = TextStringObject(
                'event.value = this.getField("Price").value * 2;')
    out = tmp_path / "other.pdf"
    writer.write(str(out))
    assert details(out)["Total"]["calculate"] == "custom"
    assert details(out)["Total"]["calculated"] is True


def test_a_format_is_changed_and_taken_away(tmp_path):
    source, out = made(tmp_path), tmp_path / "out.pdf"
    d = details(source)
    result = forms.edit_fields(source, out, [
        {"page": 1, "index": d["Price"]["index"], "format": "date", "date": "dd/mm/yyyy"},
        {"page": 1, "index": d["When"]["index"], "format": ""},
        {"page": 1, "index": d["Notes"]["index"], "format": "percent", "decimals": 1},
    ])
    assert result["changed"] == 3
    after = details(out)
    assert after["Price"]["format"] == {"format": "date", "date": "dd/mm/yyyy"}
    assert script(out, "Price", "/K") == 'AFDate_KeystrokeEx("dd/mm/yyyy");'
    assert after["When"]["format"] is None and "/AA" not in field(out, "When")
    assert after["Notes"]["format"] == {"format": "percent", "decimals": 1, "separators": "1,234.56"}
    # What wasn't asked about stays: Tax's money and limits, Total's sum.
    assert after["Tax"] == details(source)["Tax"]
    assert after["Total"]["calculate"] == {"op": "sum", "fields": ["Price", "Tax"]}


def test_limits_are_set_one_at_a_time_and_taken_away(tmp_path):
    source, out, again = made(tmp_path), tmp_path / "out.pdf", tmp_path / "again.pdf"
    index = details(source)["Tax"]["index"]
    forms.edit_fields(source, out, [{"page": 1, "index": index, "max": 90}])
    assert (details(out)["Tax"]["min"], details(out)["Tax"]["max"]) == (0, 90)
    assert details(out)["Tax"]["format"]["format"] == "currency"      # (its format is left alone)
    forms.edit_fields(out, again, [{"page": 1, "index": index, "min": "", "max": ""}])
    assert (details(again)["Tax"]["min"], details(again)["Tax"]["max"]) == (None, None)
    assert "/V" not in field(again, "Tax")["/AA"]
    with pytest.raises(core.PdfManagerError, match="must be numbers"):
        forms.edit_fields(source, out, [{"page": 1, "index": index, "min": "few"}])


def test_a_total_is_set_changed_and_taken_away(tmp_path):
    source, out, again = made(tmp_path), tmp_path / "out.pdf", tmp_path / "again.pdf"
    d = details(source)
    forms.edit_fields(source, out, [
        {"page": 1, "index": d["Total"]["index"], "calculate": {"op": "product", "fields": ["Price", "Tax"]}},
        {"page": 1, "index": d["Notes"]["index"], "calculate": "sum: Total, Price"},
    ])
    after = details(out)
    assert after["Total"]["calculate"] == {"op": "product", "fields": ["Price", "Tax"]}
    assert after["Notes"]["calculate"] == {"op": "sum", "fields": ["Total", "Price"]}
    assert after["Notes"]["readonly"] is True                 # worked out, not typed
    assert order(out) == ["Total", "Notes"]                   # each after what it uses
    assert after["Price"]["used_by"] == ["Total", "Notes"]

    result = forms.edit_fields(out, again, [{"page": 1, "index": d["Total"]["index"], "calculate": ""}])
    assert result["changed"] == 1
    last = details(again)
    assert last["Total"]["calculate"] is None and last["Total"]["readonly"] is False
    assert order(again) == ["Notes"]
    forms.edit_fields(again, out, [{"page": 1, "index": d["Notes"]["index"], "calculate": ""}])
    assert "/CO" not in PdfReader(str(out)).trailer["/Root"]["/AcroForm"]


def test_the_order_follows_what_uses_what(tmp_path):
    """Price becomes a total of Tax and Notes, and Total already adds up
    Price: Price is worked out first."""
    source, out = made(tmp_path), tmp_path / "out.pdf"
    d = details(source)
    forms.edit_fields(source, out, [{"page": 1, "index": d["Price"]["index"], "calculate": "sum: Tax, Notes"}])
    assert order(out) == ["Price", "Total"]
    # Two that use each other don't hang it.
    forms.edit_fields(out, tmp_path / "loop.pdf", [
        {"page": 1, "index": d["Tax"]["index"], "calculate": "sum: Total"}])
    assert sorted(order(tmp_path / "loop.pdf")) == ["Price", "Tax", "Total"]


def test_what_cant_be(tmp_path):
    source, out = made(tmp_path), tmp_path / "out.pdf"
    d = details(source)
    with pytest.raises(core.PdfManagerError, match="only a text box"):
        forms.edit_fields(source, out, [{"page": 1, "index": d["Agree"]["index"], "format": "number"}])
    with pytest.raises(core.PdfManagerError, match="unknown format"):
        forms.edit_fields(source, out, [{"page": 1, "index": d["Notes"]["index"], "format": "roman"}])
    with pytest.raises(core.PdfManagerError, match="from itself"):
        forms.edit_fields(source, out, [{"page": 1, "index": d["Total"]["index"], "calculate": "sum: Total, Tax"}])
    result = forms.edit_fields(source, out, [{"page": 1, "index": d["Notes"]["index"], "calculate": "sum: Price, Shipping"}])
    assert any("Shipping" in note and "doesn't have" in note for note in result["notes"])


def test_a_renamed_field_keeps_its_place_in_a_new_total(tmp_path):
    source, out = made(tmp_path), tmp_path / "out.pdf"
    d = details(source)
    forms.edit_fields(source, out, [
        {"page": 1, "index": d["Notes"]["index"], "calculate": "average: Price, Tax"},
        {"page": 1, "index": d["Price"]["index"], "name": "Net"},
    ])
    after = details(out)
    assert after["Notes"]["calculate"] == {"op": "average", "fields": ["Net", "Tax"]}
    assert after["Total"]["calculate"] == {"op": "sum", "fields": ["Net", "Tax"]}
