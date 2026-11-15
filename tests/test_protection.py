# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.40.0 (protection.py, redact.py): how strong a password is, said while
it's typed; files protected with the old encryption that is quickly broken
are named and upgraded to AES-256 with the same password and limits; and
after Black out, a check that nothing redacted is left anywhere in the file."""

import pikepdf
import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from pdfmanager import core, protection, redact


def plain(path, lines=("An ordinary page",), title=None):
    c = canvas.Canvas(str(path), pagesize=A4)
    if title:
        c.setTitle(title)
    for i, line in enumerate(lines):
        c.drawString(72, 760 - 20 * i, line)
    c.showPage()
    c.save()
    return path


def locked(path, source, user="open", owner="own", R=3, aes=False, allow=None):
    with pikepdf.open(source) as pdf:
        pdf.save(path, encryption=pikepdf.Encryption(user=user, owner=owner, R=R, aes=aes, metadata=R >= 4,
                                                     allow=allow or pikepdf.Permissions()))
    return path


# -- how strong a password is ---------------------------------------------------------------------

def test_password_strength():
    weakest = protection.strength("password")
    assert weakest["score"] == 0 and weakest["label"] == "Very weak" and "common" in weakest["advice"]
    assert protection.strength("")["score"] == 0 and protection.strength("")["label"] == ""
    assert protection.strength("12345678")["score"] == 0
    assert protection.strength("19841231")["score"] <= 1 and "numbers" in protection.strength("19841231")["advice"]
    assert protection.strength("qwertyuiop")["score"] == 0
    assert protection.strength("aaaaaaaaaaaa")["score"] <= 1
    assert protection.strength("Tr0ub4dor")["score"] in (1, 2)
    strong = protection.strength("correct horse battery staple")
    assert strong["score"] == 4 and strong["label"] == "Strong"
    assert protection.strength("vK7#pQ2m!xW9zL4t")["score"] == 4
    # longer is never weaker, and the scores go up in order
    assert [protection.strength("xk" * n)["score"] for n in (2, 4, 8, 16)] == sorted(
        protection.strength("xk" * n)["score"] for n in (2, 4, 8, 16))
    for word in ("Password1", "letmein!", "Zpdf2026"):
        assert protection.strength(word)["score"] <= 2, word
    assert set(protection.strength("anything at all")) == {"score", "label", "advice"}


# -- the protection a file has --------------------------------------------------------------------

@pytest.mark.parametrize("R, aes, method, weak", [
    (2, False, "RC4, 40-bit", True), (3, False, "RC4, 128-bit", True),
    (4, True, "AES, 128-bit", False), (6, True, "AES, 256-bit", False)])
def test_what_protects_a_file(tmp_path, R, aes, method, weak):
    src = locked(tmp_path / "locked.pdf", plain(tmp_path / "p.pdf"), R=R, aes=aes)
    info = protection.info(src)                       # read without the password
    assert info["protected"] and info["method"] == method and info["weak"] is weak
    assert ("minutes" in info["advice"]) == (method == "RC4, 40-bit")


def test_an_open_file_has_no_protection_to_speak_of(tmp_path):
    assert protection.info(plain(tmp_path / "p.pdf")) == {"protected": False, "method": "", "weak": False, "advice": ""}
    (tmp_path / "junk.pdf").write_bytes(b"not a pdf")
    assert protection.info(tmp_path / "junk.pdf")["protected"] is False


def test_upgrade_keeps_the_password_and_the_limits(tmp_path):
    allow = pikepdf.Permissions(extract=False, print_highres=False, print_lowres=False)
    src = locked(tmp_path / "old.pdf", plain(tmp_path / "p.pdf"), user="open", owner="own", R=3, allow=allow)
    out = tmp_path / "new.pdf"
    said = protection.upgrade(src, out, "own")        # with the permissions password: everything is kept
    assert protection.info(out)["method"] == "AES, 256-bit" and "AES-256" in said
    with pikepdf.open(out, password="open") as pdf:
        assert not pdf.allow.extract and not pdf.allow.print_highres and len(pdf.pages) == 1
        assert pdf.user_password_matched and not pdf.owner_password_matched
    with pikepdf.open(out, password="own") as pdf:
        assert pdf.owner_password_matched
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(out, password="wrong")


def test_upgrade_with_only_the_password_to_open(tmp_path):
    allow = pikepdf.Permissions(extract=False)
    src = locked(tmp_path / "old.pdf", plain(tmp_path / "p.pdf"), user="open", owner="own", R=2, allow=allow)
    out = tmp_path / "new.pdf"
    said = protection.upgrade(src, out, "open")
    assert "limits" in said                            # they stay, though their password isn't known
    with pikepdf.open(out, password="open") as pdf:
        assert not pdf.allow.extract and not pdf.owner_password_matched
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(out, password="own")              # the old permissions password went with the old encryption
    # no limits, one password: the simple case
    simple = locked(tmp_path / "simple.pdf", plain(tmp_path / "p.pdf"), user="pw", owner="pw", R=3,
                    allow=pikepdf.Permissions())
    protection.upgrade(simple, out, "pw")
    with pikepdf.open(out, password="pw") as pdf:
        assert pdf.allow.extract and pdf.encryption.R == 6


def test_upgrade_mistakes(tmp_path):
    src = locked(tmp_path / "old.pdf", plain(tmp_path / "p.pdf"), R=3)
    with pytest.raises(core.PdfManagerError, match="password"):
        protection.upgrade(src, tmp_path / "o.pdf", "wrong")
    with pytest.raises(core.PdfManagerError, match="isn't protected"):
        protection.upgrade(plain(tmp_path / "open.pdf"), tmp_path / "o.pdf", None)
    fine = locked(tmp_path / "fine.pdf", plain(tmp_path / "p.pdf"), R=6, aes=True)
    with pytest.raises(core.PdfManagerError, match="already"):
        protection.upgrade(fine, tmp_path / "o.pdf", "open")
    assert not (tmp_path / "o.pdf").exists()


# -- after Black out: nothing redacted is left ----------------------------------------------------

def test_the_check_after_redacting_finds_nothing_left(tmp_path):
    src = plain(tmp_path / "p.pdf", ["Invoice for John Smith", "Total 120.00", "Thank you"])
    out = tmp_path / "out.pdf"
    report = redact.redact(src, out, terms=["John Smith"])
    assert report.proof["clean"] and report.proof["found"] == []
    assert {"page text", "document details", "bookmarks", "form values", "comments", "attachments",
            "earlier versions"} <= set(report.proof["checked"])
    assert "nothing" in redact.proof_words(report.proof) and "John Smith" not in redact.proof_words(report.proof)


def test_the_check_names_where_a_redacted_word_is_still_kept(tmp_path):
    src = plain(tmp_path / "p.pdf", ["Invoice for John Smith", "Total 120.00"], title="Invoice John Smith 2026")
    w = PdfWriter(clone_from=str(src))
    w.add_attachment("John Smith cv.txt", b"harmless")
    w.add_attachment("notes.txt", b"call John Smith on Monday")
    w.write(str(tmp_path / "with.pdf"))
    out = tmp_path / "out.pdf"
    report = redact.redact(tmp_path / "with.pdf", out, terms=["John Smith"])
    proof = report.proof
    assert not proof["clean"]
    where = {f["where"] for f in proof["found"]}
    assert "the document's details (title)" in where
    assert any("John Smith cv.txt" in w_ for w_ in where) and any("notes.txt" in w_ for w_ in where)
    assert all(f["what"] == "John Smith" for f in proof["found"])
    assert "page 1" not in " ".join(where)             # the page itself is clean
    words = redact.proof_words(proof)
    assert words.startswith("Careful") and "title" in words and "Remove hidden data" in words


def test_the_check_reads_a_kind_of_number_too(tmp_path):
    src = plain(tmp_path / "p.pdf", ["Card 4111 1111 1111 1111", "Mail ada@example.org"], title="for ada@example.org")
    report = redact.redact(src, tmp_path / "out.pdf", patterns=[r"[\w.]+@[\w.]+"])
    assert not report.proof["clean"]
    assert report.proof["found"] == [{"where": "the document's details (title)", "what": "ada@example.org"}]


def test_boxes_leave_nothing_under_them(tmp_path):
    src = plain(tmp_path / "p.pdf", ["Top secret line", "An open line"])
    out = tmp_path / "out.pdf"
    report = redact.redact(src, out, boxes={0: [(60, 750, 400, 775)]})
    assert report.proof["clean"] and "under the boxes" in report.proof["checked"]
    text = PdfReader(str(out)).pages[0].extract_text()
    assert "secret" not in text and "open line" in text
    # the picture way is checked the same
    report = redact.redact(src, out, boxes={0: [(60, 750, 400, 775)]}, method="image")
    assert report.proof["clean"]


def test_the_check_notices_an_earlier_version_kept_in_the_file(tmp_path):
    src = plain(tmp_path / "p.pdf", ["Nothing to see"])
    assert redact.check_redacted(src, terms=["zzz"])["clean"]
    # a file saved "on top of itself" keeps the old version inside
    w = PdfWriter(str(src), incremental=True)
    w.add_metadata({"/Title": "changed"})
    grown = tmp_path / "grown.pdf"
    w.write(str(grown))
    assert grown.read_bytes().count(b"%%EOF") > 1
    proof = redact.check_redacted(grown, terms=["zzz"])
    assert [f["where"] for f in proof["found"]] == ["earlier versions kept in the file"]
    assert "save the result as a new file" in redact.proof_words(proof).lower()


# -- in the app and on the command line -----------------------------------------------------------

def test_the_file_panel_and_the_tool(tmp_path):
    from pdfmanager import filefacts, gui

    allow = pikepdf.Permissions(extract=False)
    old = locked(tmp_path / "old.pdf", plain(tmp_path / "p.pdf"), user="open", owner="own", R=3, allow=allow)
    assert filefacts.facts(old)["weak_protection"] == "RC4, 128-bit"          # known before its password is
    found = filefacts.facts(old, "own")
    first = filefacts.suggestions(found)[0]
    assert first["op"] == "upgrade-protection" and "RC4, 128-bit" in first["why"]
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    notes = []
    name, data, _ = gui.run_operation("upgrade-protection", [old], {}, password="own", work=out_dir, notes=notes)
    assert name == "old_protected.pdf" and "now AES-256" in notes[0]
    result = tmp_path / name
    result.write_bytes(data)
    assert protection.info(result)["method"] == "AES, 256-bit"
    with pikepdf.open(result, password="open") as pdf:                       # both passwords kept, not replaced
        assert not pdf.owner_password_matched and not pdf.allow.extract
    assert not filefacts.facts(result, "own")["weak_protection"]
    assert any(op["id"] == "upgrade-protection" for op in gui.OPERATIONS)
    assert next(f for f in gui.OPS["encrypt"]["fields"] if f["key"] == "new_password")["strength"]


def test_the_redact_tool_says_what_the_check_found(tmp_path):
    from pdfmanager import gui

    src = plain(tmp_path / "p.pdf", ["Invoice for John Smith"], title="John Smith invoice")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    notes = []
    gui._run_file_op("redact", src, out_dir, {"terms": "John Smith", "method": "remove"}, None, [], notes)
    assert any(n.startswith("Careful") and "title" in n for n in notes)
    notes = []
    gui._run_file_op("redact", plain(tmp_path / "q.pdf", ["Invoice for John Smith"]), out_dir,
                     {"terms": "John Smith", "method": "remove"}, None, [], notes)
    assert any(n.startswith("Checked: nothing redacted is left") for n in notes)


def test_command_line(tmp_path, capsys):
    from pdfmanager.cli import main

    old = locked(tmp_path / "old.pdf", plain(tmp_path / "p.pdf"), user="open", owner="open", R=2)
    assert main(["upgrade-protection", str(old), "--check", "--password", "open"]) == 2
    assert "RC4, 40-bit (weak" in capsys.readouterr().out
    out = tmp_path / "new.pdf"
    assert main(["upgrade-protection", str(old), "-o", str(out), "--password", "open"]) == 0
    assert "now AES-256" in capsys.readouterr().out
    assert main(["upgrade-protection", str(out), "--check", "--password", "open"]) == 0
    assert "AES, 256-bit" in capsys.readouterr().out
    # a weak new password is said, and still set
    assert main(["encrypt", str(plain(tmp_path / "x.pdf")), "-o", str(tmp_path / "x2.pdf"),
                 "--user-password", "password"]) == 0
    assert "very weak" in capsys.readouterr().err
    # redacting prints the check
    src = plain(tmp_path / "r.pdf", ["Invoice for John Smith"], title="John Smith invoice")
    assert main(["redact", str(src), "-o", str(tmp_path / "r2.pdf"), "--text", "John Smith"]) == 0
    assert "Careful" in capsys.readouterr().out
