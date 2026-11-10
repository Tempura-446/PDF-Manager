"""Performance limits, run with ``pytest --perf`` (the release check runs
them; the everyday test run skips them, as they take a few minutes).

Memory must grow with a file's size, not with its pages: a tool that kept
every decoded picture or rendered page would use hundreds of MB more on
the larger file, and run out of memory on a long scan. Each workload runs
in its own Python (perfwork.py) so its peak memory is its own, then the
same work on a small and a large file is compared. Times have generous
limits: they catch something gone badly wrong, not a slow computer.
"""
import io
import subprocess
import sys
import threading
import time
import tracemalloc
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.perf

HERE = Path(__file__).parent
SLACK_MB = 100  # allocator and library caches that differ from run to run
PER_FILE_MB = 6  # memory a tool may use per MB of extra file


def _work(work, src, tmp_path):
    out = tmp_path / f"out-{src.stem}-{work}.pdf"
    done = subprocess.run([sys.executable, str(HERE / "perfwork.py"), work, str(src), str(out), str(tmp_path)],
                          capture_output=True, text=True, timeout=1800, cwd=HERE.parent)
    assert done.returncode == 0, done.stderr[-2000:]
    seconds, peak = map(float, done.stdout.split()[-2:])
    return seconds, peak


def _image_pages(path, count, picture, width, height, colour="DeviceRGB"):
    """A PDF of ``count`` pages, each with its own picture object (a JPEG
    from ``picture(i)``) filling the page."""
    import pikepdf

    pdf = pikepdf.new()
    for i in range(count):
        image = pikepdf.Stream(pdf, picture(i), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                               Width=width, Height=height, ColorSpace=pikepdf.Name(f"/{colour}"),
                               BitsPerComponent=8, Filter=pikepdf.Name.DCTDecode)
        page = pdf.add_blank_page(page_size=(612, 792))
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=image))
        page.Contents = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Im0 Do Q")
    pdf.save(path)
    return path


def _photo(i, size=(1200, 1600)):
    from PIL import Image

    noise = Image.effect_noise((size[0] // 10, size[1] // 10), 60 + i % 40).resize(size, Image.BICUBIC)
    shade = Image.linear_gradient("L").rotate(i * 7).resize(size)
    im = Image.merge("RGB", [noise, shade, Image.radial_gradient("L").resize(size)])
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _check_growth(work, small, large, tmp_path, seconds_each):
    """Run ``work`` on both files: memory grows with the file, not the pages."""
    small_s, small_mb = _work(work, small, tmp_path)
    large_s, large_mb = _work(work, large, tmp_path)
    extra_file_mb = (large.stat().st_size - small.stat().st_size) / 2 ** 20
    allowed = PER_FILE_MB * extra_file_mb + SLACK_MB
    summary = (f"{work}: {small.name} {small_s:.1f} s {small_mb:.0f} MB, "
               f"{large.name} {large_s:.1f} s {large_mb:.0f} MB")
    print(summary)
    assert large_mb - small_mb <= allowed, f"{summary}: grew by more than {allowed:.0f} MB"
    assert large_s <= seconds_each, f"{summary}: slower than {seconds_each} s"


@pytest.fixture(scope="module")
def photos(tmp_path_factory):
    folder = tmp_path_factory.mktemp("perf-photos")
    return {n: _image_pages(folder / f"photos-{n}.pdf", n, _photo, 1200, 1600) for n in (10, 100)}


def test_merge_memory_follows_the_file_not_the_pages(photos, tmp_path):
    _check_growth("merge", photos[10], photos[100], tmp_path, seconds_each=60)


def test_compress_memory_follows_the_file_not_the_pages(photos, tmp_path):
    _check_growth("compress", photos[10], photos[100], tmp_path, seconds_each=300)


def test_ocr_memory_does_not_grow_with_the_pages(tmp_path):
    from pdfmanager import ocr

    if not ocr.builtin_available():
        pytest.skip("needs the built-in OCR engine (pdf-manager[ocr])")
    sys.path.insert(0, str(HERE))
    import ocrpages

    page = ocrpages.render().convert("L").resize((1654, 2339))  # A4 at 200 dpi
    buf = io.BytesIO()
    page.save(buf, "JPEG", quality=80)
    scan = buf.getvalue()
    small, large = (_image_pages(tmp_path / f"scan-{n}.pdf", n, lambda i: scan, 1654, 2339, "DeviceGray")
                    for n in (3, 15))
    _check_growth("ocr", small, large, tmp_path, seconds_each=15 * 60)


def test_compressing_scans_keeps_memory_to_a_page(tmp_path):
    """The scan compression engine works on one page's picture at a time."""
    sys.path.insert(0, str(HERE))
    import ocrpages

    page = ocrpages.render().convert("RGB")  # A4 at 300 dpi, in colour
    buf = io.BytesIO()
    page.save(buf, "JPEG", quality=85)
    scan = buf.getvalue()
    small, large = (_image_pages(tmp_path / f"colour-scan-{n}.pdf", n, lambda i: scan, *page.size)
                    for n in (3, 15))
    _check_growth("compress-scan", small, large, tmp_path, seconds_each=15 * 10)


def test_a_large_file_is_read_in_pieces_not_whole(tmp_path):
    """The reader gets a 200 MB file in pieces: a piece from its end comes
    at once, and sending all of it never holds more than a little of it."""
    from pdfmanager import gui

    big = tmp_path / "big.pdf"
    with big.open("wb") as f:
        f.write(b"%PDF-1.7\n")
        f.truncate(200 * 2 ** 20)  # sparse: quick to make, no 200 MB written
    size = big.stat().st_size
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    key = srv.RequestHandlerClass.opened.add(big)

    def get(headers=None):
        req = urllib.request.Request(f"{base}/api/file/{key}", headers={"X-Pdfm-Token": token, **(headers or {})})
        return urllib.request.urlopen(req, timeout=120)

    try:
        started = time.perf_counter()
        with get({"Range": f"bytes={size - 2 ** 20}-"}) as res:
            piece = res.read()
        assert res.status == 206 and len(piece) == 2 ** 20
        assert time.perf_counter() - started < 5, "a piece from the end of a big file was slow"
        tracemalloc.start()
        try:
            got = 0
            with get() as res:
                while chunk := res.read(2 ** 20):
                    got += len(chunk)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert got == size
        assert peak < 32 * 2 ** 20, f"sending the file held {peak / 2 ** 20:.0f} MB at once"
    finally:
        srv.shutdown()
        srv.server_close()
