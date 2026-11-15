# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.42.0: Settings → Privacy in a browser (privacyui.js drives it): Clear
history, "Remember the files I open", and when ZPDF goes online."""

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from pdfmanager import appdata, gui, privacy

HERE = Path(__file__).parent


@pytest.fixture
def app(tmp_path, monkeypatch):
    folder = tmp_path / "zpdf-data"
    monkeypatch.setattr(appdata, "_data_dir", lambda: folder)
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def test_privacy_settings_in_a_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    from reportlab.pdfgen import canvas

    src = tmp_path / "Lease.pdf"
    c = canvas.Canvas(str(src))
    for n in range(1, 4):
        c.drawString(72, 720, f"Lease, page {n}")
        c.showPage()
    c.save()
    srv, url = app
    ident = srv.RequestHandlerClass.opened.add(src)
    shots = os.environ.get("ZPDF_SHOTS")
    run = subprocess.run([node, str(HERE / "privacyui.js"), url, ident] + ([shots] if shots else []),
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]

    assert out["placesKept"] == 1 and "Lease.pdf" in out["recentBefore"]["text"]
    assert out["before"]["remember"] and "1 in the Recent list" in out["before"]["history"]
    assert len(out["before"]["online"]) == len(privacy.ONLINE) and "no telemetry" in out["before"]["page"]
    assert "the Recent list" in out["cleared"]["status"] and "where you were" in out["cleared"]["status"]
    assert "Nothing is kept right now" in out["cleared"]["history"] and out["placesAfterClear"] == 0
    assert out["off"]["history"].startswith("Off") and out["off"]["clearDisabled"]
    assert "Lease.pdf" not in out["recentAfter"]["text"]
    # read again with remembering off: no place, no Recent row
    assert out["placesWhenOff"] == 0 and "Lease.pdf" not in out["recentWhenOff"]
    assert not privacy.remembering() and gui.RecentFiles.load() == []
