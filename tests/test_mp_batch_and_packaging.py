# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Regression tests for multi-core ProcessPool batch_run and multiplatform packaging."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pdfmanager import core, progress


def _square_or_fail(x: int) -> tuple[int, int]:
    if x == 3:
        raise ValueError("bad item 3")
    return x * x, os.getpid()


def test_batch_run_processes_preserves_order_and_isolates_errors():
    seen_progress = []
    results = core.batch_run(
        [1, 2, 3, 4, 5],
        _square_or_fail,
        max_workers=2,
        processes=True,
        on_progress=lambda done, total: seen_progress.append((done, total)),
    )
    assert len(results) == 5
    assert [r["index"] for r in results] == [0, 1, 2, 3, 4]
    assert [r["ok"] for r in results] == [True, True, False, True, True]
    assert results[0]["result"][0] == 1
    assert results[1]["result"][0] == 4
    assert "bad item 3" in results[2]["error"]
    assert results[3]["result"][0] == 16
    assert results[4]["result"][0] == 25
    # Worker PIDs must be child processes (different from parent PID)
    assert all(r["result"][1] != os.getpid() for r in results if r["ok"])
    assert seen_progress[-1] == (5, 5)


def test_batch_run_processes_respects_cancellation():
    job = progress.Job()
    job.cancel()
    job.run(core.batch_run, [1, 2, 3, 4], _square_or_fail, max_workers=2, processes=True)
    assert job.state == "cancelled"


def test_packaging_manifests_and_deb_depends(tmp_path):
    root = Path(__file__).resolve().parent.parent
    cask = (root / "packaging" / "homebrew" / "zpdf.rb").read_text(encoding="utf-8")
    assert 'cask "zpdf"' in cask
    assert "ZPDF-releases" in cask
    assert "xattr -cr" in cask

    winget = (root / "packaging" / "winget" / "Tempura446.ZPDF.yaml").read_text(encoding="utf-8")
    assert "PackageIdentifier: Tempura446.ZPDF" in winget
    assert "windows-standalone.zip" in winget

    # Verify build_linux.stage_deb includes runtime Depends
    import sys
    sys.path.insert(0, str(root / "packaging"))
    try:
        import build_linux
    finally:
        sys.path.remove(str(root / "packaging"))

    fake_app = tmp_path / "ZPDF"
    fake_app.mkdir()
    (fake_app / "ZPDF").write_bytes(b"#!/bin/sh\n")
    stage, _, _ = build_linux.stage_deb(fake_app, tmp_path / "stage", version="0.61.3", arch="amd64")
    control = (stage / "DEBIAN" / "control").read_text(encoding="utf-8")
    assert "Depends: libgl1, libegl1, libnss3, libxkbcommon-x11-0" in control
