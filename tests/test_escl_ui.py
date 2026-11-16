# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.44.0: a network scanner in the app: through the server, the command
line, and the Scan page in a browser (esclui.js), with the fake scanner of
test_escl.py."""

import json
import os
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from pdfmanager import escl, gui
from pdfmanager import scanner as scanners
from test_escl import scanner  # noqa: F401 - the fake network scanner (a fixture)

HERE = Path(__file__).parent


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, url
    srv.shutdown()
    srv.server_close()


def call(url, path, data=None):
    base, token = url.split("#token=")
    req = urllib.request.Request(base.rstrip("/") + path, headers={"X-Pdfm-Token": token, "Content-Type": "application/json"},
                                 data=None if data is None else json.dumps(data).encode())
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_a_network_scanner_through_the_server(app, scanner):  # noqa: F811
    _, url = app
    status, state = call(url, "/api/scan/status")
    assert status == 200 and state["scanner"]["available"] is True
    assert call(url, "/api/scan/devices")[1]["devices"] == []
    status, said = call(url, "/api/scan/add-network", {"address": "127.0.0.1:9"})
    assert status == 400 and "didn't answer" in said["error"]
    status, added = call(url, "/api/scan/add-network", {"address": f"127.0.0.1:{scanner.port}"})
    device = added["device"]
    assert status == 200 and device["id"] == scanner.id and device["network"] and device["duplex"] and device["feeder"]
    assert [d["id"] for d in call(url, "/api/scan/devices")[1]["devices"]] == [scanner.id]
    box = call(url, "/api/scan/box", {})[1]["box"]
    status, got = call(url, "/api/scan/acquire", {"box": box, "device": scanner.id, "source": "feeder", "duplex": True,
                                                  "dpi": 300, "mode": "grey"})
    assert status == 200 and len(got["pages"]) == 6 and got["pages"][0]["dpi"] == 300
    scanner.sheets, scanner.adf = 0, "ScannerAdfEmpty"
    status, said = call(url, "/api/scan/acquire", {"box": box, "device": scanner.id, "source": "feeder"})
    assert status == 400 and "feeder is empty" in said["error"]
    assert call(url, "/api/scan/forget-network", {"device": scanner.id})[0] == 200
    assert call(url, "/api/scan/devices")[1]["devices"] == []


def test_both_kinds_of_scanner_are_listed_and_the_command_line(scanner, tmp_path, capsys):  # noqa: F811
    from pdfmanager.cli import main

    assert scanners.all_scanners() == []                       # (no WIA here, nothing remembered)
    assert main(["scan", "--add", f"127.0.0.1:{scanner.port}"]) == 0
    assert "added Acme OfficeScan 300 (network)" in capsys.readouterr().out
    (listed,) = scanners.all_scanners()
    assert listed["id"] == scanner.id and listed["network"] and listed["duplex"]
    assert main(["scan", "--list"]) == 0
    assert "Acme OfficeScan 300 (network) (feeder, both sides)" in capsys.readouterr().out
    out = tmp_path / "scan.pdf"
    assert main(["scan", "-o", str(out), "--feeder", "--duplex", "--no-ocr", "--keep-blank"]) == 0
    from pypdf import PdfReader

    assert len(PdfReader(str(out)).pages) == 6
    with pytest.raises(scanners.ScannerError, match="no document feeder"):
        scanner.feeder = False
        scanners.scan_any(scanner.id, source="feeder")
    escl.forget(scanner.id)


def test_the_scan_page_in_a_browser(app, scanner, tmp_path):  # noqa: F811
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    shots = os.environ.get("ZPDF_SHOTS")
    run = subprocess.run([node, str(HERE / "esclui.js"), app[1], f"127.0.0.1:{scanner.port}"] + ([shots] if shots else []),
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]
    assert not out["before"]["scannerOff"] and out["before"]["devices"] == 0 and out["before"]["addHidden"]
    assert "added by its address" in out["noneSaid"]
    assert "didn't answer" in out["wrong"].replace("’", "'")
    assert out["added"]["name"] == "Acme OfficeScan 300 (network)" and out["added"]["addHidden"] and out["added"]["forget"]
    assert out["added"]["go"] and not out["added"]["duplexShown"] and out["duplexWithFeeder"]
    assert out["pages"] == 6                                    # three sheets, both sides
    assert out["afterForget"] == "No scanner found"
