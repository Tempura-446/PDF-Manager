# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Fixes from the Windows check of 0.22.1, in a browser (Node's Playwright,
skipped where there is none): the file chooser from the keyboard, Undo
toasts that wait, Windows high contrast (forced colours), Settings' buttons
at the bottom, the check row's label, the damaged-file message announced,
the reader's password label and a file opened twice. See winfix.js. Also
the desktop window's autofill settings (WebView2), with stand-ins."""
import json
import os
import shutil
import subprocess
import sys
import threading
import types
from pathlib import Path

import pikepdf
import pytest
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from pdfmanager import gui

HERE = Path(__file__).resolve().parent


def text_pdf(path, pages=3):
    c = canvas.Canvas(str(path), pagesize=A4)
    for p in range(1, pages + 1):
        c.setFont("Helvetica", 11)
        c.drawString(60, 760, f"Page {p}: payment is due within thirty days.")
        c.showPage()
    c.save()
    return path


def big_pdf(path, tmp):
    """Over 4 MB (the reader reads it in pieces): a page of noise."""
    noise = Image.frombytes("RGB", (1400, 1400), os.urandom(1400 * 1400 * 3))
    noise.save(tmp / "noise.png")
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawString(60, 800, "A large file")
    c.drawImage(str(tmp / "noise.png"), 40, 100, width=500, height=500)
    c.showPage()
    c.save()
    assert path.stat().st_size > 4 * 1024 * 1024
    return path


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def test_windows_check_fixes_in_a_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = app
    docs = tmp_path / "docs"
    docs.mkdir()
    small = text_pdf(docs / "Small.pdf")
    large = big_pdf(docs / "Large.pdf", tmp_path)
    locked = tmp_path / "Locked.pdf"
    with pikepdf.open(small) as pdf:
        pdf.save(locked, encryption=pikepdf.Encryption(user="open-me", owner="owner"))
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\n1 0 obj << /Type /Catalog /Pages 2 0 R >>\nthis is not really a pdf\n" + os.urandom(3000))
    gui.RecentFiles.add(small)
    opened = srv.RequestHandlerClass.opened
    shots = os.environ.get("WINFIX_SHOTS", "")
    cfg = {"small": str(small), "large": str(large), "smallId": opened.add(small), "largeId": opened.add(large),
           "locked": opened.add(locked), "broken": str(broken), "shots": shots}
    run = subprocess.run([node, str(HERE / "winfix.js"), url, json.dumps(cfg)], capture_output=True, text=True, encoding="utf-8", timeout=600)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    if os.environ.get("WINFIX_DUMP"):
        Path(os.environ["WINFIX_DUMP"]).write_text(json.dumps(out, indent=1), encoding="utf-8")
    assert not out["errors"], out["errors"]

    # A. The file chooser from the keyboard: the drop area is a button (Tab, Enter, Space),
    # "Add more files" too; where "Choose files" shows, that button is the one stop.
    d = out["dropTab"]
    assert d["focused"] and d["role"] == "button" and d["name"] == "Drop files here or click to choose", d
    assert d["ring"] == ["solid", "2px"] and d["visible"], d
    assert out["dropEnter"] and out["dropSpace"], (out["dropEnter"], out["dropSpace"])
    more = out["addMore"]
    assert more["name"] == "Add more files" and more["focused"] and more["chooser"], more
    for key in ("mergeFirst", "imagesFirst"):
        m = out[key]
        assert m["chooseFocused"] and not m["dropTabbable"] and m["chooser"], (key, m)
    assert out["mergeAddMore"]["dropTabbable"] and out["mergeAddMore"]["chooseHidden"], out["mergeAddMore"]
    assert out["insertPdf"]["tag"] == "BUTTON" and out["insertPdf"]["chooser"], out["insertPdf"]
    assert out["mouseLook"]["outline"] == "none", out["mouseLook"]  # a click shows no ring

    # C. Undo toasts: 10 s, and not while pointed at or focused.
    t = out["toast"]
    assert t["at6"] and t["at9"] and not t["at11"], t
    assert t["hoverAt20"] and t["leftAt22"] and not t["leftAt40"], t
    assert t["focusAt30"] and not t["blurAt45"], t
    assert t["plainGone"], t  # a toast with no button keeps its own time
    assert out["rdToast"]["at9"] and out["rdToast"]["hoverAt20"] and not out["rdToast"]["leftAt40"], out["rdToast"]

    # E. Forced colours: pressed and current things differ from the rest; selects keep an arrow.
    f = out["forced"]
    assert f["docTabOn"] == f["pressedBg"], f  # the open document's tab is highlighted like a pressed button
    assert f["active"], f
    assert f["pressedBg"] != f["plainBg"], f
    assert f["annotPressed"] != f["annotPlain"], f
    assert f["tabOn"] != f["tabOff"], f
    assert f["railOn"] != f["railOff"], f
    assert f["selectAppearance"] == "auto" and f["selectImage"] == "none", f
    assert f["zoomAppearance"] == "auto", f
    assert f["swatchAdjust"] == "none", f
    assert f["normalAppearance"] == "none", f  # outside forced colours the chevron stays ours

    # F1. Settings: the buttons are the dialog's bottom (no text below them).
    s = out["settings"]
    assert s["scrolls"] and abs(s["formBottom"] - s["buttonsBottom"]) <= 1, s
    assert s["dialogBottom"] - s["buttonsBottom"] <= 2, s
    e = out["settingsEnd"]  # scrolled to the end: the same
    assert abs(e["formBottom"] - e["buttonsBottom"]) <= 1, e
    # F2. "Keep scanned text sharp": one line beside its box, the hint under it.
    k = out["checkRow"]
    assert k["labelLines"] == 1 and k["hintBelow"], k
    # F3. A file that can't be opened: said once, politely.
    live = out["live"]
    assert live["role"] == "status" and "can't be opened" in live["text"] and live["repaints"] == 0, live
    # F4. The reader's password: the label names the input; Show is outside it.
    pw = out["pwLabel"]
    assert pw == {"labelText": "Password", "buttonInLabel": False, "inputLabelled": True, "ariaLabel": None}, pw
    # F5. A file open from a tool's card, then from Recent: one tab, switched to (small and large).
    for key in ("dupSmall", "dupLarge"):
        assert out[key]["tabs"] == 1, (key, out[key])


def test_the_desktop_window_turns_on_autofill(monkeypatch):
    """WebView2 keeps password autosave and autofill off unless the app turns
    them on: done when its CoreWebView2 is ready (pywebview's EdgeChromium
    backend: window.native.browser.webview). Failures are only logged."""
    class Event:
        def __init__(self):
            self.items = []

        def __iadd__(self, fn):
            self.items.append(fn)
            return self

    settings = types.SimpleNamespace(IsPasswordAutosaveEnabled=False, IsGeneralAutofillEnabled=False)
    control = types.SimpleNamespace(CoreWebView2=None, CoreWebView2InitializationCompleted=Event())
    window = types.SimpleNamespace(events=types.SimpleNamespace(before_show=Event()),
                                   native=types.SimpleNamespace(browser=types.SimpleNamespace(webview=control)))
    logged = []
    monkeypatch.setattr(gui, "_log", logged.append)
    monkeypatch.setattr(sys, "platform", "win32")
    gui._allow_autofill(window)
    [before_show] = window.events.before_show.items
    before_show(window)
    [ready] = control.CoreWebView2InitializationCompleted.items
    sender = types.SimpleNamespace(CoreWebView2=types.SimpleNamespace(Settings=settings))
    ready(sender, types.SimpleNamespace(IsSuccess=True))
    assert settings.IsPasswordAutosaveEnabled and settings.IsGeneralAutofillEnabled
    assert not logged
    # An older WebView2 without these settings, or another backend: logged, nothing raised.
    ready(types.SimpleNamespace(CoreWebView2=types.SimpleNamespace(Settings=object())), types.SimpleNamespace(IsSuccess=True))
    before_show(types.SimpleNamespace(native=None))
    assert len(logged) == 2 and all("autofill" in m for m in logged)
    gui._allow_autofill(types.SimpleNamespace(events=types.SimpleNamespace()))  # pywebview < 5
    assert len(logged) == 3
    # Not on Windows: nothing is touched.
    monkeypatch.setattr(sys, "platform", "linux")
    other = types.SimpleNamespace(events=types.SimpleNamespace(before_show=Event()))
    gui._allow_autofill(other)
    assert not other.events.before_show.items
