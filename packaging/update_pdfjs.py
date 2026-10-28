"""Download a pdf.js release from npm and copy what the reader needs into
pdfmanager/static/pdfjs/.

    python packaging/update_pdfjs.py 6.4.299

Needs `npm` (only to download the package). Uses pdf.js's "legacy" build,
which adds polyfills for the newest JavaScript features: the app runs in
whatever web engine the computer has (Qt WebEngine, WebView2, WebKit or an
older browser), and the modern build fails in engines even a year old.
Keeps the upstream layout so the viewer stylesheet finds its images; leaves
out source maps, type definitions and the scripting sandbox.
"""

import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "pdfmanager" / "static" / "pdfjs"
# (source in the npm package, destination under static/pdfjs)
KEEP = [
    ("LICENSE", "LICENSE"),
    ("legacy/build/pdf.min.mjs", "build/pdf.min.mjs"),
    ("legacy/build/pdf.worker.min.mjs", "build/pdf.worker.min.mjs"),
    ("legacy/web/pdf_viewer.mjs", "web/pdf_viewer.mjs"),
    ("legacy/web/pdf_viewer.css", "web/pdf_viewer.css"),
    ("legacy/web/images", "web/images"),
    ("cmaps", "cmaps"),                    # character maps for Chinese, Japanese, Korean
    ("standard_fonts", "standard_fonts"),  # the 14 standard fonts when not embedded
    ("wasm", "wasm"),                      # JPEG 2000 / JBIG2 decoders (common in scans)
    ("iccs", "iccs"),
]


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    version = sys.argv[1]
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["npm", "pack", f"pdfjs-dist@{version}", "--silent"], cwd=tmp,
                       check=True, stdout=subprocess.DEVNULL, shell=sys.platform == "win32")
        with tarfile.open(Path(tmp) / f"pdfjs-dist-{version}.tgz") as tar:
            tar.extractall(tmp, filter="data")
        package = Path(tmp) / "package"
        if TARGET.exists():
            shutil.rmtree(TARGET)
        for src_rel, dst_rel in KEEP:
            src, dst = package / src_rel, TARGET / dst_rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
    (TARGET / "VERSION").write_text(f"{version} (legacy build)\n")
    size = sum(f.stat().st_size for f in TARGET.rglob("*") if f.is_file())
    print(f"pdf.js {version} -> {TARGET} ({size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
