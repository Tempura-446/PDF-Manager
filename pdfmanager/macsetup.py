# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""macOS bundle metadata (Info.plist), Finder Quick Actions, Homebrew install
hints, and Gatekeeper instructions for unsigned macOS builds."""

import plistlib
import shlex
import shutil
from pathlib import Path

from pdfmanager import core

BUNDLE_ID = "io.github.pdfmanager"
QUICK_ACTION_NAME = "Open in ZPDF.workflow"

_BREW_HINTS = {
    "libreoffice": "brew install --cask libreoffice",
    "tesseract": "brew install tesseract",
    "ghostscript": "brew install ghostscript",
    "chromium": "brew install --cask google-chrome",
    "scanimage": "brew install sane-backends",
}


def info_plist_dict(version, bundle_id=BUNDLE_ID):
    """Return the Info.plist dictionary for ``ZPDF.app`` declaring PDF document
    types so Finder 'Open With' and double-click open PDFs in ZPDF."""
    ver = str(version or "0.0.0")
    return {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": "ZPDF",
        "CFBundleExecutable": "ZPDF",
        "CFBundleIconFile": "icon.icns",
        "CFBundleIdentifier": bundle_id,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "ZPDF",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": ver,
        "CFBundleVersion": ver,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "PDF Document",
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Alternate",
                "CFBundleTypeExtensions": ["pdf"],
                "LSItemContentTypes": ["com.adobe.pdf", "public.pdf"],
            }
        ],
    }


def info_plist(version, bundle_id=BUNDLE_ID):
    """Return XML ``Info.plist`` bytes for ``ZPDF.app``."""
    return plistlib.dumps(info_plist_dict(version, bundle_id=bundle_id), fmt=plistlib.FMT_XML)


def gatekeeper_note(version):
    """Plain-language Gatekeeper note included inside the unsigned macOS .dmg."""
    return (
        f"ZPDF {version} for macOS (unsigned build)\n"
        "========================================\n\n"
        "This macOS build is built and tested automatically in CI, and is not\n"
        "signed with an Apple Developer certificate.\n\n"
        "On first launch, macOS Gatekeeper may block it. To open ZPDF:\n"
        "1. Drag ZPDF.app into your Applications folder.\n"
        "2. Right-click (or Control-click) ZPDF.app and choose Open, OR open\n"
        "   System Settings -> Privacy & Security and click 'Open Anyway'.\n"
        "3. Alternatively, run in Terminal:\n"
        "       xattr -dr com.apple.quarantine /Applications/ZPDF.app\n"
    )


def missing_program_hint(program):
    """Return a Homebrew install command hint for ``program`` on macOS."""
    key = str(program or "").strip().lower()
    aliases = {"gs": "ghostscript", "chrome": "chromium", "google-chrome": "chromium", "sane": "scanimage"}
    key = aliases.get(key, key)
    hint = _BREW_HINTS.get(key)
    if not hint:
        raise core.PdfManagerError(f"unknown program: {program!r}")
    return hint


def install_quick_action(exec_path, services_dir=None):
    """Install a Finder Quick Action (``~/Library/Services/Open in ZPDF.workflow``)
    for opening selected PDFs in ZPDF."""
    root = Path(services_dir) if services_dir is not None else (Path.home() / "Library" / "Services")
    wf = root / QUICK_ACTION_NAME
    contents = wf / "Contents"
    contents.mkdir(parents=True, exist_ok=True)

    info = {
        "NSServices": [
            {
                "NSMenuItem": {"default": "Open in ZPDF"},
                "NSMessage": "runWorkflowAsService",
                "NSSendFileTypes": ["com.adobe.pdf"],
            }
        ]
    }
    (contents / "Info.plist").write_bytes(plistlib.dumps(info, fmt=plistlib.FMT_XML))

    quoted = shlex.quote(str(exec_path))
    doc = {
        "AMApplicationBuild": "523",
        "AMApplicationVersion": "2.10",
        "actions": [
            {
                "action": {
                    "ActionParameters": {
                        "COMMAND_STRING": f'for f in "$@"; do {quoted} "$f" & done',
                        "inputMethod": 1,
                        "shell": "/bin/sh",
                    },
                    "BundleIdentifier": "com.apple.RunShellScript",
                }
            }
        ],
        "workflowMetaData": {
            "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject.PDF",
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }
    (contents / "document.wflow").write_bytes(plistlib.dumps(doc, fmt=plistlib.FMT_XML))
    return {"workflow": str(wf)}


def remove_quick_action(services_dir=None):
    """Remove the Finder Quick Action workflow if present."""
    root = Path(services_dir) if services_dir is not None else (Path.home() / "Library" / "Services")
    wf = root / QUICK_ACTION_NAME
    if wf.is_dir():
        shutil.rmtree(wf)
        return {"removed": [str(wf)]}
    return {"removed": []}
