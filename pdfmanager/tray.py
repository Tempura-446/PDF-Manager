# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""An icon in Windows' notification area (the "tray"), for ZPDF running in
the background: a click opens the window, a right-click shows a small menu,
and a note can be shown from it ("ZPDF is still watching your folders").

Written against the Win32 API with ctypes (Shell_NotifyIconW and a hidden
window of our own that receives the icon's messages), so nothing else
needs installing. It runs its own thread with its own message loop; menu
choices are run on other threads, so a slow one never freezes the icon.

    icon = Tray("C:/…/ZPDF.ico", "ZPDF", on_click=open_window,
                menu=lambda: [("Open ZPDF", open_window), None, ("Quit ZPDF", quit_app)])
    icon.start()            # False where there's no notification area (not Windows)
    icon.notify("ZPDF", "Still watching your folders.")
    icon.stop()
"""

import sys
import threading

WM_NULL, WM_DESTROY, WM_CLOSE, WM_COMMAND = 0x0000, 0x0002, 0x0010, 0x0111
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP, WM_CONTEXTMENU = 0x0202, 0x0203, 0x0205, 0x007B
WM_APP = 0x8000
WM_TRAY = WM_APP + 1          # the icon's own messages
WM_NOTE = WM_APP + 2          # "show the note that's waiting"
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO = 0x01
MF_STRING, MF_GRAYED, MF_CHECKED, MF_SEPARATOR = 0x000, 0x001, 0x008, 0x800
TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD = 0x0002, 0x0080, 0x0100
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x0010, 0x0040
IDI_APPLICATION = 32512
FIRST_ID = 1000


def available():
    """Whether there's a notification area to put an icon in."""
    return sys.platform == "win32"


def menu_entries(menu):
    """What a menu holds, from ``menu`` (a list, or a function giving one):
    [(id, label, callback, flags)] with None for a separator. An entry is
    (label, callback) or (label, callback, {"checked": bool, "enabled": bool})."""
    items = menu() if callable(menu) else menu
    out, number = [], FIRST_ID
    for item in items or []:
        if item is None:
            out.append(None)
            continue
        label, callback = item[0], item[1]
        how = item[2] if len(item) > 2 else {}
        flags = MF_STRING | (MF_CHECKED if how.get("checked") else 0) | (0 if how.get("enabled", True) else MF_GRAYED)
        out.append((number, str(label), callback, flags))
        number += 1
    return out


class Tray:
    """One icon in the notification area."""

    def __init__(self, icon_path, tip, on_click=None, menu=None, log=None):
        self.icon_path, self.tip = str(icon_path or ""), str(tip)[:127]
        self.on_click, self.menu = on_click, menu
        self.log = log or (lambda text: None)
        self.thread = None
        self.hwnd = None
        self.shown = False
        self._ready = threading.Event()
        self._note = None
        self._keep = []  # callbacks handed to Windows must outlive the calls

    # -- from any thread ------------------------------------------------------------------

    def start(self, wait=5.0):
        """Show the icon. True once it's there; False where it can't be."""
        if not available():
            return False
        if self.thread is not None and self.thread.is_alive():
            return self.shown
        self._ready.clear()
        self.thread = threading.Thread(target=self._run, name="tray-icon", daemon=True)
        self.thread.start()
        self._ready.wait(wait)
        return self.shown

    def notify(self, title, text):
        """Show a note from the icon (a "toast" on Windows 10 and 11)."""
        if not self.hwnd:
            return False
        self._note = (str(title)[:63], str(text)[:255])
        return bool(self._post(WM_NOTE))

    def stop(self, wait=5.0):
        """Take the icon away and end its thread."""
        thread = self.thread
        if self.hwnd:
            self._post(WM_CLOSE)
        if thread is not None and thread is not threading.current_thread():
            thread.join(wait)
        self.thread = None

    def _post(self, message):
        import ctypes

        return ctypes.windll.user32.PostMessageW(self.hwnd, message, 0, 0)

    def _call(self, callback):
        """Run a click's or a menu choice's function on its own thread."""
        if callback is None:
            return

        def run():
            try:
                callback()
            except Exception as exc:  # noqa: BLE001 - a menu choice must not end the icon
                self.log(f"tray: {type(exc).__name__}: {exc}")

        threading.Thread(target=run, name="tray-choice", daemon=True).start()

    # -- the icon's own thread ------------------------------------------------------------

    def _run(self):
        try:
            self._loop()
        except Exception as exc:  # noqa: BLE001 - no icon is never a reason to stop the app
            self.log(f"tray: couldn't run ({type(exc).__name__}: {exc})")
        finally:
            self.shown, self.hwnd = False, None
            self._ready.set()

    def _loop(self):
        import ctypes
        from ctypes import wintypes

        user32, shell32, kernel32 = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.kernel32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                        ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HANDLE),
                        ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                        ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                        ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                        ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HANDLE)]

        user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.DefWindowProcW.restype = LRESULT
        user32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                           wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPVOID)
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.LoadImageW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                      wintypes.UINT)
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadIconW.argtypes = (wintypes.HINSTANCE, ctypes.c_void_p)
        user32.LoadIconW.restype = wintypes.HANDLE
        user32.DestroyIcon.argtypes = (wintypes.HANDLE,)
        user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.PostMessageW.restype = wintypes.BOOL
        user32.DestroyWindow.argtypes = (wintypes.HWND,)
        user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        user32.CreatePopupMenu.restype = wintypes.HANDLE
        user32.AppendMenuW.argtypes = (wintypes.HANDLE, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR)
        user32.AppendMenuW.restype = wintypes.BOOL
        user32.TrackPopupMenu.argtypes = (wintypes.HANDLE, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                          wintypes.HWND, ctypes.c_void_p)
        user32.TrackPopupMenu.restype = ctypes.c_int
        user32.DestroyMenu.argtypes = (wintypes.HANDLE,)
        user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
        user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
        user32.GetMessageW.restype = ctypes.c_int
        user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
        user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
        user32.DispatchMessageW.restype = LRESULT
        user32.RegisterWindowMessageW.argtypes = (wintypes.LPCWSTR,)
        user32.RegisterWindowMessageW.restype = wintypes.UINT
        shell32.Shell_NotifyIconW.argtypes = (wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        user32.UnregisterClassW.argtypes = (wintypes.LPCWSTR, wintypes.HINSTANCE)
        user32.PostQuitMessage.argtypes = (ctypes.c_int,)
        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

        instance = kernel32.GetModuleHandleW(None)
        taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")  # Explorer started again
        own_icon = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE) \
            if self.icon_path else None
        icon = own_icon or user32.LoadIconW(None, IDI_APPLICATION)

        def data(flags):
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd, nid.uID, nid.uFlags = self.hwnd, 1, flags
            nid.uCallbackMessage, nid.hIcon, nid.szTip = WM_TRAY, icon, self.tip
            return nid

        def add():
            self.shown = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data(NIF_MESSAGE | NIF_ICON | NIF_TIP))))
            return self.shown

        def show_menu(hwnd):
            entries = menu_entries(self.menu)
            if not entries:
                return
            handle = user32.CreatePopupMenu()
            for entry in entries:
                if entry is None:
                    user32.AppendMenuW(handle, MF_SEPARATOR, 0, None)
                else:
                    user32.AppendMenuW(handle, entry[3], entry[0], entry[1])
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            user32.SetForegroundWindow(hwnd)   # (or the menu stays open when clicked away from)
            chosen = user32.TrackPopupMenu(handle, TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD, point.x, point.y, 0,
                                           hwnd, None)
            user32.DestroyMenu(handle)
            user32.PostMessageW(hwnd, WM_NULL, 0, 0)
            for entry in entries:
                if entry is not None and entry[0] == chosen:
                    self._call(entry[2])

        def handle(hwnd, message, wparam, lparam):
            if message == WM_TRAY:
                event = lparam & 0xFFFF
                if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._call(self.on_click)
                elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    show_menu(hwnd)
                return 0
            if message == WM_NOTE and self._note:
                nid = data(NIF_INFO)
                nid.szInfoTitle, nid.szInfo = self._note
                nid.dwInfoFlags = NIIF_INFO
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
                return 0
            if message == taskbar_created:
                add()
                return 0
            if message == WM_CLOSE:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data(0)))
                self.shown = False
                user32.DestroyWindow(hwnd)
                return 0
            if message == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        procedure = WNDPROC(handle)
        self._keep = [procedure]
        name = f"ZPDFTray{threading.get_ident()}"
        cls = WNDCLASSW()
        cls.lpfnWndProc, cls.hInstance, cls.lpszClassName = procedure, instance, name
        if not user32.RegisterClassW(ctypes.byref(cls)):
            raise OSError("RegisterClassW failed")
        # A window of our own that is never shown: it receives the icon's messages
        # (and "TaskbarCreated", which message-only windows aren't sent).
        self.hwnd = user32.CreateWindowExW(0, name, "ZPDF", 0, 0, 0, 0, 0, None, None, instance, None)
        if not self.hwnd:
            raise OSError("CreateWindowExW failed")
        add()
        self._ready.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        if own_icon:
            user32.DestroyIcon(own_icon)
        user32.UnregisterClassW(name, instance)
