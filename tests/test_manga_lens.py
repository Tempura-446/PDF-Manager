# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for RTL CBZ auto-detection, Manga Lens single-page translation, and interactive bubble apply."""

import io
import json
import zipfile

from PIL import Image, ImageDraw
from pypdf import PdfReader

from pdfmanager import cbz
from pdfmanager.translate import manga
from test_more import app, multipart  # noqa: F401


def _make_comic_page_png(width=600, height=840):
    img = Image.new("RGB", (width, height), (35, 35, 42))
    draw = ImageDraw.Draw(img)
    # Draw a crisp white speech bubble with dark outline and fake dark ink strokes inside
    draw.ellipse((140, 120, 440, 320), fill=(255, 255, 255), outline=(0, 0, 0), width=5)
    for y in range(180, 260, 18):
        draw.rectangle((210, y, 370, y + 7), fill=(15, 15, 15))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_cbz(path, *, comic_info=None, pages=2):
    png = _make_comic_page_png()
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(1, pages + 1):
            zf.writestr(f"page_{i:02d}.png", png)
        if comic_info is not None:
            zf.writestr("ComicInfo.xml", comic_info)
    return path


def test_cbz_rtl_auto_detection(tmp_path):
    rtl_cbz = _make_cbz(
        tmp_path / "manga.cbz",
        comic_info='<?xml version="1.0"?><ComicInfo><Manga>YesAndRightToLeft</Manga></ComicInfo>',
    )
    ltr_cbz = _make_cbz(
        tmp_path / "western.cbz",
        comic_info='<?xml version="1.0"?><ComicInfo><Manga>No</Manga></ComicInfo>',
    )
    assert cbz.detect_cbz_rtl(rtl_cbz) is True
    assert cbz.detect_cbz_rtl(ltr_cbz) is False

    rtl_pdf = tmp_path / "manga.pdf"
    res = cbz.cbz_to_pdf(rtl_cbz, rtl_pdf, rtl_viewer="auto")
    assert res["rtl"] is True
    vp = PdfReader(str(rtl_pdf)).trailer["/Root"].get("/ViewerPreferences")
    assert vp is not None
    assert str(vp.get("/Direction")) == "/R2L"

    ltr_pdf = tmp_path / "western.pdf"
    res2 = cbz.cbz_to_pdf(ltr_cbz, ltr_pdf, rtl_viewer="auto")
    assert res2["rtl"] is False
    vp2 = PdfReader(str(ltr_pdf)).trailer["/Root"].get("/ViewerPreferences")
    assert vp2 is None or str(vp2.get("/Direction")) != "/R2L"


def test_manga_lens_translate_and_apply(tmp_path):
    comic = _make_cbz(tmp_path / "ch1.cbz", pages=2)
    pdf_path = tmp_path / "ch1.pdf"
    cbz.cbz_to_pdf(comic, pdf_path, rtl_viewer=True)

    res = manga.translate_manga_page_bubbles(
        pdf_path,
        1,
        "ja",
        "en",
        use_ai=False,
        ocr_fn=lambda crop, lang: "こんにちは",
        translate_fn=lambda texts, src, dst: [f"Hello ({i})" for i, _ in enumerate(texts)],
    )
    assert res["page"] == 1
    assert len(res["bubbles"]) >= 1
    b0 = res["bubbles"][0]
    assert 0.0 <= b0["x0"] < b0["x1"] <= 1.0
    assert 0.0 <= b0["y0"] < b0["y1"] <= 1.0
    assert b0["original"] == "こんにちは"
    assert b0["translated"].startswith("Hello")

    # Edit text & font_scale and apply permanently to page 1
    b0["translated"] = "Edited bubble dialogue!"
    b0["font_scale"] = 1.2
    out_pdf = tmp_path / "ch1_applied.pdf"
    applied = manga.apply_manga_page_bubbles(
        pdf_path, out_pdf, 1, [b0], "en", use_ai=False,
    )
    assert applied["page"] == 1
    assert applied["bubbles_applied"] == 1
    reader = PdfReader(str(out_pdf))
    assert len(reader.pages) == 2
    assert "Edited" in (reader.pages[0].extract_text() or "")


def test_manga_lens_http_endpoints(tmp_path, monkeypatch, app):
    url, token, _srv = app
    comic = _make_cbz(tmp_path / "vol1.cbz", pages=1)
    pdf_path = tmp_path / "vol1.pdf"
    cbz.cbz_to_pdf(comic, pdf_path, rtl_viewer=True)
    pdf_bytes = pdf_path.read_bytes()

    monkeypatch.setattr(
        manga,
        "translate_manga_page_bubbles",
        lambda *a, **kw: {
            "page": 1,
            "src_lang": "ja",
            "dst_lang": "en",
            "reading_dir": "rtl",
            "width": 600,
            "height": 840,
            "bubbles": [{
                "id": 0, "x0": 0.2, "y0": 0.15, "x1": 0.7, "y1": 0.4,
                "original": "テスト", "translated": "Test line!",
                "shape": "oval", "inverted": False, "borderless": False,
                "needs_inpaint": False, "bg_rgb": [1, 1, 1], "text_rgb": [0, 0, 0],
                "font_scale": 1.0,
            }],
        },
    )

    import urllib.request

    body, hdrs = multipart(
        {"page": "1", "from": "ja", "to": "en", "manga_ai": "false"},
        [("file", pdf_path)],
    )
    req = urllib.request.Request(
        f"{url}/api/translate/manga-page",
        data=body,
        headers={**hdrs, "X-Pdfm-Token": token},
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["bubbles"][0]["translated"] == "Test line!"

    # Apply endpoint
    apply_body, apply_hdrs = multipart(
        {
            "page": "1",
            "to": "en",
            "manga_ai": "false",
            "bubbles": json.dumps(data["bubbles"]),
        },
        [("file", pdf_path)],
    )
    req2 = urllib.request.Request(
        f"{url}/api/translate/manga-page/apply",
        data=apply_body,
        headers={**apply_hdrs, "X-Pdfm-Token": token},
    )
    with urllib.request.urlopen(req2) as resp2:
        assert resp2.status == 200
        out_bytes = resp2.read()
        assert out_bytes.startswith(b"%PDF-")
        assert "Test" in (PdfReader(io.BytesIO(out_bytes)).pages[0].extract_text() or "")
