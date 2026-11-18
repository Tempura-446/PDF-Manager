# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Layout help in Prepare form mode (formlayout.js drives it in a browser)."""

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


def _two_page_fields(path):
    raw = path.with_suffix(".raw.pdf")
    c = rl_canvas.Canvas(str(raw))
    c.drawString(72, 760, "Page 1")
    c.showPage()
    c.drawString(72, 760, "Page 2")
    c.showPage()
    c.save()
    forms.add_fields(raw, path, [
        {"page": 1, "rect": [72, 700, 172, 722], "type": "text", "name": "A"},
        {"page": 1, "rect": [120, 640, 200, 660], "type": "text", "name": "B"},
        {"page": 1, "rect": [90, 560, 180, 580], "type": "text", "name": "C"},
    ])
    return path


def test_form_layout_help_in_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    pdf = _two_page_fields(tmp_path / "layout.pdf")
    file_id = app[0].RequestHandlerClass.opened.add(pdf)
    run = subprocess.run([node, str(HERE / "formlayout.js"), app[1], file_id],
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]

    assert out["guideCountDuringDrag"] >= 1

    details = forms.field_details(pdf)
    p1 = {f["name"]: f for f in details[1]}
    p2 = {f["name"]: f for f in details[2]}

    # A copied to page 2 with same name (one shared field).
    assert "A" in p2 and p1["A"]["widgets"] == 2
    # B aligned to A's left edge and same size as A.
    assert abs(p1["B"]["rect"][0] - p1["A"]["rect"][0]) < 0.5
    assert abs((p1["B"]["rect"][2] - p1["B"]["rect"][0]) - (p1["A"]["rect"][2] - p1["A"]["rect"][0])) < 0.5
    # C turned into a 2x2 table: C 1, C 2, C 3, C 4.
    for cell in ("C 1", "C 2", "C 3", "C 4"):
        assert cell in p1
