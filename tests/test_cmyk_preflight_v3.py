# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""TDD tests for Print-Production CMYK Preflight v3 (v0.70.0)."""

import base64
import io
import json
import threading
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image
from pypdf import PageObject, PdfWriter
from pypdf.generic import (
    ArrayObject, DecodedStreamObject, DictionaryObject,
    FloatObject, NameObject, NumberObject,
)

from pdfmanager import gui, preflight


def _make_form_xobject_pdf(path: Path):
    """Build a PDF whose page draws ONLY a Form XObject (/Fm0 Do) that contains
    a 355% TAC rich-CMYK fill, a 0.08pt hairline stroke, and an RGB accent."""
    writer = PdfWriter()
    page = PageObject.create_blank_page(width=200.0, height=200.0)

    form_stream = DecodedStreamObject()
    form_stream.set_data(
        b"q 0.90 0.90 0.85 0.90 k 20 20 120 120 re f Q\n"
        b"q 0.08 w 0 0 0 1 K 10 10 m 190 10 l S Q\n"
        b"q 0.8 0.1 0.1 rg 150 20 30 30 re f Q\n"
    )
    form_stream[NameObject("/Type")] = NameObject("/XObject")
    form_stream[NameObject("/Subtype")] = NameObject("/Form")
    form_stream[NameObject("/BBox")] = ArrayObject([FloatObject(0), FloatObject(0), FloatObject(200), FloatObject(200)])
    form_stream[NameObject("/Resources")] = DictionaryObject()
    fm_ref = writer._add_object(form_stream)

    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Fm0"): fm_ref}),
    })
    content = DecodedStreamObject()
    content.set_data(b"q /Fm0 Do Q\n")
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.add_page(page)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def _make_pure_python_fixes_pdf(path: Path):
    """Build a PDF with rich-black 10pt text, 355% CMYK vector fill, 368% CMYK image,
    0.08pt hairline stroke, and RGB vector + RGB image (all fixable in pure Python)."""
    writer = PdfWriter()
    page = PageObject.create_blank_page(width=200.0, height=200.0)

    # Embedded-like Type1 font
    fdict = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica-Bold"),
        NameObject("/FontDescriptor"): writer._add_object(DictionaryObject({
            NameObject("/FontFile"): writer._add_object(DecodedStreamObject()),
        })),
    })

    # Hot CMYK image (240/240/220/240 = ~368% TAC)
    cmyk_img = DecodedStreamObject()
    cmyk_img.set_data(bytes([240, 240, 220, 240]) * (10 * 10))
    cmyk_img[NameObject("/Type")] = NameObject("/XObject")
    cmyk_img[NameObject("/Subtype")] = NameObject("/Image")
    cmyk_img[NameObject("/Width")] = NumberObject(10)
    cmyk_img[NameObject("/Height")] = NumberObject(10)
    cmyk_img[NameObject("/ColorSpace")] = NameObject("/DeviceCMYK")
    cmyk_img[NameObject("/BitsPerComponent")] = NumberObject(8)

    # RGB image (200, 40, 20)
    rgb_img = DecodedStreamObject()
    rgb_img.set_data(bytes([200, 40, 20]) * (10 * 10))
    rgb_img[NameObject("/Type")] = NameObject("/XObject")
    rgb_img[NameObject("/Subtype")] = NameObject("/Image")
    rgb_img[NameObject("/Width")] = NumberObject(10)
    rgb_img[NameObject("/Height")] = NumberObject(10)
    rgb_img[NameObject("/ColorSpace")] = NameObject("/DeviceRGB")
    rgb_img[NameObject("/BitsPerComponent")] = NumberObject(8)

    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(fdict)}),
        NameObject("/XObject"): DictionaryObject({
            NameObject("/ImC"): writer._add_object(cmyk_img),
            NameObject("/ImR"): writer._add_object(rgb_img),
        }),
    })
    content = DecodedStreamObject()
    content.set_data(
        b"q 0.90 0.90 0.85 0.90 k 10 10 60 60 re f Q\n"
        b"q 0.08 w 0 0 0 1 K 10 80 m 190 80 l S Q\n"
        b"q 0.8 0.2 0.1 rg 80 10 40 40 re f Q\n"
        b"q 2 0 0 2 140 10 cm /ImC Do Q\n"
        b"q 2 0 0 2 160 10 cm /ImR Do Q\n"
        b"q BT /F1 10 Tf 0.7 0.7 0.7 0.9 k 20 140 Td (Small rich black) Tj ET Q\n"
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.add_page(page)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def _make_spot_and_registration_pdf(path: Path):
    """Build a PDF with 3 named spot inks AND /Separation /All registration ink."""
    writer = PdfWriter()
    page = PageObject.create_blank_page(width=200.0, height=200.0)
    # Minimal TintTransform function dict (Type 2 exponential)
    fn = writer._add_object(DictionaryObject({
        NameObject("/FunctionType"): NumberObject(2),
        NameObject("/Domain"): ArrayObject([FloatObject(0), FloatObject(1)]),
        NameObject("/C0"): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(0), FloatObject(0)]),
        NameObject("/C1"): ArrayObject([FloatObject(1), FloatObject(1), FloatObject(1), FloatObject(1)]),
        NameObject("/N"): FloatObject(1),
    }))
    cs_dict = DictionaryObject({
        NameObject("/CS1"): ArrayObject([NameObject("/Separation"), NameObject("/PANTONE_186_C"), NameObject("/DeviceCMYK"), fn]),
        NameObject("/CS2"): ArrayObject([NameObject("/Separation"), NameObject("/PANTONE_286_C"), NameObject("/DeviceCMYK"), fn]),
        NameObject("/CS3"): ArrayObject([NameObject("/Separation"), NameObject("/CutContour"), NameObject("/DeviceCMYK"), fn]),
        NameObject("/CSAll"): ArrayObject([NameObject("/Separation"), NameObject("/All"), NameObject("/DeviceCMYK"), fn]),
    })
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/ColorSpace"): cs_dict})
    content = DecodedStreamObject()
    content.set_data(b"q /CSAll cs 1 scn 20 20 80 80 re f Q\n")
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.add_page(page)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def test_recursive_form_xobject_check_and_plate_separation(tmp_path):
    p = _make_form_xobject_pdf(tmp_path / "form_xobj.pdf")
    res = preflight.check(p, max_tac=300.0)
    codes = {i["code"] for i in res["issues"]}
    assert "high_tac" in codes
    assert "hairline_stroke" in codes
    assert "rgb_color" in codes
    assert res["max_tac_pct"] >= 350.0

    # Plate separation must also separate inside the nested Form XObject!
    cyan_png, _ = preflight.render_plate_preview(p, page=1, plate="c", dpi=72)
    cyan_arr = np.asarray(Image.open(io.BytesIO(cyan_png)).convert("RGB"))
    # Center of the 20..140 rich-CMYK rect inside Form XObject -> (x=80, y_from_top=120) must show strong cyan (R < 80)
    assert cyan_arr[120, 80, 0] < 80


def test_pure_python_cmyk_auto_fixes_without_ghostscript(tmp_path):
    p = _make_pure_python_fixes_pdf(tmp_path / "needs_fixes.pdf")
    before = preflight.check(p, max_tac=280.0)
    before_codes = {i["code"] for i in before["issues"]}
    assert {"high_tac", "hairline_stroke", "rgb_color", "rich_black_text"} <= before_codes

    fixed = tmp_path / "pure_py_fixed.pdf"
    preflight.fix(
        p,
        fixed,
        fix_rich_black_text=True,
        clamp_tac=280.0,
        fix_hairlines=0.25,
        convert_rgb_to_cmyk=True,
        add_pdfx_intent=True,
    )
    after = preflight.check(fixed, max_tac=280.0, profile="pdfx-4")
    after_codes = {i["code"] for i in after["issues"]}
    assert "rich_black_text" not in after_codes
    assert "hairline_stroke" not in after_codes
    assert "rgb_color" not in after_codes
    assert "high_tac" not in after_codes
    assert after["max_tac_pct"] <= 281.0
    assert after["passed"] is True


def test_spot_ink_inventory_and_registration_color_hazard(tmp_path):
    p = _make_spot_and_registration_pdf(tmp_path / "spot_reg.pdf")
    res = preflight.check(p)
    assert set(res["spot_colors"]) == {"PANTONE_186_C", "PANTONE_286_C", "CutContour"}
    codes = {i["code"] for i in res["issues"]}
    assert "many_spot_colors" in codes
    assert "registration_color" in codes
    assert res["passed"] is False  # registration_color inside page is an error


def test_compute_ink_grid_and_http_inkmap_endpoint(tmp_path):
    writer = PdfWriter()
    page = PageObject.create_blank_page(width=200.0, height=200.0)
    stream = DecodedStreamObject()
    stream.set_data(
        b"q 0 0 0 1 k 20 60 60 80 re f Q\n"
        b"q 0.90 0.90 0.85 0.90 k 120 60 60 80 re f Q\n"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_page(page)
    p = tmp_path / "eyedropper.pdf"
    with open(p, "wb") as fh:
        writer.write(fh)

    grid = preflight.compute_ink_grid(p, page=1, dpi=72)
    raw = base64.b64decode(grid["cmyk_b64"])
    arr = np.frombuffer(raw, dtype=np.uint8).reshape((grid["height"], grid["width"], 4))
    # Left rect (pure K): x=50, y=100 -> C=0, M=0, Y=0, K>=95
    c_l, m_l, y_l, k_l = (int(v) for v in arr[100, 50])
    assert c_l <= 5 and m_l <= 5 and y_l <= 5 and k_l >= 95
    # Right rect (355% rich CMYK): x=150, y=100 -> C+M+Y+K >= 340
    c_r, m_r, y_r, k_r = (int(v) for v in arr[100, 150])
    assert c_r + m_r + y_r + k_r >= 340

    # Verify HTTP POST /api/preflight/inkmap endpoint
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = url.split("#", 1)[0].rstrip("/")
        token = srv.RequestHandlerClass.token
        boundary = "----zpdfinkmap"
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"page\"\r\n\r\n1\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"eye.pdf\"\r\n"
            f"Content-Type: application/pdf\r\n\r\n"
        ).encode("utf-8") + p.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(
            base + "/api/preflight/inkmap",
            data=body,
            headers={"X-Pdfm-Token": token, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload["width"] > 0 and payload["height"] > 0 and payload["cmyk_b64"]
    finally:
        srv.shutdown()
        srv.server_close()
