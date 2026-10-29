"""Regression tests for the command-line audit: output naming and collisions,
error reporting, passwords, `watch --once`, page ranges and JSON output."""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from pdfmanager import core, recipes
from pdfmanager.cli import _human_size, main

REPO = Path(__file__).resolve().parents[1]


def text_pdf(path, pages=("Hello world",)):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    for text in pages:
        c.setFont("Helvetica", 14)
        c.drawString(72, 760, text)
        c.showPage()
    c.save()
    return path


def pages_of(path):
    return len(PdfReader(str(path)).pages)


# -- output streams and error reporting ------------------------------------------------

def test_output_survives_a_narrow_console_encoding(tmp_path):
    """A redirected stdout on Windows uses the ANSI code page; characters it
    can't hold are escaped instead of crashing the command."""
    src = text_pdf(tmp_path / "lig.pdf", ["ﬁnance → résumé"])
    env = {**os.environ, "PYTHONIOENCODING": "cp1252:strict"}
    result = subprocess.run([sys.executable, "-m", "pdfmanager", "text", str(src)],
                            cwd=REPO, env=env, capture_output=True)
    err = result.stderr.decode("cp1252", "replace")
    assert result.returncode == 0, err
    assert "Traceback" not in err
    assert "nance" in result.stdout.decode("cp1252", "replace")


def test_os_errors_and_bad_values_are_plain_errors(tmp_path, capsys):
    src = text_pdf(tmp_path / "a.pdf")
    assert main(["info", str(tmp_path)]) == 1
    assert capsys.readouterr().err.startswith("error:")

    assert main(["number", str(src), "--format", "Page {page}"]) == 1
    assert "{n} and {total}" in capsys.readouterr().err
    assert not (tmp_path / "a_numbered.pdf").exists()

    assert main(["to-images", str(src), "-f", "xyz", "-o", str(tmp_path / "imgs")]) == 1
    assert "unknown image format 'xyz'" in capsys.readouterr().err

    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert main(["bookmarks", str(src), "--set", str(tmp_path / "bad.json")]) == 1
    assert capsys.readouterr().err.startswith("error:")

    for argv in (["crop", str(src), "-m", "abc"],
                 ["to-images", str(src), "--dpi", "0"],
                 ["watch", str(tmp_path), "--dest", str(tmp_path / "d"), "--interval", "-1"]):
        with pytest.raises(SystemExit) as info:
            main(argv)
        assert info.value.code == 2
        assert "error:" in capsys.readouterr().err


def test_encrypt_without_a_terminal_asks_for_the_option(tmp_path, monkeypatch, capsys):
    src = text_pdf(tmp_path / "a.pdf")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["encrypt", str(src)]) == 1
    assert "--user-password" in capsys.readouterr().err


# -- output naming ---------------------------------------------------------------------

def test_batch_refuses_outputs_that_would_collide(tmp_path, capsys):
    old = text_pdf(tmp_path / "2023" / "report.pdf", ["old"])
    new = text_pdf(tmp_path / "2024" / "report.pdf", ["new"])
    assert main(["compress", str(old), str(new), "-o", str(tmp_path / "small")]) == 1
    err = capsys.readouterr().err
    assert "would both be written to" in err and "2023" in err and "2024" in err
    assert not (tmp_path / "small" / "report.pdf").exists()


def test_batch_refuses_overwriting_a_later_input(tmp_path, capsys):
    text_pdf(tmp_path / "x.pdf", ["a", "b", "c"])
    kept = text_pdf(tmp_path / "x_edited.pdf", ["KEEP ME"])
    before = kept.read_bytes()
    code = main(["delete", str(tmp_path / "x.pdf"), str(kept), "-p", "1"])
    assert code == 1
    assert "also an input" in capsys.readouterr().err
    assert kept.read_bytes() == before
    assert not (tmp_path / "x_edited_edited.pdf").exists()


def test_recipe_run_never_replaces_its_inputs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(recipes, "data_dir", lambda: tmp_path / "appdata")
    assert main(["recipe", "save", "Shrink", "delete", "pages=1"]) == 0
    scans = tmp_path / "scans"
    a = text_pdf(scans / "a.pdf", ["1", "2", "3"])
    b = text_pdf(scans / "b.pdf", ["1", "2", "3"])
    # -o pointing at the inputs' own folder: default names, originals untouched.
    assert main(["recipe", "run", "Shrink", str(a), str(b), "-o", str(scans)]) == 0
    assert pages_of(a) == 3 and pages_of(b) == 3
    assert pages_of(scans / "a_shrink.pdf") == 2 and pages_of(scans / "b_shrink.pdf") == 2
    # One input and -o an existing folder: the file keeps its name in there.
    done = tmp_path / "done"
    done.mkdir()
    assert main(["recipe", "run", "Shrink", str(a), "-o", str(done)]) == 0
    assert pages_of(done / "a.pdf") == 2 and pages_of(a) == 3
    assert f"-> {done / 'a.pdf'}" in capsys.readouterr().out


def test_text_batch_names_and_status_line(tmp_path, capsys):
    a = text_pdf(tmp_path / "a.pdf", ["A text"])
    b = text_pdf(tmp_path / "b.pdf", ["B text"])
    assert main(["text", str(a), str(b), "-o", str(tmp_path)]) == 0
    assert (tmp_path / "a_text.txt").read_text(encoding="utf-8").startswith("A text")
    assert not list(tmp_path.glob("*_None.txt"))
    capsys.readouterr()
    assert main(["text", str(a), str(b)]) == 0
    out, err = capsys.readouterr()
    assert "A text" in out and "B text" in out
    assert "file(s) done" not in out and "2 of 2 file(s) done" in err


def test_output_folder_semantics_do_not_depend_on_the_match_count(tmp_path, capsys):
    one = text_pdf(tmp_path / "scans" / "one.pdf", ["single"])
    # A wildcard that matched one file still writes into the folder.
    assert main(["compress", str(tmp_path / "scans" / "*.pdf"), "-o", str(tmp_path / "small")]) == 0
    assert (tmp_path / "small" / "one.pdf").is_file()
    assert not (tmp_path / "small.pdf").exists()
    # A single literal input with -o naming an existing folder goes into it.
    (tmp_path / "out").mkdir()
    assert main(["rotate", str(one), "-d", "90", "-o", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "one.pdf").is_file()
    assert not list(tmp_path.glob("*.tmp"))
    # ...and so does one with a trailing separator, even if the folder is new.
    assert main(["rotate", str(one), "-d", "90", "-o", str(tmp_path / "new") + os.sep]) == 0
    assert (tmp_path / "new" / "one.pdf").is_file()
    # A plain file name is still the output file.
    assert main(["rotate", str(one), "-d", "90", "-o", str(tmp_path / "r.pdf")]) == 0
    assert (tmp_path / "r.pdf").is_file()


def test_compress_in_place_reports_the_size_before(tmp_path, capsys):
    src = text_pdf(tmp_path / "a.pdf", [f"page {n}" for n in range(8)])
    before = src.stat().st_size
    assert main(["compress", str(src), "-o", str(src), "-l", "high"]) == 0
    after = src.stat().st_size
    assert after < before
    assert f"({_human_size(before)} -> {_human_size(after)}" in capsys.readouterr().out


# -- passwords -------------------------------------------------------------------------

def test_wrong_supplied_password_is_called_wrong(tmp_path, monkeypatch, capsys):
    enc = core.encrypt(text_pdf(tmp_path / "a.pdf"), tmp_path / "enc.pdf", "s3cret")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setenv("PDFM_PASSWORD", "wrong")
    assert main(["rotate", str(enc), "-d", "90", "-o", str(tmp_path / "r.pdf")]) == 1
    err = capsys.readouterr().err
    assert "wrong password for" in err and "PDFM_PASSWORD" in err
    monkeypatch.delenv("PDFM_PASSWORD")
    assert main(["rotate", str(enc), "-d", "90", "--password", "nope",
                 "-o", str(tmp_path / "r.pdf")]) == 1
    assert "tried --password" in capsys.readouterr().err
    assert main(["rotate", str(enc), "-d", "90", "-o", str(tmp_path / "r.pdf")]) == 1
    assert "pass --password or set PDFM_PASSWORD" in capsys.readouterr().err


def test_merge_combines_owner_only_and_user_password_files(tmp_path, monkeypatch):
    plain = text_pdf(tmp_path / "plain.pdf")
    user = core.encrypt(plain, tmp_path / "user.pdf", "s3cret")
    owner = core.encrypt(plain, tmp_path / "owner.pdf", "", "own")
    seen = {}

    def fake_merge(inputs, output, bookmarks=True, password=None):
        seen["password"] = password
        shutil.copyfile(plain, output)
        return Path(output)

    monkeypatch.setattr(core, "merge", fake_merge)
    monkeypatch.setenv("PDFM_PASSWORD", "s3cret")
    assert main(["merge", str(owner), str(user), "-o", str(tmp_path / "all.pdf")]) == 0
    # The owner-only file's empty password doesn't count as a different one.
    assert seen["password"] == "s3cret"


# -- page ranges and JSON ---------------------------------------------------------------

def test_page_ranges_starting_with_a_dash(tmp_path):
    src = text_pdf(tmp_path / "a.pdf", [str(n) for n in range(1, 7)])
    assert main(["extract", str(src), "-p", "-3,5", "-o", str(tmp_path / "x.pdf")]) == 0
    assert pages_of(tmp_path / "x.pdf") == 4
    assert main(["extract", str(src), "-p", "-2", "-o", str(tmp_path / "y.pdf")]) == 0
    assert pages_of(tmp_path / "y.pdf") == 2
    # Real negative numbers are left alone.
    assert main(["rotate", str(src), "-d", "-90", "-o", str(tmp_path / "r.pdf")]) == 0
    assert PdfReader(str(tmp_path / "r.pdf")).pages[0].rotation % 360 == 270


def test_info_json_batch_is_one_array(tmp_path, capsys):
    a = text_pdf(tmp_path / "a.pdf")
    b = text_pdf(tmp_path / "b.pdf", ["1", "2"])
    assert main(["info", str(a), str(b), "--json"]) == 0
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert [d["pages"] for d in data] == [1, 2]
    assert "2 of 2 file(s) done" in err


# -- watch --once ----------------------------------------------------------------------

def test_watch_once_settles_dedupes_and_reports_failures(tmp_path, monkeypatch, capsys):
    inbox, dest = tmp_path / "inbox", tmp_path / "dest"
    inbox.mkdir()
    dest.mkdir()
    plain = text_pdf(tmp_path / "plain.pdf")
    shutil.copy(plain, dest / "already.pdf")
    shutil.copy(plain, inbox / "dup.pdf")
    text_pdf(inbox / "fresh.pdf", ["fresh"])
    (inbox / "bad.pdf").write_bytes(b"not a pdf")
    growing = inbox / "growing.pdf"
    growing.write_bytes(plain.read_bytes()[:100])
    naps = []

    def settle(seconds):
        # Something is still writing growing.pdf while we wait.
        naps.append(seconds)
        with open(growing, "ab") as fh:
            fh.write(b"more")
        os.utime(growing, (time.time() + 5, time.time() + 5))

    monkeypatch.setattr(time, "sleep", settle)
    code = main(["watch", str(inbox), "--dest", str(dest), "--once", "--skip-duplicates",
                 "--compress", "low", "--interval", "0.5"])
    out = capsys.readouterr().out
    assert code == 1  # bad.pdf failed
    assert naps == [0.5]
    assert "skipped duplicate: dup.pdf" in out and "failed: bad.pdf" in out
    assert (dest / "fresh.pdf").is_file() and not (dest / "dup.pdf").exists()
    assert growing.is_file() and not (dest / "growing.pdf").exists()

    # An empty inbox is done at once.
    for p in inbox.glob("*.pdf"):
        p.unlink()
    assert main(["watch", str(inbox), "--dest", str(dest), "--once"]) == 0
    assert naps == [0.5]
