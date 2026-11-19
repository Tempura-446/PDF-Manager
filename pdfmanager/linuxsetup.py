# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Linux desktop integration: Freedesktop .desktop entries, file manager actions
(Nautilus scripts & KDE service menus), distro-aware install hints, and Linux
package install-kind detection (AppImage vs .deb)."""

import os
import shlex
import shutil
import stat
import sys
from pathlib import Path

from pdfmanager import appicon, core

DESKTOP_FILENAME = "zpdf.desktop"
ICON_NAME = "zpdf"

_PACKAGE_HINTS = {
    "libreoffice": {
        "apt": "sudo apt install libreoffice",
        "dnf": "sudo dnf install libreoffice",
        "pacman": "sudo pacman -S libreoffice-fresh",
    },
    "tesseract": {
        "apt": "sudo apt install tesseract-ocr",
        "dnf": "sudo dnf install tesseract",
        "pacman": "sudo pacman -S tesseract",
    },
    "ghostscript": {
        "apt": "sudo apt install ghostscript",
        "dnf": "sudo dnf install ghostscript",
        "pacman": "sudo pacman -S ghostscript",
    },
    "chromium": {
        "apt": "sudo apt install chromium",
        "dnf": "sudo dnf install chromium",
        "pacman": "sudo pacman -S chromium",
    },
    "scanimage": {
        "apt": "sudo apt install sane-utils",
        "dnf": "sudo dnf install sane-backends",
        "pacman": "sudo pacman -S sane",
    },
}


def xdg_data_home(override=None):
    """Return ``$XDG_DATA_HOME`` (or ``~/.local/share``)."""
    if override is not None:
        return Path(override)
    env = os.environ.get("XDG_DATA_HOME", "").strip()
    return Path(env) if env else (Path.home() / ".local" / "share")


def desktop_entry(exec_path, icon_name=ICON_NAME):
    """Return the contents of a Freedesktop ``.desktop`` entry for ZPDF."""
    quoted = shlex.quote(str(exec_path))
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=ZPDF",
        "GenericName=PDF Manager",
        "Comment=Read, edit, OCR, sign, and organize PDF files offline",
        f"Exec={quoted} %F",
        f"Icon={icon_name}",
        "Terminal=false",
        "Categories=Office;Utility;",
        "MimeType=application/pdf;",
        "StartupNotify=true",
        "StartupWMClass=ZPDF",
    ]
    return "\n".join(lines) + "\n"


def install_desktop_entry(exec_path, data_home=None):
    """Install ``zpdf.desktop`` and the 256x256 PNG icon into XDG user dirs."""
    root = xdg_data_home(data_home)
    apps_dir = root / "applications"
    icons_dir = root / "icons" / "hicolor" / "256x256" / "apps"
    apps_dir.mkdir(parents=True, exist_ok=True)
    icons_dir.mkdir(parents=True, exist_ok=True)

    icon_file = icons_dir / f"{ICON_NAME}.png"
    img = appicon.make_icon().resize((256, 256))
    img.save(icon_file)

    desktop_file = apps_dir / DESKTOP_FILENAME
    desktop_file.write_text(desktop_entry(exec_path, icon_name=str(icon_file)), encoding="utf-8")
    return {"desktop": str(desktop_file), "icon": str(icon_file)}


def remove_desktop_entry(data_home=None):
    """Remove the user ``zpdf.desktop`` entry and icon if present."""
    root = xdg_data_home(data_home)
    removed = []
    for path in (
        root / "applications" / DESKTOP_FILENAME,
        root / "icons" / "hicolor" / "256x256" / "apps" / f"{ICON_NAME}.png",
    ):
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    return {"removed": removed}


def _kde_service_menu(exec_path):
    quoted = shlex.quote(str(exec_path))
    return (
        "[Desktop Entry]\n"
        "Type=Service\n"
        "MimeType=application/pdf;\n"
        "Actions=open;compress;ocr;\n"
        "X-KDE-Submenu=ZPDF\n"
        "\n"
        "[Desktop Action open]\n"
        "Name=Open in ZPDF\n"
        f"Exec={quoted} %F\n"
        "\n"
        "[Desktop Action compress]\n"
        "Name=Compress PDF with ZPDF\n"
        f"Exec={quoted} --tool compress %F\n"
        "\n"
        "[Desktop Action ocr]\n"
        "Name=Make Searchable (OCR) with ZPDF\n"
        f"Exec={quoted} --tool ocr %F\n"
    )


def install_file_manager_actions(exec_path, data_home=None):
    """Install Nautilus right-click scripts and KDE Dolphin service menu."""
    root = xdg_data_home(data_home)
    quoted = shlex.quote(str(exec_path))

    # Nautilus script
    nautilus_dir = root / "nautilus" / "scripts"
    nautilus_dir.mkdir(parents=True, exist_ok=True)
    nautilus_script = nautilus_dir / "Open in ZPDF"
    nautilus_script.write_text(
        "#!/bin/sh\n"
        f"exec {quoted} \"$@\"\n",
        encoding="utf-8",
    )
    nautilus_script.chmod(nautilus_script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # KDE KIO service menu
    kde_dir = root / "kio" / "servicemenus"
    kde_dir.mkdir(parents=True, exist_ok=True)
    kde_file = kde_dir / DESKTOP_FILENAME
    kde_file.write_text(_kde_service_menu(exec_path), encoding="utf-8")

    return {"nautilus": str(nautilus_script), "kde": str(kde_file)}


def remove_file_manager_actions(data_home=None):
    """Remove Nautilus script and KDE service menu if present."""
    root = xdg_data_home(data_home)
    removed = []
    for path in (
        root / "nautilus" / "scripts" / "Open in ZPDF",
        root / "kio" / "servicemenus" / DESKTOP_FILENAME,
    ):
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    return {"removed": removed}


def missing_program_hint(program, which=None):
    """Return a plain-language Linux install command hint for ``program``
    based on which package manager (apt, dnf, pacman) is available."""
    key = str(program or "").strip().lower()
    aliases = {"gs": "ghostscript", "chrome": "chromium", "google-chrome": "chromium", "sane": "scanimage"}
    key = aliases.get(key, key)
    hints = _PACKAGE_HINTS.get(key)
    if not hints:
        raise core.PdfManagerError(f"unknown program: {program!r}")
    finder = which or shutil.which
    for mgr in ("apt", "dnf", "pacman"):
        if finder(mgr):
            return hints[mgr]
    return f"{hints['apt']} (Debian/Ubuntu) or {hints['dnf']} (Fedora)"


def detect_install_kind(exe_path=None, env=None):
    """Identify how ZPDF is installed on Linux: ``'appimage'``, ``'deb'``,
    ``'frozen'``, ``'source'``, or ``'pip'``."""
    environ = env if env is not None else os.environ
    if environ.get("APPIMAGE"):
        return "appimage"
    exe = Path(exe_path or sys.executable)
    exe_str = str(exe)
    if exe_str.startswith(("/usr/bin/", "/opt/zpdf/")):
        return "deb"
    if getattr(sys, "frozen", False):
        return "frozen"
    here = Path(__file__).resolve().parent.parent
    if (here / "pyproject.toml").exists() or (here / ".git").exists():
        return "source"
    return "pip"
