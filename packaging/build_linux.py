# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Build Linux packages (.deb and AppDir / AppImage) from the PyInstaller dist/ZPDF directory."""

import argparse
import hashlib
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _deb_arch():
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "amd64"
    if m in ("aarch64", "arm64"):
        return "arm64"
    return m


def _write_sha256(path):
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    sha_path = Path(str(path) + ".sha256")
    sha_path.write_text(f"{digest}  {Path(path).name}\n", encoding="ascii", newline="\n")
    return sha_path


def stage_deb(app_dir, stage_dir, *, version=None, arch=None):
    """Populate a Debian package staging tree under ``stage_dir``."""
    sys.path.insert(0, str(ROOT))
    try:
        from pdfmanager import __version__, appicon, linuxsetup
    finally:
        sys.path.remove(str(ROOT))

    ver = version or __version__
    deb_arch = arch or _deb_arch()
    src = Path(app_dir)
    if not src.is_dir():
        raise FileNotFoundError(f"PyInstaller app directory not found: {src}")

    stage = Path(stage_dir)
    if stage.exists():
        shutil.rmtree(stage)

    debian_dir = stage / "DEBIAN"
    opt_dir = stage / "opt" / "zpdf"
    bin_dir = stage / "usr" / "bin"
    apps_dir = stage / "usr" / "share" / "applications"
    icons_dir = stage / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"

    for d in (debian_dir, bin_dir, apps_dir, icons_dir):
        d.mkdir(parents=True, exist_ok=True)

    shutil.copytree(src, opt_dir)

    wrapper = bin_dir / "zpdf"
    wrapper.write_text("#!/bin/sh\nexec /opt/zpdf/ZPDF \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)

    (apps_dir / "zpdf.desktop").write_text(
        linuxsetup.desktop_entry("/usr/bin/zpdf", icon_name="zpdf"),
        encoding="utf-8",
    )
    appicon.make_icon().resize((256, 256)).save(icons_dir / "zpdf.png")

    control = (
        f"Package: zpdf\n"
        f"Version: {ver}\n"
        f"Section: utils\n"
        f"Priority: optional\n"
        f"Architecture: {deb_arch}\n"
        f"Depends: libgl1, libegl1, libnss3, libxkbcommon-x11-0\n"
        f"Maintainer: Tempura-446 <noreply@github.com>\n"
        f"Description: ZPDF offline PDF reader, editor, OCR, and manager\n"
        f" Read, edit, merge, split, OCR, sign, translate, and organize PDF files offline.\n"
    )
    (debian_dir / "control").write_text(control, encoding="utf-8")
    return stage, ver, deb_arch


def build_deb(app_dir, out_dir, *, version=None, arch=None):
    """Build ``zpdf_<version>_<arch>.deb`` + ``.sha256`` using ``dpkg-deb``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stage_dir = out / "_deb_stage"
    stage, ver, deb_arch = stage_deb(app_dir, stage_dir, version=version, arch=arch)
    deb_path = out / f"zpdf_{ver}_{deb_arch}.deb"
    dpkg = shutil.which("dpkg-deb")
    if not dpkg:
        raise RuntimeError("dpkg-deb is not installed; staged tree is at " + str(stage))
    subprocess.run([dpkg, "--build", "--root-owner-group", str(stage), str(deb_path)], check=True)
    shutil.rmtree(stage, ignore_errors=True)
    sha_path = _write_sha256(deb_path)
    return deb_path, sha_path


def stage_appdir(app_dir, appdir_path):
    """Populate a standards-compliant ``ZPDF.AppDir`` ready for ``appimagetool``."""
    sys.path.insert(0, str(ROOT))
    try:
        from pdfmanager import appicon, linuxsetup
    finally:
        sys.path.remove(str(ROOT))

    src = Path(app_dir)
    if not src.is_dir():
        raise FileNotFoundError(f"PyInstaller app directory not found: {src}")

    dest = Path(appdir_path)
    if dest.exists():
        shutil.rmtree(dest)
    usr_bin = dest / "usr" / "bin"
    usr_bin.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, usr_bin)

    apprun = dest / "AppRun"
    apprun.write_text(
        "#!/bin/sh\n"
        "HERE=\"$(dirname \"$(readlink -f \"$0\")\")\"\n"
        "exec \"$HERE/usr/bin/ZPDF\" \"$@\"\n",
        encoding="utf-8",
    )
    apprun.chmod(apprun.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    (dest / "zpdf.desktop").write_text(
        linuxsetup.desktop_entry("ZPDF", icon_name="zpdf"),
        encoding="utf-8",
    )
    appicon.make_icon().resize((256, 256)).save(dest / "zpdf.png")
    return dest


def build_appimage(app_dir, out_dir, *, version=None):
    """Stage ``ZPDF.AppDir`` and invoke ``appimagetool`` if installed."""
    sys.path.insert(0, str(ROOT))
    try:
        from pdfmanager import __version__
    finally:
        sys.path.remove(str(ROOT))

    ver = version or __version__
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    appdir = stage_appdir(app_dir, out / "ZPDF.AppDir")
    tool = shutil.which("appimagetool")
    if not tool:
        return appdir, None
    arch = platform.machine() or "x86_64"
    img_path = out / f"ZPDF-{ver}-{arch}.AppImage"
    subprocess.run([tool, str(appdir), str(img_path)], check=True)
    sha_path = _write_sha256(img_path)
    return img_path, sha_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build Linux .deb and AppDir/AppImage packages from dist/ZPDF.")
    parser.add_argument("--app-dir", default=str(ROOT / "dist" / "ZPDF"), help="PyInstaller output directory")
    parser.add_argument("--out", default=str(ROOT / "dist"), help="output directory")
    parser.add_argument("--deb", action="store_true", help="build .deb package")
    parser.add_argument("--appdir", action="store_true", help="stage ZPDF.AppDir (and .AppImage if appimagetool is present)")
    args = parser.parse_args(argv)

    do_deb = args.deb or not args.appdir
    do_appdir = args.appdir or not args.deb
    if do_deb:
        deb_path, sha_path = build_deb(args.app_dir, args.out)
        print(f"Built {deb_path}\n      {sha_path}")
    if do_appdir:
        out_item, sha_path = build_appimage(args.app_dir, args.out)
        print(f"Built {out_item}" + (f"\n      {sha_path}" if sha_path else " (staged AppDir)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
