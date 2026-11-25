# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""CBZ comic & manga archive import, export, and one-shot Manga v2 bubble translation.

100% offline, commercial-safe, pure Python (zipfile + Pillow + pypdf + ReportLab).
Includes zip-slip path-traversal protection and decompression-bomb limits.
"""

import io
import logging
import re
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from pdfmanager import core, progress

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
MAX_CBZ_ENTRIES = 2500
MAX_CBZ_UNCOMPRESSED_BYTES = 1_500_000_000  # 1.5 GB total safety cap


def _natural_sort_key(name):
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", str(name))]


def _validate_zip_member_name(name):
    """Reject zip-slip traversal paths and absolute paths."""
    norm = str(name or "").replace("\\", "/")
    if norm.startswith("/") or norm.startswith("~") or re.match(r"^[A-Za-z]:", norm):
        raise core.PdfManagerError(f"CBZ archive contains unsafe path: {name!r}")
    parts = norm.split("/")
    if any(p == ".." for p in parts):
        raise core.PdfManagerError(f"CBZ archive contains unsafe path: {name!r}")


def _build_comic_info_xml(meta):
    """Build a ComicRack-compatible ComicInfo.xml document from a dict."""
    allowed = (
        "Title", "Series", "Number", "Volume", "Summary", "Notes",
        "Year", "Writer", "Penciller", "Publisher", "LanguageISO", "Manga", "PageCount",
    )
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<ComicInfo>"]
    for key in allowed:
        val = meta.get(key)
        if val is not None and str(val).strip():
            lines.append(f"  <{key}>{_xml_escape(str(val).strip())}</{key}>")
    lines.append("</ComicInfo>\n")
    return "\n".join(lines).encode("utf-8")


def cbz_to_pdf(cbz_path, output_pdf, *, rtl_viewer=False):
    """Convert a ``.cbz`` (or ``.zip``) comic/manga archive into a PDF.

    Images are ordered via natural sort (``page_2.png`` before ``page_10.png``)
    and embedded at their exact aspect ratio. Pass ``rtl_viewer=True`` to set
    ``/ViewerPreferences << /Direction /R2L >>`` for manga right-to-left reading.
    """
    from PIL import Image
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DictionaryObject, NameObject
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    src = Path(cbz_path)
    dst = Path(output_pdf)
    if not src.is_file():
        raise core.PdfManagerError(f"CBZ file not found: {src}")

    try:
        zf = zipfile.ZipFile(src, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise core.PdfManagerError(f"could not open CBZ archive: {exc}") from None

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_CBZ_ENTRIES:
            raise core.PdfManagerError(f"CBZ archive has too many entries ({len(infos)} > {MAX_CBZ_ENTRIES})")

        image_infos = []
        total_uncompressed = 0
        for info in infos:
            _validate_zip_member_name(info.filename)
            if info.is_dir():
                continue
            base = Path(info.filename).name
            if base.startswith(".") or "__MACOSX" in info.filename.split("/"):
                continue
            if Path(base).suffix.lower() not in IMAGE_EXTS:
                continue
            total_uncompressed += int(info.file_size or 0)
            if total_uncompressed > MAX_CBZ_UNCOMPRESSED_BYTES:
                raise core.PdfManagerError("CBZ archive exceeds safe uncompressed size limit")
            image_infos.append(info)

        if not image_infos:
            raise core.PdfManagerError("CBZ archive contains no supported images")

        image_infos.sort(key=lambda inf: _natural_sort_key(inf.filename))
        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        total = len(image_infos)
        used_names = []

        for idx, info in enumerate(image_infos):
            progress.check()
            progress.report(idx, total, "converting CBZ page")
            raw = zf.read(info)
            try:
                with Image.open(io.BytesIO(raw)) as im:
                    im_rgb = im.convert("RGB")
                    w_px, h_px = im_rgb.size
                    # Target 150 DPI -> PDF points (72 pt/in) with reasonable page bounds
                    scale = 72.0 / 150.0
                    w_pt = max(72.0, min(1440.0, w_px * scale))
                    h_pt = w_pt * (float(h_px) / max(1.0, float(w_px)))
                    c.setPageSize((w_pt, h_pt))
                    img_buf = io.BytesIO()
                    im_rgb.save(img_buf, format="JPEG", quality=92)
                    img_buf.seek(0)
                    c.drawImage(ImageReader(img_buf), 0, 0, width=w_pt, height=h_pt)
                    c.showPage()
                    used_names.append(info.filename)
            except Exception as _exc:
                logging.getLogger("zpdf").debug("cbz.cbz_to_pdf.page %s: %s", info.filename, _exc)
                continue

        if not used_names:
            raise core.PdfManagerError("no readable images found in CBZ archive")
        c.save()
        buf.seek(0)

    reader = PdfReader(buf)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    if rtl_viewer:
        vp = DictionaryObject({NameObject("/Direction"): NameObject("/R2L")})
        writer._root_object[NameObject("/ViewerPreferences")] = vp
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "wb") as fh:
        writer.write(fh)
    return {"pages": len(used_names), "entries": used_names, "rtl": bool(rtl_viewer), "output": str(dst)}


def pdf_to_cbz(
    pdf_path,
    output_cbz,
    *,
    password=None,
    dpi=150,
    image_format="webp",
    quality=88,
    comic_info=None,
):
    """Export a PDF as a standards-compliant ``.cbz`` comic/manga archive."""
    import pypdfium2 as pdfium

    src = Path(pdf_path)
    dst = Path(output_cbz)
    fmt_key = str(image_format or "webp").strip().lower()
    if fmt_key in ("jpg", "jpeg"):
        ext, pil_fmt = "jpg", "JPEG"
    elif fmt_key == "png":
        ext, pil_fmt = "png", "PNG"
    else:
        ext, pil_fmt = "webp", "WEBP"

    # Validate password / readability via core.open_pdf first
    core.open_pdf(src, password=password)
    dst.parent.mkdir(parents=True, exist_ok=True)

    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(src), password=password)
        total = len(doc)
        try:
            with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_STORED) as zf:
                for idx in range(total):
                    progress.check()
                    progress.report(idx, max(1, total), "exporting CBZ page")
                    pg = doc[idx]
                    r_scale = core.render_scale(pg, float(dpi) / 72.0)
                    pil_img = pg.render(scale=r_scale).to_pil().convert("RGB")
                    buf = io.BytesIO()
                    save_kwargs = {"format": pil_fmt}
                    if pil_fmt in ("JPEG", "WEBP"):
                        save_kwargs["quality"] = max(50, min(100, int(quality)))
                    pil_img.save(buf, **save_kwargs)
                    zf.writestr(f"{idx + 1:04d}.{ext}", buf.getvalue())
                if comic_info and isinstance(comic_info, dict):
                    meta = dict(comic_info)
                    meta.setdefault("PageCount", total)
                    zf.writestr("ComicInfo.xml", _build_comic_info_xml(meta))
        finally:
            doc.close()

    return {"pages": total, "format": ext, "output": str(dst)}


def translate_cbz(
    cbz_path,
    output_path,
    src_lang,
    dst_lang,
    *,
    mode="manga",
    translator=None,
    ocr_fn=None,
    dpi=150,
    image_format="webp",
    quality=88,
):
    """One-shot CBZ comic/manga translation pipeline: ``.cbz`` -> PDF -> translate -> ``.cbz`` or ``.pdf``."""
    from pdfmanager import translate

    src = Path(cbz_path)
    dst = Path(output_path)
    want_cbz = dst.suffix.lower() == ".cbz"

    with tempfile.TemporaryDirectory(prefix="zpdf-cbz-") as tmp:
        tmp_dir = Path(tmp)
        in_pdf = tmp_dir / "in.pdf"
        tr_pdf = tmp_dir / "translated.pdf" if want_cbz else dst
        cbz_to_pdf(src, in_pdf, rtl_viewer=(str(src_lang or "").lower() in ("", "auto", "ja", "zh", "zh-hans", "zh-hant", "ko")))
        res = translate.translate_pdf(
            in_pdf,
            tr_pdf,
            src_lang,
            dst_lang,
            mode=mode,
            translator=translator,
            ocr_fn=ocr_fn,
        )
        if want_cbz:
            pdf_to_cbz(
                tr_pdf,
                dst,
                dpi=dpi,
                image_format=image_format,
                quality=quality,
                comic_info={
                    "Title": src.stem,
                    "LanguageISO": str(dst_lang or ""),
                    "Manga": "YesAndRightToLeft" if mode == "manga" else "No",
                },
            )
            res["output"] = str(dst)
        return res
