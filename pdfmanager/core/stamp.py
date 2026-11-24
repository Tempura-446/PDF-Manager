# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Watermarks, page numbering, headers/footers, and embedded media extraction."""

import io
import re
from pathlib import Path

from pypdf import PdfReader, Transformation
from pypdf.generic import DictionaryObject, NameObject

from pdfmanager import fonts, progress
from pdfmanager.errors import PdfManagerError
from pdfmanager.pageranges import parse_page_ranges
from pdfmanager.core.io import POINTS_PER_MM, _own_contents, _shared_objects, _write, clone_document, open_pdf
from pdfmanager.core.geometry import _page_size, _visible_box, quarter_turn

# --------------------------------------------------------------------------
# Stamping
# --------------------------------------------------------------------------

def pdf_canvas(target, **kwargs):
    """A reportlab canvas writing to ``target`` (a path or file object).
    Its streams are written as plain binary: reportlab's default wraps them
    in ASCII85 text, 25% bigger, and without its optional C accelerator that
    encoding takes most of the time of writing a page picture."""
    from reportlab import rl_config
    from reportlab.pdfgen import canvas

    rl_config.useA85 = 0
    return canvas.Canvas(target, **kwargs)


def _overlay_page(width, height, draw):
    buf = io.BytesIO()
    c = pdf_canvas(buf, pagesize=(width, height))
    draw(c, width, height)
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]


def watermark(path, output, text=None, stamp_pdf=None, opacity=0.15,
              font_size=60, angle=45, pages=None, below=False, password=None):
    """Stamp ``text`` diagonally across pages, or overlay the first page of
    ``stamp_pdf``. With ``below`` the stamp goes underneath the content."""
    if (text is None) == (stamp_pdf is None):
        raise PdfManagerError("specify exactly one of text or stamp_pdf")
    reader = open_pdf(path, password)
    targets = set(parse_page_ranges(pages, len(reader.pages))) if pages else None
    stamp_page = open_pdf(stamp_pdf).pages[0] if stamp_pdf else None
    writer = clone_document(reader)
    shared = _shared_objects(writer)

    for i, page in enumerate(writer.pages):
        if targets is not None and i not in targets:
            continue
        _own_contents(page, shared)
        w, h, matrix = _upright(page)  # across what the page shows, the right way up
        if stamp_page is None:
            def draw(c, w, h):
                c.setFillAlpha(opacity)
                c.setFont(fonts.font_for(text, bold=True), font_size)
                c.translate(w / 2, h / 2)
                c.rotate(angle)
                c.drawCentredString(0, -font_size / 3, text)
            stamp = _overlay_page(w, h, draw)
        else:
            stamp = stamp_page
        page.merge_transformed_page(stamp, matrix, over=not below)
    return _write(writer, output)


def number_pages(path, output, fmt="{n} / {total}", position="bottom-center",
                 font_size=10, start=1, pages=None, password=None):
    """Add page numbers. ``fmt`` may use {n} and {total}."""
    positions = ("bottom-center", "bottom-right", "bottom-left",
                 "top-center", "top-right", "top-left")
    if position not in positions:
        raise PdfManagerError(f"position must be one of {', '.join(positions)}")
    reader = open_pdf(path, password)
    total = len(reader.pages)
    targets = set(parse_page_ranges(pages, total)) if pages else None
    writer = clone_document(reader)
    shared = _shared_objects(writer)
    vert, horiz = position.split("-")
    margin = 28
    if not _NUMBER_FORMAT.fullmatch(fmt):
        raise PdfManagerError("the format may use {n} and {total}, with other braces as {{ and }}")

    n = start
    for i, page in enumerate(writer.pages):
        if targets is not None and i not in targets:
            continue
        label = fmt.format(n=n, total=total)
        n += 1
        _own_contents(page, shared)
        w, h, matrix = _upright(page)  # inside what the page shows, the right way up

        def draw(c, w, h, label=label):
            c.setFont(fonts.font_for(label), font_size)
            y = margin if vert == "bottom" else h - margin
            if horiz == "center":
                c.drawCentredString(w / 2, y, label)
            elif horiz == "right":
                c.drawRightString(w - margin, y, label)
            else:
                c.drawString(margin, y, label)

        page.merge_transformed_page(_overlay_page(w, h, draw), matrix)
    return _write(writer, output)


# {n}, {total}, {{ and }} are fine in a page number format; anything else in
# braces would be a KeyError from str.format.
_NUMBER_FORMAT = re.compile(r"(?:[^{}]|\{\{|\}\}|\{(?:n|total)(?::[^{}]*)?\})*")


_WIN_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
MAX_ATTACHMENT_BYTES = 50_000_000  # 50 MB default safety cap per extracted attachment (S-003/S-004)
MAX_TOTAL_ATTACHMENT_BYTES = 250_000_000  # 250 MB aggregate safety cap per PDF (REL-003)

_BIDI_AND_CONTROL = frozenset(
    "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\x7f"
)


def _safe_filename(name, fallback, limit=120):
    raw = str(name or "").replace("\x00", "").replace("\\", "/")
    # Strip Windows drive prefix (e.g. "C:foo" on POSIX) and take basename only.
    base = raw.rsplit("/", 1)[-1]
    if len(base) >= 2 and base[1] == ":" and base[0].isalpha():
        base = base[2:]
    cleaned = []
    for c in base:
        cp = ord(c)
        if cp < 32 or c in '<>:"|?*' or c in _BIDI_AND_CONTROL or 0xD800 <= cp <= 0xDFFF:
            cleaned.append("_")
        else:
            cleaned.append(c)
    name = "".join(cleaned).strip(" .")
    if name in ("", ".", ".."):
        return fallback
    stem, suffix = Path(name).stem, Path(name).suffix[:16]
    if stem.upper() in _WIN_RESERVED:
        stem = "_" + stem
    while len((stem + suffix).encode("utf-8", "ignore")) > limit and stem:  # file systems stop at 255 bytes
        stem = stem[:-1]
    return (stem + suffix).strip(" .") or fallback


def extract_media(path, outdir, images=True, attachments=True, min_size=16, password=None,
                  max_attachment_bytes=MAX_ATTACHMENT_BYTES,
                  max_total_attachment_bytes=MAX_TOTAL_ATTACHMENT_BYTES):
    """Save the pictures and/or attached files of a PDF into ``outdir``.
    Pictures are saved in their own format where possible (JPEG stays JPEG),
    each picture once even if it's used on several pages; pictures smaller
    than ``min_size`` pixels (lines, dots, tiny icons) are skipped.
    Attachments larger than ``max_attachment_bytes`` (default 50 MB; None for
    no limit) or exceeding ``max_total_attachment_bytes`` (default 250 MB) are
    skipped and recorded in ``saved["skipped"]`` (S-003, REL-003).
    Returns {"images": [paths], "attachments": [paths]}."""
    import hashlib

    reader = open_pdf(path, password)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    root_resolved = outdir.resolve()
    stem = Path(path).stem
    saved = {"images": [], "attachments": []}
    taken = set()

    def target(folder, name):
        if folder.is_symlink():
            raise PdfManagerError(f"refusing to extract into symbolic link {folder}")
        folder.mkdir(parents=True, exist_ok=True)
        folder_resolved = folder.resolve()
        if folder_resolved != root_resolved and root_resolved not in folder_resolved.parents:
            raise PdfManagerError(f"unsafe extraction directory outside {root_resolved}")
        candidate, n = folder / name, 1
        while candidate.name.lower() in taken or candidate.exists() or candidate.is_symlink():
            n += 1
            candidate = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        cand_resolved = candidate.resolve()
        if folder_resolved not in cand_resolved.parents or root_resolved not in cand_resolved.parents:
            raise PdfManagerError(f"unsafe extraction target path for {name!r}")
        taken.add(candidate.name.lower())
        return candidate

    if images:
        seen = set()
        for number, page in enumerate(reader.pages, 1):
            progress.report(number - 1, len(reader.pages), "page")
            try:
                found = list(page.images)
            except Exception:
                continue  # a damaged image dictionary: skip the page's pictures
            for k, image in enumerate(found, 1):
                try:
                    data = image.data
                    width, height = image.image.size if image.image is not None else (0, 0)
                except Exception:
                    continue
                if width < min_size or height < min_size:
                    continue
                digest = hashlib.sha256(data).digest()
                if digest in seen:
                    continue
                seen.add(digest)
                ext = Path(image.name).suffix.lower() or ".png"
                if ext in (".jp2", ".jpx") and image.image is not None:
                    # A photo with a soft mask comes back as JPEG 2000, which
                    # few programs open; PNG keeps the transparency too.
                    buf = io.BytesIO()
                    image.image.save(buf, "PNG")
                    data, ext = buf.getvalue(), ".png"
                out = target(outdir, f"{stem}_page{number}_{k}{ext}")
                try:
                    out.write_bytes(data)
                except OSError:
                    saved.setdefault("skipped", []).append(out.name)
                    continue
                saved["images"].append(out)
    if attachments:
        files = []
        entries = getattr(reader, "attachment_list", None)
        if entries is not None:  # pypdf 5.4+: one entry per attached file, with its file name
            for entry in entries:
                try:
                    files.append((entry.alternative_name or entry.name, entry.content))
                except Exception:  # a damaged entry
                    continue
        else:
            for name, contents in (reader.attachments or {}).items():
                files.extend((name, data) for data in contents)
        for page in reader.pages:
            for ref in page.get("/Annots") or []:
                annot = ref.get_object()
                if not isinstance(annot, DictionaryObject):
                    continue
                if annot.get("/Subtype") != "/FileAttachment" or "/FS" not in annot:
                    continue
                spec = annot["/FS"].get_object()
                if not isinstance(spec, DictionaryObject):  # a bare file name: nothing embedded
                    continue
                try:
                    data = spec["/EF"].get_object()["/F"].get_object().get_data()
                except (KeyError, AttributeError, TypeError):
                    continue
                files.append((spec.get("/UF") or spec.get("/F") or "attachment", data))
        seen = set()  # the same file listed under two names is saved once
        total_att_bytes = 0
        for name, data in files:
            safe_name = _safe_filename(str(name), "attachment")
            if max_attachment_bytes is not None and len(data) > max_attachment_bytes:
                saved.setdefault("skipped", []).append(f"{safe_name} (exceeds size limit)")
                continue
            if max_total_attachment_bytes is not None and total_att_bytes + len(data) > max_total_attachment_bytes:
                saved.setdefault("skipped", []).append(f"{safe_name} (exceeds total extraction limit)")
                continue
            digest = hashlib.sha256(data).digest()
            if digest in seen:
                continue
            seen.add(digest)
            out = target(outdir / "attachments", safe_name)
            try:
                out.write_bytes(data)
            except OSError:  # a name the file system won't take: the rest is still saved
                saved.setdefault("skipped", []).append(out.name)
                continue
            total_att_bytes += len(data)
            saved["attachments"].append(out)
    return saved


HEADER_SPOTS = ("top_left", "top_center", "top_right", "bottom_left", "bottom_center",
                "bottom_right")


_HEADER_TOKEN = re.compile(r"\{(n|total|file|title|date|bates)\}")


def _upright(page):
    """(visual width, visual height, matrix) to draw on ``page`` as it's
    shown: inside its crop box, and after its /Rotate, so text comes out
    upright."""
    box = page.cropbox
    w, h, left, bottom = float(box.width), float(box.height), float(box.left), float(box.bottom)
    rotation = quarter_turn(page.rotation)
    matrix = {0: (1, 0, 0, 1, 0, 0), 90: (0, 1, -1, 0, w, 0),
              180: (-1, 0, 0, -1, w, h), 270: (0, -1, 1, 0, 0, h)}[rotation]
    a, b, c, d, e, f = matrix
    visual = (h, w) if rotation in (90, 270) else (w, h)
    return visual[0], visual[1], Transformation((a, b, c, d, e + left, f + bottom))


def headers_footers(path, output, texts, font_size=9, margin_mm=10, pages=None, start=1,
                    bates_prefix="", bates_start=1, bates_digits=6, date_format="%Y-%m-%d",
                    password=None):
    """Add headers and footers. ``texts`` maps spots (see HEADER_SPOTS, e.g.
    "top_right") to text, which may use {n} (page number, counting from
    ``start``), {total}, {file}, {title}, {date} and {bates} (Bates number:
    ``bates_prefix`` and a number from ``bates_start`` with ``bates_digits``
    digits, e.g. ABC000123). Returns the next Bates number, so numbering can
    continue in the next file."""
    import datetime

    texts = {k: v for k, v in (texts or {}).items() if v and v.strip()}
    unknown = set(texts) - set(HEADER_SPOTS)
    if unknown:
        raise PdfManagerError(f"unknown position {sorted(unknown)[0]!r}")
    if not texts:
        raise PdfManagerError("enter the text for at least one header or footer")
    reader = open_pdf(path, password)
    total = len(reader.pages)
    targets = set(parse_page_ranges(pages, total)) if pages else None
    title = (reader.metadata or {}).get("/Title") or ""
    writer = clone_document(reader)
    shared = _shared_objects(writer)
    margin = margin_mm * POINTS_PER_MM
    fields = {"total": total, "file": Path(path).stem, "title": str(title),
              "date": datetime.date.today().strftime(date_format)}
    n, bates = start, bates_start
    for i, page in enumerate(writer.pages):
        progress.report(i, total, "page")
        if targets is not None and i not in targets:
            continue
        _own_contents(page, shared)
        fields.update(n=n, bates=f"{bates_prefix}{bates:0{bates_digits}d}")
        # Only the known {names} are filled in; other braces stay as typed.
        lines = {spot: _HEADER_TOKEN.sub(lambda m: str(fields[m.group(1)]), text)
                 for spot, text in texts.items()}
        n += 1
        bates += 1
        w, h, matrix = _upright(page)

        def draw(c, w, h, lines=lines):
            for spot, text in lines.items():
                c.setFont(fonts.font_for(text), font_size)  # a font that has the letters
                vert, horiz = spot.split("_")
                y = h - margin - font_size * 0.8 if vert == "top" else margin
                if horiz == "center":
                    c.drawCentredString(w / 2, y, text)
                elif horiz == "right":
                    c.drawRightString(w - margin, y, text)
                else:
                    c.drawString(margin, y, text)

        page.merge_transformed_page(_overlay_page(w, h, draw), matrix)
    _write(writer, output)
    return bates
