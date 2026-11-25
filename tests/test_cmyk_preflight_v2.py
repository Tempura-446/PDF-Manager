# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""TDD tests for Print-Production CMYK Preflight v2 (v0.69.0)."""

import io
import json
import threading
import urllib.request
import zlib
from pathlib import Path

import numpy as np
from PIL import Image
from pypdf import PageObject, PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject,
    FloatObject, NameObject, NumberObject,
)

from pdfmanager import cli, gui, preflight


def _make_raw_cmyk_pdf(path: Path, *, ops_bytes: bytes, extgstate=None, cmyk_img_bytes=None, img_size=(20, 20), transparency=False):
    """Build a deterministic single-page CMYK PDF with custom content stream,
    optional ExtGState dict, and optional raw DeviceCMYK Image XObject."""
    writer = PdfWriter()
    page = PageObject.create_blank_page(width=200.0, height=200.0)
    res = DictionaryObject()

    if extgstate:
        gs_dict = DictionaryObject()
        for k, v in extgstate.items():
            d = DictionaryObject()
            for gk, gv in v.items():
                d[NameObject(gk)] = BooleanObject(gv) if isinstance(gv, bool) else NumberObject(gv)
            gs_dict[NameObject(k)] = writer._add_object(d)
        res[NameObject("/ExtGState")] = gs_dict

    if cmyk_img_bytes is not None:
        img_stream = DecodedStreamObject()
        img_stream.set_data(cmyk_img_bytes)
        img_stream[NameObject("/Type")] = NameObject("/XObject")
        img_stream[NameObject("/Subtype")] = NameObject("/Image")
        img_stream[NameObject("/Width")] = NumberObject(img_size[0])
        img_stream[NameObject("/Height")] = NumberObject(img_size[1])
        img_stream[NameObject("/ColorSpace")] = NameObject("/DeviceCMYK")
        img_stream[NameObject("/BitsPerComponent")] = NumberObject(8)
        xobjs = DictionaryObject()
        xobjs[NameObject("/Im0")] = writer._add_object(img_stream)
        res[NameObject("/XObject")] = xobjs

    if transparency:
        grp = DictionaryObject({
            NameObject("/Type"): NameObject("/Group"),
            NameObject("/S"): NameObject("/Transparency"),
        })
        page[NameObject("/Group")] = grp

    # Minimal embedded-like Type1 or vector-only resources; add Base14 only when Tf is used
    if b"Tf" in ops_bytes:
        fdict = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica-Bold"),
            NameObject("/FontDescriptor"): writer._add_object(DictionaryObject({
                NameObject("/FontFile"): writer._add_object(DecodedStreamObject()),
            })),
        })
        res[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): writer._add_object(fdict)})

    page[NameObject("/Resources")] = res
    stream = DecodedStreamObject()
    stream.set_data(ops_bytes)
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_page(page)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def test_total_ink_coverage_vector_and_cmyk_image(tmp_path):
    # 1. Vector CMYK fill with 90+90+85+90 = 355% TAC (> 300%)
    vec_hot = _make_raw_cmyk_pdf(
        tmp_path / "vec_hot.pdf",
        ops_bytes=b"q 0.90 0.90 0.85 0.90 k 20 20 120 120 re f Q\n",
    )
    res_vec = preflight.check(vec_hot, max_tac=300.0)
    assert res_vec["max_tac_pct"] >= 350.0
    assert any(i["code"] == "high_tac" for i in res_vec["issues"])

    # 2. CMYK Image XObject with (240, 240, 220, 240)/255 = ~368% TAC drawn at high DPI (20px @ 3.6pt = 400 DPI)
    px = bytes([240, 240, 220, 240]) * (20 * 20)
    img_hot = _make_raw_cmyk_pdf(
        tmp_path / "img_hot.pdf",
        ops_bytes=b"q 3.6 0 0 3.6 40 40 cm /Im0 Do Q\n",
        cmyk_img_bytes=px,
        img_size=(20, 20),
    )
    res_img = preflight.check(img_hot, max_tac=300.0)
    assert res_img["max_tac_pct"] >= 360.0
    tac_issues = [i for i in res_img["issues"] if i["code"] == "high_tac"]
    assert tac_issues and tac_issues[0]["rect"] is not None

    # 3. Clean CMYK fill (50+40+30+80 = 200% <= 300%) passes with zero high_tac issues
    vec_ok = _make_raw_cmyk_pdf(
        tmp_path / "vec_ok.pdf",
        ops_bytes=b"q 0.50 0.40 0.30 0.80 k 20 20 120 120 re f Q\n",
    )
    res_ok = preflight.check(vec_ok, max_tac=300.0)
    assert res_ok["max_tac_pct"] <= 205.0
    assert not [i for i in res_ok["issues"] if i["code"] == "high_tac"]


def test_white_overprint_and_black_knockout_audit_and_fix(tmp_path):
    p = _make_raw_cmyk_pdf(
        tmp_path / "overprint_bug.pdf",
        ops_bytes=(
            b"q /GS1 gs 0 0 0 0 k 30 30 80 80 re f Q\n"
            b"q /GS2 gs BT /F1 10 Tf 0 0 0 1 k 30 140 Td (Body text) Tj ET Q\n"
        ),
        extgstate={
            "/GS1": {"/OP": True, "/op": True},
            "/GS2": {"/OP": False, "/op": False},
        },
    )
    res = preflight.check(p)
    codes = {i["code"] for i in res["issues"]}
    assert "white_overprint" in codes
    assert "black_knockout_text" in codes
    assert res["passed"] is False  # white_overprint is a press-stopping error!

    # One-click fix neutralizes white overprint so the error disappears
    fixed = tmp_path / "overprint_fixed.pdf"
    preflight.fix(p, fixed, fix_white_overprint=True)
    res_fixed = preflight.check(fixed)
    assert not [i for i in res_fixed["issues"] if i["code"] == "white_overprint"]
    assert res_fixed["passed"] is True


def test_pdfx_1a_and_pdfx_4_compliance_and_output_intent_fix(tmp_path):
    # Raw CMYK PDF with transparency, no OutputIntents, no TrimBox, no Trapped key
    raw = _make_raw_cmyk_pdf(
        tmp_path / "raw_cmyk.pdf",
        ops_bytes=b"q 0.2 0.1 0.0 0.8 k 20 20 100 100 re f Q\n",
        transparency=True,
    )
    res_x1a = preflight.check(raw, profile="pdfx-1a")
    x1a_codes = {i["code"] for i in res_x1a["issues"]}
    assert res_x1a["passed"] is False
    assert "missing_output_intent" in x1a_codes
    assert "missing_trapped_key" in x1a_codes
    assert "missing_trim_or_art_box" in x1a_codes
    # In PDF/X-1a, live transparency is a hard error
    trans_issue = next(i for i in res_x1a["issues"] if i["code"] == "transparency")
    assert trans_issue["severity"] == "error"

    # Apply add_pdfx_intent=True: adds GTS_PDFX OutputIntent, /Trapped /False, and /TrimBox
    x4_ready = tmp_path / "x4_ready.pdf"
    preflight.fix(raw, x4_ready, add_pdfx_intent=True)

    # Under PDF/X-4, transparency IS allowed once OutputIntent + TrimBox + Trapped are set!
    res_x4 = preflight.check(x4_ready, profile="pdfx-4")
    assert res_x4["passed"] is True
    assert not ({"missing_output_intent", "missing_trapped_key", "missing_trim_or_art_box"} & {i["code"] for i in res_x4["issues"]})


def test_render_plate_preview_true_cmyk_separation_and_tac_heatmap(tmp_path):
    # Left rectangle: pure K (0, 0, 0, 1) at x=20..80, y=60..140
    # Right rectangle: hot rich CMYK (0.9, 0.9, 0.85, 0.9 = 355% TAC) at x=120..180, y=60..140
    p = _make_raw_cmyk_pdf(
        tmp_path / "plates.pdf",
        ops_bytes=(
            b"q 0 0 0 1 k 20 60 60 80 re f Q\n"
            b"q 0.90 0.90 0.85 0.90 k 120 60 60 80 re f Q\n"
        ),
    )
    cyan_png, _ = preflight.render_plate_preview(p, page=1, plate="c", dpi=72)
    black_png, _ = preflight.render_plate_preview(p, page=1, plate="k", dpi=72)
    tac_png, tac_meta = preflight.render_plate_preview(p, page=1, plate="tac", max_tac=300.0, dpi=72)

    cyan_arr = np.asarray(Image.open(io.BytesIO(cyan_png)).convert("RGB"))
    black_arr = np.asarray(Image.open(io.BytesIO(black_png)).convert("RGB"))
    tac_arr = np.asarray(Image.open(io.BytesIO(tac_png)).convert("RGB"))

    # Sample center of left rect (x=50, y_from_top=100) and right rect (x=150, y_from_top=100)
    # On Cyan plate: pure-K left rect must stay blank white (R > 240), while right rect has strong cyan (R < 80)
    assert cyan_arr[100, 50, 0] > 240
    assert cyan_arr[100, 150, 0] < 80

    # On Black (K) plate: BOTH pure-K left rect and rich-black right rect have dark K ink
    assert black_arr[100, 50].mean() < 40
    assert black_arr[100, 150].mean() < 50

    # On TAC heatmap: right rect (355% > 300%) lights up vivid red (R > 200, G < 90), left rect (100%) stays neutral
    assert tac_meta["max_tac_pct"] >= 340.0
    assert tac_arr[100, 150, 0] > 200 and tac_arr[100, 150, 1] < 90
    assert abs(int(tac_arr[100, 50, 0]) - int(tac_arr[100, 50, 1])) < 15


def test_cli_and_http_preflight_v2_routes(tmp_path, capsys):
    hot = _make_raw_cmyk_pdf(
        tmp_path / "cli_hot.pdf",
        ops_bytes=b"q 0.90 0.90 0.85 0.90 k 20 60 120 80 re f Q\n",
    )
    rc = cli.main(["preflight", str(hot), "--profile", "pdfx-1a", "--max-tac", "280", "--json"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["profile"] == "pdfx-1a"
    assert payload["max_tac_pct"] >= 350.0

    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = url.split("#", 1)[0].rstrip("/")
        token = srv.RequestHandlerClass.token
        boundary = "----zpdfpfv2"

        def _multipart(fields, file_bytes):
            chunks = []
            for k, v in fields.items():
                chunks.append(
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8")
                )
            chunks.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"hot.pdf\"\r\n"
                f"Content-Type: application/pdf\r\n\r\n".encode("utf-8")
                + file_bytes
                + f"\r\n--{boundary}--\r\n".encode("utf-8")
            )
            return b"".join(chunks)

        req = urllib.request.Request(
            base + "/api/preflight/preview",
            data=_multipart({"page": "1", "plate": "tac", "max_tac": "300"}, hot.read_bytes()),
            headers={
                "X-Pdfm-Token": token,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type") == "image/png"
            png_bytes = resp.read()
        img = Image.open(io.BytesIO(png_bytes))
        assert img.size[0] > 50 and img.size[1] > 50
    finally:
        srv.shutdown()
        srv.server_close()
