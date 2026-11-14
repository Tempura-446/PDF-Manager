# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.26.0: Scan in the app's server (/api/scan/...) and on the command line
(pdfm scan, pdfm photos-to-pdf), over a fake WIA (fakewia.py)."""
import io
import json
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

from fakewia import FakeCom, FakeScanner, text_page
from pdfmanager import gui, scanner, scanpdf
from pdfmanager.cli import main
from photosynth import photo


@pytest.fixture
def wia():
    fake = FakeCom()
    scanner.use(fake)
    yield fake
    scanner.use(None)


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    yield base, token, srv
    srv.shutdown()
    srv.server_close()


def _call(app, path, data=None, body=None, headers=None):
    base, token, _ = app
    head = {"X-Pdfm-Token": token, **(headers or {})}
    if data is not None:
        body, head["Content-Type"] = json.dumps(data).encode(), "application/json"
    req = urllib.request.Request(base + path, data=body, headers=head, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=300) as res:
            raw = res.read()
            kind = res.headers.get("Content-Type", "")
            return res.status, (json.loads(raw) if kind.startswith("application/json") else raw), res.headers
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}"), err.headers


def _multipart(fields, files):
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    for name, data in files:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n'
                     f'Content-Type: image/jpeg\r\n\r\n'.encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def test_needs_the_token(app):
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(app[0] + "/api/scan/status", timeout=10)
    assert err.value.code == 403


def test_scanner_pages_photos_and_pdf(app, wia):
    office = FakeScanner("Office scanner", feeder_pages=[text_page(n) for n in (1, 2)])
    wia.scanners = [office, FakeScanner("Empty", feeder_pages=[])]
    status, state, _ = _call(app, "/api/scan/status")
    assert status == 200 and state["scanner"]["available"] and state["resolutions"] == [150, 200, 300]
    _, found, _ = _call(app, "/api/scan/devices")
    assert [d["name"] for d in found["devices"]] == ["Office scanner", "Empty"]
    _, made, _ = _call(app, "/api/scan/box", {})
    box = made["box"]
    status, got, _ = _call(app, "/api/scan/acquire",
                           {"box": box, "device": office.id, "source": "feeder", "dpi": 150, "mode": "grey"})
    assert status == 200 and len(got["pages"]) == 2 and got["pages"][0]["dpi"] == 150
    status, said, _ = _call(app, "/api/scan/acquire", {"box": box, "device": wia.scanners[1].id, "source": "feeder"})
    assert status == 400 and "feeder is empty" in said["error"]
    _, listed, _ = _call(app, f"/api/scan/pages?box={box}")
    assert [p["id"] for p in listed["pages"]] == [p["id"] for p in got["pages"]]
    status, thumb, head = _call(app, f"/api/scan/thumb?box={box}&page={got['pages'][0]['id']}")
    assert status == 200 and head["Content-Type"] == "image/jpeg"
    assert max(Image.open(io.BytesIO(thumb)).size) <= scanpdf.THUMB_SIDE
    # Photos: one with a page in it, and something that isn't a picture.
    shot = io.BytesIO()
    photo()[0].save(shot, "JPEG", quality=88)
    body, head = _multipart({"box": box}, [("page.jpg", shot.getvalue()), ("notes.txt", b"not a picture")])
    status, added, _ = _call(app, "/api/scan/photos", body=body, headers=head)
    assert status == 200 and [p["found"] for p in added["pages"]] == [True] and added["skipped"] == ["notes.txt"]
    # The PDF: the second page first, turned; then the first; the photo last.
    order = [{"id": got["pages"][1]["id"], "turn": 90}, {"id": got["pages"][0]["id"]}, {"id": added["pages"][0]["id"]}]
    status, pdf, head = _call(app, "/api/scan/make", {"box": box, "pages": order, "ocr": False, "upright": False},
                              headers={"X-Pdfm-Keep": "1"})
    assert status == 200 and pdf[:5] == b"%PDF-", pdf
    assert head["X-Pdfm-Filename"].startswith("Scan%20") and head.get("X-Pdfm-Result-Id")
    sizes = [tuple(round(float(v)) for v in p.mediabox[2:]) for p in PdfReader(io.BytesIO(pdf)).pages]
    assert sizes == [(842, 595), (595, 842), (595, 842)], sizes
    # Gone once closed.
    _call(app, "/api/scan/close", {"box": box})
    status, said, _ = _call(app, f"/api/scan/pages?box={box}")
    assert status == 400 and "no longer there" in said["error"]


def test_errors(app, wia):
    status, said, _ = _call(app, "/api/scan/acquire", {"box": "nope", "device": "x"})
    assert status == 400 and "no longer there" in said["error"]
    _, made, _ = _call(app, "/api/scan/box", {})
    status, said, _ = _call(app, "/api/scan/make", {"box": made["box"], "pages": []})
    assert status == 400 and "no pages" in said["error"]
    status, said, _ = _call(app, "/api/scan/acquire", {"box": made["box"]})
    assert status == 400 and "choose a scanner" in said["error"]
    status, said, _ = _call(app, f"/api/scan/thumb?box={made['box']}&page=x&look=sepia")
    assert status == 400


def test_status_without_wia(app):
    scanner.use(FakeCom(missing=True))
    try:
        _, state, _ = _call(app, "/api/scan/status")
    finally:
        scanner.use(None)
    assert not state["scanner"]["available"] and state["scanner"]["reason"]


def test_scan_is_a_tool_of_the_app():
    op = gui.OPS["scan"]
    assert op["files"] == "scan" and op["group"] == "Convert" and op["fields"] == []
    html = Path(gui.__file__).with_name("gui.html").read_text(encoding="utf-8")
    assert 'id="scan-host"' in html and "Scan paper" in html


def test_cli_photos_to_pdf(tmp_path):
    photo()[0].save(tmp_path / "p1.jpg", quality=88)
    assert main(["photos-to-pdf", str(tmp_path / "p1.jpg"), "-o", str(tmp_path / "out.pdf"), "--no-ocr",
                 "--look", "colour"]) == 0
    reader = PdfReader(tmp_path / "out.pdf")
    assert len(reader.pages) == 1 and tuple(round(float(v)) for v in reader.pages[0].mediabox[2:]) == (595, 842)


def test_cli_scan(tmp_path, wia, capsys):
    s = FakeScanner("Desk scanner", feeder_pages=[text_page(1), text_page(2)])
    wia.scanners = [s]
    assert main(["scan", "--list"]) == 0
    assert "Desk scanner (feeder)" in capsys.readouterr().out
    assert main(["scan", "--scanner", "desk", "--feeder", "--dpi", "150", "--no-ocr", "-o", str(tmp_path / "s.pdf")]) == 0
    assert len(PdfReader(tmp_path / "s.pdf").pages) == 2
    wia.scanners = []
    assert main(["scan", "-o", str(tmp_path / "x.pdf")]) == 1
    assert "No scanner was found" in capsys.readouterr().err
