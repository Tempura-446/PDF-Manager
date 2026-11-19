# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Build and validate distributable ZPDF language packs (ZPDF-lang-<code>-<version>.zip + .sha256)."""
import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build_pack(code, ui_json_path, out_dir, *, name=None, version=None, automatic=True,
               ocr_dir=None, meaning_npz=None):
    """Validate ``ui_json_path`` against ``pdfmanager/locales/en.json`` and write
    ``ZPDF-lang-<code>-<version>.zip`` + ``.sha256`` into ``out_dir``."""
    sys.path.insert(0, str(ROOT))
    try:
        from pdfmanager import __version__, i18n
    finally:
        sys.path.remove(str(ROOT))

    norm_code = i18n.normalize_code(code)
    ver = version or __version__
    ui_data = json.loads(Path(ui_json_path).read_text(encoding="utf-8"))
    errors = i18n.validate_catalog(ui_data)
    if errors:
        raise ValueError("invalid locale catalog: " + "; ".join(errors))

    meta = i18n.BUILTIN_LOCALES.get(norm_code, {})
    manifest = {
        "code": norm_code,
        "name": name or meta.get("name") or norm_code,
        "version": ver,
        "automatic": bool(automatic),
        "dir": i18n.language_dir(norm_code),
    }

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    zip_path = out_path / f"ZPDF-lang-{norm_code}-{ver}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        zf.writestr("ui.json", json.dumps(ui_data, indent=2, ensure_ascii=False) + "\n")
        if ocr_dir:
            ocr_root = Path(ocr_dir)
            for asset in sorted(ocr_root.rglob("*")):
                if asset.is_file():
                    zf.write(asset, "ocr/" + asset.relative_to(ocr_root).as_posix())
        if meaning_npz:
            zf.write(Path(meaning_npz), "meaning/words.npz")

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    sha_path = out_path / (zip_path.name + ".sha256")
    sha_path.write_text(f"{digest}  {zip_path.name}\n", encoding="ascii", newline="\n")
    return zip_path, sha_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build a ZPDF language pack zip + SHA-256 checksum.")
    parser.add_argument("code", help="BCP-47 language code (e.g. zh-Hans, es, fr, de)")
    parser.add_argument("--ui", help="path to ui.json (default: pdfmanager/locales/<code>.json)")
    parser.add_argument("--name", help="display name in that language")
    parser.add_argument("--version", help="ZPDF version tag (default: current pdfmanager.__version__)")
    parser.add_argument("--ocr", help="optional directory of OCR model files to bundle under ocr/")
    parser.add_argument("--meaning", help="optional words.npz table to bundle under meaning/words.npz")
    parser.add_argument("--out", default=str(ROOT / "dist" / "langpacks"), help="output directory")
    args = parser.parse_args(argv)

    ui_path = Path(args.ui) if args.ui else (ROOT / "pdfmanager" / "locales" / f"{args.code}.json")
    zip_path, sha_path = build_pack(
        args.code, ui_path, args.out, name=args.name, version=args.version,
        ocr_dir=args.ocr, meaning_npz=args.meaning,
    )
    print(f"Built {zip_path}\n      {sha_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
