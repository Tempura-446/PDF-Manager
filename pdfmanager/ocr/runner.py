# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""PDF OCR execution, Tesseract runner, warm worker pool, and PDF raster/text-layer writer."""

import ctypes
import hashlib
import logging
import math
import os
import re
import sys
import threading
from pathlib import Path

from pdfmanager import core, scan, textlayer
from pdfmanager.textlayer import Word
from pdfmanager.ocr._facade import _ocr_attr
from pdfmanager.ocr.engines import (
    HIGH_RENDER_DPI, MAX_RENDER_SIDE, PIP_TESSERACT, RENDER_DPI, SCRIPT_NAMES, TESSERACT_LINK,
    _best_script, _codes, _tesseract_missing, builtin_available, choose_engine, script_for, tesseract_available,
)
from pdfmanager.ocr.models import _best_model, _check_quality, _language_model, default_gpu, default_quality
from pdfmanager.ocr.preprocess import (
    SMALL_TEXT_PX, _ORIG_READ, _ORIG_RECOGNISE, _lines_score, _map_lines, _median_text_height,
    _read, _retry_low_confidence, _scale_lines, _straighten_photo, _with_quality, recognise,
)
from pdfmanager.ocr.layout import (
    PICTURE_CONFIDENCE, PICTURE_LETTERS, PICTURE_TEXT, UNSURE_PAGE_MIN, UNSURE_PAGE_SHARE,
    _char_kinds, _hidden_text_in, _lines, _picture_boxes, _reads_wrong_way, _strip_layers, _words, layer_is_rubbish,
    page_has_text, page_orientation, reading_order, skew_angle,
)

def run(path, output, language="eng", force=False, deskew=False, password=None, engine="auto",
        rotate=True, quality=None, report=None, workers=None):
    """Add a searchable text layer to the scanned pages of ``path``; see
    ``core.ocr``."""
    from pdfmanager import progress

    chosen = choose_engine(engine, language)
    quality = _check_quality(default_quality() if quality is None else quality)
    script = _best_script(language) if chosen == "builtin" else None
    if script is not None:
        try:
            _language_model(script)  # fetched now, before the file is touched
        except core.PdfManagerError:
            if engine != "auto" or not _ocr_attr("tesseract_available", tesseract_available)():
                raise
            progress.say(f"the {SCRIPT_NAMES[script]} reading model can't be fetched; using Tesseract")
            chosen, script = "tesseract", None
    if chosen == "builtin" and quality == "best":
        _best_model("det")
        if script is None:
            _best_model("rec")
    source = Path(path)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_in = None
    if core.open_pdf(path, password).is_encrypted:  # also a plain error for a damaged file
        # Neither engine opens encrypted files; give them a decrypted copy.
        tmp_in = output.with_name(output.name + ".src.tmp.pdf")
        core.decrypt(path, tmp_in, password or "")
        source = tmp_in
    tmp_out = output.with_name(output.name + ".tmp.pdf")
    progress.say("recognising text (this can take a while)")
    try:
        if chosen == "tesseract":
            _ocr_attr("_run_tesseract", _run_tesseract)(source, tmp_out, language, force, deskew, rotate)
        else:
            _ocr_attr("_run_builtin", _run_builtin)(source, tmp_out, force, deskew, rotate, script, quality=quality,
                         report=report, workers=workers)
        os.replace(tmp_out, output)
    finally:
        for tmp in (tmp_in, tmp_out):
            if tmp is not None and tmp.exists():
                tmp.unlink()
    return output


def _run_tesseract(source, output, language, force, deskew, rotate=True):
    try:
        import ocrmypdf
        from ocrmypdf.exceptions import ExitCodeException, MissingDependencyError
    except ImportError:
        raise core.PdfManagerError(_tesseract_missing(no_ocrmypdf=True)) from None
    scan.add_programs_to_path()  # Tesseract and Ghostscript where installers put them
    # Turning pages needs Tesseract's orientation data, which not every
    # install has; without it OCR still runs, the pages just stay as they are.
    rotate = rotate and "osd" in _ocr_attr("_tesseract_languages", _tesseract_languages)()
    try:
        # A plain PDF out, not PDF/A: OCRmyPDF makes PDF/A with Ghostscript,
        # which drops form fields and prints a portfolio's attached files in
        # place of its pages. (PDF/A is a tool of its own.)
        ocrmypdf.ocr(str(source), str(output), language=language.split("+"),
                     skip_text=not force, force_ocr=force, deskew=deskew, rotate_pages=rotate,
                     progress_bar=False, output_type="pdf")
    except MissingDependencyError as exc:
        said = " ".join(str(exc).split())
        if re.search(r"(?i)\bgs\b|ghostscript", said):
            said = ("Ghostscript, which isn't installed on this computer: install it (free, "
                    "https://ghostscript.com/releases/gsdnld.html)")
        elif re.search(r"(?i)tesseract", said):
            said = f"the Tesseract program, which wasn't found: install it ({TESSERACT_LINK})"
        raise core.PdfManagerError(
            f"OCR with Tesseract needs {said or 'a program that is missing'}"
            + (", or choose the Built-in engine" if _ocr_attr("builtin_available", builtin_available)() else "")) from None
    except ExitCodeException as exc:
        said = " ".join(str(exc).split()) or (
            f"OCRmyPDF couldn't process the file (exit code {getattr(exc, 'exit_code', '?')}); "
            "it may be damaged, or try the built-in engine")
        raise core.PdfManagerError(f"OCR failed: {said}") from None


def _tesseract_languages():
    """The language data the Tesseract program has (empty when it can't say)."""
    import subprocess

    program = scan.find_tesseract()
    if not program:
        return set()
    try:
        out = subprocess.run([program, "--list-langs"], capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {line.strip() for line in (out.stdout + out.stderr).splitlines()[1:] if line.strip()}


def render_scale(width_pt, height_pt, dpi=RENDER_DPI):
    """Pixels per point for recognising a page of this size: ``dpi`` (300 by
    default), or less for a page whose long side would pass MAX_RENDER_SIDE
    pixels."""
    longest = max(width_pt, height_pt, 1)
    return min(dpi / 72, MAX_RENDER_SIDE / longest)


PARALLEL_MIN_PAGES = 3  # spin up worker processes only when at least this many unique scan pages need OCR
MAX_WORKERS = 4         # cap parallel OCR workers (each loads its own ONNX Runtime session)


def _available_memory_mb():
    """Approximate free/available RAM in MB, or None if unknown."""
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) // 1024
        except OSError:
            return None
    if sys.platform == "win32":
        try:
            class _MemStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            st = _MemStatus()
            st.dwLength = ctypes.sizeof(st)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return int(st.ullAvailPhys // (1024 * 1024))
        except Exception:
            return None
    return None


def default_workers(page_count):
    """Number of worker processes for parallel OCR, sized to CPU cores and
    available memory (1 when fewer than PARALLEL_MIN_PAGES need reading)."""
    if page_count < PARALLEL_MIN_PAGES:
        return 1
    cores = os.cpu_count() or 1
    n = min(MAX_WORKERS, max(1, cores // 2 if cores > 2 else cores))
    avail_mb = _ocr_attr("_available_memory_mb", _available_memory_mb)()
    if avail_mb is not None:
        n = min(n, max(1, avail_mb // 600))
    return max(1, min(n, page_count))


def _is_blank_image(image):
    """Whether a rendered page image is blank (uniform colour or faint
    scanner-bed noise with no visible ink or graphics worth running OCR on)."""
    import numpy as np

    grey = image.convert("L")
    w, h = grey.size
    if w == 0 or h == 0:
        return True
    arr = np.asarray(grey, dtype=np.int16)
    if w > 12 and h > 12:
        arr = arr[2:-2, 2:-2]
    if int(arr.max()) - int(arr.min()) < 18:
        return True
    med = int(np.median(arr[::4, ::4]))
    ink = int(np.count_nonzero(np.abs(arr - med) >= 24))
    return ink < 12


def _image_hash(image):
    """Fast content digest of a rendered page image for duplicate-page skipping."""
    h = hashlib.blake2b(digest_size=16)
    h.update(f"{image.mode}:{image.width}x{image.height}:".encode("ascii"))
    h.update(image.tobytes())
    return h.hexdigest()


def _recognise_scan_page(image, doc, page_idx, page_w, page_h, scale, rotate, deskew, script, quality):
    """Recognise a single scanned page image; returns ``(lines, turn, angle, image_size)``."""
    from PIL import Image

    lines = None
    turn = 0
    angle = 0.0
    if rotate:
        first = _with_quality(_ocr_attr("_read", _read), image, script, quality)
        turn = _with_quality(_ocr_attr("page_orientation", page_orientation), image, script, quality) if _reads_wrong_way(first) else 0
        if turn:
            image = image.rotate(turn, expand=True)
        else:
            lines = _refine_lines(image, _lines(first), script, quality)
    if deskew:
        angle = skew_angle(image)
        if angle:
            image = image.rotate(-angle, resample=Image.BICUBIC, fillcolor="white")
            lines = None
    if lines is None:
        lines = _with_quality(_ocr_attr("recognise", recognise), image, script, quality)
    # Small print: re-render at higher DPI when median text height is under SMALL_TEXT_PX.
    if lines and 0 < _median_text_height(lines) < SMALL_TEXT_PX and doc is not None:
        high_scale = render_scale(page_w, page_h, dpi=HIGH_RENDER_DPI)
        if high_scale > scale * 1.15:
            with core.PDFIUM_LOCK:
                pdfium_page = core.show_whole_page(doc[page_idx])
                high_image = pdfium_page.render(scale=high_scale).to_pil()
            if turn:
                high_image = high_image.rotate(turn, expand=True)
            if angle:
                high_image = high_image.rotate(-angle, resample=Image.BICUBIC, fillcolor="white")
            high_lines = _scale_lines(_lines(_with_quality(_ocr_attr("_read", _read), high_image, script, quality)),
                                      scale / high_scale)
            high_lines = _retry_low_confidence(image, high_lines, script, quality)
            if _lines_score(high_lines) > _lines_score(lines):
                lines = reading_order(high_lines)
    return lines, turn, angle, image.size


_WORKER_DOC_CACHE = {"key": None, "doc": None}


def _close_worker_doc():
    doc = _WORKER_DOC_CACHE.get("doc")
    _WORKER_DOC_CACHE["key"] = None
    _WORKER_DOC_CACHE["doc"] = None
    if doc is not None:
        try:
            doc.close()
        except Exception as _exc:  # noqa: BLE001 - best-effort cleanup on worker shutdown
            logging.getLogger("zpdf").debug("runner._close_worker_doc: %s", _exc)


def _worker_pdf_doc(source_str):
    """Return a cached ``pdfium.PdfDocument`` for ``source_str`` in the current
    worker process, reopening only when the file path or stat changes."""
    import atexit
    import pypdfium2 as pdfium

    st = os.stat(source_str)
    key = (source_str, st.st_mtime_ns, st.st_size)
    if _WORKER_DOC_CACHE["key"] == key and _WORKER_DOC_CACHE["doc"] is not None:
        return _WORKER_DOC_CACHE["doc"]
    _close_worker_doc()
    doc = pdfium.PdfDocument(source_str)
    _WORKER_DOC_CACHE["key"] = key
    _WORKER_DOC_CACHE["doc"] = doc
    if not getattr(_worker_pdf_doc, "_atexit_registered", False):
        atexit.register(_close_worker_doc)
        _worker_pdf_doc._atexit_registered = True
    return doc


def _recognise_scan_page_worker(task):
    """Top-level picklable worker for ProcessPoolExecutor: reuses a cached
    per-process pdfium handle and ONNX engine, renders ``page_idx``, and
    returns ``(page_idx, lines, turn, angle, image_size)``."""
    source_str, page_idx, rotate, deskew, script, quality = task
    doc = _worker_pdf_doc(source_str)
    pdfium_page = core.show_whole_page(doc[page_idx])
    try:
        page_w, page_h = pdfium_page.get_width(), pdfium_page.get_height()
        scale = render_scale(page_w, page_h)
        image = pdfium_page.render(scale=scale).to_pil()
    finally:
        pdfium_page.close()
    lines, turn, angle, img_size = _recognise_scan_page(
        image, doc, page_idx, page_w, page_h, scale, rotate, deskew, script, quality)
    return page_idx, lines, turn, angle, img_size


_OCR_POOL_LOCK = threading.Lock()
_OCR_POOL_STATE = {"pool": None, "workers": 0, "in_use": False, "timer": None, "atexit": False}
_OCR_POOL_IDLE_SECS = 120.0


def shutdown_ocr_pool():
    """Shut down the cached OCR worker pool if idle (or at interpreter exit)."""
    with _OCR_POOL_LOCK:
        timer = _OCR_POOL_STATE.get("timer")
        if timer is not None:
            timer.cancel()
            _OCR_POOL_STATE["timer"] = None
        if _OCR_POOL_STATE.get("in_use"):
            return
        pool = _OCR_POOL_STATE.get("pool")
        _OCR_POOL_STATE["pool"] = None
        _OCR_POOL_STATE["workers"] = 0
    if pool is not None:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception as _exc:  # noqa: BLE001 - best-effort shutdown
            logging.getLogger("zpdf").debug("runner.shutdown_ocr_pool: %s", _exc)


def _acquire_ocr_pool(max_workers, *, custom_worker=False):
    """Return ``(executor, is_shared)``. Reuses a warm ProcessPoolExecutor across
    batch OCR files so workers keep ONNX models loaded; falls back to a dedicated
    pool if the shared pool is currently busy or a custom worker is supplied."""
    import atexit
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    if custom_worker:
        return ProcessPoolExecutor(max_workers=max_workers, mp_context=multiprocessing.get_context("spawn")), False

    old_pool = None
    with _OCR_POOL_LOCK:
        if _OCR_POOL_STATE["in_use"]:
            # Another concurrent OCR job owns the shared pool -> dedicated one-off pool.
            return ProcessPoolExecutor(max_workers=max_workers, mp_context=multiprocessing.get_context("spawn")), False
        timer = _OCR_POOL_STATE.get("timer")
        if timer is not None:
            timer.cancel()
            _OCR_POOL_STATE["timer"] = None
        pool = _OCR_POOL_STATE.get("pool")
        if pool is not None and _OCR_POOL_STATE["workers"] < max_workers:
            old_pool = pool
            pool = None
        if pool is None:
            pool = ProcessPoolExecutor(max_workers=max_workers, mp_context=multiprocessing.get_context("spawn"))
            _OCR_POOL_STATE["pool"] = pool
            _OCR_POOL_STATE["workers"] = max_workers
        _OCR_POOL_STATE["in_use"] = True
        if not _OCR_POOL_STATE["atexit"]:
            atexit.register(shutdown_ocr_pool)
            _OCR_POOL_STATE["atexit"] = True

    if old_pool is not None:
        try:
            old_pool.shutdown(wait=False, cancel_futures=True)
        except Exception as _exc:  # noqa: BLE001
            logging.getLogger("zpdf").debug("runner._acquire_ocr_pool: %s", _exc)
    return pool, True


def _release_ocr_pool(executor, shared, *, broken=False):
    if not shared:
        executor.shutdown(wait=not broken, cancel_futures=broken)
        return
    with _OCR_POOL_LOCK:
        _OCR_POOL_STATE["in_use"] = False
        if broken:
            if _OCR_POOL_STATE.get("pool") is executor:
                _OCR_POOL_STATE["pool"] = None
                _OCR_POOL_STATE["workers"] = 0
            executor.shutdown(wait=False, cancel_futures=True)
            return
        timer = threading.Timer(_OCR_POOL_IDLE_SECS, shutdown_ocr_pool)
        timer.daemon = True
        timer.start()
        _OCR_POOL_STATE["timer"] = timer


def _run_builtin(source, output, force, deskew, rotate=True, script=None, notes=None, quality="standard",
                 report=None, workers=None, _page_worker=None):
    """``notes`` (a list) is given what was done beyond reading plain scans:
    a rubbish layer replaced, pictures read on a page of real text.
    ``report`` (a dict, if passed) is filled with per-page OCR stats and
    unsure-word counts."""
    import pypdfium2 as pdfium
    from pypdf import PdfReader

    from pdfmanager import progress

    if report is not None:
        report.setdefault("pages_read", [])
        report.setdefault("skipped", {})
        report.setdefault("replaced", [])
        report.setdefault("pictures", {})
        report.setdefault("unsure_pages", {})
        report.setdefault("flagged_pages", [])

    def note(text):
        if notes is not None:
            notes.append(text)
        progress.say(text)

    # Pages whose text is a layer nobody can read (invisible, and rubbish):
    # the layer is taken out first, and the page is then read as the scan it is.
    stripped = None
    if not force:
        stale = []
        reader = PdfReader(str(source))
        with core.PDFIUM_LOCK:
            look = pdfium.PdfDocument(str(source))
            try:
                for i, page in enumerate(reader.pages):
                    if not page_has_text(page):
                        continue
                    try:
                        text = page.extract_text()
                    except Exception:  # noqa: BLE001 - counted as having text; left alone
                        continue
                    if not layer_is_rubbish(text):
                        continue
                    pdfium_page = look[i]
                    textpage = pdfium_page.get_textpage()
                    try:
                        seen, hidden = _char_kinds(textpage)
                    finally:
                        textpage.close()
                        pdfium_page.close()
                    if hidden and not seen:
                        stale.append(i)
            finally:
                look.close()
        if stale:
            stripped = Path(str(output) + ".layers.tmp.pdf")
            _strip_layers(source, stale, stripped)
            source = stripped
            for i in stale:
                if report is not None:
                    report["replaced"].append(i + 1)
                note(f"page {i + 1}: its text layer was unreadable and has been replaced")
    try:
        _read_pages(source, output, force, deskew, rotate, script, note, quality=quality,
                    report=report, workers=workers, _page_worker=_page_worker)
    finally:
        if stripped is not None and stripped.exists():
            stripped.unlink()


def _refine_tables(image, lines, script=None, quality="standard"):
    """Detect ruled/unruled tables on a scanned page, read/refine cells
    individually with a digits-focused second pass on numeric cells, and
    preserve strict row-by-row cell order."""
    from pdfmanager import ocrtables

    return ocrtables.refine_tables(
        image,
        lines,
        read_crop_fn=lambda crop: _lines(_with_quality(_ocr_attr("_read", _read), crop, script, quality)),
    )


def _refine_lines(image, lines, script=None, quality="standard"):
    """Apply Phase 2 & 4 refinements (phone-photo perspective back-mapping,
    low-confidence line retries, and scanned table cell refinement)."""
    flat, to_photo = _straighten_photo(image)
    if flat is not None:
        flat_lines = _lines(_with_quality(_ocr_attr("_read", _read), flat, script, quality))
        if _lines_score(flat_lines) > _lines_score(lines):
            lines = reading_order(_map_lines(flat_lines, to_photo))
    lines = _retry_low_confidence(image, lines, script, quality)
    return _refine_tables(image, lines, script, quality)


def _record_skip(report, reason, page_num):
    if report is not None:
        report["skipped"].setdefault(reason, []).append(page_num)


def _record_unsure_stat(report, page_num, unsure_count, word_count):
    if report is None or unsure_count <= 0:
        return
    report["unsure_pages"][page_num] = report["unsure_pages"].get(page_num, 0) + unsure_count
    total_unsure = report["unsure_pages"][page_num]
    if total_unsure >= UNSURE_PAGE_MIN or (total_unsure / max(word_count, 1) >= UNSURE_PAGE_SHARE):
        if page_num not in report["flagged_pages"]:
            report["flagged_pages"].append(page_num)


def _read_pages(source, output, force, deskew, rotate, script, note, quality="standard",
                report=None, workers=None, _page_worker=None):
    from concurrent.futures import ProcessPoolExecutor, as_completed

    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_raw
    from PIL import Image
    from pypdf import PdfWriter, Transformation

    from pdfmanager import progress

    writer = PdfWriter(clone_from=str(source))
    core._recount(writer)
    rasterised = False
    # The pdfium lock is held only to open the file and render a page: the
    # app's thumbnails and previews wait on it too, and recognising a page
    # takes a second or two.
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(source))
    try:
        total = len(doc)
        # Pass 1: handle text+picture pages, skip blank scan pages, and group
        # identical scan pages by image hash so duplicates are only read once.
        scan_pages = []      # [(i, img_hash, page_w, page_h, scale, kept_image)]
        first_for_hash = {}  # img_hash -> (i, page_w, page_h, scale, image)
        done_count = 0

        for i in range(total):
            progress.report(done_count, total, "page")
            page = writer.pages[i]
            if not force and page_has_text(page):
                # A page with text of its own: its pictures may still hold
                # words (a scanned figure, a scan with a stamp typed on it).
                with core.PDFIUM_LOCK:
                    pdfium_page = core.show_whole_page(doc[i])
                    textpage = pdfium_page.get_textpage()
                    try:
                        scale = render_scale(pdfium_page.get_width(), pdfium_page.get_height())
                        size = (max(1, math.ceil(pdfium_page.get_width() * scale)),
                                max(1, math.ceil(pdfium_page.get_height() * scale)))
                        boxes = _picture_boxes(pdfium_page, textpage, size)
                        image = pdfium_page.render(scale=scale).to_pil() if boxes else None
                    finally:
                        textpage.close()
                if not boxes:
                    _record_skip(report, "already had text", i + 1)
                    done_count += 1
                    continue
                words = []
                for box in boxes:
                    progress.report(done_count, total, "page")  # (Cancel is heard between pictures)
                    try:
                        lines = _with_quality(_ocr_attr("recognise", recognise), image.crop(box), script, quality)
                    except progress.Cancelled:
                        raise
                    except Exception as exc:  # engine trouble: say which page
                        raise core.PdfManagerError(f"OCR failed on page {i + 1}: {exc}") from None
                    # A photo gives scraps (a horizon read as a dash, an edge as
                    # a letter): only words the engine is fairly sure of count,
                    # and a picture with next to nothing in it adds nothing.
                    # A picture full of words is a scan, and is read as a whole
                    # page is: every word the engine gave stays.
                    sure = [w for line in lines for w in line.words]
                    if sum(1 for w in sure for c in w.text if c.isalnum()) < PICTURE_TEXT:
                        sure = [w for w in sure if w.confidence >= PICTURE_CONFIDENCE]
                        if sum(1 for w in sure for c in w.text if c.isalnum()) < PICTURE_LETTERS:
                            continue
                    for w in sure:
                        words.append(Word(w.text, [(x + box[0], y + box[1]) for x, y in w.quad], w.confidence))
                if words:
                    # Not the words the page has already (a stamp typed over a scan).
                    with core.PDFIUM_LOCK:
                        pdfium_page = doc[i]
                        textpage = pdfium_page.get_textpage()
                        try:
                            fresh = []
                            for w in words:
                                cx = sum(p[0] for p in w.quad) / 4
                                cy = sum(p[1] for p in w.quad) / 4
                                px, py = ctypes.c_double(), ctypes.c_double()
                                pdfium_raw.FPDF_DeviceToPage(pdfium_page.raw, 0, 0, image.width, image.height, 0,
                                                             int(cx), int(cy), ctypes.byref(px), ctypes.byref(py))
                                if not textpage.get_text_bounded(px.value - 2, py.value - 2, px.value + 2, py.value + 2).strip():
                                    fresh.append(w)
                        finally:
                            textpage.close()
                    if fresh:
                        base_idx = textlayer._count_ocr_words_in_page(page, writer)
                        textlayer.add_text_layer(writer, page, fresh, image.size, scale * 72)
                        unsure = textlayer.record_unsure_words(
                            writer, page, fresh, image.size, scale * 72, base_index=base_idx)
                        read = len(boxes)
                        if report is not None:
                            report["pages_read"].append(i + 1)
                            report["pictures"][i + 1] = read
                        _record_unsure_stat(report, i + 1, unsure, len(fresh))
                        note(f"page {i + 1}: read the text in {read} picture{'s' if read != 1 else ''}")
                        done_count += 1
                        continue
                _record_skip(report, "already had text", i + 1)
                done_count += 1
                continue

            with core.PDFIUM_LOCK:
                pdfium_page = core.show_whole_page(doc[i])
                try:
                    page_w, page_h = pdfium_page.get_width(), pdfium_page.get_height()
                    scale = render_scale(page_w, page_h)
                    # Render a lightweight 72-DPI preview for blank detection & dedup hashing
                    # so large scans never pin gigabytes of full-DPI bitmaps across passes.
                    preview = pdfium_page.render(scale=min(1.0, scale)).to_pil()
                finally:
                    pdfium_page.close()
            if _is_blank_image(preview):
                _record_skip(report, "blank page", i + 1)
                done_count += 1
                continue
            img_hash = _image_hash(preview)
            del preview
            scan_pages.append((i, img_hash, page_w, page_h, scale))
            if img_hash not in first_for_hash:
                first_for_hash[img_hash] = (i, page_w, page_h, scale)

        # Pass 2: recognise each unique non-blank scan page (in parallel when beneficial).
        results_by_hash = {}
        unique_items = list(first_for_hash.items())
        n_workers = default_workers(len(unique_items)) if workers is None else max(1, int(workers))
        min_parallel = 2 if workers is not None else PARALLEL_MIN_PAGES
        can_pool = _page_worker is not None or (_ocr_attr("recognise", recognise) is _ORIG_RECOGNISE and _ocr_attr("_read", _read) is _ORIG_READ)
        use_pool = n_workers > 1 and len(unique_items) >= min_parallel and can_pool

        if use_pool:
            worker_fn = _page_worker or _recognise_scan_page_worker
            hash_by_page = {info[0]: h for h, info in unique_items}
            pool_size = min(n_workers, len(unique_items))
            executor, shared = _acquire_ocr_pool(pool_size, custom_worker=_page_worker is not None)
            try:
                futs = {
                    executor.submit(worker_fn, (str(source), info[0], rotate, deskew, script, quality)): info[0]
                    for _h, info in unique_items
                }
                for fut in as_completed(futs):
                    page_idx = futs[fut]
                    try:
                        _p, lines, turn, angle, img_size = fut.result()
                    except Exception as exc:
                        raise core.PdfManagerError(f"OCR failed on page {page_idx + 1}: {exc}") from None
                    results_by_hash[hash_by_page[page_idx]] = (lines, turn, angle, img_size)
                    done_count += 1
                    progress.report(min(done_count, total - 1), total, "page")
            except progress.Cancelled:
                _release_ocr_pool(executor, shared, broken=True)
                raise
            except BaseException:
                _release_ocr_pool(executor, shared, broken=True)
                raise
            else:
                _release_ocr_pool(executor, shared, broken=False)
        else:
            for img_hash, (page_idx, page_w, page_h, scale) in unique_items:
                progress.report(min(done_count, total - 1), total, "page")
                try:
                    if _page_worker is not None:
                        _p, lines, turn, angle, img_size = _page_worker(
                            (str(source), page_idx, rotate, deskew, script, quality))
                    else:
                        with core.PDFIUM_LOCK:
                            pdfium_page = core.show_whole_page(doc[page_idx])
                            try:
                                image = pdfium_page.render(scale=scale).to_pil()
                            finally:
                                pdfium_page.close()
                        lines, turn, angle, img_size = _recognise_scan_page(
                            image, doc, page_idx, page_w, page_h, scale, rotate, deskew, script, quality)
                        del image
                except progress.Cancelled:
                    raise
                except Exception as exc:  # engine trouble: say which page
                    raise core.PdfManagerError(f"OCR failed on page {page_idx + 1}: {exc}") from None
                results_by_hash[img_hash] = (lines, turn, angle, img_size)
                done_count += 1
                progress.report(min(done_count, total - 1), total, "page")

        # Pass 3: apply results to pages in deterministic page order.
        for i, img_hash, page_w, page_h, scale in scan_pages:
            progress.report(min(i, total - 1), total, "page")
            page = writer.pages[i]
            lines, turn, angle, img_size = results_by_hash[img_hash]
            if turn:
                # Show the page the right way up, as Acrobat does:
                # /Rotate turns it clockwise, the picture turned the other way round.
                _set_rotation(page, (page.rotation or 0) + 360 - turn)
            if angle:
                # Turn the page with the deskewed picture so words and page stay in step.
                _turn_page(page, -angle, Transformation)
            if force:
                with core.PDFIUM_LOCK:
                    pdfium_page = core.show_whole_page(doc[i])
                    try:
                        img = pdfium_page.render(scale=scale).to_pil()
                    finally:
                        pdfium_page.close()
                if turn:
                    img = img.rotate(turn, expand=True)
                if angle:
                    img = img.rotate(-angle, resample=Image.BICUBIC, fillcolor="white")
                _rasterise(writer, page, img, scale)
                del img
                textlayer._write_page_unsure_list(page, [])
                rasterised = True
            words = [w for line in lines for w in line.words]
            if words:
                base_idx = textlayer._count_ocr_words_in_page(page, writer)
                textlayer.add_text_layer(writer, page, words, img_size, scale * 72)
                unsure = textlayer.record_unsure_words(
                    writer, page, words, img_size, scale * 72, base_index=base_idx)
                if report is not None:
                    report["pages_read"].append(i + 1)
                _record_unsure_stat(report, i + 1, unsure, len(words))
            else:
                _record_skip(report, "no text found", i + 1)

        if report is not None:
            report["pages_read"].sort()
            report["flagged_pages"].sort()
            for pages in report["skipped"].values():
                pages.sort()
    finally:
        with core.PDFIUM_LOCK:
            doc.close()
    writer.write(str(output))
    if rasterised:
        _drop_unused_objects(output)


def _drop_unused_objects(path):
    """Rewrite ``path`` without the objects nothing refers to any more:
    pypdf writes out every object it was given, replaced page pictures and
    old fonts included."""
    import pikepdf

    tmp = path.with_name(path.name + ".gc.pdf")
    with pikepdf.open(str(path)) as pdf:
        pdf.save(str(tmp), object_stream_mode=pikepdf.ObjectStreamMode.generate)
    os.replace(tmp, path)


def _set_rotation(page, degrees):
    from pypdf.generic import NameObject, NumberObject

    page[NameObject("/Rotate")] = NumberObject(degrees % 360)


def _turn_page(page, angle, Transformation):
    """Rotate the page's content by ``angle`` degrees (counter-clockwise as
    displayed) about the centre of its crop box. Turning the displayed page
    is the same turn of its content whatever /Rotate says: rotations add up."""
    box = page.cropbox
    cx, cy = (float(box.left) + float(box.right)) / 2, (float(box.bottom) + float(box.top)) / 2
    page.add_transformation(Transformation().translate(-cx, -cy).rotate(angle).translate(cx, cy))


def _rasterise(writer, page, image, scale):
    """Replace the page's content with ``image`` (its render), dropping
    whatever text layer it had, so a wrong one is not kept beside the new.
    The page keeps its boxes, /Rotate and annotations: the picture is drawn
    through the same matrix the text layer uses."""
    import io
    import zlib

    from pypdf import Transformation
    from pypdf.generic import DictionaryObject, NameObject, NumberObject, StreamObject

    grey = image.convert("L")
    histogram = grey.histogram()
    middling = sum(histogram[32:224]) / (grey.width * grey.height)
    xobj = StreamObject()
    if middling < 0.02 and grey.getextrema()[0] < 32:
        # Black and white: a bit per pixel, as the scanner would store it.
        bits = grey.point(lambda v: 255 if v > 127 else 0).convert("1")
        xobj.set_data(zlib.compress(bits.tobytes(), 9))
        xobj[NameObject("/Filter")] = NameObject("/FlateDecode")
        xobj[NameObject("/ColorSpace")] = NameObject("/DeviceGray")
        xobj[NameObject("/BitsPerComponent")] = NumberObject(1)
    else:
        colour = image.convert("RGB") if image.mode not in ("L", "RGB") else image
        if colour.mode == "RGB" and _looks_grey(colour):
            colour = grey
        buf = io.BytesIO()
        colour.save(buf, "JPEG", quality=85, optimize=True)
        xobj.set_data(buf.getvalue())
        xobj[NameObject("/Filter")] = NameObject("/DCTDecode")
        xobj[NameObject("/ColorSpace")] = NameObject("/DeviceGray" if colour.mode == "L" else "/DeviceRGB")
        xobj[NameObject("/BitsPerComponent")] = NumberObject(8)
    xobj[NameObject("/Type")] = NameObject("/XObject")
    xobj[NameObject("/Subtype")] = NameObject("/Image")
    xobj[NameObject("/Width")] = NumberObject(image.width)
    xobj[NameObject("/Height")] = NumberObject(image.height)
    display_w, display_h = textlayer._display_size(page)
    to_page = textlayer.image_to_page_matrix(page, image.size, scale * 72)
    ctm = Transformation().scale(display_w, display_h).transform(Transformation(to_page)).ctm
    content = StreamObject()
    content.set_data(("q " + " ".join(f"{v:.4f}" for v in ctm) + " cm /Im0 Do Q").encode())
    page[NameObject("/Contents")] = writer._add_object(content)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): writer._add_object(xobj)})})


def _looks_grey(image, sample=200):
    """Whether an RGB image is grey all over (then it is stored as grey)."""
    import numpy as np

    arr = np.asarray(image.resize((sample, sample)).convert("RGB"), dtype=np.int16)
    return bool(
        np.all(np.abs(arr[:, :, 0] - arr[:, :, 1]) < 8)
        and np.all(np.abs(arr[:, :, 1] - arr[:, :, 2]) < 8)
    )
