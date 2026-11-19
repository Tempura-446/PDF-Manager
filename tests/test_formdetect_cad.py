# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Regression test: quick form detection must not false-trigger on plain CAD drawings."""
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject
from reportlab.pdfgen import canvas

from pdfmanager import formdetect


def _make_titleblock_pdf(path, producer="AutoCAD 2026", add_vp=False):
    raw = path.with_name(path.stem + "_raw.pdf")
    c = canvas.Canvas(str(raw), pagesize=(612, 792))
    # Title block with form-like lines that would otherwise look like fields
    y = 700
    for label in ("Name:", "Date:", "Drawn by:", "Checked by:", "Signature:"):
        c.drawString(72, y, label)
        c.line(150, y - 2, 360, y - 2)
        y -= 36
    c.showPage()
    c.save()

    reader = PdfReader(str(raw))
    writer = PdfWriter()
    page = writer.add_page(reader.pages[0])
    if add_vp:
        page[NameObject("/VP")] = ArrayObject([DictionaryObject({NameObject("/Type"): NameObject("/Viewport")})])
    if producer:
        writer.add_metadata({"/Producer": producer})
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def test_quick_form_detect_suppressed_on_cad_drawings(tmp_path):
    cad_meta = _make_titleblock_pdf(tmp_path / "plan_autocad.pdf", producer="AutoCAD 2026")
    assert formdetect.detect(cad_meta, quick=True) == {}
    # Explicit user-triggered detection (quick=False) still works if asked
    assert formdetect.detect(cad_meta, quick=False)

    cad_vp = _make_titleblock_pdf(tmp_path / "plan_vp.pdf", producer="Generic PDF", add_vp=True)
    assert formdetect.detect(cad_vp, quick=True) == {}

    plain_form = _make_titleblock_pdf(tmp_path / "plain_form.pdf", producer="LibreOffice Writer")
    assert formdetect.detect(plain_form, quick=True)
