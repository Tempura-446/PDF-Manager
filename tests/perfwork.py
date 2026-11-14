# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""One workload of the performance checks (test_perf.py), run in its own
Python so its peak memory is its own: ``python perfwork.py WORK SRC OUT
DATA`` prints "seconds peak_mb". The peak counts everything the process
held, the PDF and image libraries' own memory included."""
import sys
import time
from pathlib import Path


def peak_mb():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
        return counters.PeakWorkingSetSize / 2 ** 20
    try:  # Linux: this program's own high-water mark (ru_maxrss would
        # include the memory of the program that started it)
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 2 ** 10
    except OSError:
        pass
    import resource

    kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return kb / 2 ** 20 if sys.platform == "darwin" else kb / 2 ** 10


def main(work, src, out, data):
    from pdfmanager import appdata, core

    appdata._data_dir = lambda: Path(data)  # nothing reaches the user's folder
    # Everything the work needs is loaded first, so both sizes of a check
    # start from the same memory.
    if work == "ocr":
        from pdfmanager import ocr

        ocr._engine()
    started = time.perf_counter()
    if work == "merge":
        core.merge([src, src], out)
    elif work == "compress":
        core.compress(src, out, level="high")
    elif work == "compress-scan":
        kept = []
        core.compress(src, out, level="high", notes=kept)
        if not kept:
            raise SystemExit("the scanned pages weren't kept sharp")
    elif work == "ocr":
        core.ocr(src, out, engine="builtin")
    else:
        raise SystemExit(f"unknown work {work}")
    print(f"{time.perf_counter() - started:.2f} {peak_mb():.1f}")


if __name__ == "__main__":
    main(*sys.argv[1:])
