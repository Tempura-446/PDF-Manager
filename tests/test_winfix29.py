"""0.29.0, from the Windows check (static/reader.js, tabs.js, tabstate.js):
Redo of a note Undo took back (and the server kept told what's unsaved,
for closing the desktop window: test_close_guard.py); Ctrl+Z and Ctrl+Y with Select on;
the unsaved dot while drawing; the Measurements list on a tab switch. In a browser through Node's Playwright
(skipped where there is none); winfix29.js runs one scenario at a time."""
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

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


def run(app, tmp_path, scenario, **ids):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = app
    cfg = {"scenario": scenario, **ids}
    done = subprocess.run([node, str(HERE / "winfix29.js"), url, json.dumps(cfg)], capture_output=True, text=True,
                          encoding="utf-8", timeout=600)
    if done.returncode and "Executable doesn't exist" in done.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    if os.environ.get("WINFIX29_DUMP"):
        Path(os.environ["WINFIX29_DUMP"]).write_text(json.dumps(out, indent=1), encoding="utf-8")
    assert not out["errors"], out["errors"]
    assert not out["native"], out["native"]
    return out


def test_redo_brings_back_a_note_that_undo_took_away(app, tmp_path):
    srv, _ = app
    src = text_pdf(tmp_path / "Report.pdf", pages=1)
    out = run(app, tmp_path, "note", file=srv.RequestHandlerClass.opened.add(src))
    added, undone, redone, saved = out["added"], out["undone"], out["redone"], out["saved"]
    assert added["notes"] == 1 and added["dirty"] and added["titleDot"] and added["undo"] and not added["redo"], added
    # Ctrl+Z: gone, no dot; Redo on.
    assert undone["notes"] == 0 and not undone["dirty"] and not undone["titleDot"], undone
    assert undone["redo"] and not undone["undo"], undone
    # Ctrl+Y: back, the dot, Redo off again (and Undo on).
    assert redone["notes"] == 1 and redone["dirty"] and redone["titleDot"], redone
    assert not redone["redo"] and redone["undo"], redone
    # Ctrl+S: in the file.
    assert not saved["dirty"] and not saved["titleDot"], saved
    assert [kind for _, kind, _ in annotations(src) if kind != "/Popup"] == ["/Text"], annotations(src)
    # The server was kept told what's unsaved, as it changed (closing the desktop window asks from that).
    posts = [p for i, p in enumerate(out["unsavedPosts"]) if i == 0 or p != out["unsavedPosts"][i - 1]]
    assert posts == [[], ["Report.pdf"], [], ["Report.pdf"], []], out["unsavedPosts"]


def test_ctrl_z_and_ctrl_y_work_with_select_on(app, tmp_path):
    srv, _ = app
    src = text_pdf(tmp_path / "Report.pdf", pages=1)
    out = run(app, tmp_path, "select", file=srv.RequestHandlerClass.opened.add(src))
    drawn = out["drawn"]
    assert drawn["mode"] == "NONE" and drawn["pdfjsMode"] == 0 and drawn["editors"] == 1 and drawn["undo"], drawn
    assert out["inkUndone"]["editors"] == 0 and out["inkUndone"]["redo"], out["inkUndone"]
    assert out["inkRedone"]["editors"] == 1 and not out["inkRedone"]["redo"], out["inkRedone"]
    # Ctrl+Z in the find box is the box's own.
    assert out["typing"]["editors"] == 1, out["typing"]
    # A note, then Select: the reader's own undo and redo.
    noted, undone, redone = out["noted"], out["noteUndone"], out["noteRedone"]
    assert noted["mode"] == "NONE" and noted["notes"] == 1 and noted["depth"] == [1, 0], noted
    assert undone["notes"] == 0 and undone["depth"] == [0, 1] and undone["redo"], undone
    assert redone["notes"] == 1 and redone["depth"] == [1, 0] and not redone["redo"], redone


def test_the_unsaved_dot_shows_as_soon_as_save_turns_red(app, tmp_path):
    srv, _ = app
    opened = srv.RequestHandlerClass.opened
    out = run(app, tmp_path, "dot", first=opened.add(text_pdf(tmp_path / "First.pdf", pages=1)),
              second=opened.add(text_pdf(tmp_path / "Second.pdf", pages=1)))
    assert out["before"]["tabDot"] is False and not out["before"]["annotSaveMain"], out["before"]
    # The stroke drawn, Draw still on: Save and the dot together (pdf.js hasn't handed it over yet).
    drawing = out["drawing"]
    assert drawing["mode"] == "INK" and drawing["annotSaveMain"] and drawing["tabDot"], drawing
    assert out["selected"]["tabDot"] and out["selected"]["dirty"] and out["selected"]["annotSaveMain"], out["selected"]
    undone = out["undone"]
    assert not undone["tabDot"] and not undone["annotSaveMain"] and undone["editors"] == 0, undone
    # The server heard of it as soon as the dot showed.
    assert ["Second.pdf"] in out["unsavedPosts"], out["unsavedPosts"]


def test_the_measurements_list_stays_with_its_tab(app, tmp_path):
    srv, _ = app
    opened = srv.RequestHandlerClass.opened
    out = run(app, tmp_path, "measures", first=opened.add(text_pdf(tmp_path / "Plan.pdf", pages=1)),
              second=opened.add(text_pdf(tmp_path / "Other.pdf", pages=1)))
    assert out["measured"] == {"open": True, "tab": 0, "rows": 1}, out["measured"]
    # Another tab: the list isn't over it.
    assert out["other"]["tab"] == 1 and not out["other"]["open"], out["other"]
    # Back: it's there again.
    assert out["back"] == {"open": True, "tab": 0, "rows": 1}, out["back"]
    # Closed by hand there: it stays closed coming back.
    assert out["closedBack"]["tab"] == 0 and not out["closedBack"]["open"], out["closedBack"]
