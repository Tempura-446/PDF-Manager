# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Making scanned pages searchable: two engines.

The built-in engine runs PaddleOCR's PP-OCR models (through the rapidocr
package, on ONNX Runtime). It reads degraded scans better than Tesseract,
reads English, most European languages, Chinese and Japanese with the model
it ships with and no language setting, and needs nothing installed beside
ZPDF. Russian, Korean, Arabic, Hindi, Thai, Greek, Tamil and Telugu each
have a reading model of their own, fetched once (and checked) the first time
a page in that script is read. It turns sideways and upside-down pages
upright and reads multi-column pages column by column. Its text layer is
ZPDF's own (textlayer.py).

Tesseract, through OCRmyPDF, stays as the second engine: for the scripts
the built-in engine has no model for, and for PDF/A output straight from OCR.
"""

import os  # noqa: F401 - exposed for tests patching ocr.os
from pdfmanager import core, scan, textlayer  # noqa: F401
from pdfmanager.textlayer import Word  # noqa: F401

__all__ = ["Word", "Line", "ENGINES", "QUALITIES", "default_quality", "default_gpu", "default_workers",
           "choose_engine", "script_for", "pack_ocr_model", "recognise", "reading_order", "text_of", "skew_angle",
           "page_orientation", "format_report"]

from pdfmanager.ocr.engines import (
    BUILTIN_LANGUAGES, ENGINES, HIGH_RENDER_DPI, MAX_RENDER_SIDE,
    MIN_CONFIDENCE, MIN_SKEW, MIN_SKEW_BOXES, PIP_OCR, PIP_TESSERACT,
    QUALITIES, RENDER_DPI, SCRIPT_LANGUAGES, SCRIPT_NAMES, TESSERACT_LINK,
    Line, _BCP47_TO_TESS, _SCRIPT_OF, _TESS_TO_BCP47, _best_script, _builtin_imports, _codes, _tess_code,
    _tesseract_missing, builtin_available, choose_engine, ocrmypdf_installed,
    script_for, tesseract_available,
)
from pdfmanager.ocr.models import (
    _best_model, _best_model_source,
    _call_lock, _check_quality, _directml_available, _directml_failed,
    _download_model, _engine, _engine_lock, _engines, _fetch, _language_model,
    _model_entry, _model_source, _pick_onnx, _sha256, default_gpu,
    default_quality, models_dir, pack_ocr_model, reset,
)
from pdfmanager.ocr.preprocess import (
    FAINT_CONTRAST, GREY_PAPER, MAX_LINE_RETRIES, MIN_DENOISE_STROKE,
    RETRY_CONFIDENCE, SMALL_TEXT_PX, SPECKLE_RATIO, UNEVEN_LIGHT,
    _ORIG_READ, _ORIG_RECOGNISE, _array, _clean_for_reading, _clear,
    _dewarp_line_crop, _flatten_background, _lines_score, _map_lines,
    _map_quad, _measure_image, _median_text_height, _quad, _read,
    _retry_low_confidence, _scale_lines, _straighten_photo, _with_quality,
    recognise,
)
from pdfmanager.ocr.layout import (
    ORIENTATION_SIDE, PICTURES_PER_PAGE, PICTURE_CONFIDENCE, PICTURE_LETTERS,
    PICTURE_PIXELS, PICTURE_SHARE, PICTURE_TEXT, SURE_SCORE, UNSURE_PAGE_MIN,
    UNSURE_PAGE_SHARE, _WIDE, _bbox, _box_size, _char_kinds, _columns,
    _gaps, _hidden_text_in, _layout, _lines, _ordered, _page_list,
    _picture_boxes, _prose, _reading_score, _reads_wrong_way, _rows,
    _straight, _strip_layers, _tilt, _union, _unwritten_space,
    _vertical_extent, _wide, _without_blanks, _words, format_report,
    layer_is_rubbish, page_has_text, page_orientation, reading_order,
    skew_angle, text_of,
)
from pdfmanager.ocr.runner import (
    MAX_WORKERS, PARALLEL_MIN_PAGES, _OCR_POOL_IDLE_SECS, _OCR_POOL_LOCK,
    _OCR_POOL_STATE, _WORKER_DOC_CACHE, _acquire_ocr_pool, _available_memory_mb,
    _close_worker_doc, _drop_unused_objects, _image_hash, _is_blank_image,
    _looks_grey, _rasterise, _read_pages, _recognise_scan_page,
    _recognise_scan_page_worker, _record_skip, _record_unsure_stat,
    _refine_lines, _refine_tables, _release_ocr_pool, _run_builtin,
    _run_tesseract, _set_rotation, _tesseract_languages, _turn_page,
    _worker_pdf_doc, default_workers, render_scale, run, shutdown_ocr_pool,
)
