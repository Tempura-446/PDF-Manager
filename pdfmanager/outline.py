"""Bookmarks (the PDF outline): read, write and detect them from headings.

Bookmarks are lists of ``{"title": str, "page": int, "level": int}`` with
1-based page numbers and level 0 for top-level entries. As text, one per
line, indented two spaces per level::

    - Introduction (p. 1)
      - Background (p. 2)
    - Results (p. 5)
"""

import math
import re
from collections import Counter

from pypdf import PdfWriter
from pypdf.generic import NameObject

from pdfmanager import core

LINE = re.compile(r"^(?P<indent>[ \t]*)(?:[-*•]\s*)?(?P<title>.*?)\s*\(p(?:age|\.)?\s*(?P<page>\d+)\)\s*$",
                  re.IGNORECASE)


def get_outline(path, password=None):
    """[{"title", "page" (1-based), "level" (0 = top)}] for the bookmarks."""
    return core.outline_items(core.open_pdf(path, password))


def _check(items, total):
    clean, previous_level = [], -1
    for n, item in enumerate(items, 1):
        title = str(item.get("title", "")).strip()
        try:
            page, level = int(item.get("page")), max(0, int(item.get("level", 0)))
        except (TypeError, ValueError):
            raise core.PdfManagerError(f"bookmark {n}: page must be a number") from None
        if not title:
            raise core.PdfManagerError(f"bookmark {n} has no title")
        if not 1 <= page <= total:
            raise core.PdfManagerError(
                f"bookmark “{title}”: page {page} doesn't exist (the PDF has {total} pages)")
        level = min(level, previous_level + 1)  # can't skip a level
        clean.append({"title": title, "page": page, "level": level})
        previous_level = level
    return clean


def set_outline(path, output, items, password=None):
    """Replace all bookmarks with ``items`` (an empty list removes them)."""
    reader = core.open_pdf(path, password)
    items = _check(items, len(reader.pages))
    writer = core.clone_document(reader)
    root = writer._root_object
    if "/Outlines" in root:
        del root["/Outlines"]
    parents = []  # parents[level] = the last item added at that level
    for item in items:
        parent = parents[item["level"] - 1] if item["level"] else None
        added = writer.add_outline_item(item["title"], item["page"] - 1, parent=parent)
        del parents[item["level"]:]
        parents.append(added)
    if items:
        root[NameObject("/PageMode")] = NameObject("/UseOutlines")  # show them on opening
    elif root.get("/PageMode") == "/UseOutlines":
        del root["/PageMode"]
    return core._write(writer, output)


def to_text(items):
    return "".join(f"{'  ' * i['level']}- {i['title']} (p. {i['page']})\n" for i in items)


def from_text(text):
    items, indents = [], []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = LINE.match(line.expandtabs(2))
        if not match:
            raise core.PdfManagerError(
                f"line {n}: expected “- Title (p. 3)”, got {line.strip()!r}")
        indent = len(match["indent"])
        while indents and indent < indents[-1]:
            indents.pop()
        if not indents or indent > indents[-1]:
            indents.append(indent)
        items.append({"title": match["title"], "page": int(match["page"]),
                      "level": len(indents) - 1})
    return items


# --------------------------------------------------------------------------
# Detecting headings
# --------------------------------------------------------------------------

def _is_bold(font):
    try:
        name = str(font.get("/BaseFont", "")) if font else ""
    except Exception:
        return False
    return bool(re.search(r"bold|black|heavy|semibold|demi", name, re.IGNORECASE))


def _lines(page):
    """(font size, text, y, bold) for each line of text on a page, top to
    bottom. A line counts as bold if all of it is."""
    runs = []

    def visit(text, cm, tm, font, size):
        if not text.strip():
            return
        # Effective size = font size scaled by the text and drawing matrices.
        scale = math.hypot(tm[2], tm[3]) * math.hypot(cm[2], cm[3])
        y = tm[5] * cm[3] + cm[5]
        runs.append((round(size * scale, 1), text, y, _is_bold(font)))

    try:
        page.extract_text(visitor_text=visit)
    except Exception:
        return []
    lines = []
    for size, text, y, bold in runs:
        if lines and abs(lines[-1][2] - y) < 0.5 * size and lines[-1][0] == size:
            last = lines[-1]
            lines[-1] = (size, last[1] + text, last[2], last[3] and bold)
        else:
            lines.append((size, text, y, bold))
    return [(s, " ".join(t.split()), y, b) for s, t, y, b in lines if t.strip()]


def detect_headings(path, password=None, max_levels=3, limit=300):
    """Suggest bookmarks from the document's headings: lines set in a
    larger font than the body text. Text repeated on many pages (running
    headers and footers) is ignored. Returns bookmark items."""
    reader = core.open_pdf(path, password)
    pages = [_lines(page) for page in reader.pages]
    chars = Counter()
    for lines in pages:
        for size, text, _, _ in lines:
            chars[size] += len(text)
    if not chars:
        return []  # no text, e.g. a scan without OCR
    body = chars.most_common(1)[0][0]
    repeated = Counter(text.lower() for lines in pages for text in {t for _, t, _, _ in lines})
    candidates = []
    for number, lines in enumerate(pages, 1):
        for size, text, _, bold in lines:
            if size < body * 1.1:
                # A short bold line at body size is a heading too (the lowest level).
                if not (bold and size >= body * 0.97 and len(text) <= 80
                        and not text.endswith((".", ",", ";", ":"))):
                    continue
                size = body * 1.01
            if not 2 <= len(text) <= 120:
                continue
            if sum(c.isalpha() for c in text) < 2:
                continue  # page numbers, rules, bullets
            if len(pages) > 3 and repeated[text.lower()] > max(2, len(pages) * 0.3):
                continue
            candidates.append((number, size, text))
    sizes = sorted({size for _, size, _ in candidates}, reverse=True)
    # Sizes that are close together are the same level.
    levels, level, previous = {}, -1, None
    for size in sizes:
        if previous is None or size < previous * 0.93:
            level += 1
        levels[size] = min(level, max_levels - 1)
        previous = size
    items = [{"title": text, "page": number, "level": levels[size]}
             for number, size, text in candidates]
    if items:
        top = min(i["level"] for i in items)
        for item in items:
            item["level"] -= top
    return _check(items, len(pages))[:limit]
