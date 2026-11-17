# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.39.0 (risks.py): what a PDF from someone else can do, said in plain
words, and Make safe, which takes exactly those parts out and keeps the
rest (pages, text, forms and their totals, comments, harmless attachments)."""

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DictionaryObject, FloatObject, NameObject,
                           TextStringObject)
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from pdfmanager import core, forms, risks


def plain(path, words=("An ordinary page",)):
    c = canvas.Canvas(str(path), pagesize=A4)
    for i, line in enumerate(words):
        c.drawString(72, 760 - 20 * i, line)
    c.showPage()
    c.save()
    return path


def action(kind, **keys):
    out = DictionaryObject({NameObject("/S"): NameObject(kind)})
    for key, value in keys.items():
        out[NameObject("/" + key)] = value if not isinstance(value, str) else TextStringObject(value)
    return out


def link(writer, rect, act):
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Link"),
        NameObject("/Rect"): ArrayObject(FloatObject(v) for v in rect), NameObject("/A"): act})
    page = writer.pages[0]
    if "/Annots" not in page:
        page[NameObject("/Annots")] = ArrayObject()
    page["/Annots"].append(writer._add_object(annot))


def add_js(writer, script):
    """A script kept with the document, which viewers run when it opens."""
    tree = DictionaryObject({NameObject("/Names"): ArrayObject([TextStringObject("s1"), action("/JavaScript", JS=script)])})
    writer._root_object[NameObject("/Names")] = DictionaryObject({NameObject("/JavaScript"): tree})


def kinds(found):
    return {r["kind"]: r for r in found["risks"]}


def test_an_ordinary_file_has_nothing_to_report(tmp_path):
    found = risks.check(plain(tmp_path / "plain.pdf"))
    assert found == {"risks": [], "level": "none"}


def test_scripts_and_programs(tmp_path):
    w = PdfWriter(clone_from=str(plain(tmp_path / "p.pdf")))
    add_js(w, "app.alert('hello');")                                          # runs when the file opens
    link(w, [72, 700, 200, 720], action("/Launch", F="cmd.exe"))
    w.pages[0][NameObject("/AA")] = DictionaryObject({NameObject("/O"): action("/JavaScript", JS="x()")})
    src = tmp_path / "scripted.pdf"
    w.write(str(src))
    found = risks.check(src)
    got = kinds(found)
    assert found["level"] == "high"
    assert got["scripts"]["severity"] == "high" and got["scripts"]["count"] == 2
    assert got["scripts"]["detail"].startswith("2 scripts that could run") and "never runs them" in got["scripts"]["detail"]
    assert got["launch"]["severity"] == "high" and "cmd.exe" in " ".join(got["launch"]["examples"])
    assert found["risks"][0]["severity"] == "high"                           # the worst first

    out = tmp_path / "safe.pdf"
    removed = risks.make_safe(src, out)
    assert removed["scripts"] == 2 and removed["launch"] == 1
    assert risks.check(out) == {"risks": [], "level": "none"}
    assert "ordinary page" in PdfReader(str(out)).pages[0].extract_text()


def test_a_forms_own_totals_and_formats_are_not_scripts_to_fear(tmp_path):
    src = tmp_path / "form.pdf"
    forms.add_fields(plain(tmp_path / "p.pdf"), src, [
        {"page": 1, "rect": [72, 700, 200, 720], "type": "text", "name": "Price", "format": "amount"},
        {"page": 1, "rect": [72, 670, 200, 690], "type": "text", "name": "Total", "calculate": "sum: Price"}])
    assert risks.check(src)["level"] == "none"
    # ... but anything else written into a field is
    w = PdfWriter(clone_from=str(src))
    field = w.pages[0]["/Annots"][0].get_object()
    field["/AA"][NameObject("/K")] = action("/JavaScript", JS="app.launchURL('http://x.example');")
    w.write(str(tmp_path / "odd.pdf"))
    assert kinds(risks.check(tmp_path / "odd.pdf"))["scripts"]["count"] == 1
    out = tmp_path / "safe.pdf"
    risks.make_safe(tmp_path / "odd.pdf", out)
    kept = PdfReader(str(out)).pages[0]["/Annots"][1].get_object()
    assert "AFSimple_Calculate" in kept["/AA"]["/C"]["/JS"]                  # the total still adds up
    first = PdfReader(str(out)).pages[0]["/Annots"][0].get_object()
    assert "/K" not in first["/AA"] and "AFNumber_Format" in first["/AA"]["/F"]["/JS"]


def test_forms_that_send_what_you_type_and_links_to_other_files(tmp_path):
    w = PdfWriter(clone_from=str(plain(tmp_path / "p.pdf")))
    link(w, [72, 700, 200, 720], action("/SubmitForm", F=DictionaryObject({
        NameObject("/FS"): NameObject("/URL"), NameObject("/F"): TextStringObject("https://collect.example/form")})))
    link(w, [72, 660, 200, 680], action("/GoToR", F="other.pdf"))
    link(w, [72, 620, 200, 640], action("/ImportData", F="data.fdf"))
    src = tmp_path / "sends.pdf"
    w.write(str(src))
    found = risks.check(src)
    got = kinds(found)
    assert found["level"] == "medium"
    assert got["submit"]["severity"] == "medium" and "collect.example" in " ".join(got["submit"]["examples"])
    assert got["remote"]["severity"] == "low" and got["remote"]["count"] == 2
    out = tmp_path / "safe.pdf"
    removed = risks.make_safe(src, out)
    assert removed["submit"] == 1 and removed["remote"] == 2
    assert risks.check(out)["level"] == "none"
    assert len(PdfReader(str(out)).pages[0]["/Annots"]) == 3                 # the link boxes stay, doing nothing


def test_attachments_that_can_run(tmp_path):
    w = PdfWriter(clone_from=str(plain(tmp_path / "p.pdf")))
    w.add_attachment("invoice.exe", b"MZ not really")
    w.add_attachment("macro.docm", b"PK")
    w.add_attachment("notes.txt", b"harmless")
    src = tmp_path / "attached.pdf"
    w.write(str(src))
    found = risks.check(src)
    got = kinds(found)
    assert got["attachments"]["severity"] == "high" and got["attachments"]["count"] == 2
    assert got["attachments"]["examples"] == ["invoice.exe", "macro.docm"]
    assert "other_attachments" not in got                                    # a text file is nothing to warn about
    out = tmp_path / "safe.pdf"
    assert risks.make_safe(src, out)["attachments"] == 2
    names = {a["name"] for a in __import__("pdfmanager.filetools", fromlist=["x"]).attachments(out)}
    assert names == {"notes.txt"}
    assert risks.runnable("Report.PDF.exe") and risks.runnable("a.JS") and not risks.runnable("photo.jpg")


def test_a_link_that_shows_one_address_and_goes_to_another(tmp_path):
    src = plain(tmp_path / "p.pdf", ["Sign in at www.mybank.com to confirm", "Our site: example.org/help", "Click here"])
    w = PdfWriter(clone_from=str(src))
    link(w, [100, 755, 330, 772], action("/URI", URI="http://mybank.com.evil-site.net/login"))   # line 1: a lie
    link(w, [72, 735, 330, 752], action("/URI", URI="https://www.example.org/help"))             # line 2: honest
    link(w, [72, 715, 330, 732], action("/URI", URI="https://anything.example/"))                # no address shown
    w.write(str(tmp_path / "phish.pdf"))
    found = risks.check(tmp_path / "phish.pdf")
    got = kinds(found)
    assert got["hidden_links"]["count"] == 1 and got["hidden_links"]["severity"] == "medium"
    example = got["hidden_links"]["examples"][0]
    assert example == "shows www.mybank.com, goes to mybank.com.evil-site.net"
    assert got["hidden_links"]["detail"].startswith("1 link that shows one web address and goes to another")
    # nothing to take out: the reader shows the real address before opening any link
    out = tmp_path / "safe.pdf"
    removed = risks.make_safe(tmp_path / "phish.pdf", out)
    assert sum(removed.values()) == 0 and kinds(risks.check(out))["hidden_links"]["count"] == 1


def test_a_site_opened_when_the_file_opens(tmp_path):
    w = PdfWriter(clone_from=str(plain(tmp_path / "p.pdf")))
    w._root_object[NameObject("/OpenAction")] = action("/URI", URI="http://track.example/opened")
    src = tmp_path / "opens.pdf"
    w.write(str(src))
    got = kinds(risks.check(src))
    assert got["opens_site"]["severity"] == "medium" and "track.example" in got["opens_site"]["examples"][0]
    out = tmp_path / "safe.pdf"
    assert risks.make_safe(src, out)["opens_site"] == 1
    assert "/OpenAction" not in PdfReader(str(out)).trailer["/Root"]


def test_a_script_chained_behind_a_harmless_action(tmp_path):
    w = PdfWriter(clone_from=str(plain(tmp_path / "p.pdf")))
    first = action("/URI", URI="https://example.org/")
    first[NameObject("/Next")] = action("/JavaScript", JS="steal()")
    link(w, [72, 700, 200, 720], first)
    src = tmp_path / "chained.pdf"
    w.write(str(src))
    assert kinds(risks.check(src))["scripts"]["count"] == 1
    out = tmp_path / "safe.pdf"
    risks.make_safe(src, out)
    kept = PdfReader(str(out)).pages[0]["/Annots"][0].get_object()["/A"]
    assert kept["/S"] == "/URI" and "/Next" not in kept                      # the link itself still works


def test_locked_and_broken_files(tmp_path):
    w = PdfWriter(clone_from=str(plain(tmp_path / "p.pdf")))
    add_js(w, "x()")
    w.encrypt("secret")
    w.write(str(tmp_path / "locked.pdf"))
    assert risks.check(tmp_path / "locked.pdf", "secret")["level"] == "high"
    with pytest.raises(core.PdfManagerError):
        risks.check(tmp_path / "locked.pdf")
    (tmp_path / "junk.pdf").write_bytes(b"not a pdf")
    with pytest.raises(core.PdfManagerError):
        risks.check(tmp_path / "junk.pdf")


def test_nothing_to_make_safe_is_said(tmp_path):
    src = plain(tmp_path / "p.pdf")
    removed = risks.make_safe(src, tmp_path / "out.pdf")
    assert sum(removed.values()) == 0 and (tmp_path / "out.pdf").exists()
    assert risks.summary(removed) == "nothing: it had nothing that could do harm"
    assert risks.summary({"scripts": 2, "launch": 1, "submit": 0, "remote": 0, "attachments": 1, "opens_site": 0}) \
        == "2 scripts, 1 action that runs a program, 1 attachment that could run"


# -- in the app and on the command line ---------------------------------------------------------

def scripted(tmp_path):
    w = PdfWriter(clone_from=str(plain(tmp_path / "p.pdf")))
    add_js(w, "app.alert('hello');")
    w.add_attachment("invoice.exe", b"MZ not really")
    w.write(str(tmp_path / "scripted.pdf"))
    return tmp_path / "scripted.pdf"


def test_the_file_panel_is_told(tmp_path):
    from pdfmanager import filefacts

    found = filefacts.facts(scripted(tmp_path))
    assert found["risk"] == "high" and [r["kind"] for r in found["risks"]] == ["scripts", "attachments"]
    first = filefacts.suggestions(found)[0]
    assert first["op"] == "makesafe" and "scripts" in first["why"]
    assert all(s["op"] != "makesafe" for s in filefacts.suggestions(found, made_by="makesafe"))
    calm = filefacts.facts(plain(tmp_path / "calm.pdf"))
    assert calm["risk"] == "none" and all(s["op"] != "makesafe" for s in filefacts.suggestions(calm))


def test_the_make_safe_tool(tmp_path):
    from pdfmanager import gui

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    notes = []
    (result,) = gui._run_file_op("makesafe", scripted(tmp_path), out_dir, {}, None, [], notes)
    assert result.name == "scripted_safe.pdf" and risks.check(result)["level"] == "none"
    assert notes == ["scripted.pdf: took out 1 script, 1 attachment that could run"]
    assert any(op["id"] == "makesafe" and op["group"] == "Security" for op in gui.OPERATIONS)


def test_saving_attachments_warns_about_the_ones_that_run(tmp_path):
    from pdfmanager import gui

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    notes = []
    gui._run_file_op("media", scripted(tmp_path), out_dir, {"images": "", "attachments": "on"}, None, [], notes)
    assert any("invoice.exe can run" in n and "only if you trust" in n for n in notes)


def test_command_line(tmp_path, capsys):
    from pdfmanager.cli import main

    src = scripted(tmp_path)
    assert main(["make-safe", str(src), "--check"]) == 2          # 2: something that could do harm
    said = capsys.readouterr().out
    assert "[high] It has scripts" in said and "invoice.exe" in said
    out = tmp_path / "cli.pdf"
    assert main(["make-safe", str(src), "-o", str(out)]) == 0
    assert "took out 1 script, 1 attachment that could run" in capsys.readouterr().out
    assert main(["make-safe", str(out), "--check"]) == 0
    assert "nothing that could do harm" in capsys.readouterr().out


def test_a_script_made_to_stall_the_form_test_does_not(tmp_path):
    import time

    for script in ("AFa()" + " AFa()" * 60 + "!", "AFa(" + "(" * 500, "AFa()" + " " * 1500 + "x"):
        began = time.perf_counter()
        assert not (len(script) <= risks.FORM_SCRIPT_MAX and risks.FORM_SCRIPT.fullmatch(script))
        assert time.perf_counter() - began < 0.5
    for fine in ('AFNumber_Format(2, 0, 0, 0, "", true);', 'AFSimple_Calculate("SUM", new Array("a", "b"));',
                 "AFDate_FormatEx(\"dd/mm/yyyy\"); AFRange_Validate(true, 0, false, 0)"):
        assert risks.FORM_SCRIPT.fullmatch(fine)


CRAFTED = ("AFNumber_Keystroke(2, app.alert('crafted-K'))", "AFNumber_Format(2, app.alert('crafted-F'))",
           "AFSimple_Calculate(app.alert('crafted-Bl'))", 'AFNumber_Format(2, "a" + app.alert(1))',
           'AFSimple_Calculate("SUM", new Array(app.alert(1)))', "AFNumber_Format(2); app.alert(1)",
           "AFNumber_Format(`${app.alert(1)}`)", 'AFNumber_Format("a\\\\") + app.alert(1) //")',
           "AFMadeUp(1)", "AFNumber_Format(2, [app.alert(1)])", "AFNumber_Format(2, x)", "AFNumber_Format(2)\napp.alert(1)")


def test_a_script_dressed_as_a_form_function_is_a_script(tmp_path):
    """Only the form functions by name, with plain values: nothing hides in an argument."""
    for script in CRAFTED:
        assert not risks.FORM_SCRIPT.fullmatch(script), script
    for fine in ('AFNumber_Format(2, 0, 0, 0, "", true);', 'AFNumber_Keystroke(2, 0, 0, 0, "€", true);',
                 'AFSimple_Calculate("SUM", new Array("Price", "Tax"));', "AFSimple_Calculate('AVG', new Array('a'))",
                 'AFDate_FormatEx("dd/mm/yyyy");', "AFPercent_Format(1, 0);", "AFSpecial_Format(2);",
                 "AFRange_Validate(true, -5, false, 1e+06);", 'AFSimple_Calculate("SUM", new Array("He said \\"hi\\""));'):
        assert risks.FORM_SCRIPT.fullmatch(fine), fine
    # in a file: reported, and taken out by Make safe, while a real total stays
    src = tmp_path / "form.pdf"
    forms.add_fields(plain(tmp_path / "p.pdf"), src, [
        {"page": 1, "rect": [72, 700, 200, 720], "type": "text", "name": "Price", "format": "amount"},
        {"page": 1, "rect": [72, 670, 200, 690], "type": "text", "name": "Total", "calculate": "sum: Price"},
        {"page": 1, "rect": [72, 640, 200, 660], "type": "text", "name": "Other"}])
    w = PdfWriter(clone_from=str(src))
    other = w.pages[0]["/Annots"][2].get_object()
    other[NameObject("/AA")] = DictionaryObject({
        NameObject("/K"): action("/JavaScript", JS=CRAFTED[0]), NameObject("/F"): action("/JavaScript", JS=CRAFTED[1]),
        NameObject("/Bl"): action("/JavaScript", JS=CRAFTED[2])})
    crafted = tmp_path / "crafted.pdf"
    w.write(str(crafted))
    found = risks.check(crafted)
    assert found["level"] == "high" and kinds(found)["scripts"]["count"] == 3
    out = tmp_path / "safe.pdf"
    assert risks.make_safe(crafted, out)["scripts"] == 3 and risks.check(out)["level"] == "none"
    annots = PdfReader(str(out)).pages[0]["/Annots"]
    assert "/AA" not in annots[2].get_object() or not annots[2].get_object()["/AA"]
    assert "AFSimple_Calculate" in annots[1].get_object()["/AA"]["/C"]["/JS"]


def test_the_reader_runs_exactly_what_the_check_calls_harmless():
    """reader.js's pattern for the scripts its sandbox may run is this module's."""
    import json
    from pathlib import Path

    reader = (Path(risks.__file__).parent / "static" / "reader.js").read_text(encoding="utf-8")
    assert "const FORM_SCRIPT = new RegExp(" + json.dumps("^" + risks.FORM_SCRIPT_SOURCE + "$") + ");" in reader
    assert "js.length <= FORM_SCRIPT_MAX && FORM_SCRIPT.test(js)" in reader and "const FORM_SCRIPT_MAX = 2000;" in reader


def test_the_check_and_the_reader_read_the_pattern_alike():
    """No \\s or \\d, which Python and JavaScript take differently: an odd
    digit, an odd space or a byte-order mark is refused by both."""
    assert "\\s" not in risks.FORM_SCRIPT_SOURCE and "\\d" not in risks.FORM_SCRIPT_SOURCE
    for odd in ("AFNumber_Format(٢)", "AFNumber_Format(２)", "AFNumber_Format(2)\x1c",
                "﻿AFNumber_Format(2)", "AFNumber_Format(2) ", "AFNumber_Format( 2)"):
        assert not risks.FORM_SCRIPT.fullmatch(odd), repr(odd)
    assert risks.FORM_SCRIPT.fullmatch("AFNumber_Format( 2 ,\t0 )\r\n;\n")


def test_a_byte_order_mark_before_a_form_function_is_read_as_the_reader_reads_it(tmp_path):
    """pdf.js drops a mark at the start of a script; so does the check, and both call it a form function."""
    src = tmp_path / "form.pdf"
    forms.add_fields(plain(tmp_path / "p.pdf"), src, [
        {"page": 1, "rect": [72, 700, 200, 720], "type": "text", "name": "Other"}])
    w = PdfWriter(clone_from=str(src))
    field = w.pages[0]["/Annots"][0].get_object()
    field[NameObject("/AA")] = DictionaryObject({
        NameObject("/F"): action("/JavaScript", JS='﻿AFNumber_Format(2, 0, 0, 0, "X", true)'),
        NameObject("/K"): action("/JavaScript", JS='﻿﻿AFNumber_Keystroke(2, 0, 0, 0, "X", true)')})
    w.write(str(tmp_path / "bom.pdf"))
    assert risks.check(tmp_path / "bom.pdf")["level"] == "none"
    # ... and a mark doesn't dress a script up as one
    field["/AA"][NameObject("/F")] = action("/JavaScript", JS="﻿app.alert(1)")
    w.write(str(tmp_path / "bad.pdf"))
    assert kinds(risks.check(tmp_path / "bad.pdf"))["scripts"]["count"] == 1


# -- signed files -------------------------------------------------------------------------------

def signed_file(tmp_path, with_open_action):
    pytest.importorskip("pyhanko")
    from pdfmanager import certsign
    from test_certsign import PFX_PASSWORD, make_id

    base = plain(tmp_path / "contract.pdf", ["A contract"])
    if with_open_action:
        w = PdfWriter(clone_from=str(base))
        w._root_object[NameObject("/OpenAction")] = action("/URI", URI="http://track.example/opened")
        w.write(str(base))
    out = tmp_path / "contract-signed.pdf"
    certsign.sign(base, out, make_id(tmp_path / "jane.pfx")[0], PFX_PASSWORD, page=1, box=[50, 50, 250, 110])
    return out


def intact(path):
    from pdfmanager import certsign

    return [s["intact"] for s in certsign.verify(path)]


def test_a_signed_file_with_nothing_to_take_out_keeps_its_signature(tmp_path):
    src = signed_file(tmp_path, with_open_action=False)
    assert risks.signed(src) and not risks.signed(plain(tmp_path / "p.pdf"))
    out = tmp_path / "safe.pdf"
    removed = risks.make_safe(src, out)
    assert sum(removed.values()) == 0
    assert out.read_bytes() == src.read_bytes() and intact(out) == [True]   # byte for byte: still verifies


def test_making_a_signed_file_safe_says_what_happens_to_the_signature(tmp_path):
    from pdfmanager import filefacts, gui

    src = signed_file(tmp_path, with_open_action=True)
    before = src.read_bytes()
    why = filefacts.suggestions(filefacts.facts(src))[0]
    assert why["op"] == "makesafe" and "signed" in why["why"]            # said before it's run
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    notes = []
    (result,) = gui._run_file_op("makesafe", src, out_dir, {}, None, [], notes)
    assert risks.SIGNED_NOTE in notes
    assert result != src and src.read_bytes() == before and intact(src) == [True]   # the original still verifies
    assert intact(result) == [False] and risks.check(result)["level"] == "none"     # the copy is safe, not signed
    # an unsigned file gets no such note
    notes = []
    plain_risky = PdfWriter(clone_from=str(plain(tmp_path / "q.pdf")))
    plain_risky._root_object[NameObject("/OpenAction")] = action("/URI", URI="http://track.example/x")
    plain_risky.write(str(tmp_path / "risky.pdf"))
    gui._run_file_op("makesafe", tmp_path / "risky.pdf", out_dir, {}, None, [], notes)
    assert risks.SIGNED_NOTE not in notes


def test_reading_a_links_words_holds_the_pdfium_lock(tmp_path, monkeypatch):
    """pdfium is not thread-safe. Looking at a link's words without the lock,
    while the server did something else with pdfium, ended the whole app
    (seen as an abort in a browser test of 0.52.0)."""
    import pypdfium2 as pdfium

    src = tmp_path / "link.pdf"
    c = canvas.Canvas(str(src), pagesize=A4)
    c.drawString(100, 700, "www.example.com")
    c.save()
    writer = PdfWriter(clone_from=str(src))
    link(writer, [95, 690, 260, 715], DictionaryObject({NameObject("/S"): NameObject("/URI"),
                                                         NameObject("/URI"): TextStringObject("https://other.example.org/")}))
    writer.write(str(src))
    held, real = [], pdfium.PdfDocument

    def opened(*args, **kwargs):
        held.append(core.PDFIUM_LOCK.locked())
        return real(*args, **kwargs)

    monkeypatch.setattr(pdfium, "PdfDocument", opened)
    assert risks.check(src)["risks"]
    assert held == [True] and not core.PDFIUM_LOCK.locked()
