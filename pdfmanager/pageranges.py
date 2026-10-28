"""Parsing of human-friendly, 1-based page range specifications.

Examples (for a 10-page document):
    "1-3,5"   -> [0, 1, 2, 4]
    "7-"      -> [6, 7, 8, 9]
    "-2"      -> [0, 1]
    "end"     -> [9]
    "3-1"     -> [2, 1, 0]   (descending ranges are allowed)
    "odd"     -> [0, 2, 4, 6, 8]
    "even"    -> [1, 3, 5, 7, 9]
    "all"     -> [0, ..., 9]
"""


class PageRangeError(ValueError):
    pass


def _page_number(token, total):
    token = token.strip().lower()
    if token in ("end", "last"):
        return total
    if not token.isdigit():
        raise PageRangeError(f"invalid page number: {token!r}")
    number = int(token)
    if not 1 <= number <= total:
        raise PageRangeError(f"page {number} is out of range (document has {total} pages)")
    return number


def parse_page_ranges(spec, total):
    """Return a list of 0-based page indices described by ``spec``.

    Order and duplicates are preserved so the result can also be used to
    reorder or repeat pages.
    """
    if total <= 0:
        raise PageRangeError("document has no pages")
    indices = []
    for part in (spec or "").split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part in ("all", "*"):
            indices.extend(range(total))
        elif part == "odd":
            indices.extend(range(0, total, 2))
        elif part == "even":
            indices.extend(range(1, total, 2))
        elif "-" in part:
            start_tok, end_tok = part.split("-", 1)
            start = _page_number(start_tok, total) if start_tok.strip() else 1
            end = _page_number(end_tok, total) if end_tok.strip() else total
            step = 1 if start <= end else -1
            indices.extend(range(start - 1, end - 1 + step, step))
        else:
            indices.append(_page_number(part, total) - 1)
    if not indices:
        raise PageRangeError(f"page range {spec!r} selects no pages")
    return indices
