"""Fixes from the 0.23 interface critique (gui.html, static/reader.js,
reader.css, gui.py): Save… and Show in folder after a panel run, the unsaved
dot, Undo after Save wrote over a file, the app's own dialogs instead of the
browser's confirm(), Save as the seal action; the annotate bar's labels, its
More tools menu and single keys; the rail's groups; Search results; the
compact file strip; error flags and verb buttons. In a browser through Node's
Playwright (skipped where there is none), plus the server checks without it."""
import json
import os
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from reportlab.pdfgen import canvas

from pdfmanager import gui

HERE = Path(__file__).resolve().parent


def text_pdf(path, pages=3, words="Quarterly report"):
    c = canvas.Canvas(str(path))
    for n in range(pages):
        c.drawString(72, 720, f"{words}, page {n + 1}")
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


def _post(url, path, data, token):
    base = "{0.scheme}://{0.netloc}".format(urlsplit(url))
    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers={"X-Pdfm-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def test_reveal_only_opens_files_the_app_knows(app, tmp_path, monkeypatch):
    srv, url = app
    token = srv.RequestHandlerClass.token
    shown = []
    monkeypatch.setattr(gui, "reveal_in_file_manager", lambda p: shown.append(str(p)))
    known = text_pdf(tmp_path / "Known.pdf")
    stranger = text_pdf(tmp_path / "Stranger.pdf")
    key = srv.RequestHandlerClass.opened.add(known)
    assert _post(url, "/api/reveal", json.dumps({"path": str(stranger)}).encode(), token)[0] == 403
    assert _post(url, "/api/reveal", json.dumps({"path": str(tmp_path)}).encode(), token)[0] == 403
    assert _post(url, "/api/reveal", json.dumps({"path": str(known)}).encode(), token)[0] == 200
    assert _post(url, "/api/reveal", json.dumps({"id": key}).encode(), token)[0] == 200
    assert shown == [str(known.resolve())] * 2
    # No token, no reveal.
    assert _post(url, "/api/reveal", json.dumps({"path": str(known)}).encode(), "wrong")[0] == 403
    assert len(shown) == 2


def test_save_over_a_file_can_be_undone_unless_it_changed_since(app, tmp_path):
    srv, url = app
    token = srv.RequestHandlerClass.token
    path = text_pdf(tmp_path / "Report.pdf")
    before = path.read_bytes()
    key = srv.RequestHandlerClass.opened.add(path)
    base = "{0.scheme}://{0.netloc}".format(urlsplit(url))
    newer = text_pdf(tmp_path / "newer.pdf", words="Changed").read_bytes()

    def save():
        req = urllib.request.Request(f"{base}/api/save/{key}", data=newer, method="POST",
                                     headers={"X-Pdfm-Token": token, "Content-Type": "application/pdf"})
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read())

    answer = save()
    assert answer["undo"] and path.read_bytes() == newer
    status, back = _post(url, "/api/unsave", json.dumps({"undo": answer["undo"]}).encode(), token)
    assert status == 200 and path.read_bytes() == before and back["stamp"]
    # Once only.
    assert _post(url, "/api/unsave", json.dumps({"undo": answer["undo"]}).encode(), token)[0] == 400
    # Changed by something else after the save: left as it is.
    answer = save()
    path.write_bytes(newer + b"\n% edited elsewhere\n")
    status, said = _post(url, "/api/unsave", json.dumps({"undo": answer["undo"]}).encode(), token)
    assert status == 400 and "changed since" in said["error"]
    assert path.read_bytes().endswith(b"% edited elsewhere\n")
    # Too big to keep in memory: no Undo offered.
    gui.SavedOver.LIMIT, limit = 10, gui.SavedOver.LIMIT
    try:
        req = urllib.request.Request(f"{base}/api/save/{key}", data=newer, method="POST",
                                     headers={"X-Pdfm-Token": token, "Content-Type": "application/pdf"})
        with urllib.request.urlopen(req) as res:
            assert json.loads(res.read())["undo"] is None
    finally:
        gui.SavedOver.LIMIT = limit


def test_no_browser_confirm_or_alert_is_left():
    for name in ("gui.html", "static/reader.js", "static/recipes.js"):
        text = (HERE.parent / "pdfmanager" / name).read_text(encoding="utf-8")
        code = "\n".join(line for line in text.splitlines() if not line.strip().startswith(("//", "*", "/*")))
        for call in ("confirm(", "alert("):
            stray = [ln for ln in code.splitlines() if call in ln and "window.confirm(title)" not in ln and "never the browser" not in ln]
            assert not stray, (name, stray)


def test_critique_fixes_in_a_browser(app, tmp_path, monkeypatch):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = app
    shown = []
    monkeypatch.setattr(gui, "reveal_in_file_manager", lambda p: shown.append(str(p)))
    docs = tmp_path / "docs"
    docs.mkdir()
    plain = text_pdf(docs / "Quarterly report.pdf")
    undo = text_pdf(docs / "Minutes.pdf", pages=2, words="Meeting minutes")
    opened = srv.RequestHandlerClass.opened
    cfg = {"plainId": opened.add(plain), "plainPath": str(plain), "savePath": str(plain),
           "undoId": opened.add(undo), "undoPath": str(undo), "shots": os.environ.get("CRITFIX_SHOTS")}
    run = subprocess.run([node, str(HERE / "critfix.js"), url, json.dumps(cfg)], capture_output=True, text=True,
                         encoding="utf-8", timeout=900)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    if os.environ.get("CRITFIX_DUMP"):
        Path(os.environ["CRITFIX_DUMP"]).write_text(json.dumps(out, indent=1), encoding="utf-8")
    assert not out["errors"], out["errors"]
    assert not out["native"], out["native"]

    # 1. A panel run: the note has a seal Save…, the new tab a dot and the word "unsaved".
    done = out["panelDone"]
    assert done["save"] == "Save…" and done["seal"] and done["dot"] and done["srText"] == ", unsaved", done
    assert not done["otherDot"], "the original is saved"
    assert "Not saved yet" in done["note"]
    saved = out["panelSaved"]
    assert saved["saved"] and saved["saved"][0]["near"] == cfg["plainId"], saved  # starts in the original's folder
    assert saved["reveal"] == "Show in folder" and f"Saved to {plain}" in saved["note"] and not saved["dot"], saved
    assert shown == [str(plain.resolve())]  # through the server, which checks the path

    # Ctrl+S over the file: Undo for 10 s, and it puts back the bytes the file held.
    ow = out["overwrite"]
    assert ow["toast"] == "Saved over Minutes.pdf" and ow["button"] == "Undo" and ow["changed"] and ow["stillThere"], ow
    assert out["undone"]["restored"] and out["undone"]["toast"].startswith("Undone: Minutes.pdf") and out["undone"]["dirty"], out["undone"]

    # Changed elsewhere: the app's dialog; Escape does nothing; Replace file replaces it.
    c = out["conflict"]
    assert c["buttons"] == ["Cancel", "Save as a copy", "Replace file"] and c["main"] == "Replace file", c
    assert c["focus"] == "Cancel", c
    cc = out["conflictCancel"]
    assert cc["untouched"] and cc["saved"] == 0 and cc["dirty"] and "rd-container" in cc["focus"], cc
    assert out["conflictReplace"]["replaced"]
    s = out["signed"]
    assert s["title"] == "This document is signed" and s["buttons"] == ["Cancel", "Change it anyway"] and s["unchanged"], s
    assert out["nativeCalls"] == []

    # Save is the seal action; with a result to save, Run steps back (one seal action on the screen).
    rc = out["resultCard"]
    assert rc["save"] == "Save…" and rc["saveBg"] != rc["runBg"] and rc["seals"] == 1, rc
    assert out["settingsSave"]
