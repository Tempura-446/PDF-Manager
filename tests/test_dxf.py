# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for PDF ↔ DXF conversion and optional DWG conversion via ODA."""

import math
import stat
import sys
import threading
import urllib.request
from pathlib import Path

import ezdxf
import pikepdf
from pikepdf import Array, Dictionary, Name
import pytest
from reportlab.lib.pagesizes import A3
from reportlab.pdfgen import canvas

from pdfmanager import cli, core, dxf, gui, layers, viewports

PT_PER_M = 72.0 / 0.0254


def _scaled_drawing_pdf(path: Path) -> Path:
    """Make a 1:100 metric drawing: 10 m × 5 m rectangle, 2 m radius circle,
    6 m diagonal line, and a text label."""
    c = canvas.Canvas(str(path), pagesize=A3)
    # At 1:100, 1 m = 10 mm = PT_PER_M / 100 pt.
    u = PT_PER_M / 100.0
    c.setStrokeColorRGB(0.1, 0.2, 0.8)
    c.setLineWidth(1.0)
    c.rect(50, 50, 10 * u, 5 * u, stroke=1, fill=0)
    c.circle(50 + 15 * u, 50 + 3 * u, 2 * u, stroke=1, fill=0)
    c.line(50, 50 + 8 * u, 50 + 6 * u, 50 + 8 * u)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 10)
    c.drawString(50, 50 + 10 * u, "LIVING ROOM")
    c.showPage()
    c.save()

    # Attach /VP /Measure for 1:100 in metres.
    with pikepdf.open(str(path), allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        mbox = [float(v) for v in page.mediabox]
        measure = Dictionary(
            Type=Name.Measure,
            Subtype=Name.RL,
            R="1:100",
            X=Array([Dictionary(Type=Name.NumberFormat, U="m", C=100.0 / PT_PER_M, D=100)]),
            D=Array([Dictionary(Type=Name.NumberFormat, U="m", C=1.0, D=100)]),
        )
        page.VP = Array([Dictionary(Type=Name.Viewport, Name="Plan", BBox=Array(mbox), Measure=measure)])
        pdf.save(str(path))
    return path


def test_pdf_to_dxf_entities_scale_and_text(tmp_path):
    pdf_path = _scaled_drawing_pdf(tmp_path / "plan.pdf")
    dxf_path = tmp_path / "plan.dxf"

    info = dxf.pdf_to_dxf(pdf_path, dxf_path)
    assert info["units"] == "m"
    assert info["entities"] >= 4

    doc = ezdxf.readfile(str(dxf_path))
    assert doc.header["$INSUNITS"] == 6  # metres
    msp = doc.modelspace()

    polys = list(msp.query("LWPOLYLINE"))
    assert len(polys) == 1
    pts = list(polys[0].get_points("xy"))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    assert abs((max(xs) - min(xs)) - 10.0) / 10.0 < 0.005
    assert abs((max(ys) - min(ys)) - 5.0) / 5.0 < 0.005

    circles = list(msp.query("CIRCLE"))
    assert len(circles) == 1
    assert abs(circles[0].dxf.radius - 2.0) / 2.0 < 0.005

    lines = list(msp.query("LINE"))
    assert len(lines) == 1
    seg_len = math.hypot(lines[0].dxf.end.x - lines[0].dxf.start.x, lines[0].dxf.end.y - lines[0].dxf.start.y)
    assert abs(seg_len - 6.0) / 6.0 < 0.005

    texts = list(msp.query("TEXT"))
    assert any("LIVING ROOM" in t.dxf.text for t in texts)


def test_dxf_to_pdf_layers_and_scale(tmp_path):
    dxf_path = tmp_path / "house.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6  # metres
    doc.layers.add("WALLS", color=1)
    doc.layers.add("TEXT", color=3)
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "WALLS"})
    msp.add_text("KITCHEN", dxfattribs={"layer": "TEXT", "height": 0.4, "insert": (2, 1)})
    doc.saveas(str(dxf_path))

    pdf_path = tmp_path / "house.pdf"
    made = dxf.dxf_to_pdf(dxf_path, pdf_path, paper="A3", scale="1:100")
    assert set(made["layers"]) == {"WALLS", "TEXT"}
    assert made["paper"] == "A3"
    assert made["scale"] == "1:100"

    # Layers are listed by ZPDF's layers module.
    linfo = layers.info(pdf_path)
    assert {l["name"] for l in linfo["layers"]} == {"WALLS", "TEXT"}

    # Stored /VP /Measure reports 1:100 in metres.
    scales = viewports.stored_scales(pdf_path)
    assert 1 in scales and scales[1]
    assert scales[1][0]["units"] == "m"
    assert abs(scales[1][0]["ratio"] - 100.0) < 0.5

    # Round-trip PDF → DXF preserves the 10 m wall and both layers!
    rt_dxf = tmp_path / "roundtrip.dxf"
    dxf.pdf_to_dxf(pdf_path, rt_dxf)
    rt = ezdxf.readfile(str(rt_dxf))
    rt_lines = list(rt.modelspace().query("LINE"))
    assert len(rt_lines) == 1
    rt_len = math.hypot(rt_lines[0].dxf.end.x - rt_lines[0].dxf.start.x, rt_lines[0].dxf.end.y - rt_lines[0].dxf.start.y)
    assert abs(rt_len - 10.0) / 10.0 < 0.005
    assert rt_lines[0].dxf.layer == "WALLS"
    rt_texts = list(rt.modelspace().query("TEXT"))
    assert any("KITCHEN" in t.dxf.text and t.dxf.layer == "TEXT" for t in rt_texts)


def test_dwg_without_and_with_stub_oda_converter(tmp_path, monkeypatch):
    dwg_file = tmp_path / "sample.dwg"
    dwg_file.write_bytes(b"AC1027-fake-dwg")

    # Without ODA converter installed: clear message.
    monkeypatch.setattr(dxf, "find_oda_converter", lambda custom_path=None: None)
    with pytest.raises(core.PdfManagerError, match="ODA File Converter"):
        dxf.drawing_to_pdf(dwg_file, tmp_path / "out.pdf")

    # With a faked ODA converter stub that copies a real DXF into out_dir.
    template_dxf = tmp_path / "template.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    doc.layers.add("WALLS")
    doc.modelspace().add_line((0, 0), (5, 0), dxfattribs={"layer": "WALLS"})
    doc.saveas(str(template_dxf))

    stub = tmp_path / ("ODAFileConverter.bat" if sys.platform == "win32" else "ODAFileConverter")
    if sys.platform == "win32":
        stub.write_text(f'@echo off\ncopy "{template_dxf}" "%~2\\sample.dxf" >nul\n', encoding="utf-8")
    else:
        stub.write_text(f'#!/bin/sh\ncp "{template_dxf}" "$2/sample.dxf"\n', encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setattr(dxf, "find_oda_converter", lambda custom_path=None: stub)
    out_pdf = tmp_path / "from_dwg.pdf"
    made = dxf.drawing_to_pdf(dwg_file, out_pdf, paper="A3", scale="1:50")
    assert out_pdf.exists()
    assert "WALLS" in made["layers"]
    assert abs(viewports.stored_scales(out_pdf)[1][0]["ratio"] - 50.0) < 0.5


def test_cli_to_dxf_and_from_dxf_and_open_endpoint(tmp_path):
    pdf_in = _scaled_drawing_pdf(tmp_path / "cli_plan.pdf")
    dxf_out = tmp_path / "cli_plan.dxf"
    assert cli.main(["to-dxf", str(pdf_in), "-o", str(dxf_out)]) == 0
    assert dxf_out.exists()

    pdf_out = tmp_path / "cli_back.pdf"
    assert cli.main(["from-dxf", str(dxf_out), "-o", str(pdf_out), "--paper", "A3", "--scale", "1:100"]) == 0
    assert pdf_out.exists()

    # Reader /api/drawing/open converts uploaded .dxf to PDF bytes.
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base, token = url.split("/#token=")
        req = urllib.request.Request(
            base + "/api/drawing/open",
            data=dxf_out.read_bytes(),
            headers={"X-Pdfm-Token": token, "X-Pdfm-Filename": "cli_plan.dxf"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as res:
            assert res.status == 200
            body = res.read()
            assert body.startswith(b"%PDF-")
    finally:
        srv.shutdown()
        srv.server_close()
