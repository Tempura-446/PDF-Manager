# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Internationalization (i18n) catalog loader and offline language pack manager.

English (``pdfmanager/locales/en.json``) is always built in and acts as the
fallback for any missing key in an installed or older language pack. Installed
packs live in ``<data_dir>/locales/<code>/`` (containing ``manifest.json`` and
``ui.json``) so they survive ZPDF updates.
"""

import hashlib
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from pdfmanager import appdata, core, progress, update

DEFAULT_LANG = "en"
RTL_LANGS = {"ar", "he", "fa", "ur"}
_CODE_RE = re.compile(r"^[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8}){0,2}$")
_BUILTIN_DIR = Path(__file__).resolve().parent / "locales"

LANGPACKS_RELEASE_TAG = "langpacks-v1"
NLLB_PACK_CODE = "nllb-200"
NLLB_LARGE_PACK_CODE = "nllb-200-1.3b"
NLLB_XL_PACK_CODE = "nllb-200-3.3b"
NLLB_BASE_URL = "https://huggingface.co/JustFrederik/nllb-200-distilled-600M-ct2-int8/resolve/main"
NLLB_LARGE_BASE_URL = "https://huggingface.co/JustFrederik/nllb-200-distilled-1.3B-ct2-int8/resolve/main"
NLLB_XL_BASE_URL = "https://huggingface.co/OpenNMT/nllb-200-3.3B-ct2-int8/resolve/main"
NLLB_FILES = ("config.json", "shared_vocabulary.txt", "sentencepiece.bpe.model", "model.bin")
NLLB_PACKS = {
    NLLB_PACK_CODE: {
        "name": "High-Quality Universal (NLLB-200 600M · 200 languages)",
        "base_url": NLLB_BASE_URL,
        "size_bytes": 645_000_000,
        "pair_label": "200 languages (direct, fast & high quality)",
        "files": [(f, f"{NLLB_BASE_URL}/{f}") for f in NLLB_FILES],
    },
    NLLB_LARGE_PACK_CODE: {
        "name": "Best Offline Quality (NLLB-200 1.3B · 200 languages)",
        "base_url": NLLB_LARGE_BASE_URL,
        "size_bytes": 1_400_000_000,
        "pair_label": "200 languages (smartest phrasing, balanced RAM)",
        "files": [(f, f"{NLLB_LARGE_BASE_URL}/{f}") for f in NLLB_FILES],
    },
    NLLB_XL_PACK_CODE: {
        "name": "Maximum Offline Quality (NLLB-200 3.3B · 200 languages)",
        "base_url": NLLB_XL_BASE_URL,
        "size_bytes": 3_380_000_000,
        "pair_label": "200 languages (flagship offline model, 8+ GB RAM)",
        "files": [
            ("config.json", f"{NLLB_XL_BASE_URL}/config.json"),
            ("shared_vocabulary.json", f"{NLLB_XL_BASE_URL}/shared_vocabulary.json"),
            ("sentencepiece.bpe.model", f"{NLLB_BASE_URL}/sentencepiece.bpe.model"),
            ("model.bin", f"{NLLB_XL_BASE_URL}/model.bin"),
        ],
    },
}
ARGOS_INDEX_URL = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
_ZPDF_PACK_RE = re.compile(r"^ZPDF-lang-([A-Za-z0-9-]+)-(\d+(?:\.\d+)*)\.zip$")
_ARGOS_TO_BCP47 = {"zh": "zh-Hans", "zt": "zh-Hant", "pb": "pt-BR"}
_BCP47_TO_ARGOS = {v: k for k, v in _ARGOS_TO_BCP47.items()}
_ARGOS_INDEX_CACHE = ".argos-index.json"


def argos_to_bcp47(code):
    """Map an Argos Translate language code (e.g. ``zh``, ``zt``) to ZPDF's BCP-47 tag."""
    raw = str(code or "").strip()
    if raw.lower() in _ARGOS_TO_BCP47:
        return _ARGOS_TO_BCP47[raw.lower()]
    return normalize_code(raw)


def bcp47_to_argos(code):
    """Map a ZPDF BCP-47 tag (e.g. ``zh-Hans``) to Argos Translate's code (``zh``)."""
    norm = normalize_code(code)
    if norm in _BCP47_TO_ARGOS:
        return _BCP47_TO_ARGOS[norm]
    return norm.split("-", 1)[0].lower()


def normalize_code(code):
    """Validate and normalize a BCP-47 language tag (e.g. ``en``, ``zh-Hans``, ``es``)."""
    raw = str(code or "").strip()
    if not raw or raw.lower() == "auto":
        return "auto"
    if raw.lower() in NLLB_PACKS:
        return raw.lower()
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
        models_dir = sub / "models"
        pairs = sorted(d.name for d in models_dir.iterdir() if d.is_dir()) if models_dir.is_dir() else []
        try:
            size_bytes = sum(f.stat().st_size for f in sub.rglob("*") if f.is_file())
        except OSError:
            size_bytes = 0
        found.append({
            "code": code,
            "name": m.get("name") or code,
            "version": str(m.get("version") or ""),
            "automatic": bool(m.get("automatic", True)),
            "dir": language_dir(code),
            "path": str(sub),
            "size_bytes": size_bytes,
            "has_ocr": any((sub / "ocr").glob("*.onnx")) if (sub / "ocr").is_dir() else False,
            "has_meaning": (sub / "meaning" / "words.npz").is_file() or (sub / "words.npz").is_file(),
            "has_translate": bool(pairs),
            "pairs": pairs,
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
    and optional ``ocr/``, ``meaning/``, ``models/`` assets) — or an Argos
    Translate ``.argosmodel`` archive — into the user's app data directory
    after verifying optional SHA-256 checksum and blocking unsafe archive paths."""
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
            # Support Argos Translate .argosmodel zips (metadata.json + CTranslate2 model/)
            has_argos = any(
                Path(n).name in ("metadata.json", "model.bin") or Path(n).suffix == ".model"
                for n in names if ".." not in Path(n).parts
            )
            if has_argos:
                return install_argos_model_zip(src)
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


# ---- Argos .argosmodel extraction & hybrid online catalog ----------------------------

import contextvars
from contextlib import contextmanager

_dl_progress = contextvars.ContextVar("_zpdf_dl_progress", default=None)


@contextmanager
def _download_progress(label, *, expected_bytes=0, frac_start=0.0, frac_end=1.0):
    token = _dl_progress.set((label, int(expected_bytes or 0), float(frac_start), float(frac_end)))
    try:
        yield
    finally:
        _dl_progress.reset(token)


def _http_get_bytes(url, *, headers=None, timeout=30):
    """Download ``url`` over HTTPS and return its raw bytes (reporting byte-level
    progress when called inside ``_download_progress(...)``)."""
    from pdfmanager import policy

    try:
        policy.check_network_allowed("Language pack download")
    except policy.PolicyError as exc:
        raise core.PdfManagerError(str(exc)) from None
    if not str(url or "").startswith("https://"):
        raise core.PdfManagerError(f"refusing non-HTTPS language pack URL: {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": "ZPDF", **(headers or {})})
    ctx = _dl_progress.get()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if not ctx:
                return resp.read()
            label, expected_bytes, frac_start, frac_end = ctx
            raw_len = resp.headers.get("Content-Length") if getattr(resp, "headers", None) else None
            try:
                total_bytes = int(raw_len) if raw_len else expected_bytes
            except (TypeError, ValueError):
                total_bytes = expected_bytes
            buf = bytearray()
            span = max(0.0, frac_end - frac_start)
            while True:
                progress.check()
                chunk = resp.read(262_144)
                if not chunk:
                    break
                buf.extend(chunk)
                got = len(buf)
                if total_bytes > 0:
                    pct = min(100, int(got * 100 / total_bytes))
                    frac = frac_start + span * min(1.0, got / total_bytes)
                    progress.report(
                        message=f"{label} · {_fmt_bytes(got)} / {_fmt_bytes(total_bytes)} ({pct}%)",
                        fraction_in_part=frac,
                    )
                else:
                    progress.report(
                        message=f"{label} · {_fmt_bytes(got)}",
                        fraction_in_part=frac_start,
                    )
            return bytes(buf)
    except urllib.error.HTTPError as exc:
        raise core.PdfManagerError(f"download failed (HTTP {exc.code}) for {url}") from None
    except OSError as exc:
        raise core.PdfManagerError(f"could not reach {url}: {exc}") from None


def install_argos_model_zip(zip_path, *, src_lang=None, dst_lang=None, name=None):
    """Extract an Argos Translate ``.argosmodel`` zip into ZPDF's offline translation
    model layout ``<packs_dir>/<pack_code>/models/<src>-<dst>/``.

    Only CTranslate2 weights/vocabularies and SentencePiece ``.model`` files are
    extracted (Stanza sentence-splitting data is skipped to save disk space).
    """
    src_file = Path(zip_path)
    if not src_file.is_file():
        raise core.PdfManagerError(f"model archive not found: {src_file}")

    with zipfile.ZipFile(src_file, "r") as zf:
        names = zf.namelist()
        meta_member = next(
            (n for n in names if Path(n).name == "metadata.json" and ".." not in Path(n).parts),
            None,
        )
        meta = {}
        if meta_member:
            try:
                parsed = json.loads(zf.read(meta_member).decode("utf-8"))
                if isinstance(parsed, dict):
                    meta = parsed
            except (UnicodeDecodeError, ValueError):
                meta = {}

        raw_src = src_lang or meta.get("from_code")
        raw_dst = dst_lang or meta.get("to_code")
        if not raw_src or not raw_dst:
            raise core.PdfManagerError("model archive is missing from_code/to_code metadata")

        s = argos_to_bcp47(raw_src)
        d = argos_to_bcp47(raw_dst)
        if s == d:
            raise core.PdfManagerError(f"invalid translation model pair: {s} → {d}")

        pack_code = d if d != DEFAULT_LANG else s
        pair_name = f"{s}-{d}"
        root = packs_dir().resolve()
        pack_dest = (packs_dir() / pack_code).resolve()
        if root not in pack_dest.parents:
            raise core.PdfManagerError("unsafe language pack target path")
        model_dest = (pack_dest / "models" / pair_name).resolve()
        if pack_dest not in model_dest.parents:
            raise core.PdfManagerError("unsafe model target path")
        model_dest.mkdir(parents=True, exist_ok=True)

        extracted = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            member_norm = info.filename.replace("\\", "/").lstrip("/")
            parts = [p for p in member_norm.split("/") if p]
            if not parts or ".." in parts or "." in parts:
                continue
            # Skip Stanza data (ZPDF handles sentence splitting directly)
            if any(p.lower() == "stanza" for p in parts):
                continue
            # Flatten CTranslate2 "model/..." contents directly into model_dest
            if "model" in parts:
                idx = parts.index("model")
                rel_parts = parts[idx + 1 :]
                if not rel_parts:
                    continue
            elif parts[-1].endswith(".model") or parts[-1] in ("metadata.json", "README.md"):
                rel_parts = [parts[-1]]
            else:
                continue

            target = model_dest.joinpath(*rel_parts).resolve()
            if model_dest not in target.parents and target != model_dest:
                raise core.PdfManagerError("unsafe file path inside model archive")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
            extracted.append("/".join(rel_parts))

    if not extracted:
        raise core.PdfManagerError("model archive contained no CTranslate2 or SentencePiece model files")

    # Ensure manifest.json & ui.json exist while preserving any existing UI catalog
    manifest_path = pack_dest / "manifest.json"
    existing_manifest = _read_json(manifest_path) if manifest_path.is_file() else {}
    display_name = (
        name
        or existing_manifest.get("name")
        or BUILTIN_LOCALES.get(pack_code, {}).get("name")
        or (meta.get("to_name") if pack_code == d else meta.get("from_name"))
        or pack_code
    )
    manifest_data = {
        "code": pack_code,
        "name": display_name,
        "version": str(existing_manifest.get("version") or meta.get("package_version") or "1.0"),
        "automatic": bool(existing_manifest.get("automatic", True)),
        "dir": language_dir(pack_code),
    }
    manifest_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ui_path = pack_dest / "ui.json"
    if not ui_path.is_file():
        ui_path.write_text("{}\n", encoding="utf-8")

    return {
        "code": pack_code,
        "name": display_name,
        "pair": pair_name,
        "src_lang": s,
        "dst_lang": d,
        "version": manifest_data["version"],
        "dir": language_dir(pack_code),
        "files": len(extracted),
    }


def fetch_zpdf_release_packs(repo=None):
    """Fetch curated ZPDF language packs published under ``LANGPACKS_RELEASE_TAG`` on
    ``Tempura-446/ZPDF-releases``. Returns ``{code: {...}}`` (empty dict if unavailable)."""
    target_repo = repo or update.RELEASES
    url = f"{update.API}/repos/{target_repo}/releases/tags/{LANGPACKS_RELEASE_TAG}"
    try:
        raw = _http_get_bytes(url, headers={"Accept": "application/vnd.github+json"}, timeout=15)
        payload = json.loads(raw.decode("utf-8"))
    except (core.PdfManagerError, ValueError):
        return {}

    assets = payload.get("assets") if isinstance(payload, dict) else []
    if not isinstance(assets, list):
        return {}
    by_name = {a.get("name"): a for a in assets if isinstance(a, dict) and a.get("name")}
    packs = {}
    for asset_name, asset in by_name.items():
        m = _ZPDF_PACK_RE.match(asset_name)
        if not m:
            continue
        try:
            code = normalize_code(m.group(1))
        except core.PdfManagerError:
            continue
        ver = m.group(2)
        dl_url = asset.get("browser_download_url") or ""
        if not dl_url.startswith("https://"):
            continue
        sha_asset = by_name.get(asset_name + ".sha256")
        sha_url = (sha_asset.get("browser_download_url") or "") if sha_asset else ""
        packs[code] = {
            "code": code,
            "name": BUILTIN_LOCALES.get(code, {}).get("name") or code,
            "version": ver,
            "source": "zpdf",
            "download_url": dl_url,
            "sha256_url": sha_url if sha_url.startswith("https://") else "",
            "size_bytes": int(asset.get("size") or 0),
        }
    return packs


def _parse_argos_entries(raw_list):
    pairs = []
    if not isinstance(raw_list, list):
        return pairs
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        links = [u for u in (item.get("links") or []) if isinstance(u, str) and u.startswith("https://")]
        if not links:
            continue
        try:
            s = argos_to_bcp47(item.get("from_code") or "")
            d = argos_to_bcp47(item.get("to_code") or "")
        except core.PdfManagerError:
            continue
        if s == d:
            continue
        pairs.append({
            "src": s,
            "dst": d,
            "pair": f"{s}-{d}",
            "from_name": str(item.get("from_name") or s),
            "to_name": str(item.get("to_name") or d),
            "version": str(item.get("package_version") or ""),
            "links": links,
        })
    return pairs


def fetch_argos_index(use_cache=True):
    """Fetch the public Argos Translate package index (with local cache fallback)."""
    cache_path = packs_dir() / _ARGOS_INDEX_CACHE
    try:
        raw = _http_get_bytes(ARGOS_INDEX_URL, timeout=15)
        parsed = json.loads(raw.decode("utf-8"))
        if isinstance(parsed, list):
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(raw)
            except OSError:
                pass
            return _parse_argos_entries(parsed)
    except (core.PdfManagerError, ValueError):
        pass

    if use_cache and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            return _parse_argos_entries(cached)
        except (OSError, ValueError):
            pass
    return []


def list_catalog_packs(fetch=True):
    """Return the merged hybrid catalog of downloadable language packs:
    1. Curated ZPDF packs from ``Tempura-446/ZPDF-releases`` (``source='zpdf'``).
    2. Community offline translation models from Argos Translate (``source='argos'``).
    """
    installed_map = {p["code"]: p for p in list_installed_packs()}
    zpdf_packs = fetch_zpdf_release_packs() if fetch else {}
    argos_entries = fetch_argos_index(use_cache=True) if fetch else []

    # Group Argos entries by non-English language code (prioritizing en <-> lang pairs)
    argos_by_lang = {}
    for entry in argos_entries:
        if entry["src"] == DEFAULT_LANG and entry["dst"] != DEFAULT_LANG:
            code, label = entry["dst"], entry["to_name"]
        elif entry["dst"] == DEFAULT_LANG and entry["src"] != DEFAULT_LANG:
            code, label = entry["src"], entry["from_name"]
        else:
            continue
        bucket = argos_by_lang.setdefault(code, {
            "code": code,
            "name": BUILTIN_LOCALES.get(code, {}).get("name") or label,
            "version": entry["version"],
            "source": "argos",
            "available_pairs": [],
        })
        if entry["pair"] not in bucket["available_pairs"]:
            bucket["available_pairs"].append(entry["pair"])

    merged = {}
    for code, entry in argos_by_lang.items():
        pairs = sorted(entry["available_pairs"])
        merged[code] = {
            **entry,
            "available_pairs": pairs,
            "size_bytes": len(pairs) * 50_000_000,
            "size_approx": True,
        }
    for code, zpack in zpdf_packs.items():
        argos_pairs = merged.get(code, {}).get("available_pairs", [])
        merged[code] = {
            **zpack,
            "available_pairs": argos_pairs or [f"en-{code}", f"{code}-en"],
            "size_approx": False,
        }

    # Annotate with local installation status
    out = []
    for nllb_code, meta in NLLB_PACKS.items():
        nllb_dir = packs_dir() / nllb_code / "models" / nllb_code
        nllb_installed = (nllb_dir / "model.bin").is_file() and bool(list(nllb_dir.glob("*.model")))
        out.append({
            "code": nllb_code,
            "name": meta["name"],
            "version": "1.0",
            "source": "nllb",
            "available_pairs": [meta["pair_label"]],
            "size_bytes": meta["size_bytes"],
            "size_approx": False,
            "dir": "ltr",
            "installed": nllb_installed,
            "has_translate": nllb_installed,
            "installed_pairs": [nllb_code] if nllb_installed else [],
        })
    for code in sorted(merged):
        item = dict(merged[code])
        inst = installed_map.get(code)
        item["dir"] = language_dir(code)
        item["installed"] = inst is not None
        item["has_translate"] = bool(inst and inst.get("has_translate"))
        item["installed_pairs"] = list(inst.get("pairs", [])) if inst else []
        out.append(item)

    return {
        "packs": out,
        "sources": {
            "nllb": len(NLLB_PACKS),
            "zpdf": len(zpdf_packs),
            "argos": len(argos_by_lang),
        },
    }


def _fmt_bytes(num_bytes):
    """Format a byte count concisely for download progress (e.g. ``645 MB``, ``1.4 GB``)."""
    n = max(0, int(num_bytes or 0))
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f} GB"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    if n >= 1_000:
        return f"{n / 1_000:.0f} KB"
    return f"{n} B"


def _http_download_to_file(
    url,
    dest_path,
    *,
    timeout=600,
    progress_label=None,
    expected_bytes=0,
    frac_start=0.0,
    frac_end=1.0,
):
    """Stream ``url`` over HTTPS directly to ``dest_path`` without buffering large files in RAM,
    reporting byte-level progress when ``progress_label`` is given."""
    from pdfmanager import policy

    try:
        policy.check_network_allowed("Language pack download")
    except policy.PolicyError as exc:
        raise core.PdfManagerError(str(exc)) from None
    if not str(url or "").startswith("https://"):
        raise core.PdfManagerError(f"refusing non-HTTPS language pack URL: {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": "ZPDF"})
    tmp_path = Path(dest_path).with_suffix(Path(dest_path).suffix + ".part")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp_path, "wb") as fh:
            raw_len = resp.headers.get("Content-Length") if getattr(resp, "headers", None) else None
            try:
                total_bytes = int(raw_len) if raw_len else int(expected_bytes or 0)
            except (TypeError, ValueError):
                total_bytes = int(expected_bytes or 0)
            got = 0
            span = max(0.0, float(frac_end) - float(frac_start))
            while True:
                progress.check()
                chunk = resp.read(262_144)
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
                if progress_label:
                    if total_bytes > 0:
                        pct = min(100, int(got * 100 / total_bytes))
                        frac = float(frac_start) + span * min(1.0, got / total_bytes)
                        progress.report(
                            message=f"{progress_label} · {_fmt_bytes(got)} / {_fmt_bytes(total_bytes)} ({pct}%)",
                            fraction_in_part=frac,
                        )
                    else:
                        progress.report(
                            message=f"{progress_label} · {_fmt_bytes(got)}",
                            fraction_in_part=float(frac_start),
                        )
        tmp_path.replace(dest_path)
    except progress.Cancelled:
        tmp_path.unlink(missing_ok=True)
        raise
    except urllib.error.HTTPError as exc:
        tmp_path.unlink(missing_ok=True)
        raise core.PdfManagerError(f"download failed (HTTP {exc.code}) for {url}") from None
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise core.PdfManagerError(f"could not reach {url}: {exc}") from None


def _http_post_json(url, payload, *, headers=None, timeout=180):
    """POST JSON ``payload`` to ``url`` and return parsed JSON response.
    Allows ``http://`` only for loopback hosts (local Ollama / LM Studio);
    remote hosts require ``https://`` and pass ``policy.check_network_allowed``."""
    import urllib.parse
    from pdfmanager import policy

    parsed = urllib.parse.urlsplit(str(url or ""))
    host = (parsed.hostname or "").lower()
    is_local = host in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme not in ("https", "http") or (parsed.scheme == "http" and not is_local):
        raise core.PdfManagerError(f"refusing insecure AI translation URL: {url!r}")
    if not is_local:
        try:
            policy.check_network_allowed("AI translation")
        except policy.PolicyError as exc:
            raise core.PdfManagerError(str(exc)) from None
    body = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json", "User-Agent": "ZPDF", **(headers or {})}
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="ignore")[:240]
        except OSError:
            pass
        raise core.PdfManagerError(f"AI translation API failed (HTTP {exc.code}): {detail or exc.reason}") from None
    except (OSError, ValueError) as exc:
        hint = " (is Ollama / LM Studio running?)" if is_local else ""
        raise core.PdfManagerError(f"could not reach AI translation endpoint {url}{hint}: {exc}") from None


def _download_and_install_nllb(pack_code=NLLB_PACK_CODE):
    """Download Meta NLLB-200 (600M, 1.3B, or 3.3B CTranslate2 int8) into ``<packs_dir>/<pack_code>/``."""
    meta = NLLB_PACKS[pack_code]
    pack_dest = packs_dir() / pack_code
    model_dest = pack_dest / "models" / pack_code
    model_dest.mkdir(parents=True, exist_ok=True)
    file_entries = meta.get("files") or [(f, f"{meta['base_url']}/{f}") for f in NLLB_FILES]
    label = {
        NLLB_XL_PACK_CODE: "NLLB-200 3.3B",
        NLLB_LARGE_PACK_CODE: "NLLB-200 1.3B",
    }.get(pack_code, "NLLB-200")
    # Small tokenizer/config files occupy 0–5%, model.bin occupies 5–96%, finalization 96–100%.
    small_files = [f for f, _ in file_entries if f != "model.bin"]
    small_step = 0.05 / max(1, len(small_files))
    small_idx = 0
    for filename, file_url in file_entries:
        progress.check()
        if filename == "model.bin":
            frac_start, frac_end = 0.05, 0.96
            expected = meta["size_bytes"]
            dl_label = f"Downloading {label}"
        else:
            frac_start = small_idx * small_step
            frac_end = (small_idx + 1) * small_step
            small_idx += 1
            expected = 0
            dl_label = f"Downloading {label} ({filename})"
        progress.report(message=f"{dl_label}…", fraction_in_part=frac_start)
        _http_download_to_file(
            file_url,
            model_dest / filename,
            progress_label=dl_label,
            expected_bytes=expected,
            frac_start=frac_start,
            frac_end=frac_end,
        )
    progress.report(message=f"Finalizing {label}…", fraction_in_part=0.97)
    manifest = {
        "code": pack_code,
        "name": meta["name"],
        "version": "1.0",
        "dir": "ltr",
        "source": "nllb",
    }
    (pack_dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if not (pack_dest / "ui.json").is_file():
        (pack_dest / "ui.json").write_text("{}\n", encoding="utf-8")
    ensure_translation_runtime()
    progress.report(message=f"Installed {label} universal translation model", fraction_in_part=1.0)
    return {"code": pack_code, "source": "nllb", "installed": manifest}


def _parse_sha256_text(text):
    first = str(text or "").strip().splitlines()
    if not first:
        return None
    token = first[0].strip().split()[0].lower()
    return token if re.fullmatch(r"[0-9a-f]{64}", token) else None


def download_and_install_pack(code, *, pair=None, source=None):
    """Download and install a language pack using the hybrid strategy:
    - ``nllb-200`` / ``nllb-200-1.3b``: downloads Meta NLLB-200 (CTranslate2 int8, 200 languages).
    - Prefer curated ZPDF pack on ``Tempura-446/ZPDF-releases`` (SHA-256 verified)
      when ``pair`` is not specified and ``source != 'argos'``.
    - Fall back to Argos Translate's open-source CTranslate2 models for any other
      language or explicit ``src-dst`` pair.
    """
    norm_code = normalize_code(code)
    if norm_code in NLLB_PACKS:
        return _download_and_install_nllb(norm_code)
    if norm_code in ("auto", DEFAULT_LANG) and not pair:
        raise core.PdfManagerError(f"cannot download a language pack for {norm_code!r}")

    # 1. Try ZPDF curated release pack first (hybrid priority)
    if not pair and source != "argos":
        progress.check()
        progress.report(message=f"Checking ZPDF releases for {norm_code}…", fraction_in_part=0.02)
        zpdf_packs = fetch_zpdf_release_packs()
        zpack = zpdf_packs.get(norm_code)
        if zpack:
            expected_sha = None
            if zpack.get("sha256_url"):
                try:
                    sha_raw = _http_get_bytes(zpack["sha256_url"], timeout=20).decode("utf-8", errors="ignore")
                    expected_sha = _parse_sha256_text(sha_raw)
                except core.PdfManagerError:
                    expected_sha = None
            progress.check()
            dl_label = f"Downloading {norm_code} language pack"
            progress.report(message=f"{dl_label}…", fraction_in_part=0.05)
            with _download_progress(
                dl_label,
                expected_bytes=int(zpack.get("size_bytes") or 0),
                frac_start=0.05,
                frac_end=0.88,
            ):
                zip_bytes = _http_get_bytes(zpack["download_url"], timeout=180)
            with tempfile.TemporaryDirectory(prefix="pdfm-lang-dl-") as tmp:
                zip_path = Path(tmp) / "pack.zip"
                zip_path.write_bytes(zip_bytes)
                progress.report(message=f"Installing {norm_code} language pack…", fraction_in_part=0.90)
                installed = install_pack_from_zip(zip_path, expected_sha256=expected_sha)
            ensure_translation_runtime()
            progress.report(message=f"Installed {norm_code}", fraction_in_part=1.0)
            return {"code": norm_code, "source": "zpdf", "installed": installed}

    # 2. Fall back to Argos Translate index
    progress.check()
    progress.report(message=f"Fetching translation model index for {norm_code}…", fraction_in_part=0.02)
    argos_entries = fetch_argos_index(use_cache=True)
    if not argos_entries:
        raise core.PdfManagerError(
            "could not fetch the online language pack catalog; check your internet connection "
            "or install a .zip/.argosmodel file manually"
        )

    if pair:
        parts = str(pair).strip().split("-", 1)
        if len(parts) != 2:
            raise core.PdfManagerError(f"invalid translation pair: {pair!r}")
        # Handle BCP-47 tags with hyphens like "en-zh-Hans" or "zh-Hans-en"
        wanted_pairs = []
        for entry in argos_entries:
            if entry["pair"].lower() == str(pair).strip().lower():
                wanted_pairs = [entry]
                break
        if not wanted_pairs:
            raise core.PdfManagerError(f"no downloadable offline model found for pair {pair!r}")
    else:
        wanted_pairs = [
            e for e in argos_entries
            if (e["src"] == DEFAULT_LANG and e["dst"] == norm_code)
            or (e["src"] == norm_code and e["dst"] == DEFAULT_LANG)
        ]
        if not wanted_pairs:
            raise core.PdfManagerError(f"no downloadable offline translation model found for {norm_code!r}")

    installed_results = []
    total = len(wanted_pairs)
    per_pair = 0.90 / max(1, total)
    for idx, entry in enumerate(wanted_pairs):
        progress.check()
        p_start = 0.05 + idx * per_pair
        p_dl_end = p_start + per_pair * 0.85
        p_end = p_start + per_pair
        dl_label = f"Downloading {entry['pair']} ({idx + 1}/{total})"
        progress.report(message=f"{dl_label}…", fraction_in_part=p_start)
        last_err = None
        archive_bytes = None
        for link in entry["links"]:
            try:
                with _download_progress(
                    dl_label,
                    expected_bytes=50_000_000,
                    frac_start=p_start,
                    frac_end=p_dl_end,
                ):
                    archive_bytes = _http_get_bytes(link, timeout=240)
                break
            except core.PdfManagerError as exc:
                last_err = exc
        if archive_bytes is None:
            raise core.PdfManagerError(f"failed to download {entry['pair']} model: {last_err}")
        progress.report(message=f"Installing {entry['pair']}…", fraction_in_part=p_dl_end)
        with tempfile.TemporaryDirectory(prefix="pdfm-argos-dl-") as tmp:
            model_zip = Path(tmp) / "model.argosmodel"
            model_zip.write_bytes(archive_bytes)
            res = install_argos_model_zip(
                model_zip,
                src_lang=entry["src"],
                dst_lang=entry["dst"],
                name=entry["to_name"] if entry["dst"] != DEFAULT_LANG else entry["from_name"],
            )
            installed_results.append(res)
        progress.report(message=f"Installed {entry['pair']}", fraction_in_part=p_end)
    ensure_translation_runtime()
    progress.report(message=f"Installed {norm_code} translation models", fraction_in_part=1.0)
    return {
        "code": installed_results[-1]["code"],
        "source": "argos",
        "installed_pairs": [r["pair"] for r in installed_results],
        "installed": installed_results[-1],
    }


def _has_ct2_runtime():
    """Return True when both ctranslate2 and sentencepiece are importable."""
    return (
        importlib.util.find_spec("ctranslate2") is not None
        and importlib.util.find_spec("sentencepiece") is not None
    )


def ensure_translation_runtime(*, auto_install=True, force=False):
    """Ensure the offline translation runtime (``ctranslate2`` + ``sentencepiece``)
    is available. When missing in a non-frozen Python environment and online
    access is permitted by both user settings and enterprise policy, installs
    both wheels automatically so users who upgraded from an older version never
    need to run ``pip`` manually."""
    if _has_ct2_runtime():
        return True
    if not auto_install or getattr(sys, "frozen", False):
        return False
    if os.environ.get("PYTEST_CURRENT_TEST") and not force:
        return False
    from pdfmanager import policy

    if policy.get("disable_network", False):
        return False
    if update.settings().get("online") is False:
        return False
    if importlib.util.find_spec("pip") is None:
        return False
    progress.say("Installing offline translation runtime (one-time setup)…")
    try:
        proc = subprocess.run(
            [
                sys.executable, "-I", "-m", "pip", "install",
                "--disable-pip-version-check", "--no-input",
                "ctranslate2>=4.0", "sentencepiece>=0.2",
            ],
            capture_output=True, text=True, timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    importlib.invalidate_caches()
    return _has_ct2_runtime()

