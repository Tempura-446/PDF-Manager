# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Track A depth tests: smart target-size compression, no-bloat guarantee, and savings stats."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdfmanager import cli, core
from pdfmanager.server import operations


def _make_photo_pdf(path: Path, pages: int = 3) -> Path:
    rng = np.random.default_rng(42)
    c = canvas.Canvas(str(path), pagesize=letter)
    w, h = letter
    for _ in range(pages):
        # Smooth-ish colored image (high resolution, compresses well with JPEG/downsampling)
        y = np.linspace(0, 255, 900, dtype=np.uint8)[:, None]
        x = np.linspace(0, 255, 700, dtype=np.uint8)[None, :]
        noise = rng.integers(0, 18, size=(900, 700), dtype=np.uint8)
        rgb = np.stack([
            np.broadcast_to(y, (900, 700)),
            np.broadcast_to(x, (900, 700)),
            noise,
        ], axis=-1)
        img = Image.fromarray(rgb, mode="RGB")
        c.drawImage(ImageReader(img), 0, 0, w, h)
        img.close()
        c.showPage()
    c.save()
    return path


def _make_tiny_pdf(path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(72, 720, "Tiny already-optimal PDF.")
    c.showPage()
    c.save()
    return path


def test_compress_target_mb_auto_tunes_and_hits_target(tmp_path):
    src = _make_photo_pdf(tmp_path / "photo.pdf", pages=3)
    out = tmp_path / "shrunk.pdf"
    orig_size = src.stat().st_size
    assert orig_size > 150_000  # big enough to compress meaningfully

    # Ask for half the original size (in MB)
    target_mb = (orig_size * 0.5) / (1024 * 1024)
    notes = []
    stats = {}
    core.compress(src, out, target_mb=target_mb, notes=notes, stats=stats)

    assert out.exists()
    assert stats["target_met"] is True
    assert stats["output_bytes"] <= stats["target_bytes"]
    assert stats["saved_bytes"] > 0
    assert stats["saved_pct"] > 0
    assert any("met" in n for n in notes)


def test_compress_target_mb_unachievable_returns_smallest_gracefully(tmp_path):
    src = _make_tiny_pdf(tmp_path / "tiny.pdf")
    out = tmp_path / "out.pdf"
    notes = []
    stats = {}
    # 100 bytes target is impossible for a valid PDF
    core.compress(src, out, target_mb=0.0001, notes=notes, stats=stats)

    assert out.exists()
    assert stats["target_met"] is False
    assert stats["output_bytes"] <= stats["original_bytes"]
    assert any("smallest achievable" in n for n in notes)


def test_compress_never_bloats_already_optimal_pdf(tmp_path):
    src = _make_tiny_pdf(tmp_path / "tiny.pdf")
    out = tmp_path / "out.pdf"
    stats = {}
    core.compress(src, out, level="low", stats=stats)
    assert out.stat().st_size <= src.stat().st_size
    assert stats["output_bytes"] <= stats["original_bytes"]


def test_compress_invalid_target_mb_raises(tmp_path):
    src = _make_tiny_pdf(tmp_path / "tiny.pdf")
    out = tmp_path / "out.pdf"
    with pytest.raises(core.PdfManagerError, match="greater than 0"):
        core.compress(src, out, target_mb=0)


def test_cli_and_gui_compress_target_mb_wiring(tmp_path):
    src = _make_photo_pdf(tmp_path / "photo.pdf", pages=2)
    out_cli = tmp_path / "cli_out.pdf"
    rc = cli.main(["compress", str(src), "--target-mb", "0.5", "-o", str(out_cli)])
    assert rc == 0
    assert out_cli.exists()
    assert out_cli.stat().st_size <= int(0.5 * 1024 * 1024)

    # GUI tool definition includes target_mb
    comp_spec = operations.OPS["compress"]
    field_keys = [f["key"] for f in comp_spec["fields"]]
    assert "target_mb" in field_keys
