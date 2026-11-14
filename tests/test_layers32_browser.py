"""0.32.0 (static/layers.js, snap.js, reader.js): the reader's Layers tab in
a browser, through Node's Playwright (skipped where there is none). It shows
only for a PDF with layers, nested as the file orders them; hiding a layer
stops the page drawing it, hides its words in the text layer and keeps
snapping off its lines; each tab keeps its own choice; printing and the
thumbnails draw the layers shown; Save layer visibility is a change with
Undo. FIX32_SHOTS=<folder> saves light and dark screenshots of the tab."""
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from pdfmanager import gui
from layerpdfs import layered_pdf, plain_pdf

HERE = Path(__file__).resolve().parent
BLUE, WHITE, BLACK = [0, 0, 255], [255, 255, 255], [0, 0, 0]


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def near(pixel, colour, slack=60):
    return all(abs(a - b) <= slack for a, b in zip(pixel, colour))


def test_the_layers_tab_in_a_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = app
    opened = srv.RequestHandlerClass.opened
    cfg = {"plain": opened.add(plain_pdf(tmp_path / "Plain.pdf")), "plan": opened.add(layered_pdf(tmp_path / "Plan.pdf")),
           "shots": os.environ.get("FIX32_SHOTS")}
    run = subprocess.run([node, str(HERE / "fix32.js"), url, json.dumps(cfg)], capture_output=True, text=True,
                         encoding="utf-8", timeout=600)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    if os.environ.get("FIX32_DUMP"):
        Path(os.environ["FIX32_DUMP"]).write_text(json.dumps(out, indent=1), encoding="utf-8")
    assert not out["errors"], out["errors"]
    assert not out["native"], out["native"]

    # No layers, no tab.
    assert out["plain"] == {"tab": False, "layers": None}, out["plain"]

    # The file's nested order: Dimensions under Walls, the Options heading over its radio group.
    assert out["tree"] == [{"name": "Walls", "items": [{"name": "Dimensions", "items": []}]},
                           {"head": "Options", "items": [{"name": "Scheme A", "items": []}, {"name": "Scheme B", "items": []}]}], out["tree"]
    b = out["before"]
    assert b["tab"] and b["boxes"] == {
        "Walls": {"type": "checkbox", "checked": True, "disabled": True},
        "Dimensions": {"type": "checkbox", "checked": True, "disabled": False},
        "Scheme A": {"type": "radio", "checked": True, "disabled": False},
        "Scheme B": {"type": "radio", "checked": False, "disabled": False}}, b["boxes"]
    assert near(b["dimsLine"], BLUE) and near(b["wallsLine"], BLACK), b
    assert b["text"] == {"dims": True, "walls": True, "schemeA": True, "schemeB": False}, b["text"]
    assert b["snapDims"] and b["snapDims"][2] == 600 and b["snapWalls"][2] == 150, b

    # Dimensions hidden: not drawn, its words out of the text layer, its line not snapped to.
    h = out["hidden"]
    assert not h["boxes"]["Dimensions"]["checked"], h
    assert near(h["dimsLine"], WHITE) and near(h["wallsLine"], BLACK), h
    assert h["text"]["dims"] is False and h["text"]["walls"] is True, h["text"]
    assert h["snapDims"] is None and h["snapWalls"][2] == 150, h
    assert not h["dirty"], h  # (only how it's shown: not a change to the file)

    # Locked stays on; the radio group swaps.
    assert out["lockedClick"] is True
    r = out["radio"]
    assert r["boxes"]["Scheme A"]["checked"] is False and r["boxes"]["Scheme B"]["checked"] is True, r["boxes"]
    assert r["text"]["schemeA"] is False and r["text"]["schemeB"] is True, r["text"]

    # Per tab: the other tab has no layers; back here, the choice is as it was.
    assert out["otherTab"] == {"tab": False} and out["backPanel"] is True
    k = out["back"]
    assert not k["boxes"]["Dimensions"]["checked"] and k["boxes"]["Scheme B"]["checked"], k["boxes"]
    assert near(k["dimsLine"], WHITE) and k["text"]["dims"] is False and k["snapDims"] is None, k

    # Printing: each page drawn for print with the layers shown.
    assert out["print"] and all(p["config"] == {"Walls": True, "Dimensions": False, "Scheme A": False, "Scheme B": True}
                                for p in out["print"]), out["print"]
    # Thumbnails drawn again, with the layers shown (Dimensions on again then).
    assert out["thumbs"] and out["thumbs"][-1]["config"]["Dimensions"] is True, out["thumbs"]

    # Save layer visibility: a change with Undo; the file opens that way.
    s = out["saved"]
    assert s["dirty"] and s["undo"] == 1, s
    assert out["savedDefault"] == {"Walls": True, "Dimensions": False, "Scheme A": False, "Scheme B": True}, out["savedDefault"]
    assert out["undone"] == {"Walls": True, "Dimensions": True, "Scheme A": True, "Scheme B": False}, out["undone"]
