"""Stamps, comment summaries and XFDF, and web pages to PDF (0.13.0)."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from pdfmanager import comments, core, gui, markup, office
from pdfmanager.cli import main
from test_more import app, call, multipart  # noqa: F401  (app is a fixture)


def two_pages(path, turn_second=False):
    c = canvas.Canvas(str(path))
    c.drawString(72, 700, "Hello world")
    c.showPage()
    c.drawString(72, 700, "Second page")
    c.showPage()
    c.save()
    if turn_second:
        w = PdfWriter(clone_from=path)
        w.pages[1].rotate(90)
        w.write(path)
    return path


def commented(tmp_path):
    src = two_pages(tmp_path / "a.pdf", turn_second=True)
    out = tmp_path / "commented.pdf"
    markup.apply(src, out, [
        {"op": "add", "page": 1, "kind": "note", "at": [300, 500], "text": "Check this"},
        {"op": "add", "page": 1, "kind": "strikeout", "rects": [[70, 696, 105, 710]], "text": "drop it"},
        {"op": "add", "page": 1, "kind": "cloud", "rect": [300, 200, 400, 260], "text": "fix"},
        {"op": "add", "page": 1, "kind": "arrow", "points": [[100, 100], [200, 150]]},
        {"op": "add", "page": 1, "kind": "stamp", "rect": [300, 600, 460, 650], "label": "Approved",
         "dated": True},
        {"op": "add", "page": 2, "kind": "stamp", "rect": [300, 400, 350, 560], "label": "draft"},
    ], author="Ada")
    return out


# --------------------------------------------------------------------------
# Stamps
# --------------------------------------------------------------------------

def test_stamps(tmp_path):
    out = commented(tmp_path)
    reader = PdfReader(out)
    stamps = [a.get_object() for p in reader.pages for a in p.get("/Annots") or []
              if a.get_object()["/Subtype"] == "/Stamp"]
    assert [s["/Name"] for s in stamps] == ["/Approved", "/Draft"]
    approved = stamps[0]
    data = approved["/AP"]["/N"].get_data().decode("latin-1")
    assert "(APPROVED) Tj" in data and "(by Ada, " in data and "/HelvB" in data
    assert [round(c, 2) for c in approved["/C"]] == [0.12, 0.54, 0.23]  # green
    # On the turned page the drawing is turned to read upright.
    assert "0 1 -1 0 350 400 cm" in stamps[1]["/AP"]["/N"].get_data().decode("latin-1")
    with pytest.raises(core.PdfManagerError, match="needs words"):
        markup.apply(out, tmp_path / "x.pdf", [{"op": "add", "page": 1, "kind": "stamp",
                                                "rect": [0, 0, 100, 40], "label": " "}])
    with pytest.raises(core.PdfManagerError, match="bigger"):
        markup.apply(out, tmp_path / "x.pdf", [{"op": "add", "page": 1, "kind": "stamp",
                                                "rect": [0, 0, 5, 5], "label": "Paid"}])


# --------------------------------------------------------------------------
# Comment lists, summaries and XFDF
# --------------------------------------------------------------------------

def test_collect_and_summaries(tmp_path):
    found = comments.collect(commented(tmp_path))
    kinds = [(c["page"], c["kind"]) for c in found]
    assert kinds == [(1, "Strike-out"), (1, "Stamp"), (1, "Note"), (1, "Cloud"), (1, "Arrow"),
                     (2, "Stamp")]
    strike = found[0]
    assert strike["marked"].startswith("Hello") and strike["text"] == "drop it"
    assert strike["author"] == "Ada"
    assert found[1]["text"] == "Approved"
    assert comments.count(found) == 6

    comments.summary_pdf(found, tmp_path / "sum.pdf", "a.pdf")
    text = "".join(p.extract_text() for p in PdfReader(tmp_path / "sum.pdf").pages)
    assert "Comments in a.pdf" in text and "6 comments" in text and "Page 2" in text
    assert "“Hello" in text and "Check this" in text
    comments.to_csv(found + [{"page": 3, "kind": "Note", "author": "=cmd", "date": "", "text": "+1",
                              "marked": "", "replies": []}], tmp_path / "c.csv")
    rows = (tmp_path / "c.csv").read_text(encoding="utf-8-sig").splitlines()
    assert rows[0] == "Page,Type,Author,Date,Marked text,Comment,Reply to"
    assert rows[-1].startswith("3,Note,'=cmd,,,'+1")  # not formulas


def test_replies_are_kept_together(tmp_path):
    src = commented(tmp_path)
    note = next(c for c in PdfReader(src).pages[0]["/Annots"] if c.get_object()["/Subtype"] == "/Text")
    markup.apply(src, tmp_path / "r.pdf", [{"op": "reply", "page": 1, "id": f"{note.idnum}R",
                                            "text": "Done"}], author="Bob")
    found = comments.collect(tmp_path / "r.pdf")
    noted = next(c for c in found if c["kind"] == "Note")
    assert [(r["author"], r["text"]) for r in noted["replies"]] == [("Bob", "Done")]
    assert comments.count(found) == 7


def test_xfdf_round_trip(tmp_path):
    src = commented(tmp_path)
    n = comments.export_xfdf(src, tmp_path / "c.xfdf")
    assert n == 6
    root = ET.parse(tmp_path / "c.xfdf").getroot()
    assert root.tag == "{http://ns.adobe.com/xfdf/}xfdf"
    tags = [el.tag.split("}")[1] for el in root.find("{http://ns.adobe.com/xfdf/}annots")]
    assert sorted(tags) == ["line", "polygon", "stamp", "stamp", "strikeout", "text"]
    # Into a clean copy: the same comments, with their looks.
    blank = two_pages(tmp_path / "blank.pdf", turn_second=True)
    added, skipped = comments.import_xfdf(blank, tmp_path / "c.xfdf", tmp_path / "in.pdf")
    assert (added, skipped) == (6, [])
    again = comments.collect(tmp_path / "in.pdf")
    assert [(c["page"], c["kind"], c["text"]) for c in again] == [
        (c["page"], c["kind"], c["text"]) for c in comments.collect(src)]
    for page in PdfReader(tmp_path / "in.pdf").pages:
        for a in page["/Annots"]:
            assert "/N" in a.get_object()["/AP"]  # every one has its own look
    # Importing into a document that already has them adds nothing.
    assert comments.import_xfdf(src, tmp_path / "c.xfdf", tmp_path / "same.pdf") == (0, [])


def test_xfdf_from_acrobat_like_file(tmp_path):
    blank = two_pages(tmp_path / "blank.pdf")
    (tmp_path / "acro.xfdf").write_text("""<?xml version="1.0" encoding="UTF-8"?>
<xfdf xmlns="http://ns.adobe.com/xfdf/" xml:space="preserve"><annots>
<highlight color="#FFFF00" date="D:20260101120000Z" flags="print" name="h1" page="0"
  rect="70,695,140,712" title="Kim" coords="70,712,140,712,70,695,140,695" opacity="0.5"><contents>key</contents></highlight>
<text color="#FFFF00" flags="print" name="t1" page="0" rect="300,500,320,520" title="Kim" icon="Comment"><contents>Question?</contents></text>
<text flags="print" name="t2" page="0" rect="300,500,320,520" title="Lee" inreplyto="t1"><contents>Answer</contents></text>
<ink color="#FF0000" flags="print" name="i1" page="1" rect="10,10,100,100" width="2"><inklist><gesture>10,10;50,60;100,100</gesture></inklist></ink>
<freetext flags="print" name="f1" page="1" rect="100,600,300,640" title="Kim"><contents>Typed words here</contents><defaultappearance>0 0 0 rg /Helv 12 Tf</defaultappearance></freetext>
<square color="#0000FF" interior-color="#CCCCFF" flags="print" name="s1" page="1" rect="100,100,200,200" width="2"/>
<sound name="x" page="0" rect="0,0,10,10"/>
</annots></xfdf>""", encoding="utf-8")
    added, skipped = comments.import_xfdf(blank, tmp_path / "acro.xfdf", tmp_path / "out.pdf")
    assert added == 6 and skipped == ["sound"]
    found = comments.collect(tmp_path / "out.pdf")
    question = next(c for c in found if c["text"] == "Question?")
    assert question["replies"][0]["text"] == "Answer" and question["replies"][0]["author"] == "Lee"
    highlight = next(c for c in found if c["kind"] == "Highlight")
    assert highlight["marked"] == "Hello wo" or highlight["marked"].startswith("Hello")
    with pytest.raises(core.PdfManagerError, match="isn't an XFDF"):
        (tmp_path / "bad.xfdf").write_text("<html/>", encoding="utf-8")
        comments.import_xfdf(blank, tmp_path / "bad.xfdf", tmp_path / "x.pdf")


def test_comments_tool_reader_endpoints_and_cli(tmp_path, capsys, app):
    src = commented(tmp_path)
    for mode, suffix in (("summary", ".pdf"), ("csv", ".csv"), ("export", ".xfdf")):
        work = tmp_path / f"w-{mode}"
        work.mkdir()
        notes = []
        name, data, ctype = gui.run_operation("comments", [src], {"mode": mode}, None, {}, work, notes)
        assert name.endswith(suffix) and notes == ["commented.pdf: 6 comments"]
        (tmp_path / name).write_bytes(data)
    work = tmp_path / "w-import"
    work.mkdir()
    blank = two_pages(tmp_path / "blank.pdf", turn_second=True)
    name, data, _ = gui.run_operation("comments", [blank], {"mode": "import"}, None,
                                      {"xfdf": tmp_path / "commented.xfdf"}, work, notes := [])
    assert notes == ["blank.pdf: added 6 comments"]

    base, token, _ = app
    body, headers = multipart({"password": "", "name": "c.pdf"}, [("file", src)])
    status, head, res = call(base, token, "/api/comments/summary", body, headers)
    assert status == 200 and res[:5] == b"%PDF-"
    status, head, res = call(base, token, "/api/comments/export", body, headers)
    assert status == 200 and b"<xfdf" in res
    body, headers = multipart({"password": ""}, [("file", blank), ("xfdf", tmp_path / "commented.xfdf")])
    status, head, res = call(base, token, "/api/comments/import", body, headers)
    assert status == 200 and res[:5] == b"%PDF-"

    assert main(["comments", str(src)]) == 0
    out = capsys.readouterr().out
    assert "p. 1  Strike-out  Ada  “Hello" in out and "p. 2  Stamp  Ada: Draft" in out
    assert main(["comments", str(src), "--export", "-o", str(tmp_path / "x.xfdf")]) == 0
    assert main(["comments", str(blank), "--import", str(tmp_path / "x.xfdf"),
                 "-o", str(tmp_path / "y.pdf")]) == 0
    assert "added 6 comments" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Web pages
# --------------------------------------------------------------------------

def test_web_addresses():
    assert office._web_address("example.com/a?b=1") == "https://example.com/a?b=1"
    assert office._web_address("http://x.org") == "http://x.org"
    assert office._web_address("notes.html") is None
    assert office._web_address("not a web address") is None
    with pytest.raises(core.PdfManagerError, match="on this computer"):
        office.html_to_pdf("http://localhost:8000/", "x.pdf")


needs_browser = pytest.mark.skipif(not office.find_browser(), reason="needs Edge, Chrome or Chromium")


@needs_browser
def test_saved_web_page_to_pdf(tmp_path, capsys):
    page = tmp_path / "page.html"
    page.write_text("<!doctype html><title>T</title><h1>Hello web</h1>"
                    "<p>A <a href='https://example.com/'>link</a>.</p>", encoding="utf-8")
    out, program = office.html_to_pdf(page, tmp_path / "page.pdf", timeout=60)
    reader = PdfReader(out)
    assert "Hello web" in reader.pages[0].extract_text()
    uris = [a.get_object()["/A"].get("/URI") for a in reader.pages[0].get("/Annots") or []]
    assert "https://example.com/" in uris
    work = tmp_path / "w"
    work.mkdir()
    notes = []
    name, data, _ = gui.run_operation("web", [page], {"url": ""}, None, {}, work, notes)
    assert name == "page.pdf" and notes[0].startswith("page.html: printed with ")
    assert main(["web", str(page), "-o", str(tmp_path / "cli.pdf")]) == 0
    assert (tmp_path / "cli.pdf").exists()
    assert main(["from-office", str(page), "-o", str(tmp_path / "office.pdf")]) == 0


def test_web_tool_needs_an_address_or_a_file(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    with pytest.raises(gui.UserError, match="type a web address, or add saved web pages"):
        gui.run_operation("web", [], {"url": " "}, None, {}, work, [])
    assert gui.OPS["web"]["url_field"] == "url"
    assert json.dumps(gui.OPS["comments"])
