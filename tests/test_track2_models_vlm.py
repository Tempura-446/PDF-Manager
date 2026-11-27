# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for Track 2 (v0.82.0): scanlation SFX halo, AI Models Manager, and Vision-LLM OCR."""

import io
import json
import urllib.request

from PIL import Image
from pypdf import PdfWriter

from pdfmanager import fonts, models_manager, vlm
from pdfmanager.translate import manga
from test_more import app, multipart  # noqa: F401


def test_comic_font_and_borderless_sfx_halo():
    name = fonts.font_for("BOOM!", family="comic", bold=True)
    assert isinstance(name, str) and len(name) > 0

    overlay = manga.make_manga_overlay(
        400, 600, 400, 600,
        [{
            "x0": 80, "y0": 100, "x1": 320, "y1": 220,
            "shape": "rect", "borderless": True, "needs_inpaint": False,
            "bg_rgb": (1.0, 1.0, 1.0), "text_rgb": (0.05, 0.05, 0.05),
        }],
        ["**WHOOSH!**"],
        "en",
        use_ai=False,
    )
    stream = overlay.get_contents().get_data().decode("latin-1", errors="ignore")
    # Stroked halo renders 1 Tr (stroke) followed by default fill pass
    assert "1 Tr" in stream and stream.count("Tj") >= 2


def test_models_manager_list_and_delete(tmp_path, monkeypatch, app):
    url, token, _srv = app
    fake_det = tmp_path / "best_v6_det.onnx"
    fake_rec = tmp_path / "best_v6_rec.onnx"
    fake_det.write_bytes(b"0" * 1024)
    fake_rec.write_bytes(b"1" * 2048)
    monkeypatch.setattr(models_manager, "_ppocr_v6_paths", lambda: [fake_det, fake_rec])

    req = urllib.request.Request(f"{url}/api/models/status", headers={"X-Pdfm-Token": token})
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read())
    by_id = {m["id"]: m for m in data["models"]}
    assert by_id["ppocr_v6"]["installed"] is True
    assert by_id["ppocr_v6"]["size_bytes"] == 3072

    del_req = urllib.request.Request(
        f"{url}/api/models/delete",
        data=json.dumps({"id": "ppocr_v6"}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Pdfm-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(del_req) as resp2:
        assert resp2.status == 200
        del_data = json.loads(resp2.read())
    assert del_data["freed_bytes"] == 3072
    assert not fake_det.exists() and not fake_rec.exists()


def test_vlm_ocr_image_and_http_endpoint(tmp_path, monkeypatch, app):
    url, token, _srv = app
    captured = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, _n=-1):
            return json.dumps({
                "choices": [{"message": {"content": "Translated manga page text"}}]
            }).encode("utf-8")

    real_urlopen = urllib.request.urlopen

    def _fake_urlopen(req, timeout=120):
        if isinstance(req, urllib.request.Request) and req.full_url.startswith(url):
            return real_urlopen(req, timeout=timeout)
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    # Direct PIL test
    img = Image.new("RGB", (200, 300), (255, 255, 255))
    out = vlm.vlm_ocr_image(img, translate_to="English", api_url="http://localhost:11434/v1", model="qwen2.5vl:7b")
    assert out == "Translated manga page text"
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
    content = captured["body"]["messages"][0]["content"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")

    # HTTP /api/ai/vlm-ocr test
    pdf_path = tmp_path / "sample.pdf"
    w = PdfWriter()
    w.add_blank_page(200, 300)
    with open(pdf_path, "wb") as fh:
        w.write(fh)

    body, hdrs = multipart({"page": "1", "translate_to": "English", "model": "qwen2.5vl:7b"}, [("file", pdf_path)])
    req = urllib.request.Request(f"{url}/api/ai/vlm-ocr", data=body, headers={**hdrs, "X-Pdfm-Token": token})
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        res = json.loads(resp.read())
    assert res["page"] == 1
    assert res["text"] == "Translated manga page text"
