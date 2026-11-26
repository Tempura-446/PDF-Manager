# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for opening .cbz comic/manga archives directly in the ZPDF Reader."""

import io
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from pdfmanager import gui
from test_more import app, call  # noqa: F401


def _make_cbz(path: Path, pages: int = 2) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(1, pages + 1):
            img = Image.new("RGB", (200, 300), (240 - i * 20, 240, 240))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            zf.writestr(f"page_{i:02d}.png", buf.getvalue())
    return path


def test_api_cbz_open_converts_uploaded_cbz_to_pdf(tmp_path, app):
    base, token, _ = app
    cbz_path = _make_cbz(tmp_path / "manga_ch1.cbz", pages=2)
    status, headers, body = call(
        base,
        token,
        "/api/cbz/open",
        cbz_path.read_bytes(),
        {"X-Pdfm-Filename": "manga_ch1.cbz"},
    )
    assert status == 200
    assert body.startswith(b"%PDF")
    assert headers.get("X-Pdfm-Filename") == "manga_ch1.pdf"
    reader = PdfReader(io.BytesIO(body))
    assert len(reader.pages) == 2

    # Non-cbz filename rejected cleanly with 400
    bad_status, _, _ = call(
        base,
        token,
        "/api/cbz/open",
        b"not a cbz",
        {"X-Pdfm-Filename": "notes.txt"},
    )
    assert bad_status == 400


def test_open_file_serves_cbz_from_disk_with_range_support(tmp_path, app):
    base, token, srv = app
    cbz_path = _make_cbz(tmp_path / "volume_01.cbz", pages=3)
    key = srv.RequestHandlerClass.opened.add(cbz_path)

    # Full GET
    req = urllib.request.Request(f"{base}/api/file/{key}", headers={"X-Pdfm-Token": token})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
        assert resp.status == 200
        assert resp.headers.get("Content-Type") == "application/pdf"
        assert resp.headers.get("X-Pdfm-Filename") == "volume_01.pdf"
        assert data.startswith(b"%PDF")
        assert len(PdfReader(io.BytesIO(data)).pages) == 3

    # HTTP Range request (used by PDF.js streaming)
    rreq = urllib.request.Request(
        f"{base}/api/file/{key}",
        headers={"X-Pdfm-Token": token, "Range": "bytes=0-63"},
    )
    with urllib.request.urlopen(rreq, timeout=15) as rresp:
        chunk = rresp.read()
        assert rresp.status == 206
        assert len(chunk) == 64
        assert chunk == data[:64]


def test_reader_frontend_and_dialog_accept_cbz():
    root = Path(gui.__file__).parent
    assert ".cbz" in (root / "static" / "reader.js").read_text(encoding="utf-8")
    assert "/api/cbz/open" in (root / "static" / "tabs.js").read_text(encoding="utf-8")
    assert ".cbz" in (root / "static" / "tools.js").read_text(encoding="utf-8")
    assert "*.cbz" in (root / "gui.py").read_text(encoding="utf-8")
