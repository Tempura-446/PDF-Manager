# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for the folder tools: name collisions and reserved names when
organizing and renaming, duplicates and symlinks, files that vanish
mid-run, the watch folder's safety around half-written, locked and
unreadable files, and the search index."""

import os
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from pypdf import PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from pdfmanager import core, organize, search, watch


def make_pdf(path, pages=1, title=None, author=None, text=None):
    c = canvas.Canvas(str(path), pagesize=A4)
    if title:
        c.setTitle(title)
    if author:
        c.setAuthor(author)
    for i in range(pages):
        c.drawString(72, 72, text or f"Page {i + 1} of {path.name}")
        c.showPage()
    c.save()
    return path


def dateless_pdf(path, when):
    """A PDF without creation/modification dates whose file date is ``when``."""
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.write(str(path))
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


quiet = (lambda message: None)


# -- organize and rename -------------------------------------------------------------

def test_organize_names_differing_only_in_case_do_not_collide(tmp_path, monkeypatch):
    # Windows compares names without regard to case; pretend to on Linux too.
    monkeypatch.setattr(os.path, "normcase", lambda s: s.lower())
    lib = tmp_path / "lib"
    (lib / "2023").mkdir(parents=True)
    (lib / "2024").mkdir()
    make_pdf(lib / "2023" / "Report.pdf", author="Ann", text="old")
    make_pdf(lib / "2024" / "report.pdf", author="Ann", text="new")
    plan = organize.organize(lib, by="author", dest=tmp_path / "sorted", move=True,
                             recursive=True)
    assert [d.name for _, d in plan] == ["Report.pdf", "report (2).pdf"]
    assert all(d.exists() for _, d in plan)
    assert not (lib / "2023" / "Report.pdf").exists()


def test_rename_changes_letter_case_in_place(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    make_pdf(lib / "INVOICE.pdf", title="Invoice")
    plan = organize.rename(lib, pattern="{title}")
    assert plan == [(lib / "INVOICE.pdf", lib / "Invoice.pdf")]
    assert [p.name for p in lib.iterdir()] == ["Invoice.pdf"]
    assert organize.rename(lib, pattern="{title}") == []  # and stays that way


def test_rename_leaves_numbered_variants_alone(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    make_pdf(lib / "Same.pdf", title="Same")
    make_pdf(lib / "Same (2).pdf", title="Same")
    assert organize.rename(lib, pattern="{title}") == []
    make_pdf(lib / "x.pdf", title="Same")
    assert organize.rename(lib, pattern="{title}") == [(lib / "x.pdf", lib / "Same (3).pdf")]
    assert organize.rename(lib, pattern="{title}") == []


def test_safe_names_avoid_windows_device_names(tmp_path):
    assert organize._safe("CON") == "CON_"
    assert organize._safe("aux.txt") == "aux_.txt"
    assert organize._safe("com1") == "com1_"
    assert organize._safe("Console") == "Console"
    lib = tmp_path / "lib"
    lib.mkdir()
    make_pdf(lib / "a.pdf", title="NUL")
    organize.rename(lib, pattern="{title}")
    assert [p.name for p in lib.iterdir()] == ["NUL_.pdf"]


def test_duplicates_do_not_count_links(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    make_pdf(lib / "report.pdf")
    try:
        os.symlink(lib / "report.pdf", lib / "alias.pdf")
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links not available")
    assert organize.find_duplicates(lib) == []
    shutil.copy(lib / "report.pdf", lib / "copy.pdf")
    groups = organize.find_duplicates(lib)  # the copy may share its time stamp on Windows
    assert len(groups) == 1 and sorted(groups[0]) == sorted([lib / "report.pdf", lib / "copy.pdf"])


def test_folder_tools_skip_files_removed_mid_run(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir()
    make_pdf(lib / "a.pdf")
    make_pdf(lib / "b.pdf")
    describe = organize.describe

    def vanishing(path):
        if Path(path).name == "a.pdf":
            Path(path).unlink()
        return describe(path)

    monkeypatch.setattr(organize, "describe", vanishing)
    plan = organize.organize(lib, by="year", dest=tmp_path / "out", dry_run=True)
    assert [s.name for s, _ in plan] == ["b.pdf"]
    make_pdf(lib / "a.pdf")
    assert [s.name for s, _ in organize.rename(lib, "{stem}-x", dry_run=True)] == ["b.pdf"]
    monkeypatch.undo()

    make_pdf(lib / "a.pdf")
    shutil.copy(lib / "a.pdf", lib / "c.pdf")
    file_hash = organize.file_hash

    def vanishing_hash(path):
        if Path(path).name == "a.pdf":
            Path(path).unlink()
        return file_hash(path)

    monkeypatch.setattr(organize, "file_hash", vanishing_hash)
    assert organize.find_duplicates(lib) == []


def test_organize_recursive_in_place(tmp_path):
    lib = tmp_path / "lib"
    (lib / "archive").mkdir(parents=True)
    make_pdf(lib / "top.pdf")
    make_pdf(lib / "archive" / "deep.pdf")
    year = f"{datetime.now():%Y}"
    plan = organize.organize(lib, by="year", move=True, recursive=True)
    assert sorted(d.relative_to(lib) for _, d in plan) == [Path(year, "deep.pdf"),
                                                            Path(year, "top.pdf")]
    assert organize.organize(lib, by="year", move=True, recursive=True) == []


# -- watch folders -----------------------------------------------------------------

def test_watch_refuses_the_inbox_as_destination(tmp_path):
    with pytest.raises(core.PdfManagerError, match="different folder"):
        watch.WatchSettings(inbox=tmp_path / "in", dest=tmp_path / "in" / ".." / "in")


def test_watch_dates_come_from_the_original(tmp_path):
    inbox, dest = tmp_path / "inbox", tmp_path / "dest"
    inbox.mkdir()
    dateless_pdf(inbox / "old scan.pdf", datetime(2019, 5, 6, 12))
    settings = watch.WatchSettings(inbox=inbox, dest=dest, by="year", compress="low",
                                   rename="{date:%Y-%m-%d} {stem}")
    state = watch._State()
    watch.poll(settings, state, log=quiet)
    done = watch.poll(settings, state, log=quiet)
    assert done == [(inbox / "old scan.pdf", dest / "2019" / "2019-05-06 old scan.pdf")]


def test_watch_refuses_an_unreadable_file(tmp_path):
    inbox, dest = tmp_path / "inbox", tmp_path / "dest"
    inbox.mkdir()
    (inbox / "scan.pdf").write_bytes(b"%PDF-1.4 cut off")
    settings = watch.WatchSettings(inbox=inbox, dest=dest)  # no step would read it
    state, logs = watch._State(), []
    watch.poll(settings, state, log=logs.append)
    assert watch.poll(settings, state, log=logs.append) == []
    assert any(m.startswith("failed: scan.pdf: can't be read") for m in logs)
    assert (inbox / "scan.pdf").exists() and not dest.exists()


def test_watch_retries_a_replaced_file(tmp_path):
    inbox, dest = tmp_path / "inbox", tmp_path / "dest"
    inbox.mkdir()
    (inbox / "scan.pdf").write_bytes(b"junk")
    settings = watch.WatchSettings(inbox=inbox, dest=dest, compress="low")
    state, logs = watch._State(), []
    for _ in range(3):
        watch.poll(settings, state, log=logs.append)
    assert len([m for m in logs if m.startswith("failed")]) == 1  # the same file isn't retried
    (inbox / "scan.pdf").unlink()
    make_pdf(inbox / "scan.pdf")
    watch.poll(settings, state, log=logs.append)
    done = watch.poll(settings, state, log=logs.append)
    assert done == [(inbox / "scan.pdf", dest / "scan.pdf")]
    assert state.failed == set()


def test_watch_undoes_the_output_when_the_original_cannot_be_retired(tmp_path, monkeypatch):
    inbox, dest = tmp_path / "inbox", tmp_path / "dest"
    inbox.mkdir()
    make_pdf(inbox / "scan.pdf")
    settings = watch.WatchSettings(inbox=inbox, dest=dest)

    def locked(path, settings):
        raise PermissionError("locked")

    monkeypatch.setattr(watch, "_retire", locked)
    for _ in range(2):  # like two scheduled runs
        state, logs = watch._State(), []
        watch.poll(settings, state, log=logs.append)
        watch.poll(settings, state, log=logs.append)
        # Can't be moved away (open in another program): not a failure, it waits;
        # the result made meanwhile is taken back so nothing is duplicated.
        assert logs == [] and not state.failed and (inbox / "scan.pdf") in state.busy
    assert list(dest.iterdir()) == [] and (inbox / "scan.pdf").exists()
    monkeypatch.undo()
    state = watch._State()
    watch.poll(settings, state, log=quiet)
    assert watch.poll(settings, state, log=quiet) == [(inbox / "scan.pdf", dest / "scan.pdf")]
    assert [p.name for p in dest.iterdir()] == ["scan.pdf"]


def test_watch_waits_for_a_missing_inbox(tmp_path):
    inbox, dest = tmp_path / "inbox", tmp_path / "dest"
    settings = watch.WatchSettings(inbox=inbox, dest=dest)
    logs, calls = [], []

    def stop():
        calls.append(1)
        if len(calls) == 2:
            shutil.rmtree(inbox)  # the drive is unplugged...
        if len(calls) == 5:
            inbox.mkdir()         # ...and back
            make_pdf(inbox / "scan.pdf")
        return len(calls) > 8

    watch.watch(settings, interval=0.01, log=logs.append, stop=stop)
    waiting = [m for m in logs if m.startswith("waiting:")]
    assert len(waiting) == 1 and "not a directory" in waiting[0]
    assert f"watching {inbox} again" in logs
    assert logs[-1] == f"scan.pdf -> {dest / 'scan.pdf'}"


def test_once_leaves_a_growing_file_and_skips_duplicates(tmp_path, monkeypatch):
    inbox, dest = tmp_path / "inbox", tmp_path / "dest"
    inbox.mkdir()
    dest.mkdir()
    make_pdf(dest / "already.pdf")
    shutil.copy(dest / "already.pdf", inbox / "again.pdf")
    make_pdf(inbox / "new.pdf")
    growing = make_pdf(inbox / "growing.pdf")
    settled = []

    def still_writing(seconds):
        settled.append(seconds)
        with open(growing, "ab") as fh:
            fh.write(b"\n" * 1000)

    monkeypatch.setattr(watch.time, "sleep", still_writing)
    logs = []
    state = watch.once(watch.WatchSettings(inbox=inbox, dest=dest), skip_duplicates=True,
                       settle=0.25, log=logs.append)
    assert settled == [0.25]
    assert sorted(p.name for p in dest.iterdir()) == ["already.pdf", "new.pdf"]
    assert sorted(p.name for p in inbox.iterdir()) == ["growing.pdf", "originals"]
    assert sorted(p.name for p in (inbox / "originals").iterdir()) == ["again.pdf", "new.pdf"]
    assert "skipped duplicate: again.pdf" in logs
    assert set(state.sizes) == {growing}  # still being watched


# -- search ------------------------------------------------------------------------

def test_search_forgets_a_passing_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "data_dir", lambda: tmp_path / "appdata")
    folder = tmp_path / "docs"
    folder.mkdir()
    make_pdf(folder / "a.pdf", text="invoice")
    make_pdf(folder / "b.pdf", text="invoice")
    (folder / "junk.pdf").write_bytes(b"junk")
    extract, locked = search._extract, [True]

    def flaky(path):
        if path.name == "b.pdf" and locked and locked.pop():
            raise PermissionError("in use by another program")
        return extract(path)

    monkeypatch.setattr(search, "_extract", flaky)
    found = search.search(folder, "invoice")
    assert sorted(found["skipped"]) == [(str(folder / "b.pdf"), "can't be read"),
                                        (str(folder / "junk.pdf"), "can't be read")]
    found = search.search(folder, "invoice")  # b.pdf opens fine now; junk.pdf never will
    assert found["skipped"] == [(str(folder / "junk.pdf"), "can't be read")]
    assert sorted(Path(m["path"]).name for m in found["matches"]) == ["a.pdf", "b.pdf"]
    calls = []
    monkeypatch.setattr(search, "_extract", lambda p: calls.append(p) or [])
    search.search(folder, "invoice")
    assert calls == []  # the broken file's error was cached, the others' text too


def test_search_skips_a_file_removed_while_searching(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "data_dir", lambda: tmp_path / "appdata")
    folder = tmp_path / "docs"
    folder.mkdir()
    make_pdf(folder / "a.pdf", text="invoice")
    make_pdf(folder / "b.pdf", text="invoice")
    pages = search._pages

    def vanishing(conn, path):
        if path.name == "a.pdf":
            path.unlink()
        return pages(conn, path)

    monkeypatch.setattr(search, "_pages", vanishing)
    found = search.search(folder, "invoice")
    assert found["skipped"] == [(str(folder / "a.pdf"), "removed while searching")]
    assert [Path(m["path"]).name for m in found["matches"]] == ["b.pdf"]
