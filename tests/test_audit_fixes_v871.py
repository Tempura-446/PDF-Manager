# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Regression tests for v0.87.1 post-audit fixes: Live Manga Lens crash,
Save-in-Place book protection, VLM network policy, manga_ai SHA-256 caching
& session eviction, EPUB title/URI/duplicate bookmarks, and Cache-Control."""

import io
import json
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from pdfmanager import bookshelf, core, epub, models_manager, vlm
from pdfmanager.translate import manga, manga_ai, text as ttext
from test_more import app  # noqa: F401


def test_manga_page_bubbles_uses_translate_texts_not_missing_translate_batch(tmp_path, monkeypatch):
    pdf_path = tmp_path / "page.pdf"
    w = PdfWriter()
    w.add_blank_page(400, 600)
    with open(pdf_path, "wb") as fh:
        w.write(fh)

    monkeypatch.setattr(
        manga,
        "detect_speech_bubbles",
        lambda *a, **k: [{"x0": 40, "y0": 40, "x1": 200, "y1": 160, "w": 160, "h": 120, "shape": "oval"}],
    )
    monkeypatch.setattr(manga, "extract_bubble_text", lambda *a, **k: "こんにちは")
    seen = {}

    def fake_translate_texts(texts, src, dst, **kw):
        seen["args"] = (list(texts), src, dst)
        return ["Hello"]

    monkeypatch.setattr(ttext, "translate_texts", fake_translate_texts)
    res = manga.translate_manga_page_bubbles(pdf_path, 1, src_lang="auto", dst_lang="en", use_ai=False)
    assert len(res["bubbles"]) == 1
    assert res["bubbles"][0]["translated"] == "Hello"
    assert seen["args"][0] == ["こんにちは"]
    assert seen["args"][1] == "ja"


def test_save_in_place_refuses_to_overwrite_cbz_archive(tmp_path, app):
    url, token, srv = app
    cbz_path = tmp_path / "vol01.cbz"
    original_bytes = b"PK\x03\x04fake-cbz-archive"
    cbz_path.write_bytes(original_bytes)
    key = srv.RequestHandlerClass.opened.add(cbz_path)

    req = urllib.request.Request(
        f"{url}/api/save/{key}",
        data=b"%PDF-1.4\n%%EOF\n",
        headers={"Content-Type": "application/pdf", "X-Pdfm-Token": token},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400
    assert cbz_path.read_bytes() == original_bytes


def test_vlm_refuses_plaintext_http_for_remote_host():
    img = Image.new("RGB", (64, 64), (255, 255, 255))
    with pytest.raises(core.PdfManagerError, match="insecure"):
        vlm.vlm_ocr_image(img, api_url="http://example.com/v1", model="qwen2.5vl:7b")


def test_manga_ai_session_cache_skips_repeat_sha256_and_delete_evicts(tmp_path, monkeypatch):
    manga_ai.reset()
    root = tmp_path / "manga-ai"
    monkeypatch.setattr(manga_ai, "manga_ai_dir", lambda: root)
    det_dir = root / "detector"
    det_dir.mkdir(parents=True)
    det_file = det_dir / "detector-v4-s_int8.onnx"
    det_file.write_bytes(b"fake-onnx")

    sha_calls = []
    monkeypatch.setattr(manga_ai, "_sha256", lambda p: (sha_calls.append(p), manga_ai.MANGA_AI_MODELS["detector"]["files"][0]["sha256"])[1])
    monkeypatch.setattr(manga_ai, "_ort_session", lambda p: f"session:{p.name}")

    s1 = manga_ai._get_detector_session()
    s2 = manga_ai._get_detector_session()
    assert s1 == s2 == "session:detector-v4-s_int8.onnx"
    assert len(sha_calls) == 1  # hashed once on load, zero times on cached hit!

    # Deleting model via models_manager evicts cached session before unlinking
    models_manager.delete_model("manga_detector")
    assert not det_file.exists()
    assert not any(k[0] == "detector" for k in manga_ai._SESSIONS)


def test_epub_extracts_head_title_uri_paths_and_duplicate_bookmarks(tmp_path):
    epub_path = tmp_path / "book.epub"
    img_buf = io.BytesIO()
    Image.new("RGBA", (80, 60), (20, 40, 80, 128)).save(img_buf, format="PNG")
    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr("META-INF/container.xml", """<?xml version="1.0"?>
            <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
            </container>""")
        zf.writestr("OEBPS/content.opf", """<?xml version="1.0"?>
            <package xmlns="http://www.idpf.org/2007/opf">
              <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Test Book</dc:title></metadata>
              <manifest>
                <item id="c1" href="ch%201.xhtml" media-type="application/xhtml+xml"/>
                <item id="c2" href="ch%202.xhtml" media-type="application/xhtml+xml"/>
                <item id="im" href="pic%201.png" media-type="image/png"/>
              </manifest>
              <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
            </package>""")
        zf.writestr("OEBPS/ch 1.xhtml", '<html><head><title>Interlude</title></head><body><p>First\x00 chapter.</p><img src="pic%201.png#top"/></body></html>')
        zf.writestr("OEBPS/ch 2.xhtml", '<html><head><title>Interlude</title></head><body><p>Second chapter.</p></body></html>')
        zf.writestr("OEBPS/pic 1.png", img_buf.getvalue())

    out_pdf = tmp_path / "book.pdf"
    res = epub.epub_to_pdf(epub_path, out_pdf)
    assert res["chapters"] == 2
    reader = PdfReader(out_pdf)
    assert len(reader.pages) == 2
    outlines = list(reader.outline)
    assert len(outlines) == 2
    assert outlines[0].title == "Interlude"
    assert outlines[1].title == "Interlude"
    assert reader.get_destination_page_number(outlines[0]) != reader.get_destination_page_number(outlines[1])


def test_bookshelf_remove_folder_normalizes_and_thumb_single_cache_control(tmp_path, monkeypatch, app):
    url, token, _srv = app
    monkeypatch.setattr(bookshelf, "_config_path", lambda: tmp_path / "library.json")
    monkeypatch.setattr(bookshelf, "_thumbs_dir", lambda: tmp_path / "thumbs")
    lib = tmp_path / "Manga"
    lib.mkdir()
    pdf_path = lib / "ch1.pdf"
    w = PdfWriter()
    w.add_blank_page(200, 300)
    with open(pdf_path, "wb") as fh:
        w.write(fh)

    bookshelf.add_folder(str(lib))
    items = bookshelf.scan_library()["items"]
    assert len(items) == 1

    req = urllib.request.Request(f"{url}/api/library/thumb/{items[0]['id']}", headers={"X-Pdfm-Token": token})
    with urllib.request.urlopen(req) as resp:
        cc = resp.headers.get_all("Cache-Control")
        assert cc == ["private, max-age=300"]

    # Trailing slash should still remove the normalized folder
    assert bookshelf.remove_folder(str(lib) + "/") == []


def test_layers_and_scales_short_circuit_for_disk_opened_epub(tmp_path, app):
    from test_more import multipart

    url, token, srv = app
    epub_path = tmp_path / "story.epub"
    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr("ch1.xhtml", "<html><body><p>Hello EPUB</p></body></html>")
    key = srv.RequestHandlerClass.opened.add(epub_path)

    for route, expected in (("/api/layers", {"layers": []}), ("/api/scales", {"pages": []})):
        body, hdrs = multipart({"id": key}, [])
        req = urllib.request.Request(f"{url}{route}", data=body, headers={**hdrs, "X-Pdfm-Token": token})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            assert json.loads(resp.read()) == expected


def test_vlm_resolve_config_skips_legacy_text_only_qwen_default(monkeypatch):
    from pdfmanager import update

    monkeypatch.setattr(update, "settings", lambda: {"translate_model": "qwen2.5:7b"})
    _, mdl, _ = vlm._resolve_vlm_config()
    assert mdl == "qwen2.5vl:7b"

    monkeypatch.setattr(update, "settings", lambda: {"translate_model": "translategemma:4b"})
    _, mdl2, _ = vlm._resolve_vlm_config()
    assert mdl2 == "translategemma:4b"

    monkeypatch.setattr(update, "settings", lambda: {"translate_model": "qwen2.5:7b", "vlm_model": "gemma3:12b"})
    _, mdl3, _ = vlm._resolve_vlm_config()
    assert mdl3 == "gemma3:12b"


def test_models_download_supports_async_job_mode(monkeypatch, app):
    url, token, _srv = app
    monkeypatch.setattr(models_manager, "download_model", lambda pid: {"downloaded": pid, "models": []})

    req = urllib.request.Request(
        f"{url}/api/models/download",
        data=json.dumps({"id": "ppocr_v6_tiny"}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Pdfm-Token": token, "X-Pdfm-Async": "1"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read())
        assert "job" in data

