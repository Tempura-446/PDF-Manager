# ZPDF Roadmap

Master index of ZPDF's completed, in-progress, and planned feature tracks. Each track links to its detailed plan in [`docs/superpowers/plans/`](superpowers/plans/).

*Last updated: 2026-11-24 (after `v0.63.2` Commercial-Safe Translation Stack and `v0.64.0` Speed, Polish & Full Coverage)*

---

## Completed / Shipped

| Track | Status | Plan / Reference |
| --- | --- | --- |
| Built-in OCR engine | Done | [`2026-11-09-builtin-ocr-engine.md`](superpowers/plans/2026-11-09-builtin-ocr-engine.md) |
| Scan compression engine | Done | [`2026-11-10-scan-compression-engine.md`](superpowers/plans/2026-11-10-scan-compression-engine.md) |
| Native (C++) page rendering | Concluded (measured; not pursued) | [`2026-11-14-native-rendering.md`](superpowers/plans/2026-11-14-native-rendering.md) |
| Interactive form authoring | Shipped in `0.57.0` (Phases 1–4; optional QR barcode field deferred) | [`2026-11-14-form-authoring.md`](superpowers/plans/2026-11-14-form-authoring.md) |
| Drawing tools (CAD-adjacent) | Shipped in `0.57.0` (Phases 1–5; minor Phase 2/2b polish items remain) | [`2026-11-13-drawing-tools.md`](superpowers/plans/2026-11-13-drawing-tools.md) |
| Multi-line paragraph reflow | Shipped in `0.57.0` (Phases 1–5; minor editor-box polish items remain) | [`2026-11-13-paragraph-reflow.md`](superpowers/plans/2026-11-13-paragraph-reflow.md) |
| Markdown & Review Exports | Done (PRs #93, #94) | [`2026-11-18-markdown-and-exports.md`](superpowers/plans/2026-11-18-markdown-and-exports.md) |
| Better OCR (Phases 1–5) | Done (hard pages, Check OCR UI, tables/forms, speed) | [`2026-11-14-ocr-improvements.md`](superpowers/plans/2026-11-14-ocr-improvements.md) |
| Architecture Modularization (Phases 1–3) | Done (split `gui.py`, `gui.html`, `reader.js`, `textedit.py`, `forms.py`) | [`2026-11-18-architecture-modularization.md`](superpowers/plans/2026-11-18-architecture-modularization.md) |
| Reader Productivity & Print Preflight (Phases 1–3) | Done (split view, visual crop, N-up/poster, print preflight) | [`2026-11-18-reader-productivity.md`](superpowers/plans/2026-11-18-reader-productivity.md) |
| Smart Auto-Rename & Filing Rules (Phases 1–2) | Done (rule-based renaming & routing in Organize & Watch folders) | [`2026-11-18-smart-filing-rules.md`](superpowers/plans/2026-11-18-smart-filing-rules.md) |
| macOS & Linux Builds (Phases 1–3) | Done (`.dmg`/`.zip`, `.deb`/`.zip`, unified 3-OS updater) | [`2026-11-15-macos-linux-builds.md`](superpowers/plans/2026-11-15-macos-linux-builds.md) |
| Hybrid offline translation packs | Shipped in `0.60.3` (bundled `ctranslate2` + `sentencepiece`, hybrid `ZPDF-releases` `langpacks-v1` + Argos catalog) | PR #126, PR #127 |
| Engineering Hardening Phases 1–6 | Shipped in `0.60.4`–`0.61.0` (Redaction security, text editing integrity, compare engine, security/reliability, perf benchmarks, release readiness) | PRs #128–#133, [`RELEASE_READINESS.md`](RELEASE_READINESS.md) |
| Security Phases 5–8 | Shipped in `0.61.0`–`0.61.8` (PAdES/DocMDP/FieldMDP, security profiles & audit log, GPO/ADMX, FIPS, offline updates, process isolation; Authenticode/MSI gated on code-signing) | PR #135, [`2026-11-14-security.md`](superpowers/plans/2026-11-14-security.md) |
| Smart AI + Advanced Offline PDF Translation | Shipped in `0.61.9`–`0.63.0` (Smart AI OpenAI-compatible engine + Test connection, smarter offline chunking/glossary/CJK polish, live download progress/cancel, offline language auto-detect, code/math protection, bold lead-in, rotated text, outline bookmark translation, RTL shaping, page ranges, auto-OCR, CUDA GPU, bilingual stacked mode) | PRs #169–#176 |
| Post-v0.63.0 Audit Remediation (P0–P3 Core) | Shipped in `0.63.1` (fixed `/api/preflight/fix`, `/api/audit`, `/api/policy`, `do_GET` error handling; Web-to-PDF policy/SSRF/local-path guards; `/api/translate/test` key guard; fail-closed pack SHA-256, redirect & zip-bomb/symlink guards; `translate_pdf` in-place image preservation & cancel propagation; OCR Pass 1 72-DPI preview hashing; `_CT2_CACHE` 4 GB byte cap + single-flight lock; upload buffer release; `make_safe_on_open` LRU cache; per-page `PDFIUM_LOCK`; bounded job concurrency; 192 MB Reader undo cap; `auto_ocr`/`monochrome` checkbox fix; `show_if` array fix; `static/i18n.js` fix; Settings Save unification; Watch polling focus fix) | `v0.63.1` (PR #177) |
| Commercial-Safe Offline Translation Stack | Shipped in `0.63.2` (replaced `CC-BY-NC-4.0` NLLB-200 with `Apache-2.0` Google MADLAD-400 3B/7B/10.7B tiers + optional `CC-BY-4.0`/`Apache-2.0` Helsinki-NLP OPUS-MT fast pairs + third-party license notices) | `v0.63.2` (PRs #178–#179) |
| Speed, Polish & Full Coverage (P1–P3) | Ready for `0.64.0` (live HTTP integration tests for all `/api/form/*`, `/api/recent/restore`, `/api/comments/markdown`, `/api/quantities`, `/api/password/strength` routes; fixed `_form_import` `_safe_name` import and `/api/filing/preview` filename/response parsing; lazy-imported heavy backend modules in `gui.py` & `server/*`; per-process OCR `PdfDocument` cache; virtualized `pages.js` grid for 120+ page PDFs; OS dark-mode desktop startup background color; expanded `data-i18n` & 5-locale catalog parity; Smart Filing Rules rule viewer/editor; Reader ARIA arrow-key navigation for menus & tabs; Reader File Panel 1-click missing-pack download recovery) | `v0.64.0` |

---

## Active & Planned Tracks

| Priority | Track | Scope & Key Items | Target |
| --- | --- | --- | --- |
| **P4** | **Backend Modularization (Wave 2)** | Break down remaining >2,000-LOC monoliths (`accessibility.py`, `cli.py`, `translate.py`, `core.py`, `ocr.py`, `server/operations.py`) and replace silent `except Exception: pass` sites with typed handling + `applog` diagnostics. | `v0.65.0` |

---

## Deferred / Externally Gated
- **Code-signing-gated Windows items**: Authenticode signing, MSI enterprise installer, and Explorer thumbnail COM DLL (`Closer to Windows — Phase 4`, [`2026-11-15-windows-integration.md`](superpowers/plans/2026-11-15-windows-integration.md)) — paused unless a code-signing certificate is adopted.
- **Security Phase 5 owner key setup**: Code complete; release-signing key setup optional.
- **Small Maintenance / Polish Tail**:
  - Close out remaining minor visual polish notes from paragraph reflow and drawing markups as those files are touched.
  - See [`docs/RELEASE_READINESS.md`](RELEASE_READINESS.md) for the release-readiness classification and competitive matrix.
