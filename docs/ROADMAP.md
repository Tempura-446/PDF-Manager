# ZPDF Roadmap

Master index of ZPDF's completed, in-progress, and planned feature tracks. Each track links to its detailed plan in [`docs/superpowers/plans/`](superpowers/plans/).

*Last updated: 2026-11-24 (after `v0.66.0` Interactive Diff, Live Bilingual Reading, Attachments GUI & Keyboard Crop/Redaction)*

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
| Post-v0.63.0 Audit Remediation (P0–P3 Core) | Shipped in `0.63.1` | `v0.63.1` (PR #177) |
| Commercial-Safe Offline Translation Stack | Shipped in `0.63.2` (Apache-2.0 Google MADLAD-400 + optional Helsinki-NLP OPUS-MT) | `v0.63.2` (PRs #178–#179) |
| Speed, Polish & Full Coverage (P1–P3) | Shipped in `0.64.0` (full HTTP route tests, lazy imports, OCR doc cache, virtualized `pages.js`, 5-locale parity, Filing Rules UI, ARIA nav, 1-click pack recovery) | `v0.64.0` (PR #180) |
| Translation Pack Cleanup, Settings Redesign & Performance | Shipped in `0.64.1`–`0.64.4` (94 MADLAD languages, 5-tab Settings redesign, ~4× faster cold startup, 2–3× faster MADLAD translation, warm OCR pool, gzip static cache, Free model RAM, `bilingual-pages` book mode, book-styled stacked reflow, Reader quick-translate UX) | `v0.64.1`–`v0.64.4` (PRs #181–#184) |
| Backend Modularization (Wave 2 — P4) | Shipped in `0.65.0` (split `accessibility`, `server/operations`, `cli`, `translate`, `core`, `ocr` into cohesive subpackages; replaced all 44 silent `except Exception: pass` sites with scrubbed `zpdf` diagnostics + AST guard; extended `test_imports.py` to subpackages) | [`2026-11-24-backend-modularization-wave2.md`](superpowers/plans/2026-11-24-backend-modularization-wave2.md) |

---

## Active & Planned Tracks

All planned roadmap tracks through `v0.65.0` are complete. Next tracks will be scoped as new priorities emerge.

---

## Deferred / Externally Gated
- **Code-signing-gated Windows items**: Authenticode signing, MSI enterprise installer, and Explorer thumbnail COM DLL (`Closer to Windows — Phase 4`, [`2026-11-15-windows-integration.md`](superpowers/plans/2026-11-15-windows-integration.md)) — paused unless a code-signing certificate is adopted.
- **Security Phase 5 owner key setup**: Code complete; release-signing key setup optional.
- **Small Maintenance / Polish Tail**:
  - Close out remaining minor visual polish notes from paragraph reflow and drawing markups as those files are touched.
  - See [`docs/RELEASE_READINESS.md`](RELEASE_READINESS.md) for the release-readiness classification and competitive matrix.
