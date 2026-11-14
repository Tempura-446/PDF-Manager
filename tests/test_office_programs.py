# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""ZPDF and the programs it hands work to (Word, a browser): what happens
when they don't behave. Found by the 0.16.0 release check on a Windows PC:
Word never finished importing a PDF and stayed running out of sight, and
Edge's launcher ended before the browser it had started wrote the PDF."""
import subprocess
import sys
import time

import pytest
from reportlab.pdfgen import canvas

from pdfmanager import core, office

PDF = b"%PDF-1.4\n" + b"x" * 200 + b"\n%%EOF\n"


def text_pdf(path, text="Hello from ZPDF"):
    c = canvas.Canvas(str(path))
    c.drawString(72, 720, text)
    c.save()
    return path


# -- a browser that hands the printing to another process ---------------------------

def _launcher(tmp_path, target, delay, code=0):
    """A stand-in browser: it starts a process apart from itself that writes
    the PDF after ``delay`` seconds (None: never), and ends at once."""
    writer = tmp_path / "writer.py"
    writer.write_text(
        "import sys, time\n"
        f"time.sleep({delay or 0})\n"
        f"open(sys.argv[1], 'wb').write({PDF!r})\n", encoding="utf-8")
    launcher = tmp_path / "launcher.py"
    apart = ("creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP"
             if sys.platform == "win32" else "start_new_session=True")
    start = (f"subprocess.Popen([sys.executable, {str(writer)!r}, {str(target)!r}], {apart}, "
             "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n")
    launcher.write_text("import subprocess, sys\n" + (start if delay is not None else "")
                        + f"sys.exit({code})\n", encoding="utf-8")
    return [sys.executable, str(launcher)]


def test_the_pdf_is_waited_for_after_the_started_program_ends(tmp_path):
    target = tmp_path / "page.pdf"
    office._print_with(_launcher(tmp_path, target, delay=2), target, timeout=60)
    assert target.read_bytes() == PDF


def test_a_browser_still_using_the_profile_is_waited_for(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "HANDED_OVER", 0.3)
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "lockfile").write_bytes(b"")  # Chromium's sign that it's running
    target = tmp_path / "page.pdf"
    office._print_with(_launcher(tmp_path, target, delay=2), target, timeout=60, profile=profile)
    assert target.read_bytes() == PDF


def test_a_program_that_ends_without_a_pdf_fails_soon(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "HANDED_OVER", 1)
    target = tmp_path / "page.pdf"
    started = time.monotonic()
    with pytest.raises(core.PdfManagerError, match="couldn't be printed"):
        office._print_with(_launcher(tmp_path, target, delay=None), target, timeout=60)
    assert time.monotonic() - started < 20
    # A program that ends badly isn't waited for at all.
    monkeypatch.setattr(office, "HANDED_OVER", 30)
    started = time.monotonic()
    with pytest.raises(core.PdfManagerError, match="couldn't be printed"):
        office._print_with(_launcher(tmp_path, target, delay=None, code=3), target, timeout=60)
    assert time.monotonic() - started < 20


def test_a_scratch_folder_still_in_use_never_fails_the_print(tmp_path, monkeypatch):
    folder = tmp_path / "pdfm-web-x"
    folder.mkdir()
    (folder / "file").write_bytes(b"x")
    real, calls, stopped = office.shutil.rmtree, [], []

    def busy_twice(path, ignore_errors=False):
        calls.append(ignore_errors)
        if len(calls) <= 2:
            raise PermissionError(32, "in use by another process")
        return real(path, ignore_errors=ignore_errors)

    monkeypatch.setattr(office.shutil, "rmtree", busy_twice)
    monkeypatch.setattr(office, "_stop_browsers_using", stopped.append)
    office._remove_browser_folder(folder)
    assert not folder.exists() and stopped == [folder], "the browser is stopped once, then the folder goes"

    def always_busy(path, ignore_errors=False):
        if not ignore_errors:
            raise PermissionError(32, "in use by another process")

    folder.mkdir()
    monkeypatch.setattr(office.shutil, "rmtree", always_busy)
    office._remove_browser_folder(folder)  # gives up quietly


# -- Word that never answers -----------------------------------------------------------

def test_the_office_scripts_note_the_program_they_start():
    for name, script in [("pdf to word", office.PS_PDF_TO_WORD), *office.PS_TO_PDF.items()]:
        assert script.count("Start-Office") == 2, f"{name}: defined once, used once"
        assert "New-Object -ComObject $progId" in script and "$env:PDFM_PIDS" in script, name
        assert '"' not in script, f"{name}: a double quote would be lost on PowerShell's command line"


def test_a_script_that_takes_too_long_has_its_office_program_stopped(tmp_path, monkeypatch):
    stopped = []

    def never_ends(command, env=None, what="", timeout=None):
        from pathlib import Path

        Path(env["PDFM_PIDS"]).write_text("4242 4243\n", encoding="utf-8")  # as Start-Office does
        raise office.TookTooLong(f"{what} took too long and was stopped")

    monkeypatch.setattr(office, "_run", never_ends)
    monkeypatch.setattr(office, "_stop_processes", stopped.append)
    with pytest.raises(core.PdfManagerError, match="took too long"):
        office._powershell("script", tmp_path / "a.pdf", tmp_path / "a.docx", "Microsoft Word")
    assert stopped == [["4242", "4243"]]


def test_stopping_asks_windows_for_exactly_those_processes(monkeypatch):
    seen = []
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setattr(office.subprocess, "run", lambda command, **kw: seen.append(command))
    office._stop_processes(["4242", "not-a-number"])
    assert seen == [[r"C:\Windows\System32\taskkill.exe", "/F", "/T", "/PID", "4242"]]


def test_best_available_uses_the_built_in_converter_when_word_never_finishes(tmp_path, monkeypatch):
    calls = []

    def stalls(script, src, dst, what, timeout=None):
        calls.append(timeout)
        raise office.TookTooLong(f"{what} took too long and was stopped")

    monkeypatch.setattr(office, "has_word", lambda: True)
    monkeypatch.setattr(office, "_word_stalled", False)
    monkeypatch.setattr(office, "_powershell", stalls)
    src = text_pdf(tmp_path / "a.pdf")
    notes = []
    out, engine = office.pdf_to_word(src, tmp_path / "a.docx", notes=notes)
    assert engine == "basic" and out.stat().st_size > 1000
    assert calls == [62], "Word gets a minute and two seconds a page, not five minutes"
    assert "Microsoft Word took too long" in notes[0] and "built-in converter" in notes[0]
    # The next file doesn't wait for Word again, and says why.
    notes = []
    _, engine = office.pdf_to_word(src, tmp_path / "b.docx", notes=notes)
    assert engine == "basic" and len(calls) == 1 and "didn't finish a conversion earlier" in notes[0]
    # Asked for by name, Word is tried, with the full time, and its failure is the answer.
    with pytest.raises(core.PdfManagerError, match="took too long"):
        office.pdf_to_word(src, tmp_path / "c.docx", engine="word")
    assert calls == [62, None]


def test_best_available_falls_back_when_word_fails_but_tries_it_again_next_time(tmp_path, monkeypatch):
    calls = []

    def fails(script, src, dst, what, timeout=None):
        calls.append(1)
        raise core.PdfManagerError(f"{what} failed: this file can't be opened")

    monkeypatch.setattr(office, "has_word", lambda: True)
    monkeypatch.setattr(office, "_word_stalled", False)
    monkeypatch.setattr(office, "_powershell", fails)
    src = text_pdf(tmp_path / "a.pdf")
    notes = []
    assert office.pdf_to_word(src, tmp_path / "a.docx", notes=notes)[1] == "basic"
    assert "Microsoft Word failed" in notes[0]
    assert office.pdf_to_word(src, tmp_path / "b.docx")[1] == "basic" and len(calls) == 2


def test_the_app_says_why_the_built_in_converter_was_used(tmp_path, monkeypatch):
    from pdfmanager import gui

    def stalls(script, src, dst, what, timeout=None):
        raise office.TookTooLong(f"{what} took too long and was stopped")

    monkeypatch.setattr(office, "has_word", lambda: True)
    monkeypatch.setattr(office, "_word_stalled", False)
    monkeypatch.setattr(office, "_powershell", stalls)
    notes = []
    work = tmp_path / "work"
    work.mkdir()
    name, data, _ = gui.run_operation("to-word", [text_pdf(tmp_path / "a.pdf")], {"engine": "auto"},
                                      notes=notes, work=work)
    assert name.endswith(".docx") and len(notes) == 1 and "took too long" in notes[0]
    assert "install Microsoft Word" not in notes[0]


def test_the_robustness_sweep_keeps_to_zpdfs_own_converter():
    import test_robustness

    op = next(op for op in test_robustness.TOOLS if op["id"] == "to-word")
    assert test_robustness._options(op)["engine"] == "basic"
