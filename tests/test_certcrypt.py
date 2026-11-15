# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.41.0 (certcrypt.py): a PDF protected for people instead of with a
password. It's encrypted for their certificates (the public half of a
Digital ID), and only someone holding one of those Digital IDs can open
it. ZPDF's other tools say so plainly when given such a file."""

import datetime as dt

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

pytest.importorskip("pyhanko")
from pdfmanager import certcrypt, core  # noqa: E402

PW = "id-password"


def make_id(folder, name, encipher=True, days=(-1, 365), usage=True):
    """A Digital ID (.pfx) and its certificate (.cer) for ``name``."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = dt.datetime.now(dt.timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
               .serial_number(x509.random_serial_number()).not_valid_before(now + dt.timedelta(days=days[0]))
               .not_valid_after(now + dt.timedelta(days=days[1])))
    if usage:
        builder = builder.add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=True, key_encipherment=encipher, data_encipherment=False,
            key_agreement=False, key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False),
            critical=True)
    cert = builder.sign(key, hashes.SHA256())
    stem = name.lower().replace(" ", "-")
    pfx = folder / f"{stem}.pfx"
    pfx.write_bytes(pkcs12.serialize_key_and_certificates(
        b"id", key, cert, None, serialization.BestAvailableEncryption(PW.encode())))
    cer = folder / f"{stem}.cer"
    cer.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return pfx, cer


def plain(path, words="For Ann and Bob only"):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawString(72, 760, words)
    c.showPage()
    c.save()
    return path


@pytest.fixture
def people(tmp_path):
    return {name: make_id(tmp_path, name) for name in ("Ann Reader", "Bob Reader", "Eve Other")}


def test_protected_for_two_people(tmp_path, people):
    src, out = plain(tmp_path / "p.pdf"), tmp_path / "for-them.pdf"
    names = certcrypt.encrypt_for(src, out, [people["Ann Reader"][1], people["Bob Reader"][1]])
    assert names == ["Ann Reader", "Bob Reader"]
    assert certcrypt.is_for_people(out) and not certcrypt.is_for_people(src)
    assert b"For Ann" not in out.read_bytes()
    for who in ("Ann Reader", "Bob Reader"):
        opened = tmp_path / f"{who}.pdf"
        said = certcrypt.open_with_id(out, opened, people[who][0], PW)
        assert who in said
        assert "For Ann and Bob only" in PdfReader(str(opened)).pages[0].extract_text()
        assert not certcrypt.is_for_people(opened)
    # someone it wasn't protected for can't open it, and is told why
    with pytest.raises(core.PdfManagerError, match="wasn't protected for"):
        certcrypt.open_with_id(out, tmp_path / "eve.pdf", people["Eve Other"][0], PW)
    assert not (tmp_path / "eve.pdf").exists()


def test_the_sender_can_be_included(tmp_path, people):
    src, out = plain(tmp_path / "p.pdf"), tmp_path / "out.pdf"
    # a Digital ID file among the certificates stands for its certificate (the sender's own)
    with pytest.raises(core.PdfManagerError, match="needs its password"):
        certcrypt.encrypt_for(src, out, [people["Bob Reader"][1], people["Ann Reader"][0]])
    names = certcrypt.encrypt_for(src, out, [people["Bob Reader"][1], people["Ann Reader"][0]], id_password=PW)
    assert sorted(names) == ["Ann Reader", "Bob Reader"]
    certcrypt.open_with_id(out, tmp_path / "mine.pdf", people["Ann Reader"][0], PW)


def test_certificates_in_other_shapes(tmp_path, people):
    pem = people["Ann Reader"][1]
    der = tmp_path / "ann.der"
    der.write_bytes(x509.load_pem_x509_certificate(pem.read_bytes()).public_bytes(serialization.Encoding.DER))
    both = tmp_path / "both.pem"
    both.write_bytes(pem.read_bytes() + people["Bob Reader"][1].read_bytes())
    out = tmp_path / "out.pdf"
    assert certcrypt.encrypt_for(plain(tmp_path / "p.pdf"), out, [der]) == ["Ann Reader"]
    assert certcrypt.encrypt_for(plain(tmp_path / "p.pdf"), out, [both]) == ["Ann Reader", "Bob Reader"]
    assert certcrypt.encrypt_for(plain(tmp_path / "p.pdf"), out, [pem, der]) == ["Ann Reader"]   # once each


def test_mistakes_are_explained(tmp_path, people):
    src, out = plain(tmp_path / "p.pdf"), tmp_path / "out.pdf"
    with pytest.raises(core.PdfManagerError, match="certificate"):
        certcrypt.encrypt_for(src, out, [])
    (tmp_path / "junk.cer").write_bytes(b"not a certificate")
    with pytest.raises(core.PdfManagerError, match="junk.cer isn't a certificate"):
        certcrypt.encrypt_for(src, out, [tmp_path / "junk.cer"])
    _, signing_only = make_id(tmp_path, "Sig Only", encipher=False)
    with pytest.raises(core.PdfManagerError, match="Sig Only.*only for signing"):
        certcrypt.encrypt_for(src, out, [signing_only])
    _, expired = make_id(tmp_path, "Old Timer", days=(-400, -30))
    with pytest.raises(core.PdfManagerError, match="Old Timer.*expired"):
        certcrypt.encrypt_for(src, out, [expired])
    assert not out.exists()
    # opening
    made = tmp_path / "made.pdf"
    certcrypt.encrypt_for(src, made, [people["Ann Reader"][1]])
    with pytest.raises(core.PdfManagerError, match="password"):
        certcrypt.open_with_id(made, out, people["Ann Reader"][0], "wrong")
    with pytest.raises(core.PdfManagerError, match="isn't protected for"):
        certcrypt.open_with_id(src, out, people["Ann Reader"][0], PW)
    with pytest.raises(core.PdfManagerError, match="not a digital ID"):
        certcrypt.open_with_id(made, out, tmp_path / "junk.cer", PW)


def test_a_certificate_without_a_stated_use_is_taken(tmp_path):
    pfx, cer = make_id(tmp_path, "Plain Cert", usage=False)
    out = tmp_path / "out.pdf"
    assert certcrypt.encrypt_for(plain(tmp_path / "p.pdf"), out, [cer]) == ["Plain Cert"]
    certcrypt.open_with_id(out, tmp_path / "opened.pdf", pfx, PW)


def test_a_password_protected_source(tmp_path, people):
    locked = tmp_path / "locked.pdf"
    core.encrypt(plain(tmp_path / "p.pdf"), locked, "secret")
    out = tmp_path / "out.pdf"
    with pytest.raises(core.PdfManagerError):
        certcrypt.encrypt_for(locked, out, [people["Ann Reader"][1]])
    certcrypt.encrypt_for(locked, out, [people["Ann Reader"][1]], password="secret")
    certcrypt.open_with_id(out, tmp_path / "opened.pdf", people["Ann Reader"][0], PW)
    assert not PdfReader(str(tmp_path / "opened.pdf")).is_encrypted          # the people replace the password


def test_other_tools_say_what_the_file_is(tmp_path, people):
    from pdfmanager import filefacts, protection, risks

    out = tmp_path / "out.pdf"
    certcrypt.encrypt_for(plain(tmp_path / "p.pdf"), out, [people["Ann Reader"][1]])
    with pytest.raises(core.PdfManagerError, match="protected for particular people"):
        core.open_pdf(out)
    with pytest.raises(core.PdfManagerError, match="protected for particular people"):
        core.rotate(out, tmp_path / "r.pdf", 90)
    with pytest.raises(core.PdfManagerError, match="protected for particular people"):
        risks.check(out)
    seen = protection.info(out)
    assert seen["protected"] and seen["method"] == "for particular people (certificates)" and not seen["weak"]
    found = filefacts.facts(out)
    assert found["protected"] and found["for_people"] and found["pages"] is None and "error" not in found
    assert [s["op"] for s in filefacts.suggestions(found)] == ["decrypt-cert"]


def test_the_tools_and_the_command_line(tmp_path, people, capsys):
    from pdfmanager import gui
    from pdfmanager.cli import main

    src = plain(tmp_path / "p.pdf")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    notes = []
    (made,) = gui._run_file_op("encrypt-cert", src, out_dir, {}, None,
                               {"certificates[]": [people["Ann Reader"][1], people["Bob Reader"][1]]}, notes)
    assert made.name == "p_for-people.pdf" and "Ann Reader, Bob Reader" in notes[0]
    notes = []
    (opened,) = gui._run_file_op("decrypt-cert", made, out_dir, {"pfx_password": PW}, None,
                                 {"pfx": people["Bob Reader"][0]}, notes)
    assert opened.name == "p_for-people_opened.pdf" and "Bob Reader" in notes[0]
    assert "For Ann and Bob only" in PdfReader(str(opened)).pages[0].extract_text()
    with pytest.raises(gui.UserError, match="certificate"):
        gui._run_file_op("encrypt-cert", src, out_dir, {}, None, {}, [])
    assert {"encrypt-cert", "decrypt-cert"} <= {op["id"] for op in gui.OPERATIONS}

    cli_out = tmp_path / "cli.pdf"
    assert main(["encrypt-for", str(src), "-o", str(cli_out), "--cert", str(people["Ann Reader"][1]),
                 "--cert", str(people["Bob Reader"][1])]) == 0
    assert "Ann Reader, Bob Reader" in capsys.readouterr().out
    assert main(["open-with-id", str(cli_out), "-o", str(tmp_path / "plain.pdf"), "--id", str(people["Ann Reader"][0]),
                 "--id-password", PW]) == 0
    assert "Ann Reader" in capsys.readouterr().out
    assert main(["open-with-id", str(cli_out), "-o", str(tmp_path / "no.pdf"), "--id", str(people["Eve Other"][0]),
                 "--id-password", PW]) != 0


def test_any_other_tool_in_the_app_says_what_the_file_is(tmp_path, people):
    """Not "damaged", and no try at mending it: it's opened with a Digital ID."""
    from pdfmanager import gui

    out = tmp_path / "for.pdf"
    certcrypt.encrypt_for(plain(tmp_path / "p.pdf"), out, [people["Ann Reader"][1]])
    work = tmp_path / "work"
    work.mkdir()
    for op in ("rotate", "compress", "makesafe"):
        with pytest.raises(gui.UserError, match="protected for particular people"):
            gui.run_operation(op, [out], {"angle": "90"}, None, {}, work, [])
    name, data, _ = gui.run_operation("decrypt-cert", [out], {"pfx_password": PW}, None,
                                      {"pfx": people["Ann Reader"][0]}, work, [])
    assert name == "for_opened.pdf" and data.startswith(b"%PDF")

    # a plain file in the same list is left out, and said, instead of stopping the run
    work2 = tmp_path / "work2"
    work2.mkdir()
    notes = []
    name, data, _ = gui.run_operation("decrypt-cert", [tmp_path / "p.pdf", out], {"pfx_password": PW}, None,
                                      {"pfx": people["Ann Reader"][0]}, work2, notes)
    assert name == "for_opened.pdf" and any("left out" in n and "p.pdf" in n for n in notes)
    work3 = tmp_path / "work3"
    work3.mkdir()
    with pytest.raises(core.PdfManagerError, match="isn't protected for particular people"):
        gui.run_operation("decrypt-cert", [tmp_path / "p.pdf"], {"pfx_password": PW}, None,
                          {"pfx": people["Ann Reader"][0]}, work3, [])


def test_how_long_a_signature_stays_checkable(tmp_path):
    """Said with each signature: with a timestamp it lasts; without one it
    lasts as long as the signer's certificate."""
    import datetime as dt

    from pdfmanager import certsign

    pfx, _ = make_id(tmp_path, "Jane Signer", encipher=False)
    out = tmp_path / "signed.pdf"
    certsign.sign(plain(tmp_path / "p.pdf"), out, pfx, PW)
    (sig,) = certsign.verify(out)
    assert sig["certificate_expires"] and "No timestamp server vouched" in sig["lasting"]
    assert "Later on: No timestamp server" in certsign.report([sig])
    ends = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)
    stamped = {**sig, "time_source": "timestamp"}
    assert "stays checkable after" in certsign._lasting(stamped, ends)
    assert "expired on" in certsign._lasting(sig, dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1))
    assert certsign._lasting({**sig, "status": "invalid"}, ends) == ""


def test_the_tool_pages_and_the_recent_list_know_what_it_is(tmp_path, people):
    """From the Windows check of 0.41.0: the tool pages called it unreadable
    (so even Open with Digital ID wouldn't run on it), and Recent said it
    couldn't be opened."""
    from pathlib import Path

    from pdfmanager import gui

    out = tmp_path / "for.pdf"
    certcrypt.encrypt_for(plain(tmp_path / "p.pdf"), out, [people["Ann Reader"][1]])
    seen = gui._inspect_pdf(out)
    assert seen["for_people"] and not seen["locked"] and "protected for particular people" in seen["error"]
    assert "for_people" not in gui._inspect_pdf(plain(tmp_path / "q.pdf"))
    html = (Path(gui.__file__).parent / "gui.html").read_text(encoding="utf-8")
    assert '!(it.for_people && current.id === "decrypt-cert")' in html          # that tool takes it
    assert 'with your Digital ID first (Protect → Open with Digital ID)' in html
    assert "const failed = e.failed && !e.facts?.for_people;" in html
