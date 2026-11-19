# Closer to Windows: more scanners, watching in the background, thumbnails in Explorer: Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** three things asked for on 2026-11-15:
1. **TWAIN and AirScan scanners**, next to the WIA ones ZPDF drives today (TWAIN-only devices through scan-to-folder and NAPS2, not a driver layer of our own: see Phase 2).
2. **Start at login, minimized to the tray**, so Watch folders keep working without the window open.
3. **First-page thumbnails of PDFs in File Explorer.**

**Where it stands today**

- `scanner.py` scans through Windows Image Acquisition (WIA) only, behind a small COM layer the tests fake. Many office devices have a WIA driver; some come with TWAIN only, and network devices increasingly offer only driverless scanning (eSCL, which Apple calls AirScan, and WSD).
- Watch folders (`watchfolders.py`) are processed by one thread **while ZPDF is open**. Closing the window stops them. Nothing starts ZPDF at login, and there is no tray icon.
- Explorer shows ZPDF's icon for PDFs when ZPDF is the default app. Windows only draws PDF thumbnails when the default app registers a thumbnail handler (Edge and Acrobat do); ZPDF doesn't.

## Phase 1 (release A): driverless network scanners (eSCL / AirScan) — done in 0.44.0

- [x] **Find scanners:** `escl.discover` sends one multicast DNS question (`_uscan._tcp`, `_uscans._tcp`) with its own small DNS reader, only when the Scan page lists scanners; `escl.add(address)` for networks where that finds nothing (remembered in settings as `network_scanners`). Listed in "When ZPDF goes online".
- [x] **Scan:** `escl.capabilities` and `escl.scan` (ScanJobs, NextDocument until 404, 503 asked again, DELETE at the end, also when cancelled). Empty feeder, jam, open cover, busy, attention, no answer in plain words (from ScannerStatus).
- [x] **One interface:** `scanner.all_scanners()` and `scanner.scan_any()`; the Scan page gets "Both sides of each sheet", "Add a network scanner…" and "Forget this one"; `pdfm scan --add / --duplex`.
- [x] **TLS:** a scanner offering only HTTPS is used with its own certificate (local network); one offering both keeps plain HTTP.
- [x] **Tests:** `tests/test_escl.py` (a fake scanner: feeder, both sides, jam, busy, cancel; mDNS packets, damaged ones too), `tests/test_escl_ui.py` + `tests/esclui.js`. No test asks the real network (`conftest.py`).
- [ ] **Not yet tried on a real scanner.** To check: discovery on Windows (the firewall may ask the first time; replies are requested unicast), a device listed twice (Windows' WSD driver and the network one), page sizes other than the largest, PDF output from devices that don't offer JPEG.
- [ ] Seen when publishing 0.44.0: Windows itself lists a network device through its own eSCL driver (id `SWD\\Escl\\…`, e.g. the owner's HP Color LaserJet MFP M281fdw, which WIA shows as flatbed only), so the same device can appear twice: once from Windows and once as "(network)". Decide which to show: ours may offer the feeder and both sides where Windows' entry doesn't. The tests of network scanners now empty Windows' own list, since a PC may have scanners.
- [x] Seen on the owner's PC (2026-11-16): a scan through Windows' entry failed with 0x80190199, which is the scanner's HTTP 409 ("these settings conflict") passed on by Windows' eSCL driver. `scanner.py` now reads such codes (`_http_answer`), tries once more with only the needed settings (grey for black and white; intent and area as the driver had them) and otherwise points to the "(network)" entry. Which setting the HP refuses is still to find out (the colour setting, resolution and source used).
- [ ] Left for later: choosing the page size; the network scanner's own resolutions in the Resolution list (the nearest offered is used now); WSD scan.

## Phase 2 (release B): scanners without WIA or AirScan (TWAIN-only and the rest)

**Decided with the owner (2026-11-15): ZPDF doesn't write its own TWAIN helper.** Most TWAIN drivers are 32-bit, so driving them takes compiled code in two builds, a native build step in CI and a real TWAIN scanner to test on, none of which we have. These scanners are covered two other ways, both without compiled code in ZPDF.

**Task 2.1: scan to a folder, picked up by a watch folder** (done in 0.45.0)
- [x] "My scanner isn't listed…" on the Scan page: the folder the scanner saves to, what happens to each scan (Make searchable, another tool, or a saved recipe), "Pages scanned together become one document"; it adds a watch folder with `images` (and `batch`). The watched scan folders are listed there with what they've done; "All watch folders…" opens Settings.
- [x] `watch.py`: `images` (JPEG, PNG, BMP, TIFF with many pages become a PDF first, at the scan's own size when the file says its resolution) and `batch` (seconds: files within that of each other are one document, taken once nothing new has come for that long and nothing is still being written; pages in name order). `process_files`, `candidates`. Settings → Watch folders has both as tick boxes; existing folders are unchanged.
- [x] Tests: `tests/test_scan_folder.py`, `tests/test_scan_folder_ui.py` + `tests/scanfolder.js`.
- [x] From the Windows check: a file that isn't a picture no longer sinks the pages that arrive with it (left out and said; `left_out`); arrival is the later of the written and the created time (`_arrived`), so files copied in group by when they came; the guide's list keeps itself up to date.
- [ ] Seen in the check, to look at: the HP took about 2 minutes to show up in the scanner list (WIA listing, the network search, or both: to time on that PC); Remove has no confirmation; HP Smart's own scan-to-folder output not seen (item H).
- [ ] Left for later: the batch time as a setting (20 seconds now); `pdfm watch --images / --batch` on the command line; cleaning up scans from a folder like the Scan page does (straighten, blank pages) without a recipe; watching while ZPDF is closed (Phase 3).

**Task 2.2: NAPS2 as an optional helper, if it's installed** (built in 0.53.0, not tried on real hardware)
- [x] **Confirmed (2026-11-17, from NAPS2's repository; naps2.com itself wasn't reachable from the build machine):** GPL 2.0 or later, so run as a separate program and never bundled; the console program is `NAPS2.Console.exe` (it starts `NAPS2.exe console …` from its own folder); options read from `NAPS2.Lib/Automation/AutomatedScanningOptions.cs` at 8.6.0: `-o`, `--noprofile`, `--driver wia|twain|escl|sane|apple`, `--device` (name, may be inexact, or exact id), `--listdevices` (needs `--driver`; prints one name a line), `--source glass|feeder|duplex`, `--dpi`, `--bitdepth color|gray|bw`, `--pagesize`, `-n`, `-f`, `-v`. It sets no exit code of its own for a failed scan: success is told from the file it leaves.
- [x] `naps2.py`: `find` (`PDFM_NAPS2`, the PATH, Program Files\NAPS2), `devices` (TWAIN only; kept a minute), `scan` (to one PDF, whose page pictures are taken as they are; cancel stops it). In `scanner.all_scanners` / `scan_any` under ids `naps2:twain:NAME`. The Scan page's guide says whether it is installed.
- [x] Tests: `tests/test_naps2.py` with `tests/fakenaps2.py` standing in for the console program.
- [x] **Seen on the owner's PC (check of f4e10c4, NAPS2 8.6.0 installed):** `C:\Program Files\NAPS2\NAPS2.Console.exe`, found; `--listdevices --driver twain` prints nothing when there is none (exit 0); a missing device prints "The selected scanner could not be found." and an unreachable one "The selected scanner is offline." then "0 page(s) scanned." and "No scanned pages to export." (exit 1 for both, no file); no window shows. `_why` now reads these words. No TWAIN device exists on that PC, so no scan through NAPS2 was made.
- [x] From the same check: every list of scanners took about 52 s there, in 0.52.0 too, because `list_scanners` connected to each WIA device to read its feeder and the HP was off. The connecting is now done beside the list (`_learn`, remembered in `_caps`; a list waits 2 s for answers at most).
- [ ] **To check on a PC with NAPS2 and a scanner:** where its installer really puts `NAPS2.Console.exe` (assumed `Program Files\NAPS2`; the Store and portable versions differ); what `--listdevices` prints when there is none, and in another language; what it prints for an empty feeder, a cancelled TWAIN dialog, a device in use (the words `_why` looks for are guesses); whether the driver's own window shows; how long the list takes (it is asked for with every look at the Scan page's list, at most once a minute).
- [ ] Left for later: the pages as they come (NAPS2 writes them at the end); page size; a NAPS2 profile by name; WIA and eSCL through NAPS2 as a fallback for devices ZPDF's own code can't drive.
- [ ] NAPS2 is a free, open-source scanning program that already drives TWAIN (32-bit drivers too), WIA and eSCL, with a command-line mode. ZPDF uses it the way it uses LibreOffice or Tesseract: found if installed (`winprograms.py`), never bundled.
- [ ] **Before building: confirm** NAPS2's command-line options as they are today (listing devices, choosing the driver and device, resolution, colour, feeder/flatbed/duplex, output files) and that its licence allows being run as a separate program this way (the same footing as LibreOffice). Both are from memory so far.
- [ ] With NAPS2 present, the Scan page lists its devices under "More scanners (through NAPS2)", and Scan runs it for the pages, which then go through ZPDF's own clean-up and OCR. Without it, "My scanner isn't listed" offers it as the second way ("Install NAPS2 (free) to scan from ZPDF with this scanner"), after scan-to-folder.
- [ ] Errors in plain words: NAPS2 not found, no device, the feeder empty, cancelled.
- [ ] Tests: a fake `naps2.console` (a script that answers the list and writes pages) behind the same `scanner` interface; the Scan page's list with and without it.
- [ ] Check on real hardware when someone has a TWAIN-only scanner; until then it's marked "through NAPS2: not tried on a real TWAIN scanner".

**Given up, knowingly:** for these scanners the scan settings live in the vendor's program or in NAPS2, not on ZPDF's Scan page; NAPS2 is a separate install.

## Phase 3 (release C): start at login, in the tray, for Watch folders — done in 0.46.0

- [x] **Background mode:** `pdfm gui --background` (and `pdfm-app --background`) starts the app's own window hidden with only the notification-area icon (`gui.launch(background_start=True)`, `run_desktop(hidden=True)`). The icon (`tray.py`: Shell_NotifyIconW through ctypes, its own hidden window and message loop, re-added when Explorer restarts) has Open ZPDF, Pause/Resume watching, Quit ZPDF; a click opens.
- [x] **Closing the window** with a folder being watched: asked once ("ask"), then kept as "yes" or "no" (`background.keep`); with yes the window hides (`background.InBackground.closing`). Without watch folders, or all paused, closing quits as before. If the icon can't be shown the window comes straight back: ZPDF is never left running out of sight.
- [x] **Start at sign-in:** a per-user Run entry written by Settings (`background.set_start_at_login`), off by default, removed by the uninstaller (`winsetup.remove` and `uninstall.ps1`). Turning it on also sets "keep watching".
- [x] **One copy:** a start at sign-in while ZPDF is open asks quietly whether a copy is running and does nothing more (`forward_to_running_app(quiet=True)`); an ordinary second start (a double-clicked PDF) shows the hidden window again.
- [x] **Said, not silent:** a watched file done or failed is said from the icon while the window is hidden (`WatchFolders.on_news`).
- [x] **Tests:** `tests/test_background.py` (fakes for the window, the icon and the registry; the real icon's Win32 calls on the Windows CI job).
- [x] **Seen on the owner's PC (check of d3ef2c3):** hiding to the icon and coming back, a file done while hidden, a file opened from outside while hidden, the settings, the Run entry and a start with `--background`, a second such start doing nothing, the icon back after Explorer restarts. Memory while hidden is the same as open (about 610 MB with WebView2's processes).
- [ ] **Still not seen:** the icon's right-click menu (Open, Pause/Resume, Quit) and Quit with unsaved work (the check couldn't right-click the notification area); the notes from the icon (Do not disturb was on); signing out and in; a restart for an update while hidden.
- [ ] Earlier list, for reference: pywebview's `hidden=True`, `hide()` and `show()`; the balloon; the icon after Explorer restarts; memory used by the hidden window (the page stays loaded).
- [ ] Left for later: the Edge app-window and browser modes (no window of their own to hide); unloading the page while hidden to save memory; updates restarting the background copy; Linux (an autostart `.desktop` file) and macOS (a LaunchAgent).

## Phase 4 (release D): PDF thumbnails in File Explorer

*(Deferred / gated on code-signing decision: Windows 11 Smart App Control blocks unsigned Explorer shell-extension DLLs.)*

- [ ] **How Windows does it:** a thumbnail handler is a COM object (`IThumbnailProvider` with `IInitializeWithStream`) that Explorer loads **inside its own process** (or `dllhost`). It must be native code: small, fast, and unable to crash Explorer. Python can't be it.
- [ ] **The handler:** a small C++ DLL (`zpdf-thumb.dll`, 64-bit, plus ARM64 later) that renders page 1 with **PDFium** (the same library ZPDF already ships through pypdfium2, loaded from ZPDF's folder) to the size Explorer asks for, on a white background, honouring rotation. A password-protected or broken file gives no thumbnail (Explorer falls back to the icon). A time and memory limit per file.
- [ ] **Registering it:** per user, without administrator rights (`HKCU\Software\Classes\.pdf\ShellEx\{e357fccd-a995-4576-b01f-234630154e96}` → the handler's CLSID), by the installer and by a switch in Settings ("Show the first page as the icon in File Explorer"). It applies when ZPDF is the default app for PDFs, or for everyone's `.pdf` if the user chooses so; the uninstaller removes it. The preview pane (`IPreviewHandler`) is a possible follow-up, not in this release.
- [ ] **Unsigned DLLs and Smart App Control:** Windows 11's Smart App Control blocks unsigned DLLs in Explorer. Without a code-signing certificate (the owner's standing decision so far is unsigned) the handler won't load on PCs with Smart App Control on; the switch says so when it detects it. Signing would lift this (security plan, Phase 5).
- [ ] **Build and release:** the DLL is built in CI (MSVC) and shipped in the release; `release.py` checks it's there and its version matches.
- [ ] **Tests:** the DLL rendered against sample PDFs in the Windows CI job (through a small test host that calls `IThumbnailProvider`); registration written and removed with a fake registry.
- [ ] **Check on Windows:** thumbnails in large icons, the Details pane, an Open dialog; a 500 MB drawing set doesn't stall the folder; turning the switch off brings the icons back (after Explorer's thumbnail cache is cleared).

## Order and size

| Release | What | Size | Risk |
|---|---|---|---|
| A | eSCL / AirScan network scanners | small–medium | low: plain HTTP, testable with a fake device |
| B | Scan to a folder with a guided watch folder; NAPS2 if installed | small–medium | low: no compiled code; NAPS2's command line to confirm first |
| C | Tray, background watching, start at login | medium | low–medium: process lifetime, one running copy |
| D | Explorer thumbnails | medium | medium–high: native DLL inside Explorer; Smart App Control blocks it unsigned |

**Order:** A, B, C (all Python, checkable now), then D, the only one that needs a native build step in CI. B's scan-to-folder is most useful together with C (watching without the window open).

**Decisions for the owner:** D adds compiled Windows code to the releases (today everything is Python and JavaScript) and is limited on PCs with Smart App Control unless releases are code-signed. TWAIN through our own helper was decided against (Phase 2).
