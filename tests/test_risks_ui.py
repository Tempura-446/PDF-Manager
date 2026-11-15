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
from pypdf.generic import DictionaryObject, NameObject

from pdfmanager import forms, gui
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
    # A form whose total adds up, with a script of the document's own, a
    # field script that isn't a form function, and a link that misleads.
    src = tmp_path / "form.pdf"
    forms.add_fields(plain(tmp_path / "p.pdf", ["Sign in at www.mybank.com to confirm"]), src, [
        {"page": 1, "rect": [72, 600, 200, 620], "type": "text", "name": "Price", "format": "amount"},
        {"page": 1, "rect": [72, 570, 200, 590], "type": "text", "name": "Total", "calculate": "sum: Price"},
        {"page": 1, "rect": [72, 540, 200, 560], "type": "text", "name": "Other"}])
    w = PdfWriter(clone_from=str(src))
    add_js(w, "app.alert('hello');")
    other = w.pages[0]["/Annots"][2].get_object()
    other[NameObject("/AA")] = DictionaryObject({
        NameObject("/K"): action("/JavaScript", JS="app.alert('typed');"),
        NameObject("/Bl"): action("/JavaScript", JS="app.alert('left');")})
    w.pages[0][NameObject("/AA")] = DictionaryObject({NameObject("/O"): action("/JavaScript", JS="app.alert('page');")})
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

    # Nothing of the PDF's own ran: not on opening, not when its page showed,
    # not when typing in the field with a script. The form's total still adds up.
    assert out["native"] == [], out["native"]
    assert out["form"]["price"] == "12.50" and out["form"]["other"] == "abc", out["form"]
    assert out["form"]["total"] in ("12.5", "12.50"), out["form"]
    assert out["chip"]["text"] == "could do harm"
    assert "It has scripts" in out["chip"]["title"] and "Links that hide where they go" in out["chip"]["title"]
    assert out["first"]["op"] == "makesafe" and "Make it safe" in out["first"]["words"]

    assert out["dialog"]["title"] == "Open this link?"
    assert out["dialog"]["host"] == "mybank.com.evil-site.net"            # the real site stands out
    assert out["dialog"]["address"] == "http://mybank.com.evil-site.net/login?next=1"
    assert any("isn't protected" in n for n in out["dialog"]["notes"])
    assert out["afterCancel"] == 0                                         # Cancel opened nothing
    assert out["noAsk"] == {"dialog": False, "opened": True}               # asking turned off: it opens
