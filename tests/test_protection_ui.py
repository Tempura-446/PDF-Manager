# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.40.0: Add password says how strong the password is while it's typed
(pwmeter.js drives it in a browser)."""

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from pdfmanager import gui

HERE = Path(__file__).parent


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def test_the_password_meter_in_a_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    shots = os.environ.get("ZPDF_SHOTS")
    run = subprocess.run([node, str(HERE / "pwmeter.js"), app[1]] + ([shots] if shots else []),
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]
    before, common, middling, phrase, emptied = out["steps"]
    assert before["score"] == "" and before["words"] == ""              # nothing said before a password is typed
    assert common["score"] == "0" and common["words"].startswith("Very weak.") and "common" in common["words"]
    assert middling["score"] in ("1", "2")
    assert phrase["score"] == "4" and phrase["words"].startswith("Strong.")
    assert emptied["score"] == "" and emptied["words"] == ""
    assert out["meters"] == 1
