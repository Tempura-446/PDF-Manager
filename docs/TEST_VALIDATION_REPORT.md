# ZPDF / PDF-Manager — Test Validation Report (Phase 9 / Stage G)

> **Baseline commit**: `92b917a` (`v0.61.1`)
> **Validated post-remediation commit**: `447a664` (`main`, after PRs #145–#148)

---

## 1. Local Baseline Full Suite (`v0.61.1`, `92b917a`)

- **Command**: `pytest -q --tb=short` (Linux 6.18.57 x86_64, Python 3.12.16)
- **Result**: `1,674 passed, 141 skipped, 1 failed` in `567.94s`
  - Single timing failure (`tests/test_ocr_phase5.py::test_parallel_workers_faster_and_identical_to_single_worker`) occurred during CPU contention with a concurrent benchmark; re-run solo on idle CPU → **1 passed in `16.65s`** (**1,675/1,675 runnable tests passing**).
- **Skipped (141)**: Platform/optional-extra tests requiring Windows APIs, RapidOCR/ONNX, CTranslate2/SentencePiece, OCRmyPDF/Tesseract, LibreOffice, or Playwright browsers not installed in the minimal VM.

---

## 2. New Remediation Regression Suites (Post-Merge `447a664`)

Command: `pytest -q tests/test_stage_b_security.py tests/test_stage_c_reliability.py tests/test_stage_def_audit.py`

- **Result**: **`41 passed, 0 failed, 0 skipped`** in `9.43s`.

| Test Module | Tests | Coverage | Status |
|---|---:|---|---|
| `tests/test_stage_b_security.py` | 18 | SEC-001 (all 7 policies + trust-list symlink/traversal + certsign merge), SEC-002 (5 scenarios incl. CLI & direct `_internal=True`), SEC-003 (embedded PDF, encrypted PDF, `.docx`, zip bomb, unsupported binary), Phase 6 (`relsign` `require_signature`, `update._extract` symlinks/corrupt zips, downgrade protection) | **Passed (18/18)** |
| `tests/test_stage_c_reliability.py` | 17 | REL-001 (small & 4 MB payloads, 200 KB sanitized exception, crash exit code, timeout, parent cancellation, repeated cleanup), REL-002 (atomic `_write` rollback), REL-003 (10 hostile filenames + symlink escape + aggregate byte cap) | **Passed (17/17)** |
| `tests/test_stage_def_audit.py` | 6 | API-001 (malformed/negative `Content-Length` HTTP 400, `require_make_safe_on_open` `X-Pdfm-Made-Safe: 1`), SEC-005 (recursive sensitive-key redaction in `auditlog`), PERF-001 (`_looks_grey` NumPy accuracy), TXT-001 (ordered text edge cases), CMP-003 (geometry/orientation change) | **Passed (6/6)** |

---

## 3. Multi-Platform GitHub Actions CI Verification

Every remediation PR cleared full GitHub Actions CI prior to squash-merge into `main`:

| PR | Stage | `ubuntu-latest` Py 3.10 | `ubuntu-latest` Py 3.13 | `windows-latest` Py 3.13 | `OCR / PDF-A / Office` | `e2e` (Playwright) | `linux` / `windows` / `macos` audit | `CodeQL` (Py & JS) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| #145 | Stage A (Baseline docs) | n/a (docs-only) | n/a | n/a | n/a | n/a | n/a | n/a |
| #146 | Stage B (Security) | **PASS** (7m 32s) | **PASS** (7m 26s) | **PASS** (8m 54s) | **PASS** (3m 35s) | **PASS** (2m 13s) | **PASS** / **PASS** / **PASS** | **PASS** |
| #147 | Stage C (Reliability) | **PASS** (7m 31s) | **PASS** (7m 11s) | **PASS** (9m 21s) | **PASS** (2m 52s) | **PASS** (2m 09s) | n/a | **PASS** |
| #148 | Stages D–F (Audit) | **PASS** (6m 41s) | **PASS** (6m 48s) | **PASS** (8m 56s) | **PASS** (3m 49s) | **PASS** (2m 11s) | **PASS** / **PASS** / **PASS** | **PASS** |

- **Note on `Windows PC / tests`**: Self-hosted hardware runner skipped automatically in cloud PRs (normal repository configuration).
- **Note on macOS**: The `audit.yml` `macos` job passed on PR #146 and PR #148; full macOS interactive GUI/app bundle runtime testing was not executed locally.
