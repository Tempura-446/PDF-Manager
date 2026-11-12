"""The start screen and the rail (gui.html): the facts line under a recent
file, and the page in a browser (Node's Playwright, skipped where there is
none): five groups on the rail, every tool reachable, the palette, no
requests beyond this computer, nothing scrolling sideways."""
import io
import json
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pikepdf
import pytest
from PIL import Image

from pdfmanager import gui

HERE = Path(__file__).resolve().parent
GUI_HTML = HERE.parent / "pdfmanager" / "gui.html"


def _function(source, name):
    """The text of ``function name(...) {...}`` in ``source``."""
    start = source.index(f"function {name}(")
    depth, i = 0, source.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        i += 1
        if depth == 0:
            return source[start:i]


def _node():
    node = shutil.which("node")
    if not node:
        pytest.skip("needs Node.js")
    return node


FACTS = [
    ({"size": 4.1 * 1048576, "pages": 12, "protected": False, "scanned": True, "has_text": False,
      "form_fields": 0, "signatures": 0, "tagged": False, "pdfa": None},
     "12 pages · 4.1 MB · scanned, no text yet"),
    ({"size": 2_200_000, "pages": 31, "protected": True, "scanned": False, "has_text": True,
      "form_fields": 0, "signatures": 1, "tagged": False, "pdfa": None},
     "31 pages · 2.1 MB · signed · protected"),
    ({"size": 312_000, "pages": 8, "protected": False, "scanned": False, "has_text": True,
      "form_fields": 42, "signatures": 0, "tagged": True, "pdfa": None},
     "8 pages · 305 KB · form, 42 fields"),
    ({"size": 900_000, "pages": 3, "protected": False, "scanned": True, "has_text": True,
      "form_fields": 0, "signatures": 0, "tagged": False, "pdfa": "2B"},
     "3 pages · 879 KB · scanned, searchable · PDF/A"),
    ({"size": 500, "pages": 1, "protected": False, "scanned": False, "has_text": True,
      "form_fields": 1, "signatures": 0, "tagged": False, "pdfa": None},
     "1 page · 1 KB · form, 1 field"),
    ({"size": 1000, "pages": None, "protected": True}, "protected"),
    ({"size": 1000, "pages": None, "error": "not a PDF"}, "can't be read"),
    (None, ""),
]


def test_the_facts_line_says_what_a_file_is_in_plain_words():
    source = _function(GUI_HTML.read_text(encoding="utf-8"), "factsLine")
    script = source + "\nconsole.log(JSON.stringify(JSON.parse(process.argv[1]).map(factsLine)));"
    run = subprocess.run([_node(), "-e", script, json.dumps([f for f, _ in FACTS])],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == [line for _, line in FACTS]


def test_two_files_of_one_name_are_told_apart_by_their_folder():
    html = GUI_HTML.read_text(encoding="utf-8")
    script = _function(html, "folderOf") + "\nconsole.log(JSON.stringify(JSON.parse(process.argv[1]).map(folderOf)));"
    paths = ["C:\\Work\\2024\\invoice.pdf", "/home/me/taxes/invoice.pdf", "invoice.pdf", ""]
    run = subprocess.run([_node(), "-e", script, json.dumps(paths)],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == ["2024", "taxes", "", ""]
    assert "names[e.name] > 1" in html, "the folder is shown only when a name repeats"


def test_the_page_keeps_what_tests_and_scripts_rely_on():
    html = GUI_HTML.read_text(encoding="utf-8")
    assert 'class="tools tool-index" id="tools"' in html          # .tools button[data-op] for every tool
    for group in ("read", "pages", "improve", "convert", "protect", "all"):
        assert f'data-group="{group}"' in html
    assert '<link rel="stylesheet" href="https://' not in html and "fonts.googleapis" not in html
    for font in ("instrument-sans-latin-400-normal", "instrument-serif-latin-400-normal",
                 "jetbrains-mono-latin-500-normal"):
        assert f"/static/fonts/{font}.woff2" in html
        assert (HERE.parent / "pdfmanager" / "static" / "fonts" / f"{font}.woff2").is_file()


def scan_pdf(path, pages=2):
    """Pages that are one full-page picture, with no text."""
    buf = io.BytesIO()
    Image.new("L", (620, 877), 235).save(buf, "JPEG")
    pdf = pikepdf.new()
    name = pikepdf.Name
    for _ in range(pages):
        picture = pikepdf.Stream(pdf, buf.getvalue(), Type=name.XObject, Subtype=name.Image, Width=620,
                                 Height=877, ColorSpace=name.DeviceGray, BitsPerComponent=8, Filter=name.DCTDecode)
        page = pdf.add_blank_page(page_size=(595, 842))
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im=picture))
        page.Contents = pdf.make_stream(b"q 595 0 0 842 0 0 cm /Im Do Q")
    pdf.save(path)
    return Path(path)


@pytest.fixture
def served():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield url
    srv.shutdown()
    srv.server_close()


def test_the_page_in_a_browser(served, tmp_path):
    node = _node()
    if subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    gui.RecentFiles.add(scan_pdf(tmp_path / "invoice-scan.pdf", pages=3))
    run = subprocess.run([node, str(HERE / "startscreen.js"), served], capture_output=True, text=True,
                         encoding="utf-8", timeout=240)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"]

    # The start screen: no line of text above the drop area (only a heading
    # for screen readers), a recent file with its facts, several files.
    assert out["startLine"] == {"text": "Start", "shown": False}
    (recent,) = out["recent"]
    assert recent["name"] == "invoice-scan.pdf"
    assert recent["facts"].startswith("3 pages · ") and recent["facts"].endswith(" KB · scanned, no text yet")
    assert out["multi"][:3] == ["merge", "compare", "search"]
    tools = [op["id"] for op in gui.OPERATIONS]
    assert out["findLabel"] == f"Search {len(tools)} tools"
    assert out["fonts"] == [True, True, True]                     # served from static/fonts

    # The rail: six groups (by what a tool does, 0.23); every tool once under All and at most once
    # in a group of its own: the folder tools and Recipes are under none (the start screen has them).
    assert out["groups"] == ["Read", "Pages", "Improve", "Convert", "Protect", "All"]
    assert sorted(out["toolButtons"]) == sorted(tools)
    flyout = out["flyout"]
    assert sorted(flyout["all"]) == sorted(tools) and len(flyout["all"]) == len(tools)
    folders = ["recipe", "organize", "rename", "duplicates", "search"]
    grouped = flyout["pages"] + flyout["improve"] + flyout["convert"] + flyout["protect"] + ["reader"] + folders
    assert sorted(grouped) == sorted(tools)
    assert "rotate" in flyout["pages"] and "ocr" in flyout["improve"] and "encrypt" in flyout["protect"]
    assert "compress" in flyout["improve"] and "to-word" in flyout["convert"]
    for group in ("pages", "improve", "convert", "protect", "all"):
        assert flyout[group + "Focus"] and flyout[group + "Closed"], group
    assert out["closedByOutsideClick"]
    assert out["fromFlyout"] == ["Split", True]

    # The end-to-end test's selectors, the palette, the reader.
    assert out["rotateTitle"] == "Rotate" and out["rotateCurrent"] == "true"
    assert out["slashOpens"] and out["escapeCloses"] and out["ctrlKOpens"] and out["findOpens"]
    assert "blur" in out["paletteBlur"]
    assert out["railInReader"]

    # Nothing beyond this computer, and nothing scrolling sideways.
    hosts = {urlsplit(u).hostname for u in out["requests"] if not u.startswith(("data:", "blob:"))}
    assert hosts == {"127.0.0.1"}, hosts
    for key in ("scrollWidth1280", "scrollWidth420", "scrollWidth420Tool"):
        width, viewport = out[key]
        assert width == viewport, (key, width, viewport)
