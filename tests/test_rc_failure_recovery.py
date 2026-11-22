# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Phase 7 RC-008: Data integrity, failure, and recovery validation tests."""

import hashlib
import os
import stat
import sys
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pdfmanager import applog, core, isolation, progress


def _make_pdf(path: Path, text: str = "RC-008 integrity test page.") -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(72, 720, text)
    c.showPage()
    c.save()
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_corrupt_input_fails_safely_without_creating_partial_output(tmp_path):
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"%PDF-1.7\nthis is truncated garbage without xref")
    out = tmp_path / "out.pdf"

    with pytest.raises(core.PdfManagerError):
        core.rotate(bad, out, 90)
    assert not out.exists()


def test_failed_encryption_preserves_original_and_leaves_no_partial_output(tmp_path):
    src = _make_pdf(tmp_path / "original.pdf")
    orig_hash = _sha256(src)
    out = tmp_path / "protected.pdf"

    # Restricted permissions with matching user/owner password must fail cleanly
    from pdfmanager import filetools
    perms = filetools.permissions(printing="none", changes="none", copy=False)
    with pytest.raises(core.PdfManagerError):
        core.encrypt(src, out, user_password="SamePassword!", owner_password="SamePassword!", permissions=perms)

    assert _sha256(src) == orig_hash
    assert not out.exists()


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX non-root chmod test")
def test_readonly_destination_preserves_original_and_formats_permission_error(tmp_path):
    src = _make_pdf(tmp_path / "input.pdf")
    orig_hash = _sha256(src)
    ro_dir = tmp_path / "readonly"
    ro_dir.mkdir()
    ro_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
    out = ro_dir / "out.pdf"
    try:
        with pytest.raises(PermissionError) as exc_info:
            core.rotate(src, out, 90)
        msg = applog.format_user_error(exc_info.value)
        assert "permission denied" in msg.lower()
        assert _sha256(src) == orig_hash
        assert not out.exists()
    finally:
        ro_dir.chmod(stat.S_IRWXU)


def test_interrupted_batch_job_isolates_failures_and_preserves_good_outputs(tmp_path):
    good1 = _make_pdf(tmp_path / "good1.pdf", "Document 1")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    good2 = _make_pdf(tmp_path / "good2.pdf", "Document 2")

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def process(src: Path):
        dst = out_dir / f"{src.stem}_rot.pdf"
        core.rotate(src, dst, 90)
        return dst

    outcomes = core.batch_run([good1, bad, good2], process, max_workers=2)
    assert [o["ok"] for o in outcomes] == [True, False, True]
    assert (out_dir / "good1_rot.pdf").exists()
    assert not (out_dir / "bad_rot.pdf").exists()
    assert (out_dir / "good2_rot.pdf").exists()


def test_cancelled_batch_job_stops_promptly():
    job = progress.Job()
    started = []

    def work(i):
        started.append(i)
        if i == 0:
            job.cancel()
        progress.check()
        return i

    job.run(core.batch_run, range(20), work, max_workers=1)
    assert job.state == "cancelled"
    assert len(started) <= 2


def test_isolated_worker_crash_and_timeout_do_not_affect_parent():
    # Crash in child
    with pytest.raises(isolation.IsolationError):
        isolation.run_isolated("os:_exit", 139, timeout=10.0)
    # Timeout in child
    with pytest.raises(isolation.IsolationTimeoutError):
        isolation.run_isolated("time:sleep", 5.0, timeout=0.3)
    # Parent remains healthy and subsequent isolated job succeeds
    assert isolation.run_isolated("math:factorial", 6, timeout=10.0) == 720
