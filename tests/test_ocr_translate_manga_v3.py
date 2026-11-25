# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for v0.75.0 OCR, Translation & Manga Formatting Power Upgrade."""

from __future__ import annotations

import zipfile
from pathlib import Path

from PIL import Image, ImageDraw


def test_ocr_ligature_normalization_and_noise_filtering():
    from types import SimpleNamespace
    from pdfmanager.ocr import layout as ocr_layout

    raw = SimpleNamespace(
        boxes=[
            [(10, 10), (120, 10), (120, 30), (10, 30)],
            [(130, 10), (138, 10), (138, 25), (130, 25)],
            [(10, 40), (120, 40), (120, 60), (10, 60)],
        ],
        txts=["ﬁle ﬂow ﬀ", ".", "hello world"],
        scores=[0.92, 0.40, 0.95],
    )
    lines = ocr_layout._lines(raw)
    texts = [ln.text for ln in lines]
    assert "file flow ff" in texts
    assert "." not in texts

    # CJK prose detection & upright vertical CJK orientation safety
    cjk_lines = [
        ocr_layout.Line("日本語の縦書き文章です", [(200, 20), (225, 20), (225, 180), (200, 180)], 0.95, []),
        ocr_layout.Line("これは二列目の本文です", [(160, 20), (185, 20), (185, 180), (160, 180)], 0.95, []),
        ocr_layout.Line("三列目の縦書きテキスト", [(120, 20), (145, 20), (145, 180), (120, 180)], 0.95, []),
    ]
    assert ocr_layout._prose(cjk_lines) is True
    assert ocr_layout._reads_wrong_way(cjk_lines) is False


def test_ocr_border_safe_measurement():
    from pdfmanager.ocr import preprocess

    # White page with thick black scanner border around outer 3%
    img = Image.new("L", (400, 500), 250)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 399, 10], fill=5)
    draw.rectangle([0, 489, 399, 499], fill=5)
    draw.rectangle([0, 0, 10, 499], fill=5)
    draw.rectangle([389, 0, 399, 499], fill=5)
    # Normal dark text inside
    draw.rectangle([80, 100, 320, 115], fill=40)

    stats = preprocess._measure_image(img)
    assert stats["paper"] >= 240


def test_translation_entity_protection_and_column_ordering():
    from pdfmanager.translate import layout as tlayout
    from pdfmanager.translate import text as ttext

    src = "Use `my_func` with v0.75.0 and USD 120.50 on https://example.com"
    masked, mapping = ttext._protect_neural_entities(src)
    assert "`my_func`" not in masked
    assert "v0.75.0" not in masked
    assert "USD 120.50" not in masked
    assert "https://example.com" not in masked

    # Simulate MT inserting comma or fullwidth digits into sentinel tokens
    corrupted = masked
    keys = [k for k, _v in mapping]
    if keys:
        k0 = keys[0]
        corrupted = corrupted.replace(k0, k0[:3] + "," + k0[3:], 1)
    if len(keys) > 1:
        k1 = keys[1]
        fw = "".join(chr(ord("０") + int(ch)) for ch in k1)
        corrupted = corrupted.replace(k1, fw, 1)

    restored, all_ok = ttext._restore_neural_entities(corrupted, mapping)
    assert all_ok is True
    assert restored == src

    # Column-aware block ordering: left column (x=50..250) before right column (x=320..520)
    blocks = [
        {"x0": 320, "x1": 520, "top": 50, "bottom": 90, "text": "R1"},
        {"x0": 50, "x1": 250, "top": 60, "bottom": 100, "text": "L1"},
        {"x0": 320, "x1": 520, "top": 120, "bottom": 160, "text": "R2"},
        {"x0": 50, "x1": 250, "top": 130, "bottom": 170, "text": "L2"},
    ]
    ordered = tlayout._sort_blocks_reading_order(blocks, page_w=600)
    assert [b["text"] for b in ordered] == ["L1", "L2", "R1", "R2"]


def test_manga_elliptical_wrap_comic_format_and_raster_furigana():
    from pdfmanager.translate import manga

    # 1. Comic text formatting
    txt, style = manga._format_comic_text("Wait..... what ? ! Really -- no way!")
    assert "Wait..." in txt
    assert "—" in txt
    assert "?!" in txt
    assert style == "shout"

    txt_w, style_w = manga._format_comic_text("(I hope nobody heard that. . .)")
    assert "that..." in txt_w
    assert style_w == "thought"

    # 2. Elliptical wrapping produces narrower top/bottom lines than middle line
    lines = manga._wrap_elliptical(
        "This is a longer piece of comic dialogue that should taper nicely at the top and bottom of an oval bubble",
        "Helvetica", 10.0, rx=70.0, ry=55.0, leading=12.0,
    )
    assert len(lines) >= 3
    mid = len(lines) // 2
    assert len(lines[mid]) >= len(lines[0]) or len(lines[mid]) >= len(lines[-1])

    # 3. Raster vertical CJK furigana stripping & RTL column ordering
    from pdfmanager.ocr.layout import Line

    ocr_lines = [
        # Left main column (2nd in Japanese RTL reading order)
        Line("世界へ", [(30, 10), (58, 10), (58, 150), (30, 150)], 0.95, []),
        # Right main column (1st in Japanese RTL reading order)
        Line("ようこそ", [(75, 10), (103, 10), (103, 150), (75, 150)], 0.95, []),
        # Tiny furigana ruby column immediately right of right main column
        Line("ふりがな", [(105, 15), (115, 15), (115, 70), (105, 70)], 0.85, []),
    ]
    cleaned = manga._order_and_clean_bubble_lines(ocr_lines, reading_order="rtl")
    assert cleaned == "ようこそ世界へ"


def test_cbz_to_pdf_preserves_bw_manga_losslessly(tmp_path):
    from pdfmanager import cbz
    from pypdf import PdfReader

    cbz_path = tmp_path / "bw_manga.cbz"
    img = Image.new("L", (400, 600), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 40, 360, 560], outline=0, width=4)
    img_path = tmp_path / "001.png"
    img.save(img_path, format="PNG")
    with zipfile.ZipFile(cbz_path, "w") as zf:
        zf.write(img_path, arcname="001.png")

    pdf_out = tmp_path / "bw_manga.pdf"
    cbz.cbz_to_pdf(cbz_path, pdf_out, rtl_viewer=True)
    reader = PdfReader(pdf_out)
    assert len(reader.pages) == 1
    # Verify lossless FlateDecode (not lossy DCTDecode JPEG) for crisp B&W manga page
    xobjs = reader.pages[0]["/Resources"]["/XObject"].get_object()
    filters = [str(xobjs[k].get_object().get("/Filter", "")) for k in xobjs]
    assert any("FlateDecode" in f for f in filters), f"Expected FlateDecode for B&W manga, got {filters}"
