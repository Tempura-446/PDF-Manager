# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""TDD tests for Manga / Comic Speech-Bubble Mode v2 (v0.68.0)."""

import io
import math
from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfReader
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdfmanager import fonts, translate


def _draw_ink_strokes(draw, cx, cy, fill):
    for dx in (-26, 0, 26):
        for dy in (-32, -10, 12, 34):
            draw.rectangle([cx + dx - 5, cy + dy - 5, cx + dx + 5, cy + dy + 5], fill=fill)


def _make_v2_manga_image(width=840, height=1120):
    """Synthetic manga page with:
    1. Oval light bubble (top-right)
    2. Rectangular narration box (top-left)
    3. Dark / inverted dramatic bubble with white text (bottom-right)
    4. Spiky polygon shout burst (bottom-left)
    All sitting on mid-tone artwork (RGB 128, 128, 132) so both light and dark
    enclosed bubbles are cleanly bounded.
    """
    img = Image.new("RGB", (width, height), (128, 128, 132))
    draw = ImageDraw.Draw(img)

    # 1. Oval light bubble (top-right)
    draw.ellipse([500, 60, 760, 250], fill=(255, 255, 255), outline=(10, 10, 10), width=6)
    _draw_ink_strokes(draw, 630, 155, fill=(18, 18, 18))

    # 2. Rectangular narration box (top-left)
    draw.rectangle([70, 75, 340, 245], fill=(255, 255, 255), outline=(10, 10, 10), width=6)
    _draw_ink_strokes(draw, 205, 160, fill=(18, 18, 18))

    # 3. Dark / inverted dramatic bubble with white text (bottom-right)
    draw.ellipse([480, 560, 760, 770], fill=(16, 16, 20), outline=(245, 245, 245), width=6)
    _draw_ink_strokes(draw, 620, 665, fill=(248, 248, 248))

    # 4. Spiky polygon shout bubble (bottom-left)
    cx, cy = 220, 665
    pts = []
    for k in range(20):
        ang = (2.0 * math.pi * k) / 20.0
        r = 135.0 if k % 2 == 0 else 78.0
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    draw.polygon(pts, fill=(255, 255, 255), outline=(10, 10, 10))
    # Thick outline along polygon edges
    for i in range(len(pts)):
        draw.line([pts[i], pts[(i + 1) % len(pts)]], fill=(10, 10, 10), width=5)
    _draw_ink_strokes(draw, cx, cy, fill=(18, 18, 18))

    return img


def _make_digital_manga_pdf(path: Path):
    """Build a digital manga PDF with a white oval speech bubble drawn on dark
    artwork AND real selectable vertical Japanese glyphs inside the bubble."""
    w, h = 420.0, 560.0
    cjk_font = fonts.font_for("行くぞ！")
    c = canvas.Canvas(str(path), pagesize=(w, h))
    # Dark artwork background
    c.setFillColorRGB(0.22, 0.22, 0.25)
    c.rect(0, 0, w, h, stroke=0, fill=1)
    # White speech bubble with black border (upper-right)
    c.setFillColorRGB(1.0, 1.0, 1.0)
    c.setStrokeColorRGB(0.05, 0.05, 0.05)
    c.setLineWidth(3.0)
    c.ellipse(220, 340, 380, 490, stroke=1, fill=1)
    # Selectable vertical Japanese dialogue inside the bubble
    c.setFillColorRGB(0.05, 0.05, 0.05)
    c.setFont(cjk_font, 15)
    y = 455
    for ch in "行くぞ！":
        c.drawString(295, y, ch)
        y -= 18
    c.showPage()
    c.save()
    return path


def test_detect_dark_inverted_and_shape_classified_bubbles():
    from pdfmanager.translate.manga import detect_speech_bubbles

    img = _make_v2_manga_image()
    bubbles = detect_speech_bubbles(img)

    assert len(bubbles) == 4
    # Identify each detected bubble by quadrant
    top_right = next(b for b in bubbles if b["cx"] > 420 and b["cy"] < 400)
    top_left = next(b for b in bubbles if b["cx"] < 420 and b["cy"] < 400)
    bot_right = next(b for b in bubbles if b["cx"] > 420 and b["cy"] > 400)
    bot_left = next(b for b in bubbles if b["cx"] < 420 and b["cy"] > 400)

    assert top_right["shape"] == "oval"
    assert top_right["inverted"] is False

    assert top_left["shape"] == "rect"
    assert top_left["inverted"] is False

    assert bot_right["inverted"] is True
    assert max(bot_right["bg_rgb"]) < 0.25
    assert min(bot_right["text_rgb"]) > 0.85

    assert bot_left["shape"] == "polygon"
    assert bot_left.get("mask") is not None


def test_order_manga_bubbles_rtl_vs_ltr():
    from pdfmanager.translate.manga import order_manga_bubbles

    sample = [
        {"x0": 50, "y0": 50, "x1": 200, "y1": 180, "w": 150, "h": 130, "cx": 125.0, "cy": 115.0, "id": "TL"},
        {"x0": 400, "y0": 60, "x1": 550, "y1": 190, "w": 150, "h": 130, "cx": 475.0, "cy": 125.0, "id": "TR"},
        {"x0": 60, "y0": 400, "x1": 210, "y1": 520, "w": 150, "h": 120, "cx": 135.0, "cy": 460.0, "id": "BL"},
    ]
    rtl = order_manga_bubbles(sample, reading_dir="rtl")
    ltr = order_manga_bubbles(sample, reading_dir="ltr")
    assert [b["id"] for b in rtl] == ["TR", "TL", "BL"]
    assert [b["id"] for b in ltr] == ["TL", "TR", "BL"]


def test_preprocess_bubble_for_ocr_inverts_and_upscales():
    import numpy as np
    from pdfmanager.translate.manga import preprocess_bubble_for_ocr

    # Small dark bubble crop (80x60) with bright white center stroke
    dark_crop = Image.new("RGB", (80, 60), (15, 15, 18))
    ImageDraw.Draw(dark_crop).rectangle([30, 15, 50, 45], fill=(250, 250, 250))

    pre = preprocess_bubble_for_ocr(dark_crop, inverted=True)
    # Upscaled (>= 160 px min side before padding) + 12px white padding on each side
    assert min(pre.size) >= 144
    arr = np.asarray(pre.convert("L"))
    # Outer padding border must be bright white, center text stroke must be dark
    assert arr[2, 2] >= 240
    assert arr[arr.shape[0] // 2, arr.shape[1] // 2] <= 80


def test_wrap_balanced_avoids_single_word_orphan():
    from pdfmanager.translate.manga import _wrap_balanced

    # Width where naive greedy wrap would put 3 words on line 1 and 1 tiny orphan word on line 2
    lines = _wrap_balanced("Wait for me right now!", "Helvetica-Bold", 10.0, 95.0)
    assert len(lines) == 2
    assert len(lines[-1].split()) >= 2


def test_manga_v2_end_to_end_inverted_polygon_and_rect(tmp_path):
    img = _make_v2_manga_image()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    src_pdf = tmp_path / "manga_v2.pdf"
    c = canvas.Canvas(str(src_pdf), pagesize=(420.0, 560.0))
    c.drawImage(ImageReader(buf), 0, 0, width=420.0, height=560.0)
    c.showPage()
    c.save()

    out_pdf = tmp_path / "manga_v2_en.pdf"
    ocr_lines = ["行くぞ！", "その時…", "まさか！", "うおおお！"]
    idx = {"i": 0}

    def fake_ocr(crop, lang):
        i = idx["i"]
        idx["i"] += 1
        return ocr_lines[i] if i < len(ocr_lines) else ""

    def fake_tr(texts):
        mp = {
            "行くぞ！": "Let's go!",
            "その時…": "At that moment...",
            "まさか！": "No way!",
            "うおおお！": "WHOAAA!",
        }
        return [mp.get(t, t) for t in texts]

    res = translate.translate_pdf(
        src_pdf, out_pdf, "ja", "en", mode="manga", translator=fake_tr, ocr_fn=fake_ocr,
    )
    assert res["mode"] == "manga"
    assert res["bubbles"] == 4
    text = " ".join((PdfReader(str(out_pdf)).pages[0].extract_text() or "").split())
    assert "Let's go!" in text
    assert "At that moment..." in text
    assert "No way!" in text
    assert "WHOAAA!" in text


def test_manga_v2_digital_pdf_zero_ocr_fast_path(tmp_path):
    src_pdf = _make_digital_manga_pdf(tmp_path / "digital_manga.pdf")
    out_pdf = tmp_path / "digital_manga_en.pdf"

    ocr_calls = {"n": 0}

    def fail_if_ocr_called(crop, lang):
        ocr_calls["n"] += 1
        return "SHOULD_NOT_BE_CALLED"

    def fake_tr(texts):
        return ["Let's go!" if "行くぞ" in t else t for t in texts]

    res = translate.translate_pdf(
        src_pdf, out_pdf, "ja", "en", mode="manga", translator=fake_tr, ocr_fn=fail_if_ocr_called,
    )
    assert res["bubbles"] == 1
    assert ocr_calls["n"] == 0
    text = " ".join((PdfReader(str(out_pdf)).pages[0].extract_text() or "").split())
    assert "Let's go!" in text


def test_manga_mode_clamps_giant_page_render_scale(tmp_path, monkeypatch):
    from pdfmanager import core

    src_pdf = tmp_path / "giant_poster.pdf"
    c = canvas.Canvas(str(src_pdf), pagesize=(6000.0, 6000.0))
    c.setFillColorRGB(0.2, 0.2, 0.2)
    c.rect(0, 0, 6000, 6000, stroke=0, fill=1)
    c.showPage()
    c.save()

    seen_scales = []
    orig_render_scale = core.render_scale

    def spy_render_scale(pg, scale, limit=None):
        clamped = orig_render_scale(pg, scale, limit=limit)
        seen_scales.append(clamped)
        return clamped

    monkeypatch.setattr(core, "render_scale", spy_render_scale)
    out_pdf = tmp_path / "giant_out.pdf"
    translate.translate_pdf(src_pdf, out_pdf, "ja", "en", mode="manga", translator=lambda xs: xs)
    assert len(seen_scales) == 1
    assert seen_scales[0] < 2.0
