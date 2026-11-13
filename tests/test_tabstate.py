"""0.29.0 (static/tabstate.js): a reader tab's state and what the screen shows
for it. TabState's transitions (opened, loaded, annotationChanged and Undo
back to saved, page and text edits and their Undo, switching away and back,
the saves, a tool's result, redacting, closing) and deriveUi (the dots, the
Save buttons, the title, the close prompt, what Ctrl+S does), in Node
(skipped where there is none); tabflow.js goes through the same in the
reader itself, in a browser through Node's Playwright; readerparts.js checks
the parts of the reader split into modules of their own (measuring, find,
the text editor box)."""
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from pdfmanager import gui
from test_critique24 import text_pdf

HERE = Path(__file__).resolve().parent
TABSTATE = HERE.parent / "pdfmanager" / "static" / "tabstate.js"


def test_tabstate_transitions():
    node = shutil.which("node")
    if not node:
        pytest.skip("needs Node.js")
    run = subprocess.run([node, str(HERE / "tabstate.mjs"), str(TABSTATE)], capture_output=True, text=True,
                         encoding="utf-8", timeout=120)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["failed"], "\n\n".join(f"{name}: {why}" for name, why in out["failed"])
    assert len(out["ok"]) >= 15, out["ok"]


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def test_tab_state_in_the_reader(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = app
    opened = srv.RequestHandlerClass.opened
    cfg = {"a": opened.add(text_pdf(tmp_path / "Doc A.pdf")), "b": opened.add(text_pdf(tmp_path / "Doc B.pdf"))}
    run = subprocess.run([node, str(HERE / "tabflow.js"), url, json.dumps(cfg)], capture_output=True, text=True,
                         encoding="utf-8", timeout=600)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]
    assert not out["native"], out["native"]
    steps = out["steps"]
    clean = {"dot": False, "titleDot": False, "saveMain": False, "close": None, "ctrlS": "nothing"}

    def at(name, **want):
        got = steps[name]
        for k, v in want.items():
            assert got[k] == v, (name, k, got)

    at("opened", **clean, title="Doc A.pdf — ZPDF")
    at("annotated", dot=True, titleDot=True, saveMain=True, saveTitle="Save your changes (Ctrl+S)", ctrlS="in place",
       close="Your changes to it haven't been saved.")
    at("undone", **clean)
    at("redone", dot=True, saveMain=True, ctrlS="in place")
    at("saved", **clean, saveTitle="Nothing to save yet (Ctrl+S)")
    assert steps["saved"]["toast"] and "Saved over" in steps["saved"]["toast"], steps["saved"]
    # Ctrl+S again straight away: nothing to save (not another "Saved over").
    assert steps["savedAgain"]["toast"] == "No changes to save", steps["savedAgain"]
    at("pageEdited", dot=True, titleDot=True, saveNow=True, undoNow=True, ctrlS="in place")
    at("pageUndone", **clean, saveNow=False)
    at("annotatedAgain", dot=True)
    at("switched", dot=False, title="Doc B.pdf — ZPDF")
    assert steps["switched"]["dots"] == [True, False], steps["switched"]
    at("back", dot=True, titleDot=True, ctrlS="in place", title="Doc A.pdf — ZPDF")
    assert steps["back"]["dots"] == [True, False], steps["back"]
    at("savedAs", **clean)
    at("redacted", dot=True, ctrlS="redacted copy", saveTitle="Save a redacted copy beside the original (Ctrl+S)")
    at("redactedSaved", dot=False, ctrlS="already redacted")
    assert steps["redactedAgain"]["toast"] == "Already saved as a redacted copy", steps["redactedAgain"]
    at("redactUndone", dot=True, ctrlS="in place")
    # Closing a tab with changes asks; with none, it doesn't.
    assert steps["closeAsked"] == {"title": "Close “Doc A.pdf”?", "text": "Your changes to it haven't been saved."}, steps["closeAsked"]
    assert steps["closed"] == {"tabs": 1, "title": "Doc B.pdf — ZPDF", "unsaved": False}, steps["closed"]
    # The fields are the tab's own: assigning one from outside fails.
    assert steps["assign"] == {"threw": True, "dirty": False}, steps["assign"]


def test_reader_parts_in_their_modules(app, tmp_path):
    """Measuring, find and the text editor box, from the modules they moved to, as before."""
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = app
    cfg = {"a": srv.RequestHandlerClass.opened.add(text_pdf(tmp_path / "Doc A.pdf"))}
    run = subprocess.run([node, str(HERE / "readerparts.js"), url, json.dumps(cfg)], capture_output=True, text=True,
                         encoding="utf-8", timeout=600)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]
    assert not out["native"], out["native"]
    assert out["measured"] == {"bar": True, "mode": "area", "kinds": ["distance", "area"], "drawn": True}, out["measured"]
    assert out["measureUndone"] == ["distance"] and out["measureRedone"] == ["distance", "area"], out
    assert out["find"] == {"open": True, "count": "1 of 3", "marked": True} and out["findClosed"], out
    assert out["editBox"] == {"value": "Quarterly report, page 1"}, out["editBox"]
    assert out["edited"] == {"dirty": True, "text": "Annual report, page 1", "box": False}, out["edited"]
    assert out["editUndone"] == {"dirty": False, "text": "Quarterly report, page 1"}, out["editUndone"]
