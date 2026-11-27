# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for Track 4 (v0.84.0): Visual Cover-Art Bookshelf and sibling chapter navigation."""

import io
import json
import urllib.request
import zipfile

from PIL import Image
from pypdf import PdfWriter

from pdfmanager import appdata, bookshelf
from test_more import app  # noqa: F401


def _make_cbz(path, color=(180, 90, 90)):
    img = Image.new("RGB", (200, 280), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("001.png", buf.getvalue())
    return path


def _make_pdf(path):
    w = PdfWriter()
    w.add_blank_page(200, 280)
    with open(path, "wb") as fh:
        w.write(fh)
    return path


def test_bookshelf_scan_thumbs_and_sibling_chapters(tmp_path, monkeypatch, app):
    base, token, srv = app
    monkeypatch.setattr(appdata, "_data_dir", lambda: tmp_path / "zpdf-data")

    lib_dir = tmp_path / "manga-shelf"
    lib_dir.mkdir()
    c1 = _make_cbz(lib_dir / "ch_1.cbz", (200, 60, 60))
    c2 = _make_cbz(lib_dir / "ch_2.cbz", (60, 200, 60))
    c10 = _make_pdf(lib_dir / "ch_10.pdf")

    # 1) Add folder via POST /api/library/folder
    req_add = urllib.request.Request(
        f"{base}/api/library/folder",
        data=json.dumps({"action": "add", "path": str(lib_dir)}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Pdfm-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(req_add) as resp:
        assert resp.status == 200
        data = json.loads(resp.read())
    names = [it["name"] for it in data["items"]]
    assert names == ["ch_1.cbz", "ch_2.cbz", "ch_10.pdf"]  # natural sort!

    # 2) Filter via GET /api/library?q=ch_2
    req_q = urllib.request.Request(f"{base}/api/library?q=ch_2", headers={"X-Pdfm-Token": token})
    with urllib.request.urlopen(req_q) as resp_q:
        qdata = json.loads(resp_q.read())
    assert [it["name"] for it in qdata["items"]] == ["ch_2.cbz"]

    # 3) Cover thumbnail via GET /api/library/thumb/<id>
    book_id = data["items"][0]["id"]
    req_th = urllib.request.Request(f"{base}/api/library/thumb/{book_id}", headers={"X-Pdfm-Token": token})
    with urllib.request.urlopen(req_th) as resp_th:
        assert resp_th.status == 200
        assert resp_th.headers.get("Content-Type") == "image/jpeg"
        jpeg = resp_th.read()
        assert jpeg.startswith(b"\xff\xd8\xff")

    # 4) Open from library & navigate Next / Prev chapters
    book_id_2 = data["items"][1]["id"]
    req_open = urllib.request.Request(
        f"{base}/api/library/open",
        data=json.dumps({"id": book_id_2}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Pdfm-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(req_open) as resp_o:
        opened = json.loads(resp_o.read())
    assert opened["name"] == "ch_2.cbz"

    req_next = urllib.request.Request(
        f"{base}/api/library/neighbors",
        data=json.dumps({"id": opened["id"], "open": "next"}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Pdfm-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(req_next) as resp_n:
        ndata = json.loads(resp_n.read())
    assert ndata["index"] == 2 and ndata["total"] == 3
    assert ndata["opened"]["name"] == "ch_10.pdf"

    req_prev = urllib.request.Request(
        f"{base}/api/library/neighbors",
        data=json.dumps({"id": opened["id"], "open": "prev"}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Pdfm-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(req_prev) as resp_p:
        pdata = json.loads(resp_p.read())
    assert pdata["opened"]["name"] == "ch_1.cbz"
    assert c1.resolve() == bookshelf.find_by_id(book_id).resolve()
    assert c10.is_file()


def test_large_json_api_response_gzip_compression(tmp_path, monkeypatch, app):
    import gzip

    base, token, _srv = app
    monkeypatch.setattr(appdata, "_data_dir", lambda: tmp_path / "zpdf-gz")
    lib_dir = tmp_path / "big-shelf"
    lib_dir.mkdir()
    for i in range(40):
        _make_pdf(lib_dir / f"volume_{i:03d}_very_long_series_title_for_gzip_test.pdf")
    bookshelf.add_folder(str(lib_dir))

    req = urllib.request.Request(
        f"{base}/api/library",
        headers={"X-Pdfm-Token": token, "Accept-Encoding": "gzip"},
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        assert resp.headers.get("Content-Encoding") == "gzip"
        raw = gzip.decompress(resp.read())
        payload = json.loads(raw.decode("utf-8"))
        assert len(payload["items"]) == 40
