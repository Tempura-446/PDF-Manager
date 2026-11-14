# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Pin ZPDF's packages, each with its SHA-256 hash, for the Windows installer.

    python packaging/lock.py            writes packaging/windows/requirements-lock.txt
    python packaging/lock.py --out FILE

Run it on Windows with the Python the installer uses (3.13, see
$PythonVersion in packaging/windows/install.ps1), or let GitHub make it:
Actions > Lock the installer's packages > Run workflow, then download the
"requirements-lock" artifact. Check the file in; the next release zip carries
it. With it there, install.ps1 installs exactly these versions with
``pip install --require-hashes``: a package whose file doesn't match its
hash (a hijacked or replaced version on PyPI) is refused and the
installation stops. Without it, or on a Python other than the one the file
was made for, the installer installs the newest versions as before.

It does what ``pip-compile --generate-hashes`` does, with pip alone: pip
works out every package ZPDF[app,ocr] needs (``pip install --dry-run
--report``, which installs nothing) and the hash of the file it picked; a
package pip reports no hash for is downloaded (``pip download``) and hashed
here, as ``pip hash`` would. setuptools is pinned too, so ZPDF itself is
built from the folder without fetching anything unpinned.

The hashes are those of the files for this computer's Python and system:
make the file on the kind of computer it's for.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packaging" / "windows" / "requirements-lock.txt"
EXTRAS = "app,ocr"  # what install.ps1 installs
BUILD = ["setuptools>=70.1"]  # builds ZPDF itself (pyproject.toml), wheels included


def _pip(*args, **kwargs):
    return subprocess.run([sys.executable, "-m", "pip", "--disable-pip-version-check", *args],
                          check=True, **kwargs)


def resolve(root, workdir):
    """What pip would install for ZPDF[app,ocr] and its build, as its report."""
    report = Path(workdir) / "report.json"
    _pip("install", "--dry-run", "--ignore-installed", "--quiet", "--prefer-binary",
         "--report", str(report), f"{root}[{EXTRAS}]", *BUILD)
    return json.loads(report.read_text(encoding="utf-8"))


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download_hash(name, version, workdir):
    """The hash of the file pip downloads for name==version (pip hash's way)."""
    dest = Path(workdir) / f"dl-{name}"
    _pip("download", "--no-deps", "--quiet", "--prefer-binary", "--dest", str(dest), f"{name}=={version}")
    files = sorted(dest.iterdir())
    if len(files) != 1:
        raise SystemExit(f"Expected one file for {name}=={version}, got {[f.name for f in files]}")
    return _sha256(files[0])


def pins(report, hash_of=None):
    """(name, version, sha256) for each package in the report, ZPDF itself
    (a folder) left out, sorted by name. ``hash_of(name, version)`` gives the
    hash of one the report has none for."""
    out = []
    for item in report["install"]:
        info = item.get("download_info") or {}
        if "dir_info" in info:
            continue  # ZPDF, from the folder
        meta = item["metadata"]
        name = meta["name"].lower().replace("_", "-").replace(".", "-")
        archive = info.get("archive_info") or {}
        sha = (archive.get("hashes") or {}).get("sha256")
        if not sha and str(archive.get("hash", "")).startswith("sha256="):
            sha = archive["hash"].split("=", 1)[1]
        if not sha:
            if hash_of is None:
                raise SystemExit(f"No hash for {name}")
            sha = hash_of(name, meta["version"])
        out.append((name, meta["version"], sha))
    return sorted(out)


def render(entries, python=None, platform=None):
    python = python or "%d.%d" % sys.version_info[:2]
    platform = platform or sysconfig.get_platform()
    lines = [
        "# ZPDF's packages for the Windows installer, each pinned with its hash.",
        "# Made by packaging/lock.py; install.ps1 installs them with --require-hashes.",
        "# Don't edit by hand: run packaging/lock.py again (see its docstring).",
        f"# python: {python}",
        f"# platform: {platform}",
        "",
    ]
    for name, version, sha in entries:
        lines += [f"{name}=={version} \\", f"    --hash=sha256:{sha}"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=str(OUT), help="where to write the file")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("Note: not on Windows, so these hashes are for this system, not the installer's.",
              file=sys.stderr)
    with tempfile.TemporaryDirectory() as workdir:
        report = resolve(ROOT, workdir)
        entries = pins(report, lambda name, version: download_hash(name, version, workdir))
    Path(args.out).write_text(render(entries), encoding="utf-8", newline="\n")
    print(f"Wrote {args.out}: {len(entries)} packages, for Python {'%d.%d' % sys.version_info[:2]} "
          f"on {sysconfig.get_platform()}")


if __name__ == "__main__":
    main()
