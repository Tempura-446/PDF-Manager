"""Tests for compression levels, colour conversion, annotation handling,
batch CLI, duplicates, watch folders, OCR and the web GUI."""

import json
import shutil
import sys
import threading
import time
from pathlib import Path
import urllib.error
import urllib.request
import uuid

import pytest
from PIL import Image, ImageCms
from pypdf import PdfReader
from pypdf.generic import ContentStream
from reportlab.lib.pagesizes import A4, letter
from reportlab.pdfgen import canvas

from pdfmanager import core, gui, organize, watch
from pdfmanager.cli import main


def make_pdf(path, pages=1, title=None):
    c = canvas.Canvas(str(path), pagesize=A4)
    if title:
        c.setTitle(title)
    for i in range(pages):
        c.drawString(72, 72, f"Page {i + 1}")
        c.showPage()
    c.save()
    return path


def photo_pdf(path, tmp_path):
    """A page with a 1600x1200 noisy photo drawn 4 inches wide (400 DPI),
    a translucent PNG, a red rectangle and green text."""
    noise = Image.effect_noise((1600, 1200), 60).convert("RGB")
    gradient = Image.linear_gradient("L").resize((1600, 1200)).convert("RGB")
    Image.blend(noise, gradient, 0.5).save(tmp_path / "photo.png")
    alpha = Image.new("RGBA", (200, 200), (255, 0, 0, 128))
    alpha.save(tmp_path / "alpha.png")
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawImage(str(tmp_path / "photo.png"), 72, 400, width=288, height=216)
    c.drawImage(str(tmp_path / "alpha.png"), 72, 200, width=100, height=100, mask="auto")
    c.setFillColorRGB(0.9, 0.1, 0.1)
    c.rect(300, 200, 100, 100, fill=1)
    c.setFillColorRGB(0, 0.5, 0)
    c.drawString(72, 150, "Green text")
    c.showPage()
    c.save()
    return path


def images_of(path):
    return [img.image for img in PdfReader(str(path)).pages[0].images]


def image_spaces(path):
    xobjs = PdfReader(str(path)).pages[0]["/Resources"]["/XObject"]
    return {x.get_object()["/ColorSpace"] for x in xobjs.values()
            if x.get_object()["/Subtype"] == "/Image"}


def operators(path):
    reader = PdfReader(str(path))
    return [op for _, op in ContentStream(reader.pages[0].get_contents(), reader).operations]


# -- compression -----------------------------------------------------------

def test_compression_levels(tmp_path):
    src = photo_pdf(tmp_path / "photo.pdf", tmp_path)
    sizes = {}
    for level in ("low", "medium", "high"):
        out = core.compress(src, tmp_path / f"{level}.pdf", level=level)
        sizes[level] = out.stat().st_size
    original = src.stat().st_size
    assert sizes["low"] <= original
    assert sizes["high"] < sizes["medium"] < sizes["low"]
    # Medium caps the photo at 150 DPI: 4 inches -> 600 px wide.
    widths = sorted(im.width for im in images_of(tmp_path / "medium.pdf"))
    assert widths[-1] == 600
    assert max(im.width for im in images_of(tmp_path / "high.pdf")) == 384
    # Lossless keeps pixels identical.
    assert images_of(tmp_path / "low.pdf")[0].tobytes() == images_of(src)[0].tobytes()
    # Text survives, and the translucent image keeps its transparency mask.
    assert "Green text" in PdfReader(str(tmp_path / "high.pdf")).pages[0].extract_text()
    reader = PdfReader(str(tmp_path / "high.pdf"))
    xobjs = reader.pages[0]["/Resources"]["/XObject"]
    assert any("/SMask" in x.get_object() for x in xobjs.values())


def test_compression_level_alias_and_errors(tmp_path):
    src = make_pdf(tmp_path / "a.pdf")
    core.compress(src, tmp_path / "b.pdf", level="large")
    with pytest.raises(core.PdfManagerError):
        core.compress(src, tmp_path / "c.pdf", level="extreme")
    with pytest.raises(core.PdfManagerError):
        core.compress(src, tmp_path / "c.pdf", image_quality=0)


def test_compression_never_grows_images(tmp_path):
    # A tiny flat image is already smaller than any JPEG; leave it alone.
    Image.new("RGB", (40, 40), "white").save(tmp_path / "flat.png")
    c = canvas.Canvas(str(tmp_path / "flat.pdf"))
    c.drawImage(str(tmp_path / "flat.png"), 0, 0, width=500, height=500)
    c.save()
    out = core.compress(tmp_path / "flat.pdf", tmp_path / "out.pdf", level="high")
    reader = PdfReader(str(out))
    img = list(reader.pages[0]["/Resources"]["/XObject"].values())[0].get_object()
    assert img["/Filter"] != "/DCTDecode"


# -- colour ----------------------------------------------------------------

def test_convert_to_gray(tmp_path):
    src = photo_pdf(tmp_path / "photo.pdf", tmp_path)
    out = core.convert_color(src, tmp_path / "gray.pdf", "gray")
    ops = operators(out)
    assert b"rg" not in ops and b"RG" not in ops and b"g" in ops
    assert image_spaces(out) == {"/DeviceGray"}
    assert "Green text" in PdfReader(str(out)).pages[0].extract_text()


def test_convert_to_cmyk_and_back(tmp_path):
    src = photo_pdf(tmp_path / "photo.pdf", tmp_path)
    cmyk = core.convert_color(src, tmp_path / "cmyk.pdf", "cmyk")
    assert b"k" in operators(cmyk) and b"rg" not in operators(cmyk)
    assert image_spaces(cmyk) == {"/DeviceCMYK"}
    rgb = core.convert_color(cmyk, tmp_path / "rgb.pdf", "rgb")
    assert b"k" not in operators(rgb) and b"rg" in operators(rgb)
    assert image_spaces(rgb) == {"/DeviceRGB"}


def test_convert_with_icc_profile_adds_output_intent(tmp_path):
    icc = tmp_path / "srgb.icc"
    icc.write_bytes(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    src = photo_pdf(tmp_path / "photo.pdf", tmp_path)
    out = core.convert_color(src, tmp_path / "out.pdf", "rgb", icc_profile=icc)
    intents = PdfReader(str(out)).trailer["/Root"]["/OutputIntents"]
    assert intents[0].get_object()["/DestOutputProfile"].get_object()["/N"] == 3
    with pytest.raises(core.PdfManagerError):
        core.convert_color(src, tmp_path / "x.pdf", "rgb", icc_profile=tmp_path / "missing.icc")


# -- annotations -----------------------------------------------------------

def link_pdf(path, pages=4):
    """Each page: a URI link over a box at (400,600)-(480,640) and an
    internal link to the last page over a box at (100,100)-(160,130)."""
    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(pages):
        c.bookmarkPage(f"p{i}")
        c.linkURL("https://example.com", (400, 600, 480, 640))
        c.linkAbsolute("go", f"p{pages - 1}", (100, 100, 160, 130))
        c.showPage()
    c.save()
    return path


def rects(path, page=0):
    page = PdfReader(str(path)).pages[page]
    return sorted(tuple(round(float(v)) for v in a.get_object()["/Rect"])
                  for a in page.get("/Annots", []))


def test_resize_moves_links(tmp_path):
    src = link_pdf(tmp_path / "links.pdf")
    out = core.resize(src, tmp_path / "half.pdf", scale=0.5)
    assert rects(out) == [(50, 50, 80, 65), (200, 300, 240, 320)]


def test_resize_rotated_page_moves_links(tmp_path):
    src = link_pdf(tmp_path / "links.pdf", pages=1)
    rotated = core.rotate(src, tmp_path / "rot.pdf", 90)
    out = core.resize(rotated, tmp_path / "out.pdf", scale=1.0)
    # 90° clockwise on a 612x792 page: (x, y) -> (y, 612 - x)
    assert rects(out) == [(100, 452, 130, 512), (600, 132, 640, 212)]


def test_nup_keeps_links_and_retargets_internal_ones(tmp_path):
    src = link_pdf(tmp_path / "links.pdf", pages=6)
    out = core.n_up(src, tmp_path / "nup.pdf", per_sheet=4)
    reader = PdfReader(str(out))
    assert len(reader.pages) == 2
    annots = [a.get_object() for a in reader.pages[0]["/Annots"]]
    uris = [a for a in annots if "/A" in a]
    internal = [a for a in annots if "/Dest" in a]
    assert len(uris) == 4 and len(internal) == 4
    # Links to the last page (page 6) now point at sheet 2.
    target = internal[0]["/Dest"][0]
    assert target.idnum == reader.pages[1].indirect_reference.idnum
    # No source pages were dragged along as hidden objects.
    assert len(reader.pages) == 2


# -- CLI: batch and passwords ----------------------------------------------

def test_cli_batch_with_glob_and_output_folder(tmp_path, capsys):
    for name in ("a", "b", "c"):
        make_pdf(tmp_path / f"{name}.pdf", pages=2)
    (tmp_path / "bad.pdf").write_bytes(b"not a pdf")
    code = main(["rotate", str(tmp_path / "*.pdf"), "-d", "90", "-o", str(tmp_path / "out")])
    assert code == 1  # one file failed...
    out = capsys.readouterr()
    assert "3 of 4 file(s) done" in out.out
    assert "bad.pdf" in out.err and "Traceback" not in out.err
    # ...but the others were processed, keeping their names.
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["a.pdf", "b.pdf", "c.pdf"]
    assert PdfReader(str(tmp_path / "out" / "a.pdf")).pages[0].rotation == 90


def test_cli_batch_default_names(tmp_path):
    make_pdf(tmp_path / "a.pdf")
    make_pdf(tmp_path / "b.pdf")
    assert main(["compress", str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf"), "-l", "low"]) == 0
    assert (tmp_path / "a_compressed.pdf").exists() and (tmp_path / "b_compressed.pdf").exists()


def test_cli_password_from_env_and_output_stays_encrypted(tmp_path, monkeypatch):
    src = make_pdf(tmp_path / "a.pdf", pages=3)
    enc = core.encrypt(src, tmp_path / "enc.pdf", "s3cret")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["rotate", str(enc), "-d", "90", "-o", str(tmp_path / "r.pdf")]) == 1
    monkeypatch.setenv("PDFM_PASSWORD", "s3cret")
    assert main(["rotate", str(enc), "-d", "90", "-o", str(tmp_path / "r.pdf")]) == 0
    reader = PdfReader(str(tmp_path / "r.pdf"))
    assert reader.is_encrypted and reader.decrypt("s3cret")
    assert main(["split", str(enc), "-o", str(tmp_path / "parts")]) == 0
    assert all(PdfReader(str(p)).is_encrypted for p in (tmp_path / "parts").iterdir())
    assert main(["decrypt", str(enc), "-o", str(tmp_path / "plain.pdf")]) == 0
    assert not PdfReader(str(tmp_path / "plain.pdf")).is_encrypted


def test_cli_password_prompt(tmp_path, monkeypatch):
    enc = core.encrypt(make_pdf(tmp_path / "a.pdf"), tmp_path / "enc.pdf", "s3cret")
    answers = iter(["wrong", "s3cret"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    monkeypatch.delenv("PDFM_PASSWORD", raising=False)
    assert main(["info", str(enc)]) == 0


def test_owner_password_only_files_open_without_prompt(tmp_path):
    src = make_pdf(tmp_path / "a.pdf")
    enc = core.encrypt(src, tmp_path / "enc.pdf", "", owner_password="owner")
    assert core.info(enc)["pages"] == 1


def test_cli_compress_and_color_commands(tmp_path, capsys):
    src = photo_pdf(tmp_path / "p.pdf", tmp_path)
    assert main(["compress", str(src), "--level", "high"]) == 0
    assert "smaller" in capsys.readouterr().out
    assert main(["color", str(src), "--to", "gray"]) == 0
    assert (tmp_path / "p_color.pdf").exists()


# -- duplicates and watch --------------------------------------------------

def test_find_duplicates_and_organize_modes(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    make_pdf(lib / "a.pdf", title="Alpha")
    shutil.copy(lib / "a.pdf", lib / "a copy.pdf")
    make_pdf(lib / "b.pdf", pages=2, title="Beta")
    groups = organize.find_duplicates(lib)
    assert [sorted(p.name for p in g) for g in groups] == [["a copy.pdf", "a.pdf"]]

    plan = organize.organize(lib, by="title-letter", dest=tmp_path / "s1", duplicates="skip")
    assert sum(1 for _, d in plan if d is None) == 1
    assert len(list((tmp_path / "s1").rglob("*.pdf"))) == 2

    organize.organize(lib, by="title-letter", dest=tmp_path / "s2", duplicates="folder")
    assert len(list((tmp_path / "s2" / "Duplicates").iterdir())) == 1

    # Running again against the same destination skips everything.
    plan = organize.organize(lib, by="title-letter", dest=tmp_path / "s1", duplicates="skip")
    assert all(d is None for _, d in plan)


def test_cli_duplicates_delete(tmp_path, capsys):
    make_pdf(tmp_path / "a.pdf")
    shutil.copy(tmp_path / "a.pdf", tmp_path / "b.pdf")
    assert main(["duplicates", str(tmp_path), "--delete"]) == 0
    assert len(list(tmp_path.glob("*.pdf"))) == 1


def test_watch_processes_new_files(tmp_path):
    inbox, dest = tmp_path / "inbox", tmp_path / "dest"
    inbox.mkdir()
    photo_pdf(inbox / "scan.pdf", tmp_path)
    settings = watch.WatchSettings(inbox=inbox, dest=dest, by="pages",
                                   rename="{pages}p {stem}", compress="high",
                                   skip_duplicates=True)
    state = watch._State()
    known = set()
    assert watch.poll(settings, state, known, log=lambda m: None) == []  # not yet stable
    done = watch.poll(settings, state, known, log=lambda m: None)
    assert len(done) == 1
    result = done[0][1]
    assert result == dest / "1 page" / "1p scan.pdf"
    assert result.stat().st_size < (inbox / "originals" / "scan.pdf").stat().st_size
    assert not (inbox / "scan.pdf").exists()

    # The same file arriving again is skipped as a duplicate.
    shutil.copy(inbox / "originals" / "scan.pdf", inbox / "again.pdf")
    watch.poll(settings, state, known, log=lambda m: None)
    done = watch.poll(settings, state, known, log=lambda m: None)
    assert done == [(inbox / "again.pdf", None)]


def test_watch_keeps_going_after_a_bad_file(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "bad.pdf").write_bytes(b"junk")
    make_pdf(inbox / "good.pdf")
    settings = watch.WatchSettings(inbox=inbox, dest=tmp_path / "dest", compress="low")
    state, logs = watch._State(), []
    watch.poll(settings, state, log=logs.append)
    done = watch.poll(settings, state, log=logs.append)
    assert [p.name for p, _ in done] == ["good.pdf"]
    assert any("failed: bad.pdf" in m for m in logs)


# -- OCR -------------------------------------------------------------------

def test_ocr(tmp_path):
    pytest.importorskip("ocrmypdf")
    if not shutil.which("tesseract"):
        pytest.skip("tesseract not installed")
    from PIL import ImageDraw, ImageFont

    im = Image.new("L", (1700, 800), 255)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 64)
    except OSError:
        font = ImageFont.load_default(size=64)
    ImageDraw.Draw(im).text((100, 300), "Invoice number 12345", font=font, fill=0)
    im.save(tmp_path / "scan.png", dpi=(200, 200))
    scan = core.images_to_pdf([tmp_path / "scan.png"], tmp_path / "scan.pdf")
    assert core.extract_text(scan).strip() == ""
    out = core.ocr(scan, tmp_path / "ocr.pdf")
    assert "12345" in core.extract_text(out)


def test_ocr_missing_dependency_message(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("ocrmypdf"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(core.PdfManagerError, match="pip install ocrmypdf"):
        core.ocr(make_pdf(tmp_path / "a.pdf"), tmp_path / "b.pdf")


# -- GUI server ------------------------------------------------------------

@pytest.fixture
def server():
    srv, url = gui.make_server()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base, token = url.split("/#token=")
    yield base, token
    srv.shutdown()
    srv.server_close()


def request(url, token=None, data=None, headers=None):
    headers = dict(headers or {})
    if token:
        headers["X-Pdfm-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def multipart(fields, files):
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                 f"{v}\r\n").encode()
    for name, filename, data in files:
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                 f"filename=\"{filename}\"\r\nContent-Type: application/pdf\r\n\r\n").encode()
        body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def test_gui_page_and_security(server):
    base, token = server
    status, headers, body = request(base + "/")
    assert status == 200 and b"PDF Manager" in body
    assert "Content-Security-Policy" in headers
    assert request(base + "/api/operations")[0] == 403
    assert request(base + "/api/operations", "wrong")[0] == 403
    assert request(base + "/", headers={"Host": "evil.example"})[0] == 403
    status, _, body = request(base + "/api/operations", token)
    assert status == 200
    ids = {op["id"] for op in json.loads(body)["operations"]}
    assert {"merge", "compress", "color", "ocr", "organize"} <= ids


def test_gui_run_single_and_batch(server, tmp_path):
    base, token = server
    a = make_pdf(tmp_path / "a.pdf", pages=3).read_bytes()
    b = make_pdf(tmp_path / "b.pdf", pages=2).read_bytes()

    body, headers = multipart({"op": "merge", "bookmarks": "true"},
                              [("files", "a.pdf", a), ("files", "b.pdf", b)])
    status, res_headers, data = request(base + "/api/run", token, body, headers)
    assert status == 200 and res_headers["X-Pdfm-Filename"] == "merged.pdf"
    (tmp_path / "m.pdf").write_bytes(data)
    assert len(PdfReader(str(tmp_path / "m.pdf")).pages) == 5

    # Two files with the same name don't overwrite each other.
    body, headers = multipart({"op": "rotate", "degrees": "90"},
                              [("files", "a.pdf", a), ("files", "a.pdf", b)])
    status, res_headers, data = request(base + "/api/run", token, body, headers)
    assert status == 200 and res_headers["Content-Type"] == "application/zip"
    (tmp_path / "r.zip").write_bytes(data)
    import zipfile
    assert sorted(zipfile.ZipFile(tmp_path / "r.zip").namelist()) == \
        ["a_rotated (2).pdf", "a_rotated.pdf"]

    body, headers = multipart({"op": "extract", "pages": "9"}, [("files", "a.pdf", a)])
    status, _, data = request(base + "/api/run", token, body, headers)
    assert status == 400 and "out of range" in json.loads(data)["error"]

    # Path tricks in the uploaded file name are ignored.
    body, headers = multipart({"op": "reverse"}, [("files", "../../evil.pdf", a)])
    status, res_headers, _ = request(base + "/api/run", token, body, headers)
    assert status == 200 and res_headers["X-Pdfm-Filename"] == "evil_reversed.pdf"


def test_gui_keeps_protected_files_protected(server, tmp_path):
    base, token = server
    enc = core.encrypt(make_pdf(tmp_path / "a.pdf", pages=2), tmp_path / "e.pdf", "pw")
    for op, extra in (("split", {}), ("text", {}), ("rotate", {"degrees": "90"})):
        body, headers = multipart({"op": op, "password": "pw", **extra},
                                  [("files", "e.pdf", enc.read_bytes())])
        status, res_headers, data = request(base + "/api/run", token, body, headers)
        assert status == 200, data
        if op == "rotate":
            (tmp_path / "r.pdf").write_bytes(data)
            assert PdfReader(str(tmp_path / "r.pdf")).is_encrypted


def test_cli_merge_keeps_protection(tmp_path, monkeypatch):
    enc = core.encrypt(make_pdf(tmp_path / "a.pdf"), tmp_path / "e.pdf", "pw")
    monkeypatch.setenv("PDFM_PASSWORD", "pw")
    assert main(["merge", str(enc), str(tmp_path / "a.pdf"), "-o", str(tmp_path / "m.pdf")]) == 0
    assert PdfReader(str(tmp_path / "m.pdf")).is_encrypted


def test_gui_folder_preview_then_apply(server, tmp_path):
    base, token = server
    lib = tmp_path / "lib"
    lib.mkdir()
    make_pdf(lib / "a.pdf")
    shutil.copy(lib / "a.pdf", lib / "b.pdf")
    payload = {"op": "duplicates", "options": {"directory": str(lib), "delete": "true"}}
    headers = {"Content-Type": "application/json"}
    status, _, data = request(base + "/api/folder", token,
                              json.dumps({**payload, "apply": False}).encode(), headers)
    assert status == 200 and len(json.loads(data)["rows"]) == 1
    assert len(list(lib.iterdir())) == 2  # preview changed nothing
    request(base + "/api/folder", token, json.dumps({**payload, "apply": True}).encode(), headers)
    assert len(list(lib.iterdir())) == 1


# -- GUI: thumbnails, desktop window support -------------------------------

def test_gui_inspect_returns_thumbnail_and_pages(server, tmp_path):
    base, token = server
    pdf = make_pdf(tmp_path / "a.pdf", pages=3).read_bytes()
    body, headers = multipart({}, [("file", "a.pdf", pdf)])
    status, _, data = request(base + "/api/inspect", token, body, headers)
    info = json.loads(data)
    assert status == 200 and info["pages"] == 3 and info["thumb"].startswith("data:image/jpeg")

    enc = core.encrypt(tmp_path / "a.pdf", tmp_path / "e.pdf", "pw").read_bytes()
    body, headers = multipart({}, [("file", "e.pdf", enc)])
    info = json.loads(request(base + "/api/inspect", token, body, headers)[2])
    assert info["locked"] and info["pages"] is None
    body, headers = multipart({"password": "pw"}, [("file", "e.pdf", enc)])
    info = json.loads(request(base + "/api/inspect", token, body, headers)[2])
    assert info["pages"] == 3 and info["thumb"]


def test_gui_renders_thumbnails_concurrently_without_crashing(server, tmp_path):
    # pdfium isn't thread-safe; without core.PDFIUM_LOCK this crashes the process.
    base, token = server
    pdf = photo_pdf(tmp_path / "a.pdf", tmp_path).read_bytes()
    body, headers = multipart({}, [("file", "a.pdf", pdf)])
    results = []

    def hit():
        for _ in range(2):
            results.append(json.loads(request(base + "/api/inspect", token, body, headers)[2]))

    threads = [threading.Thread(target=hit) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 32 and all(r["thumb"] for r in results)


def test_gui_csp_allows_eval_only_in_desktop_window():
    for desktop, expected in ((False, False), (True, True)):
        srv, url = gui.make_server(desktop=desktop)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            _, headers, _ = request(url.split("#")[0])
            assert ("'unsafe-eval'" in headers["Content-Security-Policy"]) is expected
        finally:
            srv.shutdown()
            srv.server_close()


def test_gui_keeps_results_for_the_desktop_save_dialog(server, tmp_path, monkeypatch):
    import enum
    import types

    # A stand-in for pywebview, which the test environment doesn't need.
    fake = types.ModuleType("webview")
    fake.FileDialog = enum.IntEnum("FileDialog", {"OPEN": 10, "FOLDER": 20, "SAVE": 30})
    monkeypatch.setitem(sys.modules, "webview", fake)
    base, token = server
    pdf = make_pdf(tmp_path / "a.pdf").read_bytes()
    body, headers = multipart({"op": "reverse"}, [("files", "a.pdf", pdf)])
    _, res_headers, _ = request(base + "/api/run", token, body, headers)
    assert "X-Pdfm-Result-Id" not in res_headers  # browsers download directly

    _, res_headers, data = request(base + "/api/run", token, body, {**headers, "X-Pdfm-Keep": "1"})
    key = res_headers["X-Pdfm-Result-Id"]

    # The desktop API saves that result wherever the Save dialog says.
    cache = gui.ResultCache()
    cache.items[key] = ("a_reversed.pdf", data)
    api = gui.DesktopApi(cache)
    target = tmp_path / "chosen.pdf"

    class FakeWindow:
        def create_file_dialog(self, kind, **kwargs):
            assert kind == fake.FileDialog.SAVE
            assert kwargs["save_filename"] == "a_reversed.pdf"
            return (str(target),)

    api._window = FakeWindow()
    assert api.save_result(key, "a_reversed.pdf") == str(target)
    assert target.read_bytes() == data
    with pytest.raises(ValueError):
        api.save_result("expired", "x.pdf")


def test_result_cache_keeps_only_recent_results():
    cache = gui.ResultCache(keep=2)
    keys = [cache.add(f"{i}.pdf", b"x") for i in range(3)]
    assert cache.get(keys[0]) is None and cache.get(keys[2]) == ("2.pdf", b"x")


def test_cli_gui_falls_back_to_browser(monkeypatch):
    calls = []
    monkeypatch.setattr(gui, "run_desktop", lambda **kw: calls.append("window") or False)
    monkeypatch.setattr(gui, "run_app_window", lambda **kw: calls.append("app window") or False)
    monkeypatch.setattr(gui, "serve", lambda **kw: calls.append("browser"))
    assert main(["gui"]) == 0
    assert calls == ["window", "app window", "browser"]  # each failed -> the next
    calls.clear()
    monkeypatch.setattr(gui, "run_app_window", lambda **kw: calls.append("app window") or True)
    assert main(["gui"]) == 0
    assert calls == ["window", "app window"]
    calls.clear()
    assert main(["gui", "--browser"]) == 0
    assert calls == ["browser"]


class _FakeProc:
    def __init__(self, exit_after=None):
        self.start, self.exit_after = time.monotonic(), exit_after

    def poll(self):
        done = self.exit_after is not None and time.monotonic() - self.start >= self.exit_after
        return 0 if done else None

    def wait(self, timeout=None):
        return 0


def test_wait_until_closed():
    handler = type("H", (), {"last_seen": time.monotonic(), "closed_at": None})
    t = time.monotonic()
    gui.wait_until_closed(handler, idle=0.2, poll=0.05)  # browser tab: no pings -> done
    assert 0.2 <= time.monotonic() - t < 1
    # App window closed after a while: done at once, pings or not.
    handler.last_seen = time.monotonic() + 100
    t = time.monotonic()
    gui.wait_until_closed(handler, _FakeProc(exit_after=5.3), poll=0.05)
    assert time.monotonic() - t < 6
    # Browser handed the window to a running copy: wait for the pings to stop.
    handler.last_seen = time.monotonic()
    t = time.monotonic()
    gui.wait_until_closed(handler, _FakeProc(exit_after=0), idle_handed_off=0.3, poll=0.05)
    assert 0.3 <= time.monotonic() - t < 1


def test_closing_the_window_stops_the_app_even_if_the_browser_lingers():
    """Edge can keep running in the background after its window is closed;
    the page's goodbye is what counts."""
    handler = type("H", (), {"last_seen": time.monotonic(), "closed_at": None})
    lingering = _FakeProc(exit_after=None)  # never exits
    threading.Timer(0.3, lambda: setattr(handler, "closed_at", time.monotonic())).start()
    t = time.monotonic()
    gui.wait_until_closed(handler, lingering, poll=0.05, grace=0.2)
    assert 0.5 <= time.monotonic() - t < 2


def test_a_reload_is_not_closing():
    handler = type("H", (), {"last_seen": time.monotonic(), "closed_at": None})

    def reload():
        handler.closed_at = time.monotonic()
        time.sleep(0.05)
        handler.last_seen = time.monotonic()  # the reloaded page asks for its data

    threading.Timer(0.1, reload).start()
    t = time.monotonic()
    gui.wait_until_closed(handler, poll=0.05, grace=0.2, idle=1.0)
    assert time.monotonic() - t >= 1.0  # ended by the idle timeout, not the goodbye


def test_bye_needs_the_token(server):
    base, token = server
    assert request(base + "/api/bye?token=wrong", data=b"")[0] == 403
    assert request(base + f"/api/bye?token={token}", data=b"")[0] == 200


def test_leftover_browser_is_stopped(tmp_path):
    import subprocess

    profile = tmp_path / "window"
    sleeper = [sys.executable, "-c", "import time; time.sleep(60)", f"--user-data-dir={profile}"]
    ours = subprocess.Popen(sleeper)        # the browser we started, still running
    leftover = subprocess.Popen(sleeper)   # e.g. one Edge kept in the background
    if sys.platform != "win32":
        assert {ours.pid, leftover.pid} <= set(gui._profile_processes(profile))
    t = time.monotonic()
    gui._close_browser(ours, profile)
    assert ours.poll() is not None and time.monotonic() - t < 10
    if sys.platform != "win32":
        assert leftover.wait(timeout=5) is not None
    else:
        leftover.kill()


def test_ping_keeps_the_server_alive(server):
    base, token = server
    status, _, body = request(base + "/api/ping", token, data=b"")
    assert status == 200 and json.loads(body) == {"ok": True}
    assert request(base + "/api/ping", "wrong", data=b"")[0] == 403


def test_window_failure_is_remembered(tmp_path, monkeypatch):
    """When the pywebview window can't open (e.g. Smart App Control blocks
    its engine), the reason is saved and that version isn't tried again."""
    starts = []
    fake = type(sys)("webview")
    fake.create_window = lambda *a, **kw: object()
    fake.start = lambda: starts.append(1) or (_ for _ in ()).throw(OSError("blocked"))
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(gui, "desktop_available", lambda: True)
    monkeypatch.setattr(gui, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(gui, "_pywebview_version", lambda: "6.0")
    assert gui.run_desktop() is False and starts == [1]
    assert (tmp_path / "window-error.txt").read_text().splitlines() == ["6.0", "OSError: blocked"]
    assert gui.run_desktop() is False and starts == [1]  # not tried again
    monkeypatch.setattr(gui, "_pywebview_version", lambda: "6.1")
    assert gui.run_desktop() is False and starts == [1, 1]  # a new version is tried


def test_app_window_uses_edge_or_chrome(tmp_path, monkeypatch):
    launched = []

    class Proc(_FakeProc):
        def __init__(self, args, **kw):
            launched.append(args)
            super().__init__(exit_after=0)

    monkeypatch.setattr(gui, "find_app_browser", lambda: None)
    assert gui.run_app_window() is False
    monkeypatch.setattr(gui, "find_app_browser", lambda: "/opt/edge/msedge")
    monkeypatch.setattr(gui, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(gui.subprocess, "Popen", Proc)
    monkeypatch.setattr(gui, "wait_until_closed", lambda handler, proc: None)
    monkeypatch.setattr(gui, "_profile_processes", lambda profile: [])
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    monkeypatch.setattr(gui, "APP_PORT", probe.getsockname()[1])  # a free port
    probe.close()
    assert gui.run_app_window() is True
    args = launched[0]
    assert args[0] == "/opt/edge/msedge"
    assert args[1].startswith("--app=http://127.0.0.1:") and "#token=" in args[1]
    assert f"--user-data-dir={tmp_path / 'window'}" in args
    # A second copy of the app (the app's port is taken) gets a profile of its own.
    blocker, _ = gui.make_server(prefer=gui.APP_PORT)
    try:
        launched.clear()
        assert gui.run_app_window() is True
        second = next(a for a in launched[0] if a.startswith("--user-data-dir="))
        assert second != f"--user-data-dir={tmp_path / 'window'}" and "window-" in second
        assert not Path(second.split("=", 1)[1]).exists()  # removed afterwards
    finally:
        blocker.server_close()


def test_find_app_browser_on_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(gui.sys, "platform", "linux")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert gui.find_app_browser() is None
    chrome = tmp_path / "google-chrome"
    chrome.write_text("#!/bin/sh\n")
    chrome.chmod(0o755)
    assert gui.find_app_browser() == str(chrome)


def test_organize_puts_protected_files_in_their_own_folder(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    core.encrypt(make_pdf(tmp_path / "a.pdf"), lib / "secret.pdf", "pw")
    plan = organize.organize(lib, by="pages", dry_run=True)
    assert plan[0][1].parent.name == "Password protected"


# -- PDF reader ----------------------------------------------------------------

def raw_get(base, path, headers=None):
    """GET a path exactly as written (urllib would not normalise it either,
    but http.client makes that explicit)."""
    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(base)
    conn = http.client.HTTPConnection(parts.hostname, parts.port)
    conn.request("GET", path, headers=headers or {})
    res = conn.getresponse()
    body = res.read()
    conn.close()
    return res.status, dict(res.getheaders()), body


def test_reader_static_files_are_served(server):
    base, _ = server
    for path, ctype in (("/static/reader.js", "text/javascript"),
                        ("/static/pages.js", "text/javascript"),
                        ("/static/regions.js", "text/javascript"),
                        ("/static/bookmarks.js", "text/javascript"),
                        ("/static/recipes.js", "text/javascript"),
                        ("/static/pdfjs.js", "text/javascript"),
                        ("/static/reader.css", "text/css"),
                        ("/static/pdfjs/build/pdf.min.mjs", "text/javascript"),
                        ("/static/pdfjs/build/pdf.worker.min.mjs", "text/javascript"),
                        ("/static/pdfjs/web/pdf_viewer.css", "text/css"),
                        ("/static/pdfjs/wasm/openjpeg.wasm", "application/wasm"),
                        ("/static/pdfjs/cmaps/UniJIS-UCS2-H.bcmap", "application/octet-stream")):
        status, headers, body = raw_get(base, path)
        assert status == 200 and headers["Content-Type"] == ctype and body, path
        assert headers["Cache-Control"] == "no-cache" and headers["ETag"]


@pytest.mark.parametrize("path", [
    "/static/../gui.py", "/static/pdfjs/../../gui.py", "/static/%2e%2e/gui.py",
    "/static//etc/passwd", "/static/pdfjs/..\\..\\gui.py", "/static/C:/Windows/win.ini",
    "/static/missing.js", "/static/",
])
def test_reader_static_files_refuse_other_paths(server, path):
    base, _ = server
    status, _, body = raw_get(base, path)
    assert status == 404 and b"import" not in body


def test_page_csp_allows_pdfjs_but_nothing_else():
    csp = gui._page_csp(False)
    for needed in ("worker-src 'self' blob:", "'wasm-unsafe-eval'", "font-src 'self' data:",
                   "object-src 'none'", "frame-ancestors 'none'"):
        assert needed in csp
    assert "'unsafe-eval'" not in csp and "*" not in csp
    assert "'unsafe-eval'" in gui._page_csp(True)


def test_reader_opens_files_given_on_the_command_line(tmp_path):
    pdf = make_pdf(tmp_path / "book.pdf", pages=2)
    srv, url = gui.make_server(open_paths=[pdf])
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base, fragment = url.split("/#")
        params = dict(p.split("=", 1) for p in fragment.split("&"))
        status, headers, body = request(f"{base}/api/file/{params['open']}", params["token"])
        assert status == 200 and body == pdf.read_bytes()
        assert headers["X-Pdfm-Filename"] == "book.pdf"
        assert request(f"{base}/api/file/{params['open']}")[0] == 403  # needs the token
        assert request(f"{base}/api/file/unknown", params["token"])[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()


def test_open_files_only_registers_existing_files(tmp_path):
    files = gui.OpenFiles()
    with pytest.raises(FileNotFoundError):
        files.add(tmp_path / "missing.pdf")
    key = files.add(make_pdf(tmp_path / "a.pdf"))
    assert files.get(key) == (tmp_path / "a.pdf").resolve() and files.get("other") is None


def test_reader_save_copy_and_open_dialog_in_desktop_window(tmp_path, monkeypatch):
    import enum
    import types

    fake = types.ModuleType("webview")
    fake.FileDialog = enum.IntEnum("FileDialog", {"OPEN": 10, "FOLDER": 20, "SAVE": 30})
    monkeypatch.setitem(sys.modules, "webview", fake)
    srv, url = gui.make_server(desktop=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    pdf = make_pdf(tmp_path / "a.pdf").read_bytes()

    # "Save a copy": the page hands the bytes to the server, then Python saves them.
    body, headers = multipart({}, [("file", "a (filled).pdf", pdf)])
    status, _, data = request(base + "/api/keep", token, body, headers)
    key = json.loads(data)["id"]
    target = tmp_path / "copy.pdf"
    seen = []

    class FakeWindow:
        def create_file_dialog(self, kind, **kwargs):
            seen.append((kind, kwargs.get("save_filename") or kwargs.get("file_types")))
            return (str(target),) if kind == fake.FileDialog.SAVE else (str(tmp_path / "a.pdf"),)

    handler = srv.RequestHandlerClass
    api = gui.DesktopApi(handler.results, handler.opened)
    api._window = FakeWindow()
    assert api.save_result(key, "a (filled).pdf") == str(target) and target.read_bytes() == pdf

    # "Open a PDF": the native dialog's choice becomes an id the page can load.
    picked = api.open_pdf()
    assert picked["name"] == "a.pdf"
    assert request(f"{base}/api/file/{picked['id']}", token)[2] == pdf
    assert seen[0] == (fake.FileDialog.SAVE, "a (filled).pdf")
    assert seen[1][0] == fake.FileDialog.OPEN and "PDF files (*.pdf)" in seen[1][1]
    srv.shutdown()
    srv.server_close()


def test_cli_view_opens_files_in_the_reader(tmp_path, monkeypatch, capsys):
    pdf = make_pdf(tmp_path / "a.pdf")
    calls = []
    monkeypatch.setattr(gui, "run_desktop", lambda **kw: calls.append(kw["open_paths"]) or True)
    assert main(["view", str(pdf)]) == 0
    assert calls == [[str(pdf)]]
    assert main(["view", str(tmp_path / "missing.pdf")]) == 1
    assert "no such file" in capsys.readouterr().err


def test_app_launcher_passes_pdfs_to_the_reader(tmp_path, monkeypatch):
    pdf = make_pdf(tmp_path / "a.pdf")
    (tmp_path / "notes.txt").write_text("x")
    calls = []
    monkeypatch.setattr(gui, "run_desktop", lambda **kw: calls.append(kw["open_paths"]) or True)
    gui.main_app([str(pdf), str(tmp_path / "notes.txt"), "--some-flag", str(tmp_path / "gone.pdf")])
    assert calls == [[str(pdf)]]


def test_reader_is_listed_first(server):
    base, token = server
    ops = json.loads(request(base + "/api/operations", token)[2])["operations"]
    assert ops[0]["id"] == "reader" and ops[0]["files"] == "reader"


def test_page_tools_have_page_previews():
    """Every page tool shows the page board, and the board writes into a
    field the tool actually has."""
    from pdfmanager import gui

    for op in gui.OPERATIONS:
        if op["group"] != "Pages" or op.get("panel"):  # bookmarks have their own editor
            continue
        board = op.get("board")
        assert board and board["mode"] in ("order", "select", "view", "split"), op["id"]
        keys = {f["key"] for f in op["fields"]}
        for ref in ("field", "degrees"):
            if ref in board:
                assert board[ref] in keys, (op["id"], ref)


def test_static_files_are_revalidated_by_etag(server):
    import http.client

    base, _ = server
    host = base.split("//")[1]
    conn = http.client.HTTPConnection(host)
    conn.request("GET", "/static/pdfjs/build/pdf.worker.min.mjs")
    first = conn.getresponse()
    first.read()
    etag = first.getheader("ETag")
    # Same connection (keep-alive): the browser asks whether its copy is current.
    conn.request("GET", "/static/pdfjs/build/pdf.worker.min.mjs", headers={"If-None-Match": etag})
    again = conn.getresponse()
    assert again.status == 304 and again.read() == b""
    conn.close()


def test_preferred_port():
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free = probe.getsockname()[1]
    probe.close()
    # The preferred port is used when it's free...
    srv, url = gui.make_server(prefer=free)
    port = srv.server_address[1]
    try:
        assert port == free and url.startswith(f"http://127.0.0.1:{free}/#token=")
        # ...and any free port when it's taken (e.g. by a second copy of the app).
        taken, taken_url = gui.make_server(prefer=port)
        assert taken.server_address[1] != port
        assert f"127.0.0.1:{taken.server_address[1]}" in taken.RequestHandlerClass.allowed_hosts
        taken.server_close()
    finally:
        srv.server_close()
