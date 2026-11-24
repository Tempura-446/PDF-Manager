# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""TDD tests for Vertical CJK (tategaki) extraction and Manga speech-bubble translation (v0.67.0)."""

import io
from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfReader
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdfmanager import fonts, translate


def _make_vertical_cjk_pdf(path: Path, columns, *, furigana=None, width=420, height=600):
    """Build a synthetic vertical Japanese (tategaki) PDF.

    ``columns`` is a list of ``(x, y_start, text, size)`` tuples where each
    character of ``text`` is drawn top-to-bottom at ``x``.
    ``furigana`` is an optional list of ``(x, y_start, text, size)`` ruby runs.
    """
    sample = "".join(t for _, _, t, _ in columns) + "".join(t for _, _, t, _ in (furigana or []))
    cjk_font = fonts.font_for(sample or "日本語")
    c = canvas.Canvas(str(path), pagesize=(width, height))
    for x, y_start, text, size in columns:
        c.setFont(cjk_font, size)
        y = y_start
        for ch in text:
            c.drawString(x, y, ch)
            y -= size * 1.18
    for x, y_start, text, size in (furigana or []):
        c.setFont(cjk_font, size)
        y = y_start
        for ch in text:
            c.drawString(x, y, ch)
            y -= size * 1.12
    c.showPage()
    c.save()
    return path


def _make_horizontal_pdf(path: Path, lines, *, width=420, height=600):
    sample = "".join(lines)
    font_name = fonts.font_for(sample or "Hello")
    c = canvas.Canvas(str(path), pagesize=(width, height))
    c.setFont(font_name, 12)
    y = height - 60
    for line in lines:
        c.drawString(50, y, line)
        y -= 20
    c.showPage()
    c.save()
    return path


def _make_synthetic_manga_image(width=840, height=1120):
    """Create a synthetic raster manga page with dark artwork, 3 speech bubbles,
    a white outer margin border, and an empty white panel (no dialogue ink)."""
    img = Image.new("RGB", (width, height), (38, 38, 42))
    draw = ImageDraw.Draw(img)

    # White top margin touching page border (must NOT be detected as a bubble)
    draw.rectangle([0, 0, width - 1, 22], fill=(255, 255, 255))

    # Empty white highlight panel with no text ink (must NOT be detected)
    draw.rectangle([90, 620, 260, 760], fill=(255, 255, 255), outline=(0, 0, 0), width=4)

    # Three enclosed white speech bubbles with black outline + dark ink strokes inside
    bubbles = [
        # (x0, y0, x1, y1, label)
        (520, 70, 760, 260, "top-right"),
        (80, 95, 320, 275, "top-left"),
        (480, 560, 750, 780, "bottom-right"),
    ]
    for x0, y0, x1, y1, _ in bubbles:
        draw.ellipse([x0, y0, x1, y1], fill=(255, 255, 255), outline=(10, 10, 10), width=6)
        # Simulated vertical Japanese ink strokes inside the bubble
        cx = (x0 + x1) // 2
        cy = (y0 + y1) // 2
        for dx in (-26, 0, 26):
            for dy in (-40, -18, 4, 26):
                draw.rectangle(
                    [cx + dx - 6, cy + dy - 6, cx + dx + 6, cy + dy + 6],
                    fill=(18, 18, 18),
                )
    return img


def _make_manga_pdf(path: Path, pil_img: Image.Image):
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    buf.seek(0)
    w, h = 420.0, 560.0
    c = canvas.Canvas(str(path), pagesize=(w, h))
    c.drawImage(ImageReader(buf), 0, 0, width=w, height=h)
    c.showPage()
    c.save()
    return path


# ---- Track A: Vertical CJK (tategaki) tests ------------------------------------------


def test_normalize_vertical_punct():
    from pdfmanager.translate.vertical import normalize_vertical_punct

    raw = "﹁こんにちは﹂︵世界︶︱テスト"
    norm = normalize_vertical_punct(raw)
    assert "「こんにちは」" in norm
    assert "（世界）" in norm
    assert "—" in norm or "ー" in norm


def test_is_vertical_cjk_page_detection(tmp_path):
    import pdfplumber
    from pdfmanager.translate.vertical import is_vertical_cjk_page

    vert_pdf = _make_vertical_cjk_pdf(
        tmp_path / "vert.pdf",
        [
            (340, 520, "吾輩は猫である", 14),
            (310, 520, "名前はまだ無い", 14),
        ],
    )
    horiz_en = _make_horizontal_pdf(
        tmp_path / "horiz_en.pdf",
        ["This is a normal horizontal English page.", "Second line of text here."],
    )
    horiz_ja = _make_horizontal_pdf(
        tmp_path / "horiz_ja.pdf",
        ["吾輩は猫である。名前はまだ無い。", "どこで生れたかとんと見当がつかぬ。"],
    )

    with pdfplumber.open(str(vert_pdf)) as doc:
        assert is_vertical_cjk_page(doc.pages[0]) is True
    with pdfplumber.open(str(horiz_en)) as doc:
        assert is_vertical_cjk_page(doc.pages[0]) is False
    with pdfplumber.open(str(horiz_ja)) as doc:
        assert is_vertical_cjk_page(doc.pages[0]) is False


def test_extract_vertical_cjk_blocks_rtl_and_furigana_strip(tmp_path):
    import pdfplumber
    from pdfmanager.translate.vertical import extract_vertical_cjk_blocks

    # Two paragraphs separated by a wider horizontal gap:
    # Rightmost paragraph (cols at x=340 and x=316) -> read FIRST
    # Left paragraph (col at x=240) -> read SECOND
    # Furigana ruby at x=356 (size=6, <= 0.62*14) -> stripped
    vert_pdf = _make_vertical_cjk_pdf(
        tmp_path / "novel.pdf",
        [
            (340, 520, "﹁吾輩は猫である", 14),
            (316, 520, "名前はまだ無い﹂", 14),
            (240, 520, "どこで生れたか", 14),
        ],
        furigana=[
            (356, 504, "わがはい", 6),
        ],
    )

    with pdfplumber.open(str(vert_pdf)) as doc:
        blocks = extract_vertical_cjk_blocks(doc.pages[0])

    assert len(blocks) == 2
    # Rightmost paragraph comes first, columns joined RTL, vertical quotes normalized, furigana stripped
    assert blocks[0]["text"] == "「吾輩は猫である名前はまだ無い」"
    assert "わがはい" not in blocks[0]["text"]
    assert blocks[1]["text"] == "どこで生れたか"
    # Bounding box spans both merged columns of paragraph 1
    assert blocks[0]["x1"] > blocks[0]["x0"] + 20


def test_translate_vertical_cjk_end_to_end_bilingual_modes(tmp_path):
    vert_pdf = _make_vertical_cjk_pdf(
        tmp_path / "tategaki_book.pdf",
        [
            (340, 520, "春はあけぼの", 14),
            (260, 520, "夏は夜", 14),
        ],
    )
    seen = []

    def fake_tr(texts):
        seen.extend(texts)
        mapping = {
            "春はあけぼの": "In spring, the dawn.",
            "夏は夜": "In summer, the nights.",
        }
        return [mapping.get(t, f"EN:{t}") for t in texts]

    out_bp = tmp_path / "out_bilingual_pages.pdf"
    res_bp = translate.translate_pdf(
        vert_pdf, out_bp, "ja", "en", mode="bilingual-pages", translator=fake_tr,
    )
    assert res_bp["mode"] == "bilingual-pages"
    assert seen == ["春はあけぼの", "夏は夜"]
    reader_bp = PdfReader(str(out_bp))
    assert len(reader_bp.pages) == 2
    trans_text = " ".join((reader_bp.pages[1].extract_text() or "").split())
    assert "In spring, the dawn." in trans_text
    assert "In summer, the nights." in trans_text

    out_bi = tmp_path / "out_bilingual.pdf"
    translate.translate_pdf(
        vert_pdf, out_bi, "ja", "en", mode="bilingual", translator=fake_tr,
    )
    bi_text = PdfReader(str(out_bi)).pages[0].extract_text() or ""
    assert bi_text.index("In spring, the dawn.") < bi_text.index("In summer, the nights.")


# ---- Track B: Manga speech bubble mode tests -----------------------------------------


def test_detect_speech_bubbles_rtl_order_and_filtering():
    from pdfmanager.translate.manga import detect_speech_bubbles

    img = _make_synthetic_manga_image()
    bubbles = detect_speech_bubbles(img)

    assert len(bubbles) == 3
    # Manga reading order: top-right, then top-left, then bottom-right
    cx0, cy0 = bubbles[0]["cx"], bubbles[0]["cy"]
    cx1, cy1 = bubbles[1]["cx"], bubbles[1]["cy"]
    cx2, cy2 = bubbles[2]["cx"], bubbles[2]["cy"]

    assert cx0 > cx1  # top-right before top-left
    assert cy0 < cy2 and cy1 < cy2  # top band before bottom-right bubble
    assert cx2 > 450 and cy2 > 500


def test_translate_pdf_manga_mode_end_to_end(tmp_path):
    assert "manga" in translate.MODES

    img = _make_synthetic_manga_image()
    manga_pdf = _make_manga_pdf(tmp_path / "manga_ch1.pdf", img)
    out_pdf = tmp_path / "manga_ch1_en.pdf"

    # Deterministic OCR returning unique Japanese per bubble in detection order
    ocr_responses = ["待ってくれ！", "何だと！？", "行くぞ！"]
    ocr_idx = {"i": 0}

    def fake_ocr(crop_img, src_lang):
        i = ocr_idx["i"]
        ocr_idx["i"] += 1
        return ocr_responses[i] if i < len(ocr_responses) else ""

    def fake_tr(texts):
        mapping = {
            "待ってくれ！": "Wait for me!",
            "何だと！？": "What did you say!?",
            "行くぞ！": "Let's go!",
        }
        return [mapping.get(t, t) for t in texts]

    res = translate.translate_pdf(
        manga_pdf,
        out_pdf,
        "ja",
        "en",
        mode="manga",
        translator=fake_tr,
        ocr_fn=fake_ocr,
    )
    assert res["mode"] == "manga"
    assert res["bubbles"] == 3
    assert out_pdf.is_file()

    out_reader = PdfReader(str(out_pdf))
    assert len(out_reader.pages) == 1
    page_text = " ".join((out_reader.pages[0].extract_text() or "").split())
    assert "Wait for me!" in page_text
    assert "What did you say!?" in page_text
    assert "Let's go!" in page_text
