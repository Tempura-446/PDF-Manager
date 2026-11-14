# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.26.0: the Scan page in a browser (scan26.js), through Node's Playwright
(skipped where there is none): with a fake WIA behind the server, and
without WIA (as on Linux), where Photos still works. With SCAN26_SHOTS set
to a folder, it saves light and dark screenshots of the page there."""
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from fakewia import FakeCom, FakeScanner, text_page
from pdfmanager import gui, scanner
from photosynth import desk, photo

HERE = Path(__file__).resolve().parent


def _node(tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=HERE).returncode:
        pytest.skip("needs Node.js with Playwright")
    return node


def _drive(node, url, cfg):
    run = subprocess.run([node, str(HERE / "scan26.js"), url, json.dumps(cfg)], capture_output=True, text=True,
                         encoding="utf-8", timeout=900)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.fixture
def photos(tmp_path):
    page_photo, _ = photo()
    page_photo.save(tmp_path / "page.jpg", quality=90)
    desk((1200, 900)).save(tmp_path / "desk.jpg", quality=90)
    return [str(tmp_path / "page.jpg"), str(tmp_path / "desk.jpg")]


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def _shots():
    folder = os.environ.get("SCAN26_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
    return folder


def test_scan_page_with_a_scanner(app, tmp_path, photos):
    node = _node(tmp_path)
    office = FakeScanner("Office scanner", feeder_pages=[text_page(n, size=(620, 877)) for n in (1, 2, 3)],
                         flatbed_pages=[text_page(n, size=(620, 877)) for n in (4, 5, 6, 7)], delay=0.7)
    fakes = [office, FakeScanner("Empty feeder", feeder_pages=[]),
             FakeScanner("Busy scanner", transfer_error=0x80210006),
             FakeScanner("Slow feeder", feeder_pages=[text_page(n, size=(620, 877)) for n in range(1, 9)], delay=0.5)]
    scanner.use(FakeCom(fakes))
    try:
        out = _drive(node, app[1], {"mode": "full", "photos": photos, "shots": _shots()})
    finally:
        scanner.use(None)
    if os.environ.get("SCAN26_DUMP"):
        Path(os.environ["SCAN26_DUMP"]).write_text(json.dumps(out, indent=1), encoding="utf-8")
    assert not out["errors"], out["errors"]
    assert not out["native"], out["native"]
    assert out["start"] == ["Open a PDF", "Scan paper"]
    assert out["title"] == "Scan"
    assert out["status"]["scanner"]["available"] and not out["scannerRadio"]["disabled"]
    assert out["devices"] == ["Office scanner", "Empty feeder", "Busy scanner", "Slow feeder"]

    # The feeder's three pages, shown as they came (not all at once at the end).
    assert out["feeder"]["count"] == 3 and out["feeder"]["msg"] == "", out["feeder"]
    assert len([n for n in out["feederSeen"] if 0 < n < 3]) >= 1, out["feederSeen"]
    assert "3 pages scanned from the feeder" in out["feeder"]["status"]
    # The glass: one a press; then the button and the strip offer "Scan another page".
    assert out["flatbed"]["count"] == 4 and out["flatbed"]["go"] == "Scan another page"
    assert "Scan another page" in out["flatbed"]["status"]
    assert out["another"]["count"] == 5
    assert out["turned"]["turn"] == 90 and "rotate(90deg)" in out["turned"]["transform"]
    assert out["removed"]["count"] == 4 and out["removedThird"]
    assert out["keyMoved"] == 1 and out["keyFocus"] == out["keyFocusId"]
    before, after = out["dragged"]["before"], out["dragged"]["after"]
    assert after[0] == before[-1] and sorted(after) == sorted(before), out["dragged"]
    rescanned = out["rescanned"]
    assert rescanned["after"]["count"] == 4 and rescanned["after"]["ids"][0] != rescanned["old"]
    assert "Page 1 scanned again" in rescanned["after"]["status"]

    assert "feeder is empty" in out["empty"]["msg"] and out["empty"]["count"] == 4
    assert "busy" in out["busyScanner"]["msg"]
    assert out["cancelShown"]
    cancelled = out["cancelled"]
    assert cancelled["had"] < cancelled["after"]["count"] < cancelled["had"] + 8, cancelled
    assert "Stopped" in cancelled["after"]["status"] and "kept" in cancelled["after"]["status"]

    _check_photos_and_pdf(out)


def test_scan_page_without_wia(app, tmp_path, photos):
    node = _node(tmp_path)
    scanner.use(FakeCom(missing=True))  # as on a PC without Windows Image Acquisition
    try:
        out = _drive(node, app[1], {"mode": "nowia", "photos": photos, "shots": _shots()})
    finally:
        scanner.use(None)
    assert not out["errors"], out["errors"]
    assert out["scannerRadio"]["disabled"] and out["scannerRadio"]["photosShown"]
    assert "can’t be used here" in out["scannerRadio"]["why"] and "Windows Image Acquisition" in out["scannerRadio"]["why"]
    assert "Photos work on any computer" in out["scannerRadio"]["why"]
    assert out["tabbable"] >= 6
    _check_photos_and_pdf(out)


def _check_photos_and_pdf(out):
    photos = out["photos"]
    assert photos["after"]["count"] == photos["before"] + 2
    assert out["photoFlags"][-2:] == [True, False]  # the desk photo: no page found, kept as taken
    assert photos["after"]["caps"][-1] == "No edges found"
    assert "edges weren’t clear" in photos["after"]["status"]
    made = out["made"]
    assert made["name"].startswith("Scan ") and made["name"].endswith(".pdf")
    assert made["unsaved"] and made["readerMode"]
    assert made["pages"] == made["expected"], made
    assert made["madeBy"]["op"] == "scan" and made["madeBy"]["from"] is None
    assert "Save" in made["note"] and "Not saved yet" in made["note"], made["note"]
    assert out["back"]["count"] == made["expected"]
