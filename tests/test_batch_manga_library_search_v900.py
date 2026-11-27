# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for v0.90.0 Track B (Batch Manga Volume Translator) & Track C
(Offline Library Full-Text Search across PDFs & EPUBs)."""

import io
import json
import threading
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image
from reportlab.pdfgen import canvas as rl_canvas

from pdfmanager import appdata, bookshelf, gui
from pdfmanager.cli import parser as cli_parser
from pdfmanager.server.operations import catalog
from pdfmanager.translate import manga as manga_mod


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


def _make_cbz(path, count=1):
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(count):
            buf = io.BytesIO()
            Image.new("RGB", (120, 160), (245, 245, 245)).save(buf, format="PNG")
            zf.writestr(f"page_{i + 1:03d}.png", buf.getvalue())


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


def test_offline_library_full_text_search_pdf_and_epub(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    lib = tmp_path / "library"
    lib.mkdir()
    _make_pdf(lib / "QuantumGuide.pdf", [
        "Introduction to classical mechanics",
        "The entanglement theorem explains nonlocal correlations",
    ])
    _make_epub(lib / "Novel.epub", [
        "Once upon a time in Kyoto.",
        "A strange entanglement appeared in the lantern light.",
    ])
    bookshelf.add_folder(str(lib))

    res = bookshelf.search_library("entanglement")
    assert res["scanned_books"] == 2
    assert len(res["results"]) == 2
    by_ext = {r["ext"]: r for r in res["results"]}
    assert by_ext["PDF"]["page"] == 2
    assert by_ext["PDF"]["match"].lower() == "entanglement"
    assert by_ext["EPUB"]["page"] == 2
    assert "lantern" in by_ext["EPUB"]["after"]


def test_library_search_http_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    lib = tmp_path / "books"
    lib.mkdir()
    _make_pdf(lib / "Manual.pdf", ["ZPDF offline search verification token"])
    bookshelf.add_folder(str(lib))

    server, _url = gui.make_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        st, data = _req(server, "GET", "/api/library/search?q=verification")
        assert st == 200
        assert len(data["results"]) == 1
        assert data["results"][0]["stem"] == "Manual"
        assert data["results"][0]["page"] == 1
    finally:
        server.shutdown()
        server.server_close()


def test_translate_manga_batch_cbz_and_pdf_exports_cbz(tmp_path):
    in_dir = tmp_path / "raw_vol"
    out_dir = tmp_path / "out_vol"
    in_dir.mkdir()
    _make_cbz(in_dir / "Ch01.cbz", count=1)
    _make_pdf(in_dir / "Ch02.pdf", ["Sample manga page"])

    def fake_translate_pdf(src_pdf, out_pdf, **_kwargs):
        Path(out_pdf).write_bytes(Path(src_pdf).read_bytes())
        return {"pages": 1, "mode": "manga", "src_lang": "ja"}

    with mock.patch("pdfmanager.translate.translate_pdf", side_effect=fake_translate_pdf):
        res = manga_mod.translate_manga_batch(
            in_dir, out_dir, src_lang="ja", dst_lang="en", export_format="cbz", manga_ai=False,
        )

    assert res["succeeded"] == 2
    assert res["failed"] == 0
    assert res["total_pages"] == 2
    out_names = sorted(Path(p).name for p in res["outputs"])
    assert out_names == ["Ch01_en.cbz", "Ch02_en.cbz"]
    for p in res["outputs"]:
        with zipfile.ZipFile(p, "r") as zf:
            names = zf.namelist()
            assert "ComicInfo.xml" in names
            assert any(n.endswith(".webp") for n in names)


def test_cli_and_catalog_have_manga_batch():
    p = cli_parser.build_parser()
    args = p.parse_args(["translate-manga-batch", "ch1.cbz", "ch2.cbz", "--from", "ja", "--to", "en", "--format", "cbz"])
    assert args.inputs == ["ch1.cbz", "ch2.cbz"]
    assert args.format == "cbz"
    ids = {op["id"] for op in catalog.OPERATIONS}
    assert "manga-batch" in ids
