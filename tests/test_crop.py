# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Priority #5 Phase 2: Interactive visual crop (rotated pages, scopes, permanent trim) and Booklet live preview."""
import json
import threading
import urllib.request
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from pdfmanager import crop, gui
from pdfmanager.server import operations

STATIC = Path(gui.__file__).resolve().parent / "static"


def _make_rotated_pdf(path, rotations=(0, 90, 180, 270)):
    base = path.with_name(path.stem + "_raw.pdf")
    c = canvas.Canvas(str(base), pagesize=(400, 600))
    for i, rot in enumerate(rotations, start=1):
        c.drawString(50, 500, f"Page {i} rot={rot}")
        c.showPage()
    c.save()
    reader = PdfReader(str(base))
    writer = PdfWriter()
    for page, rot in zip(reader.pages, rotations):
        p = writer.add_page(page)
        if rot:
            p.rotate(rot)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def test_visual_to_media_rect_across_rotations():
    mb = (0, 0, 400, 600)
    # 0 deg: direct mapping
    assert crop.visual_to_media_rect((50, 100, 250, 400), mb, 0) == (50.0, 100.0, 250.0, 400.0)
    # 90 deg CW: visual size is 600x400; bottom-left visual (50, 20) -> media x=400-20=380, y=50
    r90 = crop.visual_to_media_rect((50, 20, 250, 120), mb, 90)
    assert r90 == (280.0, 50.0, 380.0, 250.0)
    # 180 deg: flipped both axes
    r180 = crop.visual_to_media_rect((50, 100, 250, 400), mb, 180)
    assert r180 == (150.0, 200.0, 350.0, 500.0)
    # 270 deg CW: visual size is 600x400
    r270 = crop.visual_to_media_rect((50, 20, 250, 120), mb, 270)
    assert r270 == (20.0, 350.0, 120.0, 550.0)


def test_crop_pages_odd_even_and_permanent_trim(tmp_path):
    src = _make_rotated_pdf(tmp_path / "rot4.pdf", (0, 90, 180, 270))

    # Non-destructive crop on odd pages only
    out_odd = tmp_path / "odd.pdf"
    cropped = crop.crop_pages(src, out_odd, [40, 60, 240, 360], scope="odd")
    assert cropped == [1, 3]
    r_odd = PdfReader(str(out_odd))
    assert float(r_odd.pages[0].cropbox.width) == pytest.approx(200.0, abs=0.5)
    assert float(r_odd.pages[0].mediabox.width) == pytest.approx(400.0, abs=0.5)
    assert float(r_odd.pages[1].cropbox.width) == pytest.approx(400.0, abs=0.5)  # page 2 untouched
    assert float(r_odd.pages[2].cropbox.width) == pytest.approx(200.0, abs=0.5)

    # Permanent trim on page 2 (rotated 90 deg)
    out_trim = tmp_path / "trim.pdf"
    cropped2 = crop.crop_pages(src, out_trim, [50, 20, 250, 120], scope="page", page_num=2, trim_permanently=True)
    assert cropped2 == [2]
    r_trim = PdfReader(str(out_trim))
    p2 = r_trim.pages[1]
    assert float(p2.mediabox.width) == pytest.approx(100.0, abs=0.5)
    assert float(p2.mediabox.height) == pytest.approx(200.0, abs=0.5)
    assert b"re W n" in p2.get_contents().get_data()


def test_http_crop_endpoint_and_booklet_live_preview(tmp_path):
    assert "booklet" in operations.PREVIEW_OPS
    booklet_op = next(o for o in operations.OPERATIONS if o["id"] == "booklet")
    assert booklet_op.get("live") is True

    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = url.split("#", 1)[0].rstrip("/")
        token = srv.RequestHandlerClass.token
        src = _make_rotated_pdf(tmp_path / "api_crop.pdf", (0, 0))

        # Verify cropmode.js served cleanly
        with urllib.request.urlopen(base + "/static/cropmode.js") as res:
            assert res.status == 200
            js = res.read().decode("utf-8")
        assert "export const CropMode" in js

        # POST /api/crop
        boundary = "----zpdfcrop"
        parts = []
        for k, v in [("rect", json.dumps([40, 40, 240, 340])), ("scope", "all")]:
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
            )
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="api_crop.pdf"\r\n'
            f"Content-Type: application/pdf\r\n\r\n".encode("utf-8")
            + src.read_bytes()
            + f"\r\n--{boundary}--\r\n".encode("utf-8")
        )
        req = urllib.request.Request(
            base + "/api/crop",
            data=b"".join(parts),
            headers={"X-Pdfm-Token": token, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req) as res:
            assert res.status == 200
            out_bytes = res.read()
        out_path = tmp_path / "out_api.pdf"
        out_path.write_bytes(out_bytes)
        r = PdfReader(str(out_path))
        assert len(r.pages) == 2
        assert float(r.pages[0].cropbox.width) == pytest.approx(200.0, abs=0.5)
    finally:
        srv.shutdown()
        srv.server_close()
