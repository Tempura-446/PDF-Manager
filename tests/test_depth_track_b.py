# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Track B depth tests: compare modes (both/text/visual), structured compare_summary, and JSON/CLI/GUI wiring."""

import json
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pdfmanager import cli, compare
from pdfmanager.server import operations


def _make_pdf(path: Path, pages: list[tuple[str, tuple[float, float] | None]]) -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    for text, pos in pages:
        x, y = pos or (72, 720)
        c.setFont("Helvetica", 12)
        c.drawString(x, y, text)
        c.showPage()
    c.save()
    return path


def test_compare_modes_text_vs_visual_vs_both(tmp_path):
    # Same text on page 1, shifted vertically (visual change only, no text change);
    # Text changed on page 2; page 3 added in new.
    old_pdf = _make_pdf(tmp_path / "old.pdf", [
        ("Identical paragraph text.", (72, 720)),
        ("Original clause value is 100.", (72, 720)),
    ])
    new_pdf = _make_pdf(tmp_path / "new.pdf", [
        ("Identical paragraph text.", (72, 540)),
        ("Original clause value is 250.", (72, 720)),
        ("Brand new appendix page.", (72, 720)),
    ])

    # Text-only mode ignores pure layout shift on page 1
    s_text = compare.compare_summary(old_pdf, new_pdf, all_pages=True, mode="text")
    assert s_text["mode"] == "text"
    assert s_text["counts"]["added"] == 1
    assert s_text["counts"]["changed"] == 1
    assert s_text["counts"]["same"] == 1

    # Visual-only mode detects layout shift on page 1 AND visual diff on page 2, without word lists
    s_vis = compare.compare_summary(old_pdf, new_pdf, all_pages=True, mode="visual")
    assert s_vis["counts"]["changed"] == 2
    assert all(r["removed"] == [] and r["added"] == [] for r in s_vis["changes"])

    # Default 'both' mode catches all 3 differing pages and formats a clear summary
    s_both = compare.compare_summary(old_pdf, new_pdf, all_pages=False, mode="both")
    assert s_both["counts"]["changed"] == 2
    assert s_both["counts"]["added"] == 1
    msg = compare.format_summary(s_both)
    assert "3 pages with differences" in msg
    assert "2 changed" in msg
    assert "1 added" in msg


def test_cli_compare_json_and_gui_fields(tmp_path, capsys):
    old_pdf = _make_pdf(tmp_path / "a.pdf", [("Alpha line", None)])
    new_pdf = _make_pdf(tmp_path / "b.pdf", [("Beta line", None)])

    rc = cli.main(["compare", str(old_pdf), str(new_pdf), "--mode", "text", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "text"
    assert payload["counts"]["changed"] == 1
    assert payload["changes"][0]["label"]

    comp_spec = operations.OPS["compare"]
    keys = [f["key"] for f in comp_spec["fields"]]
    assert "mode" in keys
    assert "export_json" in keys
