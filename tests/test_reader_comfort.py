# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for v0.72.0 Reader Comfort & Power-User Upgrade:
- Workspace Sessions persistence & security
- In-Place OCR Typo Editor (read_all_ocr_words + replace_ocr_text)
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PdfWriter

from pdfmanager import textlayer


def _make_ocr_pdf(path: Path) -> Path:
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    words = [
        textlayer.Word("Invoice", [(50, 100), (140, 100), (140, 120), (50, 120)], confidence=0.98),
        textlayer.Word("Tota1", [(150, 100), (220, 100), (220, 120), (150, 120)], confidence=0.95),
        textlayer.Word("l042", [(230, 100), (300, 100), (300, 120), (230, 120)], confidence=0.42),
        textlayer.Word("tota1", [(50, 150), (120, 150), (120, 170), (50, 170)], confidence=0.91),
    ]
    textlayer.add_text_layer(writer, page, words, (600, 800), 72.0)
    textlayer.record_unsure_words(writer, page, words, (600, 800), 72.0)
    with open(path, "wb") as f:
        writer.write(f)
    return path


def test_read_all_ocr_words_returns_high_and_low_confidence_words(tmp_path):
    pdf = _make_ocr_pdf(tmp_path / "scan.pdf")
    found = textlayer.read_all_ocr_words(pdf)
    assert found["total"] == 4
    p1 = found["pages"]["1"]
    assert [w["text"] for w in p1] == ["Invoice", "Tota1", "l042", "tota1"]
    assert [w["id"] for w in p1] == [0, 1, 2, 3]
    # Unsure flag reflects /PieceInfo metadata
    assert p1[0]["unsure"] is False
    assert p1[2]["unsure"] is True
    # Bounding boxes are valid 4-float page-space rects
    for w in p1:
        x0, y0, x1, y1 = w["box"]
        assert x1 > x0 and y1 > y0
        assert w["page_size"] == [600.0, 800.0]


def test_replace_ocr_text_case_insensitive_and_sensitive(tmp_path):
    pdf = _make_ocr_pdf(tmp_path / "scan.pdf")
    out1 = tmp_path / "fixed_ci.pdf"
    res1 = textlayer.replace_ocr_text(pdf, out1, "tota1", "Total", case_sensitive=False)
    assert res1["replaced"] == 2
    assert res1["pages"] == [1]
    txt1 = PdfReader(out1).pages[0].extract_text()
    assert "Total" in txt1
    assert "Tota1" not in txt1
    assert "tota1" not in txt1

    out2 = tmp_path / "fixed_cs.pdf"
    res2 = textlayer.replace_ocr_text(pdf, out2, "Tota1", "Total", case_sensitive=True)
    assert res2["replaced"] == 1
    txt2 = PdfReader(out2).pages[0].extract_text()
    assert "Total" in txt2
    assert "tota1" in txt2


def test_workspace_sessions_save_list_delete(tmp_path, monkeypatch):
    from pdfmanager import sessions

    monkeypatch.setattr(sessions, "_sessions_path", lambda: tmp_path / "sessions.json")
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(b"%PDF-1.4\n")
    b.write_bytes(b"%PDF-1.4\n")

    saved = sessions.save_session(
        name="Review Pair",
        tabs=[
            {"path": str(a), "name": "a.pdf", "page": 4, "scale": "page-width", "rotation": 0},
            {"path": str(b), "name": "b.pdf", "page": 2, "scale": 1.25, "rotation": 90},
        ],
        active_index=1,
        split={"open": True, "source_index": 0, "ratio": 0.45, "sync": True, "offset": 1},
    )
    assert saved["name"] == "Review Pair"
    assert saved["id"]

    loaded = sessions.list_sessions()
    assert len(loaded) == 1
    assert loaded[0]["active_index"] == 1
    assert loaded[0]["tabs"][0]["page"] == 4
    assert loaded[0]["split"]["open"] is True

    # Updating with the same name replaces in place
    updated = sessions.save_session(
        name="Review Pair",
        tabs=[{"path": str(a), "name": "a.pdf", "page": 9, "scale": "auto", "rotation": 0}],
        active_index=0,
        split={"open": False},
    )
    assert updated["id"] == saved["id"]
    assert len(sessions.list_sessions()) == 1
    assert sessions.list_sessions()[0]["tabs"][0]["page"] == 9

    assert sessions.delete_session(saved["id"]) is True
    assert sessions.list_sessions() == []
    assert sessions.delete_session("nonexistent") is False
