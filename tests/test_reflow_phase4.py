# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Phase 4 paragraph reflow tests: flow around pictures and across pages."""
from __future__ import annotations

import pypdf
import pypdfium2 as pdfium
from PIL import Image
from reportlab.pdfgen import canvas

from pdfmanager import textedit


def _page_lines(path, page_index=0):
    """Return list of (text, x0, y_bottom, x1, y_top) for lines on ``page_index``."""
    doc = pdfium.PdfDocument(str(path))
    try:
        tp = doc[page_index].get_textpage()
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
                txt = ""
                prev_r = None
                for ch, l, b, r, t in row:
                    if prev_r is not None and l - prev_r > 1.8:
                        txt += " "
                    txt += ch
                    prev_r = r
                out.append((
                    txt,
                    min(c[1] for c in row),
                    min(c[2] for c in row),
                    max(c[3] for c in row),
                    max(c[4] for c in row),
                ))
            return out
        finally:
            tp.close()
    finally:
        doc.close()


def test_paragraph_wraps_around_picture_and_expands_below(tmp_path):
    src, out = tmp_path / "pic.pdf", tmp_path / "pic_out.pdf"
    img_path = tmp_path / "box.png"
    Image.new("RGB", (120, 60), (40, 110, 180)).save(img_path)

    # Picture sits on the right side of the column: x=[260, 380], y=[675, 715].
    pic_box = (260.0, 675.0, 380.0, 715.0)
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    c.drawImage(str(img_path), pic_box[0], pic_box[1], width=pic_box[2] - pic_box[0], height=pic_box[3] - pic_box[1])
    c.setFont("Helvetica", 11)
    c.drawString(72, 700, "Short line beside the")
    c.drawString(72, 686, "embedded picture here")
    c.drawString(72, 660, "Below the picture the paragraph runs across the full column width easily.")
    c.save()

    new_text = (
        "Updated text beside the embedded picture keeps clear of the image box "
        "and once the lines clear the bottom of the picture they expand across the "
        "full column width cleanly without overlapping any part of the picture."
    )
    rep = []
    textedit.edit_text(
        src, out,
        [{"page": 1, "rect": [68, 650, 440, 716], "paragraph": True, "text": new_text}],
        report=rep,
    )
    assert not rep[0].get("covered")
    assert rep[0].get("wrapped_picture") is True

    lines = _page_lines(out, 0)
    beside = [ln for ln in lines if ln[4] > pic_box[1] and ln[2] < pic_box[3]]
    below = [ln for ln in lines if ln[4] <= pic_box[1]]
    assert beside, f"expected lines beside picture: {lines}"
    assert below, f"expected lines below picture: {lines}"
    # No letter beside the picture may overlap the picture box horizontally.
    for txt, x0, y0, x1, y1 in beside:
        assert x1 <= pic_box[0] + 1.0, f"line {txt!r} overlapped picture at x1={x1} > {pic_box[0]}"
    # Lines below the picture expand wider than the lines beside it.
    assert max(ln[3] for ln in below) > pic_box[0] + 20.0, f"lines below didn't expand: {below}"


def test_overflow_continues_onto_next_page_and_shifts_page2_down(tmp_path):
    src, out = tmp_path / "two_pages.pdf", tmp_path / "two_pages_out.pdf"
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    c.setFont("Helvetica", 11)
    # Page 1: paragraph right near the bottom margin (MARGIN=72pt).
    c.drawString(72, 102, "Closing paragraph on page one near the")
    c.drawString(72, 88, "bottom margin of the first page here.")
    c.showPage()
    # Page 2: paragraph at the top.
    c.setFont("Helvetica", 11)
    c.drawString(72, 700, "Top paragraph of page two starts right here")
    c.drawString(72, 686, "and should move down when page one overflows.")
    c.save()

    p2_before = _page_lines(src, 1)
    p2_top_before = p2_before[0][2]

    grown = (
        "Closing paragraph on page one near the bottom margin of the first page "
        "now has many extra words added so that three extra lines must flow over "
        "onto the top of the second page without pushing anything off page one "
        "and moving page two downward cleanly."
    )
    # First check default (no fit): signals can_next_page and overflow.
    rep_ask = []
    textedit.edit_text(
        src, tmp_path / "ask.pdf",
        [{"page": 1, "rect": [68, 80, 290, 118], "paragraph": True, "text": grown}],
        report=rep_ask,
    )
    assert rep_ask[0].get("overflow") is True
    assert rep_ask[0].get("can_next_page") is True

    # Now with fit="next_page": overflow moves to top of page 2 and page 2's text moves down.
    rep = []
    textedit.edit_text(
        src, out,
        [{"page": 1, "rect": [68, 80, 290, 118], "paragraph": True, "text": grown, "fit": "next_page"}],
        report=rep,
    )
    assert not rep[0].get("covered")
    assert rep[0].get("next_page", 0) >= 2
    assert rep[0].get("moved_next_page", 0) == 2

    p1_after = _page_lines(out, 0)
    p2_after = _page_lines(out, 1)
    assert any("Top paragraph of page two" in ln[0] for ln in p2_after)
    assert any("downward cleanly" in ln[0] for ln in p2_after)
    moved_top = next(ln[2] for ln in p2_after if "Top paragraph of page two" in ln[0])
    assert p2_top_before - moved_top >= 25.0, f"page 2 text didn't shift down: before={p2_top_before}, after={moved_top}"
    # Page 1 lines stay above bottom margin.
    assert min(ln[2] for ln in p1_after) >= 65.0


def test_last_page_overflow_offers_add_page_and_adds_page(tmp_path):
    src, out = tmp_path / "last_page.pdf", tmp_path / "last_page_out.pdf"
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    c.setFont("Helvetica", 11)
    c.drawString(72, 102, "Closing paragraph on the only page near")
    c.drawString(72, 88, "the bottom margin of this document.")
    c.save()

    grown = (
        "Closing paragraph on the only page near the bottom margin of this document "
        "now grows by several extra lines that cannot fit on the first page and "
        "should spill onto a newly added second page cleanly."
    )
    rep_ask = []
    textedit.edit_text(
        src, tmp_path / "ask_last.pdf",
        [{"page": 1, "rect": [68, 80, 290, 118], "paragraph": True, "text": grown}],
        report=rep_ask,
    )
    assert rep_ask[0].get("overflow") is True
    assert rep_ask[0].get("add_page") is True

    rep = []
    textedit.edit_text(
        src, out,
        [{"page": 1, "rect": [68, 80, 290, 118], "paragraph": True, "text": grown, "fit": "add_page"}],
        report=rep,
    )
    assert rep[0].get("added_page", 0) >= 1
    assert len(pypdf.PdfReader(str(out)).pages) == 2
    p2_lines = _page_lines(out, 1)
    assert any("second page cleanly" in ln[0] for ln in p2_lines), p2_lines
