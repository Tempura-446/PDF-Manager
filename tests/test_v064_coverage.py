# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""v0.64.0 integration tests: full HTTP route coverage, lazy startup, OCR worker
document caching, OS dark-mode startup color, i18n locale parity, and filing rules."""

import http.client
import json
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest
from pypdf import PdfWriter

from pdfmanager import comments, gui, ocr


def _make_pdf(path: Path, pages: int = 2) -> Path:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=612, height=792)
    with open(path, "wb") as fh:
        w.write(fh)
    return path


def _multipart(fields=None, files=None):
    boundary = f"----zpdf-{uuid.uuid4().hex}"
    parts = []
    for k, v in (fields or {}).items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{k}"\r\n\r\n'
            f"{v}\r\n".encode("utf-8")
        )
    for field_name, filename, data, ctype in (files or []):
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n".encode("utf-8")
            + data
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return f"multipart/form-data; boundary={boundary}", b"".join(parts)


@pytest.fixture()
def live_server(tmp_path, monkeypatch):
    monkeypatch.setenv("PDFM_DATA_DIR", str(tmp_path / "appdata"))
    server, url = gui.make_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    token = server.RequestHandlerClass.token
    try:
        yield host, port, token, server
    finally:
        server.shutdown()
        server.server_close()


def _req(host, port, token, method, path, body=b"", content_type="application/json"):
    conn = http.client.HTTPConnection(host, port, timeout=15)
    headers = {"X-Pdfm-Token": token}
    if body:
        headers["Content-Type"] = content_type
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    hdrs = dict(resp.getheaders())
    conn.close()
    return resp.status, data, hdrs


# ---- P1: Live HTTP integration tests for remaining untested routes ----

def test_password_strength_route(live_server):
    host, port, token, _ = live_server
    status, data, _ = _req(
        host, port, token, "POST", "/api/password/strength",
        json.dumps({"password": "123"}).encode(),
    )
    assert status == 200
    weak = json.loads(data)
    assert isinstance(weak, dict) and weak

    status2, data2, _ = _req(
        host, port, token, "POST", "/api/password/strength",
        json.dumps({"password": "Correct-Horse-Battery-Staple-99!"}).encode(),
    )
    assert status2 == 200
    strong = json.loads(data2)
    assert strong != weak


def test_recent_restore_route(live_server, tmp_path):
    host, port, token, _ = live_server
    pdf = _make_pdf(tmp_path / "sample.pdf", pages=1)
    status, data, _ = _req(
        host, port, token, "POST", "/api/recent/restore",
        json.dumps({"items": [{"path": str(pdf), "name": pdf.name}]}).encode(),
    )
    assert status == 200
    payload = json.loads(data)
    assert isinstance(payload, dict)


def test_comments_markdown_and_summary_routes(live_server, tmp_path):
    host, port, token, _ = live_server
    pdf = _make_pdf(tmp_path / "notes.pdf", pages=1)
    ctype, body = _multipart(files=[("file", "notes.pdf", pdf.read_bytes(), "application/pdf")])

    status_md, data_md, _ = _req(host, port, token, "POST", "/api/comments/markdown", body, ctype)
    assert status_md == 200
    assert isinstance(data_md.decode("utf-8"), str)

    status_sum, data_sum, _ = _req(host, port, token, "POST", "/api/comments/summary", body, ctype)
    assert status_sum == 200
    assert data_sum  # PDF or text summary


def test_quantities_route(live_server, tmp_path):
    host, port, token, _ = live_server
    pdf = _make_pdf(tmp_path / "plan.pdf", pages=1)
    # Empty measurements -> 400 UserError
    ctype, body = _multipart(
        fields={"format": "csv"},
        files=[("file", "plan.pdf", pdf.read_bytes(), "application/pdf")],
    )
    status_empty, _, _ = _req(host, port, token, "POST", "/api/quantities", body, ctype)
    assert status_empty == 400

    # With a measurement -> 200 CSV
    measures = [{"page": 1, "kind": "length", "name": "Wall A", "quantity": 12.5, "unit": "m"}]
    ctype, body = _multipart(
        fields={"format": "csv", "measures": json.dumps(measures)},
        files=[("file", "plan.pdf", pdf.read_bytes(), "application/pdf")],
    )
    status, data, _ = _req(host, port, token, "POST", "/api/quantities", body, ctype)
    assert status == 200
    assert "Wall A" in data.decode("utf-8", errors="replace")


def test_form_routes_full_lifecycle(live_server, tmp_path):
    host, port, token, _ = live_server
    pdf = _make_pdf(tmp_path / "form.pdf", pages=2)
    raw = pdf.read_bytes()

    # 1. /api/form/add
    specs = [
        {"page": 1, "kind": "text", "name": "FullName", "rect": [72, 680, 260, 704]},
        {"page": 1, "kind": "checkbox", "name": "Agree", "rect": [72, 640, 90, 658]},
    ]
    ctype, body = _multipart(
        fields={"fields": json.dumps(specs)},
        files=[("file", "form.pdf", raw, "application/pdf")],
    )
    status, pdf_with_fields, hdrs = _req(host, port, token, "POST", "/api/form/add", body, ctype)
    assert status == 200
    assert pdf_with_fields.startswith(b"%PDF")
    assert "FullName" in hdrs.get("X-Pdfm-Names", "")

    # 2. /api/form/fields (summary + details=1)
    ctype, body = _multipart(
        fields={"details": "1"},
        files=[("file", "form.pdf", pdf_with_fields, "application/pdf")],
    )
    status, data, _ = _req(host, port, token, "POST", "/api/form/fields", body, ctype)
    assert status == 200
    fields_payload = json.loads(data)["fields"]
    assert "1" in fields_payload and len(fields_payload["1"]) >= 2

    # 3. /api/form/edit (rename / resize)
    changes = [{"page": 1, "index": 0, "name": "ApplicantName", "rect": [72, 680, 300, 706]}]
    ctype, body = _multipart(
        fields={"changes": json.dumps(changes)},
        files=[("file", "form.pdf", pdf_with_fields, "application/pdf")],
    )
    status, edited_pdf, _ = _req(host, port, token, "POST", "/api/form/edit", body, ctype)
    assert status == 200
    assert edited_pdf.startswith(b"%PDF")

    # 4. /api/form/copy (copy field 0 to page 2)
    ctype, body = _multipart(
        fields={"page": "1", "index": "0", "pages": "2", "same_name": "0"},
        files=[("file", "form.pdf", edited_pdf, "application/pdf")],
    )
    status, copied_pdf, _ = _req(host, port, token, "POST", "/api/form/copy", body, ctype)
    assert status == 200
    assert copied_pdf.startswith(b"%PDF")

    # 5. /api/form/table (turn field 0 on page 2 into 2x2 grid)
    ctype, body = _multipart(
        fields={"page": "2", "index": "0", "rows": "2", "cols": "2", "row_gap": "4", "col_gap": "4"},
        files=[("file", "form.pdf", copied_pdf, "application/pdf")],
    )
    status, table_pdf, _ = _req(host, port, token, "POST", "/api/form/table", body, ctype)
    assert status == 200
    assert table_pdf.startswith(b"%PDF")

    # 6. /api/form/reorder
    ctype, body = _multipart(
        fields={"page": "1", "mode": "rows"},
        files=[("file", "form.pdf", table_pdf, "application/pdf")],
    )
    status, reordered_pdf, _ = _req(host, port, token, "POST", "/api/form/reorder", body, ctype)
    assert status == 200
    assert reordered_pdf.startswith(b"%PDF")

    # 7. /api/form/import (JSON) -> /api/form/export (JSON & CSV) -> /api/form/keep-defaults -> /api/form/clear
    import_json = json.dumps({"ApplicantName": "Ada Lovelace"}).encode("utf-8")
    ctype, body = _multipart(
        files=[
            ("file", "form.pdf", reordered_pdf, "application/pdf"),
            ("data", "values.json", import_json, "application/json"),
        ],
    )
    status, filled_pdf, _ = _req(host, port, token, "POST", "/api/form/import", body, ctype)
    assert status == 200
    assert filled_pdf.startswith(b"%PDF")

    ctype, body = _multipart(
        fields={"format": "json"},
        files=[("file", "form.pdf", filled_pdf, "application/pdf")],
    )
    status, exported_json, _ = _req(host, port, token, "POST", "/api/form/export", body, ctype)
    assert status == 200
    assert json.loads(exported_json).get("ApplicantName") == "Ada Lovelace"

    ctype, body = _multipart(files=[("file", "form.pdf", filled_pdf, "application/pdf")])
    status, defaults_pdf, _ = _req(host, port, token, "POST", "/api/form/keep-defaults", body, ctype)
    assert status == 200
    assert defaults_pdf.startswith(b"%PDF")

    status, check_data, _ = _req(host, port, token, "POST", "/api/form/check", body, ctype)
    assert status == 200
    assert "issues" in json.loads(check_data)

    status, cleared_pdf, _ = _req(host, port, token, "POST", "/api/form/clear", body, ctype)
    assert status == 200
    assert cleared_pdf.startswith(b"%PDF")

    # 8. /api/form/detect & /api/form/fillable (blank PDF has no lines -> 400 UserError on fillable, 200 {} on detect)
    ctype, body = _multipart(files=[("file", "blank.pdf", raw, "application/pdf")])
    status_det, det_data, _ = _req(host, port, token, "POST", "/api/form/detect", body, ctype)
    assert status_det == 200
    assert "fields" in json.loads(det_data)

    status_fill, fill_data, _ = _req(host, port, token, "POST", "/api/form/fillable", body, ctype)
    assert status_fill == 400
    assert "no fields found" in json.loads(fill_data).get("error", "").lower()


# ---- P2: Startup lazy imports & OCR worker PdfDocument cache ----

def test_gui_import_keeps_heavy_modules_lazy():
    code = (
        "import sys, pdfmanager.gui; "
        "heavy = ['pypdf', 'cryptography', 'PIL', 'numpy', 'pikepdf', "
        "'pdfmanager.core', 'pdfmanager.search', 'pdfmanager.ocr', "
        "'pdfmanager.accessibility', 'pdfmanager.certsign', 'pdfmanager.layers', "
        "'pdfmanager.compare', 'pdfmanager.redact', 'pdfmanager.preflight', 'pdfmanager.office']; "
        "loaded = [m for m in heavy if m in sys.modules]; "
        "assert not loaded, f'eagerly loaded: {loaded}'"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_ocr_worker_pdf_doc_caches_and_reopens_on_change(tmp_path):
    pdf = _make_pdf(tmp_path / "scan.pdf", pages=2)
    try:
        doc1 = ocr._worker_pdf_doc(str(pdf))
        doc2 = ocr._worker_pdf_doc(str(pdf))
        assert doc1 is doc2
        assert len(doc1) == 2

        # Modify file -> must reopen a fresh handle
        _make_pdf(pdf, pages=3)
        doc3 = ocr._worker_pdf_doc(str(pdf))
        assert doc3 is not doc1
        assert len(doc3) == 3
    finally:
        ocr._close_worker_doc()


# ---- P3: OS dark-mode startup color, i18n catalog parity, filing rules ----

def test_startup_background_color_matches_dark_mode(monkeypatch):
    monkeypatch.setattr(gui, "_os_prefers_dark", lambda: True)
    assert gui._startup_background_color() == "#0f1114"
    monkeypatch.setattr(gui, "_os_prefers_dark", lambda: False)
    assert gui._startup_background_color() == "#f6f6f3"


def test_locales_have_full_key_parity_and_cover_gui_html():
    root = Path(__file__).resolve().parent.parent / "pdfmanager"
    locales_dir = root / "locales"
    en = json.loads((locales_dir / "en.json").read_text("utf-8"))
    en_keys = set(en.keys())
    assert len(en_keys) >= 60

    for path in sorted(locales_dir.glob("*.json")):
        data = json.loads(path.read_text("utf-8"))
        assert set(data.keys()) == en_keys, f"{path.name} keys mismatch vs en.json"
        for k, v in data.items():
            assert isinstance(v, str) and v.strip(), f"Empty translation for {k} in {path.name}"

    html = (root / "gui.html").read_text("utf-8")
    referenced = set(re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', html))
    assert len(referenced) >= 40
    missing = referenced - en_keys
    assert not missing, f"gui.html references missing i18n keys: {sorted(missing)}"


def test_filing_rules_http_roundtrip(live_server, tmp_path):
    host, port, token, _ = live_server
    custom = [
        {"name": "Custom Invoices", "conditions": {"filename": "inv_*.pdf"}, "template": "{year}/Inv/{orig_name}.pdf"},
    ]
    status, data, _ = _req(
        host, port, token, "POST", "/api/filing/rules",
        json.dumps({"rules": custom}).encode("utf-8"),
    )
    assert status == 200
    assert json.loads(data)["rules"][0]["name"] == "Custom Invoices"

    status_get, data_get, _ = _req(host, port, token, "GET", "/api/filing/rules")
    assert status_get == 200
    got = json.loads(data_get)
    assert len(got["rules"]) == 1 and got["rules"][0]["name"] == "Custom Invoices"
    assert len(got["starters"]) >= 3

    pdf = _make_pdf(tmp_path / "inv_1001.pdf", pages=1)
    ctype, body = _multipart(files=[("file", "inv_1001.pdf", pdf.read_bytes(), "application/pdf")])
    status_prev, data_prev, _ = _req(host, port, token, "POST", "/api/filing/preview", body, ctype)
    assert status_prev == 200
    preview = json.loads(data_prev)
    assert preview["matched"] is True and preview["rule"] == "Custom Invoices"
    assert "inv_1001.pdf" in preview["target"]


def test_remove_legacy_nllb_pack_and_reject_traversal(live_server, tmp_path):
    from pdfmanager import core, i18n

    host, port, token, _ = live_server
    # Simulate an old pre-v0.63.2 NLLB-200 pack installed on disk
    legacy_dir = i18n.packs_dir() / "nllb-200-1.3b"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "manifest.json").write_text(
        json.dumps({"code": "nllb-200-1.3b", "name": "NLLB-200 1.3B (legacy)", "version": "1.0"}),
        encoding="utf-8",
    )
    (legacy_dir / "model.bin").write_bytes(b"fake-weights")

    installed_codes = [p["code"] for p in i18n.list_installed_packs()]
    assert "nllb-200-1.3b" in installed_codes

    # Remove via live HTTP endpoint
    status, data, _ = _req(
        host, port, token, "POST", "/api/i18n/packs/remove",
        json.dumps({"code": "nllb-200-1.3b"}).encode("utf-8"),
    )
    assert status == 200
    assert json.loads(data)["removed"] == "nllb-200-1.3b"
    assert not legacy_dir.exists()

    # Path traversal must still be rejected
    with pytest.raises(core.PdfManagerError):
        i18n.remove_pack("../outside")


def test_translate_language_options_cover_madlad():
    from pdfmanager import translate
    from pdfmanager.server import operations

    assert len(operations._TRANSLATE_LANGS) >= 80
    codes = [code for code, _ in operations._TRANSLATE_LANGS]
    assert {"th", "id", "sv", "el", "he", "fa", "ro", "hu", "sw", "bn", "ta", "tl"} <= set(codes)
    for code in codes:
        mapped = translate.bcp47_to_madlad(code)
        assert mapped, f"missing MADLAD mapping for {code}"


def test_translate_unload_route(live_server):
    host, port, token, _ = live_server
    status, data, _ = _req(host, port, token, "POST", "/api/translate/unload", b"{}")
    assert status == 200
    payload = json.loads(data)
    assert "unloaded" in payload and "freed_bytes" in payload


def test_static_gzip_compression(live_server):
    import gzip

    host, port, token, _ = live_server
    conn = http.client.HTTPConnection(host, port, timeout=10)
    conn.request("GET", "/static/app.css", headers={"Accept-Encoding": "gzip"})
    resp = conn.getresponse()
    body = resp.read()
    enc = resp.getheader("Content-Encoding")
    conn.close()
    assert resp.status == 200
    assert enc == "gzip"
    text = gzip.decompress(body).decode("utf-8")
    assert ".settings-dialog" in text


def test_ocr_reusable_pool_lifecycle():
    try:
        pool1, shared1 = ocr._acquire_ocr_pool(2)
        assert shared1 is True
        ocr._release_ocr_pool(pool1, shared1, broken=False)

        pool2, shared2 = ocr._acquire_ocr_pool(2)
        assert shared2 is True
        assert pool2 is pool1
        ocr._release_ocr_pool(pool2, shared2, broken=False)
    finally:
        ocr.shutdown_ocr_pool()
    assert ocr._OCR_POOL_STATE["pool"] is None


