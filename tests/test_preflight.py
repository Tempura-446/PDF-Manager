# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Priority #5 Phase 3: Print preflight engine, fixes, CLI, and HTTP API."""
import io
import json
import threading
import urllib.request
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader
from reportlab.lib.colors import CMYKColor, Color
from reportlab.pdfgen import canvas

from pdfmanager import cli, gui, preflight


def _image_pdf(path, px_w, px_h, draw_w_in=1.0, draw_h_in=1.0, mode="RGB"):
    img = Image.new(mode, (px_w, px_h), (120, 40, 200) if mode == "RGB" else (0, 100, 100, 0))
    img_path = path.with_suffix(".jpg" if mode == "CMYK" else ".png")
    img.save(img_path)
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.drawImage(str(img_path), 72, 500, width=draw_w_in * 72, height=draw_h_in * 72)
    c.showPage()
    c.save()
    return path


def test_low_dpi_vs_high_dpi_images(tmp_path):
    low = _image_pdf(tmp_path / "low72.pdf", 72, 72, draw_w_in=1.0, draw_h_in=1.0)
    res_low = preflight.check(low, min_dpi=300, error_dpi=150)
    assert res_low["passed"] is False
    low_issues = [i for i in res_low["issues"] if i["code"] == "low_dpi"]
    assert len(low_issues) == 1
    assert low_issues[0]["severity"] == "error"
    assert low_issues[0]["page"] == 1
    assert low_issues[0]["rect"] is not None

    high = _image_pdf(tmp_path / "high300.pdf", 300, 300, draw_w_in=1.0, draw_h_in=1.0)
    res_high = preflight.check(high, min_dpi=300, error_dpi=150)
    assert res_high["passed"] is True
    assert not [i for i in res_high["issues"] if i["code"] == "low_dpi"]


def test_unembedded_font_and_rgb_vs_cmyk_and_hairline(tmp_path):
    # Standard Helvetica in ReportLab is unembedded by default
    p = tmp_path / "print_issues.pdf"
    c = canvas.Canvas(str(p), pagesize=(612, 792))
    c.setFillColor(Color(0.8, 0.1, 0.1))  # DeviceRGB
    c.setFont("Helvetica", 10)
    c.drawString(72, 700, "Unembedded Helvetica in RGB")
    c.setLineWidth(0.1)  # Hairline < 0.25 pt
    c.line(72, 680, 300, 680)
    c.setFillColor(CMYKColor(0.7, 0.7, 0.7, 0.9))  # Rich black on 10pt text
    c.drawString(72, 650, "Rich black small text")
    c.showPage()
    c.save()

    res = preflight.check(p)
    codes = {i["code"] for i in res["issues"]}
    assert "unembedded_font" in codes
    assert "rgb_color" in codes
    assert "hairline_stroke" in codes
    assert "rich_black_text" in codes
    assert "DeviceRGB" in res["color_spaces"]
    assert "DeviceCMYK" in res["color_spaces"]
    assert res["passed"] is False  # unembedded font is an error


def test_mixed_page_sizes_and_normalize_and_bleed_fix(tmp_path):
    p = tmp_path / "mixed.pdf"
    c = canvas.Canvas(str(p))
    c.setPageSize((612, 792))  # Letter
    c.rect(50, 50, 100, 100, fill=1, stroke=0)
    c.showPage()
    c.setPageSize((595, 842))  # A4
    c.rect(50, 50, 100, 100, fill=1, stroke=0)
    c.showPage()
    c.setPageSize((612, 792))  # Letter (dominant)
    c.rect(50, 50, 100, 100, fill=1, stroke=0)
    c.showPage()
    c.save()

    res = preflight.check(p)
    assert any(i["code"] == "mixed_page_size" and i["page"] == 2 for i in res["issues"])

    fixed = tmp_path / "fixed.pdf"
    preflight.fix(p, fixed, normalize_size=True, add_bleed_mm=3.0, add_crop_marks=True)
    res_fixed = preflight.check(fixed)
    assert not [i for i in res_fixed["issues"] if i["code"] == "mixed_page_size"]
    assert not [i for i in res_fixed["issues"] if i["code"] == "missing_bleed"]
    # Verify TrimBox and BleedBox were written on all pages
    r = PdfReader(str(fixed))
    assert len(r.pages) == 3
    for page in r.pages:
        assert "/TrimBox" in page and "/BleedBox" in page
        assert float(page.mediabox.width) > 612.0


def test_cli_preflight_json_and_report(tmp_path, capsys):
    high = _image_pdf(tmp_path / "ok.pdf", 300, 300, draw_w_in=1.0, draw_h_in=1.0)
    rc = cli.main(["preflight", str(high), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["passed"] is True
    assert data["pages"] == 1

    low = _image_pdf(tmp_path / "bad.pdf", 72, 72, draw_w_in=1.0, draw_h_in=1.0)
    rc_bad = cli.main(["preflight", str(low)])
    assert rc_bad == 1
    text = capsys.readouterr().out
    assert "FAILED" in text and "72 DPI" in text


def test_http_preflight_check_endpoint(tmp_path):
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = url.split("#", 1)[0].rstrip("/")
        token = srv.RequestHandlerClass.token
        low = _image_pdf(tmp_path / "api_low.pdf", 72, 72, draw_w_in=1.0, draw_h_in=1.0)

        boundary = "----zpdfpreflight"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="api_low.pdf"\r\n'
            f"Content-Type: application/pdf\r\n\r\n"
        ).encode("utf-8") + low.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(
            base + "/api/preflight/check",
            data=body,
            headers={
                "X-Pdfm-Token": token,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        with urllib.request.urlopen(req) as res:
            assert res.status == 200
            payload = json.loads(res.read().decode("utf-8"))
        assert payload["passed"] is False
        assert any(i["code"] == "low_dpi" for i in payload["issues"])
    finally:
        srv.shutdown()
        srv.server_close()
