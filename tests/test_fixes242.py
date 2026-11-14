# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Fixes from the owner's Windows check of 0.24.1 (static/reader.js): a
signature put on its line stays drawn there while another is selected and
after a zoom or a turn; annotations not saved yet stay editors after
switching tabs and back, and Save writes each once; typing straight after
the Sign dialog opens keeps every letter. In a browser through Node's
Playwright (skipped where there is none)."""
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from pypdf import PdfReader

from pdfmanager import gui
from test_fixes241 import invoice_pdf, text_pdf

HERE = Path(__file__).resolve().parent


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def annotations(path):
    """Each annotation in the file: (page, subtype, rect)."""
    found = []
    for n, page in enumerate(PdfReader(str(path)).pages, 1):
        for ref in page.get("/Annots") or []:
            a = ref.get_object()
            found.append((n, str(a.get("/Subtype")), [float(v) for v in a.get("/Rect")]))
    return found


def test_fixes242_in_a_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = app
    docs = tmp_path / "docs"
    docs.mkdir()
    opened = srv.RequestHandlerClass.opened
    tab_a = invoice_pdf(docs / "Tab A.pdf")
    cfg = {"invoiceIds": [opened.add(invoice_pdf(docs / f"Invoice {n}.pdf")) for n in (1, 2)],
           "tabA": opened.add(tab_a), "tabB": opened.add(text_pdf(docs / "Tab B.pdf")),
           "typeId": opened.add(invoice_pdf(docs / "Typing.pdf")),
           "shots": os.environ.get("FIX242_SHOTS")}
    run = subprocess.run([node, str(HERE / "fix242.js"), url, json.dumps(cfg)], capture_output=True, text=True,
                         encoding="utf-8", timeout=900)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    if os.environ.get("FIX242_DUMP"):
        Path(os.environ["FIX242_DUMP"]).write_text(json.dumps(out, indent=1), encoding="utf-8")
    assert not out["errors"], out["errors"]
    assert not out["native"], out["native"]

    # A. Sign it puts the first on the line; with a second one selected, and after zooming
    # in, out and turning the view, the first is still drawn where its rectangle is, and its
    # rectangle hasn't moved (at 100% and 150% screen scale). (pdf.js draws a picture's box
    # its outline's width up and left, 2 to 4 px: within 6 px, where 0.24.1 was about 50 pt
    # off on Windows.)
    for dpr in ("1", "1.5"):
        a = out["lines-" + dpr]
        x0, _, x1, top = a["line"]["rect"]
        [first] = a["first"]
        assert abs(first["rect"][0] - x0) < 1 and abs(first["rect"][1] - top) < 1.5, (first, a["line"])
        assert first["off"] <= 6, first
        assert len(a["second"]) == 2, a["second"]
        one, two = a["second"]
        assert two["selected"] and not one["selected"], a["second"]
        assert a["handles"] == ["bottomLeft", "bottomRight", "topLeft", "topRight"], a["handles"]  # 0.24.3
        for step in ("second", "zoom150", "zoom75", "turned"):
            now = a[step][0]
            assert now["rect"] == first["rect"], (step, now, first)
            assert now["off"] <= 6, (dpr, step, now)
            assert a[step][1]["off"] <= 6, (dpr, step, a[step][1])

    # B. Before the switch: a signature and a drawing, not saved, Draw on.
    b = out["tabs"]
    assert sorted(e["type"] for e in b["before"]["editors"]) == ["ink", "stamp"], b["before"]
    assert b["before"]["state"] == {"dirty": True, "dot": True, "tabDot": True}, b["before"]
    # Back from the other tab: the same tool, both still editors where they were, still unsaved.
    assert b["before"]["mode"] == b["back"]["mode"] == "INK" and b["back"]["annot"], b["back"]
    assert sorted(e["type"] for e in b["back"]["editors"]) == ["ink", "stamp"], b["back"]
    for e in b["back"]["editors"]:
        was = next(x for x in b["before"]["editors"] if x["type"] == e["type"])
        assert all(abs(p - q) < 1 for p, q in zip(e["rect"], was["rect"])), (e, was)
        assert e["off"] <= 6 and not e["hidden"] and e["id"], e  # (an editor of the annotation now in its bytes)
    assert b["back"]["state"] == {"dirty": True, "dot": True, "tabDot": True}, b["back"]
    # Picked with a click, moved by dragging; the drawing picked and deleted.
    assert b["picked"]["selected"], b["picked"]
    start = next(x for x in b["back"]["editors"] if x["type"] == "stamp")["rect"]
    assert b["moved"]["rect"][0] - start[0] > 20 and start[1] - b["moved"]["rect"][1] > 15, (start, b["moved"])
    assert b["inkPicked"], b
    assert [e["type"] for e in b["afterDelete"]["editors"]] == ["stamp"], b["afterDelete"]
    assert b["afterDelete"]["state"]["dirty"], b["afterDelete"]
    # With no tool on (Select), a double-click picks it too.
    assert b["selectTool"], b
    # Saved: quiet again, and the file has the signature once, where it was moved, and no drawing.
    assert b["saved"] == {"dirty": False, "dot": False, "tabDot": False}, b["saved"]
    annots = annotations(tab_a)
    assert [kind for _, kind, _ in annots] == ["/Stamp"], annots
    rect = annots[0][2]
    assert all(abs(p - q) < 1.5 for p, q in zip(rect, b["moved"]["rect"])), (rect, b["moved"])

    # C. Typed straight after opening the dialog (Sign it, the Sign button, the S key):
    # every letter in the name box, focused, with no form filling offered.
    assert [t["how"] for t in out["typing"]] == ["sign it", "sign button", "S key"], out["typing"]
    for t in out["typing"]:
        assert t["value"] == "Test Signer" and t["focused"] and t["autocomplete"] == "off", t
