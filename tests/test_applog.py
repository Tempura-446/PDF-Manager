# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""The app's log, error ids and the diagnostics a friend can send."""
import json
import re
import threading
from pathlib import Path

import pytest

from pdfmanager import applog, progress


@pytest.fixture
def logs(tmp_path, monkeypatch):
    monkeypatch.setattr(applog, "data_dir", lambda: tmp_path / "data")
    applog.setup()
    yield tmp_path / "data" / "logs"
    applog.shutdown()


def read(folder):
    return (folder / "zpdf.log").read_text(encoding="utf-8")


def test_the_log_rotates_and_keeps_three_old_files(tmp_path, monkeypatch):
    monkeypatch.setattr(applog, "data_dir", lambda: tmp_path / "data")
    applog.setup(max_bytes=2000)
    try:
        for n in range(400):
            applog.log.info("line %d with some words to fill the file up", n)
    finally:
        applog.shutdown()
    names = sorted(p.name for p in (tmp_path / "data" / "logs").iterdir())
    assert names == ["zpdf.log", "zpdf.log.1", "zpdf.log.2", "zpdf.log.3"]


def test_unexpected_errors_get_an_id_and_their_details_go_to_the_log(logs):
    home = str(Path.home())
    try:
        raise RuntimeError(f"broke on {home}/Documents/Salary 2026.pdf")
    except RuntimeError as exc:
        said = applog.unexpected(exc)
    found = re.search(r"Something went wrong \(error ([0-9A-F]{4})\)", said)
    assert found and "Copy diagnostics" in said
    text = read(logs)
    assert found.group(1) in text and "Traceback" in text and "RuntimeError" in text
    assert "Salary" not in text and home not in text and "<file>" in text


def test_tool_runs_are_logged_without_file_names(logs, tmp_path):
    secret = tmp_path / "Salary 2026.pdf"
    secret.write_bytes(b"x" * 2048)
    applog.tool_run("compress", [secret], 1.234, "done")
    text = read(logs)
    assert "compress" in text and "1 file" in text and "done" in text and "1.2 s" in text
    assert "Salary" not in text


def test_every_line_is_scrubbed(logs):
    applog.log.info("launch: ['C:\\\\Users\\\\zach\\\\Desktop\\\\contract final.pdf'] port 8000")
    applog.log.info("opened %s", Path.home() / "Private" / "bank statement.docx")
    text = read(logs)
    assert "contract final" not in text and "bank statement" not in text and str(Path.home()) not in text


def test_diagnostics_say_what_helps_and_nothing_private(logs, monkeypatch):
    from pdfmanager import __version__, update

    monkeypatch.setattr(update, "data_dir", lambda: logs.parent)
    update.save_settings(github_token="ghp_very_secret")
    applog.log.info("a line from earlier")
    text = applog.diagnostics()
    for wanted in (f"ZPDF {__version__}", "Python", "Windows" if __import__("sys").platform == "win32" else "Platform",
                   "Built-in OCR:", "Tesseract:", "Ghostscript:", "Office:", "a line from earlier"):
        assert wanted in text, wanted
    assert "ghp_very_secret" not in text and str(Path.home()) not in text


def test_a_job_that_breaks_says_so_plainly(logs):
    applog.install()

    def broken():
        raise KeyError("no such thing")

    job = progress.Job()
    job.run(broken)
    assert job.state == "failed" and re.search(r"Something went wrong \(error [0-9A-F]{4}\)", job.error)
    assert "KeyError" in read(logs)
    # errors meant for the user stay as they are
    job = progress.Job()
    job.expected = (ValueError,)
    job.run(lambda: (_ for _ in ()).throw(ValueError("please choose a page")))
    assert job.error == "please choose a page"


@pytest.fixture
def served(logs, monkeypatch):
    from pdfmanager import gui

    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    yield base, token, gui
    srv.shutdown()
    srv.server_close()


def call(base, token, path, data=None):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(base + path, data=None if data is None else json.dumps(data).encode(),
                                 headers={"X-Pdfm-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return res.status, res.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_diagnostics_and_the_log_folder_from_the_app(served, monkeypatch):
    base, token, gui = served
    status, text = call(base, token, "/api/diagnostics")
    assert status == 200 and "Built-in OCR:" in text
    opened = []
    monkeypatch.setattr(applog, "open_folder", lambda: opened.append(True))
    status, _ = call(base, token, "/api/logs/open", {})
    assert status == 200 and opened == [True]


def test_tool_runs_from_the_app_are_logged(served, logs, tmp_path):
    import urllib.request

    from reportlab.pdfgen import canvas

    base, token, gui = served
    src = tmp_path / "Private letter.pdf"
    c = canvas.Canvas(str(src))
    c.drawString(72, 720, "hello")
    c.save()
    boundary = "xyz"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"op\"\r\n\r\nreverse\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{src.name}\"\r\n"
            "Content-Type: application/pdf\r\n\r\n").encode() + src.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(base + "/api/run", data=body, headers={
        "X-Pdfm-Token": token, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as res:
        assert res.status == 200
    text = read(logs)
    assert "tool reverse" in text and "done" in text and "Private letter" not in text
