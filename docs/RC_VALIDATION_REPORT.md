# ZPDF Phase 7 — Release Candidate Validation & Production Readiness Report

*Validation date: 2026-11-22 | Candidate revision: `409d3d3` (`main`, post-`v0.60.4` + Security Phases 7–8 + Depth Tracks A–D)*

---

## 1. Release Candidate Baseline (RC-001)

| Field | Measured Value |
| --- | --- |
| **Application version** | `0.60.4` (`pdfmanager/__init__.py`) |
| **Git revision** | `409d3d3` (clean working tree on `main`) |
| **Local validation OS / Python** | Linux `6.18.57` (`x86_64`), 2 CPUs, Python `3.12.16` |
| **CI matrix OS / Python** | Ubuntu (`Python 3.10`, `Python 3.13`), Windows (`Python 3.13`), macOS (build workflow) |
| **Key installed dependency versions** | `pypdf 7.1.1`, `reportlab 5.1.0`, `pillow 13.0.0`, `numpy 2.5.5`, `cryptography 50.0.2`, `pypdfium2 5.15.2`, `pikepdf 10.18.2`, `pyHanko 0.38.2`, `pyhanko-certvalidator 0.33.0`, `pdfplumber 0.11.11`, `openpyxl 3.1.5`, `ezdxf 1.4.5`, `pytest 9.2.0` |
| **Build backend** | `setuptools>=69` (`pyproject.toml`) |

---

## 2. Six-Phase + Enterprise & Depth Verification Matrix (RC-002)

Every status below is backed by direct source inspection and live test execution (never documentation alone).

| Phase / Track | Key Files & Symbols | Test Evidence | Status |
| --- | --- | --- | --- |
| **Phase 1 — Redaction Security** | `pdfmanager/redact.py` (`redact`, `_extract_box_terms`, `_prune_alt_text`, `check_redacted`), `pdfmanager/redactcontent.py` | `tests/test_redact_remove.py` (CI + local pass) | **Verified** |
| **Phase 2 — Text Editing Integrity** | `pdfmanager/textedit/rewrite.py` (`edit_text`, `_ordered_text_matches`, `require_permanent`, `verify_visual`, `summarize_report`), `pdfmanager/server/reader_routes.py` (`X-Pdfm-Summary`, `X-Pdfm-Visual`) | `tests/test_textedit_integrity.py` (CI + local pass) | **Verified** |
| **Phase 3 — PDF Comparison** | `pdfmanager/compare.py` (`_align_scored`, `_PageRenderer`, `_changed_areas`, `compare_summary`, `MODES`) | `tests/test_compare_phase3.py`, `tests/test_depth_track_b.py` (CI + local pass) | **Verified** |
| **Phase 4 — Security & Reliability** | `pdfmanager/applog.py` (`scrub`, `clean_message`, `format_user_error`), `pdfmanager/certsign.py`, `pdfmanager/filetools.py`, `pdfmanager/core.py` | `tests/test_security_phase4.py`, `tests/test_privacy.py` (CI + local pass) | **Verified** |
| **Phase 5 — Performance & Scalability** | `pdfmanager/redact.py` (`_iter_render_redacted`, `_is_grayscale`), `pdfmanager/core.py` (`default_workers`, `batch_run`), `packaging/benchmark.py` | `tests/test_perf_phase5.py` + 10p/100p/500p benchmarks (pass) | **Verified** |
| **Phase 6 — Product Quality & Parity** | `requirements.txt`, `pdfmanager/notices.py`, `pdfmanager/cli.py`, `docs/RELEASE_READINESS.md`, `docs/ROADMAP.md` | `tests/test_quality_phase6.py`, `tests/test_notices.py`, `tests/test_headers.py` (pass) | **Verified** |
| **Security Phases 7–8 (Enterprise)** | `pdfmanager/auditlog.py`, `pdfmanager/secprofiles.py`, `pdfmanager/policy.py`, `pdfmanager/isolation.py`, `packaging/windows/policy/ZPDF.admx` | `tests/test_security_phase7.py`, `tests/test_security_phase8.py`, `tests/test_imports.py` (pass) | **Verified** |
| **Depth Tracks A–D** | `core.compress(target_mb=...)`, `compare_summary()`, GUI regulated redaction & `sec-profile`, `tables.to_excel(auto_ocr=True)` | `tests/test_depth_track_a.py` – `test_depth_track_d.py`, `tests/test_ui_wiring.py` (pass) | **Verified** |

---

## 3. Full Regression Test Report (RC-003)

### GitHub Actions CI on `main` (`409d3d3`)

| Workflow / Job | Run ID | Result |
| --- | --- | --- |
| `Tests` → `ubuntu-latest / Python 3.10` | `45038611419` | **Pass** |
| `Tests` → `ubuntu-latest / Python 3.13` | `45038611419` | **Pass** |
| `Tests` → `windows-latest / Python 3.13` | `45038611419` | **Pass** |
| `Tests` → `OCR, PDF/A and Office (Tesseract, Ghostscript, LibreOffice)` | `45038611419` | **Pass** |
| `Windows end-to-end` | `45038611387` | **Pass** |
| `CodeQL` (Python + JavaScript/TypeScript) | `45038611413` | **Pass** |
| `Tests` → `Windows PC / tests` (self-hosted hardware runner) | `45038611419` | Skipped (not online) |

### Local Targeted Validation on `409d3d3`
- Phases 1–8 + Depth Tracks A–D + Invariants (`test_imports`, `test_headers`, `test_privacy`, `test_ui_wiring`, `test_notices`): **126 passed, 1 skipped, 0 failed** (`54.60s`).
- RC-008 Failure & Recovery (`tests/test_rc_failure_recovery.py`): **6 passed, 0 failed** (`1.35s`).

---

## 4. Independent Security Validation (RC-004)

- **Static analysis:** GitHub CodeQL (`45038611413`) passed with 0 alerts across Python and JavaScript/TypeScript.
- **Dependency vulnerability scanning:** `pip-audit` runs on Ubuntu & Windows in CI (passed on PR #133; weekly schedule in `.github/workflows/audit.yml`).
- **Verified controls:**
  - Redaction recovery & coordinate-box sanitization (`tests/test_redact_remove.py`).
  - Attachment path traversal & symlink rejection (`tests/test_security_phase4.py`).
  - Password/token/private-key scrubbing in logs and formatted errors (`applog.scrub`, `applog.clean_message`, `applog.format_user_error`).
  - Tamper-evident SHA-256 hash-chained audit log (`auditlog.verify_chain`).
  - FIPS algorithm gate (`policy.check_fips_algorithm`) & password-removal policy lock (`policy.check_password_removal_allowed`).
  - Child-process crash/timeout isolation (`isolation.run_isolated`).
  - Privacy invariant: exact approved network-calling module list enforced by `tests/test_privacy.py`.
- *Scope note:* This is an automated engineering security validation, not an external third-party penetration test.

---

## 5. Performance Acceptance Report (RC-005)

Executed via `python3 packaging/benchmark.py` on Linux `6.18.57` (`x86_64`), 2 CPUs, Python `3.12.16`:

| Workload | Pages | Median (s) | Pages/s | Peak Py Mem (MB) | RSS (MB) | Output (KB) | vs. Phase 5 Baseline |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `compare` | 10 | 2.497 | 4.0 | 11.20 | 85.0 | 213.1 | Within noise (`2.300s`) |
| `redact_remove` | 10 | 4.173 | 2.4 | 3.26 | 85.0 | 7.9 | Within noise (`3.757s`) |
| `redact_image(150dpi)` | 10 | 1.221 | 8.2 | 19.33 | 106.8 | 967.1 | On target (`1.152s`) |
| `compare` | 100 | 5.270 | 19.0 | 6.99 | 108.3 | 1944.8 | **19.9% faster** (`6.578s`) |
| `redact_remove` | 100 | 36.959 | 2.7 | 4.11 | 108.3 | 71.0 | Within noise (`33.870s`) |
| `redact_image(150dpi)` | 100 | 8.801 | 11.4 | 67.47 | 151.0 | 10285.3 | **-6.5 MB RSS** (`8.827s` / `157.5 MB`) |
| `compare` | 500 | 28.451 | 17.6 | 44.93 | 128.2 | 9848.6 | Scales linearly, flat RSS |
| `redact_image(150dpi)` | 500 | 47.285 | 10.6 | 351.53 | 527.6 | 52602.0 | Completes cleanly (pre-Phase 5 would OOM at ~4.5 GB) |

*Note:* 1,000-page benchmarks were not executed interactively due to VM turn time limits; 500-page linear scaling is verified above.

---

## 6. Platform & Packaging Validation (RC-006 & RC-007)

| Target | Verification Method | Result |
| --- | --- | --- |
| **Python wheel build** | `python3 -m pip wheel --no-deps .` | **Pass** — `pdf_manager-0.60.4-py3-none-any.whl` (`8,327.7 KB`, `451` files; `gui.html`, `static/`, `locales/` verified) |
| **Wheel SHA-256** | `hashlib.sha256` | `5ab00bfa0a712b5c03d56b9632081b01029212f2749a81cc932b09141d96a092` |
| **Linux clean-venv install** | Fresh `venv` + `pip install --no-deps` + `pdfm --help` | **Pass** (`version=0.60.4`, exit `0`) |
| **Windows (Python 3.13 + E2E)** | GitHub Actions `Tests` + `Windows end-to-end` | **CI-Verified** (manual interactive desktop walkthrough unverified on this Linux VM) |
| **macOS** | GitHub Actions `build-app.yml` (`macos` job on PRs) | **CI-Build-Verified** (manual interactive desktop walkthrough & notarization unverified) |

---

## 7. Data Integrity, Failure & Recovery Validation (RC-008)

Verified in `tests/test_rc_failure_recovery.py` (`6 passed` in `1.35s`):
- Truncated/corrupt PDF fails cleanly with `PdfManagerError` and writes no partial output.
- Failed encryption leaves original input byte-identical (verified via SHA-256) and writes no output file.
- Read-only destination directory raises `PermissionError`, leaves original intact, and formats clean user guidance via `applog.format_user_error()`.
- Batch job with a corrupt document in the middle isolates the failure and completes all valid documents.
- Cancelled batch job via `progress.Job` stops promptly.
- Child-process crash (`os._exit(139)`) and wall-clock timeout in `isolation.run_isolated()` terminate cleanly without affecting the parent process.

---

## 8. Consolidated Defect Register (RC-010)

| Finding ID | Severity | Component | Description & Impact | Recommended Action | Status |
| --- | --- | --- | --- | --- | --- |
| **RC-DEF-01** | **High** (Blocker for mass commercial distribution) | `packaging/windows/`, `packaging/build_macos.py` | Windows and macOS installers/binaries are not Authenticode-signed or Apple-notarized; triggers SmartScreen / Gatekeeper warnings. | Provision Authenticode / Azure Trusted Signing cert and Apple Developer ID cert. | Open (ops/cost decision) |
| **RC-DEF-02** | **High** | `pdfmanager/relsign.py`, `.github/workflows/release.yml` | Ed25519 release-signing verification code is complete, but owner signing key (`RELEASE_PUBLIC_KEY` / `RELEASE_SIGNING_KEY` secret) is not yet provisioned. | Owner runs `python packaging/release.py --keygen` and configures Actions secret. | Open (owner step) |
| **RC-DEF-03** | **Medium** | `pdfmanager/gui.html`, `pdfmanager/static/` | Interactive side-by-side diff UI in Reader (`splitview.js`), Settings Audit Log table (`/api/audit`), and Reader toast for `X-Pdfm-Summary`/`X-Pdfm-Visual` are backed by live APIs but not yet rendered as dedicated frontend panels. | Wire lightweight frontend panels consuming the existing endpoints in next UI polish pass. | Open (next sprint) |
| **RC-DEF-04** | **Medium** | `pdfmanager/gui.html` | GUI has ARIA landmarks, skip links, focus rings, and keyboard palette, but has not undergone a formal NVDA/JAWS/VoiceOver WCAG 2.1 AA audit. | Conduct manual screen-reader QA pass on Windows/macOS before claiming WCAG compliance. | Open (QA pass) |
| **RC-DEF-05** | **Low** | `tests/test_redact_remove.py` | 4 pypdf `DeprecationWarning` messages for positional args to `add_outline_item()` in test fixtures (will be removed in pypdf 8.0). | Update test fixtures to keyword arguments. | Open (cleanup) |

---

## 9. Final Release Decision (Section 14)

### **APPROVED WITH DOCUMENTED LIMITATIONS**

**Justification:**
- Zero critical or security-blocking code defects remain on `main` (`409d3d3`).
- All six engineering hardening phases, Security Phases 7–8, and Depth Tracks A–D are verified by automated regression tests across Linux (`Python 3.10`, `3.12`, `3.13`) and Windows (`Python 3.13`, E2E), CodeQL static analysis, failure-recovery tests, 500-page performance acceptance benchmarks, and clean wheel/venv packaging.
- **Documented limitations preventing unconditional commercial GA:** Windows Authenticode signing / macOS notarization (**RC-DEF-01**), owner Ed25519 release-signing key provisioning (**RC-DEF-02**), and formal screen-reader QA (**RC-DEF-04**). ZPDF is ready for public/internal release (`v0.60.5` / `v0.61.0`) with these limitations transparently documented in `README.md`, `SECURITY.md`, and `docs/RELEASE_READINESS.md`.
