"""Accessibility and usability fixes, in a browser (Node's Playwright, skipped
where there is none): focus never hides under the sticky action bar, focus
rings and field borders that can be seen, the annotate bar and Settings fit,
focus after a search, unreadable files, skip links, Escape, Undo for Recent,
the password field and target sizes. See a11yfix.js."""
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pikepdf
import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from pdfmanager import gui

HERE = Path(__file__).resolve().parent
LINE = "Payment is due within thirty days; a balance due after that date carries interest."


def report_pdf(path, pages=4):
    """Pages of text with "balance due" on each, and bookmarks (one nested)."""
    c = canvas.Canvas(str(path), pagesize=A4)
    for p in range(1, pages + 1):
        c.bookmarkPage(f"p{p}")
        c.addOutlineEntry(f"Chapter {p}", f"p{p}", level=0)
        if p == 1:
            c.addOutlineEntry("Payment terms", "p1", level=1)
        c.setFont("Helvetica", 11)
        for row in range(6):
            c.drawString(60, 760 - row * 18, LINE)
        c.showPage()
    c.save()
    return path


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def test_accessibility_fixes_in_a_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = app
    docs = tmp_path / "docs"
    docs.mkdir()
    text = report_pdf(docs / "Quarterly report.pdf")
    for n in range(3):
        report_pdf(docs / f"Report {n}.pdf", pages=3)
    locked = tmp_path / "Locked.pdf"
    with pikepdf.open(text) as pdf:
        pdf.save(locked, encryption=pikepdf.Encryption(user="open-me", owner="owner"))
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\n1 0 obj << /Type /Catalog /Pages 2 0 R >>\nthis is not really a pdf\n" + os.urandom(3000))
    for p in (docs / "Report 0.pdf", docs / "Report 1.pdf", text):
        gui.RecentFiles.add(p)
    opened = srv.RequestHandlerClass.opened
    cfg = {"text": opened.add(text), "locked": opened.add(locked), "docs": str(docs), "broken": str(broken), "good": str(docs / "Report 2.pdf")}
    run = subprocess.run([node, str(HERE / "a11yfix.js"), url, json.dumps(cfg)], capture_output=True, text=True, encoding="utf-8", timeout=600)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    if os.environ.get("A11Y_DUMP"):  # what was measured, for looking at
        Path(os.environ["A11Y_DUMP"]).write_text(json.dumps(out, indent=1), encoding="utf-8")
    assert not out["errors"], out["errors"]

    # Field borders: 3:1 against the field and around it, light and dark; a solid 2px focus ring.
    for theme in ("light", "dark"):
        t = out[theme]
        for key in ("query", "how", "repo"):
            assert t[key] >= 3, (theme, key, t[key])
        assert t["fieldRing"][0] == "solid" and t["fieldRing"][1] == "2px", t["fieldRing"]
        # Settings fits a 1280 × 800 window: Save shows, inside the dialog.
        s = t["settings"]
        assert s["hit"] and s["saveBottom"] <= s["dialogBottom"] <= s["innerHeight"], s
    for key in ("findInput", "pageInput", "zoomInput"):
        assert out[key] >= 3, (key, out[key])

    # Skip link: first Tab stop, shown, and Enter moves into the main content (the token stays).
    assert "skip-link" in out["skip"]["cls"] and out["skip"]["visible"], out["skip"]
    assert out["skipTo"]["inMain"] and out["skipTo"]["hash"], out["skipTo"]

    # Recent: the remove button shows when its row has focus, is 24px+, and its toast offers Undo.
    assert out["recentHoverOpacity"] == "1"
    assert min(out["recentX"]) >= 24, out["recentX"]
    assert out["recentToast"]["button"] == "Undo" and out["recentToast"]["text"].startswith("Removed ")
    assert len(out["recentRemoved"]) == len(out["recentBefore"]) - 1
    assert out["recentAfter"] == out["recentBefore"]
    assert set(out["recentServer"]) >= set(out["recentBefore"])
    assert min(out["toastUndo"]) >= 24, out["toastUndo"]

    # Search: focus on the summary, clear of the action bar; rows are buttons with names.
    sf = out["searchFocus"]
    assert sf["inResult"] and sf["text"].startswith("Found on"), sf
    assert sf["bottom"] <= sf["barTop"] and sf["top"] >= 0, sf
    assert out["rows"] and all(r["role"] == "button" and r["label"].startswith("Open page ") and r["label"].endswith(".pdf") for r in out["rows"]), out["rows"][:2]
    lr = out["lastRow"]
    assert lr and lr["bottom"] <= lr["barTop"] + 0.5, lr
    assert out["rowRing"] == ["solid", "2px"], out["rowRing"]
    req = out["required"]
    assert req["invalid"] == "true" and req["near"] and req["msg"] and req["banner"], req

    # An unreadable file isn't ready: Run waits, the bar says why, the message is plain.
    assert out["broken"]["run"] and "can't be opened" in out["broken"]["summary"], out["broken"]
    br = out["brokenRun"]
    assert br["error"] and "can't be opened" in br["error"] and "Stream" not in br["error"] and br["focus"] == "error" and br["details"] and br["repair"], br
    assert out["plain"].startswith("broken.pdf can't be opened") and "Repair" in out["plain"], out["plain"]
    assert out["disabledCursor"] == "not-allowed"
    assert not out["repairRun"]
    assert not out["mixed"]["run"] and out["mixed"]["summary"].startswith("Ready · 1 file") and "can't be opened" in out["mixed"]["summary"], out["mixed"]
    assert min(out["cardBtn"]) >= 24
    assert out["pwRow"] == {"label": "Current password", "autocomplete": "current-password", "toggle": True}, out["pwRow"]

    # The annotate bar: Save, Close and Undo show without scrolling it, at every width.
    for w in (1024, 1280, 1440, 1920):
        a = out[f"annot{w}"]
        assert a["save"] and a["close"] and a["undo"], (w, a)
    assert out["annot1920"]["labels"]
    assert all(out[f"annot{w}"]["saveWord"] for w in (1024, 1280, 1440, 1920)), "Save keeps its word"

    # Reader: no Tab stop per page; the page view shows focus from the keyboard, not when it opens.
    assert out["textLayerTab"] and set(out["textLayerTab"]) == {-1}, out["textLayerTab"]
    assert out["containerFocusOnLoad"] == {"focused": True, "visible": False}
    assert "rd-skip" in out["rdSkip"]["cls"] and out["rdSkip"]["visible"]
    assert out["rdSkipTo"]["container"] and out["rdSkipTo"]["outline"] == "solid" and out["rdSkipTo"]["offset"].startswith("-"), out["rdSkipTo"]
    assert out["findEsc"]["label"].startswith("Find in document"), out["findEsc"]
    assert out["annotEsc1"] == {"open": True, "mode": "NONE"}, out["annotEsc1"]
    assert not out["annotEsc2"]["open"] and out["annotEsc2"]["focus"]["label"].startswith("Annotate"), out["annotEsc2"]
    assert out["menuRing"] == {"inMenu": True, "style": "solid", "width": "2px"}, out["menuRing"]
    for key in ("twisty", "thumbAct", "paragraph", "rdToastUndo"):
        assert out[key] and min(out[key][:2]) >= 24, (key, out[key])
    assert out["paragraph"][2] is not False  # its focus ring is the pill's light ink, not teal on dark

    # Password: label, toggle, autocomplete; a wrong one is marked; Escape doesn't drop focus on the page.
    pw = out["pw"]
    assert pw["label"] == "Password" and pw["autocomplete"] == "current-password" and pw["toggle"] == "false", pw
    assert min(pw["toggleSize"]) >= 24
    assert out["pwShown"] == ["true", "text"]
    wrong = out["pwWrong"]
    assert wrong["invalid"] == "true" and wrong["errId"] and wrong["errId"] in wrong["describedby"].split() and wrong["focus"], wrong
    assert not out["pwEsc"]["open"] and out["pwEsc"]["focus"] != "BODY", out["pwEsc"]

    assert out["paletteSub"] >= 4.5, out["paletteSub"]
