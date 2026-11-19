# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Priority #6 Phase 2: Watch-folder auto-filing integration and filing HTTP API."""
import json
import threading
import urllib.request
from pathlib import Path

from reportlab.pdfgen import canvas

from pdfmanager import appdata, filing, gui, watchfolders


def _make_invoice_pdf(path, invoice_no="INV-1042", date_str="2026-11-18"):
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.drawString(72, 720, "ACME CORPORATION")
    c.drawString(72, 700, f"Invoice # {invoice_no}")
    c.drawString(72, 680, f"Date: {date_str}")
    c.showPage()
    c.save()
    return path


def test_watch_folder_routes_processed_pdf_by_filing_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(watchfolders, "data_dir", lambda: tmp_path / "appdata")
    (tmp_path / "appdata").mkdir(parents=True, exist_ok=True)

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    src = _make_invoice_pdf(inbox / "scan001.pdf", "INV-1042", "2026-11-18")
    done_dir = inbox / "Done"
    done_dir.mkdir()
    processed = _make_invoice_pdf(done_dir / "scan001.pdf", "INV-1042", "2026-11-18")

    wf = watchfolders.WatchFolders(thread=False)
    entry = wf.add(str(inbox), {"tool": "compress"}, results="done", originals="keep", auto_file=True)
    assert entry.get("auto_file") is True
    assert "auto-filed by rules" in watchfolders.describe(entry)

    wf._report(entry, src, processed, error=None, files=1)
    expected = done_dir / "2026" / "Invoices" / "2026-11-18_INV-1042.pdf"
    assert expected.is_file()
    assert not processed.exists()
    news, _ = wf.news_after(0)
    assert news and news[-1]["ok"] is True and news[-1]["result"] == str(expected)


def test_filing_rules_and_preview_http_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    (tmp_path / "appdata").mkdir(parents=True, exist_ok=True)

    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = url.split("#", 1)[0].rstrip("/")
        token = srv.RequestHandlerClass.token

        # GET /api/filing/rules returns starter presets
        req = urllib.request.Request(base + "/api/filing/rules", headers={"X-Pdfm-Token": token})
        with urllib.request.urlopen(req) as res:
            assert res.status == 200
            data = json.loads(res.read().decode("utf-8"))
        assert len(data["starters"]) >= 3
        assert len(data["rules"]) >= 3

        # POST /api/filing/rules saves custom rule
        custom = [{
            "name": "Acme Invoices",
            "conditions": {"regex": r"Invoice\s*#\s*(?P<inv>[A-Z0-9-]+)", "has_date": True},
            "template": "{year}/Acme/{date}_{inv}.pdf",
        }]
        req_save = urllib.request.Request(
            base + "/api/filing/rules",
            data=json.dumps({"rules": custom}).encode("utf-8"),
            headers={"X-Pdfm-Token": token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req_save) as res:
            assert res.status == 200
            saved = json.loads(res.read().decode("utf-8"))
        assert len(saved["rules"]) == 1 and saved["rules"][0]["name"] == "Acme Invoices"

        # POST /api/filing/preview evaluates sample PDF against saved rules
        sample = _make_invoice_pdf(tmp_path / "sample.pdf", "INV-909", "2026-11-19")
        boundary = "----zpdffiling"
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.pdf"\r\n'
            f"Content-Type: application/pdf\r\n\r\n".encode("utf-8")
            + sample.read_bytes()
            + f"\r\n--{boundary}--\r\n".encode("utf-8")
        )
        req_prev = urllib.request.Request(
            base + "/api/filing/preview",
            data=body,
            headers={"X-Pdfm-Token": token, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req_prev) as res:
            assert res.status == 200
            preview = json.loads(res.read().decode("utf-8"))
        assert preview["matched"] is True
        assert preview["rule"] == "Acme Invoices"
        assert preview["target"] == "2026/Acme/2026-11-19_INV-909.pdf"
    finally:
        srv.shutdown()
        srv.server_close()
