# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.45.0: a watch folder for what a scanner saves. Any scanner can save
scans into a folder; the folder is watched, pictures (JPEG, PNG, TIFF with
one or many pages) become PDFs, and pages that arrive together become one
document."""

import os
import time
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

from pdfmanager import core, watch, watchfolders
from test_more import text_pdf


@pytest.fixture
def fake_ocr(monkeypatch):
    seen = []

    def fake(src, out, **kw):
        seen.append((Path(src).name, len(PdfReader(str(src)).pages)))
        Path(out).write_bytes(Path(src).read_bytes())
        return Path(out)

    monkeypatch.setattr(core, "ocr", fake)
    return seen


def picture(path, size=(850, 1100), dpi=100, shade=240, frames=1):
    """A scan as a scanner saves it: Letter at 100 dpi (850 x 1100 pixels)."""
    pages = [Image.new("L", size, shade - 20 * n) for n in range(frames)]
    extra = {"save_all": True, "append_images": pages[1:]} if frames > 1 else {}
    pages[0].save(path, dpi=(dpi, dpi), **extra)
    return path


def aged(path, seconds_ago):
    """As if written that long ago."""
    when = time.time() - seconds_ago
    os.utime(path, (when, when))
    return path


def settle(watcher, times=2):
    for _ in range(times):
        watcher.poll_all()


def test_pictures_a_scanner_saves_become_pdfs(tmp_path, fake_ocr):
    inbox = tmp_path / "Scans"
    inbox.mkdir()
    watcher = watchfolders.WatchFolders(thread=False)
    entry = watcher.add(inbox, {"tool": "ocr"}, images=True)
    assert entry["images"] is True and "batch" not in entry
    assert watcher.list()[0]["describe"] == "Make searchable (OCR) → Done folder, originals kept, pictures too"
    picture(inbox / "scan0001.jpg")
    picture(inbox / "contract.tif", frames=3)
    picture(inbox / "receipt.png", size=(600, 900), dpi=0)       # no resolution saved: fitted to paper
    (inbox / "notes.txt").write_text("not a scan")
    settle(watcher)
    done = sorted(p.name for p in (inbox / "Done").iterdir())
    assert done == ["contract.pdf", "receipt.pdf", "scan0001.pdf"]
    assert sorted(fake_ocr) == [("contract.pdf", 3), ("receipt.pdf", 1), ("scan0001.pdf", 1)]
    page = PdfReader(str(inbox / "Done" / "scan0001.pdf")).pages[0]
    assert (round(float(page.mediabox.width)), round(float(page.mediabox.height))) == (612, 792)   # 8.5 x 11 in
    assert len(PdfReader(str(inbox / "Done" / "contract.pdf")).pages) == 3
    assert (inbox / "scan0001.jpg").exists() and (inbox / "notes.txt").exists()                  # originals kept
    settle(watcher, 3)
    assert len(fake_ocr) == 3                                                                    # each once


def test_without_pictures_a_folder_takes_pdfs_only(tmp_path, fake_ocr):
    inbox = tmp_path / "In"
    inbox.mkdir()
    watcher = watchfolders.WatchFolders(thread=False)
    watcher.add(inbox, {"tool": "ocr"})
    picture(inbox / "holiday.jpg")
    text_pdf(inbox / "letter.pdf")
    settle(watcher)
    assert [n for n, _ in fake_ocr] == ["letter.pdf"] and not (inbox / "Done" / "holiday.pdf").exists()


def test_pages_that_arrive_together_become_one_document(tmp_path, fake_ocr):
    inbox = tmp_path / "Scans"
    inbox.mkdir()
    watcher = watchfolders.WatchFolders(thread=False)
    entry = watcher.add(inbox, {"tool": "ocr"}, originals="move", images=True, batch=True)
    assert entry["batch"] is True and "pages that arrive together as one document" in watcher.list()[0]["describe"]
    gap = watchfolders.BATCH_GAP
    # Yesterday's stack: three pages a few seconds apart. Then, much later, a single page.
    for n, ago in ((1, 3 * gap + 8), (2, 3 * gap + 4), (3, 3 * gap)):
        aged(picture(inbox / f"invoice_{n:03}.jpg", shade=250 - n), ago)
    aged(text_pdf(inbox / "single.pdf"), gap + 5)
    settle(watcher)
    done = sorted(p.name for p in (inbox / "Done").iterdir())
    assert done == ["invoice_001.pdf", "single.pdf"]
    assert sorted(fake_ocr) == [("invoice_001.pdf", 3), ("single.pdf", 1)]
    assert sorted(p.name for p in (inbox / "Originals").iterdir()) == ["invoice_001.jpg", "invoice_002.jpg", "invoice_003.jpg",
                                                                       "single.pdf"]
    news, _ = watcher.news_after(0)
    assert len(news) == 2 and all(n["ok"] for n in news)                  # one piece of news per document

    # A stack still arriving (the last page a moment ago) waits until nothing new has come.
    aged(picture(inbox / "report_1.jpg"), 6)
    aged(picture(inbox / "report_2.jpg"), 1)
    settle(watcher, 3)
    assert not (inbox / "Done" / "report_1.pdf").exists()
    for name in ("report_1.jpg", "report_2.jpg"):
        aged(inbox / name, gap + 3 if name.endswith("1.jpg") else gap + 1)
    settle(watcher)
    assert len(PdfReader(str(inbox / "Done" / "report_1.pdf")).pages) == 2


def test_a_file_that_isnt_a_picture_is_said_and_the_rest_goes_on(tmp_path, fake_ocr):
    inbox = tmp_path / "Scans"
    inbox.mkdir()
    watcher = watchfolders.WatchFolders(thread=False)
    watcher.add(inbox, {"tool": "ocr"}, images=True)
    (inbox / "broken.jpg").write_bytes(b"not a picture at all")
    picture(inbox / "good.png")
    settle(watcher)
    assert [n for n, _ in fake_ocr] == ["good.pdf"]
    status = watcher.list()[0]["status"]
    assert status["done"] == 1 and status["failed"] == 1
    news, _ = watcher.news_after(0)
    bad = next(n for n in news if not n["ok"])
    assert "broken.jpg" in bad["message"] and "picture" in bad["message"]
    settle(watcher, 3)
    assert watcher.list()[0]["status"]["failed"] == 1                     # said once


def test_the_engine_itself(tmp_path):
    inbox, dest = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    settings = watch.WatchSettings(inbox=inbox, dest=dest, images=True, batch=10)
    aged(picture(inbox / "b.jpg"), 31)
    aged(picture(inbox / "a.jpg"), 30)
    state = watch._State()
    assert watch.poll(settings, state, log=lambda t: None) == []          # first look
    (first,) = watch.poll(settings, state, log=lambda t: None)
    assert first[1] == dest / "a.pdf" and len(PdfReader(str(dest / "a.pdf")).pages) == 2
    assert not (inbox / "a.jpg").exists() and not (inbox / "b.jpg").exists()   # (pdfm watch moves originals away)
    assert [p.name for p in watch.candidates(settings)] == []
