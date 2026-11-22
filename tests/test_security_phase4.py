# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Phase 4 Security and Reliability Audit regression tests (S-001 through S-006)."""
import datetime
import os
import stat
import sys
from pathlib import Path

import pikepdf
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from pdfmanager import appdata, applog, certsign, core, privacy, redact


def _make_pdf(path, text="Confidential report"):
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, text)
    c.showPage()
    c.save()
    return path


def _make_pfx(path, password="pfx-secret-password", expired=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Signer")])
    now = datetime.datetime.now(datetime.timezone.utc)
    if expired:
        not_before = now - datetime.timedelta(days=30)
        not_after = now - datetime.timedelta(days=1)
    else:
        not_before = now - datetime.timedelta(days=1)
        not_after = now + datetime.timedelta(days=365)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pfx_bytes = pkcs12.serialize_key_and_certificates(
        b"signer", key, cert, None, serialization.BestAvailableEncryption(password.encode("utf-8"))
    )
    path.write_bytes(pfx_bytes)
    cert_path = path.with_suffix(".pem")
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return path, cert_path


# ---- S-001: Encryption and password handling -------------------------------------------

def test_s001_encrypt_decrypt_wrong_password_never_leaks_secret(tmp_path):
    src = _make_pdf(tmp_path / "plain.pdf")
    enc = tmp_path / "enc.pdf"
    dec = tmp_path / "dec.pdf"
    secret = "UltraSecretPass!99"
    wrong = "WrongAttempt!77"

    core.encrypt(src, enc, user_password=secret)
    assert PdfReader(str(enc)).is_encrypted

    # Wrong password raises PdfManagerError and never echoes the attempted password
    with pytest.raises(core.PdfManagerError) as exc_info:
        core.decrypt(enc, dec, password=wrong)
    assert wrong not in str(exc_info.value)
    assert secret not in str(exc_info.value)
    assert not dec.exists()

    # Empty password rejected when encrypting without restrictions
    with pytest.raises(core.PdfManagerError):
        core.encrypt(src, tmp_path / "empty.pdf", user_password="")
    assert not (tmp_path / "empty.pdf").exists()

    # Correct password decrypts cleanly
    core.decrypt(enc, dec, password=secret)
    assert not PdfReader(str(dec)).is_encrypted


# ---- S-002: Digital signature validity vs. trust, expiration & modification ------------

def test_s002_signature_distinguishes_valid_untrusted_trusted_modified_and_expired(tmp_path):
    src = _make_pdf(tmp_path / "doc.pdf")
    pfx, cert_pem = _make_pfx(tmp_path / "good.pfx", password="my-pfx-password", expired=False)
    signed = tmp_path / "signed.pdf"

    # Wrong PFX password fails without echoing password
    with pytest.raises(core.PdfManagerError) as exc_info:
        certsign.sign(src, signed, pfx, pfx_password="wrong-pfx-password")
    assert "wrong-pfx-password" not in str(exc_info.value)

    # Expired digital ID refused when signing
    exp_pfx, _ = _make_pfx(tmp_path / "expired.pfx", password="exp-password", expired=True)
    with pytest.raises(core.PdfManagerError, match="expired"):
        certsign.sign(src, signed, exp_pfx, pfx_password="exp-password")

    # Valid signature without trusted root -> valid-untrusted (NOT valid/trusted!)
    certsign.sign(src, signed, pfx, pfx_password="my-pfx-password")
    untrusted_res = certsign.verify(signed)
    assert len(untrusted_res) == 1
    assert untrusted_res[0]["intact"] is True
    assert untrusted_res[0]["trusted"] is False
    assert untrusted_res[0]["status"] == certsign.UNTRUSTED

    # With explicit trusted root -> valid & trusted
    trusted_res = certsign.verify(signed, trusted=[cert_pem])
    assert trusted_res[0]["intact"] is True
    assert trusted_res[0]["trusted"] is True
    assert trusted_res[0]["status"] == certsign.VALID

    # Tampering with signed bytes -> invalid / modified (never valid)
    tampered = tmp_path / "tampered.pdf"
    raw = bytearray(signed.read_bytes())
    idx = raw.find(b"Confidential")
    if idx != -1:
        raw[idx : idx + 4] = b"XXXX"
    else:
        raw[len(raw) // 2] ^= 0xFF
    tampered.write_bytes(bytes(raw))
    tampered_res = certsign.verify(tampered, trusted=[cert_pem])
    assert not tampered_res or tampered_res[0]["status"] != certsign.VALID


# ---- S-003: Attachment extraction path traversal, reserved names & size limits ---------

def test_s003_safe_filename_blocks_traversal_reserved_names_and_null_bytes():
    assert core._safe_filename("../../etc/passwd", "att") == "passwd"
    assert core._safe_filename(r"..\..\..\Windows\System32\evil.bat", "att") == "evil.bat"
    assert core._safe_filename("/abs/path/secret.txt", "att") == "secret.txt"
    assert core._safe_filename("CON.txt", "att") == "_CON.txt"
    assert core._safe_filename("nul", "att") == "_nul"
    assert core._safe_filename("COM1.pdf", "att") == "_COM1.pdf"
    assert core._safe_filename("bad\x00name.txt", "att") == "badname.txt"
    assert core._safe_filename("..", "att") == "att"


def test_s003_extract_media_enforces_containment_dedup_and_size_limit(tmp_path):
    src = _make_pdf(tmp_path / "base.pdf")
    w = PdfWriter(clone_from=str(src))
    w.add_attachment("../../escape.txt", b"small payload 1")
    w.add_attachment("CON.txt", b"small payload 2")
    w.add_attachment("huge.bin", b"X" * 500)
    att_pdf = tmp_path / "with_att.pdf"
    w.write(str(att_pdf))

    out_dir = tmp_path / "extracted"
    saved = core.extract_media(att_pdf, out_dir, images=False, attachments=True, max_attachment_bytes=200)
    att_root = (out_dir / "attachments").resolve()
    assert len(saved["attachments"]) == 2
    for p in saved["attachments"]:
        assert att_root in p.resolve().parents
    names = {p.name for p in saved["attachments"]}
    assert "escape.txt" in names and "_CON.txt" in names
    assert any("huge.bin" in s and "size limit" in s for s in saved.get("skipped", []))
    assert not (tmp_path / "escape.txt").exists()


# ---- S-004: Malformed & hostile PDFs ---------------------------------------------------

def test_s004_corrupted_pdf_and_recursive_form_xobject_fail_safely(tmp_path):
    # 1. Corrupted PDF raises PdfManagerError cleanly
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"%PDF-1.7\nnot a valid pdf body\n%%EOF\n")
    with pytest.raises(core.PdfManagerError):
        core.open_pdf(bad)

    # 2. Self-referential Form XObject in redaction falls back to rasterization (no RecursionError)
    rec_pdf = tmp_path / "recursive.pdf"
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    form = pikepdf.Stream(pdf, b"/Fm0 Do")
    form.Type = pikepdf.Name.XObject
    form.Subtype = pikepdf.Name.Form
    form.BBox = pikepdf.Array([0, 0, 612, 792])
    form_ref = pdf.make_indirect(form)
    form.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm0=form_ref))
    page.obj.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm0=form_ref))
    page.obj.Contents = pdf.make_stream(b"/Fm0 Do")
    pdf.save(str(rec_pdf))

    out = tmp_path / "rec_out.pdf"
    report = redact.redact(rec_pdf, out, boxes={0: [(50, 50, 200, 200)]})
    assert 1 in report.pictures and out.is_file()


# ---- S-005: Temporary file cleanup and private file permissions ------------------------

def test_s005_atomic_write_cleanup_and_private_permissions(tmp_path):
    target = tmp_path / "out.pdf"

    class FailingWriter:
        def write(self, fh):
            fh.write(b"%PDF-partial")
            raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        core._write(FailingWriter(), target)
    assert not target.exists()
    assert list(tmp_path.glob("*.tmp")) == []

    # write_private sets owner-only permissions on POSIX
    secret_file = tmp_path / "settings.json"
    appdata.write_private(secret_file, '{"token": "abc"}')
    assert secret_file.is_file()
    if sys.platform != "win32":
        mode = stat.S_IMODE(os.stat(secret_file).st_mode)
        assert mode == 0o600


# ---- S-006: Logging secret scrubbing ---------------------------------------------------

def test_s006_applog_scrubs_passwords_tokens_and_private_keys():
    raw = (
        "Failed auth: password=SuperSecret123, pfx_password='MyPfxPass', "
        '{"user_password": "QuotedSecret"} '
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abcdef123456 "
        "ghp_1234567890abcdef1234567890abcdef "
        "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEF\n-----END PRIVATE KEY-----"
    )
    cleaned = applog.scrub(raw)
    for leaked in (
        "SuperSecret123",
        "MyPfxPass",
        "QuotedSecret",
        "eyJhbGciOiJIUzI1NiJ9",
        "ghp_1234567890abcdef",
        "MIIEvgIBADANBgkqhkiG9w0BAQEF",
    ):
        assert leaked not in cleaned
    assert "<private-key>" in cleaned
    assert "<token>" in cleaned
    assert "<redacted>" in cleaned
