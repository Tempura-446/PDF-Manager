# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Stages D-F regression tests: API-001 (Content-Length validation & make-safe-on-open),
SEC-005 (auditlog sensitive-key redaction), PERF-001 (vectorized _looks_grey),
TXT-001 (ordered text validation edge cases), and CMP-001/003 (geometry & blank pages)."""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

from PIL import Image
from reportlab.pdfgen import canvas

from pdfmanager import auditlog, compare, gui, ocr, policy
from pdfmanager.textedit import rewrite


def _make_pdf(path: Path, pages: list[tuple[tuple[float, float], str]]) -> Path:
    c = canvas.Canvas(str(path))
    for size, text in pages:
        c.setPageSize(size)
        if text:
            c.drawString(72, size[1] - 72, text)
        c.showPage()
    c.save()
    return path


def _start_test_server(opened_map=None):
    token = "test-stage-f-token"
    handler = type(
        "_StageFHandler",
        (gui.Handler,),
        {
            "token": token,
            "instance_secret": "inst-secret",
            "opened": opened_map if opened_map is not None else {},
            "allowed_hosts": None,
        },
    )
    server = gui._Server(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    handler.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port, token


# ---- API-001: HTTP Content-Length validation & require_make_safe_on_open --------------


def test_api001_rejects_malformed_and_negative_content_length():
    server, port, token = _start_test_server()
    try:
        for bad_len in ("abc", "-1", "-999999"):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.putrequest("POST", "/api/ping")
            conn.putheader("Host", f"127.0.0.1:{port}")
            conn.putheader("X-Pdfm-Token", token)
            conn.putheader("Content-Length", bad_len)
            conn.endheaders()
            resp = conn.getresponse()
            resp.read()
            conn.close()
            assert resp.status == 400
    finally:
        server.shutdown()
        server.server_close()


def test_api001_require_make_safe_on_open_header(monkeypatch, tmp_path):
    pdf = _make_pdf(tmp_path / "openme.pdf", [((612, 792), "Hello reader")])
    pf = tmp_path / "policy.json"
    pf.write_text(json.dumps({"require_make_safe_on_open": True}), encoding="utf-8")
    monkeypatch.setenv("ZPDF_POLICY_FILE", str(pf))
    policy.clear_cache()

    server, port, token = _start_test_server({"k1": pdf})
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/api/file/k1", headers={"Host": f"127.0.0.1:{port}", "X-Pdfm-Token": token})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        assert resp.status == 200
        assert resp.getheader("X-Pdfm-Made-Safe") == "1"
        assert body.startswith(b"%PDF-")
    finally:
        policy.clear_cache()
        server.shutdown()
        server.server_close()


# ---- SEC-005: Audit log sensitive-key scrubbing ---------------------------------------


def test_sec005_auditlog_redacts_sensitive_keys_recursively(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    entry = auditlog.record(
        "encrypt",
        target="secret.pdf",
        details={
            "user_password": "plain-user-pw-123",
            "owner_password": "plain-owner-pw-456",
            "github_token": "ghp_supersecret",
            "nested": {"private_key": "-----BEGIN PRIVATE KEY-----", "algo": "AES-256"},
            "safe_note": "ok",
        },
        log_path=log_path,
    )
    raw_text = log_path.read_text(encoding="utf-8")
    assert "plain-user-pw-123" not in raw_text
    assert "plain-owner-pw-456" not in raw_text
    assert "ghp_supersecret" not in raw_text
    assert "BEGIN PRIVATE KEY" not in raw_text
    assert entry["details"]["user_password"] == "[redacted]"
    assert entry["details"]["nested"]["private_key"] == "[redacted]"
    assert entry["details"]["nested"]["algo"] == "AES-256"
    assert entry["details"]["safe_note"] == "ok"
    assert auditlog.verify_chain(log_path)["valid"] is True


# ---- PERF-001: Vectorized _looks_grey -------------------------------------------------


def test_perf001_looks_grey_numpy_accuracy():
    grey_rgb = Image.new("RGB", (300, 300), (128, 129, 127))
    color_rgb = Image.new("RGB", (300, 300), (128, 128, 128))
    color_rgb.putpixel((150, 150), (255, 0, 0))
    assert ocr._looks_grey(grey_rgb) is True
    assert ocr._looks_grey(color_rgb) is False


# ---- TXT-001 & CMP-001/003: Integrity & geometry checks -------------------------------


def test_txt001_ordered_text_matches_edge_cases():
    assert rewrite._ordered_text_matches("Hello world", "Hello world") is True
    # Reordered characters with identical counts MUST fail
    assert rewrite._ordered_text_matches("olleh", "hello") is False
    assert rewrite._ordered_text_matches("12345", "54321") is False
    # Missing / extra internal characters MUST fail
    assert rewrite._ordered_text_matches("Hell world", "Hello world") is False
    assert rewrite._ordered_text_matches("Hello X world", "Hello world") is False
    # Ligatures and soft hyphens normalize cleanly
    assert rewrite._ordered_text_matches("ef\ufb01cient", "efficient") is True
    assert rewrite._ordered_text_matches("co\u00adoperate", "cooperate") is True


def test_cmp003_geometry_and_orientation_change_reported(tmp_path):
    p1 = _make_pdf(tmp_path / "portrait.pdf", [((612, 792), "Page one"), ((612, 792), "")])
    p2 = _make_pdf(tmp_path / "landscape.pdf", [((792, 612), "Page one"), ((612, 792), "")])
    rows = compare.compare(p1, p2, tmp_path / "diff.pdf", all_pages=True)["changes"]
    assert len(rows) == 2
    assert rows[0]["size_changed"] is True
    assert rows[0]["orientation_changed"] is True
