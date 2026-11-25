# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""PDF operations. Every function reads from paths and writes to paths so it
can be used from the CLI or imported as a library."""

from pdfmanager.errors import PdfManagerError  # noqa: F401
from pdfmanager.core.io import (
    DEFAULT_MAX_WORKERS, PAPER_SIZES, POINTS_PER_INCH, POINTS_PER_MM,
    _annot_refs, _content_refs, _mended, _own_contents, _own_info,
    _recount, _reorder_pages, _shared_objects, _write, _writer_from_pages,
    batch_run, clone_document, default_workers, extract_text, info,
    keep_size, open_pdf, open_pikepdf,
)
from pdfmanager.core.geometry import (
    PAPER_CALIPER_MM, _LINK_TARGET, _describe_size, _impose, _insert_positions,
    _moved_points, _page_size, _resolve_links, _retarget_links, _transfer_rotation,
    _transform_annotations, _turn_appearances, _unturned, _visible_box,
    arrange, booklet, crop, insert_blank_pages, n_up, parse_paper_size,
    quarter_turn, resize, rotate, spine_width_mm, trim_margins,
)
from pdfmanager.core.pages import (
    _bookmark_groups, _pages_bytes, _size_chunks, _title_filename,
    _unique_filename, delete_pages, interleave, merge, outline_items,
    reverse, select_pages, split,
)
from pdfmanager.core.security import (
    _TARGET_LADDER, _XMP_FIELDS, _Bytes, _compress_once, _decrypt_unprotected,
    _finish_compression, _fmt_bytes, _forget_pdfa, _update_xmp,
    compress, convert_color, decrypt, encrypt, pack, set_metadata,
)
from pdfmanager.core.stamp import (
    HEADER_SPOTS, MAX_ATTACHMENT_BYTES, MAX_TOTAL_ATTACHMENT_BYTES,
    _BIDI_AND_CONTROL, _HEADER_TOKEN, _NUMBER_FORMAT, _WIN_RESERVED,
    _overlay_page, _safe_filename, _upright, extract_media, headers_footers,
    number_pages, pdf_canvas, watermark,
)
from pdfmanager.core.convert import (
    MAX_RENDER_PIXELS, PDFIUM_LOCK, _EXIF_TURNS, _draw_turned, _jpeg_turn,
    _page_image, images_to_pdf, ocr, pdf_to_images, render_scale, show_whole_page,
)
