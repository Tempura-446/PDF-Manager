# P4 — Backend Modularization (Wave 2) Design Spec

**Date:** 2026-11-24  
**Target Release:** `v0.65.0`  
**Status:** Proposed (awaiting sign-off)

---

## 1. Goal
Break down ZPDF's six remaining >2,000-LOC backend monoliths (~14,534 LOC combined) into cohesive, domain-focused subpackages matching the Wave 1 pattern (`pdfmanager/forms/`, `pdfmanager/textedit/`, `pdfmanager/server/`), and replace the 44 silent `except Exception: pass` sites across `pdfmanager/` with typed error handling and `applog` diagnostics.

| Monolith | Current LOC | Test Monkeypatch Risk | Proposed Package |
| --- | ---: | --- | --- |
| `pdfmanager/accessibility.py` | 2,901 | None (0 hits) | `pdfmanager/accessibility/` |
| `pdfmanager/translate.py` | 2,658 | Low (4 hits) | `pdfmanager/translate/` |
| `pdfmanager/cli.py` | 2,426 | None (0 hits) | `pdfmanager/cli/` |
| `pdfmanager/core.py` | 2,276 | Medium (18 hits) | `pdfmanager/core/` |
| `pdfmanager/ocr.py` | 2,148 | High (69 hits) | `pdfmanager/ocr/` |
| `pdfmanager/server/operations.py` | 2,125 | Low (2 via `gui`) | `pdfmanager/server/operations/` |

---

## 2. Architecture & Design Decisions

### Pattern: Package + `__init__.py` Public Facade
Each monolith `<name>.py` becomes a package `<name>/` whose `__init__.py` re-exports the full public (and test-used) API, exactly like Wave 1's `pdfmanager/forms/` and `pdfmanager/textedit/`.

- **Zero behavior changes**: Pure structural refactor; no existing test assertions relaxed.
- **Monkeypatch preservation**:
  - In `translate/`, `core/`, and `ocr/`, internal cross-submodule calls to symbols that tests monkeypatch (e.g., `translate._load_offline_translator`, `core._pages_bytes`, `core.MAX_RENDER_PIXELS`, `ocr._read`, `ocr.recognise`, `ocr._engine`, `ocr._fetch`, etc.) resolve through `sys.modules["pdfmanager.<pkg>"]` so `monkeypatch.setattr(pkg, "symbol", ...)` continues to work identically.
- **Lazy startup preservation**:
  - Keep `import pdfmanager.gui` cold startup fast (~160 ms); no new eager imports of `pypdf`, `cryptography`, `PIL`, `numpy`, or `pikepdf` at import time.
- **Import graph hygiene (`tests/test_imports.py`)**:
  - Extend `tests/test_imports.py` to discover both top-level `.py` modules and subpackages so `test_every_module_imports_on_its_own` and `two_way_pairs()` guard the new packages too.
- **License headers (`tests/test_headers.py`)**:
  - Every new `.py` file starts with the PolyForm Internal Use License 1.0.0 header.
- **Packaging**:
  - `pyproject.toml` (`[tool.setuptools.packages.find]`) and `packaging/build_app.py` (`--collect-submodules pdfmanager`) automatically bundle all subpackages.

### Silent `except Exception: pass` Remediation (44 sites)
Classify each of the 44 sites into one of three principled categories:
1. **Expected best-effort cleanup / optional platform quirk** (e.g. `doc.close()`, `pool.shutdown()`, `AllowSetForegroundWindow`, `window.on_top`, `arabic_reshaper`, `_os_prefers_dark`): narrow exception types where safe (`OSError`, `AttributeError`, `ImportError`) or log at `applog.debug(...)` with context.
2. **Damaged/unusual PDF fallback** (e.g. XMP metadata, ToUnicode CMap, `/Pages /Count`, link rect, thumbnail preview, auto-mend): log via `applog.debug("...: %s", exc)` so failures remain non-fatal to users while visible in diagnostics logs.
3. **Runtime / callback errors** (e.g. scanner enumeration, filing OCR fallback, tray notification callback): log via `applog.warning(...)` or `applog.debug(...)` with exception details.

Zero bare silent `except Exception: pass` blocks remain across `pdfmanager/`.

---

## 3. Non-Goals
- No new user-facing features or CLI flag changes.
- No behavioral changes to PDF output, OCR accuracy, or translation quality.
- No changes to external dependencies.
