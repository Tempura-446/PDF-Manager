# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.30.0: watch folders in the app (Settings > Watch folders). A folder is
added with what to do (a tool with its usual settings, or a recipe), where
results go and what happens to the originals; a PDF put there is done once,
also across a restart; a failure is reported plainly; pause, resume and
remove. Uses the `pdfm watch` engine (watch.py) on a background thread."""

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from pypdf import PdfReader

from pdfmanager import core, gui, recipes, watch, watchfolders
from test_more import call, text_pdf

HERE = Path(__file__).resolve().parent


@pytest.fixture
def fake_ocr(monkeypatch):
    """OCR that copies the file (and says which files it saw)."""
    seen = []

    def fake(src, out, **kw):
        seen.append(Path(src).name)
        Path(out).write_bytes(Path(src).read_bytes())
        return Path(out)

    monkeypatch.setattr(core, "ocr", fake)
    return seen


def settle(watcher, times=2):
    """Look at the folders twice (a file is taken once it holds still between two looks)."""
    for _ in range(times):
        watcher.poll_all()


def test_a_file_is_done_once_into_done_and_the_original_stays(tmp_path, fake_ocr):
    inbox = tmp_path / "Scans"
    inbox.mkdir()
    watcher = watchfolders.WatchFolders(thread=False)
    entry = watcher.add(inbox, {"tool": "ocr"})
    assert entry["results"] == "done" and entry["originals"] == "keep" and not entry["paused"]
    assert watcher.list()[0]["describe"] == "Make searchable (OCR) → Done folder, originals kept"
    text_pdf(inbox / "letter.pdf")
    settle(watcher)
    assert (inbox / "Done" / "letter.pdf").exists() and (inbox / "letter.pdf").exists()
    assert fake_ocr == ["letter.pdf"]
    status = watcher.list()[0]["status"]
    assert status["done"] == 1 and status["last"]["ok"] and "letter.pdf is done" in status["last"]["message"]
    news, last = watcher.news_after(0)
    assert len(news) == 1 and news[0]["ok"] and news[0]["result"] == str(inbox / "Done" / "letter.pdf")
    settle(watcher, 3)
    assert fake_ocr == ["letter.pdf"]  # once
    # After a restart (a new app, the same data folder): still once.
    again = watchfolders.WatchFolders(thread=False)
    assert [f["folder"] for f in again.list()] == [str(inbox.resolve())]
    settle(again, 3)
    assert fake_ocr == ["letter.pdf"] and again.news_after(0)[0] == []
    # A file changed since gets another go.
    text_pdf(inbox / "letter.pdf", pages=("Changed", "Twice"))
    settle(again)
    assert fake_ocr == ["letter.pdf", "letter.pdf"]
    assert sorted(p.name for p in (inbox / "Done").iterdir()) == ["letter (2).pdf", "letter.pdf"]


def test_results_beside_the_original_and_originals_moved(tmp_path, monkeypatch):
    inbox = tmp_path / "In"
    inbox.mkdir()
    watcher = watchfolders.WatchFolders(thread=False)
    watcher.add(inbox, {"tool": "compress"}, results="beside", originals="move")
    text_pdf(inbox / "big.pdf")
    settle(watcher)
    assert (inbox / "big (smaller).pdf").exists()
    assert (inbox / "Originals" / "big.pdf").exists() and not (inbox / "big.pdf").exists()
    settle(watcher, 3)  # the result isn't taken again
    assert sorted(p.name for p in inbox.iterdir()) == ["Originals", "big (smaller).pdf"]
    assert watcher.list()[0]["status"]["done"] == 1


def test_results_beside_and_originals_kept_are_each_done_once(tmp_path):
    inbox = tmp_path / "In"
    inbox.mkdir()
    watcher = watchfolders.WatchFolders(thread=False)
    watcher.add(inbox, {"tool": "pdfa"}, results="beside", originals="keep")
    text_pdf(inbox / "deed.pdf")
    settle(watcher, 4)
    assert sorted(p.name for p in inbox.iterdir()) == ["deed (PDF-A).pdf", "deed.pdf"]
    assert watcher.list()[0]["status"]["done"] == 1


def test_a_recipe_and_partial_downloads(tmp_path):
    recipes.save("Number them", [{"op": "number", "options": {}}])
    inbox = tmp_path / "Downloads"
    inbox.mkdir()
    watcher = watchfolders.WatchFolders(thread=False)
    watcher.add(inbox, {"recipe": "Number them"})
    # A download still under way (Firefox: the file and its .part), a lock file, a hidden file.
    text_pdf(inbox / "report.pdf")
    (inbox / "report.pdf.part").write_bytes(b"...")
    text_pdf(inbox / "~$draft.pdf")
    text_pdf(inbox / ".hidden.pdf")
    (inbox / "empty.pdf").write_bytes(b"")  # being written: nothing in it yet
    settle(watcher, 3)
    assert not (inbox / "Done").exists()
    (inbox / "report.pdf.part").unlink()  # the download finished
    settle(watcher)
    assert [p.name for p in (inbox / "Done").iterdir()] == ["report.pdf"]
    assert "1" in PdfReader(inbox / "Done" / "report.pdf").pages[0].extract_text()


def test_a_failure_is_reported_plainly_and_not_retried(tmp_path):
    inbox = tmp_path / "In"
    inbox.mkdir()
    watcher = watchfolders.WatchFolders(thread=False)
    watcher.add(inbox, {"tool": "compress"})
    (inbox / "broken.pdf").write_bytes(b"%PDF-1.4\nnot really a PDF at all\n")
    settle(watcher)
    status = watcher.list()[0]["status"]
    assert status["failed"] == 1 and not status["last"]["ok"]
    [news] = watcher.news_after(0)[0]
    assert not news["ok"] and news["message"].startswith("broken.pdf couldn't be done: ")
    assert "Traceback" not in news["message"] and len(news["message"]) < 400
    settle(watcher, 3)
    assert len(watcher.news_after(0)[0]) == 1  # not again
    restarted = watchfolders.WatchFolders(thread=False)
    settle(restarted, 3)
    assert restarted.news_after(0)[0] == []  # not after a restart either
    assert (inbox / "broken.pdf").exists()  # left where it was
    assert not (inbox / "Done").exists() or not list((inbox / "Done").iterdir())


def test_pause_resume_remove_and_a_missing_folder(tmp_path, fake_ocr):
    inbox = tmp_path / "In"
    inbox.mkdir()
    watcher = watchfolders.WatchFolders(thread=False)
    entry = watcher.add(inbox, {"tool": "ocr"})
    watcher.set_paused(entry["id"], True)
    text_pdf(inbox / "a.pdf")
    settle(watcher, 3)
    assert fake_ocr == [] and watcher.list()[0]["status"]["state"] == "paused"
    watcher.set_paused(entry["id"], False)
    settle(watcher)
    assert fake_ocr == ["a.pdf"]
    memory = watchfolders._memory_file(entry["id"])
    assert memory.exists()
    watcher.remove(entry["id"])
    assert watcher.list() == [] and watchfolders.load() == [] and not memory.exists()
    assert (inbox / "a.pdf").exists() and (inbox / "Done" / "a.pdf").exists()  # files left as they are
    # A folder that goes away (a USB stick): waits for it.
    gone = tmp_path / "Stick"
    gone.mkdir()
    entry = watcher.add(gone, {"tool": "ocr"})
    gone.rmdir()
    watcher.poll_all()
    status = watcher.list()[0]["status"]
    assert status["state"] == "missing" and "Waiting for the folder" in status["detail"]


def test_adding_checks_what_its_given(tmp_path):
    watcher = watchfolders.WatchFolders(thread=False)
    with pytest.raises(core.PdfManagerError, match="choose a folder"):
        watcher.add("", {"tool": "ocr"})
    with pytest.raises(core.PdfManagerError, match="no folder"):
        watcher.add(tmp_path / "nope", {"tool": "ocr"})
    with pytest.raises(core.PdfManagerError, match="whole path"):
        watcher.add("Scans", {"tool": "ocr"})
    with pytest.raises(core.PdfManagerError, match="can't be used"):
        watcher.add(tmp_path, {"tool": "merge"})  # Combine needs several files at once
    with pytest.raises(core.PdfManagerError, match="no recipe called"):
        watcher.add(tmp_path, {"recipe": "Missing"})
    watcher.add(tmp_path, {"tool": "ocr"})
    with pytest.raises(core.PdfManagerError, match="watched already"):
        watcher.add(tmp_path, {"tool": "compress"})
    (tmp_path / "Done").mkdir()
    with pytest.raises(core.PdfManagerError, match="another watch folder puts its files"):
        watcher.add(tmp_path / "Done", {"tool": "compress"})


def test_the_cli_watch_engine_is_unchanged(tmp_path):
    """`pdfm watch` still moves originals to inbox/originals and refuses the
    inbox as the destination without a suffix."""
    inbox, dest = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    with pytest.raises(core.PdfManagerError, match="different folder"):
        watch.WatchSettings(inbox=inbox, dest=inbox)
    settings = watch.WatchSettings(inbox=inbox, dest=dest, compress="low")
    assert settings.originals == "move"
    text_pdf(inbox / "x.pdf")
    watch.once(settings, settle=0.2)
    assert (dest / "x.pdf").exists() and (inbox / "originals" / "x.pdf").exists()


# -- through the server, with the background thread ------------------------------------------

@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(watchfolders, "INTERVAL", 0.3)
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    yield base, token, srv
    srv.shutdown()
    srv.server_close()


def wait_for(check, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_watching_runs_in_the_app_while_it_is_open(app, tmp_path, fake_ocr):
    base, token, srv = app
    inbox = tmp_path / "Scans"
    inbox.mkdir()
    listing = json.loads(call(base, token, "/api/watch")[2])
    assert listing["folders"] == [] and [t["id"] for t in listing["tools"]] == ["ocr", "compress", "pdfa"]
    assert srv.RequestHandlerClass.watcher.thread is None  # nothing to watch: no thread
    status, _, body = call(base, token, "/api/watch/add", {"folder": str(inbox), "tool": "ocr"})
    assert status == 200, body
    [folder] = json.loads(body)["folders"]
    assert folder["folder"] == str(inbox.resolve()) and folder["status"]["state"] in ("starting", "watching")
    text_pdf(inbox / "scan.pdf")
    news = wait_for(lambda: json.loads(call(base, token, "/api/watch/news?after=0")[2])["news"])
    assert news[0]["ok"] and news[0]["name"] == "scan.pdf" and news[0]["id"]
    # The result opens in the reader from the toast.
    assert call(base, token, f"/api/file/{news[0]['id']}")[2].startswith(b"%PDF")
    assert (inbox / "Done" / "scan.pdf").exists()
    last = json.loads(call(base, token, "/api/watch")[2])["last"]
    assert json.loads(call(base, token, f"/api/watch/news?after={last}")[2])["news"] == []
    # Bad input is a plain 400.
    status, _, body = call(base, token, "/api/watch/add", {"folder": str(tmp_path / "nope"), "tool": "ocr"})
    assert status == 400 and "no folder" in json.loads(body)["error"]
    # Pause and remove.
    fid = folder["id"]
    paused = json.loads(call(base, token, "/api/watch/pause", {"id": fid, "paused": True})[2])["folders"][0]
    assert paused["paused"] and paused["status"]["state"] == "paused"
    assert json.loads(call(base, token, "/api/watch/remove", {"id": fid})[2])["folders"] == []
    assert call(base, "wrong", "/api/watch")[0] == 403


def test_a_second_copy_of_the_app_doesnt_watch_the_same_folders(tmp_path, fake_ocr):
    inbox = tmp_path / "In"
    inbox.mkdir()
    first = watchfolders.WatchFolders(interval=0.2)
    first.add(inbox, {"tool": "ocr"})
    wait_for(lambda: first.owner.fh is not None)  # the first copy is watching
    second = watchfolders.WatchFolders(interval=0.2)
    second.start()
    try:
        wait_for(lambda: second.list()[0]["status"]["state"] == "elsewhere")
    finally:
        first.stop()
        second.stop()


# -- Settings in a browser ------------------------------------------------------------------

def test_settings_watch_folders_in_a_browser(app, tmp_path, fake_ocr):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    base, token, srv = app
    recipes.save("Tidy scans", [{"op": "compress", "options": {"level": "low"}}])
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    drop = tmp_path / "drop.pdf"
    text_pdf(drop)
    cfg = {"folder": str(inbox), "drop": str(drop), "missing": str(tmp_path / "nope")}
    run = subprocess.run([node, str(HERE / "watch30.js"), f"{base}/#token={token}", json.dumps(cfg)],
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]
    assert not out["native"], out["native"]
    # The choices: the tools, then the saved recipes.
    assert out["choices"] == ["tool:ocr", "tool:compress", "tool:pdfa", "recipe:Tidy scans"], out["choices"]
    assert out["shellMenuHidden"]  # not offered off Windows
    assert "no folder" in out["badFolder"].lower(), out["badFolder"]
    assert out["added"]["count"] == 1 and out["added"]["folder"] == str(inbox.resolve()), out["added"]
    assert "Make smaller → Done folder, originals kept" in out["added"]["what"], out["added"]
    assert out["added"]["status"].startswith(("Watching", "Starting")), out["added"]
    assert "drop.pdf is done" in out["toast"], out["toast"]
    assert "1 done" in out["statusAfter"] and "Last: drop.pdf is done" in out["statusAfter"], out["statusAfter"]
    assert out["paused"] == {"button": "Resume", "status": "Paused"}, out["paused"]
    assert out["removed"] == 0
    assert (inbox / "Done" / "drop.pdf").exists() and (inbox / "drop.pdf").exists()


# -- A file open in another program waits; it isn't a failure ------------------------------

def test_a_file_open_in_another_program_waits_and_is_done_once_closed(tmp_path, monkeypatch):
    """On Windows a file held open with no sharing raises PermissionError (WinError 32)
    when copied: it used to fail for good with "Something went wrong"."""
    import shutil as _shutil

    from pdfmanager import watch

    inbox = tmp_path / "in"
    inbox.mkdir()
    pdf = inbox / "Locked.pdf"
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(pdf)); c.drawString(72, 720, "locked"); c.save()
    settings = watch.WatchSettings(inbox=inbox, dest=inbox / "Done", compress="low")
    state, reports = watch._State(), []
    locked = {"on": True}
    real_copy = _shutil.copy2

    def copy2(src, dst, *a, **k):
        if locked["on"] and Path(src) == pdf:
            err = PermissionError(13, "The process cannot access the file because it is being used by another process")
            err.winerror = 32
            raise err
        return real_copy(src, dst, *a, **k)

    monkeypatch.setattr(watch.shutil, "copy2", copy2)
    clock = {"t": 1000.0}
    monkeypatch.setattr(watch.time, "monotonic", lambda: clock["t"])
    report = lambda path, result, error: reports.append((path.name, result, error))
    watch.poll(settings, state, log=lambda m: None, report=report)  # first look: size noted
    watch.poll(settings, state, log=lambda m: None, report=report)  # locked: waits, no report yet
    assert reports == [] and pdf in state.busy and not state.failed
    clock["t"] += watch.IN_USE_NOTE + 1
    watch.poll(settings, state, log=lambda m: None, report=report)  # still locked after a minute: said once
    watch.poll(settings, state, log=lambda m: None, report=report)
    assert len(reports) == 1 and isinstance(reports[0][2], watch.FileInUse)
    assert "open in another program" in str(reports[0][2])
    locked["on"] = False
    done = watch.poll(settings, state, log=lambda m: None, report=report)  # closed: done
    assert done and done[0][1].exists() and reports[-1][2] is None and not state.busy


def test_in_use_news_is_not_counted_as_a_failure(tmp_path):
    from pdfmanager import watch, watchfolders

    w = watchfolders.WatchFolders(thread=False)
    entry = {"id": "x", "folder": str(tmp_path)}
    w._report(entry, tmp_path / "a.pdf", None, watch.FileInUse("a.pdf is open in another program; ZPDF will do it once it's closed"))
    assert w.status.get("x", {}).get("failed", 0) == 0
    news, _ = w.news_after(0)
    assert news and news[-1]["waiting"] and "open in another program" in news[-1]["message"]


def test_in_use_covers_errors_with_and_without_a_windows_code():
    from pdfmanager import watch

    plain = PermissionError("locked")
    plain.winerror = None  # as on Windows, for one raised without a Windows error
    sharing = PermissionError(13, "in use")
    sharing.winerror = 32
    other = PermissionError(13, "odd")
    other.winerror = 1314  # a privilege error: not "in use"
    assert watch._in_use(plain) and watch._in_use(sharing) and watch._in_use(PermissionError("x"))
    assert not watch._in_use(other) and not watch._in_use(OSError("nope"))
