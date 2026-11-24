"""Regression tests for post-v0.63.0 source, security, UI/UX, and performance audit fixes."""

from __future__ import annotations

import json
import stat
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from pdfmanager import core, gui, i18n, office, policy, progress, translate, update
from pdfmanager.server import operations


def _make_blank_pdf(path: Path) -> Path:
    w = PdfWriter()
    w.add_blank_page(width=400, height=500)
    with open(path, "wb") as fh:
        w.write(fh)
    return path


def _start_test_server():
    server, url = gui.make_server()
    base = url.split("#", 1)[0].rstrip("/")
    tok = server.RequestHandlerClass.token
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, base, tok


def test_audit_and_policy_get_endpoints_return_json():
    server, base, tok = _start_test_server()
    try:
        for ep, expected_key in (("/api/audit", "entries"), ("/api/policy", "policies")):
            req = urllib.request.Request(
                f"{base}{ep}",
                headers={"X-Pdfm-Token": tok},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
                assert expected_key in data
    finally:
        server.shutdown()
        server.server_close()


def test_preflight_fix_endpoint_returns_pdf(tmp_path):
    pdf = _make_blank_pdf(tmp_path / "sample.pdf")
    server, base, tok = _start_test_server()
    try:
        boundary = "----zpdf-test-boundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="sample.pdf"\r\n'
            f"Content-Type: application/pdf\r\n\r\n"
        ).encode("utf-8") + pdf.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(
            f"{base}/api/preflight/fix",
            data=body,
            headers={
                "X-Pdfm-Token": tok,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type", "").startswith("application/pdf")
            out = resp.read()
            assert out.startswith(b"%PDF-")
    finally:
        server.shutdown()
        server.server_close()


def test_translate_test_does_not_leak_stored_api_key_to_overridden_url(monkeypatch):
    seen = {}

    def fake_loader(src, dst, *, api_url, model, api_key):
        seen["api_url"] = api_url
        seen["api_key"] = api_key
        return lambda items: ["Hola mundo"]

    monkeypatch.setattr(
        update,
        "settings",
        lambda: {
            "translate_api_url": "https://saved.example.com/v1",
            "translate_model": "gpt-4o-mini",
            "translate_api_key": "sk-secret-stored",
        },
    )
    monkeypatch.setattr(translate, "_load_llm_translator", fake_loader)

    server, base, tok = _start_test_server()
    try:
        # 1. Overriding URL without providing key must NOT send the stored key
        payload = json.dumps({"translate_api_url": "https://evil.example.com/v1"}).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/api/translate/test",
            data=payload,
            headers={"X-Pdfm-Token": tok, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
        assert seen["api_url"] == "https://evil.example.com/v1"
        assert seen["api_key"] == ""

        # 2. Testing the saved URL without re-typing key uses the stored key
        payload2 = json.dumps({"translate_api_url": "https://saved.example.com/v1"}).encode("utf-8")
        req2 = urllib.request.Request(
            f"{base}/api/translate/test",
            data=payload2,
            headers={"X-Pdfm-Token": tok, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req2, timeout=5) as resp:
            assert resp.status == 200
        assert seen["api_key"] == "sk-secret-stored"
    finally:
        server.shutdown()
        server.server_close()


def test_web_to_pdf_ssrf_and_policy_guards(tmp_path, monkeypatch):
    out = tmp_path / "out.pdf"
    # SSRF bypass attempts must be rejected
    for bad in (
        "http://x@127.0.0.1/secret",
        "http://[::ffff:127.0.0.1]/secret",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
    ):
        with pytest.raises(core.PdfManagerError, match="localhost|computer"):
            office.html_to_pdf(bad, out)

    # Enterprise disable_network policy must block remote URLs
    monkeypatch.setattr(
        policy,
        "check_network_allowed",
        lambda feature="": (_ for _ in ()).throw(policy.PolicyError("network disabled by policy")),
    )
    with pytest.raises(core.PdfManagerError, match="network disabled"):
        office.html_to_pdf("https://example.com", out)


def test_web_op_rejects_local_file_path(tmp_path):
    html = tmp_path / "secret.html"
    html.write_text("<h1>local</h1>", encoding="utf-8")
    with pytest.raises(operations.UserError, match="http://"):
        operations.run_operation("web", [], {"url": str(html)}, None, {}, tmp_path, [])


def test_i18n_fail_closed_sha256_and_symlink_rejection(tmp_path, monkeypatch):
    # Symlink inside language pack zip must be rejected
    bad_zip = tmp_path / "symlink_pack.zip"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"code": "es", "name": "Spanish"}))
        zf.writestr("ui.json", json.dumps({"hello": "hola"}))
        zi = zipfile.ZipInfo("ocr/link")
        zi.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(zi, "/etc/passwd")
    monkeypatch.setattr(i18n, "packs_dir", lambda: tmp_path / "packs")
    with pytest.raises(core.PdfManagerError, match="symbolic link"):
        i18n.install_pack_from_zip(bad_zip)

    # Curated pack with sha256_url that returns invalid/failed checksum must fail closed
    monkeypatch.setattr(
        i18n,
        "fetch_zpdf_release_packs",
        lambda: {
            "es": {
                "code": "es",
                "download_url": "https://example.invalid/es.zip",
                "sha256_url": "https://example.invalid/es.zip.sha256",
            }
        },
    )
    monkeypatch.setattr(i18n, "_http_get_bytes", lambda url, **kw: b"not-a-valid-sha")
    with pytest.raises(core.PdfManagerError, match="checksum"):
        i18n.download_and_install_pack("es")


def test_translate_in_place_preserves_non_text_page_and_propagates_cancel(tmp_path):
    pdf = _make_blank_pdf(tmp_path / "blank.pdf")
    out = tmp_path / "out.pdf"
    res = translate.translate_pdf(pdf, out, "en", "es", mode="in-place", translator=lambda xs: xs)
    assert res["empty_pages"] == 1
    assert len(PdfReader(out).pages) == 1

    # Cancellation during layout extraction must propagate progress.Cancelled
    job = progress.Job()
    job.cancel()
    token = progress._job.set(job)
    try:
        with pytest.raises(progress.Cancelled):
            translate.translate_pdf(pdf, tmp_path / "cancelled.pdf", "en", "es", mode="in-place", translator=lambda xs: xs)
    finally:
        progress._job.reset(token)


def test_operations_use_check_not_checkbox_and_update_no_build_matches_translate():
    for op in operations.OPERATIONS:
        for field in op.get("fields", []):
            assert field["kind"] != "checkbox", f"{op['id']}.{field['key']} uses 'checkbox' instead of 'check'"
    assert update._NO_BUILD.search("ERROR: No matching distribution found for ctranslate2")
    assert update._NO_BUILD.search("ERROR: No matching distribution found for sentencepiece")


def test_static_i18n_js_has_no_es_exports_and_exposes_global():
    js = Path("pdfmanager/static/i18n.js").read_text(encoding="utf-8")
    assert "export function" not in js
    assert "global.ZpdfI18n" in js
