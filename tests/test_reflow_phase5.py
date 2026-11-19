# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Phase 5 paragraph reflow tests: CJK (kinsoku + Tc justification) and right-to-left (Unicode bidi)."""
from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
import pytest
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from pdfmanager import textedit


def _lines_with_boxes(path):
    """Return list of (visual_text, x0, y, x1, [(ch, x0, x1)]) on page 1."""
    doc = pdfium.PdfDocument(str(path))
    try:
        tp = doc[0].get_textpage()
        try:
            chars = []
            for i in range(tp.count_chars()):
                ch = tp.get_text_range(i, 1)
                box = tp.get_charbox(i, loose=True)
                if not ch or ch.isspace() or box[2] <= box[0]:
                    continue
                chars.append((ch, box[0], box[1], box[2], box[3]))
            rows = []
            for item in chars:
                if rows and abs(rows[-1][0][2] - item[2]) < 4.0:
                    rows[-1].append(item)
                else:
                    rows.append([item])
            out = []
            for row in rows:
                row.sort(key=lambda c: c[1])
                txt = "".join(c[0] for c in row)
                out.append((
                    txt,
                    min(c[1] for c in row),
                    row[0][2],
                    max(c[3] for c in row),
                    [(c[0], c[1], c[3]) for c in row],
                ))
            return out
        finally:
            tp.close()
    finally:
        doc.close()


def test_cjk_kinsoku_and_latin_word_wrapping():
    text = "これは日本語の文章です。「括弧」や句読点、そしてEnglish wordsも正しく折り返されます。"
    # Fixed-width measure: 10pt per CJK char, 6pt per ASCII char.
    measure = lambda s: sum(10.0 if ord(c) > 127 else 6.0 for c in s)
    lines = [ln for ln, _ in textedit._wrap_marked(text, 85.0, measure)]
    assert len(lines) >= 3, lines
    for ln in lines:
        assert ln[0] not in textedit._KINSOKU_NO_START, f"line starts with forbidden kinsoku char: {ln!r}"
        assert ln[-1] not in textedit._KINSOKU_NO_END, f"line ends with forbidden kinsoku char: {ln!r}"
    # Latin words stay whole (not split mid-word).
    joined = "\n".join(lines)
    assert "English" in joined
    assert "words" in joined


def _find_base_ttf():
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
    ]
    return next((p for p in candidates if p.exists()), None)


def test_japanese_paragraph_rewrap_and_justify(tmp_path):
    font_file = _find_base_ttf()
    if font_file is None:
        pytest.skip("No base TrueType font found")
    ttlib = pytest.importorskip("fontTools.ttLib")

    orig1 = "これは日本語の段落テストです。"
    orig2 = "句読点の処理を確認します、"
    # Reuse only characters already embedded in the PDF subset font so the test
    # does not depend on a system CJK font being installed on the runner.
    new_text = (
        "これは日本語の段落テストです。句読点の処理を確認します、"
        "これは日本語の段落テストです。句読点の処理を確認します。"
    )
    # Map every Japanese character used in the test into a temporary TrueType font so
    # ReportLab embeds a real Type0 Identity-H font with a complete /ToUnicode CMap.
    ttf = ttlib.TTFont(str(font_file))
    cmap = ttf.getBestCmap()
    sample_gid = cmap[ord("M")]
    for table in ttf["cmap"].tables:
        if table.isUnicode():
            for ch in set(orig1 + orig2 + new_text):
                if ord(ch) not in table.cmap:
                    table.cmap[ord(ch)] = sample_gid
    ja_ttf = tmp_path / "test_ja.ttf"
    ttf.save(str(ja_ttf))
    ttf.close()

    font_name = "TestJapaneseSans"
    pdfmetrics.registerFont(TTFont(font_name, str(ja_ttf)))

    src, out = tmp_path / "ja.pdf", tmp_path / "ja_out.pdf"
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    c.setFont(font_name, 11)
    c.drawString(72, 700, orig1)
    c.drawString(72, 684, orig2)
    c.save()

    rep = []
    textedit.edit_text(
        src, out,
        [{"page": 1, "rect": [68, 676, 260, 716], "paragraph": True, "text": new_text, "align": "justify"}],
        report=rep,
    )
    lines = _lines_with_boxes(out)
    assert len(lines) >= 2, lines
    for txt, _, _, _, _ in lines:
        assert txt[0] != "。", f"line started with 。: {txt!r}"
        assert txt[0] != "、", f"line started with 、: {txt!r}"
    # Non-final justified line fills close to the target column width via Tc spacing.
    orig_lines = _lines_with_boxes(src)
    orig_width = max(ln[3] for ln in orig_lines) - min(ln[1] for ln in orig_lines)
    first_width = lines[0][3] - lines[0][1]
    assert first_width >= orig_width - 8.0, f"justified CJK line too short: {first_width} vs {orig_width}"


def test_bidi_visual_ordering_hebrew_with_english_and_number():
    logical = "שלום hello 2026 עולם"
    visual = textedit._bidi_visual(logical, rtl=True)
    # Right-to-left Hebrew runs reversed visually; embedded "hello 2026" stays left-to-right.
    assert visual == "םלוע hello 2026 םולש", repr(visual)


def test_hebrew_paragraph_rewrap_visual_order(tmp_path):
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/seguisym.ttf"),
    ]
    font_file = next((p for p in candidates if p.exists()), None)
    if font_file is None:
        pytest.skip("No Hebrew-capable TrueType font found on this machine")

    font_name = "TestHebrewSans"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(font_file)))

    src, out = tmp_path / "he.pdf", tmp_path / "he_out.pdf"
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    c.setFont(font_name, 11)
    # Initial two-line Hebrew paragraph (stored visually in PDF).
    c.drawString(120, 700, textedit._bidi_visual("שלום עולם זהו מסמך בדיקה קצר", rtl=True))
    c.drawString(120, 685, textedit._bidi_visual("עבור עריכת פסקה בעברית כאן", rtl=True))
    c.save()

    new_text = "שלום עולם PDF 2026 זהו מסמך בדיקה ארוך יותר בעברית"
    rep = []
    textedit.edit_text(
        src, out,
        [{"page": 1, "rect": [110, 678, 300, 716], "paragraph": True, "text": new_text}],
        report=rep,
    )
    lines = _lines_with_boxes(out)
    assert len(lines) >= 2, lines
    all_visual = "\n".join(ln[0] for ln in lines)
    # English word and number remain in left-to-right visual order inside the RTL lines.
    assert "PDF" in all_visual, all_visual
    assert "2026" in all_visual, all_visual
    # First Hebrew word "שלום" appears visually as "םולש" on the first line (on the right side).
    assert "םולש" in lines[0][0], lines
