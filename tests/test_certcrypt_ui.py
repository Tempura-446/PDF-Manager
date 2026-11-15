# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.41.0: the reader says what a PDF protected for particular people is,
and opens it with a Digital ID (certopen.js drives it in a browser)."""

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

pytest.importorskip("pyhanko")
from pdfmanager import certcrypt, gui  # noqa: E402
from test_certcrypt import PW, make_id, plain  # noqa: E402

HERE = Path(__file__).parent


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def test_opening_with_a_digital_id_in_a_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    ann, ann_cer = make_id(tmp_path, "Ann Reader")
    eve, _ = make_id(tmp_path, "Eve Other")
    protected = tmp_path / "Offer.pdf"
    certcrypt.encrypt_for(plain(tmp_path / "p.pdf", "The offer is 120,000"), protected, [ann_cer])
    srv, url = app
    ident = srv.RequestHandlerClass.opened.add(protected)
    shots = os.environ.get("ZPDF_SHOTS")
    run = subprocess.run([node, str(HERE / "certopen.js"), url, ident, str(eve), str(ann), PW] + ([shots] if shots else []),
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], (out["errors"], out.get("said"))

    # not "damaged", no Repair, no password box: what it is, and the way in
    assert "protected for particular people" in out["card"]["why"] and "damaged" not in out["card"]["why"]
    assert not out["card"]["repair"] and not out["card"]["passwordDialog"]
    assert "wasn't protected for Eve Other" in out["wrong"]
    assert out["opened"]["tabs"] == ["Offer.pdf", "Offer_opened.pdf"] and out["opened"]["active"] == "Offer_opened.pdf"
    assert "The offer is 120,000" in out["opened"]["text"]
    assert out["opened"]["unsaved"]                      # the unprotected copy is kept only if saved
