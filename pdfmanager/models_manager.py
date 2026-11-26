# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Unified offline AI model manager for OCR and Manga AI packs.

Reports installation status and disk usage, downloads models on demand, and
removes cached weights cleanly from disk.
"""

import logging
from pathlib import Path

from pdfmanager import core

_LOG = logging.getLogger("zpdf")

_PACK_DEFS = (
    {
        "id": "ppocr_v6",
        "name": "PP-OCRv6 Medium (Best OCR)",
        "category": "OCR",
        "description": "High-accuracy 50-language built-in OCR detection & recognition ONNX weights.",
        "approx_mb": 139,
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
        "name": "Manga AI: Japanese Manga-OCR",
        "category": "Manga AI",
        "description": "Pure-ONNX ViT recognizer for stylized Japanese manga fonts, vertical text, and furigana.",
        "approx_mb": 370,
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


def _ppocr_v6_paths():
    from pdfmanager.ocr import models as ocr_models

    base = Path(ocr_models.models_dir())
    return [base / "best_v6_det.onnx", base / "best_v6_rec.onnx"]


def _pack_paths(pack_id):
    if pack_id == "ppocr_v6":
        return _ppocr_v6_paths()
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
    if pack_id == "ppocr_v6":
        from pdfmanager.ocr import models as ocr_models

        ocr_models._best_model("det")
        ocr_models._best_model("rec")
    elif pack_id in _MANGA_MAP:
        from pdfmanager.translate import manga_ai

        comp = _MANGA_MAP[pack_id]
        manga_ai.ensure_manga_ai(components=(comp,), download=True)
    else:
        raise core.PdfManagerError(f"unknown AI model pack: {pack_id!r}")
    return {"downloaded": pack_id, "models": list_models()}


def delete_model(pack_id):
    """Delete cached ONNX weights for ``pack_id`` and return freed bytes + updated list."""
    paths = _pack_paths(pack_id)
    freed = 0
    for p in paths:
        path = Path(p)
        if path.is_file():
            try:
                freed += path.stat().st_size
                path.unlink()
            except OSError as exc:
                _LOG.debug("models_manager.delete_model %s: %s", path, exc)
    if pack_id == "ppocr_v6":
        try:
            from pdfmanager.ocr import models as ocr_models

            ocr_models.reset()
        except Exception as exc:
            _LOG.debug("models_manager.reset ocr: %s", exc)
    return {"deleted": pack_id, "freed_bytes": freed, "models": list_models()}
