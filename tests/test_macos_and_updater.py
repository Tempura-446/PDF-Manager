# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for Priority #8 Phases 2-3: macOS bundle Info.plist, Quick Actions, .dmg staging, and 3-OS updater."""

import importlib.util
import io
import json
import plistlib
from pathlib import Path

import pytest

from pdfmanager import core, macsetup, update

ROOT = Path(__file__).resolve().parent.parent


def _load_build_macos():
    spec = importlib.util.spec_from_file_location("build_macos", ROOT / "packaging" / "build_macos.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_info_plist_declares_pdf_document_types():
    xml_bytes = macsetup.info_plist("0.60.0")
    parsed = plistlib.loads(xml_bytes)
    assert parsed["CFBundleName"] == "ZPDF"
    assert parsed["CFBundleShortVersionString"] == "0.60.0"
    assert parsed["NSHighResolutionCapable"] is True
    doc_types = parsed["CFBundleDocumentTypes"]
    assert len(doc_types) == 1
    assert "pdf" in doc_types[0]["CFBundleTypeExtensions"]
    assert "public.pdf" in doc_types[0]["LSItemContentTypes"]


def test_gatekeeper_note_and_brew_hints():
    note = macsetup.gatekeeper_note("0.60.0")
    assert "xattr -dr com.apple.quarantine /Applications/ZPDF.app" in note
    assert "Open Anyway" in note

    assert "brew install tesseract" == macsetup.missing_program_hint("tesseract")
    assert "brew install ghostscript" == macsetup.missing_program_hint("gs")
    assert "brew install --cask libreoffice" == macsetup.missing_program_hint("libreoffice")
    with pytest.raises(core.PdfManagerError):
        macsetup.missing_program_hint("unknown-program")


def test_finder_quick_action_install_and_remove(tmp_path):
    res = macsetup.install_quick_action("/Applications/ZPDF.app/Contents/MacOS/ZPDF", services_dir=tmp_path)
    wf = Path(res["workflow"])
    assert (wf / "Contents" / "Info.plist").is_file()
    assert (wf / "Contents" / "document.wflow").is_file()

    info = plistlib.loads((wf / "Contents" / "Info.plist").read_bytes())
    assert info["NSServices"][0]["NSMenuItem"]["default"] == "Open in ZPDF"

    removed = macsetup.remove_quick_action(services_dir=tmp_path)
    assert removed["removed"] == [str(wf)]
    assert not wf.exists()


def test_build_macos_patch_and_stage_dmg(tmp_path):
    builder = _load_build_macos()
    fake_app = tmp_path / "dist" / "ZPDF.app"
    (fake_app / "Contents" / "MacOS").mkdir(parents=True)
    (fake_app / "Contents" / "MacOS" / "ZPDF").write_bytes(b"#!/bin/sh\n")

    stage, ver = builder.stage_dmg(fake_app, tmp_path / "dmg_stage", version="0.60.0")
    assert ver == "0.60.0"
    plist_file = stage / "ZPDF.app" / "Contents" / "Info.plist"
    assert plist_file.is_file()
    parsed = plistlib.loads(plist_file.read_bytes())
    assert parsed["CFBundleShortVersionString"] == "0.60.0"
    assert "pdf" in parsed["CFBundleDocumentTypes"][0]["CFBundleTypeExtensions"]
    readme = stage / "README-Unsigned-macOS.txt"
    assert readme.is_file()
    assert "quarantine" in readme.read_text(encoding="utf-8")


def test_updater_selects_platform_release_asset(monkeypatch):
    assert update.release_asset_candidates("0.60.0", "win32") == ["ZPDF-0.60.0-windows.zip"]
    assert update.release_asset_candidates("0.60.0", "darwin")[0] == "ZPDF-0.60.0-macos.dmg"
    assert update.release_asset_candidates("0.60.0", "linux")[0] == "ZPDF-0.60.0-linux.zip"

    # Simulate a release carrying macOS + Linux + Windows assets
    payload = json.dumps({
        "tag_name": "v0.61.0",
        "assets": [
            {"name": "ZPDF-0.61.0-windows.zip", "url": "https://example/win.zip"},
            {"name": "ZPDF-0.61.0-windows.zip.sha256", "url": "https://example/win.sha"},
            {"name": "ZPDF-0.61.0-macos.dmg", "url": "https://example/mac.dmg"},
            {"name": "ZPDF-0.61.0-macos.dmg.sha256", "url": "https://example/mac.sha"},
            {"name": "ZPDF-0.61.0-linux.zip", "url": "https://example/lin.zip"},
            {"name": "ZPDF-0.61.0-linux.zip.sha256", "url": "https://example/lin.sha"},
        ],
    }).encode("utf-8")

    monkeypatch.setattr(update, "_open", lambda req: io.BytesIO(payload))
    monkeypatch.setattr(update.sys, "platform", "darwin")
    ver, info = update._latest_release()
    assert ver == "0.61.0"
    assert info["asset"] == "ZPDF-0.61.0-macos.dmg"
    assert info["zip"] == "https://example/mac.dmg"

    monkeypatch.setattr(update.sys, "platform", "linux")
    ver, info = update._latest_release()
    assert info["asset"] == "ZPDF-0.61.0-linux.zip"

    # Graceful fallback when only windows.zip is present on a release
    win_only = json.dumps({
        "tag_name": "v0.60.0",
        "assets": [
            {"name": "ZPDF-0.60.0-windows.zip", "url": "https://example/win.zip"},
            {"name": "ZPDF-0.60.0-windows.zip.sha256", "url": "https://example/win.sha"},
        ],
    }).encode("utf-8")
    monkeypatch.setattr(update, "_open", lambda req: io.BytesIO(win_only))
    monkeypatch.setattr(update.sys, "platform", "darwin")
    ver, info = update._latest_release()
    assert ver == "0.60.0"
    assert info["asset"] == "ZPDF-0.60.0-windows.zip"
