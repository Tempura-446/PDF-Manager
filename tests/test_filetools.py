"""What a password allows, attached files, page labels, repair and fast web
view (0.13.0)."""

import json
from pathlib import Path

import pikepdf
import pytest
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from pdfmanager import core, filetools, gui
from pdfmanager.cli import main


def pages_pdf(path, n=3):
    c = canvas.Canvas(str(path))
    for i in range(n):
        c.drawString(72, 700, f"Page {i + 1}")
        c.showPage()
    c.save()
    return path


def run(op, files, opts, tmp_path, extra=None, password=None):
    work = tmp_path / f"work-{op}-{len(list(tmp_path.iterdir()))}"
    work.mkdir()
    notes = []
    name, data, _ = gui.run_operation(op, files, opts, password, extra or {}, work, notes)
    out = tmp_path / name
    out.write_bytes(data)
    return out, notes


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------

def test_limits_without_an_open_password(tmp_path):
    src = pages_pdf(tmp_path / "a.pdf")
    perms = filetools.permissions("low", "forms", copy=False)
    core.encrypt(src, tmp_path / "limited.pdf", "", "boss", permissions=perms)
    with pikepdf.open(tmp_path / "limited.pdf") as pdf:  # anyone can open it
        assert pdf.is_encrypted and not pdf.owner_password_matched
        allow = pdf.allow
        assert allow.print_lowres and not allow.print_highres and not allow.extract
        assert allow.modify_form and not allow.modify_annotation and not allow.modify_other
        assert allow.accessibility  # screen readers may still read it
    with pikepdf.open(tmp_path / "limited.pdf", password="boss") as pdf:
        assert pdf.owner_password_matched
    assert filetools.describe(perms) == ("printing in low quality, filling in forms and signing, "
                                         "no copying")
    # Removing the protection needs the permissions password.
    with pytest.raises(core.PdfManagerError, match="needs its permissions password"):
        core.decrypt(tmp_path / "limited.pdf", tmp_path / "open.pdf", "")
    core.decrypt(tmp_path / "limited.pdf", tmp_path / "open.pdf", "boss")
    assert not PdfReader(tmp_path / "open.pdf").is_encrypted


def test_limits_need_a_separate_permissions_password(tmp_path):
    src = pages_pdf(tmp_path / "a.pdf")
    perms = filetools.permissions("none", "none", copy=False)
    with pytest.raises(core.PdfManagerError, match="permissions password different"):
        core.encrypt(src, tmp_path / "x.pdf", "secret", None, permissions=perms)
    with pytest.raises(core.PdfManagerError, match="permissions password different"):
        core.encrypt(src, tmp_path / "x.pdf", "secret", "secret", permissions=perms)
    with pytest.raises(core.PdfManagerError, match="enter a password"):
        core.encrypt(src, tmp_path / "x.pdf", "", None)
    # A plain password still allows everything (and the owner can do all).
    core.encrypt(src, tmp_path / "plain.pdf", "secret")
    with pikepdf.open(tmp_path / "plain.pdf", password="secret") as pdf:
        assert not filetools.restricts(pdf.allow)
    with pytest.raises(core.PdfManagerError, match="printing must be"):
        filetools.permissions("colour")


def test_add_password_tool_and_cli_limits(tmp_path, capsys):
    src = pages_pdf(tmp_path / "a.pdf")
    out, notes = run("encrypt", [src], {"new_password": "", "printing": "none", "changes": "comments",
                                        "copy": "false", "owner_password": "boss"}, tmp_path)
    assert notes == ["a.pdf: anyone can open it; no printing, commenting, filling in forms and "
                     "signing, no copying"]
    with pikepdf.open(out) as pdf:
        assert not pdf.allow.print_lowres and pdf.allow.modify_annotation
    with pytest.raises(gui.UserError, match="enter a password to open it"):
        run("encrypt", [src], {"new_password": "", "printing": "high", "changes": "any",
                               "copy": "true"}, tmp_path)

    assert main(["encrypt", str(src), "--owner-password", "boss", "--printing", "low",
                 "--changes", "none", "--no-copy", "-o", str(tmp_path / "c.pdf")]) == 0
    assert "low quality, no changes, no copying" in capsys.readouterr().out
    assert main(["decrypt", str(tmp_path / "c.pdf"), "--password", "boss",
                 "-o", str(tmp_path / "d.pdf")]) == 0
    assert not PdfReader(tmp_path / "d.pdf").is_encrypted


# --------------------------------------------------------------------------
# Attached files
# --------------------------------------------------------------------------

def test_attach_replace_and_remove(tmp_path):
    src = pages_pdf(tmp_path / "a.pdf")
    (tmp_path / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    added, removed = filetools.attach(src, tmp_path / "b.pdf", [tmp_path / "data.csv",
                                                                tmp_path / "notes.txt"])
    assert added == ["data.csv", "notes.txt"] and removed == []
    found = filetools.attachments(tmp_path / "b.pdf")
    assert [(f["name"], f["size"]) for f in found] == [("data.csv", 8), ("notes.txt", 5)]
    with pikepdf.open(tmp_path / "b.pdf") as pdf:
        assert pdf.Root.PageMode == "/UseAttachments"
        assert pdf.attachments["data.csv"].get_file().read_bytes() == b"a,b\n1,2\n"
        assert str(pdf.attachments["data.csv"].get_file().mime_type) == "text/csv"
    # The same name again replaces it; remove one by name.
    (tmp_path / "notes.txt").write_text("changed", encoding="utf-8")
    filetools.attach(tmp_path / "b.pdf", tmp_path / "c.pdf", [tmp_path / "notes.txt"],
                     remove=["data.csv"])
    assert [(f["name"], f["size"]) for f in filetools.attachments(tmp_path / "c.pdf")] == [
        ("notes.txt", 7)]
    with pytest.raises(core.PdfManagerError, match="no attached file is called x.doc"):
        filetools.attach(tmp_path / "c.pdf", tmp_path / "d.pdf", remove=["x.doc"])
    filetools.attach(tmp_path / "c.pdf", tmp_path / "d.pdf", remove_all=True)
    assert filetools.attachments(tmp_path / "d.pdf") == []
    with pikepdf.open(tmp_path / "d.pdf") as pdf:
        assert "/PageMode" not in pdf.Root
    with pytest.raises(core.PdfManagerError, match="choose files to attach"):
        filetools.attach(src, tmp_path / "e.pdf")


def test_attach_tool_keeps_the_password_and_cli(tmp_path, capsys):
    src = pages_pdf(tmp_path / "a.pdf")
    core.encrypt(src, tmp_path / "locked.pdf", "pw")
    (tmp_path / "one.txt").write_text("1", encoding="utf-8")
    (tmp_path / "two.txt").write_text("22", encoding="utf-8")
    two = [tmp_path / "one.txt", tmp_path / "two.txt"]
    out, notes = run("attach", [tmp_path / "locked.pdf"], {"remove": "", "remove_all": "false"},
                     tmp_path, extra={"attach": two[0], "attach[]": two}, password="pw")
    assert notes == ["locked.pdf: attached one.txt, two.txt (now attached: one.txt, two.txt)"]
    assert PdfReader(out).is_encrypted  # protected again with its password
    assert [f["name"] for f in filetools.attachments(out, "pw")] == ["one.txt", "two.txt"]

    assert main(["attach", str(src), "--add", str(two[0]), "-o", str(tmp_path / "x.pdf")]) == 0
    capsys.readouterr()
    assert main(["attach", str(tmp_path / "x.pdf")]) == 0
    assert capsys.readouterr().out.strip() == "one.txt  (1 bytes)"
    assert main(["attach", str(tmp_path / "x.pdf"), "--remove", "one.txt",
                 "-o", str(tmp_path / "y.pdf")]) == 0
    assert "removed one.txt" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Page labels
# --------------------------------------------------------------------------

def test_parse_labels():
    assert filetools.parse_labels("1-4: i; 5-: 1; 30-: A-1") == [
        (1, "i", "", 1), (5, "1", "", 1), (30, "1", "A-", 1)]
    assert filetools.parse_labels("1: Cover\n2: iv\n3: Appendix c\n4: A-c\n5: XIV") == [
        (1, "", "Cover", 1), (2, "i", "", 4), (3, "a", "Appendix ", 3), (4, "a", "A-", 3),
        (5, "I", "", 14)]
    with pytest.raises(core.PdfManagerError, match="can't read the page label"):
        filetools.parse_labels("cover: 1")


def test_page_labels(tmp_path):
    src = pages_pdf(tmp_path / "a.pdf", 7)
    filetools.set_page_labels(src, tmp_path / "b.pdf", filetools.parse_labels(
        "1: Cover; 2-3: i; 4: 1; 7: A-1"))
    assert filetools.page_labels(tmp_path / "b.pdf") == ["Cover", "i", "ii", "1", "2", "3", "A-1"]
    filetools.set_page_labels(src, tmp_path / "c.pdf", [(3, "1", "", 1)])  # from page 3
    assert filetools.page_labels(tmp_path / "c.pdf") == ["1", "2", "1", "2", "3", "4", "5"]
    filetools.set_page_labels(tmp_path / "b.pdf", tmp_path / "d.pdf", [])
    assert filetools.page_labels(tmp_path / "d.pdf") is None
    with pytest.raises(core.PdfManagerError, match="page 9 doesn't exist"):
        filetools.set_page_labels(src, tmp_path / "e.pdf", [(9, "1", "", 1)])
    with pytest.raises(core.PdfManagerError, match="page 2 has two labels"):
        filetools.set_page_labels(src, tmp_path / "e.pdf", [(2, "1", "", 1), (2, "i", "", 1)])


def test_page_labels_tool_and_cli(tmp_path, capsys):
    src = pages_pdf(tmp_path / "a.pdf", 6)
    out, notes = run("labels", [src], {"roman": "2", "style": "1", "prefix": "", "start": "1",
                                       "custom": "", "clear": "false"}, tmp_path)
    assert notes == ["a.pdf: pages labelled i, ii, 1, 2, 3, 4"]
    out, notes = run("labels", [src], {"roman": "0", "style": "A", "prefix": "Annex ", "start": "1",
                                       "custom": "", "clear": "false"}, tmp_path)
    assert filetools.page_labels(out)[:2] == ["Annex A", "Annex B"]
    out, notes = run("labels", [src], {"roman": "3", "custom": "1: Cover; 2-: 1", "clear": "false"},
                     tmp_path)
    assert filetools.page_labels(out) == ["Cover", "1", "2", "3", "4", "5"]
    out, notes = run("labels", [out], {"clear": "true"}, tmp_path)
    assert notes == [f"{Path(out).name.replace('_labelled', '', 0)}: page labels removed"] or \
        notes[0].endswith("page labels removed")

    assert main(["labels", str(src), "--set", "1-2: i; 3: 1", "-o", str(tmp_path / "x.pdf")]) == 0
    assert "(i, ii, 1, 2, 3, 4)" in capsys.readouterr().out
    assert main(["labels", str(tmp_path / "x.pdf")]) == 0
    assert capsys.readouterr().out.splitlines()[:3] == ["1: i", "2: ii", "3: 1"]


# --------------------------------------------------------------------------
# Repair and fast web view
# --------------------------------------------------------------------------

def test_repair_mends_a_broken_file_and_linearizes(tmp_path, capsys):
    src = pages_pdf(tmp_path / "a.pdf", 3)
    data = src.read_bytes()
    # Break the cross-reference table's offsets.
    start = data.rindex(b"startxref")
    broken = data[:start] + b"startxref\n123\n%%EOF\n"
    (tmp_path / "broken.pdf").write_bytes(broken)
    report = filetools.repair(tmp_path / "broken.pdf", tmp_path / "fixed.pdf")
    assert report["linearized"] and report["warnings"]
    assert filetools.is_linearized(tmp_path / "fixed.pdf")
    assert len(PdfReader(tmp_path / "fixed.pdf").pages) == 3
    plain = filetools.repair(src, tmp_path / "web.pdf", linearize=False)
    assert plain == {"warnings": [], "linearized": False}
    assert not filetools.is_linearized(tmp_path / "web.pdf")

    out, notes = run("repair", [tmp_path / "broken.pdf"], {"linearize": "true"}, tmp_path)
    assert notes[0].startswith("broken.pdf: mended ") and "fast web view" in notes[0]
    assert main(["repair", str(src), "-o", str(tmp_path / "r.pdf")]) == 0
    assert "fast web view" in capsys.readouterr().out
    with pytest.raises(core.PdfManagerError, match="too damaged to repair"):
        (tmp_path / "junk.pdf").write_bytes(b"not a pdf at all")
        filetools.repair(tmp_path / "junk.pdf", tmp_path / "j.pdf")


def test_new_tools_are_listed(tmp_path):
    ids = {op["id"] for op in gui.OPERATIONS}
    assert {"attach", "labels", "repair"} <= ids
    fields = {f["key"]: f for f in gui.OPS["encrypt"]["fields"]}
    assert not fields["new_password"].get("required")
    assert set(fields) == {"new_password", "printing", "changes", "copy", "owner_password"}
    assert json.dumps(gui.OPS["attach"])  # the page gets it as JSON
