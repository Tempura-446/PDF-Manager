# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Build and validate distributable ZPDF language packs (ZPDF-lang-<code>-<version>.zip + .sha256)."""
import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _extract_argos_into_models_dir(argos_zip, models_root, i18n_mod):
    """Unpack CTranslate2 + SentencePiece files from an .argosmodel zip into ``models_root/<src>-<dst>/``."""
    with zipfile.ZipFile(argos_zip, "r") as zf:
        names = zf.namelist()
        meta_member = next((n for n in names if Path(n).name == "metadata.json" and ".." not in Path(n).parts), None)
        meta = json.loads(zf.read(meta_member).decode("utf-8")) if meta_member else {}
        s = i18n_mod.argos_to_bcp47(meta["from_code"])
        d = i18n_mod.argos_to_bcp47(meta["to_code"])
        dest = Path(models_root) / f"{s}-{d}"
        dest.mkdir(parents=True, exist_ok=True)
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = [p for p in info.filename.replace("\\", "/").lstrip("/").split("/") if p]
            if not parts or ".." in parts or "." in parts or any(p.lower() == "stanza" for p in parts):
                continue
            if "model" in parts:
                rel = parts[parts.index("model") + 1 :]
                if not rel:
                    continue
            elif parts[-1].endswith(".model") or parts[-1] in ("metadata.json", "README.md"):
                rel = [parts[-1]]
            else:
                continue
            target = (dest.joinpath(*rel)).resolve()
            if dest.resolve() not in target.parents and target != dest.resolve():
                raise ValueError(f"unsafe path in argos archive: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
        return f"{s}-{d}"


def build_pack(code, ui_json_path, out_dir, *, name=None, version=None, automatic=True,
               ocr_dir=None, meaning_npz=None, models_dir=None, argos_zips=None,
               allow_empty_ui=False):
    """Validate ``ui_json_path`` against ``pdfmanager/locales/en.json`` and write
    ``ZPDF-lang-<code>-<version>.zip`` + ``.sha256`` into ``out_dir``."""
    import tempfile

    sys.path.insert(0, str(ROOT))
    try:
        from pdfmanager import __version__, i18n
    finally:
        sys.path.remove(str(ROOT))

    norm_code = i18n.normalize_code(code)
    ver = version or __version__
    if ui_json_path and Path(ui_json_path).is_file():
        ui_data = json.loads(Path(ui_json_path).read_text(encoding="utf-8"))
        errors = i18n.validate_catalog(ui_data)
        if errors:
            raise ValueError("invalid locale catalog: " + "; ".join(errors))
    elif allow_empty_ui:
        ui_data = {}
    else:
        raise FileNotFoundError(f"locale catalog not found: {ui_json_path}")

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
    with tempfile.TemporaryDirectory(prefix="zpdf-build-pack-") as tmp:
        staged_models = Path(tmp) / "models"
        if argos_zips:
            for az in argos_zips:
                _extract_argos_into_models_dir(az, staged_models, i18n)

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
            for mroot in [Path(models_dir) if models_dir else None, staged_models if staged_models.is_dir() else None]:
                if not mroot or not mroot.is_dir():
                    continue
                for asset in sorted(mroot.rglob("*")):
                    if asset.is_file():
                        zf.write(asset, "models/" + asset.relative_to(mroot).as_posix())

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    sha_path = out_path / (zip_path.name + ".sha256")
    sha_path.write_text(f"{digest}  {zip_path.name}\n", encoding="ascii", newline="\n")
    return zip_path, sha_path


def _download_argos_zips_for(code, work_dir):
    """Download official Argos en->code and code->en .argosmodel archives into ``work_dir``."""
    sys.path.insert(0, str(ROOT))
    try:
        from pdfmanager import i18n
    finally:
        sys.path.remove(str(ROOT))

    norm = i18n.normalize_code(code)
    entries = i18n.fetch_argos_index(use_cache=False)
    wanted = [
        e for e in entries
        if (e["src"] == "en" and e["dst"] == norm) or (e["src"] == norm and e["dst"] == "en")
    ]
    if not wanted:
        raise RuntimeError(f"no Argos Translate models found in index for {norm!r}")
    downloaded = []
    for entry in wanted:
        data = None
        last_err = None
        for link in entry["links"]:
            try:
                data = i18n._http_get_bytes(link, timeout=240)
                break
            except Exception as exc:
                last_err = exc
        if data is None:
            raise RuntimeError(f"failed to download {entry['pair']}: {last_err}")
        out = Path(work_dir) / f"{entry['pair']}.argosmodel"
        out.write_bytes(data)
        downloaded.append(out)
    return downloaded


def main(argv=None):
    import tempfile

    parser = argparse.ArgumentParser(description="Build a ZPDF language pack zip + SHA-256 checksum.")
    parser.add_argument("code", help="BCP-47 language code (e.g. zh-Hans, es, fr, de)")
    parser.add_argument("--ui", help="path to ui.json (default: pdfmanager/locales/<code>.json)")
    parser.add_argument("--name", help="display name in that language")
    parser.add_argument("--version", help="ZPDF version tag (default: current pdfmanager.__version__)")
    parser.add_argument("--ocr", help="optional directory of OCR model files to bundle under ocr/")
    parser.add_argument("--meaning", help="optional words.npz table to bundle under meaning/words.npz")
    parser.add_argument("--models", help="optional directory of <src>-<dst>/ CTranslate2 models to bundle under models/")
    parser.add_argument("--argos-model", action="append", default=[], help="path to .argosmodel zip(s) to bundle under models/")
    parser.add_argument("--fetch-argos", action="store_true", help="fetch official Argos en<->code translation models automatically")
    parser.add_argument("--allow-empty-ui", action="store_true", help="allow building a pack without a ui.json file")
    parser.add_argument("--out", default=str(ROOT / "dist" / "langpacks"), help="output directory")
    args = parser.parse_args(argv)

    ui_path = Path(args.ui) if args.ui else (ROOT / "pdfmanager" / "locales" / f"{args.code}.json")
    with tempfile.TemporaryDirectory(prefix="zpdf-argos-fetch-") as tmp:
        argos_zips = [Path(p) for p in args.argos_model]
        if args.fetch_argos:
            argos_zips.extend(_download_argos_zips_for(args.code, tmp))
        zip_path, sha_path = build_pack(
            args.code, ui_path, args.out, name=args.name, version=args.version,
            ocr_dir=args.ocr, meaning_npz=args.meaning, models_dir=args.models,
            argos_zips=argos_zips, allow_empty_ui=args.allow_empty_ui,
        )
    print(f"Built {zip_path}\n      {sha_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
