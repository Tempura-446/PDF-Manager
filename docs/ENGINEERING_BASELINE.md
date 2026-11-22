# ZPDF / PDF-Manager 0.61.1 — Engineering Baseline (Phase 0)

> Captured from unmodified release commit `92b917a8316f217567e4288a381cc7c192b47654` (`v0.61.1`) on `2026-11-22` prior to remediation changes.

---

## 1. Project Version & Environment

| Property | Value |
|---|---|
| **Project** | ZPDF / `pdf-manager` |
| **Version** | `0.61.1` (`pdfmanager/__init__.py`) |
| **Baseline Git Commit** | `92b917a8316f217567e4288a381cc7c192b47654` (`main`) |
| **License** | PolyForm Internal Use License 1.0.0 with personal-use permission (`LICENSE`) |
| **Supported Python** | `>=3.10` (`pyproject.toml`) |
| **Baseline Host OS** | `Linux 6.18.57 x86_64` (2 vCPUs) |
| **Baseline Python** | `3.12.16` |
| **Packaging Backend** | `setuptools>=69` (`setuptools.build_meta`) |

---

## 2. Source Inventory

| Area | Files | Total Lines | Notes |
|---|---:|---:|---|
| `pdfmanager/**/*.py` | 102 | 55,507 | Core library, CLI, GUI HTTP server, forms, textedit, security, OCR |
| `pdfmanager/static/` + `gui.html` | ~25 custom JS/CSS + vendored PDF.js | ~12,000 custom + PDF.js | Single-page desktop/browser UI + PDF.js viewer |
| `tests/**/*.py` | 158 | 40,718 | Unit, integration, sweep, e2e, and UI wiring tests |
| `packaging/` | 16 | 2,908 | PyInstaller app builder, Windows/macOS/Linux builders, `release.py`, `benchmark.py`, `ZPDF.admx` |
| `.github/workflows/` | 9 | 731 | `tests.yml`, `codeql.yml`, `audit.yml`, `release.yml`, `build-app.yml`, `lock.yml`, `langpacks.yml`, `sweep.yml`, `windows-e2e.yml` |
| `docs/` + root docs | 32 | 7,260 | `README.md`, `CHANGELOG.md`, `RC_VALIDATION_REPORT.md`, `RELEASE_READINESS.md`, specs/plans |

---

## 3. Architecture Overview

ZPDF is structured into six main layers:

1. **Core PDF & Document Processing**: `core.py` (2,202 LOC — open/save/merge/split/compress/encrypt/decrypt/watermark/batch), `filetools.py`, `organize.py`, `outline.py`, `crop.py`, `layout.py`, `layers.py`, `pageimages.py`, `pagephoto.py`, `viewports.py`, `pageranges.py`.
2. **Security, Redaction & Compliance**: `policy.py` (enterprise policies), `secprofiles.py` (security profiles & dynamic print watermarks), `auditlog.py` (hash-chained JSONL audit log), `isolation.py` (spawned subprocess runner), `redact.py` (1,038 LOC) & `redactcontent.py` (1,326 LOC), `risks.py` (Make Safe), `archive.py` (sanitize & PDF/A), `certsign.py` (1,195 LOC, pyHanko signing/verification), `certcrypt.py`, `protection.py`, `privacy.py`, `relsign.py` (Ed25519 release signing), `update.py`.
3. **Editing, Forms & Comparison**: `textedit/` (`extract.py`, `rewrite.py`, `reflow_ops.py`), `textflow.py`, `textlayer.py`, `forms/` (`_base.py`, `fields.py`, `data.py`, `actions.py`, `scripts.py`, `check.py`), `formdetect.py`, `compare.py`, `comments.py`, `markup.py`, `stamps.py`, `quantities.py`.
4. **OCR, Imaging, Conversion & Search**: `ocr.py` (2,004 LOC — RapidOCR/ONNX & OCRmyPDF/Tesseract), `ocrtables.py`, `imaging.py`, `scan.py`, `scanlayers.py`, `scanpdf.py`, `scanner.py`, `escl.py`, `naps2.py`, `sane.py`, `office.py`, `tables.py`, `dxf.py`, `slides.py`, `webpage.py`, `markdown_export.py`, `translate.py`, `i18n.py`, `search.py`, `meaning.py`, `pdfacheck.py`, `pdfafix.py`, `preflight.py`.
5. **Automation & Background Workers**: `background.py`, `watch.py`, `watchfolders.py`, `filing.py`, `recipes.py`, `sheetsets.py`, `quickactions.py`, `progress.py`, `tray.py`.
6. **Interfaces**:
   - **CLI**: `pdfmanager/cli.py` (2,426 LOC), entry point `pdfm = pdfmanager.cli:main`.
   - **GUI / Local HTTP API**: `pdfmanager/gui.py` (1,423 LOC) + `pdfmanager/server/` (`common.py`, `operations.py`, `reader_routes.py`, `tool_routes.py`, `form_routes.py`, `scan_watch_routes.py`), entry point `pdfm-app = pdfmanager.gui:main_app`. Frontend served from `pdfmanager/gui.html` and `pdfmanager/static/*.js`.

---

## 4. Module Dependency Overview

Static AST import analysis across all 102 Python modules in `pdfmanager/`:

- **Most depended-on internal packages/modules**: `pdfmanager` (79 dependents via `from pdfmanager import ...`), `pdfmanager.appdata` (10), `pdfmanager.pageranges` (8), `pdfmanager.server.common` (6), `pdfmanager.forms` (2), `pdfmanager.server.operations` (2).
- **Highest fan-out orchestrators**: `pdfmanager.gui` (9 internal module imports), `pdfmanager.cli` (3), `pdfmanager.recipes` (3), `pdfmanager.server.common` (3), `pdfmanager.server.tool_routes` (3), `pdfmanager.watchfolders` (3).
- **Circular-import guard**: `tests/test_imports.py` enforces acyclic top-level imports across the package. `policy.py` is intentionally kept as a leaf module (`PolicyError(ValueError)`) to prevent `core <-> policy` cycles.

---

## 5. Public APIs & Entry Points

- **Console script**: `pdfm` → `pdfmanager.cli:main`
- **GUI script**: `pdfm-app` → `pdfmanager.gui:main_app`
- **Module invocation**: `python -m pdfmanager` → `pdfmanager/__main__.py`
- **Local HTTP server**: Bound to `127.0.0.1` on an ephemeral port with per-session token (`X-Pdfm-Token`) and same-origin checks (`pdfmanager/server/common.py`, `pdfmanager/gui.py`).
- **Python library API**: `pdfmanager.core`, `pdfmanager.redact`, `pdfmanager.compare`, `pdfmanager.certsign`, `pdfmanager.secprofiles`, `pdfmanager.policy`, `pdfmanager.auditlog`, `pdfmanager.isolation`, `pdfmanager.ocr`, `pdfmanager.archive`, `pdfmanager.tables`, `pdfmanager.textedit`.

---

## 6. External Dependencies

### Core Runtime Dependencies (`pyproject.toml` & `requirements.txt`)

| Package | Spec | Baseline Installed Version | Role |
|---|---|---|---|
| `pypdf` | `>=7.0` | `7.1.1` | PDF reading/writing, encryption, annotations, outlines |
| `reportlab` | `>=4.0` | `5.1.0` | PDF overlay/watermark/form/document generation |
| `Pillow` | `>=9.0` | `13.0.0` | Image processing and rasterization |
| `numpy` | `>=1.24` | `2.5.5` | Pixel-array scan compression, visual diff, OCR preprocessing |
| `cryptography` | `>=43` | `50.0.2` | AES encryption, Ed25519 release signing, X.509 certificates |
| `pypdfium2` | `>=4.0` | `5.15.2` | PDFium page rendering |
| `pikepdf` | `>=8` | `10.18.2` | qpdf object-stream compression, low-level PDF rewriting |
| `pyhanko` | `>=0.34` | `0.38.2` | Digital signatures (CMS/PAdES) and verification |
| `pdfplumber` | `>=0.11` | `0.11.11` | Table & word extraction (`tables.py`) |
| `openpyxl` | `>=3.1` | `3.1.5` | Excel `.xlsx` export (`tables.py`) |
| `ezdxf` | `>=1.3` | `1.4.5` | CAD DXF import/export (`dxf.py`) |

### Optional Extras (Not Installed in Minimal Baseline VM)

- `app`: `pywebview>=5`, `pythonnet>=3.0` (Windows)
- `ocr`: `rapidocr>=3.10`, `onnxruntime>=1.17`
- `translate`: `ctranslate2>=4.0`, `sentencepiece>=0.2`
- `tesseract`: `ocrmypdf>=15` (plus system `tesseract`, `gs`, `soffice` in CI OCR/Office job)
- `test`: `pytest>=7` (installed: `9.2.0`)

---

## 7. Existing Test Suite

- **158 test files** (`40,718` lines total) under `tests/`.
- Covers core PDF operations, redaction removal/verification, text editing & reflow, PDF comparison, forms, digital signatures, security phases 4–8, GUI/server API routes, CLI commands, watch folders, OCR phases, PDF/A, accessibility, packaging/installer, and failure recovery.
- CI workflows run `ubuntu-latest` (Python 3.10, 3.13), `windows-latest` (Python 3.13), an OCR/PDF-A/Office Linux job (Tesseract, Ghostscript, LibreOffice), Playwright `e2e`, and CodeQL (`python`, `javascript-typescript`).

---

## 8. Existing Security Controls & Baseline Audit Gaps

### Present in `v0.61.1`
- Localhost-only HTTP server with session token (`gui.py`, `server/common.py`).
- Hash-chained JSONL audit log with `0o600` permissions, cross-process file lock, and CSV formula sanitization (`auditlog.py`).
- Safe redaction verification with `verified_clean` / `findings_detected` / `verification_incomplete` status model (`redact.py`).
- Make Safe sanitization (`risks.py`, `archive.py`) and PDF privacy temp-dir scoping (`privacy.py`).
- Ed25519 release signature verification logic (`relsign.py`, `update.py`).

### Confirmed Baseline Defects / Gaps Identified in Phase 0
1. **SEC-001 (Policy Enforcement)**: `require_make_safe_on_open`, `force_security_profile`, `fips_mode` (`check_fips_algorithm()`), and `trust_list_paths` have **zero production callers**. `disable_network` is only enforced in `update.py` — not in `ocr.py` (model downloads), `i18n.py` (translation packs), or `escl.py` (network scanner HTTP/mDNS).
2. **SEC-002 (Security Profile Bypass)**: `secprofiles.apply_profile()` calls `core.decrypt(path, current, password or "", _internal=True)` (`secprofiles.py:220`) and writes an unencrypted output when no output password is provided, bypassing `disable_password_removal`.
3. **SEC-003 (Attachment Verification)**: `redact.verify_redaction()` (`redact.py:892-918`) only performs raw byte scanning on attachments; it does not recursively inspect embedded PDFs, Office archives (`.docx`/`.xlsx`/`.pptx`), or ZIP archives with decompression/recursion limits, nor mark encrypted/unsupported attachments as `verification_incomplete`.
4. **REL-001 (Multiprocessing Deadlock)**: `isolation.run_isolated()` (`isolation.py:74-88`) calls `proc.join(timeout)` before reading `out_q`, causing a guaranteed pipe-buffer deadlock on payloads exceeding the OS pipe buffer (~64 KB on Linux), and fails to close `out_q` / `proc`.
5. **Release Signing Key**: `relsign.RELEASE_PUBLIC_KEY` (`relsign.py:26`) is empty `""`; `update.py` also lacks explicit downgrade protection and rollback-safe staging.

---

## 9. Known Architectural Limitations

- **Large monolithic modules**: `accessibility.py` (2,901 LOC), `cli.py` (2,426 LOC), `core.py` (2,202 LOC), `textedit/rewrite.py` (2,078 LOC), `ocr.py` (2,004 LOC), `server/operations.py` (1,986 LOC), `forms/fields.py` (1,714 LOC).
- **GIL-bound thread concurrency**: `core.batch_run` uses a thread pool; pure-Python `pypdf` workloads show near-1× scaling on 2 threads (see Section 11).
- **Platform coverage**: Linux and Windows are tested in CI; macOS has no CI runner and remains unverified.
- **Software FIPS filtering**: Python-level algorithm checks cannot confer FIPS 140-2/140-3 cryptographic module certification.

---

## 10. Baseline Test Results (`v0.61.1`, `92b917a`)

Command: `pytest -q --tb=short` (Linux x86_64, Python 3.12.16)

- **Full run**: `1,674 passed, 141 skipped, 1 failed` in `567.94s` (`0:09:27`).
  - The 1 failure was `tests/test_ocr_phase5.py::test_parallel_workers_faster_and_identical_to_single_worker` (`4.50s` vs `7.71s`) while a concurrent benchmark process was competing for the VM's 2 vCPUs.
  - Re-run solo on idle CPU: `pytest tests/test_ocr_phase5.py::test_parallel_workers_faster_and_identical_to_single_worker -q` → **1 passed in 16.65s**.
- **Skipped tests (141)**: Environment-dependent tests requiring Windows-only APIs, RapidOCR/ONNX, CTranslate2/SentencePiece, OCRmyPDF/Tesseract, LibreOffice, or Playwright browser binaries not installed in the minimal VM.
- **Warnings (10)**:
  - 3 × `pikepdf` `UserWarning: A password was provided, but no password was needed to open this PDF.`
  - 7 × `Pillow` `DeprecationWarning: Image.Image.getdata is deprecated and will be removed in Pillow 14` in `tests/test_construction_markups.py:54`.

---

## 11. Baseline Performance Results (`v0.61.1`, `92b917a`)

Command: `python3 packaging/benchmark.py --pages 10 --repeats 1 --json` (Linux 6.18.57 x86_64, 2 vCPUs, Python 3.12.16)

| Workload | Pages | Median (s) | Throughput (pages/s) | Peak Python (MB) | RSS (MB) | Output (KB) |
|---|---:|---:|---:|---:|---:|---:|
| `compare` | 10 | 2.3466 | 4.26 | 11.20 | 85.25 | 213.1 |
| `compare_scanned` | 10 | 0.6769 | 14.77 | 4.50 | 85.25 | 20.0 |
| `redact_remove` | 10 | 3.8631 | 2.59 | 3.26 | 85.25 | 7.9 |
| `redact_image(150dpi)` | 10 | 1.1518 | 8.68 | 19.08 | 109.68 | 967.1 |
| `textedit` | 10 | 5.4560 | 1.83 | 3.89 | 109.68 | 15.1 |
| `batch(8x10p,w=1)` | 80 | 28.3202 | 2.82 | 2.64 | 109.68 | 7.9 |
| `batch(8x10p,w=2)` | 80 | 27.4913 | 2.91 | 2.85 | 109.68 | 7.9 |
