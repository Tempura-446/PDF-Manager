"""Tools run on a file that is already open, by its id, without the page
sending the file's bytes back to the server it came from."""
import json
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from pdfmanager import core, gui


def text_pdf(path, pages=3):
    c = canvas.Canvas(str(path))
    for n in range(pages):
        c.drawString(72, 720, f"Page {n + 1}")
        c.showPage()
    c.save()
    return Path(path)


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    yield base, token, srv
    srv.shutdown()
    srv.server_close()


def run(base, token, fields, files=()):
    boundary = uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    for name, data in files:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n'
                     f"Content-Type: application/pdf\r\n\r\n".encode() + data + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(base + "/api/run", data=body, headers={
        "X-Pdfm-Token": token, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def test_a_tool_runs_on_an_opened_file_by_its_id(app, tmp_path):
    base, token, srv = app
    src = text_pdf(tmp_path / "Lease 2024.pdf")
    key = srv.RequestHandlerClass.opened.add(src)
    status, headers, data = run(base, token, {"op": "rotate", "degrees": "90", "ids": key})
    assert status == 200, data[:200]
    (tmp_path / "out.pdf").write_bytes(data)
    reader = core.open_pdf(tmp_path / "out.pdf")
    assert len(reader.pages) == 3 and reader.pages[0].rotation == 90
    assert "Lease%202024" in headers["X-Pdfm-Filename"] or "Lease 2024" in headers["X-Pdfm-Filename"]
    assert core.open_pdf(src).pages[0].rotation == 0, "the opened file itself is untouched"


def test_ids_and_uploads_keep_the_order_given(app, tmp_path):
    base, token, srv = app
    first, second = text_pdf(tmp_path / "a.pdf", 1), text_pdf(tmp_path / "b.pdf", 2)
    key = srv.RequestHandlerClass.opened.add(first)
    status, _, data = run(base, token, {"op": "merge", "ids": key}, files=[("b.pdf", second.read_bytes())])
    assert status == 200, data[:200]
    (tmp_path / "out.pdf").write_bytes(data)
    assert len(core.open_pdf(tmp_path / "out.pdf").pages) == 3


@pytest.mark.parametrize("ids", ["not-an-id", "../../etc/passwd", ","])
def test_an_id_that_isnt_open_is_refused(app, tmp_path, ids):
    base, token, _ = app
    status, _, data = run(base, token, {"op": "rotate", "degrees": "90", "ids": ids})
    if ids.strip(","):
        assert status == 400 and b"no longer available" in data
    else:
        assert status == 400, "no file at all: the tool asks for one"


def test_a_file_that_went_away_says_so(app, tmp_path):
    base, token, srv = app
    src = text_pdf(tmp_path / "gone.pdf")
    key = srv.RequestHandlerClass.opened.add(src)
    src.unlink()
    status, _, data = run(base, token, {"op": "rotate", "degrees": "90", "ids": key})
    assert status == 400 and b"no longer available" in data


def test_the_id_field_never_reaches_the_tool_as_an_option(app, tmp_path, monkeypatch):
    base, token, srv = app
    seen = {}
    real = gui.run_operation

    def spy(op, files, options, *rest):
        seen.update(options=dict(options), files=[Path(f).name for f in files])
        return real(op, files, options, *rest)

    monkeypatch.setattr(gui, "run_operation", spy)
    key = srv.RequestHandlerClass.opened.add(text_pdf(tmp_path / "a.pdf"))
    assert run(base, token, {"op": "rotate", "degrees": "90", "ids": key})[0] == 200
    assert "ids" not in seen["options"] and seen["files"] == ["a.pdf"]
