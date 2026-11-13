"""Finding Tesseract and Ghostscript where Windows installers (and winget)
put them, and the Windows installer's optional OCR and PDF/A step."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pdfmanager import archive, scan, winprograms

INSTALL_PS1 = Path(__file__).resolve().parents[1] / "packaging" / "windows" / "install.ps1"


@pytest.fixture
def windows(tmp_path, monkeypatch):
    """Pretend to be Windows with nothing on the PATH and an empty registry;
    Program Files and the user's local app data are folders in tmp_path."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(winprograms, "registry_value", lambda key, name: [])
    monkeypatch.setattr(winprograms, "registry_subkey_values", lambda key, name: [])
    for name in ("PROGRAMW6432", "PROGRAMFILES(X86)"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "Program Files"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    return tmp_path


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ")
    return path


def test_ghostscript_newest_version_in_program_files(windows):
    gs = windows / "Program Files" / "gs"
    for version in ("gs9.56.1", "gs10.05.1", "gs10.4.0"):
        touch(gs / version / "bin" / "gswin32c.exe")
        touch(gs / version / "bin" / "gswin64c.exe")
    # 10.05 is newer than 9.56, although "gs9" sorts after "gs1" as text.
    assert archive.find_ghostscript() == str(gs / "gs10.05.1" / "bin" / "gswin64c.exe")


def test_ghostscript_from_the_registry(windows, monkeypatch):
    elsewhere = touch(windows / "Apps" / "gs10.06.0" / "bin" / "gswin64c.exe")
    monkeypatch.setattr(winprograms, "registry_subkey_values",
                        lambda key, name: [str(elsewhere.parent / "gsdll64.dll")]
                        if (key, name) == (r"SOFTWARE\GPL Ghostscript", "GS_DLL") else [])
    assert archive.find_ghostscript() == str(elsewhere)


def test_ghostscript_not_installed(windows):
    assert archive.find_ghostscript() is None
    assert scan.find_tesseract() is None


def test_tesseract_in_program_files_or_for_one_user(windows, monkeypatch):
    mine = touch(windows / "Local" / "Programs" / "Tesseract-OCR" / "tesseract.exe")
    assert scan.find_tesseract() == str(mine)
    everyone = touch(windows / "Program Files" / "Tesseract-OCR" / "tesseract.exe")
    assert scan.find_tesseract() == str(everyone)


def test_tesseract_where_the_registry_says(windows, monkeypatch):
    folder = windows / "D" / "OCR"
    touch(folder / "tesseract.exe")
    monkeypatch.setattr(winprograms, "registry_value",
                        lambda key, name: [str(folder)]
                        if (key, name) == (r"SOFTWARE\Tesseract-OCR", "InstallDir") else [])
    assert scan.find_tesseract() == str(folder / "tesseract.exe")


def test_registry_lookups_are_empty_without_windows():
    if sys.platform == "win32":
        pytest.skip("reads the real registry on Windows")
    assert scan.registry_value(r"SOFTWARE\Tesseract-OCR", "InstallDir") == []
    assert scan.registry_subkey_values(r"SOFTWARE\GPL Ghostscript", "GS_DLL") == []


def test_programs_found_off_the_path_are_put_on_it_for_ocrmypdf(tmp_path, monkeypatch):
    tess = touch(tmp_path / "Tesseract-OCR" / "tesseract.exe")
    gs = touch(tmp_path / "gs" / "gs10.05.1" / "bin" / "gswin64c.exe")
    monkeypatch.setattr(scan, "find_tesseract", lambda: str(tess))
    monkeypatch.setattr(archive, "find_ghostscript", lambda: str(gs))
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", str(gs.parent)]))
    scan.add_programs_to_path()
    paths = os.environ["PATH"].split(os.pathsep)
    assert paths[0] == str(tess.parent) and paths.count(str(gs.parent)) == 1 and "/usr/bin" in paths


# -- the installer script ----------------------------------------------------------------

def script():
    return INSTALL_PS1.read_text(encoding="utf-8-sig")


def test_installer_offers_ocr_and_pdfa_programs():
    text = script()
    params = re.search(r"param\((.*?)\n\)", text, re.S).group(1)
    assert "[switch]$WithExtras" in params and "[switch]$NoExtras" in params
    # The old switch names keep working, so instructions written for 0.13 still do.
    assert '[Alias("WithOcr")]' in params and '[Alias("NoOcr")]' in params
    assert '"UB-Mannheim.TesseractOCR"' in text and '"ArtifexSoftware.GhostScript"' in text
    assert "about 150 MB" in text
    # Asked once, and never in an automated run (the Windows end-to-end job).
    assert text.count("Read-Host") == 1 and "$env:CI" in text
    # It checks with ZPDF's own lookups, so what it installs is what the app finds.
    assert "scan.find_tesseract()" in text and "archive.find_ghostscript()" in text


def test_installer_ocr_step_cannot_stop_the_installation():
    text = script()
    step = text[text.index("# Optional, and never stops the installation"):]
    assert step.lstrip().splitlines()[1].strip() == "try {"
    assert "} catch {" in step and "throw" not in step.split("} catch {")[-1]
    for function in ("Install-WithWinget", "Get-OcrStatus"):
        body = text[text.index(f"function {function}"):]
        assert "try {" in body.split("\nfunction ")[0]


def test_installer_works_where_scripts_are_limited():
    """Constrained Language mode (Smart App Control) refuses .NET method
    calls such as [Console]::ReadKey(); the script uses plain commands."""
    text = script()
    assert "]::" not in text and "New-Object" not in text and "Add-Type" not in text
    assert all(ord(c) < 128 for c in text)  # Windows PowerShell reads a BOM-less script as ANSI
    assert "\r\n" in INSTALL_PS1.read_bytes().decode("utf-8-sig")  # see .gitattributes


@pytest.mark.skipif(not shutil.which("pwsh"), reason="needs PowerShell")
def test_installer_parses():
    command = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
               f"'{INSTALL_PS1}', [ref]$null, [ref]$e); $e | ForEach-Object {{ $_.ToString() }}")
    out = subprocess.run(["pwsh", "-NoProfile", "-Command", command], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0 and not out.stdout.strip(), out.stdout + out.stderr


def test_installer_installs_the_app_with_ocr_and_adds_ocrmypdf_from_the_tesseract_extra():
    """ZPDF is installed with its built-in OCR engine ([app,ocr]); the optional
    step adds only OCRmyPDF (ZPDF isn't built again), in the version
    pyproject.toml's "tesseract" extra names."""
    text = script()
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    extra = re.search(r'^tesseract = \[([^\]]*)\]', pyproject, re.M).group(1)  # (tomllib is 3.11+)
    assert re.search(r'pip install [^\n]*"\$source\[app,ocr\]"', text)
    wanted = re.findall(r'pip install [^\n]*"(ocrmypdf[^"]*)"', text)
    assert wanted and [f'"{w}"' for w in wanted] == re.findall(r'"ocrmypdf[^"]*"', extra)


def test_build_collects_the_ocr_engine():
    """The standalone app bundles the built-in engine: rapidocr (with its
    models) and onnxruntime (with its shared libraries)."""
    build = (Path(__file__).resolve().parents[1] / "packaging" / "build_app.py").read_text(encoding="utf-8")
    assert '"--collect-all", "rapidocr"' in build and '"--collect-all", "onnxruntime"' in build


def test_installer_picks_a_python_the_ocr_engine_supports():
    """ONNX Runtime (the built-in OCR engine) has builds for Python 3.10 to
    3.14 on Windows: a newer Python, like 3.15 when it came out, is passed
    over (Python 3.13 is installed for ZPDF if nothing else fits), and an
    existing app environment on one is made again."""
    text = script()
    test_python = text[text.index("function Test-Python"):text.index("function Find-Python")]
    assert "sys.version_info >= (3, 10)" in test_python and "sys.version_info < (3, 15)" in test_python
    find_python = text[text.index("function Find-Python"):text.index("function Install-Python")]
    order = re.findall(r'"(3\.1\d)"', find_python)
    assert order[:2] == ["3.13", "3.12"] and "py.exe" in find_python and "-$version" in find_python
    assert "if (-not (Test-Python $venvPython))" in text  # an environment on 3.15 is made again


def test_installer_finishes_without_ocr_when_it_cant_be_installed():
    """If the OCR packages have no build for this Python, ZPDF is installed
    without them (it works, with OCR from Tesseract if that's there) rather
    than not at all."""
    text = script()
    with_ocr = text.index('"$source[app,ocr]"')
    without = text.index('"$source[app]"', with_ocr)
    failure = text.index('throw "Installing ZPDF\'s packages failed', with_ocr)
    assert with_ocr < without < failure
