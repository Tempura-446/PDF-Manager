# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.46.0: ZPDF in the background. While a folder is watched, closing the
window can hide it and leave an icon in the notification area; ZPDF can
start that way at sign-in. The window, the icon and the registry are fakes
here; the real icon is tried where there is a Windows to try it on."""

import json
import sys
import threading
import time
import urllib.request

import pytest

from pdfmanager import appdata, background, gui, tray
from test_more import FakeRegistry


class Registry(FakeRegistry):
    def QueryValueEx(self, key, name):
        if name not in self.keys[key.path]:
            raise FileNotFoundError(name)
        return self.keys[key.path][name], 1


@pytest.fixture
def windows(tmp_path, monkeypatch):
    """As on Windows: its registry (a fake), and a data folder of this test's own."""
    monkeypatch.setattr(appdata, "_data_dir", lambda: tmp_path / "zpdf-data")
    reg = Registry()
    monkeypatch.setitem(sys.modules, "winreg", reg)
    monkeypatch.setattr(background, "_windows", lambda: True)
    return reg


class Watcher:
    def __init__(self, *paused):
        self.folders = [{"id": str(n), "folder": f"C:/Scans{n}", "paused": p} for n, p in enumerate(paused)]

    def list(self):
        return [dict(f) for f in self.folders]

    def set_paused(self, folder_id, paused):
        next(f for f in self.folders if f["id"] == folder_id)["paused"] = paused


class Unsaved:
    def __init__(self, *names):
        self._names = list(names)

    def names(self):
        return list(self._names)


class Window:
    def __init__(self):
        self.did = []

    def hide(self):
        self.did.append("hide")

    def show(self):
        self.did.append("show")

    def destroy(self):
        self.did.append("destroy")


class Icon:
    def __init__(self, works=True):
        self.works, self.did = works, []

    def start(self):
        self.did.append("start")
        return self.works

    def notify(self, title, text):
        self.did.append(("note", text))

    def stop(self):
        self.did.append("stop")


class Handler:
    quit_requested = False

    def __init__(self, watcher, unsaved=()):
        self.watcher, self.unsaved = watcher, Unsaved(*unsaved)


def behind(watcher, answers=(), unsaved=(), icon=None, may_close=True):
    asked, raised = [], []
    answers = list(answers)

    def ask(text):
        asked.append(text)
        return answers.pop(0)

    window, icon = Window(), icon or Icon()
    it = background.InBackground(Handler(watcher, unsaved), window, may_close=lambda: may_close, ask=ask,
                                 bring_forward=lambda: raised.append(True), tray=icon)
    return it, window, icon, asked, raised


def closed(it):
    """What the close button does, once the hiding (on its own thread) has happened."""
    answer = it.closing()
    for thread in threading.enumerate():
        if thread.name == "hide-window":
            thread.join(5)
    return answer


def test_closing_asks_once_then_keeps_watching_from_the_icon(windows):
    it, window, icon, asked, raised = behind(Watcher(False), answers=[True])
    assert closed(it) is False                                   # the window doesn't close: it hides
    assert window.did == ["hide"] and it.hidden and "background" in asked[0]
    assert icon.did[0] == "start" and icon.did[1][0] == "note" and "Still watching" in icon.did[1][1]
    assert background.keep() == "yes"                            # the answer is kept
    it.open()
    assert window.did == ["hide", "show"] and not it.hidden and raised and icon.did[-1] == "stop"
    assert closed(it) is False and len(asked) == 1               # not asked again

    # "No": ZPDF closes, now and from then on, without asking again
    background.set_keep("ask")
    it, window, icon, asked, _ = behind(Watcher(False), answers=[False])
    assert closed(it) is True and window.did == [] and background.keep() == "no"
    assert closed(it) is True and len(asked) == 1


def test_what_a_watched_folder_does_is_said_from_the_icon_while_hidden(windows, tmp_path, monkeypatch):
    from pathlib import Path

    from pdfmanager import core, watchfolders
    from test_more import text_pdf

    monkeypatch.setattr(core, "ocr", lambda src, out, **kw: Path(out).write_bytes(Path(src).read_bytes()))
    inbox = tmp_path / "Scans"
    inbox.mkdir()
    watcher = watchfolders.WatchFolders(thread=False)
    watcher.add(inbox, {"tool": "ocr"})
    background.set_keep("yes")
    it, window, icon, *_ = behind(watcher)
    text_pdf(inbox / "shown.pdf")
    watcher.poll_all()
    watcher.poll_all()
    assert not any(isinstance(d, tuple) for d in icon.did)       # window open: the page says it, not the icon
    closed(it)
    text_pdf(inbox / "hidden.pdf")
    watcher.poll_all()
    watcher.poll_all()
    notes = [d[1] for d in icon.did if isinstance(d, tuple)]
    assert notes[-1] == "hidden.pdf is done: hidden.pdf in Done"


def test_nothing_watched_means_closing_closes(windows):
    for watcher in (Watcher(), Watcher(True, True)):             # no folders; all paused
        it, window, icon, asked, _ = behind(watcher)
        assert closed(it) is True and not asked and window.did == [] and icon.did == []
    background.set_keep("yes")
    it, window, *_ = behind(Watcher(True))
    assert closed(it) is True                                    # even with "keep watching": nothing to watch


def test_unsaved_work_is_asked_about_only_when_really_closing(windows):
    background.set_keep("yes")
    it, window, icon, asked, _ = behind(Watcher(False), unsaved=["Offer.pdf"], may_close=False)
    assert closed(it) is False and window.did == ["hide"]        # hidden: the work stays open in it, nothing asked
    # Quit from the icon: the window comes back, and closing asks as usual (here: stays open)
    it.quit()
    assert window.did == ["hide", "show", "destroy"] and it.leaving
    assert it.closing() is False                                 # may_close said no: it stays, shown
    # with nothing unsaved, Quit just quits
    it, window, icon, *_ = behind(Watcher(False))
    closed(it)
    it.quit()
    assert window.did == ["hide", "destroy"] and icon.did[-1] == "stop" and it.closing() is True


def test_a_restart_for_an_update_is_never_turned_into_hiding(windows):
    background.set_keep("yes")
    it, window, *_ = behind(Watcher(False))
    it.handler.quit_requested = True
    assert it.closing() is True and window.did == []


def test_without_an_icon_the_window_is_never_left_out_of_sight(windows):
    background.set_keep("yes")
    it, window, icon, _, raised = behind(Watcher(False), icon=Icon(works=False))
    closed(it)
    assert window.did == ["hide", "show"] and not it.hidden and raised
    it, window, icon, _, raised = behind(Watcher(False), icon=Icon(works=False))
    it.started_hidden()
    assert window.did == ["show"] and not it.hidden


def test_the_icons_menu(windows):
    watcher = Watcher(False, True)
    it, *_ = behind(watcher)
    labels = [e[0] if e else None for e in it.menu()]
    assert labels == ["Open ZPDF", None, "Pause watching", None, "Quit ZPDF"]
    it.toggle_watching()
    assert [f["paused"] for f in watcher.folders] == [True, True]
    assert it.menu()[2][0] == "Resume watching"
    it.toggle_watching()
    assert [f["paused"] for f in watcher.folders] == [False, False]
    entries = tray.menu_entries(it.menu)
    assert [e[1] if e else None for e in entries] == labels and entries[0][0] == tray.FIRST_ID and entries[4][0] == tray.FIRST_ID + 2
    checked = tray.menu_entries([("On", None, {"checked": True}), ("Off", None, {"enabled": False})])
    assert checked[0][3] & tray.MF_CHECKED and checked[1][3] & tray.MF_GRAYED


def test_starting_when_you_sign_in(windows, monkeypatch):
    from pdfmanager import winsetup

    monkeypatch.setattr(winsetup, "pythonw", lambda: r"C:\\Py\\pythonw.exe")
    assert not background.starts_at_login() and background.state("desktop")["login"] is False
    background.set_start_at_login(True)
    run = windows.keys[background.RUN_KEY.lower()]
    assert run == {"ZPDF": r'"C:\\Py\\pythonw.exe" -I -m pdfmanager gui --background'}
    assert background.starts_at_login() and background.keep() == "yes"       # starting hidden implies staying
    assert background.state("desktop") == {"available": True, "reason": "", "keep": "yes", "login": True}
    background.set_start_at_login(False)
    assert run == {} and not background.starts_at_login()
    background.set_start_at_login(False)                                     # again: nothing to remove, no error
    # uninstalling takes the entry away too
    background.set_start_at_login(True)
    monkeypatch.setattr(winsetup.sys, "platform", "win32")
    monkeypatch.setattr(winsetup, "shortcut_paths", lambda desktop=True: [])
    monkeypatch.setattr(winsetup, "_notify_shell", lambda: None)
    winsetup.remove()
    assert not background.starts_at_login()
    assert 'CurrentVersion\\Run" -Name "{name}"' in winsetup.UNINSTALL_PS1


def test_where_it_cant_be_done_the_reason_is_said(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "_data_dir", lambda: tmp_path / "zpdf-data")
    monkeypatch.setattr(background, "_windows", lambda: False)
    state = background.state("desktop")
    assert not state["available"] and "Windows only" in state["reason"] and state["login"] is False
    monkeypatch.setattr(background, "_windows", lambda: True)
    state = background.supported("app-window")
    assert not state[0] and "own window" in state[1]
    monkeypatch.setattr(background, "_windows", lambda: False)
    it, window, *_ = behind(Watcher(False))
    assert it.closing() is True                                              # closing closes, as before


def test_settings_through_the_server(windows, monkeypatch):
    from pdfmanager import winsetup

    monkeypatch.setattr(winsetup, "pythonw", lambda: r"C:\\Py\\pythonw.exe")
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("#token=")

    def call(data=None):
        req = urllib.request.Request(base.rstrip("/") + "/api/settings", headers={"X-Pdfm-Token": token,
                                     "Content-Type": "application/json"}, data=None if data is None else json.dumps(data).encode())
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    try:
        srv.RequestHandlerClass.instance_mode = "desktop"
        status, s = call()
        assert status == 200 and s["background"] == {"available": True, "reason": "", "keep": "ask", "login": False}
        assert call({"background": "yes"})[1]["background"]["keep"] == "yes"
        assert call({"login": True})[1]["background"]["login"] is True
        assert call({"background": "sometimes"})[0] == 400
        srv.RequestHandlerClass.instance_mode = "browser"                    # no window of its own to hide
        status, said = call({"background": "no"})
        assert status == 400 and "own window" in said["error"]
        assert call()[1]["background"]["available"] is False
    finally:
        srv.RequestHandlerClass.instance_mode = None
        srv.shutdown()
        srv.server_close()


def test_a_quiet_question_whether_zpdf_is_running(tmp_path, monkeypatch):
    """A start at sign-in while ZPDF is open: nothing opens, nothing comes forward."""
    monkeypatch.setattr(appdata, "_data_dir", lambda: tmp_path / "zpdf-data")
    assert gui.forward_to_running_app(quiet=True) is False                   # nothing running
    srv, _ = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    raised = []
    try:
        handler = srv.RequestHandlerClass
        handler.bring_to_front = lambda: raised.append(True)
        gui._register_instance(srv, "desktop")
        assert gui.forward_to_running_app(quiet=True) is True
        time.sleep(0.3)
        assert raised == []
        assert gui.forward_to_running_app() is True                          # an ordinary second start does raise it
        for _ in range(50):
            if raised:
                break
            time.sleep(0.1)
        assert raised == [True]
    finally:
        gui._unregister_instance(srv)
        srv.shutdown()
        srv.server_close()


def test_the_page_and_the_command_line_offer_it():
    from pathlib import Path
    from pdfmanager.cli import build_parser

    page = (Path(gui.__file__).parent / "gui.html").read_text(encoding="utf-8")
    assert 'id="set-background"' in page and 'id="set-login"' in page and "by the clock" in page
    args = build_parser().parse_args(["gui", "--background"])
    assert args.background is True


@pytest.mark.skipif(sys.platform != "win32", reason="the notification area is Windows'")
def test_the_real_icon_on_windows(tmp_path, monkeypatch):
    """The Win32 calls themselves: the icon's window is made and taken away
    without an error, whether or not this session has a notification area."""
    monkeypatch.setattr(appdata, "_data_dir", lambda: tmp_path / "zpdf-data")
    said = []
    icon = tray.Tray(background.icon_file(), "ZPDF test", on_click=lambda: None,
                     menu=lambda: [("Open", lambda: None), None, ("Quit", lambda: None)], log=said.append)
    shown = icon.start()
    assert not said, said                                                    # no error making it
    assert icon.hwnd                                                         # its own window exists
    if shown:
        assert icon.notify("ZPDF", "A note from the test")
    thread = icon.thread
    icon.stop()
    assert not thread.is_alive() and not said, said
    assert icon.start() in (True, False)                                     # and again, after stopping
    icon.stop()
