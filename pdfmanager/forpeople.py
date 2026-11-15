# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Telling a PDF protected for particular people (encrypted for their
certificates, not with a password) from any other. On its own, so that
core.py can name such a file when it can't open it, without the module
that protects and opens them (certcrypt.py), which uses core.py."""

import io
from pathlib import Path

FOR_PEOPLE = "for particular people (certificates)"
NOT_READABLE = ("is protected for particular people: only their Digital IDs open it. "
                "Open it with yours (Open with Digital ID)")


def is_for_people(path):
    """Whether the PDF is encrypted for certificates (not with a password)."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return False
    if b"Adobe.PubSec" not in data and b"Adobe.PPKLite" not in data and b"Entrust.PPKEF" not in data:
        return False
    try:
        from pyhanko.pdf_utils.crypt import PubKeySecurityHandler
        from pyhanko.pdf_utils.reader import PdfFileReader

        reader = PdfFileReader(io.BytesIO(data), strict=False)
        return isinstance(reader.security_handler, PubKeySecurityHandler)
    except ImportError:
        return True  # (said by the file itself; pyHanko isn't here to confirm)
    except Exception:  # noqa: BLE001 - a damaged file isn't one of these
        return False
