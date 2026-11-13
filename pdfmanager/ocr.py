"""Making scanned pages searchable: two engines.

The built-in engine runs PaddleOCR's PP-OCR models (through the rapidocr
package, on ONNX Runtime). It reads degraded scans better than Tesseract,
reads English, most European languages, Chinese and Japanese with the model
it ships with and no language setting, and needs nothing installed beside
ZPDF. Russian, Korean, Arabic, Hindi, Thai, Greek, Tamil and Telugu each
have a reading model of their own, fetched once (and checked) the first time
a page in that script is read. It turns sideways and upside-down pages
upright and reads multi-column pages column by column. Its text layer is
ZPDF's own (textlayer.py).

Tesseract, through OCRmyPDF, stays as the second engine: for the scripts
the built-in engine has no model for, and for PDF/A output straight from OCR.
"""
import importlib.util
import logging
import math
import os
import re
import statistics
import threading
from dataclasses import dataclass
from pathlib import Path

from pdfmanager import core, scan, textlayer
from pdfmanager.textlayer import Word

__all__ = ["Word", "Line", "ENGINES", "choose_engine", "script_for", "recognise", "reading_order",
           "text_of", "skew_angle", "page_orientation"]

ENGINES = ("auto", "builtin", "tesseract")
MIN_CONFIDENCE = 0.3   # words and lines the model is less sure of than this are dropped
RENDER_DPI = 300
MAX_RENDER_SIDE = 6000  # pixels; a 300 dpi A0 drawing would be 14000 and gain nothing (the model works at 2000)

# Tesseract's codes for the languages the model ZPDF ships with reads.
BUILTIN_LANGUAGES = frozenset("""
    afr aze bos cat ces chi_sim chi_tra cym dan deu eng est eus fin fra gle glg hrv hun ind isl
    ita jpn kur lat lav lit ltz mlt mri msa nld nor oci pol por que roh ron slk slv spa sqi srp_latn
    swa swe tgl tur uzb vie equ osd
""".split())
# The scripts with a reading model of their own (PP-OCRv5, fetched on first
# use), by Tesseract's language codes. With engine "auto", a language that is
# in neither list goes to Tesseract when it is there.
SCRIPT_LANGUAGES = {
    "eslav": "rus ukr bel",
    "cyrillic": "bul srp mkd kaz kir mon tgk tat bak chv uzb_cyrl aze_cyrl",
    "korean": "kor kor_vert",
    "arabic": "ara fas urd uig pus snd",
    "devanagari": "hin mar nep san",
    "th": "tha",
    "el": "ell grc",
    "ta": "tam",
    "te": "tel",
}
SCRIPT_NAMES = {"eslav": "Russian", "cyrillic": "Cyrillic", "korean": "Korean", "arabic": "Arabic",
                "devanagari": "Devanagari", "th": "Thai", "el": "Greek", "ta": "Tamil", "te": "Telugu"}
_SCRIPT_OF = {code: script for script, codes in SCRIPT_LANGUAGES.items() for code in codes.split()}

MIN_SKEW = 0.3         # degrees; smaller tilts are left alone
MIN_SKEW_BOXES = 3     # wide text boxes needed to trust a skew estimate

TESSERACT_LINK = "https://tesseract-ocr.github.io/tessdoc/Installation.html"


@dataclass
class Line:
    """A recognised line of text with its words; boxes in image pixels."""
    text: str
    quad: list
    confidence: float
    words: list


_builtin_imports = None  # None: not tried yet


def builtin_available():
    """Whether the built-in engine can be used: its packages import. (A
    package that is installed but broken, like onnxruntime without its
    runtime library on Windows, counts as missing, so "auto" can turn to
    Tesseract.)"""
    global _builtin_imports
    if _builtin_imports is None:
        try:
            import onnxruntime
            import rapidocr

            _builtin_imports = bool(onnxruntime and rapidocr)
        except Exception:
            _builtin_imports = False
    return _builtin_imports


def tesseract_available():
    return bool(importlib.util.find_spec("ocrmypdf")) and bool(scan.find_tesseract())


def _codes(language):
    return [code.strip() for code in (language or "").split("+") if code.strip()]


def script_for(language):
    """Which reading model the built-in engine needs for ``language``
    (Tesseract codes joined with '+'): None for the one it ships with, or
    the name of a script model. ValueError when it has none for a language,
    or the languages need two different script models (a page is read with
    one); English and the other languages of the shipped model mix with any
    script, as each script model reads Latin letters and digits too."""
    scripts = []
    for code in _codes(language):
        if code in BUILTIN_LANGUAGES:
            continue
        script = _SCRIPT_OF.get(code)
        if script is None:
            raise ValueError(f"no built-in reading model for {code!r}")
        if script not in scripts:
            scripts.append(script)
    if len(scripts) > 1:
        raise ValueError("one page is read with one script model: " + " and ".join(scripts))
    return scripts[0] if scripts else None


def _best_script(language):
    """script_for(), or for languages it refuses the first script it knows
    (the built-in engine asked for by name does what it can)."""
    try:
        return script_for(language)
    except ValueError:
        return next((_SCRIPT_OF[c] for c in _codes(language) if c in _SCRIPT_OF), None)


def choose_engine(engine, language="eng"):
    """'builtin' or 'tesseract' for ``engine`` (one of ENGINES), or a
    PdfManagerError saying what to install. ``language`` (Tesseract codes)
    matters for "auto": a language the built-in engine has no reading model
    for, such as Hebrew, goes to Tesseract when it is installed."""
    if engine not in ENGINES:
        raise core.PdfManagerError(f"unknown OCR engine {engine!r}; choose auto, builtin or tesseract")
    if engine == "auto":
        try:
            script_for(language)
            readable = True
        except ValueError:
            readable = False
        if builtin_available() and (readable or not tesseract_available()):
            return "builtin"
        if tesseract_available():
            return "tesseract"
        raise core.PdfManagerError(
            "OCR needs a reading engine, and neither is installed: reinstall ZPDF to get its built-in "
            f"one back ({PIP_OCR}), or install the Tesseract program ({TESSERACT_LINK}) and OCRmyPDF "
            f"({PIP_TESSERACT})")
    if engine == "builtin" and not builtin_available():
        raise core.PdfManagerError(
            f"OCR's built-in engine isn't installed with this copy of ZPDF: reinstall ZPDF ({PIP_OCR})"
            + (", or choose the Tesseract engine" if tesseract_available() else ""))
    if engine == "tesseract" and not tesseract_available():
        raise core.PdfManagerError(_tesseract_missing())
    return engine


PIP_OCR = "from Python: pip install pdf-manager[ocr]"
PIP_TESSERACT = "from Python: pip install pdf-manager[tesseract]"


def _tesseract_missing(no_ocrmypdf=False):
    """What OCR with Tesseract lacks here, and what to do (``no_ocrmypdf``:
    it didn't import)."""
    instead = ", or choose the Built-in engine" if builtin_available() else ""
    if no_ocrmypdf or not importlib.util.find_spec("ocrmypdf"):
        also = "" if scan.find_tesseract() else f", and the Tesseract program ({TESSERACT_LINK})"
        return ("OCR with Tesseract needs OCRmyPDF, which isn't installed with this copy of ZPDF "
                f"({PIP_TESSERACT}){also}{instead}")
    return ("OCR with Tesseract needs the Tesseract program, which isn't installed on this computer: "
            f"install it ({TESSERACT_LINK}) and try again{instead}")


# -- the built-in engine -------------------------------------------------------

_engine_lock = threading.Lock()   # building the instances
_call_lock = threading.Lock()     # using them: each keeps the last call's settings
_engines = {}                     # script (None: the shipped model) -> RapidOCR


def models_dir():
    """Where fetched reading models are kept: ZPDF's own folder for this user."""
    from pdfmanager import appdata

    return appdata.data_dir() / "ocr-models"


def _model_source(script):
    """(address, SHA-256) of a script's reading model, from rapidocr's own
    list of models: its ONNX build (the list also has the same models built
    for other engines, which ONNX Runtime can't load)."""
    import rapidocr
    import yaml

    key = f"{script}_PP-OCRv5_rec_mobile"
    listing = yaml.safe_load((Path(rapidocr.__file__).parent / "default_models.yaml").read_text(encoding="utf-8"))
    stack = [listing.get("onnxruntime", {}).get("PP-OCRv5", {})]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            entry = node.get(key)
            if isinstance(entry, dict) and entry.get("model_dir") and entry.get("SHA256"):
                return entry["model_dir"], entry["SHA256"].lower()
            stack.extend(node.values())
    raise core.PdfManagerError(f"this rapidocr has no {SCRIPT_NAMES.get(script, script)} reading model")


def _fetch(url, target):
    import urllib.request

    with urllib.request.urlopen(url, timeout=60) as response, open(target, "wb") as out:
        while True:
            chunk = response.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)


def _sha256(path):
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _language_model(script):
    """The path of a script's reading model, fetched and checked on first use."""
    from pdfmanager import progress

    name = SCRIPT_NAMES.get(script, script)
    url, digest = _model_source(script)
    target = models_dir() / f"{script}.onnx"
    if target.exists() and _sha256(target) == digest:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    progress.say(f"getting the {name} reading model (once, about 10 MB)")
    try:
        _fetch(url, part)
    except Exception as exc:
        part.unlink(missing_ok=True)
        raise core.PdfManagerError(
            f"couldn't get the {name} reading model ({exc}); connect to the internet once so "
            "ZPDF can fetch it, or choose the Tesseract engine") from None
    if _sha256(part) != digest:
        part.unlink(missing_ok=True)
        raise core.PdfManagerError(
            f"the {name} reading model came down damaged; try again, or choose the Tesseract engine")
    os.replace(part, target)
    return target


def _engine(script=None):
    """The RapidOCR instance for a script (None: the model ZPDF ships with).
    Models take about a second to load, so each is made once."""
    with _engine_lock:
        if script not in _engines:
            params = {"Global.return_word_box": True}
            if script is not None:
                from rapidocr import ModelType, OCRVersion

                params.update({"Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.lang_type": script,
                               "Rec.model_type": ModelType.MOBILE,
                               "Rec.model_path": str(_language_model(script))})
            try:
                from rapidocr import RapidOCR

                _engines[script] = RapidOCR(params=params)
            except Exception as exc:  # a broken install, or model files missing from a build
                raise core.PdfManagerError(
                    f"OCR's built-in engine could not start; reinstall ZPDF ({PIP_OCR}) or choose the "
                    f"Tesseract engine ({exc})") from None
            # It announces every model it loads; only trouble is worth hearing.
            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        return _engines[script]


def reset():
    """Forget the loaded engines and what was learnt about the install (tests)."""
    global _builtin_imports
    with _engine_lock:
        _engines.clear()
        _builtin_imports = None


def _array(image):
    import numpy

    return numpy.asarray(image.convert("RGB"))


GREY_PAPER = 190   # a page whose paper is darker than this (of 255) is stretched to full contrast


def _clear(image):
    """``image`` with its contrast stretched when the paper is grey: on dark
    ink over mid-grey paper the model reads the letters but loses the spaces
    between words, and reads them all once the paper is white."""
    from PIL import ImageOps

    small = image.convert("L")
    small.thumbnail((200, 200))
    levels = sorted(small.tobytes())
    if not levels or levels[len(levels) // 2] >= GREY_PAPER:
        return image
    return ImageOps.autocontrast(image.convert("RGB"), cutoff=1)


def _quad(points):
    return [(float(x), float(y)) for x, y in points]


def _read(image, script=None):
    """The model's raw result for an image. Every switch is named each
    time: an engine keeps the last call's settings, and skew_angle() runs
    the detector alone. The per-line flip check stays off: on short crops it
    guesses wrong (a lone "6" became "9", 7 pt print turned to nonsense), and
    whole pages are turned upright by page_orientation() instead."""
    engine = _engine(script)
    with _call_lock:
        return engine(_array(_clear(image)), use_det=True, use_cls=False, use_rec=True)


def recognise(image, script=None):
    """The lines of text in a PIL image, each with its words, in reading
    order. ``script``: see script_for()."""
    return _lines(_read(image, script))


def _lines(result):
    if not result.txts:
        return []
    lines = []
    word_results = getattr(result, "word_results", None) or [None] * len(result.txts)
    for text, box, score, found in zip(result.txts, result.boxes, result.scores, word_results):
        if score < MIN_CONFIDENCE or not text.strip():
            continue
        words = _words(text, found) if found else [Word(text, _quad(box), float(score))]
        words = [w for w in (_without_blanks(word) for word in words) if w]
        if not words:
            continue
        lines.append(Line(text, _quad(box), float(score), words))
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


def _prose(lines):
    """Whether lines read like a column of running text, not table cells:
    several words a line, and lines that fill most of their column."""
    if len(lines) < 2:
        return False
    boxes = [_bbox(line) for line in lines]
    width = max(b[2] for b in boxes) - min(b[0] for b in boxes)
    words = statistics.median(len(line.text.split()) for line in lines)
    filled = statistics.median(b[2] - b[0] for b in boxes)
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
    """``lines`` as rows in reading order, however crooked the scan."""
    level, real = _straight(lines)
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


def text_of(lines):
    """The recognised text as a reader gets it: words separated by spaces,
    one line per row, columns one after the other."""
    return "\n".join(" ".join(w.text for line in row for w in line.words) for row in _ordered(lines))


def skew_angle(image):
    """How far the text in ``image`` tilts, in degrees (positive: it rises
    to the right); 0.0 when it is straight or there is too little text to
    tell. Only the text detector runs, so this is quick."""
    engine = _engine()
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


ORIENTATION_SIDE = 1200   # pixels; the long side of the copy page_orientation() reads
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


def page_orientation(image, script=None):
    """How far to turn ``image`` (degrees counter-clockwise: 0, 90, 180 or
    270) so that its text reads upright; 0 when it already does, or when
    there is too little text to tell. The shape of the text boxes says
    whether the lines run across or down the page, and reading both ways
    round says which way up."""
    small = image.copy()
    small.thumbnail((ORIENTATION_SIDE, ORIENTATION_SIDE))
    engine = _engine(script)
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
    of what it read (upside-down text reads as low-confidence nonsense)."""
    texts, scores = result.txts or (), result.scores or ()
    boxes = result.boxes if result.boxes is not None else ()
    across = down = 0.0
    for box in boxes:
        width, height = _box_size(box)
        if width > 1.2 * height:
            across += width
        elif height > 1.2 * width:
            down += height
    if down > across:
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


def run(path, output, language="eng", force=False, deskew=False, password=None, engine="auto",
        rotate=True):
    """Add a searchable text layer to the scanned pages of ``path``; see
    ``core.ocr``."""
    from pdfmanager import progress

    chosen = choose_engine(engine, language)
    script = _best_script(language) if chosen == "builtin" else None
    if script is not None:
        try:
            _language_model(script)  # fetched now, before the file is touched
        except core.PdfManagerError:
            if engine != "auto" or not tesseract_available():
                raise
            progress.say(f"the {SCRIPT_NAMES[script]} reading model can't be fetched; using Tesseract")
            chosen, script = "tesseract", None
    source = Path(path)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_in = None
    if core.open_pdf(path, password).is_encrypted:  # also a plain error for a damaged file
        # Neither engine opens encrypted files; give them a decrypted copy.
        tmp_in = output.with_name(output.name + ".src.tmp.pdf")
        core.decrypt(path, tmp_in, password or "")
        source = tmp_in
    tmp_out = output.with_name(output.name + ".tmp.pdf")
    progress.say("recognising text (this can take a while)")
    try:
        if chosen == "tesseract":
            _run_tesseract(source, tmp_out, language, force, deskew, rotate)
        else:
            _run_builtin(source, tmp_out, force, deskew, rotate, script)
        os.replace(tmp_out, output)
    finally:
        for tmp in (tmp_in, tmp_out):
            if tmp is not None and tmp.exists():
                tmp.unlink()
    return output


def _run_tesseract(source, output, language, force, deskew, rotate=True):
    try:
        import ocrmypdf
        from ocrmypdf.exceptions import ExitCodeException, MissingDependencyError
    except ImportError:
        raise core.PdfManagerError(_tesseract_missing(no_ocrmypdf=True)) from None
    scan.add_programs_to_path()  # Tesseract and Ghostscript where installers put them
    # Turning pages needs Tesseract's orientation data, which not every
    # install has; without it OCR still runs, the pages just stay as they are.
    rotate = rotate and "osd" in _tesseract_languages()
    try:
        # A plain PDF out, not PDF/A: OCRmyPDF makes PDF/A with Ghostscript,
        # which drops form fields and prints a portfolio's attached files in
        # place of its pages. (PDF/A is a tool of its own.)
        ocrmypdf.ocr(str(source), str(output), language=language.split("+"),
                     skip_text=not force, force_ocr=force, deskew=deskew, rotate_pages=rotate,
                     progress_bar=False, output_type="pdf")
    except MissingDependencyError as exc:
        said = " ".join(str(exc).split())
        if re.search(r"(?i)\bgs\b|ghostscript", said):
            said = ("Ghostscript, which isn't installed on this computer: install it (free, "
                    "https://ghostscript.com/releases/gsdnld.html)")
        elif re.search(r"(?i)tesseract", said):
            said = f"the Tesseract program, which wasn't found: install it ({TESSERACT_LINK})"
        raise core.PdfManagerError(
            f"OCR with Tesseract needs {said or 'a program that is missing'}"
            + (", or choose the Built-in engine" if builtin_available() else "")) from None
    except ExitCodeException as exc:
        said = " ".join(str(exc).split()) or (
            f"OCRmyPDF couldn't process the file (exit code {getattr(exc, 'exit_code', '?')}); "
            "it may be damaged, or try the built-in engine")
        raise core.PdfManagerError(f"OCR failed: {said}") from None


def _tesseract_languages():
    """The language data the Tesseract program has (empty when it can't say)."""
    import subprocess

    program = scan.find_tesseract()
    if not program:
        return set()
    try:
        out = subprocess.run([program, "--list-langs"], capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {line.strip() for line in (out.stdout + out.stderr).splitlines()[1:] if line.strip()}


def render_scale(width_pt, height_pt):
    """Pixels per point for recognising a page of this size: 300 dpi, or
    less for a page whose long side would pass MAX_RENDER_SIDE pixels."""
    longest = max(width_pt, height_pt, 1)
    return min(RENDER_DPI / 72, MAX_RENDER_SIDE / longest)


def _run_builtin(source, output, force, deskew, rotate=True, script=None):
    import pypdfium2 as pdfium
    from PIL import Image
    from pypdf import PdfWriter, Transformation

    from pdfmanager import progress

    writer = PdfWriter(clone_from=str(source))
    core._recount(writer)
    rasterised = False
    # The pdfium lock is held only to open the file and render a page: the
    # app's thumbnails and previews wait on it too, and recognising a page
    # takes a second or two.
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(source))
    try:
        total = len(doc)
        for i in range(total):
            progress.report(i, total, "page")
            page = writer.pages[i]
            if not force and page_has_text(page):
                continue
            with core.PDFIUM_LOCK:
                pdfium_page = core.show_whole_page(doc[i])
                scale = render_scale(pdfium_page.get_width(), pdfium_page.get_height())
                image = pdfium_page.render(scale=scale).to_pil()
            try:
                lines = None
                if rotate:
                    first = _read(image, script)
                    turn = page_orientation(image, script) if _reads_wrong_way(first) else 0
                    if turn:
                        # Show the page the right way up, as Acrobat does:
                        # /Rotate turns it clockwise, the picture turned the
                        # other way round.
                        image = image.rotate(turn, expand=True)
                        _set_rotation(page, (page.rotation or 0) + 360 - turn)
                    else:
                        lines = _lines(first)
                if deskew:
                    angle = skew_angle(image)
                    if angle:
                        # Turn the picture straight, and the page with it, so
                        # the words and the page stay in step.
                        image = image.rotate(-angle, resample=Image.BICUBIC, fillcolor="white")
                        _turn_page(page, -angle, Transformation)
                        lines = None
                if lines is None:
                    lines = recognise(image, script)
            except progress.Cancelled:
                raise
            except Exception as exc:  # engine trouble: say which page
                raise core.PdfManagerError(f"OCR failed on page {i + 1}: {exc}") from None
            if force:
                _rasterise(writer, page, image, scale)
                rasterised = True
            words = [w for line in lines for w in line.words]
            if words:
                textlayer.add_text_layer(writer, page, words, image.size, scale * 72)
    finally:
        with core.PDFIUM_LOCK:
            doc.close()
    writer.write(str(output))
    if rasterised:
        _drop_unused_objects(output)


def _drop_unused_objects(path):
    """Rewrite ``path`` without the objects nothing refers to any more:
    pypdf writes out every object it was given, replaced page pictures and
    old fonts included."""
    import pikepdf

    tmp = path.with_name(path.name + ".gc.pdf")
    with pikepdf.open(str(path)) as pdf:
        pdf.save(str(tmp), object_stream_mode=pikepdf.ObjectStreamMode.generate)
    os.replace(tmp, path)


def _set_rotation(page, degrees):
    from pypdf.generic import NameObject, NumberObject

    page[NameObject("/Rotate")] = NumberObject(degrees % 360)


def _turn_page(page, angle, Transformation):
    """Rotate the page's content by ``angle`` degrees (counter-clockwise as
    displayed) about the centre of its crop box. Turning the displayed page
    is the same turn of its content whatever /Rotate says: rotations add up."""
    box = page.cropbox
    cx, cy = (float(box.left) + float(box.right)) / 2, (float(box.bottom) + float(box.top)) / 2
    page.add_transformation(Transformation().translate(-cx, -cy).rotate(angle).translate(cx, cy))


def _rasterise(writer, page, image, scale):
    """Replace the page's content with ``image`` (its render), dropping
    whatever text layer it had, so a wrong one is not kept beside the new.
    The page keeps its boxes, /Rotate and annotations: the picture is drawn
    through the same matrix the text layer uses."""
    import io
    import zlib

    from pypdf import Transformation
    from pypdf.generic import DictionaryObject, NameObject, NumberObject, StreamObject

    grey = image.convert("L")
    histogram = grey.histogram()
    middling = sum(histogram[32:224]) / (grey.width * grey.height)
    xobj = StreamObject()
    if middling < 0.02 and grey.getextrema()[0] < 32:
        # Black and white: a bit per pixel, as the scanner would store it.
        bits = grey.point(lambda v: 255 if v > 127 else 0).convert("1")
        xobj.set_data(zlib.compress(bits.tobytes(), 9))
        xobj[NameObject("/Filter")] = NameObject("/FlateDecode")
        xobj[NameObject("/ColorSpace")] = NameObject("/DeviceGray")
        xobj[NameObject("/BitsPerComponent")] = NumberObject(1)
    else:
        colour = image.convert("RGB") if image.mode not in ("L", "RGB") else image
        if colour.mode == "RGB" and _looks_grey(colour):
            colour = grey
        buf = io.BytesIO()
        colour.save(buf, "JPEG", quality=85, optimize=True)
        xobj.set_data(buf.getvalue())
        xobj[NameObject("/Filter")] = NameObject("/DCTDecode")
        xobj[NameObject("/ColorSpace")] = NameObject("/DeviceGray" if colour.mode == "L" else "/DeviceRGB")
        xobj[NameObject("/BitsPerComponent")] = NumberObject(8)
    xobj[NameObject("/Type")] = NameObject("/XObject")
    xobj[NameObject("/Subtype")] = NameObject("/Image")
    xobj[NameObject("/Width")] = NumberObject(image.width)
    xobj[NameObject("/Height")] = NumberObject(image.height)
    display_w, display_h = textlayer._display_size(page)
    to_page = textlayer.image_to_page_matrix(page, image.size, scale * 72)
    ctm = Transformation().scale(display_w, display_h).transform(Transformation(to_page)).ctm
    content = StreamObject()
    content.set_data(("q " + " ".join(f"{v:.4f}" for v in ctm) + " cm /Im0 Do Q").encode())
    page[NameObject("/Contents")] = writer._add_object(content)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): writer._add_object(xobj)})})


def _looks_grey(image, sample=200):
    """Whether an RGB image is grey all over (then it is stored as grey)."""
    small = image.resize((sample, sample))
    r, g, b = (band.getdata() for band in small.split())
    return all(abs(x - y) < 8 and abs(y - z) < 8 for x, y, z in zip(r, g, b))
