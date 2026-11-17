# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.48.0: stamps of your own (stamps.py). Their words, colour and whether
they're dated are kept in the settings, offered in the reader's Stamp list
(ownstamps.js drives it in a browser) and drawn like any other stamp."""

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from pypdf import PdfReader

from pdfmanager import core, gui, markup, stamps, update
from test_construction_markups import sheet
from test_more import call

HERE = Path(__file__).parent


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def test_stamps_are_kept_tidied_and_read_back():
    assert stamps.saved() == []
    kept = stamps.save([{"label": "  checked  by  site ", "color": "#1F8A3B", "dated": True},
                        {"label": "Paid"}, {"label": "PAID", "color": "#000000"}])
    assert kept == [{"label": "CHECKED BY SITE", "color": "#1f8a3b", "dated": True},
                    {"label": "PAID", "color": stamps.DEFAULT_COLOR, "dated": False}]      # the first of the same words
    assert stamps.saved() == kept and update.settings()["stamps"] == kept
    assert stamps.save([]) == [] and "stamps" not in update.settings()                      # all removed


@pytest.mark.parametrize("bad,words", [([{"label": " "}], "needs words"), ([{"label": "x" * 61}], "60 letters"),
                                       ([{"label": "OK", "color": "green"}], "colour is written like"),
                                       (["PAID"], "its words, its colour"), ({"label": "PAID"}, "come as a list"),
                                       ([{"label": f"S{i}"} for i in range(31)], "30 stamps")])
def test_what_cant_be_kept_is_said(bad, words):
    with pytest.raises(core.PdfManagerError, match=words):
        stamps.save(bad)
    assert stamps.saved() == []


def test_settings_changed_by_hand_dont_break_the_list():
    update.save_settings(stamps=[{"label": "fine"}, "nonsense", {"label": ""}, {"label": "FINE"}])
    assert stamps.saved() == [{"label": "FINE", "color": stamps.DEFAULT_COLOR, "dated": False}]
    update.save_settings(stamps="nonsense")
    assert stamps.saved() == []


def test_the_server_keeps_and_lists_them(app):
    _, url = app
    base, token = url.split("#token=")
    base = base.rstrip("/")
    status, _, data = call(base, token, "/api/stamps")
    assert status == 200 and json.loads(data) == {"stamps": [], "most": 30}
    status, _, data = call(base, token, "/api/stamps", {"stamps": [{"label": "checked", "color": "#1f8a3b", "dated": True}]})
    assert status == 200 and json.loads(data)["stamps"] == [{"label": "CHECKED", "color": "#1f8a3b", "dated": True}]
    assert json.loads(call(base, token, "/api/stamps")[2])["stamps"][0]["label"] == "CHECKED"
    status, _, data = call(base, token, "/api/stamps", {"stamps": [{"label": ""}]})
    assert status == 400 and "needs words" in json.loads(data)["error"]
    assert call(base, "wrong", "/api/stamps")[0] == 403


def test_a_stamp_of_your_own_is_drawn_in_its_colour(tmp_path):
    out = tmp_path / "out.pdf"
    markup.apply(sheet(tmp_path / "sheet.pdf"), out, [
        {"op": "add", "page": 1, "kind": "stamp", "rect": [100, 500, 260, 550], "label": "CHECKED", "color": "#1f8a3b"}])
    annot = PdfReader(str(out)).pages[0]["/Annots"][0].get_object()
    assert annot["/Subtype"] == "/Stamp" and annot["/Subj"] == "Checked"
    assert [round(float(c), 2) for c in annot["/C"]] == [0.12, 0.54, 0.23]


def test_your_stamps_in_a_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = app
    ident = srv.RequestHandlerClass.opened.add(sheet(tmp_path / "Plan.pdf"))
    shots = os.environ.get("ZPDF_SHOTS")
    run = subprocess.run([node, str(HERE / "ownstamps.js"), url, ident] + ([shots] if shots else []),
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]

    assert out["atFirst"] == {"yours": [], "manage": True}
    assert out["saved"] == ["Checked"] and out["chosen"] == "own:CHECKED"          # added, listed under Yours and chosen
    assert out["datedBox"] is False                                                  # the box follows how it was saved
    assert out["placed"] == {"subtype": "Stamp", "color": [31, 138, 59]}
    assert out["afterReload"] == {"yours": ["Checked"], "chosen": "own:CHECKED"}    # there again when ZPDF starts again
    assert out["afterRemove"] == [] and stamps.saved() == []
