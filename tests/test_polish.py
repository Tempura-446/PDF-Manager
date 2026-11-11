"""Stage 3, the polish pass, in a browser (Node's Playwright, skipped where
there is none): one Recent list on the start screen and under the reader's
"Open a PDF", the drop-first pages of the tools that start from several
files (Rotate's page unchanged), the panel's "Make it fillable" and "Sign it"
suggestions, motion switched off when the system asks for less, toasts, no
requests beyond this computer and nothing scrolling sideways."""
import json
import re
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pikepdf
import pytest

from pdfmanager import gui
from test_filepanel import scan_pdf, text_pdf
from test_formfind_pdfa import make_form

HERE = Path(__file__).resolve().parent
GUI_HTML = HERE.parent / "pdfmanager" / "gui.html"


def to_sign_pdf(path):
    """A PDF with a signature field nobody has signed yet."""
    pdf = pikepdf.open(text_pdf(path.with_name("plain-" + path.name)))
    empty = pdf.make_indirect(pikepdf.Dictionary(FT=pikepdf.Name.Sig, T=pikepdf.String("Signature1")))
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=[empty])
    pdf.save(path)
    return path


def test_the_glass_and_motion_tokens():
    html = GUI_HTML.read_text(encoding="utf-8")
    # Dark mode's frosted fill is the same in both dark blocks, and has a rim.
    dark = re.findall(r"--glass: (rgb\([^)]*\));\s*--glass-edge: (rgb\([^)]*\))", html)
    assert len(dark) == 2 and dark[0] == dark[1], dark
    assert "@media (prefers-reduced-motion: reduce) { :root { --t: 0ms; } }" in html


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def run_polish(app, tmp_path, shots=None, theme="light"):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = app
    for name, maker in (("old-scan.pdf", lambda p: scan_pdf(p, pages=3)), ("Quarterly report.pdf", text_pdf),
                        ("Minutes.pdf", lambda p: text_pdf(p, pages=1))):
        gui.RecentFiles.add(maker(tmp_path / name))
    opened = srv.RequestHandlerClass.opened
    form_id = opened.add(make_form(tmp_path / "Application form.pdf"))
    sign_id = opened.add(to_sign_pdf(tmp_path / "Contract to sign.pdf"))
    a, b = text_pdf(tmp_path / "part one.pdf", pages=2), text_pdf(tmp_path / "part two.pdf", pages=1)
    args = [node, str(HERE / "polish.js"), url, form_id, sign_id, str(a), str(b)] + ([str(shots), theme] if shots else [])
    run = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=420)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_polish_in_a_browser(app, tmp_path):
    out = run_polish(app, tmp_path)
    assert not out["errors"], out["errors"]

    # (a) One list: the reader's empty state shows the start screen's rows, facts and all.
    home = out["homeRecent"]
    assert [r["name"] for r in home] == ["Minutes.pdf", "Quarterly report.pdf", "old-scan.pdf"]
    assert home[2]["facts"].startswith("3 pages · ") and home[2]["facts"].endswith("scanned, no text yet")
    assert home[0]["facts"].startswith("1 page · ") and all(r["when"] for r in home)
    assert out["readerRecent"] == home
    assert out["readerHint"][0] == "balance"
    # Removed there, gone here too; the toast says so (and the start screen's own toast looks the same).
    assert out["removed"] == "old-scan.pdf" and out["readerToast"] == "Removed old-scan.pdf from Recent"
    assert out["readerAfter"] == home[:2] and out["homeAfter"] == home[:2]
    toast = out["homeToast"]
    assert toast["text"] == "Removed Quarterly report.pdf from Recent"
    assert "blur" in toast["blur"] and toast["radius"] == "999px" and toast["position"] == "fixed"
    assert toast["anim"] == "rd-rise" and out["toastGone"]

    # (b) Merge: the tool's name and line and one big drop area; options and Run with the files.
    before = out["mergeBefore"]
    assert before["body"] == ["drop-first", "opts-later"]
    assert before["title"] == "Merge" and before["help"].startswith("Combine PDFs")
    assert before["drop"] == "Drop two or more PDFs to merge" and before["hint"] == "Add PDFs in the order to combine them"
    assert before["choose"] and not before["filesHead"] and not before["options"] and not before["bar"]
    assert before["dropHeight"] >= 300
    assert out["chooseByTab"]
    one = out["mergeOne"]
    assert one["options"] and one["bar"] and one["runDisabled"] and one["summary"] == "Add at least 2 files"
    after = out["mergeAfter"]
    assert after["body"] == ["drop-first", "opts-later", "has-files"] and after["cards"] == 2
    assert after["options"] and after["run"] and not after["runDisabled"] and not after["choose"] and after["filesHead"]
    assert after["drop"] == "Add more files"
    assert out["mergeResult"] == "rise"
    assert out["page_compare"]["drop"] == "Drop the two versions to compare" and not out["page_compare"]["options"]
    assert out["page_interleave"]["drop"] == "Drop the two scans to interleave"
    assert out["page_from-images"]["drop"] == "Drop photos or scans to turn into a PDF"
    assert out["page_from-images"]["help"] == "Turn photos and scans into a PDF"
    assert out["page_from-office"]["drop"] == "Drop Office documents to turn into PDFs"
    web = out["page_web"]  # an address can do instead of files: its options stay
    assert web["body"] == ["drop-first"] and web["options"] and web["bar"] and web["drop"] == "Drop saved web pages to turn into PDFs"
    assert out["page_organize"]["body"] == []  # a folder tool: no files
    # Single-PDF tools keep their page: Rotate (the end-to-end script's) and Split.
    for key in ("rotate", "page_split"):
        page = out[key]
        assert page["body"] == [] and page["filesHead"] and page["options"] and page["bar"] and not page["choose"], key
        assert page["drop"] == "Drop files here or click to choose" and page["dropHeight"] < 220, key
    assert out["rotateDone"]

    # (c) A printed form: "Make it fillable" in the panel does what the bar does; the bar stays.
    form = out["form"]
    assert form["suggestions"] == ["Make it fillable"] and form["why"] == "It looks like a form you can't type in yet"
    assert form["bar"] and "Make it fillable" in form["barText"]
    assert out["formToast"].startswith("Added ") and "place" in out["formToast"]
    assert not out["formAfter"]["card"] and not out["formAfter"]["bar"]
    # The long "Changes" choice fits the panel.
    changes = out["changes"]
    assert changes["text"] == "Any changes except extracting pages" and changes["long"]
    assert changes["needs"] <= changes["room"], changes
    # An empty signature field: "Sign it", which opens the signing options (one set, in the card).
    assert out["sign"] == ["Sign it"]
    so = out["signOpen"]
    assert so["expanded"] == "true" and so["grid"] == "true" and so["pfx"] and so["inCard"] and so["ids"] == 1
    assert out["signClosed"] == ["false", True]
    # The link back to the original in a result's note: its focus ring stays inside its own line.
    note = out["noteLink"]
    assert note["focusVisible"] and note["offset"] == "0px" and note["height"] + 4 <= note["line"], note

    # (d) Less motion: nothing animates.
    calm = out["calm"]
    for key in ("flyout", "palette", "toast", "card", "readerToast"):
        assert calm[key] and all(v in ("0s", "0s, 0s", "0s, 0s, 0s") or set(v.split(", ")) == {"0s"} for v in calm[key]), (key, calm[key])
    assert calm["opts"] == [0, False, False]

    # (e) Nothing beyond this computer; nothing scrolls sideways.
    hosts = {urlsplit(u).hostname for u in out["requests"] if not u.startswith(("data:", "blob:"))}
    assert hosts == {"127.0.0.1"}, hosts
    for key in ("width1280Home", "width1280Merge", "width1280Reader", "width1000Merge", "width1000Home"):
        width, viewport = out[key]
        assert width == viewport, (key, width, viewport)


def test_polish_screenshots(app, tmp_path):
    """Light and dark screenshots for looking at (POLISH_SHOTS=<folder>)."""
    import os
    shots = os.environ.get("POLISH_SHOTS")
    if not shots:
        pytest.skip("set POLISH_SHOTS to a folder to take the screenshots")
    for theme in ("light", "dark"):
        (tmp_path / theme).mkdir()
        out = run_polish(app, tmp_path / theme, Path(shots), theme)
        assert not out["errors"], out["errors"]
