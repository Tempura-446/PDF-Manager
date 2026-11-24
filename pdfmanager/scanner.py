# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
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

import logging
import contextlib
import sys
import tempfile
import threading
import time
from pathlib import Path

from pdfmanager import core, progress

MODES = ("colour", "grey", "bw")
RESOLUTIONS = (150, 200, 300)
SOURCES = ("flatbed", "feeder")
DEFAULT_MODE, DEFAULT_DPI = "grey", 300
MAX_PAGES = 500  # a feeder that never says it's empty stops here
FEEDER_RETRY_WAIT = 0.25  # seconds before reconnecting when a driver balks between feeder pages

SCANNER_DEVICE = 1  # WiaDeviceType.ScannerDeviceType

# Device properties (WIA_DPS_*)
DOCUMENT_HANDLING_CAPABILITIES = 3086
DOCUMENT_HANDLING_STATUS = 3087
DOCUMENT_HANDLING_SELECT = 3088
PAGES = 3096
FEEDER, FLATBED = 1, 2            # select / capabilities bits
FEED_READY = 1                    # status bit
FEED_EMPTY = 8                    # status bit on drivers that mark an empty tray explicitly

# Item properties (WIA_IPA_*, WIA_IPS_*)
ITEM_NAME = 4098
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
    0x80210016: ("cover", "The scanner's cover or door is open. Close it, then try again."),
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
        import clr  # pythonnet: loads the .NET runtime
        import System
        from System.Reflection import BindingFlags

        self.System = System
        self.object_type = clr.GetClrType(System.Object)  # a System.Type, for Array.CreateInstance
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
        out = S.Array.CreateInstance(self.object_type, len(values))
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
        _some(obj)
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
_scanning = False    # True while a real scan() / scan_any() holds _scan_lock


@contextlib.contextmanager
def _exclusive_scan():
    """Hold ``_scan_lock`` for a scan. Fails at once if another scan is
    running, but waits briefly if ``_learn`` is finishing a capability probe."""
    global _scanning
    if _scanning:
        raise ScannerError("A scan is already running. Wait for it to finish.", "busy")
    if not _scan_lock.acquire(timeout=2.5):
        raise ScannerError("A scan is already running. Wait for it to finish.", "busy")
    if _scanning:
        _scan_lock.release()
        raise ScannerError("A scan is already running. Wait for it to finish.", "busy")
    _scanning = True
    try:
        yield
    finally:
        _scanning = False
        _scan_lock.release()


def use(com):
    """Use ``com`` as the COM layer (None: the real one again). For tests."""
    global _com, _com_error
    _com, _com_error = com, None
    with _caps_lock:   # (what the scanners of the layer before said doesn't hold for this one's)
        _caps.clear()


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
        raise ScannerError("ZPDF can't reach Windows Image Acquisition, the part of Windows that talks "
                           "to scanners: a piece of ZPDF it needs (pythonnet) is missing. Reinstall ZPDF "
                           "(from Python: pip install pywebview). Photos of the pages still work.",
                           "unavailable") from None
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


NO_ANSWER = ("The scanner's driver didn't answer as expected. Switch the scanner off and on again, "
             "then try again.")


def _some(value):
    """``value``, which a driver should have given: some hand back nothing
    (no device, no item, no picture) instead of failing."""
    if value is None:
        raise ScannerError(NO_ANSWER, "noanswer")
    return value


def _open(com, info, source="flatbed"):
    """Connect to a scanner: (the device, its item to scan with). Some WIA 2.0
    drivers keep one Item for the glass and another for the feeder; when there
    is more than one, the one matching ``source`` is chosen."""
    device = _some(com.call(info, "Connect"))
    items = _some(com.get(device, "Items"))
    first = _some(com.get(items, "Item", 1))
    try:
        count = _int(com.get(items, "Count"), 1)
    except Exception:
        count = 1
    if count <= 1:
        return device, first
    wanted_bit = FEEDER if source == "feeder" else FLATBED
    wanted_words = ("feeder", "adf", "sheet") if source == "feeder" else ("flatbed", "glass", "platen")
    for i in range(1, count + 1):
        try:
            candidate = com.get(items, "Item", i)
        except Exception:
            continue
        if candidate is None:
            continue
        for pid in (DOCUMENT_HANDLING_CAPABILITIES, DOCUMENT_HANDLING_SELECT):
            val = _read(com, candidate, pid)
            if val is not None and (_int(val) & wanted_bit):
                return device, candidate
        name = str(_read(com, candidate, ITEM_NAME) or _read(com, candidate, "Item Name") or "").lower()
        if any(w in name for w in wanted_words):
            return device, candidate
    return device, first


def _short(exc):
    return " ".join(str(exc).split())[:160] or type(exc).__name__


def _http_answer(code):
    """The HTTP status inside an HRESULT of Windows' HTTP kind (0x8019....),
    or None. Windows' own driver for network scanners (eSCL) passes on what
    the scanner answered this way: 0x80190199 is 409, "these settings
    conflict"."""
    return code & 0xFFFF if code and code >> 16 == 0x8019 else None


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
    answer = _http_answer(code)
    if answer in (404, 410):
        return ScannerError(ERRORS[PAPER_EMPTY][1], "empty", code)
    if answer in (400, 409):
        return ScannerError(f"The scanner refused these settings (it answered {answer}). Try another resolution "
                            "or colour setting.", "refused", code)
    if answer == 503:
        return ScannerError(ERRORS[BUSY][1], "busy", code)
    if answer:
        return ScannerError(f"The scanner failed{while_doing} (it answered {answer}). Switch it off and on again, "
                            "then try again.", "general", code)
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


def _feeder_status(com, device, item=None):
    """The feeder's DOCUMENT_HANDLING_STATUS (3087), read from the item or
    the device (some WIA 2.0 drivers put it on the feeder item)."""
    if item is not None:
        val = _read(com, item, DOCUMENT_HANDLING_STATUS)
        if val is not None:
            return val
    return _read(com, device, DOCUMENT_HANDLING_STATUS)


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
        except Exception as _exc:
            logging.getLogger("zpdf").debug("scanner._devices: %s", _exc)
        found.append((str(com.get(info, "DeviceID")), name or "Scanner", info))
    return found


_caps = {}                    # scanner id -> (has a feeder, has a glass), as the scanner itself said
_asking = set()               # ids being asked right now
_caps_lock = threading.Lock()
LEARN_WAIT = 2.0              # seconds a list of scanners waits for them to say what they have


def list_scanners():
    """The scanners Windows knows: [{"id", "name", "feeder", "flatbed"}].
    Windows names them at once; whether one has a feeder it has to be asked
    itself, and a scanner that is off or asleep takes Windows most of a
    minute to give up on. So the asking is done beside the list: what a
    scanner has said before is used, one that answers within a moment is
    waited for, and until one has answered both the glass and the feeder
    are offered (scanning says so if it has no feeder)."""
    com = _layer()
    with com.apartment():
        try:
            found = [(device_id, name) for device_id, name, _ in _devices(com)]
        except Exception as exc:
            raise _failure(com, exc, " to list scanners") from None
    with _caps_lock:
        unknown = [device_id for device_id, _ in found if device_id not in _caps and device_id not in _asking]
        _asking.update(unknown)
    if unknown and not _scanning:
        asker = threading.Thread(target=_learn, args=(com, unknown), daemon=True)
        asker.start()
        asker.join(LEARN_WAIT)
    out = []
    for device_id, name in found:
        feeder, flatbed = _caps.get(device_id, (True, True))
        out.append({"id": device_id, "name": name, "feeder": feeder, "flatbed": flatbed or not feeder})
    return out


def _learn(com, ids):
    """Ask the scanners ``ids`` what they have (a feeder, a glass), for the
    next list. One that can't be reached now is asked again next time. Yields
    to any running scan so the driver is never opened from two threads at once."""
    try:
        with com.apartment():
            for device_id, _, info in _devices(com):
                if device_id not in ids:
                    continue
                if _scanning or not _scan_lock.acquire(blocking=False):
                    break
                try:
                    if _scanning:
                        break
                    device = com.call(info, "Connect")
                    handling = _read(com, device, DOCUMENT_HANDLING_CAPABILITIES)
                    if handling is None:
                        try:
                            items = com.get(device, "Items")
                            for i in range(1, _int(com.get(items, "Count"), 1) + 1):
                                item_caps = _read(com, com.get(items, "Item", i), DOCUMENT_HANDLING_CAPABILITIES)
                                if item_caps is not None:
                                    handling = (handling or 0) | _int(item_caps)
                        except Exception as _exc:
                            logging.getLogger("zpdf").debug("scanner._learn: %s", _exc)
                except Exception:
                    continue
                finally:
                    _scan_lock.release()
                feeder, flatbed = False, True
                if handling is not None:
                    feeder, flatbed = bool(_int(handling) & FEEDER), bool(_int(handling) & FLATBED)
                with _caps_lock:
                    _caps[device_id] = (feeder, flatbed)
    except Exception as _exc:  # noqa: BLE001 - the list does without
        logging.getLogger("zpdf").debug("scanner._learn: %s", _exc)
    finally:
        with _caps_lock:
            _asking.difference_update(ids)


def _configure(com, device, item, mode, dpi, source, before=None):
    """Put the device and its item in the asked mode. Returns what the
    intent and the area were before. Given that as ``before`` (a scanner
    refused the full set), only what is needed is set (the source, the kind
    of picture, the resolution) and the intent and area are put back as the
    driver had them."""
    handling = _read(com, device, DOCUMENT_HANDLING_CAPABILITIES)
    if handling is None:
        handling = _read(com, item, DOCUMENT_HANDLING_CAPABILITIES)
    if source == "feeder":
        if handling is not None and not _int(handling) & FEEDER:
            raise ScannerError("This scanner has no document feeder. Choose Flatbed (the glass).", "nofeeder")
        set_dev = _write(com, device, DOCUMENT_HANDLING_SELECT, FEEDER)
        set_item = _write(com, item, DOCUMENT_HANDLING_SELECT, FEEDER)
        if handling is not None and not (set_dev or set_item):
            _write(com, device, DOCUMENT_HANDLING_SELECT, FEEDER, needed=True)
        _write(com, device, PAGES, 1)  # one page each Transfer
        _write(com, item, PAGES, 1)
    elif handling is not None:
        if not _int(handling) & FLATBED:
            raise ScannerError("This scanner has no glass to scan on. Choose Document feeder.", "noflatbed")
        _write(com, device, DOCUMENT_HANDLING_SELECT, FLATBED)
        _write(com, item, DOCUMENT_HANDLING_SELECT, FLATBED)
    if before is not None:
        for pid, value in before.items():
            if value is not None:
                _write(com, item, pid, value)
    # (Read only the first time: the second try has what it needs already.)
    was = None if before is not None else {pid: _read(com, item, pid) for pid in (CUR_INTENT, XPOS, YPOS, XEXTENT, YEXTENT)}
    # The intent first: drivers reset the type, depth and resolution from it.
    if before is None:
        _write(com, item, CUR_INTENT, INTENT[mode])
    _write(com, item, DATATYPE, DATA_TYPE[mode])
    _write(com, item, DEPTH, BITS[mode])
    _write(com, item, XRES, dpi, needed=True)
    _write(com, item, YRES, dpi, needed=True)
    if before is not None:
        return was
    # The whole glass or page: some drivers keep a smaller area from last time.
    _write(com, item, XPOS, 0)
    _write(com, item, YPOS, 0)
    for pid in (XEXTENT, YEXTENT):
        prop = _prop(com, item, pid)
        if prop is not None:
            try:
                com.put(prop, "Value", _int(com.get(prop, "SubTypeMax")))
            except Exception as _exc:
                logging.getLogger("zpdf").debug("scanner._configure: %s", _exc)
    return was


def _transfer(com, item, folder, number, formats=None):
    """Scan one page: a PIL image. ``formats`` remembers when a driver only
    gives bitmaps so later pages in the feeder don't ask for PNG again."""
    from PIL import Image

    if formats is None:
        formats = [FORMAT_PNG, FORMAT_BMP]
    try:
        picture = _some(com.call(item, "Transfer", formats[0]))
    except Exception as exc:
        code = com.hresult(exc)
        can_try_bmp = (
            len(formats) > 1
            and number == 1
            and (
                code in (0x8021000B, 0x8021000C, 0x80070057, 0x80004001)
                or (isinstance(exc, ScannerError) and exc.code == "noanswer")
            )
        )
        if not can_try_bmp:
            raise
        picture = _some(com.call(item, "Transfer", formats[1]))  # drivers that only give bitmaps
        formats[:] = [formats[1]]
    target = Path(folder) / f"page{number:04d}.scan"
    if target.exists():
        target.unlink()
    com.call(picture, "SaveFile", str(target))
    with Image.open(target) as image:
        image.load()
        return image.copy()


def _reconnect_feeder_page(com, info, mode, dpi, source, before, folder, number, formats,
                           status_tracks_ready, first_failure):
    """When a feeder transfer fails on page 2+, reconnect and either scan the
    next sheet (many WIA drivers end the session after PAGES=1 or stay busy
    while ejecting the sheet) or recognise that the feeder is now empty."""
    last_failure = first_failure
    for attempt in range(3):
        progress.check()
        if FEEDER_RETRY_WAIT:
            time.sleep(FEEDER_RETRY_WAIT * (attempt + 1))
        try:
            device, item = _open(com, info, source)
            fresh_status = _feeder_status(com, device, item)
            if fresh_status is not None:
                bits = _int(fresh_status)
                if not (bits & FEED_READY) and (status_tracks_ready or (bits & FEED_EMPTY)):
                    return device, item, None
            _configure(com, device, item, mode, dpi, source, before=before)
            image = _transfer(com, item, folder, number, formats)
            return device, item, image
        except Exception as exc:
            last_failure = _failure(com, exc, f" on page {number}")
            if last_failure.code == "empty":
                return None, None, None
            if last_failure.code in ("jam", "cover", "lamp", "multifeed", "offline", "gone"):
                raise last_failure from None
    # After 1+ pages have already been scanned, drivers that report an empty
    # feeder as HTTP 409 ("refused"), E_FAIL / driver error, or None ("noanswer")
    # have simply finished the stack in the tray.
    if last_failure.code in ("empty", "noanswer", "refused", "paper"):
        return None, None, None
    raise last_failure from None


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
    pages = []
    with _exclusive_scan():
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
                device, item = _open(com, match[0], source)
                before = _configure(com, device, item, mode, dpi, source)
            except Exception as exc:
                raise _failure(com, exc, " to start") from None
            feeder = source == "feeder"
            used_mode = mode
            used_before = None
            formats = [FORMAT_PNG, FORMAT_BMP]
            initial_status = _feeder_status(com, device, item) if feeder else None
            status_tracks_ready = initial_status is not None and bool(_int(initial_status) & FEED_READY)
            while True:
                number = len(pages) + 1
                progress.check()
                progress.say(f"scanning page {number}" if feeder else "scanning")
                if feeder and pages:
                    # Re-assert PAGES=1 for drivers that decrement it to 0 on each Transfer.
                    _write(com, device, DOCUMENT_HANDLING_SELECT, FEEDER)
                    _write(com, item, DOCUMENT_HANDLING_SELECT, FEEDER)
                    _write(com, device, PAGES, 1)
                    _write(com, item, PAGES, 1)
                try:
                    image = _transfer(com, item, folder, number, formats)
                except Exception as exc:
                    failure = _failure(com, exc, f" on page {number}")
                    if feeder and pages and failure.code == "empty":
                        break  # every page in the feeder is done
                    if feeder and pages:
                        if failure.code in ("jam", "cover", "lamp", "multifeed", "offline", "gone"):
                            raise failure from None
                        device, item, image = _reconnect_feeder_page(
                            com, match[0], used_mode, dpi, source, used_before, folder, number,
                            formats, status_tracks_ready, failure)
                        if image is None:
                            break
                    else:
                        if failure.code not in ("refused", "noanswer"):
                            raise failure from None
                        # Refused (or nothing given) before the first page: once more with only the
                        # settings that matter, in grey where black and white was
                        # asked (made black and white here afterwards).
                        plainer = "grey" if mode == "bw" else mode
                        try:
                            device, item = _open(com, match[0], source)
                            _configure(com, device, item, plainer, dpi, source, before=before)
                            image = _transfer(com, item, folder, number, formats)
                            used_mode = plainer
                            used_before = before
                        except Exception as again:
                            second = _failure(com, again, f" on page {number}")
                            last = second if second.code != "general" else failure
                            if last.code in ("refused", "noanswer") and "\\escl\\" in str(device_id).lower():
                                last = ScannerError(f"{last} Or choose this scanner's \"(network)\" entry in the "
                                                    "list, where ZPDF talks to it directly.", last.code, last.hresult)
                            raise last from None
                if mode == "bw" and image.mode != "1":
                    image = image.convert("L").point(lambda v: 255 if v >= 160 else 0).convert("1")
                image.info["dpi"] = (dpi, dpi)
                pages.append((image, dpi))
                if on_page is not None:
                    on_page(image, dpi)
                if not feeder or len(pages) >= MAX_PAGES:
                    break
                status_value = _feeder_status(com, device, item)
                if status_value is not None:
                    bits = _int(status_value)
                    if bits & FEED_READY:
                        status_tracks_ready = True
                    elif status_tracks_ready or (bits & FEED_EMPTY):
                        break  # the feeder says it has no more
    return pages


# -- every scanner: Windows' own (WIA) and the network's driverless ones (escl.py) --------

def all_scanners(network=True, look=True):
    """WIA scanners (where Windows has them) and then network scanners that
    need no driver (eSCL / AirScan; see escl.py): [{"id", "name", "feeder",
    "flatbed", "duplex", "network"}]. Listing never fails for one kind's
    sake: what Windows couldn't list is left out."""
    from pdfmanager import escl

    out = []
    try:
        out += [{**d, "duplex": False, "network": False} for d in list_scanners()]
    except ScannerError:
        pass
    if network:
        out += [{k: d[k] for k in ("id", "name", "feeder", "flatbed", "duplex", "network", "found")}
                for d in escl.devices(look=look)]
    # Scanners that only have a TWAIN driver, when NAPS2 is installed to drive them (naps2.py).
    from pdfmanager import naps2

    try:
        out += naps2.devices()
    except Exception as _exc:  # noqa: BLE001 - the others are listed all the same
        logging.getLogger("zpdf").debug("scanner.all_scanners: %s", _exc)
    # SANE scanners on Linux / macOS when scanimage is installed (sane.py).
    if sys.platform != "win32":
        from pdfmanager import sane

        try:
            out += sane.devices()
        except Exception as _exc:  # noqa: BLE001
            logging.getLogger("zpdf").debug("scanner.all_scanners: %s", _exc)
    return out


def scan_any(device_id, mode=DEFAULT_MODE, dpi=DEFAULT_DPI, source="flatbed", duplex=False, on_page=None):
    """scan(), for any scanner backend (WIA, eSCL, NAPS2, or SANE).
    ``duplex``: both sides, from a feeder that supports it."""
    from pdfmanager import escl, naps2, sane

    if str(device_id).startswith(sane.PREFIX):
        with _exclusive_scan():
            try:
                return sane.scan(device_id, mode=mode, dpi=dpi, source=source, duplex=duplex, on_page=on_page)
            except sane.SaneError as exc:
                raise ScannerError(str(exc), exc.code) from None
    if str(device_id).startswith(naps2.PREFIX):
        with _exclusive_scan():
            try:
                return naps2.scan(device_id, mode=mode, dpi=dpi, source=source, duplex=duplex, on_page=on_page)
            except naps2.Naps2Error as exc:
                raise ScannerError(str(exc), exc.code) from None
    if not str(device_id).startswith("escl:"):
        return scan(device_id, mode=mode, dpi=dpi, source=source, on_page=on_page)
    if mode not in MODES:
        raise ScannerError(f"choose a colour setting: {', '.join(MODES)}", "refused")
    with _exclusive_scan():
        try:
            return escl.scan(device_id, mode=mode, dpi=dpi, source=source, duplex=duplex, on_page=on_page)
        except escl.EsclError as exc:
            raise ScannerError(str(exc), exc.code) from None

