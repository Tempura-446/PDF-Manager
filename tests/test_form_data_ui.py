# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Browser check for Prepare form Data menu: Import, Export, and Clear form."""

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


def _data_pdf(path):
    raw = path.with_name("raw.pdf")
    c = canvas.Canvas(str(raw))
    c.drawString(72, 760, "Data test")
    c.showPage()
    c.save()
    forms.add_fields(raw, path, [
        {"page": 1, "rect": [72, 700, 220, 722], "type": "text", "name": "FullName"},
        {"page": 1, "rect": [72, 660, 172, 682], "type": "text", "name": "Amount"},
    ])
    return path


def test_form_data_menu_in_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    pdf = _data_pdf(tmp_path / "dataform.pdf")
    data_json = tmp_path / "values.json"
    data_json.write_text(json.dumps({"FullName": "Ada Lovelace", "Amount": "42"}), encoding="utf-8")
    file_id = app[0].RequestHandlerClass.opened.add(pdf)
    run = subprocess.run([node, str(HERE / "formdata.js"), app[1], file_id, str(data_json)],
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]

    assert out["afterImportName"] == "Ada Lovelace"
    assert out["exportedFilename"] == "dataform.xfdf"
    assert out["afterClearName"] == ""
