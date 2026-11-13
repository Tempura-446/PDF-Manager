"""0.26.0: Scan. scanner.py over a made-up WIA (fakewia.py): listing
scanners, colour / resolution / feeder settings, an empty feeder, a busy
or offline scanner, cancelling; pagephoto.py on made-up phone photos
(photosynth.py): the page's edges, flattening, straightening, the
document look, and keeping the photo when no page is found; scanpdf.py:
pages to a searchable PDF with blank pages taken out."""
import math
import threading
import time

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

from fakewia import FakeCom, FakeScanner, text_page
from pdfmanager import core, ocr, pagephoto, progress, scan, scanner, scanpdf
from photosynth import CORNERS, camera_corners, desk, photo


@pytest.fixture
def wia():
    """Puts a fake WIA behind scanner.py; the test fills it."""
    fake = FakeCom()
    scanner.use(fake)
    yield fake
    scanner.use(None)


# -- scanner.py ------------------------------------------------------------------------------

def test_lists_scanners_only(wia):
    wia.scanners = [FakeScanner("Desk scanner"), FakeScanner("Glass only", feeder=False)]
    found = scanner.list_scanners()
    assert [s["name"] for s in found] == ["Desk scanner", "Glass only"]  # the camera isn't listed
    assert found[0]["feeder"] and found[0]["flatbed"]
    assert not found[1]["feeder"] and found[1]["flatbed"]
    assert all(s["id"].startswith("{6BDD1FC6") for s in found)
    assert wia.threads  # in a COM apartment


def test_no_scanner(wia):
    assert scanner.list_scanners() == []
    assert scanner.status() == {"available": True, "reason": ""}


def test_wia_missing(wia):
    wia.missing = True
    state = scanner.status()
    assert not state["available"] and "Windows Image Acquisition" in state["reason"]


def test_not_windows_says_so(monkeypatch):
    scanner.use(None)
    monkeypatch.setattr(scanner.sys, "platform", "linux")
    state = scanner.status()
    assert not state["available"] and "Windows" in state["reason"] and "Photos" in state["reason"]


def test_flatbed_page_in_grey_at_300(wia):
    desk_scanner = FakeScanner("Desk scanner", flatbed_pages=[text_page(1), text_page(2)])
    wia.scanners = [desk_scanner]
    arrived = []
    pages = scanner.scan(desk_scanner.id, mode="grey", dpi=300, source="flatbed",
                         on_page=lambda im, dpi: arrived.append(dpi))
    assert len(pages) == 1 and arrived == [300]
    image, dpi = pages[0]
    assert dpi == 300 and image.info["dpi"] == (300, 300)
    written = dict(desk_scanner.item_props.log)
    assert written[6146] == 2 and written[4103] == 2 and written[4104] == 8  # greyscale intent, type, depth
    assert written[6147] == 300 and written[6148] == 300
    assert written[6151] == 2550 and written[6152] == 3508  # the whole glass
    assert dict(desk_scanner.device_props.log)[3088] == 2  # flatbed
    # "Scan another page": the next press gives the next page.
    assert scanner.scan(desk_scanner.id, source="flatbed")[0][0].tobytes() == text_page(2).tobytes()


@pytest.mark.parametrize("mode,intent,kind,bits", [("colour", 1, 3, 24), ("bw", 4, 0, 1)])
def test_colour_and_black_and_white(wia, mode, intent, kind, bits):
    s = FakeScanner()
    wia.scanners = [s]
    scanner.scan(s.id, mode=mode, dpi=150)
    written = dict(s.item_props.log)
    assert (written[6146], written[4103], written[4104], written[6147]) == (intent, kind, bits, 150)


def test_feeder_gives_every_page(wia):
    s = FakeScanner("Office scanner", feeder_pages=[text_page(n) for n in (1, 2, 3)])
    wia.scanners = [s]
    pages = scanner.scan(s.id, source="feeder", dpi=200)
    assert len(pages) == 3 and s.scanned == 3
    assert dict(s.device_props.log)[3088] == 1 and dict(s.device_props.log)[3096] == 1


def test_feeder_without_status_scans_until_empty(wia):
    s = FakeScanner(feeder_pages=[text_page(1), text_page(2)], reports_status=False)
    wia.scanners = [s]
    assert len(scanner.scan(s.id, source="feeder")) == 2


def test_feeder_empty(wia):
    s = FakeScanner(feeder_pages=[])
    wia.scanners = [s]
    with pytest.raises(scanner.ScannerError) as err:
        scanner.scan(s.id, source="feeder")
    assert err.value.code == "empty" and "feeder is empty" in str(err.value)


def test_no_feeder(wia):
    s = FakeScanner(feeder=False)
    wia.scanners = [s]
    with pytest.raises(scanner.ScannerError, match="no document feeder"):
        scanner.scan(s.id, source="feeder")


@pytest.mark.parametrize("hresult,code,words", [(0x80210006, "busy", "busy"), (0x80210005, "offline", "offline"),
                                                (0x80210002, "jam", "jammed"), (0x80210016, "cover", "cover")])
def test_busy_offline_and_others(wia, hresult, code, words):
    s = FakeScanner(transfer_error=hresult)
    wia.scanners = [s]
    with pytest.raises(scanner.ScannerError) as err:
        scanner.scan(s.id)
    assert err.value.code == code and words in str(err.value) and err.value.hresult == hresult


def test_busy_when_connecting(wia):
    s = FakeScanner(connect_error=0x80210006)
    wia.scanners = [s]
    assert scanner.list_scanners()[0]["name"] == "Fake scanner"  # still listed
    with pytest.raises(scanner.ScannerError, match="busy"):
        scanner.scan(s.id)


def test_unknown_failure_names_its_code(wia):
    s = FakeScanner(transfer_error=0x80004005)
    wia.scanners = [s]
    with pytest.raises(scanner.ScannerError, match="0x80004005"):
        scanner.scan(s.id)


def test_gone_scanner(wia):
    wia.scanners = [FakeScanner()]
    with pytest.raises(scanner.ScannerError) as err:
        scanner.scan("{nothing}\\0001")
    assert err.value.code == "gone"


def test_bitmap_only_driver(wia):
    s = FakeScanner(png=False)
    wia.scanners = [s]
    assert len(scanner.scan(s.id)) == 1
    assert [f[:9] for f in s.formats] == ["{B96B3CAF", "{B96B3CAB"]  # PNG asked, then BMP


def test_refused_resolution(wia):
    s = FakeScanner()
    s.item_props.refuse = {"6147"}
    wia.scanners = [s]
    with pytest.raises(scanner.ScannerError):
        scanner.scan(s.id, dpi=300)


def test_cancel_between_pages(wia):
    s = FakeScanner(feeder_pages=[text_page(n) for n in range(1, 9)], delay=0.15)
    wia.scanners = [s]
    job = progress.Job()
    arrived = []

    def work():
        return scanner.scan(s.id, source="feeder", on_page=lambda im, dpi: arrived.append(1))

    job.start(work)
    while len(arrived) < 2:
        time.sleep(0.02)
    job.cancel()
    job.thread.join(10)
    assert job.state == "cancelled"
    assert 2 <= s.scanned < 8
    # The scanner is free again afterwards.
    s.feeder_pages = [text_page(1)]
    s.device_props.values["3087"].Value = 1
    assert len(scanner.scan(s.id, source="feeder")) == 1


def test_one_scan_at_a_time(wia):
    s = FakeScanner(delay=0.4)
    wia.scanners = [s]
    t = threading.Thread(target=scanner.scan, args=(s.id,))
    t.start()
    time.sleep(0.1)
    with pytest.raises(scanner.ScannerError, match="already running"):
        scanner.scan(s.id)
    t.join()


def test_hresult_through_dotnet_wrappers():
    class Inner(Exception):
        HResult = -2145320957  # 0x80210003 as .NET gives it (signed)

    class Outer(Exception):
        HResult = -2146232828  # TargetInvocationException
        InnerException = Inner()

    assert scanner.Com.hresult(Outer()) == scanner.PAPER_EMPTY


# -- pagephoto.py ------------------------------------------------------------------------------

def _err(found, truth):
    return max(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(found, truth))


@pytest.mark.parametrize("corners", [CORNERS, camera_corners(turn=-15, tilt=15, sideways=-10),
                                     camera_corners(turn=25, tilt=20, distance=0.6),
                                     camera_corners(turn=2, tilt=0, sideways=0, distance=0.55)])
def test_finds_the_page_edges(corners):
    shot, truth = photo(corners=corners)
    found = pagephoto.find_page(shot)
    assert found.found and found.confidence > 0.5, found
    assert _err(found.corners, truth) < 3.0, (found.corners, truth)


def test_flattened_page_is_upright_and_a4():
    shot, _ = photo()
    flat, found = pagephoto.flatten_photo(shot)
    assert found.found
    assert abs(flat.height / flat.width - 297 / 210) < 0.02  # A4, as photographed
    grey = np.asarray(flat.convert("L"), float)
    h, w = grey.shape
    # No desk left round the edges: the paper reaches every side.
    for strip in (grey[:6, w // 4:3 * w // 4], grey[-6:, w // 4:3 * w // 4], grey[h // 4:3 * h // 4, :6],
                  grey[h // 4:3 * h // 4, -6:]):
        assert np.median(strip) > 120, np.median(strip)
    # Upright: the black square is at the top left, the heading runs level along the top.
    square = grey[int(h * 0.08):int(h * 0.13), int(w * 0.09):int(w * 0.18)]
    assert square.mean() < 90, square.mean()
    assert grey[int(h * 0.85):int(h * 0.9), int(w * 0.09):int(w * 0.18)].mean() > grey[int(h * 0.08):int(h * 0.13), int(w * 0.09):int(w * 0.18)].mean() + 60
    heading = grey[int(h * 0.075):int(h * 0.1), int(w * 0.3):int(w * 0.9)]
    assert (heading.mean(axis=0) < 110).mean() > 0.9  # dark the whole way along: level


def test_document_look_whitens_paper_and_keeps_ink():
    shot, _ = photo()
    flat, _ = pagephoto.flatten_photo(shot)
    raw = np.asarray(flat.convert("L"), float)
    look = pagephoto.document_look(flat)
    assert look.mode == "L"
    out = np.asarray(look, float)
    h, w = out.shape
    # The paper in the shadow (low on the page) and in the light end up alike, and white.
    blank_dark, blank_light = (slice(int(h * 0.955), int(h * 0.985)), slice(int(w * 0.1), int(w * 0.3))), \
        (slice(int(h * 0.02), int(h * 0.05)), slice(int(w * 0.6), int(w * 0.9)))
    assert abs(raw[blank_dark].mean() - raw[blank_light].mean()) > 25  # it was uneven
    assert out[blank_dark].mean() > 240 and out[blank_light].mean() > 240
    assert out[int(h * 0.08):int(h * 0.13), int(w * 0.09):int(w * 0.18)].mean() < 40  # the ink stays black


def test_keep_colours():
    shot, _ = photo(colour=True)
    flat, _ = pagephoto.flatten_photo(shot)
    look = pagephoto.document_look(flat, keep_colours=True)
    assert look.mode == "RGB"
    r, g, b = (np.asarray(look, float)[int(look.height * 0.89):int(look.height * 0.92),
                                       int(look.width * 0.75):int(look.width * 0.88), i].mean() for i in range(3))
    assert r > 150 and g < 90 and b < 90, (r, g, b)  # the red stamp stays red
    paper = np.asarray(look, float)[int(look.height * 0.02):int(look.height * 0.05), int(look.width * 0.6):int(look.width * 0.9)]
    assert paper.mean() > 240


@pytest.mark.parametrize("case", ["desk only", "page fills the photo", "white page on white"])
def test_no_confident_edges_keeps_the_photo(case):
    if case == "desk only":
        shot = desk((1200, 900))
    elif case == "page fills the photo":
        shot, _ = photo(corners=camera_corners(turn=1, tilt=0, sideways=0, distance=0.30))
    else:
        shot, _ = photo(background=Image.new("RGB", (1600, 1200), (244, 243, 240)), light=False)
    found = pagephoto.find_page(shot)
    assert not found.found and found.why
    flat, found = pagephoto.flatten_photo(shot)
    assert flat.size == shot.size and not found.found


def test_straightens_a_crooked_photo():
    page = pagephoto.document_look(pagephoto.flatten_photo(photo()[0])[0])
    crooked = page.rotate(4, resample=Image.BICUBIC, fillcolor=255)
    assert pagephoto.skew_angle(crooked) == pytest.approx(-4, abs=0.3)
    assert pagephoto.skew_angle(pagephoto.straighten(crooked)) == 0.0
    assert pagephoto.skew_angle(Image.new("L", (800, 1100), 255)) == 0.0  # nothing to tell from


def test_exif_turned_photo():
    shot, _ = photo()
    turned = shot.rotate(90, expand=True)  # stored sideways, the camera said "turn it back"
    exif = Image.Exif()
    exif[0x0112] = 8
    turned.info["exif"] = exif.tobytes()
    import io
    buf = io.BytesIO()
    turned.save(buf, "JPEG", exif=exif.tobytes(), quality=92)
    flat, found = pagephoto.flatten_photo(Image.open(io.BytesIO(buf.getvalue())))
    assert found.found and flat.height > flat.width


# -- scanpdf.py ------------------------------------------------------------------------------

def words_page(lines, dpi=200, angle=0.0):
    """A scanned page of real words (for OCR), turned ``angle`` degrees."""
    w, h = int(8.27 * dpi), int(11.69 * dpi)
    im = Image.new("L", (w, h), 248)
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", int(dpi * 0.17))
    except OSError:
        font = ImageFont.load_default(size=int(dpi * 0.17))
    y = dpi
    for line in lines:
        d.text((dpi, y), line, fill=20, font=font)
        y += int(dpi * 0.3)
    return im.rotate(angle, resample=Image.BICUBIC, fillcolor=248) if angle else im


def test_pages_to_pdf_sizes_and_blank_page(tmp_path):
    pages = [scanpdf.Page(text_page(1, size=(1240, 1754)), 150),
             scanpdf.Page(Image.new("L", (1240, 1754), 250), 150),
             scanpdf.Page(text_page(2, size=(1754, 1240)), 150, turn=90)]
    notes = []
    out = scanpdf.make_pdf(pages, tmp_path / "scan.pdf", upright=False, recognise=False, notes=notes)
    reader = PdfReader(out)
    sizes = [tuple(round(float(v)) for v in p.mediabox[2:]) for p in reader.pages]
    assert sizes == [(595, 842), (595, 842)], sizes  # 150 dpi: A4; the sideways page turned by hand
    assert "1 blank page taken out" in notes


def test_photos_fit_a4_or_letter(tmp_path):
    a4, _ = scanpdf.photo_page(photo()[0])
    wide = scanpdf.Page(Image.new("RGB", (1100, 850), "white"), None, photo=True)
    out = scanpdf.make_pdf([a4, wide], tmp_path / "photos.pdf", remove_blank=False, upright=False,
                           recognise=False)
    sizes = [tuple(round(float(v)) for v in p.mediabox[2:]) for p in PdfReader(out).pages]
    assert sizes == [(595, 842), (792, 612)], sizes  # A4 for the A4 page; Letter, sideways, for the wide one
    out = scanpdf.make_pdf([a4], tmp_path / "letter.pdf", paper="letter", recognise=False, upright=False)
    assert tuple(round(float(v)) for v in PdfReader(out).pages[0].mediabox[2:]) == (612, 792)


@pytest.mark.skipif(scanpdf.ocr_engine() is None, reason="needs an OCR engine (built-in or Tesseract)")
def test_searchable_pdf_from_scanned_pages(tmp_path):
    crooked = words_page(["Invoice number 4471", "Payment due in thirty days"] * 4, angle=2.5)
    upside_down = words_page(["Second page of the letter", "Kind regards from the office"] * 4).rotate(180)
    pages = [scanpdf.Page(crooked, 200), scanpdf.Page(Image.new("L", crooked.size, 248), 200),
             scanpdf.Page(upside_down, 200)]
    notes = []
    job = progress.Job()
    job.run(scanpdf.make_pdf, pages, tmp_path / "scan.pdf", notes=notes)
    assert job.state == "done", job.error
    reader = PdfReader(tmp_path / "scan.pdf")
    assert len(reader.pages) == 2
    texts = [p.extract_text() for p in reader.pages]
    assert "Invoice number 4471" in texts[0] and "Payment due" in texts[0], texts[0]
    assert "Kind regards" in texts[1], texts[1]
    assert "1 blank page taken out" in notes and any(n.startswith("Made searchable") for n in notes)
    if scan.find_tesseract() or ocr.builtin_available():
        assert "1 page turned upright" in notes, notes


def test_no_ocr_engine_is_said(tmp_path, monkeypatch):
    monkeypatch.setattr(scanpdf, "ocr_engine", lambda: None)
    notes = []
    scanpdf.make_pdf([scanpdf.Page(text_page(1), 150)], tmp_path / "a.pdf", upright=False, notes=notes)
    assert any("no OCR engine" in n for n in notes)


def test_no_pages():
    with pytest.raises(core.PdfManagerError, match="no pages"):
        scanpdf.make_pdf([], "x.pdf")


def test_scanbox_keeps_pages(tmp_path):
    boxes = scanpdf.ScanBoxes()
    box = boxes.get(None)
    first = box.add(text_page(1), 300)
    second = box.add(photo()[0], None, photo=True, found=True)
    assert [p["id"] for p in box.list()] == [first["id"], second["id"]]
    thumb = Image.open(__import__("io").BytesIO(box.thumbnail(second["id"], "colour")))
    assert max(thumb.size) <= scanpdf.THUMB_SIDE
    assert boxes.get(box.id) is box
    folder = box.folder
    boxes.drop(box.id)
    assert not folder.exists()
    with pytest.raises(core.PdfManagerError):
        boxes.get(box.id, create=False)
