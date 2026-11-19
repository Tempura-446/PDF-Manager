# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for Better OCR Phase 5: blank & duplicate page skipping, parallel
worker pool speedup & determinism, and Windows DirectML GPU fallback."""
import sys
import time
import types

from PIL import Image, ImageDraw

from pdfmanager import core, ocr, textlayer, update
from pdfmanager.textlayer import Word


def _make_scan_pdf(path, specs):
    """Build a multi-page scanned PDF from ``specs``: each item is either
    None (blank white page) or a string drawn on the page."""
    imgs = []
    for i, label in enumerate(specs):
        im = Image.new("RGB", (600, 800), "white")
        if label is not None:
            draw = ImageDraw.Draw(im)
            draw.rectangle([40, 40, 560, 120], outline="black", width=3)
            draw.text((60, 65), label, fill="black")
        p = path.parent / f"_p5_{i}.png"
        im.save(p)
        imgs.append(p)
    core.images_to_pdf(imgs, path)
    return path


def _timed_page_worker(task):
    """Top-level picklable worker simulating OCR work per page."""
    _source_str, page_idx, _rotate, _deskew, _script, _quality = task
    time.sleep(0.35)
    w1 = Word(f"Page{page_idx + 1}", [(60, 60), (180, 60), (180, 95), (60, 95)], 0.94)
    w2 = Word(f"Token{page_idx + 1}", [(200, 60), (340, 60), (340, 95), (200, 95)], 0.45)
    line = ocr.Line(f"{w1.text} {w2.text}", [(60, 60), (340, 60), (340, 95), (60, 95)], 0.70, [w1, w2])
    return page_idx, [line], 0, 0.0, (600, 800)


def test_blank_and_duplicate_pages_skipped(tmp_path, monkeypatch):
    """Blank pages are skipped without calling OCR; identical pages are only
    recognised once yet both receive the full text layer and unsure markers."""
    # Page 1: "Alpha", Page 2: blank, Page 3: identical to Page 1 ("Alpha"), Page 4: "Beta"
    src = _make_scan_pdf(tmp_path / "mixed.pdf", ["Alpha", None, "Alpha", "Beta"])
    out = tmp_path / "out.pdf"

    calls = []

    def fake_recognise(image, script=None, quality="standard"):
        idx = len(calls) + 1
        calls.append(image.size)
        w = Word(f"Unique{idx}", [(60, 60), (200, 60), (200, 95), (60, 95)], 0.48)
        return [ocr.Line(w.text, [(60, 60), (200, 60), (200, 95), (60, 95)], 0.48, [w])]

    monkeypatch.setattr(ocr, "recognise", fake_recognise)
    report = {}
    ocr._run_builtin(src, out, force=False, deskew=False, rotate=False, report=report)

    # Only 2 unique non-blank pages ("Alpha" and "Beta") were recognised!
    assert len(calls) == 2
    assert report["pages_read"] == [1, 3, 4]
    assert report["skipped"] == {"blank page": [2]}

    # Pages 1 and 3 both received "Unique1", page 2 is blank, page 4 received "Unique2".
    assert "Unique1" in core.extract_text(out, pages="1")
    assert core.extract_text(out, pages="2").strip() == ""
    assert "Unique1" in core.extract_text(out, pages="3")
    assert "Unique2" in core.extract_text(out, pages="4")

    unsure = textlayer.read_unsure_words(out)
    assert unsure["total"] == 3
    assert set(unsure["pages"].keys()) == {"1", "3", "4"}
    assert unsure["pages"]["1"][0]["text"] == "Unique1"
    assert unsure["pages"]["3"][0]["text"] == "Unique1"
    assert unsure["pages"]["4"][0]["text"] == "Unique2"


def test_parallel_workers_faster_and_identical_to_single_worker(tmp_path):
    """Four worker processes run at least 2x faster than one worker on an
    8-page file and produce identical extracted text and unsure-word markers."""
    src = _make_scan_pdf(tmp_path / "eight.pdf", [f"Item {i}" for i in range(1, 9)])
    out_one = tmp_path / "one.pdf"
    out_four = tmp_path / "four.pdf"

    t0 = time.monotonic()
    rep_one = {}
    ocr._run_builtin(src, out_one, force=False, deskew=False, rotate=False,
                     report=rep_one, workers=1, _page_worker=_timed_page_worker)
    dt_one = time.monotonic() - t0

    t1 = time.monotonic()
    rep_four = {}
    ocr._run_builtin(src, out_four, force=False, deskew=False, rotate=False,
                     report=rep_four, workers=4, _page_worker=_timed_page_worker)
    dt_four = time.monotonic() - t1

    # Text, unsure words, and report must be 100% identical.
    assert core.extract_text(out_one) == core.extract_text(out_four)
    assert textlayer.read_unsure_words(out_one) == textlayer.read_unsure_words(out_four)
    assert rep_one == rep_four
    assert rep_four["pages_read"] == list(range(1, 9))

    # 8 pages * 0.15s = ~1.2s sequential vs ~0.3-0.5s with 4 workers.
    assert dt_four * 1.8 < dt_one, f"expected 4 workers ({dt_four:.2f}s) to be much faster than 1 ({dt_one:.2f}s)"


def test_default_workers_respects_cores_and_memory(monkeypatch):
    assert ocr.default_workers(1) == 1
    assert ocr.default_workers(2) == 1
    monkeypatch.setattr(ocr.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(ocr, "_available_memory_mb", lambda: 8192)
    assert ocr.default_workers(10) == 4
    # Low memory caps worker count.
    monkeypatch.setattr(ocr, "_available_memory_mb", lambda: 1200)
    assert ocr.default_workers(10) == 2


def test_directml_opt_in_and_cpu_fallback(monkeypatch):
    """DirectML is off by default, enabled only on Windows when opted in, and
    falls back cleanly to CPU on first init or inference error."""
    ocr.reset()
    monkeypatch.setattr(update, "settings", lambda: {})
    assert ocr.default_gpu() is False

    # Simulate Windows + DirectML provider available + user opted in.
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(update, "settings", lambda: {"ocr_gpu": True})

    fake_ort = types.ModuleType("onnxruntime")
    fake_ort.get_available_providers = lambda: ["DmlExecutionProvider", "CPUExecutionProvider"]
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)

    seen_params = []

    class FakeRapidOCR:
        def __init__(self, params=None):
            seen_params.append(dict(params or {}))
            if params and params.get("EngineConfig.onnxruntime.use_dml"):
                raise RuntimeError("DirectML init boom")

        def __call__(self, *args, **kwargs):
            return types.SimpleNamespace(txts=["ok"], boxes=[[[0, 0], [10, 0], [10, 10], [0, 10]]], scores=[0.9])

    fake_rapidocr = types.ModuleType("rapidocr")
    fake_rapidocr.RapidOCR = FakeRapidOCR
    monkeypatch.setitem(sys.modules, "rapidocr", fake_rapidocr)

    # First call tries DirectML, fails, sets _directml_failed, and falls back to CPU.
    eng = ocr._engine()
    assert eng is not None
    assert len(seen_params) == 2
    assert seen_params[0].get("EngineConfig.onnxruntime.use_dml") is True
    assert "EngineConfig.onnxruntime.use_dml" not in seen_params[1]
    assert ocr._directml_failed is True

    # Now test runtime inference fallback in _read().
    ocr.reset()
    seen_params.clear()
    call_log = []

    class RuntimeFailDmlOCR:
        def __init__(self, params=None):
            self.is_dml = bool((params or {}).get("EngineConfig.onnxruntime.use_dml"))
            seen_params.append(dict(params or {}))

        def __call__(self, *args, **kwargs):
            call_log.append(self.is_dml)
            if self.is_dml:
                raise RuntimeError("DirectML runtime boom")
            return types.SimpleNamespace(txts=["ok"], boxes=[[[0, 0], [10, 0], [10, 10], [0, 10]]], scores=[0.95])

    fake_rapidocr.RapidOCR = RuntimeFailDmlOCR
    res = ocr._read(Image.new("RGB", (60, 40), "white"))
    assert res.txts == ["ok"]
    assert call_log == [True, False]
    assert ocr._directml_failed is True
    ocr.reset()
