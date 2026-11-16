# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""ZPDF in the background: watch folders keep working with the window
closed, from an icon in the notification area, and ZPDF can start that way
when you sign in to Windows.

Two settings, both the person's choice (Settings → Watch folders):

- ``keep``: what closing the window does while a folder is being watched.
  "ask" (the default: asked once, and the answer kept), "yes" (the window
  hides, the icon stays, watching goes on) or "no" (ZPDF closes).
- start at login: an entry under the current user's Run key (no
  administrator rights; Windows lists it under Startup apps) that starts
  ZPDF with ``--background``: no window, just the icon.

Nothing else runs in the background: with no folder watched (or all of
them paused), closing the window closes ZPDF as before.
"""

import sys
from pathlib import Path

from pdfmanager import core

KEEP = ("ask", "yes", "no")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "ZPDF"
QUESTION = ("Keep ZPDF watching your folders in the background?\n\n"
            "Yes: the window closes and ZPDF stays in the notification area (by the clock), "
            "doing each file that arrives. Its icon there opens ZPDF again or quits it.\n\n"
            "No: ZPDF closes, and the folders are watched again the next time it's open.\n\n"
            "You can change this under Settings → Watch folders.")
STILL_WATCHING = "Still watching your folders. Click the icon to open ZPDF, or right-click it to quit."


def _windows():
    return sys.platform == "win32"


def supported(mode=None):
    """(whether ZPDF can stay in the background here, why not). It needs
    Windows' notification area and ZPDF's own window (which can hide)."""
    if not _windows():
        return False, "Staying in the background works on Windows only for now."
    if mode is not None and mode != "desktop":
        return False, ("Staying in the background needs ZPDF's own window; this copy runs in a browser window. "
                       "Reinstalling ZPDF usually brings its own window back.")
    return True, ""


def keep():
    """What closing the window does while folders are watched: ask, yes or no."""
    from pdfmanager import update

    value = update.settings().get("background")
    return value if value in KEEP else "ask"


def set_keep(value):
    from pdfmanager import update

    if value not in KEEP:
        raise core.PdfManagerError(f"choose one of {', '.join(KEEP)}")
    update.save_settings(background=None if value == "ask" else value)


def watching(watcher):
    """Whether any folder is being watched right now (one that isn't paused)."""
    try:
        return any(not f.get("paused") for f in watcher.list())
    except Exception:  # noqa: BLE001 - no watcher, nothing watched
        return False


def stay(watcher, ask):
    """Whether closing the window should hide it and keep ZPDF running.
    With "ask", ``ask(QUESTION)`` is called (True for yes) and the answer
    is kept, so it's asked once. Whatever goes wrong asking, ZPDF closes."""
    if not supported()[0] or not watching(watcher):
        return False
    choice = keep()
    if choice == "ask":
        try:
            choice = "yes" if ask(QUESTION) else "no"
        except Exception:  # noqa: BLE001
            return False
        set_keep(choice)
    return choice == "yes"


# ---- starting when you sign in ----------------------------------------------------------

def login_command():
    """The command Windows runs at sign-in: this ZPDF, with no window."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    from pdfmanager import winsetup

    return f'"{winsetup.pythonw()}" -I -m pdfmanager gui --background'


def starts_at_login():
    """Whether Windows starts ZPDF when this user signs in."""
    if not _windows():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            return bool(winreg.QueryValueEx(key, RUN_NAME)[0])
    except OSError:
        return False


def set_start_at_login(on):
    """Add or remove the sign-in entry (this user only). With it on, closing
    the window keeps ZPDF in the background too: starting hidden and then
    quitting at the first close would undo it."""
    if not _windows():
        raise core.PdfManagerError("starting when you sign in is set up on Windows only for now")
    import winreg

    if on:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, login_command())
        if keep() != "yes":
            set_keep("yes")
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_NAME)
    except OSError:
        pass  # (wasn't there)


def state(mode=None):
    """For the Settings page: {"available", "reason", "keep", "login"}."""
    ok, why = supported(mode)
    return {"available": ok, "reason": why, "keep": keep(), "login": starts_at_login() if ok else False}


def icon_file():
    """The app's icon as a file for the notification area (made once, in
    the data folder)."""
    from pdfmanager import appdata, appicon

    path = Path(appdata.data_dir()) / "ZPDF.ico"
    if not path.exists():
        appicon.save_ico(path)
    return path


class InBackground:
    """The app's own window while ZPDF can stay in the background: what its
    close button does, the notification-area icon, and the icon's menu.

    ``window`` has hide(), show() and destroy() (pywebview's); ``handler``
    is the server's (its watcher, unsaved work, quit_requested);
    ``may_close()`` is the usual closing question about unsaved work (True
    lets the window close); ``ask(text)`` asks yes or no; ``bring_forward()``
    raises the window. ``tray`` is a tray.Tray, or anything with start(),
    notify(title, text) and stop()."""

    def __init__(self, handler, window, may_close, ask, bring_forward, tray=None, log=None):
        self.handler, self.window = handler, window
        self.may_close, self.ask, self.bring_forward = may_close, ask, bring_forward
        self.log = log or (lambda text: None)
        self.tray = tray
        self.hidden = False
        self.leaving = False  # Quit was chosen: the next close really closes
        try:
            handler.watcher.on_news = self.news
        except AttributeError:
            pass

    def news(self, message, ok):
        """A watched file was done (or couldn't be): said from the icon while
        the window is hidden; with the window open, the page says it."""
        if self.hidden and self.tray is not None:
            self.tray.notify("ZPDF" if ok else "ZPDF couldn't do a file", message)

    def _tray(self):
        if self.tray is None:
            from pdfmanager import tray

            self.tray = tray.Tray(icon_file(), "ZPDF: watching your folders", on_click=self.open, menu=self.menu,
                                  log=self.log)
        return self.tray

    def closing(self):
        """The window's closing event: True lets it close. While a folder is
        watched (and the person wants it), the window hides instead and ZPDF
        stays, with its icon; unsaved work stays open in the hidden window."""
        if getattr(self.handler, "quit_requested", False) or self.leaving:
            return self.may_close()
        if stay(self.handler.watcher, self.ask):
            # (Hidden from another thread: the closing event runs on the window's
            # own, which pywebview's hide() would wait for.)
            import threading

            threading.Thread(target=self.hide, name="hide-window", daemon=True).start()
            return False
        return self.may_close()

    def hide(self, note=True):
        try:
            self.window.hide()
        except Exception as exc:  # noqa: BLE001
            self.log(f"background: couldn't hide the window: {exc!r}")
            return
        self.hidden = True
        shown = self._tray().start()
        self.log(f"background: window hidden, icon {'shown' if shown else 'not shown'}")
        if not shown:
            # No icon to come back by: never leave ZPDF running out of sight.
            self.open()
            return
        if note:
            self._tray().notify("ZPDF", STILL_WATCHING)

    def started_hidden(self):
        """ZPDF was started with no window (at sign-in): just the icon."""
        self.hidden = True
        if not self._tray().start():
            self.open()

    def open(self):
        """Show the window again (the icon's click, its menu, or a file opened
        from Explorer while ZPDF was in the background)."""
        if self.hidden:
            try:
                self.window.show()
            except Exception as exc:  # noqa: BLE001
                self.log(f"background: couldn't show the window: {exc!r}")
            self.hidden = False
            if self.tray is not None:
                self.tray.stop()
        self.bring_forward()

    def toggle_watching(self):
        watcher = self.handler.watcher
        pause = watching(watcher)
        for folder in watcher.list():
            if bool(folder.get("paused")) != pause:
                watcher.set_paused(folder["id"], pause)

    def quit(self):
        """Quit ZPDF from the icon. With unsaved work the window comes back
        first, and closing asks about it as usual."""
        self.leaving = True
        unsaved = getattr(self.handler, "unsaved", None)
        if unsaved is not None and unsaved.names():
            self.open()
        elif self.tray is not None:
            self.tray.stop()
        self.window.destroy()

    def menu(self):
        on = watching(self.handler.watcher)
        return [("Open ZPDF", self.open), None,
                ("Pause watching" if on else "Resume watching", self.toggle_watching), None,
                ("Quit ZPDF", self.quit)]

    def stop(self):
        if self.tray is not None:
            self.tray.stop()
