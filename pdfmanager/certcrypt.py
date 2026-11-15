# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""A PDF protected for people instead of with a password.

``encrypt_for`` encrypts a PDF for certificates: the public half of each
person's Digital ID, which they can hand out freely (a .cer file). Only
someone holding one of the matching Digital IDs (.pfx) can open the
result, so there's no password to send along or to be guessed. It's the
"certificate security" of Acrobat, which opens these files too.

``open_with_id`` opens such a file with a Digital ID and writes an
unprotected copy, which the rest of ZPDF then works on.

The encryption is AES-256 (pyHanko's public-key security handler).
"""

import io
from pathlib import Path

from pdfmanager import core

from pdfmanager.forpeople import FOR_PEOPLE, NOT_READABLE, is_for_people  # noqa: F401 - part of this module's face

PEM_START, PEM_END = b"-----BEGIN CERTIFICATE-----", b"-----END CERTIFICATE-----"


def _need_pyhanko():
    try:
        import pyhanko.pdf_utils.crypt  # noqa: F401
    except ImportError:
        raise core.PdfManagerError(
            "protecting a PDF for people's certificates needs pyHanko: pip install pyhanko") from None


def _name(cert):
    """Who a certificate is for: its common name, else its whole subject."""
    try:
        return cert.subject.native.get("common_name") or cert.subject.human_friendly
    except Exception:  # noqa: BLE001
        return "someone"


def _certificates(source, label):
    """The certificates in one file's bytes: PEM (one or several), DER, or
    a Digital ID (.pfx: its own certificate, without needing its password
    when the file lets it be read; else an error says to export the .cer)."""
    from asn1crypto import pem, x509

    found = []
    if PEM_START in source:
        for _, _, der in pem.unarmor(source, multiple=True):
            found.append(x509.Certificate.load(der))
    else:
        try:
            cert = x509.Certificate.load(source)
            cert.native  # noqa: B018 - parsed now, so a file that isn't one fails here
            found.append(cert)
        except Exception:  # noqa: BLE001
            found = []
    if not found:
        raise core.PdfManagerError(
            f"{label} isn't a certificate: use the .cer (or .crt, .pem) file the person gave you, "
            "or exported from their Digital ID")
    return found


def _own_certificate(pfx, pfx_password, label):
    """The certificate of a Digital ID, to protect a file for its holder too."""
    from asn1crypto import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12

    for password in ([pfx_password.encode()] if pfx_password else []) + [None, b""]:
        try:
            _, cert, _ = pkcs12.load_key_and_certificates(pfx, password)
        except Exception:  # noqa: BLE001 - the wrong password, tried next
            continue
        if cert is not None:
            return x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))
    raise core.PdfManagerError(
        f"{label} is a Digital ID that needs its password to be read: give its certificate (.cer) "
        "instead, or its password")


def _usable(cert, now=None):
    """Raise if a certificate can't be encrypted for: expired, or made for
    signing only."""
    import datetime as dt

    name = _name(cert)
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        if cert.not_valid_after < now:
            raise core.PdfManagerError(
                f"{name}'s certificate expired on {cert.not_valid_after:%d %b %Y}: ask them for their current one")
        if cert.not_valid_before > now:
            raise core.PdfManagerError(f"{name}'s certificate isn't valid yet (from {cert.not_valid_before:%d %b %Y})")
    except (TypeError, AttributeError):
        pass
    usage = cert.key_usage_value
    if usage is not None and not ({"key_encipherment", "key_agreement"} & set(usage.native)):
        raise core.PdfManagerError(
            f"{name}'s certificate is only for signing, not for protecting files: ask them for one "
            "that allows encryption (many Digital IDs have a second certificate for it)")


def encrypt_for(path, output, certificates, password=None, id_password=None):
    """Write the PDF encrypted for ``certificates``: files (or their bytes)
    holding one or more certificates (.cer, .crt, .pem, .der), or a Digital
    ID (.pfx, .p12), which stands for its own certificate (``id_password``
    if it needs one): add yours, or you won't be able to open the result
    yourself. ``password`` opens the source if it's password-protected; the
    result has no password. Returns the names of the people it's for."""
    _need_pyhanko()
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.pdf_utils.writer import copy_into_new_writer

    from pdfmanager import certsign

    reader = core.open_pdf(path, password)
    chosen, seen = [], set()
    for number, source in enumerate(certificates or [], 1):
        if isinstance(source, (bytes, bytearray, memoryview)):
            data, label = bytes(source), f"file {number}"
        else:
            try:
                data = Path(source).read_bytes()
            except OSError:
                raise core.PdfManagerError(f"certificate not found: {source}") from None
            label = Path(source).name
        certs = [_own_certificate(data, id_password, label)] if certsign._is_pfx(data) else _certificates(data, label)
        for cert in certs:
            if cert.sha256 not in seen:
                seen.add(cert.sha256)
                chosen.append(cert)
    if not chosen:
        raise core.PdfManagerError("choose at least one certificate: the people who may open the file "
                                   "(and yours, to open it yourself)")
    for cert in chosen:
        _usable(cert)

    plain = io.BytesIO()
    core.clone_document(reader).write(plain)  # (without its password, if it had one)
    plain.seek(0)
    try:
        writer = copy_into_new_writer(PdfFileReader(plain, strict=False))
        # (What each certificate may be used for was checked above, in plain
        # words; one that states no use at all is taken, as Acrobat takes it.)
        from pyhanko.pdf_utils.crypt.pubkey import RecipientEncryptionPolicy

        writer.encrypt_pubkey(chosen, policy=RecipientEncryptionPolicy(ignore_key_usage=True))
        protected = io.BytesIO()
        writer.write(protected)
    except core.PdfManagerError:
        raise
    except Exception as exc:  # noqa: BLE001 - pyHanko's own errors, said plainly
        raise core.PdfManagerError(f"{Path(path).name} couldn't be protected for these certificates ({exc})") from None
    core._write(core._Bytes(protected.getvalue()), output)
    return [_name(cert) for cert in chosen]


def open_with_id(path, output, pfx, pfx_password=None):
    """Open a PDF protected for people with the Digital ID ``pfx`` (a .pfx
    or .p12 file, or its bytes) and write an unprotected copy. Returns what
    was done, in words."""
    _need_pyhanko()
    from pyhanko.pdf_utils.crypt import AuthStatus, SimpleEnvelopeKeyDecrypter
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.pdf_utils.writer import copy_into_new_writer

    from pdfmanager import certsign

    name = Path(path).name
    if not is_for_people(path):
        raise core.PdfManagerError(f"{name} isn't protected for particular people: there's nothing for a "
                                   "Digital ID to open (a password-protected file takes Remove password)")
    data = bytes(pfx) if isinstance(pfx, (bytes, bytearray, memoryview)) else None
    if data is None:
        try:
            data = Path(pfx).read_bytes()
        except OSError:
            raise core.PdfManagerError(f"digital ID not found: {pfx}") from None
    # (Read as the signing tool reads it: the same plain words for a wrong
    # password or a file that isn't a Digital ID. An expired ID still opens
    # what was protected for it.)
    try:
        _, info = certsign.load_id(data if isinstance(pfx, (bytes, bytearray, memoryview)) else pfx, pfx_password)
        holder = info.get("signer") or "this Digital ID"
    except core.PdfManagerError as exc:
        if "expired" not in str(exc):
            raise
        holder = "this Digital ID"
    from cryptography.hazmat.primitives.serialization import pkcs12

    try:
        key, cert, _ = pkcs12.load_key_and_certificates(data, pfx_password.encode() if pfx_password else None)
    except Exception:  # noqa: BLE001
        raise core.PdfManagerError("the Digital ID's password is missing or wrong") from None
    if key is None or cert is None:
        raise core.PdfManagerError("that Digital ID has no private key: it can't open anything")
    from asn1crypto import keys, x509
    from cryptography.hazmat.primitives import serialization

    credential = SimpleEnvelopeKeyDecrypter(
        cert=x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER)),
        private_key=keys.PrivateKeyInfo.load(key.private_bytes(
            serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())))
    try:
        reader = PdfFileReader(io.BytesIO(Path(path).read_bytes()), strict=False)
        result = reader.decrypt_pubkey(credential)
        if result.status == AuthStatus.FAILED:
            raise core.PdfManagerError(
                f"{name} wasn't protected for {holder}: it opens only with the Digital ID of one of "
                "the people it was made for")
        plain = io.BytesIO()
        copy_into_new_writer(reader).write(plain)
    except core.PdfManagerError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise core.PdfManagerError(f"{name} couldn't be opened with that Digital ID ({exc})") from None
    core._write(core._Bytes(plain.getvalue()), output)
    return f"opened with the Digital ID of {holder}; this copy has no protection"
