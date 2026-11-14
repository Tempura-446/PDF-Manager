# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
r"""Where Windows programs are installed: the Program Files folders and the
registry. Shared by the finders of Tesseract (scan.py), Ghostscript
(archive.py) and veraPDF (pdfacheck.py); it imports no other part of ZPDF.
On other systems the registry lookups find nothing."""

import os
from pathlib import Path


def program_dirs():
    r"""Folders Windows programs are installed in: Program Files (64- and
    32-bit) for everyone, and for one user %LOCALAPPDATA%\Programs (and
    %LOCALAPPDATA% itself, where some older installers went)."""
    local = os.environ.get("LOCALAPPDATA")
    found = [os.environ.get(name) for name in ("PROGRAMFILES", "PROGRAMW6432", "PROGRAMFILES(X86)")]
    found += [str(Path(local) / "Programs"), local] if local else []
    return [Path(d) for d in dict.fromkeys(found) if d]


def _registry_keys(key):
    """Open handles of ``key`` under HKEY_LOCAL_MACHINE and HKEY_CURRENT_USER,
    in the 64- and 32-bit views of the registry (Windows only)."""
    try:
        import winreg
    except ImportError:
        return
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0)):
            try:
                handle = winreg.OpenKey(root, key, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with handle:
                yield winreg, handle


def registry_value(key, name):
    """The values called ``name`` of the registry ``key`` (see _registry_keys)."""
    found = []
    for winreg, handle in _registry_keys(key):
        try:
            found.append(winreg.QueryValueEx(handle, name)[0])
        except OSError:
            continue
    return found


def registry_subkey_values(key, name):
    """The values called ``name`` of each sub-key of the registry ``key``."""
    found = []
    for winreg, handle in _registry_keys(key):
        n = 0
        while True:
            try:
                sub = winreg.EnumKey(handle, n)
            except OSError:
                break
            n += 1
            try:
                with winreg.OpenKey(handle, sub) as child:
                    found.append(winreg.QueryValueEx(child, name)[0])
            except OSError:
                continue
    return found
