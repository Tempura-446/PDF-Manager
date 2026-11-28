# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for v0.95.0 Polish, Integration & Hardening Pass."""
from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

from reportlab.pdfgen import canvas

from pdfmanager import bookshelf, gui, models_manager, redline, tts

STATIC = Path(__file__).resolve().parents[1] / "pdfmanager" / "static"


def _make_pdf(path: Path, lines: list[str]) -> Path:
    c = canvas.Canvas(str(path))
    y = 750
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.showPage()
    c.save()
    return path


def _start_test_server():
    server, _ = gui.make_server(port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    return server, base, server.token


def _multipart(fields: dict[str, str], files: list[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    boundary = "----zpdf950testboundary"
    chunks: list[bytes] = []
    for k, v in fields.items():
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{k}"\r\n\r\n'
            f"{v}\r\n".encode("utf-8")
        )
    for field, fname, raw in files:
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field}"; filename="{fname}"\r\n'
            f"Content-Type: application/pdf\r\n\r\n".encode("utf-8")
            + raw
            + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def test_kokoro_pack_in_models_manager_and_tts_reset(tmp_path, monkeypatch):
    packs = {p["id"]: p for p in models_manager.list_models()}
    assert "kokoro_tts" in packs
    assert "Kokoro" in packs["kokoro_tts"]["name"]
    assert any("kokoro" in name for name, _url in tts.KOKORO_FILES)

    # Singleton cache reset
    tts._KOKORO_INSTANCE = object()
    tts.reset()
    assert tts._KOKORO_INSTANCE is None

    # delete_model resets tts cache and removes fake files
    model_p = tmp_path / "kokoro-v1.0.onnx"
    voices_p = tmp_path / "voices-v1.0.bin"
    model_p.write_bytes(b"fake")
    voices_p.write_bytes(b"fake")
    monkeypatch.setattr(tts, "_kokoro_paths", lambda: (model_p, voices_p))
    tts._KOKORO_INSTANCE = object()
    res = models_manager.delete_model("kokoro_tts")
    assert res["deleted"] == "kokoro_tts"
    assert tts._KOKORO_INSTANCE is None
    assert not model_p.exists()


def test_clear_progress_and_http_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(bookshelf, "_progress_path", lambda: tmp_path / "reading-progress.json")
    pdf = _make_pdf(tmp_path / "demo.pdf", ["Hello world"])
    bookshelf.save_progress(pdf, page=3, total=10)
    bid = bookshelf._book_id(pdf)
    assert bookshelf.get_progress(pdf)["page"] == 3

    # Clear via HTTP endpoint using book ID
    server, base, tok = _start_test_server()
    try:
        req = urllib.request.Request(
            f"{base}/api/library/progress/clear",
            data=json.dumps({"id": bid}).encode("utf-8"),
            headers={"X-Pdfm-Token": tok, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        assert data["ok"] is True
        assert data["removed"] is True
        assert bookshelf.get_progress(pdf) is None
    finally:
        server.shutdown()
        server.server_close()


def test_companion_files_endpoint_lists_open_files(tmp_path):
    pdf = _make_pdf(tmp_path / "contract.pdf", ["Clause 1"])
    server, base, tok = _start_test_server()
    try:
        fid = server.RequestHandlerClass.opened.add(pdf)
        req = urllib.request.Request(
            f"{base}/api/companion/files",
            headers={"X-Pdfm-Token": tok},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data.get("files", [])
        assert any(f["id"] == fid and f["name"] == "contract.pdf" and f["size_bytes"] > 0 for f in items)
    finally:
        server.shutdown()
        server.server_close()


def test_redline_pdf_endpoint_and_summary_table(tmp_path):
    old_pdf = _make_pdf(tmp_path / "v1.pdf", ["Payment shall be Net 30 days for $10,000."])
    new_pdf = _make_pdf(tmp_path / "v2.pdf", ["Payment shall be Net 60 days for $12,500."])

    # Direct PDF export succeeds with summary table
    report = redline.compute_redline(old_pdf, new_pdf)
    out_pdf = tmp_path / "out_redline.pdf"
    redline.export_redline_pdf(report, out_pdf)
    assert out_pdf.read_bytes().startswith(b"%PDF-")

    # HTTP POST /api/redline with opened original + uploaded revised PDF
    server, base, tok = _start_test_server()
    try:
        fid = server.RequestHandlerClass.opened.add(old_pdf)
        body, ctype = _multipart(
            {"id": fid},
            [("new", "v2.pdf", new_pdf.read_bytes())],
        )
        req = urllib.request.Request(
            f"{base}/api/redline",
            data=body,
            headers={"X-Pdfm-Token": tok, "Content-Type": ctype},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            fname = resp.headers.get("X-Pdfm-Filename", "")
            note = resp.headers.get("X-Pdfm-Note", "")
        assert raw.startswith(b"%PDF-")
        assert "redline.pdf" in fname
        assert "Redline" in note
    finally:
        server.shutdown()
        server.server_close()


def test_v950_frontend_wiring():
    reader_js = (STATIC / "reader.js").read_text(encoding="utf-8")
    render_js = (STATIC / "reader-render.js").read_text(encoding="utf-8")
    mobile_html = (STATIC / "mobile-drop.html").read_text(encoding="utf-8")
    tools_js = (STATIC / "tools.js").read_text(encoding="utf-8")

    assert "_loadTtsStatus" in reader_js
    assert "rd-tts-voice" in reader_js
    assert "rd-tts-auto" in reader_js
    assert "redlineWithFile" in reader_js
    assert "/api/redline" in reader_js
    assert "_stopReadAloud()" in render_js

    assert "/api/companion/files" in mobile_html
    assert "Grab from Desktop" in mobile_html
    assert "thumb-rm" in mobile_html

    assert "/api/library/progress/clear" in tools_js
    assert "/api/library/index/status" in tools_js
    assert "continue-dismiss" in tools_js
