# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for drawing sheet sets: split/name sheets, cross-sheet links, and set compare."""

from pathlib import Path

from pypdf import PdfReader
from reportlab.lib.pagesizes import landscape, A3
from reportlab.pdfgen import canvas

from pdfmanager import cli, filetools, sheetsets


def _make_set(path: Path, sheets: list[dict]) -> Path:
    """Create a multi-sheet drawing PDF with a bottom-right title block on each page."""
    w, h = landscape(A3)
    c = canvas.Canvas(str(path), pagesize=(w, h))
    for sh in sheets:
        # Drawing body
        c.setLineWidth(2)
        c.rect(60, 120, w - 340, h - 180)
        if sh.get("extra_box"):
            c.setFillColorRGB(0, 0, 0)
            c.rect(180, 300, 260, 180, fill=1, stroke=1)
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica", 12)
        for idx, note in enumerate(sh.get("notes", [])):
            c.drawString(100, h - 100 - idx * 24, note)

        # Bottom-right title block
        c.setLineWidth(1)
        c.rect(w - 260, 30, 230, 110)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(w - 245, 95, sh["title"])
        c.setFont("Helvetica-Bold", 18)
        c.drawString(w - 245, 50, sh["number"])
        c.showPage()
    c.save()
    return path


def test_split_and_name_sheets(tmp_path):
    pdf = _make_set(tmp_path / "set.pdf", [
        {"number": "A-101", "title": "Floor plan"},
        {"number": "A-102", "title": "Roof plan"},
        {"number": "S2.03", "title": "Framing plan"},
    ])

    split_dir = tmp_path / "split"
    made = sheetsets.split_sheets(pdf, split_dir)
    names = sorted(p["output"].name for p in made)
    assert names == ["A-101 Floor plan.pdf", "A-102 Roof plan.pdf", "S2.03 Framing plan.pdf"]
    for item in made:
        assert len(PdfReader(str(item["output"])).pages) == 1

    named_pdf = tmp_path / "named.pdf"
    sheetsets.name_pages(pdf, named_pdf)
    assert filetools.page_labels(named_pdf) == ["A-101", "A-102", "S2.03"]


def test_link_sheets_between_detail_references(tmp_path):
    raw = _make_set(tmp_path / "set.pdf", [
        {"number": "A-101", "title": "Floor plan", "notes": ["DETAIL 2/A-102", "SEE S2.03"]},
        {"number": "A-102", "title": "Roof plan"},
        {"number": "S2.03", "title": "Framing plan"},
    ])
    named = tmp_path / "named.pdf"
    sheetsets.name_pages(raw, named)

    linked = tmp_path / "linked.pdf"
    res = sheetsets.link_sheets(named, linked)
    assert res["links"] == 2
    assert {(t["from_page"], t["to_page"], t["sheet"]) for t in res["targets"]} == {
        (1, 2, "A-102"),
        (1, 3, "S2.03"),
    }

    reader = PdfReader(str(linked))
    annots = [a.get_object() for a in reader.pages[0]["/Annots"]]
    assert len(annots) == 2
    dest_pages = set()
    for a in annots:
        assert a["/Subtype"] == "/Link"
        target_ref = a["/Dest"][0]
        for idx, pg in enumerate(reader.pages, 1):
            if pg.indirect_reference == target_ref:
                dest_pages.add(idx)
    assert dest_pages == {2, 3}


def test_compare_sets_matches_by_sheet_number(tmp_path):
    v1 = _make_set(tmp_path / "v1.pdf", [
        {"number": "A-101", "title": "Floor plan"},
        {"number": "A-102", "title": "Roof plan"},
        {"number": "S2.03", "title": "Framing plan"},
    ])
    # Reorder pages in v2, change A-102, remove S2.03, add E-101.
    v2 = _make_set(tmp_path / "v2.pdf", [
        {"number": "E-101", "title": "Electrical plan"},
        {"number": "A-102", "title": "Roof plan", "extra_box": True},
        {"number": "A-101", "title": "Floor plan"},
    ])

    diff_pdf = tmp_path / "diff.pdf"
    res = sheetsets.compare_sets(v1, v2, out_pdf=diff_pdf)
    assert [x["sheet"] for x in res["changed"]] == ["A-102"]
    assert [x["sheet"] for x in res["added"]] == ["E-101"]
    assert [x["sheet"] for x in res["removed"]] == ["S2.03"]
    assert [x["sheet"] for x in res["unchanged"]] == ["A-101"]
    assert diff_pdf.exists()
    assert len(PdfReader(str(diff_pdf)).pages) == 1


def test_sheet_set_cli_commands(tmp_path):
    v1 = _make_set(tmp_path / "cli_v1.pdf", [
        {"number": "A-101", "title": "Floor plan", "notes": ["SEE A-102"]},
        {"number": "A-102", "title": "Roof plan"},
    ])
    v2 = _make_set(tmp_path / "cli_v2.pdf", [
        {"number": "A-101", "title": "Floor plan", "notes": ["SEE A-102"]},
        {"number": "A-102", "title": "Roof plan", "extra_box": True},
        {"number": "A-103", "title": "Elevations"},
    ])
    assert cli.main(["sheet-split", str(v1), "-o", str(tmp_path / "out_split")]) == 0
    assert (tmp_path / "out_split" / "A-101 Floor plan.pdf").exists()

    named = tmp_path / "cli_named.pdf"
    assert cli.main(["sheet-name", str(v1), "-o", str(named)]) == 0
    assert filetools.page_labels(named) == ["A-101", "A-102"]

    linked = tmp_path / "cli_linked.pdf"
    assert cli.main(["sheet-link", str(named), "-o", str(linked)]) == 0
    assert len(PdfReader(str(linked)).pages[0]["/Annots"]) == 1

    diff_out = tmp_path / "cli_diff.pdf"
    assert cli.main(["sheet-compare", str(v1), str(v2), "-o", str(diff_out)]) == 0
    assert diff_out.exists()
