# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Top-level PDF translation orchestrator (side-by-side, bilingual-pages, bilingual, in-place, text)."""

import logging
import sys
from pathlib import Path

from pypdf import PdfWriter, Transformation
from pypdf.generic import NameObject

from pdfmanager import core, i18n, progress
from pdfmanager.translate.engines import _extract_paragraphs, _load_offline_translator
from pdfmanager.translate.layout import (
    _clone_and_remap_outlines, _detect_running_margin_texts, _extract_layout_blocks,
    _is_code_or_math_block, _is_running_margin_block, _make_bilingual_pages,
    _make_layout_overlay, _make_text_page, _page_crop_origin, _render_page_bg,
    _shape_rtl, _strip_text_from_pdf, _translate_annotations_and_fields, _translate_outlines,
)
from pdfmanager.translate.models import detect_language
from pdfmanager.translate.text import MODES, translate_texts

def translate_pdf(
    input_path,
    output_path,
    src_lang,
    dst_lang,
    *,
    mode="side-by-side",
    password=None,
    translator=None,
    pages=None,
    auto_ocr=False,
    ocr_fn=None,
    manga_ai=False,
):
    """Translate a PDF into one of six output modes:
    - ``side-by-side``: double-width PDF with original page left, layout-preserved translation right.
    - ``bilingual-pages``: alternating original + layout-preserved translated pages (ideal for books with covers, images, and tables; pure-image pages appear once).
    - ``bilingual``: book-styled stacked bilingual PDF with original paragraph + translation directly underneath (preserves pure-image pages verbatim and strips repetitive running headers/footers).
    - ``in-place``: layout-preserving translation overlaid on the original pages + translated annotations, form fields, and bookmarks.
    - ``manga``: detects speech bubbles in right-to-left manga order, cleans bubble interiors while preserving borders & artwork, and typesets centered dialogue inside each bubble.
    - ``text``: plain-text ``.txt`` file with page markers.

    Optional ``pages`` (e.g. ``"1-5, 8"``) limits translation to those 1-based pages.
    Optional ``auto_ocr=True`` runs built-in offline OCR first if selected pages have no selectable text.
    Optional ``ocr_fn(pil_crop, src_lang) -> str`` overrides built-in OCR for ``mode="manga"`` bubbles.
    """
    import tempfile
    from pdfmanager import pageranges

    src = Path(input_path)
    dst = Path(output_path)
    mode = (mode or "side-by-side").strip().lower()
    if mode not in MODES:
        raise core.PdfManagerError(f"unknown translation mode: {mode!r}")

    reader = core.open_pdf(src, password=password)
    total_doc_pages = len(reader.pages)
    if pages and str(pages).strip():
        try:
            selected_indices = sorted(set(pageranges.parse_page_ranges(str(pages), total_doc_pages)))
        except pageranges.PageRangeError as exc:
            raise core.PdfManagerError(str(exc)) from None
    else:
        selected_indices = list(range(total_doc_pages))
    selected_set = set(selected_indices)

    # Optional auto-OCR when selected pages appear to be scanned images (no selectable text)
    ocr_tmp_dir = None
    ocr_applied = False
    work_src = src
    if auto_ocr and mode != "manga" and selected_indices:
        has_any_text = any((reader.pages[i].extract_text() or "").strip() for i in selected_indices)
        if not has_any_text:
            progress.report(message="Running offline OCR on scanned pages…", fraction_in_part=0.03)
            ocr_tmp_dir = tempfile.TemporaryDirectory(prefix="zpdf-tr-ocr-")
            ocr_out = Path(ocr_tmp_dir.name) / "ocr.pdf"
            try:
                core.ocr(src, ocr_out, password=password, engine="auto")
                if ocr_out.is_file():
                    work_src = ocr_out
                    reader = core.open_pdf(work_src, password=password)
                    ocr_applied = True
            except progress.Cancelled:
                raise
            except Exception as _exc:  # noqa: BLE001 - continue gracefully if OCR unavailable
                logging.getLogger("zpdf").debug("pipeline.translate_pdf: %s", _exc)

    want_auto = str(src_lang or "").strip().lower() in ("", "auto")

    try:
        if mode == "manga":
            from pdfmanager.translate import manga as _manga

            progress.report(message="Detecting speech bubbles…", fraction_in_part=0.05)
            pdfium_doc = None
            try:
                import pypdfium2 as pdfium

                with core.PDFIUM_LOCK:
                    pdfium_doc = pdfium.PdfDocument(str(work_src), password=password)
            except Exception as _exc:
                logging.getLogger("zpdf").debug("pipeline.translate_pdf.manga.pdfium: %s", _exc)
                pdfium_doc = None

            plumber_doc = None
            try:
                import pdfplumber

                plumber_doc = pdfplumber.open(str(work_src), password=password or "")
            except Exception as _exc:
                logging.getLogger("zpdf").debug("pipeline.translate_pdf.manga.pdfplumber: %s", _exc)
                plumber_doc = None

            from pdfmanager.translate import manga_ai as _manga_ai

            page_bubbles = {}
            page_img_sizes = {}
            page_inpaint_imgs = {}
            page_bubble_texts = {}
            page_bubble_idxs = {}
            all_bubble_texts = []
            bubble_refs = []
            src_hint = None if want_auto else i18n.normalize_code(src_lang)
            if manga_ai:
                comps = ["detector", "inpaint"]
                if not src_hint or src_hint == "ja" or str(src_hint).startswith("ja-"):
                    comps.append("ocr")
                _manga_ai.ensure_manga_ai(tuple(comps), download=True)
            reading_dir = _manga.default_reading_dir(src_hint)
            empty_pages = 0
            total_sel = max(1, len(selected_indices))

            try:
                for step_i, idx in enumerate(selected_indices):
                    progress.check()
                    progress.report(
                        message=f"Scanning bubbles (page {idx + 1}/{total_doc_pages})…",
                        fraction_in_part=0.05 + 0.35 * ((step_i + 1) / total_sel),
                    )
                    if pdfium_doc is None or idx >= len(pdfium_doc):
                        page_bubbles[idx] = []
                        empty_pages += 1
                        continue
                    with core.PDFIUM_LOCK:
                        pg = pdfium_doc[idx]
                        r_scale = core.render_scale(pg, 2.0)
                        pil_img = pg.render(scale=r_scale).to_pil()
                    page_img_sizes[idx] = pil_img.size
                    p_page = plumber_doc.pages[idx] if (plumber_doc is not None and idx < len(plumber_doc.pages)) else None
                    bubbles = _manga.detect_speech_bubbles(pil_img, reading_dir=reading_dir, plumber_page=p_page)
                    page_bubbles[idx] = bubbles
                    if not bubbles:
                        empty_pages += 1
                        continue
                    if any(b.get("needs_inpaint") for b in bubbles) and _manga_ai.is_component_installed("inpaint"):
                        page_inpaint_imgs[idx] = pil_img
                    p_texts = []
                    p_bidxs = []
                    for b_i, bubble in enumerate(bubbles):
                        txt = _manga.extract_bubble_text(
                            pil_img, bubble, src_hint, ocr_fn=ocr_fn, plumber_page=p_page,
                        )
                        if txt:
                            p_texts.append(txt)
                            p_bidxs.append(b_i)
                            all_bubble_texts.append(txt)
                            bubble_refs.append((idx, b_i))
                    page_bubble_texts[idx] = p_texts
                    page_bubble_idxs[idx] = p_bidxs
            finally:
                if plumber_doc is not None:
                    try:
                        plumber_doc.close()
                    except Exception as _exc:
                        logging.getLogger("zpdf").debug("pipeline.translate_pdf.manga.plumber_close: %s", _exc)
                if pdfium_doc is not None:
                    try:
                        with core.PDFIUM_LOCK:
                            pdfium_doc.close()
                    except Exception as _exc:
                        logging.getLogger("zpdf").debug("pipeline.translate_pdf.manga.close: %s", _exc)

            resolved_src = detect_language(" ".join(all_bubble_texts[:30])) if want_auto else i18n.normalize_code(src_lang)
            if translator is not None:
                fn = translator
            else:
                loader = getattr(sys.modules.get("pdfmanager.translate"), "_load_offline_translator", _load_offline_translator)
                try:
                    fn = loader(resolved_src, dst_lang, manga=True)
                except TypeError:
                    fn = loader(resolved_src, dst_lang)

            page_trans = {idx: [""] * len(page_bubbles.get(idx, ())) for idx in selected_indices}
            if all_bubble_texts:
                ordered_pages = [page_bubble_texts.get(idx, []) for idx in selected_indices]
                with progress.section(1, 3, "Translating dialogue"):
                    translated_pages = _manga_ai.translate_manga_pages(
                        ordered_pages, resolved_src, dst_lang, translator=fn
                    )
                for idx, tr_list in zip(selected_indices, translated_pages):
                    for b_i, tr_txt in zip(page_bubble_idxs.get(idx, ()), tr_list):
                        page_trans[idx][b_i] = tr_txt

            writer = PdfWriter()
            for step_i, idx in enumerate(selected_indices):
                progress.check()
                progress.report(
                    message=f"Typesetting bubbles (page {idx + 1}/{total_doc_pages})…",
                    fraction_in_part=0.75 + 0.22 * ((step_i + 1) / total_sel),
                )
                orig_page = reader.pages[idx]
                page_out = writer.add_page(orig_page)
                bubbles = page_bubbles.get(idx) or []
                if bubbles and any(page_trans[idx]):
                    w = float(orig_page.mediabox.width)
                    h = float(orig_page.mediabox.height)
                    img_w, img_h = page_img_sizes[idx]
                    overlay = _manga.make_manga_overlay(
                        w, h, img_w, img_h, bubbles, page_trans[idx], dst_lang,
                        pil_img=page_inpaint_imgs.get(idx),
                    )
                    page_out.merge_page(overlay)

            with open(dst, "wb") as fh:
                writer.write(fh)
            return {
                "mode": mode,
                "pages": len(selected_indices),
                "bubbles": len(bubble_refs),
                "src_lang": resolved_src,
                "empty_pages": empty_pages,
                "ocr_applied": ocr_applied,
                "output": str(dst),
            }

        if mode == "text":
            progress.report(message="Extracting text…", fraction_in_part=0.05)
            pages_raw = []
            empty_pages = 0
            for i in selected_indices:
                txt = (reader.pages[i].extract_text() or "").strip()
                paras = _extract_paragraphs(txt)
                if not paras:
                    empty_pages += 1
                pages_raw.append((i + 1, paras))
            flat_paras = [p for _, paras in pages_raw for p in paras]
            resolved_src = detect_language(" ".join(flat_paras[:30])) if want_auto else i18n.normalize_code(src_lang)
            fn = translator if translator is not None else getattr(sys.modules.get("pdfmanager.translate"), "_load_offline_translator", _load_offline_translator)(resolved_src, dst_lang)
            with progress.section(0, 1, "Translating"):
                flat_translated = translate_texts(flat_paras, resolved_src, dst_lang, translator=fn) if flat_paras else []
            sections = []
            cursor = 0
            for page_no, paras in pages_raw:
                chunk = flat_translated[cursor : cursor + len(paras)]
                cursor += len(paras)
                sections.append(f"--- Page {page_no} ---\n" + "\n\n".join(chunk))
            dst.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
            return {
                "mode": mode,
                "pages": len(selected_indices),
                "src_lang": resolved_src,
                "empty_pages": empty_pages,
                "ocr_applied": ocr_applied,
                "output": str(dst),
            }

        # Phase 1: Extract positioned layout blocks for selected pages via pdfplumber
        progress.report(message="Extracting page layout…", fraction_in_part=0.05)
        pages_blocks = [[] for _ in range(total_doc_pages)]
        try:
            import pdfplumber

            with pdfplumber.open(str(work_src), password=password or "") as pdoc:
                total_sel = max(1, len(selected_indices))
                for step_i, page_idx in enumerate(selected_indices):
                    if page_idx >= len(pdoc.pages):
                        continue
                    progress.check()
                    progress.report(
                        message=f"Extracting layout (page {page_idx + 1}/{total_doc_pages})…",
                        fraction_in_part=0.05 + 0.10 * ((step_i + 1) / total_sel),
                    )
                    pages_blocks[page_idx] = _extract_layout_blocks(pdoc.pages[page_idx])
        except progress.Cancelled:
            raise
        except Exception:  # noqa: BLE001 - fall back to plain text pages if pdfplumber fails
            pages_blocks = [[] for _ in range(total_doc_pages)]

        # Filter out code & math blocks from translation (keep them verbatim)
        to_translate = []
        block_positions = []
        for page_idx in selected_indices:
            for b_idx, blk in enumerate(pages_blocks[page_idx]):
                if not _is_code_or_math_block(blk):
                    to_translate.append(blk["text"])
                    block_positions.append((page_idx, b_idx))

        if want_auto:
            sample = " ".join(to_translate[:40]) or " ".join(
                (reader.pages[i].extract_text() or "")[:500] for i in selected_indices[:3]
            )
            resolved_src = detect_language(sample)
        else:
            resolved_src = i18n.normalize_code(src_lang)

        # Load translator once and reuse across blocks, fallback pages, annotations, and bookmarks
        fn = translator if translator is not None else getattr(sys.modules.get("pdfmanager.translate"), "_load_offline_translator", _load_offline_translator)(resolved_src, dst_lang)

        # Phase 2: Translate all non-code/non-math blocks in one deduplicated batch
        if to_translate:
            with progress.section(1, 5, "Translating text"):
                translated_flat = translate_texts(to_translate, resolved_src, dst_lang, translator=fn)
        else:
            translated_flat = []

        pages_trans_blocks = [[blk["text"] for blk in blks] for blks in pages_blocks]
        for (page_idx, b_idx), tr_text in zip(block_positions, translated_flat):
            pages_trans_blocks[page_idx][b_idx] = tr_text

        # Bilingual stacked mode: book-styled reflowed pages with original + translation underneath
        if mode == "bilingual":
            writer = PdfWriter()
            empty_pages = 0
            pages_heights = [float(reader.pages[i].mediabox.height) for i in selected_indices]
            repeated_margins = _detect_running_margin_texts(
                [pages_blocks[i] for i in selected_indices],
                pages_heights,
            )
            for step_i, idx in enumerate(selected_indices):
                progress.check()
                progress.report(
                    message=f"Building bilingual page {idx + 1}/{total_doc_pages}…",
                    fraction_in_part=0.84 + 0.14 * ((step_i + 1) / max(1, len(selected_indices))),
                )
                orig_page = reader.pages[idx]
                w = float(orig_page.mediabox.width)
                h = float(orig_page.mediabox.height)
                blks = pages_blocks[idx]
                trans_blks = pages_trans_blocks[idx]
                if blks:
                    filtered_pairs = [
                        (b, t) for b, t in zip(blks, trans_blks)
                        if not _is_running_margin_block(b, h, repeated_margins)
                    ]
                    if not filtered_pairs:
                        # Page only had a running header/footer or page number (e.g. full-page illustration)
                        writer.add_page(orig_page)
                        continue
                    blks = [b for b, _ in filtered_pairs]
                    trans_blks = [t for _, t in filtered_pairs]
                else:
                    raw_txt = (orig_page.extract_text() or "").strip()
                    if not raw_txt:
                        # Pure-image cover or full-page illustration: keep verbatim
                        empty_pages += 1
                        writer.add_page(orig_page)
                        continue
                    paras = _extract_paragraphs(raw_txt)
                    blks = [{"text": p, "size": 10.0} for p in paras]
                    trans_blks = translate_texts(paras, resolved_src, dst_lang, translator=fn)
                for bpage in _make_bilingual_pages(w, h, blks, trans_blks, dst_lang, page_num=idx + 1):
                    writer.add_page(bpage)
            with open(dst, "wb") as fh:
                writer.write(fh)
            return {
                "mode": mode,
                "pages": len(selected_indices),
                "src_lang": resolved_src,
                "empty_pages": empty_pages,
                "ocr_applied": ocr_applied,
                "output": str(dst),
            }

        # Phase 3: Strip original BT..ET text operators from selected pages with blocks
        progress.report(message="Rebuilding PDF layout…", fraction_in_part=0.82)
        block_page_indices = {i for i in selected_indices if pages_blocks[i]}
        stripped_reader = _strip_text_from_pdf(work_src, block_page_indices, password=password)
        use_stripped = stripped_reader is not None and len(stripped_reader.pages) == total_doc_pages

        pdfium_doc = None
        try:
            import pypdfium2 as pdfium

            with core.PDFIUM_LOCK:
                pdfium_doc = pdfium.PdfDocument(str(work_src), password=password)
        except Exception:  # noqa: BLE001
            pdfium_doc = None

        empty_pages = 0
        try:
            writer = PdfWriter()
            if mode == "bilingual-pages":
                total_sel = max(1, len(selected_indices))
                orig_page_map = {}
                for step_i, idx in enumerate(selected_indices):
                    progress.check()
                    progress.report(
                        message=f"Rendering bilingual book page {idx + 1}/{total_doc_pages}…",
                        fraction_in_part=0.84 + 0.14 * ((step_i + 1) / total_sel),
                    )
                    orig_page = reader.pages[idx]
                    blks = pages_blocks[idx]
                    trans_blks = pages_trans_blocks[idx]
                    w = float(orig_page.mediabox.width)
                    h = float(orig_page.mediabox.height)
                    cx0, cy0 = _page_crop_origin(orig_page)
                    orig_page_map[idx] = len(writer.pages)
                    writer.add_page(orig_page)
                    if blks:
                        bg_img = _render_page_bg(pdfium_doc, idx)
                        overlay = _make_layout_overlay(
                            w, h, blks, trans_blks,
                            bg_image=bg_img, draw_bg=not use_stripped,
                            crop_x0=cx0, crop_y0=cy0, dst_lang=dst_lang,
                        )
                        if use_stripped:
                            tr_page = writer.add_page(stripped_reader.pages[idx])
                            tr_page.merge_page(overlay)
                        else:
                            tr_page = writer.add_blank_page(width=w, height=h)
                            tr_page.merge_page(orig_page)
                            tr_page.merge_page(overlay)
                    else:
                        raw_txt = (orig_page.extract_text() or "").strip()
                        if not raw_txt:
                            # Pure-image cover / illustration: emit once without duplicating
                            empty_pages += 1
                        else:
                            fallback = "\n\n".join(
                                translate_texts(_extract_paragraphs(raw_txt), resolved_src, dst_lang, translator=fn)
                            )
                            writer.add_page(_make_text_page(w, h, _shape_rtl(fallback, dst_lang)))
                translated_bookmarks = _clone_and_remap_outlines(
                    reader, writer, orig_page_map, resolved_src, dst_lang, fn,
                )
                with open(dst, "wb") as fh:
                    writer.write(fh)
                return {
                    "mode": mode,
                    "pages": len(selected_indices),
                    "src_lang": resolved_src,
                    "empty_pages": empty_pages,
                    "bookmarks_translated": translated_bookmarks,
                    "ocr_applied": ocr_applied,
                    "output": str(dst),
                }

            if mode == "side-by-side":
                total_sel = max(1, len(selected_indices))
                for step_i, idx in enumerate(selected_indices):
                    progress.check()
                    progress.report(
                        message=f"Rendering page {idx + 1}/{total_doc_pages}…",
                        fraction_in_part=0.84 + 0.14 * ((step_i + 1) / total_sel),
                    )
                    orig_page = reader.pages[idx]
                    blks = pages_blocks[idx]
                    trans_blks = pages_trans_blocks[idx]
                    w = float(orig_page.mediabox.width)
                    h = float(orig_page.mediabox.height)
                    cx0, cy0 = _page_crop_origin(orig_page)
                    spread = writer.add_blank_page(width=w * 2, height=h)
                    spread.merge_page(orig_page)
                    if blks:
                        bg_img = _render_page_bg(pdfium_doc, idx)
                        overlay = _make_layout_overlay(
                            w, h, blks, trans_blks,
                            bg_image=bg_img, draw_bg=not use_stripped,
                            crop_x0=cx0, crop_y0=cy0, dst_lang=dst_lang,
                        )
                        base_right = stripped_reader.pages[idx] if use_stripped else orig_page
                        shift = Transformation().translate(w, 0)
                        spread.merge_transformed_page(base_right, shift)
                        spread.merge_transformed_page(overlay, shift)
                    else:
                        raw_txt = (orig_page.extract_text() or "").strip()
                        if not raw_txt:
                            empty_pages += 1
                        fallback = "\n\n".join(
                            translate_texts(_extract_paragraphs(raw_txt), resolved_src, dst_lang, translator=fn)
                        ) if raw_txt else "(no selectable text on page)"
                        right_page = _make_text_page(w, h, _shape_rtl(fallback, dst_lang))
                        spread.merge_transformed_page(right_page, Transformation().translate(w, 0))
                with open(dst, "wb") as fh:
                    writer.write(fh)
                return {
                    "mode": mode,
                    "pages": len(selected_indices),
                    "src_lang": resolved_src,
                    "empty_pages": empty_pages,
                    "ocr_applied": ocr_applied,
                    "output": str(dst),
                }

            # in-place mode: overlay translated blocks onto text-stripped original pages
            # (unselected pages when ``pages`` is specified are kept untouched)
            for idx in range(total_doc_pages):
                orig_page = reader.pages[idx]
                if idx not in selected_set:
                    writer.add_page(orig_page)
                    continue
                progress.check()
                progress.report(
                    message=f"Rendering page {idx + 1}/{total_doc_pages}…",
                    fraction_in_part=0.84 + 0.14 * ((idx + 1) / max(1, total_doc_pages)),
                )
                blks = pages_blocks[idx]
                trans_blks = pages_trans_blocks[idx]
                w = float(orig_page.mediabox.width)
                h = float(orig_page.mediabox.height)
                cx0, cy0 = _page_crop_origin(orig_page)
                if blks:
                    bg_img = _render_page_bg(pdfium_doc, idx)
                    overlay = _make_layout_overlay(
                        w, h, blks, trans_blks,
                        bg_image=bg_img, draw_bg=not use_stripped,
                        crop_x0=cx0, crop_y0=cy0, dst_lang=dst_lang,
                    )
                    base_page = stripped_reader.pages[idx] if use_stripped else orig_page
                    attached = writer.add_page(base_page)
                    attached.merge_page(overlay)
                else:
                    raw_txt = (orig_page.extract_text() or "").strip()
                    if not raw_txt:
                        empty_pages += 1
                        writer.add_page(orig_page)
                    else:
                        fallback = "\n\n".join(
                            translate_texts(_extract_paragraphs(raw_txt), resolved_src, dst_lang, translator=fn)
                        )
                        new_page = _make_text_page(w, h, _shape_rtl(fallback, dst_lang))
                        if "/Annots" in orig_page:
                            new_page[NameObject("/Annots")] = orig_page["/Annots"]
                        writer.add_page(new_page)
            # Copy & translate document bookmarks (/Outlines) when present
            if "/Outlines" in reader.root_object:
                try:
                    writer.clone_document_from_reader  # check pypdf capability
                    outlines_ref = reader.root_object.get("/Outlines")
                    if outlines_ref is not None:
                        writer._root_object[NameObject("/Outlines")] = outlines_ref.clone(writer)
                except Exception as _exc:  # noqa: BLE001
                    logging.getLogger("zpdf").debug("pipeline.translate_pdf: %s", _exc)
            updated_items = _translate_annotations_and_fields(
                writer, resolved_src, dst_lang, fn, selected_pages=selected_set
            )
            translated_bookmarks = _translate_outlines(writer, resolved_src, dst_lang, fn)
            with open(dst, "wb") as fh:
                writer.write(fh)
            return {
                "mode": mode,
                "pages": len(selected_indices),
                "src_lang": resolved_src,
                "empty_pages": empty_pages,
                "ocr_applied": ocr_applied,
                "annotations_translated": updated_items,
                "bookmarks_translated": translated_bookmarks,
                "output": str(dst),
            }
        finally:
            if pdfium_doc is not None:
                try:
                    with core.PDFIUM_LOCK:
                        pdfium_doc.close()
                except Exception as _exc:  # noqa: BLE001
                    logging.getLogger("zpdf").debug("pipeline.translate_pdf: %s", _exc)
    finally:
        if ocr_tmp_dir is not None:
            ocr_tmp_dir.cleanup()
