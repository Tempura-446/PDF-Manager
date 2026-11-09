"""Making scanned pages searchable: two engines.

The built-in engine runs PaddleOCR's PP-OCR models (through the rapidocr
package, on ONNX Runtime). It reads degraded scans better than Tesseract,
reads English, most European languages, Chinese and Japanese with one model
and no language setting, and needs nothing installed beside ZPDF. Its text
layer is ZPDF's own (textlayer.py).

Tesseract, through OCRmyPDF, stays as the second engine: for other scripts
through its language packs, and for PDF/A output straight from OCR.
"""
import importlib.util
import logging
import math
import statistics
import threading
from dataclasses import dataclass

from pdfmanager import core, scan
from pdfmanager.textlayer import Word

__all__ = ["Word", "Line", "ENGINES", "choose_engine", "recognise", "reading_order", "text_of", "skew_angle"]

ENGINES = ("auto", "builtin", "tesseract")
MIN_CONFIDENCE = 0.3   # words and lines the model is less sure of than this are dropped
RENDER_DPI = 300
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


def builtin_available():
    return all(importlib.util.find_spec(name) for name in ("rapidocr", "onnxruntime"))


def tesseract_available():
    return bool(importlib.util.find_spec("ocrmypdf")) and bool(scan.find_tesseract())


def choose_engine(engine):
    """'builtin' or 'tesseract' for ``engine`` (one of ENGINES), or a
    PdfManagerError saying what to install."""
    if engine not in ENGINES:
        raise core.PdfManagerError(f"unknown OCR engine {engine!r}; choose auto, builtin or tesseract")
    if engine == "auto":
        if builtin_available():
            return "builtin"
        if tesseract_available():
            return "tesseract"
        raise core.PdfManagerError(
            "OCR needs an engine: pip install pdf-manager[ocr] for the built-in one, "
            f"or OCRmyPDF and Tesseract ({TESSERACT_LINK})")
    if engine == "builtin" and not builtin_available():
        raise core.PdfManagerError("OCR's built-in engine isn't installed: pip install pdf-manager[ocr]")
    if engine == "tesseract" and not tesseract_available():
        raise core.PdfManagerError(
            "OCR with Tesseract needs OCRmyPDF and the Tesseract program: pip install "
            f"pdf-manager[tesseract], then install Tesseract ({TESSERACT_LINK})")
    return engine


# -- the built-in engine -------------------------------------------------------

_engine_lock = threading.Lock()
_engine_instance = None


def _engine():
    """The one RapidOCR instance (its models take about a second to load)."""
    global _engine_instance
    with _engine_lock:
        if _engine_instance is None:
            from rapidocr import RapidOCR

            _engine_instance = RapidOCR(params={"Global.return_word_box": True})
            # It announces every model it loads; only trouble is worth hearing.
            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        return _engine_instance


def reset():
    """Forget the loaded engine (tests)."""
    global _engine_instance
    with _engine_lock:
        _engine_instance = None


def _array(image):
    import numpy

    return numpy.asarray(image.convert("RGB"))


def _quad(points):
    return [(float(x), float(y)) for x, y in points]


def recognise(image):
    """The lines of text in a PIL image, each with its words."""
    result = _engine()(_array(image))
    if not result.txts:
        return []
    lines = []
    word_results = getattr(result, "word_results", None) or [None] * len(result.txts)
    for text, box, score, found in zip(result.txts, result.boxes, result.scores, word_results):
        if score < MIN_CONFIDENCE or not text.strip():
            continue
        words = [Word(w_text, _quad(w_box), float(w_score))
                 for w_text, w_score, w_box in (found or ())
                 if w_text.strip() and w_score >= MIN_CONFIDENCE]
        if not found:
            words = [Word(text, _quad(box), float(score))]
        if not words:
            continue
        lines.append(Line(text, _quad(box), float(score), words))
    return reading_order(lines)


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


def reading_order(lines):
    """``lines`` in reading order: rows top to bottom, left to right within a row."""
    return [line for row in _rows(lines) for line in row]


def text_of(lines):
    """The recognised text as a reader gets it: words separated by spaces,
    one line per row of the page."""
    return "\n".join(" ".join(w.text for line in row for w in line.words) for row in _rows(lines))


def skew_angle(image):
    """How far the text in ``image`` tilts, in degrees (positive: it rises
    to the right); 0.0 when it is straight or there is too little text to
    tell. Only the text detector runs, so this is quick."""
    boxes = _engine()(_array(image), use_det=True, use_cls=False, use_rec=False).boxes
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
