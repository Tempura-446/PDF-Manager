# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tool operation specs (OPERATIONS) and execution helpers for the ZPDF local web app."""

from pdfmanager.server.operations._deps import (
    accessibility, archive, certcrypt, certsign, comments, compare, core,
    filetools, formdetect, forms, layers, ocr, office, organize, outline,
    pdfacheck, protection, recipes, redact, risks, scan, search, watchfolders,
)
from pdfmanager.server.operations.catalog import (
    OPERATIONS, OPS, PAPER, SUFFIX, _TRANSLATE_LANGS, _f, default_options,
)
from pdfmanager.server.operations.dispatch import (
    _clean_note, _form_op, _pages_list, _redact_boxes, _redact_extra,
    _redact_request, _run_file_op, _sign_cert_op, _to_excel,
)
from pdfmanager.server.operations.repair import (
    _check_pages, _damaged_errors, _flatten_outputs, _mend_inputs,
    _needs_mending, _plainly, _protect, _rebuildable, _tidy, _was_protected,
)
from pdfmanager.server.operations.runner import (
    _run_files, _run_operation, _start_action,
    run_action, run_folder_operation, run_operation,
)
from pdfmanager.server.operations.preview import (
    PREVIEW_OPS, PREVIEW_PAGES, THUMB_WIDTH,
    _inspect_pdf, _preview_operation, inspect_pdf, preview_operation,
)
