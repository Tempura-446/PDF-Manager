# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""OCR word/line grouping, XY-cut reading order, skew/orientation, and text-layer heuristics."""

import ctypes
import math
import re
import statistics

from pdfmanager.textlayer import Word
from pdfmanager.ocr._facade import _ocr_attr
from pdfmanager.ocr.engines import Line, MIN_CONFIDENCE, MIN_SKEW, MIN_SKEW_BOXES
from pdfmanager.ocr.models import _call_lock, _engine
from pdfmanager.ocr.preprocess import _array, _quad, _read, recognise

_LIGATURES = str.maketrans({
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl",
    "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st",
})
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CJK_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uac00-\ud7af]")


def _normalize_ocr_text(text: str) -> str:
    """Expand Latin ligatures and strip stray control characters."""
    if not text:
        return ""
    return _CTRL_RE.sub("", text.translate(_LIGATURES))


def _is_repeated_border_noise(text: str) -> bool:
    """True when OCR hallucinated a table border, scan edge, or horizontal rule
    (e.g. '||||||||', '========', '--------', '........')."""
    compact = re.sub(r"\s+", "", text or "")
    if len(compact) < 5:
        return False
    if any(ch.isalnum() for ch in compact):
        return False
    return len(set(compact)) <= 2


def _lines(result):
    if not result.txts:
        return []
    lines = []
    word_results = getattr(result, "word_results", None) or [None] * len(result.txts)
    for text, box, score, found in zip(result.txts, result.boxes, result.scores, word_results):
        clean = _normalize_ocr_text(text)
        stripped = clean.strip()
        if score < MIN_CONFIDENCE or not stripped:
            continue
        # Drop isolated 1-character low-confidence punctuation speckles
        if len(stripped) == 1 and not stripped.isalnum() and float(score) < 0.65:
            continue
        # Drop hallucinated repeated-symbol border/ruler lines
        if _is_repeated_border_noise(stripped):
            continue
        words = _words(clean, found) if found else [Word(clean, _quad(box), float(score))]
        words = [Word(_normalize_ocr_text(w.text), w.quad, w.confidence) for w in words]
        words = [w for w in (_without_blanks(word) for word in words) if w]
        if not words:
            continue
        lines.append(Line(clean, _quad(box), float(score), words))
    return reading_order(lines)


def _without_blanks(word):
    """A word without what the model makes of a form's blank to write on
    ("birth:__", "/_ _"): the rule is not text. None when nothing is left."""
    if "_" not in word.text:
        return word
    text = re.sub(r"_[_/\-–—.\s]*", "", word.text).strip()
    return Word(text, word.quad, word.confidence) if re.search(r"\w", text) else None


def _words(text, found):
    """The words of a line from the model's word boxes. For Latin text the
    model gives one box per word (and splits words better than its own line
    text, which can lose a space on a rough scan); for Chinese and Japanese
    it gives one box per character. So single-character pieces are joined
    into a word until the line's text puts a space between them, and a
    piece of several characters is always a word of its own."""
    pieces = [(w_text.strip(), _quad(w_box), float(w_score)) for w_text, w_score, w_box in found if w_text.strip()]
    if "".join(piece[0] for piece in pieces) != text.replace(" ", ""):
        return [Word(*piece) for piece in pieces if piece[2] >= MIN_CONFIDENCE]
    words, pos, run = [], 0, False   # run: the last word is joined single characters
    for piece_text, quad, score in pieces:
        space_before = False
        while pos < len(text) and text[pos] == " ":
            space_before, pos = True, pos + 1
        pos += len(piece_text)
        if words and run and len(piece_text) == 1 and not space_before:
            space_before = _unwritten_space(words[-1], piece_text, quad)
        if words and run and not space_before and len(piece_text) == 1:
            last = words[-1]
            words[-1] = Word(last.text + piece_text, _union([last.quad, quad]), min(last.confidence, score))
        else:
            words.append(Word(piece_text, quad, score))
            run = len(piece_text) == 1
    return [w for w in words if w.confidence >= MIN_CONFIDENCE]


def _wide(char):
    return bool(_WIDE.match(char))


_WIDE = re.compile("[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]")


def _unwritten_space(last, char, quad):
    """Whether a space belongs between a word so far and the next character
    although the model's text has none. A line with Chinese in it comes
    letter by letter, and the model drops the spaces around and inside its
    English: "发票号码Invoice No.2024". One is put back where Chinese meets a
    Latin letter, and inside Latin text where the letters stand apart and
    the kinds change (windTraders, No.2024)."""
    before = last.text[-1]
    if _wide(before) != _wide(char):
        return (char if _wide(before) else before).isalpha()
    if _wide(char):
        return False
    height = abs(quad[3][1] - quad[0][1]) or 1
    apart = quad[0][0] - last.quad[1][0] >= 0.04 * height
    return apart and ((before.islower() and char.isupper())
                      or (before in ".,:;)" and char.isalnum() and not (before in ".," and char.isdigit() and last.text[-2:-1].isdigit()))
                      or (before.isalpha() != char.isalpha() and before.isalnum() and char.isalnum()))


def _union(quads):
    """One box around several boxes on a line: the first's left edge, the
    last's right edge."""
    quads = list(quads)
    first, last = quads[0], quads[-1]
    return [first[0], last[1], last[2], first[3]]


def _vertical_extent(quad):
    ys = [y for _, y in quad]
    return min(ys), max(ys)


def _rows(lines):
    """``lines`` grouped into rows, top to bottom, each row left to right.
    Boxes whose vertical middles fall within each other share a row, so a
    table row the detector split at a wide gap reads across before going
    down."""
    rows = []
    for line in sorted(lines, key=lambda l: sum(_vertical_extent(l.quad)) / 2):
        top, bottom = _vertical_extent(line.quad)
        middle = (top + bottom) / 2
        for row in rows:
            if row["top"] <= middle <= row["bottom"]:
                row["lines"].append(line)
                row["top"], row["bottom"] = min(row["top"], top), max(row["bottom"], bottom)
                break
        else:
            rows.append({"top": top, "bottom": bottom, "lines": [line]})
    return [sorted(row["lines"], key=lambda l: min(x for x, _ in l.quad)) for row in rows]


def _bbox(line):
    xs, ys = [x for x, _ in line.quad], [y for _, y in line.quad]
    return min(xs), min(ys), max(xs), max(ys)


def _gaps(spans):
    """The empty stretches between ``spans`` ((start, end) pairs) along one
    axis: (width, where to cut) for each."""
    gaps, reach = [], None
    for start, end in sorted(spans):
        if reach is not None and start > reach:
            gaps.append((start - reach, (start + reach) / 2))
        reach = end if reach is None else max(reach, end)
    return gaps


def _effective_word_count(text: str) -> int:
    cjk = len(_CJK_CHAR_RE.findall(text or ""))
    non_cjk = len(_CJK_CHAR_RE.sub(" ", text or "").split())
    return cjk + non_cjk


def _prose(lines):
    """Whether lines read like a column of running text, not table cells:
    several words a line, and lines that fill most of their column."""
    if len(lines) < 2:
        return False
    boxes = [_bbox(line) for line in lines]
    width = max(b[2] for b in boxes) - min(b[0] for b in boxes)
    words = statistics.median(_effective_word_count(line.text) for line in lines)
    filled = statistics.median(b[2] - b[0] for b in boxes)
    # Upright vertical CJK columns are tall rather than wide; accept them as prose too.
    tall_cjk = sum(1 for ln, b in zip(lines, boxes) if (b[3] - b[1]) > 1.2 * (b[2] - b[0]) and _CJK_CHAR_RE.search(ln.text))
    if tall_cjk >= max(2, len(lines) // 2) and words >= 3:
        return True
    return words >= 3 and filled >= 0.5 * width


def _columns(lines):
    """Whether lines are a column of running text, or several side by side
    (the two right-hand columns of three don't fill half their joint width,
    but each does its own)."""
    if _prose(lines):
        return True
    boxes = [_bbox(line) for line in lines]
    for _, cut in _gaps([(b[0], b[2]) for b in boxes]):
        left = [l for l, b in zip(lines, boxes) if b[2] <= cut]
        right = [l for l, b in zip(lines, boxes) if b[0] >= cut]
        if _prose(left) and _columns(right) or _columns(left) and _prose(right):
            return True
    return False


def _layout(lines):
    """``lines`` as rows in reading order. The page is cut at its widest
    empty stretch, again and again: across, between blocks (a heading, the
    body, a footer); or down, between columns of running text, which are then
    read one after the other. Table columns are not prose, so a table is cut
    across into its rows, each read left to right."""
    if len(lines) < 2:
        return _rows(lines)
    boxes = {id(line): _bbox(line) for line in lines}
    down = [(width, cut) for width, cut in _gaps([(b[0], b[2]) for b in boxes.values()])
            if _columns([l for l in lines if boxes[id(l)][2] <= cut])
            and _columns([l for l in lines if boxes[id(l)][0] >= cut])]
    across = _gaps([(b[1], b[3]) for b in boxes.values()])
    if down and (not across or max(down)[0] >= max(across)[0]):
        cut = max(down)[1]
        return (_layout([l for l in lines if boxes[id(l)][2] <= cut])
                + _layout([l for l in lines if boxes[id(l)][0] >= cut]))
    if across:
        cut = max(across)[1]
        return (_layout([l for l in lines if boxes[id(l)][3] <= cut])
                + _layout([l for l in lines if boxes[id(l)][1] >= cut]))
    return _rows(lines)


def reading_order(lines):
    """``lines`` in reading order: blocks top to bottom, columns of running
    text one after the other, and rows left to right."""
    return [line for row in _ordered(lines) for line in row]


def _ordered(lines):
    """``lines`` as rows in reading order, however crooked the scan.
    For predominantly upright vertical CJK (tategaki) pages, columns are sorted
    right-to-left and top-to-bottom."""
    level, real = _straight(lines)
    if len(level) >= 2:
        boxes = [_bbox(ln) for ln in level]
        tall_cjk = sum(
            1 for ln, b in zip(level, boxes)
            if (b[3] - b[1]) > 1.2 * (b[2] - b[0]) and _CJK_CHAR_RE.search(ln.text or "")
        )
        if tall_cjk >= max(2, len(level) // 2):
            widths = sorted(max(1.0, b[2] - b[0]) for b in boxes)
            col_bin = max(4.0, widths[len(widths) // 2] * 0.65)
            ordered_v = sorted(
                zip(level, boxes),
                key=lambda pair: (-round(((pair[1][0] + pair[1][2]) / 2.0) / col_bin), pair[1][1]),
            )
            return [[real.get(id(ln), ln)] for ln, _b in ordered_v]
    return [[real.get(id(line), line) for line in row] for row in _layout(level)]


def _tilt(lines):
    """The slope of the text, in radians, from the lines' own boxes (image
    y points down); 0.0 when there is too little to tell."""
    angles = []
    for line in lines:
        (x0, y0), (x1, y1), (x2, y2), _ = line.quad
        if math.hypot(x1 - x0, y1 - y0) > 2 * math.hypot(x2 - x1, y2 - y1):
            angles.append(math.atan2(y1 - y0, x1 - x0))
    if len(angles) < MIN_SKEW_BOXES:
        return 0.0
    angle = statistics.median(angles)
    return angle if abs(math.degrees(angle)) >= MIN_SKEW else 0.0


def _straight(lines):
    """Copies of ``lines`` with their boxes turned level, each with the line
    it stands for. On a crooked scan the cells of one table row drift down
    the page until they overlap the next row's; order is worked out on the
    level copies."""
    angle = _tilt(lines)
    if not angle:
        return lines, {}
    cos, sin = math.cos(-angle), math.sin(-angle)
    level, real = [], {}
    for line in lines:
        copy = Line(line.text, [(x * cos - y * sin, x * sin + y * cos) for x, y in line.quad],
                    line.confidence, line.words)
        level.append(copy)
        real[id(copy)] = line
    return level, real


def text_of(lines, *, dehyphenate=False):
    """The recognised text as a reader gets it: words separated by spaces,
    one line per row, columns one after the other."""
    rows = [" ".join(w.text for line in row for w in line.words).replace("\u00ad", "") for row in _ordered(lines)]
    if not dehyphenate or len(rows) < 2:
        return "\n".join(rows)
    merged = [rows[0]]
    for nxt in rows[1:]:
        prev = merged[-1]
        if prev.endswith(("-", "\u2010", "\u2011")) and len(prev) >= 2 and prev[-2].isalpha() and nxt and nxt[0].islower():
            merged[-1] = prev[:-1] + nxt
        else:
            merged.append(nxt)
    return "\n".join(merged)


def skew_angle(image):
    """How far the text in ``image`` tilts, in degrees (positive: it rises
    to the right); 0.0 when it is straight or there is too little text to
    tell. Only the text detector runs, so this is quick."""
    engine = _ocr_attr("_engine", _engine)()
    with _call_lock:
        boxes = engine(_array(image), use_det=True, use_cls=False, use_rec=False).boxes
    if boxes is None:
        return 0.0
    angles = []
    for box in boxes:
        (x0, y0), (x1, y1), (x2, y2), _ = _quad(box)
        width, height = math.hypot(x1 - x0, y1 - y0), math.hypot(x2 - x1, y2 - y1)
        if width > 2 * height:
            angles.append(-math.degrees(math.atan2(y1 - y0, x1 - x0)))  # image y points down
    if len(angles) < MIN_SKEW_BOXES:
        return 0.0
    angle = statistics.median(angles)
    return 0.0 if abs(angle) < MIN_SKEW else round(angle, 2)


ORIENTATION_SIDE = 1200   # pixels; the long side of the copy _ocr_attr("page_orientation", page_orientation)() reads
SURE_SCORE = 0.8          # how sure of a line the model must be for it to count there


def _box_size(box):
    (x0, y0), (x1, y1), (x2, y2), _ = _quad(box)
    return math.hypot(x1 - x0, y1 - y0), math.hypot(x2 - x1, y2 - y1)


def _reading_score(engine, image):
    """How much text the model reads with confidence in ``image``, upright:
    the characters of the lines it is sure of, in boxes wider than tall."""
    with _call_lock:
        result = engine(_array(image), use_det=True, use_cls=False, use_rec=True)
    score = 0.0
    for text, box, sure in zip(result.txts or (), result.boxes if result.boxes is not None else (),
                               result.scores or ()):
        width, height = _box_size(box)
        if sure >= SURE_SCORE and width > height:
            score += sure * len(text)
    return score


def page_orientation(image, script=None, quality="standard"):
    """How far to turn ``image`` (degrees counter-clockwise: 0, 90, 180 or
    270) so that its text reads upright; 0 when it already does, or when
    there is too little text to tell. The shape of the text boxes says
    whether the lines run across or down the page, and reading both ways
    round says which way up."""
    small = image.copy()
    small.thumbnail((ORIENTATION_SIDE, ORIENTATION_SIDE))
    engine = _ocr_attr("_engine", _engine)(script, quality)
    with _call_lock:
        boxes = engine(_array(small), use_det=True, use_cls=False, use_rec=False).boxes
    across = down = 0.0
    for box in boxes if boxes is not None else ():
        width, height = _box_size(box)
        if width > 1.2 * height:
            across += width
        elif height > 1.2 * width:
            down += height
    if not across and not down:
        return 0
    candidates = (90, 270) if down > across else (0, 180)
    scores = {turn: _reading_score(engine, small.rotate(turn, expand=True)) for turn in candidates}
    best, other = sorted(candidates, key=scores.get, reverse=True)
    if scores[best] < 20 or scores[best] < 1.5 * scores[other]:
        return 0
    return best


def _reads_wrong_way(result):
    """Whether a page's first reading suggests it is sideways or upside
    down: its text boxes run down the page, or the model is unsure of most
    of what it read (upside-down text reads as low-confidence nonsense).
    Upright vertical CJK (tategaki) columns are tall by design and are not
    misclassified as sideways."""
    if isinstance(result, (list, tuple)):
        texts = [ln.text for ln in result]
        scores = [ln.confidence for ln in result]
        boxes = [ln.quad for ln in result]
    else:
        texts, scores = result.txts or (), result.scores or ()
        boxes = result.boxes if result.boxes is not None else ()
    across = down = 0.0
    cjk_down = 0.0
    for text, box, score in zip(texts, boxes, scores):
        width, height = _box_size(box)
        if width > 1.2 * height:
            across += width
        elif height > 1.2 * width:
            down += height
            if score >= 0.75 and _CJK_CHAR_RE.search(text or ""):
                cjk_down += height
    if down > across:
        # Upright vertical CJK (tategaki) reads cleanly top-to-bottom without 90° rotation.
        if cjk_down >= 0.5 * down:
            return False
        return True
    chars = sum(len(t) for t in texts)
    if not chars:
        return False
    return sum(s * len(t) for t, s in zip(texts, scores)) / chars < 0.75


# -- running OCR on a file ------------------------------------------------------

def page_has_text(page):
    """Whether a page already has selectable text (then it is left alone
    unless ``force`` says otherwise, as OCRmyPDF's skip-text does)."""
    try:
        return bool(page.extract_text().strip())
    except Exception:  # a page too damaged to read is treated as a scan
        return False


def layer_is_rubbish(text):
    """Whether a page's text is one nobody could have meant: signs, broken
    letters and control codes, as an old program's failed reading leaves
    behind a scan. Real text in any script, figures and codes are not; a
    few characters are too little to judge."""
    import unicodedata

    chars = [c for c in text if not c.isspace()]
    if len(chars) < 20:
        return False
    broken = sum(1 for c in chars if c == "\ufffd" or unicodedata.category(c) in ("Co", "Cc", "Cn", "Cs"))
    letters = sum(1 for c in chars if c.isalnum())
    if broken / len(chars) > 0.15 or letters / len(chars) < 0.5:
        return True
    # Letters, but not words: mostly single characters and signs strung together.
    tokens = text.split()
    wordlike = sum(1 for t in tokens if sum(c.isalnum() for c in t) >= max(2, len(t) * 0.6)
                   or any("\u2e80" <= c for c in t))  # (a CJK character is a word)
    return wordlike / max(len(tokens), 1) < 0.3


PICTURE_SHARE = 0.03      # of the page: a smaller picture (a logo, an icon) isn't looked into
PICTURE_PIXELS = (120, 40)  # ... nor one smaller than this when drawn for reading
PICTURES_PER_PAGE = 8
PICTURE_CONFIDENCE = 0.6  # a word read in a picture counts when the engine is this sure of it
PICTURE_LETTERS = 4       # ... and a picture's words are added when they have this many letters and digits
PICTURE_TEXT = 40         # with this many letters and digits a picture is text: all its words stay


def _char_kinds(textpage):
    """(letters a reader sees, letters set invisibly as OCR sets them) on a page."""
    import pypdfium2.raw as raw

    seen = hidden = 0
    modes = {}
    for i in range(textpage.count_chars()):
        if raw.FPDFText_GetUnicode(textpage.raw, i) in (32, 13, 10, 9) or raw.FPDFText_IsGenerated(textpage.raw, i) == 1:
            continue
        obj = raw.FPDFText_GetTextObject(textpage.raw, i)
        key = ctypes.cast(obj, ctypes.c_void_p).value if obj else None
        if key not in modes:
            modes[key] = raw.FPDFTextObj_GetTextRenderMode(obj) if obj else 0
        if modes[key] == 3:
            hidden += 1
        else:
            seen += 1
    return seen, hidden


def _picture_boxes(pdfium_page, textpage, image_size):
    """The page's pictures worth reading, as boxes in the pixels of its
    render (``image_size``): [(left, top, right, bottom)], largest first.
    Small ones are passed over, and those with invisible text over them
    (read already)."""
    import pypdfium2.raw as raw

    width, height = image_size
    page_area = max(pdfium_page.get_width() * pdfium_page.get_height(), 1.0)
    found = []
    for obj in pdfium_page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=4):
        try:
            left, bottom, right, top = obj.get_bounds()
        except Exception:  # noqa: BLE001 - a picture pdfium can't place
            continue
        if (right - left) * (top - bottom) < page_area * PICTURE_SHARE:
            continue
        xs, ys = [], []
        for x, y in ((left, bottom), (right, bottom), (right, top), (left, top)):
            dx, dy = ctypes.c_int(), ctypes.c_int()
            raw.FPDF_PageToDevice(pdfium_page.raw, 0, 0, width, height, 0, x, y, ctypes.byref(dx), ctypes.byref(dy))
            xs.append(dx.value)
            ys.append(dy.value)
        box = (max(min(xs), 0), max(min(ys), 0), min(max(xs), width), min(max(ys), height))
        if box[2] - box[0] < PICTURE_PIXELS[0] or box[3] - box[1] < PICTURE_PIXELS[1]:
            continue
        if _hidden_text_in(textpage, left, bottom, right, top):
            continue
        found.append(box)
    found.sort(key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))
    kept = []
    for box in found:  # (a picture drawn twice, or one inside another, is read once)
        if not any(k[0] <= box[0] and k[1] <= box[1] and k[2] >= box[2] and k[3] >= box[3] for k in kept):
            kept.append(box)
    return kept[:PICTURES_PER_PAGE]


def _hidden_text_in(textpage, left, bottom, right, top):
    """Whether invisible text (an OCR layer) lies in the box (page coordinates)."""
    import pypdfium2.raw as raw

    hits = 0
    for i in range(textpage.count_chars()):
        if raw.FPDFText_GetUnicode(textpage.raw, i) in (32, 13, 10, 9) or raw.FPDFText_IsGenerated(textpage.raw, i) == 1:
            continue
        obj = raw.FPDFText_GetTextObject(textpage.raw, i)
        if not obj or raw.FPDFTextObj_GetTextRenderMode(obj) != 3:
            continue
        l, b, r, t = textpage.get_charbox(i)
        if left <= (l + r) / 2 <= right and bottom <= (b + t) / 2 <= top:
            hits += 1
            if hits > 3:
                return True
    return False


def _strip_layers(source, pages, target):
    """A copy of ``source`` whose ``pages`` (indices) have their text taken
    out, pictures and drawing left exactly as they are."""
    import pikepdf

    from pdfmanager import layout

    with pikepdf.open(str(source)) as pdf:
        for i in pages:
            layout._strip_text(pdf, pdf.pages[i])
        pdf.save(str(target))
    return target


UNSURE_PAGE_MIN = 3       # a page with at least this many unsure words (or >= 10% unsure) is flagged
UNSURE_PAGE_SHARE = 0.10


def _page_list(nums):
    return ", ".join(str(n) for n in nums)


def format_report(report):
    """Human-readable summary lines from an OCR ``report`` dict: pages read,
    pages skipped and why, and pages with many unsure words."""
    if not report:
        return []
    lines = []
    read = report.get("pages_read") or []
    skipped = report.get("skipped") or {}
    parts = []
    if read:
        parts.append(f"Read {len(read)} page{'s' if len(read) != 1 else ''}")
    skip_bits = []
    total_skipped = 0
    for reason, pages in skipped.items():
        if pages:
            total_skipped += len(pages)
            skip_bits.append(f"{reason}: {_page_list(pages)}")
    if total_skipped:
        parts.append(f"skipped {total_skipped} page{'s' if total_skipped != 1 else ''} ({'; '.join(skip_bits)})")
    if parts:
        lines.append("; ".join(parts) + ".")
    flagged = report.get("flagged_pages") or []
    if flagged:
        n = len(flagged)
        lines.append(f"{n} page{'s' if n != 1 else ''} may have mistakes: {_page_list(flagged)}")
    return lines
