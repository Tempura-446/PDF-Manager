# macOS and Linux Builds: Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ZPDF installs and runs on macOS and on Linux the way it does on Windows: one download, a double-click, its own window, updates from the Update button. Asked for on 2026-11-15.

**Where it stands today**

- The engine and the app are Python and a local web page, so most of ZPDF already runs on all three. The tests run on Ubuntu in CI, and `build-app.yml` already builds a standalone Linux app next to the Windows one.
- `appdata.py` already has a data folder for each system (`~/Library/Application Support/PDF Manager`, `~/.local/share/pdf-manager`).
- macOS was taken out of CI early on (no machine to check it on, and the minutes cost ten times as much). Nothing has been tried on a Mac since.
- What is Windows-only now:
  - the installer and uninstaller (`winsetup.py`, `packaging/windows`), the Start menu and desktop shortcuts, "Open with", the right-click menu in Explorer;
  - the app window through WebView2 (pywebview uses WebKit on macOS and GTK/Qt WebKit on Linux);
  - scanning from a scanner (WIA) in `scanner.py`;
  - Microsoft Word and PowerPoint through COM in `office.py` (LibreOffice is already the other route);
  - the print-to-PDF of web pages through Edge (`webpage.py`/`office.py`; Chrome or Chromium does the same);
  - the single-running-copy hand-off and restart after an update, written against Windows processes;
  - the self-hosted Windows runner and the Windows check sessions the releases rely on.

## Phase 1 (release A): Linux, properly

Linux first: CI already runs there, so every step can be checked without new hardware.

- [ ] **A real Linux run-through of the app**, not only the tests: the pywebview window (GTK WebKit, else Qt), falling back to a Chrome/Chromium app window, then a browser tab. File dialogs, Save as, drag and drop, opening a PDF given on the command line, a second launch handing its file to the running copy, closing.
- [ ] **Packages:** an AppImage (one file, runs anywhere) built by `build-app.yml` from the PyInstaller build, and a `.deb`. Each carries a `.desktop` entry (name, icon, `MimeType=application/pdf`) so ZPDF appears in the menu and under "Open with".
- [ ] **Outside programs:** find LibreOffice, Tesseract, Ghostscript and Chrome/Chromium in their usual Linux places and say plainly how to install the missing one (`apt install …`, `dnf install …`).
- [ ] **Scanning:** SANE (`scanimage`) behind the same `scanner.py` interface as WIA: list devices, flatbed or feeder, resolution, colour.
- [ ] **File manager actions:** the right-click actions as a Nautilus script / KDE service menu, written by Settings like the Explorer menu is.
- [ ] **Updates:** the Update button replaces the AppImage in place (it's one file); a `.deb` install says to update through the package.
- [ ] **CI:** the Linux build job smoke-tests the built app (starts, opens a PDF, runs one tool, closes) the way `windows-e2e.yml` does.
- [ ] **Tests:** the platform paths (`winsetup`'s Linux counterpart, SANE, the `.desktop` file) with fakes; the smoke test above.

## Phase 2 (release B): macOS

Needs a Mac to check on: the owner's, a friend's, or GitHub's macOS runners (paid minutes on a private repository, free on a public one).

- [ ] **macOS back in CI** for the test suite (`macos-latest`, Apple silicon), fixing what it turns up: paths, fonts (`fonts.py` looks in Windows and Linux folders), the default browser, case-insensitive file names.
- [ ] **The app bundle:** `ZPDF.app` from PyInstaller with an `.icns` icon and an `Info.plist` that declares PDF as a document type (so "Open With" and double-click work, through the `open-file` Apple event, which pywebview/pyobjc must pass on to the reader). A `.dmg` to drag it into Applications. Built for Apple silicon and Intel (two builds or one universal).
- [ ] **The window:** pywebview on WKWebView; check Save as, printing, the password-autofill switches (the WebView2 ones don't exist there), and that the native menu bar has the usual Quit, Close, Copy and Paste.
- [ ] **Signing and notarization (the owner's decision, costs money):** an unsigned app is blocked by Gatekeeper; friends would have to right-click → Open, or run `xattr -d com.apple.quarantine`. Passing Gatekeeper cleanly needs an Apple Developer account (US$99 a year), a Developer ID certificate and notarization in `release.py`. The README says how to open it either way.
- [ ] **Outside programs:** LibreOffice, Tesseract and Ghostscript from their installers or Homebrew; Microsoft Word for Mac through AppleScript only if it proves worth it (LibreOffice otherwise); Chrome or Edge for web pages (Safari can't print to PDF from a command).
- [ ] **Scanning:** Image Capture (ImageCaptureCore through pyobjc), or SANE where installed; otherwise "Scan" takes pictures and PDFs only and says so.
- [ ] **Finder:** a Quick Action ("Services") for the right-click actions.
- [ ] **Updates:** the Update button downloads the new `.dmg`/zip, replaces the bundle and starts again; with a signed build, the signature is checked first (ties in with the signed releases of the security plan, Phase 5).
- [ ] **Tests:** the bundle's `Info.plist` and document types; the open-file event with a fake; the suite green on `macos-latest`.

## Phase 3: one release, three systems

- [ ] `release.py` builds or collects all three (the Windows zip and installer, the AppImage and `.deb`, the `.dmg`), with a checksum each, and publishes them as one release. Builds for the other systems come from CI artifacts, since the owner's PC is Windows.
- [ ] The Update check picks the download for the system it runs on.
- [ ] README: an Install section per system; "what needs what" (which tools use LibreOffice, Tesseract, a scanner) per system.
- [ ] A check routine per system like the Windows one: who runs it, on what machine, with which files. Without one, a system is marked "builds, not checked by a person" in the README.

## Not taken on (and why)

- **Mac App Store / Microsoft Store / Flatpak on Flathub / Snap Store:** each has its own review, sandbox rules (ZPDF opens files anywhere and starts other programs) and, for some, fees. Direct downloads first.
- **iOS, iPadOS, Android:** a different app (no local Python, no file system of the same kind).
- **Word through COM on the other systems:** there is no COM; LibreOffice and the built-in converters do the work.

## Order and size

| Release | What | Size | Risk |
|---|---|---|---|
| A | Linux: run-through, AppImage and .deb, SANE, file manager actions, updates, CI smoke test | medium | low: CI already runs there |
| B | macOS: CI, .app and .dmg, WKWebView, Finder, updates; signing if the owner wants it | medium–large | medium: no Mac to check on yet; Gatekeeper without signing |
| C | One release for three systems, README, check routines | small | low |

**Decisions for the owner before B:** which Mac the checks run on; whether to pay for an Apple Developer account (signed, notarized builds) or ship unsigned with instructions.

**Place in the roadmap:** after the drawing tools' B2 and the security plan's release D/E (signed releases help all three systems), or sooner if a friend on a Mac or Linux is waiting. Linux (A) can go at any time.
