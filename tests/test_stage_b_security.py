# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Stage B regression tests: SEC-001 (policy enforcement), SEC-002 (secprofile bypass),
SEC-003 (format-aware attachment verification), SIGN-001, and Phase 6 (update/relsign)."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from pdfmanager import (
    certsign,
    cli,
    core,
    escl,
    i18n,
    ocr,
    policy,
    redact,
    relsign,
    secprofiles,
    update,
)


def _make_pdf(path: Path, text: str = "Hello world") -> Path:
    c = canvas.Canvas(str(path))
    c.drawString(72, 720, text)
    c.save()
    return path


def _with_policy(monkeypatch, tmp_path: Path, data: dict) -> Path:
    pf = tmp_path / "policy.json"
    pf.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("ZPDF_POLICY_FILE", str(pf))
    policy.clear_cache()
    return pf


# ---- SEC-001: Administrator policy enforcement ----------------------------------------


def test_sec001_disable_network_blocks_all_network_consumers(monkeypatch, tmp_path):
    _with_policy(monkeypatch, tmp_path, {"disable_network": True})
    try:
        with pytest.raises(core.PdfManagerError, match="disable_network"):
            ocr._fetch("https://example.invalid/model.onnx", tmp_path / "m.onnx")

        with pytest.raises(core.PdfManagerError, match="disable_network"):
            i18n._http_get_bytes("https://example.invalid/pack.zip")

        parts = escl._url("escl:http://192.0.2.1/eSCL")
        with pytest.raises(escl.EsclError, match="disable_network"):
            escl._request(parts, "GET", "ScannerCapabilities")

        with pytest.raises(core.PdfManagerError, match="disabled by your organisation"):
            update._open(update._request("/repos/Tempura-446/ZPDF-releases/releases/latest"))
    finally:
        policy.clear_cache()


def test_sec001_disable_updates_blocks_check_and_install(monkeypatch, tmp_path):
    _with_policy(monkeypatch, tmp_path, {"disable_updates": True})
    try:
        res = update.check(force=True)
        assert res["available"] is False
        assert "policy" in (res["error"] or "").lower()

        with pytest.raises(core.PdfManagerError, match="policy"):
            update.install()
    finally:
        policy.clear_cache()


def test_sec001_force_security_profile_enforced(monkeypatch, tmp_path):
    _with_policy(monkeypatch, tmp_path, {"force_security_profile": "Confidential"})
    try:
        src = _make_pdf(tmp_path / "in.pdf")
        out = tmp_path / "out.pdf"
        # Requesting a weaker/different profile must be rejected
        with pytest.raises(core.PdfManagerError, match="blocked by administrator policy"):
            secprofiles.apply_profile(src, out, "Public Share")
        assert not out.exists()

        # Ad-hoc dict with different name must also be rejected
        with pytest.raises(core.PdfManagerError, match="blocked by administrator policy"):
            secprofiles.apply_profile(src, out, {"name": "Weak", "make_safe": False})
        assert not out.exists()

        # Passing None or the forced profile succeeds
        res = secprofiles.apply_profile(src, out, None)
        assert res["profile"] == "Confidential"
        assert out.is_file()
    finally:
        policy.clear_cache()


def test_sec001_require_make_safe_on_open_enforced_in_secprofile(monkeypatch, tmp_path):
    _with_policy(monkeypatch, tmp_path, {"require_make_safe_on_open": True})
    try:
        src = _make_pdf(tmp_path / "in.pdf")
        out = tmp_path / "out.pdf"
        res = secprofiles.apply_profile(src, out, {"name": "Minimal", "make_safe": False})
        assert "make_safe" in res["steps"]
    finally:
        policy.clear_cache()


def test_sec001_trust_list_paths_validation_and_certsign_merge(monkeypatch, tmp_path):
    import datetime as dt
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    trust_dir = tmp_path / "trusted"
    trust_dir.mkdir()
    outside = tmp_path / "outside.pem"
    p12 = tmp_path / "id.p12"

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = dt.datetime.now(dt.timezone.utc)
    subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Trust Tester")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subj)
        .issuer_name(subj)
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
    p12.write_bytes(
        pkcs12.serialize_key_and_certificates(
            b"tester", key, cert, None, serialization.BestAvailableEncryption(b"pw")
        )
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    good_cert = trust_dir / "corp_root.pem"
    good_cert.write_bytes(pem)
    outside.write_bytes(pem)

    # Create an escaping symlink inside trust_dir -> outside.pem
    escape_link = trust_dir / "escape.pem"
    try:
        escape_link.symlink_to(outside)
        symlink_supported = True
    except OSError:
        symlink_supported = False

    bad_policies = {
        "trust_list_paths": [
            "relative/cert.pem",
            str(trust_dir / ".." / "outside.pem"),
            str(tmp_path / "does_not_exist.pem"),
            str(trust_dir),
        ]
    }
    validated = policy.validated_trust_list_paths(policies=bad_policies)
    assert good_cert.resolve() in validated
    assert outside.resolve() not in validated
    if symlink_supported:
        assert escape_link.resolve() not in validated

    # End-to-end: certsign.verify() automatically trusts corp_root.pem via policy!
    src = _make_pdf(tmp_path / "doc.pdf")
    signed = tmp_path / "signed.pdf"
    certsign.sign(src, signed, p12, pfx_password="pw")

    _with_policy(monkeypatch, tmp_path, {"trust_list_paths": [str(trust_dir)]})
    try:
        results = certsign.verify(signed)
        assert len(results) == 1
        assert results[0]["intact"] is True
        assert results[0]["trusted"] is True
        assert results[0]["status"] == certsign.VALID
        assert results[0]["revocation_status"] == "not_checked"
    finally:
        policy.clear_cache()


def test_sec001_fips_mode_algorithm_checks(monkeypatch, tmp_path):
    _with_policy(monkeypatch, tmp_path, {"fips_mode": True})
    try:
        for bad in ("rc4", "RC4-128", "md5", "sha1", "SHA-1", "des", "3des", "aes-128-ecb"):
            with pytest.raises(policy.PolicyError):
                policy.check_fips_algorithm(bad)
        # Approved algorithms pass
        policy.check_fips_algorithm("aes-256")
        policy.check_fips_algorithm("sha256")
    finally:
        policy.clear_cache()


# ---- SEC-002: Security Profile password-removal bypass --------------------------------


def test_sec002_policy_disabled_allows_unencrypted_profile_output(monkeypatch, tmp_path):
    _with_policy(monkeypatch, tmp_path, {"disable_password_removal": False})
    try:
        plain = _make_pdf(tmp_path / "plain.pdf")
        enc = tmp_path / "enc.pdf"
        core.encrypt(plain, enc, user_password="secret")

        out = tmp_path / "out.pdf"
        secprofiles.apply_profile(enc, out, "Public Share", password="secret")
        assert out.is_file()
        assert not PdfReader(str(out)).is_encrypted
    finally:
        policy.clear_cache()


def test_sec002_policy_enabled_blocks_unencrypted_profile_output(monkeypatch, tmp_path):
    _with_policy(monkeypatch, tmp_path, {"disable_password_removal": True})
    try:
        plain = _make_pdf(tmp_path / "plain.pdf")
        enc = tmp_path / "enc.pdf"
        core.encrypt(plain, enc, user_password="secret")

        out = tmp_path / "out.pdf"
        with pytest.raises(core.PdfManagerError, match="password"):
            secprofiles.apply_profile(enc, out, "Public Share", password="secret")
        # No partial or decrypted output left at destination
        assert not out.exists()
    finally:
        policy.clear_cache()


def test_sec002_policy_enabled_allows_reencrypted_profile_output(monkeypatch, tmp_path):
    _with_policy(monkeypatch, tmp_path, {"disable_password_removal": True})
    try:
        plain = _make_pdf(tmp_path / "plain.pdf")
        enc = tmp_path / "enc.pdf"
        core.encrypt(plain, enc, user_password="secret")

        out = tmp_path / "reenc.pdf"
        res = secprofiles.apply_profile(
            enc, out, "Confidential", password="secret", user_password="newpw"
        )
        assert "encrypt" in res["steps"]
        assert PdfReader(str(out)).is_encrypted
        assert core.open_pdf(out, "newpw").is_encrypted
    finally:
        policy.clear_cache()


def test_sec002_direct_internal_decrypt_blocked_by_policy(monkeypatch, tmp_path):
    _with_policy(monkeypatch, tmp_path, {"disable_password_removal": True})
    try:
        plain = _make_pdf(tmp_path / "plain.pdf")
        enc = tmp_path / "enc.pdf"
        core.encrypt(plain, enc, user_password="secret")

        out = tmp_path / "stolen.pdf"
        with pytest.raises(core.PdfManagerError, match="password"):
            core.decrypt(enc, out, "secret", _internal=True)
        assert not out.exists()
    finally:
        policy.clear_cache()


def test_sec002_cli_sec_profile_respects_disable_password_removal(monkeypatch, tmp_path):
    _with_policy(monkeypatch, tmp_path, {"disable_password_removal": True})
    try:
        plain = _make_pdf(tmp_path / "plain.pdf")
        enc = tmp_path / "enc.pdf"
        core.encrypt(plain, enc, user_password="secret")
        out = tmp_path / "cli_out.pdf"

        rc = cli.main([
            "profile", "apply", "Public Share", str(enc),
            "-o", str(out), "--password", "secret",
        ])
        assert rc != 0
        assert not out.exists()
    finally:
        policy.clear_cache()


# ---- SEC-003: Format-aware recursive attachment verification --------------------------


def _attach_bytes(base_pdf: Path, out_pdf: Path, filename: str, payload: bytes) -> Path:
    import pikepdf

    with pikepdf.open(base_pdf) as pdf:
        pdf.attachments[filename] = payload
        pdf.save(out_pdf, compress_streams=True)
    return out_pdf


def test_sec003_detects_secret_in_embedded_pdf(tmp_path):
    marker = "ZPDF-SECRET-EMBED-PDF-991"
    base = _make_pdf(tmp_path / "base.pdf", "Public cover sheet")
    sub = _make_pdf(tmp_path / "sub.pdf", f"Confidential: {marker}")
    host = _attach_bytes(base, tmp_path / "host.pdf", "appendix.pdf", sub.read_bytes())

    res = redact.check_redacted(host, terms=[marker])
    assert res["status"] == redact.STATUS_FINDINGS
    assert res["clean"] is False
    assert "appendix.pdf" in res["attachments_checked"]
    assert any(marker in f["what"] for f in res["found"])


def test_sec003_encrypted_embedded_pdf_is_incomplete(tmp_path):
    base = _make_pdf(tmp_path / "base.pdf", "Public cover sheet")
    sub_plain = _make_pdf(tmp_path / "sub.pdf", "Secret inside encrypted PDF")
    sub_enc = tmp_path / "sub_enc.pdf"
    core.encrypt(sub_plain, sub_enc, user_password="locked")
    host = _attach_bytes(base, tmp_path / "host.pdf", "locked.pdf", sub_enc.read_bytes())

    res = redact.check_redacted(host, terms=["NONEXISTENT-MARKER"])
    assert res["status"] == redact.STATUS_INCOMPLETE
    assert res["clean"] is False
    assert any(s["name"] == "locked.pdf" for s in res["attachments_skipped"])


def test_sec003_detects_secret_in_embedded_docx_and_zip(tmp_path):
    marker = "ZPDF-SECRET-DOCX-774"
    base = _make_pdf(tmp_path / "base.pdf", "Public cover sheet")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", f"<w:document><w:t>Hidden {marker} text</w:t></w:document>")
    host_docx = _attach_bytes(base, tmp_path / "host_docx.pdf", "memo.docx", buf.getvalue())

    res = redact.check_redacted(host_docx, terms=[marker])
    assert res["status"] == redact.STATUS_FINDINGS
    assert res["clean"] is False
    assert "memo.docx" in res["attachments_checked"]
    assert any(marker in f["what"] for f in res["found"])


def test_sec003_zip_bomb_and_unsupported_binary_are_incomplete(tmp_path):
    base = _make_pdf(tmp_path / "base.pdf", "Public cover sheet")

    # High-compression-ratio payload (2 MB of zeroes compresses to ~2 KB -> >1000x ratio)
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bomb.txt", b"0" * (2 * 1024 * 1024))
    host_bomb = _attach_bytes(base, tmp_path / "host_bomb.pdf", "bomb.zip", zbuf.getvalue())

    res_bomb = redact.check_redacted(host_bomb, terms=["SOMETHING"])
    assert res_bomb["status"] == redact.STATUS_INCOMPLETE
    assert res_bomb["clean"] is False
    assert any("bomb.zip!bomb.txt" in s["name"] for s in res_bomb["attachments_skipped"])

    # Unsupported binary attachment cannot return verified_clean
    host_bin = _attach_bytes(base, tmp_path / "host_bin.pdf", "blob.bin", b"\x00\x01\x02\xffbinary")
    res_bin = redact.check_redacted(host_bin, terms=["SOMETHING"])
    assert res_bin["status"] == redact.STATUS_INCOMPLETE
    assert res_bin["clean"] is False
    assert any(s["name"] == "blob.bin" for s in res_bin["attachments_skipped"])


# ---- Phase 6: Release signing & update extraction hardening ---------------------------


def test_phase6_relsign_require_signature_rejects_empty_key():
    payload = b"fake-release-zip"
    sha = f"{hashlib.sha256(payload).hexdigest()}  ZPDF-0.62.0-windows.zip\n".encode("ascii")
    # Without require_signature, empty public key falls back to SHA-256 only
    relsign.verify_release(payload, sha, sig_bytes=None, public_key_pem="", require_signature=False)
    # With require_signature=True, empty public key must fail closed
    with pytest.raises(ValueError, match="no release public key"):
        relsign.verify_release(payload, sha, sig_bytes=None, public_key_pem="", require_signature=True)


def test_phase6_update_extract_rejects_symlinks_and_corrupt_zip(tmp_path):
    with pytest.raises(core.PdfManagerError, match="not a valid zip"):
        update._extract(b"not-a-zip", tmp_path)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zi = zipfile.ZipInfo("zpdf-1.0/evil_link")
        zi.create_system = 3  # Unix
        zi.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(zi, "/etc/passwd")
    with pytest.raises(core.PdfManagerError, match="symbolic links"):
        update._extract(buf.getvalue(), tmp_path)


def test_phase6_update_install_blocks_downgrade(monkeypatch):
    policy.clear_cache()
    monkeypatch.setattr(update, "how_installed", lambda: "pip")
    monkeypatch.setattr(update, "latest_version", lambda: ("0.0.1", "main"))
    with pytest.raises(core.PdfManagerError, match="downgrade"):
        update.install()
