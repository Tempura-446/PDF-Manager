# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""TDD tests for Book & Manga Publishing Studio (CBZ archives, RTL/signature booklet imposition, spine calculator) — v0.71.0."""

import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from pdfmanager import cli, core


def _make_numbered_pdf(path: Path, count: int, *, width=300.0, height=450.0):
    c = canvas.Canvas(str(path), pagesize=(width, height))
    for i in range(1, count + 1):
        c.setFont("Helvetica-Bold", 24)
        c.drawCentredString(width / 2.0, height / 2.0, f"P{i}")
        c.showPage()
    c.save()
    return path


def _make_sample_cbz(path: Path, entries):
    """Create a .cbz archive from ``[(filename, pil_image), ...]``."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        for name, img in entries:
            buf = io.BytesIO()
            fmt = "JPEG" if name.lower().endswith((".jpg", ".jpeg")) else "PNG"
            img.save(buf, format=fmt)
            zf.writestr(name, buf.getvalue())
    return path


def test_cbz_to_pdf_natural_sort_rtl_and_zip_slip_guard(tmp_path):
    from pdfmanager import cbz

    # Deliberately out-of-order filenames where lexicographical sort would put page_10 before page_2
    img1 = Image.new("RGB", (200, 300), (255, 0, 0))
    img2 = Image.new("RGB", (200, 300), (0, 255, 0))
    img10 = Image.new("RGB", (200, 300), (0, 0, 255))
    src = _make_sample_cbz(
        tmp_path / "ch1.cbz",
        [("page_10.png", img10), ("page_1.png", img1), ("page_2.png", img2)],
    )
    out_pdf = tmp_path / "ch1.pdf"
    res = cbz.cbz_to_pdf(src, out_pdf, rtl_viewer=True)
    assert res["pages"] == 3
    assert res["entries"] == ["page_1.png", "page_2.png", "page_10.png"]
    reader = PdfReader(str(out_pdf))
    assert len(reader.pages) == 3
    vp = reader.trailer["/Root"].get("/ViewerPreferences")
    assert vp is not None and str(vp.get("/Direction")) == "/R2L"

    # Zip-slip traversal entry must be rejected
    evil = tmp_path / "evil.cbz"
    with zipfile.ZipFile(evil, "w") as zf:
        buf = io.BytesIO()
        img1.save(buf, format="PNG")
        zf.writestr("../escape.png", buf.getvalue())
    with pytest.raises(core.PdfManagerError, match="unsafe path"):
        cbz.cbz_to_pdf(evil, tmp_path / "evil.pdf")


def test_pdf_to_cbz_and_translate_cbz_end_to_end(tmp_path):
    from pdfmanager import cbz

    # Build a 1-page synthetic manga CBZ with 1 white speech bubble on dark artwork
    img = Image.new("RGB", (600, 800), (35, 35, 40))
    draw = ImageDraw.Draw(img)
    draw.ellipse([260, 80, 520, 260], fill=(255, 255, 255), outline=(10, 10, 10), width=6)
    for dx in (-20, 0, 20):
        for dy in (-24, 0, 24):
            draw.rectangle([390 + dx - 5, 170 + dy - 5, 390 + dx + 5, 170 + dy + 5], fill=(15, 15, 15))
    src_cbz = _make_sample_cbz(tmp_path / "manga.cbz", [("001.png", img)])

    # Round-trip PDF -> CBZ with ComicInfo.xml
    mid_pdf = tmp_path / "mid.pdf"
    cbz.cbz_to_pdf(src_cbz, mid_pdf)
    out_cbz = tmp_path / "exported.cbz"
    exp = cbz.pdf_to_cbz(
        mid_pdf,
        out_cbz,
        dpi=72,
        image_format="png",
        comic_info={"Title": "Chapter 1", "Series": "ZPDF Manga", "LanguageISO": "en", "Manga": "YesAndRightToLeft"},
    )
    assert exp["pages"] == 1
    with zipfile.ZipFile(out_cbz, "r") as zf:
        names = zf.namelist()
        assert "0001.png" in names and "ComicInfo.xml" in names
        xml = zf.read("ComicInfo.xml").decode("utf-8")
        assert "<Title>Chapter 1</Title>" in xml and "<Manga>YesAndRightToLeft</Manga>" in xml

    # One-shot translate_cbz (.cbz -> manga mode -> .cbz)
    tr_cbz = tmp_path / "manga_en.cbz"
    res_tr = cbz.translate_cbz(
        src_cbz,
        tr_cbz,
        "ja",
        "en",
        mode="manga",
        ocr_fn=lambda crop, lang: "行くぞ！",
        translator=lambda xs: ["Let's go!" for _ in xs],
        dpi=72,
        image_format="png",
    )
    assert res_tr["bubbles"] == 1
    assert tr_cbz.is_file()
    with zipfile.ZipFile(tr_cbz, "r") as zf:
        assert "0001.png" in zf.namelist()


def test_booklet_rtl_binding_multi_signature_and_creep(tmp_path):
    # 16-page book: test RTL manga binding & 8-page signatures (2 signatures of 2 sheets each = 8 sides)
    src = _make_numbered_pdf(tmp_path / "book16.pdf", 16)
    out_rtl = tmp_path / "book16_rtl_sig8.pdf"
    core.booklet(src, out_rtl, binding="rtl", signature_pages=8, creep_mm=1.5)
    r = PdfReader(str(out_rtl))
    assert len(r.pages) == 8  # 2 signatures * 4 sides per 8-page signature

    # In RTL 8-page signature 1 (pages 1..8):
    # Front of outer sheet has P1 on LEFT and P8 on RIGHT (so when folded right-to-left, P1 is front cover on right!)
    s0_text = r.pages[0].extract_text() or ""
    assert "P1" in s0_text and "P8" in s0_text
    # Signature 2 (pages 9..16): outer front sheet has P9 and P16
    s4_text = r.pages[4].extract_text() or ""
    assert "P9" in s4_text and "P16" in s4_text


def test_spine_width_calculator_and_cli_commands(tmp_path, capsys):
    info = core.spine_width_mm(200, paper="cream_90gsm", trim_w_mm=152.4, trim_h_mm=228.6, bleed_mm=3.0)
    assert info["pages"] == 200
    assert info["sheets"] == 100
    assert 11.0 <= info["spine_mm"] <= 14.0
    assert info["cover_width_mm"] == pytest.approx(2 * 152.4 + info["spine_mm"] + 6.0, abs=0.2)
    assert info["cover_height_mm"] == pytest.approx(228.6 + 6.0, abs=0.2)

    # CLI: spine
    rc_sp = cli.main(["spine", "200", "--paper", "manga_60gsm", "--json"])
    assert rc_sp == 0
    sp_data = json.loads(capsys.readouterr().out)
    assert sp_data["paper"] == "manga_60gsm" and sp_data["spine_mm"] > 0

    # CLI: cbz-to-pdf & pdf-to-cbz
    img = Image.new("RGB", (120, 180), (240, 240, 240))
    cbz_in = _make_sample_cbz(tmp_path / "cli.cbz", [("01.png", img)])
    pdf_out = tmp_path / "cli.pdf"
    assert cli.main(["cbz-to-pdf", str(cbz_in), "-o", str(pdf_out), "--rtl"]) == 0
    assert pdf_out.is_file() and len(PdfReader(str(pdf_out)).pages) == 1

    cbz_out = tmp_path / "cli_out.cbz"
    assert cli.main(["pdf-to-cbz", str(pdf_out), "-o", str(cbz_out), "--dpi", "72", "--format", "png"]) == 0
    assert cbz_out.is_file()
