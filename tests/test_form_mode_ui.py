# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Prepare form mode in the reader (formmode.js drives it in a browser)."""

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas as rl_canvas

from pdfmanager import gui

HERE = Path(__file__).parent


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def _two_page_blank(path):
    c = rl_canvas.Canvas(str(path))
    c.drawString(72, 740, "Page 1")
    c.showPage()
    c.drawString(72, 740, "Page 2")
    c.showPage()
    c.save()
    return path


def _printed_form(path):
    c = rl_canvas.Canvas(str(path))
    for i, label in enumerate(("Name:", "Email:", "Phone:", "Address:")):
        y = 700 - i * 40
        c.drawString(72, y, label)
        c.line(140, y - 2, 360, y - 2)
    c.rect(72, 500, 12, 12)
    c.drawString(90, 502, "I agree")
    c.save()
    return path


def test_prepare_form_mode_in_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    blank = _two_page_blank(tmp_path / "blank.pdf")
    printed = _printed_form(tmp_path / "printed.pdf")
    blank_id = app[0].RequestHandlerClass.opened.add(blank)
    printed_id = app[0].RequestHandlerClass.opened.add(printed)
    run = subprocess.run([node, str(HERE / "formmode.js"), app[1], blank_id, printed_id],
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]

    assert out["tools"] == ["select", "text", "multiline", "checkbox", "radio", "dropdown", "list", "date", "signature", "button"]
    assert [f["type"] for f in out["afterAdd"]] == ["text", "checkbox"]
    assert out["marqueeSelected"] == 2
    assert out["afterDup"] == 4
    assert abs(out["moved"]["dx"] - 40) <= 3 and abs(out["moved"]["dy"] - 20) <= 3
    assert abs(out["resized"]["dw"] - 30) <= 3 and abs(out["resized"]["dh"] - 12) <= 3
    assert out["nudgedDx"] >= 8
    assert out["pastedOnPage2"] == 1
    assert out["afterDeleteP2"] == 0
    assert out["afterUndoP2"] == 1
    assert out["afterRedoP2"] == 0
    assert out["savePrimary"] is True
    assert out["foundFields"] >= 4
