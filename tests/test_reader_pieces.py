"""A file read in pieces (over 4 MB, opened from this computer) when its
pieces can't be had: the server fails or stops answering, the file is
deleted or replaced by another program, or the same file is opened twice.
The reader must never get stuck: it says what happened once, and the tab
can be closed and other files opened. In a browser (Node's Playwright,
skipped where there is none), against a server whose answers for the file
can be made to fail (see ``failing``)."""
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pikepdf
import pytest

from pdfmanager import gui
from test_filepanel import text_pdf

HERE = Path(__file__).resolve().parent


def lazy_pdf(path, pages=16):
    """Text pages, the last also showing a big picture (12 MB, stored as it is) that no other page uses:
    its bytes, past the first 4 MB, are read only when the last page is shown (or all of the file is read)."""
    with pikepdf.open(text_pdf(path.with_name("text-" + path.name), pages=pages)) as pdf:
        side = 2000
        picture = pdf.make_stream(os.urandom(side * side * 3), Width=side, Height=side, ColorSpace=pikepdf.Name.DeviceRGB,
                                  BitsPerComponent=8, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image)
        last = pdf.pages[-1]
        last.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Big=picture))
        last.Contents = pdf.make_stream(last.Contents.read_bytes() + b"\nq 200 0 0 200 100 100 cm /Big Do Q\n")
        pdf.save(path, compress_streams=False)
    assert path.stat().st_size > 8 << 20
    return path


@pytest.fixture
def failing(tmp_path):
    """A server whose answers for opened files follow the rules in a JSON
    file the browser script writes: [{"mode": "after", "at": bytes} (pieces
    from there on) or {"mode": "whole"} (the reader's background read of all
    of a file), "how": "fail" (a 500) or "hang" (no answer for "secs")]."""
    srv, url = gui.make_server()
    rules = tmp_path / "rules.json"
    rules.write_text("[]")
    handler = srv.RequestHandlerClass
    real = handler._open_file

    def _open_file(self, key):
        wanted = self.headers.get("Range") or ""
        try:
            todo = json.loads(rules.read_text() or "[]")
        except ValueError:
            todo = []
        if wanted.startswith("bytes="):
            first, _, last = wanted[6:].partition("-")
            path = self.opened.get(key)
            size = path.stat().st_size if path and path.is_file() else -1
            for rule in todo:
                hit = (rule["mode"] == "after" and int(first or 0) >= rule["at"]) or (
                    rule["mode"] == "whole" and int(first or 0) == 4 << 20 and last and int(last) == size - 1)
                if hit:
                    if rule.get("how") == "hang":
                        time.sleep(rule.get("secs", 30))
                    return self._error(500, "the disk said no")
        return real(self, key)

    handler._open_file = _open_file
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url, rules
    srv.shutdown()
    srv.server_close()


def test_pieces_that_cant_be_had_in_a_browser(failing, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url, rules = failing
    opened = srv.RequestHandlerClass.opened
    noisy = lazy_pdf(tmp_path / "lazy.pdf")
    files = {}
    for name in ("midread", "opening", "hang", "held-deleted", "held-replaced", "deleted", "replaced", "twice"):
        path = tmp_path / f"{name}.pdf"
        shutil.copy(noisy, path)
        files[name] = {"id": opened.add(path), "path": str(path)}
    other = text_pdf(tmp_path / "Other.pdf", pages=3)  # what a replaced file becomes
    small = text_pdf(tmp_path / "Small.pdf", pages=2)
    run = subprocess.run([node, str(HERE / "readerpieces.js"), url, str(rules), json.dumps(files), opened.add(small), str(other)],
                         capture_output=True, text=True, encoding="utf-8", timeout=600)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    s = out["scenarios"]
    assert not out["errors"], (out["errors"], s)
    said = lambda r, words: [t for t in r["toasts"] if words in t]

    # A piece that can't be had after the file is open: said once; the reader goes on.
    r = s["midread"]
    assert r["held"] is False and len(said(r, "Couldn't read more of")) == 1, r
    assert r["busy"] is False and r["openAnother"] and r["closeIt"], r
    assert r["saveSays"].startswith("Could not save a copy"), r  # (nothing is written from a part of the file)
    # While it is being opened: "Could not open", no tab left, another file opens.
    r = s["opening"]
    assert said(r, "Could not open “opening.pdf”") and r["tabs"] == [] and r["openAnother"], r
    # A piece that never comes: the reader doesn't wait for it to open or close something else.
    r = s["hang"]
    assert r["openAnother"] and r["closeIt"], r
    # Read in full in the background, the file can go away or change: the tab stays what it was.
    r = s["held-deleted"]
    assert r["held"] and r["back"] == {"name": "held-deleted.pdf", "pages": 16} and r["bytes"] == r["size"], r
    assert not said(r, "Can't show"), r
    r = s["held-replaced"]
    assert r["held"] and r["back"] == {"name": "held-replaced.pdf", "pages": 16}, r
    assert any("changed by another program" in d for d in r["dialogs"]), r  # a save says so
    # Not all of it read yet: the tab isn't shown as something else; it says so and can be closed.
    r = s["deleted"]
    assert r["held"] is False and said(r, "Can't show “deleted.pdf” again: the file is no longer where it was"), r
    assert r["active"] == "Small.pdf" and r["tabs"] == ["deleted.pdf", "Small.pdf"] and r["closeIt"], r
    r = s["replaced"]
    assert r["held"] is False and said(r, "Can't show “replaced.pdf” again: another program changed the file"), r
    assert r["active"] == "Small.pdf" and r["closeIt"], r
    # The same file opened again (a drop, or the file box): its tab, not a second one.
    r = s["twice"]
    assert r["beforeHeld"] == 1 and r["afterHeld"] == 1, r
