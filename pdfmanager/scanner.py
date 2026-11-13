"""Scanning paper with a scanner on Windows, through Windows Image
Acquisition (WIA): the "WIA.DeviceManager" automation object that comes
with Windows (wiaaut.dll), driven late-bound (IDispatch) through pythonnet,
which the desktop window already brings with it on Windows. No PowerShell
and no other program: it works under Constrained Language mode and Smart
App Control.

    scanner.status()                       # {"available": bool, "reason": "..."}
    scanner.list_scanners()                # [{"id", "name", "feeder", "flatbed"}]
    scanner.scan(device_id, mode="grey", dpi=300, source="feeder", on_page=...)

Everything that touches WIA goes through a small COM layer (``Com``: create
an object by name, get or set a property, call a method, read a failure's
HRESULT) so the tests can put a fake WIA behind it. The WIA object model
used, as documented by Microsoft (WIA Automation Layer, WIA 2.0 property
constants):

- DeviceManager.DeviceInfos: Count, Item(i) (1-based); each DeviceInfo has
  DeviceID, Type (1 = scanner), Properties("Name").Value, and Connect(),
  which gives the Device.
- Device.Properties: 3086 document handling capabilities (1 feeder,
  2 flatbed), 3087 document handling status (1 feeder ready), 3088
  document handling select (1 feeder, 2 flatbed), 3096 pages to scan.
- Device.Items(1), the scanning item, whose Properties take 6146 the
  intent (1 colour, 2 greyscale, 4 text / black and white), 4103 the data
  type (3 colour, 2 greyscale, 0 black and white), 4104 bits per pixel,
  6147 / 6148 the resolution, 6149-6152 the area to scan; and
  Item.Transfer(format GUID) scans one page, giving an ImageFile whose
  SaveFile(path) writes it out.
- Failures come back as HRESULTs: 0x80210003 the feeder is empty,
  0x80210005 offline, 0x80210006 busy, and the others in ERRORS below.

COM objects are apartment threaded: each call here runs in a thread that
joins a single-threaded apartment first (CoInitializeEx through ctypes).
"""

import contextlib
import sys
import tempfile
import threading
from pathlib import Path

from pdfmanager import core, progress

MODES = ("colour", "grey", "bw")
RESOLUTIONS = (150, 200, 300)
SOURCES = ("flatbed", "feeder")
DEFAULT_MODE, DEFAULT_DPI = "grey", 300
MAX_PAGES = 500  # a feeder that never says it's empty stops here

SCANNER_DEVICE = 1  # WiaDeviceType.ScannerDeviceType

# Device properties (WIA_DPS_*)
DOCUMENT_HANDLING_CAPABILITIES = 3086
DOCUMENT_HANDLING_STATUS = 3087
DOCUMENT_HANDLING_SELECT = 3088
PAGES = 3096
FEEDER, FLATBED = 1, 2            # select / capabilities bits
FEED_READY = 1                    # status bit

# Item properties (WIA_IPA_*, WIA_IPS_*)
DATATYPE = 4103
DEPTH = 4104
CUR_INTENT = 6146
XRES, YRES = 6147, 6148
XPOS, YPOS, XEXTENT, YEXTENT = 6149, 6150, 6151, 6152

INTENT = {"colour": 1, "grey": 2, "bw": 4}      # WIA_INTENT_IMAGE_TYPE_COLOR / GRAYSCALE / TEXT
DATA_TYPE = {"colour": 3, "grey": 2, "bw": 0}   # WIA_DATA_COLOR / GRAYSCALE / THRESHOLD
BITS = {"colour": 24, "grey": 8, "bw": 1}

FORMAT_PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"
FORMAT_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"

PAPER_EMPTY = 0x80210003
OFFLINE = 0x80210005
BUSY = 0x80210006
# What each WIA failure means, in plain words. (WIA_ERROR_* and WIA_S_*.)
ERRORS = {
    0x80210001: ("general", "The scanner reported a problem. Switch it off and on again, then try again."),
    0x80210002: ("jam", "The paper jammed. Clear it from the scanner, then scan again."),
    PAPER_EMPTY: ("empty", "The document feeder is empty. Put the pages in the feeder, then scan again."),
    0x80210004: ("paper", "The scanner has a problem with the paper. Check the pages in the feeder, then scan again."),
    OFFLINE: ("offline", "The scanner is offline. Check that it is switched on and connected, then try again."),
    BUSY: ("busy", "The scanner is busy. Wait for it to finish, or close the other program using it, then try again."),
    0x80210007: ("warming", "The scanner is warming up. Wait a moment, then try again."),
    0x80210008: ("attention", "The scanner needs attention: look at its display or lights, then try again."),
    0x80210009: ("gone", "The scanner was disconnected. Connect it again, then try again."),
    0x8021000A: ("offline", "ZPDF couldn't talk to the scanner. Check its cable or network, then try again."),
    0x8021000B: ("refused", "The scanner can't do that. Try another resolution or colour setting."),
    0x8021000C: ("refused", "The scanner can't do that. Try another resolution or colour setting."),
    0x8021000D: ("busy", "The scanner is in use by another program. Close it, then try again."),
    0x8021000E: ("driver", "The scanner's driver failed. Switch the scanner off and on again; if it keeps "
                           "failing, reinstall its driver."),
    0x8021000F: ("driver", "The scanner's driver failed. Switch the scanner off and on again; if it keeps "
                           "failing, reinstall its driver."),
    0x80210015: ("none", "No scanner was found."),
    0x80210016: ("cover", "The scanner's cover or door is open. Close it, then scan again."),
    0x80210017: ("lamp", "The scanner's lamp is off. Switch the scanner off and on again, then try again."),
    0x80210020: ("attention", "The scanner needs attention: look at its display or lights, then try again."),
    0x80210021: ("multifeed", "The feeder took several sheets at once. Put the pages back and scan again."),
}
NONE_FOUND = ("No scanner was found. Check that it is switched on and connected (by USB or on the "
              "same network), and that Windows lists it under Settings → Bluetooth & devices → "
              "Printers & scanners.")


class ScannerError(core.PdfManagerError):
    """Scanning failed; the message says why in plain words. ``code``: a
    short name for the reason ("none", "busy", "offline", "empty", ...)."""

    def __init__(self, message, code="general", hresult=None):
        super().__init__(message)
        self.code = code
        self.hresult = hresult


# -- the COM layer -----------------------------------------------------------------------

class Com:
    """IDispatch late binding through pythonnet: System.Type.GetTypeFromProgID
    and Activator.CreateInstance make the object, Type.InvokeMember reads
    and sets its properties and calls its methods (the .NET runtime routes
    that to IDispatch for a COM object)."""

    def __init__(self):
        import clr  # noqa: F401  (pythonnet: loads the .NET runtime)
        import System
        from System.Reflection import BindingFlags

        self.System = System
        self.flags = BindingFlags

    def create(self, progid):
        kind = self.System.Type.GetTypeFromProgID(progid)
        if kind is None:
            raise ScannerError("Windows Image Acquisition isn't installed on this PC, so ZPDF can't "
                               "talk to scanners.", "unavailable")
        return self.System.Activator.CreateInstance(kind)

    def _args(self, values):
        """An object[] of the values, each boxed as its own .NET type: the
        numbers as Int32, made inside .NET (Array.Copy from a typed array),
        so they reach IDispatch as VT_I4 whatever pythonnet's conversions."""
        S = self.System
        out = S.Array.CreateInstance(S.Object, len(values))
        for i, value in enumerate(values):
            if isinstance(value, bool):
                typed = S.Array[S.Boolean]([value])
            elif isinstance(value, int):
                typed = S.Array[S.Int32]([value])
            else:
                typed = S.Array[S.String]([str(value)])
            S.Array.Copy(typed, 0, out, i, 1)
        return out

    def _invoke(self, obj, name, flag, values):
        return obj.GetType().InvokeMember(name, flag, None, obj, self._args(values))

    def get(self, obj, name, *index):
        return self._invoke(obj, name, self.flags.GetProperty, index)

    def put(self, obj, name, value, *index):
        self._invoke(obj, name, self.flags.SetProperty, (*index, value))

    def call(self, obj, name, *args):
        return self._invoke(obj, name, self.flags.InvokeMethod, args)

    @staticmethod
    def hresult(exc):
        """The HRESULT behind a failed call (unsigned), or None. InvokeMember
        wraps the COMException in a TargetInvocationException: the chain of
        inner exceptions is followed, and a WIA error (0x8021....) wins."""
        codes = []
        for _ in range(6):
            if exc is None:
                break
            for attr in ("HResult", "ErrorCode"):
                try:
                    code = int(getattr(exc, attr)) & 0xFFFFFFFF
                except (AttributeError, TypeError, ValueError):
                    continue
                if code and code != 0x80131604:  # (TargetInvocationException's own)
                    codes.append(code)
            inner = getattr(exc, "InnerException", None)
            exc = inner if inner is not exc else None
        wia = [c for c in codes if c >> 16 == 0x8021]
        return (wia or codes or [None])[0]

    @contextlib.contextmanager
    def apartment(self):
        """Run the block in a single-threaded COM apartment (this thread's)."""
        ole32 = None
        if sys.platform == "win32":
            import ctypes

            ole32 = ctypes.windll.ole32
            result = ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
            ok = result in (0, 1)  # S_OK, S_FALSE (already); RPC_E_CHANGED_MODE: use what's there
        try:
            yield
        finally:
            if ole32 is not None and ok:
                ole32.CoUninitialize()


_com = None          # the COM layer in use (tests put a fake one here with use())
_com_error = None    # why it couldn't be made
_scan_lock = threading.Lock()


def use(com):
    """Use ``com`` as the COM layer (None: the real one again). For tests."""
    global _com, _com_error
    _com, _com_error = com, None


def _layer():
    global _com, _com_error
    if _com is not None:
        return _com
    if sys.platform != "win32":
        raise ScannerError("Scanning from a scanner works on Windows only.", "unavailable")
    try:
        _com = Com()
    except ScannerError:
        raise
    except Exception as exc:  # pythonnet missing, or the .NET runtime blocked
        _com_error = str(exc) or type(exc).__name__
        raise ScannerError("ZPDF can't use Windows Image Acquisition here (it needs pythonnet, which "
                           "comes with the desktop window: pip install pywebview).", "unavailable") from None
    return _com


def status():
    """Whether scanning from a scanner can work here: {"available", "reason"}.
    Doesn't look for scanners (list_scanners does)."""
    try:
        com = _layer()
        with com.apartment():
            com.create("WIA.DeviceManager")
    except ScannerError as exc:
        return {"available": False, "reason": str(exc)}
    except Exception as exc:
        return {"available": False, "reason": "Windows Image Acquisition isn't working on this PC "
                                              f"({_short(exc)}). Check that the Windows Image Acquisition "
                                              "service is running."}
    return {"available": True, "reason": ""}


def _short(exc):
    return " ".join(str(exc).split())[:160] or type(exc).__name__


def _failure(com, exc, while_doing=""):
    """A ScannerError for a failed WIA call."""
    if isinstance(exc, ScannerError):
        return exc
    code = com.hresult(exc)
    if code in ERRORS:
        reason, message = ERRORS[code]
        if reason == "none":
            message = NONE_FOUND
        return ScannerError(message, reason, code)
    said = f" (error 0x{code:08X})" if code else f" ({_short(exc)})"
    return ScannerError(f"The scanner failed{while_doing}{said}. Switch it off and on again, then "
                        "try again.", "general", code)


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return default


def _prop(com, obj, pid):
    """The Property with id ``pid`` of a device or item, or None."""
    props = com.get(obj, "Properties")
    try:
        if not com.call(props, "Exists", str(pid)):
            return None
    except Exception:
        return None
    return com.get(props, "Item", str(pid))


def _read(com, obj, pid, default=None):
    prop = _prop(com, obj, pid)
    if prop is None:
        return default
    try:
        return com.get(prop, "Value")
    except Exception:
        return default


def _write(com, obj, pid, value, needed=False):
    """Set a property; a driver that refuses it is told off only when ``needed``."""
    prop = _prop(com, obj, pid)
    if prop is None:
        if needed:
            raise ScannerError("The scanner can't do that. Try another resolution or colour setting.", "refused")
        return False
    try:
        com.put(prop, "Value", value)
        return True
    except Exception as exc:
        if needed:
            raise _failure(com, exc, " to take the settings") from None
        return False


def _devices(com):
    manager = com.create("WIA.DeviceManager")
    infos = com.get(manager, "DeviceInfos")
    found = []
    for i in range(1, _int(com.get(infos, "Count")) + 1):
        info = com.get(infos, "Item", i)
        if _int(com.get(info, "Type")) != SCANNER_DEVICE:
            continue
        name = ""
        try:
            name = str(com.get(com.get(com.get(info, "Properties"), "Item", "Name"), "Value"))
        except Exception:
            pass
        found.append((str(com.get(info, "DeviceID")), name or "Scanner", info))
    return found


def list_scanners():
    """The scanners Windows knows: [{"id", "name", "feeder", "flatbed"}].
    Whether one has a feeder is read from the device when it can be
    connected to; a busy or offline scanner is still listed."""
    com = _layer()
    out = []
    with com.apartment():
        try:
            found = _devices(com)
        except Exception as exc:
            raise _failure(com, exc, " to list scanners") from None
        for device_id, name, info in found:
            feeder, flatbed = False, True
            try:
                device = com.call(info, "Connect")
                handling = _read(com, device, DOCUMENT_HANDLING_CAPABILITIES)
                if handling is not None:
                    feeder = bool(_int(handling) & FEEDER)
                    flatbed = bool(_int(handling) & FLATBED)
            except Exception:
                pass  # listed anyway; scanning says what's wrong
            out.append({"id": device_id, "name": name, "feeder": feeder, "flatbed": flatbed or not feeder})
    return out


def _configure(com, device, item, mode, dpi, source):
    """Put the device and its item in the asked mode."""
    handling = _read(com, device, DOCUMENT_HANDLING_CAPABILITIES)
    if source == "feeder":
        if handling is not None and not _int(handling) & FEEDER:
            raise ScannerError("This scanner has no document feeder. Choose Flatbed (the glass).", "nofeeder")
        _write(com, device, DOCUMENT_HANDLING_SELECT, FEEDER, needed=handling is not None)
        _write(com, device, PAGES, 1)  # one page each Transfer
    elif handling is not None:
        if not _int(handling) & FLATBED:
            raise ScannerError("This scanner has no glass to scan on. Choose Document feeder.", "noflatbed")
        _write(com, device, DOCUMENT_HANDLING_SELECT, FLATBED)
    # The intent first: drivers reset the type, depth and resolution from it.
    _write(com, item, CUR_INTENT, INTENT[mode])
    _write(com, item, DATATYPE, DATA_TYPE[mode])
    _write(com, item, DEPTH, BITS[mode])
    _write(com, item, XRES, dpi, needed=True)
    _write(com, item, YRES, dpi, needed=True)
    # The whole glass or page: some drivers keep a smaller area from last time.
    _write(com, item, XPOS, 0)
    _write(com, item, YPOS, 0)
    for pid in (XEXTENT, YEXTENT):
        prop = _prop(com, item, pid)
        if prop is not None:
            try:
                com.put(prop, "Value", _int(com.get(prop, "SubTypeMax")))
            except Exception:
                pass


def _transfer(com, item, folder, number):
    """Scan one page: a PIL image."""
    from PIL import Image

    try:
        picture = com.call(item, "Transfer", FORMAT_PNG)
    except Exception as exc:
        code = com.hresult(exc)
        if code in ERRORS and code not in (0x8021000B, 0x8021000C):
            raise
        picture = com.call(item, "Transfer", FORMAT_BMP)  # drivers that only give bitmaps
    target = Path(folder) / f"page{number:04d}.scan"
    if target.exists():
        target.unlink()
    com.call(picture, "SaveFile", str(target))
    with Image.open(target) as image:
        image.load()
        return image.copy()


def scan(device_id, mode=DEFAULT_MODE, dpi=DEFAULT_DPI, source="flatbed", on_page=None):
    """Scan from the scanner with WIA id ``device_id``: one page from the
    flatbed, or every page in the feeder. ``on_page(image, dpi)`` is called
    as each page arrives. Returns [(PIL image, dpi)]. Reports progress and
    stops between pages when the job is cancelled (progress.Cancelled)."""
    if mode not in MODES:
        raise ScannerError(f"choose a colour setting: {', '.join(MODES)}", "refused")
    if source not in SOURCES:
        raise ScannerError("choose Flatbed or Document feeder", "refused")
    dpi = int(dpi)
    if not 50 <= dpi <= 1200:
        raise ScannerError("choose a resolution between 50 and 1200 dpi", "refused")
    com = _layer()
    if not _scan_lock.acquire(blocking=False):
        raise ScannerError("A scan is already running. Wait for it to finish.", "busy")
    pages = []
    try:
        with com.apartment(), tempfile.TemporaryDirectory(prefix="pdfm-scan-") as folder:
            progress.say("connecting to the scanner")
            try:
                match = [info for did, _, info in _devices(com) if did == device_id]
            except Exception as exc:
                raise _failure(com, exc, " to list scanners") from None
            if not match:
                raise ScannerError("That scanner isn't connected any more. Check that it is switched on, "
                                   "then choose it again.", "gone")
            try:
                device = com.call(match[0], "Connect")
                item = com.get(com.get(device, "Items"), "Item", 1)
                _configure(com, device, item, mode, dpi, source)
            except Exception as exc:
                raise _failure(com, exc, " to start") from None
            feeder = source == "feeder"
            while True:
                number = len(pages) + 1
                progress.check()
                progress.say(f"scanning page {number}" if feeder else "scanning")
                try:
                    image = _transfer(com, item, folder, number)
                except Exception as exc:
                    failure = _failure(com, exc, f" on page {number}")
                    if feeder and pages and failure.code == "empty":
                        break  # every page in the feeder is done
                    raise failure from None
                image.info["dpi"] = (dpi, dpi)
                pages.append((image, dpi))
                if on_page is not None:
                    on_page(image, dpi)
                if not feeder or len(pages) >= MAX_PAGES:
                    break
                status_value = _read(com, device, DOCUMENT_HANDLING_STATUS)
                if status_value is not None and not _int(status_value) & FEED_READY:
                    break  # the feeder says it has no more
    finally:
        _scan_lock.release()
    return pages

