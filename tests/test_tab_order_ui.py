# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Browser check for Prepare form tab order: numbered badges, sidebar Fields
list (click to select, rename in place, drag to reorder, Order by rows),
and Tab traversal in pdf.js."""

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from pypdf import PdfReader
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


def _three_fields_pdf(path):
    raw = path.with_name("raw.pdf")
    c = canvas.Canvas(str(raw))
    c.drawString(72, 760, "Tab order test")
    c.showPage()
    c.save()
    forms.add_fields(raw, path, [
        {"page": 1, "rect": [72, 700, 172, 722], "type": "text", "name": "First"},
        {"page": 1, "rect": [72, 660, 172, 682], "type": "text", "name": "Second"},
        {"page": 1, "rect": [72, 620, 172, 642], "type": "text", "name": "Third"},
    ])
    return path


def test_tab_order_in_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    pdf = _three_fields_pdf(tmp_path / "taborder.pdf")
    file_id = app[0].RequestHandlerClass.opened.add(pdf)
    run = subprocess.run([node, str(HERE / "taborder.js"), app[1], file_id],
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]

    assert out["initialBadges"] == [
        {"name": "First", "badge": "1"},
        {"name": "Second", "badge": "2"},
        {"name": "Third", "badge": "3"},
    ]
    assert out["sidebarNames"] == ["First", "Second", "Third"]
    assert out["afterDragOrder"] == ["Last", "First", "Second"]
    assert out["domOrder"] == ["Last", "First", "Second"]
    assert out["tabSeq"] == ["Last", "First", "Second"]
    assert out["afterRowsReset"] == ["First", "Second", "Last"]
    assert str(PdfReader(str(pdf)).pages[0]["/Tabs"]) == "/R"
