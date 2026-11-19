# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Internationalization (i18n) catalog loader and offline language pack manager.

English (``pdfmanager/locales/en.json``) is always built in and acts as the
fallback for any missing key in an installed or older language pack. Installed
packs live in ``<data_dir>/locales/<code>/`` (containing ``manifest.json`` and
``ui.json``) so they survive ZPDF updates.
"""

import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

from pdfmanager import appdata, core, update

DEFAULT_LANG = "en"
RTL_LANGS = {"ar", "he", "fa", "ur"}
_CODE_RE = re.compile(r"^[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8}){0,2}$")
_BUILTIN_DIR = Path(__file__).resolve().parent / "locales"


def normalize_code(code):
    """Validate and normalize a BCP-47 language tag (e.g. ``en``, ``zh-Hans``, ``es``)."""
    raw = str(code or "").strip()
    if not raw or raw.lower() == "auto":
        return "auto"
    if not _CODE_RE.match(raw):
        raise core.PdfManagerError(f"invalid language code: {raw!r}")
    parts = raw.split("-")
    norm = [parts[0].lower()]
    for p in parts[1:]:
        if len(p) == 4:
            norm.append(p.title())  # Script subtag e.g. Hans, Latn
        elif len(p) == 2:
            norm.append(p.upper())  # Region subtag e.g. US, CA
        else:
            norm.append(p)
    return "-".join(norm)


def language_dir(code):
    """Return ``'rtl'`` for right-to-left languages, otherwise ``'ltr'``."""
    base = str(code or DEFAULT_LANG).split("-", 1)[0].lower()
    return "rtl" if base in RTL_LANGS else "ltr"


def packs_dir():
    """Directory where user-installed language packs are stored."""
    return appdata.data_dir() / "locales"


def get_language():
    """Return the configured language setting (``'auto'``, ``'en'``, or a pack code)."""
    val = str(update.settings().get("language") or DEFAULT_LANG).strip()
    try:
        return normalize_code(val)
    except core.PdfManagerError:
        return DEFAULT_LANG


def set_language(code):
    """Persist the chosen UI language (``'auto'``, ``'en'``, or an installed pack code)."""
    norm = normalize_code(code)
    if norm not in ("auto", DEFAULT_LANG):
        installed = {p["code"] for p in list_installed_packs()}
        builtin = {p.stem for p in _BUILTIN_DIR.glob("*.json")} if _BUILTIN_DIR.is_dir() else set()
        if norm not in installed and norm not in builtin:
            raise core.PdfManagerError(f"language pack {norm!r} is not installed")
    update.save_settings(language=None if norm == DEFAULT_LANG else norm)
    return norm


def _read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_catalog(lang=None):
    """Return the merged ``{key: text}`` catalog for ``lang`` with English fallback."""
    en = _read_json(_BUILTIN_DIR / "en.json")
    code = DEFAULT_LANG if not lang or lang == "auto" else normalize_code(lang)
    if code == DEFAULT_LANG:
        return dict(en)
    merged = dict(en)
    builtin_file = _BUILTIN_DIR / f"{code}.json"
    if builtin_file.is_file():
        merged.update({k: v for k, v in _read_json(builtin_file).items() if isinstance(v, str)})
    pack_ui = packs_dir() / code / "ui.json"
    if pack_ui.is_file():
        merged.update({k: v for k, v in _read_json(pack_ui).items() if isinstance(v, str)})
    return merged


def t(key, lang=None, **vars):
    """Look up ``key`` in ``lang`` (defaulting to configured language) and interpolate ``{vars}``."""
    active = lang or get_language()
    catalog = load_catalog(active)
    text = catalog.get(key, key)
    if not vars:
        return text

    def _sub(match):
        name = match.group(1)
        return str(vars[name]) if name in vars else match.group(0)

    return re.sub(r"\{([a-zA-Z0-9_]+)\}", _sub, text)


def list_installed_packs():
    """Return metadata dicts for all installed language packs."""
    root = packs_dir()
    if not root.is_dir():
        return []
    found = []
    for sub in sorted(root.iterdir()):
        manifest_path = sub / "manifest.json"
        if not sub.is_dir() or not manifest_path.is_file():
            continue
        m = _read_json(manifest_path)
        code = m.get("code") or sub.name
        found.append({
            "code": code,
            "name": m.get("name") or code,
            "version": str(m.get("version") or ""),
            "automatic": bool(m.get("automatic", True)),
            "dir": language_dir(code),
            "path": str(sub),
            "has_ocr": any((sub / "ocr").glob("*.onnx")) if (sub / "ocr").is_dir() else False,
            "has_meaning": (sub / "meaning" / "words.npz").is_file() or (sub / "words.npz").is_file(),
            "has_translate": (sub / "models").is_dir() and any((sub / "models").iterdir()),
        })
    return found


_PACK_ASSET_DIRS = frozenset({"ocr", "meaning", "models"})


def _reset_meaning_cache():
    try:
        from pdfmanager import meaning
        meaning.reset()
    except Exception:
        pass


def install_pack_from_zip(zip_path, expected_sha256=None):
    """Install a language pack zip (containing ``manifest.json``, ``ui.json``,
    and optional ``ocr/``, ``meaning/``, ``models/`` assets) into the user's
    app data directory after verifying optional SHA-256 checksum and blocking
    unsafe archive paths."""
    src = Path(zip_path)
    if not src.is_file():
        raise core.PdfManagerError(f"language pack file not found: {src}")
    raw = src.read_bytes()
    if expected_sha256:
        actual = hashlib.sha256(raw).hexdigest().lower()
        if actual != str(expected_sha256).strip().lower():
            raise core.PdfManagerError("language pack checksum does not match; refusing to install")

    with zipfile.ZipFile(src, "r") as zf:
        names = zf.namelist()
        manifest_member = next((n for n in names if Path(n).name == "manifest.json" and ".." not in Path(n).parts), None)
        ui_member = next((n for n in names if Path(n).name == "ui.json" and ".." not in Path(n).parts), None)
        if not manifest_member or not ui_member:
            raise core.PdfManagerError("language pack zip must contain manifest.json and ui.json")
        try:
            manifest = json.loads(zf.read(manifest_member).decode("utf-8"))
            ui_data = json.loads(zf.read(ui_member).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise core.PdfManagerError(f"invalid JSON inside language pack: {exc}") from None
        if not isinstance(manifest, dict) or not isinstance(ui_data, dict):
            raise core.PdfManagerError("language pack manifest.json and ui.json must be JSON objects")
        code = normalize_code(manifest.get("code") or "")
        if code in ("auto", DEFAULT_LANG):
            raise core.PdfManagerError(f"cannot install a language pack for {code!r}")

        dest = (packs_dir() / code).resolve()
        root = packs_dir().resolve()
        if root not in dest.parents:
            raise core.PdfManagerError("unsafe language pack target path")
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "manifest.json").write_text(json.dumps({**manifest, "code": code}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (dest / "ui.json").write_text(json.dumps(ui_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        # Safely extract optional asset folders (ocr/, meaning/, models/)
        extracted_assets = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            member_norm = info.filename.replace("\\", "/").lstrip("/")
            parts = [p for p in member_norm.split("/") if p]
            if not parts or ".." in parts or "." in parts:
                continue
            # Support both top-level "ocr/..." and single-prefix "<pack>/ocr/..."
            if parts[0] in _PACK_ASSET_DIRS:
                rel_parts = parts
            elif len(parts) >= 2 and parts[1] in _PACK_ASSET_DIRS:
                rel_parts = parts[1:]
            else:
                continue
            target = (dest.joinpath(*rel_parts)).resolve()
            if dest not in target.parents:
                raise core.PdfManagerError("unsafe asset path inside language pack")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
            extracted_assets.append("/".join(rel_parts))

    _reset_meaning_cache()
    return {
        "code": code,
        "name": manifest.get("name") or code,
        "version": str(manifest.get("version") or ""),
        "automatic": bool(manifest.get("automatic", True)),
        "dir": language_dir(code),
        "strings": len(ui_data),
        "assets": extracted_assets,
    }


def remove_pack(code):
    """Remove an installed language pack and reset language to English if it was active."""
    norm = normalize_code(code)
    if norm in ("auto", DEFAULT_LANG):
        raise core.PdfManagerError("built-in English cannot be removed")
    dest = (packs_dir() / norm).resolve()
    root = packs_dir().resolve()
    if root not in dest.parents:
        raise core.PdfManagerError("unsafe language pack path")
    if dest.is_dir():
        shutil.rmtree(dest)
    if get_language() == norm:
        set_language(DEFAULT_LANG)
    _reset_meaning_cache()
    return {"removed": norm}


BUILTIN_LOCALES = {
    "en": {"name": "English", "automatic": False},
    "zh-Hans": {"name": "简体中文", "automatic": True},
    "es": {"name": "Español", "automatic": True},
    "fr": {"name": "Français", "automatic": True},
    "de": {"name": "Deutsch", "automatic": True},
}

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


def validate_catalog(strings, reference=None):
    """Validate a translated ``{key: text}`` catalog against English ``reference``.
    Returns a list of error messages (empty list means valid)."""
    ref = reference if reference is not None else _read_json(_BUILTIN_DIR / "en.json")
    errors = []
    if not isinstance(strings, dict):
        return ["catalog must be a JSON object"]
    for key, en_val in ref.items():
        if key not in strings or not isinstance(strings[key], str) or not strings[key].strip():
            errors.append(f"missing or empty translation for {key!r}")
            continue
        expected_vars = set(_PLACEHOLDER_RE.findall(en_val))
        actual_vars = set(_PLACEHOLDER_RE.findall(strings[key]))
        if expected_vars != actual_vars:
            errors.append(f"placeholder mismatch for {key!r}: expected {sorted(expected_vars)}, got {sorted(actual_vars)}")
    return errors


def list_available_languages():
    """List built-in languages plus any user-installed language packs."""
    seen = {}
    if _BUILTIN_DIR.is_dir():
        for f in sorted(_BUILTIN_DIR.glob("*.json")):
            code = f.stem
            meta = BUILTIN_LOCALES.get(code, {"name": code, "automatic": code != DEFAULT_LANG})
            seen[code] = {
                "code": code,
                "name": meta["name"],
                "automatic": meta["automatic"],
                "builtin": True,
                "dir": language_dir(code),
            }
    for pack in list_installed_packs():
        seen[pack["code"]] = {**pack, "builtin": False}
    # Keep English first, then sorted by code
    ordered = []
    if DEFAULT_LANG in seen:
        ordered.append(seen.pop(DEFAULT_LANG))
    ordered.extend(seen[k] for k in sorted(seen))
    return ordered
