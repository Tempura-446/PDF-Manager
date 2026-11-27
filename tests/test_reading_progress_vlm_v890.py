# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for v0.89.0 Track A: persistent reading progress, Continue Reading
bookshelf enrichment, /api/library/progress endpoints, and Vision-LLM reader wiring."""

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from pdfmanager import appdata, bookshelf, gui


def _req(server, method, path, body=None):
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"X-Pdfm-Token": server.token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as res:
        return res.status, json.loads(res.read().decode("utf-8"))


def test_bookshelf_reading_progress_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    lib = tmp_path / "manga"
    lib.mkdir()
    vol1 = lib / "Vol01.cbz"
    vol2 = lib / "Vol02.pdf"
    vol1.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    vol2.write_bytes(b"%PDF-1.4\n")

    bookshelf.add_folder(str(lib))
    saved = bookshelf.save_progress(vol1, page=42, total=200, zoom="page-fit", rtl=True, spread=True)
    assert saved["page"] == 42
    assert saved["total"] == 200
    assert saved["percent"] == 21
    assert saved["rtl"] is True
    assert saved["spread"] is True

    loaded = bookshelf.get_progress(vol1)
    assert loaded is not None
    assert loaded["page"] == 42
    assert loaded["zoom"] == "page-fit"

    scan = bookshelf.scan_library()
    by_name = {it["name"]: it for it in scan["items"]}
    assert by_name["Vol01.cbz"]["progress"]["page"] == 42
    assert "progress" not in by_name["Vol02.pdf"]
    assert len(scan["recent"]) == 1
    assert scan["recent"][0]["name"] == "Vol01.cbz"
    assert bookshelf.find_by_id(scan["recent"][0]["id"]) == vol1.resolve()


def test_library_progress_http_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    doc = tmp_path / "book.pdf"
    doc.write_bytes(b"%PDF-1.4\n")

    server, _url = gui.make_server(port=0, open_paths=(doc,))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        file_id = next(iter(server.RequestHandlerClass.opened.items))
        st, body = _req(server, "POST", "/api/library/progress", {
            "file_id": file_id,
            "page": 15,
            "total": 60,
            "zoom": "1.25",
            "rtl": False,
            "spread": True,
        })
        assert st == 200
        assert body["ok"] is True
        assert body["progress"]["percent"] == 25

        st2, body2 = _req(server, "GET", f"/api/library/progress?file_id={file_id}")
        assert st2 == 200
        assert body2["progress"]["page"] == 15
        assert body2["progress"]["spread"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_reader_has_vlm_page_tool_and_continue_reading_ui():
    static_dir = Path(__file__).resolve().parent.parent / "pdfmanager" / "static"
    reader_js = (static_dir / "reader.js").read_text(encoding="utf-8")
    tools_js = (static_dir / "tools.js").read_text(encoding="utf-8")
    assert "openVlmPageTool" in reader_js
    assert "/api/ai/vlm-ocr" in reader_js
    assert "translategemma:4b" in reader_js
    assert "Continue Reading" in tools_js
    assert "shelf-progress-bar" in tools_js
