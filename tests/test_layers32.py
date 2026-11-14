"""0.32.0: layers (optional content) of drawings. Reading them for the
reader's Layers tab, "Save layer visibility" (the file opens with the
layers chosen) and "Flatten layers" (the layers shown become part of the
page, the hidden ones' content goes, and the file has no layers after), in
the engine, the app's tool, its endpoints and the command line."""
import io
import json

import pikepdf
import pypdfium2 as pdfium
import pytest

from pdfmanager import core, gui, layers
from pdfmanager.cli import main
from layerpdfs import layered_pdf, plain_pdf
from test_more import app, call, multipart  # noqa: F401 - app is a fixture


def pdfium_text(path_or_bytes):
    doc = pdfium.PdfDocument(path_or_bytes)
    try:
        return "\n".join(page.get_textpage().get_text_range() for page in doc)
    finally:
        doc.close()


def stream_bytes(path):
    """Every stream of the file, decoded, joined."""
    with pikepdf.open(path) as pdf:
        out = []
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Stream):
                try:
                    out.append(obj.read_bytes())
                except pikepdf.PdfError:
                    pass
        return b"\n".join(out)


def off_names(path):
    """The layers a file opens with hidden, by name (saving numbers the objects anew)."""
    found = layers.info(path)
    names = {i["id"]: i["name"] for i in found["layers"]}
    return sorted(names[i] for i in found["off"])


def test_info_lists_the_layers_in_their_nested_order(tmp_path):
    found = layers.info(layered_pdf(tmp_path / "plan.pdf"))
    names = {item["id"]: item["name"] for item in found["layers"]}
    by_name = {v: k for k, v in names.items()}
    assert sorted(names.values()) == ["Dimensions", "Scheme A", "Scheme B", "Walls"]
    # Walls with Dimensions nested under it, then the "Options" heading.
    walls, options = found["order"]
    assert walls["name"] == "Walls" and [c["name"] for c in walls["children"]] == ["Dimensions"]
    assert options["label"] == "Options" and [c["name"] for c in options["children"]] == ["Scheme A", "Scheme B"]
    assert found["locked"] == [by_name["Walls"]]
    assert found["rbgroups"] == [[by_name["Scheme A"], by_name["Scheme B"]]]
    assert found["off"] == [by_name["Scheme B"]]
    # pdf.js's ids: "<object number>R".
    assert all(i.endswith("R") and i[:-1].isdigit() for i in names)
    assert layers.info(plain_pdf(tmp_path / "plain.pdf")) == {"layers": [], "order": [], "locked": [],
                                                             "rbgroups": [], "off": []}


def test_flatten_removes_the_hidden_layer_and_keeps_the_rest(tmp_path):
    src = layered_pdf(tmp_path / "plan.pdf")
    assert "DIMENSION 3600" in pdfium_text(str(src))
    out = tmp_path / "flat.pdf"
    result = layers.flatten_layers(src, out, hidden=["Dimensions", "Scheme B"])
    assert result == {"kept": ["Walls", "Scheme A"], "removed": ["Dimensions", "Scheme B"]}
    text = pdfium_text(str(out))
    # The hidden layer's text is gone from what pdfium reads, also the one in its form XObject...
    assert "DIMENSION" not in text and "DIMFORM" not in text and "SCHEME B" not in text
    # ... and from the file itself; what shows stays.
    raw = stream_bytes(out)
    assert b"DIMENSION" not in raw and b"DIMFORM" not in raw and b"SCHEME B" not in raw
    assert "WALLS" in text and "SCHEME A" in text and "TITLE BLOCK" in text
    with pikepdf.open(out) as pdf:
        assert "/OCProperties" not in pdf.Root
        page = pdf.pages[0]
        ops = [str(op) for _, op in pikepdf.parse_content_stream(page)]
        assert "BDC" not in ops and "EMC" not in ops  # (no marks of layers left)
        assert "/Fm1" not in page.Resources.XObject  # the hidden layer's XObject
        assert not page.Resources.get("/Properties")
        # The visible line (Walls) is still drawn; the hidden one (y=600) isn't.
        content = page.Contents.read_bytes()
        assert b"500 150 l" in content and b"500 600 l" not in content
    assert layers.info(out)["layers"] == []


def test_flatten_with_the_layers_as_the_file_opens(tmp_path):
    src = layered_pdf(tmp_path / "plan.pdf", dimensions_on=False)
    out = tmp_path / "flat.pdf"
    assert layers.flatten_layers(src, out)["removed"] == ["Dimensions", "Scheme B"]
    assert "DIMENSION" not in pdfium_text(str(out))
    with pytest.raises(core.PdfManagerError, match="no layers"):
        layers.flatten_layers(plain_pdf(tmp_path / "plain.pdf"), tmp_path / "x.pdf")


def test_save_visibility_writes_the_default_configuration(tmp_path):
    src = layered_pdf(tmp_path / "plan.pdf")
    ids = {i["name"]: i["id"] for i in layers.info(src)["layers"]}
    out = tmp_path / "saved.pdf"
    # Walls is locked: it keeps its state, though asked to hide it.
    hidden = layers.save_visibility(src, out, [ids["Dimensions"], ids["Scheme A"], ids["Walls"]])
    assert hidden == ["Dimensions", "Scheme A"]
    with pikepdf.open(out) as pdf:
        d = pdf.Root.OCProperties.D
        assert sorted(str(o.Name) for o in d.OFF) == ["Dimensions", "Scheme A"]
        assert sorted(str(o.Name) for o in d.ON) == ["Scheme B", "Walls"]
        assert str(d.BaseState) == "/ON"
        assert len(d.Order) == 3 and len(d.Locked) == 1  # the rest of it as it was
    assert off_names(out) == ["Dimensions", "Scheme A"]
    # Nothing but the configuration changes: the layers' content is all there.
    assert "DIMENSION 3600" in pdfium_text(str(out))


def test_hidden_names_starts_from_the_file(tmp_path):
    src = layered_pdf(tmp_path / "plan.pdf")
    ids = {i["name"]: i["id"] for i in layers.info(src)["layers"]}
    assert layers.hidden_names(src) == [ids["Scheme B"]]
    assert sorted(layers.hidden_names(src, "Dimensions, Walls", ["Scheme B"])) == sorted([ids["Walls"], ids["Dimensions"]])
    with pytest.raises(core.PdfManagerError, match="no layer called “Roof”"):
        layers.hidden_names(src, ["Roof"])


def test_the_flatten_layers_tool_and_the_command_line(tmp_path, capsys):
    src = layered_pdf(tmp_path / "plan.pdf")
    spec = gui.OPS["flatten-layers"]
    assert spec["label"] == "Flatten layers" and spec["files"] == "pdf"
    work = tmp_path / "work"
    work.mkdir()
    notes = []
    name, data, _ = gui.run_operation("flatten-layers", [src], {"hide": "Dimensions", "show": ""}, work=work, notes=notes)
    assert name == "plan_flat.pdf"
    assert notes == ["plan.pdf: removed the hidden layers Dimensions, Scheme B."]
    text = pdfium_text(data)
    assert "DIMENSION" not in text and "WALLS" in text
    work2 = tmp_path / "work2"
    work2.mkdir()
    with pytest.raises(core.PdfManagerError, match="no layers"):
        gui.run_operation("flatten-layers", [plain_pdf(tmp_path / "plain.pdf")], {}, work=work2)

    assert main(["flatten-layers", str(src), "--list"]) == 0
    listed = capsys.readouterr().out
    assert "shown   Dimensions" in listed and "hidden  Scheme B" in listed
    out = tmp_path / "cli.pdf"
    assert main(["flatten-layers", str(src), "--show", "Scheme B", "--hide", "Scheme A,Dimensions", "-o", str(out)]) == 0
    assert "(removed: Dimensions, Scheme A)" in capsys.readouterr().out
    text = pdfium_text(str(out))
    assert "SCHEME B" in text and "SCHEME A" not in text and "DIMENSION" not in text


def test_the_reader_endpoints(app, tmp_path):  # noqa: F811
    base, token, srv = app
    src = layered_pdf(tmp_path / "plan.pdf")
    body, headers = multipart({"password": ""}, [("file", src)])
    status, _, res = call(base, token, "/api/layers", body, headers)
    assert status == 200
    found = json.loads(res)
    ids = {i["name"]: i["id"] for i in found["layers"]}
    # Of a file opened from this computer, by its id.
    key = srv.RequestHandlerClass.opened.add(src)
    body, headers = multipart({"id": key}, [])
    status, _, res = call(base, token, "/api/layers", body, headers)
    assert status == 200 and json.loads(res) == found

    body, headers = multipart({"password": "", "hidden": json.dumps([ids["Dimensions"]])}, [("file", src)])
    status, head, res = call(base, token, "/api/layers/save", body, headers)
    assert status == 200 and head["Content-Type"] == "application/pdf"
    saved = tmp_path / "saved.pdf"
    saved.write_bytes(res)
    assert off_names(saved) == ["Dimensions"]

    status, _, res = call(base, token, "/api/layers/flatten", body, headers)
    assert status == 200
    text = pdfium_text(res)
    assert "DIMENSION" not in text and "SCHEME B" in text  # (as chosen: Scheme B shown)
    with pikepdf.open(io.BytesIO(res)) as pdf:
        assert "/OCProperties" not in pdf.Root

    body, headers = multipart({"hidden": "{}"}, [("file", src)])
    status, _, res = call(base, token, "/api/layers/save", body, headers)
    assert status == 400 and b"list of layers" in res


def test_a_protected_file_stays_protected(app, tmp_path):  # noqa: F811
    base, token, _ = app
    src = layered_pdf(tmp_path / "plan.pdf")
    locked = tmp_path / "locked.pdf"
    core.encrypt(src, locked, "pw")
    body, headers = multipart({"password": "pw", "hidden": "[]"}, [("file", locked)])
    status, _, res = call(base, token, "/api/layers/flatten", body, headers)
    assert status == 200
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(io.BytesIO(res))
    with pikepdf.open(io.BytesIO(res), password="pw") as pdf:
        assert "/OCProperties" not in pdf.Root
