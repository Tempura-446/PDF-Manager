# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Unified offline AI model manager for OCR and Manga AI packs.

Reports installation status and disk usage, downloads models on demand, and
removes cached weights cleanly from disk.
"""

import logging
from pathlib import Path

from pdfmanager import core

_LOG = logging.getLogger("zpdf")
_PACK_LOCKS = {}

_PACK_DEFS = (
    {
        "id": "ppocr_v6",
        "name": "PP-OCRv6 Medium (Best OCR)",
        "category": "OCR",
        "description": "High-accuracy 50-language built-in OCR detection & recognition ONNX weights (34.5M).",
        "approx_mb": 139,
    },
    {
        "id": "ppocr_v6_small",
        "name": "PP-OCRv6 Small (Standard OCR)",
        "category": "OCR",
        "description": "Fast 50-language PP-OCRv6 Standard-mode weights (7.7M) — upgrades Standard OCR from PP-OCRv5.",
        "approx_mb": 16,
    },
    {
        "id": "ppocr_v6_tiny",
        "name": "PP-OCRv6 Tiny (Ultra-Fast OCR)",
        "category": "OCR",
        "description": "Ultra-lightweight 1.5M PP-OCRv6 weights (~3.9× faster on CPU than PP-OCRv5 Mobile).",
        "approx_mb": 4,
    },
    {
        "id": "manga_detector",
        "name": "Manga AI: Neural Bubble Detector",
        "category": "Manga AI",
        "description": "RT-DETR v2 ONNX detector for speech bubbles, shout bubbles, and borderless text.",
        "approx_mb": 11,
    },
    {
        "id": "manga_ocr",
        "name": "Manga AI: Baberu OCR v1.2 (JA/ZH/EN)",
        "category": "Manga AI",
        "description": "122 MB INT4+INT8 ONNX recognizer for Japanese, Chinese & English bubbles, handwriting, and SFX.",
        "approx_mb": 122,
    },
    {
        "id": "manga_inpaint",
        "name": "Manga AI: LaMa-Manga Inpainting",
        "category": "Manga AI",
        "description": "INT8 neural inpainter for cleanly erasing borderless text over screentones and artwork.",
        "approx_mb": 60,
    },
)

_MANGA_MAP = {
    "manga_detector": "detector",
    "manga_ocr": "ocr",
    "manga_inpaint": "inpaint",
}

_OCR_V6_TIERS = {
    "ppocr_v6": "medium",
    "ppocr_v6_small": "small",
    "ppocr_v6_tiny": "tiny",
}


def _ppocr_v6_paths():
    from pdfmanager import ocr

    base = Path(ocr.models_dir())
    return [base / "best_v6_det.onnx", base / "best_v6_rec.onnx"]


def _ppocr_v6_tier_paths(tier):
    if tier == "medium":
        return _ppocr_v6_paths()
    from pdfmanager import ocr

    base = Path(ocr.models_dir())
    return [base / f"v6_{tier}_det.onnx", base / f"v6_{tier}_rec.onnx"]


def _pack_paths(pack_id):
    if pack_id in _OCR_V6_TIERS:
        return _ppocr_v6_tier_paths(_OCR_V6_TIERS[pack_id])
    if pack_id in _MANGA_MAP:
        from pdfmanager.translate import manga_ai

        return list(manga_ai._component_paths(_MANGA_MAP[pack_id]))
    raise core.PdfManagerError(f"unknown AI model pack: {pack_id!r}")


def _dir_or_files_size(paths):
    total = 0
    for p in paths:
        path = Path(p)
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def list_models():
    """Return status dicts for all manageable offline AI model packs."""
    out = []
    for spec in _PACK_DEFS:
        pid = spec["id"]
        paths = _pack_paths(pid)
        installed = bool(paths) and all(Path(p).is_file() and Path(p).stat().st_size > 0 for p in paths)
        size_bytes = _dir_or_files_size(paths) if installed else 0
        out.append({
            **spec,
            "installed": installed,
            "size_bytes": size_bytes,
        })
    return out


def download_model(pack_id):
    """Download the requested offline AI model pack and return its updated status."""
    import threading

    lock = _PACK_LOCKS.setdefault(pack_id, threading.Lock())
    with lock:
        if pack_id == "ppocr_v6":
            from pdfmanager.ocr import models as ocr_models

            ocr_models._best_model("det")
            ocr_models._best_model("rec")
            ocr_models.reset()
        elif pack_id in _OCR_V6_TIERS:
            from pdfmanager.ocr import models as ocr_models

            tier = _OCR_V6_TIERS[pack_id]
            ocr_models._v6_tier_model("det", tier=tier)
            ocr_models._v6_tier_model("rec", tier=tier)
            ocr_models.reset()
        elif pack_id in _MANGA_MAP:
            from pdfmanager.translate import manga_ai

            comp = _MANGA_MAP[pack_id]
            manga_ai.ensure_manga_ai(components=(comp,), download=True)
            manga_ai.reset(comp)
        else:
            raise core.PdfManagerError(f"unknown AI model pack: {pack_id!r}")
    return {"downloaded": pack_id, "models": list_models()}


def delete_model(pack_id):
    """Delete cached ONNX weights for ``pack_id`` and return freed bytes + updated list."""
    paths = list(_pack_paths(pack_id))
    # Release open ONNX Runtime sessions BEFORE unlinking so RAM is freed and
    # Windows does not hit PermissionError on mapped model files.
    if pack_id in _MANGA_MAP:
        try:
            from pdfmanager.translate import manga_ai

            manga_ai.reset(_MANGA_MAP[pack_id])
            if pack_id == "manga_ocr":
                legacy_root = manga_ai.manga_ai_dir() / "ocr"
                for legacy_name in ("encoder_model.onnx", "decoder_model.onnx", "vocab.txt", "vision_fp16.onnx"):
                    cand = legacy_root / legacy_name
                    if cand not in paths:
                        paths.append(cand)
        except Exception as exc:
            _LOG.debug("models_manager.reset manga_ai: %s", exc)
    elif pack_id in _OCR_V6_TIERS:
        try:
            from pdfmanager.ocr import models as ocr_models

            ocr_models.reset()
        except Exception as exc:
            _LOG.debug("models_manager.reset ocr: %s", exc)
    freed = 0
    for p in paths:
        path = Path(p)
        if path.is_file():
            try:
                freed += path.stat().st_size
                path.unlink()
            except OSError as exc:
                _LOG.debug("models_manager.delete_model %s: %s", path, exc)
    return {"deleted": pack_id, "freed_bytes": freed, "models": list_models()}
