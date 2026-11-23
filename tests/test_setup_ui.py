# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.35.0 (packaging/windows/setup_ui.py, setup.html, install.ps1): the
setup window. Its program serves the page to this PC only, takes yes or no
for each choice and nothing else, follows the installer's "@@" lines step
by step, and ends as installed, failed or closed. The page itself is tried
in a browser through Node's Playwright (skipped where there is none), with
the installer stood in for by a small script. SETUP_SHOTS=<folder> saves
light and dark screenshots of each screen."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "packaging" / "windows"
INSTALL_PS1 = WINDOWS / "install.ps1"
REAL_LOCALAPPDATA = os.environ.get("LOCALAPPDATA")


def load():
    spec = importlib.util.spec_from_file_location("setup_ui", WINDOWS / "setup_ui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup_ui = load()
DEFAULTS = {"desktop": True, "openWith": True, "contextMenu": True, "extras": False}

# A stand-in for install.ps1 -Engine: the same lines, in a second each.
ENGINE = r'''
import sys, time
flags = sys.argv[1:]
pause = 0.25
def out(line):
    print(line, flush=True)
out("@@step python|Looking for Python 3.10 to 3.14"); out("Found C:\\Python313\\python.exe"); time.sleep(pause)
out("@@step app|Installing ZPDF"); out("Installing the pinned packages, each checked against its hash"); time.sleep(pause)
if "--fail" in flags:
    out("A package didn't install or didn't match its pinned hash (see above). Nothing unchecked was installed instead.")
    out("At C:\\ZPDF\\packaging\\windows\\install.ps1:201 char:9")
    out("+         throw \"A package didn't install\"")
    out("    + CategoryInfo          : OperationStopped: (...) [], RuntimeException")
    out("    + FullyQualifiedErrorId : A package didn't install")
    sys.exit(1)
out("Done"); time.sleep(pause)
out("@@step shortcuts|Adding shortcuts"); out("Start menu shortcut added"); time.sleep(pause)
if "-WithExtras" in flags:
    out("@@step extras|Adding Tesseract and Ghostscript"); time.sleep(pause)
    out("@@extra OCRmyPDF|1"); out("@@extra Tesseract (second OCR engine)|1"); out("@@extra Ghostscript (PDF/A)|0")
out("@@future something a newer installer says")
out("@@done")
'''


def fake_engine(tmp_path, fail=False):
    script = tmp_path / "engine.py"
    script.write_text(ENGINE, encoding="utf-8")

    def engine(options):
        real = setup_ui.Setup(ROOT, INSTALL_PS1, DEFAULTS)._engine(options)
        return [sys.executable, str(script), *real[real.index("-Engine"):], *(["--fail"] if fail else [])]
    return engine


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    made = []

    def make(fail=False, defaults=DEFAULTS):
        setup = setup_ui.Setup(ROOT, INSTALL_PS1, defaults, engine=fake_engine(tmp_path, fail))
        server, url, token = setup_ui.make_server(setup)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        made.append(server)
        return setup, url, token
    yield make
    for server in made:
        server.shutdown()
        server.server_close()


def call(url, token=None, body=None, host=None):
    request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                     headers={**({"X-Setup-Token": token} if token else {}),
                                              **({"Host": host} if host else {}),
                                              **({"Content-Type": "application/json"} if body is not None else {})})
    try:
        with urllib.request.urlopen(request) as res:
            data = res.read()
            kind = res.headers["Content-Type"]
            return res.status, (json.loads(data) if kind.startswith("application/json")
                                else data.decode() if kind.startswith("text/") else data)
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def until(setup, phases, seconds=20):
    end = time.time() + seconds
    while time.time() < end and setup.phase not in phases:
        time.sleep(0.05)
    return setup.state()


def test_the_page_and_its_calls_need_the_token_and_this_pc(served):
    setup, url, token = served()
    base = url.split("/?")[0]
    status, page = call(url)
    assert status == 200 and "ZPDF Setup" in page and token in page and "/*CONFIG*/" not in page
    assert call(base + "/")[0] == 403 and call(base + "/?t=wrong")[0] == 403
    assert call(base + "/api/state")[0] == 403 and call(base + "/api/state", "wrong")[0] == 403
    assert call(base + "/api/start", None, {})[0] == 403
    assert call(base + "/api/state", token, host="evil.example")[0] == 403  # another site's name for this address
    status, state = call(base + "/api/state", token)
    assert status == 200 and state["phase"] == "welcome"
    assert call(base + "/fonts/instrument-sans-latin-400-normal.woff2")[0] == 200
    status, icon = call(base + "/icon.svg")  # the window's own icon (else Edge shows a globe)
    assert status == 200 and icon.startswith(b"<svg xmlns=") and 'rel="icon" href="/icon.svg"' in page
    for path in ("/fonts/../gui.html", "/fonts/..%2f..%2fgui.html", "/setup_ui.py", "/fonts/OFL-instrument-sans.txt"):
        assert call(base + path)[0] == 404, path


def test_the_page_is_told_what_it_installs(served, tmp_path):
    from pdfmanager import __version__
    setup, url, token = served(defaults={**DEFAULTS, "desktop": False})
    config = setup.config(token)
    assert config["version"] == __version__ and config["updating"] is False and config["desktop"] is False
    assert config["appDir"].endswith(os.path.join("PDF Manager", "app")) and config["mark"].startswith("<svg")
    (tmp_path / "Local" / "PDF Manager" / "app" / "venv").mkdir(parents=True)
    assert setup.config(token)["updating"] is True  # already installed: an update


def test_an_installation_step_by_step(served):
    setup, url, token = served()
    base = url.split("/?")[0]
    status, state = call(base + "/api/start", token, {"desktop": False, "extras": True})
    assert status == 200 and state["phase"] == "installing"
    assert [s["key"] for s in state["steps"]] == ["python", "app", "shortcuts", "extras"]
    seen = set()
    while setup.phase == "installing":
        now = [s["key"] for s in setup.state()["steps"] if s["state"] == "now"]
        assert len(now) <= 1
        seen.update(now)
        time.sleep(0.03)
    state = setup.state()
    assert state["phase"] == "done" and seen >= {"app", "shortcuts"}
    assert [s["state"] for s in state["steps"]] == ["done"] * 4
    assert state["extras"] == [{"name": "OCRmyPDF", "ready": True}, {"name": "Tesseract (second OCR engine)", "ready": True},
                               {"name": "Ghostscript (PDF/A)", "ready": False}]
    assert state["options"] == {"desktop": False, "openWith": True, "contextMenu": True, "extras": True}
    assert "Start menu shortcut added" in state["log"] and not any(line.startswith("@@") for line in state["log"])
    assert setup.exit_code() == setup_ui.OK


def test_only_yes_or_no_reaches_the_installer(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    setup = setup_ui.Setup(ROOT, INSTALL_PS1, DEFAULTS)
    assert setup._engine({"desktop": True, "openWith": True, "contextMenu": True, "extras": False})[-3:] == ["-Engine", "-NoLaunch", "-NoExtras"]
    assert setup._engine({"desktop": False, "openWith": False, "contextMenu": False, "extras": True})[-5:] == [
        "-NoLaunch", "-NoDesktop", "-NoOpenWith", "-NoContextMenu", "-WithExtras"]
    ran = []
    setup.engine = lambda options: ran.append(options) or [sys.executable, "-c", "print('@@done')"]
    setup.start({"desktop": "; Remove-Item C:\\ -Recurse", "extras": ["x"], "installer": "evil.ps1", "openWith": 0})
    until(setup, ("done", "failed"))
    assert ran == [{"desktop": True, "openWith": False, "contextMenu": True, "extras": True}]  # truthy or not, nothing more
    assert setup.installer == str(INSTALL_PS1)


def test_a_failure_says_why_and_can_be_tried_again(served):
    setup, url, token = served(fail=True)
    base = url.split("/?")[0]
    call(base + "/api/start", token, {})
    state = until(setup, ("done", "failed"))
    assert state["phase"] == "failed" and setup.exit_code() == setup_ui.FAILED
    assert state["error"] == "A package didn't install or didn't match its pinned hash. Nothing unchecked was installed instead."
    assert [s["state"] for s in state["steps"]] == ["done", "failed", "pending"]
    assert call(base + "/api/quit", token, {})[0] == 200 and setup.finished.is_set()
    # Try again: starts afresh.
    setup.engine = lambda options: [sys.executable, "-c", "print('@@step app|x'); print('@@done')"]
    call(base + "/api/start", token, {})
    assert until(setup, ("done", "failed"))["phase"] == "done"


def test_the_reason_is_what_stopped_it_not_where_powershell_says_it_was():
    """From the Windows check: the window gave "252 char:28" as the reason
    (PowerShell's report of the place, wrapped over two lines)."""
    log = ["Installing ZPDF in C:\\Users\\me\\AppData\\Local\\PDF Manager\\app",
           "  File \"tomllib\\_parser.py\", line 102, in loads",
           "tomllib.TOMLDecodeError: Expected ']' at the end of a table declaration (at line 1, column 9)",
           "Installing ZPDF's packages failed (see above).",
           "At C:\\Users\\me\\Downloads\\PDF-M",
           "anager-copy\\packaging\\windows\\install.ps1:252 char:28",
           "+ if ($LASTEXITCODE -ne 0) { throw \"Installing ZPDF's packages failed (see  ...",
           "+                            ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~",
           "    + CategoryInfo          : OperationStopped: (Installing ZPDF...ed (see above).:String) [], RuntimeException",
           "    + FullyQualifiedErrorId : Installing ZPDF's packages failed (see above)."]
    wanted = ("Installing ZPDF's packages failed. (tomllib.TOMLDecodeError: Expected ']' at the end of a table "
              "declaration (at line 1, column 9))")
    assert setup_ui._reason(log) == wanted
    # Told by the installer itself on its "@@error" line: that, with the cause.
    assert setup_ui._reason(log[:3], "Installing ZPDF's packages failed (see above).") == wanted
    assert setup_ui._reason(["Looking for Python"], "Python was installed but can't be found.") == "Python was installed but can't be found."
    assert setup_ui._reason(["ERROR: Could not find a version that satisfies the requirement pikepdf"], "Installing ZPDF's packages failed.") \
        == "Installing ZPDF's packages failed. (ERROR: Could not find a version that satisfies the requirement pikepdf)"
    assert setup_ui._reason([]) == "The installer stopped without saying why."


def test_the_installers_own_word_on_what_stopped_it(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    script = "print('@@step app|x'); print('pip said something'); print('@@error The disk is full.'); raise SystemExit(1)"
    setup = setup_ui.Setup(ROOT, INSTALL_PS1, DEFAULTS, engine=lambda options: [sys.executable, "-c", script])
    setup.start({})
    state = until(setup, ("done", "failed"))
    assert state["phase"] == "failed" and state["error"] == "The disk is full." and "@@error" not in "\n".join(state["log"])


def test_closed_before_installing_and_while_installing(served):
    setup, url, token = served()
    base = url.split("/?")[0]
    assert setup.exit_code() == setup_ui.CANCELLED
    call(base + "/api/start", token, {})
    call(base + "/api/quit", token, {})
    assert not setup.finished.is_set()  # asked to close while installing: it goes on
    assert until(setup, ("done", "failed"))["phase"] == "done"


def test_an_installer_that_cant_be_started(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    setup = setup_ui.Setup(ROOT, INSTALL_PS1, DEFAULTS, engine=lambda options: [str(tmp_path / "no-such-program")])
    setup.start({})
    state = until(setup, ("done", "failed"))
    assert state["phase"] == "failed" and "could not be started" in state["error"]


def test_no_edge_no_window(monkeypatch, tmp_path):
    for name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert setup_ui.find_edge() is None
    assert setup_ui.main(["--source", str(ROOT), "--installer", str(INSTALL_PS1)]) == setup_ui.NO_WINDOW
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"MZ")
    assert setup_ui.find_edge() == str(edge)


def test_the_installer_script_hands_over_to_the_window_and_runs_for_it():
    text = INSTALL_PS1.read_text(encoding="utf-8-sig")
    params = text.split("param(", 1)[1].split("\n)", 1)[0]
    assert "[switch]$Console" in params and "[switch]$Engine" in params
    # The window is started after Python is found, never in an automated run or with -Console or -Engine.
    hand_over = text.split('Step "python"', 1)[1].split('Step "app"', 1)[0]
    assert "setup_ui.py" in hand_over and "-not $Engine -and -not $Console -and -not $automated" in hand_over
    for code, then in (("0", "exit 0"), ("2", "exit 0"), ("1", "exit 1")):
        assert f"if ($code -eq {code})" in hand_over and then in hand_over.split(f"if ($code -eq {code})", 1)[1].split("\n", 1)[0]
    assert "carrying on here" in hand_over  # no window: the console does it
    # Run by the window it marks its steps and asks nothing.
    for key, _ in setup_ui.STEPS:
        assert f'Step "{key}"' in text or f"@@step {key}|" in text, key
    assert 'Write-Host "@@step $key|$text"' in text and 'Write-Host "@@done"' in text and "@@extra" in text
    # A stop is told on one line; pip's lines are shown by the window, kept quiet in the console.
    assert 'trap {' in text and 'Write-Host "@@error $_"; exit 1' in text
    assert '$PipSays = "--quiet"' in text and 'if ($Engine) { $PipSays = "--progress-bar=off" }' in text
    assert "--disable-pip-version-check --quiet" not in text and text.count("--disable-pip-version-check $PipSays") == 7
    # Already installed, the two programs are still listed at the end.
    already = text.split('Write-Host "@@note extras|Already installed"', 1)[1].split("}", 1)[0]
    assert already.count("@@extra ") == 3 and already.count("|1") == 3
    # Not the whole folder: said plainly, not as an exception.
    assert "throw \"Run this from" not in text and "pyproject.toml is missing" in text
    assert text.count("Read-Host") == 1
    asking = text.split("Read-Host", 1)[0].rsplit("if ($complete -or $NoExtras)", 1)[1]
    assert "elseif ($Engine -or $automated)" in asking
    # What the window's program passes is what the script takes.
    for flag in ("-Engine", "-NoLaunch", "-NoDesktop", "-NoOpenWith", "-NoContextMenu", "-WithExtras", "-NoExtras"):
        assert f"[switch]${flag[1:]}" in params, flag


def test_the_release_carries_the_window():
    sys.path.insert(0, str(ROOT / "packaging"))
    try:
        import release
    finally:
        sys.path.pop(0)
    names = release.release_files(ROOT) if hasattr(release, "release_files") else None
    if names is None:
        assert "packaging/windows/" in release.INCLUDE
        return
    assert "packaging/windows/setup_ui.py" in names and "packaging/windows/setup.html" in names


def test_the_setup_window_in_a_browser(served, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    ok, ok_url, _ = served()
    extras, extras_url, _ = served()
    bad, bad_url, _ = served(fail=True)
    fresh, fresh_url, _ = served()  # (never started: for the first screen in dark and small)
    cfg = {"ok": ok_url, "extras": extras_url, "bad": bad_url, "fresh": fresh_url, "shots": os.environ.get("SETUP_SHOTS")}
    # (Playwright looks for its browsers under the real LOCALAPPDATA, which the fixture stands in for.)
    env = {**os.environ, **({"LOCALAPPDATA": REAL_LOCALAPPDATA} if REAL_LOCALAPPDATA else {})}
    run = subprocess.run([node, str(Path(__file__).with_name("setupwin.js")), json.dumps(cfg)], capture_output=True,
                         text=True, encoding="utf-8", timeout=300, env=env)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]
    # Welcome: what it is, the choices, one main action; nothing scrolls sideways or is cut off.
    w = out["welcome"]
    assert w["title"] == "Install ZPDF" and w["primary"] == "Install" and w["focus"] == "primary"
    assert w["choices"] == [["Desktop shortcut", True], ["Open PDFs with ZPDF", True],
                            ["Right-click menu in File Explorer", True], ["Tesseract and Ghostscript", False]]
    assert w["fits"] and w["mark"] and w["fontLoaded"]
    assert min(w["targets"]) >= 40, w["targets"]            # buttons and choices big enough to hit
    assert w["contrast"] >= 4.5 and w["mutedContrast"] >= 4.5, w
    # Keyboard: Tab reaches every choice and both buttons, each with a visible ring; Space ticks.
    tabbed = [t for t in out["tabbed"] if t]  # (from the main button, Tab first leaves the page)
    assert tabbed[:4] == ["opt-desktop", "opt-openwith", "opt-menu", "opt-extras"]
    assert "secondary" in tabbed and out["ring"] and out["spaceTicks"]
    # Installing: steps in order, one at a time, a progress bar that moves, details on request.
    i = out["installing"]
    assert i["title"] == "Installing ZPDF" and i["steps"] == ["Python", "ZPDF and its packages", "Shortcuts and menus"]
    assert i["buttonsHidden"] and "doesn't stop the installation" in i["foot"]
    assert i["progress"] == sorted(i["progress"]) and i["progress"][-1] > i["progress"][0], i["progress"]
    assert i["current"] and i["detailsOpen"] and "Found" in i["log"]
    # Done: where to find it, Open ZPDF as the main action.
    d = out["done"]
    assert d["title"] == "ZPDF is installed" and d["primary"] == "Open ZPDF" and d["secondary"] == "Close" and d["focus"] == "primary"
    assert "Start menu or on your desktop" in d["msg"] and d["live"] == "ZPDF is installed."
    # With the two programs asked for: a fourth step, and what became of each.
    assert out["extras"]["steps"][-1] == "Tesseract and Ghostscript"
    assert out["extras"]["facts"] == [["Ready", "OCRmyPDF"], ["Ready", "Tesseract (second OCR engine)"],
                                      ["Not installed", "Ghostscript (PDF/A)"]]
    assert "Start menu." in out["extras"]["msg"] and "desktop" not in out["extras"]["msg"]  # (its desktop box was unticked)
    # Failed: what went wrong in plain words, the details, Try again.
    f = out["failed"]
    assert f["title"] == "The installation didn't finish" and f["primary"] == "Try again" and f["copy"]
    assert f["msg"].startswith("A package didn't install or didn't match its pinned hash")
    assert "CategoryInfo" in f["log"] and "It stopped at \u201cZPDF and its packages\u201d." in f["more"]
    # Dark: the same, readable.
    assert out["dark"]["contrast"] >= 4.5 and out["dark"]["mutedContrast"] >= 4.5 and out["dark"]["fits"], out["dark"]
    # A small window (125 % scaling on a small laptop): still all there.
    assert out["small"]["fits"] and min(out["small"]["targets"]) >= 40, out["small"]
    # The window as Edge really gives it at 100 % (its title bar and edges taken off): nothing scrolls.
    for name in ("asked", "old"):
        assert out[name]["fits"], (name, out[name])
