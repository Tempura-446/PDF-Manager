# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Static code-quality guardrails and resource-hygiene tests.

These checks keep the codebase free of common Python footguns (bare excepts,
mutable default arguments, unclosed inline file handles, ``shell=True``,
deprecated Pillow APIs) and ensure release metadata stays in sync.
"""

import ast
import re
import warnings
from pathlib import Path

import pikepdf
import pytest

import pdfmanager
from pdfmanager import core

ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = ROOT / "pdfmanager"
TESTS_DIR = ROOT / "tests"


def _iter_python_files(directory: Path):
    return sorted(p for p in directory.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_bare_except_or_mutable_defaults():
    """Enforce zero bare ``except:`` and zero mutable default arguments in pdfmanager/."""
    bare_excepts = []
    mutable_defaults = []

    for path in _iter_python_files(PKG_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(ROOT)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                bare_excepts.append(f"{rel}:{node.lineno}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defaults = list(node.args.defaults) + [
                    d for d in node.args.kw_defaults if d is not None
                ]
                for default in defaults:
                    if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                        mutable_defaults.append(f"{rel}:{node.lineno} ({node.name})")

    assert not bare_excepts, f"Bare except clauses found: {bare_excepts}"
    assert not mutable_defaults, f"Mutable default arguments found: {mutable_defaults}"


def test_no_unclosed_inline_open_calls():
    """Reject ``open(...).read()`` / ``.write()`` / ``.readlines()`` without a context manager."""
    leaks = []
    for path in _iter_python_files(PKG_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(ROOT)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("read", "readlines", "readline", "write", "writelines"):
                    inner = node.func.value
                    if (
                        isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)
                        and inner.func.id == "open"
                    ):
                        leaks.append(f"{rel}:{node.lineno}")

    assert not leaks, f"Unclosed inline open(...) calls found: {leaks}"


def test_no_subprocess_shell_true():
    """Reject ``shell=True`` in pdfmanager/ to prevent command injection."""
    hits = []
    for path in _iter_python_files(PKG_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(ROOT)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (
                        kw.arg == "shell"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        hits.append(f"{rel}:{node.lineno}")

    assert not hits, f"shell=True calls found: {hits}"


def test_no_deprecated_pillow_getdata():
    """Guard against Pillow 14 deprecated ``Image.getdata()`` calls."""
    hits = []
    for directory in (PKG_DIR, TESTS_DIR):
        for path in _iter_python_files(directory):
            if path.name == Path(__file__).name:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            rel = path.relative_to(ROOT)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "getdata"
                ):
                    hits.append(f"{rel}:{node.lineno}")

    assert not hits, f"Deprecated Pillow .getdata() calls found: {hits}"


def test_version_metadata_consistency():
    """Ensure package version matches CHANGELOG and packaging manifests."""
    version = pdfmanager.__version__

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m_changelog = re.search(r"^##\s+(\d+\.\d+\.\d+)\b", changelog, re.MULTILINE)
    assert m_changelog, "No version heading found in CHANGELOG.md"
    assert m_changelog.group(1) == version, (
        f"CHANGELOG.md top version ({m_changelog.group(1)}) != __version__ ({version})"
    )

    brew = (ROOT / "packaging" / "homebrew" / "zpdf.rb").read_text(encoding="utf-8")
    m_brew = re.search(r'^\s*version\s+"([^"]+)"', brew, re.MULTILINE)
    assert m_brew and m_brew.group(1) == version, (
        f"Homebrew formula version != __version__ ({version})"
    )

    winget = (ROOT / "packaging" / "winget" / "Tempura446.ZPDF.yaml").read_text(encoding="utf-8")
    m_winget = re.search(r"^PackageVersion:\s*(\S+)", winget, re.MULTILINE)
    assert m_winget and m_winget.group(1) == version, (
        f"WinGet PackageVersion != __version__ ({version})"
    )
    assert f"/v{version}/ZPDF-{version}-windows-standalone.zip" in winget, (
        f"WinGet InstallerUrl does not match __version__ ({version})"
    )


def test_open_pikepdf_no_warning_and_handles_encryption(tmp_path):
    """core.open_pikepdf suppresses spurious password warnings on unencrypted PDFs
    while still decrypting password-protected PDFs."""
    plain = tmp_path / "plain.pdf"
    locked = tmp_path / "locked.pdf"

    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        pdf.save(plain)
        pdf.save(locked, encryption=pikepdf.Encryption(user="secret", owner="owner"))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with core.open_pikepdf(plain, password="unused-batch-password") as pdf:
            assert len(pdf.pages) == 1
    assert not [w for w in caught if issubclass(w.category, UserWarning)], (
        f"Unexpected UserWarning(s): {[str(w.message) for w in caught]}"
    )

    with core.open_pikepdf(locked, password="secret") as pdf:
        assert len(pdf.pages) == 1

    with pytest.raises(pikepdf.PasswordError):
        core.open_pikepdf(locked, password=None)

    with pytest.raises(pikepdf.PasswordError):
        core.open_pikepdf(locked, password="wrong")
