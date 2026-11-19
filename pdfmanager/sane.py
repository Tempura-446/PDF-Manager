# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Scanning paper on Linux (and macOS with SANE installed) via ``scanimage``.

Exposes the same lightweight device/scan contract as ``escl.py`` and
``naps2.py`` so ``scanner.all_scanners()`` and ``scanner.scan_any()`` work
transparently on Linux:

    sane.available()                      # bool
    sane.devices()                        # [{"id": "sane:...", "name", "feeder", "flatbed", "duplex", "network"}]
    sane.scan(device_id, mode, dpi, source, duplex=False, on_page=None)
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from pdfmanager import core

PREFIX = "sane:"
_MODE_MAP = {
    "colour": "Color",
    "grey": "Gray",
    "bw": "Lineart",
}
_SOURCE_MAP = {
    "flatbed": "Flatbed",
    "feeder": "ADF",
}


class SaneError(core.PdfManagerError):
    def __init__(self, message, code="general"):
        super().__init__(message)
        self.code = code


def find_scanimage(which=None):
    finder = which or shutil.which
    return finder("scanimage")


def available(which=None):
    return bool(find_scanimage(which=which))


def _run(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def parse_device_list(output):
    """Parse ``scanimage -f '%d\\t%v %m\\n'`` output into scanner dicts."""
    found = []
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line or "\t" not in line:
            continue
        dev_id, label = line.split("\t", 1)
        dev_id = dev_id.strip()
        label = " ".join(label.split()) or dev_id
        if not dev_id:
            continue
        found.append({
            "id": PREFIX + dev_id,
            "name": label,
            "feeder": True,
            "flatbed": True,
            "duplex": False,
            "network": dev_id.startswith(("airscan:", "escl:", "net:")),
        })
    return found


def devices(runner=None, which=None):
    """List scanners visible to SANE's ``scanimage`` command."""
    exe = find_scanimage(which=which)
    if not exe:
        return []
    run = runner or _run
    try:
        proc = run([exe, "-f", "%d\t%v %m\n"], timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return parse_device_list(proc.stdout)


def scan(device_id, mode="grey", dpi=300, source="flatbed", duplex=False, on_page=None,
         runner=None, which=None):
    """Scan one page (flatbed) or a batch (feeder) with ``scanimage``.
    Returns ``[(PIL.Image, dpi)]``."""
    exe = find_scanimage(which=which)
    if not exe:
        raise SaneError(
            "SANE (scanimage) is not installed. Install it with: sudo apt install sane-utils",
            code="unavailable",
        )
    raw_id = str(device_id or "")
    if raw_id.startswith(PREFIX):
        raw_id = raw_id[len(PREFIX):]
    if not raw_id:
        raise SaneError("no SANE scanner device selected", code="none")

    sane_mode = _MODE_MAP.get(mode, "Gray")
    sane_source = _SOURCE_MAP.get(source, "Flatbed")
    if source == "feeder" and duplex:
        sane_source = "ADF Duplex"
    run = runner or _run

    pages = []
    with tempfile.TemporaryDirectory(prefix="zpdf-sane-") as tmp:
        tmp_dir = Path(tmp)
        if source == "feeder":
            pattern = str(tmp_dir / "page-%04d.png")
            cmd = [
                exe, "-d", raw_id,
                "--format=png",
                f"--mode={sane_mode}",
                f"--resolution={int(dpi)}",
                f"--source={sane_source}",
                f"--batch={pattern}",
            ]
            proc = run(cmd, timeout=180)
            files = sorted(tmp_dir.glob("page-*.png"))
            if proc.returncode != 0 and not files:
                err = (proc.stderr or proc.stdout or "scanimage failed").strip()
                code = "empty" if "out of documents" in err.lower() or "empty" in err.lower() else "general"
                raise SaneError(f"SANE scan failed: {err}", code=code)
        else:
            out_file = tmp_dir / "page-0001.png"
            cmd = [
                exe, "-d", raw_id,
                "--format=png",
                f"--mode={sane_mode}",
                f"--resolution={int(dpi)}",
                f"--source={sane_source}",
                "-o", str(out_file),
            ]
            proc = run(cmd, timeout=120)
            if proc.returncode != 0 or not out_file.is_file():
                err = (proc.stderr or proc.stdout or "scanimage failed").strip()
                raise SaneError(f"SANE scan failed: {err}", code="general")
            files = [out_file]

        for img_path in files:
            with Image.open(img_path) as opened:
                img = opened.copy()
            img.info["dpi"] = (int(dpi), int(dpi))
            pages.append((img, int(dpi)))
            if on_page is not None:
                on_page(img, int(dpi))

    if not pages:
        raise SaneError("the scanner returned no pages", code="empty")
    return pages
