"""Build a standalone, double-clickable PDF Manager app with PyInstaller.

    pip install -e ".[build]"
    python packaging/build_app.py

Produces, in dist/:
  - Windows: "PDF Manager/PDF Manager.exe" (zipped as PDF-Manager-windows.zip)
  - macOS:   "PDF Manager.app"             (zipped as PDF-Manager-macos.zip)
  - Linux:   "PDF Manager/PDF Manager"     (zipped as PDF-Manager-linux.zip)

The app opens in its own window (pywebview). OCR still needs Tesseract
installed on the computer; everything else is bundled.
"""

import platform
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "app"
NAME = "PDF Manager"
ACCENT = (194, 65, 43)


def make_icon(size=1024):
    """Draw the app icon (the same mark as in the app's sidebar)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([size * 0.06, size * 0.06, size * 0.94, size * 0.94],
                        radius=size * 0.22, fill=ACCENT)
    white = (255, 255, 255)
    # The page glyph spans x 5..19, y 3..21 on its 24-unit grid; centre it
    # and make it 60% of the tile's height.
    u = size * 0.60 / 18
    w = round(size * 0.055)

    def p(x, y):
        return (size / 2 + (x - 12) * u, size / 2 + (y - 12) * u)

    def stroke(*points):
        pts = [p(*pt) for pt in points]
        d.line(pts, fill=white, width=w, joint="curve")
        for x, y in (pts[0], pts[-1]):  # round caps
            d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=white)

    stroke((7, 3), (14, 3), (19, 8), (19, 19), (17, 21), (7, 21), (5, 19), (5, 5), (7, 3))
    stroke((14, 3), (14, 8), (19, 8))
    stroke((9, 13), (15, 13))
    stroke((9, 17), (13, 17))
    return img


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
        icon.save(icon_path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
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
    archive = shutil.make_archive(str(dist / f"PDF-Manager-{label}"), "zip", dist, target.name)
    print(f"\nBuilt {target}\nZipped to {archive}")


if __name__ == "__main__":
    main()
