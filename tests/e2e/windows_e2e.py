"""End-to-end test of the installed app on Windows.

Run by .github/workflows/windows-e2e.yml after "Install PDF Manager.cmd".
It starts the installed app the way its Start menu shortcut does, drives
its window through the Chrome DevTools protocol (which both Edge and the
WebView2 control behind the app's own window speak), uses a few tools,
closes the window like a person would and checks that nothing of the app
is left running (as Task Manager would show).

    python tests/e2e/windows_e2e.py desktop      # the app's own window
    python tests/e2e/windows_e2e.py app-window   # the Edge app window

Needs: pip install playwright (no browser download: it connects to the
app's window).
"""

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

from playwright.sync_api import sync_playwright

DATA = Path(os.environ["LOCALAPPDATA"]) / "PDF Manager"
APP = DATA / "app"
VENV = APP / "venv" / "Scripts"
PORT = 9333  # remote debugging


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def processes():
    """[(pid, name, command line)] of every running process."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Select-Object ProcessId, Name, CommandLine | ConvertTo-Json"],
        capture_output=True, text=True, check=True).stdout
    return [(p["ProcessId"], p["Name"], p["CommandLine"] or "") for p in json.loads(out)]


def ours():
    """Processes that belong to the app: its Python, and the browser or
    WebView2 processes using the app's data folder."""
    data = str(DATA).lower()
    return [p for p in processes()
            if "-m pdfmanager" in p[2] or "pdfmanager.update" in p[2]
            or (data in p[2].lower() and p[1].lower() != "powershell.exe")]


def wait_for(check, timeout, what):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {what}")


def make_pdf(path, pages=3):
    script = ("import sys; from reportlab.pdfgen import canvas; c = canvas.Canvas(sys.argv[1])\n"
              f"for i in range({pages}):\n"
              "    c.setFont('Helvetica', 24); c.drawString(72, 720, f'End-to-end page {i + 1}'); c.showPage()\n"
              "c.save()")
    subprocess.run([str(VENV / "python.exe"), "-c", script, str(path)], check=True)
    return path


def start(mode, *files):
    env = dict(os.environ, PDFM_WINDOW=mode, PDFM_DEBUG_PORT=str(PORT), PDFM_LOG="1")
    # What the Start menu shortcut runs:
    return subprocess.Popen([str(VENV / "pythonw.exe"), "-I", "-m", "pdfmanager", "gui", *map(str, files)],
                            env=env, cwd=str(Path.home()))


def debugging_ready():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2) as res:
            return json.load(res)
    except OSError:
        return None


def app_page(browser):
    pages = [page for context in browser.contexts for page in context.pages]
    for page in pages:
        # Ask the page: in WebView2, Playwright may still report the
        # about:blank it attached to before the app's page loaded.
        try:
            href = page.evaluate("() => location.href")
        except Exception:
            continue
        if "#token=" in href:
            return page
    return None


def targets():
    """What the window's debugging port lists (for failure messages)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=5) as res:
            return [(t.get("type"), t.get("url")) for t in json.load(res)]
    except OSError as exc:
        return f"can't list: {exc}"


def window_handles(title_part="PDF Manager"):
    """Visible top-level windows whose title contains ``title_part``."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            if title_part in buf.value:
                found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(each, 0)
    return found


def exercise(page, pdf):
    """Use the app: run a tool, then read the PDF in the reader."""
    page.wait_for_selector('.tools button[data-op="rotate"]', timeout=30000)
    log("app page:", page.title())
    page.click('.tools button[data-op="rotate"]')
    page.set_input_files("#file-input", str(pdf))
    page.wait_for_selector(".file-card", timeout=30000)
    page.click("#run")
    page.wait_for_selector(".result-actions", timeout=60000)
    log("tool result:", " ".join(page.inner_text("#result").split())[:120])
    page.click('.tools button[data-op="reader"]')
    page.set_input_files("#reader-host input[type=file]", str(pdf))
    page.wait_for_selector(".page canvas", timeout=60000)
    page.wait_for_function("() => document.querySelector('.rd-title')?.textContent.includes('.pdf')")
    log("reader shows:", page.inner_text(".rd-title"))


def app_log():
    try:
        return (DATA / "app.log").read_text(encoding="utf-8")
    except OSError:
        return "(no app.log)"


def stacks(token):
    """Every thread's stack in the running app (PDFM_LOG makes it available)."""
    try:
        req = urllib.request.Request("http://127.0.0.1:47821/api/debug/stacks", headers={"X-Pdfm-Token": token})
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.read().decode("utf-8", "replace")
    except OSError as exc:
        return f"(no stacks: {exc})"


def no_leftovers(what):
    deadline = time.monotonic() + 45
    left = ours()
    while left and time.monotonic() < deadline:
        time.sleep(1)
        left = ours()
    if left:
        for pid, name, cmd in left:
            log("STILL RUNNING:", pid, name, cmd[:200])
        log("app.log:\n" + app_log())
        raise AssertionError(f"processes left running after {what}")
    log(f"nothing left running after {what}")


def run(mode):
    assert not ours(), f"something of the app is already running: {ours()}"
    tmp = Path(tempfile.mkdtemp(prefix="pdfm-e2e-"))
    pdf = make_pdf(tmp / "e2e.pdf")
    proc = start(mode)
    try:
        wait_for(debugging_ready, 90, "the window's debugging port")
    except AssertionError:
        note = DATA / "window-error.txt"
        if note.exists():
            log("window-error.txt:", note.read_text(encoding="utf-8"))
        log("windows:", window_handles(), "app processes:", ours())
        raise
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        try:
            page = wait_for(lambda: app_page(browser), 30, "the app page")
        except AssertionError:
            log("pages:", [pg.url for c in browser.contexts for pg in c.pages], "targets:", targets())
            raise
        exercise(page, pdf)

        # Opening a PDF again (e.g. from Explorer) hands it to the running app.
        second = make_pdf(tmp / "second.pdf", pages=2)
        handed = start(mode, second)
        assert handed.wait(60) == 0, "the second launch should hand the file over and end"
        # Its own window opens the file in a new tab; with the Edge window the
        # running app opens it in a new window of its own.
        shows_second = ("() => [...document.querySelectorAll('.rd-doctab, .rd-title')]"
                        ".some((e) => e.textContent.includes('second.pdf'))")

        def window_with_second():
            for pg in [pg for c in browser.contexts for pg in c.pages]:
                try:
                    if pg.evaluate(shows_second):
                        return pg
                except Exception:
                    continue  # still loading
            return None

        shown = wait_for(window_with_second, 45, "the second file in the reader")
        log("second launch handed its file to the running app",
            "(same window)" if shown == page else "(new window)")
        if shown != page:
            shown.close()

        if mode == "app-window":
            # Restarting (as after an update): this copy ends, a new one opens.
            old_pid = json.loads((DATA / "instance.json").read_text(encoding="utf-8"))["pid"]
            token = page.evaluate("() => new URLSearchParams(location.hash.slice(1)).get('token')")
            status = page.evaluate(
                "async (t) => (await fetch('/api/update/restart', {method: 'POST', headers: {'X-Pdfm-Token': t}})).status",
                token)
            assert status == 200, f"restart answered {status}"
            try:
                wait_for(lambda: not debugging_ready() or None, 60, "the old window to close")
            except AssertionError:
                log("app.log:\n" + app_log())
                log("old app's threads:\n" + stacks(token))
                log("processes:", ours())
                raise

            def new_instance():
                try:
                    record = json.loads((DATA / "instance.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return None
                return record if record["pid"] != old_pid and debugging_ready() else None

            wait_for(new_instance, 90, "the app to start again")
            log("restarted:", old_pid, "->", new_instance()["pid"])
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
            page = wait_for(lambda: app_page(browser), 30, "the restarted app page")
            page.wait_for_selector('.tools button[data-op="rotate"]', timeout=30000)
            # Close the window like a person would (the app window's only page).
            page.close()
        else:
            # (Not browser.close(): that would end the window's web engine.)
            windows = wait_for(window_handles, 10, "the app window")
            log("closing window:", windows)
            for hwnd, _ in windows:
                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    try:
        proc.wait(60)
    except subprocess.TimeoutExpired:
        pass
    no_leftovers(f"closing the {mode} window")
    assert not (DATA / "instance.json").exists(), "instance.json should be removed when the app ends"


if __name__ == "__main__":
    if sys.platform != "win32":
        sys.exit("Windows only")
    run(sys.argv[1] if len(sys.argv) > 1 else "desktop")
    log("passed")
