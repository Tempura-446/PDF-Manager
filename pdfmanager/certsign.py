"""Certificate signatures: sign a PDF with a digital ID (a .pfx or .p12
file, like Acrobat's "Sign with a digital ID") and check the signatures in
a PDF.

The signing is done by pyHanko. Signatures are added as an incremental
update: the bytes of the file stay as they are and the signature is
appended, so signatures already in the file stay valid. Password-protected
files stay protected (with the same password).

Checking a signature tells whether the document was changed after signing
and who signed it. Whether the signer is who the certificate says can only
be confirmed against certificates you trust: ZPDF has no list of trusted
certificate authorities built in, so a correct signature by an unknown
digital ID is reported as "valid-untrusted" (Acrobat says "the signer's
identity is unknown") unless its certificate, or the one that issued it,
is passed in ``trusted``.

Digital IDs that have expired (or aren't valid yet) are refused: a
signature made with one would be reported as invalid by every reader.
"""

import datetime as dt
import io
import logging
import unicodedata
from pathlib import Path

from pdfmanager import core

# Statuses of a checked signature.
VALID, UNTRUSTED, MODIFIED, INVALID = "valid", "valid-untrusted", "modified", "invalid"

POSITIONS = ("bottom-right", "bottom-left", "bottom-center", "top-right", "top-left",
             "top-center")
# Width and height of a visible signature, in points.
SIZES = {"small": (150, 45), "medium": (200, 60), "large": (260, 80)}
MARGIN = 36  # points from the edge of the page (half an inch)
MIN_SIZE = 12  # smallest visible signature box, points

TIMESTAMP_TIMEOUT = 20  # seconds

for _name in ("pyhanko", "pyhanko_certvalidator"):
    # pyHanko logs failed checks (an untrusted certificate, say) with full
    # tracebacks; the results say it in words, so keep the terminal quiet.
    logging.getLogger(_name).addHandler(logging.NullHandler())
    logging.getLogger(_name).propagate = False


class IdPasswordError(core.PdfManagerError):
    """The digital ID needs a password, or the one given is wrong."""


def _need_pyhanko():
    try:
        import pyhanko.sign  # noqa: F401
    except ImportError:
        raise core.PdfManagerError(
            "signing with a digital ID needs pyHanko: pip install pyhanko") from None


# --------------------------------------------------------------------------
# Digital IDs
# --------------------------------------------------------------------------

def _name_of(cert):
    """The common name of an asn1crypto certificate (the person or company)."""
    subject = cert.subject.native
    for key in ("common_name", "organization_name", "email_address"):
        value = subject.get(key)
        if isinstance(value, list):
            value = value[0] if value else None
        if value:
            return str(value)
    return cert.subject.human_friendly


def _email_of(cert):
    email = cert.subject.native.get("email_address")
    if isinstance(email, list):
        email = email[0] if email else None
    if not email:
        try:
            for name in (cert.subject_alt_name_value or []):
                if name.name == "rfc822_name":
                    return str(name.native)
        except (ValueError, KeyError):
            pass
    return str(email) if email else None


def _when(value):
    """An aware datetime as an ISO 8601 string (None stays None)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.isoformat(timespec="seconds")


def _is_pfx(data):
    """Whether ``data`` is a PKCS#12 file (whatever its password)."""
    from asn1crypto import pkcs12

    try:
        pfx = pkcs12.Pfx.load(bytes(data))
        return pfx["version"].native == "v3" and pfx["auth_safe"]["content_type"].native in (
            "data", "signed_data")
    except Exception:
        return False


def load_id(pfx, pfx_password=None, now=None):
    """Read a digital ID (.pfx / .p12 file, or its bytes). Returns (pyHanko
    signer, info) where info is {"signer", "email", "issuer", "expires"}.

    Raises IdPasswordError when the password is missing or wrong, and
    PdfManagerError for files that aren't digital IDs, IDs without a
    private key, and expired IDs."""
    _need_pyhanko()
    from cryptography.hazmat.primitives.serialization import pkcs12
    from pyhanko.sign import signers

    if isinstance(pfx, (bytes, bytearray, memoryview)):
        data, label = bytes(pfx), "the digital ID"
    else:
        try:
            data = Path(pfx).read_bytes()
        except FileNotFoundError:
            raise core.PdfManagerError(f"digital ID not found: {pfx}") from None
        label = Path(pfx).name
    if not _is_pfx(data):
        raise core.PdfManagerError(
            f"{label} is not a digital ID; use a .pfx or .p12 file (a certificate with its "
            "private key, e.g. exported from Acrobat or Windows)")
    password = pfx_password.encode("utf-8") if isinstance(pfx_password, str) else pfx_password
    key = cert = None
    for candidate in dict.fromkeys([password or None, password or b""]):
        try:
            key, cert, _ = pkcs12.load_key_and_certificates(data, candidate)
            password = candidate
            break
        except ValueError:
            continue
    if cert is None and key is None:
        raise IdPasswordError(f"wrong password for {label}" if pfx_password
                              else f"{label} is protected; enter its password")
    if key is None:
        raise core.PdfManagerError(
            f"{label} has no private key, so it can't sign; export the digital ID with "
            "its private key")
    if cert is None:
        raise core.PdfManagerError(f"{label} has no certificate")
    signer = signers.SimpleSigner.load_pkcs12_data(data, [], password)
    asn1 = signer.signing_cert
    valid = asn1["tbs_certificate"]["validity"]
    start, end = valid["not_before"].native, valid["not_after"].native
    now = now or dt.datetime.now(dt.timezone.utc)
    if end < now:
        raise core.PdfManagerError(
            f"the digital ID in {label} expired on {end:%Y-%m-%d}; a signature made with it "
            "would show as invalid. Get a new one from whoever issued it")
    if start > now:
        raise core.PdfManagerError(
            f"the digital ID in {label} is only valid from {start:%Y-%m-%d}")
    usage = asn1.key_usage_value
    if usage is not None and not ({"digital_signature", "non_repudiation"} & set(usage.native)):
        raise core.PdfManagerError(
            f"the digital ID in {label} isn't meant for signing (its certificate is "
            "for encryption only)")
    info = {"signer": _name_of(asn1), "email": _email_of(asn1),
            "issuer": None if asn1.self_signed != "no" else _name_of_issuer(asn1),
            "expires": _when(end)}
    return signer, info


def _name_of_issuer(cert):
    issuer = cert.issuer.native
    for key in ("common_name", "organization_name"):
        value = issuer.get(key)
        if isinstance(value, list):
            value = value[0] if value else None
        if value:
            return str(value)
    return cert.issuer.human_friendly


# --------------------------------------------------------------------------
# Placing a visible signature
# --------------------------------------------------------------------------

def place(path, page, position="bottom-right", size="medium", password=None):
    """(page, box): where a visible signature goes on ``page`` (1-based, or
    "last" / "first"): a box of ``size`` ("small", "medium", "large" or
    (width, height) in points) in a corner or at an edge of the page as it's
    seen (turned pages included), in PDF coordinates."""
    reader = core.open_pdf(path, password)
    count = len(reader.pages)
    page = _page_number(page, count)
    if position not in POSITIONS:
        raise core.PdfManagerError(f"position must be one of {', '.join(POSITIONS)}")
    width, height = SIZES.get(size, size) if not isinstance(size, str) or size in SIZES else (
        None, None)
    if width is None:
        raise core.PdfManagerError(f"size must be one of {', '.join(SIZES)}")
    pdf_page = reader.pages[page - 1]
    x0, y0, x1, y1 = (float(v) for v in pdf_page.cropbox)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    pw, ph = x1 - x0, y1 - y0
    rotation = core.quarter_turn(pdf_page.rotation)
    sw, sh = (ph, pw) if rotation in (90, 270) else (pw, ph)  # the page as seen
    width, height = min(width, sw - 2 * MARGIN), min(height, sh - 2 * MARGIN)
    if width < MIN_SIZE or height < MIN_SIZE:
        width, height = min(width, sw), min(height, sh)
    vertical, horizontal = position.split("-")
    u = {"left": MARGIN, "right": sw - MARGIN - width,
         "center": (sw - width) / 2}[horizontal]
    v = MARGIN if vertical == "bottom" else sh - MARGIN - height
    # Seen (u, v) -> page coordinates.
    corners = []
    for cu, cv in ((u, v), (u + width, v + height)):
        if rotation == 90:
            corners.append((x0 + pw - cv, y0 + cu))
        elif rotation == 180:
            corners.append((x0 + pw - cu, y0 + ph - cv))
        elif rotation == 270:
            corners.append((x0 + cv, y0 + ph - cu))
        else:
            corners.append((x0 + cu, y0 + cv))
    (ax, ay), (bx, by) = corners
    return page, [round(min(ax, bx), 2), round(min(ay, by), 2),
                  round(max(ax, bx), 2), round(max(ay, by), 2)]


def _page_number(page, count):
    text = str(page).strip().lower()
    if text in ("last", "end", "-1", ""):
        return count
    if text == "first":
        return 1
    try:
        number = int(text)
    except ValueError:
        raise core.PdfManagerError(f"not a page number: {page!r}") from None
    if not 1 <= number <= count:
        raise core.PdfManagerError(f"page {number} doesn't exist (the PDF has {count} "
                                   f"page{'s' if count != 1 else ''})")
    return number


def parse_box(text):
    """'x0,y0,x1,y1' (or a list of 4 numbers) -> [x0, y0, x1, y1]."""
    try:
        values = [float(v) for v in (text.replace(";", ",").split(",")
                                     if isinstance(text, str) else text)]
    except (TypeError, ValueError):
        values = []
    if len(values) != 4:
        raise core.PdfManagerError(
            f"invalid box {text!r}; give x0,y0,x1,y1 in points from the bottom left of the page")
    return values


_PLAIN = str.maketrans({"\u2013": "-", "\u2014": "-", "\u2018": "'", "\u2019": "'",
                        "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u2022": "*",
                        "\u20ac": "EUR", "\u2122": "TM", "\u0152": "OE", "\u0153": "oe"})


def _latin(text):
    """``text`` with only the characters the standard PDF fonts can show
    (accents are dropped from other letters, the rest becomes ?)."""
    out = []
    for ch in str(text).translate(_PLAIN):
        if ch == "\n" or 0x20 <= ord(ch) <= 0x7E or 0xA0 <= ord(ch) <= 0xFF:
            out.append(ch)
            continue
        base = "".join(c for c in unicodedata.normalize("NFKD", ch)
                       if 0x20 <= ord(c) <= 0x7E or 0xA0 <= ord(c) <= 0xFF)
        out.append(base or "?")
    return "".join(out)


def _stamp_style(lines, height, rotation):
    """A text stamp: thin grey border, Helvetica, sized to the box."""
    from pyhanko.pdf_utils import layout
    from pyhanko.pdf_utils.font.basic import SimpleFontEngineFactory
    from pyhanko.pdf_utils.text import TextBoxStyle
    from pyhanko.stamp import TextStamp, TextStampStyle

    margin = max(2.0, min(6.0, height * 0.08))
    font_size = max(5.0, min(12.0, (height - 2 * margin) / (len(lines) * 1.25)))

    class Stamp(TextStamp):
        def as_form_xobject(self):
            xobject = super().as_form_xobject()
            if rotation:
                from pyhanko.pdf_utils import generic

                # Turn the appearance with the page, so it's upright on screen.
                cos, sin = {90: (0, 1), 180: (-1, 0), 270: (0, -1)}[rotation]
                xobject["/Matrix"] = generic.ArrayObject(
                    generic.NumberObject(v) for v in (cos, sin, -sin, cos, 0, 0))
            return xobject

    class Style(TextStampStyle):
        def create_stamp(self, writer, box, text_params):
            if rotation in (90, 270):
                box = layout.BoxConstraints(width=box.height, height=box.width)
            return Stamp(writer=writer, style=self, box=box, text_params=text_params)

    return Style(
        stamp_text="\n".join(lines),
        timestamp_format="%Y-%m-%d %H:%M:%S %z",
        border_width=1,
        border_color=(0.55, 0.55, 0.55),
        text_box_style=TextBoxStyle(
            font=SimpleFontEngineFactory("Helvetica", 0.5), font_size=int(round(font_size)),
            leading=int(round(font_size * 1.25)),
            box_layout_rule=layout.SimpleBoxLayoutRule(
                x_align=layout.AxisAlignment.ALIGN_MIN, y_align=layout.AxisAlignment.ALIGN_MID,
                margins=layout.Margins(0, 0, 0, 0))),
        inner_content_layout=layout.SimpleBoxLayoutRule(
            x_align=layout.AxisAlignment.ALIGN_MIN, y_align=layout.AxisAlignment.ALIGN_MID,
            margins=layout.Margins(margin, margin, margin, margin)),
    )


# --------------------------------------------------------------------------
# Signing
# --------------------------------------------------------------------------

def _has_signatures(reader):
    """Whether a (pypdf) document has filled-in signature fields."""
    try:
        return any(f.get("/FT") == "/Sig" and f.get("/V") is not None
                   for f in (reader.get_fields() or {}).values())
    except Exception:
        return True  # can't tell: don't risk rewriting a signed file


def _open_for_update(data, password, name, reader=None):
    """An incremental writer on ``data``, the password-protected kind
    unlocked with ``password``. A file pyHanko can't read, but pypdf can
    (``reader``), is first rewritten by pypdf, if that can't break a
    signature in it."""
    from pyhanko.pdf_utils.crypt import AuthStatus
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.pdf_utils.misc import PdfError

    try:
        writer = IncrementalPdfFileWriter(io.BytesIO(data), strict=False)
    except (PdfError, ValueError, KeyError, TypeError) as exc:
        if reader is None or reader.is_encrypted or _has_signatures(reader):
            raise core.PdfManagerError(f"{name} can't be signed: it's damaged ({exc})") from None
        repaired = io.BytesIO()
        core.clone_document(reader).write(repaired)
        return _open_for_update(repaired.getvalue(), password, name)
    if writer.prev.encrypted:
        try:
            for candidate in dict.fromkeys([password or "", ""]):
                if writer.encrypt(candidate).status != AuthStatus.FAILED:
                    break
            else:
                raise core.PdfManagerError(f"wrong password for {name}" if password else
                                           f"{name} is encrypted; supply a password")
        except PdfError as exc:  # e.g. protected with a certificate, not a password
            raise core.PdfManagerError(f"{name} can't be unlocked for signing ({exc})") from None
    return writer


def _widget_refs(field_ref):
    """Object numbers of a (pyHanko) signature field and its widgets."""
    found = set()
    if field_ref is None:
        return found
    found.add(getattr(field_ref, "idnum", None))
    try:
        for kid in field_ref.get_object().get("/Kids") or []:
            found.add(getattr(kid, "idnum", None))
    except Exception:
        pass
    found.discard(None)
    return found


def _widget_page(reader, field_ref):
    """(1-based page, rect) of the widget of a signature field, by pypdf."""
    wanted = _widget_refs(field_ref)
    for number, page in enumerate(reader.pages, 1):
        for ref in page.get("/Annots") or []:
            if getattr(ref, "idnum", None) in wanted:
                annot = ref.get_object()
                rect = [float(v) for v in annot.get("/Rect", [0, 0, 0, 0])]
                return number, [round(min(rect[0], rect[2]), 2), round(min(rect[1], rect[3]), 2),
                                round(max(rect[0], rect[2]), 2), round(max(rect[1], rect[3]), 2)]
    return None, None


def _visible(box):
    return bool(box) and box[2] - box[0] > 0 and box[3] - box[1] > 0


def sign(path, output, pfx, pfx_password=None, *, page=None, box=None, field=None, reason=None,
         location=None, contact=None, timestamp_url=None, password=None):
    """Sign ``path`` with the digital ID ``pfx`` (a .pfx/.p12 file or its
    bytes) and write the signed PDF to ``output``.

    Where the signature goes:
      - ``page`` (1-based) and ``box`` ([x0, y0, x1, y1], PDF points from the
        bottom left): a visible signature showing the signer's name, the
        date and time and the reason;
      - ``field``: the name of an empty signature field already in the PDF
        (e.g. one added with the Add form fields tool);
      - neither: an invisible signature (shown in a reader's signature panel).

    ``timestamp_url``: an RFC 3161 timestamp server to vouch for the time of
    signing. If it can't be reached, nothing is written.

    Returns {"signer", "email", "field", "page", "box", "visible", "time",
    "timestamp", "reason", "location"}."""
    _need_pyhanko()
    from pyhanko.pdf_utils.misc import PdfError
    from pyhanko.sign import fields, signers, timestamps
    from pyhanko.sign.general import SigningError

    name = Path(path).name
    signer, info = load_id(pfx, pfx_password)
    reader = core.open_pdf(path, password)  # checks the file and the password, nicely
    if field and (page is not None or box is not None):
        raise core.PdfManagerError("sign an existing field or a new place on a page, not both")
    writer = _open_for_update(Path(path).read_bytes(), password, name, reader)

    existing = {}
    for fq_name, value, ref in fields.enumerate_sig_fields(writer.prev, filled_status=None):
        existing[fq_name] = (value is not None, ref)
    try:
        taken = set(existing) | set(reader.get_fields() or {})
    except Exception:
        taken = set(existing)

    rotation = 0
    if field:
        if field not in existing:
            empty = [n for n, (filled, _) in existing.items() if not filled]
            raise core.PdfManagerError(
                f"{name} has no signature field called {field!r}"
                + (f"; empty ones: {', '.join(empty)}" if empty else ""))
        if existing[field][0]:
            raise core.PdfManagerError(f"the field {field!r} in {name} is already signed")
        ref = existing[field][1]
        page, box = _widget_page(reader, ref)
        if page is not None:
            rotation = core.quarter_turn(reader.pages[page - 1].rotation)
        field_name, spec = field, None
    else:
        if box is not None:
            if page is None:
                raise core.PdfManagerError("say which page the signature goes on")
            page = _page_number(page, len(reader.pages))
            x0, y0, x1, y1 = parse_box(box)
            box = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
            if box[2] - box[0] < MIN_SIZE or box[3] - box[1] < MIN_SIZE:
                raise core.PdfManagerError("the signature box is too small")
            rotation = core.quarter_turn(reader.pages[page - 1].rotation)
        elif page is not None:
            raise core.PdfManagerError("give the box for the signature on the page as well")
        n = 1
        while f"Signature{n}" in taken:
            n += 1
        field_name = f"Signature{n}"
        spec = fields.SigFieldSpec(
            field_name, on_page=(page - 1) if box else 0,
            box=tuple(box) if box else None)

    visible = _visible(box)
    lines = [f"Digitally signed by {_latin(info['signer'])}", "Date: %(ts)s"]
    if reason:
        lines.append(f"Reason: {_latin(reason)}")
    if location:
        lines.append(f"Location: {_latin(location)}")
    height = (box[3] - box[1]) if visible else 60
    if rotation in (90, 270) and visible:
        height = box[2] - box[0]
    style = _stamp_style([line.replace("%", "%%") if "%(ts)s" not in line else line
                          for line in lines], height, rotation)

    meta = signers.PdfSignatureMetadata(
        field_name=field_name, reason=reason or None, location=location or None,
        contact_info=contact or None, name=info["signer"])
    timestamper = None
    if timestamp_url:
        if not str(timestamp_url).lower().startswith(("http://", "https://")):
            raise core.PdfManagerError("the timestamp server must be an http:// or https:// "
                                       "address")
        timestamper = timestamps.HTTPTimeStamper(timestamp_url, timeout=TIMESTAMP_TIMEOUT)
    pdf_signer = signers.PdfSigner(meta, signer, timestamper=timestamper, stamp_style=style,
                                   new_field_spec=spec)
    out = io.BytesIO()
    try:
        pdf_signer.sign_pdf(writer, output=out, existing_fields_only=bool(field))
    except timestamps.TimestampRequestError as exc:
        raise core.PdfManagerError(
            f"the timestamp server {timestamp_url} didn't answer "
            f"({_short(exc.__cause__ or exc)}); nothing was "
            "signed. Check the address and the internet connection, or sign without a "
            "timestamp") from None
    except (SigningError, PdfError) as exc:
        raise core.PdfManagerError(f"{name} can't be signed: {_short(exc)}") from None
    except OSError as exc:  # the network, from inside the timestamp client
        if not timestamp_url:
            raise
        raise core.PdfManagerError(
            f"the timestamp server {timestamp_url} can't be reached ({exc}); nothing was "
            "signed") from None
    signed = out.getvalue()
    core._write(core._Bytes(signed), output)
    return {"signer": info["signer"], "email": info["email"], "field": field_name,
            "page": page if visible else None, "box": box if visible else None,
            "visible": visible, "time": _when(_signing_time(signed, field_name, password)),
            "timestamp": bool(timestamp_url), "reason": reason or None,
            "location": location or None}


def _short(exc):
    text = str(exc).strip() or type(exc).__name__
    return text if len(text) < 300 else text[:297] + "…"


def _signing_time(data, field_name, password):
    """The time written into the new signature (its /M entry)."""
    from pyhanko.pdf_utils.reader import PdfFileReader

    try:
        reader = PdfFileReader(io.BytesIO(data), strict=False)
        if reader.encrypted:
            reader.decrypt(password or "")
        from pyhanko.sign.fields import enumerate_sig_fields

        for fq_name, value, _ in enumerate_sig_fields(reader, filled_status=True,
                                                       with_name=field_name):
            from pyhanko.pdf_utils.misc import parse_pdf_date

            return parse_pdf_date(str(value.get_object()["/M"]))
    except Exception:
        pass
    return dt.datetime.now(dt.timezone.utc).astimezone()


# --------------------------------------------------------------------------
# Checking
# --------------------------------------------------------------------------

def _load_certs(paths):
    """Certificates (PEM or DER files, or asn1crypto certificates)."""
    from asn1crypto import pem, x509

    certs = []
    for item in paths or ():
        if isinstance(item, x509.Certificate):
            certs.append(item)
            continue
        try:
            data = Path(item).read_bytes()
        except OSError as exc:
            raise core.PdfManagerError(f"can't read certificate {item}: {exc}") from None
        try:
            if pem.detect(data):
                certs.extend(x509.Certificate.load(der) for _, _, der in pem.unarmor(data, True))
            else:
                cert = x509.Certificate.load(data)
                cert.native  # parse it now, so a wrong file fails here
                certs.append(cert)
        except Exception:
            raise core.PdfManagerError(
                f"{item} is not a certificate (.cer, .crt or .pem)") from None
    return certs


CHANGES = {
    "NONE": "none",
    "LTA_UPDATES": "allowed",
    "FORM_FILLING": "allowed",
    "ANNOTATIONS": "allowed",
    "OTHER": "disallowed",
}
CHANGE_WORDS = {
    "LTA_UPDATES": "only validation data was added",
    "FORM_FILLING": "form filling or more signatures",
    "ANNOTATIONS": "comments, form filling or more signatures",
}
COVERAGE = {
    "ENTIRE_FILE": "entire-file",
    "ENTIRE_REVISION": "entire-revision",
    "CONTIGUOUS_BLOCK_FROM_START": "partial",
    "UNCLEAR": "partial",
}


def verify(path, password=None, trusted=None):
    """Check every signature in a PDF. Returns one dict per signature, in
    signing order ([] for an unsigned PDF):

      field, signer, email, issuer, kind ("signature" or "timestamp"),
      time (ISO 8601), time_source ("timestamp" or "signer"),
      timestamp_trusted (None without a timestamp),
      intact (the signed bytes are unchanged and the signature matches),
      coverage ("entire-file", "entire-revision" or "partial"),
      changes ("none", "allowed" or "disallowed": made after signing),
      changes_detail (in words), trusted (the certificate chain leads to a
      certificate in ``trusted``), status ("valid", "valid-untrusted",
      "modified" or "invalid"), summary (one sentence), reason, location,
      contact, page (1-based) and box ([x0, y0, x1, y1]) of a visible
      signature (else None), revision (1 = first), error (when it couldn't
      be checked at all).

    ``trusted``: certificates (files) to trust, e.g. your company's root
    certificate."""
    _need_pyhanko()
    from pyhanko.pdf_utils.crypt import AuthStatus
    from pyhanko.pdf_utils.misc import PdfError
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.fields import enumerate_sig_fields

    name = Path(path).name
    roots = _load_certs(trusted)
    pages = core.open_pdf(path, password)  # checks the file and the password, nicely
    data = Path(path).read_bytes()
    try:
        reader = PdfFileReader(io.BytesIO(data), strict=False)
        if reader.encrypted:
            for candidate in dict.fromkeys([password or "", ""]):
                if reader.decrypt(candidate).status != AuthStatus.FAILED:
                    break
            else:
                raise core.PdfManagerError(f"wrong password for {name}" if password else
                                           f"{name} is encrypted; supply a password")
        found = list(enumerate_sig_fields(reader, filled_status=True))
    except PdfError as exc:
        raise core.PdfManagerError(f"{name} can't be checked: it's damaged ({exc})") from None

    results = []
    for fq_name, value, ref in found:
        entry = _check_one(reader, fq_name, value, ref, roots, pages)
        results.append(entry)
    results.sort(key=lambda e: (e["revision"] is None, e["revision"] or 0))
    return results


def _blank(fq_name):
    return {"field": fq_name, "signer": None, "email": None, "issuer": None,
            "kind": "signature", "time": None, "time_source": None, "timestamp_trusted": None,
            "intact": False, "coverage": None, "changes": None, "changes_detail": None,
            "trusted": False, "status": INVALID, "summary": "", "reason": None,
            "location": None, "contact": None, "page": None, "box": None, "revision": None,
            "error": None}


def _check_one(reader, fq_name, value, ref, roots, pages):
    from pyhanko.sign.validation import (KeyUsageConstraints, validate_pdf_signature,
                                         validate_pdf_timestamp)
    from pyhanko.sign.validation.pdf_embedded import EmbeddedPdfSignature
    from pyhanko_certvalidator import ValidationContext

    entry = _blank(fq_name)
    page, box = _widget_page(pages, ref)
    if _visible(box):
        entry["page"], entry["box"] = page, box
    try:
        sig_dict = value.get_object()
        for key, out in (("/Reason", "reason"), ("/Location", "location"),
                         ("/ContactInfo", "contact")):
            if sig_dict.get(key):
                entry[out] = str(sig_dict[key])
        sig = EmbeddedPdfSignature(reader, ref.get_object() if ref is not None else None,
                                   fq_name)
    except Exception as exc:
        entry["error"] = _short(exc)
        entry["summary"] = f"Can't be checked: the signature is damaged ({entry['error']})."
        return entry
    entry["revision"] = sig.signed_revision + 1
    cert = sig.signer_cert
    entry["signer"], entry["email"] = _name_of(cert), _email_of(cert)
    entry["issuer"] = None if cert.self_signed != "no" else _name_of_issuer(cert)
    claimed = sig.self_reported_timestamp
    timestamp = sig.sig_object_type == "/DocTimeStamp"
    entry["kind"] = "timestamp" if timestamp else "signature"
    try:
        context = ValidationContext(trust_roots=list(roots), allow_fetching=False,
                                    moment=claimed, revocation_mode="soft-fail")
        ts_context = ValidationContext(trust_roots=list(roots), allow_fetching=False,
                                       revocation_mode="soft-fail")
        if timestamp:
            status = validate_pdf_timestamp(sig, validation_context=ts_context)
        else:
            status = validate_pdf_signature(
                sig, context, ts_context,
                key_usage_settings=KeyUsageConstraints(
                    key_usage={"digital_signature", "non_repudiation"},
                    match_all_key_usages=False))
    except Exception as exc:
        entry["error"] = _short(exc)
        entry["summary"] = f"Can't be checked ({entry['error']})."
        return entry

    entry["intact"] = bool(status.intact and status.valid)
    entry["trusted"] = bool(status.trusted)
    coverage = getattr(status.coverage, "name", None)
    entry["coverage"] = COVERAGE.get(coverage, "partial") if coverage else None
    level = getattr(getattr(status, "modification_level", None), "name", None)
    if timestamp:
        entry["time"], entry["time_source"] = _when(status.timestamp), "timestamp"
        entry["timestamp_trusted"] = bool(status.trusted)
        level = level or ("NONE" if coverage == "ENTIRE_FILE" else "LTA_UPDATES")
    else:
        when, source = status.signer_reported_dt or claimed, "signer"
        tst = status.timestamp_validity
        if tst is not None:
            entry["timestamp_trusted"] = bool(tst.trusted)
            if tst.intact and tst.valid:
                when, source = tst.timestamp, "timestamp"
        entry["time"], entry["time_source"] = _when(when), source
    docmdp_ok = getattr(status, "docmdp_ok", True)
    if coverage == "ENTIRE_FILE":
        level = "NONE"
    elif level is None or docmdp_ok is False:
        level = "OTHER"
    entry["changes"] = CHANGES.get(level, "disallowed")
    entry["changes_detail"] = CHANGE_WORDS.get(level)

    expired = False
    validity = cert["tbs_certificate"]["validity"]
    signed_at = status.signer_reported_dt if not timestamp else None
    if signed_at is not None:
        expired = not (validity["not_before"].native <= signed_at <= validity["not_after"].native)
    revoked = bool(getattr(status, "revoked", False))

    if not entry["intact"] or revoked or expired:
        entry["status"] = INVALID
    elif entry["changes"] == "disallowed":
        entry["status"] = MODIFIED
    elif entry["trusted"]:
        entry["status"] = VALID
    else:
        entry["status"] = UNTRUSTED
    entry["summary"] = _summary(entry, revoked, expired)
    return entry


def _summary(e, revoked=False, expired=False):
    who = e["signer"] or "an unknown signer"
    if not e["intact"]:
        return (f"Invalid: the document was altered or damaged after {who} signed it, or the "
                "signature doesn't match.")
    if revoked:
        return f"Invalid: {who}'s certificate has been revoked."
    if expired:
        return f"Invalid: {who}'s certificate wasn't valid at the time of signing."
    if e["status"] == MODIFIED:
        return (f"Signed by {who}, but the document was changed afterwards in a way the "
                "signature doesn't allow.")
    after = {"none": "The document hasn't been changed since it was signed.",
             "allowed": f"Changed after signing, but only in allowed ways "
                        f"({e['changes_detail']})."}.get(e["changes"], "")
    if e["status"] == VALID:
        return f"Valid: signed by {who}, whose identity is confirmed. {after}".strip()
    return (f"Valid signature by {who}, but the signer's identity isn't verified (the "
            f"certificate isn't one you trust). {after}").strip()


def report(results, name=None):
    """The results of ``verify`` as plain text for people."""
    lines = []
    if name:
        lines += [f"Signatures in {name}", ""]
    if not results:
        return "\n".join(lines + ["This PDF has no digital signatures."]) + "\n"
    for n, e in enumerate(results, 1):
        label = "Timestamp" if e["kind"] == "timestamp" else "Signature"
        lines.append(f"{n}. {label} {e['field']}: {STATUS_WORDS[e['status']]}")
        lines.append(f"   {e['summary']}")
        if e["signer"]:
            lines.append(f"   Signed by: {e['signer']}"
                         + (f" <{e['email']}>" if e["email"] else ""))
            lines.append("   Certificate issued by: "
                         + (e["issuer"] or "the signer (self-signed)"))
        if e["time"]:
            source = ("a trusted timestamp server" if e["timestamp_trusted"] else
                      "a timestamp server you don't trust") \
                if e["time_source"] == "timestamp" else "the signer's computer clock"
            lines.append(f"   Signed on: {_local(e['time'])} (from {source})")
        for key, word in (("reason", "Reason"), ("location", "Location"),
                          ("contact", "Contact")):
            if e[key]:
                lines.append(f"   {word}: {e[key]}")
        if e["page"]:
            lines.append(f"   Shown on page {e['page']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _local(iso):
    """An ISO time as local time for people: 2026-11-07 18:49:53 CET."""
    try:
        when = dt.datetime.fromisoformat(iso).astimezone()
    except (TypeError, ValueError):
        return str(iso)
    return when.strftime("%Y-%m-%d %H:%M:%S %Z").strip()


STATUS_WORDS = {
    VALID: "valid",
    UNTRUSTED: "valid, but the signer's identity isn't verified",
    MODIFIED: "the document was changed after signing",
    INVALID: "invalid",
}


def all_valid(results):
    """Whether there are signatures and all of them are valid (trusted or not)."""
    return bool(results) and all(e["status"] in (VALID, UNTRUSTED) for e in results)
