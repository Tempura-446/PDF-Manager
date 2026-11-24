# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Late-binding attribute lookup on pdfmanager.ocr so test monkeypatches work across submodules."""

import sys


def _ocr_attr(name, default):
    pkg = sys.modules.get("pdfmanager.ocr")
    return getattr(pkg, name, default) if pkg is not None else default
