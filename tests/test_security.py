"""Fixes from the 0.16 security review."""
import csv
import json
import re
import shutil
from pathlib import Path

import pytest

from pdfmanager import archive, core, office, organize


# -- Office: an untrusted document's macros never run ------------------------------

@pytest.mark.parametrize("app", ["word", "excel", "powerpoint"])
def test_office_conversions_force_macros_off_before_opening(app):
    script = office.PS_TO_PDF[app]
    opened = re.search(r"\.(Documents|Workbooks|Presentations)\.Open\(", script).start()
    secured = script.find("AutomationSecurity = 3")
    assert 0 <= secured < opened, script
    if app == "word":
        assert 0 <= script.find("DisableAutoMacros(1)") < opened
    if app == "excel":
        assert 0 <= script.find("EnableEvents = $false") < opened
        assert 0 <= script.find("AskToUpdateLinks = $false") < opened


def test_pdf_to_word_through_word_forces_macros_off_too():
    script = office.PS_PDF_TO_WORD
    assert 0 <= script.find("AutomationSecurity = 3") < script.find("Documents.Open(")


def test_programs_are_started_by_their_full_windows_path(monkeypatch):
    monkeypatch.setattr(office.sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    assert office.powershell_exe().lower() == r"c:\windows\system32\windowspowershell\v1.0\powershell.exe"
    seen = []
    monkeypatch.setattr(office, "_run", lambda command, env=None, what="": seen.append(command) or
                        type("R", (), {"returncode": 1, "stderr": "x", "stdout": ""})())
    with pytest.raises(core.PdfManagerError):
        office._powershell("Write-Host", "a.docx", "b.pdf", "test")
    assert seen[0][0].lower().endswith(r"\system32\windowspowershell\v1.0\powershell.exe")


# -- HTML: a page can't pull local files into the PDF --------------------------------

needs_browser = pytest.mark.skipif(not office.find_browser(), reason="needs Edge, Chrome or Chromium")


@needs_browser
def test_a_saved_page_cannot_print_other_local_files(tmp_path):
    secret = tmp_path / "private" / "passwords.txt"
    secret.parent.mkdir()
    secret.write_text("TOP-SECRET-PASSWORD-123", encoding="utf-8")
    page = tmp_path / "web" / "invoice.html"
    page.parent.mkdir()
    page.write_text(
        "<html><body><h1>Invoice 42</h1>"
        f'<iframe src="{secret.as_uri()}" width="600" height="100"></iframe>'
        f'<object data="{secret.as_uri()}" width="600" height="100"></object>'
        "<p id=w></p><script>document.getElementById('w').textContent = 'SCRIPT-RAN ' + location.href;</script>"
        "</body></html>", encoding="utf-8")
    out, _ = office.html_to_pdf(page, tmp_path / "out.pdf")
    text = core.extract_text(out)
    assert "Invoice 42" in text
    assert "TOP-SECRET" not in text and "SCRIPT-RAN" not in text


@needs_browser
def test_a_saved_page_keeps_its_own_pictures(tmp_path):
    from PIL import Image

    page = tmp_path / "web" / "with picture.html"
    (tmp_path / "web" / "page_files").mkdir(parents=True)
    Image.new("RGB", (200, 100), (200, 30, 30)).save(tmp_path / "web" / "page_files" / "logo.png")
    page.write_text('<html><body><h1>Logo below</h1><img src="page_files/logo.png"></body></html>',
                    encoding="utf-8")
    out, _ = office.html_to_pdf(page, tmp_path / "out.pdf")
    from pypdf import PdfReader

    assert PdfReader(out).pages[0].images, "the page's own picture is missing"


# -- updates: only ZPDF's own repositories -----------------------------------------

def test_settings_accept_only_zpdfs_own_repositories(tmp_path):
    import threading
    import urllib.error
    import urllib.request

    from pdfmanager import gui, update

    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    try:
        def post(data):
            req = urllib.request.Request(base + "/api/settings", data=json.dumps(data).encode(),
                                         headers={"X-Pdfm-Token": token, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as res:
                    return res.status
            except urllib.error.HTTPError as exc:
                return exc.code

        assert post({"repo": "attacker/evil"}) == 400
        assert update.settings().get("update_repo") in (None, "")
        assert post({"repo": update.REPO}) == 200 and update.settings()["update_repo"] == update.REPO
        assert post({"repo": update.RELEASES}) == 200 and "update_repo" not in update.settings()
    finally:
        srv.shutdown()
        srv.server_close()


# -- Ghostscript: always in safe mode ----------------------------------------------

def test_ghostscript_always_runs_in_safe_mode(tmp_path, monkeypatch):
    from reportlab.pdfgen import canvas

    src = tmp_path / "a.pdf"
    c = canvas.Canvas(str(src))
    c.drawString(72, 720, "hello")
    c.save()
    seen = []

    class Stop(Exception):
        pass

    def fake_run(command, **kwargs):
        seen.append(command)
        raise Stop

    monkeypatch.setattr(archive, "find_ghostscript", lambda: "gs")
    monkeypatch.setattr(archive.subprocess, "run", fake_run)
    with pytest.raises((Stop, core.PdfManagerError)):
        archive.to_pdfa(src, tmp_path / "out.pdf")
    assert seen and "-dSAFER" in seen[0]


# -- catalog CSV: a title can't become a formula -------------------------------------

def test_the_catalog_does_not_let_a_title_become_a_formula(tmp_path):
    from pypdf import PdfWriter

    folder = tmp_path / "pdfs"
    folder.mkdir()
    w = PdfWriter()
    w.add_blank_page(200, 200)
    w.add_metadata({"/Title": '=HYPERLINK("http://evil.example","open")', "/Author": "+1+cmd"})
    w.write(folder / "a.pdf")
    out, _ = organize.catalog(folder, tmp_path / "catalog.csv")
    row = list(csv.DictReader(open(out, encoding="utf-8-sig")))[0]
    assert row["title"].startswith("'=") and row["author"].startswith("'+")
