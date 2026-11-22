# ZPDF / PDF-Manager — Release Readiness Report (Phase 11 / Stage G)

> **Evaluated revision**: `447a664628f38479db052266b8e47e2c1d45d5cf` (`main`, post-remediation over `v0.61.1`)
> **Date**: `2026-11-22`

---

## 1. Phase 11 Release Validation Checklist

| # | Check | Verification Method | Result |
|---:|---|---|---|
| 1 | Version consistency | `pdfmanager/__init__.py` (`0.61.1`), `python3 -m pdfmanager --version` (`pdfm 0.61.1`) | **Verified** |
| 2 | Dependency configuration | `pyproject.toml` & `requirements.txt` synced (`11` core deps, `>=3.10`) | **Verified** |
| 3 | Python bytecode compilation | `python3 -m compileall -q pdfmanager` (exit code 0 across all 102 modules) | **Verified** |
| 4 | Test collection & execution | Baseline 1,675/1,675 runnable tests + 41 new remediation tests + full multi-platform CI (PRs #146, #147, #148) | **Verified** |
| 5 | Security regression tests | `tests/test_stage_b_security.py`, `tests/test_stage_c_reliability.py`, `tests/test_stage_def_audit.py`, `test_security_phase4/6/7/8.py`, `CodeQL` | **Verified** |
| 6 | CLI startup | `python3 -m pdfmanager --version`, `python3 -m pdfmanager policy show` | **Verified** |
| 7 | Local HTTP API / GUI server | `tests/test_stage_def_audit.py`, `tests/test_audit_server.py`, Playwright `e2e` CI job | **Verified** |
| 8 | Cross-platform CI | `ubuntu-latest` (Py 3.10, 3.13), `windows-latest` (Py 3.13), `macos` (`audit.yml`), `OCR/PDF-A/Office` | **Verified** (Linux & Windows full suite; macOS audit job only) |
| 9 | Update verification & extraction | Symlink/zip-bomb/corrupt-zip/downgrade tests passing (`tests/test_stage_b_security.py`) | **Verified** |
| 10 | Release signing key provisioning | `pdfmanager/relsign.py:RELEASE_PUBLIC_KEY == ""` | **Blocked — Owner Action Required** |
| 11 | Documentation consistency | `ENGINEERING_BASELINE.md`, `ENGINEERING_AUDIT_REPORT.md`, `SECURITY_REMEDIATION_REPORT.md`, `PERFORMANCE_REPORT.md`, `TEST_VALIDATION_REPORT.md` | **Verified** |
| 12 | License preservation | `LICENSE` (PolyForm Internal Use License 1.0.0 with personal-use permission) untouched | **Verified** |

---

## 2. Published Release Artifacts & Checksums (`v0.61.1`)

Published on `Tempura-446/ZPDF-releases` (`tag: v0.61.1`, workflow run `45049913587`):
- `ZPDF-0.61.1-windows.zip` (`8,804,946` bytes)
- `ZPDF-0.61.1-windows.zip.sha256` (`90` bytes)

> Note: The remediations from PRs #145–#148 are merged on `main` (`447a664`) and ready to be cut as **`v0.61.2`** once the owner decides whether to provision `RELEASE_PUBLIC_KEY` first. Local wheel generation via `pip wheel --no-build-isolation` was skipped because `setuptools` is not preinstalled in the isolated offline VM environment (packaging is executed by GitHub Actions `release.yml`).

---

## 3. Documented Remaining Limitations

1. **Production Ed25519 Release Signing Key (`REL-SIGN-001`)**: `pdfmanager/relsign.py:RELEASE_PUBLIC_KEY` is intentionally empty (`""`) because only the repository owner should generate and hold the production signing key (`python packaging/release.py --keygen` + `RELEASE_SIGNING_KEY` Actions secret). Until provisioned, published updates are verified by SHA-256 checksum over HTTPS rather than Ed25519 signature.
2. **FIPS Mode Scope (`SEC-001`)**: `fips_mode` enforces software algorithm-policy filtering (rejecting RC4, MD5, SHA-1, DES, 3DES, AES-128-ECB) at encryption and signature boundaries. It does **not** claim FIPS 140-2/140-3 validated cryptographic module certification.
3. **macOS Interactive Runtime**: macOS `audit.yml` passed in CI, but full interactive macOS `.app` bundle / PyWebView testing was not performed on macOS hardware.
4. **Offline Signature Revocation (`SIGN-001`)**: `certsign.verify()` runs with `allow_fetching=False` by default for privacy/offline determinism and explicitly reports `revocation_status: "not_checked"` unless a revocation is embedded.
5. **1,000-Page & Optional-OCR Benchmarks**: Multi-scale benchmarks were measured at 10, 100, and 500 pages on a 2-vCPU Linux VM without optional `rapidocr`/`onnxruntime` installed.

---

## 4. Final Release Decision

### **APPROVED WITH DOCUMENTED LIMITATIONS**

All critical and high-severity code defects in `v0.61.1` (`SEC-001`, `SEC-002`, `SEC-003`, `SEC-004`, `SEC-005`, `REL-001`, `REL-002`, `REL-003`, `SIGN-001`, `API-001`) have been fixed, regression-tested, and verified across Linux, Windows, macOS audit, OCR/Office, Playwright E2E, and CodeQL CI pipelines. The remaining non-code release item — provisioning the owner's production Ed25519 signing key in `relsign.py` — is explicitly documented above along with FIPS algorithm-policy scope and macOS interactive testing limits.
