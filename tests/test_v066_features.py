# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""v0.66.0 TDD suite: interactive reader compare API, bilingual-pages outline remapping,
reader attachments update API, and multiprocessing core.batch_run."""
import json
import threading
import urllib.request
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from pdfmanager import compare, core, filetools, gui, translate


def _make_pdf(path: Path, pages_lines, bookmarks=None):
    raw = path.with_name(path.stem + "_raw.pdf")
    c = canvas.Canvas(str(raw), pagesize=(612, 792))
    for lines in pages_lines:
        y = 720
        for ln in lines:
            c.setFont("Helvetica", 12)
            c.drawString(72, y, ln)
            y -= 24
        c.showPage()
    c.save()
    if not bookmarks:
        raw.replace(path)
        return path
    reader = PdfReader(str(raw))
    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)
    for title, page_idx in bookmarks:
        writer.add_outline_item(title, page_idx)
    with open(path, "wb") as fh:
        writer.write(fh)
    raw.unlink(missing_ok=True)
    return path


def _multipart(fields=(), files=()):
    boundary = "----zpdfv066"
    parts = []
    for k, v in fields:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
        )
    for field_name, filename, content_type, data in files:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
            + data
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _square_worker(x):
    if x < 0:
        raise ValueError("negative not allowed")
    return x * x


def test_compare_summary_includes_dpi_and_reader_compare_endpoint(tmp_path):
    old_pdf = _make_pdf(tmp_path / "old.pdf", [["Page one hello"], ["Page two unchanged"]])
    new_pdf = _make_pdf(tmp_path / "new.pdf", [["Page one modified"], ["Page two unchanged"]])

    summary = compare.compare_summary(old_pdf, new_pdf, dpi=72)
    assert summary["dpi"] == 72
    assert summary["counts"]["changed"] == 1

    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = url.split("#", 1)[0].rstrip("/")
        token = srv.RequestHandlerClass.token
        body, ctype = _multipart(
            fields=[("mode", "both"), ("dpi", "72")],
            files=[
                ("file_old", "old.pdf", "application/pdf", old_pdf.read_bytes()),
                ("file_new", "new.pdf", "application/pdf", new_pdf.read_bytes()),
            ],
        )
        req = urllib.request.Request(
            base + "/api/reader/compare",
            data=body,
            headers={"X-Pdfm-Token": token, "Content-Type": ctype},
        )
        with urllib.request.urlopen(req) as res:
            assert res.status == 200
            data = json.loads(res.read().decode("utf-8"))
        assert data["dpi"] == 72
        assert data["old_pages"] == 2 and data["new_pages"] == 2
        assert data["counts"]["changed"] == 1
        assert any(r["kind"] == "changed" and r["old"] == 1 and r["new"] == 1 for r in data["changes"])
    finally:
        srv.shutdown()
        srv.server_close()


def test_bilingual_pages_preserves_remaps_and_translates_outlines(tmp_path):
    # Page 0: Cover (blank/no text -> emitted once)
    # Page 1: Chapter One (emitted as orig p1 + trans p2)
    # Page 2: Chapter Two (emitted as orig p3 + trans p4)
    src = _make_pdf(
        tmp_path / "book_toc.pdf",
        [[], ["Chapter One begins here."], ["Chapter Two continues here."]],
        bookmarks=[("Cover", 0), ("Chapter One", 1), ("Chapter Two", 2)],
    )
    out = tmp_path / "book_toc_bilingual.pdf"
    res = translate.translate_pdf(
        src,
        out,
        "en",
        "fr",
        mode="bilingual-pages",
        translator=lambda batch: [f"[FR:{s}]" for s in batch],
    )
    assert res["pages"] == 3
    assert res["bookmarks_translated"] == 3
    reader = PdfReader(str(out))
    # 1 cover + 2*(ch1) + 2*(ch2) = 5 pages
    assert len(reader.pages) == 5
    items = core.outline_items(reader)
    assert len(items) == 3
    titles = [it["title"] for it in items]
    pages = [it["page"] for it in items]
    # 1-based page numbers: Cover -> 1, Chapter One -> 2, Chapter Two -> 4
    assert pages == [1, 2, 4]
    assert all("[FR:" in t for t in titles)


def test_reader_attachments_update_endpoint_adds_and_removes(tmp_path):
    src = _make_pdf(tmp_path / "doc.pdf", [["Document with attachments"]])
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = url.split("#", 1)[0].rstrip("/")
        token = srv.RequestHandlerClass.token

        # 1. Add two attachments
        body, ctype = _multipart(
            fields=[("description", "supporting files")],
            files=[
                ("file", "doc.pdf", "application/pdf", src.read_bytes()),
                ("attach", "notes.txt", "text/plain", b"hello notes"),
                ("attach", "data.csv", "text/csv", b"a,b\n1,2\n"),
            ],
        )
        req = urllib.request.Request(
            base + "/api/attachments/update",
            data=body,
            headers={"X-Pdfm-Token": token, "Content-Type": ctype},
        )
        with urllib.request.urlopen(req) as res:
            assert res.status == 200
            pdf_with_att = res.read()
        p1 = tmp_path / "with_att.pdf"
        p1.write_bytes(pdf_with_att)
        names = [a["name"] for a in filetools.attachments(p1)]
        assert sorted(names) == ["data.csv", "notes.txt"]

        # 2. Remove one attachment
        body2, ctype2 = _multipart(
            fields=[("remove", json.dumps(["notes.txt"]))],
            files=[("file", "with_att.pdf", "application/pdf", pdf_with_att)],
        )
        req2 = urllib.request.Request(
            base + "/api/attachments/update",
            data=body2,
            headers={"X-Pdfm-Token": token, "Content-Type": ctype2},
        )
        with urllib.request.urlopen(req2) as res2:
            assert res2.status == 200
            pdf_after_rm = res2.read()
        p2 = tmp_path / "after_rm.pdf"
        p2.write_bytes(pdf_after_rm)
        assert [a["name"] for a in filetools.attachments(p2)] == ["data.csv"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_batch_run_process_mode_and_error_isolation():
    results = core.batch_run([2, -1, 4, 5], _square_worker, max_workers=2, mode="process")
    assert len(results) == 4
    assert results[0]["ok"] is True and results[0]["result"] == 4
    assert results[1]["ok"] is False and isinstance(results[1]["exception"], ValueError)
    assert results[2]["ok"] is True and results[2]["result"] == 16
    assert results[3]["ok"] is True and results[3]["result"] == 25
