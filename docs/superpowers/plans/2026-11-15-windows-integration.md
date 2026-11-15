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

## Phase 1 (release A): driverless network scanners (eSCL / AirScan)

First, because it needs no driver and no native code, and works the same on Linux and macOS later.

- [ ] **Find scanners:** mDNS (`_uscan._tcp`, `_uscans._tcp`) on the local network, plus "Add by address" for networks where discovery is blocked. The find runs only when the Scan page asks, and is listed in "When ZPDF goes online" as a local-network request (privacy plan: nothing leaves the network).
- [ ] **Scan:** the eSCL protocol over HTTP(S): read `ScannerCapabilities` (flatbed, feeder, duplex, resolutions, colour modes, sizes), post a `ScanSettings` job, fetch pages (`NextDocument`) as JPEG or PDF until the feeder is empty, delete the job. Plain words for busy, cover open, jam, empty feeder.
- [ ] **One interface:** `scanner.list_scanners()` and `scanner.scan()` return eSCL devices beside WIA ones, marked "network"; the Scan page needs no new controls beyond duplex where the device has it.
- [ ] **TLS:** printers use self-signed certificates; accept them for the chosen device only and say so once.
- [ ] **Tests:** a fake eSCL device (a small local HTTP server) with a feeder of three pages, a jam, and a duplex job.

## Phase 2 (release B): scanners without WIA or AirScan (TWAIN-only and the rest)

**Decided with the owner (2026-11-15): ZPDF doesn't write its own TWAIN helper.** Most TWAIN drivers are 32-bit, so driving them takes compiled code in two builds, a native build step in CI and a real TWAIN scanner to test on, none of which we have. These scanners are covered two other ways, both without compiled code in ZPDF.

**Task 2.1: scan to a folder, picked up by a watch folder**
- [ ] Nearly every scanner (TWAIN-only ones included) has a button or a vendor program that saves scans into a folder. On the Scan page, "My scanner isn't listed" opens a short guide: choose (or make) the folder the scanner saves to, choose what happens to each scan (make searchable; or clean up, make searchable and compress; or a saved recipe), and where results go. It creates the watch folder (`watchfolders.py`) with those choices.
- [ ] Watch folders take the picture files scanners write (JPEG, PNG, TIFF, multi-page TIFF), not only PDFs: each becomes a PDF first (the Scan page's own path through `scanpdf.py`), then the recipe runs.
- [ ] Pages scanned one file each can be joined: files arriving within a short time of each other ("a batch") become one PDF, in name order, when the guide's "one document per batch" is on.
- [ ] The Scan page shows the watched scanner folder as a source ("Scans from folder: …"), with the last results and Open.
- [ ] Tests: the guide creates the watch folder with the chosen recipe; a JPEG and a multi-page TIFF dropped in come out as searchable PDFs; three files within the batch time become one PDF; a file still being written is left until it holds still.

**Task 2.2: NAPS2 as an optional helper, if it's installed**
- [ ] NAPS2 is a free, open-source scanning program that already drives TWAIN (32-bit drivers too), WIA and eSCL, with a command-line mode. ZPDF uses it the way it uses LibreOffice or Tesseract: found if installed (`winprograms.py`), never bundled.
- [ ] **Before building: confirm** NAPS2's command-line options as they are today (listing devices, choosing the driver and device, resolution, colour, feeder/flatbed/duplex, output files) and that its licence allows being run as a separate program this way (the same footing as LibreOffice). Both are from memory so far.
- [ ] With NAPS2 present, the Scan page lists its devices under "More scanners (through NAPS2)", and Scan runs it for the pages, which then go through ZPDF's own clean-up and OCR. Without it, "My scanner isn't listed" offers it as the second way ("Install NAPS2 (free) to scan from ZPDF with this scanner"), after scan-to-folder.
- [ ] Errors in plain words: NAPS2 not found, no device, the feeder empty, cancelled.
- [ ] Tests: a fake `naps2.console` (a script that answers the list and writes pages) behind the same `scanner` interface; the Scan page's list with and without it.
- [ ] Check on real hardware when someone has a TWAIN-only scanner; until then it's marked "through NAPS2: not tried on a real TWAIN scanner".

**Given up, knowingly:** for these scanners the scan settings live in the vendor's program or in NAPS2, not on ZPDF's Scan page; NAPS2 is a separate install.

## Phase 3 (release C): start at login, in the tray, for Watch folders

- [ ] **A background mode:** `pdfm-app --background` starts the server and the watch thread without a window. A **tray icon** (pystray, or the Win32 `Shell_NotifyIcon` through ctypes to avoid a dependency) with: Open ZPDF, the watched folders and what they did last, Pause watching / Resume, Settings, Quit.
- [ ] **Closing the window:** with any watch folder on, closing asks once: "Keep watching in the background?" (Yes keeps the tray icon; No quits). The answer is a setting. Without watch folders, closing quits as now.
- [ ] **Start at login:** a switch in Settings → Watch folders: "Start ZPDF when I sign in, to keep watching". It writes a per-user Run entry (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, no administrator rights) that starts `--background`. Off by default; shown in Windows' own Startup apps list; removed by the uninstaller.
- [ ] **One copy:** a window opened later (a double-clicked PDF, the Start menu) joins the background copy instead of starting a second one (the hand-off that exists today), and the watch lock stays with it.
- [ ] **Said, not silent:** a toast when a watched file is done or fails (the news the page shows today), through the tray icon; nothing else runs in the background, and the privacy list says what does.
- [ ] **Updates:** the Update button restarts the background copy too.
- [ ] **Linux and macOS later:** an autostart `.desktop` file / a LaunchAgent, with the same switch (builds plan).
- [ ] **Tests:** the Run entry written and removed (a fake registry); `--background` serves and watches with no window; closing with a watch folder keeps the server; the second launch joins.

## Phase 4 (release D): PDF thumbnails in File Explorer

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
