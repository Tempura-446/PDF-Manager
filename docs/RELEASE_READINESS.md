# ZPDF Phase 6 — Competitive Analysis & Release-Readiness Assessment

*Last updated: 2026-11-22 (ZPDF `0.60.3`, after Phases 1–6 engineering hardening)*

This document summarizes ZPDF's verified capabilities against major PDF editors, its packaging and dependency-license posture, honest release-readiness classification, and evidence-backed product differentiators.

---

## 1. Competitive Feature Comparison Matrix (Q-001)

Capability statuses for ZPDF are verified directly against the source tree (`pdfmanager/`). Competitor columns reflect documented, publicly available product capabilities (marked *Not verified* where version-dependent behavior was not directly tested).

| Feature | ZPDF (`0.60.3`) | Adobe Acrobat Pro | Foxit PDF Editor | PDF24 | Stirling PDF | PDFgear | ZPDF Gap | User Impact | Complexity | Priority |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **PDF viewing & navigation** | Fully implemented (tabbed `pdf.js` reader, split view w/ sync scroll, bookmarks, thumbnails, dark mode) | Fully implemented | Fully implemented | Partially implemented (basic viewer) | Partially implemented (web viewer) | Fully implemented | Minor (no 3D/multimedia playback) | Low | Medium | Low |
| **Text editing & reflow** | Implemented with limitations (line & paragraph reflow, `require_permanent`, `verify_visual`) | Fully implemented | Fully implemented | Missing / limited | Missing | Partially implemented | Complex multi-column / exotic font subsetting still falls back or warns | Medium | High | Medium |
| **Image editing** | Partially implemented (extract, downsample, scan-layer compression, crop, insert) | Fully implemented | Fully implemented | Partially implemented | Partially implemented | Partially implemented | No in-place raster brush/pixel editor | Low | High | Low |
| **Page organization** | Fully implemented (reorder, extract, delete, insert blank, rotate, reverse, crop, auto-trim, booklet, n-up) | Fully implemented | Fully implemented | Fully implemented | Fully implemented | Fully implemented | None | — | — | — |
| **Merging & splitting** | Fully implemented (merge, interleave, split by range/size/bookmark) | Fully implemented | Fully implemented | Fully implemented | Fully implemented | Fully implemented | None | — | — | — |
| **OCR** | Fully implemented (built-in PP-OCR/ONNX + optional Tesseract/OCRmyPDF, auto-deskew, tables, Check OCR UI) | Fully implemented | Fully implemented | Implemented (Tesseract) | Implemented (Tesseract) | Partially implemented | Built-in language packs require 1-time download for non-Latin/CJK scripts | Low | Low | Low |
| **Search & indexing** | Fully implemented (in-reader search + local folder full-text & semantic search index) | Fully implemented | Fully implemented | Missing | Missing | Partially implemented | Semantic ranking currently English-focused (Chinese word-matched) | Low | Medium | Low |
| **Permanent redaction** | Fully implemented (content-stream & Form XObject removal, coordinate scrubbing, streaming raster fallback, post-write verification) | Fully implemented | Fully implemented | Implemented with limitations (raster) | Implemented with limitations | Partially implemented | Raster fallback required for text inside embedded images | Low | High | Low |
| **Digital signatures** | Fully implemented (`pyhanko` PFX/P12 signing, DocMDP/FieldMDP, TSA timestamps, verification, Windows cert store) | Fully implemented | Fully implemented | Partially implemented | Partially implemented | Partially implemented | Hardware HSM/PKCS#11 tokens not yet integrated | Medium | High | Medium |
| **Encryption & protection** | Fully implemented (AES-256/128, owner permissions, certificate encryption `forpeople.py`, password retention) | Fully implemented | Fully implemented | Fully implemented | Fully implemented | Fully implemented | None | — | — | — |
| **PDF comparison** | Fully implemented (multi-signal digital+scanned alignment, confidence scores, aspect-preserving diff, streaming memory) | Fully implemented | Fully implemented | Partially implemented | Partially implemented | Missing / limited | No side-by-side interactive diff viewer inside Reader (outputs annotated diff PDF) | Medium | Medium | Medium |
| **Interactive forms** | Fully implemented (auto-detect fields, authoring, calculations, FDF/XFDF data import/export, flatten) | Fully implemented | Fully implemented | Partially implemented | Missing | Partially implemented | No XFA dynamic forms or barcode fields | Low | High | Low |
| **Annotations & markups** | Fully implemented (highlights, notes, stamps, CAD-adjacent drawing & measuring tools, comment export) | Fully implemented | Fully implemented | Partially implemented | Partially implemented | Fully implemented | No real-time cloud co-authoring | Low | High | Low |
| **Attachments** | Fully implemented (extract with path-traversal/symlink hardening, inspect) | Fully implemented | Fully implemented | Partially implemented | Partially implemented | Partially implemented | Adding/editing embedded attachments via GUI is limited | Low | Low | Low |
| **Batch processing & automation** | Fully implemented (CLI, multi-file GUI, saved Recipes, Watch folders, Smart filing rules, `core.batch_run`) | Fully implemented (Action Wizard) | Fully implemented | Partially implemented | Fully implemented (API/pipelines) | Partially implemented | `batch_run` uses threads (GIL-bound for pure-Python ops; `ocr.py` uses multiprocessing) | Low | Medium | Low |
| **Document conversion** | Fully implemented (PDF ↔ Word/Excel/PPTX/HTML/Markdown/Images/DXF, PDF/A convert & check, offline PDF translation) | Fully implemented | Fully implemented | Fully implemented | Fully implemented | Fully implemented | Office → PDF relies on local Word or LibreOffice | Low | High | Low |
| **PDF Accessibility (PDF/UA)** | Fully implemented (auto-tagging + Acrobat-like accessibility checker in `accessibility.py`) | Fully implemented | Fully implemented | Missing | Missing | Missing | App GUI itself has ARIA/keyboard support but has not undergone formal WCAG 2.1 AA screen-reader audit | Medium | Medium | Medium |
| **Collaboration (cloud)** | Not applicable (local-first / offline by design) | Fully implemented | Fully implemented | Missing | Self-hosted web | Limited | Intentional privacy design | — | — | — |
| **Platform compatibility** | Windows (installer + tray + Explorer), Linux (AppImage/`.deb`), macOS (`.app`/`.dmg`), Python 3.10+ | Win / macOS | Win / macOS | Win / Web | Docker / Web | Win / macOS / iOS | Windows & macOS binaries not yet code-signed / notarized | **High** | Low (ops/cert) | **Release Blocker** |

---

## 2. Packaging, Dependencies & Licensing Audit (Q-008)

- **Project license:** PolyForm Internal Use License 1.0.0 with a personal-use permission (`LICENSE`). Source is public for inspection and internal/personal use, not OSI open source. Do not change without explicit owner authorization.
- **Core dependencies (`pyproject.toml` & `requirements.txt`):**
  `pypdf>=7.0`, `reportlab>=4.0`, `Pillow>=9.0`, `numpy>=1.24`, `cryptography>=43`, `pypdfium2>=4.0`, `pikepdf>=8`, `pyhanko>=0.34`, `pdfplumber>=0.11`, `openpyxl>=3.1`, `ezdxf>=1.3`.
- **Optional extras:**
  - `app`: `pywebview>=5`, `pythonnet>=3.0` (Windows)
  - `ocr`: `rapidocr>=3.10`, `onnxruntime>=1.17`
  - `translate`: `ctranslate2>=4.0`, `sentencepiece>=0.2`
  - `tesseract`: `ocrmypdf>=15`
- **Third-party notices (`pdfmanager/notices.py`):** Dynamically collects installed package licenses across core + `("app", "ocr", "translate")` extras plus bundled `pdf.js`, fonts, and glyphless OCR font notices.
- **Supported Python versions:** Python 3.10+ (tested on 3.10 and 3.13 in CI on Ubuntu and Windows).

---

## 3. Accessibility Scope & Honest Limitations (Q-005)

**Implemented in the GUI (`pdfmanager/gui.html`, `pdfmanager/static/app.css`):**
- Skip-to-main-content link (`#skip-link`), semantic landmarks (`<nav>`, `<main>`, `<dialog>`).
- ARIA live regions (`role="alert"`, `role="status"`, `aria-live="polite"`) and progress bars (`role="progressbar"`).
- Keyboard command palette (`Ctrl+K` / `/`), keyboard shortcuts help dialog (`#keys-dialog`), and high-contrast `:focus-visible` indicators.
- Automatic `prefers-color-scheme: dark` and `prefers-reduced-motion: reduce` support.

**Documented limitations:**
- ZPDF has **not** undergone a formal third-party WCAG 2.1 AA audit with NVDA/JAWS/VoiceOver.
- Canvas-based interactive tools inside the Reader (freehand drawing, visual crop box dragging, interactive form-field placement) remain pointer-centric; numeric/CLI equivalents exist for crop, redact, rotate, and text edit.

---

## 4. Release-Readiness Classification (Section 14)

| Severity | Item | Status / Notes |
| --- | --- | --- |
| **Release Blocker** (for public mass distribution) | Windows Authenticode code signing & macOS Developer ID notarization | Unsigned installers trigger SmartScreen / Gatekeeper warnings; documented in `README.md` and `SECURITY.md`. |
| **High Priority** | Owner release-signing key setup for `relsign.py` | Verification code is complete; requires owner Ed25519 key provisioning in GitHub Actions secrets. |
| **Medium Priority** | Interactive side-by-side comparison viewer in Reader GUI | Currently outputs a diff PDF; side-by-side split-view integration would improve review UX. |
| **Medium Priority** | Formal screen-reader pass (NVDA / VoiceOver) on dialogs and Reader toolbars | ARIA markup and keyboard navigation exist; real assistive-tech QA pass still needed before claiming WCAG compliance. |
| **Low Priority** | PKCS#11 / hardware token signing support in `certsign.py` | PFX/P12 and Windows certificate store cover most users; smart-card tokens remain a future enterprise need. |
| **Future Enhancement** | Windows Explorer thumbnail shell extension COM DLL | Deferred pending code-signing certificate. |

---

## 5. Evidence-Backed Differentiators & Post-Release Roadmap (Q-009)

1. **Verifiable Offline-First Privacy & Permanent Redaction:** Unlike cloud-backed tools, every core workflow (OCR, semantic folder search, offline neural PDF translation, permanent content-stream redaction with post-write verification) runs 100% locally with zero telemetry.
2. **Technical Drawing & CAD Workflows (`dxf.py`, `measuring.py`, `quantities.py`, `sheetsets.py`):** Native PDF ↔ DXF conversion, calibrated measuring/takeoff tools, and sheet-set handling differentiate ZPDF strongly from consumer editors (PDF24, PDFgear, Stirling PDF) at zero subscription cost.
3. **Automation Parity (GUI + CLI + Recipes + Watch Folders + Smart Filing):** The same deterministic engine powers the desktop app, `pdfm` CLI, multi-step Recipes, and background Watch folders.

### Prioritized Post-Release Roadmap
1. **Provision code-signing / notarization + `relsign` owner keys** (removes OS trust warnings and completes supply-chain signing).
2. **Interactive diff mode in Reader split view** (leverages existing `splitview.js` + Phase 3 `compare.py` confidence/alignment data).
3. **Formal assistive-tech QA pass** (NVDA on Windows, VoiceOver on macOS) and keyboard alternatives for canvas crop/redaction boxes.
4. **Multiprocessing option for CPU-bound batch operations** (extend `core.batch_run` with optional process-pool mode for pure-Python workloads like `redact_remove`).
