# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""GUI review and wiring regression tests."""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = REPO_ROOT / "pdfmanager" / "static"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_all_static_js_files_pass_node_syntax_check():
    js_files = sorted(STATIC_DIR.glob("*.js"))
    assert js_files
    for js in js_files:
        res = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True, check=False)
        assert res.returncode == 0, f"Syntax error in {js.name}: {res.stderr}"


def test_redact_live_preview_sends_regulated_presets_and_regex():
    tools_js = (STATIC_DIR / "tools.js").read_text(encoding="utf-8")
    for key in ('"regex"', '"ssn"', '"ein"', '"uk_ni"', '"iban"', '"credit_card"'):
        assert key in tools_js, f"PANELS.redact missing live preview field {key}"


def test_reader_save_surfaces_textedit_summary_and_visual_headers():
    save_js = (STATIC_DIR / "reader-save.js").read_text(encoding="utf-8")
    assert "X-Pdfm-Summary" in save_js
    assert "X-Pdfm-Visual" in save_js
    assert "visually covered" in save_js


def test_settings_gui_wires_audit_log_and_policy_controls():
    html = (REPO_ROOT / "pdfmanager" / "gui.html").read_text(encoding="utf-8")
    settings_js = (STATIC_DIR / "settings.js").read_text(encoding="utf-8")
    for dom_id in ("show-audit", "verify-audit", "audit-status", "policy-status"):
        assert f'id="{dom_id}"' in html, f"gui.html missing #{dom_id}"
        assert dom_id in settings_js, f"settings.js missing handler/ref for #{dom_id}"
    assert "/api/audit" in settings_js
    assert "/api/policy" in settings_js
