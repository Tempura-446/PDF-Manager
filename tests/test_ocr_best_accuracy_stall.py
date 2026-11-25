# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Regression tests for Best-accuracy built-in OCR stalls, memory/thread bounds,
download progress/cancellation, and retry caps."""

import hashlib
import io
import sys
import types
from types import SimpleNamespace

import pytest
from PIL import Image

from pdfmanager import ocr, ocrtables, progress
from pdfmanager.ocr import models as ocr_models
from pdfmanager.ocr import preprocess as ocr_preprocess
from pdfmanager.ocr import runner as ocr_runner
from pdfmanager.ocr.engines import Line
from pdfmanager.textlayer import Word


def test_fetch_reports_progress_and_honours_cancel(tmp_path, monkeypatch):
    chunks = [b"a" * (256 * 1024)] * 6  # 1.5 MB total
    seen_messages = []

    class FakeResp:
        headers = {"Content-Length": str(sum(len(c) for c in chunks))}

        def __init__(self):
            self._idx = 0

        def read(self, n=-1):
            if self._idx >= len(chunks):
                return b""
            data = chunks[self._idx]
            self._idx += 1
            return data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=60: FakeResp())
    monkeypatch.setattr("pdfmanager.policy.check_network_allowed", lambda *a, **k: None)
    monkeypatch.setattr(progress, "say", lambda msg: seen_messages.append(msg))

    target = tmp_path / "model.onnx"
    ocr_models._fetch("https://example.invalid/best_det.onnx", target, label="best-accuracy detection model")
    assert target.read_bytes() == b"".join(chunks)
    assert any("MB" in m for m in seen_messages), f"expected MB progress updates, got {seen_messages!r}"

    # Cancellation mid-download aborts cleanly via progress.Cancelled.
    job = progress.Job()
    reads = 0

    class CancelResp(FakeResp):
        def read(self, n=-1):
            nonlocal reads
            reads += 1
            if reads == 2:
                job.cancel()
            return super().read(n)

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=60: CancelResp())
    part = tmp_path / "cancel.part"
    token = progress._job.set(job)
    try:
        with pytest.raises(progress.Cancelled):
            ocr_models._fetch("https://example.invalid/best_det.onnx", part, label="best-accuracy detection model")
    finally:
        progress._job.reset(token)
    assert reads <= 3


def test_default_workers_caps_best_accuracy_for_ram_and_cpu(monkeypatch):
    monkeypatch.setattr("os.cpu_count", lambda: 16)
    monkeypatch.setattr(ocr, "_available_memory_mb", lambda: 32000)
    # Standard can use up to MAX_WORKERS (4), best is capped at 2.
    assert ocr_runner.default_workers(10, quality="standard") == 4
    assert ocr_runner.default_workers(10, quality="best") == 2

    # On a 3 GB free RAM machine, best accuracy drops to 1 worker (1800 MB budget/worker).
    monkeypatch.setattr(ocr, "_available_memory_mb", lambda: 3000)
    assert ocr_runner.default_workers(10, quality="best") == 1


def test_engine_sets_onnx_thread_limits_from_env(tmp_path, monkeypatch):
    ocr.reset()
    monkeypatch.setenv("ZPDF_OCR_THREADS", "3")
    captured = []

    class FakeRapidOCR:
        def __init__(self, params=None):
            captured.append(params)

    fake_rapidocr = types.ModuleType("rapidocr")
    fake_rapidocr.ModelType = SimpleNamespace(MOBILE="mobile", SERVER="server")
    fake_rapidocr.OCRVersion = SimpleNamespace(PPOCRV5="PP-OCRv5")
    fake_rapidocr.RapidOCR = FakeRapidOCR
    monkeypatch.setitem(sys.modules, "rapidocr", fake_rapidocr)

    try:
        ocr_models._engine(script=None, quality="standard", use_gpu=False)
        assert len(captured) == 1
        assert captured[0]["EngineConfig.onnxruntime.intra_op_num_threads"] == 3
        assert captured[0]["EngineConfig.onnxruntime.inter_op_num_threads"] == 1
    finally:
        ocr.reset()


def test_best_accuracy_uses_standard_model_for_page_orientation(monkeypatch):
    seen_orientation_qualities = []

    def fake_read(image, script=None, quality="standard"):
        return SimpleNamespace(txts=["x"], boxes=[[[0, 0], [10, 0], [10, 10], [0, 10]]], scores=[0.2], word_results=None)

    def fake_orientation(image, script=None, quality="standard"):
        seen_orientation_qualities.append(quality)
        return 0

    monkeypatch.setattr(ocr, "_read", fake_read)
    monkeypatch.setattr(ocr, "page_orientation", fake_orientation)
    monkeypatch.setattr(ocr_runner, "_reads_wrong_way", lambda raw: True)
    monkeypatch.setattr(ocr_runner, "_refine_lines", lambda img, lines, script, quality: lines)

    img = Image.new("RGB", (400, 400), "white")
    ocr_runner._recognise_scan_page(img, None, 0, 400, 400, 1.0, True, False, None, "best")
    assert seen_orientation_qualities == ["standard"]


def test_best_accuracy_caps_low_confidence_line_retries(monkeypatch):
    calls = 0

    class FakeEngine:
        def __call__(self, arr, **kwargs):
            nonlocal calls
            calls += 1
            # Return low-confidence result to verify best mode does not run 2nd binarize pass per line
            return SimpleNamespace(
                txts=["abc"],
                boxes=[[[2, 2], [40, 2], [40, 18], [2, 18]]],
                scores=[0.65],
                word_results=[[("abc", 0.65, [[2, 2], [40, 2], [40, 18], [2, 18]])]],
            )

    monkeypatch.setattr(ocr, "_engine", lambda script=None, quality="standard": FakeEngine())
    img = Image.new("RGB", (400, 800), "white")
    lines = [
        Line(
            f"line{i}",
            [(10, 20 + i * 30), (200, 20 + i * 30), (200, 40 + i * 30), (10, 40 + i * 30)],
            0.50 + i * 0.01,
            [Word(f"line{i}", [(10, 20 + i * 30), (200, 20 + i * 30), (200, 40 + i * 30), (10, 40 + i * 30)], 0.50)],
        )
        for i in range(20)
    ]
    ocr_preprocess._retry_low_confidence(img, lines, script=None, quality="best")
    # Best accuracy caps retries to <= 8 lines and skips redundant 2nd pass when text was found.
    assert calls <= 8, f"expected <= 8 server-model retry calls, got {calls}"


def test_ocrtables_caps_cell_retries_and_propagates_cancel(monkeypatch):
    # Build a synthetic table with 30 low-confidence cells and verify max_cell_retries cap.
    img = Image.new("RGB", (600, 600), "white")
    cells = [[(c * 100, r * 50, (c + 1) * 100, (r + 1) * 50) for c in range(5)] for r in range(6)]
    fake_table = {
        "box": (0, 0, 500, 300),
        "rows": [r * 50 for r in range(7)],
        "cols": [c * 100 for c in range(6)],
        "cells": cells,
    }
    monkeypatch.setattr(ocrtables, "detect_ruled_tables", lambda image: [fake_table])

    lines = [
        Line(
            "bad",
            [(c * 100 + 5, r * 50 + 5), (c * 100 + 60, r * 50 + 5),
             (c * 100 + 60, r * 50 + 25), (c * 100 + 5, r * 50 + 25)],
            0.4,
            [Word("bad", [(c * 100 + 5, r * 50 + 5), (c * 100 + 60, r * 50 + 5),
                          (c * 100 + 60, r * 50 + 25), (c * 100 + 5, r * 50 + 25)], 0.4)],
        )
        for r in range(6) for c in range(5)
    ]
    calls = 0

    def fake_read_crop(crop):
        nonlocal calls
        calls += 1
        return []

    ocrtables.refine_tables(img, lines, read_crop_fn=fake_read_crop, max_cell_retries=10)
    assert calls == 10

    def cancel_read_crop(crop):
        raise progress.Cancelled()

    with pytest.raises(progress.Cancelled):
        ocrtables.refine_tables(img, lines, read_crop_fn=cancel_read_crop, max_cell_retries=10)


def test_best_accuracy_prefers_pp_ocrv6_medium_models(tmp_path, monkeypatch):
    ocr.reset()
    models_root = tmp_path / "ocr-models"
    monkeypatch.setattr(ocr, "models_dir", lambda: models_root)

    fake_bytes = {"det": b"fake-v6-medium-det", "rec": b"fake-v6-medium-rec"}
    digests = {k: hashlib.sha256(v).hexdigest() for k, v in fake_bytes.items()}
    monkeypatch.setattr(
        ocr,
        "_best_model_source",
        lambda kind: (f"https://example.invalid/onnx/PP-OCRv6/{kind}/PP-OCRv6_{kind}_medium.onnx", digests[kind]),
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
        ocr.recognise(Image.new("L", (200, 200), 255), quality="best")
        assert (models_root / "best_v6_det.onnx").is_file()
        assert (models_root / "best_v6_rec.onnx").is_file()
        assert len(captured) == 1
        params = captured[0]
        assert params["Det.ocr_version"] == fake_rapidocr.OCRVersion.PPOCRV6
        assert params["Det.model_type"] == fake_rapidocr.ModelType.MEDIUM
        assert params["Det.model_path"] == str(models_root / "best_v6_det.onnx")
        assert params["Rec.ocr_version"] == fake_rapidocr.OCRVersion.PPOCRV6
        assert params["Rec.model_type"] == fake_rapidocr.ModelType.MEDIUM
        assert params["Rec.model_path"] == str(models_root / "best_v6_rec.onnx")
    finally:
        ocr.reset()
