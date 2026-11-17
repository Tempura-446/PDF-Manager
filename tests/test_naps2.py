# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.53.0: scanners that only have a TWAIN driver, through NAPS2 when it is
installed (naps2.py). A stand-in console program (fakenaps2.py) answers the
way NAPS2's source says it does; nothing here needs NAPS2 or a scanner."""

import json
import sys
import time
from pathlib import Path

import pytest

from pdfmanager import naps2, progress, scanner

HERE = Path(__file__).parent


@pytest.fixture
def program(tmp_path, monkeypatch):
    """A fake NAPS2.Console in a folder of its own; returns the file its calls are written to."""
    launcher = tmp_path / ("NAPS2.Console.cmd" if sys.platform == "win32" else "NAPS2.Console")
    if sys.platform == "win32":
        launcher.write_text(f'@"{sys.executable}" "{HERE / "fakenaps2.py"}" %*\n', encoding="utf-8")
    else:
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{HERE / "fakenaps2.py"}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    log = tmp_path / "asked.log"
    monkeypatch.setenv("PDFM_NAPS2", str(launcher))
    monkeypatch.setenv("FAKE_NAPS2_LOG", str(log))
    monkeypatch.setattr(naps2, "_listed", {"at": 0.0, "program": None, "devices": []})
    return log


def asked(log):
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_without_naps2_nothing_is_offered(monkeypatch, tmp_path):
    monkeypatch.setenv("PDFM_NAPS2", str(tmp_path / "not-there.exe"))
    assert naps2.find() is None and not naps2.installed() and naps2.devices() == []
    with pytest.raises(naps2.Naps2Error, match="isn't installed"):
        naps2.scan("naps2:twain:Old Flatbed 3000")


def test_its_twain_scanners_are_listed_once_a_minute(program):
    found = naps2.devices()
    assert [d["id"] for d in found] == ["naps2:twain:Old Flatbed 3000", "naps2:twain:Office Sheetfed TW"]
    assert found[0]["name"] == "Old Flatbed 3000 (through NAPS2)" and found[0]["naps2"] and not found[0]["network"]
    assert asked(program) == [["--listdevices", "--driver", "twain"]]
    naps2.devices()
    assert len(asked(program)) == 1                     # kept: NAPS2 isn't started for every look at the list
    naps2.devices(fresh=True)
    assert len(asked(program)) == 2


def test_they_show_with_the_other_scanners(program, monkeypatch):
    monkeypatch.setattr(scanner, "list_scanners", lambda: [])
    names = [d["name"] for d in scanner.all_scanners(network=False)]
    assert names == ["Old Flatbed 3000 (through NAPS2)", "Office Sheetfed TW (through NAPS2)"]


@pytest.mark.parametrize("mode,source,duplex,asks,count,kind", [
    ("grey", "flatbed", False, ["glass", "gray"], 1, "L"),
    ("colour", "feeder", False, ["feeder", "color"], 3, "RGB"),
    ("bw", "feeder", True, ["duplex", "bw"], 6, "1"),
])
def test_a_scan_asks_naps2_and_gives_the_pages(program, mode, source, duplex, asks, count, kind):
    seen = []
    pages = scanner.scan_any("naps2:twain:Office Sheetfed TW", mode=mode, dpi=200, source=source, duplex=duplex,
                             on_page=lambda image, dpi: seen.append(image.size))
    assert len(pages) == count == len(seen) and all(dpi == 200 for _, dpi in pages)
    assert {image.mode for image, _ in pages} == {kind} and pages[0][0].info["dpi"] == (200, 200)
    said = asked(program)[-1]
    assert said[0] == "-o" and said[1].endswith("scan.pdf")
    assert said[2:] == ["--noprofile", "--driver", "twain", "--device", "Office Sheetfed TW", "--source", asks[0],
                        "--dpi", "200", "--bitdepth", asks[1], "-f", "-v"]
    assert not Path(said[1]).exists()                   # what NAPS2 wrote is gone with its folder


@pytest.mark.parametrize("how,source,code,words", [
    ("empty", "feeder", "empty", "feeder is empty"),
    ("empty", "flatbed", "empty", "sent no page"),
    ("gone", "flatbed", "gone", "couldn't find that scanner"),
    ("offline", "flatbed", "offline", "scanner is offline"),
])
def test_what_went_wrong_is_said_in_words(program, monkeypatch, how, source, code, words):
    monkeypatch.setenv("FAKE_NAPS2", how)
    with pytest.raises(scanner.ScannerError, match=words) as err:
        scanner.scan_any("naps2:twain:Old Flatbed 3000", source=source)
    assert err.value.code == code


def test_what_is_asked_is_checked_first(program):
    for bad, words in ((dict(mode="sepia"), "colour setting"), (dict(source="tray"), "Flatbed or Document feeder"),
                       (dict(dpi=5), "between 50 and 1200")):
        with pytest.raises(naps2.Naps2Error, match=words):
            naps2.scan("naps2:twain:Old Flatbed 3000", **bad)
    with pytest.raises(naps2.Naps2Error, match="choose it again"):
        naps2.scan("naps2:twain:")
    assert not program.exists()                         # NAPS2 wasn't started for any of these


def test_cancelling_stops_naps2(program, monkeypatch):
    monkeypatch.setenv("FAKE_NAPS2", "slow")
    job = progress.Job()
    started = time.monotonic()
    job.start(lambda: naps2.scan("naps2:twain:Old Flatbed 3000"))
    time.sleep(1.0)
    job.cancel()
    job.thread.join(timeout=8)
    assert not job.thread.is_alive() and job.state == "cancelled"
    assert time.monotonic() - started < 5                 # not the ten seconds the scan would have taken
    # ...and what NAPS2's console program started is stopped with it (on Windows it only starts
    # NAPS2.exe and waits): nothing of it is still running, so nothing more is written to its log.
    before = program.read_text(encoding="utf-8")
    time.sleep(0.5)
    assert program.read_text(encoding="utf-8") == before


def test_the_scan_page_is_told_whether_naps2_is_there(program):
    import threading

    from pdfmanager import gui
    from test_more import call

    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base, token = url.split("#token=")
        status = json.loads(call(base.rstrip("/"), token, "/api/scan/status")[2])
        listed = json.loads(call(base.rstrip("/"), token, "/api/scan/devices")[2])["devices"]
    finally:
        srv.shutdown()
        srv.server_close()
    assert status["naps2"] is True
    assert "Office Sheetfed TW (through NAPS2)" in [d["name"] for d in listed]
