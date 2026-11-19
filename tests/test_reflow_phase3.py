# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Phase 3 paragraph reflow tests: indents & bullets, multi-paragraph, and unchanged leading lines."""
from __future__ import annotations

import pypdf
import pypdfium2 as pdfium
from reportlab.pdfgen import canvas

from pdfmanager import textedit


def _lines_with_positions(path):
    """Return list of (text, left_x, baseline_y) for each text line on page 1."""
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
                txt = ""
                prev_r = None
                for ch, l, b, r, t in row:
                    if prev_r is not None and l - prev_r > 1.8:
                        txt += " "
                    txt += ch
                    prev_r = r
                out.append((txt, row[0][1], row[0][2]))
            return out
        finally:
            tp.close()
    finally:
        doc.close()


def test_bullet_hanging_indent_keeps_bullet_and_wraps_under_text(tmp_path):
    src, out = tmp_path / "bullet.pdf", tmp_path / "bullet_out.pdf"
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    c.setFont("Helvetica", 11)
    # Bullet at x=72, body text starts at x=90; wraps at x=90 on line 2.
    c.drawString(72, 700, "•")
    c.drawString(90, 700, "First bullet item that runs across the")
    c.drawString(90, 685, "margin onto a second line here.")
    c.save()

    before = _lines_with_positions(src)
    bullet_x_before = before[0][1]
    bullet_y_before = before[0][2]

    new_text = (
        "• First bullet item that runs across the margin with several extra "
        "words added so that it grows onto a third line cleanly."
    )
    rep = []
    textedit.edit_text(
        src, out,
        [{"page": 1, "rect": [68, 678, 340, 714], "paragraph": True, "text": new_text}],
        report=rep,
    )
    assert not rep[0].get("covered")
    after = _lines_with_positions(out)
    assert len(after) >= 3, after
    # Bullet glyph itself did not move.
    assert after[0][0].startswith("•")
    assert abs(after[0][1] - bullet_x_before) < 0.5
    assert abs(after[0][2] - bullet_y_before) < 0.5
    # Wrapped continuation lines start under the body text (~90pt), NOT under the bullet (72pt).
    for line_txt, lx, _ in after[1:]:
        assert lx > bullet_x_before + 10, f"continuation line wrapped under bullet: {line_txt!r} at x={lx}"
        assert abs(lx - 90.0) < 3.0, f"expected ~90pt, got {lx}"


def test_first_line_indent_preserved(tmp_path):
    src, out = tmp_path / "indent.pdf", tmp_path / "indent_out.pdf"
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    c.setFont("Helvetica", 11)
    c.drawString(96, 700, "This paragraph begins with a clear")
    c.drawString(72, 685, "first-line indent of twenty-four points")
    c.drawString(72, 670, "while all following lines align left.")
    c.save()

    new_text = (
        "This revised paragraph still begins with a clear first-line indent "
        "while all following lines align cleanly at the left margin."
    )
    rep = []
    textedit.edit_text(
        src, out,
        [{"page": 1, "rect": [68, 664, 300, 714], "paragraph": True, "text": new_text}],
        report=rep,
    )
    assert not rep[0].get("covered")
    after = _lines_with_positions(out)
    assert len(after) >= 2, after
    # First line keeps ~24pt indent relative to subsequent lines.
    assert abs(after[0][1] - 96.0) < 3.0, after
    for _, lx, _ in after[1:]:
        assert abs(lx - 72.0) < 3.0, after


def test_multi_paragraph_keeps_gap_and_split_adds_gap(tmp_path):
    src, out = tmp_path / "two_paras.pdf", tmp_path / "two_paras_out.pdf"
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    c.setFont("Helvetica", 11)
    # Paragraph 1 (lead = 14pt)
    c.drawString(72, 700, "First paragraph line one has enough words")
    c.drawString(72, 686, "to wrap neatly across two lines.")
    # Paragraph gap: extra 12pt (baseline step = 26pt)
    c.drawString(72, 660, "Second paragraph starts after a clear gap")
    c.drawString(72, 646, "and continues on its second line.")
    c.save()

    combined = (
        "First paragraph line one has updated words to wrap neatly across two lines.\n\n"
        "Second paragraph starts after a clear gap and continues on its second line."
    )
    rep = []
    textedit.edit_text(
        src, out,
        [{"page": 1, "rect": [68, 640, 320, 714], "paragraph": True, "text": combined}],
        report=rep,
    )
    assert not rep[0].get("covered")
    after = _lines_with_positions(out)
    ys = [y for _, _, y in after]
    steps = [ys[i] - ys[i + 1] for i in range(len(ys) - 1)]
    assert any(s > 21.0 for s in steps), f"paragraph gap lost: {steps}"
    assert any(12.0 <= s <= 16.0 for s in steps), f"intra-paragraph lead wrong: {steps}"

    # Splitting a single paragraph with \n\n adds a paragraph gap.
    src2, out2 = tmp_path / "split.pdf", tmp_path / "split_out.pdf"
    c = canvas.Canvas(str(src2), pagesize=(612, 792))
    c.setFont("Helvetica", 11)
    c.drawString(72, 700, "Alpha beta gamma delta epsilon zeta")
    c.drawString(72, 686, "eta theta iota kappa lambda mu nu.")
    c.save()

    split_text = "Alpha beta gamma delta epsilon.\n\nZeta eta theta iota kappa lambda mu nu."
    rep2 = []
    textedit.edit_text(
        src2, out2,
        [{"page": 1, "rect": [68, 680, 300, 714], "paragraph": True, "text": split_text}],
        report=rep2,
    )
    assert not rep2[0].get("covered")
    after2 = _lines_with_positions(out2)
    assert len(after2) >= 2, after2
    ys2 = [y for _, _, y in after2]
    steps2 = [ys2[i] - ys2[i + 1] for i in range(len(ys2) - 1)]
    assert max(steps2) > 19.0, f"split did not add paragraph gap: {steps2}"


def test_last_line_change_leaves_first_nine_lines_byte_identical(tmp_path):
    src, out = tmp_path / "ten_lines.pdf", tmp_path / "ten_lines_out.pdf"
    orig_lines = [
        f"Line {i:02d} contains steady unchanging words across the full width."
        for i in range(1, 11)
    ]
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    c.setFont("Helvetica", 11)
    for idx, line in enumerate(orig_lines):
        c.drawString(72, 720 - idx * 14, line)
    c.save()

    before_ops = [
        (bytes(ops[0].original_bytes) if hasattr(ops[0], "original_bytes") else str(ops[0]), op)
        for ops, op in pypdf.generic.ContentStream(pypdf.PdfReader(str(src)).pages[0].get_contents(), None).operations
        if op == b"Tj"
    ]
    new_lines = list(orig_lines)
    new_lines[-1] = "Line 10 contains steady updated words across the full width."
    rep = []
    textedit.edit_text(
        src, out,
        [{"page": 1, "rect": [68, 580, 460, 734], "paragraph": True, "text": " ".join(new_lines)}],
        report=rep,
    )
    assert not rep[0].get("covered")
    after_ops = [
        (bytes(ops[0].original_bytes) if hasattr(ops[0], "original_bytes") else str(ops[0]), op)
        for ops, op in pypdf.generic.ContentStream(pypdf.PdfReader(str(out)).pages[0].get_contents(), None).operations
        if op == b"Tj"
    ]
    # All 9 untouched leading lines keep their exact original Tj operations and glyph bytes.
    assert len(before_ops) == 10
    assert after_ops == before_ops[:9]
    # And the 10th line was updated.
    after_text = " ".join(t for t, _, _ in _lines_with_positions(out))
    assert "updated words" in after_text
    assert "Line 10 contains steady unchanging" not in after_text
