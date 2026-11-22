# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Phase 6 product quality, CLI/GUI parity, and packaging regression tests."""

import errno
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest import mock

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pdfmanager import applog, cli, notices, progress

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_pdf(path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(72, 720, "Phase 6 quality test document.")
    c.showPage()
    c.save()
    return path


# ---------------------------------------------------------------------------
# Q-008: Packaging, dependency sync, notices & resource availability
# ---------------------------------------------------------------------------

def test_requirements_txt_matches_pyproject_core_dependencies():
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    core_deps = [
        re.split(r"[<>=!~;\s]", spec, maxsplit=1)[0].strip().lower()
        for spec in pyproject["project"]["dependencies"]
    ]
    req_lines = [
        line.strip()
        for line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    req_deps = [
        re.split(r"[<>=!~;\s]", spec, maxsplit=1)[0].strip().lower()
        for spec in req_lines
    ]
    assert set(core_deps) == set(req_deps)
    assert "numpy" in req_deps


def test_notices_includes_translate_extra():
    assert "app" in notices.EXTRAS
    assert "ocr" in notices.EXTRAS
    assert "translate" in notices.EXTRAS


def test_packaged_gui_and_locale_resources_exist():
    pkg = REPO_ROOT / "pdfmanager"
    assert (pkg / "gui.html").is_file()
    assert (pkg / "static" / "app.css").is_file()
    assert (pkg / "static" / "app-core.js").is_file()
    assert list((pkg / "locales").glob("*.json"))


def test_cli_help_clean_startup():
    proc = subprocess.run(
        [sys.executable, "-m", "pdfmanager", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0
    assert "pdfm" in proc.stdout.lower() or "usage:" in proc.stdout.lower()


# ---------------------------------------------------------------------------
# Q-006: Unified, password-safe error formatting
# ---------------------------------------------------------------------------

def test_format_user_error_translates_os_errors_and_cancellation():
    perm = PermissionError(errno.EACCES, "Permission denied", "/tmp/locked.pdf")
    msg_perm = applog.format_user_error(perm)
    assert "permission denied" in msg_perm.lower()
    assert "locked.pdf" in msg_perm

    enospc = OSError(errno.ENOSPC, "No space left on device", "/tmp/out.pdf")
    msg_disk = applog.format_user_error(enospc)
    assert "disk space" in msg_disk.lower()

    missing = FileNotFoundError(errno.ENOENT, "No such file", "/tmp/missing.pdf")
    msg_missing = applog.format_user_error(missing)
    assert "file not found" in msg_missing.lower()

    assert applog.format_user_error(progress.Cancelled()) == "operation cancelled"


def test_format_user_error_redacts_secrets_while_preserving_filename():
    exc = ValueError("failed opening /tmp/report.pdf with password=SuperSecret123! and Bearer abcdef1234567890")
    msg = applog.format_user_error(exc)
    assert "SuperSecret123!" not in msg
    assert "abcdef1234567890" not in msg
    assert "<redacted>" in msg
    assert "<token>" in msg
    assert "/tmp/report.pdf" in msg


# ---------------------------------------------------------------------------
# Q-003: CLI signed-PDF modification warning parity
# ---------------------------------------------------------------------------

def test_cli_warns_on_stderr_when_mutating_signed_pdf(tmp_path, capsys):
    src = _make_pdf(tmp_path / "signed_contract.pdf")
    out = tmp_path / "rotated.pdf"

    with mock.patch("pdfmanager.risks.signed", return_value=True):
        rc = cli.main(["rotate", str(src), "-d", "90", "-o", str(out)])

    assert rc == 0
    assert out.exists()
    captured = capsys.readouterr()
    assert "digitally signed" in captured.err.lower()
    assert "broken" in captured.err.lower()


def test_cli_does_not_warn_signed_on_readonly_info(tmp_path, capsys):
    src = _make_pdf(tmp_path / "signed_contract.pdf")

    with mock.patch("pdfmanager.risks.signed", return_value=True):
        rc = cli.main(["info", str(src)])

    assert rc == 0
    captured = capsys.readouterr()
    assert "digitally signed" not in captured.err.lower()
