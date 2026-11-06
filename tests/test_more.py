"""Tests for progress and cancelling, recent files, single instance, headers
and footers, extracting pictures and attachments, folder search, form
fields, text edits, Windows set-up and updates."""

import io
import json
import re
import threading
import zipfile
from pathlib import Path
import time
import urllib.error
import urllib.request

import pytest
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from pdfmanager import core, gui, progress
from pdfmanager.cli import main


def text_pdf(path, pages=("Hello world",)):
    c = canvas.Canvas(str(path), pagesize=A4)
    for text in pages:
        c.setFont("Helvetica", 14)
        c.drawString(72, 760, text)
        c.showPage()
    c.save()
    return path


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A running server whose app data lives in tmp_path."""
    from pdfmanager import appdata, recipes

    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(gui, "data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(recipes, "data_dir", lambda: tmp_path / "appdata")
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    yield base, token, srv
    srv.shutdown()
    srv.server_close()


def call(base, token, path, data=None, headers=None, method=None):
    headers = {"X-Pdfm-Token": token, **(headers or {})}
    if isinstance(data, (dict, list)):
        data, headers["Content-Type"] = json.dumps(data).encode(), "application/json"
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def multipart(fields, files):
    boundary = "pdfmtestboundary"
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    for name, path in files:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                     f'filename="{path.name}"\r\nContent-Type: application/pdf\r\n\r\n'.encode()
                     + path.read_bytes() + b"\r\n")
    return (b"".join(parts) + f"--{boundary}--\r\n".encode(),
            {"Content-Type": f"multipart/form-data; boundary={boundary}"})


# -- progress and cancelling ----------------------------------------------------------

def test_progress_sections_and_pages():
    seen = []

    def work():
        for i in range(2):
            with progress.section(i, 2, f"file{i}"):
                for n in range(4):
                    progress.report(n, 4, "page")
                    seen.append((round(job.fraction, 3), job.message))
        return "ok"

    job = progress.Job()
    job.run(work)
    assert job.state == "done" and job.result == "ok" and job.fraction == 1.0
    assert seen[0] == (0.0, "file0 — page 1 of 4")
    assert seen[5] == (0.625, "file1 — page 2 of 4")
    progress.report(1, 2, "page")  # outside a job: nothing happens


def test_cancelled_job_stops_at_the_next_report():
    reached = []

    def work():
        for n in range(1000):
            progress.report(n, 1000)
            reached.append(n)
            time.sleep(0.002)

    job = progress.Job().start(work)
    time.sleep(0.1)
    job.cancel()
    time.sleep(0.1)
    assert job.state == "cancelled" and len(reached) < 1000


def test_failed_job_reports_the_error():
    job = progress.Job()
    job.expected = (core.PdfManagerError,)
    job.run(lambda: (_ for _ in ()).throw(core.PdfManagerError("no good")))
    assert job.snapshot()["error"] == "no good"
    job = progress.Job()
    job.run(lambda: 1 / 0)
    assert job.snapshot()["error"].startswith("ZeroDivisionError")


def test_tools_run_as_jobs(app, tmp_path):
    base, token, _ = app
    src = text_pdf(tmp_path / "a.pdf", ["One", "Two", "Three"])
    body, headers = multipart({"op": "reverse"}, [("files", src)])
    status, _, out = call(base, token, "/api/run", body, {**headers, "X-Pdfm-Async": "1"})
    assert status == 200
    job = json.loads(out)["job"]
    for _ in range(100):
        state = json.loads(call(base, token, f"/api/job/{job}")[2])
        if state["state"] != "running":
            break
        time.sleep(0.05)
    assert state["state"] == "done" and state["fraction"] == 1.0
    status, headers, data = call(base, token, f"/api/job/{job}/result")
    assert status == 200 and headers["X-Pdfm-Filename"] == "a_reversed.pdf"
    assert data.startswith(b"%PDF")
    assert call(base, token, f"/api/job/{job}")[0] == 404  # collected
    assert call(base, "wrong", f"/api/job/{job}")[0] == 403


def test_failed_job_result_is_an_error(app, tmp_path):
    base, token, _ = app
    body, headers = multipart({"op": "extract", "pages": "9"}, [("files", text_pdf(tmp_path / "a.pdf"))])
    job = json.loads(call(base, token, "/api/run", body, {**headers, "X-Pdfm-Async": "1"})[2])["job"]
    for _ in range(100):
        if json.loads(call(base, token, f"/api/job/{job}")[2])["state"] != "running":
            break
        time.sleep(0.05)
    status, _, out = call(base, token, f"/api/job/{job}/result")
    assert status == 400 and "out of range" in json.loads(out)["error"]


# -- recent files -------------------------------------------------------------------------

def test_recent_files(app, tmp_path):
    base, token, srv = app
    a, b = text_pdf(tmp_path / "a.pdf"), text_pdf(tmp_path / "b.pdf")
    opened = srv.RequestHandlerClass.opened
    for path in (a, b, a):  # opening by id (desktop Open dialog, "Open with") records it
        assert call(base, token, f"/api/file/{opened.add(path)}")[0] == 200
    files = json.loads(call(base, token, "/api/recent")[2])["files"]
    assert [f["name"] for f in files] == ["a.pdf", "b.pdf"]  # newest first, no repeats
    # Each comes with an id the reader can open, and files that are gone are left out.
    assert call(base, token, f"/api/file/{files[1]['id']}")[2].startswith(b"%PDF")
    b.unlink()
    assert [f["name"] for f in json.loads(call(base, token, "/api/recent")[2])["files"]] == ["a.pdf"]
    call(base, token, "/api/recent/forget", {"path": files[0]["path"]})
    assert json.loads(call(base, token, "/api/recent")[2])["files"] == []
    assert call(base, "wrong", "/api/recent")[0] == 403


# -- one running copy ---------------------------------------------------------------------

def test_files_are_handed_to_the_running_app(app, tmp_path, monkeypatch):
    base, token, srv = app
    pdf = text_pdf(tmp_path / "a.pdf")
    raised = threading.Event()
    release = threading.Event()

    def bring_to_front():  # pywebview can take a while (it waits for the window's thread)
        raised.set()
        release.wait(10)

    srv.RequestHandlerClass.bring_to_front = bring_to_front
    gui._register_instance(srv, "desktop")
    # Its own window: the app opens the files itself and comes to the front,
    # answering straight away (not after the window has come up).
    started = time.monotonic()
    assert gui.forward_to_running_app([pdf]) is True
    assert time.monotonic() - started < 3
    assert raised.wait(5)
    release.set()
    events = json.loads(call(base, token, "/api/events?after=0")[2])
    ids = events["events"][0]["open"]
    assert len(ids) == 1
    assert call(base, token, f"/api/file/{ids[0]}")[2].startswith(b"%PDF")
    # Edge window: the new launch opens a window on the running app, with
    # the browser and profile it works out itself (nothing from the answer).
    launched = []
    monkeypatch.setattr(gui.subprocess, "Popen", lambda args, **kw: launched.append(args))
    monkeypatch.setattr(gui, "find_app_browser", lambda: "/opt/edge")
    monkeypatch.setattr(gui, "data_dir", lambda: tmp_path)
    gui._register_instance(srv, "app-window")
    assert gui.forward_to_running_app([pdf]) is True
    assert launched[0][0] == "/opt/edge" and launched[0][1].startswith("--app=http://127.0.0.1:")
    assert f"#token={token}&open=" in launched[0][1]
    assert launched[0][2] == f"--user-data-dir={tmp_path / f'window-{srv.server_address[1]}'}"
    # Browser tab: a new tab.
    opened = []
    monkeypatch.setattr(gui.webbrowser, "open", opened.append)
    gui._register_instance(srv, "browser")
    assert gui.forward_to_running_app([]) is True and opened[0].endswith(f"#token={token}")
    gui._unregister_instance(srv)
    assert gui.forward_to_running_app([pdf]) is False  # nothing registered


def test_handing_over_needs_the_secret(app):
    base, token, srv = app
    gui._register_instance(srv, "desktop")
    assert call(base, token, "/api/instance", {"secret": "guess", "paths": []})[0] == 403
    assert call(base, token, "/api/instance", {"nonce": "n", "proof": "guess", "paths": []})[0] == 403
    # the real proof works, and the answer proves the server knows the secret too
    secret = srv.RequestHandlerClass.instance_secret
    status, _, out = call(base, token, "/api/instance", {"nonce": "n", "proof": gui._proof(secret, "open", "n")})
    assert status == 200 and json.loads(out)["proof"] == gui._proof(secret, "answer", "n")
    assert "secret" not in out.decode()


def test_an_impostor_on_the_port_is_ignored(tmp_path, monkeypatch):
    """After a crash instance.json can name a port someone else now listens
    on; its answers must not be acted on."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Impostor(BaseHTTPRequestHandler):
        seen = []

        def do_POST(self):
            Impostor.seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"url": "http://127.0.0.1:1/#token=x", "mode": "app-window",
                               "browser": "/tmp/evil"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Impostor)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(gui, "data_dir", lambda: tmp_path)
    (tmp_path / "instance.json").write_text(json.dumps({"port": srv.server_address[1], "pid": 1, "secret": "s3cret"}))
    launched, opened = [], []
    monkeypatch.setattr(gui.subprocess, "Popen", lambda args, **kw: launched.append(args))
    monkeypatch.setattr(gui.webbrowser, "open", opened.append)
    assert gui.forward_to_running_app([text_pdf(tmp_path / "a.pdf")]) is False
    assert launched == [] and opened == []
    assert "secret" not in Impostor.seen[0] and Impostor.seen[0]["proof"] != "s3cret"  # never sent
    srv.shutdown()
    srv.server_close()


@pytest.mark.skipif(__import__("os").name != "posix", reason="file modes")
def test_app_files_are_private(tmp_path, monkeypatch, app):
    import os
    import stat
    from pdfmanager import appdata, search, update

    private = lambda: appdata._private_dir(tmp_path / "home" / "pdf-manager")  # noqa: E731
    folder = private()
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    base, token, srv = app
    for module in (gui, update, search):
        monkeypatch.setattr(module, "data_dir", private)
    gui._register_instance(srv, "browser")
    update.save_settings(github_token="ghp_x")
    search._db().close()
    gui.RecentFiles.add(text_pdf(tmp_path / "a.pdf"))
    for name in ("instance.json", "settings.json", "search.sqlite", "recent.json"):
        assert stat.S_IMODE((folder / name).stat().st_mode) == 0o600, name
    assert not list(folder.glob("*.tmp"))
    gui._unregister_instance(srv)


def test_uninstaller_only_deletes_the_installers_folder(tmp_path, monkeypatch):
    import sys
    from pdfmanager import winsetup

    reg = FakeRegistry()
    monkeypatch.setitem(sys.modules, "winreg", reg)
    monkeypatch.setattr(winsetup.sys, "platform", "win32")
    monkeypatch.setattr(winsetup, "_folder", {winsetup.CSIDL_PROGRAMS: tmp_path / "S", winsetup.CSIDL_DESKTOPDIRECTORY: tmp_path / "D"}.get)
    monkeypatch.setattr(winsetup, "_notify_shell", lambda: None)
    monkeypatch.setattr(winsetup, "make_shortcut", lambda path, *a, **k: Path(path).parent.mkdir(parents=True, exist_ok=True) or Path(path).write_text("x"))
    monkeypatch.setattr(winsetup, "data_dir", lambda: tmp_path / "data")
    winsetup.install(tmp_path / "data" / "app")  # where the installer puts it
    assert "Remove-Item -LiteralPath $app -Recurse" in (tmp_path / "data" / "app" / "uninstall.ps1").read_text(encoding="utf-8-sig")
    winsetup.install(tmp_path / "src" / "venv")  # a hand-made environment somewhere else
    script = (tmp_path / "src" / "venv" / "uninstall.ps1").read_text(encoding="utf-8-sig")
    assert "Remove-Item -LiteralPath $app" not in script and "left for you" in script


@pytest.mark.skipif(not __import__("pathlib").Path("/proc").is_dir(), reason="needs /proc")
def test_profile_processes_match_the_whole_argument(tmp_path):
    import subprocess
    import sys

    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", f"--user-data-dir={tmp_path / 'window-9000'}"])
    try:
        time.sleep(0.3)
        assert other.pid in gui._profile_processes(tmp_path / "window-9000")
        assert other.pid not in gui._profile_processes(tmp_path / "window")  # a prefix is not a match
    finally:
        other.kill()
        other.wait()


def test_stale_instance_file_is_ignored(app, tmp_path):
    from pdfmanager import appdata

    target = appdata.data_dir() / "instance.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"port": 1, "secret": "x", "pid": 1, "mode": "desktop"}))
    assert gui.forward_to_running_app([]) is False


def test_closing_one_of_two_windows_keeps_the_app():
    pages = {"a": time.monotonic(), "b": time.monotonic()}
    handler = type("H", (), {"last_seen": time.monotonic(), "closed_at": None, "pages": pages})

    def close_a_then_b():
        time.sleep(0.2)
        pages.pop("a")
        handler.closed_at = time.monotonic()
        time.sleep(0.6)  # b is still open: the app must keep running
        assert not done
        pages.pop("b")
        handler.closed_at = time.monotonic()

    done = []
    threading.Thread(target=close_a_then_b, daemon=True).start()
    t = time.monotonic()
    gui.wait_until_closed(handler, poll=0.05, grace=0.2)
    done.append(1)
    assert 1.0 <= time.monotonic() - t < 2


def test_page_ids_are_tracked(app):
    base, token, srv = app
    call(base, token, "/api/ping", b"", {"X-Pdfm-Page": "p1"})
    assert "p1" in srv.RequestHandlerClass.pages
    call(base, token, f"/api/bye?token={token}&page=p1", b"")
    assert "p1" not in srv.RequestHandlerClass.pages


# -- headers and footers ------------------------------------------------------------------

def test_headers_and_footers(tmp_path):
    from pypdf import PdfWriter

    src = text_pdf(tmp_path / "doc.pdf", ["One", "Two", "Three"])
    writer = PdfWriter(clone_from=str(src))
    writer.pages[1].rotate(90)
    writer.write(src)
    nxt = core.headers_footers(src, tmp_path / "out.pdf",
                               {"top_right": "{file}", "bottom_center": "Page {n} of {total}",
                                "bottom_right": "{bates}", "top_left": "{unknown} stays"},
                               bates_prefix="ABC", bates_start=41, pages="2-3")
    assert nxt == 43
    reader = PdfReader(tmp_path / "out.pdf")
    assert "Page" not in reader.pages[0].extract_text()  # not in the page list
    second = reader.pages[1].extract_text()
    assert "Page 1 of 3" in second and "ABC000041" in second and "doc" in second
    assert "{unknown} stays" in second
    assert "ABC000042" in reader.pages[2].extract_text()
    with pytest.raises(core.PdfManagerError, match="at least one"):
        core.headers_footers(src, tmp_path / "x.pdf", {"top_left": " "})


def test_bates_numbers_continue_across_files(tmp_path, capsys):
    a = text_pdf(tmp_path / "a.pdf", ["1", "2"])
    b = text_pdf(tmp_path / "b.pdf", ["3", "4", "5"])
    assert main(["headers", str(a), str(b), "-o", str(tmp_path / "out"), "--bottom-right",
                 "{bates}", "--bates", "X-", "--bates-digits", "4"]) == 0
    out = capsys.readouterr().out
    assert "X-0001–X-0002" in out and "X-0003–X-0005" in out
    assert "X-0005" in PdfReader(tmp_path / "out" / "b.pdf").pages[2].extract_text()
    # The app does the same for several files.
    work = tmp_path / "work"
    work.mkdir()
    name, data, _ = gui.run_operation("headers", [a, b], {"bottom_right": "{bates}",
                                                         "bates_prefix": "Q", "bates_start": "9",
                                                         "bates_digits": "3"}, work=work)
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        second = PdfReader(io.BytesIO(z.read("b_headers.pdf")))
    assert "Q011" in second.pages[0].extract_text()


# -- pictures and attachments -----------------------------------------------------------------

def media_pdf(tmp_path):
    from PIL import Image
    from pypdf import PdfWriter

    Image.new("RGB", (300, 200), (200, 30, 30)).save(tmp_path / "red.jpg", quality=90)
    Image.new("RGB", (120, 120), (30, 30, 200)).save(tmp_path / "blue.png")
    Image.new("RGB", (4, 4), (0, 0, 0)).save(tmp_path / "dot.png")
    c = canvas.Canvas(str(tmp_path / "pics.pdf"), pagesize=A4)
    c.drawImage(str(tmp_path / "red.jpg"), 72, 500, 300, 200)
    c.drawImage(str(tmp_path / "dot.png"), 72, 400, 4, 4)
    c.showPage()
    c.drawImage(str(tmp_path / "red.jpg"), 72, 500, 150, 100)  # the same picture again
    c.drawImage(str(tmp_path / "blue.png"), 72, 300, 120, 120)
    c.showPage()
    c.save()
    writer = PdfWriter(clone_from=str(tmp_path / "pics.pdf"))
    writer.add_attachment("notes.txt", b"attached notes")
    writer.write(tmp_path / "pics.pdf")
    return tmp_path / "pics.pdf"


def test_extract_pictures_and_attachments(tmp_path):
    found = core.extract_media(media_pdf(tmp_path), tmp_path / "out")
    from PIL import Image

    # Each picture once (the red one is on both pages), the tiny dot skipped,
    # JPEG kept as JPEG.
    sizes = sorted((p.name.split("_")[1], p.suffix, Image.open(p).size) for p in found["images"])
    assert sizes == [("page1", ".jpg", (300, 200)), ("page2", ".png", (120, 120))]
    assert [p.read_bytes() for p in found["attachments"]] == [b"attached notes"]
    assert found["attachments"][0].name == "notes.txt"


def test_cli_and_app_extract_media(tmp_path, capsys):
    src = media_pdf(tmp_path)
    assert main(["extract-media", str(src), "--no-attachments"]) == 0
    assert "2 picture(s), 0 attachment(s)" in capsys.readouterr().out
    assert len(list((tmp_path / "pics_media").glob("pics_page1_*.jpg"))) == 1
    work = tmp_path / "work"
    work.mkdir()
    notes = []
    name, data, ctype = gui.run_operation("media", [src], {}, work=work, notes=notes)
    assert name == "pics_media.zip" and ctype == "application/zip"
    assert notes == ["pics.pdf: 2 pictures, 1 attached file"]
    work2 = tmp_path / "work2"
    work2.mkdir()
    with pytest.raises(gui.UserError, match="nothing was found"):
        gui.run_operation("media", [text_pdf(tmp_path / "plain.pdf")], {}, work=work2)


# -- folder search ------------------------------------------------------------------------------

def test_search_a_folder(tmp_path, monkeypatch):
    from pdfmanager import search

    monkeypatch.setattr(search, "data_dir", lambda: tmp_path / "appdata")
    folder = tmp_path / "docs"
    (folder / "sub").mkdir(parents=True)
    text_pdf(folder / "a.pdf", ["The invoice total", "nothing here", "Invoice again, invoice"])
    text_pdf(folder / "sub" / "b.pdf", ["Old INVOICE"])
    core.encrypt(text_pdf(folder / "c.pdf", ["invoice"]), folder / "locked.pdf", "pw")
    found = search.search(folder, "invoice")
    by_file = {(Path(m["path"]).name, m["page"]): m for m in found["matches"]}
    assert set(by_file) == {("a.pdf", 1), ("a.pdf", 3), ("c.pdf", 1)}  # not in sub/ without -R
    assert by_file[("a.pdf", 3)]["count"] == 2 and by_file[("a.pdf", 1)]["match"] == "invoice"
    assert [s[1] for s in found["skipped"]] == ["password protected"]
    deep = search.search(folder, "old invoice", recursive=True)
    assert [m["page"] for m in deep["matches"]] == [1]
    assert search.search(folder, "INVOICE", case_sensitive=True)["files"] == 3
    assert len(search.search(folder, "INVOICE", case_sensitive=True, recursive=True)["matches"]) == 1
    # The text is cached: a second search doesn't read the files again.
    calls = []
    monkeypatch.setattr(search, "_extract", lambda p: calls.append(p) or [])
    search.search(folder, "invoice")
    assert calls == []
    text_pdf(folder / "a.pdf", ["changed"])  # a changed file is read again
    search.search(folder, "invoice")
    assert [p.name for p in calls] == ["a.pdf"]


def test_cli_search_and_app(tmp_path, monkeypatch, capsys, app):
    from pdfmanager import search

    monkeypatch.setattr(search, "data_dir", lambda: tmp_path / "appdata")
    text_pdf(tmp_path / "x.pdf", ["Find the needle here"])
    assert main(["search", str(tmp_path), "needle"]) == 0
    assert "page 1: Find the [needle] here" in capsys.readouterr().out
    assert main(["search", str(tmp_path), "haystack"]) == 1
    base, token, _ = app
    status, _, body = call(base, token, "/api/folder", {"op": "search", "options": {
        "directory": str(tmp_path), "query": "needle"}})
    match = json.loads(body)["matches"][0]
    assert status == 200 and match["page"] == 1
    assert call(base, token, f"/api/file/{match['id']}")[2].startswith(b"%PDF")


# -- form fields ----------------------------------------------------------------------------------

def test_add_form_fields(tmp_path):
    from pypdf import PdfWriter
    from pdfmanager import forms

    src = text_pdf(tmp_path / "blank.pdf", ["Name:", "Sideways"])
    w = PdfWriter(clone_from=src)
    w.pages[1].rotate(90)
    w.write(src)
    out = tmp_path / "form.pdf"
    names = forms.add_fields(src, out, [
        {"page": 1, "rect": [120, 756, 400, 776], "type": "text", "name": "Name"},
        {"page": 1, "rect": [140, 716, 152, 728], "type": "checkbox"},
        {"page": 1, "rect": [72, 500, 400, 600], "type": "multiline", "required": True},
        {"page": 1, "rect": [272, 450, 72, 400], "type": "signature"},  # corners in any order
        {"page": 2, "rect": [72, 700, 300, 720], "type": "text", "name": "Na.me"},
    ])
    assert names == ["Name", "Checkbox 1", "Text 1", "Signature 1", "Na me"]
    reader = PdfReader(out)
    fields = reader.get_fields()
    assert {k: v["/FT"] for k, v in fields.items()} == {
        "Name": "/Tx", "Checkbox 1": "/Btn", "Text 1": "/Tx", "Signature 1": "/Sig", "Na me": "/Tx"}
    assert fields["Text 1"]["/Ff"] == forms.MULTILINE | forms.REQUIRED
    assert reader.trailer["/Root"]["/AcroForm"]["/SigFlags"] == 1
    box = [a.get_object() for a in reader.pages[0]["/Annots"]][1]
    assert set(box["/AP"]["/N"]) == {"/Yes", "/Off"} and box["/AS"] == "/Off"
    sideways = reader.pages[1]["/Annots"][0].get_object()
    assert sideways["/MK"]["/R"] == 90 and list(sideways["/AP"]["/N"]["/BBox"]) == [0, 0, 20, 228]
    assert reader.pages[0]["/Tabs"] == "/R"
    found = forms.existing_fields(out)
    assert [f["name"] for f in found[1]] == ["Name", "Checkbox 1", "Text 1", "Signature 1"]
    assert found[1][3] == {"rect": [72.0, 400.0, 272.0, 450.0], "name": "Signature 1",
                           "type": "signature"}
    # adding more keeps the names unique
    again = forms.add_fields(out, tmp_path / "more.pdf", [
        {"page": 1, "rect": [10, 10, 100, 30], "type": "text"},
        {"page": 1, "rect": [10, 40, 100, 60], "type": "text", "name": "Name"}])
    assert again == ["Name 2", "Text 2"] or again == ["Text 2", "Name 2"]
    assert len(PdfReader(tmp_path / "more.pdf").get_fields()) == 7
    # filling in works
    filled = PdfWriter(clone_from=out)
    filled.update_page_form_field_values(filled.pages[0], {"Name": "Ada", "Checkbox 1": "/Yes"},
                                         auto_regenerate=False)
    filled.write(tmp_path / "filled.pdf")
    values = PdfReader(tmp_path / "filled.pdf").get_fields()
    assert values["Name"]["/V"] == "Ada" and values["Checkbox 1"]["/V"] == "/Yes"


def test_form_field_mistakes(tmp_path):
    from pdfmanager import forms

    src = text_pdf(tmp_path / "blank.pdf")
    with pytest.raises(core.PdfManagerError, match="unknown field type"):
        forms.add_fields(src, tmp_path / "o.pdf", [{"page": 1, "rect": [0, 0, 50, 50], "type": "radio"}])
    with pytest.raises(core.PdfManagerError, match="page 3 doesn't exist"):
        forms.add_fields(src, tmp_path / "o.pdf", [{"page": 3, "rect": [0, 0, 50, 50]}])
    with pytest.raises(core.PdfManagerError, match="too small"):
        forms.add_fields(src, tmp_path / "o.pdf", [{"page": 1, "rect": [0, 0, 2, 50]}])
    with pytest.raises(core.PdfManagerError, match="no fields"):
        forms.add_fields(src, tmp_path / "o.pdf", [])
    with pytest.raises(core.PdfManagerError, match="invalid field"):
        forms.parse_field("text:1:1,2,3")
    assert forms.parse_field("checkbox:2:1,2,3,4:I: agree") == {
        "type": "checkbox", "page": 2, "rect": [1, 2, 3, 4], "name": "I: agree"}


def test_cli_and_app_form_fields(tmp_path, capsys, app):
    src = text_pdf(tmp_path / "blank.pdf")
    assert main(["form", str(src), "-f", "text:1:72,700,300,720:Name", "-f",
                 "checkbox:1:72,650,84,662", "--no-border"]) == 0
    assert "2 fields: Name, Checkbox 1" in capsys.readouterr().out
    out = tmp_path / "blank_form.pdf"
    assert main(["form", str(out)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "text:1:72,700,300,720:Name", "checkbox:1:72,650,84,662:Checkbox 1"]
    assert "/BS" not in PdfReader(out).pages[0]["/Annots"][0].get_object()

    work = tmp_path / "work"
    work.mkdir()
    notes = []
    fields = {"1": [{"rect": [72, 600, 300, 620], "type": "text", "name": "Email"},
                    {"rect": [72, 500, 300, 560], "type": "multiline", "name": "Notes"}]}
    name, data, _ = gui.run_operation("form", [src], {"fields": json.dumps(fields), "border": "true",
                                                      "required": "true", "font_size": "12"},
                                      work=work, notes=notes)
    assert name == "blank_form.pdf" and notes == ["blank.pdf: added 2 fields (Email, Notes)"]
    email = PdfReader(io.BytesIO(data)).pages[0]["/Annots"][0].get_object()
    assert email["/T"] == "Email" and email["/DA"] == "/Helv 12 Tf 0 g" and email["/Ff"] == 2
    with pytest.raises(gui.UserError, match="draw the fields"):
        gui.run_operation("form", [src], {"fields": "{}"}, work=tmp_path)

    base, token, _ = app
    body, headers = multipart({"password": ""}, [("file", out)])
    status, _, res = call(base, token, "/api/form/fields", body, headers)
    assert status == 200
    assert [f["name"] for f in json.loads(res)["fields"]["1"]] == ["Name", "Checkbox 1"]


# -- changing words on a page ---------------------------------------------------------------------

def edit_pdf(path):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFillColorRGB(0.85, 0.92, 1)
    c.rect(0, 600, 595, 242, fill=1, stroke=0)
    c.setFillColorRGB(0.8, 0, 0)
    c.setFont("Times-Bold", 18)
    c.drawString(72, 760, "Invoice for March")
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 11)
    c.drawString(72, 700, "Total due: 1,200.00 EUR")
    c.save()
    return path


def test_edit_text(tmp_path):
    import pypdfium2 as pdfium
    from pdfmanager import textedit

    src = edit_pdf(tmp_path / "invoice.pdf")
    out = tmp_path / "edited.pdf"
    assert textedit.edit_text(src, out, [
        {"page": 1, "rect": [72, 755, 230, 775], "text": "Invoice for April"},
        {"page": 1, "rect": [72, 697, 200, 711], "text": ""},  # just cover it
        {"page": 1, "rect": [300, 300, 400, 320], "text": "New words"},  # nothing there before
    ]) == 3
    doc = pdfium.PdfDocument(str(out))
    page = doc[0]
    text = page.get_textpage().get_text_range()
    assert "Invoice for April" in text and "New words" in text
    assert "Invoice for March" in text  # still underneath, as the tool says
    image = page.render(scale=1).to_pil().convert("RGB")
    # the new title is red like the old one
    reds = [image.getpixel((x, 842 - 763)) for x in range(72, 230)]
    assert any(r > 150 and g < 60 and b < 60 for r, g, b in reds)
    # the covered total is gone from view: the line is all background colour
    row = {image.getpixel((x, 842 - 703)) for x in range(72, 200)}
    assert row == {image.getpixel((60, 842 - 703))}
    doc.close()
    # the new title uses the same kind of font, size and colour
    from pypdf import PdfReader as Reader
    fonts = Reader(out).pages[0]["/Resources"]["/Font"]
    assert any(f.get_object()["/BaseFont"] == "/Times-Bold" for f in fonts.values())
    with pytest.raises(core.PdfManagerError, match="page 2 doesn't exist"):
        textedit.edit_text(src, out, [{"page": 2, "rect": [0, 0, 1, 1], "text": "x"}])
    with pytest.raises(core.PdfManagerError, match="no changes"):
        textedit.edit_text(src, out, [])


def test_arrange_endpoint_inserts_and_keeps_the_password(app, tmp_path):
    base, token, _ = app
    src = edit_pdf(tmp_path / "invoice.pdf")
    core.encrypt(src, tmp_path / "locked.pdf", "secret")
    plan = json.dumps([{"file": 1, "page": 1, "rotate": 90}, {"page": 1}])
    body, headers = multipart({"password": "secret", "plan": plan},
                              [("file", tmp_path / "locked.pdf"), ("insert", src)])
    status, _, data = call(base, token, "/api/pages/arrange", body, headers)
    assert status == 200
    reader = PdfReader(io.BytesIO(data))
    assert reader.is_encrypted and reader.decrypt("secret")
    assert len(reader.pages) == 2 and reader.pages[0].rotation == 90
    body, headers = multipart({"password": "secret", "plan": "[]"}, [("file", tmp_path / "locked.pdf")])
    assert call(base, token, "/api/pages/arrange", body, headers)[0] == 400


def test_preview_shows_page_one_of_the_result(app, tmp_path):
    from PIL import Image

    base, token, _ = app
    src = edit_pdf(tmp_path / "invoice.pdf")
    core.encrypt(src, tmp_path / "locked.pdf", "secret")
    plain = multipart({"op": "number", "format": "{n} / {total}", "position": "bottom-center",
                       "start": "1", "font_size": "10"}, [("file", src)])
    status, headers, png = call(base, token, "/api/preview", *plain)
    assert status == 200 and headers["Content-Type"] == "image/png"
    image = Image.open(io.BytesIO(png))
    assert image.width == 960 and image.height > image.width  # an A4 page, upright
    # The watermark shows on it (and a locked file is read with its password).
    body = multipart({"op": "watermark", "text": "PAID", "opacity": "1", "font_size": "80", "angle": "0",
                      "below": "false", "pages": "", "password": "secret"}, [("file", tmp_path / "locked.pdf")])
    status, _, marked = call(base, token, "/api/preview", *body)
    assert status == 200
    before = Image.open(io.BytesIO(png)).convert("L")
    after = Image.open(io.BytesIO(marked)).convert("L")
    middle = (0, before.height // 3, before.width, before.height * 2 // 3)
    assert after.crop(middle).getextrema()[0] < before.crop(middle).getextrema()[0] - 50
    # A tool with nothing to preview, a missing field or a wrong password: a plain error.
    for fields in ({"op": "compress"}, {"op": "watermark", "text": ""}, {"op": "number", "password": "wrong"}):
        status, _, data = call(base, token, "/api/preview", *multipart(fields, [("file", tmp_path / "locked.pdf")]))
        assert status == 400 and json.loads(data)["error"]
    assert {op["id"] for op in json.loads(call(base, token, "/api/operations")[2])["operations"] if op.get("live")} \
        == gui.PREVIEW_OPS


def test_split_uses_ranges_only_when_chosen(tmp_path):
    src = toc_like(tmp_path / "six.pdf", 6)
    work = tmp_path / "work"
    work.mkdir()
    name, data, _ = gui.run_operation("split", [src], {"by": "every", "every": "3", "ranges": "1 2-6"}, work=work)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert len(z.namelist()) == 2  # every 3 pages; the ranges (filled in by the page previews) wait
    work2 = tmp_path / "work2"
    work2.mkdir()
    _, data, _ = gui.run_operation("split", [src], {"by": "ranges", "every": "3", "ranges": "1 2-6"}, work=work2)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert len(z.namelist()) == 2 and any("1" in n for n in z.namelist())
    work3 = tmp_path / "work3"
    work3.mkdir()
    _, data, _ = gui.run_operation("split", [src], {"every": "1", "ranges": "1-2 3-6"}, work=work3)  # older recipes
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert len(z.namelist()) == 2


def toc_like(path, pages):
    c = canvas.Canvas(str(path), pagesize=A4)
    for n in range(pages):
        c.drawString(72, 700, f"Page {n + 1}")
        c.showPage()
    c.save()
    return path


def test_edit_text_endpoint_keeps_the_password(app, tmp_path):
    base, token, _ = app
    src = edit_pdf(tmp_path / "invoice.pdf")
    core.encrypt(src, tmp_path / "locked.pdf", "secret")
    edits = json.dumps([{"page": 1, "rect": [72, 755, 230, 775], "text": "Invoice for May"}])
    body, headers = multipart({"password": "secret", "edits": edits}, [("file", tmp_path / "locked.pdf")])
    status, res_headers, data = call(base, token, "/api/text/edit", body, headers)
    assert status == 200 and res_headers["Content-Type"] == "application/pdf"
    reader = PdfReader(io.BytesIO(data))
    assert reader.is_encrypted and reader.decrypt("secret")
    assert json.loads(res_headers["X-Pdfm-Fonts"]) == [{"font": "Times Bold", "kind": "same"}]
    assert "Invoice for May" in reader.pages[0].extract_text()
    body, headers = multipart({"password": "wrong", "edits": edits}, [("file", tmp_path / "locked.pdf")])
    assert call(base, token, "/api/text/edit", body, headers)[0] == 400


# -- Windows set-up -------------------------------------------------------------------------------

class FakeRegistry:
    """Enough of winreg for winsetup, keeping keys in a dict."""
    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = KEY_READ = KEY_WRITE = 0
    REG_SZ, REG_DWORD = 1, 4

    def __init__(self):
        self.keys = {}

    class _Key:
        def __init__(self, reg, path):
            self.reg, self.path = reg, path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def CreateKeyEx(self, root, path, reserved=0, access=0):
        parts = path.lower().split("\\")
        for n in range(1, len(parts) + 1):  # with the keys above it, like Windows
            self.keys.setdefault("\\".join(parts[:n]), {})
        return self._Key(self, path.lower())

    def OpenKey(self, root, path, reserved=0, access=0):
        if path.lower() not in self.keys:
            raise FileNotFoundError(path)
        return self._Key(self, path.lower())

    def SetValueEx(self, key, name, reserved, kind, value):
        self.keys[key.path][name] = value

    def DeleteValue(self, key, name):
        self.keys[key.path].pop(name, None)

    def EnumKey(self, key, index):
        children = sorted({k[len(key.path) + 1:].split("\\")[0] for k in self.keys
                           if k.startswith(key.path + "\\")})
        if index >= len(children):
            raise OSError("no more")
        return children[index]

    def DeleteKey(self, root, path):
        if any(k.startswith(path.lower() + "\\") for k in self.keys):
            raise PermissionError("key has subkeys")  # as winreg does
        self.keys.pop(path.lower())


def test_an_install_under_the_old_name_gets_the_new_name(tmp_path, monkeypatch):
    import sys
    from pdfmanager import winsetup

    reg = FakeRegistry()
    monkeypatch.setitem(sys.modules, "winreg", reg)
    monkeypatch.setattr(winsetup.sys, "platform", "win32")
    folders = {winsetup.CSIDL_PROGRAMS: tmp_path / "Start", winsetup.CSIDL_DESKTOPDIRECTORY: tmp_path / "Desktop"}
    monkeypatch.setattr(winsetup, "_folder", folders.get)
    monkeypatch.setattr(winsetup, "_notify_shell", lambda: None)
    monkeypatch.setattr(winsetup, "make_shortcut", lambda path, *a, **kw: Path(path).write_text("lnk"))
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    monkeypatch.setattr(winsetup, "default_app_dir", lambda: app_dir)
    assert not winsetup.rename_old_install()  # nothing installed under the old name
    for folder in folders.values():
        folder.mkdir()
    (tmp_path / "Start" / "PDF Manager.lnk").write_text("old")  # no desktop shortcut
    (app_dir / "PDF Manager.ico").write_bytes(b"old")
    reg.keys[r"software\registeredapplications"] = {"PDF Manager": "old"}
    assert winsetup.rename_old_install()
    assert sorted(p.name for p in tmp_path.glob("*/*.lnk")) == ["ZPDF.lnk"]
    assert (tmp_path / "Start" / "ZPDF.lnk").exists()
    assert (app_dir / "ZPDF.ico").exists() and not (app_dir / "PDF Manager.ico").exists()
    assert list(reg.keys[r"software\registeredapplications"]) == ["ZPDF"]
    assert not winsetup.rename_old_install()  # once is enough


def test_windows_setup_registers_and_removes(tmp_path, monkeypatch):
    import sys
    from pdfmanager import winsetup

    reg = FakeRegistry()
    monkeypatch.setitem(sys.modules, "winreg", reg)
    monkeypatch.setattr(winsetup.sys, "platform", "win32")
    folders = {winsetup.CSIDL_PROGRAMS: tmp_path / "Start", winsetup.CSIDL_DESKTOPDIRECTORY: tmp_path / "Desktop"}
    monkeypatch.setattr(winsetup, "_folder", folders.get)
    monkeypatch.setattr(winsetup, "_notify_shell", lambda: None)
    made = []

    def fake_shortcut(path, target, arguments="", **kw):
        made.append((Path(path), Path(target).name, arguments))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("lnk")

    monkeypatch.setattr(winsetup, "make_shortcut", fake_shortcut)
    app_dir = tmp_path / "app"
    done = winsetup.install(app_dir)
    assert len(done) == 4
    assert made[0][0] == tmp_path / "Start" / "ZPDF.lnk"
    assert made[1][0] == tmp_path / "Desktop" / "ZPDF.lnk"
    assert made[0][2] == "-I -m pdfmanager gui"  # -I: the PDF's folder isn't searched for modules
    assert (app_dir / "ZPDF.ico").stat().st_size > 1000
    uninstall = (app_dir / "uninstall.ps1").read_text(encoding="utf-8-sig")
    assert str(app_dir) in uninstall and "setup-windows" not in uninstall  # works without Python
    assert str(tmp_path / "Start" / "ZPDF.lnk") in uninstall
    assert "PDFManager.Document" in uninstall and r"HKCU:\Software\PDFManager" in uninstall
    keys = reg.keys
    command = keys[r"software\classes\pdfmanager.document\shell\open\command"][""]
    assert command.endswith('-I -m pdfmanager gui "%1"')
    assert keys[r"software\classes\.pdf\openwithprogids"] == {"PDFManager.Document": ""}
    assert keys[r"software\registeredapplications"]["ZPDF"] == r"Software\PDFManager\Capabilities"
    uninstall = keys[r"software\microsoft\windows\currentversion\uninstall\pdfmanager"]
    assert uninstall["DisplayName"] == "ZPDF" and "uninstall.ps1" in uninstall["UninstallString"]
    winsetup.remove()
    assert not (tmp_path / "Start" / "ZPDF.lnk").exists()
    assert not (tmp_path / "Desktop" / "ZPDF.lnk").exists()
    assert not any(k.startswith(r"software\classes\pdfmanager") or k.startswith(r"software\pdfmanager")
                   or k.endswith(r"uninstall\pdfmanager") for k in keys)
    assert keys[r"software\classes\.pdf\openwithprogids"] == {}
    assert "ZPDF" not in keys[r"software\registeredapplications"]
    # without a desktop shortcut or Open with
    made.clear()
    reg.keys.clear()
    winsetup.install(app_dir, desktop=False, open_with=False)
    assert [m[0].parent.name for m in made] == ["Start"]
    assert not any("pdfmanager.document" in k for k in reg.keys)


def test_windows_setup_needs_windows(monkeypatch, capsys):
    import sys
    if sys.platform == "win32":
        pytest.skip("this is Windows")
    assert main(["setup-windows"]) == 1
    assert "only works on Windows" in capsys.readouterr().err


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows only")
def test_windows_shortcuts_and_registry_for_real(tmp_path, monkeypatch):
    import winreg
    from pdfmanager import winsetup

    folders = {winsetup.CSIDL_PROGRAMS: tmp_path / "Start", winsetup.CSIDL_DESKTOPDIRECTORY: tmp_path / "Desktop"}
    monkeypatch.setattr(winsetup, "_folder", folders.get)
    # Keys of their own, so an installed ZPDF on this computer is left alone.
    monkeypatch.setattr(winsetup, "NAME", "ZPDF Test")
    monkeypatch.setattr(winsetup, "PROG_ID", "PDFManagerTest.Document")
    monkeypatch.setattr(winsetup, "APP_KEY", r"Software\PDFManagerTest")
    monkeypatch.setattr(winsetup, "UNINSTALL_KEY",
                        r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PDFManagerTest")
    winsetup.install(tmp_path / "app")
    try:
        target, args = winsetup.read_shortcut(tmp_path / "Start" / "ZPDF Test.lnk")
        assert Path(target).name.lower() in ("pythonw.exe", "python.exe") and args == "-I -m pdfmanager gui"
        assert (tmp_path / "Desktop" / "ZPDF Test.lnk").exists()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, winsetup.UNINSTALL_KEY) as key:
            assert winreg.QueryValueEx(key, "DisplayName")[0] == "ZPDF Test"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            rf"{winsetup.CLASSES}\{winsetup.PROG_ID}\shell\open\command") as key:
            assert winreg.QueryValueEx(key, "")[0].endswith('-I -m pdfmanager gui "%1"')
    finally:
        winsetup.remove()
    assert not (tmp_path / "Start" / "ZPDF Test.lnk").exists()
    with pytest.raises(FileNotFoundError):
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, winsetup.UNINSTALL_KEY)


# -- updates --------------------------------------------------------------------------------------

class FakeGitHub:
    """Answers update.py's requests like GitHub's API."""

    def __init__(self, version="9.0.0", status=200, zipball=b""):
        self.version, self.status, self.zipball, self.requests = version, status, zipball, []

    real = staticmethod(urllib.request.urlopen)

    def __call__(self, req, timeout=None):
        import urllib.error

        if not getattr(req, "full_url", str(req)).startswith("https://api.github.com"):
            return self.real(req) if timeout is None else self.real(req, timeout=timeout)
        self.requests.append(req)
        if self.status != 200:
            raise urllib.error.HTTPError(req.full_url, self.status, "Not Found", {}, None)
        url = req.full_url
        if url.endswith("/repos/Tempura-446/PDF-Manager"):
            body = json.dumps({"default_branch": "main"}).encode()
        elif "/contents/pdfmanager/__init__.py?ref=main" in url:
            body = f'"""ZPDF."""\n\n__version__ = "{self.version}"\n'.encode()
        elif url.endswith("/zipball/main"):
            body = self.zipball
        else:
            raise AssertionError(url)
        return io.BytesIO(body)


@pytest.fixture
def updates(tmp_path, monkeypatch):
    from pdfmanager import appdata, update

    monkeypatch.setattr(update, "data_dir", lambda: tmp_path / "appdata")
    github = FakeGitHub()
    monkeypatch.setattr(update.urllib.request, "urlopen", github)
    return update, github


def test_update_check(updates):
    from pdfmanager import __version__

    update, github = updates
    info = update.check()
    assert info["current"] == __version__ and info["latest"] == "9.0.0" and info["available"]
    assert info["error"] is None and info["how"] == "source"  # this test runs from a checkout
    asked = len(github.requests)
    assert update.check()["available"] and len(github.requests) == asked  # remembered for a while
    github.version = __version__
    assert not update.check(force=True)["available"]
    # a private repository without a token
    github.status = 404
    info = update.check(force=True)
    assert not info["available"] and "add a GitHub token in Settings" in info["error"]
    update.save_settings(github_token="ghp_secret")
    assert "the token can't read the repository" in update.check(force=True)["error"]
    req = github.requests[-1]
    assert req.unredirected_hdrs["Authorization"] == "Bearer ghp_secret"
    assert "Authorization" not in req.headers  # not sent on to other servers after a redirect
    # turned off: no requests
    update.save_settings(check_updates=False)
    (update.data_dir() / "update.json").unlink()
    asked = len(github.requests)
    assert update.check()["latest"] is None and len(github.requests) == asked
    assert update.parse_version("0.10.1") > update.parse_version("0.9.9")


def test_update_install(updates, tmp_path, monkeypatch):
    import zipfile

    update, github = updates
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Tempura-446-PDF-Manager-abc123/pyproject.toml", "[project]\n")
        zf.writestr("Tempura-446-PDF-Manager-abc123/pdfmanager/__init__.py", '__version__ = "9.0.0"\n')
    github.zipball = buf.getvalue()
    with pytest.raises(core.PdfManagerError, match="git pull"):
        update.install()  # running from a checkout
    monkeypatch.setattr(update, "how_installed", lambda: "pip")
    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        assert (Path(cmd[-1].replace("[app]", "")) / "pyproject.toml").exists()
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    assert update.install(python="py") == "9.0.0"
    assert ran[0][:6] == ["py", "-I", "-m", "pip", "install", "--upgrade"]
    assert ran[0][-1].split("Tempura-446-PDF-Manager-abc123")[1] in ("", "[app]")
    monkeypatch.setattr(update.subprocess, "run", lambda cmd, **kw: type(
        "Failed", (), {"returncode": 1, "stdout": "", "stderr": "ERROR: no space left"})())
    with pytest.raises(core.PdfManagerError, match="no space left"):
        update.install()
    # a download with paths leading outside its folder is refused
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.txt", "x")
    with pytest.raises(core.PdfManagerError, match="unsafe"):
        update._extract(bad.getvalue(), tmp_path / "x")


def test_settings_and_update_endpoints(app, updates):
    base, token, srv = app
    update, github = updates
    status, _, data = call(base, token, "/api/settings")
    assert status == 200 and json.loads(data) == {"repo": "Tempura-446/PDF-Manager", "token_set": False,
                                                 "check": True, "how": "source"}
    status, _, data = call(base, token, "/api/settings", {"token": "ghp_x", "check": False})
    assert json.loads(data)["token_set"] and "ghp_x" not in data.decode()
    assert update.settings()["github_token"] == "ghp_x" and update.settings()["check_updates"] is False
    assert call(base, token, "/api/settings", {"repo": "not a repo"})[0] == 400
    call(base, token, "/api/settings", {"clear_token": True, "check": True})
    assert "github_token" not in update.settings() and "check_updates" not in update.settings()
    status, _, data = call(base, token, "/api/update/check", {})
    info = json.loads(data)
    assert info["available"] and info["latest"] == "9.0.0" and info["can_restart"] is False
    status, _, data = call(base, token, "/api/update/restart", {})
    assert status == 400 and "open it again" in json.loads(data)["error"]


def test_restart_ends_the_wait():
    class H:
        pages, closed_at, last_seen, quit_requested = {}, None, time.monotonic(), False

    threading.Timer(0.3, lambda: setattr(H, "quit_requested", True)).start()
    started = time.monotonic()
    gui.wait_until_closed(H, poll=0.05)
    assert time.monotonic() - started < 2


# -- launching: proxies, several launches at once, hand-offs --------------------------------------

def test_handing_over_ignores_proxies(app, tmp_path, monkeypatch):
    """The call to the running copy stays on this computer even with a proxy set."""
    base, token, srv = app
    gui._register_instance(srv, "browser")
    opened = []
    monkeypatch.setattr(gui.webbrowser, "open", opened.append)
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9/")  # nothing listens there
    monkeypatch.delenv("no_proxy", raising=False)
    assert gui.forward_to_running_app([text_pdf(tmp_path / "a.pdf")]) is True
    assert len(opened) == 1
    gui._unregister_instance(srv)


def test_launches_at_the_same_time_share_one_copy(tmp_path, monkeypatch):
    """Three PDFs opened together in Explorer start three launches at once;
    only the first becomes the app, the others hand their files to it."""
    monkeypatch.setattr(gui, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(gui, "run_desktop", lambda **kw: False)
    monkeypatch.setattr(gui, "run_app_window", lambda **kw: False)
    started, opened, servers = [], [], []

    def fake_serve(**kw):
        started.append(kw["open_paths"])
        srv, _ = gui.make_server()
        servers.append(srv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        gui._register_instance(srv, "browser")
        time.sleep(0.6)  # "running"
        gui._unregister_instance(srv)

    monkeypatch.setattr(gui, "serve", fake_serve)
    monkeypatch.setattr(gui.webbrowser, "open", opened.append)
    pdfs = [text_pdf(tmp_path / f"{i}.pdf") for i in range(3)]
    threads = [threading.Thread(target=gui.launch, args=([pdf],)) for pdf in pdfs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    for srv in servers:
        srv.shutdown()
        srv.server_close()
    assert len(started) == 1 and len(opened) == 2
    assert gui._startup.fh is None  # the lock is let go


def test_hand_off_to_a_running_browser_waits_for_the_pages():
    """The browser exits at once when it hands the window to a copy already
    running; the app then lives as long as the page keeps pinging."""
    handler = type("H", (), {"last_seen": time.monotonic(), "closed_at": None})
    stop = time.monotonic() + 1.2

    def ping():
        while time.monotonic() < stop:
            handler.last_seen = time.monotonic()
            time.sleep(0.05)

    threading.Thread(target=ping, daemon=True).start()
    t = time.monotonic()

    class ExitedAtOnce:
        def poll(self):
            return 0

    gui.wait_until_closed(handler, ExitedAtOnce(), idle_handed_off=0.3, poll=0.05, handoff=0.5)
    assert 1.4 <= time.monotonic() - t < 3  # pings until 1.2 s, then 0.3 s quiet


def test_only_the_desktop_window_waits_for_events(app):
    base, token, srv = app
    gui._register_instance(srv, "app-window", browser="/opt/edge", profile="p")
    t = time.monotonic()
    status, _, out = call(base, token, "/api/events?after=0")
    assert status == 200 and json.loads(out)["none"] is True and time.monotonic() - t < 2
    gui._register_instance(srv, "desktop")
    t = time.monotonic()
    threading.Timer(0.2, lambda: srv.RequestHandlerClass.events.push({"open": ["x"]})).start()
    data = json.loads(call(base, token, "/api/events?after=0")[2])
    assert data["events"] == [{"open": ["x"]}] and "none" not in data
    gui._unregister_instance(srv)


def test_file_names_with_brackets_are_not_patterns(tmp_path):
    from pdfmanager.cli import _expand

    odd = text_pdf(tmp_path / "Report [12].pdf")
    text_pdf(tmp_path / "Report 1.pdf")
    assert _expand([str(odd)]) == [str(odd)]
    with pytest.raises(core.PdfManagerError, match="no files match"):
        _expand([str(tmp_path / "Invoice [2024].pdf")])
    assert _expand([str(tmp_path / "Report *.pdf")]) == sorted([str(tmp_path / "Report 1.pdf"), str(odd)])


# -- fixes from the review: PDF processing --------------------------------------------------------

def page_text(path, page=0, bounded=False):
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    pg = doc[page]
    tp = pg.get_textpage()
    if bounded:  # only what the crop box shows
        left, bottom, right, top = pg.get_cropbox()
        text = tp.get_text_bounded(left=left, bottom=bottom, right=right, top=top)
    else:
        text = tp.get_text_range()
    doc.close()
    return text


def test_headers_stay_inside_the_crop_box(tmp_path):
    from pypdf import PdfWriter

    src = text_pdf(tmp_path / "bleed.pdf")
    w = PdfWriter(clone_from=src)
    from pypdf.generic import RectangleObject
    w.pages[0].cropbox = RectangleObject([50, 50, 562, 742])  # bleed: viewers show only this
    w.write(src)
    out = tmp_path / "out.pdf"
    core.headers_footers(src, out, {"top_left": "TOPLEFT", "bottom_center": "{bates}"},
                         bates_prefix="ABC")
    shown = page_text(out, bounded=True)
    assert "TOPLEFT" in shown and "ABC000001" in shown
    # ... and on a rotated page with a crop box
    w = PdfWriter(clone_from=src)
    w.pages[0].rotate(270)
    w.write(src)
    core.headers_footers(src, out, {"top_left": "TOPLEFT"})
    assert "TOPLEFT" in page_text(out, bounded=True)


def test_header_text_with_braces_and_other_alphabets(tmp_path):
    src = text_pdf(tmp_path / "Raport_Łódź.pdf")
    out = tmp_path / "out.pdf"
    core.headers_footers(src, out, {"top_left": "Draft } {} {0} {n}", "top_center": "{file}",
                                    "bottom_left": "Страница {n}"})
    text = page_text(out)
    assert "Draft } {} {0} 1" in text
    assert "Raport_Łódź" in text and "Страница 1" in text


def test_bates_numbers_continue_through_a_recipe(tmp_path):
    from pdfmanager import recipes

    a, b = text_pdf(tmp_path / "a.pdf"), text_pdf(tmp_path / "b.pdf")
    steps = [{"op": "headers", "options": {"bottom_right": "{bates}", "bates_prefix": "ABC"}}]
    state = {}
    recipes.run(steps, a, tmp_path / "a_out.pdf", state=state)
    recipes.run(steps, b, tmp_path / "b_out.pdf", state=state)
    assert "ABC000001" in page_text(tmp_path / "a_out.pdf")
    assert "ABC000002" in page_text(tmp_path / "b_out.pdf")
    # the app's recipe tool keeps the state between the files of one run
    work = tmp_path / "work"
    work.mkdir()
    opts = {"steps": json.dumps(steps)}
    name, data, _ = gui.run_operation("recipe", [a, b], opts, work=work)
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        texts = {n: zf.read(n) for n in zf.namelist()}
    found = sorted(re.search(rb"ABC00000(\d)", d).group(1) for d in texts.values())
    assert found == [b"1", b"2"]


def test_form_fields_with_a_parent_loop_dont_hang(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject
    from pdfmanager import forms

    src = text_pdf(tmp_path / "f.pdf")
    forms.add_fields(src, tmp_path / "form.pdf", [{"page": 1, "rect": [72, 700, 300, 720], "name": "a"}])
    w = PdfWriter(clone_from=tmp_path / "form.pdf")
    widget = w.pages[0]["/Annots"][0].get_object()
    widget[NameObject("/Parent")] = w.pages[0]["/Annots"][0]  # points at itself
    w.write(tmp_path / "loop.pdf")
    done = []
    worker = threading.Thread(target=lambda: done.append(forms.existing_fields(tmp_path / "loop.pdf")))
    worker.start()
    worker.join(10)
    assert done and done[0][1][0]["name"] == "a"


def test_attachments_saved_once_and_bare_file_specs_skipped(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject, ArrayObject, FloatObject

    src = text_pdf(tmp_path / "att.pdf")
    w = PdfWriter(clone_from=src)
    w.add_attachment("report.txt", b"the same bytes")
    w.add_attachment("copy of report.txt", b"the same bytes")
    # a file-attachment comment whose file spec is just a name (nothing embedded)
    annot = DictionaryObject({NameObject("/Type"): NameObject("/Annot"),
                              NameObject("/Subtype"): NameObject("/FileAttachment"),
                              NameObject("/Rect"): ArrayObject([FloatObject(10), FloatObject(10), FloatObject(30), FloatObject(30)]),
                              NameObject("/FS"): TextStringObject("other.pdf")})
    w.pages[0][NameObject("/Annots")] = ArrayObject([w._add_object(annot)])
    w.write(src)
    out = tmp_path / "media"
    saved = core.extract_media(src, out, images=False)
    assert len(saved["attachments"]) == 1 and saved["attachments"][0].read_bytes() == b"the same bytes"


def test_app_icon_matches_the_page_and_draws(tmp_path):
    from pdfmanager import appicon
    import urllib.parse
    html = (Path(__file__).resolve().parents[1] / "pdfmanager" / "gui.html").read_text(encoding="utf-8")
    inner = appicon.svg().split(">", 1)[1]  # everything after the opening <svg ...>
    assert inner in html  # the sidebar mark
    assert urllib.parse.quote(appicon.svg(), safe="") in html  # the tab icon
    ico = appicon.save_ico(tmp_path / "z.ico")
    from PIL import Image
    with Image.open(ico) as im:
        assert (256, 256) in im.info["sizes"] or im.size == (256, 256)
        assert im.convert("RGBA").getpixel((128, 128))[3] == 255


def test_drawing_scales_and_measuring_js():
    """static/measure.js: reading a sheet's scale, and lengths and areas in feet and metres."""
    import shutil
    import subprocess
    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("needs Node.js")
    module = (Path(__file__).resolve().parents[1] / "pdfmanager" / "static" / "measure.js").as_uri()
    script = """
const M = await import(process.argv[1]);
const out = {};
const scale = (t) => { const s = M.findScale(t); return s && [s.label, s.ratio, s.units]; };
out.arch = scale(`FLOOR PLAN  SCALE: 1/4" = 1'-0"  DETAIL 3  1" = 1'-0"`);
out.arch2 = scale(`SCALE 1 1/2" = 1'-0"`);
out.words = scale(`Scale 1/8 in = 1 ft`);
out.eng = scale(`SITE PLAN 1" = 20'`);
out.metric = scale(`SITE PLAN  SCALE 1:100`);
out.time = scale(`Meeting at 1:30 pm, ratio 1:2`);
out.none = scale(`NTS`);
const quarter = { ratio: 48, units: "ft" }, hundred = { ratio: 100, units: "m" };
out.len = M.formatLength(72 * 6, quarter);
out.len2 = M.formatLength(72 * 3.125, quarter);
out.paper = M.formatLength(72, null);
out.m = M.formatLength(72 * 100 / 25.4, hundred);
out.mm = M.formatLength(72 * 4.5 / 25.4, hundred);
out.area = M.formatArea(M.polygonArea([[0, 0], [432, 0], [432, 288], [0, 288]]), quarter);
out.perimeter = M.formatLength(M.perimeter([[0, 0], [432, 0], [432, 288], [0, 288]]), quarter);
out.lengths = ["12'-6\\"", "12' 6 1/2\\"", "150\\"", "12.5'", "3.5 m", "3500mm", "12", "abc"].map((t) => {
  const r = M.parseLength(t); return r && [Math.round(r.inches * 100) / 100, r.units]; });
const fromDim = M.scaleFrom(144, M.parseLength("8'"));
out.fromDim = [fromDim.ratio, fromDim.units, fromDim.label];
// Units and rounding: the same 20' 4 5/16" line, and a 30 sq in (on paper) room at 1/4".
const line = 72 * (244.3125 / 48);
out.units = Object.fromEntries(M.UNITS.map(([u]) => [u, M.formatLength(line, quarter, u)]));
out.areaUnits = ["auto", "ft", "in", "m"].map((u) => M.formatArea(72 * 72 * 30, quarter, u));
out.total = M.formatInches(12 * 40, "auto", "ft");
out.path = M.formatLength(M.pathLength([[216, 216], [216, 504], [648, 504], [648, 216]]), quarter);
out.nearest = [M.nearestScale(47.8, "ft"), M.nearestScale(98, "m"), M.nearestScale(250, "ft")].map((n) => [n.scale.label, +n.off.toFixed(4)]);
console.log(JSON.stringify(out));
"""
    out = subprocess.run([node, "--input-type=module", "-e", script, module], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["arch"] == ['1/4" = 1\'-0"', 48, "ft"]  # the one after "SCALE", not the detail's
    assert got["arch2"] == ['1 1/2" = 1\'-0"', 8, "ft"]
    assert got["words"] == ['1/8" = 1\'-0"', 96, "ft"]
    assert got["eng"] == ['1" = 20\'', 240, "ft"]
    assert got["metric"] == ["1:100", 100, "m"]
    assert got["time"] is None and got["none"] is None
    assert got["len"] == "24' 0\"" and got["len2"] == "12' 6\""
    assert got["paper"] == "1.000 in on paper"
    assert got["m"] == "10.000 m" and got["mm"] == "450 mm"
    assert got["area"] == "384.00 sq ft" and got["perimeter"] == "80' 0\""
    assert got["lengths"] == [[150, "ft"], [150.5, "ft"], [150, "ft"], [150, "ft"], [137.8, "m"], [137.8, "m"], [144, "ft"], None]
    assert got["fromDim"] == [48, "ft", 'Set from a dimension (1/4" = 1\'-0")']
    assert got["units"] == {"auto": "20' 4 5/16\"", "ftin16": "20' 4 5/16\"", "ftin8": "20' 4 3/8\"", "ftin4": "20' 4 1/4\"",
                            "ftin2": "20' 4 1/2\"", "ftin1": "20' 4\"", "ft": "20.36 ft", "in": "244 5/16\"",
                            "m": "6.206 m", "mm": "6206 mm"}
    assert got["areaUnits"] == ["480.00 sq ft", "480.00 sq ft", "69120.0 sq in", "44.593 m²"]
    assert got["total"] == "40' 0\""
    assert got["path"] == "56' 0\""  # three walls of the 24' x 16' room, open
    assert got["nearest"] == [['1/4" = 1\'-0"', 0.0042], ["1:100", 0.02], ['1" = 20\'', 0.0417]]


def test_snapping_to_the_drawing_js():
    """static/snap.js: the lines of a page from a pdf.js operator list, and snapping to them."""
    import shutil
    import subprocess
    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("needs Node.js")
    module = (Path(__file__).resolve().parents[1] / "pdfmanager" / "static" / "snap.js").as_uri()
    script = """
const S = await import(process.argv[1]);
const OPS = { save: 10, restore: 11, transform: 12, stroke: 20, closeStroke: 21, fill: 22, eoFill: 23, fillStroke: 24,
  eoFillStroke: 25, closeFillStroke: 26, closeEOFillStroke: 27, endPath: 28, paintFormXObjectBegin: 74, paintFormXObjectEnd: 75, constructPath: 91 };
const path = (...a) => [new Float32Array(a)];
const ol = { fnArray: [12, 91, 91, 10, 12, 91, 11, 74, 91, 75, 91, 91], argsArray: [
  [1, 0, 0, 1, 0, 0],
  [20, path(0, 216, 216, 1, 648, 216, 1, 648, 504, 1, 216, 504, 4), null],  // a closed rectangle, stroked
  [28, path(0, 0, 0, 1, 10, 10), null],                                      // a clipping path: not a line
  null, [2, 0, 0, 2, 100, 100], [22, path(0, 0, 0, 1, 50, 50), null], null,  // filled, under a transform
  [[1, 0, 0, 1, 300, 300], [0, 0, 10, 10]], [20, path(0, 0, 0, 1, 0, 100), null], null,  // inside a form XObject
  [20, path(0, 216, 216, 2, 300, 216, 400, 504, 648, 504), null],            // a curve
  [20, path(0, 200, 360, 1, 700, 360), null],                                // a line right across the rectangle
]};
const g = S.fromOperatorList(ol, OPS);
const r = (h) => h && [h.kind, +h.x.toFixed(6), +h.y.toFixed(6)];
const out = {
  count: g.count,
  rect: [...g.segments.slice(0, 16)],
  transformed: [...g.segments.slice(16, 20)],
  form: [...g.segments.slice(20, 24)],
  end: r(g.snap([650, 506], 8)),
  cross: r(g.snap([647, 362], 6)),
  mid: r(g.snap([432, 218], 6)),
  perp: r(g.snap([500, 215], 6, { from: [500, 300] })),
  on: r(g.snap([520, 217], 6)),
  ortho: r(g.snap([647, 300], 6, { from: [216, 300], ortho: 0 })),
  none: g.snap([900, 900], 6),
  nearestEndWins: r(g.snap([217, 217], 8)),
};
// Fallbacks: built a chunk at a time; a pdf.js we don't know; bad numbers; too many lines.
const built = await S.Geometry.build(S.segmentsFromOperatorList(ol, OPS), { yieldWith: (fn) => fn() });
out.builtSame = built.count === g.count && r(built.snap([650, 506], 8))[0] === "end";
out.unknownOps = S.fromOperatorList(ol, {}).count;
out.noList = S.fromOperatorList({}, OPS).count;
out.nan = S.fromOperatorList({ fnArray: [91], argsArray: [[20, path(0, 0, 0, 1, NaN, 5, 1, 10, 10), null]] }, OPS).count;
const unindexed = new S.Geometry(S.segmentsFromOperatorList(ol, OPS), { index: false });
out.beforeIndexed = unindexed.snap([650, 506], 8);
out.badTol = g.snap([650, 506], 0) === null && g.snap([NaN, 1], 8) === null;
// Everything there is to snap to near a corner, best first, each spot once (with all its lines).
const c = g.candidates([217, 217], 8);
out.cands = c.map((h) => [h.kind, Math.round(h.x), Math.round(h.y)]);
out.spots = new Set(c.map((h) => `${h.x},${h.y}`)).size === c.length;
out.cornerLines = c[0].segs.length;
out.candsFirstIsSnap = r(g.snap([217, 217], 8))[1] === Math.round(c[0].x);
out.seg = g.segment(0);
console.log(JSON.stringify(out));
"""
    out = subprocess.run([node, "--input-type=module", "-e", script, module], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["count"] == 4 + 1 + 1 + 16 + 1  # the curve becomes 16 pieces
    assert got["rect"] == [216, 216, 648, 216, 648, 216, 648, 504, 648, 504, 216, 504, 216, 504, 216, 216]
    assert got["transformed"] == [100, 100, 200, 200]
    assert got["form"] == [300, 300, 300, 400]
    assert got["end"] == ["end", 648, 504]
    assert got["cross"] == ["cross", 648, 360]
    assert got["mid"] == ["mid", 432, 216]
    assert got["perp"] == ["perp", 500, 216]
    assert got["on"] == ["on", 520, 216]
    assert got["ortho"] == ["cross", 648, 300]
    assert got["none"] is None
    assert got["nearestEndWins"] == ["end", 216, 216]
    assert got["builtSame"] is True
    assert got["unknownOps"] == 0 and got["noList"] == 0
    assert got["nan"] == 1  # the segment with a NaN end is left out, the good one kept
    assert got["beforeIndexed"] is None and got["badTol"] is True
    assert got["cands"][0] == ["end", 216, 216] and got["candsFirstIsSnap"] is True
    assert got["cornerLines"] == 3  # the rectangle's two sides and the curve meet there
    assert got["spots"] is True and len(got["cands"]) >= 3
    assert ["on", 217, 216] in got["cands"] and ["on", 216, 217] in got["cands"]
    assert got["seg"] == [216, 216, 648, 216]


def test_printing_puts_each_page_on_one_sheet():
    """The reader prints each page as a picture: one sheet each, fitted
    inside it, with breaks between pages only. (A picture at the full width,
    a break after every page, printed a blank sheet after each page.)"""
    root = Path(__file__).resolve().parents[1] / "pdfmanager" / "static"
    css = (root / "reader.css").read_text(encoding="utf-8")
    js = (root / "reader.js").read_text(encoding="utf-8")
    rules = css[css.index("@media print"):]
    rules = rules[:rules.index("\n}\n")]
    assert ".rd-print-page { width: 100vw; height: 100vh;" in rules
    assert ".rd-print-page + .rd-print-page { break-before: page;" in rules
    assert "max-width: 100%; max-height: 100%" in rules and "object-fit: contain" in rules
    assert "break-after: page" not in rules
    assert 'el("div", { class: "rd-print-page" }, el("img"' in js


# -- 0.8.2: faster writing and reading of pictures, safer blank pages ------------

def test_faded_or_pencil_text_is_not_a_blank_page(tmp_path):
    """At the quick 40 dpi look, small faded text blurs to light grey; pages
    that look blank are checked again closer before they are removed.
    Show-through from the other side of the paper still counts as blank."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    from pdfmanager import scan

    def page(fill, mirrored=False):
        im = Image.new("L", (1240, 1754), 245)
        draw, font = ImageDraw.Draw(im), ImageFont.load_default(30)
        for k in range(30):
            draw.text((80, 100 + k * 50), "The quick brown fox jumps over the lazy dog.", fill=fill, font=font)
        return im.transpose(Image.FLIP_LEFT_RIGHT).filter(ImageFilter.GaussianBlur(1.5)) if mirrored else im

    pictures = [page(195, mirrored=True), page(150), page(120), Image.new("L", (1240, 1754), 240)]
    for k, im in enumerate(pictures):
        im.save(tmp_path / f"p{k}.png")
    src = core.images_to_pdf([tmp_path / f"p{k}.png" for k in range(4)], tmp_path / "scan.pdf")
    for sensitivity in ("low", "medium", "high"):
        assert scan.find_blank_pages(src, sensitivity=sensitivity) == [0, 3]
    # Faded print in small type, drawn as text.
    c = canvas.Canvas(str(tmp_path / "faded.pdf"), pagesize=A4)
    c.setFillGray(0.45)
    c.setFont("Helvetica", 10)
    for i in range(45):
        c.drawString(60, 780 - i * 13, "The quick brown fox jumps over the lazy dog, 1234567890.")
    c.showPage()
    c.save()
    assert scan.find_blank_pages(tmp_path / "faded.pdf", sensitivity="low") == []


def test_jpeg_photos_go_into_the_pdf_as_they_are(tmp_path):
    from PIL import Image

    photo = Image.new("RGB", (400, 300), (240, 240, 240))
    photo.paste((220, 20, 20), (0, 0, 120, 90))  # red at the top left when upright
    for orientation, stored in ((1, photo), (3, photo.rotate(180)), (6, photo.rotate(90, expand=True)),
                                (8, photo.rotate(-90, expand=True))):
        exif = Image.Exif()
        exif[0x0112] = orientation
        stored.save(tmp_path / f"o{orientation}.jpg", quality=95, exif=exif, dpi=(72, 72))
        out = core.images_to_pdf([tmp_path / f"o{orientation}.jpg"], tmp_path / f"o{orientation}.pdf")
        page = PdfReader(out).pages[0]
        assert [round(float(v)) for v in page.mediabox[2:]] == [400, 300]
        images = page["/Resources"]["/XObject"]
        assert [images[k].get_object()["/Filter"] for k in images] == [["/DCTDecode"]]
        assert (tmp_path / f"o{orientation}.pdf").stat().st_size < (tmp_path / f"o{orientation}.jpg").stat().st_size + 3000
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(out))
        shown = doc[0].render(scale=1).to_pil().convert("RGB")
        doc.close()
        assert shown.getpixel((40, 30))[0] > 180 and shown.getpixel((40, 30))[1] < 80, orientation
        assert shown.getpixel((360, 270)) > (200, 200, 200), orientation


def test_written_pictures_are_binary_and_ascii85_reads_fast(tmp_path):
    """ZPDF's own pages (redacted, compared, from pictures) are not wrapped
    in ASCII85; files that are still read correctly through pypdf."""
    import base64

    from pypdf.filters import ASCII85Decode
    from reportlab import rl_config

    from pdfmanager import redact

    src = text_pdf(tmp_path / "doc.pdf", ["Call 555-1234 today"])
    assert redact.redact(src, tmp_path / "red.pdf", terms=["555-1234"]) == {1: 1}
    assert b"/ASCII85Decode" not in (tmp_path / "red.pdf").read_bytes()
    raw = bytes(range(256)) * 50 + b"\0" * 40 + b"end"
    assert ASCII85Decode.decode(base64.a85encode(raw, adobe=True, wrapcol=75)) == raw
    assert ASCII85Decode.decode(base64.a85encode(raw).decode() + "~>") == raw
    # A picture written the old way still comes out the same.
    from PIL import Image

    Image.new("RGB", (64, 48), (10, 120, 220)).save(tmp_path / "pic.png")
    old = rl_config.useA85
    rl_config.useA85 = 1
    try:
        c = canvas.Canvas(str(tmp_path / "a85.pdf"))
        c.drawImage(str(tmp_path / "pic.png"), 0, 0, 64, 48)
        c.showPage()
        c.save()
    finally:
        rl_config.useA85 = old
    assert b"/ASCII85Decode" in (tmp_path / "a85.pdf").read_bytes()
    saved = core.extract_media(tmp_path / "a85.pdf", tmp_path / "media")
    assert len(saved["images"]) == 1
    with Image.open(saved["images"][0]) as im:
        assert im.convert("RGB").getpixel((5, 5)) == (10, 120, 220)
