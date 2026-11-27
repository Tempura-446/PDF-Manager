# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Regression tests for v0.88.0 deep 3-track audit fixes."""

import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest
from pypdf import PdfWriter

from pdfmanager import core, organize, redact
from pdfmanager.cli import common as cli_common
from test_more import app  # noqa: F401


def _make_pdf(path, pages=2):
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(300, 400)
    with open(path, "wb") as fh:
        w.write(fh)
    return path


def test_workspace_sessions_save_list_delete_and_reject_non_object_json(tmp_path, monkeypatch, app):
    from pdfmanager import sessions

    monkeypatch.setattr(sessions, "_sessions_path", lambda: tmp_path / "sessions.json")
    url, token, srv = app
    pdf = _make_pdf(tmp_path / "doc.pdf", pages=1)
    fid = srv.RequestHandlerClass.opened.add(pdf)

    # Save session
    req = urllib.request.Request(
        f"{url}/api/sessions",
        data=json.dumps({"name": "Audit Session", "tabs": [{"fileId": fid, "page": 1}]}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Pdfm-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        saved = json.loads(resp.read())["session"]
    assert saved["name"] == "Audit Session"

    # List sessions
    req2 = urllib.request.Request(f"{url}/api/sessions", headers={"X-Pdfm-Token": token})
    with urllib.request.urlopen(req2) as resp2:
        listed = json.loads(resp2.read())["sessions"]
    assert any(s["id"] == saved["id"] for s in listed)

    # Reject non-object JSON cleanly with 400
    bad_req = urllib.request.Request(
        f"{url}/api/sessions",
        data=b"[1, 2, 3]",
        headers={"Content-Type": "application/json", "X-Pdfm-Token": token},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(bad_req)
    assert exc_info.value.code == 400

    # Delete session
    del_req = urllib.request.Request(
        f"{url}/api/sessions/delete",
        data=json.dumps({"id": saved["id"]}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Pdfm-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(del_req) as resp3:
        assert json.loads(resp3.read())["deleted"] is True


def test_split_prefix_sanitizes_path_traversal(tmp_path):
    pdf = _make_pdf(tmp_path / "src.pdf", pages=2)
    out_dir = tmp_path / "out"
    written = core.split(pdf, out_dir, every=1, prefix="../../evil/escape")
    assert len(written) == 2
    out_resolved = out_dir.resolve()
    for p in written:
        assert out_resolved in Path(p).resolve().parents


def test_organize_format_name_blocks_attribute_traversal(tmp_path):
    record = {
        "path": tmp_path / "sample.pdf",
        "title": "Report",
        "author": "Alice",
        "pages": 3,
        "date": datetime(2026, 11, 27),
    }
    assert organize.format_name(record, "{date:%Y-%m-%d} {title}") == "2026-11-27 Report"
    with pytest.raises(core.PdfManagerError, match="unsupported field"):
        organize.format_name(record, "{date.__class__.__name__}")


def test_redact_iter_render_releases_pdfium_lock_before_yield(tmp_path):
    pdf = _make_pdf(tmp_path / "r.pdf", pages=1)
    gen = redact._iter_render_redacted(pdf, {0: [(10, 10, 50, 50)]}, dpi=72, password=None)
    number, img, _box = next(gen)
    try:
        assert number == 0
        # Lock must NOT be held across yield (otherwise callers using PDFium deadlock)
        acquired = core.PDFIUM_LOCK.acquire(blocking=False)
        assert acquired is True
        core.PDFIUM_LOCK.release()
    finally:
        img.close()
        gen.close()


def test_cli_keep_unencrypted_includes_to_dxf_and_sheet_split():
    assert "to-dxf" in cli_common.KEEP_UNENCRYPTED
    assert "sheet-split" in cli_common.KEEP_UNENCRYPTED


def test_frontend_v88_invariants():
    static = Path(__file__).resolve().parent.parent / "pdfmanager" / "static"
    assert "get currentPage()" in (static / "reader.js").read_text(encoding="utf-8")
    assert "/api/operations" in (static / "reader.js").read_text(encoding="utf-8")
    assert "message(msg)" in (static / "settings.js").read_text(encoding="utf-8")
    assert "display: flex" in (static / "reader.css").read_text(encoding="utf-8")
    assert "X-Pdfm-Token" in (static / "tabs.js").read_text(encoding="utf-8")
