# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Change words already on a page, rewrite a paragraph, or add words."""

from .extract import (
    ALIGNS, BASE14_NAMES, FIT_LIMIT, INSTALLED, LOOKS, MARKUPS, MOVED, PAD,
    READER_FONTS, SAME, SIMILAR, STANDARD,
    _Covers, _OwnFont, _Unsure, _can_draw, _chars, _letters, _origin,
    _page_fonts, _private, _raw, _widths,
)
from .reflow_ops import (
    _KINSOKU_NO_END, _KINSOKU_NO_START, _bidi_visual, _divisions,
    _wrap, _wrap_marked,
)
from .rewrite import (
    _Page, _edit, _intact, _ordered_text_matches, edit_text, font_choices,
    paragraph_runs, summarize_report, verify_visual_fidelity,
)

__all__ = [
    "ALIGNS", "BASE14_NAMES", "FIT_LIMIT", "INSTALLED", "LOOKS", "MARKUPS",
    "MOVED", "PAD", "READER_FONTS", "SAME", "SIMILAR", "STANDARD",
    "edit_text", "font_choices", "paragraph_runs", "summarize_report",
    "verify_visual_fidelity",
]
