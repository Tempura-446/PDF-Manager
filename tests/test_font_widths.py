# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""fonts.installed_family: a family's normal-width face comes before its
condensed one when both carry the family's name (DejaVu Sans Condensed Bold
is "DejaVu Sans" with the style "Condensed Bold")."""
from pathlib import Path

import pytest
import reportlab

from pdfmanager import fonts

DEJAVU_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
STAND_IN = Path(reportlab.__file__).parent / "fonts" / "VeraBd.ttf"  # plays the condensed face


@pytest.mark.skipif(not (DEJAVU_BOLD.is_file() and STAND_IN.is_file()), reason="needs DejaVu Sans Bold and Vera Bold")
def test_the_normal_width_face_before_the_condensed_one(monkeypatch):
    index = [  # (as _font_index lists them) the condensed face found first
        ("dejavusanscondensedbold", "dejavusanscondensedbold", "dejavusans", True, False, str(STAND_IN), 0,
         "DejaVu Sans", True),
        ("dejavusansbold", "dejavusansbold", "dejavusans", True, False, str(DEJAVU_BOLD), 0, "DejaVu Sans", False),
    ]
    monkeypatch.setattr(fonts, "_index", index)
    assert fonts.installed_family("DejaVu Sans", True, False, "Bold words") == fonts._register(str(DEJAVU_BOLD), 0)


def test_width_words_mark_the_face(monkeypatch, tmp_path):
    assert all(w in fonts.WIDTH_WORDS for w in ("condensed", "narrow"))
    assert all(len(e) == 9 and isinstance(e[8], bool) for e in fonts._font_index())
