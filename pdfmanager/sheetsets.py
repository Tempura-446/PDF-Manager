# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Drawing sheet sets: title-block detection, splitting/naming sheets,
automatic cross-sheet detail links, and revision set comparison.
"""

from __future__ import annotations

import logging

import io
import re
from pathlib import Path

import numpy as np
from PIL import Image
import pypdfium2 as pdfium
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject

from pdfmanager import core, filetools

# Typical CAD/architectural sheet numbers: A-101, S2.03, E-001, M1.01, A101, C-201A.
_SHEET_NUM_PAT = r"[A-Z]{1,3}(?:[-.]\d{1,4}(?:\.\d{1,3})?|\d{1,4}\.\d{1,3}|\d{2,4})[A-Z]?"
_SHEET_NUM_RE = re.compile(rf"\b({_SHEET_NUM_PAT})\b")
# Detail / cross-sheet references: "2/A-102", "SEE A-201", "SHEET S2.03", "REF. E-101".
_REF_CONTEXT_RE = re.compile(
    rf"(?:\b\d+[A-Z]?\s*/\s*|\b(?:SEE|DETAIL|DTL|SHEET|SHT|REF\.?|DWG\.?)\s+)({_SHEET_NUM_PAT})\b",
    re.IGNORECASE,
)
_BOILERPLATE_RE = re.compile(
    r"^(?:SHEET|SHT|DWG|DRAWING|NO\.?|NUMBER|SCALE|DATE|REV|REVISION|PROJECT|DRAWN|CHECKED|BY)\b[:.\s]*$",
    re.IGNORECASE,
)
_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _safe_filename(text: str) -> str:
    cleaned = _INVALID_FILENAME_RE.sub(" ", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(". ")


def _open_pdfium(path: Path, password: str | None = None):
    try:
        return pdfium.PdfDocument(str(path), password=password or None)
    except Exception as exc:
        raise core.PdfManagerError(f"Could not open {path.name} ({exc})") from None


def _extract_lines_in_region(page, x0: float, y0: float, x1: float, y1: float) -> list[dict]:
    """Extract text lines (with bounding boxes) inside ``(x0, y0, x1, y1)`` in PDF coordinates."""
    tp = page.get_textpage()
    try:
        n = tp.count_chars()
        if n <= 0:
            return []
        text = tp.get_text_range(0, n)
        chars = []
        for idx, ch in enumerate(text):
            if ch in ("\r", "\n"):
                continue
            try:
                l, b, r, t = tp.get_charbox(idx)
            except Exception:
                continue
            cx = (l + r) * 0.5
            cy = (b + t) * 0.5
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                chars.append((ch, l, b, r, t))
    finally:
        tp.close()

    if not chars:
        return []

    # Group characters into horizontal lines by vertical midpoint.
    chars.sort(key=lambda c: (-round((c[2] + c[4]) * 0.5, 1), c[1]))
    lines: list[list[tuple]] = []
    for item in chars:
        cy = (item[2] + item[4]) * 0.5
        h = max(4.0, item[4] - item[2])
        placed = False
        for line in lines:
            lcy = (line[0][2] + line[0][4]) * 0.5
            lh = max(4.0, line[0][4] - line[0][2])
            if abs(cy - lcy) <= max(h, lh) * 0.6:
                line.append(item)
                placed = True
                break
        if not placed:
            lines.append([item])

    out = []
    for line in lines:
        line.sort(key=lambda c: c[1])
        parts = []
        prev_r = None
        for ch, l, b, r, t in line:
            if ch != " " and prev_r is not None and l - prev_r > max(4.0, (t - b) * 0.65):
                if not parts or parts[-1] != " ":
                    parts.append(" ")
            parts.append(ch)
            if ch != " ":
                prev_r = r
        s = re.sub(r"\s+", " ", "".join(parts)).strip()
        if not s:
            continue
        out.append({
            "text": s,
            "rect": [
                min(c[1] for c in line),
                min(c[2] for c in line),
                max(c[3] for c in line),
                max(c[4] for c in line),
            ],
            "height": max(c[4] - c[2] for c in line),
        })
    return out


def _pick_sheet_number(lines: list[dict]) -> tuple[str | None, int | None]:
    """Pick the best sheet number candidate and its line index from title-block lines."""
    candidates = []
    for idx, ln in enumerate(lines):
        for m in _SHEET_NUM_RE.finditer(ln["text"]):
            cand = m.group(1).strip()
            # Filter out obvious dates/years like "2026" (already prevented by leading letters)
            # or pure scale strings like "SCALE" (no digits).
            score = 0
            if "-" in cand or "." in cand:
                score += 10
            if ln["text"].strip() == cand:
                score += 6
            score += min(ln["height"], 36.0) * 0.5
            # Prefer bottom-most / right-most in title block.
            score += (ln["rect"][0] * 0.001) - (ln["rect"][1] * 0.002)
            candidates.append((score, cand, idx))
    if not candidates:
        return None, None
    candidates.sort(key=lambda c: -c[0])
    return candidates[0][1], candidates[0][2]


def _pick_sheet_title(lines: list[dict], num_idx: int | None, sheet_num: str | None) -> str:
    """Pick the sheet title from title-block lines (excluding the sheet number and boilerplate)."""
    best = ""
    best_score = -1e9
    for idx, ln in enumerate(lines):
        txt = ln["text"].strip()
        if idx == num_idx:
            # If the same line has "A-101 Floor plan", strip the number.
            if sheet_num:
                rem = re.sub(rf"\b{re.escape(sheet_num)}\b", "", txt).strip(" -–:|")
                if len(rem) >= 3 and not _BOILERPLATE_RE.match(rem):
                    return rem
            continue
        if len(txt) < 3 or _BOILERPLATE_RE.match(txt):
            continue
        if re.match(r"^(?:SCALE|DATE|REV|PROJECT|DRAWN)\s*[:.]", txt, re.IGNORECASE):
            continue
        if not re.search(r"[A-Za-z]{2,}", txt):
            continue
        score = ln["height"] * 1.5 + min(len(txt), 40) * 0.2
        if score > best_score:
            best_score = score
            best = txt
    return best


def detect_sheets(path, region=None, password=None) -> list[dict]:
    """Detect each page's sheet number and title from its title block.

    ``region`` is an optional ``(x0, y0, x1, y1)`` tuple in fractional page
    coordinates (``0..1``, bottom-left origin). Defaults to the bottom-right
    title-block area ``(0.50, 0.0, 1.0, 0.35)``, falling back to the full page
    if nothing is found there.
    """
    src = Path(path)
    rx0, ry0, rx1, ry1 = region if region and len(region) == 4 else (0.50, 0.0, 1.0, 0.35)
    with core.PDFIUM_LOCK:
        doc = _open_pdfium(src, password)
        results = []
        try:
            for pno in range(1, len(doc) + 1):
                page = doc[pno - 1]
                try:
                    pw, ph = page.get_size()
                    lines = _extract_lines_in_region(page, rx0 * pw, ry0 * ph, rx1 * pw, ry1 * ph)
                    num, num_idx = _pick_sheet_number(lines)
                    if not num and region is None:
                        # Fallback: scan full page if title block is elsewhere.
                        all_lines = _extract_lines_in_region(page, 0.0, 0.0, pw, ph)
                        num, num_idx = _pick_sheet_number(all_lines)
                        if num:
                            lines = all_lines
                    title = _pick_sheet_title(lines, num_idx, num) if lines else ""
                    label = f"{num} {title}".strip() if num else (title or f"Page {pno}")
                    results.append({
                        "page": pno,
                        "number": num or f"Page-{pno}",
                        "detected": bool(num),
                        "title": title,
                        "label": label,
                    })
                finally:
                    page.close()
        finally:
            doc.close()
    return results


def split_sheets(path, out_dir, region=None, password=None) -> list[dict]:
    """Split a sheet-set PDF into one file per sheet named ``<number> <title>.pdf``."""
    src = Path(path)
    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    sheets = detect_sheets(src, region=region, password=password)

    reader = core.open_pdf(src, password)
    used_names: dict[str, int] = {}
    outputs = []
    for s in sheets:
        base = _safe_filename(f"{s['number']} {s['title']}".strip()) or f"Page-{s['page']}"
        key = base.lower()
        count = used_names.get(key, 0) + 1
        used_names[key] = count
        fname = f"{base}.pdf" if count == 1 else f"{base} ({count}).pdf"
        out_path = dest / fname

        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_page(reader.pages[s["page"] - 1])
        core._write(writer, out_path)
        outputs.append({**s, "output": out_path})
    return outputs


def name_pages(path, output, region=None, password=None) -> dict:
    """Set PDF ``/PageLabels`` on ``output`` to each page's detected sheet number."""
    src = Path(path)
    out = Path(output)
    sheets = detect_sheets(src, region=region, password=password)
    labels = [(s["page"], "", s["number"], 1) for s in sheets]
    filetools.set_page_labels(src, out, labels, password=password)
    return {"output": out, "sheets": sheets}


def _sheet_map(path: Path, region=None, password=None) -> dict[str, int]:
    """Map uppercase sheet number → 1-based page number, preferring existing ``/PageLabels``."""
    existing = filetools.page_labels(path, password) or []
    mapping: dict[str, int] = {}
    for idx, lbl in enumerate(existing, 1):
        s = str(lbl or "").strip()
        if s and _SHEET_NUM_RE.fullmatch(s):
            mapping.setdefault(s.upper(), idx)
    if not mapping:
        for sh in detect_sheets(path, region=region, password=password):
            if sh["detected"]:
                mapping.setdefault(sh["number"].upper(), sh["page"])
    return mapping


def _find_reference_rects(page, known_sheets: dict[str, int], current_page: int) -> list[tuple[str, int, list[float]]]:
    """Find cross-sheet references on ``page`` and return ``[(target_num, target_page, rect), ...]``."""
    tp = page.get_textpage()
    try:
        n = tp.count_chars()
        if n <= 0:
            return []
        text = tp.get_text_range(0, n)
        found = []
        seen_spans: set[tuple[int, int]] = set()
        for m in _REF_CONTEXT_RE.finditer(text):
            target_num = m.group(1).strip().upper()
            target_page = known_sheets.get(target_num)
            if not target_page or target_page == current_page:
                continue
            span = (m.start(), m.end())
            if span in seen_spans:
                continue
            seen_spans.add(span)
            boxes = []
            for ci in range(m.start(), m.end()):
                if text[ci] in ("\r", "\n"):
                    continue
                try:
                    boxes.append(tp.get_charbox(ci))
                except Exception as _exc:
                    logging.getLogger("zpdf").debug("sheetsets._find_reference_rects: %s", _exc)
            if boxes:
                rect = [
                    min(b[0] for b in boxes) - 1.0,
                    min(b[1] for b in boxes) - 1.0,
                    max(b[2] for b in boxes) + 1.0,
                    max(b[3] for b in boxes) + 1.0,
                ]
                found.append((target_num, target_page, rect))
        return found
    finally:
        tp.close()


def link_sheets(path, output, region=None, password=None) -> dict:
    """Add PDF ``/Link`` annotations from detail/sheet references (e.g. ``2/A-102``,
    ``SEE A-201``) to the page labelled with that sheet number."""
    src = Path(path)
    out = Path(output)
    known = _sheet_map(src, region=region, password=password)
    if not known:
        raise core.PdfManagerError("No sheet numbers found to link to (run Name pages first or check title blocks).")

    doc = _open_pdfium(src, password)
    page_refs: dict[int, list[tuple[str, int, list[float]]]] = {}
    try:
        for pno in range(1, len(doc) + 1):
            page = doc[pno - 1]
            try:
                refs = _find_reference_rects(page, known, pno)
                if refs:
                    page_refs[pno] = refs
            finally:
                page.close()
    finally:
        doc.close()

    reader = core.open_pdf(src, password)
    writer = core.clone_document(reader)
    total_links = 0
    targets = []

    for pno, refs in page_refs.items():
        page_obj = writer.pages[pno - 1]
        annots = page_obj.get("/Annots")
        if annots is None:
            annots = ArrayObject()
            page_obj[NameObject("/Annots")] = annots
        else:
            annots = annots.get_object()
        for target_num, target_page, rect in refs:
            target_ref = writer.pages[target_page - 1].indirect_reference
            link = DictionaryObject({
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Link"),
                NameObject("/Rect"): ArrayObject([FloatObject(v) for v in rect]),
                NameObject("/Border"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(0)]),
                NameObject("/Dest"): ArrayObject([target_ref, NameObject("/Fit")]),
            })
            annots.append(writer._add_object(link))
            total_links += 1
            targets.append({"from_page": pno, "to_page": target_page, "sheet": target_num})

    core._write(writer, out)
    return {"output": out, "links": total_links, "targets": targets}


def _render_gray(doc, page_idx: int, dpi: int) -> np.ndarray:
    page = doc[page_idx]
    try:
        bmp = page.render(scale=dpi / 72.0)
        try:
            img = bmp.to_pil().convert("L")
            return np.asarray(img, dtype=np.int16)
        finally:
            bmp.close()
    finally:
        page.close()


def _overlay_image(arr1: np.ndarray, arr2: np.ndarray) -> Image.Image:
    """Create a red/blue visual overlay diff image (old=red, new=blue, unchanged=gray)."""
    h = max(arr1.shape[0], arr2.shape[0])
    w = max(arr1.shape[1], arr2.shape[1])
    a1 = np.full((h, w), 255, dtype=np.int16)
    a2 = np.full((h, w), 255, dtype=np.int16)
    a1[:arr1.shape[0], :arr1.shape[1]] = arr1
    a2[:arr2.shape[0], :arr2.shape[1]] = arr2

    ink1 = a1 < 235
    ink2 = a2 < 235
    both = ink1 & ink2
    only1 = ink1 & ~ink2
    only2 = ink2 & ~ink1

    rgb = np.full((h, w, 3), 255, dtype=np.uint8)
    rgb[both] = (120, 120, 120)
    rgb[only1] = (220, 50, 50)   # removed / old = red
    rgb[only2] = (30, 100, 220)  # added / new = blue
    return Image.fromarray(rgb, mode="RGB")


def compare_sets(
    path_v1,
    path_v2,
    out_pdf=None,
    region=None,
    threshold: float = 0.002,
    dpi: int = 96,
    password_v1=None,
    password_v2=None,
) -> dict:
    """Compare two versions of a drawing set sheet by sheet, matched by sheet number.

    Returns ``{"changed": [...], "added": [...], "removed": [...], "unchanged": [...]}``.
    If ``out_pdf`` is provided, writes a visual red/blue overlay PDF of changed sheets.
    """
    p1, p2 = Path(path_v1), Path(path_v2)
    map1 = _sheet_map(p1, region=region, password=password_v1)
    map2 = _sheet_map(p2, region=region, password=password_v2)

    all_nums = list(dict.fromkeys(list(map1.keys()) + list(map2.keys())))
    changed = []
    added = []
    removed = []
    unchanged = []
    diff_images: list[Image.Image] = []

    with core.PDFIUM_LOCK:
        doc1 = _open_pdfium(p1, password_v1)
        try:
            doc2 = _open_pdfium(p2, password_v2)
        except Exception:
            doc1.close()
            raise
        try:
            for num in all_nums:
                in1 = num in map1
                in2 = num in map2
                if in1 and not in2:
                    removed.append({"sheet": num, "page_v1": map1[num]})
                elif in2 and not in1:
                    added.append({"sheet": num, "page_v2": map2[num]})
                else:
                    a1 = _render_gray(doc1, map1[num] - 1, dpi)
                    a2 = _render_gray(doc2, map2[num] - 1, dpi)
                    if a1.shape != a2.shape:
                        diff_ratio = 1.0
                    else:
                        diff_ratio = float(np.mean(np.abs(a1 - a2) > 25))
                    entry = {"sheet": num, "page_v1": map1[num], "page_v2": map2[num], "diff_ratio": round(diff_ratio, 5)}
                    if diff_ratio > threshold:
                        changed.append(entry)
                        if out_pdf is not None:
                            diff_images.append(_overlay_image(a1, a2))
                    else:
                        unchanged.append(entry)
        finally:
            doc1.close()
            doc2.close()

    result_pdf = None
    if out_pdf is not None and diff_images:
        out_path = Path(out_pdf)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        diff_images[0].save(
            str(out_path),
            format="PDF",
            save_all=True,
            append_images=diff_images[1:],
            resolution=float(dpi),
        )
        result_pdf = out_path

    return {
        "changed": changed,
        "added": added,
        "removed": removed,
        "unchanged": unchanged,
        "output": result_pdf,
    }
