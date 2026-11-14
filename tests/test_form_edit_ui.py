# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.37.0: the Add form fields page changes the fields a form already has
(formedit.js drives it in a browser)."""

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from pdfmanager import forms, gui
from test_form_edit import blank, made

HERE = Path(__file__).parent


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def test_changing_a_forms_own_fields_in_a_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    src = made(tmp_path)
    other = blank(tmp_path / "other.pdf")
    run = subprocess.run([node, str(HERE / "formedit.js"), app[1], str(src), str(other)],
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]

    assert [o["type"] for o in out["start"]] == ["text", "text", "text", "checkbox", "radio", "radio"]
    assert "already in the file: 7" in out["status0"]
    # the bar shows the picked field; its type is fixed, but a text box can take several lines
    assert out["price"]["shown"] and out["price"]["name"] == "Price" and out["price"]["type"] == "text"
    assert out["price"]["types"] == ["text", "multiline"] and not out["price"]["typeOff"]
    assert out["agree"]["typeOff"] and out["agree"]["type"] == "checkbox" and out["agree"]["requiredShown"]
    assert out["radio"]["typeOff"] and out["radio"]["name"] == "Size"
    assert abs(out["movedBy"][0] - 150) <= 2 and abs(out["movedBy"][1] - 24) <= 2
    assert "to delete" in out["afterDelete"]["status"] and "Agree will be deleted" in out["afterDelete"]["status"]
    assert not out["afterDelete"]["bar"]                 # nothing to set on a field that's going
    assert out["radioDeleted"] and out["radioKept"]
    assert out["newFields"] == 1
    assert "(2 changed)" in out["status"] and "(1 to delete)" in out["status"]

    edits = {e["index"]: e for e in out["edits"]}
    assert set(edits) == {0, 2, 3}                       # a click alone (Tax) changes nothing
    assert edits[0]["name"] == "Net price" and "rect" in edits[0]
    assert edits[2] == {"page": 1, "index": 2, "type": "multiline", "required": True}
    assert edits[3] == {"page": 1, "index": 3, "delete": True}
    assert json.loads(out["sent"]["edits"]) == out["edits"] and json.loads(out["sent"]["fields"])["1"]
    assert "added 1 field" in out["result"] and "changed 2 and deleted 1" in out["result"]

    # what was sent does what was meant
    done = tmp_path / "done.pdf"
    forms.edit_fields(src, done, out["edits"])
    fields = {f["name"]: f for f in forms.field_details(done)[1]}
    assert set(fields) == {"Net price", "Tax", "Total", "Size"}
    assert fields["Total"]["type"] == "multiline" and fields["Total"]["required"]
    moved, was = fields["Net price"]["rect"], [72.0, 700.0, 200.0, 720.0]
    assert moved[0] > was[0] + 50 and moved[1] < was[1] - 5
    assert abs((moved[2] - moved[0]) - 128) < 0.5 and abs((moved[3] - moved[1]) - 20) < 0.5

    assert out["two"] == {"edits": "", "striped": 6}
