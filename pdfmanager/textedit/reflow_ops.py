# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Line wrapping, CJK/kinsoku & bidi reordering, and paragraph layout detection."""

import re
import statistics
import unicodedata

from .extract import FIT_LIMIT, _CORE, _fit, _text_of

def _divisions(word, language=None):
    """Where a word may be broken at a line end: [(letters before the
    break, "soft" or "hard")]. A word with hyphens of its own breaks after
    them ("well-|known") and nowhere else; other English words where the
    hyphenation patterns say."""
    from pdfmanager import hyphen

    lead, core, _ = _CORE.match(word).groups()
    if "-" in core:
        out, at = [], len(lead)
        parts = core.split("-")
        for k, part in enumerate(parts[:-1]):
            at += len(part) + 1
            if len(part) >= 2 and len(parts[k + 1]) >= 2 and part.isalpha() and parts[k + 1].isalpha():
                out.append((at, "hard"))
        return out
    return [(len(lead) + p, "soft") for p in hyphen.points(core, language)]


def _pieces_of(words, divide, justify, room, measure):
    """The words cut at their divisions: (pieces, what follows each: see :func:`_fit`,
    the hyphen's width left 0 for the caller). Words are divided in
    justified text, and where one is wider than the room; never the
    paragraph's last word. ``words`` are strings or lists of (letter, style)."""
    base_room = room(0) if callable(room) else room
    pieces, joins = [], []
    for k, word in enumerate(words):
        cuts = []
        if divide and word and k < len(words) - 1 and (justify or measure(word) > base_room):
            cuts = divide(word if isinstance(word, str) else "".join(ch for ch, _ in word))
        start = 0
        for at, kind in cuts:
            pieces.append(word[start:at])
            joins.append((kind, 0.0))
            start = at
        pieces.append(word[start:])
        joins.append(None)
    return pieces, joins


_KINSOKU_NO_START = frozenset("。、，．！？：；）」』】》〉〕］｝.,!?:;)]}ー々〜…・ゝゞヽヾ")


_KINSOKU_NO_END = frozenset("（「『【《〈〔［｛([{")


_BIDI_MIRROR = str.maketrans("()[]{}<>", ")(][}{><")


def _is_cjk(ch):
    """Whether ``ch`` is a CJK ideograph, kana, hangul, or CJK punctuation."""
    o = ord(ch)
    return (0x3000 <= o <= 0x30FF or 0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF
            or 0xAC00 <= o <= 0xD7AF or 0xF900 <= o <= 0xFAFF or 0xFF00 <= o <= 0xFFEF)


def _has_cjk(text):
    return any(_is_cjk(ch) for ch in text)


def _cjk_tokens(text):
    """Break CJK/mixed text into atomic ``(token, space_before)`` pieces.

    Breaks are allowed between CJK characters except where kinsoku forbids it
    (closing punctuation stays attached to the preceding character; opening
    punctuation stays attached to the following character). Runs of Latin/ASCII
    letters and digits stay whole as single words.
    """
    raw = []
    i, n = 0, len(text)
    pending_space = False
    while i < n:
        ch = text[i]
        if ch.isspace():
            pending_space = True
            i += 1
            continue
        if _is_cjk(ch):
            raw.append((ch, pending_space))
            pending_space = False
            i += 1
        else:
            j = i + 1
            while j < n and not text[j].isspace() and not _is_cjk(text[j]):
                j += 1
            raw.append((text[i:j], pending_space))
            pending_space = False
            i = j
    if not raw:
        return []
    # Apply kinsoku: bind NO_END to the following token, NO_START to the preceding token.
    merged = []
    k = 0
    while k < len(raw):
        tok, sp = raw[k]
        while tok and tok[-1] in _KINSOKU_NO_END and k + 1 < len(raw) and not raw[k + 1][1]:
            k += 1
            tok += raw[k][0]
        if merged and not sp and tok and tok[0] in _KINSOKU_NO_START:
            prev_tok, prev_sp = merged[-1]
            merged[-1] = (prev_tok + tok, prev_sp)
        else:
            merged.append((tok, sp))
        k += 1
    return merged


def _wrap_cjk(text, room, measure):
    """Wrap CJK/mixed text into lines honoring kinsoku rules and keeping Latin words whole."""
    tokens = _cjk_tokens(text)
    if not tokens:
        return [("", None)]
    lines, line = [], ""
    for tok, sp in tokens:
        r = room(len(lines)) if callable(room) else room
        sep = " " if (line and sp) else ""
        trial = line + sep + tok
        if line and measure(trial) > r + 1e-9:
            lines.append(line)
            line = tok
        else:
            line = trial
    lines.append(line)
    return [(ln, None) for ln in lines]


def _bidi_visual(text, rtl=True):
    """Reorder a single logical-order line into visual left-to-right order
    following UAX #9 paragraph-level and run-reordering rules.

    Inside an RTL paragraph (``rtl=True``), strong RTL runs (Hebrew, Arabic)
    are ordered right-to-left while embedded LTR runs (Latin words, numbers)
    remain left-to-right.
    """
    if not rtl or not text:
        return text
    n = len(text)
    types = []
    for ch in text:
        bc = unicodedata.bidirectional(ch)
        if bc in ("R", "AL"):
            types.append("R")
        elif bc == "L":
            types.append("L")
        elif bc in ("EN", "AN"):
            types.append("EN")
        elif bc in ("ES", "CS", "ET"):
            types.append("SEP")
        else:
            types.append("N")
    # Resolve numeric separators/terminators adjacent to digits.
    for i in range(n):
        if types[i] == "SEP":
            prev_en = i > 0 and types[i - 1] == "EN"
            next_en = i + 1 < n and types[i + 1] == "EN"
            types[i] = "EN" if (prev_en and next_en) or (text[i] in "%$" and (prev_en or next_en)) else "N"
    # Resolve neutrals: if bounded by LTR/number on both sides -> LTR, else paragraph direction R.
    strong = [t if t in ("R", "L", "EN") else None for t in types]
    for i in range(n):
        if strong[i] is None:
            left = next((strong[k] for k in range(i - 1, -1, -1) if strong[k] is not None), "R")
            right = next((strong[k] for k in range(i + 1, n) if strong[k] is not None), "R")
            types[i] = "L" if (left in ("L", "EN") and right in ("L", "EN")) else "R"
    levels = [2 if t in ("L", "EN") else 1 for t in types]
    # Mirror paired brackets on RTL (level 1) positions.
    chars = [ch.translate(_BIDI_MIRROR) if lv == 1 else ch for ch, lv in zip(text, levels)]
    # UAX #9 L2 for levels in {1, 2}: reverse the whole line, then reverse back each level-2 run.
    chars.reverse()
    levels.reverse()
    i = 0
    while i < n:
        if levels[i] == 2:
            j = i + 1
            while j < n and levels[j] == 2:
                j += 1
            chars[i:j] = reversed(chars[i:j])
            i = j
        else:
            i += 1
    return "".join(chars)


def _wrap_marked(text, room, measure, justify=False, greedy=False, divide=None):
    """Lines of one paragraph (no line breaks in it) no wider than ``room``:
    [(line, how it ends)], the end None (a space was there), "soft" (a word
    divided: the line has the hyphen) or "hard" (broken after a word's own
    hyphen). Broken looking at the whole paragraph (see :func:`_fit`), or
    line by line with ``greedy`` (and for a very long paragraph).
    ``divide(word)`` gives a word's divisions (see :func:`_divisions`)."""
    if _has_cjk(text):
        return _wrap_cjk(text, room, measure)
    words = text.split(" ")
    if greedy or len(words) > FIT_LIMIT or len(words) < 2:
        lines, line = [], ""
        for word in words:
            r = room(len(lines)) if callable(room) else room
            trial = word if not line else line + " " + word
            if line and measure(trial) > r:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
        return [(line, None) for line in lines]
    gap = measure(words[0] + " " + words[1]) - measure(words[0]) - measure(words[1])
    pieces, joins = _pieces_of(words, divide, justify, room, measure)
    hyphen_width = measure("-") if divide else 0.0
    joins = [(j[0], hyphen_width) if j and j[0] == "soft" else j for j in joins]
    out = []
    for i, j in _fit([measure(piece) for piece in pieces], gap, room, justify, joins):
        line = "".join(pieces[k] + (" " if joins[k] is None and k < j - 1 else "") for k in range(i, j))
        end = joins[j - 1][0] if j < len(pieces) and joins[j - 1] else None
        out.append((line + ("-" if end == "soft" else ""), end))
    return out


def _wrap(text, room, measure, justify=False, greedy=False, divide=None):
    """The lines of :func:`_wrap_marked`, as text."""
    return [line for line, _ in _wrap_marked(text, room, measure, justify, greedy, divide)]


def _split_seq(seq, sep):
    """A list of (character, style) split at ``sep`` characters."""
    parts, part = [], []
    for item in seq:
        if item[0] == sep:
            parts.append(part)
            part = []
        else:
            part.append(item)
    parts.append(part)
    return parts


def _wrap_seq_marked(seq, room, measure, justify=False, greedy=False, divide=None):
    """:func:`_wrap_marked` for a list of (character, style)."""
    plain = "".join(ch for ch, _ in seq)
    if _has_cjk(plain):
        tokens = _cjk_tokens(plain)
        if not tokens:
            return [([], None)]
        # Map each token back to its slice of styled (ch, key) items.
        pos, styled_tokens = 0, []
        for tok, sp in tokens:
            while pos < len(seq) and seq[pos][0].isspace():
                pos += 1
            chunk = seq[pos:pos + len(tok)]
            pos += len(tok)
            styled_tokens.append((chunk, sp))
        lines, line = [], []
        for chunk, sp in styled_tokens:
            r = room(len(lines)) if callable(room) else room
            sep = [(" ", chunk[0][1] if chunk else line[-1][1])] if (line and sp) else []
            trial = line + sep + chunk
            if line and measure(trial) > r + 1e-9:
                lines.append(line)
                line = list(chunk)
            else:
                line = trial
        lines.append(line)
        return [(ln, None) for ln in lines]
    words = _split_seq(seq, " ")

    def joined(some, spaced):
        line = []
        for word, space in zip(some, [False] + spaced):
            line += ([(" ", word[0][1] if word else line[-1][1])] if space and line else []) + word
        return line

    if greedy or len(words) > FIT_LIMIT or len(words) < 2 or not (words[0] or words[1]):
        lines, line = [], []
        for word in words:
            r = room(len(lines)) if callable(room) else room
            trial = word if not line else line + [(" ", word[0][1] if word else line[-1][1])] + word
            if line and measure(trial) > r:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
        return [(line, None) for line in lines]
    pair = [w for w in words if w][:2]
    gap = measure(joined(pair, [True])) - measure(pair[0]) - measure(pair[1]) if len(pair) == 2 else 0.0
    pieces, joins = _pieces_of(words, divide, justify, room, measure)
    joins = [(j[0], measure([("-", pieces[k][-1][1])])) if j and j[0] == "soft" else j for k, j in enumerate(joins)]
    out = []
    for i, j in _fit([measure(piece) if piece else 0.0 for piece in pieces], gap, room, justify, joins):
        line = joined(pieces[i:j], [joins[k] is None for k in range(i, j - 1)])
        end = joins[j - 1][0] if j < len(pieces) and joins[j - 1] else None
        out.append((line + ([("-", line[-1][1])] if end == "soft" else []), end))
    return out


def _wrap_seq(seq, room, measure, justify=False, greedy=False, divide=None):
    return [line for line, _ in _wrap_seq_marked(seq, room, measure, justify, greedy, divide)]


def _strip_seq(seq):
    a, b = 0, len(seq)
    while a < b and seq[a][0].isspace():
        a += 1
    while b > a and seq[b - 1][0].isspace():
        b -= 1
    return seq[a:b]


def _alignment(rows, left, right, word, natural, size, first_indent=0.0):
    """How a paragraph's lines are set: "justify", "center", "right" or "left"."""
    tol = max(1.0, 0.1 * size)
    starts = [r[0]["s"] for r in rows]
    ends = [r[-1]["s"] + r[-1]["adv"] for r in rows]
    body = rows[:-1]
    left_ok = (bool(body)
               and abs(starts[0] - (left + first_indent)) < tol
               and all(abs(s - left) < tol for s in starts[1:-1]))
    aligned = bool(body) and all(abs(e - right) < tol for e in ends[:-1]) and left_ok
    if aligned and (len(body) >= 2 or (word is not None and word - natural > max(0.2, natural * 0.08))):
        return "justify"
    if len(rows) >= 2 and not all(abs(s - left) < tol for s in starts) and first_indent == 0.0:
        middles = [(s + e) / 2 for s, e in zip(starts, ends)]
        if max(middles) - min(middles) < tol * 2:
            return "center"
        if all(abs(e - right) < tol for e in ends):
            return "right"
    return "left"


_BULLET_RE = re.compile(r"^(?:[•●▪▫◦‣⁃–—*-]|(?:\(?\d{1,3}[.)]|\(?[A-Za-z][.)]))\s+")


def _detect_indent(rows, size, right):
    """Detect a hanging bullet/numbered marker or a first-line indent.

    Returns ``(marker_count, marker_prefix, body_left, first_indent)`` where
    ``marker_count`` is the number of leading letters in ``rows[0]`` belonging
    to a kept bullet/number marker, ``marker_prefix`` is its text prefix,
    ``body_left`` is the body column's left edge, and ``first_indent`` is any
    extra first-line indent (> 0 for indented non-bullet paragraphs).
    """
    line0 = _text_of(rows[0])
    m = _BULLET_RE.match(line0)
    if m:
        prefix = m.group(0)
        # Find the index in rows[0] of the first letter of the body text after the marker.
        marker_text = prefix.rstrip()
        pos, acc = 0, ""
        while pos < len(rows[0]) and len(acc) < len(marker_text):
            ch = rows[0][pos]["char"]
            if not ch.isspace():
                acc += ch
            pos += 1
        while pos < len(rows[0]) and rows[0][pos]["char"].isspace():
            pos += 1
        if 0 < pos < len(rows[0]):
            body_start = rows[0][pos]["s"]
            # Confirm hanging indent: either multi-line wraps under body_start, or clear gap after marker.
            if len(rows) >= 2:
                rest_left = min(r[0]["s"] for r in rows[1:])
                if rest_left >= rows[0][0]["s"] + size * 0.25 and abs(rest_left - body_start) <= size * 1.5:
                    return pos, prefix, min(body_start, rest_left), 0.0
            elif body_start - rows[0][0]["s"] >= size * 0.5:
                return pos, prefix, body_start, 0.0
    if len(rows) >= 2:
        tol = max(1.5, 0.15 * size)
        rest_starts = [r[0]["s"] for r in rows[1:]]
        rest_left = min(rest_starts)
        ends = [r[-1]["s"] + r[-1]["adv"] for r in rows]
        middles = [(r[0]["s"] + e) / 2 for r, e in zip(rows, ends)]
        first_indent = rows[0][0]["s"] - rest_left
        # Require lines 1..N to share a common left edge and reject centered/right-aligned blocks.
        rest_aligned = all(abs(s - rest_left) <= tol for s in rest_starts)
        is_centered = max(middles) - min(middles) < tol * 2
        is_right = len(rows) == 2 and abs(ends[0] - ends[1]) < tol
        if rest_aligned and not is_centered and not is_right \
                and size * 0.4 < first_indent < max(right - rest_left, size * 4) * 0.5:
            return 0, "", rest_left, first_indent
    return 0, "", min(r[0]["s"] for r in rows), 0.0


def _para_spacing(rows, down, default_lead):
    """Return ``(lead, para_gap)`` measured from ``rows``.

    When ``rows`` spans multiple paragraphs (a baseline gap > 1.35 * lead),
    ``lead`` is the intra-paragraph line spacing and ``para_gap`` is the extra
    space between paragraphs. Otherwise ``para_gap`` defaults to ``0.6 * lead``.
    """
    if len(rows) < 2:
        return default_lead, default_lead * 0.6
    steps = []
    for a, b in zip(rows, rows[1:]):
        step = ((a[0]["origin"][0] - b[0]["origin"][0]) * -down[0]
                + (a[0]["origin"][1] - b[0]["origin"][1]) * -down[1])
        if step > 0:
            steps.append(step)
    if not steps:
        return default_lead, default_lead * 0.6
    ordered = sorted(steps)
    lead = ordered[len(ordered) // 3] if len(ordered) >= 3 else min(ordered)
    big = [s for s in steps if s > lead * 1.35]
    if big:
        para_gap = max(0.0, sorted(big)[len(big) // 2] - lead)
        normal = [s for s in steps if s <= lead * 1.35]
        if normal:
            lead = sum(normal) / len(normal)
        return lead, para_gap
    return sum(steps) / len(steps), (sum(steps) / len(steps)) * 0.6


def _leading_unchanged(rows, text, marks=None):
    """How many leading lines of ``rows`` match the prefix of ``text`` untouched.

    Returns ``(keep_head, text_offset)`` so callers can leave ``rows[:keep_head]``
    byte-identical in the content stream and only re-set from ``keep_head`` on.
    """
    if len(rows) < 2:
        return 0, 0
    pos = 0
    keep = 0
    n = len(text)
    for k in range(len(rows) - 1):
        line_txt = _text_of(rows[k]).strip()
        if not line_txt:
            break
        while pos < n and text[pos] == " ":
            pos += 1
        if not text.startswith(line_txt, pos):
            break
        end = pos + len(line_txt)
        if end < n and not text[end].isspace():
            break
        if marks is not None:
            # Verify bold/italic marks on this line match the original letters.
            row_marks = [(L["style"]["bold"], L["style"]["italic"]) for L in rows[k] if not L["char"].isspace()]
            new_marks = [marks[i] for i in range(pos, end) if not text[i].isspace()]
            if row_marks != new_marks:
                break
        # Don't skip past line k if the next line's first word changed and might now fit on line k.
        nxt_orig = _text_of(rows[k + 1]).strip().split(" ", 1)[0] if k + 1 < len(rows) else ""
        nxt_pos = end
        while nxt_pos < n and text[nxt_pos] == " ":
            nxt_pos += 1
        nxt_new = text[nxt_pos:].split(" ", 1)[0] if nxt_pos < n else ""
        if nxt_orig and nxt_new and nxt_new != nxt_orig and len(nxt_new) < len(nxt_orig):
            break
        keep = k + 1
        pos = nxt_pos
    return keep, pos


def _free_span(y_base, col_left, col_right, size, obstacles):
    """Widest horizontal span inside ``[col_left, col_right]`` on the line at
    ``y_base`` that stays clear of ``obstacles``: ``(span_left, span_right)``."""
    pad_x = max(4.0, size * 0.4)
    pad_y = size * 0.15
    lo, hi = y_base - 0.25 * size - pad_y, y_base + 0.85 * size + pad_y
    blocked = []
    for ox0, oy0, ox1, oy1 in obstacles:
        if oy1 > lo and oy0 < hi and ox1 + pad_x > col_left and ox0 - pad_x < col_right:
            blocked.append((max(col_left, ox0 - pad_x), min(col_right, ox1 + pad_x)))
    if not blocked:
        return col_left, col_right
    blocked.sort()
    spans, cur = [], col_left
    for bx0, bx1 in blocked:
        if bx0 > cur:
            spans.append((cur, bx0))
        cur = max(cur, bx1)
    if cur < col_right:
        spans.append((cur, col_right))
    if not spans:
        return col_left, col_left
    return max(spans, key=lambda s: s[1] - s[0])

