# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for Priority #8 Phase 1: Linux desktop integration, SANE scanning, and Linux packaging."""

import importlib.util
import os
import stat
import types
from pathlib import Path

import pytest
from PIL import Image

from pdfmanager import core, linuxsetup, sane, scanner

ROOT = Path(__file__).resolve().parent.parent


def _load_build_linux():
    spec = importlib.util.spec_from_file_location("build_linux", ROOT / "packaging" / "build_linux.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_desktop_entry_install_and_remove(tmp_path):
    res = linuxsetup.install_desktop_entry("/opt/zpdf/ZPDF", data_home=tmp_path)
    desktop = Path(res["desktop"])
    icon = Path(res["icon"])
    assert desktop.is_file()
    assert icon.is_file()
    content = desktop.read_text(encoding="utf-8")
    assert "Name=ZPDF" in content
    assert "MimeType=application/pdf;" in content
    assert "Exec=/opt/zpdf/ZPDF %F" in content

    removed = linuxsetup.remove_desktop_entry(data_home=tmp_path)
    assert len(removed["removed"]) == 2
    assert not desktop.exists()
    assert not icon.exists()


def test_file_manager_actions_install_and_remove(tmp_path):
    res = linuxsetup.install_file_manager_actions("/usr/bin/zpdf", data_home=tmp_path)
    nautilus = Path(res["nautilus"])
    kde = Path(res["kde"])
    assert nautilus.is_file()
    assert kde.is_file()
    if os.name != "nt":
        assert bool(nautilus.stat().st_mode & stat.S_IXUSR)
    kde_text = kde.read_text(encoding="utf-8")
    assert "X-KDE-Submenu=ZPDF" in kde_text
    assert "MimeType=application/pdf;" in kde_text

    removed = linuxsetup.remove_file_manager_actions(data_home=tmp_path)
    assert len(removed["removed"]) == 2
    assert not nautilus.exists()
    assert not kde.exists()


def test_missing_program_hints_and_install_kind():
    assert "apt install tesseract-ocr" in linuxsetup.missing_program_hint("tesseract", which=lambda m: m == "apt")
    assert "dnf install ghostscript" in linuxsetup.missing_program_hint("gs", which=lambda m: m == "dnf")
    assert "pacman -S sane" in linuxsetup.missing_program_hint("sane", which=lambda m: m == "pacman")
    with pytest.raises(core.PdfManagerError):
        linuxsetup.missing_program_hint("nonexistent-tool")

    assert linuxsetup.detect_install_kind(env={"APPIMAGE": "/tmp/ZPDF.AppImage"}) == "appimage"
    assert linuxsetup.detect_install_kind(exe_path="/opt/zpdf/ZPDF", env={}) == "deb"
    assert linuxsetup.detect_install_kind(exe_path="/usr/bin/zpdf", env={}) == "deb"


def test_sane_devices_and_flatbed_feeder_scan(monkeypatch):
    raw = "epson2:libusb:001:004\tEpson Perfection V39\nairscan:e0:Canon\tCanon MF644Cdw\n"
    parsed = sane.parse_device_list(raw)
    assert len(parsed) == 2
    assert parsed[0]["id"] == "sane:epson2:libusb:001:004"
    assert parsed[0]["name"] == "Epson Perfection V39"
    assert parsed[0]["network"] is False
    assert parsed[1]["network"] is True

    def fake_list_runner(cmd, timeout=15):
        assert "-f" in cmd
        return types.SimpleNamespace(returncode=0, stdout=raw, stderr="")

    devs = sane.devices(runner=fake_list_runner, which=lambda _: "/usr/bin/scanimage")
    assert len(devs) == 2

    def fake_scan_runner(cmd, timeout=120):
        if "-o" in cmd:
            out_path = Path(cmd[cmd.index("-o") + 1])
            Image.new("RGB", (60, 80), "white").save(out_path)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        batch_arg = next(a for a in cmd if a.startswith("--batch="))
        pattern = batch_arg.split("=", 1)[1]
        for idx in (1, 2):
            Image.new("L", (60, 80), 240).save(pattern % idx)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    flatbed_pages = sane.scan(
        "sane:epson2:libusb:001:004",
        mode="colour",
        dpi=200,
        source="flatbed",
        runner=fake_scan_runner,
        which=lambda _: "/usr/bin/scanimage",
    )
    assert len(flatbed_pages) == 1
    assert flatbed_pages[0][1] == 200

    seen = []
    feeder_pages = sane.scan(
        "sane:epson2:libusb:001:004",
        mode="grey",
        dpi=300,
        source="feeder",
        on_page=lambda img, dpi: seen.append((img.size, dpi)),
        runner=fake_scan_runner,
        which=lambda _: "/usr/bin/scanimage",
    )
    assert len(feeder_pages) == 2
    assert seen == [((60, 80), 300), ((60, 80), 300)]

    # Verify scanner.scan_any routes sane: device IDs to sane.scan
    monkeypatch.setattr(sane, "scan", lambda dev, **kw: [(Image.new("L", (10, 10), 255), kw["dpi"])])
    routed = scanner.scan_any("sane:epson2:libusb:001:004", dpi=150)
    assert len(routed) == 1 and routed[0][1] == 150


def test_build_linux_stage_deb_and_appdir(tmp_path):
    builder = _load_build_linux()
    fake_app = tmp_path / "dist" / "ZPDF"
    fake_app.mkdir(parents=True)
    (fake_app / "ZPDF").write_bytes(b"#!/bin/sh\necho ZPDF\n")

    stage, ver, arch = builder.stage_deb(fake_app, tmp_path / "deb_stage", version="0.60.0", arch="amd64")
    assert ver == "0.60.0"
    assert arch == "amd64"
    assert (stage / "DEBIAN" / "control").is_file()
    assert (stage / "usr" / "bin" / "zpdf").is_file()
    assert (stage / "usr" / "share" / "applications" / "zpdf.desktop").is_file()
    assert (stage / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "zpdf.png").is_file()
    assert (stage / "opt" / "zpdf" / "ZPDF").is_file()

    appdir = builder.stage_appdir(fake_app, tmp_path / "ZPDF.AppDir")
    assert (appdir / "AppRun").is_file()
    assert (appdir / "zpdf.desktop").is_file()
    assert (appdir / "zpdf.png").is_file()
    assert (appdir / "usr" / "bin" / "ZPDF").is_file()
