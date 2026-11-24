# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Vertical CJK (tategaki) page detection, furigana stripping, and RTL column extraction."""

import logging

from pdfmanager.translate.layout import _detect_font_style, _normalize_color

# Vertical presentation forms -> standard horizontal equivalents
_VERTICAL_PUNCT_TABLE = str.maketrans({
    "\ufe10": "、",  # ︐
    "\ufe11": "、",  # ︑
    "\ufe12": "。",  # ︒
    "\ufe13": "：",  # ︓
    "\ufe14": "；",  # ︔
    "\ufe15": "！",  # ︕
    "\ufe16": "？",  # ︖
    "\ufe17": "〖",  # ︗
    "\ufe18": "〗",  # ︘
    "\ufe19": "…",  # ︙
    "\ufe30": "‥",  # ︰
    "\ufe31": "—",  # ︱
    "\ufe32": "–",  # ︲
    "\ufe33": "＿",  # ︳
    "\ufe34": "＿",  # ︴
    "\ufe35": "（",  # ︵
    "\ufe36": "）",  # ︶
    "\ufe37": "｛",  # ︷
    "\ufe38": "｝",  # ︸
    "\ufe39": "〔",  # ︹
    "\ufe3a": "〕",  # ︺
    "\ufe3b": "【",  # ︻
    "\ufe3c": "】",  # ︼
    "\ufe3d": "《",  # ︽
    "\ufe3e": "》",  # ︾
    "\ufe3f": "〈",  # ︿
    "\ufe40": "〉",  # ﹀
    "\ufe41": "「",  # ﹁
    "\ufe42": "」",  # ﹂
    "\ufe43": "『",  # ﹃
    "\ufe44": "』",  # ﹄
    "\ufe47": "［",  # ﹇
    "\ufe48": "］",  # ﹈
})


def _is_cjk_char(ch):
    """Return True if ``ch`` is a CJK ideograph, kana, hangul, or CJK punctuation."""
    if not ch:
        return False
    cp = ord(ch[0])
    return (
        0x3000 <= cp <= 0x30FF
        or 0x3400 <= cp <= 0x4DBF
        or 0x4E00 <= cp <= 0x9FFF
        or 0xF900 <= cp <= 0xFAFF
        or 0xFE10 <= cp <= 0xFE4F
        or 0xFF00 <= cp <= 0xFFEF
        or 0xAC00 <= cp <= 0xD7AF
    )


def normalize_vertical_punct(text):
    """Map Unicode vertical presentation punctuation forms to horizontal equivalents."""
    if not text:
        return ""
    return str(text).translate(_VERTICAL_PUNCT_TABLE)


def _clean_chars(plumber_page):
    """Extract non-whitespace character dicts with normalized float coordinates."""
    out = []
    try:
        raw_chars = plumber_page.chars or []
    except Exception as _exc:
        logging.getLogger("zpdf").debug("vertical._clean_chars: %s", _exc)
        return out
    for ch in raw_chars:
        txt = str(ch.get("text") or "")
        if not txt or txt.isspace():
            continue
        x0 = float(ch.get("x0", 0.0))
        x1 = float(ch.get("x1", x0))
        top = float(ch.get("top", 0.0))
        bottom = float(ch.get("bottom", top))
        size = max(4.0, float(ch.get("size") or (bottom - top) or 10.0))
        out.append({
            "text": txt,
            "x0": x0,
            "x1": x1,
            "top": top,
            "bottom": bottom,
            "cx": (x0 + x1) / 2.0,
            "cy": (top + bottom) / 2.0,
            "size": size,
            "fontname": str(ch.get("fontname") or ""),
            "non_stroking_color": ch.get("non_stroking_color"),
        })
    return out


def is_vertical_cjk_page(plumber_page):
    """Return True when ``plumber_page`` uses vertical CJK (*tategaki*) layout.

    Compares vertical-downward neighbor continuity against horizontal-rightward
    continuity across all CJK characters on the page.
    """
    chars = _clean_chars(plumber_page)
    cjk = [c for c in chars if _is_cjk_char(c["text"])]
    if len(cjk) < 6:
        return False

    # Estimate body glyph size using 60th percentile so small furigana don't skew bins
    sizes = sorted(c["size"] for c in cjk)
    body_size = sizes[min(len(sizes) - 1, int(len(sizes) * 0.60))]
    x_bin = max(3.0, body_size * 0.55)
    y_bin = max(3.0, body_size * 0.45)

    # Count vertical-downward neighbor pairs
    by_col = sorted(cjk, key=lambda c: (round(c["cx"] / x_bin), c["top"]))
    vert_pairs = 0
    for a, b in zip(by_col, by_col[1:]):
        sz = max(a["size"], b["size"])
        dx = abs(b["cx"] - a["cx"])
        dy = b["cy"] - a["cy"]
        if dx <= sz * 0.65 and sz * 0.25 <= dy <= sz * 2.2:
            vert_pairs += 1

    # Count horizontal-rightward neighbor pairs
    by_row = sorted(cjk, key=lambda c: (round(c["cy"] / y_bin), c["x0"]))
    horiz_pairs = 0
    for a, b in zip(by_row, by_row[1:]):
        sz = max(a["size"], b["size"])
        dy = abs(b["cy"] - a["cy"])
        dx = b["cx"] - a["cx"]
        if dy <= sz * 0.45 and sz * 0.25 <= dx <= sz * 2.0:
            horiz_pairs += 1

    total_pairs = vert_pairs + horiz_pairs
    if total_pairs < 3:
        return False
    return (vert_pairs / total_pairs) >= 0.60


def extract_vertical_cjk_blocks(plumber_page):
    """Extract positioned text blocks from a vertical CJK (*tategaki*) page.

    - Strips small furigana / ruby annotations (``size <= 0.62 * body_size``).
    - Groups glyphs into vertical columns (top-to-bottom).
    - Orders columns right-to-left (``-x_center``).
    - Merges adjacent same-style columns into coherent paragraph blocks and
      normalizes vertical presentation punctuation.
    """
    chars = _clean_chars(plumber_page)
    if not chars:
        return []

    # Estimate main body font size (60th percentile avoids ruby/furigana skew)
    sizes = sorted(c["size"] for c in chars)
    body_size = sizes[min(len(sizes) - 1, int(len(sizes) * 0.60))]

    # Filter out furigana / ruby annotations
    ruby_cutoff = body_size * 0.62
    main_chars = [c for c in chars if c["size"] > ruby_cutoff]
    if not main_chars:
        main_chars = chars

    # Group characters into vertical columns by x-center (right-to-left)
    main_chars.sort(key=lambda c: (-c["cx"], c["top"]))
    raw_cols = []
    for ch in main_chars:
        placed = False
        for col in raw_cols:
            col_sz = col["size"]
            if abs(ch["cx"] - col["cx"]) <= max(4.0, col_sz * 0.55):
                col["chars"].append(ch)
                n = len(col["chars"])
                col["cx"] = ((col["cx"] * (n - 1)) + ch["cx"]) / n
                placed = True
                break
        if not placed:
            raw_cols.append({"cx": ch["cx"], "size": ch["size"], "chars": [ch]})

    # Split any column that has a large internal vertical gap (e.g. upper/lower dan)
    columns = []
    for col in raw_cols:
        col_chars = sorted(col["chars"], key=lambda c: c["top"])
        segs = [[col_chars[0]]]
        for ch in col_chars[1:]:
            prev = segs[-1][-1]
            v_gap = ch["top"] - prev["bottom"]
            if v_gap > max(24.0, max(prev["size"], ch["size"]) * 3.2):
                segs.append([ch])
            else:
                segs[-1].append(ch)
        for seg in segs:
            avg_sz = sum(c["size"] for c in seg) / len(seg)
            cx = sum(c["cx"] for c in seg) / len(seg)
            columns.append({
                "chars": seg,
                "text": "".join(c["text"] for c in seg),
                "x0": min(c["x0"] for c in seg),
                "x1": max(c["x1"] for c in seg),
                "top": min(c["top"] for c in seg),
                "bottom": max(c["bottom"] for c in seg),
                "cx": cx,
                "size": avg_sz,
            })

    # Order columns strictly right-to-left (highest x first), then top-to-bottom
    columns.sort(key=lambda col: (-col["cx"], col["top"]))

    # Merge adjacent RTL columns into coherent paragraphs
    merged_groups = []
    for col in columns:
        if not merged_groups:
            merged_groups.append([col])
            continue
        prev = merged_groups[-1][-1]
        dx = prev["cx"] - col["cx"]
        avg_sz = max(prev["size"], col["size"])
        same_size = abs(col["size"] - prev["size"]) <= max(2.5, prev["size"] * 0.25)
        v_overlap = min(prev["bottom"], col["bottom"]) - max(prev["top"], col["top"])
        min_h = max(1.0, min(prev["bottom"] - prev["top"], col["bottom"] - col["top"]))
        # Adjacent column in same paragraph: close horizontally, same font size, vertically overlapping
        if (
            0.0 <= dx <= max(18.0, avg_sz * 2.2)
            and same_size
            and v_overlap >= -avg_sz * 0.5
            and (v_overlap > 0 or min_h < avg_sz * 1.5)
        ):
            merged_groups[-1].append(col)
        else:
            merged_groups.append([col])

    blocks = []
    for grp in merged_groups:
        all_chars = [c for col in grp for c in col["chars"]]
        avg_sz = sum(c["size"] for c in all_chars) / len(all_chars)
        family, bold, italic = _detect_font_style(all_chars)
        bx0 = min(col["x0"] for col in grp)
        bx1 = max(col["x1"] for col in grp)
        btop = min(col["top"] for col in grp)
        bbot = max(col["bottom"] for col in grp)
        raw_text = "".join(col["text"] for col in grp)
        norm_text = normalize_vertical_punct(raw_text).strip()
        if not norm_text:
            continue
        blocks.append({
            "text": norm_text,
            "x0": bx0,
            "top": btop,
            "x1": bx1,
            "bottom": bbot,
            "box_x0": bx0,
            "box_x1": bx1,
            "size": avg_sz,
            "color": _normalize_color(all_chars[0].get("non_stroking_color")),
            "family": family,
            "bold": bold,
            "italic": italic,
            "align": "left",
            "lines": len(grp),
            "in_table": False,
            "vertical": True,
        })
    return blocks
