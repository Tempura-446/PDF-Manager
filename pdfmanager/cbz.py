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
            and raw.rstrip(b"\x00\xff") != raw[:2]
            and b"\xff\xd9" in raw[-64:]
            and b"\xff\xc9" not in raw[:512]
        )
        if fast_jpeg:
            w_px, h_px = im.size
            cs = "/DeviceGray" if im.mode == "L" else "/DeviceRGB"
            img_bytes = raw
            flt = "/DCTDecode"
        else:
            im2 = ImageOps.exif_transpose(im)
            if im2.mode in ("RGBA", "LA", "PA") or (im2.mode == "P" and "transparency" in im2.info):
                rgba = im2.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[3])
                im2 = bg
            is_gray = im2.mode in ("1", "L")
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
            try:
                raw = zf.read(info)
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
    src_resolved = src.resolve()
    with tempfile.TemporaryDirectory(prefix="zpdf-cbr-") as tmp:
        tmp_dir = Path(tmp)
        if tool == "unrar":
            cmd = [exe, "x", "-o+", "-idq", "--", str(src_resolved), str(tmp_dir) + "/"]
        elif tool == "bsdtar":
            cmd = [exe, "-xf", str(src_resolved), "-C", str(tmp_dir)]
        else:
            cmd = [exe, "x", "-y", f"-o{tmp_dir}", "--", str(src_resolved)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            raise core.PdfManagerError(f"CBR extraction timed out ({tool})") from None
        if proc.returncode != 0:
            raise core.PdfManagerError(f"could not extract CBR archive ({tool}): {(proc.stderr or proc.stdout).strip()[:200]}")

        root_resolved = tmp_dir.resolve()
        images = []
        total_bytes = 0
        is_rtl = False
        for p in sorted(tmp_dir.rglob("*")):
            if not p.is_file():
                continue
            try:
                rel = p.resolve().relative_to(root_resolved)
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
                if len(images) >= MAX_PAGES:
                    raise core.PdfManagerError(f"CBR archive has more than {MAX_PAGES} images")
                total_bytes += p.stat().st_size
                if total_bytes > MAX_TOTAL_BYTES:
                    raise core.PdfManagerError("CBR archive exceeds safe uncompressed size limit")
                # Preserve relative folder path so multi-chapter CBRs sort in order
                images.append((rel.as_posix(), p.read_bytes()))
        if rtl_viewer != "auto":
            is_rtl = bool(rtl_viewer)
        return images_to_pdf(images, output_pdf, rtl_viewer=is_rtl)


_BATCH_MANGA_EXTS = {".cbz", ".cbr", ".pdf"}


def translate_manga_batch(
    inputs,
    output_dir,
    *,
    src_lang="ja",
    dst_lang="en",
    export_format="cbz",
    manga_ai=True,
    engine="auto",
    dpi=180,
    quality=85,
    image_format="webp",
    password=None,
    progress_cb=None,
):
    """Translate a folder or list of ``.cbz``/``.cbr``/``.pdf`` manga chapters
    offline and export each chapter as a translated ``.cbz`` or ``.pdf``.

    Per-chapter errors are recorded without aborting earlier/later chapters.
    """
    import logging
    import os
    import shutil
    import tempfile

    from pdfmanager import progress
    from pdfmanager.translate import translate_pdf
    from pdfmanager.translate.manga import default_reading_dir

    if isinstance(inputs, (str, Path)):
        p = Path(inputs)
        if p.is_dir():
            sources = sorted(
                (
                    e for e in p.iterdir()
                    if e.is_file() and not e.name.startswith(".") and e.suffix.lower() in _BATCH_MANGA_EXTS
                ),
                key=lambda e: _natural_sort_key(e.name),
            )
        elif p.is_file():
            sources = [p]
        else:
            raise core.PdfManagerError(f"input not found: {p}")
    else:
        sources = []
        for item in inputs or ():
            p = Path(item)
            if p.is_dir():
                sources.extend(sorted(
                    (
                        e for e in p.iterdir()
                        if e.is_file() and not e.name.startswith(".") and e.suffix.lower() in _BATCH_MANGA_EXTS
                    ),
                    key=lambda e: _natural_sort_key(e.name),
                ))
            elif p.is_file() and p.suffix.lower() in _BATCH_MANGA_EXTS:
                sources.append(p)

    if not sources:
        raise core.PdfManagerError("no .cbz, .cbr, or .pdf manga files found")

    fmt = str(export_format or "cbz").strip().lower()
    if fmt not in ("cbz", "pdf"):
        fmt = "cbz"
    dst_code = re.sub(r"[^A-Za-z0-9_-]+", "-", str(dst_lang or "en").strip().lower()).strip("-") or "en"
    out_root = Path(output_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    out_root_prefix = str(out_root) + os.sep

    chapters = []
    outputs = []
    total_pages = 0
    succeeded = 0
    failed = 0
    total_files = len(sources)
    is_rtl = default_reading_dir(src_lang) == "rtl"

    for idx, src in enumerate(sources):
        progress.check()
        progress.report(idx, total_files, f"chapter {idx + 1}/{total_files}: {src.name}")
        if callable(progress_cb):
            progress_cb(idx, total_files, src.name)
        ext = src.suffix.lower()
        safe_stem = re.sub(r"[^\w.-]+", "_", src.stem).strip("._") or f"chapter_{idx + 1:03d}"
        out_path_str = os.path.normpath(os.path.join(str(out_root), f"{safe_stem}_{dst_code}.{fmt}"))
        if not out_path_str.startswith(out_root_prefix):
            raise core.PdfManagerError("invalid output filename")
        out_path = Path(out_path_str)
        try:
            with tempfile.TemporaryDirectory(prefix="zpdf-mbatch-") as tmp:
                tmp_dir = Path(tmp)
                if ext == ".cbz":
                    src_pdf = tmp_dir / "source.pdf"
                    cbz_to_pdf(src, src_pdf)
                elif ext == ".cbr":
                    src_pdf = tmp_dir / "source.pdf"
                    cbr_to_pdf(src, src_pdf)
                else:
                    src_pdf = src

                translated_pdf = tmp_dir / "translated.pdf"
                info = translate_pdf(
                    src_pdf,
                    translated_pdf,
                    src_lang=src_lang,
                    dst_lang=dst_lang,
                    mode="manga",
                    engine=engine,
                    manga_ai=bool(manga_ai),
                    password=password if ext == ".pdf" else None,
                )
                pages_done = int((info or {}).get("pages") or 0)
                if fmt == "cbz":
                    pdf_to_cbz(
                        translated_pdf,
                        out_path,
                        dpi=dpi,
                        image_format=image_format,
                        quality=quality,
                        comic_info={
                            "Title": src.stem,
                            "Manga": "YesAndRightToLeft" if is_rtl else "No",
                        },
                    )
                else:
                    shutil.copyfile(translated_pdf, out_path)
            outputs.append(str(out_path))
            total_pages += pages_done
            succeeded += 1
            chapters.append({
                "input": str(src),
                "output": str(out_path),
                "pages": pages_done,
                "status": "ok",
                "error": None,
            })
        except progress.Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("zpdf").debug("cbz.translate_manga_batch %s: %s", src, exc)
            failed += 1
            chapters.append({
                "input": str(src),
                "output": None,
                "pages": 0,
                "status": "error",
                "error": str(exc),
            })

    progress.report(total_files, total_files, "done")
    return {
        "outputs": outputs,
        "chapters": chapters,
        "total_pages": total_pages,
        "succeeded": succeeded,
        "failed": failed,
        "export_format": fmt,
    }

