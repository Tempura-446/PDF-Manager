# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
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
import ctypes
import hashlib
import importlib.util
import logging
import math
import os
import re
import statistics
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from pdfmanager import core, scan, textlayer
from pdfmanager.textlayer import Word

__all__ = ["Word", "Line", "ENGINES", "QUALITIES", "default_quality", "default_gpu", "default_workers",
           "choose_engine", "script_for", "recognise", "reading_order", "text_of", "skew_angle",
           "page_orientation", "format_report"]

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
    if _builtin_imports is None:
        try:
            import onnxruntime
            import rapidocr

            _builtin_imports = bool(onnxruntime and rapidocr)
        except Exception:
            _builtin_imports = False
    return _builtin_imports


def ocrmypdf_installed():
    """OCRmyPDF (the Python package that drives Tesseract) can be imported."""
    try:
        return bool(importlib.util.find_spec("ocrmypdf"))
    except (ImportError, ValueError):
        return False


def tesseract_available():
    return ocrmypdf_installed() and bool(scan.find_tesseract())


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
    it didn't import). Names everything missing, so one install round is
    enough."""
    instead = ", or choose the Built-in engine" if builtin_available() else ""
    no_program = not scan.find_tesseract()
    if no_ocrmypdf or not ocrmypdf_installed():
        if no_program:
            return ("OCR with Tesseract needs OCRmyPDF and the Tesseract program, and neither is installed: "
                    f"install OCRmyPDF ({PIP_TESSERACT}) and the Tesseract program ({TESSERACT_LINK})"
                    f"{instead}")
        return ("OCR with Tesseract needs OCRmyPDF, which isn't installed with this copy of ZPDF "
                f"({PIP_TESSERACT}){instead}")
    return ("OCR with Tesseract needs the Tesseract program, which isn't installed on this computer: "
            f"install it ({TESSERACT_LINK}) and try again{instead}")


# -- the built-in engine -------------------------------------------------------

_engine_lock = threading.Lock()   # building the instances
_call_lock = threading.Lock()     # using them: each keeps the last call's settings
_engines = {}                     # (script, quality, gpu) -> RapidOCR
_directml_failed = False


def default_quality():
    """The OCR quality saved in Settings ('standard' or 'best')."""
    from pdfmanager import update

    chosen = update.settings().get("ocr_quality")
    return chosen if chosen in QUALITIES else "standard"


def default_gpu():
    """Whether Windows DirectML GPU acceleration for OCR is turned on in Settings
    (off by default)."""
    from pdfmanager import update

    return bool(update.settings().get("ocr_gpu", False))


def _directml_available():
    """Whether ONNX Runtime's DirectML provider can be tried on this machine."""
    if sys.platform != "win32" or _directml_failed:
        return False
    try:
        import onnxruntime

        return "DmlExecutionProvider" in (onnxruntime.get_available_providers() or ())
    except Exception:
        return False


def _check_quality(quality):
    if quality not in QUALITIES:
        raise core.PdfManagerError(f"unknown OCR quality {quality!r}; choose standard or best")
    return quality


def models_dir():
    """Where fetched reading models are kept: ZPDF's own folder for this user."""
    from pdfmanager import appdata

    return appdata.data_dir() / "ocr-models"


def _model_entry(key, label):
    """(address, SHA-256) of a PP-OCRv5 ONNX model from rapidocr's own list."""
    import rapidocr
    import yaml

    listing = yaml.safe_load((Path(rapidocr.__file__).parent / "default_models.yaml").read_text(encoding="utf-8"))
    stack = [listing.get("onnxruntime", {}).get("PP-OCRv5", {})]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            entry = node.get(key)
            if isinstance(entry, dict) and entry.get("model_dir") and entry.get("SHA256"):
                return entry["model_dir"], entry["SHA256"].lower()
            stack.extend(node.values())
    raise core.PdfManagerError(f"this rapidocr has no {label} model")


def _model_source(script):
    """(address, SHA-256) of a script's reading model, from rapidocr's own
    list of models: its ONNX build (the list also has the same models built
    for other engines, which ONNX Runtime can't load)."""
    return _model_entry(f"{script}_PP-OCRv5_rec_mobile", f"{SCRIPT_NAMES.get(script, script)} reading")


def _best_model_source(kind):
    """(address, SHA-256) of the larger ('best accuracy') detector or reader."""
    key = "ch_PP-OCRv5_det_server" if kind == "det" else "ch_PP-OCRv5_rec_server"
    label = "best-accuracy detection" if kind == "det" else "best-accuracy reading"
    return _model_entry(key, label)


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


def _download_model(label, url, digest, target, size_note="about 10 MB", fallback_hint="choose the Tesseract engine"):
    """Fetch ``url`` into ``target`` once and verify its SHA-256."""
    from pdfmanager import progress

    if target.exists() and _sha256(target) == digest:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    progress.say(f"getting the {label} (once, {size_note})")
    try:
        _fetch(url, part)
    except Exception as exc:
        part.unlink(missing_ok=True)
        raise core.PdfManagerError(
            f"couldn't get the {label} ({exc}); connect to the internet once so "
            f"ZPDF can fetch it, or {fallback_hint}") from None
    if _sha256(part) != digest:
        part.unlink(missing_ok=True)
        raise core.PdfManagerError(
            f"the {label} came down damaged; try again, or {fallback_hint}")
    os.replace(part, target)
    return target


def _language_model(script):
    """The path of a script's reading model, fetched and checked on first use."""
    name = SCRIPT_NAMES.get(script, script)
    url, digest = _model_source(script)
    return _download_model(f"{name} reading model", url, digest, models_dir() / f"{script}.onnx")


def _best_model(kind):
    """The path of the larger ('best accuracy') detector ('det') or reader ('rec'),
    fetched once and SHA-256 checked into models_dir()."""
    url, digest = _best_model_source(kind)
    label = "best-accuracy detection model" if kind == "det" else "best-accuracy reading model"
    return _download_model(label, url, digest, models_dir() / f"best_{kind}.onnx",
                           size_note="about 80 MB", fallback_hint="choose Standard accuracy")


def _engine(script=None, quality="standard", use_gpu=None):
    """The RapidOCR instance for a script and quality ('standard' or 'best').
    Models take about a second to load, so each is made once. When DirectML
    GPU acceleration is turned on in Settings (Windows), tries it and falls
    back to the CPU at the first error."""
    global _directml_failed
    quality = _check_quality(quality)
    gpu = (default_gpu() if use_gpu is None else bool(use_gpu)) and _directml_available()
    key = (script, quality, gpu)
    with _engine_lock:
        if key not in _engines:
            params = {"Global.return_word_box": True}
            if gpu:
                params["EngineConfig.onnxruntime.use_dml"] = True
            if quality == "best":
                from rapidocr import ModelType, OCRVersion

                params.update({
                    "Det.ocr_version": OCRVersion.PPOCRV5,
                    "Det.model_type": ModelType.SERVER,
                    "Det.model_path": str(_best_model("det")),
                })
                if script is None:
                    params.update({
                        "Rec.ocr_version": OCRVersion.PPOCRV5,
                        "Rec.model_type": ModelType.SERVER,
                        "Rec.model_path": str(_best_model("rec")),
                    })
            if script is not None:
                from rapidocr import ModelType, OCRVersion

                params.update({"Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.lang_type": script,
                               "Rec.model_type": ModelType.MOBILE,
                               "Rec.model_path": str(_language_model(script))})
            try:
                from rapidocr import RapidOCR

                _engines[key] = RapidOCR(params=params)
            except Exception as exc:  # a broken install, or model files missing from a build
                if gpu:
                    logging.getLogger(__name__).warning("DirectML OCR engine failed (%s); falling back to CPU", exc)
                    _directml_failed = True
                    params.pop("EngineConfig.onnxruntime.use_dml", None)
                    key = (script, quality, False)
                    try:
                        from rapidocr import RapidOCR

                        _engines[key] = RapidOCR(params=params)
                    except Exception as cpu_exc:
                        raise core.PdfManagerError(
                            f"OCR's built-in engine could not start; reinstall ZPDF ({PIP_OCR}) or choose the "
                            f"Tesseract engine ({cpu_exc})") from None
                else:
                    raise core.PdfManagerError(
                        f"OCR's built-in engine could not start; reinstall ZPDF ({PIP_OCR}) or choose the "
                        f"Tesseract engine ({exc})") from None
            # It announces every model it loads; only trouble is worth hearing.
            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        return _engines[key]


def reset():
    """Forget the loaded engines and what was learnt about the install (tests)."""
    global _builtin_imports, _directml_failed
    with _engine_lock:
        _engines.clear()
        _builtin_imports = None
        _directml_failed = False


def _array(image):
    import numpy

    return numpy.asarray(image.convert("RGB"))


GREY_PAPER = 190          # paper darker than this (of 255) is stretched to full contrast
FAINT_CONTRAST = 75       # paper-to-ink range below this (when ink exists) is faint print
UNEVEN_LIGHT = 35         # background 95th - 10th percentile above this triggers background flattening
SPECKLE_RATIO = 0.004     # share of isolated single-pixel spikes that triggers light denoising
MIN_DENOISE_STROKE = 2.2  # estimated stroke width (px) below which median denoising is skipped
SMALL_TEXT_PX = 20.0      # median line height (px) below which higher-resolution reading is tried
RETRY_CONFIDENCE = 0.78   # lines below this get a second reading from a cleaned/enlarged crop
MAX_LINE_RETRIES = 25     # cap per-page line retries so pure noise cannot stall


def _measure_image(image):
    """Measure paper brightness, ink contrast, lighting evenness, speckle and
    approximate stroke width on a fast copy of ``image``."""
    import numpy as np
    from PIL import ImageFilter

    grey = image.convert("L")
    w, h = grey.size
    if w == 0 or h == 0:
        return {"paper": 255.0, "contrast": 0.0, "uneven": 0.0, "speckle": 0.0, "stroke_width": 0.0}

    # Unblurred strided sample preserves true ink darkness (thumbnailing blurs thin strokes to grey).
    step = max(1, max(w, h) // 1000)
    full_arr = np.asarray(grey, np.float32)
    sample = full_arr[::step, ::step]
    paper = float(np.median(sample))
    p_ink = float(np.percentile(sample, 0.5))
    contrast = max(0.0, paper - p_ink)

    small = grey.copy()
    small.thumbnail((400, 400))
    bg_im = small.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(5))
    bg = np.asarray(bg_im, np.float32)
    uneven = float(np.percentile(bg, 95) - np.percentile(bg, 10))

    # Isolated 1-px speckle on a 1:1 centre patch: differs sharply from all 4 neighbours
    # (continuous text strokes always share a neighbour along the stroke, so they score 0).
    pw, ph = min(w, 1000), min(h, 1000)
    x0, y0 = (w - pw) // 2, (h - ph) // 2
    patch = full_arr[y0:y0 + ph, x0:x0 + pw]
    if patch.shape[0] >= 3 and patch.shape[1] >= 3:
        c = patch[1:-1, 1:-1]
        min_diff = np.minimum(
            np.minimum(np.abs(c - patch[:-2, 1:-1]), np.abs(c - patch[2:, 1:-1])),
            np.minimum(np.abs(c - patch[1:-1, :-2]), np.abs(c - patch[1:-1, 2:])),
        )
        speckle = float(np.mean(min_diff > 28.0))
    else:
        speckle = 0.0

    # Approximate stroke width after a 3x3 median so 1-2 px random noise clusters
    # vanish and only real print strokes are measured (thin 1-px strokes also vanish,
    # keeping stroke_width < MIN_DENOISE_STROKE to protect small/thin print).
    ink_thresh = paper - max(18.0, contrast * 0.35)
    stroke_width = 0.0
    for px0, py0 in ((x0, y0), (min(max(0, w - pw), w // 10), min(max(0, h - ph), h // 10))):
        sub = grey.crop((px0, py0, px0 + pw, py0 + ph)).filter(ImageFilter.MedianFilter(3))
        ink = np.asarray(sub, np.float32) < ink_thresh
        ink_count = int(ink.sum())
        if ink_count >= 20:
            cpad = np.pad(ink, 1, mode="constant", constant_values=False)
            interior = cpad[1:-1, 1:-1] & cpad[:-2, 1:-1] & cpad[2:, 1:-1] & cpad[1:-1, :-2] & cpad[1:-1, 2:]
            boundary = int((ink & ~interior).sum())
            stroke_width = 2.0 * ink_count / max(boundary, 1)
            break
    return {"paper": paper, "contrast": contrast, "uneven": uneven,
            "speckle": speckle, "stroke_width": float(stroke_width)}


def _flatten_background(image):
    """Even out shadows or uneven lighting for OCR without binarising."""
    import numpy as np
    from PIL import Image

    from pdfmanager import pagephoto

    rgb = image.convert("RGB")
    grey = rgb.convert("L")
    bg = np.maximum(pagephoto._background(grey), 24.0)
    arr = np.asarray(rgb, np.float32)
    norm = np.clip((arr / bg[..., None]) * 245.0, 0, 255).astype(np.uint8)
    return Image.fromarray(norm, "RGB")


def _clean_for_reading(image):
    """Clean ``image`` for OCR only (the PDF keeps its original picture),
    applying only the steps the page's measurements call for. Returns
    ``(cleaned_image, steps)`` where ``steps`` names what was applied."""
    from PIL import ImageFilter, ImageOps

    stats = _measure_image(image)
    out = image
    steps = []
    if stats["uneven"] >= UNEVEN_LIGHT and stats["paper"] < 245.0:
        out = _flatten_background(out)
        steps.append("background")
        # Re-check contrast after evening out the background.
        after = _measure_image(out)
        if after["paper"] < GREY_PAPER or (5.0 <= after["contrast"] < FAINT_CONTRAST):
            out = ImageOps.autocontrast(out.convert("RGB"), cutoff=1)
            steps.append("contrast")
    elif stats["paper"] < GREY_PAPER or (5.0 <= stats["contrast"] < FAINT_CONTRAST):
        out = ImageOps.autocontrast(out.convert("RGB"), cutoff=1)
        steps.append("contrast")

    if stats["speckle"] >= SPECKLE_RATIO and stats["stroke_width"] >= MIN_DENOISE_STROKE:
        out = out.filter(ImageFilter.MedianFilter(3))
        steps.append("denoise")
    return out, steps


def _clear(image):
    """``image`` cleaned for reading when its paper is dark, uneven, faint
    or speckled; returned untouched when it is already clean."""
    return _clean_for_reading(image)[0]


def _quad(points):
    return [(float(x), float(y)) for x, y in points]


def _map_quad(quad, matrix):
    """Project a 4-point quad through a 3x3 homography ``matrix``."""
    out = []
    for x, y in quad:
        w = matrix[2, 0] * x + matrix[2, 1] * y + matrix[2, 2]
        if abs(w) < 1e-9:
            w = 1.0
        px = (matrix[0, 0] * x + matrix[0, 1] * y + matrix[0, 2]) / w
        py = (matrix[1, 0] * x + matrix[1, 1] * y + matrix[1, 2]) / w
        out.append((float(px), float(py)))
    return out


def _straighten_photo(image):
    """If ``image`` is a phone photo of a page on a darker surface with
    noticeable perspective, return ``(flattened_image, to_photo_matrix)``;
    otherwise ``(None, None)``. Used for reading only: word boxes are mapped
    back to the original photo via ``to_photo_matrix``."""
    import numpy as np
    from PIL import Image

    from pdfmanager import pagephoto

    if min(image.size) < 300:
        return None, None
    found = pagephoto.find_page(image)
    if not found.found or found.confidence < 0.5 or not found.corners:
        return None, None
    w, h = image.size
    corners = [np.asarray(c, float) for c in found.corners]
    # Skip if the detected corners are already essentially the full image rectangle.
    img_corners = [np.array([0.0, 0.0]), np.array([float(w), 0.0]),
                   np.array([float(w), float(h)]), np.array([0.0, float(h)])]
    max_shift = max(float(np.linalg.norm(c - ic)) for c, ic in zip(corners, img_corners))
    if max_shift < 0.02 * max(w, h):
        return None, None
    ratio = pagephoto.snap_shape(pagephoto.page_shape(found.corners, image.size))
    height = max(np.linalg.norm(corners[3] - corners[0]), np.linalg.norm(corners[2] - corners[1]))
    width = height * ratio
    longest = max(width, height, 1.0)
    if longest > max(w, h):
        width, height = width * max(w, h) / longest, height * max(w, h) / longest
    fw, fh = max(1, round(width)), max(1, round(height))
    target = [(0.0, 0.0), (float(fw), 0.0), (float(fw), float(fh)), (0.0, float(fh))]
    to_photo = pagephoto._homography(target, corners)
    coeffs = (to_photo / to_photo[2, 2]).ravel()[:8]
    mode = image.mode if image.mode in ("RGB", "L") else "RGB"
    flat = image.convert(mode).transform((fw, fh), Image.PERSPECTIVE, tuple(coeffs), Image.BICUBIC)
    return flat, to_photo


def _dewarp_line_crop(crop):
    """Mildly flatten a curved baseline in a single-line crop by fitting a
    quadratic y(x) to dark-ink column centroids and shifting columns vertically.
    Returns ``crop`` untouched when the line is already straight."""
    import numpy as np
    from PIL import Image

    grey = np.asarray(crop.convert("L"), np.float32)
    h, w = grey.shape
    if w < 60 or h < 12:
        return crop
    paper = float(np.percentile(grey, 85))
    thresh = paper - max(20.0, (paper - float(np.percentile(grey, 5))) * 0.35)
    ink = grey < thresh
    ys = np.arange(h, dtype=np.float32)
    xs_fit, ys_fit = [], []
    step = max(1, w // 32)
    for x in range(0, w, step):
        col = ink[:, x:min(w, x + step)]
        if col.sum() >= 2:
            weights = col.sum(axis=1).astype(np.float32)
            xs_fit.append(x + step / 2.0)
            ys_fit.append(float((ys * weights).sum() / weights.sum()))
    if len(xs_fit) < 6:
        return crop
    xs_arr = np.asarray(xs_fit, np.float64)
    ys_arr = np.asarray(ys_fit, np.float64)
    coeffs = np.polyfit(xs_arr, ys_arr, 2)
    col_x = np.arange(w, dtype=np.float64)
    curve = np.polyval(coeffs, col_x)
    # Remove linear tilt component so we only measure curvature sag, then shift relative to median.
    sag = float(curve.max() - curve.min())
    if sag < 2.5 or sag > 0.35 * h:
        return crop
    shifts = np.round(curve - np.median(curve)).astype(int)
    rgb = np.asarray(crop.convert("RGB"))
    fill = int(round(paper))
    out = np.full_like(rgb, fill)
    for x in range(w):
        dy = int(shifts[x])
        if dy == 0:
            out[:, x] = rgb[:, x]
        elif dy > 0 and dy < h:
            out[:h - dy, x] = rgb[dy:, x]
        elif dy < 0 and -dy < h:
            out[-dy:, x] = rgb[:h + dy, x]
    return Image.fromarray(out, "RGB")


def _lines_score(lines):
    """Quality score for comparing two readings of the same page: confident characters."""
    return sum(w.confidence * len(w.text) for line in lines for w in line.words)


def _median_text_height(lines):
    """Median height (px) of recognised line boxes; 0.0 when empty."""
    heights = [_box_size(line.quad)[1] for line in lines]
    return float(statistics.median(heights)) if heights else 0.0


def _scale_lines(lines, factor):
    """Scale every line and word quad by ``factor``."""
    out = []
    for line in lines:
        words = [Word(w.text, [(x * factor, y * factor) for x, y in w.quad], w.confidence) for w in line.words]
        out.append(Line(line.text, [(x * factor, y * factor) for x, y in line.quad], line.confidence, words))
    return out


def _map_lines(lines, matrix):
    """Project every line and word quad through a 3x3 homography ``matrix``."""
    out = []
    for line in lines:
        words = [Word(w.text, _map_quad(w.quad, matrix), w.confidence) for w in line.words]
        out.append(Line(line.text, _map_quad(line.quad, matrix), line.confidence, words))
    return out


def _retry_low_confidence(image, lines, script=None, quality="standard"):
    """Re-read low-confidence lines from a cleaned/enlarged crop and keep
    whichever reading has higher confidence (never both)."""
    from PIL import Image, ImageOps

    if not lines:
        return lines
    w_img, h_img = image.size
    engine = _engine(script, quality)
    updated = []
    retried = 0
    for line in lines:
        if line.confidence >= RETRY_CONFIDENCE or retried >= MAX_LINE_RETRIES:
            updated.append(line)
            continue
        retried += 1
        x0, y0, x1, y1 = _bbox(line)
        lh = max(y1 - y0, 1.0)
        pad_x = max(8, int(round(lh * 0.35)))
        pad_y = max(6, int(round(lh * 0.35)))
        left = max(0, int(math.floor(x0)) - pad_x)
        top = max(0, int(math.floor(y0)) - pad_y)
        right = min(w_img, int(math.ceil(x1)) + pad_x)
        bottom = min(h_img, int(math.ceil(y1)) + pad_y)
        if right - left < 8 or bottom - top < 6:
            updated.append(line)
            continue
        crop = image.crop((left, top, right, bottom))
        crop = ImageOps.autocontrast(crop.convert("RGB"), cutoff=1)
        crop = _dewarp_line_crop(crop)
        up = 2.0 if lh < 36.0 and max(crop.size) * 2 <= MAX_RENDER_SIDE else 1.0
        if up > 1.0:
            crop = crop.resize((max(1, round(crop.width * up)), max(1, round(crop.height * up))), Image.BICUBIC)
        with _call_lock:
            res = engine(_array(crop), use_det=True, use_cls=False, use_rec=True)
        cand_lines = _lines(res)
        if not cand_lines:
            updated.append(line)
            continue
        # Combine candidate lines on this single-line crop into one replacement if strictly more confident.
        cand_words = []
        for cl in cand_lines:
            for cw in cl.words:
                mapped_quad = [(left + x / up, top + y / up) for x, y in cw.quad]
                cand_words.append(Word(cw.text, mapped_quad, cw.confidence))
        if not cand_words:
            updated.append(line)
            continue
        cand_conf = sum(cw.confidence * len(cw.text) for cw in cand_words) / max(sum(len(cw.text) for cw in cand_words), 1)
        if cand_conf > line.confidence:
            cand_text = " ".join(cw.text for cw in cand_words)
            cand_quad = _union([cw.quad for cw in cand_words]) if len(cand_words) > 1 else cand_words[0].quad
            updated.append(Line(cand_text, cand_quad, float(cand_conf), cand_words))
        else:
            updated.append(line)
    return reading_order(updated)


def _with_quality(fn, image, script, quality):
    """Call ``fn(image, script)``, passing ``quality`` only when non-standard
    so 2-argument fakes in tests stay compatible."""
    return fn(image, script) if quality == "standard" else fn(image, script, quality=quality)


def _read(image, script=None, quality="standard"):
    """The model's raw result for an image. Every switch is named each
    time: an engine keeps the last call's settings, and skew_angle() runs
    the detector alone. The per-line flip check stays off: on short crops it
    guesses wrong (a lone "6" became "9", 7 pt print turned to nonsense), and
    whole pages are turned upright by page_orientation() instead."""
    global _directml_failed
    arr = _array(_clear(image))
    used_gpu = default_gpu() and _directml_available()
    engine = _engine(script, quality)
    try:
        with _call_lock:
            return engine(arr, use_det=True, use_cls=False, use_rec=True)
    except Exception as exc:
        if not used_gpu:
            raise
        logging.getLogger(__name__).warning("DirectML OCR call failed (%s); falling back to CPU", exc)
        with _engine_lock:
            _directml_failed = True
            for k in [k for k in _engines if k[2]]:
                _engines.pop(k, None)
        engine = _engine(script, quality, use_gpu=False)
        with _call_lock:
            return engine(arr, use_det=True, use_cls=False, use_rec=True)


def recognise(image, script=None, quality="standard"):
    """The lines of text in a PIL image, each with its words, in reading
    order. ``script``: see script_for(); ``quality``: 'standard' or 'best'."""
    from PIL import Image

    lines = _lines(_with_quality(_read, image, script, quality))
    # Phone photos: try reading the perspective-flattened page and map boxes back.
    flat, to_photo = _straighten_photo(image)
    if flat is not None:
        flat_lines = _lines(_with_quality(_read, flat, script, quality))
        if _lines_score(flat_lines) > _lines_score(lines):
            lines = reading_order(_map_lines(flat_lines, to_photo))
    # Two readings, the better kept: re-read low-confidence lines from cleaned/enlarged crops.
    lines = _retry_low_confidence(image, lines, script, quality)
    # Small print on a standalone image: upscale 2x when median text height is tiny.
    med_h = _median_text_height(lines)
    if lines and 0 < med_h < SMALL_TEXT_PX and max(image.size) * 2 <= MAX_RENDER_SIDE:
        if any(line.confidence < 0.92 for line in lines):
            enlarged = image.resize((image.width * 2, image.height * 2), Image.BICUBIC)
            big_lines = _scale_lines(_lines(_with_quality(_read, enlarged, script, quality)), 0.5)
            if _lines_score(big_lines) > _lines_score(lines):
                lines = reading_order(big_lines)
    return lines


_ORIG_READ = _read
_ORIG_RECOGNISE = recognise


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


def page_orientation(image, script=None, quality="standard"):
    """How far to turn ``image`` (degrees counter-clockwise: 0, 90, 180 or
    270) so that its text reads upright; 0 when it already does, or when
    there is too little text to tell. The shape of the text boxes says
    whether the lines run across or down the page, and reading both ways
    round says which way up."""
    small = image.copy()
    small.thumbnail((ORIENTATION_SIDE, ORIENTATION_SIDE))
    engine = _engine(script, quality)
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


def run(path, output, language="eng", force=False, deskew=False, password=None, engine="auto",
        rotate=True, quality=None, report=None, workers=None):
    """Add a searchable text layer to the scanned pages of ``path``; see
    ``core.ocr``."""
    from pdfmanager import progress

    chosen = choose_engine(engine, language)
    quality = _check_quality(default_quality() if quality is None else quality)
    script = _best_script(language) if chosen == "builtin" else None
    if script is not None:
        try:
            _language_model(script)  # fetched now, before the file is touched
        except core.PdfManagerError:
            if engine != "auto" or not tesseract_available():
                raise
            progress.say(f"the {SCRIPT_NAMES[script]} reading model can't be fetched; using Tesseract")
            chosen, script = "tesseract", None
    if chosen == "builtin" and quality == "best":
        _best_model("det")
        if script is None:
            _best_model("rec")
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
            _run_builtin(source, tmp_out, force, deskew, rotate, script, quality=quality,
                         report=report, workers=workers)
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


def render_scale(width_pt, height_pt, dpi=RENDER_DPI):
    """Pixels per point for recognising a page of this size: ``dpi`` (300 by
    default), or less for a page whose long side would pass MAX_RENDER_SIDE
    pixels."""
    longest = max(width_pt, height_pt, 1)
    return min(dpi / 72, MAX_RENDER_SIDE / longest)


PARALLEL_MIN_PAGES = 3  # spin up worker processes only when at least this many unique scan pages need OCR
MAX_WORKERS = 4         # cap parallel OCR workers (each loads its own ONNX Runtime session)


def _available_memory_mb():
    """Approximate free/available RAM in MB, or None if unknown."""
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) // 1024
        except OSError:
            return None
    if sys.platform == "win32":
        try:
            class _MemStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            st = _MemStatus()
            st.dwLength = ctypes.sizeof(st)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return int(st.ullAvailPhys // (1024 * 1024))
        except Exception:
            return None
    return None


def default_workers(page_count):
    """Number of worker processes for parallel OCR, sized to CPU cores and
    available memory (1 when fewer than PARALLEL_MIN_PAGES need reading)."""
    if page_count < PARALLEL_MIN_PAGES:
        return 1
    cores = os.cpu_count() or 1
    n = min(MAX_WORKERS, max(1, cores // 2 if cores > 2 else cores))
    avail_mb = _available_memory_mb()
    if avail_mb is not None:
        n = min(n, max(1, avail_mb // 600))
    return max(1, min(n, page_count))


def _is_blank_image(image):
    """Whether a rendered page image is blank (uniform colour or faint
    scanner-bed noise with no visible ink or graphics worth running OCR on)."""
    import numpy as np

    grey = image.convert("L")
    w, h = grey.size
    if w == 0 or h == 0:
        return True
    arr = np.asarray(grey, dtype=np.int16)
    if w > 12 and h > 12:
        arr = arr[2:-2, 2:-2]
    if int(arr.max()) - int(arr.min()) < 18:
        return True
    med = int(np.median(arr[::4, ::4]))
    ink = int(np.count_nonzero(np.abs(arr - med) >= 24))
    return ink < 12


def _image_hash(image):
    """Fast content digest of a rendered page image for duplicate-page skipping."""
    h = hashlib.blake2b(digest_size=16)
    h.update(f"{image.mode}:{image.width}x{image.height}:".encode("ascii"))
    h.update(image.tobytes())
    return h.hexdigest()


def _recognise_scan_page(image, doc, page_idx, page_w, page_h, scale, rotate, deskew, script, quality):
    """Recognise a single scanned page image; returns ``(lines, turn, angle, image_size)``."""
    from PIL import Image

    lines = None
    turn = 0
    angle = 0.0
    if rotate:
        first = _with_quality(_read, image, script, quality)
        turn = _with_quality(page_orientation, image, script, quality) if _reads_wrong_way(first) else 0
        if turn:
            image = image.rotate(turn, expand=True)
        else:
            lines = _refine_lines(image, _lines(first), script, quality)
    if deskew:
        angle = skew_angle(image)
        if angle:
            image = image.rotate(-angle, resample=Image.BICUBIC, fillcolor="white")
            lines = None
    if lines is None:
        lines = _with_quality(recognise, image, script, quality)
    # Small print: re-render at higher DPI when median text height is under SMALL_TEXT_PX.
    if lines and 0 < _median_text_height(lines) < SMALL_TEXT_PX and doc is not None:
        high_scale = render_scale(page_w, page_h, dpi=HIGH_RENDER_DPI)
        if high_scale > scale * 1.15:
            with core.PDFIUM_LOCK:
                pdfium_page = core.show_whole_page(doc[page_idx])
                high_image = pdfium_page.render(scale=high_scale).to_pil()
            if turn:
                high_image = high_image.rotate(turn, expand=True)
            if angle:
                high_image = high_image.rotate(-angle, resample=Image.BICUBIC, fillcolor="white")
            high_lines = _scale_lines(_lines(_with_quality(_read, high_image, script, quality)),
                                      scale / high_scale)
            high_lines = _retry_low_confidence(image, high_lines, script, quality)
            if _lines_score(high_lines) > _lines_score(lines):
                lines = reading_order(high_lines)
    return lines, turn, angle, image.size


def _recognise_scan_page_worker(task):
    """Top-level picklable worker for ProcessPoolExecutor: opens its own pdfium
    handle and ONNX engine, renders ``page_idx``, and returns
    ``(page_idx, lines, turn, angle, image_size)``."""
    import pypdfium2 as pdfium

    source_str, page_idx, rotate, deskew, script, quality = task
    doc = pdfium.PdfDocument(source_str)
    try:
        pdfium_page = core.show_whole_page(doc[page_idx])
        page_w, page_h = pdfium_page.get_width(), pdfium_page.get_height()
        scale = render_scale(page_w, page_h)
        image = pdfium_page.render(scale=scale).to_pil()
        lines, turn, angle, img_size = _recognise_scan_page(
            image, doc, page_idx, page_w, page_h, scale, rotate, deskew, script, quality)
    finally:
        doc.close()
    return page_idx, lines, turn, angle, img_size


def _run_builtin(source, output, force, deskew, rotate=True, script=None, notes=None, quality="standard",
                 report=None, workers=None, _page_worker=None):
    """``notes`` (a list) is given what was done beyond reading plain scans:
    a rubbish layer replaced, pictures read on a page of real text.
    ``report`` (a dict, if passed) is filled with per-page OCR stats and
    unsure-word counts."""
    import pypdfium2 as pdfium
    from pypdf import PdfReader

    from pdfmanager import progress

    if report is not None:
        report.setdefault("pages_read", [])
        report.setdefault("skipped", {})
        report.setdefault("replaced", [])
        report.setdefault("pictures", {})
        report.setdefault("unsure_pages", {})
        report.setdefault("flagged_pages", [])

    def note(text):
        if notes is not None:
            notes.append(text)
        progress.say(text)

    # Pages whose text is a layer nobody can read (invisible, and rubbish):
    # the layer is taken out first, and the page is then read as the scan it is.
    stripped = None
    if not force:
        stale = []
        reader = PdfReader(str(source))
        with core.PDFIUM_LOCK:
            look = pdfium.PdfDocument(str(source))
            try:
                for i, page in enumerate(reader.pages):
                    if not page_has_text(page):
                        continue
                    try:
                        text = page.extract_text()
                    except Exception:  # noqa: BLE001 - counted as having text; left alone
                        continue
                    if not layer_is_rubbish(text):
                        continue
                    pdfium_page = look[i]
                    textpage = pdfium_page.get_textpage()
                    try:
                        seen, hidden = _char_kinds(textpage)
                    finally:
                        textpage.close()
                        pdfium_page.close()
                    if hidden and not seen:
                        stale.append(i)
            finally:
                look.close()
        if stale:
            stripped = Path(str(output) + ".layers.tmp.pdf")
            _strip_layers(source, stale, stripped)
            source = stripped
            for i in stale:
                if report is not None:
                    report["replaced"].append(i + 1)
                note(f"page {i + 1}: its text layer was unreadable and has been replaced")
    try:
        _read_pages(source, output, force, deskew, rotate, script, note, quality=quality,
                    report=report, workers=workers, _page_worker=_page_worker)
    finally:
        if stripped is not None and stripped.exists():
            stripped.unlink()


def _refine_lines(image, lines, script=None, quality="standard"):
    """Apply Phase 2 refinements (phone-photo perspective back-mapping and
    low-confidence line retries) to an already-computed ``lines`` list."""
    flat, to_photo = _straighten_photo(image)
    if flat is not None:
        flat_lines = _lines(_with_quality(_read, flat, script, quality))
        if _lines_score(flat_lines) > _lines_score(lines):
            lines = reading_order(_map_lines(flat_lines, to_photo))
    return _retry_low_confidence(image, lines, script, quality)


def _record_skip(report, reason, page_num):
    if report is not None:
        report["skipped"].setdefault(reason, []).append(page_num)


def _record_unsure_stat(report, page_num, unsure_count, word_count):
    if report is None or unsure_count <= 0:
        return
    report["unsure_pages"][page_num] = report["unsure_pages"].get(page_num, 0) + unsure_count
    total_unsure = report["unsure_pages"][page_num]
    if total_unsure >= UNSURE_PAGE_MIN or (total_unsure / max(word_count, 1) >= UNSURE_PAGE_SHARE):
        if page_num not in report["flagged_pages"]:
            report["flagged_pages"].append(page_num)


def _read_pages(source, output, force, deskew, rotate, script, note, quality="standard",
                report=None, workers=None, _page_worker=None):
    from concurrent.futures import ProcessPoolExecutor, as_completed

    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_raw
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
        # Pass 1: handle text+picture pages, skip blank scan pages, and group
        # identical scan pages by image hash so duplicates are only read once.
        scan_pages = []      # [(i, img_hash, page_w, page_h, scale, kept_image)]
        first_for_hash = {}  # img_hash -> (i, page_w, page_h, scale, image)
        done_count = 0

        for i in range(total):
            progress.report(done_count, total, "page")
            page = writer.pages[i]
            if not force and page_has_text(page):
                # A page with text of its own: its pictures may still hold
                # words (a scanned figure, a scan with a stamp typed on it).
                with core.PDFIUM_LOCK:
                    pdfium_page = core.show_whole_page(doc[i])
                    textpage = pdfium_page.get_textpage()
                    try:
                        scale = render_scale(pdfium_page.get_width(), pdfium_page.get_height())
                        size = (max(1, math.ceil(pdfium_page.get_width() * scale)),
                                max(1, math.ceil(pdfium_page.get_height() * scale)))
                        boxes = _picture_boxes(pdfium_page, textpage, size)
                        image = pdfium_page.render(scale=scale).to_pil() if boxes else None
                    finally:
                        textpage.close()
                if not boxes:
                    _record_skip(report, "already had text", i + 1)
                    done_count += 1
                    continue
                words = []
                for box in boxes:
                    progress.report(done_count, total, "page")  # (Cancel is heard between pictures)
                    try:
                        lines = _with_quality(recognise, image.crop(box), script, quality)
                    except progress.Cancelled:
                        raise
                    except Exception as exc:  # engine trouble: say which page
                        raise core.PdfManagerError(f"OCR failed on page {i + 1}: {exc}") from None
                    # A photo gives scraps (a horizon read as a dash, an edge as
                    # a letter): only words the engine is fairly sure of count,
                    # and a picture with next to nothing in it adds nothing.
                    # A picture full of words is a scan, and is read as a whole
                    # page is: every word the engine gave stays.
                    sure = [w for line in lines for w in line.words]
                    if sum(1 for w in sure for c in w.text if c.isalnum()) < PICTURE_TEXT:
                        sure = [w for w in sure if w.confidence >= PICTURE_CONFIDENCE]
                        if sum(1 for w in sure for c in w.text if c.isalnum()) < PICTURE_LETTERS:
                            continue
                    for w in sure:
                        words.append(Word(w.text, [(x + box[0], y + box[1]) for x, y in w.quad], w.confidence))
                if words:
                    # Not the words the page has already (a stamp typed over a scan).
                    with core.PDFIUM_LOCK:
                        pdfium_page = doc[i]
                        textpage = pdfium_page.get_textpage()
                        try:
                            fresh = []
                            for w in words:
                                cx = sum(p[0] for p in w.quad) / 4
                                cy = sum(p[1] for p in w.quad) / 4
                                px, py = ctypes.c_double(), ctypes.c_double()
                                pdfium_raw.FPDF_DeviceToPage(pdfium_page.raw, 0, 0, image.width, image.height, 0,
                                                             int(cx), int(cy), ctypes.byref(px), ctypes.byref(py))
                                if not textpage.get_text_bounded(px.value - 2, py.value - 2, px.value + 2, py.value + 2).strip():
                                    fresh.append(w)
                        finally:
                            textpage.close()
                    if fresh:
                        base_idx = textlayer._count_ocr_words_in_page(page, writer)
                        textlayer.add_text_layer(writer, page, fresh, image.size, scale * 72)
                        unsure = textlayer.record_unsure_words(
                            writer, page, fresh, image.size, scale * 72, base_index=base_idx)
                        read = len(boxes)
                        if report is not None:
                            report["pages_read"].append(i + 1)
                            report["pictures"][i + 1] = read
                        _record_unsure_stat(report, i + 1, unsure, len(fresh))
                        note(f"page {i + 1}: read the text in {read} picture{'s' if read != 1 else ''}")
                        done_count += 1
                        continue
                _record_skip(report, "already had text", i + 1)
                done_count += 1
                continue

            with core.PDFIUM_LOCK:
                pdfium_page = core.show_whole_page(doc[i])
                page_w, page_h = pdfium_page.get_width(), pdfium_page.get_height()
                scale = render_scale(page_w, page_h)
                image = pdfium_page.render(scale=scale).to_pil()
            if _is_blank_image(image):
                _record_skip(report, "blank page", i + 1)
                done_count += 1
                continue
            img_hash = _image_hash(image)
            scan_pages.append((i, img_hash, page_w, page_h, scale, image if force else None))
            if img_hash not in first_for_hash:
                first_for_hash[img_hash] = (i, page_w, page_h, scale, image)

        # Pass 2: recognise each unique non-blank scan page (in parallel when beneficial).
        results_by_hash = {}
        unique_items = list(first_for_hash.items())
        n_workers = default_workers(len(unique_items)) if workers is None else max(1, int(workers))
        min_parallel = 2 if workers is not None else PARALLEL_MIN_PAGES
        can_pool = _page_worker is not None or (recognise is _ORIG_RECOGNISE and _read is _ORIG_READ)
        use_pool = n_workers > 1 and len(unique_items) >= min_parallel and can_pool

        if use_pool:
            worker_fn = _page_worker or _recognise_scan_page_worker
            hash_by_page = {info[0]: h for h, info in unique_items}
            executor = ProcessPoolExecutor(max_workers=min(n_workers, len(unique_items)))
            try:
                futs = {
                    executor.submit(worker_fn, (str(source), info[0], rotate, deskew, script, quality)): info[0]
                    for _h, info in unique_items
                }
                for fut in as_completed(futs):
                    page_idx = futs[fut]
                    try:
                        _p, lines, turn, angle, img_size = fut.result()
                    except Exception as exc:
                        raise core.PdfManagerError(f"OCR failed on page {page_idx + 1}: {exc}") from None
                    results_by_hash[hash_by_page[page_idx]] = (lines, turn, angle, img_size)
                    done_count += 1
                    progress.report(min(done_count, total - 1), total, "page")
            except progress.Cancelled:
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            except BaseException:
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            else:
                executor.shutdown(wait=True)
        else:
            for img_hash, (page_idx, page_w, page_h, scale, image) in unique_items:
                progress.report(min(done_count, total - 1), total, "page")
                try:
                    if _page_worker is not None:
                        _p, lines, turn, angle, img_size = _page_worker(
                            (str(source), page_idx, rotate, deskew, script, quality))
                    else:
                        lines, turn, angle, img_size = _recognise_scan_page(
                            image, doc, page_idx, page_w, page_h, scale, rotate, deskew, script, quality)
                except progress.Cancelled:
                    raise
                except Exception as exc:  # engine trouble: say which page
                    raise core.PdfManagerError(f"OCR failed on page {page_idx + 1}: {exc}") from None
                results_by_hash[img_hash] = (lines, turn, angle, img_size)
                done_count += 1

        # Pass 3: apply results to pages in deterministic page order.
        for i, img_hash, page_w, page_h, scale, kept_image in scan_pages:
            page = writer.pages[i]
            lines, turn, angle, img_size = results_by_hash[img_hash]
            if turn:
                # Show the page the right way up, as Acrobat does:
                # /Rotate turns it clockwise, the picture turned the other way round.
                _set_rotation(page, (page.rotation or 0) + 360 - turn)
            if angle:
                # Turn the page with the deskewed picture so words and page stay in step.
                _turn_page(page, -angle, Transformation)
            if force:
                img = kept_image
                if turn:
                    img = img.rotate(turn, expand=True)
                if angle:
                    img = img.rotate(-angle, resample=Image.BICUBIC, fillcolor="white")
                _rasterise(writer, page, img, scale)
                textlayer._write_page_unsure_list(page, [])
                rasterised = True
            words = [w for line in lines for w in line.words]
            if words:
                base_idx = textlayer._count_ocr_words_in_page(page, writer)
                textlayer.add_text_layer(writer, page, words, img_size, scale * 72)
                unsure = textlayer.record_unsure_words(
                    writer, page, words, img_size, scale * 72, base_index=base_idx)
                if report is not None:
                    report["pages_read"].append(i + 1)
                _record_unsure_stat(report, i + 1, unsure, len(words))
            else:
                _record_skip(report, "no text found", i + 1)

        if report is not None:
            report["pages_read"].sort()
            report["flagged_pages"].sort()
            for pages in report["skipped"].values():
                pages.sort()
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
