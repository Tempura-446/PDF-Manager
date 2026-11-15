# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.38.0: the reader's markups for drawings (drawmarks.js drives them in a
browser): clouds and arrows through clicked points, a dimension that snaps
to the drawing and reads its scale, a callout, the review stamps."""

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from pdfmanager import gui

HERE = Path(__file__).parent


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def drawing(path):
    """A box 2 inches wide (144 points), its bottom left corner at 100, 300."""
    c = canvas.Canvas(str(path), pagesize=A4)
    c.rect(100, 300, 144, 100)
    c.drawString(100, 780, "Sheet A-101")
    c.showPage()
    c.save()
    return path


def test_markups_for_drawings_in_a_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = app
    ident = srv.RequestHandlerClass.opened.add(drawing(tmp_path / "Plan.pdf"))
    shots = os.environ.get("ZPDF_SHOTS")
    run = subprocess.run([node, str(HERE / "drawmarks.js"), url, ident] + ([shots] if shots else []),
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]

    assert {"polycloud", "polyline", "polyarrow", "dimension", "callout"} <= set(out["options"]["shapes"])
    assert {"APPROVED AS NOTED", "REVISE AND RESUBMIT", "REJECTED", "FOR INFORMATION",
            "NOT FOR CONSTRUCTION"} <= set(out["options"]["stamps"])
    assert out["steps"]["drafting"] and out["steps"]["givenUp"]
    assert out["steps"]["dimDraft"] == "8' 0\""            # shown while dragging

    cloud, arrow, dim, callout, stamp = out["annots"]
    assert cloud["subtype"] == "Polygon" and len(cloud["vertices"]) == 8
    assert [round(v) for v in cloud["vertices"]] == [330, 620, 500, 620, 500, 740, 330, 740]
    assert arrow["subtype"] == "PolyLine" and len(arrow["vertices"]) == 6
    assert arrow["ends"] == ["None", "ClosedArrow"]
    # the dimension's ends sit on the box's corners, and it carries the real length
    assert dim["subtype"] == "Line" and dim["text"] == "8' 0\""
    assert [round(v, 1) for v in dim["line"]] == [100.0, 300.0, 244.0, 300.0]
    assert callout["subtype"] == "FreeText" and callout["text"] == "Move this wall"
    assert stamp["subtype"] == "Stamp"
