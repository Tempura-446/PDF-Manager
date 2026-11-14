# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""The installer's hash-pinned packages: packaging/lock.py, which writes
packaging/windows/requirements-lock.txt, and how install.ps1 uses it."""

import hashlib
import importlib.util
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL_PS1 = ROOT / "packaging" / "windows" / "install.ps1"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lock = load("lock")

REPORT = {"install": [
    {"metadata": {"name": "pdf-manager", "version": "0.31.0"},
     "download_info": {"url": "file:///zpdf", "dir_info": {}}},
    {"metadata": {"name": "Pillow", "version": "11.0.0"},
     "download_info": {"url": "https://files/pillow.whl", "archive_info": {
         "hash": "sha256=" + "a" * 64, "hashes": {"sha256": "a" * 64}}}},
    {"metadata": {"name": "typing_extensions", "version": "4.12.2"},
     "download_info": {"url": "https://files/te.whl", "archive_info": {"hash": "sha256=" + "b" * 64}}},
    {"metadata": {"name": "proxy.tools", "version": "0.1.0"},
     "download_info": {"url": "https://files/proxy_tools.tar.gz", "archive_info": {}}},
]}


def test_pins_every_package_but_zpdf_with_its_hash():
    asked = []
    entries = lock.pins(REPORT, lambda name, version: asked.append((name, version)) or "c" * 64)
    assert entries == [("pillow", "11.0.0", "a" * 64), ("proxy-tools", "0.1.0", "c" * 64),
                       ("typing-extensions", "4.12.2", "b" * 64)]
    assert asked == [("proxy-tools", "0.1.0")]  # downloaded and hashed only when pip gave no hash


def test_a_package_without_a_hash_is_never_written_without_one():
    with pytest.raises(SystemExit):
        lock.pins(REPORT)


def test_the_file_says_which_python_it_is_for_as_install_ps1_reads_it():
    text = lock.render([("pillow", "11.0.0", "a" * 64)], python="3.13", platform="win-amd64")
    assert "pillow==11.0.0 \\\n    --hash=sha256:" + "a" * 64 in text
    pattern = re.search(r"-match '(\^# python: [^']+)'", INSTALL_PS1.read_text(encoding="utf-8-sig")).group(1)
    pattern = pattern.replace("\\\\", "\\")
    assert [m.group(1) for line in text.splitlines()[:10] if (m := re.match(pattern, line))] == ["3.13"]
    assert lock.EXTRAS == "app,ocr" and any(b.startswith("setuptools") for b in lock.BUILD)


def test_installer_uses_the_pinned_packages_only_when_they_are_there():
    text = INSTALL_PS1.read_text(encoding="utf-8-sig")
    assert '$lock = Join-Path $PSScriptRoot "requirements-lock.txt"' in text
    assert "if (Test-Path -LiteralPath $lock)" in text
    branch = text[text.index("if ($lockPython -and ($venvVersion -eq $lockPython))"):text.index("} else {", text.index("$lockPython -and"))]
    assert "--require-hashes -r $lock" in branch and "--no-deps" in branch
    # A hash that doesn't match stops the installation; nothing unpinned is tried instead.
    assert "throw" in branch.split("--require-hashes")[1].split('"$source"')[0]
    assert '"$source[app' not in branch and "--upgrade pip" not in branch
    assert "--no-build-isolation --no-index" in branch  # ZPDF itself: nothing fetched
    # Without the file, today's installation as before.
    rest = text[text.index("} else {", text.index("$lockPython -and")):]
    assert '"$source[app,ocr]"' in rest and '"$source[app]"' in rest


def test_release_zip_carries_the_file():
    assert any("packaging/windows/".startswith(p) for p in load("release").INCLUDE)


def _wheel(folder):
    path = folder / "zpdflocktest-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("zpdflocktest/__init__.py", "")
        z.writestr("zpdflocktest-1.0.dist-info/METADATA",
                   "Metadata-Version: 2.1\nName: zpdflocktest\nVersion: 1.0\n")
        z.writestr("zpdflocktest-1.0.dist-info/WHEEL",
                   "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        z.writestr("zpdflocktest-1.0.dist-info/RECORD", "")
    return path


@pytest.mark.parametrize("matching", [True, False])
def test_pip_refuses_a_package_whose_hash_does_not_match(tmp_path, matching):
    """The file lock.py writes, as pip reads it with --require-hashes (a dry
    run from a local folder: nothing is installed or downloaded)."""
    wheel = _wheel(tmp_path)
    sha = hashlib.sha256(wheel.read_bytes()).hexdigest() if matching else "0" * 64
    lockfile = tmp_path / "requirements-lock.txt"
    lockfile.write_text(lock.render([("zpdflocktest", "1.0", sha)]), encoding="utf-8")
    out = subprocess.run([sys.executable, "-m", "pip", "install", "--dry-run", "--no-index",
                          "--find-links", str(tmp_path), "--no-deps", "--require-hashes",
                          "--disable-pip-version-check", "--ignore-installed", "-r", str(lockfile)],
                         capture_output=True, text=True, timeout=120)
    if "No module named pip" in out.stderr:
        pytest.skip("needs pip")
    if matching:
        assert out.returncode == 0, out.stdout + out.stderr
    else:
        assert out.returncode != 0 and "hash" in (out.stdout + out.stderr).lower()
