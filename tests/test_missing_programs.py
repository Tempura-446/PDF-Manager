"""The programs ZPDF can use but doesn't bring: Microsoft Word (through
PowerShell), LibreOffice, a Chromium browser, Tesseract with OCRmyPDF,
Ghostscript, the built-in OCR engine and Windows Image Acquisition.

Each is made unavailable here (its finder says no, its package doesn't
import, the PATH is empty) and the tools that use it are run the way the
app runs them: through the server's /api/run and /api/scan. Each must
either work another way (LibreOffice when Word is missing, the built-in
OCR engine when Tesseract is, ...) or stop with a plain message that names
what's missing and what to install or do instead: never a traceback, never
"Something went wrong", never a raw "[Errno 2] ..."."""
import importlib.machinery
import io
import json
import shutil
import sys
import threading
import types
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pikepdf
import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from pdfmanager import archive, gui, ocr, office, pdfafix, scan, scanner

GS_LINK = "https://ghostscript.com/releases/gsdnld.html"
LO_LINK = "https://www.libreoffice.org"


# -- what's on this computer ---------------------------------------------------------------

@pytest.fixture
def bare(tmp_path, monkeypatch):
    """A computer with none of the optional programs: an empty PATH, and
    every finder ZPDF has says no. Tests put back what they need."""
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(office, "_has_com", lambda prog_id: False)   # no Word, Excel, PowerPoint
    monkeypatch.setattr(office, "_word_stalled", False)
    monkeypatch.setattr(office, "find_libreoffice", lambda: None)
    monkeypatch.setattr(office, "find_browser", lambda: None)
    monkeypatch.setattr(archive, "find_ghostscript", lambda: None)
    monkeypatch.setattr(scan, "find_tesseract", lambda: None)
    monkeypatch.setattr(ocr, "_builtin_imports", False)             # rapidocr/onnxruntime missing
    monkeypatch.setitem(sys.modules, "ocrmypdf", None)              # `import ocrmypdf` fails
    monkeypatch.setitem(sys.modules, "ocrmypdf.exceptions", None)
    scanner.use(None)
    yield tmp_path
    scanner.use(None)


@pytest.fixture
def app(bare):
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    yield base, token
    srv.shutdown()
    srv.server_close()


def _call(app, path, body=None, headers=None):
    base, token = app
    request = urllib.request.Request(base + path, data=body, headers={"X-Pdfm-Token": token, **(headers or {})},
                                     method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=120) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def run_tool(app, op, files=(), **fields):
    """Run ``op`` as the app does. (status, message or None, notes, data)."""
    boundary = "zpdfmissingprograms"
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in {"op": op, **fields}.items()]
    for path in files:
        path = Path(path)
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{path.name}"'
                     f'\r\nContent-Type: application/octet-stream\r\n\r\n'.encode() + path.read_bytes() + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    status, headers, data = _call(app, "/api/run", body,
                                  {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    if status != 200:
        return status, json.loads(data).get("error", ""), [], None
    notes = urllib.parse.unquote(headers.get("X-Pdfm-Note", "")).splitlines()
    return status, None, notes, data


def assert_plain(message, *words):
    """A message for a person: no traceback or error codes, and it names
    ``words`` (what's missing, what to do)."""
    assert message, "no message at all"
    for bad in ("Traceback", "Errno", "Something went wrong", "Error:", "Exception", "NoneType",
                "object has no attribute", "KeyError"):
        assert bad not in message, f"{bad!r} in {message!r}"
    for word in words:
        assert word.lower() in message.lower(), f"{word!r} not in {message!r}"


def assert_stopped(result, *words):
    status, message, _, _ = result
    assert status == 400, f"expected a plain stop, got {status}: {message}"
    assert_plain(message, *words)


# -- files ---------------------------------------------------------------------------------

def text_pdf(path, text="Invoice 42: total 176.16"):
    c = canvas.Canvas(str(path))
    c.setFont("Helvetica", 14)
    c.drawString(72, 720, text)
    c.save()
    return Path(path)


def picture_pdf(path):
    """A page that is one picture, as a scan is: no fonts."""
    buf = io.BytesIO()
    Image.new("RGB", (400, 560), (200, 60, 40)).save(buf, "JPEG")
    pdf = pikepdf.new()
    name = pikepdf.Name
    picture = pikepdf.Stream(pdf, buf.getvalue(), Type=name.XObject, Subtype=name.Image, Width=400, Height=560,
                             ColorSpace=name.DeviceRGB, BitsPerComponent=8, Filter=name.DCTDecode)
    page = pdf.add_blank_page(page_size=(400, 560))
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im=picture))
    page.Contents = pdf.make_stream(b"q 400 0 0 560 0 0 cm /Im Do Q")
    pdf.save(path)
    return Path(path)


def docx_file(tmp_path):
    from docx import Document

    doc = Document()
    doc.add_paragraph("Hello from a Word document")
    doc.save(tmp_path / "letter.docx")
    return tmp_path / "letter.docx"


def html_file(tmp_path):
    path = tmp_path / "page.html"
    path.write_text("<html><body><h1>Hello</h1><p>A saved web page.</p></body></html>", encoding="utf-8")
    return path


def fake_libreoffice(monkeypatch, made):
    """LibreOffice "installed": its conversions write a small PDF where it would."""
    monkeypatch.setattr(office, "find_libreoffice", lambda: "/opt/libreoffice/program/soffice")

    def run(command, env=None, what="", timeout=None):
        outdir = Path(command[command.index("--outdir") + 1])
        outdir.mkdir(parents=True, exist_ok=True)
        text_pdf(outdir / (Path(command[-1]).stem + ".pdf"), "converted")
        made.append(command)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(office, "_run", run)


# -- Office -> PDF ---------------------------------------------------------------------------

def test_office_to_pdf_without_office_or_libreoffice_says_what_to_install(app, bare):
    pytest.importorskip("docx")
    assert_stopped(run_tool(app, "from-office", [docx_file(bare)]),
                   "Microsoft Office or LibreOffice", "install LibreOffice", LO_LINK)


def test_office_to_pdf_uses_libreoffice_when_word_is_missing(app, bare, monkeypatch):
    pytest.importorskip("docx")
    made = []
    fake_libreoffice(monkeypatch, made)
    status, message, notes, data = run_tool(app, "from-office", [docx_file(bare)])
    assert status == 200, message
    assert data.startswith(b"%PDF") and len(made) == 1
    assert notes == ["letter.docx: converted with LibreOffice"]


def test_a_text_file_needs_neither(app, bare):
    (bare / "notes.txt").write_text("Plain words\n", encoding="utf-8")
    status, message, notes, data = run_tool(app, "from-office", [bare / "notes.txt"])
    assert status == 200 and data.startswith(b"%PDF"), message
    assert notes == ["notes.txt: converted with ZPDF"]


# -- PDF -> Word ------------------------------------------------------------------------------

def test_microsoft_word_only_without_word_says_what_to_choose(app, bare):
    assert_stopped(run_tool(app, "to-word", [text_pdf(bare / "a.pdf")], engine="word"),
                   "Microsoft Word isn't installed", "Built-in converter")


def test_best_available_without_word_uses_the_built_in_converter(app, bare):
    status, message, notes, data = run_tool(app, "to-word", [text_pdf(bare / "a.pdf")], engine="auto")
    assert status == 200 and data[:2] == b"PK", message
    assert any("built-in converter" in n and "install Microsoft Word" in n for n in notes)


def test_word_that_cant_be_started_falls_back_without_an_error_code(app, bare, monkeypatch):
    """Word is registered but PowerShell, which starts it, isn't there."""
    monkeypatch.setattr(office, "has_word", lambda: True)
    monkeypatch.setattr(office, "powershell_exe", lambda: str(bare / "System32" / "powershell.exe"))
    status, message, notes, data = run_tool(app, "to-word", [text_pdf(bare / "a.pdf")], engine="auto")
    assert status == 200 and data[:2] == b"PK", message
    assert len(notes) == 1
    assert_plain(notes[0], "Microsoft Word could not start", "wasn't found", "built-in converter")


def test_exact_layout_without_libreoffice_says_what_to_do(app, bare):
    assert_stopped(run_tool(app, "to-word", [text_pdf(bare / "a.pdf")], engine="layout"),
                   "Exact layout needs LibreOffice", LO_LINK, "Built-in converter")


# -- web page -> PDF ---------------------------------------------------------------------------

def test_a_saved_web_page_without_a_browser_says_what_to_install(app, bare):
    assert_stopped(run_tool(app, "web", [html_file(bare)]),
                   "Microsoft Edge, Google Chrome or Chromium", "install", "LibreOffice")


def test_a_typed_address_without_a_browser_says_what_to_install(app, bare):
    """(It used to stop with a KeyError: a typed address has no files.)"""
    result = run_tool(app, "web", url="https://example.com/page")
    assert_stopped(result, "Microsoft Edge, Google Chrome or Chromium", "install")
    assert "LibreOffice" not in result[1]


def test_a_saved_web_page_without_a_browser_uses_libreoffice(app, bare, monkeypatch):
    made = []
    fake_libreoffice(monkeypatch, made)
    status, message, notes, data = run_tool(app, "web", [html_file(bare)])
    assert status == 200 and data.startswith(b"%PDF"), message
    assert notes == ["page.html: printed with LibreOffice"] and len(made) == 1


def test_a_browser_that_is_gone_says_so_plainly(app, bare, monkeypatch):
    monkeypatch.setattr(office, "find_browser", lambda: str(bare / "uninstalled" / "chrome"))
    assert_stopped(run_tool(app, "web", [html_file(bare)]),
                   "the browser could not start", "wasn't found", "Reinstall")


# -- OCR -----------------------------------------------------------------------------------------

def _fake_engine(monkeypatch, name, used):
    """An engine that "reads" by copying the file (the real ones are tested elsewhere)."""
    def run(source, output, *args, **kwargs):
        shutil.copyfile(source, output)
        used.append(name)

    monkeypatch.setattr(ocr, f"_run_{name}", run)


def test_ocr_without_any_engine_says_what_to_install(app, bare):
    assert_stopped(run_tool(app, "ocr", [picture_pdf(bare / "scan.pdf")], engine="auto", language="eng"),
                   "neither is installed", "reinstall ZPDF", "Tesseract", ocr.TESSERACT_LINK)


def test_ocr_built_in_without_it_says_what_to_do(app, bare):
    assert_stopped(run_tool(app, "ocr", [picture_pdf(bare / "scan.pdf")], engine="builtin", language="eng"),
                   "built-in engine isn't installed", "reinstall ZPDF")


def test_ocr_tesseract_without_ocrmypdf_says_what_to_do(app, bare):
    assert_stopped(run_tool(app, "ocr", [picture_pdf(bare / "scan.pdf")], engine="tesseract", language="eng"),
                   "needs OCRmyPDF", "pip install pdf-manager[tesseract]", "Tesseract program",
                   ocr.TESSERACT_LINK)


def test_ocr_tesseract_without_the_program_says_what_to_install(app, bare, monkeypatch):
    fake = types.ModuleType("ocrmypdf")
    fake.__spec__ = importlib.machinery.ModuleSpec("ocrmypdf", None)
    monkeypatch.setitem(sys.modules, "ocrmypdf", fake)
    monkeypatch.setattr(ocr, "_builtin_imports", True)
    result = run_tool(app, "ocr", [picture_pdf(bare / "scan.pdf")], engine="tesseract", language="eng")
    assert_stopped(result, "needs the Tesseract program", ocr.TESSERACT_LINK, "choose the Built-in engine")
    assert "OCRmyPDF" not in result[1]


def test_ocr_that_misses_ghostscript_names_it(app, bare, monkeypatch):
    """An OCRmyPDF that wants Ghostscript (older versions do) and can't find it."""
    fake = types.ModuleType("ocrmypdf")
    exceptions = types.ModuleType("ocrmypdf.exceptions")
    exceptions.MissingDependencyError = type("MissingDependencyError", (Exception,), {})
    exceptions.ExitCodeException = type("ExitCodeException", (Exception,), {})

    def needs_gs(*args, **kwargs):
        raise exceptions.MissingDependencyError("Could not find program 'gs' on the PATH")

    fake.ocr = needs_gs
    monkeypatch.setitem(sys.modules, "ocrmypdf", fake)
    monkeypatch.setitem(sys.modules, "ocrmypdf.exceptions", exceptions)
    monkeypatch.setattr(ocr, "tesseract_available", lambda: True)
    monkeypatch.setattr(ocr, "_tesseract_languages", lambda: set())
    assert_stopped(run_tool(app, "ocr", [picture_pdf(bare / "scan.pdf")], engine="tesseract", language="eng"),
                   "Ghostscript", "install it", GS_LINK)


def test_ocr_automatic_uses_the_built_in_engine_when_tesseract_is_missing(app, bare, monkeypatch):
    used = []
    monkeypatch.setattr(ocr, "_builtin_imports", True)
    _fake_engine(monkeypatch, "builtin", used)
    status, message, _, data = run_tool(app, "ocr", [picture_pdf(bare / "scan.pdf")], engine="auto",
                                        language="eng")
    assert status == 200 and data.startswith(b"%PDF"), message
    assert used == ["builtin"]


def test_ocr_automatic_uses_tesseract_when_the_built_in_engine_is_missing(app, bare, monkeypatch):
    used = []
    monkeypatch.setattr(ocr, "tesseract_available", lambda: True)
    _fake_engine(monkeypatch, "tesseract", used)
    status, message, _, data = run_tool(app, "ocr", [picture_pdf(bare / "scan.pdf")], engine="auto",
                                        language="eng")
    assert status == 200 and data.startswith(b"%PDF"), message
    assert used == ["tesseract"]


def test_turning_pages_upright_without_ocr_says_what_to_do(app, bare):
    assert_stopped(run_tool(app, "clean", [picture_pdf(bare / "scan.pdf")], auto_rotate="true"),
                   "built-in OCR engine or the Tesseract program", "untick")


# -- PDF/A -----------------------------------------------------------------------------------------

def test_pdfa_needs_no_ghostscript_for_what_zpdf_can_fix(app, bare):
    status, message, notes, data = run_tool(app, "pdfa", [picture_pdf(bare / "scan.pdf")], level="2")
    assert status == 200 and data.startswith(b"%PDF"), message


def test_pdfa_without_ghostscript_for_what_zpdf_cant_fix_says_what_to_install(app, bare, monkeypatch):
    monkeypatch.setattr(pdfafix, "stand_in", lambda name: None)   # no font to embed in its place
    assert_stopped(run_tool(app, "pdfa", [text_pdf(bare / "a.pdf")], level="2"),
                   "can't be made PDF/A-2b", "Helvetica", "Ghostscript", "install it", GS_LINK)


def test_pdfa_with_ghostscript_by_name_says_what_to_do(bare, capsys):
    from pdfmanager.cli import main

    code = main(["pdfa", str(picture_pdf(bare / "scan.pdf")), "--engine", "ghostscript",
                 "-o", str(bare / "out.pdf")])
    said = capsys.readouterr().err
    assert code != 0
    assert_plain(said, "Ghostscript program, which isn't installed", GS_LINK)


# -- Scan -------------------------------------------------------------------------------------------

class _ComWithout:
    """A COM layer (scanner.Com's shape) on a PC where WIA doesn't work."""

    def __init__(self, failure):
        self.failure = failure

    def apartment(self):
        import contextlib

        return contextlib.nullcontext()

    def create(self, progid):
        raise self.failure

    def hresult(self, exc):
        return None


def test_scan_status_off_windows_says_why(app, bare, monkeypatch):
    monkeypatch.setattr(scanner, "sys", types.SimpleNamespace(platform="linux", modules=sys.modules))
    status, _, data = _call(app, "/api/scan/status")
    state = json.loads(data)
    assert status == 200 and state["scanner"]["available"] is False
    assert_plain(state["scanner"]["reason"], "Windows only")
    # Without OCR, Make PDF says it can't read the pages (the page shows that).
    assert state["ocr"] is None and state["upright"] is False
    status, _, data = _call(app, "/api/scan/devices")
    assert status == 400
    assert_plain(json.loads(data)["error"], "Windows only")


def test_scan_status_without_pythonnet_says_why(app, bare, monkeypatch):
    monkeypatch.setattr(scanner, "sys", types.SimpleNamespace(platform="win32", modules=sys.modules))
    monkeypatch.setitem(sys.modules, "clr", None)   # pythonnet missing: `import clr` fails
    status, _, data = _call(app, "/api/scan/status")
    state = json.loads(data)["scanner"]
    assert status == 200 and state["available"] is False
    assert_plain(state["reason"], "Windows Image Acquisition", "Reinstall ZPDF", "Photos")
    status, _, data = _call(app, "/api/scan/devices")
    assert status == 400
    assert_plain(json.loads(data)["error"], "Windows Image Acquisition")


def test_scan_status_without_wia_says_why(app, bare):
    scanner.use(_ComWithout(scanner.ScannerError(
        "Windows Image Acquisition isn't installed on this PC, so ZPDF can't talk to scanners.",
        "unavailable")))
    state = json.loads(_call(app, "/api/scan/status")[2])["scanner"]
    assert state["available"] is False
    assert_plain(state["reason"], "Windows Image Acquisition isn't installed")


def test_scan_status_with_wia_stopped_says_what_to_do(app, bare):
    scanner.use(_ComWithout(OSError("The service has not been started")))
    state = json.loads(_call(app, "/api/scan/status")[2])["scanner"]
    assert state["available"] is False
    assert_plain(state["reason"], "Windows Image Acquisition isn't working",
                 "service is running")


# -- every tool, with nothing installed ---------------------------------------------------------

def test_every_pdf_tool_works_or_says_why_with_nothing_installed(app, bare):
    """No optional program and an empty PATH: each tool on an ordinary PDF
    works, or stops with a message; none fails unexpectedly."""
    import test_robustness

    src = text_pdf(bare / "a.pdf")
    other = text_pdf(bare / "b.pdf", "Another page")
    problems = []
    for op in test_robustness.TOOLS:
        if op["id"] == "recipe":
            continue  # runs other tools (covered one by one)
        opts = test_robustness._options(op)
        if op["id"] == "to-word":
            opts["engine"] = "auto"
        files = [src, other] if op.get("files") == "pdf2" or op.get("min", 1) >= 2 else [src]
        status, message, _, _ = run_tool(app, op["id"], files, **opts)
        if status not in (200, 400):
            problems.append(f"{op['id']}: {status} {message}")
        elif status == 400:
            try:
                assert_plain(message)
            except AssertionError as exc:
                problems.append(f"{op['id']}: {exc}")
    assert not problems, "\n".join(problems)
