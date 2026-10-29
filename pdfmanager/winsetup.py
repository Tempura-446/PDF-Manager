"""Set up PDF Manager on Windows once it's installed with Python: Start menu
and desktop shortcuts, PDF Manager in "Open with" and Default apps for
PDFs, and an entry in Settings > Apps to uninstall it. Everything is for
the current user only, so no administrator rights are needed.

The installer (packaging/windows/install.ps1) runs ``pdfm setup-windows``
with the Python in the app's own folder. The shortcuts start that Python's
pythonw.exe, which is signed by the Python Software Foundation, so Windows
Smart App Control lets it run.
"""

import os
import subprocess
import sys
from pathlib import Path

from pdfmanager import __version__, appicon, core

NAME = "PDF Manager"
PROG_ID = "PDFManager.Document"
APP_KEY = r"Software\PDFManager"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PDFManager"
CLASSES = r"Software\Classes"
CSIDL_PROGRAMS, CSIDL_DESKTOPDIRECTORY = 0x02, 0x10

UNINSTALL_PS1 = r"""# Removes PDF Manager (run from Settings > Apps, or right-click > Run with PowerShell).
$ErrorActionPreference = "Continue"
$app = "{app_dir}"
Write-Host "Removing PDF Manager..."
# Close the app if it's running.
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
    Where-Object {{ $_.CommandLine -like "*-m pdfmanager*" }} |
    ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}
Start-Sleep -Seconds 1
& "$app\venv\Scripts\python.exe" -m pdfmanager setup-windows --remove
Remove-Item -LiteralPath $app -Recurse -Force -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $app) {{
    Write-Host "Some files could not be removed: $app"
}} else {{
    Write-Host "PDF Manager was removed. Your recipes and settings are kept in $env:LOCALAPPDATA\PDF Manager; delete that folder to remove them too."
}}
Start-Sleep -Seconds 4
"""


def _require_windows():
    if sys.platform != "win32":
        raise core.PdfManagerError("setup-windows only works on Windows")


def default_app_dir():
    """The installer puts Python's environment in <app dir>\\venv."""
    prefix = Path(sys.prefix)
    return prefix.parent if prefix.name.lower() == "venv" else prefix


def pythonw():
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return windowless if windowless.exists() else exe


def _folder(csidl):
    import ctypes

    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return Path(buf.value)


def shortcut_paths(desktop=True):
    paths = [_folder(CSIDL_PROGRAMS) / f"{NAME}.lnk"]
    if desktop:
        paths.append(_folder(CSIDL_DESKTOPDIRECTORY) / f"{NAME}.lnk")
    return paths


# ---- shortcuts (the shell's IShellLink, called through ctypes) ------------------------

def _com():
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    ole32 = ctypes.OleDLL("ole32")

    def guid(text):
        g = GUID()
        ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
        return g

    def method(obj, index, *argtypes):
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
        return lambda *args: proto(vtable[index])(obj, *args)

    try:
        ole32.CoInitialize(None)
    except OSError:
        pass  # already initialised in another mode
    link = ctypes.c_void_p()
    ole32.CoCreateInstance(ctypes.byref(guid("{00021401-0000-0000-C000-000000000046}")), None, 1,
                           ctypes.byref(guid("{000214F9-0000-0000-C000-000000000046}")),
                           ctypes.byref(link))
    persist = ctypes.c_void_p()
    method(link, 0, ctypes.c_void_p, ctypes.c_void_p)(
        ctypes.byref(guid("{0000010b-0000-0000-C000-000000000046}")), ctypes.byref(persist))
    return ctypes, wintypes, link, persist, method


def make_shortcut(path, target, arguments="", icon=None, workdir=None, description=""):
    """Create (or replace) the shortcut ``path``."""
    ctypes, wintypes, link, persist, method = _com()
    try:
        text = ctypes.c_wchar_p
        method(link, 20, text)(str(target))                     # SetPath
        method(link, 11, text)(arguments)                       # SetArguments
        method(link, 7, text)(description)                      # SetDescription
        if workdir:
            method(link, 9, text)(str(workdir))                 # SetWorkingDirectory
        if icon:
            method(link, 17, text, ctypes.c_int)(str(icon), 0)  # SetIconLocation
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        method(persist, 6, text, wintypes.BOOL)(str(path), True)  # IPersistFile.Save
    finally:
        method(persist, 2)()
        method(link, 2)()


def read_shortcut(path):
    """(target, arguments) of a shortcut."""
    ctypes, wintypes, link, persist, method = _com()
    try:
        method(persist, 5, ctypes.c_wchar_p, wintypes.DWORD)(str(path), 0)  # IPersistFile.Load
        target, args = ctypes.create_unicode_buffer(1024), ctypes.create_unicode_buffer(1024)
        method(link, 3, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)(
            target, 1024, None, 0)                                        # GetPath
        method(link, 10, ctypes.c_wchar_p, ctypes.c_int)(args, 1024)      # GetArguments
        return target.value, args.value
    finally:
        method(persist, 2)()
        method(link, 2)()


# ---- registry -----------------------------------------------------------------------

def _set(key_path, values):
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        for name, value in values.items():
            kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
            winreg.SetValueEx(key, name, 0, kind, value)


def _delete_tree(key_path):
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            children, i = [], 0
            while True:
                try:
                    children.append(winreg.EnumKey(key, i))
                except OSError:
                    break
                i += 1
        for child in children:
            _delete_tree(key_path + "\\" + child)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
    except FileNotFoundError:
        pass


def _delete_value(key_path, name):
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass


def _notify_shell():
    import ctypes

    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # associations changed


def register_open_with(command, icon):
    """PDF Manager in "Open with" and in Settings > Default apps for .pdf
    files (it doesn't take over as the default app)."""
    _set(rf"{CLASSES}\{PROG_ID}", {"": "PDF document", "FriendlyTypeName": "PDF document"})
    _set(rf"{CLASSES}\{PROG_ID}\DefaultIcon", {"": f"{icon},0"})
    _set(rf"{CLASSES}\{PROG_ID}\Application", {
        "ApplicationName": NAME, "ApplicationIcon": f"{icon},0",
        "ApplicationDescription": "Read, edit and organize PDFs"})
    _set(rf"{CLASSES}\{PROG_ID}\shell\open", {"FriendlyAppName": NAME})
    _set(rf"{CLASSES}\{PROG_ID}\shell\open\command", {"": command})
    _set(rf"{CLASSES}\.pdf\OpenWithProgids", {PROG_ID: ""})
    _set(rf"{APP_KEY}\Capabilities", {
        "ApplicationName": NAME, "ApplicationDescription": "Read, edit and organize PDFs",
        "ApplicationIcon": f"{icon},0"})
    _set(rf"{APP_KEY}\Capabilities\FileAssociations", {".pdf": PROG_ID})
    _set(r"Software\RegisteredApplications", {NAME: rf"{APP_KEY}\Capabilities"})


def register_uninstall(app_dir, icon):
    size_kb = sum(f.stat().st_size for f in Path(app_dir).rglob("*") if f.is_file()) // 1024
    script = Path(app_dir) / "uninstall.ps1"
    _set(UNINSTALL_KEY, {
        "DisplayName": NAME, "DisplayVersion": __version__, "Publisher": NAME,
        "DisplayIcon": str(icon), "InstallLocation": str(app_dir),
        "UninstallString": f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{script}"',
        "NoModify": 1, "NoRepair": 1, "EstimatedSize": int(size_kb),
    })


# ---- install / remove -----------------------------------------------------------------

def install(app_dir=None, desktop=True, open_with=True):
    """Add the shortcuts and registry entries. Returns what was done, as lines."""
    _require_windows()
    app_dir = Path(app_dir or default_app_dir())
    app_dir.mkdir(parents=True, exist_ok=True)
    icon = appicon.save_ico(app_dir / "PDF Manager.ico")
    (app_dir / "uninstall.ps1").write_text(UNINSTALL_PS1.format(app_dir=app_dir), encoding="utf-8-sig")
    exe = pythonw()
    done = []
    for path in shortcut_paths(desktop):
        make_shortcut(path, exe, "-m pdfmanager gui", icon=icon, workdir=Path.home(),
                      description="Read, edit and organize PDFs")
        done.append(f"shortcut: {path}")
    if open_with:
        register_open_with(f'"{exe}" -m pdfmanager gui "%1"', icon)
        done.append('"Open with" for PDF files')
    register_uninstall(app_dir, icon)
    done.append("Settings > Apps entry (to uninstall)")
    _notify_shell()
    return done


def remove():
    """Take away the shortcuts and registry entries (not the files)."""
    _require_windows()
    for path in shortcut_paths(desktop=True):
        Path(path).unlink(missing_ok=True)
    _delete_tree(rf"{CLASSES}\{PROG_ID}")
    _delete_value(rf"{CLASSES}\.pdf\OpenWithProgids", PROG_ID)
    _delete_tree(APP_KEY)
    _delete_value(r"Software\RegisteredApplications", NAME)
    _delete_tree(UNINSTALL_KEY)
    _notify_shell()


def launch_detached(args=("-m", "pdfmanager", "gui")):
    """Start the installed app without a console window."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen([str(pythonw()), *args], creationflags=flags, close_fds=True,
                            cwd=os.path.expanduser("~"))
