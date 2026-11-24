# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""OCR engine constants, language/script maps, Line dataclass, and engine selection."""

import importlib.util
import re
import sys
from dataclasses import dataclass

from pdfmanager import core, scan
from pdfmanager.ocr._facade import _ocr_attr

ENGINES = ("auto", "builtin", "tesseract")
QUALITIES = ("standard", "best")
MIN_CONFIDENCE = 0.3   # words and lines the model is less sure of than this are dropped
RENDER_DPI = 300
HIGH_RENDER_DPI = 450  # re-render DPI when detected text is small print (< SMALL_TEXT_PX at 300 dpi)
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
    val = _ocr_attr("_builtin_imports", _builtin_imports)
    if val is None:
        try:
            import onnxruntime
            import rapidocr

            val = bool(onnxruntime and rapidocr)
        except Exception:
            val = False
        _builtin_imports = val
        pkg = sys.modules.get("pdfmanager.ocr")
        if pkg is not None:
            pkg._builtin_imports = val
    return val


def ocrmypdf_installed():
    """OCRmyPDF (the Python package that drives Tesseract) can be imported."""
    try:
        return bool(importlib.util.find_spec("ocrmypdf"))
    except (ImportError, ValueError):
        return False


def tesseract_available():
    return _ocr_attr("ocrmypdf_installed", ocrmypdf_installed)() and bool(scan.find_tesseract())


def _codes(language):
    return [code.strip() for code in (language or "").split("+") if code.strip()]


def _tess_code(code):
    return _BCP47_TO_TESS.get(code.lower(), code)


def script_for(language):
    """Which reading model the built-in engine needs for ``language``
    (Tesseract or BCP-47 codes joined with '+'): None for the one it ships with,
    or the name of a script model. ValueError when it has none for a language,
    or the languages need two different script models (a page is read with
    one); English and the other languages of the shipped model mix with any
    script, as each script model reads Latin letters and digits too."""
    scripts = []
    for raw in _codes(language):
        code = _tess_code(raw)
        if code in BUILTIN_LANGUAGES:
            continue
        script = _SCRIPT_OF.get(code)
        if script is None:
            raise ValueError(f"no built-in reading model for {raw!r}")
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
        return next((_SCRIPT_OF[_tess_code(c)] for c in _codes(language) if _tess_code(c) in _SCRIPT_OF), None)


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
        if _ocr_attr("builtin_available", builtin_available)() and (readable or not _ocr_attr("tesseract_available", tesseract_available)()):
            return "builtin"
        if _ocr_attr("tesseract_available", tesseract_available)():
            return "tesseract"
        raise core.PdfManagerError(
            "OCR needs a reading engine, and neither is installed: reinstall ZPDF to get its built-in "
            f"one back ({PIP_OCR}), or install the Tesseract program ({TESSERACT_LINK}) and OCRmyPDF "
            f"({PIP_TESSERACT})")
    if engine == "builtin" and not _ocr_attr("builtin_available", builtin_available)():
        raise core.PdfManagerError(
            f"OCR's built-in engine isn't installed with this copy of ZPDF: reinstall ZPDF ({PIP_OCR})"
            + (", or choose the Tesseract engine" if _ocr_attr("tesseract_available", tesseract_available)() else ""))
    if engine == "tesseract" and not _ocr_attr("tesseract_available", tesseract_available)():
        raise core.PdfManagerError(_tesseract_missing())
    return engine


PIP_OCR = "from Python: pip install pdf-manager[ocr]"
PIP_TESSERACT = "from Python: pip install pdf-manager[tesseract]"


def _tesseract_missing(no_ocrmypdf=False):
    """What OCR with Tesseract lacks here, and what to do (``no_ocrmypdf``:
    it didn't import). Names everything missing, so one install round is
    enough."""
    instead = ", or choose the Built-in engine" if _ocr_attr("builtin_available", builtin_available)() else ""
    no_program = not scan.find_tesseract()
    if no_ocrmypdf or not _ocr_attr("ocrmypdf_installed", ocrmypdf_installed)():
        if no_program:
            return ("OCR with Tesseract needs OCRmyPDF and the Tesseract program, and neither is installed: "
                    f"install OCRmyPDF ({PIP_TESSERACT}) and the Tesseract program ({TESSERACT_LINK})"
                    f"{instead}")
        return ("OCR with Tesseract needs OCRmyPDF, which isn't installed with this copy of ZPDF "
                f"({PIP_TESSERACT}){instead}")
    return ("OCR with Tesseract needs the Tesseract program, which isn't installed on this computer: "
            f"install it ({TESSERACT_LINK}) and try again{instead}")


_BCP47_TO_TESS = {
    "en": "eng", "fr": "fra", "es": "spa", "de": "deu", "it": "ita", "pt": "por",
    "nl": "nld", "pl": "pol", "tr": "tur", "vi": "vie", "zh": "chi_sim", "zh-hans": "chi_sim",
    "zh-hant": "chi_tra", "ja": "jpn", "ko": "kor", "ru": "rus", "uk": "ukr", "ar": "ara",
    "fa": "fas", "ur": "urd", "hi": "hin", "th": "tha", "el": "ell", "ta": "tam", "te": "tel",
}
_TESS_TO_BCP47 = {v: k for k, v in _BCP47_TO_TESS.items()}
