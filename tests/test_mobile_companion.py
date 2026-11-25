# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for v0.73.0 Android Companion App (Phase 1 Foundation):
- PWA manifest & service worker endpoints (/manifest.webmanifest, /sw.js)
- Touch-first mobile reader module (static/mobile-touch.js)
- Android Kotlin WebView companion scaffold (mobile/android/)
"""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

from pdfmanager import gui

ROOT = Path(__file__).resolve().parent.parent


def test_pwa_manifest_and_service_worker_served():
    server, _url = gui.make_server(port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(f"{base}/manifest.webmanifest", timeout=5) as resp:
            assert resp.status == 200
            assert "manifest+json" in resp.headers.get("Content-Type", "")
            manifest = json.loads(resp.read().decode("utf-8"))
        assert manifest["short_name"] == "ZPDF"
        assert manifest["display"] == "standalone"
        assert manifest["start_url"] == "/"
        assert manifest["icons"]

        with urllib.request.urlopen(f"{base}/sw.js", timeout=5) as resp:
            assert resp.status == 200
            assert "javascript" in resp.headers.get("Content-Type", "")
            sw_text = resp.read().decode("utf-8")
        assert "/api/" in sw_text

        with urllib.request.urlopen(f"{base}/static/icon.svg", timeout=5) as resp:
            assert resp.status == 200
            assert "svg" in resp.headers.get("Content-Type", "")

        with urllib.request.urlopen(f"{base}/static/mobile-touch.js", timeout=5) as resp:
            assert resp.status == 200
            assert "javascript" in resp.headers.get("Content-Type", "")
    finally:
        server.shutdown()
        server.server_close()


def test_android_companion_scaffold_files_present():
    android = ROOT / "mobile" / "android"
    manifest = android / "app" / "src" / "main" / "AndroidManifest.xml"
    activity = android / "app" / "src" / "main" / "java" / "com" / "tempura446" / "zpdf" / "MainActivity.kt"
    gradle = android / "app" / "build.gradle.kts"
    readme = android / "README.md"
    for p in (manifest, activity, gradle, readme):
        assert p.is_file(), f"missing {p}"
    assert "application/pdf" in manifest.read_text(encoding="utf-8")
    assert "WebView" in activity.read_text(encoding="utf-8")
