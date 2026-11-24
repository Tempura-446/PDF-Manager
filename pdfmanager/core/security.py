# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Compression, color conversion, encryption/decryption, and metadata/XMP updates."""

import io
import os
import re
import tempfile
from pathlib import Path

from pypdf import PdfWriter
from pypdf.errors import PyPdfError
from pypdf.generic import NameObject, NumberObject, StreamObject

from pdfmanager import imaging, progress
from pdfmanager.errors import PdfManagerError
from pdfmanager.core.io import (
    _mended, _own_info, _write, clone_document, keep_size, open_pdf, open_pikepdf,
)

# --------------------------------------------------------------------------
# File size, security, metadata
# --------------------------------------------------------------------------

def _finish_compression(writer):
    imaging.unwrap_ascii(writer)
    for page in writer.pages:
        page.compress_content_streams()
    imaging.deflate_plain_streams(writer)
    try:
        try:
            writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
        except TypeError:  # pypdf < 6
            writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
    except NotImplementedError:
        # A stream in a compression pypdf doesn't know can't be compared with
        # the others: the file is smaller all the same, only not by sharing
        # identical parts.
        pass


def _compress_once(path, output, level, image_quality, max_dpi, password, sharp_scans, notes):
    preset_dpi, preset_quality = imaging.COMPRESSION_LEVELS[level]
    asked_dpi, asked_quality = max_dpi, image_quality
    eff_dpi = preset_dpi if max_dpi is None else max_dpi
    eff_quality = preset_quality if image_quality is None else image_quality
    reader = open_pdf(path, password)
    was_encrypted = reader.is_encrypted
    writer = clone_document(reader)
    try:
        imaging.unwrap_ascii(writer)  # first: everything after reads the streams
        if eff_dpi or eff_quality:
            layers = imaging.SCAN_LAYERS.get(level) if sharp_scans else None
            if layers:
                layers = (asked_dpi or layers[0], asked_quality or layers[1], *layers[2:])
            sharp = imaging.downsample_images(writer, eff_dpi, eff_quality or 85, layers)[2]
            if sharp and notes is not None:
                notes.append(f"{sharp} scanned page{'s' if sharp != 1 else ''} kept sharp")
        _finish_compression(writer)
    except PyPdfError as exc:  # a stream pypdf can't rewrite
        raise PdfManagerError(f"can't rewrite {path} ({exc})") from None
    _write(writer, output)
    pack(output)
    return was_encrypted, eff_dpi, eff_quality


# Quality-preserving ladder used by target_mb auto-tuning (highest quality first).
_TARGET_LADDER = (
    ("low", None, None),
    ("medium", 150, 80),
    ("medium", 130, 68),
    ("high", 110, 58),
    ("high", 96, 48),
    ("high", 72, 38),
    ("high", 60, 28),
)


def _fmt_bytes(n):
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.2f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def compress(path, output, level="medium", image_quality=None, max_dpi=None, password=None,
             sharp_scans=True, notes=None, *, target_mb=None, keep_smaller=True, stats=None):
    """Reduce file size.

    ``level`` is a preset:
      - "low":    lossless only (compress streams, drop ASCII85/hex wrappers,
                  remove duplicate objects, pack objects into object
                  streams when pikepdf is installed)
      - "medium": also downsample images above 150 DPI, JPEG quality 75
      - "high":   also downsample images above 96 DPI, JPEG quality 50
                  ("large" is accepted as an alias)
    ``image_quality`` (1-95) and ``max_dpi`` override the preset's values.
    Images are only replaced when the new version is smaller.

    With ``target_mb`` (positive float), ZPDF steps through a quality-preserving
    ladder to reach ``<= target_mb`` MB at the highest visual quality possible.
    With ``keep_smaller=True`` (default), an unencrypted PDF that cannot be
    shrunk keeps its original bytes rather than bloating.
    If a dict is passed via ``stats``, it is populated with ``original_bytes``,
    ``output_bytes``, ``saved_bytes``, ``saved_pct``, ``kept_original``,
    ``target_bytes``, ``target_met``, ``level``, ``max_dpi``, ``image_quality``.
    """
    import shutil

    try:
        level = imaging.normalize_level(level)
    except ValueError as exc:
        raise PdfManagerError(str(exc)) from None
    if image_quality is not None and not 1 <= image_quality <= 95:
        raise PdfManagerError("image quality must be between 1 and 95")
    if max_dpi is not None and max_dpi < 1:
        raise PdfManagerError("maximum DPI must be at least 1")
    target_bytes = None
    if target_mb is not None:
        try:
            target_mb = float(target_mb)
        except (TypeError, ValueError):
            raise PdfManagerError("target size must be a positive number of MB") from None
        if target_mb <= 0:
            raise PdfManagerError("target size must be greater than 0 MB")
        target_bytes = int(target_mb * 1024 * 1024)

    src_path = Path(path)
    out_path = Path(output)
    original_bytes = src_path.stat().st_size if src_path.is_file() else 0
    used_level, used_dpi, used_quality = level, max_dpi, image_quality
    was_encrypted = False
    kept_original = False
    target_met = None

    if target_bytes is not None:
        best_path = None
        best_size = None
        best_meta = (level, max_dpi, image_quality)
        best_notes = []
        with tempfile.TemporaryDirectory(prefix="pdfm-target-") as tmp:
            for idx, (cand_level, cand_dpi, cand_q) in enumerate(_TARGET_LADDER):
                progress.check()
                progress.report(idx, len(_TARGET_LADDER), f"trying {cand_level} compression")
                cand_out = Path(tmp) / f"cand_{idx}.pdf"
                cand_notes = []
                was_encrypted, eff_dpi, eff_q = _compress_once(
                    src_path, cand_out, cand_level, cand_q, cand_dpi,
                    password, sharp_scans, cand_notes,
                )
                cand_size = cand_out.stat().st_size
                if best_size is None or cand_size < best_size:
                    best_size = cand_size
                    best_path = cand_out
                    best_meta = (cand_level, eff_dpi, eff_q)
                    best_notes = cand_notes
                if cand_size <= target_bytes:
                    target_met = True
                    break
            else:
                target_met = best_size is not None and best_size <= target_bytes
            used_level, used_dpi, used_quality = best_meta
            out_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(best_path, out_path)
            if notes is not None:
                notes.extend(best_notes)
    else:
        same_file = src_path.is_file() and src_path.resolve() == out_path.resolve()
        if keep_smaller and same_file:
            with tempfile.TemporaryDirectory(prefix="pdfm-comp-") as tmp:
                tmp_out = Path(tmp) / "compressed.pdf"
                was_encrypted, used_dpi, used_quality = _compress_once(
                    src_path, tmp_out, level, image_quality, max_dpi, password, sharp_scans, notes,
                )
                cand_size = tmp_out.stat().st_size
                if not was_encrypted and original_bytes > 0 and cand_size > original_bytes:
                    kept_original = True
                else:
                    shutil.copyfile(tmp_out, out_path)
        else:
            was_encrypted, used_dpi, used_quality = _compress_once(
                src_path, out_path, level, image_quality, max_dpi, password, sharp_scans, notes,
            )

    output_bytes = out_path.stat().st_size if out_path.is_file() else 0
    if not kept_original and keep_smaller and not was_encrypted and original_bytes > 0 and output_bytes > original_bytes:
        if src_path.resolve() != out_path.resolve():
            shutil.copyfile(src_path, out_path)
        output_bytes = original_bytes
        kept_original = True
        if target_bytes is not None:
            target_met = output_bytes <= target_bytes
            if notes is not None:
                notes.append("already optimized; kept original size")

    saved_bytes = max(0, original_bytes - output_bytes)
    saved_pct = round((saved_bytes / original_bytes) * 100, 1) if original_bytes else 0.0
    if target_bytes is not None and notes is not None:
        if target_met:
            notes.append(f"target {_fmt_bytes(target_bytes)} met ({_fmt_bytes(output_bytes)})")
        else:
            notes.append(
                f"smallest achievable was {_fmt_bytes(output_bytes)} "
                f"(target {_fmt_bytes(target_bytes)})"
            )
    if stats is not None:
        stats.update({
            "original_bytes": original_bytes,
            "output_bytes": output_bytes,
            "saved_bytes": saved_bytes,
            "saved_pct": saved_pct,
            "kept_original": kept_original,
            "target_bytes": target_bytes,
            "target_met": target_met,
            "level": used_level,
            "max_dpi": used_dpi,
            "image_quality": used_quality,
        })
    return out_path


def pack(path):
    """Repack the PDF at ``path`` with qpdf (through pikepdf), in place: its
    many small objects (page dictionaries, fonts' descriptions, outlines)
    go into compressed object streams, which pypdf can't write. Nothing is
    decoded or re-encoded, so it is lossless; text-heavy files lose a
    third or more. Skipped, leaving the file as it is, when pikepdf isn't
    installed, can't read the file, or the result isn't smaller. Returns
    whether the file was repacked."""
    try:
        import pikepdf
    except ImportError:
        return False
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        try:
            with pikepdf.open(path) as pdf:
                pdf.save(tmp, object_stream_mode=pikepdf.ObjectStreamMode.generate,
                         compress_streams=True, recompress_flate=False)
        except (pikepdf.PdfError, OSError, RuntimeError):
            return False  # pypdf's file is still there, as good as before
        if os.path.getsize(tmp) >= path.stat().st_size:
            return False
        os.replace(tmp, path)
        return True
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def convert_color(path, output, target, icc_profile=None, password=None, images="all"):
    """Convert a PDF's colours to ``target`` ("gray", "rgb" or "cmyk").

    Converts colours set in page content and form XObjects with the device,
    calibrated and ICC-based colour spaces, plus all images. With
    ``icc_profile`` images are converted through that profile and it is
    embedded as the document's output intent (e.g. for print shops).
    Gradients, patterns and spot colours are left unchanged."""
    if target not in imaging.COLOR_TARGETS:
        raise PdfManagerError(f"colour target must be one of {', '.join(imaging.COLOR_TARGETS)}")
    if icc_profile and not Path(icc_profile).is_file():
        raise PdfManagerError(f"ICC profile not found: {icc_profile}")
    reader = open_pdf(path, password)
    writer = clone_document(reader)
    from PIL import ImageCms

    try:
        imaging.convert_colors(writer, target, icc_profile, images=images)
        _finish_compression(writer)
    except (OSError, ImageCms.PyCMSError) as exc:  # unreadable or wrong-type ICC profile
        raise PdfManagerError(f"cannot use ICC profile {icc_profile}: {exc}") from None
    except PyPdfError as exc:  # a stream pypdf can't rewrite
        raise PdfManagerError(f"can't rewrite {path} ({exc})") from None
    return _write(writer, output)


class _Bytes:
    """Written data that ``_write`` can save like a writer."""

    def __init__(self, data):
        self.data = data

    def write(self, stream):
        stream.write(self.data)


def encrypt(path, output, user_password, owner_password=None, password=None, permissions=None):
    """Protect with AES-256. With pikepdf installed, qpdf does the protecting
    and packs the file into object streams on the way (see ``pack``), so a
    compressed file that is protected again keeps its packing; the
    unprotected document only passes through memory.

    ``permissions`` (filetools.permissions) limits what people with the
    open password (``user_password``, which may be empty: anyone can open
    it) can do; the permissions password (``owner_password``) lifts the
    limits, so it must differ from the open password."""
    from pdfmanager import pdfpermissions, policy

    try:
        policy.check_fips_algorithm("aes-256")
    except policy.PolicyError as exc:
        raise PdfManagerError(str(exc)) from None

    reader = open_pdf(path, password)
    restricted = pdfpermissions.restricts(permissions)
    if restricted and (not owner_password or owner_password == user_password):
        raise PdfManagerError("to limit printing, copying or changes, set a permissions "
                              "password different from the password to open it")
    if not user_password and not owner_password:
        raise PdfManagerError("enter a password to open it, or choose what to limit")
    owner_password = owner_password or user_password
    try:
        import pikepdf
    except ImportError:
        pikepdf = None
    if pikepdf is not None:
        plain = io.BytesIO()
        clone_document(reader).write(plain)
        plain.seek(0)
        try:
            with pikepdf.open(plain) as pdf:
                _forget_pdfa(pdf)
                protected = io.BytesIO()
                allow = permissions if permissions is not None else pikepdf.Permissions(
                    modify_assembly=True)
                pdf.save(protected, encryption=pikepdf.Encryption(
                             user=user_password or "", owner=owner_password, aes=True, R=6,
                             allow=allow),
                         object_stream_mode=pikepdf.ObjectStreamMode.generate,
                         compress_streams=True, recompress_flate=False)
            return _write(_Bytes(protected.getvalue()), output)
        except (pikepdf.PdfError, RuntimeError):
            pass  # qpdf can't read pypdf's copy: protect it the pypdf way
    writer = clone_document(reader)
    if "/Metadata" in writer._root_object:  # an encrypted file can't be PDF/A
        del writer._root_object["/Metadata"]
    extra = {"permissions_flag": pdfpermissions.pypdf_flags(permissions)} if permissions else {}
    writer.encrypt(user_password=user_password or "", owner_password=owner_password,
                   algorithm="AES-256", **extra)
    return _write(writer, output)


def _forget_pdfa(pdf):
    """Take the PDF/A identification out of a pikepdf document about to be
    encrypted: PDF/A forbids encryption, and a file that kept the claim
    would say it is an archive copy when it can't be."""
    if "/Metadata" not in pdf.Root:
        return
    try:
        with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
            for key in ("pdfaid:part", "pdfaid:conformance", "pdfaid:amd"):
                if key in meta:
                    del meta[key]
    except Exception:  # noqa: BLE001 - metadata pikepdf can't edit: drop it rather than lie
        del pdf.Root.Metadata


def _decrypt_unprotected(path, output, password):
    """Internal helper: decrypt ``path`` into a temporary staging path when the
    caller is guaranteed to re-encrypt before producing a final output."""
    reader = open_pdf(path, password)
    try:
        import pikepdf
    except ImportError:
        pikepdf = None
    if pikepdf is not None and reader.is_encrypted:
        from pdfmanager import pdfpermissions

        try:
            with pikepdf.open(path, password=password or "") as pdf:
                if not pdf.owner_password_matched and pdfpermissions.restricts(pdf.allow):
                    raise PdfManagerError(
                        f"this PDF limits what can be done with it ({pdfpermissions.describe(pdf.allow)}):"
                        " removing its protection needs its permissions password")
        except pikepdf.PdfError:
            pass  # qpdf can't read it: pypdf already could
    writer = clone_document(reader)
    return _write(writer, output)


def decrypt(path, output, password, *, _internal=False):
    """Remove the protection. A PDF that limits printing, copying or
    changes needs its permissions password for this (as in Acrobat), not
    just the password to open it.

    Administrator ``disable_password_removal`` policy is always enforced
    unconditionally (``_internal`` is retained only for signature compatibility
    and cannot bypass policy).
    """
    from pdfmanager import policy

    try:
        policy.check_password_removal_allowed()
    except policy.PolicyError as exc:
        raise PdfManagerError(str(exc)) from None
    return _decrypt_unprotected(path, output, password)


def set_metadata(path, output, password=None, **fields):
    reader = open_pdf(path, password)
    writer = clone_document(reader)
    updates = {f"/{k.capitalize()}": v for k, v in fields.items() if v is not None}
    if updates:
        writer.add_metadata(updates)
        _update_xmp(writer, updates)
    return _write(writer, output)


# Where viewers look first for the title and author: the XMP metadata, when
# a file has it (Word, LibreOffice, InDesign and Ghostscript all write it).
_XMP_FIELDS = {
    "/Title": ("dc:title", "http://purl.org/dc/elements/1.1/", "Alt"),
    "/Author": ("dc:creator", "http://purl.org/dc/elements/1.1/", "Seq"),
    "/Subject": ("dc:description", "http://purl.org/dc/elements/1.1/", "Alt"),
    "/Keywords": ("pdf:Keywords", "http://ns.adobe.com/pdf/1.3/", None),
}


def _update_xmp(writer, updates):
    """Keep the XMP metadata in step with the Info fields, so readers that
    prefer it (this app's own, Acrobat) show the new title and author."""
    from xml.sax.saxutils import escape

    ref = writer._root_object.get("/Metadata")
    stream = ref.get_object() if ref is not None else None
    if not isinstance(stream, StreamObject):
        return
    try:
        xml = stream.get_data().decode("utf-8")
    except (UnicodeDecodeError, PyPdfError):
        return
    for key, value in updates.items():
        if key not in _XMP_FIELDS:
            continue
        tag, namespace, kind = _XMP_FIELDS[key]
        prefix = tag.split(":")[0]
        text = escape(str(value))
        if kind == "Alt":
            inner = f'<rdf:Alt><rdf:li xml:lang="x-default">{text}</rdf:li></rdf:Alt>'
        elif kind == "Seq":
            inner = f"<rdf:Seq><rdf:li>{text}</rdf:li></rdf:Seq>"
        else:
            inner = text
        element = f'<{tag} xmlns:{prefix}="{namespace}">{inner}</{tag}>' if text else ""
        pattern = re.compile(rf"<{tag}\b[^>]*?(?:/>|>.*?</{tag}>)", re.DOTALL)
        if pattern.search(xml):
            xml = pattern.sub(lambda m: element, xml, count=1)
        elif element and "</rdf:Description>" in xml:
            xml = xml.replace("</rdf:Description>", element + "</rdf:Description>", 1)
    data = xml.encode("utf-8")
    for key in ("/Filter", "/DecodeParms"):
        if key in stream:
            del stream[key]
    stream._data = data
    stream[NameObject("/Length")] = NumberObject(len(data))
    if hasattr(stream, "decoded_self"):
        stream.decoded_self = None
