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


def detect_cbz_rtl(cbz_path):
    """Return True when a CBZ archive's ComicInfo.xml marks it as RTL manga."""
    try:
        with zipfile.ZipFile(cbz_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if Path(info.filename).name.lower() == "comicinfo.xml" and (info.file_size or 0) <= 512_000:
                    raw = zf.read(info).decode("utf-8", errors="ignore")
                    m = re.search(r"<Manga>\s*([^<]+?)\s*</Manga>", raw, re.IGNORECASE)
                    if m and m.group(1).strip().lower() in ("yes", "yesandrighttoleft"):
                        return True
                    return False
    except Exception:
        return False
    return False


def _append_image_page(writer, raw):
    """Embed raw image bytes directly as a PDF Image XObject page.

    Baseline RGB/Grayscale JPEGs without EXIF rotation are passed through
    zero-copy as ``/DCTDecode`` (zero pixel decode, zero generation loss).
    Other formats (PNG, WebP, progressive JPEG, EXIF-rotated photos) are
    normalized cleanly with ``ImageOps.exif_transpose`` and embedded directly.
    """
    import zlib
    from PIL import Image, ImageOps
    from pypdf.generic import DictionaryObject, NameObject, NumberObject, StreamObject

    with Image.open(io.BytesIO(raw)) as im:
        exif_orient = None
        try:
            exif_orient = im.getexif().get(274)
        except Exception:
            exif_orient = None
        fast_jpeg = (
            im.format == "JPEG"
            and im.mode in ("RGB", "L")
            and not im.info.get("progressive")
            and exif_orient in (None, 1)
        )
        if fast_jpeg:
            w_px, h_px = im.size
            cs = "/DeviceGray" if im.mode == "L" else "/DeviceRGB"
            img_bytes = raw
            flt = "/DCTDecode"
        else:
            im2 = ImageOps.exif_transpose(im)
            is_gray = im2.mode in ("1", "L", "LA")
            im_out = im2.convert("L") if is_gray else im2.convert("RGB")
            w_px, h_px = im_out.size
            if is_gray:
                cs = "/DeviceGray"
                img_bytes = zlib.compress(im_out.tobytes(), level=6)
                flt = "/FlateDecode"
            else:
                cs = "/DeviceRGB"
                jbuf = io.BytesIO()
                im_out.save(jbuf, format="JPEG", quality=92)
                img_bytes = jbuf.getvalue()
                flt = "/DCTDecode"

    scale = 72.0 / 150.0
    w_pt = max(72.0, min(1440.0, w_px * scale))
    h_pt = w_pt * (float(h_px) / max(1.0, float(w_px)))

    img_obj = StreamObject()
    img_obj._data = img_bytes
    img_obj.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Image"),
        NameObject("/Width"): NumberObject(w_px),
        NameObject("/Height"): NumberObject(h_px),
        NameObject("/ColorSpace"): NameObject(cs),
        NameObject("/BitsPerComponent"): NumberObject(8),
        NameObject("/Filter"): NameObject(flt),
        NameObject("/Length"): NumberObject(len(img_bytes)),
    })
    img_ref = writer._add_object(img_obj)

    content = StreamObject()
    content_bytes = f"q {w_pt:.2f} 0 0 {h_pt:.2f} 0 0 cm /Im0 Do Q\n".encode("ascii")
    content._data = content_bytes
    content[NameObject("/Length")] = NumberObject(len(content_bytes))
    content_ref = writer._add_object(content)

    page = writer.add_blank_page(width=w_pt, height=h_pt)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): img_ref}),
    })
    page[NameObject("/Contents")] = content_ref


def cbz_to_pdf(cbz_path, output_pdf, *, rtl_viewer="auto"):
    """Convert a ``.cbz`` (or ``.zip``) comic/manga archive into a PDF.

    Images are ordered via natural sort (``page_2.png`` before ``page_10.png``)
    and embedded at their exact aspect ratio with zero-copy JPEG passthrough.
    Pass ``rtl_viewer=True`` to set ``/ViewerPreferences << /Direction /R2L >>``
    for manga right-to-left reading, or ``rtl_viewer="auto"`` (default) to detect
    from ``ComicInfo.xml``.
    """
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject

    src = Path(cbz_path)
    dst = Path(output_pdf)
    if not src.is_file():
        raise core.PdfManagerError(f"CBZ file not found: {src}")

    is_rtl = detect_cbz_rtl(src) if rtl_viewer == "auto" else bool(rtl_viewer)

    try:
        zf = zipfile.ZipFile(src, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise core.PdfManagerError(f"could not open CBZ archive: {exc}") from None

    writer = PdfWriter()
    used_names = []
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
        total = len(image_infos)

        for idx, info in enumerate(image_infos):
            progress.check()
            progress.report(idx, total, "converting CBZ page")
            raw = zf.read(info)
            try:
                _append_image_page(writer, raw)
                used_names.append(info.filename)
            except Exception as _exc:
                logging.getLogger("zpdf").debug("cbz.cbz_to_pdf.page %s: %s", info.filename, _exc)
                continue

        if not used_names:
            raise core.PdfManagerError("no readable images found in CBZ archive")

    if is_rtl:
        vp = DictionaryObject({NameObject("/Direction"): NameObject("/R2L")})
        writer._root_object[NameObject("/ViewerPreferences")] = vp
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "wb") as fh:
        writer.write(fh)
    return {"pages": len(used_names), "entries": used_names, "rtl": is_rtl, "output": str(dst)}


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

    import collections
    import concurrent.futures
    import os

    save_kwargs = {"format": pil_fmt}
    if pil_fmt in ("JPEG", "WEBP"):
        save_kwargs["quality"] = max(50, min(100, int(quality)))
    if pil_fmt == "WEBP":
        save_kwargs["method"] = 4

    def _encode_page(pil_img):
        buf = io.BytesIO()
        pil_img.save(buf, **save_kwargs)
        return buf.getvalue()

    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(src), password=password)
        total = len(doc)

    workers = min(4, max(1, os.cpu_count() or 2))
    max_in_flight = workers * 2
    try:
        with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_STORED) as zf:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                pending = collections.deque()
                for idx in range(total):
                    progress.check()
                    progress.report(idx, max(1, total), "exporting CBZ page")
                    with core.PDFIUM_LOCK:
                        pg = doc[idx]
                        try:
                            r_scale = core.render_scale(pg, float(dpi) / 72.0)
                            pil_img = pg.render(scale=r_scale).to_pil().convert("RGB")
                        finally:
                            pg.close()
                    pending.append((idx, pool.submit(_encode_page, pil_img)))
                    if len(pending) >= max_in_flight:
                        done_idx, fut = pending.popleft()
                        zf.writestr(f"{done_idx + 1:04d}.{ext}", fut.result())
                while pending:
                    done_idx, fut = pending.popleft()
                    zf.writestr(f"{done_idx + 1:04d}.{ext}", fut.result())
            if comic_info and isinstance(comic_info, dict):
                meta = dict(comic_info)
                meta.setdefault("PageCount", total)
                zf.writestr("ComicInfo.xml", _build_comic_info_xml(meta))
    finally:
        with core.PDFIUM_LOCK:
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


def images_to_pdf(items, output_pdf, *, rtl_viewer=False):
    """Convert an ordered sequence of image ``Path``s or ``(name, bytes)`` pairs into a PDF.

    Items are natural-sorted by filename and embedded at their exact aspect ratio
    with zero-copy JPEG passthrough.
    """
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject

    dst = Path(output_pdf)
    normalized = []
    for item in items:
        if isinstance(item, (str, Path)):
            p = Path(item)
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                normalized.append((p.name, p.read_bytes()))
        elif isinstance(item, tuple) and len(item) == 2:
            name, raw = item
            if Path(str(name)).suffix.lower() in IMAGE_EXTS and raw:
                normalized.append((str(name), bytes(raw)))
    if not normalized:
        raise core.PdfManagerError("no supported images provided")

    normalized.sort(key=lambda pair: _natural_sort_key(pair[0]))
    writer = PdfWriter()
    used_names = []
    total = len(normalized)
    for idx, (name, raw) in enumerate(normalized):
        progress.check()
        progress.report(idx, total, "converting image page")
        try:
            _append_image_page(writer, raw)
            used_names.append(name)
        except Exception as _exc:
            logging.getLogger("zpdf").debug("cbz.images_to_pdf %s: %s", name, _exc)
            continue

    if not used_names:
        raise core.PdfManagerError("no readable images found")

    if rtl_viewer:
        writer._root_object[NameObject("/ViewerPreferences")] = DictionaryObject(
            {NameObject("/Direction"): NameObject("/R2L")}
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "wb") as fh:
        writer.write(fh)
    return {"pages": len(used_names), "entries": used_names, "rtl": bool(rtl_viewer), "output": str(dst)}


def cbr_to_pdf(cbr_path, output_pdf, *, rtl_viewer="auto"):
    """Convert a ``.cbr`` (RAR comic/manga archive) into a PDF using available system tools.

    Uses ``bsdtar``, ``unrar``, or ``7z`` if installed on PATH. Also supports
    ZIP-renamed ``.cbr`` files automatically.
    """
    import shutil
    import subprocess

    src = Path(cbr_path)
    if not src.is_file():
        raise core.PdfManagerError(f"CBR file not found: {src}")

    # Many ".cbr" files in the wild are actually ZIP archives renamed to .cbr
    if zipfile.is_zipfile(src):
        return cbz_to_pdf(src, output_pdf, rtl_viewer=rtl_viewer)

    extractor = None
    for tool in ("unrar", "bsdtar", "7z"):
        exe = shutil.which(tool)
        if exe:
            extractor = (tool, exe)
            break
    if extractor is None:
        raise core.PdfManagerError(
            "Opening RAR-format .cbr archives requires 'unrar', 'bsdtar', or '7z' on PATH "
            "(or convert the archive to .cbz)."
        )

    tool, exe = extractor
    with tempfile.TemporaryDirectory(prefix="zpdf-cbr-") as tmp:
        tmp_dir = Path(tmp)
        if tool == "unrar":
            cmd = [exe, "x", "-o+", "-idq", str(src), str(tmp_dir) + "/"]
        elif tool == "bsdtar":
            cmd = [exe, "-xf", str(src), "-C", str(tmp_dir)]
        else:
            cmd = [exe, "x", "-y", f"-o{tmp_dir}", str(src)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise core.PdfManagerError(f"could not extract CBR archive ({tool}): {(proc.stderr or proc.stdout).strip()[:200]}")

        root_resolved = tmp_dir.resolve()
        images = []
        is_rtl = False
        for p in sorted(tmp_dir.rglob("*")):
            if not p.is_file():
                continue
            try:
                p.resolve().relative_to(root_resolved)
            except ValueError:
                continue
            if p.name.startswith(".") or "__MACOSX" in p.parts:
                continue
            if p.name.lower() == "comicinfo.xml" and rtl_viewer == "auto":
                raw = p.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r"<Manga>\s*([^<]+?)\s*</Manga>", raw, re.IGNORECASE)
                if m and m.group(1).strip().lower() in ("yes", "yesandrighttoleft"):
                    is_rtl = True
            elif p.suffix.lower() in IMAGE_EXTS:
                images.append(p)
        if rtl_viewer != "auto":
            is_rtl = bool(rtl_viewer)
        return images_to_pdf(images, output_pdf, rtl_viewer=is_rtl)
