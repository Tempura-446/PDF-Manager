# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Security Phase 5: Ed25519 release signing and verification (relsign, release.py, update.py)."""
import importlib.util
import io
from pathlib import Path

import pytest

from pdfmanager import core, relsign, update

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def release():
    spec = importlib.util.spec_from_file_location("zpdf_release_sig", ROOT / "packaging" / "release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_made_by_release_py_verifies_with_builtin_key(release, tmp_path):
    priv_pem, pub_pem = relsign.generate_keypair()
    key_file = tmp_path / "release.pem"
    key_file.write_text(priv_pem, encoding="ascii")

    changelog = "# Changelog\n\n## 9.9.9\n\n- Signed release test.\n"
    files = release.publish(ROOT, "9.9.9", changelog, out_dir=tmp_path / "out",
                            checks=False, dry_run=True, sign_key=key_file)
    zip_path, sha_path, sig_path, notes_path = files
    assert sig_path.name == "ZPDF-9.9.9-windows.zip.sig"
    assert notes_path.name == "ZPDF-9.9.9-notes.md"

    # Verifies cleanly with the matching public key.
    relsign.verify_release(
        zip_path.read_bytes(),
        sha_path.read_bytes(),
        sig_path.read_bytes(),
        public_key_pem=pub_pem,
        version="9.9.9",
    )

    # Also works when signing directly from PEM text (e.g. RELEASE_SIGNING_KEY secret).
    sig_from_pem = relsign.sign_checksum(sha_path.read_bytes(), priv_pem)
    relsign.verify_checksum(sha_path.read_bytes(), sig_from_pem, public_key_pem=pub_pem)


def test_tampered_zip_or_bad_signature_is_refused(release, tmp_path):
    priv_pem, pub_pem = relsign.generate_keypair()
    _, other_pub = relsign.generate_keypair()

    zip_path, sha_path = release.make_zip(ROOT, tmp_path, "9.9.9", "NOTICES")
    sig_path = release.sign_release(ROOT, sha_path, priv_pem)
    zip_bytes = zip_path.read_bytes()
    sha_bytes = sha_path.read_bytes()
    sig_bytes = sig_path.read_bytes()

    # 1. Wrong signing key is refused.
    with pytest.raises(ValueError, match="failed signature verification"):
        relsign.verify_release(zip_bytes, sha_bytes, sig_bytes, public_key_pem=other_pub, version="9.9.9")

    # 2. Tampered signature is refused.
    bad_sig = sig_bytes[:-4] + b"AAAA"
    with pytest.raises(ValueError, match="failed signature verification"):
        relsign.verify_release(zip_bytes, sha_bytes, bad_sig, public_key_pem=pub_pem, version="9.9.9")

    # 3. Missing signature is refused when a public key is configured.
    with pytest.raises(ValueError, match="has no signature"):
        relsign.verify_release(zip_bytes, sha_bytes, None, public_key_pem=pub_pem, version="9.9.9")

    # 4. Tampered zip with matching attacker-rewritten checksum is still refused by the signature.
    tampered_zip = zip_bytes + b"evil"
    import hashlib
    fake_sha = f"{hashlib.sha256(tampered_zip).hexdigest()}  {zip_path.name}\n".encode("ascii")
    with pytest.raises(ValueError, match="failed signature verification"):
        relsign.verify_release(tampered_zip, fake_sha, sig_bytes, public_key_pem=pub_pem, version="9.9.9")


def test_update_install_refuses_bad_or_missing_signature(release, tmp_path, monkeypatch):
    priv_pem, pub_pem = relsign.generate_keypair()
    other_priv, _ = relsign.generate_keypair()
    zip_path, sha_path = release.make_zip(ROOT, tmp_path, "9.9.9", "NOTICES")
    bad_sig = relsign.sign_checksum(sha_path.read_bytes(), other_priv).encode("ascii")

    monkeypatch.setattr(relsign, "RELEASE_PUBLIC_KEY", pub_pem)
    monkeypatch.setattr(update, "how_installed", lambda: "pip")

    def fake_open(req):
        url = req.full_url
        if url == "https://example.test/zip":
            return io.BytesIO(zip_path.read_bytes())
        if url == "https://example.test/sha":
            return io.BytesIO(sha_path.read_bytes())
        if url == "https://example.test/sig":
            return io.BytesIO(bad_sig)
        raise AssertionError(url)

    monkeypatch.setattr(update, "_open", fake_open)
    monkeypatch.setattr(update, "latest_version", lambda: ("9.9.9", {
        "zip": "https://example.test/zip",
        "sha256": "https://example.test/sha",
        "sig": "https://example.test/sig",
    }))
    with pytest.raises(core.PdfManagerError, match="failed signature verification"):
        update.install()

    # Unsigned release is also refused when RELEASE_PUBLIC_KEY is set.
    monkeypatch.setattr(update, "latest_version", lambda: ("9.9.9", {
        "zip": "https://example.test/zip",
        "sha256": "https://example.test/sha",
        "sig": None,
    }))
    with pytest.raises(core.PdfManagerError, match="has no signature"):
        update.install()


def test_keygen_cli_writes_private_key_and_prints_public_key(release, tmp_path, capsys):
    key_path = tmp_path / "zpdf-release.pem"
    assert release.main(["--keygen", str(key_path)]) == 0
    assert key_path.is_file()
    out = capsys.readouterr().out
    assert "-----BEGIN PUBLIC KEY-----" in out
    pub_pem = out[out.index("-----BEGIN PUBLIC KEY-----"):]
    sig = relsign.sign_checksum(b"abc  ZPDF-1.0.0-windows.zip\n", key_path)
    relsign.verify_checksum(b"abc  ZPDF-1.0.0-windows.zip\n", sig, public_key_pem=pub_pem)
