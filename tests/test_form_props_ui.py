# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Field properties panel in the reader (formprops.js drives it in a browser)."""

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas as rl_canvas

from pdfmanager import forms, gui

HERE = Path(__file__).parent


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def _three_boxes(path):
    raw = path.with_suffix(".raw.pdf")
    c = rl_canvas.Canvas(str(raw))
    c.drawString(72, 760, "Order")
    c.save()
    forms.add_fields(raw, path, [
        {"page": 1, "rect": [72, 700, 180, 722], "type": "text", "name": "Box 1"},
        {"page": 1, "rect": [72, 660, 180, 682], "type": "text", "name": "Box 2"},
        {"page": 1, "rect": [72, 620, 180, 642], "type": "text", "name": "Box 3"},
    ])
    return path


def test_field_properties_panel_in_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    pdf = _three_boxes(tmp_path / "order.pdf")
    file_id = app[0].RequestHandlerClass.opened.add(pdf)
    run = subprocess.run([node, str(HERE / "formprops.js"), app[1], file_id],
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]

    assert out["multiBadge"] == "2 fields selected"
    assert out["widgetCount"] == 3
    assert out["qtyMaxLen"] == "4"
    assert out["priceVal"] == "$5.00"
    assert out["totalVal"] in ("7", "7.00", "$7.00")

    # Saved file on disk has every property persisted.
    by_name = {f["name"]: f for f in forms.field_details(pdf)[1]}
    assert by_name["Qty"]["required"] is True
    assert by_name["Qty"]["comb"] is True
    assert by_name["Qty"]["max_length"] == 4
    assert by_name["Qty"]["align"] == "right"
    assert by_name["Price"]["format"] == "currency"
    assert by_name["Price"]["currency"] == "$"
    assert by_name["Price"]["decimals"] == 2
    assert by_name["Price"]["align"] == "right"
    assert by_name["Total"]["calculate"]["op"] == "sum"
    assert set(by_name["Total"]["calculate"]["fields"]) == {"Qty", "Price"}
