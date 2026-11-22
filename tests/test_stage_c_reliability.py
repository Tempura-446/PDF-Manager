# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Stage C regression tests: REL-001 (isolation queue deadlock, cancellation, cleanup),
REL-002 (atomic outputs & temp-file cleanup), and REL-003 (attachment extraction security)."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pikepdf
import pytest
from reportlab.pdfgen import canvas

from pdfmanager import core, isolation


# ---- Helper callables for spawned isolation worker ------------------------------------


def _worker_small(x: int) -> int:
    return x * 2


def _worker_large_payload(size_bytes: int) -> bytes:
    return b"Z" * size_bytes


def _worker_large_exception(size_bytes: int) -> None:
    raise RuntimeError("password=SuperSecret123 " + ("E" * size_bytes))


def _worker_crash() -> None:
    os._exit(42)


def _worker_sleep(seconds: float) -> str:
    time.sleep(seconds)
    return "done"


# ---- REL-001: Multiprocessing isolation -----------------------------------------------


def test_rel001_small_and_large_multimegabyte_payloads():
    assert isolation.run_isolated(f"{__name__}:_worker_small", 21, timeout=15.0) == 42

    # 4 MB payload — far larger than the ~64 KB OS pipe buffer (previously deadlocked!)
    size = 4 * 1024 * 1024
    data = isolation.run_isolated(f"{__name__}:_worker_large_payload", size, timeout=20.0)
    assert isinstance(data, bytes)
    assert len(data) == size
    assert data[:4] == b"ZZZZ" and data[-4:] == b"ZZZZ"


def test_rel001_large_exception_sanitized_and_no_deadlock():
    with pytest.raises(isolation.IsolationError) as exc_info:
        isolation.run_isolated(f"{__name__}:_worker_large_exception", 200_000, timeout=15.0)
    msg = str(exc_info.value)
    assert "SuperSecret123" not in msg
    assert "truncated" in msg


def test_rel001_child_crash_and_timeout():
    with pytest.raises(isolation.IsolationError, match="exit code 42"):
        isolation.run_isolated(f"{__name__}:_worker_crash", timeout=15.0)

    with pytest.raises(isolation.IsolationTimeoutError, match="timed out"):
        isolation.run_isolated(f"{__name__}:_worker_sleep", 10.0, timeout=0.4)


def test_rel001_parent_cancellation():
    cancel = threading.Event()

    def trigger():
        time.sleep(0.25)
        cancel.set()

    t = threading.Thread(target=trigger, daemon=True)
    t.start()
    with pytest.raises(isolation.IsolationCancelledError, match="cancelled"):
        isolation.run_isolated(f"{__name__}:_worker_sleep", 10.0, timeout=15.0, cancel_event=cancel)
    t.join(timeout=2.0)


def test_rel001_repeated_executions_cleanup():
    for i in range(6):
        assert isolation.run_isolated(f"{__name__}:_worker_small", i, timeout=15.0) == i * 2


# ---- REL-002: Atomic outputs & temp cleanup -------------------------------------------


def test_rel002_atomic_write_preserves_existing_file_and_cleans_tmp(tmp_path):
    target = tmp_path / "important.pdf"
    target.write_bytes(b"ORIGINAL-CONTENT")

    class _ExplodingWriter:
        def write(self, fh):
            fh.write(b"PARTIAL-")
            raise OSError("simulated disk error")

    with pytest.raises(OSError, match="simulated disk error"):
        core._write(_ExplodingWriter(), target)

    # Original file is completely intact and no .tmp files remain
    assert target.read_bytes() == b"ORIGINAL-CONTENT"
    assert list(tmp_path.glob("*.tmp")) == []


# ---- REL-003: Attachment extraction security ------------------------------------------


@pytest.mark.parametrize(
    "malicious",
    [
        "../../../etc/passwd",
        "/etc/shadow",
        r"C:\Windows\System32\evil.dll",
        "C:evil.txt",
        r"\\attacker\share\payload.exe",
        "invoice_\u202efdp.exe",
        "bad\x00name.txt",
        "CON.txt",
        "NUL",
        "..",
    ],
)
def test_rel003_safe_filename_neutralizes_hostile_names(malicious):
    safe = core._safe_filename(malicious, "attachment")
    assert "/" not in safe and "\\" not in safe
    assert ".." not in safe
    assert "\x00" not in safe
    assert "\u202e" not in safe
    assert ":" not in safe
    assert safe.upper().split(".")[0] not in ("CON", "NUL")


def test_rel003_extract_media_blocks_symlink_escape_and_enforces_total_cap(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    c = canvas.Canvas(str(pdf_path))
    c.drawString(72, 720, "Host PDF")
    c.save()

    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        pdf.attachments["a.txt"] = b"A" * 500
        pdf.attachments["b.txt"] = b"B" * 500
        pdf.attachments["c.txt"] = b"C" * 500
        pdf.save(pdf_path)

    # 1. Aggregate total byte cap (allow 1000 bytes -> 2 files extracted, 3rd skipped)
    out1 = tmp_path / "out_cap"
    res = core.extract_media(
        pdf_path, out1, images=False, attachments=True,
        max_attachment_bytes=600, max_total_attachment_bytes=1000,
    )
    assert len(res["attachments"]) == 2
    assert any("total extraction limit" in s for s in res.get("skipped", []))

    # 2. Symlink attack: outdir/attachments points outside outdir
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    out2 = tmp_path / "out_sym"
    out2.mkdir()
    att_link = out2 / "attachments"
    try:
        att_link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")

    with pytest.raises(core.PdfManagerError, match="symbolic link"):
        core.extract_media(pdf_path, out2, images=False, attachments=True)
    assert list(outside.iterdir()) == []
