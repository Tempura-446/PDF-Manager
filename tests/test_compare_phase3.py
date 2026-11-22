# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Phase 3 PDF Comparison Engine regression tests (C-001, C-002, C-003)."""
import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4, LETTER, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdfmanager import compare, progress


def _scan_image(pattern_id, size=(612, 792)):
    """Create a distinct synthetic scanned-page image (no extractable text)."""
    w, h = size
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    if pattern_id == "blank":
        return img
    if pattern_id == "A":
        # Thick top banner + left column blocks
        d.rectangle([40, 40, w - 40, 140], fill=(20, 20, 20))
        for y in range(180, 680, 60):
            d.rectangle([50, y, 260, y + 30], fill=(40, 40, 40))
    elif pattern_id == "B":
        # Right column blocks + bottom banner
        for y in range(80, 560, 60):
            d.rectangle([320, y, w - 50, y + 30], fill=(30, 30, 30))
        d.rectangle([40, h - 140, w - 40, h - 40], fill=(20, 20, 20))
    elif pattern_id == "C":
        # Diagonal stripes / center X
        d.line([(50, 50), (w - 50, h - 50)], fill=(15, 15, 15), width=28)
        d.line([(w - 50, 50), (50, h - 50)], fill=(15, 15, 15), width=28)
        d.ellipse([w // 2 - 90, h // 2 - 90, w // 2 + 90, h // 2 + 90], outline=(10, 10, 10), width=18)
    elif pattern_id == "D":
        # Checkerboard quadrants
        d.rectangle([40, 40, w // 2 - 20, h // 2 - 20], fill=(25, 25, 25))
        d.rectangle([w // 2 + 20, h // 2 + 20, w - 40, h - 40], fill=(25, 25, 25))
    elif pattern_id == "E":
        # Three horizontal bands in the middle
        for y in (220, 360, 500):
            d.rectangle([60, y, w - 60, y + 45], fill=(35, 35, 35))
    return img


def _make_scanned_pdf(path, patterns, pagesize=(612, 792)):
    c = canvas.Canvas(str(path), pagesize=pagesize)
    w, h = pagesize
    for pat in patterns:
        img = _scan_image(pat, size=(int(w), int(h)))
        c.drawImage(ImageReader(img), 0, 0, w, h)
        c.showPage()
    c.save()
    return path


def _make_text_pdf(path, pages, pagesize=LETTER):
    c = canvas.Canvas(str(path), pagesize=pagesize)
    for page_items in pages:
        c.setFont("Helvetica", 12)
        for item in page_items:
            if isinstance(item, tuple) and len(item) == 3:
                x, y, text = item
                c.drawString(x, y, text)
            elif isinstance(item, tuple) and item[0] == "rect":
                _, x, y, w, h = item
                c.setFillColorRGB(0.1, 0.1, 0.1)
                c.rect(x, y, w, h, fill=1, stroke=0)
                c.setFillColorRGB(0, 0, 0)
        c.showPage()
    c.save()
    return path


# ---- C-001: Multi-signal page alignment (scanned, inserted, deleted, reordered, blank) ----

def test_c001_scanned_pdf_insertion_and_deletion_align_accurately(tmp_path):
    """Inserted/deleted pages in OCR-less scanned PDFs must not misalign subsequent pages."""
    old_pdf = _make_scanned_pdf(tmp_path / "scan_old.pdf", ["A", "B", "C"])
    # Insert "D" at the very beginning (page 1)
    ins_pdf = _make_scanned_pdf(tmp_path / "scan_ins.pdf", ["D", "A", "B", "C"])
    res_ins = compare.compare(old_pdf, ins_pdf, tmp_path / "diff_ins.pdf", all_pages=True)
    pairs_ins = [(r["old"], r["new"], r["kind"]) for r in res_ins["changes"]]
    assert pairs_ins == [
        (None, 1, "added"),
        (1, 2, "same"),
        (2, 3, "same"),
        (3, 4, "same"),
    ]

    # Delete middle page "B"
    del_pdf = _make_scanned_pdf(tmp_path / "scan_del.pdf", ["A", "C"])
    res_del = compare.compare(old_pdf, del_pdf, tmp_path / "diff_del.pdf", all_pages=True)
    pairs_del = [(r["old"], r["new"], r["kind"]) for r in res_del["changes"]]
    assert pairs_del == [
        (1, 1, "same"),
        (2, None, "removed"),
        (3, 2, "same"),
    ]


def test_c001_reordered_scanned_pages_detected_with_confidence(tmp_path):
    old_pdf = _make_scanned_pdf(tmp_path / "ord_old.pdf", ["A", "B", "C"])
    new_pdf = _make_scanned_pdf(tmp_path / "ord_new.pdf", ["C", "B", "A"])
    res = compare.compare(old_pdf, new_pdf, tmp_path / "diff_ord.pdf", all_pages=True)
    # Every page aligns to its exact visual counterpart: 1->3, 2->2, 3->1
    by_old = {r["old"]: r for r in res["changes"] if r["old"] is not None}
    assert by_old[1]["new"] == 3 and by_old[1]["kind"] == "same"
    assert by_old[2]["new"] == 2 and by_old[2]["kind"] == "same"
    assert by_old[3]["new"] == 1 and by_old[3]["kind"] == "same"
    # At least two of the swapped pages are flagged as reordered with high confidence
    reordered_rows = [r for r in by_old.values() if r["reordered"]]
    assert len(reordered_rows) >= 2 and all(r["confidence"] >= 0.85 for r in reordered_rows)


def test_c001_blank_page_does_not_match_nonblank_scanned_page(tmp_path):
    old_pdf = _make_scanned_pdf(tmp_path / "blank_old.pdf", ["A", "blank"])
    new_pdf = _make_scanned_pdf(tmp_path / "blank_new.pdf", ["A", "C"])
    res = compare.compare(old_pdf, new_pdf, tmp_path / "diff_blank.pdf", all_pages=True)
    # Page 1 matches; blank page 2 must NOT be paired with non-blank scanned page "C"
    assert (1, 1, "same") in [(r["old"], r["new"], r["kind"]) for r in res["changes"]]
    assert any(r["old"] == 2 and r["new"] is None and r["kind"] == "removed" for r in res["changes"])
    assert any(r["old"] is None and r["new"] == 2 and r["kind"] == "added" for r in res["changes"])


def test_c001_mixed_digital_and_scanned_document(tmp_path):
    old_pdf = tmp_path / "mix_old.pdf"
    new_pdf = tmp_path / "mix_new.pdf"
    for path, insert_scan in ((old_pdf, False), (new_pdf, True)):
        c = canvas.Canvas(str(path), pagesize=LETTER)
        c.setFont("Helvetica", 14)
        c.drawString(72, 700, "Chapter 1 Digital Introduction Page")
        c.showPage()
        if insert_scan:
            c.drawImage(ImageReader(_scan_image("D")), 0, 0, 612, 792)
            c.showPage()
        c.drawImage(ImageReader(_scan_image("C")), 0, 0, 612, 792)
        c.showPage()
        c.setFont("Helvetica", 14)
        c.drawString(72, 700, "Chapter 3 Digital Conclusion Page")
        c.showPage()
        c.save()

    res = compare.compare(old_pdf, new_pdf, tmp_path / "mix_diff.pdf", all_pages=True)
    assert [(r["old"], r["new"], r["kind"]) for r in res["changes"]] == [
        (1, 1, "same"),
        (None, 2, "added"),
        (2, 3, "same"),
        (3, 4, "same"),
    ]


# ---- C-002: Bounded memory & resource cleanup ------------------------------------------

def test_c002_page_renderer_cache_is_bounded_and_cleans_up_on_error(tmp_path):
    pdf = _make_text_pdf(tmp_path / "many.pdf", [[(72, 700, f"Page {i}")] for i in range(1, 16)])
    with compare._PageRenderer(pdf, None, dpi=72, cache_size=3) as renderer:
        for i in range(15):
            img = renderer.get(i)
            assert img.size[0] > 0
            assert len(renderer._cache) <= 3
    assert len(renderer._cache) == 0
    assert renderer._doc is None

    # Cleanup on exception
    with pytest.raises(RuntimeError, match="abort"):
        with compare._PageRenderer(pdf, None, dpi=72, cache_size=3) as renderer:
            renderer.get(0)
            raise RuntimeError("abort")
    assert len(renderer._cache) == 0
    assert renderer._doc is None


def test_c002_compare_does_not_call_legacy_load_and_respects_cancellation(tmp_path, monkeypatch):
    a = _make_text_pdf(tmp_path / "a.pdf", [[(72, 700, f"Line {i}")] for i in range(6)])
    b = _make_text_pdf(tmp_path / "b.pdf", [[(72, 700, f"Line {i}")] for i in range(6)])
    # Ensure compare() uses lazy _analyze() + _PageRenderer, not full-doc _load()
    monkeypatch.setattr(compare, "_load", lambda *args, **kw: (_ for _ in ()).throw(AssertionError("_load must not be called")))
    out = tmp_path / "out.pdf"
    res = compare.compare(a, b, out)
    assert res["old_pages"] == 6 and res["changes"] == []

    # Cancellation during analysis stops cleanly via progress.Job
    job = progress.Job()
    job.cancel()
    job.run(compare.compare, a, b, tmp_path / "cancel.pdf")
    assert job.snapshot()["state"] == "cancelled"


# ---- C-003: Aspect-ratio preservation & page geometry ----------------------------------

def test_c003_letter_vs_legal_and_a4_preserve_aspect_ratio_without_distortion(tmp_path):
    """Comparing Letter vs Legal (and A4) with identical top-left content must NOT
    stretch one page into the other's aspect ratio, while explicitly flagging size_changed."""
    legal = (612.0, 1008.0)
    p_letter = _make_text_pdf(tmp_path / "letter.pdf", [[("rect", 72, LETTER[1] - 140, 120, 60)]], pagesize=LETTER)
    p_legal = _make_text_pdf(tmp_path / "legal.pdf", [[("rect", 72, legal[1] - 140, 120, 60)]], pagesize=legal)

    out = tmp_path / "size_diff.pdf"
    res = compare.compare(p_letter, p_legal, out)
    assert len(res["changes"]) == 1
    row = res["changes"][0]
    assert row["kind"] == "changed"
    assert row["size_changed"] is True
    assert row["orientation_changed"] is False
    # Exact same physical top-left box -> zero distorted pixel diff! (Resizing would have stretched it by 27%)
    assert row["areas"] == []
    report_text = "\n".join((p.extract_text() or "") for p in PdfReader(str(out)).pages)
    assert "page size changed" in report_text.lower()

    # Also verify A4 vs Letter flags size_changed without orientation_changed
    p_a4 = _make_text_pdf(tmp_path / "a4.pdf", [[(72, A4[1] - 100, "Same Header")]], pagesize=A4)
    p_let2 = _make_text_pdf(tmp_path / "let2.pdf", [[(72, LETTER[1] - 100, "Same Header")]], pagesize=LETTER)
    res_a4 = compare.compare(p_let2, p_a4, tmp_path / "a4_diff.pdf")
    assert res_a4["changes"][0]["size_changed"] is True
    assert res_a4["changes"][0]["orientation_changed"] is False


def test_c003_portrait_vs_landscape_flags_orientation_change_and_detects_edge_content(tmp_path):
    p_port = _make_text_pdf(tmp_path / "port.pdf", [[(72, 700, "Shared Header")]], pagesize=LETTER)
    p_land = _make_text_pdf(
        tmp_path / "land.pdf",
        [[(72, 540, "Shared Header"), ("rect", 680, 100, 80, 80)]],
        pagesize=landscape(LETTER),
    )
    out = tmp_path / "orient_diff.pdf"
    res = compare.compare(p_port, p_land, out)
    assert len(res["changes"]) == 1
    row = res["changes"][0]
    assert row["orientation_changed"] is True
    assert row["size_changed"] is True
    # Edge content outside the portrait width must be detected in shared-canvas areas
    assert len(row["areas"]) >= 1
    report_text = "\n".join((p.extract_text() or "") for p in PdfReader(str(out)).pages)
    assert "orientation changed" in report_text.lower()
