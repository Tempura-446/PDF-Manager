# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Browser check for button actions (reset, goto, url, email) and checkbox show/hide."""

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


def _actions_pdf(path):
    raw = path.with_name("raw.pdf")
    c = canvas.Canvas(str(raw))
    c.drawString(72, 760, "Page 1")
    c.showPage()
    c.drawString(72, 760, "Page 2")
    c.showPage()
    c.save()
    forms.add_fields(raw, path, [
        {"page": 1, "rect": [72, 700, 200, 722], "type": "text", "name": "Name"},
        {"page": 1, "rect": [72, 660, 200, 682], "type": "text", "name": "Details"},
        {"page": 1, "rect": [72, 620, 88, 636], "type": "checkbox", "name": "ShowMore", "show_hide": ["Details"]},
        {"page": 1, "rect": [72, 570, 152, 594], "type": "button", "name": "BtnReset", "label": "Clear Name",
         "action": {"type": "reset", "fields": ["Name"]}},
        {"page": 1, "rect": [160, 570, 240, 594], "type": "button", "name": "BtnGoto", "label": "Next",
         "action": {"type": "goto", "page": 2}},
        {"page": 1, "rect": [250, 570, 330, 594], "type": "button", "name": "BtnUrl", "label": "Site",
         "action": {"type": "url", "url": "https://example.com"}},
        {"page": 1, "rect": [340, 570, 420, 594], "type": "button", "name": "BtnEmail", "label": "Email",
         "action": {"type": "email", "to": "desk@example.com", "subject": "Form", "submit": "fdf"}},
    ])
    return path


def test_button_actions_and_show_hide_in_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    pdf = _actions_pdf(tmp_path / "actions.pdf")
    file_id = app[0].RequestHandlerClass.opened.add(pdf)
    run = subprocess.run([node, str(HERE / "formactions.js"), app[1], file_id],
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]

    assert out["detailsHiddenInitially"] is True
    assert out["detailsHiddenAfterTick"] is False
    assert out["nameAfterReset"] == ""
    assert out["lastUrl"] == "https://example.com"
    assert out["lastMailto"] == "mailto:desk@example.com?subject=Form"
    assert out["pageAfterGoto"] == 2
