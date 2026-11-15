# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.43.0: the reader's Measurements panel saves a quantity schedule
(quantitiesui.js drives it in a browser)."""

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from pdfmanager import gui
from test_quantities import marked

HERE = Path(__file__).parent


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def test_the_schedule_from_the_measurements_panel(app, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    srv, url = app
    ident = srv.RequestHandlerClass.opened.add(marked(tmp_path))
    got = tmp_path / "got"
    got.mkdir()
    shots = os.environ.get("ZPDF_SHOTS")
    run = subprocess.run([node, str(HERE / "quantitiesui.js"), url, ident, str(got)] + ([shots] if shots else []),
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]
    assert "Excel" in out["buttons"] and "CSV" in out["buttons"]
    assert out["emptyButtons"] == ["CSV", "Excel"]          # (Windows check of 31261d4: unreachable with an empty list)
    assert out["Excel"] == "plan quantities.xlsx" and out["CSV"] == "plan quantities.csv"

    rows = list(openpyxl.load_workbook(got / "book.xlsx")["Quantities"].iter_rows(values_only=True))
    names = {r[3]: r for r in rows[1:]}
    assert len(rows) == 7                                   # five in the PDF, one in the reader's list
    south = names["South wall"]                             # from the list: worked out by the reader
    assert south[4] == pytest.approx(12) and south[5] == "ft" and south[2] == "Walls" and south[1] == "Length"
    assert names["North wall"][4] == pytest.approx(12) and names["Living room"][4] == pytest.approx(196)
    totals = {(r[0], r[1]): r for r in openpyxl.load_workbook(got / "book.xlsx")["Totals"].iter_rows(min_row=2, values_only=True)}
    assert totals[("Walls", "Length")][2] == pytest.approx(24)
    assert "South wall" in (got / "list.csv").read_text(encoding="utf-8-sig")
