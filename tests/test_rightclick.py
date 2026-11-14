"""0.30.0: ZPDF's actions in Explorer's right-click menu. The registry keys
(per user) that install, uninstall and the Settings toggle add and take
away; files selected together reaching one running app as one job; and each
action's job through the server, its results shown as unsaved tabs."""

import io
import json
import re
import sys
import threading
import time
import types
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

from pdfmanager import core, gui, quickactions, winsetup
from pdfmanager.cli import build_parser
from test_more import FakeRegistry, call, text_pdf

INSTALL_PS1 = Path(__file__).resolve().parents[1] / "packaging" / "windows" / "install.ps1"
PDF_MENU = r"software\classes\systemfileassociations\.pdf\shell\zpdf"


@pytest.fixture
def windows(tmp_path, monkeypatch):
    reg = FakeRegistry()
    monkeypatch.setitem(sys.modules, "winreg", reg)
    # Windows for winsetup only (the test's own HTTP calls stay on this system).
    monkeypatch.setattr(winsetup, "sys", types.SimpleNamespace(platform="win32", prefix=sys.prefix,
                                                               executable=sys.executable))
    folders = {winsetup.CSIDL_PROGRAMS: tmp_path / "Start", winsetup.CSIDL_DESKTOPDIRECTORY: tmp_path / "Desktop"}
    monkeypatch.setattr(winsetup, "_folder", folders.get)
    monkeypatch.setattr(winsetup, "_notify_shell", lambda: None)
    monkeypatch.setattr(winsetup, "make_shortcut",
                        lambda path, *a, **k: Path(path).parent.mkdir(parents=True, exist_ok=True) or Path(path).write_text("x"))
    return reg


@pytest.fixture
def quick(monkeypatch):
    """Batches close quickly in tests."""
    monkeypatch.setattr(quickactions, "QUIET", 0.4)
    monkeypatch.setattr(quickactions, "FIRST_WAIT", 0.4)


# -- the registry ------------------------------------------------------------------------

def test_install_adds_the_right_click_menu_and_remove_takes_it_away(windows, tmp_path):
    keys = windows.keys
    app_dir = tmp_path / "app"
    done = winsetup.install(app_dir)
    assert "ZPDF in the right-click menu of PDFs and pictures" in done
    # PDFs: a "ZPDF" submenu (a verb with SubCommands and its own shell key: no COM, no admin).
    top = keys[PDF_MENU]
    assert top["MUIVerb"] == "ZPDF" and top["SubCommands"] == "" and top["Icon"].endswith("ZPDF.ico,0")
    assert top["MultiSelectModel"] == "Player"
    verbs = sorted(k[len(PDF_MENU + "\\shell\\"):] for k in keys
                   if k.startswith(PDF_MENU + "\\shell\\") and k.count("\\") == PDF_MENU.count("\\") + 2)
    assert verbs == ["1open", "2combine", "3ocr", "4compress"]  # Explorer lists them by key
    labels = [keys[rf"{PDF_MENU}\shell\{v}"]["MUIVerb"] for v in verbs]
    assert labels == ["Open in ZPDF", "Combine in ZPDF", "Make searchable (OCR)", "Make smaller"]
    for verb in verbs:
        assert keys[rf"{PDF_MENU}\shell\{verb}"]["MultiSelectModel"] == "Player"  # past 15 files too
    commands = {v: keys[rf"{PDF_MENU}\shell\{v}\command"][""] for v in verbs}
    assert commands["1open"].endswith('pythonw.exe" -I -m pdfmanager gui "%1"') or \
        commands["1open"].endswith('" -I -m pdfmanager gui "%1"')
    for verb, action in (("2combine", "combine"), ("3ocr", "ocr"), ("4compress", "compress")):
        assert commands[verb].endswith(f' -I -m pdfmanager gui --action {action} "%1"'), commands[verb]
    # Pictures: one verb on each kind (HEIC isn't offered: it can't be read).
    for ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
        verb = keys[rf"software\classes\systemfileassociations\{ext}\shell\zpdf.makepdf"]
        assert verb["MUIVerb"] == "Make a PDF with ZPDF" and verb["MultiSelectModel"] == "Player"
        command = keys[rf"software\classes\systemfileassociations\{ext}\shell\zpdf.makepdf\command"][""]
        assert command.endswith(' -I -m pdfmanager gui --action images "%1"')
    assert not any(".heic" in k for k in keys)
    # Everything under the user's own hive: HKCU\Software\Classes.
    assert all(k == "software" or k.startswith("software\\") for k in keys)
    # Explorer's command for each action parses as the app's own command line.
    args = build_parser().parse_args(["gui", "--action", "combine", "C:/a.pdf"])
    assert args.action == "combine" and args.files == ["C:/a.pdf"]

    # The uninstaller (plain PowerShell, no Python) takes the keys away too.
    script = (app_dir / "uninstall.ps1").read_text(encoding="utf-8-sig")
    for key in winsetup.context_menu_keys():
        assert f'"HKCU:\\{key}"' in script
    assert r'"HKCU:\Software\Classes\SystemFileAssociations\.pdf\shell\ZPDF"' in script
    assert "]::" not in script and all(ord(c) < 128 for c in script)  # Constrained Language mode

    winsetup.remove()
    assert not any("systemfileassociations" in k and "zpdf" in k for k in keys)


def test_install_without_the_menu_and_the_settings_toggle(windows, tmp_path, monkeypatch):
    keys = windows.keys
    winsetup.install(tmp_path / "app", context_menu=False)
    assert PDF_MENU not in keys and not winsetup.context_menu_registered()
    monkeypatch.setattr(winsetup, "default_app_dir", lambda: tmp_path / "app")
    assert winsetup.set_context_menu(True) is True
    assert keys[PDF_MENU]["Icon"] == f"{tmp_path / 'app' / 'ZPDF.ico'},0"
    assert winsetup.context_menu_registered()
    assert winsetup.set_context_menu(False) is False
    assert not any("zpdf" in k and "systemfileassociations" in k for k in keys)
    # The Open with entry is left as it was.
    assert keys[r"software\classes\.pdf\openwithprogids"] == {"PDFManager.Document": ""}


def test_installer_passes_no_context_menu_on():
    text = INSTALL_PS1.read_text(encoding="utf-8-sig")
    params = re.search(r"param\((.*?)\n\)", text.replace("\r\n", "\n"), re.S).group(1)
    assert "[switch]$NoContextMenu" in params
    assert 'if ($NoContextMenu) { $setup += "--no-context-menu" }' in text
    args = build_parser().parse_args(["setup-windows", "--no-context-menu"])
    assert args.no_context_menu


def test_settings_toggle_through_the_server(windows, tmp_path, monkeypatch):
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    try:
        monkeypatch.setattr(gui, "_shell_menu_available", lambda: False)
        assert json.loads(call(base, token, "/api/shellmenu")[2]) == {"available": False, "on": False}
        assert call(base, token, "/api/shellmenu", {"on": True})[0] == 400
        monkeypatch.setattr(gui, "_shell_menu_available", lambda: True)
        monkeypatch.setattr(winsetup, "default_app_dir", lambda: tmp_path / "app")
        assert json.loads(call(base, token, "/api/shellmenu", {"on": True})[2]) == {"available": True, "on": True}
        assert PDF_MENU in windows.keys
        assert json.loads(call(base, token, "/api/shellmenu")[2])["on"] is True
        assert json.loads(call(base, token, "/api/shellmenu", {"on": False})[2])["on"] is False
        assert PDF_MENU not in windows.keys
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_menu_is_only_offered_for_an_installed_copy_on_windows(monkeypatch):
    from pdfmanager import update

    monkeypatch.setattr(gui.sys, "platform", "linux")
    assert not gui._shell_menu_available()
    monkeypatch.setattr(gui.sys, "platform", "win32")
    monkeypatch.setattr(update, "how_installed", lambda: "frozen")
    assert not gui._shell_menu_available()
    monkeypatch.setattr(update, "how_installed", lambda: "pip")
    assert gui._shell_menu_available()


# -- gathering files into one job --------------------------------------------------------

def test_files_arriving_together_make_one_batch_in_name_order(quick, tmp_path):
    started = []
    batches = quickactions.Batches(started.append, quiet_check=0.05)
    names = ["Scan 10.pdf", "Scan 2.pdf", "scan 1.pdf"]
    for name in names:
        batch, new = batches.add("combine", [tmp_path / name])
        assert new == (name == names[0])
    other, new = batches.add("ocr", [tmp_path / "x.pdf"])  # another action: its own batch
    assert new and other is not batch
    deadline = time.monotonic() + 5
    while len(started) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert batch in started and other in started and batch.state == "running"
    assert batch.snapshot()["files"] == ["scan 1.pdf", "Scan 2.pdf", "Scan 10.pdf"]
    # A file after the batch closed starts a new one.
    later, new = batches.add("combine", [tmp_path / "late.pdf"])
    assert new and later is not batch
    batches.close()
    time.sleep(0.6)
    assert later.state == "closed" and len(started) == 2


def test_a_batch_waits_while_files_keep_coming(quick):
    batch = quickactions.Batch("combine")
    # (Fixed times: with the clock's own large numbers, now + 30 - now can come out
    # a hair under 30 in floating point, and "never forever" failed on a long run.)
    now = 1000.0
    batch.created = batch.last = now
    assert not batch.due(now + 0.2)
    assert batch.due(now + 0.5)
    batch.last = now + 0.4
    assert not batch.due(now + 0.6) and batch.due(now + 0.9)
    batch.last = now + quickactions.LONGEST_WAIT
    assert batch.due(now + quickactions.LONGEST_WAIT)  # never forever


@pytest.fixture
def app(tmp_path, monkeypatch):
    from pdfmanager import appdata

    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(gui, "data_dir", lambda: tmp_path / "appdata")
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    yield base, token, srv
    gui._unregister_instance(srv)
    srv.shutdown()
    srv.server_close()


def wait_batch(base, token, batch_id, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = json.loads(call(base, token, f"/api/action/{batch_id}")[2])
        if info["job"]:
            job = info["job"]
            while time.monotonic() < deadline:
                state = json.loads(call(base, token, f"/api/job/{job}")[2])
                if state["state"] != "running":
                    status, _, body = call(base, token, f"/api/job/{job}/result")
                    return info, status, json.loads(body)
                time.sleep(0.1)
        time.sleep(0.1)
    raise AssertionError("the batch didn't finish")


def fetch(base, token, batch_id, n):
    status, headers, body = call(base, token, f"/api/action/{batch_id}/file/{n}")
    assert status == 200, body
    return headers, body


def test_launches_from_explorer_reach_one_app_as_one_job(app, quick, tmp_path):
    """Explorer starts one copy per selected file; each hands its file to the
    running app, which shows the batch once and merges them all."""
    base, token, srv = app
    raised = []
    srv.RequestHandlerClass.bring_to_front = lambda: raised.append(1)
    gui._register_instance(srv, "desktop")
    files = [text_pdf(tmp_path / f"Part {n}.pdf", pages=(f"Part {n}",)) for n in (3, 1, 2, 10)]
    results = []
    threads = [threading.Thread(target=lambda f=f: results.append(gui.forward_to_running_app([f], action="combine")))
               for f in files]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [True] * 4
    events = json.loads(call(base, token, "/api/events?after=0")[2])["events"]
    assert len(events) == 1 and events[0]["action"]  # one batch, shown once
    batch_id = events[0]["action"]
    info, status, out = wait_batch(base, token, batch_id)
    assert info["label"] == "Combine" and info["files"] == ["Part 1.pdf", "Part 2.pdf", "Part 3.pdf", "Part 10.pdf"]
    assert status == 200 and out["failed"] == [] and len(out["results"]) == 1
    result = out["results"][0]
    assert result["name"] == "Part 1 (combined).pdf" and result["count"] == 4
    assert result["from"] == "Part 1.pdf and 3 more" and result["op"] if "op" in result else out["op"] == "merge"
    # Save starts in the files' folder: the first file is known to the app by its id.
    assert srv.RequestHandlerClass.opened.get(result["from_id"]) == tmp_path / "Part 1.pdf"
    headers, data = fetch(base, token, batch_id, 0)
    reader = PdfReader(io.BytesIO(data))
    assert [p.extract_text().strip() for p in reader.pages] == ["Part 1", "Part 2", "Part 3", "Part 10"]
    assert call(base, token, f"/api/action/{batch_id}/file/0")[0] == 404  # given once
    assert raised  # the window came forward
    # The originals are untouched.
    assert all(f.exists() for f in files)


def test_other_kinds_of_window_open_one_window_per_batch(app, quick, tmp_path, monkeypatch):
    base, token, srv = app
    opened = []
    monkeypatch.setattr(gui.webbrowser, "open", opened.append)
    gui._register_instance(srv, "browser")
    a, b = text_pdf(tmp_path / "a.pdf"), text_pdf(tmp_path / "b.pdf")
    assert gui.forward_to_running_app([a], action="compress") is True
    assert gui.forward_to_running_app([b], action="compress") is True
    assert len(opened) == 1 and re.search(rf"#token={token}&action=a\d+-\d+$", opened[0])
    batch_id = opened[0].rsplit("&action=", 1)[1]
    info, status, out = wait_batch(base, token, batch_id)
    assert info["files"] == ["a.pdf", "b.pdf"] and len(out["results"]) == 2  # one result per file


def test_a_fresh_start_from_explorer_runs_the_action(quick, tmp_path):
    a = text_pdf(tmp_path / "a.pdf")
    image = tmp_path / "photo.png"
    Image.new("RGB", (200, 100), (200, 30, 30)).save(image)
    srv, url = gui.make_server(action=("images", [image]))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, rest = url.split("/#token=")
    token, batch_id = rest.split("&action=")
    try:
        assert "&open=" not in url
        info, status, out = wait_batch(base, token, batch_id)
        assert out["results"][0]["name"] == "photo.pdf" and out["op"] == "from-images"
        _, data = fetch(base, token, batch_id, 0)
        assert len(PdfReader(io.BytesIO(data)).pages) == 1
    finally:
        srv.shutdown()
        srv.server_close()
    assert a.exists()


def test_bad_action_requests_are_refused(app, tmp_path):
    base, token, srv = app
    gui._register_instance(srv, "desktop")
    secret = srv.RequestHandlerClass.instance_secret
    proof = gui._proof(secret, "open", "n")
    assert call(base, token, "/api/instance", {"nonce": "n", "proof": proof, "action": "format-disk", "paths": []})[0] == 400
    assert call(base, token, "/api/instance", {"nonce": "n", "proof": "x", "action": "combine", "paths": []})[0] == 403
    assert call(base, token, "/api/action/nope")[0] == 404
    assert call(base, "wrong", "/api/action/nope")[0] == 403


# -- each action ---------------------------------------------------------------------------

def run(action, paths, tmp_path):
    work = tmp_path / f"work-{action}-{time.monotonic_ns()}"
    work.mkdir()
    return gui.run_action(action, paths, work)


def test_combine_needs_two_pdfs_and_skips_what_isnt_one(tmp_path):
    a = text_pdf(tmp_path / "a.pdf")
    (tmp_path / "notes.txt").write_text("x")
    with pytest.raises(gui.UserError, match="needs at least 2 PDFs"):
        run("combine", [a], tmp_path)
    b = text_pdf(tmp_path / "b.pdf", pages=("one", "two"))
    results, failed = run("combine", [b, tmp_path / "notes.txt", a], tmp_path)
    assert failed == [{"name": "notes.txt", "error": "it isn't a PDF"}]
    assert len(PdfReader(io.BytesIO(results[0]["data"])).pages) == 3
    assert results[0]["sources"] == [a, b]


def test_make_smaller_gives_one_result_per_file_and_goes_on_past_a_bad_one(tmp_path):
    good = text_pdf(tmp_path / "good.pdf")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 this is not really a PDF")
    results, failed = run("compress", [good, bad], tmp_path)
    assert [r["name"] for r in results] == ["good_compressed.pdf"] or results[0]["name"].startswith("good")
    assert [f["name"] for f in failed] == ["bad.pdf"] and failed[0]["error"]


def test_make_searchable_runs_ocr_with_its_default_options(tmp_path, monkeypatch):
    seen = {}

    def fake(src, out, **kw):
        seen.update(kw)
        Path(out).write_bytes(Path(src).read_bytes())
        return Path(out)

    monkeypatch.setattr(core, "ocr", fake)
    scan = text_pdf(tmp_path / "scan.pdf")
    results, failed = run("ocr", [scan], tmp_path)
    assert not failed and results[0]["data"].startswith(b"%PDF")
    assert seen["engine"] == "auto" and seen["language"] == "eng" and seen["rotate"] is True
    assert seen["force"] is False and seen["deskew"] is False


def test_pictures_make_one_pdf_and_heic_is_refused_plainly(tmp_path):
    pics = []
    for name, size in (("b.jpg", (300, 200)), ("a.png", (200, 300))):
        Image.new("RGB", size, (20, 120, 200)).save(tmp_path / name)
        pics.append(tmp_path / name)
    heic = tmp_path / "phone.heic"
    heic.write_bytes(b"\x00\x00\x00\x18ftypheic")
    results, failed = run("images", pics + [heic], tmp_path)
    assert failed[0]["name"] == "phone.heic" and "HEIC" in failed[0]["error"] and "JPG" in failed[0]["error"]
    pages = PdfReader(io.BytesIO(results[0]["data"])).pages
    assert len(pages) == 2 and results[0]["name"] == "a (combined).pdf"
    assert float(pages[0].mediabox.width) < float(pages[0].mediabox.height)  # a.png (portrait) first
    with pytest.raises(gui.UserError, match="HEIC"):
        run("images", [heic], tmp_path)


def test_each_action_through_the_server(app, quick, tmp_path, monkeypatch):
    """Combine, Make searchable, Make smaller and Make a PDF, each from a
    launch handed to the running app, end to end."""
    base, token, srv = app
    monkeypatch.setattr(core, "ocr", lambda src, out, **kw: Path(out).write_bytes(Path(src).read_bytes()) or Path(out))
    gui._register_instance(srv, "desktop")
    a, b = text_pdf(tmp_path / "a.pdf"), text_pdf(tmp_path / "b.pdf")
    pic = tmp_path / "pic.jpg"
    Image.new("RGB", (120, 80), (0, 0, 0)).save(pic)
    after = 0
    for action, files, results in (("combine", [a, b], 1), ("ocr", [a], 1), ("compress", [a, b], 2), ("images", [pic], 1)):
        for f in files:
            assert gui.forward_to_running_app([f], action=action)
        events = json.loads(call(base, token, f"/api/events?after={after}")[2])
        after = events["last"]
        [batch_id] = [e["action"] for e in events["events"]]
        info, status, out = wait_batch(base, token, batch_id)
        assert status == 200 and len(out["results"]) == results and not out["failed"], (action, out)
        for r in out["results"]:
            assert fetch(base, token, batch_id, r["n"])[1].startswith(b"%PDF")
            assert r["size"] > 0 and r["before"] > 0 and r["notes"] is not None


def test_a_failed_action_says_why(app, quick, tmp_path):
    base, token, srv = app
    gui._register_instance(srv, "desktop")
    a = text_pdf(tmp_path / "only.pdf")
    assert gui.forward_to_running_app([a], action="combine")
    [event] = json.loads(call(base, token, "/api/events?after=0")[2])["events"]
    info, status, body = wait_batch(base, token, event["action"])
    assert status == 400 and "needs at least 2 PDFs" in body["error"]


# -- in the app's page ---------------------------------------------------------------------

def test_the_page_runs_the_action_and_shows_an_unsaved_tab(quick, tmp_path):
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    a = text_pdf(tmp_path / "Letter 1.pdf", pages=("one",))
    b = text_pdf(tmp_path / "Letter 2.pdf", pages=("two", "three"))
    srv, url = gui.make_server(action=("combine", [b, a]))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        run_js = subprocess.run([node, str(Path(__file__).resolve().parent / "actions30.js"), url],
                                capture_output=True, text=True, encoding="utf-8", timeout=300)
    finally:
        srv.shutdown()
        srv.server_close()
    if run_js.returncode and "Executable doesn't exist" in run_js.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run_js.returncode == 0, run_js.stderr
    out = json.loads(run_js.stdout)
    assert not out["errors"], out["errors"]
    assert not out["native"], out["native"]
    assert out["cardTitle"] == "Combine: 2 files"
    assert "Opened Letter 1 (combined).pdf in a new tab" in out["cardMsg"] and "Save asks where" in out["cardMsg"]
    tab = out["tab"]
    assert tab["name"] == "Letter 1 (combined).pdf" and tab["unsaved"] and tab["pages"] == 3, tab
    assert tab["from"] == "Letter 1.pdf and 1 more" and tab["fromId"] and tab["op"] == "merge", tab
    # Save starts in the folder of the files (the first file is known to the app by that id).
    assert srv.RequestHandlerClass.opened.get(tab["fromId"]) == a
    assert "Made from Letter 1.pdf and 1 more." in out["note"] and "still open" not in out["note"], out["note"]
    assert out["saveOffered"] and out["cardGone"]
