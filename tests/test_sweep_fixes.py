"""Fixes from the 0.27.0 maturity sweep (tests/sweep): each test makes a
small file with the trait that broke a tool, and checks the tool now works
or stops with a plain message."""
import sys
from pathlib import Path

import pikepdf
import pytest
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).parent))
from test_certsign import PFX_PASSWORD, make_id  # noqa: E402

from pdfmanager import gui  # noqa: E402


def text_pdf(path, pages=2, size=(612, 792), words="The quick brown fox"):
    c = canvas.Canvas(str(path), pagesize=size)
    for n in range(pages):
        c.drawString(72, size[1] - 72, f"{words}, page {n + 1}")
        c.showPage()
    c.save()
    return path


def edit(path, change):
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        change(pdf)
        pdf.save(path)
    return path


def run(op, files, opts, tmp_path, extra=None):
    work = tmp_path / f"work-{op}"
    work.mkdir()
    notes = []
    name, data, ctype = gui.run_operation(op, [Path(f) for f in files], opts, None, extra or {}, work, notes)
    return name, data, notes


def test_a_rotation_that_isnt_a_quarter_turn_counts_as_none(tmp_path):
    src = edit(text_pdf(tmp_path / "odd.pdf"), lambda pdf: [setattr(p.obj, "Rotate", 45) for p in pdf.pages])
    pfx = make_id(tmp_path / "id.pfx")[0]
    name, data, notes = run("sign-cert", [src], {"pfx_password": PFX_PASSWORD, "appearance": "visible",
                                                 "page": "last", "position": "bottom-right", "size": "medium"},
                            tmp_path, {"pfx": pfx, "pfx[]": [pfx]})
    assert data.startswith(b"%PDF") and any("signed" in n for n in notes)


def test_a_table_cell_with_only_a_sign_or_a_currency_is_text():
    from pdfmanager.tables import parse_number

    for text in ("-", "–", "+", "$", "€", "($)", "(-)", "$ -"):
        assert parse_number(text) is None, text
    assert parse_number("$-12")[0] == -12 and parse_number("12 €")[0] == 12


def test_a_table_with_dashes_for_empty_cells_goes_to_excel(tmp_path):
    src = tmp_path / "dashes.pdf"
    c = canvas.Canvas(str(src))
    for r in range(6):
        for col in range(4):
            c.rect(50 + col * 100, 700 - r * 20, 100, 20)
            c.drawString(54 + col * 100, 706 - r * 20, ["Item", "-", "$", "12.50"][col] if r else f"Head {col}")
    c.showPage()
    c.save()
    name, data, notes = run("to-excel", [src], {"layout": "sheet-per-table", "format": "xlsx"}, tmp_path)
    assert name.endswith(".xlsx") and data[:2] == b"PK"


def test_inline_images_pass_the_pdfa_check(tmp_path):
    from pdfmanager import pdfacheck

    src = tmp_path / "inline.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.pages[0].obj.Contents = pdf.make_stream(
        b"q 100 0 0 100 50 600 cm BI /W 2 /H 2 /CS /G /BPC 8 /F /AHx ID 00ff00ff> EI Q")
    pdf.save(src)
    result = pdfacheck.check(src, level="2b", engine="builtin")
    assert isinstance(result["issues"], list)
    name, data, notes = run("pdfa", [src], {"level": "2"}, tmp_path)
    assert data.startswith(b"%PDF")


@pytest.mark.parametrize("op", ["to-images", "to-pptx", "to-word", "to-html", "form", "compare", "autocrop"])
def test_a_huge_page_is_drawn_with_fewer_pixels_not_out_of_memory(tmp_path, monkeypatch, op):
    import io
    import zipfile

    from PIL import Image

    from pdfmanager import core

    drawn = []
    real = core.render_scale

    def watched(page, scale, limit=None):
        got = real(page, scale, limit)
        w, h = page.get_size()
        drawn.append(w * h * got * got)
        return got

    monkeypatch.setattr(core, "MAX_RENDER_PIXELS", 2_000_000)
    monkeypatch.setattr(core, "render_scale", watched)
    src = text_pdf(tmp_path / "poster.pdf", pages=1, size=(14400, 14400))
    files = [src, text_pdf(tmp_path / "other.pdf", pages=1, size=(14400, 14400))] if op == "compare" else [src]
    opts = {"to-word": {"engine": "basic"}, "form": {"detect": "true"}}.get(op, {})
    try:
        name, data, notes = run(op, files, opts, tmp_path)
    except gui.EXPECTED_ERRORS as exc:  # (Add form fields finds nothing to fill in on a poster)
        assert op == "form", exc
        return
    assert drawn and max(drawn) <= 2_000_001
    if op == "to-images":
        image = Image.open(io.BytesIO(data)) if name.endswith(".png") else None
        if image is None:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                image = Image.open(io.BytesIO(z.read(z.namelist()[0])))
        assert image.width * image.height <= 2_020_000
        assert round(image.info["dpi"][0]) < 150  # the picture still says how big the page is
