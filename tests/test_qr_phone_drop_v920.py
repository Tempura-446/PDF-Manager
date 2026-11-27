# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for v0.92.0: Pure-Python QR SVG pairing, /mobile-drop, and /api/companion/drop."""

import io
import json
import threading
import urllib.request

from PIL import Image

from pdfmanager import appdata, gui, qrcode_svg


def _multipart(fields, files):
    boundary = "----ZpdfTestBoundary920"
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    for field, fname, ctype, raw in files:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{fname}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n".encode() + raw + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def test_make_qr_svg_generates_valid_svg():
    svg = qrcode_svg.make_qr_svg("http://192.168.1.50:8080/mobile-drop#token=secret")
    assert svg.startswith("<svg ")
    assert 'role="img"' in svg
    assert "<path d=" in svg
    assert svg.endswith("</svg>")


def test_companion_qr_mobile_drop_and_camera_scan_to_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    server, _url = gui.make_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    headers = {"X-Pdfm-Token": server.token}
    try:
        # 1. /mobile-drop static shell
        with urllib.request.urlopen(f"{base}/mobile-drop", timeout=5) as res:
            assert res.status == 200
            html = res.read().decode("utf-8")
            assert "ZPDF Phone Companion" in html

        # 2. /api/companion/info & /api/companion/qr
        req = urllib.request.Request(f"{base}/api/companion/info", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as res:
            info = json.loads(res.read().decode("utf-8"))
            assert "/mobile-drop#token=" in info["mobile_drop_url"]
            assert info["qr_url"] == "/api/companion/qr"

        req_qr = urllib.request.Request(f"{base}/api/companion/qr", headers=headers)
        with urllib.request.urlopen(req_qr, timeout=5) as res:
            assert "image/svg+xml" in res.headers.get("Content-Type", "")
            assert b"<svg " in res.read()

        # 3. POST /api/companion/drop in scan mode (2 camera photos -> bundled PDF)
        buf = io.BytesIO()
        Image.new("RGB", (100, 140), (240, 240, 240)).save(buf, format="JPEG")
        jpg = buf.getvalue()
        body, ctype = _multipart(
            {"mode": "scan"},
            [("file", "p1.jpg", "image/jpeg", jpg), ("file", "p2.jpg", "image/jpeg", jpg)],
        )
        req_drop = urllib.request.Request(
            f"{base}/api/companion/drop",
            data=body,
            headers={**headers, "Content-Type": ctype},
            method="POST",
        )
        with urllib.request.urlopen(req_drop, timeout=5) as res:
            out = json.loads(res.read().decode("utf-8"))
            assert out["ok"] is True
            assert out["pages"] == 2
            assert out["name"].endswith(".pdf")
            assert len(out["ids"]) == 1
            opened_path = server.RequestHandlerClass.opened.get(out["ids"][0])
            assert opened_path is not None and opened_path.is_file()
    finally:
        server.shutdown()
        server.server_close()
