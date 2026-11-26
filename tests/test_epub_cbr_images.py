# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for Track 3 (v0.83.0): direct .epub, .cbr, and multi-image opening in the Reader."""

import io
import urllib.request
import zipfile

from PIL import Image
from pypdf import PdfReader

from pdfmanager import cbz, epub
from test_more import app, call, multipart  # noqa: F401


def _make_sample_epub(path, *, rtl=False):
    img = Image.new("RGB", (180, 120), (40, 120, 200))
    ibuf = io.BytesIO()
    img.save(ibuf, format="PNG")
    direction = ' page-progression-direction="rtl"' if rtl else ""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
            "</container>",
        )
        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Test Novel</dc:title></metadata>'
            '<manifest>'
            '<item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="pic" href="cover.png" media-type="image/png"/>'
            "</manifest>"
            f'<spine{direction}><itemref idref="ch1"/><itemref idref="ch2"/></spine>'
            "</package>",
        )
        zf.writestr(
            "OEBPS/ch1.xhtml",
            '<html><head><title>Chapter One</title></head><body>'
            '<h1>Chapter One: The Beginning</h1><p>Hello from EPUB chapter one.</p><img src="cover.png"/>'
            "</body></html>",
        )
        zf.writestr(
            "OEBPS/ch2.xhtml",
            '<html><head><title>Chapter Two</title></head><body>'
            '<h1>Chapter Two: The Journey</h1><p>Second chapter body text.</p>'
            "</body></html>",
        )
        zf.writestr("OEBPS/cover.png", ibuf.getvalue())
    return path


def _make_png_file(path, color=(200, 180, 160)):
    img = Image.new("RGB", (240, 320), color)
    img.save(path, format="PNG")
    return path


def test_epub_to_pdf_with_bookmarks_and_rtl(tmp_path):
    ep = _make_sample_epub(tmp_path / "novel.epub", rtl=True)
    out = tmp_path / "novel.pdf"
    res = epub.epub_to_pdf(ep, out)
    assert res["pages"] >= 2
    assert res["chapters"] == 2
    assert res["rtl"] is True
    reader = PdfReader(str(out))
    assert len(reader.outline) == 2
    assert "Chapter One" in (reader.pages[0].extract_text() or "")
    vp = reader.trailer["/Root"].get("/ViewerPreferences")
    assert vp is not None and str(vp.get("/Direction")) == "/R2L"


def test_epub_cbr_and_images_http_endpoints(tmp_path, app):
    base, token, srv = app
    ep = _make_sample_epub(tmp_path / "book.epub")

    # 1) POST /api/epub/open
    status, headers, body = call(
        base, token, "/api/epub/open", ep.read_bytes(), {"X-Pdfm-Filename": "book.epub"},
    )
    assert status == 200
    assert body.startswith(b"%PDF-")
    assert headers.get("X-Pdfm-Filename") == "book.pdf"
    assert len(PdfReader(io.BytesIO(body)).pages) >= 2

    # 2) GET /api/file/<key> for .epub from disk with Range support
    key = srv.RequestHandlerClass.opened.add(ep)
    req = urllib.request.Request(
        f"{base}/api/file/{key}",
        headers={"X-Pdfm-Token": token, "Range": "bytes=0-31"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        assert resp.status == 206
        assert resp.read().startswith(b"%PDF-")

    # 3) POST /api/cbr/open (ZIP-backed .cbr archive)
    cbr_path = tmp_path / "issue1.cbr"
    p1 = _make_png_file(tmp_path / "p1.png")
    p2 = _make_png_file(tmp_path / "p2.png", (120, 140, 160))
    with zipfile.ZipFile(cbr_path, "w") as zf:
        zf.write(p1, "01.png")
        zf.write(p2, "02.png")
    st2, hdr2, body2 = call(
        base, token, "/api/cbr/open", cbr_path.read_bytes(), {"X-Pdfm-Filename": "issue1.cbr"},
    )
    assert st2 == 200
    assert hdr2.get("X-Pdfm-Filename") == "issue1.pdf"
    assert len(PdfReader(io.BytesIO(body2)).pages) == 2

    # 4) POST /api/images/open (multi-image drop)
    mp_body, mp_hdrs = multipart({"title": "scans"}, [("file", p1), ("file", p2)])
    req_img = urllib.request.Request(
        f"{base}/api/images/open",
        data=mp_body,
        headers={**mp_hdrs, "X-Pdfm-Token": token},
    )
    with urllib.request.urlopen(req_img, timeout=15) as resp_img:
        assert resp_img.status == 200
        img_pdf = resp_img.read()
        assert len(PdfReader(io.BytesIO(img_pdf)).pages) == 2
