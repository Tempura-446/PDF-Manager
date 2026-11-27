# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for v0.87.0 module upgrades: Baberu OCR v1.2 (ja/zh/en KV-cache ONNX),
PP-OCRv6 Small/Tiny Standard OCR tiers, TranslateGemma presets, and pdf.js 6.5."""

import hashlib
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from pdfmanager import models_manager, ocr, vlm
from pdfmanager.translate import manga, manga_ai


def test_baberu_ocr_v12_kv_cache_multilingual_decoding(tmp_path, monkeypatch):
    # Charset ids: 4='你', 5='好', 6='!', 7='A', 8='B'
    vocab_file = tmp_path / "vocab.json"
    vocab_file.write_text(json.dumps(["你", "好", "!", "A", "B"]), encoding="utf-8")
    vocab = manga_ai._BaberuVocab(vocab_file)
    assert vocab.bos == 1 and vocab.eos == 2

    class FakeVision:
        def run(self, out_names, feeds):
            assert out_names == ["vision_embeds"]
            pv = feeds["pixel_values"]
            assert pv.shape == (1, 3, 224, 224)
            assert pv.dtype == np.float32
            return [np.zeros((1, 16, 384), dtype=np.float32)]

    # Sequence to emit: 4('你'), 5('好'), 6('!'), 2(EOS)
    past_tensors = [np.zeros((1, 4, 17, 64), dtype=np.float32) for _ in range(12)]
    step_calls = []

    class FakePrefill:
        def run(self, _out, feeds):
            assert feeds["input_ids"].tolist() == [[1]]
            logits = np.full((1, 1, 9), -5.0, dtype=np.float32)
            logits[0, 0, 4] = 10.0  # '你'
            return [logits, *past_tensors]

    emitted_after_prefill = [5, 6, 2]  # '好', '!', EOS

    class FakeStep:
        def run(self, _out, feeds):
            idx = len(step_calls)
            step_calls.append((int(feeds["input_ids"][0, 0]), int(feeds["position_ids"][0, 0])))
            assert "past_k0" in feeds and "past_v5" in feeds
            nxt = emitted_after_prefill[min(idx, len(emitted_after_prefill) - 1)]
            logits = np.full((1, 1, 9), -5.0, dtype=np.float32)
            logits[0, 0, nxt] = 10.0
            return [logits, *past_tensors]

    monkeypatch.setattr(
        manga_ai,
        "_get_ocr_sessions",
        lambda model_dir=None: ("baberu", FakeVision(), FakePrefill(), FakeStep(), vocab),
    )
    crop = Image.new("RGB", (140, 220), (255, 255, 255))
    text = manga_ai.ocr_manga_crop(crop)
    assert text == "你好!"
    # Prefill produced '你' at pos=17, then step 0 at pos=17 -> '好', step 1 at pos=18 -> '!', step 2 at pos=19 -> EOS
    assert step_calls == [(4, 17), (5, 18), (6, 19)]


def test_extract_bubble_text_uses_baberu_for_zh_and_en(monkeypatch):
    seen = []
    monkeypatch.setattr(manga_ai, "ocr_manga_crop", lambda crop: (seen.append(crop.size), "HELLO!")[1])
    img = Image.new("RGB", (300, 300), (255, 255, 255))
    bubble = {"x0": 20, "y0": 20, "x1": 200, "y1": 160, "w": 180, "h": 140}
    assert manga.extract_bubble_text(img, bubble, "zh", use_ai=True) == "HELLO!"
    assert manga.extract_bubble_text(img, bubble, "en", use_ai=True) == "HELLO!"
    assert len(seen) == 2


def test_standard_ocr_auto_activates_installed_pp_ocrv6_small(tmp_path, monkeypatch):
    ocr.reset()
    models_root = tmp_path / "ocr-models"
    monkeypatch.setattr(ocr, "models_dir", lambda: models_root)

    fake_bytes = {"det": b"fake-v6-small-det", "rec": b"fake-v6-small-rec"}
    digests = {k: hashlib.sha256(v).hexdigest() for k, v in fake_bytes.items()}
    monkeypatch.setattr(
        ocr,
        "_v6_tier_model_source",
        lambda kind, tier="small": (
            f"https://example.invalid/onnx/PP-OCRv6/{kind}/PP-OCRv6_{kind}_{tier}.onnx",
            digests[kind],
        ),
    )
    monkeypatch.setattr(
        ocr,
        "_fetch",
        lambda url, target: target.write_bytes(fake_bytes["det" if "det" in url else "rec"]),
    )

    captured = []

    class FakeRapidOCR:
        def __init__(self, params=None):
            captured.append(params)

        def __call__(self, arr, **kwargs):
            return SimpleNamespace(txts=None, boxes=None, scores=None, word_results=None)

    fake_rapidocr = types.ModuleType("rapidocr")
    fake_rapidocr.ModelType = SimpleNamespace(TINY="tiny", SMALL="small", MEDIUM="medium", MOBILE="mobile", SERVER="server")
    fake_rapidocr.OCRVersion = SimpleNamespace(PPOCRV5="PP-OCRv5", PPOCRV6="PP-OCRv6")
    fake_rapidocr.RapidOCR = FakeRapidOCR
    monkeypatch.setitem(sys.modules, "rapidocr", fake_rapidocr)

    try:
        # Before download: standard mode uses zero-download default
        ocr.recognise(Image.new("L", (120, 120), 255), quality="standard")
        assert "Det.ocr_version" not in captured[0]

        # Download PP-OCRv6 Small via models_manager -> Standard mode auto-upgrades to PP-OCRv6 Small
        res = models_manager.download_model("ppocr_v6_small")
        by_id = {m["id"]: m for m in res["models"]}
        assert by_id["ppocr_v6_small"]["installed"] is True
        assert by_id["manga_ocr"]["approx_mb"] == 122

        ocr.recognise(Image.new("L", (120, 120), 255), quality="standard")
        assert len(captured) == 2
        params = captured[1]
        assert params["Det.ocr_version"] == fake_rapidocr.OCRVersion.PPOCRV6
        assert params["Det.model_type"] == fake_rapidocr.ModelType.SMALL
        assert params["Rec.ocr_version"] == fake_rapidocr.OCRVersion.PPOCRV6
        assert params["Rec.model_type"] == fake_rapidocr.ModelType.SMALL
    finally:
        ocr.reset()


def test_translategemma_presets_and_pdfjs_version():
    models = {p["model"] for p in vlm.RECOMMENDED_LLM_PRESETS}
    assert {"translategemma:4b", "translategemma:12b", "qwen2.5vl:7b", "hunyuan-mt:7b"} <= models
    ver = (Path(__file__).resolve().parent.parent / "pdfmanager" / "static" / "pdfjs" / "VERSION").read_text(encoding="utf-8")
    assert ver.startswith("6.5.438")
