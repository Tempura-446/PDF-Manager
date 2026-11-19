# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Priority #6 Phase 1: Smart auto-rename & filing rules engine and CLI."""
import json
from pathlib import Path

from reportlab.pdfgen import canvas

from pdfmanager import cli, filing


def _text_pdf(path, lines, title=None, author=None):
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    if title:
        c.setTitle(title)
    if author:
        c.setAuthor(author)
    y = 720
    for ln in lines:
        c.drawString(72, y, ln)
        y -= 18
    c.showPage()
    c.save()
    return path


def test_date_extraction_and_normalization():
    assert filing.extract_date("Invoice Date: 2026-11-18") == "2026-11-18"
    assert filing.extract_date("Issued on Nov 18, 2026 for services") == "2026-11-18"
    assert filing.extract_date("Date: 18 November 2026") == "2026-11-18"
    assert filing.extract_date("Rechnung vom 18.11.2026") == "2026-11-18"
    assert filing.extract_date("Date: 11/18/2026") == "2026-11-18"
    assert filing.extract_date("No calendar date here") == ""


def test_sanitize_and_template_rendering():
    assert filing.sanitize_part('Acme: Corp / "Q4" <2026>?*') == "Acme Corp Q4 2026"
    assert filing.sanitize_part("CON") == "_CON"
    rel = filing.render_template(
        "{year}/{date}_{vendor}_{invoice_no}.pdf",
        {"year": "2026", "date": "2026-11-18", "vendor": "Acme: Labs", "invoice_no": "INV/1042"},
    )
    assert rel == Path("2026") / "2026-11-18_Acme Labs_INV 1042.pdf"


def test_named_regex_captures_collision_avoidance_apply_and_undo(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    f1 = _text_pdf(inbox / "scan1.pdf", [
        "ACME CORP",
        "Invoice # INV-1042",
        "Date: Nov 18, 2026",
    ])
    f2 = _text_pdf(inbox / "scan2.pdf", [
        "ACME CORP",
        "Invoice # INV-1042",
        "Date: 2026-11-18",
    ])
    unmatched = _text_pdf(inbox / "notes.pdf", ["Random meeting notes without date"])

    rules = [
        {
            "name": "Acme Invoices",
            "conditions": {
                "contains": "ACME",
                "regex": r"Invoice\s*#\s*(?P<invoice_no>[A-Z0-9-]+)",
                "has_date": True,
            },
            "template": "{year}/{date}_Acme_{invoice_no}.pdf",
        }
    ]

    # Dry-run plan must not touch disk
    plan = filing.plan_filing([f1, f2, unmatched], rules, base_dir=tmp_path / "filed")
    assert len(plan) == 3
    assert plan[0]["matched"] is True
    assert Path(plan[0]["dst"]).name == "2026-11-18_Acme_INV-1042.pdf"
    assert plan[1]["matched"] is True
    assert Path(plan[1]["dst"]).name == "2026-11-18_Acme_INV-1042-2.pdf"  # collision suffix
    assert plan[2]["matched"] is False
    assert f1.exists() and f2.exists() and not (tmp_path / "filed").exists()

    # Apply moves files and writes undo manifest
    undo_log = tmp_path / "undo.json"
    applied = filing.apply_filing(plan, undo_path=undo_log)
    assert applied["moved"] == 2
    assert not f1.exists() and not f2.exists()
    assert Path(plan[0]["dst"]).is_file()
    assert Path(plan[1]["dst"]).is_file()
    assert unmatched.is_file()

    # Undo restores original paths cleanly
    restored = filing.undo_filing(undo_path=undo_log)
    assert restored["restored"] == 2
    assert f1.is_file() and f2.is_file()
    assert not Path(plan[0]["dst"]).exists()
    assert not Path(plan[1]["dst"]).exists()


def test_cli_auto_file_dry_run_apply_and_undo(tmp_path, capsys):
    inbox = tmp_path / "scans"
    inbox.mkdir()
    src = _text_pdf(inbox / "raw.pdf", ["Invoice No: INV-77", "Date: 2026-11-19"])
    rules_file = tmp_path / "rules.json"
    filing.save_rules(rules_file, filing.STARTER_RULES)
    undo_log = tmp_path / "undo.json"

    # Dry-run JSON
    rc = cli.main(["auto-file", str(inbox), "--rules", str(rules_file), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    assert out["plan"][0]["matched"] is True
    assert src.is_file()

    # Apply
    rc = cli.main(["auto-file", str(inbox), "--rules", str(rules_file), "--apply", "--undo-log", str(undo_log), "--json"])
    assert rc == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["moved"] == 1
    assert not src.exists()

    # Undo
    rc = cli.main(["auto-file", "--undo", "--undo-log", str(undo_log), "--json"])
    assert rc == 0
    undone = json.loads(capsys.readouterr().out)
    assert undone["restored"] == 1
    assert src.is_file()
