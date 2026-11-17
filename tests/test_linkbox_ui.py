# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.52.0: with Links on, a click on a link opens the box for changing it
also on a page with many annotations (linkbox.js drives the reader in a
browser). Before, the page's own link lay above the box and the click
asked "Open this link?"."""

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from pdfmanager import gui, markup
from test_construction_markups import sheet

HERE = Path(__file__).parent


def test_a_link_on_a_busy_page_opens_its_box(tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    src = tmp_path / "Plan.pdf"
    many = [{"op": "add", "page": 1, "kind": "rect", "rect": [60 + 30 * i, 700, 80 + 30 * i, 720], "text": f"n{i}"}
            for i in range(15)]
    markup.apply(sheet(tmp_path / "sheet.pdf"), src, many + [
        {"op": "add", "page": 1, "kind": "link", "rect": [100, 400, 300, 440], "url": "https://example.com/plan"}], author="Ada")
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ident = srv.RequestHandlerClass.opened.add(src)
        run = subprocess.run([node, str(HERE / "linkbox.js"), url, ident], capture_output=True, text=True,
                             encoding="utf-8", timeout=300)
    finally:
        srv.shutdown()
        srv.server_close()
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]
    assert out["sections"] >= 16                         # enough before the link for it to be stacked above the box
    assert out["under"] == {"tag": "DIV", "box": True}   # the click lands on the box for editing the link
    assert out["box"] == "Link"                          # ...and opens it, not "Open this link?"
