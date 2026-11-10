"""The reader's file panel (gui.html, static/reader.js): the shown file's
name, facts and suggestions; a tool opened and run right there, on the open
file by its id or with its bytes; failures and results that aren't PDFs; the
T key; a narrow window. In a browser through Node's Playwright (skipped where
there is none), against a real server with its own data folder (conftest)."""
import io
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pikepdf
import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from pdfmanager import gui

HERE = Path(__file__).resolve().parent
GUI_HTML = HERE.parent / "pdfmanager" / "gui.html"
STATIC = HERE.parent / "pdfmanager" / "static"


def scan_pdf(path, pages=2):
    """Pages that are one full-page picture, with no text."""
    buf = io.BytesIO()
    Image.new("L", (1240, 1754), 235).save(buf, "JPEG")
    pdf = pikepdf.new()
    name = pikepdf.Name
    for _ in range(pages):
        picture = pikepdf.Stream(pdf, buf.getvalue(), Type=name.XObject, Subtype=name.Image, Width=1240,
                                 Height=1754, ColorSpace=name.DeviceGray, BitsPerComponent=8, Filter=name.DCTDecode)
        page = pdf.add_blank_page(page_size=(595, 842))
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im=picture))
        page.Contents = pdf.make_stream(b"q 595 0 0 842 0 0 cm /Im Do Q")
    pdf.save(path)
    return Path(path)


def text_pdf(path, pages=3):
    c = canvas.Canvas(str(path))
    for n in range(pages):
        c.drawString(72, 720, f"Quarterly report, page {n + 1}")
        c.showPage()
    c.save()
    return Path(path)


def test_the_common_tools_are_real_tools_and_the_tray_is_gone():
    html = GUI_HTML.read_text(encoding="utf-8")
    common = re.search(r"const COMMON_TOOLS = \[(.*?)\];", html, re.S).group(1)
    ids = re.findall(r'\["([a-z-]+)", "', common)
    assert ids == ["split", "sign-cert", "to-word", "pdfa", "to-excel", "encrypt"]
    assert set(ids) <= {op["id"] for op in gui.OPERATIONS}
    for name in ("reader.js", "reader.css"):
        assert "rd-tray" not in (STATIC / name).read_text(encoding="utf-8")


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def test_the_file_panel_in_a_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = app
    opened = srv.RequestHandlerClass.opened
    scan_id = opened.add(scan_pdf(tmp_path / "invoice-scan.pdf", pages=3))
    plain_id = opened.add(text_pdf(tmp_path / "Quarterly report.pdf"))
    upload = text_pdf(tmp_path / "upload copy.pdf", pages=2)
    from test_certsign import PFX_PASSWORD, make_id  # a digital ID, for the panel's file and password fields
    pfx = make_id(tmp_path / "jane.pfx")[0]
    env = {**os.environ, "PFX": str(pfx), "PFX_PASSWORD": PFX_PASSWORD}
    run = subprocess.run([node, str(HERE / "filepanel.js"), url, scan_id, plain_id, str(upload)],
                         capture_output=True, text=True, encoding="utf-8", timeout=420, env=env)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"]

    # (a) A scanned file: its name, the flag, one suggestion; a labelled region.
    scan = out["scan"]
    assert scan["visible"] and not scan["overlay"]
    assert (scan["role"], scan["label"]) == ("region", "This file")
    assert scan["button"] == ["File panel", "true"]
    assert scan["name"] == "invoice-scan.pdf"
    assert scan["chips"][0] == "3 pages" and scan["chips"][-1] == "scanned, no text yet"
    assert scan["flagged"] == ["scanned, no text yet"]
    assert scan["headings"] == ["Suggested for this file", "Or do something else"]
    assert scan["suggestions"] == ["Make it searchable"]
    assert [c[0] for c in scan["common"]] == ["split", "sign-cert", "to-word", "pdfa", "to-excel", "encrypt"]
    assert scan["common"][0][2] is None                    # Split hands over to its page (it has a board)
    assert all(c[2] == "false" for c in scan["common"][1:])  # the others open in place
    assert scan["all"] == f"All {len(gui.OPERATIONS)} tools"
    # The suggestion opens to its options (focus goes in) and closes (focus comes back).
    assert out["ocrOpen"]["expanded"] == "true" and out["ocrOpen"]["run"] and out["ocrOpen"]["focusInside"]
    assert "engine" in out["ocrOpen"]["fields"]
    assert out["ocrClosed"] == ["false", True]
    assert out["tabIntoPanel"]
    # A plain text PDF: no suggestions, and no heading for them.
    assert out["plain"]["name"] == "Quarterly report.pdf"
    assert out["plain"]["headings"] == ["Or do something else"] and out["plain"]["suggestions"] == []

    # (b) From the palette into the panel; Run opens a second tab beside the original.
    assert out["chosen"]["expanded"] == "true" and out["chosen"]["focusInside"]
    after = out["afterRun"]
    assert after["tabs"] == [["Quarterly report.pdf", False], ["Quarterly report_numbered.pdf", True]]
    assert after["toast"] == "Opened Quarterly report_numbered.pdf in a new tab"
    assert after["panel"]["name"] == "Quarterly report_numbered.pdf"
    assert "Made from Quarterly report.pdf" in after["note"] and "→" in after["note"]
    # Opened by id and not changed: the id went, not the file.
    assert ["ids", "text"] in after["fields"] and not any(k == "files" for k, _ in after["fields"])
    assert 'name="ids"' in after["postData"] and plain_id in after["postData"]
    assert 'name="files"' not in after["postData"] and "filename=" not in after["postData"]

    # (f) A failure: the server's message in the panel, the options kept.
    assert out["encryptFocus"]
    assert out["failure"]["text"] == "Enter a password to open it, or choose what to limit"
    assert out["failure"]["optionsKept"] and out["failure"]["tabs"] == 2

    # (c) Opened from bytes: the file goes with the request. (g) Not a PDF: Save, no new tab.
    assert out["uploaded"]["tabs"][-1] == ["upload copy.pdf", True]
    word = out["word"]
    assert ["files", "file"] in word["fields"] and not any(k == "ids" for k, _ in word["fields"])
    assert not word["error"], word["error"]
    assert word["done"].startswith("upload copy.docx")
    assert word["save"] and word["save"][0].startswith("Save")
    assert word["tabs"] == 3

    # A tool with a file field (the digital ID) and a password field, on the original by its id.
    signed = out["signed"]
    assert not signed["error"], signed["error"]
    assert signed["tabs"][1] == ["Quarterly report_signed.pdf", True] and signed["tabs"][0][0] == "Quarterly report.pdf"
    assert ["pfx", "file"] in signed["fields"] and ["pfx_password", "text"] in signed["fields"]
    assert ["ids", "text"] in signed["fields"] and ["files", "file"] not in signed["fields"]

    # (d) T closes and opens the panel, and a reload keeps the choice.
    assert not out["tCloses"]["visible"] and out["tCloses"]["button"][1] == "false"
    assert not out["afterReloadClosed"]["visible"]
    assert out["tOpens"]["visible"] and out["afterReloadOpen"]["visible"]

    # (e) 1000 px: closed at first, over the page with a scrim, Escape or the scrim closes it.
    assert not out["narrowStart"]["visible"]
    assert out["narrowOpen"]["visible"] and out["narrowOpen"]["overlay"] and out["narrowOpen"]["scrim"]
    assert out["narrowFocusInside"]
    assert not out["narrowEscape"]["visible"] and not out["narrowEscape"]["scrim"]
    assert not out["narrowScrim"]["visible"]

    # (h) Nothing beyond this computer; nothing scrolls sideways; the toolbar fits.
    hosts = {urlsplit(u).hostname for u in out["requests"] if not u.startswith(("data:", "blob:"))}
    assert hosts == {"127.0.0.1"}, hosts
    for key in ("scrollWidth1280", "scrollWidth1000"):
        width, viewport = out[key]
        assert width == viewport, (key, width, viewport)
    assert out["toolbarFits"][0] <= out["toolbarFits"][1]
