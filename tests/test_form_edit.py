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


# -- 0.54.0: a field moved to another page ---------------------------------------------------

def test_a_field_goes_to_another_page(tmp_path):
    src, out = made(tmp_path), tmp_path / "out.pdf"
    look = widget(src, 1, 0)["/AP"]["/N"].get_data()
    result = forms.edit_fields(src, out, [{"page": 1, "index": 0, "to_page": 2}])
    assert result["changed"] == 1 and result["notes"] == []
    found = forms.field_details(out)
    assert [f["name"] for f in found[1]] == ["Tax", "Total", "Agree", "Size", "Size"]
    assert [f["name"] for f in found[2]] == ["Country", "Price"]          # last on its new page (tab order)
    moved = widget(out, 2, 1)
    reader = PdfReader(str(out))
    assert moved.raw_get("/P").idnum == reader.pages[1].indirect_reference.idnum   # it names its new page
    assert [float(v) for v in moved["/Rect"]] == [72, 700, 200, 720]      # the same place on the paper
    assert moved["/AP"]["/N"].get_data() == look                          # and the same look
    assert by_name(out)["Total"]["calculated"] and by_name(out)["Price"]["used_by"] == ["Total"]   # the total still adds it up
    assert len(reader.trailer["/Root"]["/AcroForm"]["/Fields"]) == len(
        PdfReader(str(src)).trailer["/Root"]["/AcroForm"]["/Fields"])     # the same fields, nothing doubled


def test_moved_and_placed_and_renamed_in_one_change(tmp_path):
    src, out = made(tmp_path), tmp_path / "out.pdf"
    forms.edit_fields(src, out, [{"page": 1, "index": 3, "to_page": 2, "rect": [300, 300, 320, 320], "name": "Accept"}])
    tick = by_name(out)["Accept"]
    assert tick["rect"] == [300, 300, 320, 320] and [f["name"] for f in forms.field_details(out)[2]] == ["Country", "Accept"]


def test_one_button_of_a_group_can_go_alone(tmp_path):
    src, out = made(tmp_path), tmp_path / "out.pdf"
    forms.edit_fields(src, out, [{"page": 1, "index": 5, "to_page": 2}])
    found = forms.field_details(out)
    assert [f["name"] for f in found[1]].count("Size") == 1 and [f["name"] for f in found[2]] == ["Country", "Size"]
    sizes = [f for fields in found.values() for f in fields if f["name"] == "Size"]
    assert len(sizes) == 2 and {f["type"] for f in sizes} == {"radio"}   # still one group, on two pages


def test_a_field_that_wouldnt_be_on_the_new_page_is_brought_onto_it(tmp_path):
    src = tmp_path / "two-sizes.pdf"
    c = canvas.Canvas(str(src), pagesize=A4)
    c.showPage()
    c.setPageSize((300, 200))
    c.showPage()
    c.save()
    form, out = tmp_path / "form.pdf", tmp_path / "out.pdf"
    forms.add_fields(src, form, [{"page": 1, "rect": [400, 700, 520, 720], "type": "text", "name": "Far"}])
    result = forms.edit_fields(form, out, [{"page": 1, "index": 0, "to_page": 2}])
    assert forms.field_details(out)[2][0]["rect"] == [180, 180, 300, 200]
    assert "didn't fit page 2" in result["notes"][0]


def test_a_page_that_isnt_there_is_said(tmp_path):
    src = made(tmp_path)
    for bad in (3, 0, "two"):
        with pytest.raises(core.PdfManagerError, match="this PDF has 2 pages"):
            forms.edit_fields(src, tmp_path / "no.pdf", [{"page": 1, "index": 0, "to_page": bad}])
    same = forms.edit_fields(src, tmp_path / "same.pdf", [{"page": 1, "index": 0, "to_page": 1}])
    assert same["changed"] == 0                                           # already there: nothing changes


# -- 0.55.0: a field that holds words becomes another kind that does ---------------------------

def kind_of(path, name):
    return by_name(path)[name]["type"]


def test_a_text_box_becomes_a_dropdown_and_keeps_what_fits(tmp_path):
    src, filled, out = made(tmp_path), tmp_path / "filled.pdf", tmp_path / "out.pdf"
    writer = PdfWriter(clone_from=str(src))
    writer.update_page_form_field_values(writer.pages[0], {"Price": "10"}, auto_regenerate=False)
    writer.write(str(filled))
    before = by_name(filled)["Price"]
    result = forms.edit_fields(filled, out, [{"page": 1, "index": 0, "type": "dropdown", "options": "5, 10, 20"}])
    after = by_name(out)["Price"]
    assert result["changed"] == 1 and result["notes"] == []
    assert after["type"] == "dropdown" and after["options"] == ["5", "10", "20"] and after["value"] == "10"
    assert (after["rect"], after["tooltip"], after["index"]) == (before["rect"], before["tooltip"], before["index"])
    assert by_name(out)["Total"]["calculated"] and after["used_by"] == ["Total"]      # the total still adds it up
    field = widget(out, 1, 0)
    assert field["/FT"] == "/Ch" and int(field["/Ff"]) & forms.COMBO and "/AP" in field


def test_a_value_that_isnt_a_choice_is_cleared_and_said(tmp_path):
    src, filled, out = made(tmp_path), tmp_path / "filled.pdf", tmp_path / "out.pdf"
    writer = PdfWriter(clone_from=str(src))
    writer.update_page_form_field_values(writer.pages[0], {"Price": "12.50"}, auto_regenerate=False)
    writer.write(str(filled))
    result = forms.edit_fields(filled, out, [{"page": 1, "index": 0, "type": "list", "options": ["5", "10"]}])
    assert by_name(out)["Price"]["type"] == "list" and by_name(out)["Price"]["value"] == ""
    assert "isn't one of its choices" in result["notes"][0]


def test_a_dropdown_becomes_a_list_a_text_box_and_several_lines(tmp_path):
    src = made(tmp_path)
    for wanted in ("list", "text", "multiline"):
        out = tmp_path / f"{wanted}.pdf"
        forms.edit_fields(src, out, [{"page": 2, "index": 0, "type": wanted}])
        country = by_name(out)["Country"]
        assert country["type"] == wanted and country["rect"] == by_name(src)["Country"]["rect"]
        assert country["options"] == (["France", "Spain"] if wanted == "list" else [])
        field = widget(out, 2, 0)
        assert field["/FT"] == ("/Ch" if wanted == "list" else "/Tx") and ("/Opt" in field) == (wanted == "list")
    assert int(widget(tmp_path / "multiline.pdf", 2, 0)["/Ff"]) & forms.MULTILINE
    back = tmp_path / "back.pdf"                           # there and back: a dropdown again, with new choices
    forms.edit_fields(tmp_path / "text.pdf", back, [{"page": 2, "index": 0, "type": "dropdown", "options": "Italy, Peru"}])
    assert by_name(back)["Country"]["options"] == ["Italy", "Peru"]


def test_a_list_needs_choices_and_other_kinds_cant_be_switched(tmp_path):
    src = made(tmp_path)
    with pytest.raises(core.PdfManagerError, match="needs choices to become a dropdown"):
        forms.edit_fields(src, tmp_path / "no.pdf", [{"page": 1, "index": 0, "type": "dropdown"}])
    for index, wanted in ((3, "text"), (0, "checkbox"), (4, "dropdown")):
        with pytest.raises(core.PdfManagerError, match="can't be turned into"):
            forms.edit_fields(src, tmp_path / "no.pdf", [{"page": 1, "index": index, "type": wanted}])


def test_required_and_a_new_name_go_with_the_switch(tmp_path):
    src, out = made(tmp_path), tmp_path / "out.pdf"
    forms.edit_fields(src, out, [{"page": 1, "index": 1, "type": "dropdown", "options": "0, 5", "required": True, "name": "VAT"}])
    vat = by_name(out)["VAT"]
    assert vat["type"] == "dropdown" and vat["required"] and by_name(out)["Total"]["calculated"]


def test_a_checkbox_and_a_radio_button_switch_into_one_another(tmp_path):
    src, ticked, as_radio, back, split = (
        made(tmp_path), tmp_path / "ticked.pdf", tmp_path / "radio.pdf", tmp_path / "back.pdf", tmp_path / "split.pdf")
    writer = PdfWriter(clone_from=str(src))
    writer.update_page_form_field_values(writer.pages[0], {"Agree": "/Yes", "Size": "/Large"}, auto_regenerate=False)
    writer.write(str(ticked))

    # A lone checkbox becomes a radio group of one, keeping its name, place, required flag and state.
    forms.edit_fields(ticked, as_radio, [{"page": 1, "index": 3, "type": "radio", "required": True}])
    agree = by_name(as_radio)["Agree"]
    assert agree["type"] == "radio" and agree["widgets"] == 1 and agree["required"] and agree["value"] == "Yes"
    assert agree["rect"] == by_name(src)["Agree"]["rect"]

    # A radio group of one button becomes a checkbox with its group's name.
    forms.edit_fields(as_radio, back, [{"page": 1, "index": 3, "type": "checkbox"}])
    again = by_name(back)["Agree"]
    assert again["type"] == "checkbox" and again["required"] and again["value"] == "Yes"

    # A radio group of several buttons splits into separate checkboxes named from their values.
    forms.edit_fields(ticked, split, [{"page": 1, "index": 4, "type": "checkbox"}])
    fields = by_name(split)
    assert "Size" not in fields
    assert fields["Small"]["type"] == "checkbox" and fields["Small"]["value"] == ""
    assert fields["Large"]["type"] == "checkbox" and fields["Large"]["value"] == "Yes"
    assert fields["Small"]["rect"] == [72.0, 570.0, 86.0, 584.0]
    assert fields["Large"]["rect"] == [100.0, 570.0, 114.0, 584.0]


def test_a_text_box_and_a_signature_field_switch_when_unsigned(tmp_path):
    src, sig, back = made(tmp_path), tmp_path / "sig.pdf", tmp_path / "back.pdf"
    forms.edit_fields(src, sig, [{"page": 1, "index": 0, "type": "signature"}])
    price = by_name(sig)["Price"]
    assert price["type"] == "signature" and not price["signed"] and price["rect"] == by_name(src)["Price"]["rect"]
    assert widget(sig, 1, 0)["/FT"] == "/Sig"
    assert int(PdfReader(str(sig)).trailer["/Root"]["/AcroForm"]["/SigFlags"]) & 1

    forms.edit_fields(sig, back, [{"page": 1, "index": 0, "type": "text"}])
    assert by_name(back)["Price"]["type"] == "text" and widget(back, 1, 0)["/FT"] == "/Tx"

    # A filled text box has its value cleared with a note when turned into a signature field.
    filled, cleared = tmp_path / "filled.pdf", tmp_path / "cleared.pdf"
    writer = PdfWriter(clone_from=str(src))
    writer.update_page_form_field_values(writer.pages[0], {"Price": "25"}, auto_regenerate=False)
    writer.write(str(filled))
    res = forms.edit_fields(filled, cleared, [{"page": 1, "index": 0, "type": "signature"}])
    assert by_name(cleared)["Price"]["type"] == "signature" and "starts empty" in res["notes"][0]

    # A signed signature field is refused.
    signed = tmp_path / "signed.pdf"
    w = PdfWriter(clone_from=str(sig))
    w.pages[0]["/Annots"][0].get_object()[NameObject("/V")] = DictionaryObject({NameObject("/Type"): NameObject("/Sig")})
    w.write(str(signed))
    assert by_name(signed)["Price"]["signed"]
    with pytest.raises(core.PdfManagerError, match="signed"):
        forms.edit_fields(signed, tmp_path / "no.pdf", [{"page": 1, "index": 0, "type": "text"}])


def test_format_and_range_of_an_existing_field_are_read_and_changed(tmp_path):
    src = tmp_path / "acrobat.pdf"
    forms.add_fields(blank(tmp_path / "blank.pdf"), src, [
        {"page": 1, "rect": [72, 700, 200, 720], "type": "text", "name": "Amount",
         "format": "amount", "decimals": 2, "min": 0, "max": 1000},
        {"page": 1, "rect": [72, 670, 200, 690], "type": "text", "name": "Fee",
         "format": "currency", "currency": "£", "currency_after": True, "decimals": 2},
        {"page": 1, "rect": [72, 640, 200, 660], "type": "text", "name": "Phone", "format": "phone"},
    ])
    info = by_name(src)
    assert info["Amount"]["format"] == "amount" and info["Amount"]["decimals"] == 2 and info["Amount"]["separators"]
    assert info["Amount"]["min"] == 0 and info["Amount"]["max"] == 1000
    assert info["Fee"]["format"] == "currency" and info["Fee"]["currency"] == "£" and info["Fee"]["currency_after"]
    assert info["Phone"]["format"] == "phone"

    # Change the Acrobat-made amount field's format to a date, and clear Fee's format.
    dated = tmp_path / "dated.pdf"
    forms.edit_fields(src, dated, [
        {"page": 1, "index": 0, "format": "date", "date": "yyyy-mm-dd", "min": None, "max": None},
        {"page": 1, "index": 1, "format": ""},
    ])
    after = by_name(dated)
    assert after["Amount"]["format"] == "date" and after["Amount"]["date"] == "yyyy-mm-dd"
    assert "min" not in after["Amount"] and "max" not in after["Amount"]
    assert after["Fee"]["format"] == "" and "/AA" not in widget(dated, 1, 1)

    # A custom script is reported as "custom" and left untouched unless replaced.
    custom = tmp_path / "custom.pdf"
    w = PdfWriter(clone_from=str(dated))
    w.pages[0]["/Annots"][1].get_object()[NameObject("/AA")] = DictionaryObject({
        NameObject("/F"): forms._js("event.value = event.value.toUpperCase();")})
    w.write(str(custom))
    assert by_name(custom)["Fee"]["format"] == "custom"
    untouched = tmp_path / "untouched.pdf"
    forms.edit_fields(custom, untouched, [{"page": 1, "index": 1, "required": True}])
    assert by_name(untouched)["Fee"]["format"] == "custom" and by_name(untouched)["Fee"]["required"]


def test_total_and_formula_of_an_existing_field_are_read_and_changed(tmp_path):
    src = made(tmp_path)
    info = by_name(src)
    assert info["Total"]["calculate"] == {"op": "sum", "fields": ["Price", "Tax"]}

    # Edit Total's list of fields and add a formula on Tax (Price * 0.2), checking /CO order.
    edited = tmp_path / "edited.pdf"
    forms.edit_fields(src, edited, [
        {"page": 1, "index": 1, "calculate": {"op": "formula", "formula": "Price * 0.2"}},
        {"page": 1, "index": 2, "calculate": "sum: Price, Tax"},
    ])
    after = by_name(edited)
    assert after["Tax"]["calculate"] == {"op": "formula", "formula": "Price * 0.2", "fields": ["Price"]}
    assert after["Tax"]["readonly"] and after["Price"]["used_by"] == ["Tax", "Total"]
    co = [o.get_object()["/T"] for o in PdfReader(str(edited)).trailer["/Root"]["/AcroForm"]["/CO"]]
    assert co == ["Tax", "Total"]  # Tax is worked out before Total, which adds Price and Tax

    # Clear a calculation: read-only is cleared and /CO drops the field.
    cleared = tmp_path / "cleared.pdf"
    forms.edit_fields(edited, cleared, [{"page": 1, "index": 1, "calculate": ""}])
    gone = by_name(cleared)
    assert not gone["Tax"]["calculated"] and not gone["Tax"]["readonly"]
    co2 = [o.get_object()["/T"] for o in PdfReader(str(cleared)).trailer["/Root"]["/AcroForm"]["/CO"]]
    assert co2 == ["Total"]


def test_add_list_and_button_fields(tmp_path):
    src = blank(tmp_path / "blank.pdf")
    out = tmp_path / "out.pdf"
    names = forms.add_fields(src, out, [
        {"page": 1, "rect": [72, 620, 220, 680], "type": "list", "name": "Items", "options": ["One", "Two"]},
        {"page": 1, "rect": [72, 570, 160, 596], "type": "button", "name": "Reset", "label": "Clear"},
    ])
    assert names == ["Items", "Reset"]
    info = by_name(out)
    assert info["Items"]["type"] == "list" and info["Items"]["options"] == ["One", "Two"]
    assert info["Reset"]["type"] == "button"
    btn_w = widget(out, 1, 1)
    assert str(btn_w["/MK"]["/CA"]) == "Clear"
    assert int(btn_w["/Ff"]) & forms.PUSHBUTTON


def test_field_properties_roundtrip(tmp_path):
    src = blank(tmp_path / "blank.pdf")
    added = tmp_path / "added.pdf"
    forms.add_fields(src, added, [
        {"page": 1, "rect": [72, 700, 220, 722], "type": "text", "name": "Code"},
        {"page": 1, "rect": [72, 660, 220, 682], "type": "dropdown", "name": "Country", "options": ["France", "Spain"]},
        {"page": 1, "rect": [72, 620, 90, 638], "type": "checkbox", "name": "Agree"},
        {"page": 1, "rect": [72, 580, 90, 598], "type": "radio", "name": "Size", "value": "Small"},
        {"page": 1, "rect": [110, 580, 128, 598], "type": "radio", "name": "Size", "value": "Large"},
    ])
    out = tmp_path / "edited.pdf"
    forms.edit_fields(added, out, [
        {"page": 1, "index": 0, "visibility": "noprint", "font": "Times", "font_size": 14,
         "text_color": "#112233", "fill_color": "#eef2ff", "border_color": "#334455",
         "border_width": 2, "align": "center", "default_value": "AB12", "max_length": 6,
         "comb": True, "pattern": "[A-Z]{2}\\d{2,4}"},
        {"page": 1, "index": 1, "choices": [{"label": "United Kingdom", "value": "UK"},
         {"label": "France", "value": "FR"}], "default_choice": "FR", "allow_custom": True},
        {"page": 1, "index": 2, "tick_style": "cross", "export_value": "Accepted", "default_checked": True},
        {"page": 1, "index": 4, "group": "Tier", "tick_style": "square", "default_checked": True},
    ])
    details = forms.field_details(out)[1]
    code = details[0]
    assert code["visibility"] == "noprint"
    assert code["font"] == "Times" and code["font_size"] == 14
    assert code["text_color"] == "#112233" and code["fill_color"] == "#eef2ff"
    assert code["border_color"] == "#334455" and code["border_width"] == 2
    assert code["align"] == "center" and code["default_value"] == "AB12"
    assert code["max_length"] == 6 and code["comb"] is True
    assert code["pattern"] == "[A-Z]{2}\\d{2,4}"

    country = details[1]
    assert country["choices"] == [{"label": "United Kingdom", "value": "UK"}, {"label": "France", "value": "FR"}]
    assert country["default_choice"] == "FR" and country["allow_custom"] is True

    agree = details[2]
    assert agree["tick_style"] == "cross" and agree["export_value"] == "Accepted" and agree["default_checked"] is True

    tier = details[4]
    assert tier["name"] == "Tier" and tier["group"] == "Tier"
    assert tier["tick_style"] == "square" and tier["default_checked"] is True


def test_copy_to_pages_and_make_table(tmp_path):
    raw = tmp_path / "three.pdf"
    c = canvas.Canvas(str(raw))
    for p in (1, 2, 3):
        c.drawString(72, 740, f"Page {p}")
        c.showPage()
    c.save()

    added = tmp_path / "added.pdf"
    forms.add_fields(raw, added, [
        {"page": 1, "rect": [72, 700, 172, 722], "type": "text", "name": "Header"},
        {"page": 1, "rect": [72, 620, 140, 642], "type": "text", "name": "Qty"},
    ])

    # Copy Header to pages 2-3 with same name (one shared field).
    copied_same = tmp_path / "copied_same.pdf"
    names = forms.copy_to_pages(added, copied_same, 1, 0, "2-3", same_name=True)
    assert names == ["Header", "Header"]
    d_same = forms.field_details(copied_same)
    assert d_same[1][0]["name"] == "Header" and d_same[1][0]["widgets"] == 3
    assert d_same[2][0]["name"] == "Header" and d_same[3][0]["name"] == "Header"

    # Copy Qty to page 2 with numbered name.
    copied_num = tmp_path / "copied_num.pdf"
    names2 = forms.copy_to_pages(copied_same, copied_num, 1, 1, "2", same_name=False)
    assert names2 == ["Qty 2"]
    assert forms.field_details(copied_num)[2][1]["name"] == "Qty 2"

    # Make a 2x3 table from Qty on page 1.
    tabled = tmp_path / "tabled.pdf"
    t_names = forms.make_table(added, tabled, 1, 1, 2, 3, row_gap=4, col_gap=6)
    assert t_names == ["Qty 1", "Qty 2", "Qty 3", "Qty 4", "Qty 5", "Qty 6"]
    p1 = forms.field_details(tabled)[1]
    assert [f["name"] for f in p1[1:]] == ["Qty 1", "Qty 2", "Qty 3", "Qty 4", "Qty 5", "Qty 6"]
    # Check row 1 col 2 x0 == 72 + 68 + 6 == 146
    assert abs(p1[2]["rect"][0] - 146) < 0.2
    # Check row 2 col 1 y1 == 642 - 22 - 4 == 616
    assert abs(p1[4]["rect"][3] - 616) < 0.2


def test_reorder_fields_and_tabs(tmp_path):
    raw = blank(tmp_path / "grid.pdf", 1)
    added = tmp_path / "added.pdf"
    forms.add_fields(raw, added, [
        {"page": 1, "rect": [72, 700, 172, 722], "type": "text", "name": "TopLeft"},
        {"page": 1, "rect": [220, 700, 320, 722], "type": "text", "name": "TopRight"},
        {"page": 1, "rect": [72, 640, 172, 662], "type": "text", "name": "BotLeft"},
        {"page": 1, "rect": [220, 640, 320, 662], "type": "text", "name": "BotRight"},
    ])

    # Custom order
    custom = tmp_path / "custom.pdf"
    names = forms.reorder_fields(added, custom, 1, order=[3, 1, 2, 0], mode="custom")
    assert names == ["BotRight", "TopRight", "BotLeft", "TopLeft"]
    r = PdfReader(str(custom))
    assert str(r.pages[0]["/Tabs"]) == "/A"
    assert [f["name"] for f in forms.field_details(custom)[1]] == names
    assert forms.field_details(custom)[1][0]["tab_order"] == "custom"

    # Verify pdfium reports annotations in the same custom order
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_c
        doc = pdfium.PdfDocument(str(custom))
        page = doc[0]
        pdfium_names = []
        for i in range(pdfium_c.FPDFPage_GetAnnotCount(page)):
            annot = pdfium_c.FPDFPage_GetAnnot(page, i)
            if pdfium_c.FPDFAnnot_GetSubtype(annot) == pdfium_c.FPDF_ANNOT_WIDGET:
                n = pdfium_c.FPDFAnnot_GetStringValue(annot, b"T\x00", None, 0)
                buf = (pdfium_c.c_ushort * (n // 2))()
                pdfium_c.FPDFAnnot_GetStringValue(annot, b"T\x00", buf, n)
                pdfium_names.append(bytes(buf).decode("utf-16-le").rstrip("\x00"))
            pdfium_c.FPDFPage_CloseAnnot(annot)
        doc.close()
        assert pdfium_names == names
    except ImportError:
        pass

    # Order by columns
    cols = tmp_path / "cols.pdf"
    col_names = forms.reorder_fields(custom, cols, 1, mode="cols")
    assert col_names == ["TopLeft", "BotLeft", "TopRight", "BotRight"]
    assert str(PdfReader(str(cols)).pages[0]["/Tabs"]) == "/C"
    assert forms.field_details(cols)[1][0]["tab_order"] == "cols"

    # Order by rows
    rows_pdf = tmp_path / "rows.pdf"
    row_names = forms.reorder_fields(cols, rows_pdf, 1, mode="rows")
    assert row_names == ["TopLeft", "TopRight", "BotLeft", "BotRight"]
    assert str(PdfReader(str(rows_pdf)).pages[0]["/Tabs"]) == "/R"
    assert forms.field_details(rows_pdf)[1][0]["tab_order"] == "rows"


def test_keep_defaults_and_check_form(tmp_path):
    raw = blank(tmp_path / "blank.pdf")
    clean = tmp_path / "clean.pdf"
    forms.add_fields(raw, clean, [
        {"page": 1, "rect": [72, 700, 172, 722], "type": "text", "name": "Qty", "tooltip": "Quantity"},
        {"page": 1, "rect": [72, 660, 172, 682], "type": "text", "name": "Price", "tooltip": "Unit price"},
        {"page": 1, "rect": [72, 620, 172, 642], "type": "text", "name": "Total", "tooltip": "Total",
         "readonly": True, "calculate": {"op": "product", "fields": ["Qty", "Price"]}},
    ])
    assert forms.check_form(clean) == []

    # Simulate preview-filled values and promote them to /DV
    filled = tmp_path / "filled.pdf"
    w = PdfWriter(clone_from=str(clean))
    w.update_page_form_field_values(w.pages[0], {"Qty": "3", "Price": "10", "Total": "30"}, auto_regenerate=False)
    w.write(str(filled))
    kept = tmp_path / "kept.pdf"
    count = forms.keep_defaults(filled, kept)
    assert count == 3
    details = by_name(kept)
    assert details["Qty"]["default_value"] == "3"
    assert details["Price"]["default_value"] == "10"
    assert details["Total"]["default_value"] == "30"

    # Build a form with all 6 check_form issue kinds
    bad = tmp_path / "bad.pdf"
    forms.add_fields(raw, bad, [
        {"page": 1, "rect": [72, 700, 172, 722], "type": "text", "name": "Dup"},  # missing_tooltip + duplicate_type
        {"page": 1, "rect": [120, 705, 220, 727], "type": "checkbox", "name": "Dup", "tooltip": "Checkbox dup"},  # overlapping + duplicate_type
        {"page": 1, "rect": [-10, 600, 80, 622], "type": "text", "name": "Off", "tooltip": "Off page",
         "required": True, "readonly": True, "calculate": {"op": "sum", "fields": ["Ghost"]}},  # off_page + required_readonly + missing_calc_field
    ])
    # Force duplicate name on checkbox and remove tooltip on first field
    bad_dup = tmp_path / "bad_dup.pdf"
    w2 = PdfWriter(clone_from=str(bad))
    del w2.pages[0]["/Annots"][0].get_object()[NameObject("/TU")]
    w2.pages[0]["/Annots"][1].get_object()[NameObject("/T")] = TextStringObject("Dup")
    w2.write(str(bad_dup))

    kinds = {issue["kind"] for issue in forms.check_form(bad_dup)}
    assert kinds == {
        "missing_tooltip",
        "duplicate_type",
        "overlapping",
        "off_page",
        "required_readonly",
        "missing_calc_field",
    }


def test_form_data_export_import_clear_and_fill_many(tmp_path):
    import csv
    import openpyxl

    raw = blank(tmp_path / "blank.pdf")
    form = tmp_path / "form.pdf"
    forms.add_fields(raw, form, [
        {"page": 1, "rect": [72, 700, 220, 722], "type": "text", "name": "FullName"},
        {"page": 1, "rect": [72, 660, 172, 682], "type": "text", "name": "Amount"},
        {"page": 1, "rect": [72, 620, 88, 636], "type": "checkbox", "name": "Agree"},
        {"page": 1, "rect": [72, 580, 172, 602], "type": "dropdown", "name": "Plan",
         "options": ["Basic", "Pro", "Team"]},
    ])

    expected = {"FullName": "Ada Lovelace", "Amount": "42.50", "Agree": "Yes", "Plan": "Pro"}
    filled = tmp_path / "filled.pdf"
    assert forms.fill_form(form, filled, expected) == 4
    assert forms.read_form_values(filled) == expected

    # Export to FDF, XFDF, CSV, JSON, clear, then import each format back
    cleared = tmp_path / "cleared.pdf"
    forms.clear_form(filled, cleared)
    assert forms.read_form_values(cleared) == {"FullName": "", "Amount": "", "Agree": "", "Plan": ""}

    for fmt in ("fdf", "xfdf", "csv", "json"):
        data_file = tmp_path / f"values.{fmt}"
        forms.export_data(filled, data_file, fmt=fmt)
        reimported = tmp_path / f"reimported_{fmt}.pdf"
        forms.import_data(cleared, data_file, reimported)
        assert forms.read_form_values(reimported) == expected, f"round-trip failed for {fmt}"

    # Fill many (mail merge): 10 CSV rows -> 10 named PDFs
    csv_path = tmp_path / "people.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["FullName", "Amount", "Agree", "Plan"])
        w.writeheader()
        for i in range(1, 11):
            w.writerow({
                "FullName": f"Person {i}",
                "Amount": str(i * 10),
                "Agree": "Yes" if i % 2 else "",
                "Plan": ["Basic", "Pro", "Team"][i % 3],
            })
    out_dir = tmp_path / "merged_csv"
    made = forms.fill_many(form, csv_path, out_dir, name_column="FullName")
    assert len(made) == 10
    assert [p.name for p in made] == [f"Person {i}.pdf" for i in range(1, 11)]
    assert forms.read_form_values(made[0]) == {"FullName": "Person 1", "Amount": "10", "Agree": "Yes", "Plan": "Pro"}
    assert forms.read_form_values(made[9]) == {"FullName": "Person 10", "Amount": "100", "Agree": "", "Plan": "Pro"}

    # Also verify XLSX mail merge
    xlsx_path = tmp_path / "people.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["FullName", "Amount", "Agree", "Plan"])
    ws.append(["Grace Hopper", "99", "Yes", "Team"])
    wb.save(str(xlsx_path))
    xlsx_made = forms.fill_many(form, xlsx_path, tmp_path / "merged_xlsx", name_column="FullName")
    assert [p.name for p in xlsx_made] == ["Grace Hopper.pdf"]
    assert forms.read_form_values(xlsx_made[0])["Amount"] == "99"


def test_button_actions_and_checkbox_show_hide(tmp_path):
    raw = blank(tmp_path / "two.pdf", 2)
    form = tmp_path / "form.pdf"
    forms.add_fields(raw, form, [
        {"page": 1, "rect": [72, 700, 200, 722], "type": "text", "name": "Name"},
        {"page": 1, "rect": [72, 660, 200, 682], "type": "text", "name": "Details"},
        {"page": 1, "rect": [72, 620, 88, 636], "type": "checkbox", "name": "ShowMore"},
        {"page": 1, "rect": [72, 570, 152, 594], "type": "button", "name": "BtnReset"},
        {"page": 1, "rect": [160, 570, 240, 594], "type": "button", "name": "BtnPrint"},
        {"page": 1, "rect": [250, 570, 330, 594], "type": "button", "name": "BtnGoto"},
        {"page": 1, "rect": [340, 570, 420, 594], "type": "button", "name": "BtnUrl"},
        {"page": 1, "rect": [430, 570, 510, 594], "type": "button", "name": "BtnEmail"},
    ])
    edited = tmp_path / "edited.pdf"
    forms.edit_fields(form, edited, [
        {"page": 1, "index": 2, "show_hide": ["Details"]},
        {"page": 1, "index": 3, "label": "Clear Name", "action": {"type": "reset", "fields": ["Name"]}},
        {"page": 1, "index": 4, "action": {"type": "print"}},
        {"page": 1, "index": 5, "action": {"type": "goto", "page": 2}},
        {"page": 1, "index": 6, "action": {"type": "url", "url": "https://example.com"}},
        {"page": 1, "index": 7, "action": {"type": "email", "to": "desk@example.com", "subject": "Form", "submit": "fdf"}},
    ])
    details = by_name(edited)
    assert details["ShowMore"]["show_hide"] == ["Details"]
    assert details["BtnReset"]["label"] == "Clear Name"
    assert details["BtnReset"]["action"] == {"type": "reset", "fields": ["Name"]}
    assert details["BtnPrint"]["action"] == {"type": "print"}
    assert details["BtnGoto"]["action"] == {"type": "goto", "page": 2}
    assert details["BtnUrl"]["action"] == {"type": "url", "url": "https://example.com"}
    assert details["BtnEmail"]["action"] == {"type": "email", "to": "desk@example.com", "subject": "Form", "submit": "fdf"}

    r = PdfReader(str(edited))
    annots = [ref.get_object() for ref in r.pages[0]["/Annots"]]
    assert "/U" in annots[2]["/AA"]
    assert str(annots[3]["/A"]["/S"]) == "/ResetForm"
    assert str(annots[4]["/A"]["/S"]) == "/Named" and str(annots[4]["/A"]["/N"]) == "/Print"
    assert str(annots[5]["/A"]["/S"]) == "/GoTo"
    assert str(annots[6]["/A"]["/S"]) == "/URI"
    assert str(annots[7]["/A"]["/S"]) == "/SubmitForm"




