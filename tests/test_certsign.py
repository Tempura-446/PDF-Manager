# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Signing with a digital ID (a .pfx file) and checking signatures:
certsign.py, its CLI commands, the GUI tools and the reader's endpoints."""

import datetime as dt
import json
import socket
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Text
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

pytest.importorskip("pyhanko")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.hazmat.primitives.serialization import pkcs12  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from pdfmanager import certsign, core, forms, gui  # noqa: E402
from pdfmanager.cli import main  # noqa: E402

PFX_PASSWORD = "s3cret"


def text_pdf(path, pages=("Hello world", "Second page")):
    c = canvas.Canvas(str(path), pagesize=A4)
    for text in pages:
        c.setFont("Helvetica", 14)
        c.drawString(72, 760, text)
        c.showPage()
    c.save()
    return path


def make_id(path, name="Jane Tester", password=PFX_PASSWORD, days=(-1, 365), usage=True):
    """A self-signed digital ID (.pfx) for ``name``; returns (pfx path,
    certificate path)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name),
                         x509.NameAttribute(NameOID.EMAIL_ADDRESS, "jane@example.com")])
    now = dt.datetime.now(dt.timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now + dt.timedelta(days=days[0]))
               .not_valid_after(now + dt.timedelta(days=days[1])))
    if usage:
        builder = builder.add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=True, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
            encipher_only=False, decipher_only=False), critical=True)
    cert = builder.sign(key, hashes.SHA256())
    encryption = (serialization.BestAvailableEncryption(password.encode()) if password
                  else serialization.NoEncryption())
    path.write_bytes(pkcs12.serialize_key_and_certificates(b"id", key, cert, None, encryption))
    cer = path.with_suffix(".cer")
    cer.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return path, cer


@pytest.fixture
def pfx(tmp_path):
    return make_id(tmp_path / "jane.pfx")[0]


@pytest.fixture
def doc(tmp_path):
    return text_pdf(tmp_path / "doc.pdf")


# -- signing and checking --------------------------------------------------------------

def test_visible_signature_is_valid_but_untrusted(tmp_path, pfx, doc):
    out = tmp_path / "signed.pdf"
    info = certsign.sign(doc, out, pfx, PFX_PASSWORD, page=2, box=[350, 50, 550, 110],
                         reason="Approved", location="Berlin")
    assert info["signer"] == "Jane Tester" and info["field"] == "Signature1"
    assert info["page"] == 2 and info["box"] == [350, 50, 550, 110] and info["visible"]
    assert out.read_bytes().startswith(doc.read_bytes())  # an incremental update

    (sig,) = certsign.verify(out)
    assert sig["status"] == "valid-untrusted" and sig["intact"] and not sig["trusted"]
    assert sig["field"] == "Signature1" and sig["signer"] == "Jane Tester"
    assert sig["email"] == "jane@example.com" and sig["issuer"] is None  # self-signed
    assert sig["reason"] == "Approved" and sig["location"] == "Berlin"
    assert sig["page"] == 2 and sig["box"] == [350, 50, 550, 110]
    assert sig["coverage"] == "entire-file" and sig["changes"] == "none"
    assert sig["time_source"] == "signer" and sig["time"]
    assert "identity isn't verified" in sig["summary"]

    # The appearance shows who signed, when and why.
    page = PdfReader(str(out)).pages[1]
    widget = [a.get_object() for a in page["/Annots"]][0]
    stream = widget["/AP"]["/N"].get_object().get_data()
    xobjects = widget["/AP"]["/N"].get_object()["/Resources"].get("/XObject")
    if xobjects:  # the text is in a nested form
        stream += b"".join(x.get_object().get_data() for x in xobjects.values())
    stream = stream.replace(b"\\072", b":")
    assert b"Jane Tester" in stream and b"Reason: Approved" in stream


def test_invisible_signature(tmp_path, pfx, doc):
    out = tmp_path / "signed.pdf"
    info = certsign.sign(doc, out, pfx, PFX_PASSWORD)
    assert info["visible"] is False and info["page"] is None and info["box"] is None
    (sig,) = certsign.verify(out)
    assert sig["status"] == "valid-untrusted" and sig["page"] is None and sig["box"] is None


def test_unsigned_document_has_no_signatures(doc):
    assert certsign.verify(doc) == []
    assert "no digital signatures" in certsign.report([])
    assert not certsign.all_valid([])


def test_trusted_certificate_makes_it_valid(tmp_path, doc):
    pfx, cer = make_id(tmp_path / "id.pfx")
    out = tmp_path / "signed.pdf"
    certsign.sign(doc, out, pfx, PFX_PASSWORD)
    (sig,) = certsign.verify(out, trusted=[cer])
    assert sig["status"] == "valid" and sig["trusted"]
    assert "identity is confirmed" in sig["summary"]


def test_rewritten_file_is_invalid(tmp_path, pfx, doc):
    out = tmp_path / "signed.pdf"
    certsign.sign(doc, out, pfx, PFX_PASSWORD, page=1, box=[50, 50, 250, 110])
    rewritten = tmp_path / "rewritten.pdf"
    PdfWriter(clone_from=str(out)).write(str(rewritten))
    (sig,) = certsign.verify(rewritten)
    assert sig["status"] == "invalid" and not sig["intact"]

    # A changed byte in the signed part.
    data = bytearray(out.read_bytes())
    at = data.find(b"/MediaBox")
    at = data.find(b"595", at)
    data[at:at + 3] = b"590"
    tampered = tmp_path / "tampered.pdf"
    tampered.write_bytes(bytes(data))
    (sig,) = certsign.verify(tampered)
    assert sig["status"] == "invalid" and "altered" in sig["summary"]


def test_change_appended_after_signing_is_reported(tmp_path, pfx, doc):
    out = tmp_path / "signed.pdf"
    certsign.sign(doc, out, pfx, PFX_PASSWORD)
    writer = PdfWriter(str(out), incremental=True)
    page = writer.pages[0]
    content = page.get_contents()
    content.set_data(content.get_data() + b"\nBT /F1 30 Tf 100 400 Td (FORGED) Tj ET")
    page.replace_contents(content)
    changed = tmp_path / "changed.pdf"
    writer.write(str(changed))
    (sig,) = certsign.verify(changed)
    assert sig["status"] == "modified" and sig["intact"] and sig["changes"] == "disallowed"
    assert not certsign.all_valid([sig])


def test_signing_twice_keeps_both_valid(tmp_path, pfx, doc):
    once, twice = tmp_path / "once.pdf", tmp_path / "twice.pdf"
    certsign.sign(doc, once, pfx, PFX_PASSWORD, page=1, box=[50, 50, 250, 110])
    other = make_id(tmp_path / "bob.pfx", name="Bob Second")[0]
    info = certsign.sign(once, twice, other, PFX_PASSWORD, page=2, box=[50, 50, 250, 110])
    assert info["field"] == "Signature2"
    first, second = certsign.verify(twice)
    assert [first["signer"], second["signer"]] == ["Jane Tester", "Bob Second"]
    assert first["status"] == second["status"] == "valid-untrusted"
    assert first["changes"] == "allowed" and second["changes"] == "none"
    assert certsign.all_valid([first, second])


def test_encrypted_input_stays_encrypted(tmp_path, pfx, doc):
    locked = tmp_path / "locked.pdf"
    core.encrypt(doc, locked, "pw")
    out = tmp_path / "signed.pdf"
    with pytest.raises(core.PdfManagerError, match="encrypted"):
        certsign.sign(locked, out, pfx, PFX_PASSWORD)
    certsign.sign(locked, out, pfx, PFX_PASSWORD, page=1, box=[50, 50, 250, 110], password="pw")
    assert PdfReader(str(out)).is_encrypted
    assert not PdfReader(str(out)).decrypt("wrong")
    (sig,) = certsign.verify(out, password="pw")
    assert sig["status"] == "valid-untrusted"
    with pytest.raises(core.PdfManagerError, match="encrypted"):
        certsign.verify(out)


def test_signing_an_existing_signature_field(tmp_path, pfx, doc):
    form = tmp_path / "form.pdf"
    forms.add_fields(doc, form, [{"type": "signature", "page": 1, "rect": [300, 600, 520, 660],
                                  "name": "Boss"}])
    out = tmp_path / "signed.pdf"
    with pytest.raises(core.PdfManagerError, match="empty ones: Boss"):
        certsign.sign(form, out, pfx, PFX_PASSWORD, field="Nobody")
    info = certsign.sign(form, out, pfx, PFX_PASSWORD, field="Boss", reason="OK")
    assert info["field"] == "Boss" and info["page"] == 1 and info["box"] == [300, 600, 520, 660]
    (sig,) = certsign.verify(out)
    assert sig["field"] == "Boss" and sig["status"] == "valid-untrusted" and sig["page"] == 1
    with pytest.raises(core.PdfManagerError, match="already signed"):
        certsign.sign(out, tmp_path / "again.pdf", pfx, PFX_PASSWORD, field="Boss")


def test_digital_id_problems_are_explained(tmp_path, pfx, doc):
    out = tmp_path / "signed.pdf"
    with pytest.raises(certsign.IdPasswordError, match="wrong password"):
        certsign.sign(doc, out, pfx, "nope")
    with pytest.raises(certsign.IdPasswordError, match="enter its password"):
        certsign.sign(doc, out, pfx)
    with pytest.raises(core.PdfManagerError, match="not a digital ID"):
        certsign.sign(doc, out, doc, "x")
    expired = make_id(tmp_path / "old.pfx", days=(-400, -1))[0]
    with pytest.raises(core.PdfManagerError, match="expired"):
        certsign.sign(doc, out, expired, PFX_PASSWORD)
    with pytest.raises(core.PdfManagerError, match="not found"):
        certsign.sign(doc, out, tmp_path / "missing.pfx", PFX_PASSWORD)
    assert not out.exists()
    # An ID without a password, and one without a key usage extension, sign fine.
    plain = make_id(tmp_path / "plain.pfx", password=None, usage=False)[0]
    certsign.sign(doc, out, plain)
    assert certsign.verify(out)[0]["status"] == "valid-untrusted"


def test_unreachable_timestamp_server_writes_nothing(tmp_path, pfx, doc):
    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    out = tmp_path / "signed.pdf"
    with pytest.raises(core.PdfManagerError, match="timestamp server.*nothing was signed"):
        certsign.sign(doc, out, pfx, PFX_PASSWORD, timestamp_url=f"http://127.0.0.1:{port}/")
    assert not out.exists()
    with pytest.raises(core.PdfManagerError, match="http"):
        certsign.sign(doc, out, pfx, PFX_PASSWORD, timestamp_url="ftp://example.com")


def test_timestamp_gives_the_signing_time(tmp_path, pfx, doc, monkeypatch):
    from pyhanko.sign import signers, timestamps

    tsa = signers.SimpleSigner.load_pkcs12_data(pfx.read_bytes(), [], PFX_PASSWORD.encode())
    asked = []

    def fake(url, timeout=None):
        asked.append(url)
        return timestamps.DummyTimeStamper(tsa_cert=tsa.signing_cert, tsa_key=tsa.signing_key)

    monkeypatch.setattr(timestamps, "HTTPTimeStamper", fake)
    out = tmp_path / "signed.pdf"
    info = certsign.sign(doc, out, pfx, PFX_PASSWORD, timestamp_url="http://tsa.example/")
    assert asked == ["http://tsa.example/"] and info["timestamp"]
    (sig,) = certsign.verify(out)
    assert sig["status"] == "valid-untrusted"
    assert sig["time_source"] == "timestamp" and sig["timestamp_trusted"] is False
    assert "timestamp server" in certsign.report([sig])


def test_place_puts_the_box_in_a_corner_of_the_page_as_seen(tmp_path):
    src = text_pdf(tmp_path / "a.pdf")
    assert certsign.place(src, "last", "bottom-right", "medium") == (
        2, [round(595.28 - 36 - 200, 2), 36, round(595.28 - 36, 2), 96])
    assert certsign.place(src, 1, "top-left", "small")[1] == [36, round(841.89 - 36 - 45, 2), 186,
                                                              round(841.89 - 36, 2)]
    turned = tmp_path / "turned.pdf"
    writer = PdfWriter(clone_from=str(src))
    writer.pages[0].rotate(90)
    writer.write(str(turned))
    # Seen bottom right of a page turned clockwise: the top right of the page.
    page, (x0, y0, x1, y1) = certsign.place(turned, 1, "bottom-right", "medium")
    assert (x1 - x0, y1 - y0) == (60, 200) and x1 > 500 and y1 > 700
    out = tmp_path / "signed.pdf"
    certsign.sign(turned, out, make_id(tmp_path / "id.pfx")[0], PFX_PASSWORD, page=page,
                  box=[x0, y0, x1, y1])
    widget = PdfReader(str(out)).pages[0]["/Annots"][0].get_object()
    assert list(widget["/AP"]["/N"].get_object()["/Matrix"])[:4] == [0, 1, -1, 0]
    with pytest.raises(core.PdfManagerError, match="doesn't exist"):
        certsign.place(src, 5)
    with pytest.raises(core.PdfManagerError, match="position"):
        certsign.place(src, 1, "middle")


# -- the app's tools -------------------------------------------------------------------

def test_gui_tools_sign_and_check(tmp_path, pfx, doc):
    (tmp_path / "w1").mkdir()
    notes = []
    name, data, ctype = gui.run_operation(
        "sign-cert", [doc], {"pfx_password": PFX_PASSWORD, "reason": "Approved",
                             "appearance": "visible", "page": "last", "position": "bottom-left",
                             "size": "small"},
        extra_files={"pfx": pfx}, work=tmp_path / "w1", notes=notes)
    assert name == "doc_signed.pdf" and ctype == "application/pdf"
    assert notes == ["doc.pdf: signed by Jane Tester on page 2"]
    signed = tmp_path / name
    signed.write_bytes(data)

    (tmp_path / "w2").mkdir()
    notes = []
    name, report, ctype = gui.run_operation("verify-sig", [signed], {}, extra_files={},
                                            work=tmp_path / "w2", notes=notes)
    assert name == "doc_signed_signatures.txt" and ctype.startswith("text/plain")
    assert b"Jane Tester" in report and b"Reason: Approved" in report
    assert len(notes) == 1 and "identity isn't verified" in notes[0]

    (tmp_path / "w3").mkdir()
    with pytest.raises(gui.UserError, match="digital ID"):
        gui.run_operation("sign-cert", [doc], {}, extra_files={}, work=tmp_path / "w3", notes=[])


def test_gui_signing_keeps_the_password_inside_the_signature(tmp_path, pfx, doc):
    locked = tmp_path / "locked.pdf"
    core.encrypt(doc, locked, "pw")
    (tmp_path / "w").mkdir()
    name, data, _ = gui.run_operation(
        "sign-cert", [locked], {"pfx_password": PFX_PASSWORD, "appearance": "invisible"}, "pw",
        extra_files={"pfx": pfx}, work=tmp_path / "w", notes=[])
    signed = tmp_path / name
    signed.write_bytes(data)
    assert PdfReader(str(signed)).is_encrypted
    assert certsign.verify(signed, "pw")[0]["status"] == "valid-untrusted"


# -- the reader's endpoints ------------------------------------------------------------

@pytest.fixture
def app(tmp_path, monkeypatch):
    from pdfmanager import appdata, recipes

    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(gui, "data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(recipes, "data_dir", lambda: tmp_path / "appdata")
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    yield base, token
    srv.shutdown()
    srv.server_close()


def call(base, token, path, data, headers):
    request = urllib.request.Request(base + path, data=data,
                                     headers={"X-Pdfm-Token": token, **headers})
    try:
        with urllib.request.urlopen(request, timeout=30) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def multipart(fields, files):
    boundary = "pdfmcertboundary"
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    for name, path in files:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                     f'filename="{Path(path).name}"\r\nContent-Type: application/octet-stream'
                     f'\r\n\r\n'.encode() + Path(path).read_bytes() + b"\r\n")
    return (b"".join(parts) + f"--{boundary}--\r\n".encode(),
            {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def test_sign_and_verify_endpoints(app, tmp_path, pfx, doc):
    base, token = app
    body, headers = multipart({"pfx_password": PFX_PASSWORD, "password": "", "page": "1",
                               "box": json.dumps([100, 100, 300, 160]), "reason": "Seen",
                               "location": "Paris", "timestamp_url": ""},
                              [("file", doc), ("pfx", pfx)])
    status, answer, data = call(base, token, "/api/sign/cert", body, headers)
    assert status == 200 and answer["Content-Type"] == "application/pdf"
    signed_info = json.loads(answer["X-Pdfm-Signed"])
    assert signed_info["signer"] == "Jane Tester" and signed_info["page"] == 1
    assert signed_info["box"] == [100, 100, 300, 160] and signed_info["field"] == "Signature1"
    signed = tmp_path / "signed.pdf"
    signed.write_bytes(data)

    body, headers = multipart({"password": ""}, [("file", signed)])
    status, answer, data = call(base, token, "/api/sign/verify", body, headers)
    assert status == 200
    (sig,) = json.loads(data)["signatures"]
    assert sig["status"] == "valid-untrusted" and sig["reason"] == "Seen"
    assert sig["page"] == 1 and sig["box"] == [100, 100, 300, 160]

    body, headers = multipart({"password": ""}, [("file", doc)])
    assert json.loads(call(base, token, "/api/sign/verify", body, headers)[2]) == {
        "signatures": []}


def test_sign_endpoint_errors(app, tmp_path, pfx, doc):
    base, token = app
    body, headers = multipart({"pfx_password": "wrong"}, [("file", doc), ("pfx", pfx)])
    status, _, data = call(base, token, "/api/sign/cert", body, headers)
    assert status == 400 and json.loads(data) == {"error": "wrong password for the digital ID",
                                                  "code": "pfx_password"}
    body, headers = multipart({"pfx_password": PFX_PASSWORD}, [("file", doc)])
    status, _, data = call(base, token, "/api/sign/cert", body, headers)
    assert status == 400 and "digital ID" in json.loads(data)["error"]
    body, headers = multipart({"pfx_password": PFX_PASSWORD, "page": "9",
                               "box": "[1, 2, 300, 400]"}, [("file", doc), ("pfx", pfx)])
    status, _, data = call(base, token, "/api/sign/cert", body, headers)
    assert status == 400 and "page 9" in json.loads(data)["error"]
    # A page without a box: the bottom right corner.
    body, headers = multipart({"pfx_password": PFX_PASSWORD, "page": "2"},
                              [("file", doc), ("pfx", pfx)])
    status, answer, _ = call(base, token, "/api/sign/cert", body, headers)
    assert status == 200 and json.loads(answer["X-Pdfm-Signed"])["page"] == 2


# -- the command line ------------------------------------------------------------------

def test_cli_sign_and_verify(tmp_path, pfx, doc, capsys, monkeypatch):
    monkeypatch.delenv("PDFM_PFX_PASSWORD", raising=False)
    assert main(["sign-cert", str(doc), "--pfx", str(pfx), "--pfx-password", PFX_PASSWORD,
                 "--page", "last", "--reason", "Approved", "--location", "Rome"]) == 0
    signed = tmp_path / "doc_signed.pdf"
    assert "signed by Jane Tester, on page 2" in capsys.readouterr().out
    assert main(["verify", str(signed)]) == 0
    out = capsys.readouterr().out
    assert "valid, but the signer's identity isn't verified" in out and "Reason: Approved" in out

    monkeypatch.setenv("PDFM_PFX_PASSWORD", PFX_PASSWORD)
    boxed = tmp_path / "boxed.pdf"
    assert main(["sign-cert", str(signed), "--pfx", str(pfx), "--page", "1",
                 "--box", "72,72,272,132", "-o", str(boxed)]) == 0
    assert main(["verify", "--json", str(boxed)]) == 0
    (record,) = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert [s["status"] for s in record["signatures"]] == ["valid-untrusted"] * 2
    assert record["signatures"][1]["box"] == [72, 72, 272, 132]

    # Unsigned or changed files: exit code 1.
    assert main(["verify", str(doc)]) == 1
    assert "no digital signatures" in capsys.readouterr().out
    rewritten = tmp_path / "rewritten.pdf"
    PdfWriter(clone_from=str(signed)).write(str(rewritten))
    assert main(["verify", str(rewritten), str(signed)]) == 1
    assert "invalid" in capsys.readouterr().out

    monkeypatch.delenv("PDFM_PFX_PASSWORD")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    assert main(["sign-cert", str(doc), "--pfx", str(pfx), "-o", str(tmp_path / "x.pdf")]) == 1
    assert "pass --pfx-password" in capsys.readouterr().err
    assert main(["sign-cert", str(doc), "--pfx", str(pfx), "--pfx-password", "bad",
                 "-o", str(tmp_path / "x.pdf")]) == 1
    assert "wrong password" in capsys.readouterr().err
    assert not (tmp_path / "x.pdf").exists()


def test_cli_signing_keeps_a_protected_file_protected(tmp_path, pfx, doc, capsys):
    locked = tmp_path / "locked.pdf"
    core.encrypt(doc, locked, "pw")
    assert main(["sign-cert", str(locked), "--pfx", str(pfx), "--pfx-password", PFX_PASSWORD,
                 "--password", "pw"]) == 0
    signed = tmp_path / "locked_signed.pdf"
    assert PdfReader(str(signed)).is_encrypted
    assert main(["verify", str(signed), "--password", "pw"]) == 0


def test_annotation_added_later_by_another_program(tmp_path, pfx, doc):
    """pyHanko judges changes made after signing strictly: a note added by a
    program that rewrites the page counts as a change the signature doesn't
    allow (the signed part itself is still intact)."""
    out = tmp_path / "signed.pdf"
    certsign.sign(doc, out, pfx, PFX_PASSWORD)
    writer = PdfWriter(str(out), incremental=True)
    writer.add_annotation(0, Text(rect=(50, 550, 200, 650), text="Hello"))
    noted = tmp_path / "noted.pdf"
    writer.write(str(noted))
    (sig,) = certsign.verify(noted)
    assert sig["intact"] and sig["status"] in ("modified", "valid-untrusted")


def test_file_with_a_broken_cross_reference_table_can_be_signed(tmp_path, pfx, doc):
    import re

    broken = tmp_path / "broken.pdf"
    broken.write_bytes(re.sub(rb"startxref\s+\d+", b"startxref\n123", doc.read_bytes()))
    out = tmp_path / "signed.pdf"
    certsign.sign(broken, out, pfx, PFX_PASSWORD, page=1, box=[50, 50, 250, 110])
    assert [s["status"] for s in certsign.verify(out)] == ["valid-untrusted"]
