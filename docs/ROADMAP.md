# ZPDF Roadmap

Master index of ZPDF's completed, in-progress, and planned feature tracks. Each track links to its detailed plan in [`docs/superpowers/plans/`](superpowers/plans/).

*Last updated: 2026-11-22 (after `v0.60.3` and Engineering Hardening Phases 1–6)*

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
| Hybrid offline translation packs | Shipped in `0.60.3` (bundled `ctranslate2` + `sentencepiece`, hybrid `ZPDF-releases` `langpacks-v1` + Argos catalog) | PR #126, PR #127 |
| Phase 1: Redaction Security Remediation | Done (R-001 coordinate scrub, R-002 content-stream safety, R-003 verification) | PR #128 |
| Phase 2: Text Editing Integrity | Done (T-001 `require_permanent`, T-002 layout/font safety, T-003 `verify_visual`) | PR #129 |
| Phase 3: PDF Comparison Engine | Done (C-001 multi-signal alignment, C-002 streaming memory, C-003 aspect-preserving diff) | PR #130 |
| Phase 4: Security & Reliability Hardening | Done (S-001–S-006 encryption, certsign, safe attachments, temp/log scrubbing) | PR #131 |
| Phase 5: Performance & Scalability | Done (P-001 `packaging/benchmark.py`, P-004 streaming raster redaction, P-005/P-006 `core.batch_run`) | PR #132 |
| Phase 6: Product Quality & Release Readiness | Done (Q-001–Q-009 release readiness, CLI/GUI parity, dependency & notice sync) | [`RELEASE_READINESS.md`](RELEASE_READINESS.md) |

---

## Active & Planned Tracks (Recommended Order)

| Priority | Track | Current State | Plan |
| --- | --- | --- | --- |
| 1 | **Markdown & Review Exports** (PDF → Markdown, annotation/comment summary export) | Done (PRs #93, #94) | [`2026-11-18-markdown-and-exports.md`](superpowers/plans/2026-11-18-markdown-and-exports.md) |
| 2 | **Better OCR** (Phase 2: hard pages, Phase 3: Check OCR UI, Phase 4: tables/forms, Phase 5: speed) | Done (Phases 1–5) | [`2026-11-14-ocr-improvements.md`](superpowers/plans/2026-11-14-ocr-improvements.md) |
| 3 | **Architecture Modularization** (split `gui.py`, `gui.html`, `reader.js`, `textedit.py`, `forms.py` before i18n / new UI modes) | Done (Phases 1–3) | [`2026-11-18-architecture-modularization.md`](superpowers/plans/2026-11-18-architecture-modularization.md) |
| 4 | **Security — Phase 5** (signed releases, lockfile verification, supply-chain hardening) | Code done (owner key setup remaining) | [`2026-11-14-security.md`](superpowers/plans/2026-11-14-security.md) |
| 5 | **Reader Productivity & Print Preflight** (split view with sync scroll, interactive visual crop, N-up/poster preview, print preflight) | Done (Phases 1–3) | [`2026-11-18-reader-productivity.md`](superpowers/plans/2026-11-18-reader-productivity.md) |
| 6 | **Smart Auto-Rename & Filing Rules** (rule-based renaming & routing in Organize and Watch folders) | Done (Phases 1–2) | [`2026-11-18-smart-filing-rules.md`](superpowers/plans/2026-11-18-smart-filing-rules.md) |
| 7 | **Language Packs & Offline Translation** (i18n framework, UI packs, offline PDF translation, multilingual OCR/search) | Done (Phases 1–4 + hybrid packs) | [`2026-11-13-language-packs.md`](superpowers/plans/2026-11-13-language-packs.md) |
| 8 | **macOS & Linux Builds** (Linux AppImage/`.deb`, macOS `.app`/`.dmg`, unified 3-OS updater) | Done (Phases 1–3) | [`2026-11-15-macos-linux-builds.md`](superpowers/plans/2026-11-15-macos-linux-builds.md) |
| 9 | **Security — Phases 6–8** (PAdES/DocMDP/FieldMDP, security profiles & tamper-evident audit log, GPO/ADMX policies, FIPS mode, offline update channel, process isolation) | Done (Phases 6–8; MSI & Authenticode signing gated on code-signing cert) | [`2026-11-14-security.md`](superpowers/plans/2026-11-14-security.md) |
| 10 | **Closer to Windows — Phase 4** (Explorer thumbnail COM DLL — gated on code-signing decision) | Phases 1–3 done | [`2026-11-15-windows-integration.md`](superpowers/plans/2026-11-15-windows-integration.md) |

---

## Small Maintenance / Polish Tail
- ~~Suppress false "This looks like a form to fill in" banner on plain CAD drawings~~ (fixed in `formdetect.py`).
- Keep [`release.yml`](../.github/workflows/release.yml) dispatching `build-app.yml` and `lock.yml` automatically on published tags (fixed in PR #105).
- Close out remaining minor visual polish notes from paragraph reflow and drawing markups as those files are touched.
- See [`docs/RELEASE_READINESS.md`](RELEASE_READINESS.md) for the release-readiness classification, competitive matrix, and post-release priorities.
