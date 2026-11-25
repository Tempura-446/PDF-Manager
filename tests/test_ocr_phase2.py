# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Better OCR Phase 2: adaptive page cleaning, low-confidence retry, small-print
higher-DPI re-read, phone-photo perspective mapping, and Best accuracy models."""
import hashlib
import random
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

import ocrpages
import ocrscore
from pdfmanager import cli, core, gui, ocr, update
from pdfmanager.textlayer import Word

needs_builtin = pytest.mark.skipif(not ocr.builtin_available(), reason="needs rapidocr")


# -- Task 2.1: adaptive cleaning, dewarping, phone-photo perspective ----------

def test_clean_for_reading_leaves_clean_and_blank_pages_untouched():
    blank = Image.new("RGB", (600, 800), (255, 255, 255))
    cleaned, steps = ocr._clean_for_reading(blank)
    assert cleaned is blank and steps == []

    clean_page, _ = ocrpages.letter()
    cleaned, steps = ocr._clean_for_reading(clean_page)
    assert cleaned is clean_page and steps == []


def test_clean_for_reading_stretches_faint_print():
    clean_page, _ = ocrpages.letter()
    faint = ocrscore.faint(clean_page)
    cleaned, steps = ocr._clean_for_reading(faint)
    assert "contrast" in steps
    before = ocr._measure_image(faint)
    after = ocr._measure_image(cleaned)
    assert after["contrast"] > before["contrast"] + 80


def test_clean_for_reading_flattens_uneven_shadow():
    clean_page, _ = ocrpages.letter()
    w, h = clean_page.size
    grad = Image.linear_gradient("L").resize((w, h))
    # Dark shadow across one side of the page.
    shadowed = Image.composite(clean_page, clean_page.point(lambda v: int(v * 0.45)),
                               grad.point(lambda v: 255 if v > 140 else int(v * 1.8)))
    cleaned, steps = ocr._clean_for_reading(shadowed)
    assert "background" in steps
    assert ocr._measure_image(cleaned)["uneven"] < ocr._measure_image(shadowed)["uneven"]


def test_clean_for_reading_denoises_speckled_thick_print_but_protects_thin_strokes():
    # Thick print with heavy salt-and-pepper speckle -> denoise triggers.
    thick = ocrpages.render("Invoice 12345\nTotal 99.00", font_size=64)
    rnd = random.Random(42)
    px = thick.load()
    for _ in range(90000):
        px[rnd.randrange(thick.width), rnd.randrange(thick.height)] = rnd.choice((0, 255))
    _, steps = ocr._clean_for_reading(thick)
    assert "denoise" in steps

    # Very thin 1-px strokes -> stroke_width below MIN_DENOISE_STROKE so denoise is skipped.
    thin = Image.new("L", (400, 400), 255)
    draw = ImageDraw.Draw(thin)
    for y in range(40, 360, 20):
        draw.line([(40, y), (360, y)], fill=0, width=1)
    assert ocr._measure_image(thin)["stroke_width"] < ocr.MIN_DENOISE_STROKE


def test_dewarp_line_crop_straightens_curved_baseline_and_leaves_straight_alone():
    # Straight line: untouched.
    straight = Image.new("RGB", (300, 50), (255, 255, 255))
    ImageDraw.Draw(straight).line([(20, 25), (280, 25)], fill=(0, 0, 0), width=4)
    assert ocr._dewarp_line_crop(straight) is straight

    # Gently curved line (parabola with ~8 px sag): dewarped flatter.
    curved = Image.new("RGB", (300, 60), (255, 255, 255))
    draw = ImageDraw.Draw(curved)
    pts = [(x, 22 + 8.0 * ((x - 150) / 130.0) ** 2) for x in range(20, 281, 4)]
    draw.line(pts, fill=(0, 0, 0), width=4)
    fixed = ocr._dewarp_line_crop(curved)
    assert fixed is not curved
    # Measure vertical spread of ink centroids before vs after.
    def spread(im):
        arr = np.asarray(im.convert("L")) < 128
        ys = [np.nonzero(arr[:, x])[0].mean() for x in range(30, 270, 10) if arr[:, x].any()]
        return max(ys) - min(ys)
    assert spread(fixed) < spread(curved) * 0.5


def test_straighten_photo_detects_tilted_page_and_maps_quads_back():
    # White page on a dark desk with perspective tilt.
    w, h = 900, 1100
    photo = Image.new("RGB", (w, h), (45, 45, 50))
    page_corners = [(140, 110), (760, 150), (800, 970), (110, 940)]
    ImageDraw.Draw(photo).polygon(page_corners, fill=(248, 248, 246))
    flat, to_photo = ocr._straighten_photo(photo)
    assert flat is not None and to_photo is not None
    # The four corners of the flattened image must map back near the page corners on the photo.
    fw, fh = flat.size
    mapped = ocr._map_quad([(0, 0), (fw, 0), (fw, fh), (0, fh)], to_photo)
    for got, want in zip(mapped, page_corners):
        assert abs(got[0] - want[0]) < 15 and abs(got[1] - want[1]) < 15


# -- Task 2.2: two readings, the better kept ----------------------------------

def _fake_result(text, score, box):
    return SimpleNamespace(txts=[text], boxes=[box], scores=[score],
                           word_results=[[(text, score, box)]])


def test_retry_low_confidence_keeps_higher_confidence_and_never_duplicates(monkeypatch):
    im = Image.new("RGB", (600, 200), (255, 255, 255))
    low_quad = [(40.0, 40.0), (240.0, 40.0), (240.0, 80.0), (40.0, 80.0)]
    high_quad = [(40.0, 120.0), (240.0, 120.0), (240.0, 160.0), (40.0, 160.0)]
    low_line = ocr.Line("rnistake", low_quad, 0.52, [Word("rnistake", low_quad, 0.52)])
    high_line = ocr.Line("already good", high_quad, 0.96, [Word("already good", high_quad, 0.96)])

    # Case 1: retry gives a higher-confidence reading -> replaced (never both).
    calls = []
    def better_engine(arr, **kwargs):
        calls.append(arr.shape)
        return _fake_result("mistake", 0.93, [(10, 10), (190, 10), (190, 50), (10, 50)])

    monkeypatch.setattr(ocr, "_engine", lambda script=None, quality="standard": better_engine)
    out = ocr._retry_low_confidence(im, [low_line, high_line])
    assert len(calls) == 1, "only the low-confidence line is re-read"
    assert [l.text for l in out] == ["mistake", "already good"]
    assert out[0].confidence == pytest.approx(0.93)

    # Case 2: retry gives a LOWER-confidence reading -> original kept (never both).
    def worse_engine(arr, **kwargs):
        return _fake_result("garbage", 0.41, [(10, 10), (190, 10), (190, 50), (10, 50)])

    monkeypatch.setattr(ocr, "_engine", lambda script=None, quality="standard": worse_engine)
    out2 = ocr._retry_low_confidence(im, [low_line, high_line])
    assert [l.text for l in out2] == ["rnistake", "already good"]
    assert out2[0].confidence == pytest.approx(0.52)


@needs_builtin
def test_faint_and_small_print_holdout_pages_read_accurately():
    letter, truth = ocrpages.letter()
    faint_text = ocr.text_of(ocr.recognise(ocrscore.faint(letter)))
    assert ocrpages.cer(truth, faint_text) < 0.02

    small_im, small_truth = ocrpages.small_print()
    small_text = ocr.text_of(ocr.recognise(small_im))
    assert ocrpages.cer(small_truth, small_text) < 0.02


# -- Task 2.3: Best accuracy model choice -------------------------------------

@needs_builtin
@pytest.mark.parametrize("kind, suffix", [
    ("det", "/onnx/PP-OCRv6/det/PP-OCRv6_det_medium.onnx"),
    ("rec", "/onnx/PP-OCRv6/rec/PP-OCRv6_rec_medium.onnx"),
])
def test_best_accuracy_models_are_onnx_models_rapidocr_lists(kind, suffix):
    url, digest = ocr._best_model_source(kind)
    assert url.endswith(suffix), url
    assert len(digest) == 64


def test_best_accuracy_choice_reaches_engine_with_faked_models(tmp_path, monkeypatch):
    ocr.reset()
    models_root = tmp_path / "ocr-models"
    monkeypatch.setattr(ocr, "models_dir", lambda: models_root)

    fake_bytes = {"det": b"fake-server-det-onnx", "rec": b"fake-server-rec-onnx"}
    digests = {k: hashlib.sha256(v).hexdigest() for k, v in fake_bytes.items()}
    fetched = []

    monkeypatch.setattr(ocr, "_best_model_source",
                        lambda kind: (f"https://example.invalid/{kind}.onnx", digests[kind]))

    def fake_fetch(url, target):
        fetched.append(url)
        kind = "det" if "det" in url else "rec"
        target.write_bytes(fake_bytes[kind])

    monkeypatch.setattr(ocr, "_fetch", fake_fetch)

    import sys
    import types

    captured = []
    class FakeRapidOCR:
        def __init__(self, params=None):
            captured.append(params)
        def __call__(self, arr, **kwargs):
            return SimpleNamespace(txts=None, boxes=None, scores=None, word_results=None)

    fake_rapidocr = types.ModuleType("rapidocr")
    fake_rapidocr.ModelType = SimpleNamespace(MOBILE="mobile", SERVER="server")
    fake_rapidocr.OCRVersion = SimpleNamespace(PPOCRV5="PP-OCRv5")
    fake_rapidocr.RapidOCR = FakeRapidOCR
    monkeypatch.setitem(sys.modules, "rapidocr", fake_rapidocr)

    try:
        ocr.recognise(Image.new("L", (200, 200), 255), quality="best")
        assert len(fetched) == 2
        assert (models_root / "best_det.onnx").is_file()
        assert (models_root / "best_rec.onnx").is_file()
        assert len(captured) == 1
        params = captured[0]
        assert params["Det.model_type"] == fake_rapidocr.ModelType.SERVER
        assert params["Rec.model_type"] == fake_rapidocr.ModelType.SERVER
        assert params["Det.model_path"] == str(models_root / "best_det.onnx")
        assert params["Rec.model_path"] == str(models_root / "best_rec.onnx")
        # Second call reuses the cached engine and never re-downloads.
        ocr.recognise(Image.new("L", (200, 200), 255), quality="best")
        assert len(fetched) == 2 and len(captured) == 1
    finally:
        ocr.reset()


def test_best_accuracy_rejects_unknown_quality_and_damaged_download(tmp_path, monkeypatch):
    with pytest.raises(core.PdfManagerError, match="unknown OCR quality"):
        ocr._check_quality("ultra")

    monkeypatch.setattr(ocr, "models_dir", lambda: tmp_path / "models")
    monkeypatch.setattr(ocr, "_best_model_source", lambda kind: ("https://example.invalid/det.onnx", "0" * 64))
    monkeypatch.setattr(ocr, "_fetch", lambda url, target: target.write_bytes(b"corrupt"))
    with pytest.raises(core.PdfManagerError, match="damaged"):
        ocr._best_model("det")
    assert not list((tmp_path / "models").glob("*"))


def test_settings_default_quality_and_cli_wiring(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "data_dir", lambda: tmp_path)
    assert ocr.default_quality() == "standard"
    update.save_settings(ocr_quality="best")
    assert ocr.default_quality() == "best"
    assert update.public_settings()["ocr_quality"] == "best"

    # CLI --quality passes through to core.ocr.
    seen = {}
    monkeypatch.setattr(core, "ocr", lambda *a, **k: seen.update(k) or a[1])
    parser = cli.build_parser()
    args = parser.parse_args(["ocr", "in.pdf", "-o", "out.pdf", "--quality", "best"])
    cli.run_ocr(args, tmp_path / "in.pdf", tmp_path / "out.pdf", None)
    assert seen["quality"] == "best"


def test_gui_tool_and_settings_expose_ocr_quality(tmp_path, monkeypatch):
    ocr_tool = next(t for t in gui.OPERATIONS if t["id"] == "ocr")
    quality_field = next(f for f in ocr_tool["fields"] if f["key"] == "quality")
    choice_ids = [c[0] for c in quality_field["choices"]]
    assert choice_ids == ["", "standard", "best"]

    monkeypatch.setattr(update, "data_dir", lambda: tmp_path)
    assert gui._settings_for_page()["ocr_quality"] == "standard"


def test_ocrscore_reports_both_qualities_when_asked(tmp_path, monkeypatch, capsys):
    seen_qualities = []
    monkeypatch.setattr(ocr, "builtin_available", lambda: True)
    monkeypatch.setattr(ocr, "recognise", lambda im, script=None, quality="standard": (
        seen_qualities.append(quality) or []))
    monkeypatch.setattr(ocr, "run", lambda src, out, **k: (
        seen_qualities.append(k.get("quality")) or out.write_bytes(src.read_bytes())))
    monkeypatch.setattr(ocrscore, "pages", lambda: [("Clean, 300 dpi", Image.new("L", (200, 200), 255), "hello")])
    monkeypatch.setattr(ocrscore, "garbage_layer", lambda folder: (
        ocrpages.scan_pdf(Image.new("L", (200, 200), 255), folder / "g.pdf"), "hello"))
    monkeypatch.setattr(ocrscore, "mixed", lambda folder: (
        ocrpages.scan_pdf(Image.new("L", (200, 200), 255), folder / "m.pdf"), "hello"))
    monkeypatch.setattr("sys.argv", ["ocrscore.py", "--quality", "both"])

    ocrscore.main()
    out = capsys.readouterr().out
    assert "### Quality: standard" in out and "### Quality: best" in out
    assert "standard" in seen_qualities and "best" in seen_qualities
