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
    extra = text_pdf(docs / "Notes.pdf", pages=1, words="Notes")
    # A folder to search: two sub-folders, the words in a file in each.
    found = tmp_path / "Archive"
    for sub, name, amount in (("Accounts", "Invoice 2041.pdf", 120), ("Accounts", "Invoice 2042.pdf", 80), ("Letters", "Reminder.pdf", 45)):
        (found / sub).mkdir(parents=True, exist_ok=True)
        text_pdf(found / sub / name, pages=2, words=f"The balance due is {amount} euros, payable within thirty days")
    broken = tmp_path / "Broken.pdf"
    broken.write_bytes(b"%PDF-1.7\n1 0 obj << /Type /Catalog /Pages 2 0 R >>\nthis is not really a pdf\n" + os.urandom(3000))
    for p in (broken, plain):
        gui.RecentFiles.add(p)
    opened = srv.RequestHandlerClass.opened
    cfg = {"plainId": opened.add(plain), "plainPath": str(plain), "savePath": str(plain),
           "undoId": opened.add(undo), "undoPath": str(undo), "extraPath": str(extra), "searchDir": str(found),
           "brokenPath": str(broken), "shots": os.environ.get("CRITFIX_SHOTS")}
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

    # 2. The annotate bar keeps its words at 1180, 1280 and 1440, fits, and Save and Close show.
    assert not out["annot1280"]["saveSealBefore"] and out["saveSealAfter"], "Save is quiet until there is something to save"
    assert out["saveRightAfterDrawing"].startswith("Saved"), out["saveRightAfterDrawing"]
    assert not out["saveSealAfterSaving"], "and quiet again once saved"
    assert out["search-light"]["runEnabled"], "Search can run after a tool that wanted a file"
    for key in ("annot1180", "annot1180dark", "annot1280", "annot1440"):
        a = out[key]
        for word in ("Highlight", "Note", "Add text", "Draw", "Sign", "More tools", "Save"):
            assert word in a["worded"], (key, word, a["worded"])
        assert a["save"] and a["close"] and a["scrollW"] <= a["clientW"] and not a["pageScroll"], (key, a)
    # More tools: a labelled menu button, keyboard operable; the tool in use shows on it.
    m = out["menuOpen"]
    assert m["open"] and m["expanded"] == "true" and m["role"] == "menuitemradio" and m["focus"] == "Underline", m
    assert m["items"] == ["Underline", "Strike out", "Shapes", "Stamp", "Add picture", "Edit text", "Edit pictures", "Links",
                          "Digital ID", "Redact"], m
    assert out["menuDown"]["focus"] == "Strike out" and out["menuEnd"]["focus"] == "Redact"
    e = out["menuEsc"]
    assert not e["open"] and e["expanded"] == "false" and e["focus"] == "More tools" and e["barOpen"], e
    p = out["menuPicked"]
    assert not p["open"] and p["button"] == "Shapes" and p["on"] and "Shapes" in p["label"] and p["focus"] == "Shapes", p
    # Single keys while the bar is open (not in a box, not with the bar closed); n is still the next page.
    k = out["keys"]
    assert [k["h"], k["c"], k["a"], k["d"]] == ["HIGHLIGHT", "NOTE", "FREETEXT", "INK"] and k["again"] == "NONE", k
    assert k["button"] == "true" and k["next"] and k["typing"] != "HIGHLIGHT" and k["sign"] == "Sign", k
    for mode, key in (("HIGHLIGHT", "H"), ("NOTE", "C"), ("FREETEXT", "A"), ("INK", "D"), ("SIGN", "S")):
        assert k["tips"][mode].endswith(f"({key})"), k["tips"]
    assert not k["closed"]["bar"] and k["closed"]["mode"] != "HIGHLIGHT", k["closed"]

    # 3. The rail by intent: Compress under Improve, Search PDFs under no group; never a wrong item lit.
    rail = out["rail"]
    assert rail["groups"] == ["Read", "Pages", "Improve", "Convert", "Protect", "All"], rail["groups"]
    improve, convert, every = rail["flyouts"]["improve"], rail["flyouts"]["convert"], rail["flyouts"]["all"]
    assert set(improve["tools"]) == {"repair", "compress", "color", "ocr", "clean", "flatten", "tag", "access-check"}, improve
    assert "Optimize" in improve["heads"] and "Convert" not in improve["heads"], improve["heads"]
    for folder_tool in ("search", "organize", "rename", "duplicates", "recipe", "compress"):
        assert folder_tool not in convert["tools"], folder_tool
    assert all(f["fits"] for f in rail["flyouts"].values())
    assert every["cue"] and not every["cueAtEnd"] and every["cueAbove"], every  # a long list says it goes on
    assert rail["lit-compress"] == ["improve"] and rail["lit-to-word"] == ["convert"] and rail["lit-flatten"] == ["improve"]
    assert rail["lit-merge"] == ["pages"]
    for folder_tool in ("search", "organize", "recipe"):
        assert rail["lit-" + folder_tool] == [], (folder_tool, rail["lit-" + folder_tool])
    sev = rail["several"]
    assert sev["title"] == "Several files" and {"search", "organize", "rename", "duplicates", "recipe", "merge"} <= set(sev["tools"]), sev
    assert sev["focusBack"]

    # 4. Search: the form folds to one line, results under the title, grouped by folder;
    # only the words are marked, dark ink on yellow in both themes.
    for theme in ("light", "dark"):
        t = out["search-" + theme]
        assert t["help"] <= 75, t["help"]
        assert not t["formShown"] and t["recap"].startswith("“how much is the balance due” in ") and t["recap"].endswith("Edit"), t
        assert str(found) in t["recap"] and t["resultTop"] < 60 and t["gap"] < 40, t
        assert sorted(t["folders"]) == [str(found / "Accounts"), str(found / "Letters")], t["folders"]
        assert set(t["names"]) == {"Invoice 2041.pdf", "Invoice 2042.pdf", "Reminder.pdf"}, t["names"]
        assert all(label.startswith("Open page ") and label.endswith(".pdf") for label in t["labels"])
        assert t["marks"] and {m.lower() for m in t["marks"]} <= {"balance", "due"}, t["marks"]
        assert all(len(m) < len(s) / 3 for m, s in zip(t["marks"], t["snippets"])), "not the whole snippet"
        assert t["markContrast"] >= 4.5, (theme, t["markContrast"])
        assert t["edit"] == {"formShown": True, "recap": False, "focus": "f-query"}, t["edit"]
        assert t["exactMarks"] and {m.lower() for m in t["exactMarks"]} == {"balance due"}, t["exactMarks"]
        # One file: a compact strip (serif name, mono facts, actions shown); three: the grid.
        s = t["strip"]
        assert s["strip"] and s["height"] <= 90 and "Source Serif 4" in s["nameFont"] and "JetBrains Mono" in s["factsFont"], s
        assert s["actions"] == "1" and s["drop"] == 0 and s["dropText"] == "Add more files" and s["cardHeight"] < 240, s
        assert not t["grid3"]

    # 5. "can't be read" in the error colour on the start screen (readable, with its words); the panel's chip too.
    for theme in ("light", "dark"):
        t = out["errors-" + theme]
        f = t["flag"]
        assert f["text"] == "can't be read" and f["bad"] and f["color"] == f["err"], f
        assert t["flagContrast"] >= 4.5, (theme, t["flagContrast"])
        assert t["chips"] == [{"text": "can't be read", "flag": False, "bad": True}], t["chips"]
    t = out["errors-light"]
    # Destructive tools say what happens; the rest still say Run.
    assert t["redactLabel"] == "Black out" and t["redactCount"] == "Black out 3 matches", t
    assert t["deleteLabel"] == "Delete pages" and t["rotateLabel"] == "Run", t
    assert t["skipped"] == "1 file was left out (can't be opened): Broken.pdf", t["skipped"]
    assert t["palette"].count("Redact") == 1 and t["palette"].count("Redact this document") == 1, t["palette"]
    assert not any(p.startswith("Redact:") or "(black out" in p for p in t["palette"]), t["palette"]


def test_destructive_tools_name_their_action():
    ops = {op["id"]: op for op in gui.OPERATIONS}
    assert ops["redact"]["action"] == "Black out" and ops["delete"]["action"] == "Delete pages"
    assert ops["sanitize"]["action"] == "Remove hidden data" and ops["flatten"]["action"] == "Flatten"
