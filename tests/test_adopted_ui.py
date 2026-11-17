# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.50.0: choosing Highlight (or Text, or Draw) on a document with a text
box of its own, such as a callout, no longer marks it as changed
(adopted.js drives the reader in a browser)."""

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from pdfmanager import gui, markup
from test_construction_markups import sheet

HERE = Path(__file__).parent


def test_a_tool_turned_on_is_not_a_change(tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    src = tmp_path / "Plan.pdf"
    markup.apply(sheet(tmp_path / "sheet.pdf"), src, [
        {"op": "add", "page": 1, "kind": "callout", "tip": [420, 300], "at": [470, 360], "text": "Check this"}], author="Ada")
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ident = srv.RequestHandlerClass.opened.add(src)
        run = subprocess.run([node, str(HERE / "adopted.js"), url, ident], capture_output=True, text=True,
                             encoding="utf-8", timeout=300)
    finally:
        srv.shutdown()
        srv.server_close()
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]
    untouched = {"version": 0, "changed": False, "needsSaving": False}
    assert out["opened"] == {**untouched, "taken": 0}
    for mode in ("HIGHLIGHT", "FREETEXT", "INK"):
        assert out[mode] == {**untouched, "taken": 1}, mode          # pdf.js took the callout to edit it: not a change
    assert out["afterAChange"]["version"] == 1 and out["afterAChange"]["needsSaving"]   # a real one still is
