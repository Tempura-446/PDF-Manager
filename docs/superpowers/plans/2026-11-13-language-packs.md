# Language Packs (the app in other languages, and translation): Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ZPDF works in your language, and translates PDFs between languages, offline as everything else is. A **language pack** is one optional download per language. It holds:
- the app's words in that language;
- a translation model to and from English;
- an OCR model for that script, if the built-in one doesn't cover it;
- a word table so search by meaning understands that language.

English stays built in.

**Where it stands:**
- The interface's text is written straight into `gui.html`, `static/*.js` and the server's messages (`gui.py` and the modules' `PdfManagerError` texts). There's no translation layer.
- OCR: the built-in engine (rapidocr) reads English and Chinese, and Tesseract reads its installed languages.
- Search by meaning: English only (`static/meaning/words.npz`).

**How packs are delivered:**
- Packs are published as files on ZPDF-releases (`ZPDF-lang-<code>-<version>.zip`, with a checksum).
- Settings → Language lists them and downloads one when asked. This is the one time ZPDF goes online for this, and only when the user clicks.
- A pack can also be installed from a file, for offline PCs.
- Packs live in the app's data folder and survive updates. A pack made for an older app version still works: missing strings fall back to English.

## Global constraints

- **Offline once installed:** translation, OCR and search never send text anywhere.
- **Licences allow redistribution:**
  - translation models: OPUS-MT (CC-BY 4.0) through Argos Translate (MIT) or CTranslate2 (MIT), with attribution in `notices.py` and in the pack;
  - the CTranslate2 runtime (MIT) is added only when the first translation pack is installed, or ships with the app if it's small enough. Decide by measuring.
- **Size:** about 30–100 MB per translation direction (quantized int8 models). That's why packs are separate downloads and the app stays small.
- **Right-to-left languages** (Arabic, Hebrew): the interface mirrors with `dir="rtl"`, using logical CSS properties.
- **Plurals and numbers:** use ICU-style plural rules (`Intl.PluralRules`) and `Intl.NumberFormat` and `Intl.DateTimeFormat` in the reader. In Python, a small plural table per language.
- Python 3.10 to 3.14, Linux and Windows. No model identifiers in code or commits.

---

### Phase 1 (release A): the app ready for other languages

**Task 1.1: one place for every string**
- [ ] Extract every user-visible string, building `locales/en.json` with stable keys:
  - `gui.html` and `static/*.js`: use a `t("key", {vars})` helper; attributes like `title`, `aria-label` and `placeholder` go through `data-i18n` markup;
  - the server's messages and notes: Python `t("key", **vars)`;
  - tool names and descriptions in `OPERATIONS`;
  - the command palette's search words.
- [ ] English output must be byte-identical to today's. A test compares rendered pages and API messages before and after.
- [ ] A test fails if a new user-visible string is added without going through `t()`. For JavaScript and HTML, it scans for literal text in `el(...)` calls and `textContent` assignments. For Python, it scans `PdfManagerError("...")` and notes. Known exceptions are allow-listed.

**Task 1.2: language switch**
- [ ] Settings → Language: "Same as Windows" (default), English, then installed packs. Changing it reloads the interface without restarting.
- [ ] Set `lang` and `dir` on `<html>`. Use logical CSS (`margin-inline-start` and similar) wherever left/right matters, so RTL mirrors. The reader's page view itself never mirrors.
- [ ] Test: a fake "xx" locale with all strings wrapped in brackets shows no unbracketed text on the start screen, a tool page, the reader, Settings or the palette, in light and dark. An "ar" test locale mirrors the rail and panels.

### Phase 2 (release B): the first interface packs

- [ ] A pack's format: `manifest.json` (code, name in its own language, app versions it fits, contents, checksums) plus `ui.json` (strings).
- [ ] Translations are made with care, not raw machine output: first a draft, then a review pass by a native speaker if one is available. Key terms (Redact, Flatten, OCR, PDF/A, Bates numbering) get a glossary per language so they stay consistent.
- [ ] First languages: decide with the owner, based on who uses ZPDF. Suggested start: Simplified Chinese, Spanish, French, German.
- [ ] Download and install from Settings, with checksum verification and an "install from file" option. Removing a pack deletes its folder.
- [ ] Test: install a pack from a file, switch language, update the app version (the pack still loads, missing keys show in English), remove the pack.

### Phase 3 (release C): translate a PDF

**Task 3.1: the translation engine**
- [ ] `pdfmanager/translate.py` runs CTranslate2 with an OPUS-MT or Argos model from an installed pack. Text is split into sentences (keeping numbers, codes, URLs and emails as they are) and translated in batches on the CPU, off the UI thread, with progress and Cancel.
- [ ] Without a pack for the language pair, the message says which pack to install, with the size.
- [ ] Test: with a tiny test model (or the engine faked), sentences come back in order, and placeholders such as numbers and "INV-2026-118" pass through unchanged.

**Task 3.2: the Translate tool (three outputs)**
1. **Translated copy, same layout:**
   - Each paragraph is replaced in place with its translation, using the paragraph rewrite engine (`textedit.py` and `textflow.py`): same font where it has the letters, else a matching installed or embedded font for that script; same box.
   - Shrink to fit when the translation is longer, and say so where it had to shrink more than the 0.75 limit.
   - Scanned pages: OCR first.
2. **Side by side:** each page with its translation next to it or on a facing page. Simplest to read, and never loses layout.
3. **Text or Word:** the translated text as .txt or .docx, using the PDF to Word engine for structure.
- [ ] Tables, form field values and annotations' text are translated too. Headers and footers are translated once per distinct text.
- [ ] In the reader: "Translate this page" shows the translation in the file panel, without changing the file.
- [ ] Test: an English invoice and letter translated to French (with a test model, or the engine faked to reverse words), using output 1. Every paragraph is replaced, nothing overlaps its neighbours, and numbers are unchanged.

### Phase 4 (release D): OCR and search by meaning in the pack's language

- [ ] OCR: if the language's script isn't covered by the built-in engine, the pack carries a rapidocr recognition model for it (PaddleOCR's models are Apache-2.0: Latin, Cyrillic, Arabic, Japanese, Korean and others). OCR picks it by the document's or the user's language.
- [ ] Search by meaning: the pack carries a word table for its language, built the same way as `words.npz`, from a multilingual static-embedding source with a redistributable licence. Search by meaning then works across languages: an English query finds a French page.
- [ ] Test: a French scanned page OCRs with the pack's model. A Spanish query finds the right page in a Spanish document.

---

## Order and size

| Release | Phase | Size | Risk |
|---|---|---|---|
| A | 1: strings extracted, language switch, RTL-ready | large (touches every screen), but mechanical | medium: must stay byte-identical in English |
| B | 2: first interface packs, download/install | medium | low |
| C | 3: translate a PDF (engine and three outputs) | large | medium: model size, fonts for other scripts |
| D | 4: OCR models and meaning tables in packs | medium | low–medium |

**Place in the roadmap:** after 0.30. It sits with the drawing tools and paragraph reflow plans; release A is best done before many new screens are added, so less text has to be extracted later. Translation (C) builds on paragraph reflow, so reflow's releases A–B should come first.

**Decisions for the owner:**
- which languages first;
- whether a native speaker can review each interface pack;
- whether translation models may be downloaded from ZPDF-releases when the user asks (the one-time online step).
