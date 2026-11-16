# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.47.0: a markup changed after it's placed, in the reader (markedit.js
drives it in a browser): selected, reshaped, nudged, recoloured, a
dimension's end moved, a line deleted; and through the server."""

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from pdfmanager import gui, markup
from test_construction_markups import sheet
from test_more import call, multipart

HERE = Path(__file__).parent
QUARTER = {"ratio": 48, "units": "ft", "label": "1/4\" = 1'-0\""}


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def marked(tmp_path):
    out = tmp_path / "Plan.pdf"
    markup.apply(sheet(tmp_path / "sheet.pdf"), out, [
        {"op": "add", "page": 1, "kind": "rect", "rect": [100, 500, 200, 560], "color": "#d52b1e", "width": 2},
        {"op": "add", "page": 1, "kind": "dimension", "points": [[100, 300], [244, 300]], "value": "8' 0\"", "scale": QUARTER},
        {"op": "add", "page": 1, "kind": "line", "points": [[100, 420], [300, 420]]},
        {"op": "add", "page": 1, "kind": "underline", "rects": [[72, 795, 140, 810]]},
        {"op": "add", "page": 1, "kind": "callout", "tip": [420, 300], "at": [470, 360], "text": "Check this"},
        {"op": "add", "page": 1, "kind": "cloud", "rect": [400, 480, 520, 560], "width": 4},
    ], author="Ada")
    return out


def test_the_server_lists_what_can_be_changed(app, tmp_path):
    _, url = app
    base, token = url.split("#token=")
    base = base.rstrip("/")
    body, headers = multipart({}, [("file", marked(tmp_path))])
    status, _, data = call(base, token, "/api/markup/describe", body, headers)
    shapes = json.loads(data)["pages"]["1"]
    assert status == 200 and [s["kind"] for s in shapes] == ["rect", "dimension", "line", "callout", "cloud"]     # not the underline
    assert all(s["id"].endswith("R") and len(s["bounds"]) == 4 for s in shapes)
    assert shapes[0]["rect"] == [100.0, 500.0, 200.0, 560.0] and shapes[1]["value"] == "8' 0\""
    assert shapes[1]["scale"]["units"] == "ft"


def test_changing_markups_in_a_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = app
    ident = srv.RequestHandlerClass.opened.add(marked(tmp_path))
    shots = os.environ.get("ZPDF_SHOTS")
    run = subprocess.run([node, str(HERE / "markedit.js"), url, ident] + ([shots] if shots else []),
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]

    assert len(out["start"]) == 6 and out["start"][0]["rect"] == [99, 499, 201, 561]
    assert out["rectSelected"] == {"kind": "rect", "index": 0, "grips": 4}
    assert out["moving"]                                             # shown as it would be, while dragged
    resized = out["afterResize"]["annots"][0]["rect"]
    assert abs(resized[0] - 99) <= 1 and abs(resized[1] - 499) <= 1 and abs(resized[2] - 261) <= 3 and abs(resized[3] - 601) <= 3
    assert out["afterResize"]["selected"]["kind"] == "rect"          # still selected after the change
    assert out["afterNudge"][0] == resized[0] + 10 and out["afterNudge"][2] == resized[2] + 10
    # A held arrow key: every press counts, however they fall among the writes; the page doesn't scroll.
    assert out["afterHold"][0] == out["afterNudge"][0] + 12 and out["scrolled"] == 0
    # Esc while the key is held: let go for good; the presses after it move nothing and scroll nothing.
    assert out["escLetGo"] and 1 <= out["afterEsc"][0] - out["afterHold"][0] <= 4 and out["escScrolled"] == 0
    assert out["binReady"] and out["cloudByItsBumps"] == "cloud"
    assert out["wordsBox"] and out["afterWords"] == "Wall moved"       # a double-click opens the callout's words
    assert out["afterColour"] == [31, 79, 216]                       # the bar's blue
    assert out["dimSelected"] == {"kind": "dimension", "index": 1, "grips": 2}
    assert out["afterDim"]["text"] == "12' 0\"" and abs(out["afterDim"]["line"][2] - 316) <= 2
    assert out["lineSelected"]["kind"] == "line" and out["afterDelete"] == 5
    assert out["letGo"] and out["afterDraw"] == 6                    # drawing still works beside it
