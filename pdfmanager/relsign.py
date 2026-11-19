# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Ed25519 signing and verification for published ZPDF releases.

A release consists of ``ZPDF-<version>-windows.zip`` and its checksum file
``ZPDF-<version>-windows.zip.sha256``. Signing the checksum file with the
owner's Ed25519 private key (producing ``ZPDF-<version>-windows.zip.sig``)
cryptographically authenticates both the zip's SHA-256 digest and its filename.

When ``RELEASE_PUBLIC_KEY`` is set, ``pdfmanager/update.py`` requires every
published release to carry a valid signature matching that key and refuses
unsigned or tampered downloads.
"""

import base64
import hashlib
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

# Built-in Ed25519 public key (PEM) for verifying published releases.
# Populate this with the public key printed by `python packaging/release.py --keygen`
# and keep the matching private key in the owner's possession (or the
# RELEASE_SIGNING_KEY Actions secret), never in the repository.
RELEASE_PUBLIC_KEY = ""

SIGNATURE_HEADER = "zpdf-ed25519-v1"
_DOMAIN = b"zpdf-release-v1\n"


def generate_keypair():
    """Generate a new Ed25519 (private_pem, public_pem) pair for release signing."""
    priv = Ed25519PrivateKey.generate()
    private_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return private_pem, public_pem


def load_private_key(key_or_path):
    """Load an Ed25519 private key from a PEM string/bytes or a path to a PEM file."""
    if isinstance(key_or_path, Ed25519PrivateKey):
        return key_or_path
    if isinstance(key_or_path, Path):
        raw = key_or_path.read_bytes()
    elif isinstance(key_or_path, bytes):
        raw = key_or_path
    elif isinstance(key_or_path, str):
        text = key_or_path.strip()
        if text.startswith("-----BEGIN "):
            raw = text.encode("ascii")
        else:
            raw = Path(text).read_bytes()
    else:
        raise ValueError("unsupported signing key input")
    key = serialization.load_pem_private_key(raw, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("release signing key must be an Ed25519 private key")
    return key


def load_public_key(public_key_pem=None):
    """Load an Ed25519 public key from PEM text (defaults to ``RELEASE_PUBLIC_KEY``)."""
    pem = RELEASE_PUBLIC_KEY if public_key_pem is None else public_key_pem
    if isinstance(pem, Ed25519PublicKey):
        return pem
    text = (pem or "").strip()
    if not text:
        return None
    key = serialization.load_pem_public_key(text.encode("ascii"))
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("release public key must be an Ed25519 public key")
    return key


def sign_checksum(sha256_bytes, private_key):
    """Sign ``sha256_bytes`` with ``private_key`` and return the ``.sig`` text."""
    key = load_private_key(private_key)
    sig = key.sign(_DOMAIN + bytes(sha256_bytes))
    encoded = base64.b64encode(sig).decode("ascii")
    return f"{SIGNATURE_HEADER}\n{encoded}\n"


def verify_checksum(sha256_bytes, signature_text, public_key_pem=None):
    """Verify ``signature_text`` over ``sha256_bytes``. Raises ``ValueError`` on failure."""
    pub = load_public_key(public_key_pem)
    if pub is None:
        raise ValueError("no release public key is configured")
    text = signature_text.decode("ascii", "replace") if isinstance(signature_text, (bytes, bytearray)) else str(signature_text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) != 2 or lines[0] != SIGNATURE_HEADER:
        raise ValueError("release signature has an unrecognised format")
    try:
        raw_sig = base64.b64decode(lines[1], validate=True)
    except Exception as exc:
        raise ValueError("release signature is not valid base64") from exc
    try:
        pub.verify(raw_sig, _DOMAIN + bytes(sha256_bytes))
    except InvalidSignature as exc:
        raise ValueError("release signature does not match the built-in signing key") from exc


def verify_release(zip_bytes, sha256_bytes, sig_bytes=None, public_key_pem=None, version=""):
    """Verify a downloaded release zip against its SHA-256 checksum and, when a
    public key is configured, its Ed25519 signature. Raises ``ValueError`` on
    any mismatch or missing/invalid signature."""
    label = f"version {version}" if version else "the download"
    published = sha256_bytes.decode("ascii", "replace").split()
    if not published or hashlib.sha256(zip_bytes).hexdigest() != published[0].lower():
        raise ValueError(f"the download of {label} came out damaged (its checksum doesn't match); try again")
    pub = load_public_key(public_key_pem)
    if pub is not None:
        if not sig_bytes:
            raise ValueError(f"the release of {label} has no signature; refusing to install an unsigned update")
        try:
            verify_checksum(sha256_bytes, sig_bytes, public_key_pem=pub)
        except ValueError as exc:
            raise ValueError(f"the release of {label} failed signature verification ({exc}); refusing to install") from None
