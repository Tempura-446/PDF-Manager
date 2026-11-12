"""Fixes from the second interface critique of 0.23 and the owner's Windows
check (gui.html, static/reader.js, reader.css, gui.py, filefacts.py): Sign is
the drawn signature and the certificate tool is Digital ID; a damaged file
keeps a state of its own with Try to repair it, and Recent says so; the file
panel steps aside while annotating or finding; the note editor stays in
sight; the unsaved dot by the title; redacted copies; files carried between
tools; Redact waits for a choice; Search a folder; Save in the close dialog;
flyout descriptions; other words in the palette and a shortcuts sheet.
In a browser through Node's Playwright (skipped where there is none), plus
the server checks without it."""
import json
import os
import shutil
import subprocess
import threading
import time
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
        if n == pages - 1 and words.startswith("Contract"):
            c.drawString(72, 160, "Signature: ______________________")
        c.showPage()
    c.save()
    return Path(path)


def broken_pdf(path):
    """A real PDF cut short: its start reads, but no reader can show it."""
    c = canvas.Canvas(str(path.with_name("_whole.pdf")))
    for n in range(6):
        for line in range(44):
            c.drawString(60, 760 - line * 16, f"Statement line {n * 44 + line}: payment received with thanks, balance carried")
        c.showPage()
    c.save()
    whole = path.with_name("_whole.pdf").read_bytes()
    path.with_name("_whole.pdf").unlink()
    path.write_bytes(whole[: len(whole) // 3])  # (Repair can mend this one: its first pages are whole)
    return path


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


def test_digital_id_is_the_certificate_tools_name_and_its_id_stays():
    ops = {op["id"]: op for op in gui.OPERATIONS}
    assert ops["sign-cert"]["label"] == "Digital ID"
    assert "Sign" not in [op["label"] for op in gui.OPERATIONS]  # "Sign" is the drawn signature, not a tool
    assert ops["search"]["label"] == "Search a folder"


def test_a_failed_open_is_remembered_in_recent_until_the_file_changes(app, tmp_path):
    srv, url = app
    token = srv.RequestHandlerClass.token
    old = text_pdf(tmp_path / "Old.pdf")
    bad = broken_pdf(tmp_path / "Bad.pdf")
    gui.RecentFiles.add(bad)
    time.sleep(0.01)
    gui.RecentFiles.add(old)
    key = srv.RequestHandlerClass.opened.add(bad)
    gui.RecentFiles.add(bad)  # opened again: to the top while it's being read
    assert [Path(i["path"]).name for i in gui.RecentFiles.load()] == ["Bad.pdf", "Old.pdf"]
    assert _post(url, "/api/recent/failed", json.dumps({"id": key}).encode(), token)[0] == 200
    items = gui.RecentFiles.load()
    assert [Path(i["path"]).name for i in items] == ["Old.pdf", "Bad.pdf"]  # back where it was
    assert gui.RecentFiles.is_failed(items[1])
    gui.RecentFiles.add(bad)  # unchanged: it stays where it is, still flagged
    assert [Path(i["path"]).name for i in gui.RecentFiles.load()] == ["Old.pdf", "Bad.pdf"]
    # Changed (mended elsewhere): tried again like any file.
    bad.write_bytes(text_pdf(tmp_path / "mended.pdf").read_bytes())
    gui.RecentFiles.add(bad)
    items = gui.RecentFiles.load()
    assert Path(items[0]["path"]).name == "Bad.pdf" and not gui.RecentFiles.is_failed(items[0])
    assert _post(url, "/api/recent/failed", json.dumps({"id": "nope"}).encode(), token)[0] == 400


def test_facts_of_a_compress_result_dont_offer_compress(app, tmp_path):
    from pdfmanager import filefacts

    found = {"size": 5_000_000, "pages": 2, "scanned": False, "has_text": True, "signatures": 0}
    assert [s["op"] for s in filefacts.suggestions(found)] == ["compress"]
    assert filefacts.suggestions(found, "compress") == []


def test_critique24_in_a_browser(app, tmp_path, monkeypatch):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = app
    docs = tmp_path / "docs"
    docs.mkdir()
    plain = text_pdf(docs / "Quarterly report.pdf")
    extra = text_pdf(docs / "Notes.pdf", pages=1, words="Notes")
    sign = text_pdf(docs / "Contract.pdf", pages=2, words="Contract for services")
    redact = text_pdf(docs / "Payroll.pdf", pages=1, words="Payroll for March: Jane Doe 3,200")
    close = text_pdf(docs / "Minutes.pdf", pages=2, words="Meeting minutes")
    room = {w: text_pdf(docs / f"Report {w}.pdf", pages=2, words="Annual report") for w in (1280, 1180)}
    broken = broken_pdf(docs / "Bank statement.pdf")
    # Recent: the damaged file before the others; opening it mustn't put it first.
    for p in (broken, extra, plain):
        gui.RecentFiles.add(p)
        time.sleep(0.01)
    # A folder to search, with a copy of one file (the same name) in another sub-folder.
    found = tmp_path / "Archive"
    for sub, name, words in (("Accounts", "Water bill.pdf", "water rates invoice due within thirty days"),
                             ("Accounts", "Electricity bill.pdf", "electricity bill amount payable"),
                             ("Letters", "Landlord.pdf", "rent increase notice from the landlord")):
        (found / sub).mkdir(parents=True, exist_ok=True)
        text_pdf(found / sub / name, pages=1, words=words)
    (found / "Backup").mkdir()
    shutil.copy2(found / "Accounts" / "Water bill.pdf", found / "Backup" / "Water bill.pdf")
    opened = srv.RequestHandlerClass.opened
    cfg = {"plainId": opened.add(plain), "plainPath": str(plain), "extraId": opened.add(extra), "extraPath": str(extra),
           "signId": opened.add(sign), "redactId": opened.add(redact), "redactPath": str(redact),
           "closeId": opened.add(close), "closePath": str(close), "brokenId": opened.add(broken),
           "roomIds": {str(w): opened.add(p) for w, p in room.items()},
           "savePath": str(docs / "Payroll (redacted).pdf"), "searchDir": str(found),
           "shots": os.environ.get("CRIT24_SHOTS")}
    run = subprocess.run([node, str(HERE / "crit24.js"), url, json.dumps(cfg)], capture_output=True, text=True,
                         encoding="utf-8", timeout=900)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    if os.environ.get("CRIT24_DUMP"):
        Path(os.environ["CRIT24_DUMP"]).write_text(json.dumps(out, indent=1), encoding="utf-8")
    assert not out["errors"], out["errors"]
    assert not out["native"], out["native"]

    # 1. The panel's Sign opens the Sign dialog (draw, type, picture), not the certificate form;
    # a printed signature line is offered "Sign it". The certificate tool is Digital ID.
    assert out["signSuggestion"].startswith("Sign it") and "line for your signature" in out["signSuggestion"], out["signSuggestion"]
    assert out["panelSign"] == {"title": "Sign", "typed": True, "pfx": False}, out["panelSign"]
    d = out["digitalId"]
    assert d["label"] == "Digital ID" and "Digital ID" in d["menu"] and d["palette"] == ["Digital ID"], d
    assert "draw or type" in d["help"], d["help"]

    # 2. A damaged file: a state that stays, with Try to repair it (seal), Remove from Recent, Close.
    b = out["broken"]
    assert b["shown"] and b["name"] == "Bank statement.pdf" and "Source Serif 4" in b["nameFont"], b
    assert b["why"] == "ZPDF can’t show this file — it may be damaged.", b["why"]
    assert b["buttons"] == ["Try to repair it", "Remove from Recent", "Close"] and b["seal"], b
    assert b["focus"] == "Try to repair it" and not b["toast"], b
    r = out["repaired"]
    assert r["tabs"][0] == ["Bank statement.pdf", True, False] and r["tabs"][1][1:] == [False, True], r
    assert r["active"] == r["tabs"][1][0] and r["active"].endswith(".pdf") and r["dot"], r
    assert out["closedBroken"] == {"tabs": [r["tabs"][1][0]], "broken": False}, out["closedBroken"]
    for theme in ("light", "dark"):
        rows = out["recent-" + theme]
        names = [row["name"] for row in rows]
        assert names.index("Bank statement.pdf") > names.index("Quarterly report.pdf"), names  # not first
        row = next(row for row in rows if row["name"] == "Bank statement.pdf")
        assert row["facts"] == "can't be opened" and row["bad"], row

    # 3. The panel steps aside while annotating or finding, and comes back; the note editor is in sight.
    for w in (1280, 1180):
        room = out["room%d" % w]
        assert room["start"]["side"] and room["start"]["button"] == "true", room["start"]
        assert not room["annotating"]["side"] and room["annotating"]["button"] == "false", room["annotating"]
        assert room["annotating"]["column"] > room["start"]["column"] + 300, room
        assert room["note"] == {"box": True, "add": True}, room["note"]
        assert room["noteSaved"] == {"box": False}, "Ctrl+S adds the note being typed"
        # One row at a time: find closes the annotate bar; the panel stays aside, then comes back.
        assert room["finding"]["find"] and not room["finding"]["annot"] and not room["finding"]["side"], room["finding"]
        assert room["after"]["side"] and room["after"]["button"] == "true" and not room["after"]["find"], room["after"]
        assert room["byHand"]["side"] and room["byHand"]["annot"], "opened by hand while annotating, it stays"
        assert room["hint"].startswith("Click a line to change it"), room["hint"]
        assert not any(t.startswith("Click a line") for t in room["hintAfterFind"]), room["hintAfterFind"]

    # 4. The unsaved dot by the title with one tab; redaction saves a copy; Replace original asks.
    assert not out["dotBefore"]
    assert out["dot"] == {"tabbar": False, "dot": True, "sr": ", unsaved"}, out["dot"]
    assert out["applyBefore"] == {"text": "Black out", "disabled": True}, out["applyBefore"]
    a = out["apply"]
    assert a["text"] == "Black out 1 area" and a["seal"] and not a["disabled"] and not a["saveSeal"], a
    t = out["redactToast"]
    assert "Save a redacted copy" in t["text"] and t["buttons"] == [["Save redacted copy…", True], ["Replace original", False]], t
    ra = out["replaceAsk"]
    assert ra["title"] == "Replace Payroll.pdf with the redacted version?", ra
    assert ra["text"] == "The original text can’t be recovered from it." and ra["buttons"] == ["Cancel", "Replace original"], ra
    assert out["replaceCancelled"] and out["redactUntouched"]
    saved = out["redactSave"]["saved"]
    assert len(saved) == 1 and saved[0]["name"] == "Payroll (redacted).pdf" and saved[0]["near"] == cfg["redactId"], saved
    assert not out["redactSave"]["dirty"]

    # 5. Files kept from the last tool: said, with Clear; no file twice; Redact waits for a choice.
    for theme in ("light", "dark"):
        c = out["carry-" + theme]
        assert c["afterTwice"] == ["Quarterly report.pdf", "Notes.pdf"], c["afterTwice"]
        assert c["redact"]["line"].startswith("2 files kept from Compress") and "Clear" in c["redact"]["line"], c["redact"]
        assert c["redact"]["summary"].startswith("Choose what to black out") and c["redact"]["run"], c["redact"]
        assert c["chosen"]["summary"].startswith("Ready") and not c["chosen"]["run"], c["chosen"]
        assert c["cleared"] == {"items": 0, "line": False}, c["cleared"]
        assert c["carriedAgain"] and not c["afterRemove"]
        e = c["error"]
        assert e["top"] == "That page doesn't exist" and e["bar"] == "That page doesn't exist" and e["inBar"], e

    # 6a. A Compress result: facts asked with what made it, and no "Make it smaller".
    assert out["compressFacts"] and all(out["compressFacts"]), out["compressFacts"]
    assert out["compressSuggested"] == 0
    # 6f. The close dialog offers Save (seal), which saves and then closes.
    assert out["closeDialog"] == [["Cancel", ""], ["Close without saving", ""], ["Save", "primary seal"]], out["closeDialog"]
    assert out["closeSaved"] == {"tabs": ["Notes.pdf"], "changed": True}, out["closeSaved"]
    for theme in ("light", "dark"):
        s = out["search-" + theme]
        # 6d. One name for the tool.
        assert s["names"]["rail"] == "Search a folder" and s["title"] == "Search a folder" and s["names"]["start"].startswith("Search a folder"), s
        # 6c. Headings below the searched folder, the full path as the tooltip.
        heads = dict(s["heads"])
        assert "Accounts" in heads and heads["Accounts"] == str(found / "Accounts"), s["heads"]
        # 6b. The other copy named, with its folder (the names match), once per file.
        also = [a for name, a in s["also"] if name == "Water bill.pdf"]
        assert also and also[0] == "The same as Water bill.pdf in Backup", s["also"]
        assert all(a == "" for a in also[1:]), s["also"]
        # 6e. The form is kept for the session.
        assert s["kept"] == [str(found), "when is the water bill due"], s["kept"]
        # 6g. No flyout description cut off, at 1280 or 1180.
        assert s["cut"] == [], s["cut"]
        # 6h. Other words for tools, and the shortcuts sheet (the palette, and ? when not typing).
        p = s["palette"]
        assert p["shrink"][0] == "Compress" and p["join"][0] == "Merge" and p["combine"][0] == "Merge", p
        assert p["lock"][0] == "Add password" and p["protect"][0] == "Add password" and p["unlock"][0] == "Remove password", p
        assert p["sign"][0] == "Digital ID", p
        assert p["help"][0] == "Keyboard shortcuts" and p["shortcuts"][0] == "Keyboard shortcuts", p
        assert s["sheet"]["title"] == "Keyboard shortcuts" and {"Ctrl+K", "Ctrl+F", "Ctrl+S", "T", "E", "H"} <= set(s["sheet"]["keys"]), s["sheet"]
        assert s["sheet"]["heads"] == ["Everywhere", "Tool pages", "Annotate bar open", "Measuring", "Reader"], s["sheet"]
        assert s["questionMark"] and not s["typedQuestionMark"], s
    assert out["readerSign"][:2] == ["Sign", "Digital ID"], out["readerSign"]
