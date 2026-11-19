# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for GUI wiring of Offline PDF Translation and Settings -> Language."""

from pathlib import Path

from reportlab.pdfgen import canvas

from pdfmanager import translate
from pdfmanager.server import operations

ROOT = Path(__file__).resolve().parent.parent


def _make_pdf(path, text="Invoice INV-2026-118 total due."):
    c = canvas.Canvas(str(path), pagesize=(595, 842))
    c.setFont("Helvetica", 12)
    c.drawString(72, 760, text)
    c.showPage()
    c.save()


def test_translate_tool_registered_in_operations():
    op = next((o for o in operations.OPERATIONS if o["id"] == "translate"), None)
    assert op is not None
    assert op["group"] == "Convert"
    assert "Translate" in op["label"]
    field_names = {f["key"] for f in op["fields"]}
    assert {"source_lang", "target_lang", "mode"} <= field_names


def test_translate_operation_runs_side_by_side_and_text(tmp_path, monkeypatch):
    src = tmp_path / "sample.pdf"
    _make_pdf(src)
    monkeypatch.setattr(translate, "_load_offline_translator", lambda s, t: (lambda batch: [f"[es] {x}" for x in batch]))

    work1 = tmp_path / "work_pdf"
    work1.mkdir()
    notes1 = []
    name_pdf, data_pdf, mime_pdf = operations.run_operation(
        "translate",
        [src],
        {"source_lang": "en", "target_lang": "es", "mode": "side-by-side"},
        work=work1,
        notes=notes1,
    )
    assert name_pdf.endswith(".pdf")
    assert mime_pdf == "application/pdf"
    assert data_pdf.startswith(b"%PDF")
    assert notes1 and "en → es" in notes1[0]

    work2 = tmp_path / "work_txt"
    work2.mkdir()
    name_txt, data_txt, mime_txt = operations.run_operation(
        "translate",
        [src],
        {"source_lang": "en", "target_lang": "es", "mode": "text"},
        work=work2,
    )
    assert name_txt.endswith(".txt")
    body = data_txt.decode("utf-8")
    assert "INV-2026-118" in body
    assert "[es]" in body


def test_gui_and_reader_wire_translate_and_language_settings():
    html = (ROOT / "pdfmanager" / "gui.html").read_text(encoding="utf-8")
    assert "/static/i18n.js" in html
    assert 'id="set-language"' in html
    assert 'id="set-pack-install"' in html
    assert 'id="set-pack-file"' in html

    reader_js = (ROOT / "pdfmanager" / "static" / "reader.js").read_text(encoding="utf-8")
    assert "Translate this page" in reader_js
    assert "translateCurrentPage()" in reader_js
    assert "/api/translate/page" in reader_js
