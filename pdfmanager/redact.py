# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Redaction: permanently black out text and areas.

Simply drawing a black box over text leaves the text in the file, where it
can be copied out. There are two ways to really get rid of it:

- "remove" (the default, like Acrobat): what lies under the boxes is taken
  out of the page's content (letters, the covered pixels of pictures,
  drawings, comments and form fields), and the rest of the page stays real
  text that can be searched and copied. See redactcontent.py. A page that
  can't be edited safely, or that fails the check afterwards, falls back
  to the second way, and the report says which pages and why.
- "image": every page with something to redact is replaced by a picture of
  the page with the black boxes burned in, so the text, images and
  comments on that page are gone for good. Such pages can be made
  searchable again with OCR (the blacked-out parts stay black).

Pages without redactions are left untouched either way. Bookmark titles
that contain the searched words have those words blacked out too (as
"███"), since they would otherwise give them away.
"""

import io
import re

from pypdf import PdfReader, Transformation
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, NameObject,
                           RectangleObject, TextStringObject)

from pdfmanager import core, progress

PAD = 1.5  # points added around found text
METHODS = ("remove", "image")
BLACKED = "███"  # what replaces the searched words in bookmark titles

# Ready-made patterns for things people often need to black out.
PRESETS = {
    "emails": r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
    "phones": r"(?<![\w.])\+?\(?\d[\d ()./-]{6,}\d(?![\w.])",
    "numbers": r"(?<!\w)\d(?:[ -]?\d){7,}(?!\w)",  # card, account and ID numbers
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",  # US Social Security Number
    "ein": r"\b\d{2}-\d{7}\b",  # US Employer Identification Number
    "uk_ni": r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b",  # UK National Insurance
    "iban": r"\b[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]){11,30}\b",  # EU/International IBAN
    "credit_card": r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)",  # payment cards (filtered with Luhn check)
}


def luhn_valid(value):
    """True when the digits in ``value`` pass the Luhn check (13–19 digits)."""
    digits = [int(ch) for ch in str(value) if ch.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for idx, digit in enumerate(reversed(digits)):
        if idx % 2 == 1:
            doubled = digit * 2
            total += doubled - 9 if doubled > 9 else doubled
        else:
            total += digit
    return total % 10 == 0


def find_text(path, terms=(), patterns=(), case_sensitive=False, whole_words=False,
              password=None):
    """Boxes around every match: {0-based page: [(x0, y0, x1, y1), ...]} in
    PDF points (origin bottom left, before the page's rotation). ``terms``
    are plain text, ``patterns`` regular expressions."""
    import pypdfium2 as pdfium

    regexes = _regexes(terms, patterns, case_sensitive, whole_words)
    luhn_pattern = PRESETS["credit_card"]
    found = {}
    if not regexes:
        return found
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path), password=password)
        try:
            for number, page in enumerate(doc):
                progress.report(number, len(doc), "searching page")
                textpage = page.get_textpage()
                text = textpage.get_text_range()
                boxes = []
                for regex in regexes:
                    check_luhn = regex.pattern == luhn_pattern
                    for match in regex.finditer(text):
                        if check_luhn and not luhn_valid(match.group(0)):
                            continue
                        boxes.extend(_match_boxes(textpage, match.start(), match.end()))
                textpage.close()
                if boxes:
                    found[number] = _distinct(boxes)
        finally:
            doc.close()
    return found


def _regexes(terms=(), patterns=(), case_sensitive=False, whole_words=False):
    regexes = []
    flags = 0 if case_sensitive else re.IGNORECASE
    for term in terms:
        term = term.strip()
        if term:
            # Any run of spaces in a term matches any whitespace (line breaks too).
            body = r"\s+".join(re.escape(part) for part in term.split())
            regexes.append(re.compile(rf"\b{body}\b" if whole_words else body, flags))
    for pattern in patterns:
        try:
            regexes.append(re.compile(pattern, flags))
        except re.error as exc:
            raise core.PdfManagerError(f"invalid pattern {pattern!r}: {exc}") from None
    return regexes


def _distinct(boxes, slack=1.0):
    """Drop boxes that lie inside another (when two searches find the same text)."""
    def inside(a, b):
        return (a[0] >= b[0] - slack and a[1] >= b[1] - slack and a[2] <= b[2] + slack
                and a[3] <= b[3] + slack)
    unique = []
    for box in sorted(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True):
        if not any(inside(box, other) for other in unique):
            unique.append(box)
    return unique


def _match_boxes(textpage, start, end):
    """One box per line of a match."""
    lines = []
    for i in range(start, end):
        x0, y0, x1, y1 = textpage.get_charbox(i)
        if x1 - x0 <= 0 and y1 - y0 <= 0:
            continue  # spaces and line breaks pdfium adds itself
        line = next((ln for ln in lines if y0 < ln[3] and y1 > ln[1]), None)
        if line is None:
            lines.append([x0, y0, x1, y1])
        else:
            line[0], line[1] = min(line[0], x0), min(line[1], y0)
            line[2], line[3] = max(line[2], x1), max(line[3], y1)
    boxes = []
    for x0, y0, x1, y1 in lines:
        h = y1 - y0
        boxes.append((x0 - PAD, y0 - 0.25 * h - PAD, x1 + PAD, y1 + 0.1 * h + PAD))
    return boxes


def _parse_box(spec):
    try:
        page, coords = spec.split(":", 1)
        x0, y0, x1, y1 = (float(v) for v in coords.split(","))
        return int(page) - 1, (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    except ValueError:
        raise core.PdfManagerError(
            f"invalid box {spec!r}; use PAGE:X0,Y0,X1,Y1 in points from the bottom left") \
            from None


class Report(dict):
    """{1-based page: number of areas blacked out}, plus what else happened:

    - ``method``: "remove" or "image";
    - ``pictures``: {1-based page: why} for the pages that were turned into
      pictures although the method was "remove" (empty for "image", where
      every redacted page becomes one);
    - ``bookmarks``: how many bookmark titles had words blacked out;
    - ``proof``: the check made on the written file (``check_redacted``), or
      None when nothing was redacted or the check couldn't be made.
    """

    def __init__(self, counts=(), method="remove", pictures=None, bookmarks=0, redaction_log=None):
        super().__init__(counts)
        self.method = method
        self.pictures = dict(pictures or {})
        self.bookmarks = bookmarks
        self.proof = None
        self.redaction_log = list(redaction_log or [])


def export_redaction_log(report, output, fmt="json"):
    """Export ``report.redaction_log`` (page, box, reason, actor, timestamp) to JSON or CSV."""
    import csv
    import json
    from pathlib import Path

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    entries = list(getattr(report, "redaction_log", None) or [])
    fmt = str(fmt).lower()
    if fmt == "json":
        out.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    elif fmt == "csv":
        fields = ["page", "box", "reason", "actor", "timestamp", "method"]
        def _safe_cell(val):
            s = "" if val is None else str(val)
            return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s
        with out.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for item in entries:
                row = dict(item)
                if isinstance(row.get("box"), (list, tuple)):
                    row["box"] = ",".join(str(v) for v in row["box"])
                writer.writerow({k: _safe_cell(row.get(k, "")) for k in fields})
    else:
        raise core.PdfManagerError(f"unsupported redaction log format {fmt!r} (use 'json' or 'csv')")
    return out


# Short/generic standalone words skipped when inferring document-wide sanitization
# terms from coordinate-based redaction boxes (R-001), to avoid false positives.
_COMMON_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "have", "been", "were",
    "will", "your", "into", "over", "also", "more", "some", "such", "than", "then",
    "they", "them", "when", "what", "which", "where", "page", "date", "name",
    "total", "item", "note", "notes", "none", "true", "false", "copy", "file",
})


def _is_safe_sanitization_term(term):
    """Return True if ``term`` is specific enough to propagate into document-wide
    sanitization and verification without matching unrelated common prose."""
    cleaned = " ".join(str(term or "").split())
    if not cleaned:
        return False
    compact = re.sub(r"\s+", "", cleaned)
    if len(compact) < 3:
        return False
    # Reject pure punctuation/symbols
    if not any(ch.isalnum() for ch in compact):
        return False
    words = cleaned.split()
    if len(words) == 1:
        low = cleaned.lower()
        if low in _COMMON_STOPWORDS:
            return False
        if len(compact) == 3 and not any(ch.isdigit() or not ch.isalnum() for ch in compact):
            return False
    else:
        if all(w.lower() in _COMMON_STOPWORDS for w in words):
            return False
    return True


def _normalize_extracted_terms(raw_texts):
    """Normalize extracted text snippets into deduplicated sanitization terms."""
    terms, seen = [], set()
    for raw in raw_texts:
        if not raw:
            continue
        for line in str(raw).splitlines():
            cleaned = " ".join(line.split())
            if not _is_safe_sanitization_term(cleaned):
                continue
            key = cleaned.lower()
            if key not in seen:
                seen.add(key)
                terms.append(cleaned)
    return terms


def _extract_box_terms(path, boxes, password=None):
    """Extract and normalize sensitive text inside coordinate-based ``boxes``
    ({0-based page: [(x0, y0, x1, y1), ...]}) before removal (R-001)."""
    if not boxes:
        return []
    import pypdfium2 as pdfium

    snippets = []
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path), password=password)
        try:
            for page_num, page_boxes in boxes.items():
                if not 0 <= int(page_num) < len(doc):
                    continue
                textpage = doc[int(page_num)].get_textpage()
                try:
                    for x0, y0, x1, y1 in page_boxes:
                        raw = textpage.get_text_bounded(
                            min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
                        )
                        if raw and raw.strip():
                            snippets.append(raw)
                finally:
                    textpage.close()
        finally:
            doc.close()
    return _normalize_extracted_terms(snippets)


def _box_regexes(box_terms, case_sensitive=False):
    """Build regexes for coordinate-extracted terms (using word boundaries on
    short single tokens to avoid accidentally matching unrelated words)."""
    regexes = []
    flags = 0 if case_sensitive else re.IGNORECASE
    for term in box_terms:
        parts = term.split()
        if not parts:
            continue
        body = r"\s+".join(re.escape(part) for part in parts)
        use_bounds = len(parts) == 1 and len(term) <= 4 and term.isalnum()
        regexes.append(re.compile(rf"\b{body}\b" if use_bounds else body, flags))
    return regexes


def redact(path, output, terms=(), patterns=(), boxes=None, case_sensitive=False,
           whole_words=False, dpi=200, password=None, method="remove",
           reason_code=None, actor=None):
    """Black out every match of ``terms``/``patterns`` and the given
    ``boxes`` ({0-based page: [(x0, y0, x1, y1)]} in PDF points, or
    "PAGE:X0,Y0,X1,Y1" strings with 1-based pages). ``method`` "remove"
    takes out what's under the boxes and keeps the rest of the page as it
    was; "image" turns the redacted pages into pictures (``dpi``).
    Optional ``reason_code`` (e.g. FOIA ``"(b)(6)"`` or ``"GDPR Art. 17"``)
    is recorded in ``report.redaction_log`` and stamped on rasterized boxes.
    Returns a :class:`Report`: {1-based page: number of areas blacked out}."""
    if method not in METHODS:
        raise core.PdfManagerError(f"unknown redaction method {method!r}; use "
                                   + " or ".join(METHODS))
    reader = core.open_pdf(path, password)
    regexes = _regexes(terms, patterns, case_sensitive, whole_words)
    areas = find_text(path, terms, patterns, case_sensitive, whole_words, password)
    if isinstance(boxes, (list, tuple)):
        parsed = {}
        for spec in boxes:
            page, box = _parse_box(spec)
            parsed.setdefault(page, []).append(box)
        boxes = parsed
    coord_boxes = {}
    for page, page_boxes in (boxes or {}).items():
        if not 0 <= int(page) < len(reader.pages):
            raise core.PdfManagerError(f"page {int(page) + 1} doesn't exist")
        norm_boxes = [tuple(map(float, b)) for b in page_boxes]
        coord_boxes.setdefault(int(page), []).extend(norm_boxes)
        areas.setdefault(int(page), []).extend(norm_boxes)
    # R-001: extract text from coordinate-based boxes before removal so it is
    # propagated into document-wide sanitization and post-redaction verification.
    box_terms = _extract_box_terms(path, coord_boxes, password)
    writer = core.clone_document(reader)
    pictures, bookmarks = {}, 0
    if areas:
        removed_widgets = set()
        removed = {}
        if method == "remove":
            from pdfmanager import redactcontent

            data, pictures, removed = redactcontent.remove(path, password, areas, regexes)
            writer = core.clone_document(PdfReader(io.BytesIO(data)))
            kept = {n: found for n, found in areas.items() if n not in pictures}
            for number, found in kept.items():
                removed_widgets |= _drop_annotations(writer.pages[number], found)
            if coord_boxes and removed:
                # Merge any text actually removed from coordinate-redacted pages
                # (handles text split across PDF content objects or Form XObjects).
                extra_terms = _normalize_extracted_terms(
                    removed.get(n, "") for n in coord_boxes if n in removed
                )
                seen_low = {t.lower() for t in box_terms}
                for t in extra_terms:
                    if t.lower() not in seen_low:
                        seen_low.add(t.lower())
                        box_terms.append(t)
            sanitize_regexes = list(regexes) + _box_regexes(box_terms, case_sensitive)
            _prune_alt_text(writer, {n: removed.get(n, "") for n in kept}, sanitize_regexes)
            to_picture = {n: areas[n] for n in pictures}
        else:
            sanitize_regexes = list(regexes) + _box_regexes(box_terms, case_sensitive)
            to_picture = areas
        if to_picture:
            replaced_pages = []
            for number, image, box in _iter_render_redacted(
                path, to_picture, dpi, password, reason_code=reason_code
            ):
                try:
                    removed_widgets |= _replace_page(writer, writer.pages[number], image, box)
                finally:
                    image.close()
                replaced_pages.append(writer.pages[number])
            _prune_structure(writer, replaced_pages)
        _prune_fields(writer, removed_widgets)
        bookmarks = _scrub_outline(writer, sanitize_regexes)
        # pypdf writes every object it holds, unlinked or not, so the old
        # content, pictures and fields would still sit in the file. A copy
        # made from the written result carries only what is reachable.
        buf = io.BytesIO()
        writer.write(buf)
        buf.seek(0)
        writer = core.clone_document(PdfReader(buf))
    core._write(writer, output)

    from datetime import datetime, timezone
    import getpass
    from pdfmanager import auditlog

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if actor:
        who = str(actor)
    else:
        try:
            who = getpass.getuser()
        except Exception:
            who = "unknown"
    reason_str = str(reason_code).strip() if reason_code else ""
    redaction_log = [
        {
            "page": number + 1,
            "box": [round(float(v), 2) for v in b],
            "reason": reason_str,
            "actor": who,
            "timestamp": ts,
            "method": "image" if (method == "image" or number in pictures) else "remove",
        }
        for number, found in sorted(areas.items())
        for b in found
    ]
    report = Report(
        {number + 1: len(found) for number, found in sorted(areas.items())},
        method,
        {n + 1: why for n, why in sorted(pictures.items())},
        bookmarks,
        redaction_log=redaction_log,
    )
    if areas:
        # Look again, in the file as written: is anything redacted still kept somewhere?
        check_terms = list(terms)
        seen_terms = {t.lower() for t in check_terms}
        for t in box_terms:
            if t.lower() not in seen_terms:
                seen_terms.add(t.lower())
                check_terms.append(t)
        try:
            report.proof = check_redacted(
                output, check_terms, patterns, case_sensitive, whole_words, areas, password
            )
        except Exception as exc:  # noqa: BLE001 - the check failing must not lose the redacted file
            report.proof = {
                "clean": False,
                "status": STATUS_INCOMPLETE,
                "checked": [],
                "found": [],
                "skipped": [{"where": "verification", "reason": str(exc) or type(exc).__name__}],
                "warnings": [f"Redaction verification could not complete: {exc}"],
                "checks": {},
            }
        try:
            auditlog.record(
                "redact",
                target=path,
                actor=who,
                details={
                    "pages": len(areas),
                    "boxes": len(redaction_log),
                    "method": method,
                    "reason": reason_str,
                },
            )
        except OSError:
            pass  # never fail redaction if the local audit file is unwritable
    return report


def _drop_annotations(page, areas):
    """Remove the comments, links and form fields on ``page`` that touch
    one of ``areas`` (and the pop-ups of removed comments). Returns ids of
    removed form widgets."""
    annots = page.get("/Annots")
    annots = annots.get_object() if annots is not None else None
    if not isinstance(annots, ArrayObject):
        return set()
    gone, widgets, kept, broken = set(), set(), [], False
    for ref in annots:
        annot = ref.get_object() if ref is not None else None
        if not isinstance(annot, DictionaryObject):
            broken = True  # dangling or broken: nothing worth keeping
            continue
        try:
            x0, y0, x1, y1 = (float(v) for v in annot.get("/Rect", [0, 0, 0, 0]))
        except (TypeError, ValueError):
            x0 = y0 = x1 = y1 = 0.0
        box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        if any(box[0] < a[2] and box[2] > a[0] and box[1] < a[3] and box[3] > a[1] for a in areas):
            gone.add(getattr(ref, "idnum", id(annot)))
            if annot.get("/Subtype") == "/Widget":
                widgets.add(getattr(ref, "idnum", None))
            continue
        kept.append((ref, annot))
    if not gone and not broken:
        return set()
    result = ArrayObject()
    for ref, annot in kept:
        parent = annot.raw_get("/Parent") if "/Parent" in annot else None
        if annot.get("/Subtype") == "/Popup" and getattr(parent, "idnum", None) in gone:
            continue
        popup = annot.raw_get("/Popup") if "/Popup" in annot else None
        if getattr(popup, "idnum", None) in gone:
            del annot["/Popup"]
        result.append(ref)
    page[NameObject("/Annots")] = result
    return widgets - {None}


def _prune_alt_text(writer, removed, regexes):
    """Drop the structure tree's replacement texts (/ActualText, /Alt, /E)
    on pages in ``removed`` ({0-based page: the text taken off it}) that
    repeat a removed or searched word: a tagged PDF can carry the words
    there a second time."""
    tree = writer._root_object.get("/StructTreeRoot")
    if tree is None or not removed:
        return
    pages = {writer.pages[n].indirect_reference.idnum: n for n in removed}
    words = {n: {w.lower() for w in re.findall(r"\w[\w@.'-]*\w", text)}
             for n, text in removed.items()}

    def repeats(value, n):
        value = str(value)
        low = value.lower()
        return any(r.search(value) for r in regexes) or any(w in low for w in words[n])

    pending = [(tree.get_object(), None)]
    seen = set()
    while pending:
        node, page = pending.pop()
        if not isinstance(node, DictionaryObject) or id(node) in seen:
            continue
        seen.add(id(node))
        if "/Pg" in node:
            page = pages.get(getattr(node.raw_get("/Pg"), "idnum", None))
        if page is not None:
            for key in ("/ActualText", "/Alt", "/E"):
                if key in node and repeats(node[key], page):
                    del node[key]
        kids = node.get("/K")
        kids = kids.get_object() if kids is not None else None
        for kid in (kids if isinstance(kids, ArrayObject) else [kids]):
            kid = kid.get_object() if kid is not None else None
            if isinstance(kid, DictionaryObject):
                pending.append((kid, page))


def _scrub_outline(writer, regexes):
    """Black out the searched words in bookmark titles, which would give
    them away otherwise. Returns how many titles changed."""
    outlines = writer._root_object.get("/Outlines")
    if outlines is None or not regexes:
        return 0
    changed = 0
    pending = [outlines.get_object()]
    seen = set()
    while pending:
        node = pending.pop()
        if not isinstance(node, DictionaryObject) or id(node) in seen:
            continue
        seen.add(id(node))
        title = node.get("/Title")
        if title is not None:
            new = str(title)
            for regex in regexes:
                new = regex.sub(BLACKED, new)
            if new != str(title):
                node[NameObject("/Title")] = TextStringObject(new)
                changed += 1
        for key in ("/First", "/Next"):
            if node.get(key) is not None:
                pending.append(node[key].get_object())
    return changed


def _visible_box(page):
    """The part of the page a reader shows and pdfium renders: the crop box
    within the media box (a crop box reaching outside it is cut off)."""
    media = page.get_mediabox()
    crop = page.get_cropbox()
    box = (max(media[0], crop[0]), max(media[1], crop[1]), min(media[2], crop[2]),
           min(media[3], crop[3]))
    return box if box[2] > box[0] and box[3] > box[1] else media


def _iter_render_redacted(path, areas, dpi, password, reason_code=None):
    """Yield ``(page_number, PIL image with black boxes, visible_box)`` one page at a time.

    Streaming avoids holding all rendered pages simultaneously in memory on
    large documents. Callers should close each yielded PIL image after use.
    """
    import pypdfium2 as pdfium
    from PIL import ImageDraw

    label = str(reason_code).strip() if reason_code else ""
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path), password=password)
        try:
            doc.init_forms()  # without this, filled-in fields are left out of the picture
        except Exception:
            doc.close()
            raise
    try:
        for n, (number, boxes) in enumerate(areas.items()):
            progress.check()
            progress.report(n, len(areas), "blacking out page")
            with core.PDFIUM_LOCK:
                page = doc[number]
                try:
                    page.set_rotation(0)  # draw in the page's own coordinates
                    left, bottom, right, top = _visible_box(page)
                    scale = core.render_scale(page, dpi / 72)
                    image = page.render(scale=scale, may_draw_forms=True).to_pil().convert("RGB")
                finally:
                    page.close()
            draw = ImageDraw.Draw(image)
            for x0, y0, x1, y1 in boxes:
                rx0, ry0 = (x0 - left) * scale, (top - y1) * scale
                rx1, ry1 = (x1 - left) * scale, (top - y0) * scale
                draw.rectangle([rx0, ry0, rx1, ry1], fill=(0, 0, 0))
                if label:
                    draw.text((rx0 + 2, ry0 + 1), label, fill=(255, 255, 255))
            yield number, image, (left, bottom, right, top)
    finally:
        with core.PDFIUM_LOCK:
            doc.close()


def _render_redacted(path, areas, dpi, password, reason_code=None):
    """{page: (PIL image with black boxes, visible box)} at ``dpi``."""
    return {number: (image, box)
            for number, image, box in _iter_render_redacted(
                path, areas, dpi, password, reason_code=reason_code
            )}


def _is_grayscale(image):
    """True when an RGB image has identical R, G, B channels without full tobytes() copies."""
    if image.mode == "L":
        return True
    if image.mode != "RGB":
        return False
    try:
        import numpy as np
        arr = np.asarray(image)
        return bool((arr[:, :, 0] == arr[:, :, 1]).all() and (arr[:, :, 1] == arr[:, :, 2]).all())
    except ImportError:
        from PIL import ImageChops
        r, g, b = image.split()
        return (ImageChops.difference(r, g).getbbox() is None
                and ImageChops.difference(g, b).getbbox() is None)


def _image_page(image, width, height):
    """A one-page PDF showing ``image`` at width x height points."""
    from reportlab.lib.utils import ImageReader

    converted = False
    if _is_grayscale(image) and image.mode != "L":
        image = image.convert("L")  # black and white or gray: a third of the size
        converted = True
    try:
        buf = io.BytesIO()
        c = core.pdf_canvas(buf, pagesize=(width, height))
        c.drawImage(ImageReader(image), 0, 0, width, height)
        c.showPage()
        c.save()
        return PdfReader(buf).pages[0]
    finally:
        if converted:
            image.close()


def _replace_page(writer, page, image, box):
    """Replace everything on ``page`` with ``image`` (keeping its size,
    rotation and links into it). Returns ids of removed form widgets."""
    left, bottom, right, top = box
    removed = set()
    for ref in page.get("/Annots") or []:
        annot = ref.get_object()
        if isinstance(annot, DictionaryObject) and annot.get("/Subtype") == "/Widget":
            removed.add(getattr(ref, "idnum", None))
    for key in ("/Annots", "/Contents", "/Resources", "/Group", "/Thumb", "/PieceInfo",
                "/Metadata", "/AA", "/StructParents"):
        if key in page:
            del page[key]
    empty = DecodedStreamObject()
    empty.set_data(b"")
    page[NameObject("/Contents")] = writer._add_object(empty)
    page[NameObject("/Resources")] = DictionaryObject()
    # Nothing is left outside the visible area, which is what the picture shows.
    page.mediabox = page.cropbox = RectangleObject(box)
    page.merge_transformed_page(_image_page(image, right - left, top - bottom),
                                Transformation().translate(left, bottom))
    return removed - {None}


def _prune_structure(writer, pages):
    """Drop the structure tree's entries for the redacted ``pages``: a
    tagged PDF repeats their words there (/ActualText, /Alt)."""
    root = writer._root_object
    tree = root.get("/StructTreeRoot")
    if tree is None:
        return
    tree = tree.get_object()
    gone = {page.indirect_reference.idnum for page in pages}

    def on_page(obj):
        return getattr(obj.get("/Pg"), "idnum", None) in gone

    def prune(items):
        kept = ArrayObject()
        for item in items:
            obj = item.get_object()
            if isinstance(obj, DictionaryObject):
                if on_page(obj):
                    continue
                kids = obj.get("/K")
                if kids is not None:
                    kids = kids.get_object()
                    obj[NameObject("/K")] = prune(
                        kids if isinstance(kids, ArrayObject) else ArrayObject([obj.raw_get("/K")]))
            kept.append(item)
        return kept

    kids = tree.get("/K")
    if kids is not None:
        kids = kids.get_object()
        tree[NameObject("/K")] = prune(kids if isinstance(kids, ArrayObject)
                                       else ArrayObject([tree.raw_get("/K")]))
    # The parent tree points at the same elements, by page.
    pending = [tree.get("/ParentTree")]
    while pending:
        node = pending.pop()
        node = node.get_object() if node is not None else None
        if not isinstance(node, DictionaryObject):
            continue
        pending.extend(node.get("/Kids", []))
        nums = node.get("/Nums")
        if nums is None:
            continue
        nums = nums.get_object()
        kept = ArrayObject()
        for key, value in zip(nums[0::2], nums[1::2]):
            entry = value.get_object()
            if isinstance(entry, ArrayObject):
                entry = prune(entry)
                if not entry:
                    continue
                value = entry
            elif isinstance(entry, DictionaryObject) and on_page(entry):
                continue
            kept.extend([key, value])
        node[NameObject("/Nums")] = kept


def _prune_fields(writer, widget_ids):
    """Drop form fields whose widgets were on redacted pages."""
    if not widget_ids or "/AcroForm" not in writer._root_object:
        return
    acroform = writer._root_object["/AcroForm"].get_object()

    def keep(ref):
        field = ref.get_object()
        if getattr(ref, "idnum", None) in widget_ids:
            return False
        kids = field.get("/Kids")
        if kids is not None:
            remaining = ArrayObject(k for k in kids if keep(k))
            if not remaining:
                return False
            field[NameObject("/Kids")] = remaining
        return True

    acroform[NameObject("/Fields")] = ArrayObject(f for f in acroform.get("/Fields", []) if keep(f))


# -- after Black out: is anything redacted still in the file? ---------------------------------

INFO_NAMES = {"/Title": "title", "/Author": "author", "/Subject": "subject", "/Keywords": "keywords",
              "/Creator": "made with", "/Producer": "made with"}
DEEP_STREAM = 5_000_000  # bytes: larger inner data isn't searched (pictures aren't either)
MAX_EMBED_DEPTH = 3
MAX_ARCHIVE_ENTRIES = 200
MAX_ARCHIVE_DECOMPRESSED = 10 * 1024 * 1024  # 10 MB total decompressed cap per archive
MAX_COMPRESSION_RATIO = 100

_TEXT_ATTACHMENT_SUFFIXES = frozenset({
    ".txt", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".md",
    ".rtf", ".log", ".yaml", ".yml", ".ini", ".cfg", ".svg",
})
_ARCHIVE_ATTACHMENT_SUFFIXES = frozenset({
    ".zip", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
})
_XML_TAG_RE = re.compile(r"<[^>]+>")

STATUS_CLEAN = "verified_clean"
STATUS_FINDINGS = "findings_detected"
STATUS_INCOMPLETE = "verification_incomplete"


def _strip_xml_text(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    return _XML_TAG_RE.sub(" ", text)


def _read_zip_member_capped(zf, info, remaining: int) -> tuple[bytes | None, str | None]:
    """Read a zip member with strict streaming byte cap (guards against spoofed headers)."""
    if info.flag_bits & 0x1:
        return None, "password-protected archive entry"
    if info.file_size > remaining:
        return None, f"decompressed size exceeds {MAX_ARCHIVE_DECOMPRESSED}-byte limit"
    if info.compress_size > 0 and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
        return None, f"compression ratio exceeds {MAX_COMPRESSION_RATIO}x limit (possible archive bomb)"
    chunks = []
    total = 0
    with zf.open(info, "r") as stream:
        while True:
            buf = stream.read(65536)
            if not buf:
                break
            total += len(buf)
            if total > remaining:
                return None, f"decompressed size exceeds {MAX_ARCHIVE_DECOMPRESSED}-byte limit"
            if info.compress_size > 0 and total / info.compress_size > MAX_COMPRESSION_RATIO:
                return None, f"compression ratio exceeds {MAX_COMPRESSION_RATIO}x limit (possible archive bomb)"
            chunks.append(buf)
    return b"".join(chunks), None


def _inspect_attachment_bytes(
    name: str,
    raw_att: bytes,
    *,
    terms,
    patterns,
    case_sensitive: bool,
    whole_words: bool,
    search,
    note,
    skip_attachment,
    attachments_checked: list,
    depth: int = 0,
) -> bool:
    """Format-aware recursive inspection of an embedded attachment.

    Returns True if any part of this attachment could not be completely verified.
    """
    import io
    import tempfile
    import zipfile
    from pathlib import Path

    incomplete = False
    label = f"attached file ({name})"
    if depth > MAX_EMBED_DEPTH:
        skip_attachment(name, f"exceeded maximum nesting depth ({MAX_EMBED_DEPTH})")
        return True
    size_limit = min(DEEP_STREAM, MAX_ARCHIVE_DECOMPRESSED)
    if len(raw_att) > size_limit:
        skip_attachment(name, f"exceeds {size_limit}-byte size limit ({len(raw_att)} bytes)")
        return True

    suffix = Path(name).suffix.lower()

    # 1. Embedded PDF (by extension or magic header)
    if suffix == ".pdf" or raw_att[:5] == b"%PDF-":
        try:
            with tempfile.TemporaryDirectory(prefix="zpdf-att-") as tmp:
                sub_pdf = Path(tmp) / "embedded.pdf"
                sub_pdf.write_bytes(raw_att)
                try:
                    sub_reader = core.open_pdf(sub_pdf)
                except core.PdfManagerError as exc:
                    skip_attachment(name, f"unreadable or password-protected embedded PDF ({exc})")
                    return True
                if sub_reader.is_encrypted:
                    skip_attachment(name, "password-protected embedded PDF")
                    return True
                sub_res = check_redacted(
                    sub_pdf,
                    terms=terms,
                    patterns=patterns,
                    case_sensitive=case_sensitive,
                    whole_words=whole_words,
                    _depth=depth + 1,
                )
            attachments_checked.append(name)
            for item in sub_res.get("found") or ():
                note(f"inside {label} -> {item['where']}", item["what"])
            if sub_res.get("status") == STATUS_INCOMPLETE or sub_res.get("skipped"):
                for sk in sub_res.get("skipped") or ():
                    skip_attachment(f"{name}/{sk['where']}", sk["reason"])
                incomplete = True
            return incomplete
        except Exception as exc:  # noqa: BLE001
            skip_attachment(name, f"embedded PDF inspection error: {exc}")
            return True

    # 2. Office Open XML (.docx/.xlsx/.pptx/.odt/...) or ZIP archive
    if suffix in _ARCHIVE_ATTACHMENT_SUFFIXES or raw_att[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(raw_att)) as zf:
                infos = zf.infolist()
                if len(infos) > MAX_ARCHIVE_ENTRIES:
                    skip_attachment(name, f"archive exceeds {MAX_ARCHIVE_ENTRIES}-entry limit ({len(infos)} entries)")
                    return True
                remaining = MAX_ARCHIVE_DECOMPRESSED
                attachments_checked.append(name)
                for info in infos:
                    if info.is_dir():
                        continue
                    member_label = f"{name}!{info.filename}"
                    data, err = _read_zip_member_capped(zf, info, remaining)
                    if err is not None:
                        skip_attachment(member_label, err)
                        incomplete = True
                        continue
                    remaining -= len(data)
                    msuf = Path(info.filename).suffix.lower()
                    if msuf == ".pdf" or data[:5] == b"%PDF-" or msuf in _ARCHIVE_ATTACHMENT_SUFFIXES or data[:4] == b"PK\x03\x04":
                        sub_inc = _inspect_attachment_bytes(
                            member_label,
                            data,
                            terms=terms,
                            patterns=patterns,
                            case_sensitive=case_sensitive,
                            whole_words=whole_words,
                            search=search,
                            note=note,
                            skip_attachment=skip_attachment,
                            attachments_checked=attachments_checked,
                            depth=depth + 1,
                        )
                        if sub_inc:
                            incomplete = True
                    elif msuf in (".xml", ".rels", ".html", ".htm", ".svg"):
                        search(_strip_xml_text(data), f"inside {label} ({info.filename})")
                    elif msuf in _TEXT_ATTACHMENT_SUFFIXES or b"\x00" not in data:
                        for encoding in ("utf-8", "utf-16"):
                            if search(data.decode(encoding, "ignore"), f"inside {label} ({info.filename})"):
                                break
                    # Binary media inside Office docs (e.g. word/media/image1.png) cannot be text-verified;
                    # skip only if non-Office ZIP contains unsupported binaries, or flag image binaries explicitly?
                    # For Office XML, text lives in XML parts; still flag unknown non-image binaries in raw ZIPs.
                    elif suffix == ".zip":
                        skip_attachment(member_label, f"unsupported binary archive entry ({msuf or 'binary'})")
                        incomplete = True
                return incomplete
        except zipfile.BadZipFile as exc:
            skip_attachment(name, f"corrupt archive ({exc})")
            return True
        except Exception as exc:  # noqa: BLE001
            skip_attachment(name, f"archive inspection error: {exc}")
            return True

    # 3. Plain / structured text formats
    if suffix in _TEXT_ATTACHMENT_SUFFIXES:
        attachments_checked.append(name)
        for encoding in ("utf-8", "utf-16"):
            if search(raw_att.decode(encoding, "ignore"), f"inside {label}"):
                break
        return False

    # 4. UTF-8 text without a known extension (no NUL bytes and valid UTF-8)
    if b"\x00" not in raw_att:
        try:
            decoded = raw_att.decode("utf-8")
            attachments_checked.append(name)
            search(decoded, f"inside {label}")
            return False
        except UnicodeDecodeError:
            pass

    # 5. Unsupported binary format: scan raw bytes best-effort for findings, AND mark incomplete
    # so we never falsely report verified_clean on an unverified binary attachment.
    for encoding in ("utf-8", "utf-16"):
        if search(raw_att[:DEEP_STREAM].decode(encoding, "ignore"), f"inside {label}"):
            break
    skip_attachment(name, f"unsupported attachment format ({suffix or 'binary'})")
    return True


def check_redacted(path, terms=(), patterns=(), case_sensitive=False, whole_words=False, areas=None,
                   password=None, _depth=0):
    """Look through the PDF at ``path`` for what should be gone: the words
    and kinds of numbers that were redacted (``terms``, ``patterns``),
    anywhere they could be kept, and any text left under the redacted
    ``areas`` ({0-based page: [boxes]}).

    Returns ``{"clean", "status", "checked", "found", "skipped", "warnings",
    "checks", "attachments_checked", "attachments_skipped", "inspection_errors"}``
    where ``status`` is one of ``verified_clean``, ``findings_detected``, or
    ``verification_incomplete``. ``clean`` is True only when
    ``status == "verified_clean"`` (R-003, SEC-003)."""
    import pypdfium2 as pdfium

    regexes = _regexes(terms, patterns, case_sensitive, whole_words)
    found, seen = [], set()
    skipped, warnings, checks = [], [], {}
    attachments_checked, attachments_skipped, inspection_errors = [], [], []

    def note(where, what):
        what = " ".join(str(what).split())[:80]
        if (where, what) not in seen:
            seen.add((where, what))
            found.append({"where": where, "what": what})

    def skip(where, reason, check_key=None):
        reason_str = str(reason) or "unreadable"
        skipped.append({"where": where, "reason": reason_str})
        warnings.append(f"Skipped {where}: {reason_str}")
        inspection_errors.append({"where": where, "reason": reason_str})
        if check_key and checks.get(check_key) != "findings":
            checks[check_key] = "incomplete"

    def skip_attachment(att_name, reason):
        reason_str = str(reason) or "unreadable"
        attachments_skipped.append({"name": att_name, "reason": reason_str})
        skip(f"attached file ({att_name})", reason_str, "attachments")

    def search(text, where):
        hit = False
        for regex in regexes:
            for match in regex.finditer(text or ""):
                note(where, match.group(0))
                hit = True
        return hit

    checked = ["page text"]
    in_pages = set()
    before_pages = len(found)
    before_boxes = 0
    box_hits = False
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path), password=password)
        try:
            for number, page in enumerate(doc):
                textpage = page.get_textpage()
                try:
                    before = len(found)
                    search(textpage.get_text_range(), f"the text of page {number + 1}")
                    in_pages.update(f["what"] for f in found[before:])
                    for x0, y0, x1, y1 in (areas or {}).get(number, ()):
                        left = textpage.get_text_bounded(min(x0, x1) + 0.5, min(y0, y1) + 0.5,
                                                         max(x0, x1) - 0.5, max(y0, y1) - 0.5).strip()
                        if left:
                            before_boxes += 1
                            box_hits = True
                            note(f"under a box on page {number + 1}", left)
                finally:
                    textpage.close()
        finally:
            doc.close()
    checks["page_text"] = "findings" if (len(found) - before_pages - before_boxes) > 0 else "passed"
    if areas:
        checked.append("under the boxes")
        checks["under_boxes"] = "findings" if box_hits else "passed"

    reader = core.open_pdf(path, password)
    root = reader.trailer["/Root"].get_object()
    checked.append("document details")
    before_meta = len(found)
    try:
        info = reader.trailer.get("/Info")
        info = info.get_object() if info is not None else {}
        for key, value in info.items():
            search(str(value), f"the document's details ({INFO_NAMES.get(key, key.lstrip('/').lower())})")
        meta = root.get("/Metadata")
        if meta is not None:
            search(meta.get_object().get_data().decode("utf-8", "replace"), "the document's details (XMP)")
        checks["metadata"] = "findings" if len(found) > before_meta else "passed"
    except Exception as exc:  # noqa: BLE001 - report unreadable metadata as incomplete verification
        skip("document details", exc, "metadata")

    checked.append("bookmarks")
    from pdfmanager import risks

    before_bm = len(found)
    try:
        for item in risks._walk(root.get("/Outlines"), "/First", "/Next"):
            if "/Title" in item:
                search(str(item["/Title"]), "a bookmark")
        checks["bookmarks"] = "findings" if len(found) > before_bm else "passed"
    except Exception as exc:  # noqa: BLE001
        skip("bookmarks", exc, "bookmarks")

    checked.append("form values")
    before_forms = len(found)
    try:
        acroform = root.get("/AcroForm")
        if acroform is not None:
            for field in risks._walk(acroform.get_object().get("/Fields"), "/Kids"):
                for key in ("/V", "/DV", "/TU"):
                    if key in field:
                        search(str(field[key].get_object()), f"a form field ({field.get('/T', 'unnamed')})")
        checks["form_values"] = "findings" if len(found) > before_forms else "passed"
    except Exception as exc:  # noqa: BLE001
        skip("form values", exc, "form_values")

    checked.append("comments")
    before_comments = len(found)
    try:
        for number, page in enumerate(reader.pages):
            for ref in page.get("/Annots") or []:
                annot = ref.get_object()
                if not isinstance(annot, DictionaryObject) or annot.get("/Subtype") in ("/Widget", "/Link"):
                    continue
                for key in ("/Contents", "/RC", "/Subj"):
                    if key in annot:
                        search(str(annot[key].get_object()), f"a comment on page {number + 1}")
        checks["comments"] = "findings" if len(found) > before_comments else "passed"
    except Exception as exc:  # noqa: BLE001
        skip("comments", exc, "comments")

    checked.append("attachments")
    before_att = len(found)
    att_incomplete = False
    try:
        for name, _ in risks._attached(root, reader.pages):
            search(name, f"the name of an attached file ({name})")
        for name, contents in (reader.attachments or {}).items():
            for data in contents if isinstance(contents, list) else [contents]:
                raw_att = bytes(data)
                if _inspect_attachment_bytes(
                    name,
                    raw_att,
                    terms=terms,
                    patterns=patterns,
                    case_sensitive=case_sensitive,
                    whole_words=whole_words,
                    search=search,
                    note=note,
                    skip_attachment=skip_attachment,
                    attachments_checked=attachments_checked,
                    depth=_depth,
                ):
                    att_incomplete = True
        if len(found) > before_att:
            checks["attachments"] = "findings"
        elif att_incomplete:
            checks["attachments"] = "incomplete"
        else:
            checks["attachments"] = "passed"
    except Exception as exc:  # noqa: BLE001 - report unreadable attachment as incomplete verification
        skip("attachments", exc, "attachments")

    # The rest of the file's data: anything written in the open that the
    # places above don't cover.
    if regexes:
        checked.append("inner data")
        before_inner = len(found)
        inner_incomplete = False
        try:
            import pikepdf

            with core.open_pikepdf(path, password) as pdf:
                for obj in pdf.objects:
                    if not isinstance(obj, pikepdf.Stream) or obj.get("/Subtype") == "/Image" \
                            or obj.get("/Type") in ("/EmbeddedFile", "/Metadata", "/XRef", "/ObjStm"):
                        continue
                    try:
                        data = obj.read_bytes()
                    except Exception as exc:  # noqa: BLE001
                        inner_incomplete = True
                        skip(f"inner stream {getattr(obj, 'objgen', '')}".strip(), exc, "inner_streams")
                        continue
                    if len(data) > DEEP_STREAM:
                        inner_incomplete = True
                        skip(
                            f"inner stream {getattr(obj, 'objgen', '')}".strip(),
                            f"exceeds {DEEP_STREAM}-byte scan limit ({len(data)} bytes)",
                            "inner_streams",
                        )
                        continue
                    before = len(found)
                    search(data.decode("latin-1"), "the file's inner data")
                    # (What the page text already showed is the same find, not another.)
                    found[before:] = [f for f in found[before:] if f["what"] not in in_pages]
            if len(found) > before_inner:
                checks["inner_streams"] = "findings"
            elif inner_incomplete:
                checks["inner_streams"] = "incomplete"
            else:
                checks["inner_streams"] = "passed"
        except Exception as exc:  # noqa: BLE001 - report qpdf/pikepdf failure as incomplete verification
            skip("inner data streams", exc, "inner_streams")
    else:
        checks["inner_streams"] = "skipped"

    checked.append("earlier versions")
    before_prev = len(found)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        if raw.count(b"%%EOF") > 1 or "/Prev" in reader.trailer:
            note("earlier versions kept in the file", "what was there before the last save")
        checks["earlier_versions"] = "findings" if len(found) > before_prev else "passed"
    except OSError as exc:
        skip("earlier versions", exc, "earlier_versions")

    if found:
        status = STATUS_FINDINGS
    elif skipped or warnings:
        status = STATUS_INCOMPLETE
    else:
        status = STATUS_CLEAN
    return {
        "clean": status == STATUS_CLEAN,
        "status": status,
        "checked": checked,
        "found": found,
        "skipped": skipped,
        "warnings": warnings,
        "checks": checks,
        "attachments_checked": attachments_checked,
        "attachments_skipped": attachments_skipped,
        "inspection_errors": inspection_errors,
    }


def proof_words(proof):
    """The check's result as one sentence for the person."""
    status = proof.get("status") or (STATUS_CLEAN if proof.get("clean") else STATUS_FINDINGS)
    if status == STATUS_CLEAN:
        places = [p for p in proof["checked"] if p not in ("under the boxes", "earlier versions")]
        under = " Nothing is left under the boxes." if "under the boxes" in proof["checked"] else ""
        if len(places) >= 2:
            joined = f"{', '.join(places[:-1])} or {places[-1]}"
        elif places:
            joined = places[0]
        else:
            joined = "document"
        return (f"Checked: nothing redacted is left in the {joined}, "
                f"and the file keeps no earlier version.{under}")
    if status == STATUS_INCOMPLETE:
        reasons = [
            f"{item['where']} ({item['reason']})"
            for item in (proof.get("skipped") or [])[:3]
        ]
        if not reasons:
            reasons = list((proof.get("warnings") or [])[:3])
        more = "" if len(proof.get("skipped") or []) <= 3 else f" (and {len(proof['skipped']) - 3} more)"
        detail = "; ".join(reasons) + more if reasons else "some checks could not run"
        return (f"Verification incomplete: no redacted text was found in the checked areas, "
                f"but parts of the file could not be verified ({detail}).")
    by_what = {}
    for item in proof["found"]:
        by_what.setdefault(item["what"], []).append(item["where"])
    parts = [f'"{what}" is still in ' + "; ".join(wheres[:4]) + ("..." if len(wheres) > 4 else "")
             for what, wheres in list(by_what.items())[:4]]
    more = "" if len(by_what) <= 4 else f" (and {len(by_what) - 4} more)"
    fix = []
    wheres = " ".join(w for item in proof["found"] for w in [item["where"]])
    if "details" in wheres or "attached" in wheres:
        fix.append("Remove hidden data takes out document details and attached files")
    if "comment" in wheres or "form field" in wheres or "bookmark" in wheres:
        fix.append("comments, form values and bookmarks can be changed in the reader")
    if "under a box" in wheres or "text of page" in wheres or "inner data" in wheres:
        fix.append("black it out again with the method that turns pages into pictures")
    if "earlier versions" in wheres:
        fix.append("save the result as a new file")
    incomplete_note = (
        " Also, verification was incomplete for some parts of the file."
        if (proof.get("skipped") or proof.get("warnings")) else ""
    )
    return ("Careful: " + ". ".join(parts) + more + "."
            + (" " + "; ".join(fix).capitalize() + "." if fix else "")
            + incomplete_note)

