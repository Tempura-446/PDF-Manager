# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Phase 5 performance and scalability regression tests (P-001, P-004, P-005, P-006)."""

import threading
import time
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pdfmanager import core, progress, redact


def _make_pdf(path: Path, num_pages: int, secret: str = "TOP_SECRET_42") -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(num_pages):
        c.setFont("Helvetica", 12)
        c.drawString(72, 720, f"Page {i + 1}: public text and {secret} token.")
        c.showPage()
    c.save()
    return path


# ---------------------------------------------------------------------------
# P-004: Streaming raster redaction in pdfmanager/redact.py
# ---------------------------------------------------------------------------

def test_iter_render_redacted_streams_one_page_at_a_time(tmp_path):
    src = _make_pdf(tmp_path / "doc.pdf", 4)
    areas = {i: [(70, 710, 350, 735)] for i in range(4)}

    gen = redact._iter_render_redacted(src, areas, dpi=72, password=None)
    # Verify it is a true lazy generator (not a precomputed list/dict).
    assert hasattr(gen, "__next__")

    seen = []
    for number, image, box in gen:
        assert isinstance(image, Image.Image)
        assert len(box) == 4
        seen.append(number)
        image.close()

    assert seen == [0, 1, 2, 3]


def test_render_redacted_backward_compatible_wrapper(tmp_path):
    src = _make_pdf(tmp_path / "doc.pdf", 2)
    areas = {0: [(70, 710, 350, 735)], 1: [(70, 710, 350, 735)]}
    images = redact._render_redacted(src, areas, dpi=72, password=None)
    try:
        assert set(images.keys()) == {0, 1}
        for img, box in images.values():
            assert isinstance(img, Image.Image)
            assert len(box) == 4
    finally:
        for img, _ in images.values():
            img.close()


def test_is_grayscale_matches_rgb_gray_and_rejects_color():
    gray_rgb = Image.new("RGB", (16, 16), (120, 120, 120))
    color_rgb = Image.new("RGB", (16, 16), (120, 120, 120))
    color_rgb.putpixel((5, 5), (200, 10, 10))
    gray_l = Image.new("L", (16, 16), 80)
    try:
        assert redact._is_grayscale(gray_rgb) is True
        assert redact._is_grayscale(color_rgb) is False
        assert redact._is_grayscale(gray_l) is True
    finally:
        gray_rgb.close()
        color_rgb.close()
        gray_l.close()


def test_redact_image_streaming_removes_secret_and_verifies_clean(tmp_path):
    src = _make_pdf(tmp_path / "secret.pdf", 5, secret="CONFIDENTIAL_XYZ")
    out = tmp_path / "redacted.pdf"

    report = redact.redact(src, out, terms=["CONFIDENTIAL_XYZ"], method="image", dpi=100)
    assert out.exists()
    assert len(report) == 5
    assert report.method == "image"
    assert report.proof is not None
    assert not report.proof.get("still_in")

    reader = PdfReader(str(out))
    assert len(reader.pages) == 5
    for page in reader.pages:
        assert "CONFIDENTIAL_XYZ" not in (page.extract_text() or "")


# ---------------------------------------------------------------------------
# P-005 & P-006: Bounded concurrent batch_run() in pdfmanager/core.py
# ---------------------------------------------------------------------------

def test_default_workers_is_bounded():
    assert 1 <= core.default_workers() <= core.DEFAULT_MAX_WORKERS
    assert core.default_workers(1) == 1
    assert core.default_workers(0) == 1
    assert core.default_workers(-5) == 1
    assert core.default_workers(3) == 3


def test_batch_run_empty():
    assert core.batch_run([], lambda x: x) == []


def test_batch_run_preserves_order_with_varying_durations():
    items = [5, 1, 4, 2, 3, 0]
    progress_events = []

    def work(x):
        time.sleep(x * 0.005)
        return x * 10

    outcomes = core.batch_run(
        items, work, max_workers=3,
        on_progress=lambda done, total: progress_events.append((done, total)),
    )
    assert [o["index"] for o in outcomes] == list(range(len(items)))
    assert [o["item"] for o in outcomes] == items
    assert all(o["ok"] for o in outcomes)
    assert [o["result"] for o in outcomes] == [x * 10 for x in items]
    assert len(progress_events) == len(items)
    assert progress_events[-1] == (len(items), len(items))


def test_batch_run_isolates_per_item_errors():
    def work(x):
        if x == 2:
            raise ValueError("bad document")
        return x + 1

    for workers in (1, 3):
        outcomes = core.batch_run([0, 1, 2, 3], work, max_workers=workers)
        assert [o["ok"] for o in outcomes] == [True, True, False, True]
        assert outcomes[0]["result"] == 1
        assert outcomes[1]["result"] == 2
        assert "bad document" in outcomes[2]["error"]
        assert isinstance(outcomes[2]["exception"], ValueError)
        assert outcomes[3]["result"] == 4


def test_batch_run_bounds_concurrency():
    active = 0
    peak_active = 0
    lock = threading.Lock()

    def work(x):
        nonlocal active, peak_active
        with lock:
            active += 1
            peak_active = max(peak_active, active)
        try:
            time.sleep(0.01)
            return x
        finally:
            with lock:
                active -= 1

    outcomes = core.batch_run(range(12), work, max_workers=2)
    assert len(outcomes) == 12
    assert all(o["ok"] for o in outcomes)
    assert peak_active <= 2


def test_batch_run_respects_progress_cancellation():
    job = progress.Job()
    ran = []

    def work(x):
        ran.append(x)
        if x == 1:
            job.cancel()
        time.sleep(0.01)
        progress.check()
        return x

    job.run(core.batch_run, range(20), work, max_workers=2)
    assert job.state == "cancelled"
    assert len(ran) < 20


# ---------------------------------------------------------------------------
# P-001: Reproducible benchmark harness smoke test
# ---------------------------------------------------------------------------

def test_benchmark_harness_smoke():
    import importlib.util

    bench_path = Path(__file__).resolve().parents[1] / "packaging" / "benchmark.py"
    spec = importlib.util.spec_from_file_location("zpdf_benchmark", bench_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    report = mod.run_benchmarks(
        [3],
        ["compare", "redact_remove", "redact_image", "batch"],
        repeats=1,
        dpi=72,
        batch_docs=2,
    )
    assert "environment" in report
    assert len(report["results"]) >= 4
    md = mod.format_markdown(report)
    assert "| Workload |" in md
    assert "redact_image(72dpi)" in md
