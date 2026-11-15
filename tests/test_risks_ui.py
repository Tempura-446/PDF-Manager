# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.39.0: the file panel says what a PDF could do, and the reader shows a
link's real address before opening it (riskui.js drives it in a browser)."""

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from pypdf import PdfWriter

from pdfmanager import gui
from test_risks import action, add_js, link, plain

HERE = Path(__file__).parent


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def test_risks_in_a_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    src = plain(tmp_path / "p.pdf", ["Sign in at www.mybank.com to confirm"])
    w = PdfWriter(clone_from=str(src))
    add_js(w, "app.alert('hello');")
    link(w, [100, 755, 330, 772], action("/URI", URI="http://mybank.com.evil-site.net/login?next=1"))
    risky = tmp_path / "Invoice.pdf"
    w.write(str(risky))
    srv, url = app
    ident = srv.RequestHandlerClass.opened.add(risky)
    shots = os.environ.get("ZPDF_SHOTS")
    run = subprocess.run([node, str(HERE / "riskui.js"), url, ident] + ([shots] if shots else []),
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]

    assert out["chip"]["text"] == "could do harm"
    assert "It has scripts" in out["chip"]["title"] and "Links that hide where they go" in out["chip"]["title"]
    assert out["first"]["op"] == "makesafe" and "Make it safe" in out["first"]["words"]

    assert out["dialog"]["title"] == "Open this link?"
    assert out["dialog"]["host"] == "mybank.com.evil-site.net"            # the real site stands out
    assert out["dialog"]["address"] == "http://mybank.com.evil-site.net/login?next=1"
    assert any("isn't protected" in n for n in out["dialog"]["notes"])
    assert out["afterCancel"] == 0                                         # Cancel opened nothing
    assert out["noAsk"] == {"dialog": False, "opened": True}               # asking turned off: it opens
