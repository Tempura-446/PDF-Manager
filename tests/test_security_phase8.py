# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Security Phase 8 tests: Enterprise Policies, ADMX/ADML, FIPS mode, Offline Updates & Job Isolation."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pdfmanager import cli, core, isolation, policy

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_pdf(path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(72, 720, "Enterprise policy test PDF.")
    c.showPage()
    c.save()
    return path


# ---------------------------------------------------------------------------
# Administrator Policy Precedence (HKLM > HKCU > JSON) & Locked State
# ---------------------------------------------------------------------------

def test_policy_precedence_hklm_over_hkcu_over_json(tmp_path):
    pol_json = tmp_path / "policy.json"
    pol_json.write_text(
        json.dumps({"disable_updates": False, "fips_mode": True, "force_security_profile": "Internal"}),
        encoding="utf-8",
    )

    def fake_registry(hive, _subkey):
        if hive == "HKEY_CURRENT_USER":
            return {"disable_updates": 1, "force_security_profile": "Public Share"}
        if hive == "HKEY_LOCAL_MACHINE":
            return {"force_security_profile": "Confidential", "disable_network": 1}
        return {}

    eff = policy.load(reload=True, registry_reader=fake_registry, policy_file=pol_json)
    assert eff["fips_mode"] is True
    assert eff["disable_updates"] is True
    assert eff["disable_network"] is True
    assert eff["force_security_profile"] == "Confidential"

    assert policy.is_locked("force_security_profile", policies=eff) is True
    assert policy.is_locked("offline_update_dir", policies=eff) is False

    snap = policy.snapshot(policies=eff)
    assert snap["disable_network"] == {"value": True, "locked": True}
    assert snap["offline_update_dir"] == {"value": "", "locked": False}


# ---------------------------------------------------------------------------
# FIPS Mode & Disable Password Removal Enforcement
# ---------------------------------------------------------------------------

def test_fips_mode_refuses_rc4_and_weak_algorithms():
    fips_on = {"fips_mode": True}
    for bad in ("RC4", "rc4-128", "MD5", "SHA-1", "sha1"):
        with pytest.raises(policy.PolicyError, match="FIPS mode"):
            policy.check_fips_algorithm(bad, policies=fips_on)

    # Strong algorithms remain allowed
    policy.check_fips_algorithm("AES-256", policies=fips_on)
    policy.check_fips_algorithm("SHA-256", policies=fips_on)
    # When FIPS mode is off, check is a no-op
    policy.check_fips_algorithm("RC4", policies={"fips_mode": False})


def test_disable_password_removal_blocks_core_decrypt(tmp_path, monkeypatch):
    pol_json = tmp_path / "policy.json"
    pol_json.write_text(json.dumps({"disable_password_removal": True}), encoding="utf-8")
    monkeypatch.setenv("ZPDF_POLICY_FILE", str(pol_json))
    policy.clear_cache()

    src = _make_pdf(tmp_path / "plain.pdf")
    enc = tmp_path / "enc.pdf"
    dec = tmp_path / "dec.pdf"
    core.encrypt(src, enc, user_password="Secret123!")

    try:
        with pytest.raises(core.PdfManagerError, match="security policy"):
            core.decrypt(enc, dec, "Secret123!")
        assert not dec.exists()
    finally:
        monkeypatch.delenv("ZPDF_POLICY_FILE", raising=False)
        policy.clear_cache()


# ---------------------------------------------------------------------------
# Offline Update Channel
# ---------------------------------------------------------------------------

def test_offline_update_candidates_reads_local_share(tmp_path):
    share = tmp_path / "company_updates"
    share.mkdir()
    pkg = share / "ZPDF-0.60.4-windows.zip"
    sha = share / "ZPDF-0.60.4-windows.zip.sha256"
    pkg.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    sha.write_text("deadbeef  ZPDF-0.60.4-windows.zip\n", encoding="utf-8")

    items = policy.offline_update_candidates(policies={"offline_update_dir": str(share)})
    assert len(items) == 1
    assert items[0]["name"] == "ZPDF-0.60.4-windows.zip"
    assert items[0]["sha256_path"] == str(sha)


# ---------------------------------------------------------------------------
# Process Isolation (Success, Timeout, Failure)
# ---------------------------------------------------------------------------

def test_isolation_runs_callable_and_enforces_timeout():
    # Normal execution succeeds in spawned child
    val = isolation.run_isolated("math:sqrt", 144, timeout=15.0)
    assert val == 12.0

    # Timeout kills hung worker cleanly
    with pytest.raises(isolation.IsolationTimeoutError, match="timed out"):
        isolation.run_isolated("time:sleep", 10.0, timeout=0.4)

    # Unhandled exception in child returns clean IsolationError
    with pytest.raises(isolation.IsolationError, match="ZeroDivisionError"):
        isolation.run_isolated("operator:truediv", 1, 0, timeout=15.0)


# ---------------------------------------------------------------------------
# ADMX / ADML Group Policy XML Validation & CLI `pdfm policy show`
# ---------------------------------------------------------------------------

def test_admx_and_adml_templates_are_well_formed_xml():
    admx = REPO_ROOT / "packaging" / "windows" / "policy" / "ZPDF.admx"
    adml = REPO_ROOT / "packaging" / "windows" / "policy" / "en-US" / "ZPDF.adml"
    assert admx.is_file()
    assert adml.is_file()

    admx_root = ET.parse(admx).getroot()
    adml_root = ET.parse(adml).getroot()
    assert admx_root.tag.endswith("policyDefinitions")
    assert adml_root.tag.endswith("policyDefinitionResources")


def test_cli_policy_show(capsys):
    assert cli.main(["policy", "show", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "fips_mode" in data
    assert "disable_password_removal" in data
