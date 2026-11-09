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
import os
import statistics
import threading
from dataclasses import dataclass
from pathlib import Path

from pdfmanager import core, scan, textlayer
from pdfmanager.textlayer import Word

__all__ = ["Word", "Line", "ENGINES", "choose_engine", "recognise", "reading_order", "text_of", "skew_angle"]

ENGINES = ("auto", "builtin", "tesseract")
MIN_CONFIDENCE = 0.3   # words and lines the model is less sure of than this are dropped
RENDER_DPI = 300
MAX_RENDER_SIDE = 6000  # pixels; a 300 dpi A0 drawing would be 14000 and gain nothing (the model works at 2000)

# Tesseract's codes for the languages the built-in model reads. With engine
# "auto", a language outside this set goes to Tesseract when it is there.
BUILTIN_LANGUAGES = frozenset("""
    afr aze bos cat ces chi_sim chi_tra cym dan deu eng est eus fin fra gle glg hrv hun ind isl
    ita jpn kur lat lav lit ltz mlt mri msa nld nor oci pol por que roh ron slk slv spa sqi srp_latn
    swa swe tgl tur uzb vie equ osd
""".split())
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


def choose_engine(engine, language="eng"):
    """'builtin' or 'tesseract' for ``engine`` (one of ENGINES), or a
    PdfManagerError saying what to install. ``language`` (Tesseract codes)
    matters for "auto": a script the built-in model cannot read, such as
    Russian or Arabic, goes to Tesseract when it is installed."""
    if engine not in ENGINES:
        raise core.PdfManagerError(f"unknown OCR engine {engine!r}; choose auto, builtin or tesseract")
    if engine == "auto":
        wanted = {code.strip() for code in (language or "").split("+") if code.strip()}
        if builtin_available() and (wanted <= BUILTIN_LANGUAGES or not tesseract_available()):
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

_engine_lock = threading.Lock()   # building the instance
_call_lock = threading.Lock()     # using it: it keeps the last call's settings
_engine_instance = None


def _engine():
    """The one RapidOCR instance (its models take about a second to load)."""
    global _engine_instance
    with _engine_lock:
        if _engine_instance is None:
            try:
                from rapidocr import RapidOCR

                _engine_instance = RapidOCR(params={"Global.return_word_box": True})
            except Exception as exc:  # a broken install, or model files missing from a build
                raise core.PdfManagerError(
                    "OCR's built-in engine could not start; reinstall it (pip install "
                    f"pdf-manager[ocr]) or choose the Tesseract engine ({exc})") from None
            # It announces every model it loads; only trouble is worth hearing.
            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        return _engine_instance


def reset():
    """Forget the loaded engine and what was learnt about its install (tests)."""
    global _engine_instance, _builtin_imports
    with _engine_lock:
        _engine_instance = None
        _builtin_imports = None


def _array(image):
    import numpy

    return numpy.asarray(image.convert("RGB"))


def _quad(points):
    return [(float(x), float(y)) for x, y in points]


def recognise(image):
    """The lines of text in a PIL image, each with its words."""
    # Every switch is named each time: the engine keeps the last call's
    # settings, and skew_angle() runs it with the detector alone.
    engine = _engine()
    with _call_lock:
        result = engine(_array(image), use_det=True, use_cls=True, use_rec=True)
    if not result.txts:
        return []
    lines = []
    word_results = getattr(result, "word_results", None) or [None] * len(result.txts)
    for text, box, score, found in zip(result.txts, result.boxes, result.scores, word_results):
        if score < MIN_CONFIDENCE or not text.strip():
            continue
        words = _words(text, found) if found else [Word(text, _quad(box), float(score))]
        if not words:
            continue
        lines.append(Line(text, _quad(box), float(score), words))
    return reading_order(lines)


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
        if words and run and not space_before and len(piece_text) == 1:
            last = words[-1]
            words[-1] = Word(last.text + piece_text, _union([last.quad, quad]), min(last.confidence, score))
        else:
            words.append(Word(piece_text, quad, score))
            run = len(piece_text) == 1
    return [w for w in words if w.confidence >= MIN_CONFIDENCE]


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


# -- running OCR on a file ------------------------------------------------------

def page_has_text(page):
    """Whether a page already has selectable text (then it is left alone
    unless ``force`` says otherwise, as OCRmyPDF's skip-text does)."""
    try:
        return bool(page.extract_text().strip())
    except Exception:  # a page too damaged to read is treated as a scan
        return False


def run(path, output, language="eng", force=False, deskew=False, password=None, engine="auto"):
    """Add a searchable text layer to the scanned pages of ``path``; see
    ``core.ocr``."""
    from pdfmanager import progress

    chosen = choose_engine(engine, language)
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
            _run_tesseract(source, tmp_out, language, force, deskew)
        else:
            _run_builtin(source, tmp_out, force, deskew)
        os.replace(tmp_out, output)
    finally:
        for tmp in (tmp_in, tmp_out):
            if tmp is not None and tmp.exists():
                tmp.unlink()
    return output


def _run_tesseract(source, output, language, force, deskew):
    try:
        import ocrmypdf
        from ocrmypdf.exceptions import ExitCodeException, MissingDependencyError
    except ImportError:
        raise core.PdfManagerError(
            "OCR with Tesseract needs OCRmyPDF and the Tesseract program: pip install "
            f"pdf-manager[tesseract], then install Tesseract ({TESSERACT_LINK})") from None
    scan.add_programs_to_path()  # Tesseract and Ghostscript where installers put them
    try:
        ocrmypdf.ocr(str(source), str(output), language=language.split("+"),
                     skip_text=not force, force_ocr=force, deskew=deskew, progress_bar=False)
    except MissingDependencyError as exc:
        raise core.PdfManagerError(f"OCR is missing a program: {exc}") from None
    except ExitCodeException as exc:
        raise core.PdfManagerError(f"OCR failed: {exc}") from None


def render_scale(width_pt, height_pt):
    """Pixels per point for recognising a page of this size: 300 dpi, or
    less for a page whose long side would pass MAX_RENDER_SIDE pixels."""
    longest = max(width_pt, height_pt, 1)
    return min(RENDER_DPI / 72, MAX_RENDER_SIDE / longest)


def _run_builtin(source, output, force, deskew):
    import pypdfium2 as pdfium
    from PIL import Image
    from pypdf import PdfWriter, Transformation

    from pdfmanager import progress

    writer = PdfWriter(clone_from=str(source))
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
                pdfium_page = doc[i]
                scale = render_scale(pdfium_page.get_width(), pdfium_page.get_height())
                image = pdfium_page.render(scale=scale).to_pil()
            try:
                if deskew:
                    angle = skew_angle(image)
                    if angle:
                        # Turn the picture straight, and the page with it, so
                        # the words and the page stay in step.
                        image = image.rotate(-angle, resample=Image.BICUBIC, fillcolor="white")
                        _turn_page(page, -angle, Transformation)
                lines = recognise(image)
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
