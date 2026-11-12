"""Tests for the server findings of the audit: names outside latin-1 in
response headers, results kept until sent, time asleep not counting as
idle, passwords protecting only what was protected, work folders of jobs
cut short, restarting under a plain ``pdfm gui``, and bounded caches."""

import io
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from pdfmanager import core, gui, progress


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
        with urllib.request.urlopen(request, timeout=20) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def multipart(fields, files):
    """``files`` are (field, file name, bytes); names may be any unicode."""
    boundary = "pdfmtestboundary"
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    for name, filename, data in files:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                     f'filename="{filename}"\r\nContent-Type: application/pdf\r\n\r\n'.encode("utf-8")
                     + data + b"\r\n")
    return (b"".join(parts) + f"--{boundary}--\r\n".encode(),
            {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def run_job(base, token, path, data, headers):
    """Run a tool as a job and return its id once it has finished."""
    status, _, out = call(base, token, path, data, {**headers, "X-Pdfm-Async": "1"})
    assert status == 200, out
    job = json.loads(out)["job"]
    for _ in range(200):
        state = json.loads(call(base, token, f"/api/job/{job}")[2])["state"]
        if state in ("done", "failed", "cancelled"):
            break
        time.sleep(0.05)
    assert state == "done"
    return job


# -- names outside latin-1 ----------------------------------------------------------------

NAMES = ["John’s report.pdf", "Résumé – final.pdf", "Отчёт.pdf"]


@pytest.mark.parametrize("filename", NAMES)
def test_result_names_outside_latin1_are_sent(app, tmp_path, filename):
    base, token, _ = app
    pdf = text_pdf(tmp_path / "a.pdf").read_bytes()
    body, headers = multipart({"op": "rotate", "degrees": "90"}, [("files", filename, pdf)])
    status, res_headers, data = call(base, token, "/api/run", body, headers)
    assert status == 200, data
    expected = Path(filename).stem + "_rotated.pdf"
    assert urllib.parse.unquote(res_headers["X-Pdfm-Filename"]) == expected
    disposition = res_headers["Content-Disposition"]
    assert f"filename*=UTF-8''{urllib.parse.quote(expected, safe='')}" in disposition
    # The plain stand-in is ASCII and still a usable file name.
    plain = disposition.split('filename="')[1].split('"')[0]
    plain.encode("ascii")
    assert plain.endswith("_rotated.pdf") and not plain.startswith(".")
    assert data.startswith(b"%PDF") and len(PdfReader(io.BytesIO(data)).pages) == 1


def test_plain_names_are_unchanged_in_the_header(app, tmp_path):
    base, token, _ = app
    pdf = text_pdf(tmp_path / "a.pdf").read_bytes()
    body, headers = multipart({"op": "rotate", "degrees": "90"}, [("files", "plain.pdf", pdf)])
    status, res_headers, _ = call(base, token, "/api/run", body, headers)
    assert status == 200 and res_headers["X-Pdfm-Filename"] == "plain_rotated.pdf"
    assert res_headers["Content-Disposition"].startswith('attachment; filename="plain_rotated.pdf"')


def test_job_result_with_such_a_name_is_delivered_and_then_forgotten(app, tmp_path):
    base, token, _ = app
    pdf = text_pdf(tmp_path / "a.pdf").read_bytes()
    body, headers = multipart({"op": "rotate", "degrees": "90"},
                              [("files", "Résumé – final.pdf", pdf)])
    job = run_job(base, token, "/api/run", body, headers)
    status, res_headers, data = call(base, token, f"/api/job/{job}/result")
    assert status == 200 and data.startswith(b"%PDF")
    assert urllib.parse.unquote(res_headers["X-Pdfm-Filename"]) == "Résumé – final_rotated.pdf"
    status, _, out = call(base, token, f"/api/job/{job}/result")
    assert status == 404 and "no longer available" in json.loads(out)["error"]


def test_job_result_is_kept_when_sending_it_fails(app, tmp_path, monkeypatch):
    base, token, srv = app
    pdf = text_pdf(tmp_path / "a.pdf").read_bytes()
    body, headers = multipart({"op": "rotate", "degrees": "90"}, [("files", "a.pdf", pdf)])
    job = run_job(base, token, "/api/run", body, headers)
    handler = srv.RequestHandlerClass
    real = handler._send_result

    def cut_off(self, result):  # the window went away mid-transfer
        raise BrokenPipeError("gone")

    monkeypatch.setattr(handler, "_send_result", cut_off)
    with pytest.raises((urllib.error.URLError, ConnectionError)):
        call(base, token, f"/api/job/{job}/result")
    monkeypatch.setattr(handler, "_send_result", real)
    status, _, data = call(base, token, f"/api/job/{job}/result")
    assert status == 200 and data.startswith(b"%PDF")


def test_reader_opens_files_under_folders_outside_latin1(app, tmp_path):
    base, token, srv = app
    folder = tmp_path / "Документы – 2024"
    folder.mkdir()
    path = text_pdf(folder / "Отчёт.pdf")
    key = srv.RequestHandlerClass.opened.add(path)
    status, res_headers, data = call(base, token, f"/api/file/{key}")
    assert status == 200 and data.startswith(b"%PDF")
    assert urllib.parse.unquote(res_headers["X-Pdfm-Filename"]) == "Отчёт.pdf"
    assert urllib.parse.unquote(res_headers["X-Pdfm-Path"]) == str(path.resolve())


def test_an_unsendable_header_fails_before_the_status_line(app, tmp_path, monkeypatch):
    """A header value that can't be encoded is caught before anything is
    written, so the client sees one clean error, not a 200 with a 400
    inside its body."""
    base, token, _ = app
    monkeypatch.setattr(gui, "_download_headers",
                        lambda name: {"X-Pdfm-Filename": "Отчёт.pdf"})
    pdf = text_pdf(tmp_path / "a.pdf").read_bytes()
    body, headers = multipart({"op": "rotate", "degrees": "90"}, [("files", "a.pdf", pdf)])
    status, _, data = call(base, token, "/api/run", body, headers)
    error = json.loads(data)["error"]
    assert status == 500 and "Something went wrong (error " in error
    # the details are in the log, under the error's id
    from pdfmanager import applog

    error_id = error.split("(error ", 1)[1][:4]
    logged = (applog.log_dir() / "zpdf.log").read_text(encoding="utf-8")
    assert error_id in logged and "X-Pdfm-Filename" in logged


def test_ascii_stand_in_for_a_name_with_nothing_ascii_in_it():
    headers = gui._download_headers("Отчёт.pdf")
    assert headers["Content-Disposition"].startswith('attachment; filename="file.pdf"')
    assert gui._download_headers("John’s report.pdf")["Content-Disposition"].startswith(
        'attachment; filename="Johns report.pdf"')


# -- time asleep ----------------------------------------------------------------------------

class _FakeTime:
    """A clock that only moves when the wait sleeps; ``jumps`` maps a poll
    number to extra seconds the clock leaps by during that sleep (the
    computer was asleep), and ``on_poll`` lets a test act as the page."""

    def __init__(self, jumps=None, on_poll=None):
        self.now, self.polls, self.jumps, self.on_poll = 1000.0, 0, jumps or {}, on_poll

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.polls += 1
        self.now += seconds + self.jumps.get(self.polls, 0)
        if self.on_poll:
            self.on_poll(self)

    def __getattr__(self, name):
        return getattr(time, name)


class _Browser:
    def __init__(self, exit_on_poll=None):
        self.exit_on_poll = exit_on_poll
        self.clock = None

    def poll(self):
        done = self.exit_on_poll is not None and self.clock.polls >= self.exit_on_poll
        return 0 if done else None


def test_waking_from_sleep_does_not_close_the_window(monkeypatch):
    """The clock leaps 20 minutes during one poll while the window is still
    open; the page then pings as usual and the app lives on until the
    browser really exits."""
    browser = _Browser(exit_on_poll=40)

    def page(clock):
        if clock.polls == 12:  # the page's timer runs again after waking
            handler.last_seen = handler.pages["p1"] = clock.now

    clock = _FakeTime(jumps={10: 1200}, on_poll=page)
    browser.clock = clock
    monkeypatch.setattr(gui, "time", clock)
    handler = type("H", (), {"last_seen": clock.now, "closed_at": None, "pages": {"p1": clock.now},
                             "quit_requested": False})
    gui.wait_until_closed(handler, browser, poll=0.5)
    assert clock.polls == 40 and "p1" in handler.pages


def test_after_waking_a_page_that_never_returns_still_counts_as_gone(monkeypatch):
    clock = _FakeTime(jumps={10: 1200})
    monkeypatch.setattr(gui, "time", clock)
    handler = type("H", (), {"last_seen": clock.now, "closed_at": None, "pages": {"p1": clock.now},
                             "quit_requested": False})
    gui.wait_until_closed(handler, idle=300, poll=0.5)
    # Closed once the page had been quiet for the idle time while the
    # computer was awake (5 s before the sleep and the rest after it), not
    # the moment it woke.
    awake = clock.now - 1000.0 - 1200
    assert 300 < awake <= 302


# -- passwords ------------------------------------------------------------------------------

def test_password_protects_only_results_of_protected_inputs(tmp_path):
    plain = text_pdf(tmp_path / "plain.pdf")
    locked = core.encrypt(text_pdf(tmp_path / "l.pdf"), tmp_path / "locked.pdf", "secret")

    def encrypted(data):
        return PdfReader(io.BytesIO(data)).is_encrypted

    work = tmp_path / "w1"
    work.mkdir()
    _, data, _ = gui.run_operation("rotate", [plain], {"degrees": "90"}, password="secret", work=work)
    assert not encrypted(data)
    work = tmp_path / "w2"
    work.mkdir()
    _, data, _ = gui.run_operation("number", [locked, plain], {}, password="secret", work=work)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert encrypted(zf.read("locked_numbered.pdf"))
        assert not encrypted(zf.read("plain_numbered.pdf"))
    # One result from several inputs: protected if any of them was.
    work = tmp_path / "w3"
    work.mkdir()
    _, data, _ = gui.run_operation("merge", [plain, locked], {}, password="secret", work=work)
    assert encrypted(data)
    work = tmp_path / "w4"
    work.mkdir()
    _, data, _ = gui.run_operation("merge", [plain, plain], {}, password="secret", work=work)
    assert not encrypted(data)


# -- work folders of jobs cut short ------------------------------------------------------

def _server():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    return srv, base, token


def _start_slow_job(monkeypatch, srv, base, token, tmp_path, body_of):
    monkeypatch.setattr(gui, "run_operation", body_of)
    pdf = text_pdf(tmp_path / "a.pdf").read_bytes()
    body, headers = multipart({"op": "rotate", "degrees": "90"}, [("files", "a.pdf", pdf)])
    status, _, out = call(base, token, "/api/run", body, {**headers, "X-Pdfm-Async": "1"})
    assert status == 200, out
    job = srv.RequestHandlerClass.jobs.get(json.loads(out)["job"])
    for _ in range(200):  # until the job has made its folder and written the upload
        folders = list(srv.RequestHandlerClass.work.items)
        if folders and any(folders[0].rglob("a.pdf")):
            break
        time.sleep(0.02)
    (folder,) = folders
    assert folder.is_dir() and any(folder.rglob("a.pdf"))
    return job, folder


def test_closing_the_app_cancels_jobs_and_removes_their_work_folders(tmp_path, monkeypatch):
    def long_op(*args, **kwargs):
        while True:  # reports progress, so it notices being cancelled
            progress.report(1, 10)
            time.sleep(0.02)

    srv, base, token = _server()
    job, folder = _start_slow_job(monkeypatch, srv, base, token, tmp_path, long_op)
    srv.shutdown()
    srv.server_close()
    assert job.state == "cancelled"
    assert not folder.exists() and not srv.RequestHandlerClass.work.items


def test_a_job_stuck_in_a_long_step_loses_its_work_folder_too(tmp_path, monkeypatch):
    release = threading.Event()

    def stuck_op(*args, **kwargs):
        release.wait(10)  # never looks at the cancel flag
        raise core.PdfManagerError("stopped")

    srv, base, token = _server()
    job, folder = _start_slow_job(monkeypatch, srv, base, token, tmp_path, stuck_op)
    srv.shutdown()
    handler = srv.RequestHandlerClass
    started = time.monotonic()
    handler.jobs.stop(timeout=0.3)  # what server_close does, with a short wait
    assert job.cancelled and job.state == "running" and time.monotonic() - started < 2
    handler.work.clear()
    assert not folder.exists()
    release.set()
    job.thread.join(5)
    srv.server_close()


# -- restarting under a plain `pdfm gui` ------------------------------------------------

def test_restart_under_plain_serve_stops_the_old_copy(monkeypatch, capsys):
    made = []
    real = gui.make_server

    def make_server(*args, **kwargs):
        made.append(real(*args, **kwargs))
        return made[-1]

    spawned = []
    monkeypatch.setattr(gui, "make_server", make_server)
    monkeypatch.setattr(gui.update, "restart_after", spawned.append)
    thread = threading.Thread(target=gui.serve, kwargs={"port": 0, "open_browser": False}, daemon=True)
    thread.start()
    for _ in range(100):
        if made:
            break
        time.sleep(0.02)
    srv, url = made[0]
    base, token = url.split("/#token=")
    assert json.loads(call(base, token, "/api/update")[2])["can_restart"] is True
    status, _, out = call(base, token, "/api/update/restart", {})
    assert status == 200 and json.loads(out) == {"restarting": True} and spawned
    thread.join(5)
    assert not thread.is_alive()
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(base + "/", timeout=2)


# -- bounded caches and the upload limit -------------------------------------------------

def test_result_cache_is_bounded_by_bytes():
    cache = gui.ResultCache(keep=5, max_bytes=100)
    keys = [cache.add(f"{i}.pdf", b"x" * 60) for i in range(3)]
    assert [cache.get(k) is not None for k in keys] == [False, False, True]
    big = cache.add("big.pdf", b"y" * 500)  # larger than the whole budget: still kept
    assert cache.get(big) is not None and cache.get(keys[2]) is None


def test_uncollected_job_results_expire_on_their_own():
    store = progress.JobStore(keep_seconds=0.05)
    job = progress.Job()
    job.run(lambda: b"result")
    store.add(job)
    assert store.get(job.id) is job
    time.sleep(0.1)
    assert store.get(job.id) is None  # gone without another job being added


def test_upload_limit_is_realistic_and_the_message_matches(app):
    base, token, _ = app
    # Uploads are held once in memory (the parser hands out views of the
    # body, see below), so 1 GB is safe on any computer that runs the app.
    assert gui.MAX_UPLOAD <= 1024 * 1024 * 1024
    status, _, out = call(base, token, "/api/ping", b"", {"Content-Length": str(gui.MAX_UPLOAD + 1)})
    assert status == 413
    assert json.loads(out)["error"] == f"upload too large ({gui.MAX_UPLOAD // 2**20} MB max)"


def test_multipart_uploads_are_split_without_copies():
    body, headers = multipart({"op": "compress", "note": "a\r\nb"},
                              [("files", "Отчёт “2026”.pdf", b"%PDF-1.4 x\r\n--pdfmtestboundar"),
                               ("files", "b.pdf", b"")])
    fields, files = gui._parse_multipart(headers["Content-Type"], body)
    assert fields == {"op": "compress", "note": "a\r\nb"}
    assert [(n, f, bytes(d)) for n, f, d in files] == [
        ("files", "Отчёт “2026”.pdf", b"%PDF-1.4 x\r\n--pdfmtestboundar"), ("files", "b.pdf", b"")]
    assert all(isinstance(d, memoryview) and d.obj is body for _, _, d in files)
    with pytest.raises(gui.UserError, match="cut short"):
        gui._parse_multipart(headers["Content-Type"], body[:-40])
    with pytest.raises(gui.UserError, match="form upload"):
        gui._parse_multipart("application/json", body)


def test_opened_files_are_read_in_pieces_and_saved_in_place(app, tmp_path):
    base, token, srv = app
    src = text_pdf(tmp_path / "Plan – A.pdf", ["one", "two"])
    original = src.read_bytes()
    key = srv.RequestHandlerClass.opened.add(src)
    # Whole file, then pieces (the reader reads big files a piece at a time).
    status, headers, body = call(base, token, f"/api/file/{key}")
    assert status == 200 and body == original and headers["Accept-Ranges"] == "bytes"
    stamp = headers["X-Pdfm-Stamp"]
    status, headers, body = call(base, token, f"/api/file/{key}", headers={"Range": "bytes=0-9"})
    assert status == 206 and body == original[:10]
    assert headers["Content-Range"] == f"bytes 0-9/{len(original)}"
    status, _, body = call(base, token, f"/api/file/{key}", headers={"Range": "bytes=-5"})
    assert status == 206 and body == original[-5:]
    status, headers, _ = call(base, token, f"/api/file/{key}", headers={"Range": f"bytes={len(original)}-"})
    assert status == 416 and headers["Content-Range"] == f"bytes */{len(original)}"
    # Save in place: the file takes the new bytes, nothing is left beside it.
    changed = text_pdf(tmp_path / "other.pdf", ["changed"]).read_bytes()
    status, _, out = call(base, token, f"/api/save/{key}", changed,
                          {"Content-Type": "application/pdf", "X-Pdfm-Stamp": stamp})
    assert status == 200 and src.read_bytes() == changed
    new_stamp = json.loads(out)["stamp"]
    assert new_stamp != stamp and sorted(p.name for p in tmp_path.iterdir()
                                         if p.suffix != "") == ["Plan – A.pdf", "other.pdf"]
    # Another program changed the file since: refused unless forced.
    status, _, out = call(base, token, f"/api/save/{key}", original,
                          {"Content-Type": "application/pdf", "X-Pdfm-Stamp": stamp})
    assert status == 409 and json.loads(out)["changed"] and src.read_bytes() == changed
    status, _, _ = call(base, token, f"/api/save/{key}", original,
                        {"Content-Type": "application/pdf", "X-Pdfm-Stamp": stamp, "X-Pdfm-Force": "1"})
    assert status == 200 and src.read_bytes() == original
    # Only files the person opened, and only PDFs.
    status, _, out = call(base, token, "/api/save/nope", original, {"Content-Type": "application/pdf"})
    assert status == 400 and "Save as" in json.loads(out)["error"]
    status, _, out = call(base, token, f"/api/save/{key}", b"hello", {"Content-Type": "application/pdf"})
    assert status == 400 and src.read_bytes() == original
    status, _, _ = call(base, "wrong", f"/api/save/{key}", original, {"Content-Type": "application/pdf"})
    assert status == 403


def test_save_as_starts_next_to_the_opened_file(tmp_path, monkeypatch):
    import sys
    import types

    seen = {}
    fake = types.SimpleNamespace(FileDialog=types.SimpleNamespace(SAVE="save"))
    monkeypatch.setitem(sys.modules, "webview", fake)
    folder = tmp_path / "drawings"
    folder.mkdir()
    src = text_pdf(folder / "a.pdf")
    results, opened = gui.ResultCache(), gui.OpenFiles()
    api = gui.DesktopApi(results, opened)

    def dialog(kind, directory, save_filename, file_types=()):
        seen["directory"], seen["types"] = directory, file_types
        return str(folder / save_filename)

    api._window = types.SimpleNamespace(create_file_dialog=dialog)
    key = results.add("a (edited).pdf", b"%PDF-1.4 x")
    assert api.save_result(key, "a (edited).pdf", opened.add(src)) == str(folder / "a (edited).pdf")
    assert seen["directory"] == str(folder)
    assert seen["types"][0] == "PDF files (*.pdf)", "Windows offers PDF files first, not All files"
    key = results.add("b.pdf", b"%PDF-1.4 y")
    api.save_result(key, "b.pdf")
    assert seen["directory"] == str(Path.home())


def test_a_reader_that_went_away_leaves_no_traceback(app, tmp_path, monkeypatch, capsys):
    base, token, srv = app
    pdf = text_pdf(tmp_path / "a.pdf").read_bytes()
    body, headers = multipart({"op": "rotate", "degrees": "90"}, [("files", "a.pdf", pdf)])
    job = run_job(base, token, "/api/run", body, headers)
    handler = srv.RequestHandlerClass

    def cut_off(self, result):
        raise BrokenPipeError("gone")

    monkeypatch.setattr(handler, "_send_result", cut_off)
    with pytest.raises((urllib.error.URLError, ConnectionError)):
        call(base, token, f"/api/job/{job}/result")
    assert "Traceback" not in capsys.readouterr().err

    def broken(self, result):
        raise ValueError("a real fault")

    monkeypatch.setattr(handler, "_send_result", broken)
    with pytest.raises((urllib.error.URLError, ConnectionError)):
        call(base, token, f"/api/job/{job}/result")
    assert "ValueError: a real fault" in capsys.readouterr().err, "real faults are still reported"
