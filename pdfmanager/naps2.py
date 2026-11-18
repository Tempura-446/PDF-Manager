# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Scanners that only come with a TWAIN driver, through NAPS2 when it is
installed.

NAPS2 (https://www.naps2.com, GPL 2.0 or later) is a free scanning program
that drives TWAIN scanners, the 32-bit drivers too. ZPDF doesn't contain
it and isn't built from it: like LibreOffice or Tesseract it is a separate
program, found if the user installed it and run through its command line
(``NAPS2.Console.exe``). Without it nothing here does anything.

What is used of its command line (read from its source, 8.6.0):

    NAPS2.Console.exe --listdevices --driver twain
        the devices' names, one a line
    NAPS2.Console.exe -o OUT.pdf --noprofile --driver twain --device NAME
        --source glass|feeder|duplex --dpi N --bitdepth color|gray|bw -f -v
        scans to one PDF, a picture a page; -v says "scanned page" as it goes

It doesn't promise an exit code, so whether a scan worked is told from the
PDF it leaves, and what went wrong from what it printed.

    find()            -> the console program's path, or None
    devices()         -> [{"id": "naps2:twain:NAME", "name", ...}] (kept a minute)
    scan(id, ...)     -> [(PIL image, dpi)], like scanner.scan
"""
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from pdfmanager import core, progress, winprograms

PREFIX = "naps2:"
DRIVER = "twain"           # (WIA and network scanners ZPDF drives itself)
LIST_TIMEOUT = 20          # seconds for the list of devices
SCAN_TIMEOUT = 1800        # seconds for one scan (a full feeder at a high resolution)
KEPT = 60                  # seconds the list of devices is kept
BITS = {"colour": "color", "grey": "gray", "bw": "bw"}
SOURCE = {("flatbed", False): "glass", ("flatbed", True): "glass", ("feeder", False): "feeder", ("feeder", True): "duplex"}

_lock = threading.Lock()
_listed = {"at": 0.0, "program": None, "devices": []}


class Naps2Error(core.PdfManagerError):
    """A scan through NAPS2 that didn't work, in words for the user; ``code``
    is the same kind of reason scanner.ScannerError has."""

    def __init__(self, message, code="general"):
        super().__init__(message)
        self.code = code


def find():
    """NAPS2's console program, or None: ``PDFM_NAPS2`` when set, the PATH,
    then where its installer puts it."""
    named = os.environ.get("PDFM_NAPS2")
    if named:
        return named if Path(named).is_file() else None
    for name in ("NAPS2.Console", "NAPS2.Console.exe", "naps2.console"):
        found = shutil.which(name)
        if found:
            return found
    for base in winprograms.program_dirs():
        path = base / "NAPS2" / "NAPS2.Console.exe"
        if path.is_file():
            return str(path)
    return None


def _run(command, timeout, said=None):
    """Run NAPS2 without a window; what it printed (both streams). Stops it
    when the job is cancelled. ``said(line)``: called for each line as it comes."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        # (A group of its own where there are groups: it is stopped with what it started, see _stop.)
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace", creationflags=flags,
                                start_new_session=os.name != "nt")
    except OSError as exc:
        raise Naps2Error(f"NAPS2 couldn't be started ({exc.strerror or exc}). Install it again from naps2.com.",
                         "unavailable") from None
    lines = []

    def read():
        for line in proc.stdout:
            line = line.strip()
            if line:
                lines.append(line)
                if said:
                    said(line)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    try:
        while proc.poll() is None:
            if time.monotonic() > deadline:
                raise Naps2Error("NAPS2 took too long and was stopped. Check the scanner, then try again.", "busy")
            progress.check()  # (cancelled: the scan is stopped)
            time.sleep(0.2)
    finally:
        if proc.poll() is None:
            _stop(proc)
        reader.join(timeout=5)
    return "\n".join(lines)


def _stop(proc):
    """End NAPS2 and what it started: NAPS2.Console.exe only starts
    NAPS2.exe and waits for it, so ending the first alone would leave the
    scan running."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            import signal

            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def devices(fresh=False):
    """The TWAIN scanners NAPS2 lists, for the Scan page: [{"id", "name",
    "feeder", "flatbed", "duplex", "network", "naps2"}]. NAPS2 doesn't say
    what a scanner can do, so the feeder and both sides are offered and the
    scanner says so if it has none. Kept for a minute; [] without NAPS2 or
    when the list couldn't be had."""
    program = find()
    if not program:
        return []
    with _lock:
        if not fresh and _listed["program"] == program and time.monotonic() - _listed["at"] < KEPT:
            return [dict(d) for d in _listed["devices"]]
    try:
        printed = _run([program, "--listdevices", "--driver", DRIVER], LIST_TIMEOUT)
    except (Naps2Error, progress.Cancelled):
        return []
    names = [line for line in dict.fromkeys(printed.splitlines()) if line and len(line) < 200]
    found = [{"id": f"{PREFIX}{DRIVER}:{name}", "name": f"{name} (through NAPS2)", "feeder": True, "flatbed": True,
              "duplex": True, "network": False, "naps2": True} for name in names]
    with _lock:
        _listed.update(at=time.monotonic(), program=program, devices=found)
    return [dict(d) for d in found]


def _why(printed, source):
    """What NAPS2 printed when no pages came, as a reason and words for the user."""
    low = printed.lower()
    # (NAPS2 8.6.0's own words, seen on a PC: "The selected scanner could not be found.",
    # "The selected scanner is offline.", "No scanned pages to export.")
    if "offline" in low:
        return "offline", "The scanner is offline. Check that it is switched on and connected, then try again."
    if "not found" in low or "not be found" in low or "no device" in low:
        return "gone", "NAPS2 couldn't find that scanner. Check that it is switched on and connected, then try again."
    if "feeder" in low and ("empty" in low or "no paper" in low or "no documents" in low):
        return "empty", "The document feeder is empty. Put the pages in and scan again."
    if "busy" in low or "in use" in low:
        return "busy", "The scanner is in use by another program. Close it, then try again."
    if "no pages" in low or "no scanned pages" in low:
        return "empty", ("The document feeder is empty. Put the pages in and scan again." if source == "feeder"
                         else "The scanner sent no page. Try again.")
    said = " ".join(printed.split())[-240:]
    return "general", "NAPS2 couldn't scan" + (f": {said}" if said else ". Open NAPS2 itself to see what the scanner says.")


def _pages(pdf):
    """The scanned pages of the PDF NAPS2 wrote, as pictures: the picture
    each page holds (the scan itself, not drawn again)."""
    from pypdf import PdfReader

    out = []
    for page in PdfReader(str(pdf)).pages:
        best = None
        for held in page.images:
            image = held.image
            if best is None or image.width * image.height > best.width * best.height:
                best = image
        if best is not None:
            best.load()
            out.append(best.copy())
    return out


def scan(device_id, mode="grey", dpi=300, source="flatbed", duplex=False, on_page=None):
    """Scan from a scanner NAPS2 drives: one page from the glass, or every
    page in the feeder (both sides with ``duplex``). Returns [(PIL image,
    dpi)]; ``on_page(image, dpi)`` is called for each once they are in (NAPS2
    hands them over together, at the end). Cancelling stops NAPS2."""
    program = find()
    if not program:
        raise Naps2Error("NAPS2 isn't installed any more. Install it from naps2.com, or choose another scanner.",
                         "unavailable")
    parts = str(device_id).split(":", 2)
    if len(parts) != 3 or parts[0] + ":" != PREFIX or not parts[2]:
        raise Naps2Error("that scanner can't be found; choose it again", "gone")
    if mode not in BITS:
        raise Naps2Error(f"choose a colour setting: {', '.join(BITS)}", "refused")
    if source not in ("flatbed", "feeder"):
        raise Naps2Error("choose Flatbed or Document feeder", "refused")
    dpi = int(dpi)
    if not 50 <= dpi <= 1200:
        raise Naps2Error("choose a resolution between 50 and 1200 dpi", "refused")
    seen = [0]

    def said(line):
        # (-v: a line for each page as it is scanned; ignore "Starting scan 1 of 1" and "N page(s) scanned.")
        low = line.lower()
        if any(ch.isdigit() for ch in line) and "1 of 1" not in low and "(s)" not in low:
            seen[0] += 1
            progress.say(f"scanning page {seen[0]}" if source == "feeder" else "scanning")

    with tempfile.TemporaryDirectory(prefix="pdfm-naps2-") as folder:
        target = Path(folder) / "scan.pdf"
        progress.say("starting NAPS2")
        printed = _run([program, "-o", str(target), "--noprofile", "--driver", parts[1], "--device", parts[2],
                        "--source", SOURCE[(source, bool(duplex))], "--dpi", str(dpi), "--bitdepth", BITS[mode],
                        "-f", "-v"], SCAN_TIMEOUT, said)
        if not target.is_file() or target.stat().st_size < 100:
            raise Naps2Error(*reversed(_why(printed, source)))
        try:
            images = _pages(target)
        except Exception as exc:  # noqa: BLE001 - whatever NAPS2 left isn't a readable PDF
            raise Naps2Error(f"What NAPS2 scanned couldn't be read ({type(exc).__name__}). Try again.") from None
    if not images:
        raise Naps2Error(*reversed(_why(printed + "\nno pages", source)))
    pages = []
    for image in images:
        if mode == "bw" and image.mode != "1":
            image = image.convert("L").point(lambda v: 255 if v >= 160 else 0).convert("1")
        elif mode == "grey" and image.mode not in ("L", "1"):
            image = image.convert("L")
        image.info["dpi"] = (dpi, dpi)
        pages.append((image, dpi))
        if on_page is not None:
            on_page(image, dpi)
    return pages


def installed():
    """Whether NAPS2 is there to be used (for what the Scan page offers)."""
    return find() is not None
