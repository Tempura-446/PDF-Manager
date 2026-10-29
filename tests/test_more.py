"""Tests for progress and cancelling, recent files, single instance, headers
and footers, extracting pictures and attachments, folder search, form
fields, text edits, Windows set-up and updates."""

import json
import threading
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
    raised = []
    srv.RequestHandlerClass.bring_to_front = lambda: raised.append(1)
    gui._register_instance(srv, "desktop")
    # Its own window: the app opens the files itself and comes to the front.
    assert gui.forward_to_running_app([pdf]) is True
    events = json.loads(call(base, token, "/api/events?after=0")[2])
    ids = events["events"][0]["open"]
    assert raised == [1] and len(ids) == 1
    assert call(base, token, f"/api/file/{ids[0]}")[2].startswith(b"%PDF")
    # Edge window: the new launch opens a window on the running app.
    launched = []
    monkeypatch.setattr(gui.subprocess, "Popen", lambda args, **kw: launched.append(args))
    gui._register_instance(srv, "app-window", browser="/opt/edge", profile=tmp_path / "window")
    assert gui.forward_to_running_app([pdf]) is True
    assert launched[0][0] == "/opt/edge" and launched[0][1].startswith("--app=http://127.0.0.1:")
    assert f"#token={token}&open=" in launched[0][1]
    assert launched[0][2] == f"--user-data-dir={tmp_path / 'window'}"
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
