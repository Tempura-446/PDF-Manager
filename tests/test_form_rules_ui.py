# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.56.0: the Add form fields page shows and changes what a text box the
form already has takes and adds up (formrules.js drives it in a browser)."""

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from pdfmanager import forms, gui
from test_form_rules import details, made

HERE = Path(__file__).parent


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def test_a_text_boxs_format_and_total_in_a_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    src = made(tmp_path)
    run = subprocess.run([node, str(HERE / "formrules.js"), app[1], str(src)],
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]

    # What the file has is shown.
    assert out["price"] == {**out["price"], "shown": True, "format": "amount", "sum": ""}
    assert "keep" not in out["price"]["choices"]
    assert out["when"]["format"] == "keep" and out["when"]["label"] == "As in the file: date (yyyy-mm-dd)"
    assert out["whenNow"]["format"] == "date" and "keep" in out["whenNow"]["choices"]   # (it can go back)
    assert out["total"]["sum"] == "Price, Tax" and out["total"]["format"] == ""
    assert out["notes"] == {**out["notes"], "shown": True, "format": "", "sum": ""}
    assert out["tax"]["format"] == "currency"
    assert not out["agree"]["shown"]
    assert out["notesNow"]["format"] == "number" and out["notesNow"]["sum"] == "Price, Tax"
    assert "(4 changed)" in out["status"]
    assert len(set(out["tops"])) == 1, out["tops"]      # the page stays put as fields are picked

    edits = {e["index"]: e for e in out["edits"]}
    assert set(edits) == {1, 2, 3, 4}                     # (Price, set and put back, isn't sent)
    assert edits[1] == {"page": 1, "index": 1, "format": ""}
    assert edits[2] == {"page": 1, "index": 2, "calculate": "sum: Price"}
    assert edits[3] == {"page": 1, "index": 3, "format": "date", "date": "mm/dd/yyyy"}
    assert edits[4] == {"page": 1, "index": 4, "format": "number", "calculate": "sum: Price, Tax"}
    assert "changed 4" in out["result"]

    done = tmp_path / "done.pdf"
    forms.edit_fields(src, done, out["edits"])
    after = details(done)
    assert after["Tax"]["format"] is None and (after["Tax"]["min"], after["Tax"]["max"]) == (0, 500)
    assert after["Total"]["calculate"] == {"op": "sum", "fields": ["Price"]}
    assert after["When"]["format"] == {"format": "date", "date": "mm/dd/yyyy"}
    assert after["Notes"]["format"]["format"] == "number"
    assert after["Notes"]["calculate"] == {"op": "sum", "fields": ["Price", "Tax"]}
    assert after["Price"] == details(src)["Price"] | {"used_by": ["Total", "Notes"]}
