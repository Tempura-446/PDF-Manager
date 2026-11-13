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
