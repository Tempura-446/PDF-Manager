# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.24.4 (static/reader.js): with Select (no tool) on, a single click picks a
signature or a drawing, also after switching tabs and back (when both are
annotations in the tab's bytes, which pdf.js otherwise picks only on a
double-click); dragged it moves, Delete deletes, Save writes each once.
Links, form fields and selecting text still work with Select on. In a
browser through Node's Playwright (skipped where there is none)."""
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from pdfmanager import gui
from test_fixes241 import text_pdf
from test_fixes242 import annotations

HERE = Path(__file__).resolve().parent


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def letter_pdf(path):
    """A page with a heading, a link and a field to type in, low on the page
    (away from where the signature and the drawing go)."""
    c = canvas.Canvas(str(path))
    c.setFont("Helvetica-Bold", 20)
    c.drawString(72, 440, "Invoice 2026-114")
    c.setFont("Helvetica", 12)
    c.drawString(72, 260, "Terms and conditions")
    c.linkURL("https://example.com/terms", (70, 255, 200, 275))
    c.drawString(72, 200, "Name")
    c.acroForm.textfield(name="name", x=120, y=192, width=200, height=20)
    c.save()
    return Path(path)


def test_single_click_picks_after_a_tab_switch(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = app
    opened = srv.RequestHandlerClass.opened
    tab_a = letter_pdf(tmp_path / "Tab A.pdf")
    cfg = {"tabA": opened.add(tab_a), "tabB": opened.add(text_pdf(tmp_path / "Tab B.pdf"))}
    run = subprocess.run([node, str(HERE / "fix244.js"), url, json.dumps(cfg)], capture_output=True, text=True,
                         encoding="utf-8", timeout=600)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    if os.environ.get("FIX244_DUMP"):
        Path(os.environ["FIX244_DUMP"]).write_text(json.dumps(out, indent=1), encoding="utf-8")
    assert not out["errors"], out["errors"]
    assert not out["native"], out["native"]

    # Placed here, Select on: a single click picks the signature.
    assert out["placed"]["mode"] == "NONE" and sorted(e["type"] for e in out["placed"]["editors"]) == ["ink", "stamp"], out["placed"]
    assert [e["selected"] for e in out["pickedHere"]["editors"] if e["type"] == "stamp"] == [True], out["pickedHere"]

    # Back from the other tab with Select on: no editors, both drawn from the tab's bytes.
    back = out["back"]
    assert back["mode"] == "NONE" and back["annot"] and back["dirty"], back
    assert not [e for e in back["editors"] if not e["hidden"]], back
    assert back["shown"] == ["inkAnnotation", "stampAnnotation"], back

    # Links, form fields and selecting text: as before, Select still on.
    assert out["link"] == {"followed": True, "mode": "NONE"}, out["link"]
    assert out["field"] == {"value": "Ann", "mode": "NONE"}, out["field"]
    assert "Invoice" in out["text"]["selected"] and out["text"]["mode"] == "NONE", out["text"]

    # A single click picks the signature; dragged, it moves.
    picked = [e for e in out["pickedBack"]["editors"] if e["type"] == "stamp"]
    assert len(picked) == 1 and picked[0]["selected"] and picked[0]["id"], out["pickedBack"]
    [moved] = [e for e in out["moved"]["editors"] if e["type"] == "stamp"]
    assert moved["rect"][0] - picked[0]["rect"][0] > 20 and picked[0]["rect"][1] - moved["rect"][1] > 15, (picked, moved)

    # Esc (Select again) leaves it, and a single click picks the drawing; Delete deletes it.
    assert out["selectAgain"]["mode"] == "NONE" and out["selectAgain"]["annot"], out["selectAgain"]
    assert [e["selected"] for e in out["inkPicked"]["editors"] if e["type"] == "ink"] == [True], out["inkPicked"]
    left = out["afterDelete"]
    assert [e["type"] for e in left["editors"] if not e["hidden"]] == ["stamp"] and left["dirty"], left

    # Saved: the file has the signature once, where it was moved, and no drawing.
    assert not out["saved"]["dirty"], out["saved"]
    annots = [a for a in annotations(tab_a) if a[1] in ("/Stamp", "/Ink")]
    assert [kind for _, kind, _ in annots] == ["/Stamp"], annotations(tab_a)
    assert all(abs(p - q) < 1.5 for p, q in zip(annots[0][2], moved["rect"])), (annots, moved)
