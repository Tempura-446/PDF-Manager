# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for the Manga AI Pack: ONNX bubble/text detector, Japanese manga-ocr,
LaMa-Manga inpainting, and page-context LLM manga script translation."""

import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from pdfmanager import core, gui, i18n
from pdfmanager.translate import engines, manga, manga_ai


def test_manga_ai_status_and_sha256_verified_download(tmp_path, monkeypatch):
    monkeypatch.setattr(manga_ai, "manga_ai_dir", lambda: tmp_path / "manga-ai")
    status = manga_ai.manga_ai_status()
    assert set(status) == {"detector", "ocr", "inpaint"}
    assert all(not info["installed"] for info in status.values())

    # Fake a small detector file with matching SHA-256
    payload = b"fake-rtdetr-onnx-bytes"
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setitem(
        manga_ai.MANGA_AI_MODELS,
        "detector",
        {
            "label": "manga bubble detector",
            "size_mb": 11,
            "files": [
                {
                    "name": "detector-v4-s_int8.onnx",
                    "url": "https://example.invalid/detector-v4-s_int8.onnx",
                    "sha256": digest,
                }
            ],
        },
    )
    monkeypatch.setattr(manga_ai, "_fetch_file", lambda url, target, label=None: target.write_bytes(payload))
    paths = manga_ai.ensure_manga_ai(components=("detector",), download=True)
    assert paths["detector"].is_file()
    assert manga_ai.manga_ai_status()["detector"]["installed"] is True

    # Corrupted download is rejected and cleaned up.
    (tmp_path / "manga-ai" / "detector" / "detector-v4-s_int8.onnx").unlink()
    monkeypatch.setattr(manga_ai, "_fetch_file", lambda url, target, label=None: target.write_bytes(b"corrupt"))
    with pytest.raises(core.PdfManagerError, match="damaged"):
        manga_ai.ensure_manga_ai(components=("detector",), download=True)


def test_neural_bubble_detector_finds_bubbles_and_borderless_text_rtl(monkeypatch):
    img = Image.new("RGB", (800, 1200), (245, 245, 245))
    draw = ImageDraw.Draw(img)
    # Draw dark text inside two regions
    draw.rectangle([500, 100, 720, 260], fill=(255, 255, 255), outline=(0, 0, 0), width=3)
    draw.rectangle([80, 120, 300, 280], fill=(120, 120, 120))  # textured/borderless area

    class FakeIO:
        def __init__(self, name, shape=None):
            self.name = name
            self.shape = shape

    class FakeSession:
        def get_inputs(self):
            return [FakeIO("images", [1, 3, 640, 640]), FakeIO("orig_target_sizes", [1, 2])]

        def get_outputs(self):
            return [FakeIO("labels"), FakeIO("boxes"), FakeIO("scores")]

        def run(self, _out, feeds):
            assert feeds["images"].shape == (1, 3, 640, 640)
            assert feeds["images"].dtype == np.float32
            assert 0.0 <= float(feeds["images"].max()) <= 1.0
            # Class 0 = bubble (right), Class 2 = text_free (left), plus low-score noise
            labels = np.array([[0, 2, 0]], dtype=np.int64)
            boxes = np.array([[[500.0, 100.0, 720.0, 260.0],
                               [80.0, 120.0, 300.0, 280.0],
                               [10.0, 10.0, 40.0, 40.0]]], dtype=np.float32)
            scores = np.array([[0.92, 0.85, 0.12]], dtype=np.float32)
            return [labels, boxes, scores]

    monkeypatch.setattr(manga_ai, "_get_detector_session", lambda model_path=None: FakeSession())
    bubbles = manga_ai.detect_bubbles_neural(img, reading_dir="rtl", score_threshold=0.35)
    assert len(bubbles) == 2
    # RTL order: right bubble (x=500) comes before left text_free (x=80)
    assert bubbles[0]["box"][0] > bubbles[1]["box"][0]
    assert bubbles[0]["kind"] == "bubble"
    assert bubbles[1]["kind"] == "text_free"
    assert bubbles[1]["needs_inpaint"] is True


def test_manga_ocr_onnx_encoder_decoder_greedy_decoding(tmp_path, monkeypatch):
    vocab_file = tmp_path / "vocab.txt"
    vocab_file.write_text("[PAD]\n[UNK]\n[CLS]\n[SEP]\n[MASK]\nこ\nん\nに\nち\nは\n", encoding="utf-8")

    class FakeIO:
        def __init__(self, name):
            self.name = name

    class FakeEncoder:
        def get_inputs(self):
            return [FakeIO("pixel_values")]

        def run(self, _out, feeds):
            arr = feeds["pixel_values"]
            assert arr.shape == (1, 3, 224, 224)
            assert -1.01 <= float(arr.min()) and float(arr.max()) <= 1.01
            return [np.zeros((1, 197, 768), dtype=np.float32)]

    # Emit tokens: 5(こ), 6(ん), 7(に), 8(ち), 9(は), 3([SEP])
    seq = [5, 6, 7, 8, 9, 3]

    class FakeDecoder:
        def get_inputs(self):
            return [FakeIO("input_ids"), FakeIO("encoder_hidden_states")]

        def run(self, _out, feeds):
            ids = feeds["input_ids"]
            step = ids.shape[1] - 1
            tok = seq[min(step, len(seq) - 1)]
            logits = np.full((1, ids.shape[1], 10), -10.0, dtype=np.float32)
            logits[0, -1, tok] = 10.0
            return [logits]

    monkeypatch.setattr(
        manga_ai,
        "_get_ocr_sessions",
        lambda model_dir=None: (FakeEncoder(), FakeDecoder(), manga_ai._load_vocab(vocab_file)),
    )
    crop = Image.new("RGB", (120, 240), (255, 255, 255))
    text = manga_ai.ocr_manga_crop(crop)
    assert text == "こんにちは"


def test_lama_inpainting_and_needs_inpainting_heuristic(monkeypatch):
    # Flat white bubble -> does not need LaMa inpainting
    flat_img = Image.new("RGB", (400, 400), (255, 255, 255))
    flat_bubble = {"box": (100, 100, 220, 200), "kind": "bubble", "bg_color": (1.0, 1.0, 1.0)}
    assert manga_ai.needs_inpainting(flat_img, flat_bubble) is False

    # Borderless text_free -> always needs LaMa inpainting
    free_bubble = {"box": (100, 100, 220, 200), "kind": "text_free", "bg_color": (1.0, 1.0, 1.0)}
    assert manga_ai.needs_inpainting(flat_img, free_bubble) is True

    # Screentone / checkerboard background -> high outer-ring variance -> needs inpainting
    arr = np.zeros((400, 400, 3), dtype=np.uint8)
    arr[::2, ::2] = 255
    arr[1::2, 1::2] = 255
    tone_img = Image.fromarray(arr, "RGB")
    assert manga_ai.needs_inpainting(tone_img, flat_bubble) is True

    # Verify LaMa 4-channel packing [1, 4, 512, 512] and output resizing
    class FakeIO:
        def __init__(self, name):
            self.name = name

    class FakeLama:
        def get_inputs(self):
            return [FakeIO("input")]

        def run(self, _out, feeds):
            inp = feeds["input"]
            assert inp.shape == (1, 4, 512, 512)
            assert inp.dtype == np.float32
            # Return clean neutral gray patch (0.8)
            return [np.full((1, 3, 512, 512), 0.8, dtype=np.float32)]

    monkeypatch.setattr(manga_ai, "_get_inpaint_session", lambda model_path=None: FakeLama())
    cleaned = manga_ai.inpaint_manga_region(tone_img, (100, 100, 220, 200))
    assert cleaned.size == (120, 100)
    assert cleaned.mode == "RGB"
    px = cleaned.getpixel((60, 50))
    assert all(195 <= c <= 215 for c in px)


def test_page_context_llm_manga_script_translation(monkeypatch):
    sent_payloads = []

    def fake_post(url, payload, headers=None, timeout=60):
        sent_payloads.append(payload)
        items = json.loads(payload["messages"][1]["content"])
        if isinstance(items, dict) and "bubbles" in items:
            src_list = items["bubbles"]
        else:
            src_list = items
        return {
            "choices": [
                {"message": {"content": json.dumps([f"EN:{t}" for t in src_list])}}
            ]
        }

    monkeypatch.setattr(i18n, "_http_post_json", fake_post)
    fn = engines._load_llm_translator(
        "ja", "en", api_url="http://localhost:11434/v1", model="qwen2.5:7b", manga=True
    )
    assert getattr(fn, "_llm", False) is True
    pages = [["おい！", "待てよ！"], ["何だ？"]]
    out = manga_ai.translate_manga_pages(pages, "ja", "en", translator=fn)
    assert out == [["EN:おい！", "EN:待てよ！"], ["EN:何だ？"]]
    # System prompt should be tailored for natural manga/comic dialogue (not formal document tone)
    sys_prompt = sent_payloads[0]["messages"][0]["content"].lower()
    assert "manga" in sys_prompt or "dialogue" in sys_prompt
    assert "formal document" not in sys_prompt
    # Page 2 should receive trailing context from Page 1
    assert len(sent_payloads) == 2


def test_translate_tool_exposes_manga_ai_option():
    tool = next(t for t in gui.OPERATIONS if t["id"] == "translate")
    field_ids = {f["key"] for f in tool["fields"]}
    assert "manga_ai" in field_ids
