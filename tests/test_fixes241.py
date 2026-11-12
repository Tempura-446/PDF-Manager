"""Fixes from the owner's Windows check of 0.24.0 (filefacts.py, gui.html,
static/reader.js): the signature line's place on the page, and Sign it
putting the signature on it; Sign without a line at a moderate size in
view; Undo back to the saved state clearing the unsaved dot; the annotate
bar coming back after find; the shortcuts sheet's descriptions on one
edge; turning a page just after switching tabs. In a browser through
Node's Playwright (skipped where there is none)."""
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from pdfmanager import gui

HERE = Path(__file__).resolve().parent


def invoice_pdf(path):
    """An invoice with "Signature: ____" near its foot, and a rule beside "Date"."""
    c = canvas.Canvas(str(path))
    c.setFont("Helvetica-Bold", 20)
    c.drawString(72, 740, "Invoice 2026-114")
    c.setFont("Helvetica", 11)
    for n, (what, amount) in enumerate([("Garden design, 6 hours", "480.00"), ("Plants and soil", "212.50"),
                                        ("Delivery", "35.00")]):
        c.drawString(72, 680 - n * 20, what)
        c.drawRightString(520, 680 - n * 20, amount)
    c.line(72, 612, 520, 612)
    c.drawString(72, 594, "Total")
    c.drawRightString(520, 594, "727.50")
    c.setFont("Helvetica", 12)
    c.drawString(72, 160, "Signature: ______________________________")
    c.drawString(360, 160, "Date")
    c.line(392, 158, 520, 158)
    c.save()
    return Path(path)


def text_pdf(path, pages=2, words="Quarterly report", field=False):
    """``field``: with a field to type in on its first page (pdf.js then
    starts its form scripting for it)."""
    c = canvas.Canvas(str(path))
    for n in range(pages):
        c.drawString(72, 720, f"{words}, page {n + 1}")
        if field and n == 0:
            c.drawString(72, 660, "Name")
            c.acroForm.textfield(name="name", x=120, y=652, width=220, height=20)
        c.showPage()
    c.save()
    return Path(path)


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def test_fixes241_in_a_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = app
    docs = tmp_path / "docs"
    docs.mkdir()
    opened = srv.RequestHandlerClass.opened
    # (Fields on the pages to turn: pdf.js starts form scripting for them, which was where the error came from.)
    cfg = {"invoiceIds": {theme: opened.add(invoice_pdf(docs / f"Invoice {theme}.pdf")) for theme in ("light", "dark")},
           "plainId": opened.add(text_pdf(docs / "Plain.pdf")),
           "turnA": opened.add(text_pdf(docs / "Turn A.pdf", 3, "First file", field=True)),
           "turnB": opened.add(text_pdf(docs / "Turn B.pdf", 2, "Second file", field=True)),
           "shots": os.environ.get("FIX241_SHOTS")}
    run = subprocess.run([node, str(HERE / "fix241.js"), url, json.dumps(cfg)], capture_output=True, text=True,
                         encoding="utf-8", timeout=900)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    if os.environ.get("FIX241_DUMP"):
        Path(os.environ["FIX241_DUMP"]).write_text(json.dumps(out, indent=1), encoding="utf-8")
    assert not out["errors"], out["errors"]
    assert not out["native"], out["native"]

    # 1. Sign it: on the line the facts found (the invoice's "Signature: ____" on page 1), its foot
    # on the rule at the line's start, 1.6 times the label's height, and resizable like any picture.
    for theme in ("light", "dark"):
        s = out["sign-" + theme]
        if s.get("deleteIcon"):
            assert s["deleteIcon"]["lineHeight"] != "250px", s["deleteIcon"]
            assert s["deleteIcon"]["icon"] <= s["deleteIcon"]["button"] + 1, s["deleteIcon"]
        line = s["line"]
        assert line["page"] == 1 and line["text"] == 12, line
        x0, y0, x1, top = line["rect"]
        assert 120 < x0 < 160 and 300 < x1 < 360 and 155 < top < 162, line
        r = s["rect"]
        assert s["page"] == 1 and s["resizable"] and s["inView"], s
        assert abs(r[0] - x0) < 1 and r[2] <= x1 + 1, (r, line)  # left-aligned, no longer than the line
        assert abs(r[1] - top) < 1.5, (r, line)  # its foot on the rule
        height = r[3] - r[1]
        assert abs(height - 1.6 * 12) < 1 or abs((r[2] - r[0]) - (x1 - x0)) < 1, (height, r)
        assert 10 < height < 25 and r[2] - r[0] > 20, r
        assert s["after"] == {"dirty": True, "dot": True, "seal": True}, s["after"]
    # 1b. Sign with no line known: 2 inches wide, in sight (not three quarters of the page).
    p = out["signPlain"]
    assert abs((p["rect"][2] - p["rect"][0]) - 144) < 2 and p["rect"][3] - p["rect"][1] < 73 and p["inView"], p
    assert abs((p["rect"][0] + p["rect"][2]) / 2 - 306) < 30, p  # in the middle of what's shown of the page

    # 2. Undo of the only change: not changed (no dot, Save quiet); Redo: changed again.
    s = out["sign-light"]
    assert s["undoneEditors"] == 0
    assert s["undone"] == {"dirty": False, "dot": False, "seal": False}, s["undone"]
    assert s["redone"] == {"dirty": True, "dot": True, "seal": True}, s["redone"]
    # After a save, a new change undone is back at the saved state.
    assert s["saved"] == {"dirty": False, "dot": False, "seal": False}, s["saved"]
    assert s["second"]["dirty"] and s["second"]["dot"], s["second"]
    assert s["secondUndone"] == {"dirty": False, "dot": False, "seal": False}, s["secondUndone"]

    # 3. E, Ctrl+F, Escape: the annotate bar comes back (with its tool), the panel still aside.
    b = out["bars"]
    assert b["start"]["side"] and not b["start"]["annot"], b["start"]
    assert b["annotating"]["annot"] and b["annotating"]["mode"] == "EDITTEXT" and not b["annotating"]["side"], b
    assert b["finding"]["find"] and not b["finding"]["annot"] and not b["finding"]["side"], b["finding"]
    assert b["back"] == {"side": False, "find": False, "annot": True, "mode": "EDITTEXT"}, b["back"]
    assert b["closed"]["side"] and not b["closed"]["annot"] and not b["closed"]["find"], b["closed"]
    # The other way round: the find bar comes back when the annotate bar opened over it closes.
    assert b["annotOverFind"]["annot"] and not b["annotOverFind"]["find"], b["annotOverFind"]
    assert b["findBack"]["find"] and not b["findBack"]["annot"] and not b["findBack"]["side"], b["findBack"]

    # 4. The shortcuts sheet: every description starts the same distance from its section's
    # edge (one key column for all), and in each column on one line.
    for theme in ("light", "dark"):
        secs = out["keys-" + theme]
        assert len(secs) == 5, secs
        offsets = [dd - sec["left"] for sec in secs for dd in sec["dd"]]
        assert max(offsets) - min(offsets) <= 1, secs
        for left in {round(sec["left"]) for sec in secs}:
            edges = [dd for sec in secs if round(sec["left"]) == left for dd in sec["dd"]]
            assert max(edges) - min(edges) <= 1, secs
        assert not any(sec["dtOver"] for sec in secs), secs

    # 5. Turning page 1 just after switching tabs: no error, and it's the tab switched to that
    # turns (each time a quarter more), never the other one.
    for n, t in enumerate(out["turn"], 1):
        assert not t["errors"], t
        assert t["active"] == 0 and t["rotateA"] == (90 * n) % 360 and t["rotateB"] == 0, t
        assert t["pagesA"] == 3 and t["pagesB"] == 2, t
