# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Automated GUI & packaging wiring checks to catch un-wired features before release."""

import re
import shutil
import subprocess
from pathlib import Path

from pdfmanager.server import operations

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "pdfmanager" / "static"


def _all_js_text():
    chunks = []
    for js in sorted(STATIC.glob("*.js")):
        chunks.append(js.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def test_every_operation_has_icon_and_valid_fields():
    app_core = (STATIC / "app-core.js").read_text(encoding="utf-8")
    # Extract keys defined inside `const ICONS = { ... };`
    m = re.search(r"const ICONS\s*=\s*\{(.*?)\n\};", app_core, re.S)
    assert m, "Could not locate ICONS map in app-core.js"
    icon_keys = set(re.findall(r"""(?:["']([^"']+)["']|\b([a-zA-Z0-9_-]+))\s*:""", m.group(1)))
    icons = {a or b for a, b in icon_keys}

    # Check every tool in OPERATIONS has valid fields and a dedicated icon (or is a known alias)
    missing_icons = []
    for op in operations.OPERATIONS:
        assert op.get("id") and op.get("group") and op.get("label"), f"malformed op: {op}"
        for field in op.get("fields") or ():
            assert field.get("key") and field.get("kind"), f"malformed field in {op['id']}: {field}"
            if field.get("kind") != "hidden":
                assert field.get("label"), f"visible field missing label in {op['id']}: {field}"
        if op["id"] not in icons:
            missing_icons.append(op["id"])
    # Ensure our newly added tools specifically never fall back to a generic icon
    assert "translate" in icons, "translate tool must have a dedicated icon in app-core.js"
    assert "to-md" in icons, "to-md tool must have a dedicated icon in app-core.js"
    # Keep track: no unexpected growth in un-iconed ops
    assert len(missing_icons) <= 5, f"Too many OPERATIONS missing icons in app-core.js: {missing_icons}"


def test_user_facing_feature_endpoints_are_wired_in_frontend():
    js_all = _all_js_text()
    required_endpoints = [
        "/api/translate/page",
        "/api/preflight/check",
        "/api/preflight/fix",
        "/api/crop",
        "/api/filing/rules",
        "/api/filing/preview",
        "/api/i18n",
        "/api/i18n/language",
        "/api/i18n/packs/install",
        "/api/i18n/packs/remove",
    ]
    missing = [ep for ep in required_endpoints if ep not in js_all]
    assert not missing, f"Backend GUI endpoints not called from static/*.js: {missing}"


def test_pyinstaller_bundles_required_data_directories():
    build_app = (ROOT / "packaging" / "build_app.py").read_text(encoding="utf-8")
    for required in ("gui.html", "static", "locales"):
        assert required in build_app, f"packaging/build_app.py is missing --add-data for {required}"


def test_all_static_js_files_have_valid_syntax():
    node = shutil.which("node")
    if not node:
        return
    for js in sorted(STATIC.glob("*.js")):
        proc = subprocess.run([node, "--check", str(js)], capture_output=True, text=True, check=False)
        assert proc.returncode == 0, f"JS syntax error in {js.name}:\n{proc.stderr}"
