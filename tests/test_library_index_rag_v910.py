# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for v0.91.0: Persistent SQLite FTS5 Library Index & Offline Ask Your Bookshelf RAG."""

import json
import threading
import urllib.request
import zipfile
from unittest import mock

from reportlab.pdfgen import canvas as rl_canvas

from pdfmanager import appdata, bookshelf, gui, library_index
from pdfmanager.cli import parser as cli_parser


def _make_pdf(path, pages_text):
    c = rl_canvas.Canvas(str(path))
    for text in pages_text:
        c.drawString(72, 720, text)
        c.showPage()
    c.save()


def _make_epub(path, chapters):
    with zipfile.ZipFile(path, "w") as zf:
        for i, body in enumerate(chapters, 1):
            html = f"<html><body><h1>Chapter {i}</h1><p>{body}</p></body></html>"
            zf.writestr(f"OEBPS/ch{i:02d}.xhtml", html.encode("utf-8"))


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


def test_fts5_incremental_sync_and_bm25_search(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    lib = tmp_path / "lib"
    lib.mkdir()
    _make_pdf(lib / "Caching.pdf", [
        "Overview of transformer attention layers.",
        "KV cache quantization reduces memory bandwidth by 4x.",
    ])
    _make_epub(lib / "Systems.epub", [
        "Storage engines and write-ahead logs.",
        "SQLite FTS5 provides fast BM25 full-text ranking.",
    ])
    bookshelf.add_folder(str(lib))

    items = bookshelf.scan_library()["items"]
    s1 = library_index.sync_index(items)
    assert s1["indexed"] == 2
    assert s1["skipped"] == 0
    assert s1["total_books"] == 2

    # Second sync with unchanged mtimes skips both files cleanly
    s2 = library_index.sync_index(items)
    assert s2["indexed"] == 0
    assert s2["skipped"] == 2

    res = bookshelf.search_library("quantization")
    assert res["engine"] in ("sqlite-fts5", "sqlite-like")
    assert len(res["results"]) == 1
    assert res["results"][0]["stem"] == "Caching"
    assert res["results"][0]["page"] == 2

    # Removing a book prunes it from the index
    (lib / "Systems.epub").unlink()
    bookshelf._invalidate_cache()
    s3 = library_index.sync_index(bookshelf.scan_library()["items"])
    assert s3["removed"] == 1
    assert s3["total_books"] == 1


def test_ask_library_rag_http_and_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    lib = tmp_path / "research"
    lib.mkdir()
    _make_pdf(lib / "SafetyManual.pdf", [
        "Always wear protective goggles when operating the laser cutter.",
    ])
    bookshelf.add_folder(str(lib))

    with mock.patch("pdfmanager.assistant._chat", return_value=("Wear protective goggles [1].", "qwen2.5:7b")):
        server, _url = gui.make_server(port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            st, ans = _req(server, "POST", "/api/library/ask", {"query": "What safety gear is required for the laser cutter?"})
            assert st == 200
            assert "goggles" in ans["answer"]
            assert ans["model"] == "qwen2.5:7b"
            assert len(ans["citations"]) == 1
            assert ans["citations"][0]["stem"] == "SafetyManual"
            assert ans["citations"][0]["page"] == 1

            st2, status = _req(server, "GET", "/api/library/index/status")
            assert st2 == 200
            assert status["total_books"] == 1
            assert status["total_pages"] >= 1
        finally:
            server.shutdown()
            server.server_close()

    p = cli_parser.build_parser()
    a1 = p.parse_args(["library-search", "goggles", "--limit", "10"])
    assert a1.query == "goggles"
    a2 = p.parse_args(["library-ask", "What gear?", "--top-k", "4"])
    assert a2.question == "What gear?"
