# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for Priority #9 Phase 6: PAdES levels, DocMDP certification, FieldMDP locking,
detached CMS/CAdES (.p7s), and ETSI XML/PDF validation reports."""

import datetime as dt
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from reportlab.pdfgen import canvas

from pdfmanager import certsign, core
from pdfmanager.server import operations


def _make_pdf(path, text="Enterprise contract v1"):
    c = canvas.Canvas(str(path), pagesize=(595, 842))
    c.setFont("Helvetica", 12)
    c.drawString(72, 760, text)
    c.showPage()
    c.save()


def _make_test_id(tmp_path, cn="Alice Auditor", password="secret"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = dt.datetime.now(dt.timezone.utc)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ZPDF Test Org"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=True, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    pfx_bytes = pkcs12.serialize_key_and_certificates(
        cn.encode("utf-8"),
        key,
        cert,
        None,
        serialization.BestAvailableEncryption(password.encode("utf-8")),
    )
    pfx_path = tmp_path / "test-id.pfx"
    pfx_path.write_bytes(pfx_bytes)
    cert_path = tmp_path / "test-root.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return pfx_path, cert_path, password


def test_pades_levels_validation_and_docmdp_certification(tmp_path):
    src = tmp_path / "contract.pdf"
    _make_pdf(src)
    pfx, cert_pem, pwd = _make_test_id(tmp_path)

    # PAdES B-T / B-LT / B-LTA require a timestamp server URL
    for level in ("B-T", "B-LT", "B-LTA"):
        with pytest.raises(core.PdfManagerError, match="timestamp"):
            certsign.sign(src, tmp_path / f"fail_{level}.pdf", pfx, pwd, pades_level=level)

    # Certifying signature: no-changes (DocMDP P=1)
    certified_no_changes = tmp_path / "certified_p1.pdf"
    info = certsign.sign(
        src, certified_no_changes, pfx, pwd,
        pades_level="B-B", certify="no-changes", reason="Final release",
    )
    assert info["pades_level"] == "B-B"
    assert info["certify"] == "no-changes"
    assert certsign.docmdp_permissions(certified_no_changes) == "no-changes"
    with pytest.raises(core.PdfManagerError, match="no changes allowed"):
        certsign.check_edit_allowed(certified_no_changes, action="edit")

    # Certifying signature: fill-and-sign (DocMDP P=2)
    certified_fill = tmp_path / "certified_p2.pdf"
    certsign.sign(src, certified_fill, pfx, pwd, certify="fill-and-sign")
    assert certsign.docmdp_permissions(certified_fill) == "fill-and-sign"
    assert certsign.check_edit_allowed(certified_fill, action="fill") is True
    with pytest.raises(core.PdfManagerError, match="form filling and signing"):
        certsign.check_edit_allowed(certified_fill, action="comment")

    # Signature verifies cleanly against the root cert
    results = certsign.verify(certified_no_changes, trusted=[cert_pem])
    assert certsign.all_valid(results)


def test_fieldmdp_locking_records_lock_dictionary(tmp_path):
    src = tmp_path / "form.pdf"
    _make_pdf(src)
    pfx, _, pwd = _make_test_id(tmp_path)

    signed_all = tmp_path / "locked_all.pdf"
    info_all = certsign.sign(src, signed_all, pfx, pwd, lock_fields="all")
    assert info_all["locked_fields"] == ["*"]
    locks = certsign.locked_fields(signed_all)
    assert info_all["field"] in locks
    assert locks[info_all["field"]]["action"] == "All"

    signed_some = tmp_path / "locked_some.pdf"
    info_some = certsign.sign(src, signed_some, pfx, pwd, lock_fields=["Amount", "Vendor"])
    assert info_some["locked_fields"] == ["Amount", "Vendor"]
    locks2 = certsign.locked_fields(signed_some)
    assert locks2[info_some["field"]]["action"] == "Include"
    assert locks2[info_some["field"]]["fields"] == ["Amount", "Vendor"]


def test_detached_cms_p7s_sign_verify_and_tamper_detection(tmp_path):
    doc = tmp_path / "report.pdf"
    _make_pdf(doc, "Quarterly statement $12,345")
    pfx, cert_pem, pwd = _make_test_id(tmp_path)
    p7s = tmp_path / "report.pdf.p7s"

    signed = certsign.sign_detached(doc, p7s, pfx, pwd)
    assert Path(signed["output"]).is_file()
    assert signed["algorithm"] == "SHA-256"

    # Untrusted without root cert, but intact
    res_untrusted = certsign.verify_detached(doc, p7s)
    assert res_untrusted["intact"] is True
    assert res_untrusted["status"] == certsign.UNTRUSTED
    assert res_untrusted["signer"] == "Alice Auditor"

    # Valid + trusted with root cert
    res_trusted = certsign.verify_detached(doc, p7s, trusted=[cert_pem])
    assert res_trusted["intact"] is True
    assert res_trusted["trusted"] is True
    assert res_trusted["status"] == certsign.VALID

    # Tampering with file bytes is detected as MODIFIED
    doc.write_bytes(doc.read_bytes() + b"\n%tampered")
    res_tampered = certsign.verify_detached(doc, p7s, trusted=[cert_pem])
    assert res_tampered["intact"] is False
    assert res_tampered["status"] == certsign.MODIFIED


def test_validation_reports_xml_and_pdf_and_operations(tmp_path):
    src = tmp_path / "signed.pdf"
    raw = tmp_path / "raw.pdf"
    _make_pdf(raw)
    pfx, cert_pem, pwd = _make_test_id(tmp_path)
    certsign.sign(raw, src, pfx, pwd, reason="Approved")

    results = certsign.verify(src, trusted=[cert_pem])
    xml_out = tmp_path / "report.xml"
    certsign.export_validation_xml(results, xml_out, name=src.name)
    root = ET.fromstring(xml_out.read_text(encoding="utf-8"))
    assert root.tag.endswith("ValidationReport")
    assert "TOTAL-PASSED" in xml_out.read_text(encoding="utf-8")

    pdf_out = tmp_path / "report.pdf"
    certsign.export_validation_pdf(results, pdf_out, name=src.name)
    assert pdf_out.read_bytes().startswith(b"%PDF")

    # End-to-end through GUI operations.run_operation('verify-sig')
    work = tmp_path / "work"
    work.mkdir()
    fname, data, _ = operations.run_operation(
        "verify-sig", [src], {"report_format": "xml"},
        extra_files={"trust": cert_pem}, work=work,
    )
    assert fname.endswith(".xml")
    assert b"TOTAL-PASSED" in data
