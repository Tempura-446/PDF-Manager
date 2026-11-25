# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for v0.76.0 OCR, Translation & Manga Formatting v4."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw


def test_ocr_adaptive_binarize_and_vertical_cjk_rtl_order():
    from pdfmanager.ocr import layout as ocr_layout
    from pdfmanager.ocr import preprocess
    from pdfmanager.textlayer import Word

    # 1. Faint text on uneven shaded gradient -> adaptive binarization separates ink cleanly
    arr = np.tile(np.linspace(140, 235, 200, dtype=np.uint8), (60, 1))
    # Faint dark strokes ~35 levels darker than local background
    arr[20:40, 40:55] = np.clip(arr[20:40, 40:55].astype(int) - 38, 0, 255).astype(np.uint8)
    arr[20:40, 140:155] = np.clip(arr[20:40, 140:155].astype(int) - 38, 0, 255).astype(np.uint8)
    crop = Image.fromarray(arr, mode="L").convert("RGB")
    bin_img = preprocess._adaptive_binarize_crop(crop)
    b_arr = np.asarray(bin_img.convert("L"))
    # Both left (darker bg) and right (brighter bg) strokes become dark ink (<50) on white paper (>200)
    assert b_arr[30, 47] < 50
    assert b_arr[30, 147] < 50
    assert b_arr[5, 100] > 200

    # 2. Full-page vertical CJK reading order: right column first, then middle, then left
    q_r = [(200, 20), (225, 20), (225, 180), (200, 180)]
    q_m = [(140, 20), (165, 20), (165, 180), (140, 180)]
    q_l = [(80, 20), (105, 20), (105, 180), (80, 180)]
    lines = [
        ocr_layout.Line("三列目", q_l, 0.95, [Word("三列目", q_l, 0.95)]),
        ocr_layout.Line("一列目", q_r, 0.95, [Word("一列目", q_r, 0.95)]),
        ocr_layout.Line("二列目", q_m, 0.95, [Word("二列目", q_m, 0.95)]),
    ]
    ordered = ocr_layout.reading_order(lines)
    assert [ln.text for ln in ordered] == ["一列目", "二列目", "三列目"]

    # 3. De-hyphenation in text_of(..., dehyphenate=True)
    qa = [(10, 10), (120, 10), (120, 30), (10, 30)]
    qb = [(10, 40), (120, 40), (120, 60), (10, 60)]
    h_lines = [
        ocr_layout.Line("transla-", qa, 0.95, [Word("transla-", qa, 0.95)]),
        ocr_layout.Line("tion works", qb, 0.95, [Word("tion", qb, 0.95), Word("works", qb, 0.95)]),
    ]
    assert ocr_layout.text_of(h_lines, dehyphenate=True) == "translation works"


def test_translation_wcag_contrast_and_long_paragraph_chunking():
    from pdfmanager.translate import layout as tlayout
    from pdfmanager.translate import text as ttext

    # 1. WCAG contrast ratio: black on white is 21.0, mid-grey on mid-grey is ~1.0
    assert tlayout._contrast_ratio((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)) >= 20.9
    assert tlayout._contrast_ratio((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)) == 1.0
    assert tlayout._contrast_ratio((0.7, 0.7, 0.7), (1.0, 1.0, 1.0)) < 4.5

    # 2. Sentence-aware chunking keeps chunks under max_chars without splitting sentences mid-word
    long_para = " ".join(f"Sentence number {i} contains important legal details about the contract." for i in range(1, 20))
    chunks = ttext._split_long_for_mt(long_para, max_chars=260)
    assert len(chunks) >= 3
    assert all(len(ch) <= 260 for ch in chunks)
    assert " ".join(chunks) == long_para


def test_manga_broken_outline_bubble_and_xycut_rtl_panel_ordering():
    from pdfmanager.translate import manga

    # 1. Hand-drawn speech bubble with a 2px gap in its black ink outline
    img = Image.new("RGB", (400, 400), (210, 210, 210))  # grey screentone panel background
    draw = ImageDraw.Draw(img)
    draw.ellipse([100, 100, 300, 280], fill=(255, 255, 255), outline=(10, 10, 10), width=4)
    # Punch a small 2px gap in the outline (simulating hand-drawn open ink stroke)
    draw.rectangle([198, 98, 200, 106], fill=(255, 255, 255))
    # Draw Japanese-like dark ink strokes inside bubble
    draw.rectangle([170, 140, 185, 230], fill=(15, 15, 15))
    draw.rectangle([210, 145, 225, 235], fill=(15, 15, 15))

    bubbles = manga.detect_speech_bubbles(img, reading_dir="rtl")
    assert len(bubbles) == 1, f"Expected broken-outline bubble to be recovered, got {len(bubbles)}"

    # 2. 2-column / 4-koma manga page: right column (top -> bottom) BEFORE left column (top -> bottom)
    b_list = [
        {"x0": 40, "y0": 40, "x1": 160, "y1": 140, "w": 120, "h": 100, "cx": 100, "cy": 90, "id": "L1"},
        {"x0": 240, "y0": 50, "x1": 360, "y1": 150, "w": 120, "h": 100, "cx": 300, "cy": 100, "id": "R1"},
        {"x0": 40, "y0": 200, "x1": 160, "y1": 300, "w": 120, "h": 100, "cx": 100, "cy": 250, "id": "L2"},
        {"x0": 240, "y0": 210, "x1": 360, "y1": 310, "w": 120, "h": 100, "cx": 300, "cy": 260, "id": "R2"},
    ]
    rtl_ordered = manga.order_manga_bubbles(b_list, reading_dir="rtl")
    assert [b["id"] for b in rtl_ordered] == ["R1", "R2", "L1", "L2"]

    ltr_ordered = manga.order_manga_bubbles(b_list, reading_dir="ltr")
    assert [b["id"] for b in ltr_ordered] == ["L1", "L2", "R1", "R2"]
