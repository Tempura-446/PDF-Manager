# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Browser check for Prepare form Preview mode, Keep as default values, and Check form."""

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from pdfmanager import forms, gui

HERE = Path(__file__).resolve().parent


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def _preview_pdf(path):
    raw = path.with_name("raw.pdf")
    c = canvas.Canvas(str(raw))
    c.drawString(72, 760, "Preview test")
    c.showPage()
    c.save()
    forms.add_fields(raw, path, [
        {"page": 1, "rect": [72, 700, 172, 722], "type": "text", "name": "Qty", "tooltip": "Quantity"},
        {"page": 1, "rect": [72, 660, 172, 682], "type": "text", "name": "Price", "tooltip": "Price"},
        {"page": 1, "rect": [72, 620, 172, 642], "type": "text", "name": "Total", "tooltip": "Total",
         "readonly": True, "calculate": {"op": "product", "fields": ["Qty", "Price"]}},
        {"page": 1, "rect": [72, 560, 172, 582], "type": "text", "name": "Bad",
         "required": True, "readonly": True},
    ])
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, NumberObject
    w = PdfWriter(clone_from=str(path))
    bad = w.pages[0]["/Annots"][3].get_object()
    del bad["/TU"]
    bad[NameObject("/Ff")] = NumberObject(int(bad.get("/Ff", 0)) | forms.READ_ONLY)
    w.write(str(path))
    return path


def test_form_preview_and_check_in_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    pdf = _preview_pdf(tmp_path / "preview.pdf")
    file_id = app[0].RequestHandlerClass.opened.add(pdf)
    run = subprocess.run([node, str(HERE / "formpreview.js"), app[1], file_id],
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]

    assert out["previewTotal"] == "36"
    assert out["afterExitQty"] == ""
    details = {f["name"]: f for f in forms.field_details(pdf)[1]}
    assert details["Qty"]["default_value"] == "4"
    assert details["Price"]["default_value"] == "5"
    assert details["Total"]["default_value"] == "20"
    assert set(out["checkKinds"]) == {"missing_tooltip", "required_readonly"}
    assert out["selectedName"] == "Bad"
