# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.42.0: what ZPDF leaves on the computer. Working files go into one
folder per run (deleted on close, swept after a crash); Clear history
empties every store; "Remember the files I open" off keeps none; and the
occasions ZPDF goes online are stated."""

import json
import os
import tempfile
import threading
import urllib.request
from pathlib import Path

import pytest
from pypdf import PdfWriter

from pdfmanager import appdata, filefacts, gui, privacy, search, update


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A data folder of this test's own (conftest gives a shared one)."""
    folder = tmp_path / "zpdf-data"
    monkeypatch.setattr(appdata, "_data_dir", lambda: folder)
    before = tempfile.tempdir
    yield folder
    privacy.end_run()
    tempfile.tempdir = before


def pdf(path, text="Invoice for Ann"):
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    c.drawString(72, 720, text)
    c.save()
    return path


def test_working_files_live_in_one_folder_of_this_run(home):
    before = tempfile.gettempdir()
    run = privacy.begin_run()
    assert run == home / "temp" / f"run-{os.getpid()}" and run.is_dir()
    assert privacy.begin_run() == run                       # again: nothing changes
    with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
        assert Path(tmp).parent == run
        (Path(tmp) / "half-done.pdf").write_bytes(b"%PDF")
        assert privacy.left_behind()
    assert privacy.left_behind() == []                      # a finished job leaves nothing
    stray = Path(tempfile.mkdtemp(prefix="pdfm-")) / "left.pdf"
    stray.write_bytes(b"%PDF")                              # (a job folder nobody cleaned up)
    privacy.end_run()
    assert not run.exists() and not stray.exists() and tempfile.gettempdir() == before


def test_a_crashed_copys_folder_is_swept_at_the_next_start(home):
    dead = home / "temp" / "run-999999999"
    dead.mkdir(parents=True)
    (dead / "contract.pdf").write_bytes(b"%PDF secret")
    other = home / "temp" / f"run-{os.getppid()}"           # a copy that's still running
    other.mkdir()
    (other / "busy.pdf").write_bytes(b"%PDF")
    privacy.begin_run()
    assert not dead.exists() and other.exists()


def test_a_tool_run_leaves_no_working_files(home, tmp_path):
    privacy.begin_run()
    src = pdf(tmp_path / "a.pdf")
    work = Path(tempfile.mkdtemp(prefix="pdfm-"))
    try:
        name, data, _ = gui.run_operation("rotate", [src], {"angle": "90"}, None, {}, work, [])
        assert data.startswith(b"%PDF")
    finally:
        import shutil

        shutil.rmtree(work)
    assert privacy.left_behind() == []


def test_clear_history_empties_every_store(home, tmp_path):
    src = pdf(tmp_path / "a.pdf")
    gui.RecentFiles.add(src)
    filefacts.remembered(src)
    search.search(tmp_path, "Invoice", False, False, False, how="exact")
    kept = privacy.history()
    assert kept == {"recent": 1, "facts": 1, "search": True}
    cleared = privacy.clear_history()
    assert len(cleared) == 3 and "the Recent list" in cleared
    assert privacy.history() == {"recent": 0, "facts": 0, "search": False}
    assert gui.RecentFiles.load() == []
    assert not any((home / n).exists() for n in ("recent.json", "filefacts.json", "search.sqlite"))
    assert privacy.clear_history() == []                    # nothing left to clear
    assert update.settings() == {} or "remember_files" not in update.settings()   # settings aren't history


def test_not_remembering_keeps_nothing(home, tmp_path):
    src = pdf(tmp_path / "a.pdf")
    gui.RecentFiles.add(src)
    privacy.set_remembering(False)                          # turning it off clears what was kept
    assert not privacy.remembering() and privacy.history() == {"recent": 0, "facts": 0, "search": False}
    gui.RecentFiles.add(src)
    gui.RecentFiles.failed(src)
    assert filefacts.remembered(src)["pages"] == 1          # still answered, not stored
    found = gui.run_folder_operation("search", {"directory": str(tmp_path), "query": "Invoice"}, False)
    assert found["matches"]                                 # searched, and the text read for it isn't kept
    assert privacy.history() == {"recent": 0, "facts": 0, "search": False}
    assert gui.RecentFiles.load() == []
    privacy.set_remembering(True)
    gui.RecentFiles.add(src)
    assert privacy.remembering() and len(gui.RecentFiles.load()) == 1


def test_the_settings_page_gets_it_all(home, tmp_path):
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base, token = url.split("#token=")
        base = base.rstrip("/")

        def call(path, data=None):
            req = urllib.request.Request(base + path, headers={"X-PDFM-Token": token, "Content-Type": "application/json"},
                                         data=None if data is None else json.dumps(data).encode())
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())

        gui.RecentFiles.add(pdf(tmp_path / "a.pdf"))
        s = call("/api/settings")
        assert s["remember"] is True and s["history"]["recent"] == 1
        assert len(s["online"]) == len(privacy.ONLINE) and all(o["what"] and o["when"] and o["sends"] for o in s["online"])
        s = call("/api/history/clear", {})
        assert s["cleared"] == ["the Recent list"] and s["history"]["recent"] == 0
        s = call("/api/settings", {"remember": False})
        assert s["remember"] is False
        assert call("/api/recent")["files"] == []
        assert call("/api/settings", {"remember": True})["remember"] is True
    finally:
        srv.shutdown()
        srv.server_close()


def test_every_way_online_is_stated():
    """Each place the code opens a connection has its line in ONLINE; a new
    one must be added there (and here)."""
    said = " ".join(o["what"].lower() for o in privacy.ONLINE)
    for word in ("new version", "update", "language", "timestamp", "web page", "link"):
        assert word in said
    root = Path(privacy.__file__).parent
    calling = sorted(p.name for p in root.glob("*.py")
                     if "urlopen(" in p.read_text(encoding="utf-8") and p.name != "privacy.py")
    # ocr.py: language data; update.py: versions and updates. (Timestamps go through pyHanko, in certsign.py.)
    assert calling == ["ocr.py", "update.py"], calling
    page = (root / "gui.html").read_text(encoding="utf-8")
    assert "no telemetry" in page and 'id="set-clear-history"' in page and 'id="set-remember"' in page
