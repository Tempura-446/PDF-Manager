# ZPDF / PDF-Manager — Security Remediation Report

> **Baseline**: `v0.61.1` (`92b917a`) → **Remediated**: `447a664` (`main`)

---

## 1. SEC-001 — Administrator Policy Enforcement Matrix

| Policy Name | Configuration Source | Enforcement Function(s) | Production Consumers | Remaining Bypass Risks | Regression Tests | Final Status |
|---|---|---|---|---|---|---|
| `disable_network` | HKLM → HKCU → `$ZPDF_POLICY_FILE` / `/etc/zpdf/policy.json` | `policy.check_network_allowed()`, `policy.check_updates_allowed()` | `update._open()`, `update.check()`, `update.install()`, `ocr._fetch()`, `i18n._http_get_bytes()`, `escl._request()`, `certsign.sign(timestamp_url=...)`, `settings.js` | External optional binaries (e.g. LibreOffice/Tesseract if invoked with remote URLs) are outside Python urllib; loopback `127.0.0.1` GUI IPC is intentionally exempt. | `test_sec001_disable_network_blocks_all_network_consumers` | **Enforced** |
| `disable_updates` | HKLM → HKCU → JSON policy file | `policy.check_updates_allowed()` | `update.check()`, `update._open()`, `update.latest_version()`, `update.install()`, `settings.js` | None within ZPDF; `offline_update_dir` remains read-only local discovery. | `test_sec001_disable_updates_blocks_check_and_install` | **Enforced** |
| `disable_password_removal` | HKLM → HKCU → JSON policy file | `policy.check_password_removal_allowed()` | `core.decrypt()` (unconditional, `_internal=True` closed), `secprofiles.apply_profile()` (Python, CLI `profile apply`, GUI/API) | None via public or internal decrypt APIs; re-encrypting an encrypted PDF via `secprofiles` is permitted only when a new output password is supplied and enforced atomically. | `test_sec002_*` (5 tests) | **Enforced** |
| `require_make_safe_on_open` | HKLM → HKCU → JSON policy file | `policy.requires_make_safe_on_open()` | `ReaderRoutesMixin._open_file()` (`/api/file/<key>` fails closed if Make Safe fails; emits `X-Pdfm-Made-Safe: 1`), `secprofiles.apply_profile()` (forces `make_safe=True`) | Direct read-only library calls (`core.open_pdf`) cannot mutate files in place; enforced at viewer open and profile boundaries. | `test_sec001_require_make_safe_on_open_enforced_in_secprofile`, `test_api001_require_make_safe_on_open_header` | **Enforced** |
| `force_security_profile` | HKLM → HKCU → JSON policy file | `policy.forced_security_profile()`, `policy.check_security_profile_allowed()` | `secprofiles.apply_profile()` (blocks non-matching profile names and ad-hoc dicts; auto-applies forced profile when `profile=None`), CLI `profile apply` | Only applies when invoking Security Profiles; does not intercept non-profile tools (e.g. `merge`). | `test_sec001_force_security_profile_enforced` | **Enforced** |
| `fips_mode` | HKLM → HKCU → JSON policy file | `policy.check_fips_algorithm()` | `core.encrypt()`, `certsign.sign()`, `certsign.verify()`, `certsign.verify_detached()` | **Important**: Enforces software algorithm filtering (rejects RC4, MD5, SHA-1, DES, 3DES, AES-128-ECB). Does **not** constitute FIPS 140-2/140-3 validated cryptographic module certification. | `test_sec001_fips_mode_algorithm_checks`, `test_fips_mode_refuses_rc4_and_weak_algorithms` | **Enforced (Algorithm Policy Only)** |
| `trust_list_paths` | HKLM → HKCU → JSON policy file | `policy.validated_trust_list_paths()` | `certsign.verify()`, `certsign.verify_detached()` | Rejects relative paths, `..` segments, missing paths, and escaping symlinks; fails safely by skipping invalid entries; cannot be overridden by document content. | `test_sec001_trust_list_paths_validation_and_certsign_merge` | **Enforced** |

---

## 2. SEC-002 — Security Profile Password-Removal Bypass

- **Previous vulnerability**: `secprofiles.apply_profile()` decrypted encrypted inputs using `core.decrypt(path, current, password or "", _internal=True)` and moved the decrypted file to `output` whenever the profile had no output password (e.g., `"Public Share"`), bypassing `disable_password_removal`.
- **Remediation**:
  1. `core.decrypt()` now enforces `policy.check_password_removal_allowed()` **unconditionally** — passing `_internal=True` no longer bypasses policy.
  2. `secprofiles.apply_profile()` checks `input_encrypted` and `will_encrypt` *before* creating any staging files. If `input_encrypted and not will_encrypt` and `disable_password_removal` is active, it immediately raises `core.PdfManagerError` and writes nothing to `output`.
  3. When `input_encrypted and will_encrypt`, staging decryption uses private helper `core._decrypt_unprotected()` inside a `0o700` `TemporaryDirectory`, followed by guaranteed `core.encrypt()` and atomic `mkstemp` + `os.replace` to `output`.

---

## 3. SEC-003 & SEC-004 — Redaction Verification & Embedded Attachments

- **Remediation in `pdfmanager/redact.py`**:
  - Added `_inspect_attachment_bytes()` and `_read_zip_member_capped()` for format-aware recursive inspection of embedded PDFs, Office Open XML documents (`.docx`, `.xlsx`, `.pptx`, `.odt`, `.ods`, `.odp`), ZIP archives, and plain/structured text files.
  - Enforced resource limits: `MAX_EMBED_DEPTH = 3`, `MAX_ARCHIVE_ENTRIES = 200`, `MAX_ARCHIVE_DECOMPRESSED = 10 MB` (capped by `DEEP_STREAM`), and `MAX_COMPRESSION_RATIO = 100` (streaming check against spoofed ZIP headers / archive bombs).
  - Encrypted embedded PDFs, password-protected ZIP entries, corrupt archives, oversized attachments, and unsupported binary formats are explicitly recorded in `attachments_skipped` and `inspection_errors`, ensuring `status == "verification_incomplete"` (`clean == False`).
- **Documented verification limits**: Text baked into raster images inside attachments (e.g., PNGs inside a `.docx`) is not OCR'd during fast verification; PDFs with untrusted attachments should still be run through **Remove hidden data** (`archive.sanitize`) when distributing externally.

---

## 4. SEC-005 & API-001 — Audit Log Hygiene & HTTP Server Hardening

- **`pdfmanager/auditlog.py` (`SEC-005`)**: `_clean_details()` now recursively redacts any detail key whose lowercase name contains `password`, `passwd`, `passphrase`, `secret`, `token`, `private_key`, `api_key`, `authorization`, `cookie`, or `key_pem` to `"[redacted]"`.
- **`pdfmanager/gui.py` & `pdfmanager/server/common.py` (`API-001`)**: Validated `Content-Length` as a non-negative integer before reading request bodies, returning HTTP 400 on malformed or negative values so `rfile.read(-1)` cannot bypass `MAX_UPLOAD`.

---

## 5. Phase 6 — Release Signing & Update Hardening (`relsign.py`, `update.py`)

- **Implemented**:
  - `update._open()` and `update.install()` enforce `policy.check_updates_allowed()`.
  - `update.install()` refuses version downgrades (`parse_version(version) < parse_version(__version__)`) unless `allow_downgrade=True`.
  - `update._extract()` rejects corrupt zips, Unix symlinks (`S_IFLNK`), drive/absolute/`..` traversal paths, > 5,000 members, and > 200 MB decompressed size.
  - `relsign.verify_release(..., require_signature=True)` fails closed when `RELEASE_PUBLIC_KEY` is empty.
- **Remaining Owner Action (Required for Signed Auto-Updates)**:
  1. Run `python packaging/release.py --keygen` on an offline owner machine.
  2. Store the private PEM in the repository's `RELEASE_SIGNING_KEY` GitHub Actions secret (never commit it).
  3. Embed the printed public PEM into `RELEASE_PUBLIC_KEY` in `pdfmanager/relsign.py` and switch `update.py` to `require_signature=True`.
