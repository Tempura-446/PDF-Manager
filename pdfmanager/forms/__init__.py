# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Fillable forms: add text boxes, checkboxes, radio buttons, dropdowns and
signature fields to a PDF.

The fields are standard PDF form fields, so they can be filled in with any
PDF reader, including this app's. Each field gets its own appearance (a
thin border, and a tick in a checked box), so it looks the same everywhere,
and readers draw the text in as it's typed.
"""

from ._base import (
    BORDER, CALCULATIONS, COMB, COMBO, DO_NOT_SCROLL, DOT, EDIT, FORMATS,
    MIN_SIZE, MULTILINE, MULTI_SELECT, NO_TOGGLE_OFF, PASSWORD, PUSHBUTTON,
    RADIO, READ_ONLY, REQUIRED, TICK, TICK_NAMES, TICK_STYLES, TYPES,
    _field_of, _inherited, _qualified_name, _unique, existing_fields,
)
from .scripts import CALC_NAMES, _js
from .fields import (
    add_fields, copy_to_pages, edit_fields, field_details, keep_defaults,
    make_table, parse_field, reorder_fields,
)
from .check import check_form
from .data import (
    clear_form, export_data, fill_form, fill_many, import_data, read_form_values,
)

__all__ = [
    "BORDER", "CALCULATIONS", "CALC_NAMES", "COMB", "COMBO", "DO_NOT_SCROLL",
    "DOT", "EDIT", "FORMATS", "MIN_SIZE", "MULTILINE", "MULTI_SELECT",
    "NO_TOGGLE_OFF", "PASSWORD", "PUSHBUTTON", "RADIO", "READ_ONLY", "REQUIRED",
    "TICK", "TICK_NAMES", "TICK_STYLES", "TYPES",
    "add_fields", "check_form", "clear_form", "copy_to_pages", "edit_fields",
    "existing_fields", "export_data", "field_details", "fill_form", "fill_many",
    "import_data", "keep_defaults", "make_table", "parse_field",
    "read_form_values", "reorder_fields",
]
