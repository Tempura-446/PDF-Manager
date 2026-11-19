# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Patch ZPDF.app Info.plist with PDF document types and build an unsigned macOS .dmg + .sha256."""

import argparse
import hashlib
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _write_sha256(path):
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    sha_path = Path(str(path) + ".sha256")
    sha_path.write_text(f"{digest}  {Path(path).name}\n", encoding="ascii", newline="\n")
    return sha_path


def patch_info_plist(app_bundle, *, version=None):
    """Merge ZPDF PDF document types and version metadata into ``ZPDF.app/Contents/Info.plist``."""
    sys.path.insert(0, str(ROOT))
    try:
        from pdfmanager import __version__, macsetup
    finally:
        sys.path.remove(str(ROOT))

    ver = version or __version__
    contents = Path(app_bundle) / "Contents"
    contents.mkdir(parents=True, exist_ok=True)
    plist_path = contents / "Info.plist"
    existing = {}
    if plist_path.is_file():
        try:
            existing = plistlib.loads(plist_path.read_bytes())
        except Exception:
            existing = {}
    merged = {**existing, **macsetup.info_plist_dict(ver)}
    plist_path.write_bytes(plistlib.dumps(merged, fmt=plistlib.FMT_XML))
    return plist_path


def stage_dmg(app_bundle, stage_dir, *, version=None):
    """Stage ``ZPDF.app``, an ``Applications`` symlink (on POSIX), and the
    unsigned-build Gatekeeper README into ``stage_dir``."""
    sys.path.insert(0, str(ROOT))
    try:
        from pdfmanager import __version__, macsetup
    finally:
        sys.path.remove(str(ROOT))

    ver = version or __version__
    src = Path(app_bundle)
    if not src.is_dir():
        raise FileNotFoundError(f"macOS app bundle not found: {src}")

    stage = Path(stage_dir)
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)

    dst_app = stage / src.name
    shutil.copytree(src, dst_app, symlinks=True)
    patch_info_plist(dst_app, version=ver)

    if os.name != "nt":
        apps_link = stage / "Applications"
        if not apps_link.exists():
            os.symlink("/Applications", apps_link)

    (stage / "README-Unsigned-macOS.txt").write_text(macsetup.gatekeeper_note(ver), encoding="utf-8")
    return stage, ver


def build_dmg(app_bundle, out_dir, *, version=None):
    """Build ``ZPDF-<version>-macos.dmg`` + ``.sha256`` via ``hdiutil``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stage, ver = stage_dmg(app_bundle, out / "_dmg_stage", version=version)
    dmg_path = out / f"ZPDF-{ver}-macos.dmg"
    if dmg_path.exists():
        dmg_path.unlink()
    hdiutil = shutil.which("hdiutil")
    if not hdiutil:
        raise RuntimeError("hdiutil is not available on this OS; staged DMG folder is at " + str(stage))
    subprocess.run(
        [
            hdiutil, "create",
            "-volname", f"ZPDF {ver}",
            "-srcfolder", str(stage),
            "-ov", "-format", "UDZO",
            str(dmg_path),
        ],
        check=True,
    )
    shutil.rmtree(stage, ignore_errors=True)
    sha_path = _write_sha256(dmg_path)
    return dmg_path, sha_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Patch ZPDF.app Info.plist and build macOS .dmg.")
    parser.add_argument("--app", default=str(ROOT / "dist" / "ZPDF.app"), help="path to ZPDF.app bundle")
    parser.add_argument("--out", default=str(ROOT / "dist"), help="output directory")
    parser.add_argument("--version", help="override version string")
    args = parser.parse_args(argv)

    patch_info_plist(args.app, version=args.version)
    dmg_path, sha_path = build_dmg(args.app, args.out, version=args.version)
    print(f"Built {dmg_path}\n      {sha_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
