# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""A stand-in for NAPS2.Console.exe, for test_naps2.py: it takes the options
naps2.py gives it and answers the way NAPS2's source says it does.
``FAKE_NAPS2`` says what kind of scanner is behind it:

    ok (default)   three pages in the feeder, one on the glass
    empty          the feeder is empty: no file is written
    gone           the device isn't found
    slow           takes ten seconds (to be cancelled)

What it was asked is written to the file named by ``FAKE_NAPS2_LOG``."""
import json
import os
import sys
import time

from PIL import Image, ImageDraw


def main(argv):
    how = os.environ.get("FAKE_NAPS2", "ok")
    log = os.environ.get("FAKE_NAPS2_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(argv) + "\n")
    if "--listdevices" in argv:
        if "--driver" not in argv:
            print("The --driver option is required.")
            return 0
        for name in ("Old Flatbed 3000", "Office Sheetfed TW"):
            print(name)
        return 0
    get = lambda flag: argv[argv.index(flag) + 1] if flag in argv else None  # noqa: E731
    if not get("--device"):
        print("No device specified.")
        return 0
    if how == "gone":
        print("Device not found.")
        return 0
    if how == "slow":
        time.sleep(10)
    if how == "empty":
        print("Starting scan 1 of 1...")
        print("No scanned pages to export.")
        return 0
    count = {"glass": 1, "feeder": 3, "duplex": 6}[get("--source")]
    mode = {"color": "RGB", "gray": "L", "bw": "L"}[get("--bitdepth")]
    dpi = int(get("--dpi"))
    pages = []
    print("Starting scan 1 of 1...")
    for n in range(count):
        page = Image.new(mode, (int(8.27 * dpi) // 4, int(11.69 * dpi) // 4), "white")
        ImageDraw.Draw(page).rectangle([20, 20 + 30 * n, 120, 40 + 30 * n], fill="black" if mode == "L" else (200, 0, 0))
        pages.append(page)
        print(f"Scanned page {n + 1}.")
    print(f"{count} page(s) scanned.")
    pages[0].save(get("-o"), "PDF", save_all=True, append_images=pages[1:], resolution=dpi / 4)
    print(f"Successfully saved PDF file to {get('-o')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
