"""Build a standalone, double-clickable ZPDF app with PyInstaller.

    pip install -e ".[build]"
    python packaging/build_app.py

Produces, in dist/:
  - Windows: "ZPDF/ZPDF.exe" (zipped as ZPDF-windows.zip)
  - macOS:   "ZPDF.app"             (zipped as ZPDF-macos.zip)
  - Linux:   "ZPDF/ZPDF"     (zipped as ZPDF-linux.zip)

The app opens in its own window (pywebview). OCR still needs Tesseract
installed on the computer; everything else is bundled.
"""

import platform
import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BUILD = ROOT / "build" / "app"
NAME = "ZPDF"

from pdfmanager.appicon import ICO_SIZES, make_icon  # noqa: E402


def main():
    try:
        import PyInstaller.__main__ as pyinstaller
    except ImportError:
        sys.exit('PyInstaller is not installed: pip install -e ".[build]"')

    BUILD.mkdir(parents=True, exist_ok=True)
    icon = make_icon()
    system = platform.system()
    if system == "Windows":
        icon_path = BUILD / "icon.ico"
        icon.save(icon_path, sizes=ICO_SIZES)
    elif system == "Darwin":
        icon_path = BUILD / "icon.icns"
        icon.save(icon_path)
    else:
        icon_path = BUILD / "icon.png"
        icon.resize((256, 256), Image.LANCZOS).save(icon_path)

    sep = ";" if system == "Windows" else ":"
    args = [
        str(ROOT / "packaging" / "launcher.py"),
        "--name", NAME,
        "--windowed",
        "--noconfirm",
        "--clean",
        "--icon", str(icon_path),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(BUILD / "work"),
        "--specpath", str(BUILD),
        "--add-data", f"{ROOT / 'pdfmanager' / 'gui.html'}{sep}pdfmanager",
        "--add-data", f"{ROOT / 'pdfmanager' / 'static'}{sep}pdfmanager/static",
        "--collect-all", "pypdfium2",
        "--collect-all", "pypdfium2_raw",
        "--collect-data", "reportlab",
        "--collect-all", "pikepdf",
        "--collect-data", "pdfminer",  # PDF → Excel: pdfplumber's text engine (its CMaps)
        "--collect-all", "webview",
        # Find the package from the source tree: editable installs use import
        # hooks PyInstaller can't follow.
        "--paths", str(ROOT),
        "--collect-submodules", "pdfmanager",
    ]
    if system == "Darwin":
        args += ["--osx-bundle-identifier", "io.github.pdfmanager"]
    pyinstaller.run(args)

    dist = ROOT / "dist"
    target = dist / (f"{NAME}.app" if system == "Darwin" else NAME)
    label = {"Windows": "windows", "Darwin": "macos"}.get(system, "linux")
    archive = shutil.make_archive(str(dist / f"ZPDF-{label}"), "zip", dist, target.name)
    print(f"\nBuilt {target}\nZipped to {archive}")


if __name__ == "__main__":
    main()
