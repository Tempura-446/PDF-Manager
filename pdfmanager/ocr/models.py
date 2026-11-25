# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""OCR model discovery, SHA-256 verified downloads, GPU/quality defaults, and RapidOCR cache."""

import hashlib
import logging
import os
import sys
import threading
from pathlib import Path

from pdfmanager import core
from pdfmanager.ocr._facade import _ocr_attr
from pdfmanager.ocr.engines import (
    PIP_OCR, QUALITIES, SCRIPT_NAMES, _BCP47_TO_TESS, _SCRIPT_OF, _TESS_TO_BCP47, _codes, builtin_available, script_for,
)

# -- the built-in engine -------------------------------------------------------

_engine_lock = threading.Lock()   # building the instances
_call_lock = threading.Lock()     # using them: each keeps the last call's settings
_engines = {}                     # (script, quality, gpu) -> RapidOCR
_directml_failed = False


def default_quality():
    """The OCR quality saved in Settings ('standard' or 'best')."""
    from pdfmanager import update

    chosen = update.settings().get("ocr_quality")
    return chosen if chosen in QUALITIES else "standard"


def default_gpu():
    """Whether Windows DirectML GPU acceleration for OCR is turned on in Settings
    (off by default)."""
    from pdfmanager import update

    return bool(update.settings().get("ocr_gpu", False))


def _directml_available():
    """Whether ONNX Runtime's DirectML provider can be tried on this machine."""
    if sys.platform != "win32" or _directml_failed:
        return False
    try:
        import onnxruntime

        return "DmlExecutionProvider" in (onnxruntime.get_available_providers() or ())
    except Exception:
        return False


def _check_quality(quality):
    if quality not in QUALITIES:
        raise core.PdfManagerError(f"unknown OCR quality {quality!r}; choose standard or best")
    return quality


def models_dir():
    """Where fetched reading models are kept: ZPDF's own folder for this user."""
    from pdfmanager import appdata

    return appdata.data_dir() / "ocr-models"


def _model_entry(key, label):
    """(address, SHA-256) of a PP-OCRv5 ONNX model from rapidocr's own list."""
    import rapidocr
    import yaml

    listing = yaml.safe_load((Path(rapidocr.__file__).parent / "default_models.yaml").read_text(encoding="utf-8"))
    stack = [listing.get("onnxruntime", {}).get("PP-OCRv5", {})]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            entry = node.get(key)
            if isinstance(entry, dict) and entry.get("model_dir") and entry.get("SHA256"):
                return entry["model_dir"], entry["SHA256"].lower()
            stack.extend(node.values())
    raise core.PdfManagerError(f"this rapidocr has no {label} model")


def _model_source(script):
    """(address, SHA-256) of a script's reading model, from rapidocr's own
    list of models: its ONNX build (the list also has the same models built
    for other engines, which ONNX Runtime can't load)."""
    return _model_entry(f"{script}_PP-OCRv5_rec_mobile", f"{SCRIPT_NAMES.get(script, script)} reading")


def _best_model_source(kind):
    """(address, SHA-256) of the larger ('best accuracy') detector or reader."""
    key = "ch_PP-OCRv5_det_server" if kind == "det" else "ch_PP-OCRv5_rec_server"
    label = "best-accuracy detection" if kind == "det" else "best-accuracy reading"
    return _model_entry(key, label)


def _fetch(url, target):
    import urllib.request
    from pdfmanager import policy

    if not str(url or "").startswith("https://"):
        raise core.PdfManagerError(f"OCR model download URL must use https:// (got {url!r})")

    try:
        policy.check_network_allowed("OCR model download")
    except policy.PolicyError as exc:
        raise core.PdfManagerError(str(exc)) from None

    with urllib.request.urlopen(url, timeout=60) as response, open(target, "wb") as out:
        while True:
            chunk = response.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)


def _sha256(path):
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_model(label, url, digest, target, size_note="about 10 MB", fallback_hint="choose the Tesseract engine"):
    """Fetch ``url`` into ``target`` once and verify its SHA-256."""
    from pdfmanager import progress

    if target.exists() and _sha256(target) == digest:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    progress.say(f"getting the {label} (once, {size_note})")
    try:
        _ocr_attr("_fetch", _fetch)(url, part)
    except Exception as exc:
        part.unlink(missing_ok=True)
        raise core.PdfManagerError(
            f"couldn't get the {label} ({exc}); connect to the internet once so "
            f"ZPDF can fetch it, or {fallback_hint}") from None
    if _sha256(part) != digest:
        part.unlink(missing_ok=True)
        raise core.PdfManagerError(
            f"the {label} came down damaged; try again, or {fallback_hint}")
    os.replace(part, target)
    return target




def _pick_onnx(ocr_dir, script=None):
    """Return the best ``.onnx`` recognition model inside ``ocr_dir``, or None."""
    if not ocr_dir.is_dir():
        return None
    if script:
        exact = ocr_dir / f"{script}.onnx"
        if exact.is_file():
            return exact
    rec = ocr_dir / "rec.onnx"
    if rec.is_file():
        return rec
    found = sorted(ocr_dir.glob("*.onnx"))
    return found[0] if found else None


def pack_ocr_model(script=None, language=None):
    """Return a Path to an offline OCR recognition model bundled in an installed
    language pack (``<data_dir>/locales/<code>/ocr/*.onnx``), or None."""
    from pdfmanager import appdata

    locales_root = appdata.data_dir() / "locales"
    if not locales_root.is_dir():
        return None

    # 1. Explicit language match (supports both BCP-47 like 'fr' and Tesseract like 'fra').
    if language:
        candidates = []
        for raw in _codes(language):
            low = raw.lower()
            candidates.append(raw)
            candidates.append(low)
            if low in _TESS_TO_BCP47:
                candidates.append(_TESS_TO_BCP47[low])
            if "-" in low:
                candidates.append(low.split("-", 1)[0])
        for sub in sorted(locales_root.iterdir()):
            if not sub.is_dir():
                continue
            if sub.name in candidates or sub.name.lower() in candidates:
                hit = _pick_onnx(sub / "ocr", script=script)
                if hit is not None:
                    return hit

    # 2. Script match across any installed pack (e.g. locales/ar/ocr/arabic.onnx or rec.onnx).
    if script:
        for sub in sorted(locales_root.iterdir()):
            if not sub.is_dir():
                continue
            exact = sub / "ocr" / f"{script}.onnx"
            if exact.is_file():
                return exact
            tess = _BCP47_TO_TESS.get(sub.name.lower())
            if tess and _SCRIPT_OF.get(tess) == script:
                hit = _pick_onnx(sub / "ocr", script=script)
                if hit is not None:
                    return hit
    return None


def _language_model(script):
    """The path of a script's reading model, preferring an installed language
    pack (offline) before fetching and checking on first use."""
    bundled = pack_ocr_model(script=script)
    if bundled is not None:
        return bundled
    name = SCRIPT_NAMES.get(script, script)
    url, digest = _ocr_attr("_model_source", _model_source)(script)
    return _ocr_attr("_download_model", _download_model)(f"{name} reading model", url, digest, _ocr_attr("models_dir", models_dir)() / f"{script}.onnx")


def _best_model(kind):
    """The path of the larger ('best accuracy') detector ('det') or reader ('rec'),
    fetched once and SHA-256 checked into _ocr_attr("models_dir", models_dir)()."""
    url, digest = _ocr_attr("_best_model_source", _best_model_source)(kind)
    label = "best-accuracy detection model" if kind == "det" else "best-accuracy reading model"
    return _ocr_attr("_download_model", _download_model)(label, url, digest, _ocr_attr("models_dir", models_dir)() / f"best_{kind}.onnx",
                           size_note="about 80 MB", fallback_hint="choose Standard accuracy")


def _engine(script=None, quality="standard", use_gpu=None, rec_model_path=None):
    """The RapidOCR instance for a script, quality ('standard' or 'best'), and
    optional language-pack recognition model path. Models take about a second to
    load, so each is made once. When DirectML GPU acceleration is turned on in
    Settings (Windows), tries it and falls back to the CPU at the first error."""
    global _directml_failed
    quality = _check_quality(quality)
    gpu = (default_gpu() if use_gpu is None else bool(use_gpu)) and _directml_available()
    rec_key = str(rec_model_path) if rec_model_path else None
    key = (script, quality, gpu, rec_key) if rec_key else (script, quality, gpu)
    with _engine_lock:
        if key not in _engines:
            params = {"Global.return_word_box": True}
            if gpu:
                params["EngineConfig.onnxruntime.use_dml"] = True
            if quality == "best":
                from rapidocr import ModelType, OCRVersion

                params.update({
                    "Det.ocr_version": OCRVersion.PPOCRV5,
                    "Det.model_type": ModelType.SERVER,
                    "Det.model_path": str(_best_model("det")),
                })
                if script is None and not rec_key:
                    params.update({
                        "Rec.ocr_version": OCRVersion.PPOCRV5,
                        "Rec.model_type": ModelType.SERVER,
                        "Rec.model_path": str(_best_model("rec")),
                    })
            if script is not None:
                from rapidocr import ModelType, OCRVersion

                model_path = rec_key or str(_language_model(script))
                params.update({"Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.lang_type": script,
                               "Rec.model_type": ModelType.MOBILE,
                               "Rec.model_path": model_path})
            elif rec_key:
                params["Rec.model_path"] = rec_key
                for dict_name in ("dict.txt", "rec_keys.txt"):
                    cand = Path(rec_key).parent / dict_name
                    if cand.is_file():
                        params["Rec.rec_keys_path"] = str(cand)
                        break
            try:
                from rapidocr import RapidOCR

                while len(_engines) >= 2:
                    _engines.pop(next(iter(_engines)))
                _engines[key] = RapidOCR(params=params)
            except Exception as exc:  # a broken install, or model files missing from a build
                if gpu:
                    logging.getLogger(__name__).warning("DirectML OCR engine failed (%s); falling back to CPU", exc)
                    _directml_failed = True
                    _pm = sys.modules.get("pdfmanager.ocr")
                    if _pm is not None:
                        _pm._directml_failed = True
                    params.pop("EngineConfig.onnxruntime.use_dml", None)
                    key = (script, quality, False, rec_key) if rec_key else (script, quality, False)
                    try:
                        from rapidocr import RapidOCR

                        while len(_engines) >= 2:
                            _engines.pop(next(iter(_engines)))
                        _engines[key] = RapidOCR(params=params)
                    except Exception as cpu_exc:
                        raise core.PdfManagerError(
                            f"OCR's built-in engine could not start; reinstall ZPDF ({PIP_OCR}) or choose the "
                            f"Tesseract engine ({cpu_exc})") from None
                else:
                    raise core.PdfManagerError(
                        f"OCR's built-in engine could not start; reinstall ZPDF ({PIP_OCR}) or choose the "
                        f"Tesseract engine ({exc})") from None
            # It announces every model it loads; only trouble is worth hearing.
            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        return _engines[key]


def reset():
    """Forget the loaded engines and what was learnt about the install (tests)."""
    global _directml_failed
    import sys
    from pdfmanager.ocr import engines as _eng
    with _engine_lock:
        _engines.clear()
        _eng._builtin_imports = None
        pkg_mod = sys.modules.get("pdfmanager.ocr")
        if pkg_mod is not None:
            pkg_mod._builtin_imports = None
        _directml_failed = False
        if pkg_mod is not None:
            pkg_mod._directml_failed = False
