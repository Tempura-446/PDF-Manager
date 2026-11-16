# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.45.0: "My scanner isn't listed" on the Scan page (scanfolder.js drives
it in a browser): the folder a scanner saves to is watched from there."""

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from pdfmanager import appdata, core, gui, watchfolders
from test_scan_folder import aged, picture

HERE = Path(__file__).parent


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "_data_dir", lambda: tmp_path / "zpdf-data")
    monkeypatch.setattr(core, "ocr", lambda src, out, **kw: Path(out).write_bytes(Path(src).read_bytes()))
    monkeypatch.setattr(watchfolders, "INTERVAL", 0.3)
    from pdfmanager import watch

    monkeypatch.setattr(watch, "_arrived", lambda path, state: state.sizes[path][1])   # (aged(): see test_scan_folder.py)
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.RequestHandlerClass.watcher.stop()
    srv.shutdown()
    srv.server_close()


def test_a_folder_for_scans_from_the_scan_page(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    folder = tmp_path / "Scans"
    folder.mkdir()
    for n in (1, 2):                     # a stack of two pages, scanned a while ago
        aged(picture(folder / f"page_{n}.jpg"), watchfolders.BATCH_GAP + 10 - n)
    shots = os.environ.get("ZPDF_SHOTS")
    run = subprocess.run([node, str(HERE / "scanfolder.js"), app[1], str(folder), str(folder / "Done" / "page_1.pdf")]
                         + ([shots] if shots else []), capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]
    assert out["before"] == {"guideHidden": True, "expanded": "false"}
    assert out["open"]["expanded"] == "true" and out["open"]["batch"] and out["open"]["focused"] == "scan-folder"
    assert out["open"]["choices"][0] == "Made searchable (OCR)"
    assert "Type the folder" in out["empty"] and "no folder" in out["missing"]
    assert "now watches" in out["added"]["said"] and str(folder) in out["added"]["listed"][0]
    assert out["done"] and "1 done" in out["after"][0]

    (entry,) = watchfolders.load()
    assert entry["images"] and entry["batch"] and entry["do"] == {"tool": "ocr"} and entry["results"] == "done"
    from pypdf import PdfReader

    assert len(PdfReader(str(folder / "Done" / "page_1.pdf")).pages) == 2      # the stack: one document
