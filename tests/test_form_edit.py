# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.37.0 (forms.py): the fields a form already has can be changed: moved,
resized, renamed, deleted, and their settings (required, read-only,
tooltip, one line or several, a dropdown's choices) set. Checked on forms
ZPDF made and on forms shaped the way other programs write them (one field
shown in several places, names in a tree, settings on a parent)."""

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject,
                           TextStringObject)
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from pdfmanager import core, forms


def blank(path, pages=1):
    c = canvas.Canvas(str(path), pagesize=A4)
    for n in range(pages):
        c.drawString(72, 800, f"Page {n + 1}")
        c.showPage()
    c.save()
    return path


def made(tmp_path):
    """A form made by ZPDF: two prices, a total of them, a tick box, a pair
    of radio buttons, a dropdown."""
    out = tmp_path / "form.pdf"
    forms.add_fields(blank(tmp_path / "blank.pdf", 2), out, [
        {"page": 1, "rect": [72, 700, 200, 720], "type": "text", "name": "Price"},
        {"page": 1, "rect": [72, 670, 200, 690], "type": "text", "name": "Tax"},
        {"page": 1, "rect": [72, 640, 200, 660], "type": "text", "name": "Total",
         "calculate": "sum: Price, Tax"},
        {"page": 1, "rect": [72, 600, 86, 614], "type": "checkbox", "name": "Agree"},
        {"page": 1, "rect": [72, 570, 86, 584], "type": "radio", "name": "Size", "value": "Small"},
        {"page": 1, "rect": [100, 570, 114, 584], "type": "radio", "name": "Size", "value": "Large"},
        {"page": 2, "rect": [72, 700, 200, 720], "type": "dropdown", "name": "Country",
         "options": ["France", "Spain"]},
    ])
    return out


def widget(path, page, index):
    widgets = [a.get_object() for a in PdfReader(str(path)).pages[page - 1].get("/Annots") or []
               if a.get_object().get("/Subtype") == "/Widget"]
    return widgets[index]


def by_name(path):
    return {f["name"]: f for fields in forms.field_details(path).values() for f in fields}


def test_details_of_a_made_form(tmp_path):
    src = made(tmp_path)
    details = forms.field_details(src)
    assert [f["index"] for f in details[1]] == [0, 1, 2, 3, 4, 5]
    assert [f["name"] for f in details[1]] == ["Price", "Tax", "Total", "Agree", "Size", "Size"]
    total = details[1][2]
    assert total["calculated"] and total["readonly"] and not total["required"]
    assert details[1][0]["used_by"] == ["Total"] and details[1][3]["used_by"] == []
    assert details[1][4]["widgets"] == 2 and details[1][0]["widgets"] == 1
    assert details[2][0]["options"] == ["France", "Spain"] and details[2][0]["type"] == "dropdown"
    assert details[1][0]["tooltip"] == "Price"
    # the short list is as it was
    assert forms.existing_fields(src)[2] == [
        {"rect": [72.0, 700.0, 200.0, 720.0], "name": "Country", "type": "dropdown"}]


def test_move_keeps_the_look_and_resize_remakes_it(tmp_path):
    src, out = made(tmp_path), tmp_path / "out.pdf"
    before = widget(src, 1, 0)["/AP"]["/N"].get_data()
    result = forms.edit_fields(src, out, [
        {"page": 1, "index": 0, "rect": [300, 500, 428, 520]},          # moved, same size
        {"page": 1, "index": 1, "rect": [300, 400, 500, 450]},          # bigger
        {"page": 1, "index": 3, "rect": [72, 590, 102, 620]},           # a bigger tick box
    ])
    assert result["changed"] == 3 and result["deleted"] == 0
    moved = widget(out, 1, 0)
    assert [float(v) for v in moved["/Rect"]] == [300, 500, 428, 520]
    assert moved["/AP"]["/N"].get_data() == before
    bigger = widget(out, 1, 1)
    assert [float(v) for v in bigger["/AP"]["/N"]["/BBox"]] == [0, 0, 200, 50]
    assert b"199.00 49.00 re" in bigger["/AP"]["/N"].get_data()         # its border, the new size
    box = widget(out, 1, 3)
    assert set(box["/AP"]["/N"]) == {"/Yes", "/Off"}
    assert [float(v) for v in box["/AP"]["/N"]["/Yes"]["/BBox"]] == [0, 0, 30, 30]
    assert b"/ZaDb 24.00 Tf" in box["/AP"]["/N"]["/Yes"].get_data()
    # the form still fills in
    filled = PdfWriter(clone_from=str(out))
    filled.update_page_form_field_values(filled.pages[0], {"Price": "3", "Agree": "/Yes"},
                                         auto_regenerate=False)
    filled.write(tmp_path / "filled.pdf")
    values = PdfReader(str(tmp_path / "filled.pdf")).get_fields()
    assert values["Price"]["/V"] == "3" and values["Agree"]["/V"] == "/Yes"


def test_a_resized_radio_button_stays_round(tmp_path):
    src, out = made(tmp_path), tmp_path / "out.pdf"
    forms.edit_fields(src, out, [{"page": 1, "index": 5, "rect": [100, 560, 130, 590]}])
    button = widget(out, 1, 5)
    assert set(button["/AP"]["/N"]) == {"/Large", "/Off"}
    on = button["/AP"]["/N"]["/Large"]
    assert [float(v) for v in on["/BBox"]] == [0, 0, 30, 30] and b" c f" in on.get_data()
    assert {"/Small", "/Large"} <= set(PdfReader(str(out)).get_fields()["Size"]["/_States_"])


def test_resizing_a_filled_box_asks_readers_to_redraw_it(tmp_path):
    src = made(tmp_path)
    filled = PdfWriter(clone_from=str(src))
    filled.update_page_form_field_values(filled.pages[0], {"Price": "12.50"}, auto_regenerate=False)
    filled.write(tmp_path / "filled.pdf")
    out = tmp_path / "out.pdf"
    forms.edit_fields(tmp_path / "filled.pdf", out, [{"page": 1, "index": 0, "rect": [72, 700, 300, 730]}])
    reader = PdfReader(str(out))
    assert reader.trailer["/Root"]["/AcroForm"]["/NeedAppearances"] is True or \
        bool(reader.trailer["/Root"]["/AcroForm"]["/NeedAppearances"])
    assert reader.get_fields()["Price"]["/V"] == "12.50"


def test_rename_follows_into_calculations(tmp_path):
    src, out = made(tmp_path), tmp_path / "out.pdf"
    result = forms.edit_fields(src, out, [
        {"page": 1, "index": 0, "name": "Net price"},
        {"page": 1, "index": 1, "name": "Total"},          # taken: becomes "Total 2"
    ])
    assert result["names"] == {"Price": "Net price", "Tax": "Total 2"}
    fields = PdfReader(str(out)).get_fields()
    assert set(fields) == {"Net price", "Total 2", "Total", "Agree", "Size", "Country"}
    script = widget(out, 1, 2)["/AA"]["/C"]["/JS"]
    assert script == 'AFSimple_Calculate("SUM", new Array("Net price", "Total 2"));'
    assert widget(out, 1, 0)["/TU"] == "Net price"          # the tooltip was the name: it follows
    assert by_name(out)["Net price"]["used_by"] == ["Total"]


def test_rename_a_radio_group_once(tmp_path):
    src, out = made(tmp_path), tmp_path / "out.pdf"
    result = forms.edit_fields(src, out, [{"page": 1, "index": 4, "name": "Shirt size"},
                                          {"page": 1, "index": 5, "name": "Shirt size"}])
    assert result["names"] == {"Size": "Shirt size"}
    assert "Shirt size" in PdfReader(str(out)).get_fields()


def test_delete(tmp_path):
    src, out = made(tmp_path), tmp_path / "out.pdf"
    result = forms.edit_fields(src, out, [
        {"page": 1, "index": 0, "delete": True},        # Price: Total used it
        {"page": 1, "index": 4, "delete": True},        # one of the two radio buttons
        {"page": 2, "index": 0, "delete": True},
    ])
    assert result["deleted"] == 3
    assert any("Total" in note and "Price" in note for note in result["notes"])
    reader = PdfReader(str(out))
    assert set(reader.get_fields()) == {"Tax", "Total", "Agree", "Size"}
    assert "/Annots" not in reader.pages[1] or len(reader.pages[1]["/Annots"]) == 0
    assert len(reader.pages[0]["/Annots"]) == 4
    assert widget(out, 1, 1)["/AA"]["/C"]["/JS"] == 'AFSimple_Calculate("SUM", new Array("Tax"));'
    size = next(f for f in reader.trailer["/Root"]["/AcroForm"]["/Fields"]
                if f.get_object().get("/T") == "Size").get_object()
    assert len(size["/Kids"]) == 1


def test_deleting_everything_a_total_adds_up_frees_the_total(tmp_path):
    src, out = made(tmp_path), tmp_path / "out.pdf"
    forms.edit_fields(src, out, [{"page": 1, "index": 0, "delete": True},
                                 {"page": 1, "index": 1, "delete": True}])
    total = widget(out, 1, 0)
    assert "/C" not in total.get("/AA", {})
    assert not int(total.get("/Ff", 0)) & forms.READ_ONLY
    acroform = PdfReader(str(out)).trailer["/Root"]["/AcroForm"]
    assert len(acroform.get("/CO", [])) == 0


def test_deleting_a_calculated_field_leaves_no_order_entry(tmp_path):
    src, out = made(tmp_path), tmp_path / "out.pdf"
    forms.edit_fields(src, out, [{"page": 1, "index": 2, "delete": True}])
    reader = PdfReader(str(out))
    assert len(reader.trailer["/Root"]["/AcroForm"].get("/CO", [])) == 0
    assert set(reader.get_fields()) == {"Price", "Tax", "Agree", "Size", "Country"}
    # deleting both buttons takes the group too
    forms.edit_fields(src, out, [{"page": 1, "index": 4, "delete": True},
                                 {"page": 1, "index": 5, "delete": True}])
    assert "Size" not in PdfReader(str(out)).get_fields()


def test_settings(tmp_path):
    src, out = made(tmp_path), tmp_path / "out.pdf"
    forms.edit_fields(src, out, [
        {"page": 1, "index": 0, "required": True, "tooltip": "Before tax", "type": "multiline"},
        {"page": 1, "index": 3, "readonly": True, "tooltip": ""},
        {"page": 2, "index": 0, "options": "Italy, Spain\nPortugal"},
    ])
    fields = by_name(out)
    price = fields["Price"]
    assert price["required"] and price["tooltip"] == "Before tax" and price["type"] == "multiline"
    assert "10 Tf" in widget(out, 1, 0)["/DA"]       # several lines aren't fitted to the box
    assert fields["Agree"]["readonly"] and fields["Agree"]["tooltip"] == ""
    assert fields["Country"]["options"] == ["Italy", "Spain", "Portugal"]
    back = tmp_path / "back.pdf"
    forms.edit_fields(out, back, [{"page": 1, "index": 0, "required": False, "type": "text"}])
    again = by_name(back)["Price"]
    assert not again["required"] and again["type"] == "text"


def test_mistakes(tmp_path):
    src, out = made(tmp_path), tmp_path / "out.pdf"
    with pytest.raises(core.PdfManagerError, match="nothing to change"):
        forms.edit_fields(src, out, [])
    with pytest.raises(core.PdfManagerError, match="no field"):
        forms.edit_fields(src, out, [{"page": 1, "index": 9, "delete": True}])
    with pytest.raises(core.PdfManagerError, match="no field"):
        forms.edit_fields(src, out, [{"page": 5, "index": 0, "delete": True}])
    with pytest.raises(core.PdfManagerError, match="too small"):
        forms.edit_fields(src, out, [{"page": 1, "index": 0, "rect": [72, 700, 74, 720]}])
    with pytest.raises(core.PdfManagerError, match="can't be turned into"):
        forms.edit_fields(src, out, [{"page": 1, "index": 0, "type": "checkbox"}])
    with pytest.raises(core.PdfManagerError, match="no choices"):
        forms.edit_fields(src, out, [{"page": 2, "index": 0, "options": " , "}])
    assert not out.exists()


# -- forms shaped the way other programs write them ---------------------------------------------

def foreign(tmp_path):
    """Like a form from Acrobat: "person.name" is a field in a tree, shown
    on both pages (two widgets, no names of their own), with its type and
    flags on the parent and the text style on the form; "person.age" is a
    widget that is its own field."""
    src = blank(tmp_path / "plain.pdf", 2)
    w = PdfWriter(clone_from=str(src))

    def add(d):
        return w._add_object(DictionaryObject(d))

    def rect(*v):
        return ArrayObject(FloatObject(x) for x in v)

    look = DictionaryObject()
    person = add({NameObject("/T"): TextStringObject("person"), NameObject("/Kids"): ArrayObject()})
    name = add({NameObject("/T"): TextStringObject("name"), NameObject("/FT"): NameObject("/Tx"),
                NameObject("/Ff"): NumberObject(forms.REQUIRED), NameObject("/Parent"): person,
                NameObject("/V"): TextStringObject("Ada"), NameObject("/Kids"): ArrayObject()})
    kids = []
    for page, y in ((0, 700), (1, 600)):
        kid = add({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
                   NameObject("/Rect"): rect(72, y, 272, y + 20), NameObject("/Parent"): name,
                   NameObject("/P"): w.pages[page].indirect_reference,
                   NameObject("/MK"): DictionaryObject({NameObject("/BC"): rect(0, 0, 1)})})
        kids.append(kid)
        w.pages[page][NameObject("/Annots")] = ArrayObject([kid])
    name.get_object()["/Kids"].extend(kids)
    age = add({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
               NameObject("/T"): TextStringObject("age"), NameObject("/FT"): NameObject("/Tx"),
               NameObject("/Rect"): rect(72, 650, 172, 670), NameObject("/Parent"): person,
               NameObject("/P"): w.pages[0].indirect_reference})
    w.pages[0]["/Annots"].append(age)
    person.get_object()["/Kids"].extend([name, age])
    w._root_object[NameObject("/AcroForm")] = add({
        NameObject("/Fields"): ArrayObject([person]),
        NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g"),
        NameObject("/DR"): look})
    out = tmp_path / "foreign.pdf"
    w.write(str(out))
    return out


def test_details_of_a_foreign_form(tmp_path):
    src = foreign(tmp_path)
    details = forms.field_details(src)
    first = details[1][0]
    assert first["name"] == "person.name" and first["required"] and first["value"] == "Ada"
    assert first["widgets"] == 2 and details[2][0]["name"] == "person.name"
    assert details[1][1]["name"] == "person.age" and details[1][1]["widgets"] == 1


def test_edit_a_foreign_form(tmp_path):
    src, out = foreign(tmp_path), tmp_path / "out.pdf"
    result = forms.edit_fields(src, out, [
        {"page": 1, "index": 0, "name": "full name", "required": False,
         "rect": [72, 700, 372, 730]},
        {"page": 1, "index": 1, "name": "full name"},     # taken beside it: "full name 2"
    ])
    assert result["names"] == {"person.name": "person.full name", "person.age": "person.full name 2"}
    reader = PdfReader(str(out))
    assert set(reader.get_fields()) >= {"person.full name", "person.full name 2"}
    details = forms.field_details(out)
    assert details[2][0]["name"] == "person.full name" and not details[2][0]["required"]
    assert details[2][0]["rect"] == [72.0, 600.0, 272.0, 620.0]       # the other place stays put
    resized = widget(out, 1, 0)
    assert [float(v) for v in resized["/AP"]["/N"]["/BBox"]] == [0, 0, 300, 30]
    assert b"0 0 1 RG" in resized["/AP"]["/N"].get_data()              # its own border colour
    assert reader.trailer["/Root"]["/AcroForm"]["/NeedAppearances"]    # "Ada" is redrawn


def test_delete_in_a_foreign_form(tmp_path):
    src, out = foreign(tmp_path), tmp_path / "out.pdf"
    forms.edit_fields(src, out, [{"page": 1, "index": 0, "delete": True}])
    details = forms.field_details(out)
    assert [f["name"] for f in details[1]] == ["person.age"]
    assert details[2][0]["name"] == "person.name" and details[2][0]["widgets"] == 1
    # the last place it's shown takes the field, and an emptied branch goes too
    forms.edit_fields(src, out, [{"page": 1, "index": 0, "delete": True},
                                 {"page": 2, "index": 0, "delete": True},
                                 {"page": 1, "index": 1, "delete": True}])
    reader = PdfReader(str(out))
    assert not reader.get_fields()
    assert len(reader.trailer["/Root"]["/AcroForm"]["/Fields"]) == 0


def test_a_signed_file_gets_a_note(tmp_path):
    src = made(tmp_path)
    w = PdfWriter(clone_from=str(src))
    w.pages[0]["/Annots"].append(w._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/FT"): NameObject("/Sig"), NameObject("/T"): TextStringObject("Sig"),
        NameObject("/Rect"): ArrayObject(FloatObject(v) for v in (300, 100, 400, 150)),
        NameObject("/V"): DictionaryObject({NameObject("/Type"): NameObject("/Sig")})})))
    w._root_object["/AcroForm"]["/Fields"].append(w.pages[0]["/Annots"][-1])
    signed = tmp_path / "signed.pdf"
    w.write(str(signed))
    assert forms.field_details(signed)[1][-1]["signed"]
    result = forms.edit_fields(signed, tmp_path / "out.pdf", [{"page": 1, "index": 0, "tooltip": "x"}])
    assert any("signed" in note for note in result["notes"])


def test_xfa_forms_are_refused(tmp_path):
    src = made(tmp_path)
    w = PdfWriter(clone_from=str(src))
    w._root_object["/AcroForm"][NameObject("/XFA")] = ArrayObject()
    w.write(str(tmp_path / "xfa.pdf"))
    with pytest.raises(core.PdfManagerError, match="XFA"):
        forms.edit_fields(tmp_path / "xfa.pdf", tmp_path / "out.pdf", [{"page": 1, "index": 0, "delete": True}])


# -- the command line and the app ----------------------------------------------------------------

def test_command_line(tmp_path, capsys):
    import json
    from pdfmanager.cli import main

    src, out = made(tmp_path), tmp_path / "cli.pdf"
    changes = tmp_path / "changes.json"
    changes.write_text(json.dumps([{"page": 1, "index": 0, "name": "Net"},
                                   {"page": 2, "index": 0, "delete": True}]))
    assert main(["form", str(src), "-o", str(out), "--edit", str(changes)]) == 0
    assert "changed 1, deleted 1" in capsys.readouterr().out
    assert set(PdfReader(str(out)).get_fields()) == {"Net", "Tax", "Total", "Agree", "Size"}
    assert main(["form", str(src), "-o", str(out), "--edit", str(changes), "--detect"]) != 0


def test_the_form_tool_changes_then_adds(tmp_path):
    import json
    from pdfmanager import gui

    src = made(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    notes = []
    opts = {"edits": json.dumps([{"page": 1, "index": 0, "name": "Net"},
                                 {"page": 1, "index": 3, "delete": True}]),
            "fields": json.dumps({"2": [{"rect": [72, 600, 200, 620], "type": "text", "name": "Extra"}]}),
            "border": "on"}
    outputs = gui._run_file_op("form", src, out_dir, opts, None, [], notes)
    assert [p.name for p in out_dir.iterdir()] == [outputs[0].name]       # nothing left behind
    assert set(PdfReader(str(outputs[0])).get_fields()) == {"Net", "Tax", "Total", "Size", "Country", "Extra"}
    assert any("changed 1 and deleted 1" in n for n in notes) and any("added 1 field" in n for n in notes)
    # changes alone are a result too
    notes = []
    only = gui._run_file_op("form", src, out_dir, {"edits": opts["edits"], "fields": ""}, None, [], notes)
    assert set(PdfReader(str(only[0])).get_fields()) == {"Net", "Tax", "Total", "Size", "Country"}
    with pytest.raises(gui.UserError, match="damaged"):
        gui._run_file_op("form", src, out_dir, {"edits": "{}"}, None, [], [])


def test_an_encrypted_form(tmp_path):
    src = made(tmp_path)
    locked = tmp_path / "locked.pdf"
    w = PdfWriter(clone_from=str(src))
    w.encrypt("secret")
    w.write(str(locked))
    assert forms.field_details(locked, "secret")[1][0]["name"] == "Price"
    out = tmp_path / "out.pdf"
    forms.edit_fields(locked, out, [{"page": 1, "index": 0, "name": "Net"}], password="secret")
    reader = PdfReader(str(out))
    if reader.is_encrypted:
        reader.decrypt("secret")
    assert "Net" in reader.get_fields()
