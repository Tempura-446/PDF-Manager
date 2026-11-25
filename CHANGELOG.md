# Changelog

What changed in each version of ZPDF. The newest is first; each section is
also the notes of its release on
[ZPDF-releases](https://github.com/Tempura-446/ZPDF-releases/releases).

## 0.77.0

OCR, Translation & Manga Formatting v5 + Android Companion App Phase 2.

- **OCR, Translation & Manga v5 (`ocr/layout.py`, `translate/text.py`, `translate/manga.py`).**
  - OCR now drops hallucinated repeated-symbol border/ruler lines (`||||||||`,
    `========`, `--------`) inside `_lines()` so table borders and scan edges
    never pollute search or translation.
  - Translation now protects URLs, reference codes, emails, and user glossary
    terms with sentinels on the **LLM path** as well as offline neural MT, and
    automatically chunks overlong sentences (>450 chars) with
    `_split_long_for_mt` so 512-token neural models never truncate tails.
  - Manga formatting adds optional **Comic ALL-CAPS** lettering (`comic_caps=True`
    / `_to_comic_caps()`) and distinct upright regular typography for rectangular
    narration/caption boxes.
- **Android & Mobile Companion App Phase 2 (`gui.py`, `server/common.py`, `mobile-touch.js`, `mobile/android/`).**
  - Added opt-in **LAN Companion Mode** (`make_server(..., lan=True)`) with
    strict RFC-1918 / `.local` host validation (`_is_lan_host`) and
    `GET /api/companion/info` pairing metadata (default remains strictly
    `127.0.0.1`-only).
  - Upgraded the floating mobile bottom dock (`mobile-touch.js`) with an
    **LTR / RTL Manga swipe-direction toggle** and 1-tap **Local AI Assistant**
    shortcut, plus `window.zpdfOpenBase64Pdf` Android intent bridge.
  - Upgraded Android `MainActivity.kt` with incoming `ACTION_VIEW` PDF intent
    handoff into the Reader and a clean native offline reconnect screen with
    `ZpdfAndroid.openSettings()` / `retryConnection()` bridge.

## 0.76.0

OCR, Translation & Manga Formatting v4 (Adaptive Binarization Retry, Full-Page Vertical CJK RTL Order, WCAG Contrast, Sentence-Aware Chunking & Broken-Outline Manga Recovery).

- **OCR v4 (`pdfmanager/ocr/preprocess.py`, `pdfmanager/ocr/layout.py`).**
  - Added local-mean adaptive binarization fallback (`_adaptive_binarize_crop`)
    inside `_retry_low_confidence()` to recover faint/shaded ink on uneven
    scans.
  - Added full-page vertical CJK (*tategaki*) RTL column reading order in
    `_ordered()` so scanned Japanese books and manga extract right-to-left,
    top-to-bottom.
  - Added soft-hyphen (`\u00ad`) cleanup and optional line-wrap de-hyphenation
    in `text_of(lines, dehyphenate=True)`.
- **Translation v4 (`pdfmanager/translate/text.py`, `layout.py`).**
  - Added sentence-aware long-paragraph chunking (`_split_long_for_mt`) so
    512-token neural MT models never truncate long paragraphs.
  - Upgraded overlay text contrast selection to WCAG 2.1 linearized relative
    luminance (`_contrast_ratio < 4.5`) for crisp legibility on tinted boxes.
- **Manga Formatting v4 (`pdfmanager/translate/manga.py`).**
  - Added 1px morphological closing before bubble CCL to recover hand-drawn
    speech bubbles with tiny 1–2px ink outline gaps.
  - Upgraded `order_manga_bubbles()` with panel/gutter-aware XY-cut RTL/LTR
    ordering for 2-column and 4-koma manga pages.

## 0.75.0

OCR, Translation & Manga Formatting Power Upgrade.

- **Strengthened OCR (`pdfmanager/ocr/preprocess.py`, `pdfmanager/ocr/layout.py`).**
  - Border-safe paper/contrast measurement ignores outer 4% black scan margins
    so scanner borders no longer skew background flattening.
  - Low-confidence line retries now apply `UnsharpMask` after 2× upscaling on
    small/blurry text and automatically attempt an inverted-polarity pass on
    dark-background crops (white text on dark headers/banners).
  - CJK-aware `_prose()` enables clean multi-column CJK splitting; upright
    vertical CJK (*tategaki*) pages are no longer misclassified as rotated
    sideways pages in `_reads_wrong_way()`.
  - Post-OCR normalization expands Latin ligatures (`ﬁ/ﬂ/ﬀ/ﬃ/ﬄ`), strips stray
    control characters, and drops isolated 1-char low-confidence punctuation
    speckles.
- **Improved Translation (`pdfmanager/translate/text.py`, `layout.py`).**
  - Hardened neural entity protection for inline `` `code` ``, semantic
    versions (`v1.2.3`), `snake_case` identifiers, and ISO currency amounts
    (`USD`/`EUR`/`GBP`/`JPY`/`￥`), with fullwidth-digit and separator-tolerant
    sentinel restoration.
  - Added column-aware reading-order sorting (`_sort_blocks_reading_order`),
    Unicode de-hyphenation (`-`, `\u00ad`, `\u2010`, `\u2011`), zero-space
    CJK-to-CJK line joining, bold subheading separation, trimmed border-safe
    background color sampling, and progressive line-leading tightening
    (`1.20 → 1.08`) before clipping dense cells.
- **Improved Manga Formatting (`pdfmanager/translate/manga.py`, `pdfmanager/cbz.py`).**
  - Raster bubble OCR now strips narrow furigana/ruby columns and sorts
    vertical Japanese columns right-to-left (`_order_and_clean_bubble_lines`).
  - Added elliptical/diamond-shaped comic dialogue wrapping
    (`_wrap_elliptical`) for oval bubbles, comic punctuation & emphasis polish
    (`_format_comic_text`: `…`, `—`, `?!`, bold shouts up to 22pt via binary
    search, italic whispers/thoughts), Gaussian-feathered bubble erasing masks,
    and adaptive paper-luminance bubble detection.
  - `cbz_to_pdf` preserves monochrome/grayscale manga pages losslessly
    (`FlateDecode` PNG) instead of lossy JPEG so fine kanji strokes &
    screentones stay crisp for OCR.

## 0.74.0

Local AI Document Assistant (Ask this PDF with Clickable Page Citations, Document/Chapter Summarizer & Structured Table/Key-Value Extractor).

- **Local AI Document Assistant Engine (`pdfmanager/assistant.py`).**
  - Reuses the user's configured Ollama / LM Studio / OpenAI-compatible
    endpoint from Settings (`translate_api_url`, `translate_model`,
    `translate_api_key`) and routes all requests exclusively through
    `i18n._http_post_json` (loopback-HTTP allowed, HTTPS-only remote, redirect
    sanitization, and enterprise network policy enforcement).
  - **Ask this PDF (`POST /api/ai/ask`)**: lightweight local lexical page
    ranking fits large PDFs into local 7B model context windows and extracts
    inline `[p. N]` citations.
  - **Document / Chapter Summarizer (`POST /api/ai/summarize`)**: summarizes
    full PDFs or custom page ranges in Key Bullets, Executive, or Study Notes
    styles with page citations.
  - **Structured Table & Key-Value Extraction (`POST /api/ai/extract`)**:
    extracts invoices, forms, and tables to clean JSON and CSV.
- **Reader UI (`static/ai-assistant.js`).**
  - Added Local AI Assistant modal to the Reader More menu and Command Palette
    with **clickable `[p. N]` page citation chips** and one-click **Copy /
    Download JSON / Download CSV** export buttons.

## 0.73.0

Android Companion App (Phase 1 Foundation) — Installable PWA Shell, Touch-First Mobile Reader & Kotlin WebView Project.

- **Installable PWA & Offline UI Shell (`manifest.webmanifest`, `icon.svg`, `sw.js`).**
  - Added `.webmanifest` MIME type (`application/manifest+json`) and pre-token
    routes for `/manifest.webmanifest` and `/sw.js` in `pdfmanager/gui.py`.
  - `sw.js` caches static UI shell assets (`/static/*`) while strictly
    bypassing all `/api/*` private document endpoints.
- **Touch-First Mobile Reader & Translator Shell (`static/mobile-touch.js` + `reader.css`).**
  - Added swipe-left / swipe-right page turns (respecting horizontal scroll
    when zoomed in), double-tap zoom toggle (`page-width` ↔ `150%`), and a
    44px-target floating mobile bottom dock (Prev / Page / Next / Reading Theme
    cycle / Translate page) on narrow and coarse-pointer screens.
- **Android Companion Kotlin WebView Scaffold (`mobile/android/`).**
  - Added ready-to-build Gradle/Kotlin Android project (`MainActivity.kt`,
    `AndroidManifest.xml` with `application/pdf` `ACTION_VIEW` intent filter,
    native file chooser, hardware back navigation, and saved companion URL
    preference).

## 0.72.0

Reader Comfort & Power-User Upgrade (Smart Night/Sepia/E-Ink Themes, Workspace Sessions & In-Place OCR Typo Editor).

- **Smart Reading Themes (Smart Night, Warm Sepia, E-Ink High Contrast).**
  - Upgraded the Reader page display from a single dark toggle to four
    persistent reading themes (`light`, `night`, `sepia`, `eink`) synced across
    primary and split-view panes, accessible from the Reader More menu and
    Command Palette.
- **Workspace Sessions (`pdfmanager/sessions.py`).**
  - Save, restore, and delete named multi-tab workspace snapshots including open
    documents, active tab, per-tab reading positions/zoom/rotation, and
    split-view layout/sync settings via `GET/POST /api/sessions` and
    `POST /api/sessions/delete`.
  - Respects ZPDF's privacy settings and strict `OpenFiles` filesystem
    sandboxing (never accepts arbitrary paths from the browser).
- **In-Place OCR Typo Editor (`pdfmanager/textlayer.py` + `checkocr.js`).**
  - Added `read_all_ocr_words` (`POST /api/ocr/words`) and **All words** mode
    in Check OCR so users can click and fix *any* invisible OCR word—even
    high-confidence ones—without touching page pixels.
  - Added `replace_ocr_text` (`POST /api/ocr/replace`) and inline **Find &
    Replace** across the invisible OCR text layer with proportional `Tm`
    horizontal scaling and full undo/redo integration.

## 0.71.0

Book & Manga Publishing Studio (CBZ Archives, RTL/Signature Booklet Imposition & Spine Calculator).

- **CBZ Comic & Manga Archive Engine (`pdfmanager/cbz.py`).**
  - `cbz_to_pdf`: natural-sorted image extraction (`page_2.png` before
    `page_10.png`), zip-slip path-traversal & decompression-bomb guards, and
    optional `/ViewerPreferences << /Direction /R2L >>` for right-to-left manga
    reading.
  - `pdf_to_cbz`: exports PDFs to `.cbz` archives (WebP, JPEG, or PNG pages)
    with `ComicInfo.xml` metadata and `core.render_scale` OOM protection.
  - `translate_cbz`: one-shot `.cbz` → Manga v2 speech-bubble translation →
    `.cbz` or `.pdf` pipeline.
  - Exposed in CLI (`zpdf cbz-to-pdf`, `zpdf pdf-to-cbz`) and GUI Convert tools
    (**PDF → CBZ (Comic/Manga)**).
- **Booklet Imposition v2 (`core.booklet`) & Print-on-Demand Spine Calculator (`core.spine_width_mm`).**
  - Added **Right-to-Left (Manga / Japanese)** binding alongside Western LTR
    saddle-stitch (`--binding ltr|rtl`), **multi-signature sections**
    (`--signature 8|16|32`), and **creep compensation** (`--creep` mm) in both
    CLI and the GUI **Booklet** tool.
  - Added `core.spine_width_mm` and `zpdf spine` with commercial & POD paper
    stock presets (`manga_60gsm`, `uncoated_80gsm`, `white_90gsm`,
    `cream_90gsm`, `gloss_115gsm`, `matte_130gsm`) and full-wrap cover
    dimensions.

## 0.70.0

Print-Production CMYK Preflight v3 (Press-Room Grade).

- **Recursive Form XObject (`/Subtype /Form`) Inspection & Separation (`pdfmanager/preflight.py`).**
  - Preflight checks, pure-Python fixes, and C/M/Y/K plate separation now
    recursively traverse nested Form XObjects (with CTM composition, Form-local
    `/Resources`, and cycle guards), catching low-DPI images, >300% TAC fills,
    RGB logos, hairlines, and white overprint inside placed artwork from
    InDesign, Illustrator, Figma, and Canva.
- **Pure-Python One-Click CMYK Press Fixes (Zero Ghostscript Required).**
  - `fix_rich_black_text=True`: converts small rich-black body text (`<= 12 pt`)
    to crisp pure 100% K (`0/0/0/1 k`) with `/op true` overprint.
  - `clamp_tac`: GCR-preserving ink-limit clamping on both CMYK vectors and
    embedded CMYK `Image` XObjects so every pixel/fill stays under your paper
    stock's TAC limit (280% / 300% / 320%).
  - `fix_hairlines`: bumps sub-threshold hairline strokes (`< 0.25 pt`) up to
    `0.25 pt`.
  - `convert_rgb_to_cmyk=True`: converts `DeviceRGB` vectors and Image XObjects
    to `DeviceCMYK` directly in Python.
- **Spot Ink Inventory & Registration-Colour Hazard Audit.**
  - Reports all named `Separation` / `DeviceN` spot inks (`spot_colors`), warns
    on >2 spot inks (`many_spot_colors`), and flags `/Separation /All` (400%
    registration ink) accidentally used inside the page (`registration_color`).
- **Live Ink-Density Eyedropper & "Fix All CMYK Press Issues" (`POST /api/preflight/inkmap`, `reader.js`).**
  - Hover over the Reader's Output Preview to read live **`C% · M% · Y% · K%`
    and `TAC%`** under your cursor, and apply all pure-Python CMYK press fixes
    in one click.

## 0.69.0

Print-Production CMYK Preflight v2 & Output Preview Suite.

- **PDF/X-1a & PDF/X-4 Compliance Profiles (`pdfmanager/preflight.py`).**
  - Added `--profile standard|pdfx-1a|pdfx-4` checking ISO 15930 rules:
    `GTS_PDFX` `/OutputIntents` characterization profile, `/Info /Trapped`
    status (`/True` or `/False`), `/TrimBox` or `/ArtBox` exclusivity,
    encryption and JavaScript prohibitions, RGB colour errors, and live
    transparency rules (`error` in PDF/X-1a, permitted in PDF/X-4).
- **Overprint, Knockout & Total Ink Coverage (TAC) Audit.**
  - Tracks `/ExtGState` `/OP` and `/op` across graphics states to flag critical
    **white overprint** errors (`white_overprint` — white text/vectors that
    would vanish on press) and pure-black small text knockouts
    (`black_knockout_text`).
  - Measures **Total Ink / Area Coverage (`max_tac`, default 300%)** across both
    CMYK vector fills/strokes and embedded CMYK `Image` XObjects (`high_tac`).
- **True CMYK Plate Separation & TAC Heatmap Output Preview (`/api/preflight/preview`, `reader.js`).**
  - Separates real PDF `DeviceCMYK`, `DeviceGray` (K-only), and `DeviceRGB`
    (GCR) operators and Image XObjects into individual **Cyan**, **Magenta**,
    **Yellow**, and **Black (K)** plates plus a quantitative **TAC Heatmap**
    highlighting warning and over-limit ink zones right inside the Reader's
    Print Preflight dialog.
- **One-Click Press Fixes (`preflight.fix`).**
  - Added **Fix white overprint** (`fix_white_overprint=True`) and **Add PDF/X
    OutputIntent** (`add_pdfx_intent=True`, setting `GTS_PDFX` `CGATS TR 001`,
    `/Trapped /False`, and `/TrimBox`).
- **Security Hardening.** Enforced `https://`-only URLs in OCR model downloads
  and `core.render_scale` giant-page OOM protection in Manga mode.

## 0.68.0

Manga / Comic Speech-Bubble Mode v2.

- **Shape-Aware Interior Cleaning & Pixel-Accurate Masks (`pdfmanager/translate/manga.py`).**
  - Automatically classifies detected bubbles into rectangular narration boxes
    (`rect` → inset rounded-rect), standard speech bubbles (`oval` → inset
    ellipse), and irregular/spiky shout bursts (`polygon` → hole-filled,
    inward-eroded RGBA mask patch) so the artist's borders and surrounding
    artwork stay completely intact.
- **Dark / Inverted Speech Bubbles (`inverted=True`).**
  - Detects dramatic dark/black bubbles with white text alongside standard light
    bubbles, cleaning with the sampled dark interior color and typesetting
    translated dialogue in crisp white.
- **Digital Manga Zero-OCR Fast Path (`extract_digital_bubble_text`).**
  - When a manga or comic PDF already contains selectable glyphs inside a
    bubble, extracts them directly (with vertical CJK RTL column ordering and
    furigana stripping) without running OCR.
- **OCR Pre-Processing & Comic Lettering Polish.**
  - Added `preprocess_bubble_for_ocr` (auto-inversion for dark bubbles, 2×
    LANCZOS upscaling for small crops, autocontrast, and white margin padding).
  - Added automatic reading direction (`default_reading_dir`: RTL for
    Japanese/Chinese/Korean manga, LTR for Western comics) and balanced
    multi-line comic wrapping (`_wrap_balanced`) to eliminate single-word orphan
    lines inside bubbles.

## 0.67.0

Vertical CJK (*Tategaki*) Book Translation & Manga / Comic Speech-Bubble Mode.

- **Vertical CJK (*Tategaki*) Support (`pdfmanager/translate/vertical.py`).**
  - Automatic page-level detection of vertical Japanese and Traditional/Simplified
    Chinese layouts (`is_vertical_cjk_page`) across all translation modes
    (`side-by-side`, `bilingual-pages`, `bilingual`, `in-place`, `text`).
  - Automatic furigana / ruby annotation stripping (`size <= 0.62 × body_size`),
    Unicode vertical presentation punctuation normalization (`﹁﹂﹃﹄︵︶︱` →
    `「」『』（）—`), top-to-bottom column grouping, and right-to-left paragraph
    merging so novels and light novels translate cleanly without cross-column
    scrambling.
- **Manga / Comic Speech-Bubble Translation Mode (`mode="manga"`, `pdfmanager/translate/manga.py`).**
  - 100% offline, commercial-safe pure Python + NumPy + Pillow + ReportLab
    connected-component speech-bubble detector (`detect_speech_bubbles`) that
    filters out page margins and empty panels, orders bubbles in manga reading
    order (top-to-bottom bands, right-to-left within each band), extracts bubble
    dialogue, cleans strictly inside each bubble's inset ellipse (preserving the
    artist's black outline and surrounding artwork), and typesets centered,
    auto-fitted dialogue inside each bubble.
  - Available in the Translate tool UI (**Manga / Comic (speech bubbles)**) and
    CLI (`zpdf translate --mode manga`).

## 0.66.0

Interactive Reader Diff Viewer, Live Bilingual Book Reading, Attachments GUI, Keyboard Crop/Redaction & Batch Hardening.

- **Interactive Side-by-Side Diff Viewer in Reader Split View.** Added
  `POST /api/reader/compare` (supporting both opened `fileId`s and uploads) and
  a **Highlight diff** mode in Split View with red/green change overlays,
  aligned page syncing (`row.old <-> row.new`), and Prev/Next change navigation.
- **Book & Reading Experience Suite.**
  - **Live bilingual split-view reading (`__translate__`)**: read the original
    PDF on the left with a live, cached translated pane on the right as you flip
    pages (`From`/`To` selectors, `dir="auto"` RTL, Original/Translated toggle).
  - **Preserved & remapped bookmarks in `bilingual-pages`**: chapter outlines
    are now cloned, remapped to the interleaved page numbers, and translated.
  - **Persistent reading progress**: tracks `maxPage`, `totalPages`, and
    percentage per document fingerprint in `reader-render.js` / `reader-nav.js`.
- **Embedded Attachments GUI + Keyboard Crop & Redaction.**
  - Added `POST /api/attachments/update` and **Attach file… / Remove** controls
    in Reader Document Properties with full Undo/Redo + Save support.
  - Added Arrow-key nudge (`1 pt`, `Shift` = `10 pt`), `Alt+Arrow` resize,
    `Enter` apply, `Delete` remove, and `Escape` cancel to Reader visual crop
    (`cropmode.js`), redaction marks (`annotate.js`), and `RegionEditor`
    (`regions.js`).
- **Multiprocessing `core.batch_run` & Docs Refresh.** Added explicit
  `mode="thread" | "process"` support and `BrokenProcessPool` hardening to
  `core.batch_run`, and refreshed `docs/RELEASE_READINESS.md` for `v0.66.0`.

## 0.65.0

Backend Modularization (Wave 2) & diagnostic error-handling hardening.

- **Modularized all six remaining >2,000-LOC backend monoliths (~14,500 LOC)
  into cohesive, domain-focused subpackages** with 100% public API compatibility:
  - `pdfmanager/accessibility/` (`geometry`, `scanner`, `layout`, `tagger`, `checker`)
  - `pdfmanager/server/operations/` (`_deps`, `catalog`, `dispatch`, `repair`, `runner`, `preview`)
  - `pdfmanager/cli/` (`common`, `commands_edit`, `commands_convert`, `commands_multi`, `parser`)
  - `pdfmanager/translate/` (`models`, `engines`, `text`, `layout`, `pipeline`)
  - `pdfmanager/core/` (`io`, `geometry`, `pages`, `security`, `stamp`, `convert`)
  - `pdfmanager/ocr/` (`_facade`, `engines`, `models`, `preprocess`, `layout`, `runner`)
- **Replaced all 44 silent `except Exception: pass` sites across `pdfmanager/`**
  with scrubbed `zpdf` (`applog`) debug diagnostics and added a permanent
  AST regression guard in `tests/test_code_quality.py`.
- **Extended `tests/test_imports.py`** to verify independent importability and
  two-way dependency guards across both top-level `.py` modules and subpackages.

## 0.64.4

Book-focused bilingual translation + Reader & Translate PDF UI/UX and batching optimizations.

- **New `bilingual-pages` mode (Bilingual book).** Outputs alternating Original
  Page N → Translated Page N at normal single-page book width, preserving covers,
  illustrations, diagrams, charts, tables, and formatting; pure-image covers and
  full-page illustrations are emitted once without duplication.
- **Book-optimized `bilingual` (stacked paragraph reflow).** Preserves pure-image
  covers and full-page illustrations verbatim instead of replacing them with a
  placeholder, detects and strips repetitive running headers/footers and
  standalone page numbers in the top/bottom 9% margin bands, and upgrades
  typography with chapter heading hierarchy, a left accent bar for translations,
  and a quiet `Original p. N` footer.
- **Faster book translation & zero-upload Reader quick-translate.** Increased
  outer neural batch size to 128 in `translate_texts()` for better CTranslate2
  length-sorted bucketing across pages, and switched Reader's
  `translateCurrentPage()` to reference open unmodified PDFs by `fileId` instead
  of re-uploading the entire PDF blob over HTTP.
- **Translation UI & UX polish.** Moved `English` to the top of the `To language`
  selector, shortened layout dropdown labels with a book-focused hint, fixed
  1-click pack recovery for MADLAD-only languages (`madlad-400-3b`), removed the
  duplicate language list in `reader.js`, and upgraded Reader quick-translate
  with persisted languages, `⇄ Swap`, `dir="auto"` RTL support, a
  `Translated / Original` toggle, and a `Next page →` reading flow.

## 0.64.3

Performance & memory release: ~4× faster cold startup, 2–3× faster offline MADLAD-400 translation, warm OCR batch pool, gzipped static assets, and 1-click model RAM release.

- **~4× faster cold startup.** Extracted zero-dependency `pdfmanager/errors.py`
  (`PdfManagerError`) and deferred `pypdf`, `cryptography`, `PIL`, and `core`
  imports across `gui.py` and `server/*` so the desktop window and HTTP server
  launch in ~160 ms before any PDF/crypto libraries load.
- **2–3× faster offline MADLAD-400 translation & instant Cancel.** Added
  per-batch string deduplication (headers/footers/table headings translated
  once), length-sorted token-bounded sub-batching (`max_batch_size=2048,
  batch_type="tokens"`, 32 chunks/sub-batch with mid-batch `progress.check()`),
  `beam_size=2` for multi-billion MADLAD-400 models, and `inter_threads=2` on
  6+ core CPUs.
- **Free model RAM button.** Added `POST /api/translate/unload` and a **Free
  model RAM** button in **Settings → Language** to release cached CTranslate2
  translators from memory on demand without restarting ZPDF.
- **Warm OCR worker pool for batch jobs.** Reuses a bounded
  `ProcessPoolExecutor` across consecutive OCR files with a 120 s idle TTL and
  `atexit` cleanup so batch folder OCR avoids re-spawning ONNX workers per file.
- **Pre-cached gzip static asset serving.** Automatically serves gzip-compressed
  JS/CSS/SVG/JSON assets (>4 KB) from `_STATIC_CACHE` when the browser sends
  `Accept-Encoding: gzip`, cutting initial reader payload by ~75%.

## 0.64.2

Fixed pack removal confirmation dialog and redesigned Settings with clean section tabs.

- **Fixed Remove language pack / Clear history / Starter rules confirmation.**
  `appAsk()` in `app-core.js` previously only accepted positional arguments
  `(title, text, buttons)`, causing object-style confirmation calls in
  `settings.js` and `watch-ui.js` to throw before the removal API call ever ran.
  `appAsk()` now supports both signatures cleanly, with danger-styled confirm
  buttons and proper error feedback.
- **Redesigned Settings dialog.** Organized Settings into 5 focused tabs
  (**General**, **Language**, **Automation**, **Privacy**, **About**) with
  keyboard arrow-key navigation, grouped surface cards, collapsible secondary
  forms (Add watched folder, Developer GitHub repo/token), and polished
  installed-pack cards with a **Retired · safe to remove** badge for legacy
  `nllb-200-*` packs.

## 0.64.1

Translation pack cleanup & expanded MADLAD-400 language options.

- **Removable legacy language packs.** Fixed `remove_pack()` in `i18n.py` so
  previously downloaded retired packs (such as `nllb-200-1.3b` and
  `nllb-200-3.3b`) can be cleanly removed from **Settings → Language** while
  keeping strict path-traversal protection.
- **94 languages in Translate PDF & Reader.** Expanded the **From language**
  and **To language** dropdowns in both **Translate PDF** (including the Reader
  File Panel) and Reader's **Translate this page…** dialog (plus MADLAD-400
  code mappings in `translate.py`) from 18 to 94 major world languages, with
  **Auto-detect** enabled by default in Reader page translation.

## 0.64.0

Speed, Polish & Full Coverage: faster startup, virtualized large-PDF page board, OCR worker caching, Smart Filing Rules editor, full i18n/ARIA polish, and complete HTTP route test coverage.

- **Faster startup & large-PDF performance.** Lazy-imported heavy backend
  modules (`numpy`/`search`, `pikepdf`/`layers`, `ocr`, `accessibility`,
  `certsign`, `compare`, `redact`, `preflight`, `office`) in `gui.py` and
  `server/*`; cached `PdfDocument` handles per OCR worker process instead of
  re-parsing the PDF on every page; and virtualized the `pages.js` page-picker
  grid for 120+ page PDFs with windowed row rendering and overscan.
- **Full i18n catalog & UI/accessibility polish.** Expanded `data-i18n`
  attributes across navigation, tool cards, Settings, Translate, and dialogs
  with 100% key parity across all 5 locales (`en`, `es`, `fr`, `de`,
  `zh-Hans`); added a Smart Filing Rules viewer/editor (add, edit, reorder,
  delete) in Settings; added shared arrow-key navigation across Reader
  `role="menu"` popups and `role="tablist"` strips; added 1-click missing-pack
  download recovery in the Reader File Panel; and matched desktop window
  startup background color (`#0f1114` / `#f6f6f3`) to OS dark mode.
- **Full HTTP route coverage & bug fixes.** Added live HTTP integration tests
  for all `/api/form/*`, `/api/recent/restore`, `/api/comments/markdown`,
  `/api/quantities`, `/api/password/strength`, and `/api/filing/*` endpoints,
  fixing a missing `_safe_name` import in `POST /api/form/import` and filename
  glob / response parsing in `POST /api/filing/preview`.

## 0.63.2

100% commercial-safe offline translation stack: Google MADLAD-400 (3B / 7B / 10.7B, Apache-2.0) and optional Helsinki-NLP OPUS-MT fast pairs.

- **Replaced Meta NLLB-200 (`CC-BY-NC-4.0`) with Google MADLAD-400 (`Apache-2.0`).**
  Added all three CTranslate2 `int8` universal tiers covering 400+ languages
  directly: **MADLAD-400 3B** (2.96 GB, fast, 8+ GB RAM), **MADLAD-400 7B**
  (8.32 GB, back-translated, 16+ GB RAM), and **MADLAD-400 10.7B** (10.74 GB,
  flagship offline quality, 24+ GB RAM / GPU), automatically preferring the
  highest installed tier (`10.7B > 7B > 3B`).
- **Optional Helsinki-NLP OPUS-MT fast pairs (`CC-BY-4.0 / Apache-2.0`).**
  Added lightweight bidirectional pairs (`opus-es`, `opus-fr`, `opus-de`,
  ~312 MB each) for fast CPU translation on lower-RAM machines.
- **License notices.** Added third-party attributions for Google MADLAD-400,
  Helsinki-NLP OPUS-MT, and Argos Translate (`MIT / CC0`) in **Settings → About**
  and `THIRD-PARTY-NOTICES.txt`.

## 0.63.1

Post-release audit hardening: broken API endpoint fixes, security & SSRF/checksum guards, major OCR/translation memory reductions, and UI/UX/i18n fixes.

- **Critical API & translation fixes.** Fixed `POST /api/preflight/fix`,
  `GET /api/audit`, and `GET /api/policy` (`AttributeError` / top-level `do_GET`
  error handling); preserved non-text image/diagram pages in `translate_pdf`
  `in-place` mode; propagated `progress.Cancelled` during OCR/layout/LLM/CT2
  translation; scoped annotation translation to selected `pages`; and added
  `ctranslate2|sentencepiece` to the self-updater's `_NO_BUILD` fallback regex.
- **Security & privacy hardening.** Enforced `policy.check_network_allowed()`
  and IP/DNS-level SSRF checks in Web-to-PDF (blocking local paths in URL mode),
  prevented stored AI API key fallback when `/api/translate/test` overrides the
  endpoint URL, made curated language-pack SHA-256 verification fail-closed with
  safe 3xx redirect validation and zip-bomb/symlink extraction guards, sanitized
  `target_lang` and `_form_import` filenames, and blocked metadata hosts in eSCL.
- **Performance & memory ceilings.** Eliminated 2–5 GB PIL bitmap retention in
  OCR Pass 1 (72-DPI preview hashing + on-demand full-DPI rendering), capped
  `_CT2_CACHE` with a 4 GB byte budget + single-flight loading lock and bounded
  `_SubwordBpe._cache` / `ocr._engines`, released upload buffers before running
  async jobs, cached `make_safe_on_open` across HTTP Range requests, narrowed
  `PDFIUM_LOCK` per-page in `pdf_to_images` and `trim_margins`, bounded
  concurrent background jobs, capped Reader undo history to 192 MB per tab, and
  cleaned up leaked thumbnail canvases/blob URLs.
- **UI/UX, accessibility & i18n fixes.** Fixed `auto_ocr` and `monochrome`
  rendering as text boxes (`kind="check"`), fixed `show_if` array conditions
  (Comments `author`/`kind` filters), fixed `static/i18n.js` loading and
  `window.ZpdfI18n` registration, unified Settings primary `Save` to include AI
  Translation & Glossary fields, stopped 2 s Watch-folder polling from stealing
  focus or collapsing dropdowns, serialized language-pack downloads, added
  destructive-action confirmations, fixed `from-drawing` file picker filters,
  and styled Settings `<textarea>` / search controls and `.btn.sm` buttons.

## 0.63.0

Bilingual stacked mode, page range & auto-OCR translation, GPU acceleration, code/math/RTL/rotated/bookmark fidelity, and NLLB-200 3.3B.

- **Layout & content fidelity.** Preserves monospace code blocks and math
  formulas verbatim, keeps bold lead-in labels (e.g. **Note:** / **Skills:**),
  renders 90°/270° rotated margin text at its original orientation, translates
  PDF bookmarks (`/Outlines`) in `in-place` mode, and applies BiDi visual
  ordering + right-alignment for Arabic/Hebrew/Persian/Urdu (RTL) targets.
- **Bilingual stacked mode.** New `bilingual` output layout renders clean
  paginated PDFs with each translated paragraph stacked directly underneath its
  original in a subtle slate-blue accent.
- **Page range, Auto-OCR & GPU acceleration.** Added optional **Pages** range
  (`1-5, 8`) and **Auto-OCR scanned pages** checkbox to **Translate PDF**, plus
  automatic CUDA GPU detection (with CPU fallback) and tuned CPU threading in
  CTranslate2.
- **Maximum Offline Quality pack: NLLB-200 3.3B (`int8`, 3.38 GB).** Available
  as a 1-click download (`nllb-200-3.3b`) under **Settings → Language**,
  automatically preferred over 1.3B and 600M when installed.

## 0.62.1

Language module download sizes & progress bar, model caching, auto-detect, vertical headroom, and Smart AI connection test.

- **Language module download sizes & live progress bar.** Displays exact sizes
  for NLLB-200 (`645 MB`, `1.4 GB`) and ZPDF packs (and estimated sizes for
  Argos packs) in **Settings → Language**, shows on-disk size for installed
  packs, and streams real-time MB/GB & `%` progress with a **Cancel** button
  in both Settings and the inline Translate PDF download flow.
- **In-memory model LRU cache & reuse.** Keeps the active CTranslate2 model +
  tokenizer in memory (LRU size 2, auto-invalidated on `model.bin` update) and
  reuses one loaded translator across blocks, fallback pages, and annotations
  for 5–10× faster repeat and page-by-page translations.
- **Offline auto-detect source language.** Added zero-dependency script &
  language detection (`Auto-detect` default in **Translate PDF**).
- **Smart vertical headroom, cropped-page fix & scanned-PDF warning.** Uses
  available whitespace below non-table blocks so longer target languages (e.g.
  EN → ES/DE/FR) don't clip, accounts for non-zero `CropBox` offsets, and warns
  when pages have no selectable text (suggesting **Make searchable (OCR)**).
- **Multi-phase PDF translation progress & Smart AI Test connection button.**
  Reports distinct layout/translate/render phases and lets you verify local
  Ollama or API endpoints in 1 click under **Settings → Language**.

## 0.62.0

Smarter 100% offline PDF translation, NLLB-200 1.3B pack, and custom glossary.

- **Smarter offline translation pipeline (zero extra download).** Added
  abbreviation-safe & numbered-list-safe sentence splitting (preserving `Dr.`,
  `Inc.`, `U.S.`, `Fig. 2`, `Jan. 5`, `e.g.`, and `1. First item`),
  paragraph-level context chunking (up to 360 chars) for NLLB-200, multi-word
  ALL-CAPS heading normalization (keeping short acronyms like `PDF`/`USA`/`API`),
  bullet & `Label: <verbatim>` separation, length-bucketed CTranslate2 decoding
  with `repetition_penalty=1.2` and short-heading anti-hallucination retry, and
  CJK full-width / Latin punctuation polish.
- **Best Offline Quality pack: NLLB-200-distilled-1.3B (`int8`, 200 languages).**
  Available as a 1-click download (`nllb-200-1.3b`, ~1.4 GB) under **Settings →
  Language → Download language packs…** and automatically preferred over 600M
  when installed — zero LLM server required.
- **Custom Translation Glossary & Keep-Verbatim Terms.** Configurable under
  **Settings → Language** (`Term` to keep verbatim or `Source = Target` to
  force exact terminology), enforced deterministically in both offline
  CTranslate2 and Smart AI modes.

## 0.61.9

Bordered-page layout fix and Smart AI (context-aware LLM) translation engine.

- **Fixed bordered-page & multi-column layout regression.** Guarded
  `pdfplumber.find_tables()` so outer page borders and large boxed sections
  (resumes, forms, invoices) are never treated as 1×1 table cells, requiring
  real multi-row/multi-column tables with homogeneous font sizes and no large
  internal paragraph gaps, and restricted free-text centering to true solo
  centered titles.
- **Smart Context-Aware AI Translation Engine (Local Ollama / LM Studio or
  OpenAI-compatible API).** Configurable under **Settings → Language → PDF
  translation engine**; translates multi-block batches with full document
  context, natural idiomatic tone, and verbatim emails/URLs/numbers/codes —
  100% offline & free with local Ollama (e.g. `qwen2.5:7b` at
  `http://localhost:11434/v1`) or with any OpenAI-compatible HTTPS API key.

## 0.61.8

High-quality NLLB-200 offline translation engine, entity protection, and deeper PDF layout fidelity.

- **High-Quality Universal Offline Model — Meta NLLB-200 (`nllb-200`).**
  Downloadable with one click from **Settings → Language Packs**
  (**High-Quality Universal (NLLB-200 · 200 languages)**, ~615 MB streaming
  download, zero new dependencies). Automatically preferred over Argos
  OpenSubtitles models whenever installed for direct 200-language translation
  with much stronger accuracy on business, formal, and technical PDFs.
- **True content-stream text removal & deeper layout fidelity.** Removes
  original `BT … ET` text operators via `pikepdf` (preserving all images,
  logos, tables, borders, shadings, and gradients with zero whiteout patches
  and zero ghost text on Copy/Find), adds `pdfplumber.find_tables()` cell-aware
  bounds, and preserves `left`/`center`/`right` alignment and `bold`/`italic`
  font styles.
- **Verbatim entity protection.** Keeps standalone data cells verbatim and
  protects inline emails, URLs, phone numbers, currency amounts, ISO dates,
  and reference codes using collision-free numeric sentinels with an automatic
  translate-around fallback.

## 0.61.7

Major offline PDF translation upgrade: layout preservation, fluent CJK/neural quality, BPE tokenizer support, and one-click language pack downloads.

- **Layout-preserving PDF translation (`in-place` and `side-by-side`).**
  Original pages stay intact — preserving images, logos, tables, borders,
  colored banners, multi-column layouts, and heading font sizes — while
  translated blocks are overlaid in-place with sampled background colors,
  contrast checks, and CJK-aware auto-fit wrapping.
- **Major neural translation quality fix.** Eliminated `__ZTOK__` placeholder
  hallucinations on neural MT models, stripped leaked SentencePiece `▁`
  markers, added smart PDF paragraph/heading reflow, upgraded CTranslate2
  decoding (`beam_size=4`, `replace_unknowns=True`, `no_repeat_ngram_size=4`),
  and replaced ASCII-only wrapping with CJK-aware line wrapping.
- **Inline 1-click language pack download on the Translate PDF page.**
  Translating to a language whose pack isn't downloaded yet now offers a
  **Download pack (…) & translate** button directly on the page.
- **Automatic runtime self-healing & Argos 1.9 BPE support.** Automatically
  installs `ctranslate2` + `sentencepiece` when missing in non-frozen installs
  (respecting Offline mode and enterprise policies) and adds built-in
  subword-nmt `bpe.model` tokenizer support (e.g. Spanish → English).

## 0.61.6

Offline translation runtime fix and codebase solidification.

- **Offline translation runtime included in installer and in-app updater.**
  Fixed `Install ZPDF.cmd` (`install.ps1`), the in-app updater (`update.py`),
  and the lockfile generator (`lock.py`) to install `[app,ocr,translate]`
  (`ctranslate2` + `sentencepiece`) automatically with graceful fallback, and
  verified both modules inside the standalone app `--self-test` across Windows,
  macOS, and Linux.
- **Resource hygiene & zero-warning `pikepdf` opening.** Fixed an unclosed file
  handle in `redact.py`, added `core.open_pikepdf()` to eliminate spurious
  password warnings on unencrypted PDFs, and replaced deprecated Pillow
  `Image.getdata()` usage.
- **Static code-quality guardrails.** Added `tests/test_code_quality.py` to
  permanently enforce clean AST, zero bare `except:`, zero mutable defaults,
  zero unclosed inline `open()` calls, zero `shell=True`, and release metadata
  consistency.

## 0.61.5

Major performance optimizations for permanent content redaction and in-place
text editing (zero accuracy or security trade-offs).

- **3.8× faster content redaction (`redactcontent.py`).** Added a horizontal
  vertical-span fast-path in `_text()` to skip per-glyph matrix/quad checks on
  lines outside redaction boxes, added global bounding-box rejection to
  `_Areas` / `_center_in()`, and streamlined `_chars()` and `_same_places()`.
  50 dense pages dropped from `7.54s` to `1.97s` (`3.82×` faster), and 100-page
  `benchmark.py` RSS fell from `98 MB` to `62 MB`.
- **3.4× faster text editing (`textedit/rewrite.py`, `glyphs.py`).** Replaced
  per-character `get_text_range(i, 1)` FFI/UTF-16 decoding with direct
  `FPDFText_GetUnicode` (preserving PDFium `0x0002` line-end hyphen handling),
  cached `pypdfium2.raw`, reused `ctypes.c_double` origin buffers, and switched
  `_intact()` to `(0, 0)`-first neighbor lookup. 10 dense pages dropped from
  `1.87s` to `0.54s` (`3.45×` faster).

## 0.61.4

Windows standalone double-click bundle publishing, trimmed Linux packages,
multi-core process-pool batching, and Homebrew / winget manifest templates.

- **Windows standalone release asset (`.github/workflows/build-app.yml`).**
  Releases now publish `ZPDF-<version>-windows-standalone.zip` (+ `.sha256`)
  alongside the lightweight Python-installer `ZPDF-<version>-windows.zip`,
  macOS `.dmg`/`.zip`, and Linux `.deb`/`.zip`.
- **Trimmed Linux & standalone bundles (`packaging/build_app.py`,
  `packaging/build_linux.py`).** Excluded unused dev/test and Qt3D/Multimedia/
  Sql/Pdf modules, pruned non-essential `qtwebengine_locales` packs on Linux,
  and declared `Depends: libgl1, libegl1, libnss3, libxkbcommon-x11-0` in the
  `.deb` package metadata.
- **Multi-core batch processing (`core.py`).** Added `processes=True` mode to
  `core.batch_run()` using a spawned `ProcessPoolExecutor` with bounded
  concurrency, cancellation, and per-item error isolation to bypass the Python
  GIL on CPU-bound PDF batches.
- **Package-manager templates (`packaging/homebrew/zpdf.rb`,
  `packaging/winget/Tempura446.ZPDF.yaml`).** Added ready-to-use Homebrew Cask
  (macOS) and winget portable (Windows) manifest templates.

## 0.61.3

First full three-platform release (Windows, Linux, and macOS) with macOS
compatibility fixes and automated multiplatform package publishing.

- **Multiplatform release publishing (`.github/workflows/build-app.yml`,
  `.github/workflows/tests.yml`).** Releases now automatically publish Windows
  (`.zip`), Linux (`.zip` and `.deb`), and macOS (`.dmg` and `.zip`) packages
  with `.sha256` checksums (and optional `.sig` Ed25519 signatures) to
  `Tempura-446/ZPDF-releases`, backed by full `pytest` CI across Ubuntu,
  Windows, and macOS.
- **macOS compatibility fixes (`textedit/rewrite.py`, `gui.py`, `watch.py`).**
  Resolved PDF Base-14 standard fonts (`Times`, `Helvetica`, `Courier`) before
  macOS system fonts so text editing matches Windows and Linux consistently;
  added `ps`-based browser-profile process cleanup on macOS where `/proc` is
  absent; and included `st_ctime` in `watch._arrived()` for macOS APFS
  backdated-mtime handling.

## 0.61.2

Comprehensive security, reliability, and audit remediation across administrator
policies, security profiles, recursive redaction verification, process
isolation, attachment extraction, and local HTTP server hardening.

- **Administrator policy enforcement (`policy.py`, `ocr.py`, `i18n.py`,
  `escl.py`, `update.py`, `certsign.py`, `server/reader_routes.py`,
  `ZPDF.adml`).** Wired production enforcement across all 7 administrator
  policies (`disable_network`, `disable_updates`, `disable_password_removal`,
  `require_make_safe_on_open`, `force_security_profile`, `fips_mode`, and
  validated `trust_list_paths` with symlink/traversal guards merged into
  digital signature verification). Clarified FIPS algorithm-policy wording.
- **Security Profile password-removal fix (`secprofiles.py`, `core.py`).**
  Closed the `_internal=True` policy bypass in `core.decrypt()` and enforced
  `disable_password_removal`, `force_security_profile`, `require_make_safe_on_open`,
  and atomic output writes in `secprofiles.apply_profile()`.
- **Recursive attachment redaction verification (`redact.py`).** Upgraded
  `check_redacted()` with format-aware inspection of embedded PDFs, Office
  Open XML documents (`.docx`, `.xlsx`, `.pptx`), and ZIP archives under
  depth, entry-count, decompressed-size, and compression-ratio limits; flagged
  encrypted, oversized, and unsupported binary attachments as
  `verification_incomplete`.
- **Process isolation & attachment extraction hardening (`isolation.py`,
  `core.py`).** Fixed the multiprocessing pipe-buffer deadlock on multi-megabyte
  payloads by draining `out_q` before `proc.join()`, added cancellation support
  and guaranteed handle/process cleanup, stripped Unicode bidi/control/drive
  prefixes from extracted attachment names, blocked symlink escapes, and added
  a 250 MB aggregate attachment extraction cap.
- **Update, HTTP server, audit log & OCR fixes (`update.py`, `relsign.py`,
  `gui.py`, `server/common.py`, `auditlog.py`, `ocr.py`).** Hardened update
  zip extraction against symlinks/zip-bombs/corrupt archives and added downgrade
  protection; validated `Content-Length` headers against negative/malformed
  values; added recursive sensitive-key redaction to `auditlog._clean_details()`;
  and vectorized `ocr._looks_grey()` with NumPy (removing Pillow 14
  `Image.getdata()` deprecation).

## 0.61.1

Source-code security hardening and desktop GUI bug fixes following a full
backend and frontend audit.

- **In-place compression safety (`core.py`).** Fixed an in-place
  `compress(doc, doc)` edge case where an already-optimal PDF could be
  overwritten before comparing sizes; now writes to a temporary file first and
  keeps the original intact whenever compression does not shrink it. Also
  validates non-numeric `target_mb` input cleanly in the GUI.
- **Audit log & profile export hardening (`auditlog.py`, `redact.py`,
  `secprofiles.py`).** Added cross-process file locking (`fcntl.flock` /
  `msvcrt.locking`) and owner-only `0o600` permissions on `audit.jsonl`,
  neutralized CSV formula-injection prefixes (`=`, `+`, `-`, `@`) in exported
  audit and redaction logs, stripped stored passwords from exported security
  profile JSON files, and switched custom profile saves to atomic private
  writes.
- **Enterprise policy & pypdf 8.0 compatibility (`update.py`, `outline.py`).**
  Enforced `disable_updates` / `disable_network` policies in `update.check()`,
  and migrated `add_outline_item()` calls to keyword arguments ahead of
  pypdf 8.0.
- **GUI live redaction preview, Reader integrity toasts & Settings audit log
  (`gui.html`, `tools.js`, `reader-save.js`, `settings.js`).** Fixed live
  striped match previews in **Redact** for all regulated presets (`SSN`, `EIN`,
  `UK NI`, `IBAN`, Luhn `credit_card`) and custom regular expressions; surfaced
  Reader text-edit visual-cover and visual-check warnings from `X-Pdfm-Summary`
  and `X-Pdfm-Visual`; added **Security audit log & policies** (View / Verify
  integrity) to Settings; and locked Update controls in Settings when disabled
  by administrator policy.

## 0.61.0

Enterprise security controls, major functional depth upgrades across
compression, comparison, redaction, and Excel conversion, and full Release
Candidate validation.

- **Security Profiles, Dynamic Print Watermarks & Tamper-Evident Audit Log
  (Security Phase 7, `secprofiles.py`, `auditlog.py`).** Added named Security
  Profiles (`Confidential`, `Internal`, `Public Share`, plus custom
  exportable/importable profiles) available in both the desktop GUI
  (**Security profile** tool) and CLI (`pdfm profile`), dynamic traceable
  print/export watermarks (`{user}`, `{date}`, `{time}`, `{datetime}`,
  `{file}`), and a local SHA-256 hash-chained audit log (`pdfm audit` +
  `GET /api/audit`).
- **Enterprise Policies (GPO/ADMX), FIPS Mode, Offline Updates & Process
  Isolation (Security Phase 8, `policy.py`, `isolation.py`).** Added
  precedence-ordered administrator policy loading (`HKLM\Software\Policies\ZPDF`
  > `HKCU` > JSON policy file) with Windows Group Policy/Intune `ZPDF.admx` /
  `ZPDF.adml` templates, FIPS-approved cryptography enforcement, password-removal
  locking in `core.decrypt()`, offline local/UNC update channel discovery,
  `pdfm policy show` + `GET /api/policy`, and spawned child-process
  timeout/memory isolation (`isolation.run_isolated()`).
- **Smart Target-Size Compression (`core.compress()`, GUI & CLI).** Added
  `--target-mb` / **Target file size in MB** quality-preserving auto-tuning
  ladder, a no-bloat guarantee (`keep_smaller=True`) so already-optimal PDFs
  never grow larger, and structured before/after savings statistics.
- **Comparison Depth (`compare.py`, GUI & CLI).** Added `both`, `text`, and
  `visual` comparison modes, JSON-safe `compare_summary()` without PDF
  generation, detailed changed/added/removed/reordered/low-confidence summary
  counts, and optional JSON diff export (`pdfm compare --mode --json`).
- **Regulated Redaction in GUI (`redact.py`, `operations.py`).** Exposed US SSN,
  US EIN, UK National Insurance, EU/International IBAN, Luhn-verified payment
  cards, custom regular expressions, FOIA/GDPR reason-code stamps, and
  downloadable JSON/CSV redaction logs directly in the desktop app.
- **PDF → Excel Auto-OCR & Styling, plus Reader Text-Edit Integrity Headers
  (`tables.py`, `reader_routes.py`).** Added automatic OCR fallback (`auto_ocr`)
  when extracting tables from scanned PDFs, subtle header shading and cell
  borders in `.xlsx` output, and `X-Pdfm-Summary` / `X-Pdfm-Visual` integrity
  headers from the Reader text-edit endpoint.
- **Release Candidate Validation (`docs/RC_VALIDATION_REPORT.md`).** Verified
  across Linux (`Python 3.10`, `3.12`, `3.13`), Windows (`Python 3.13`, E2E),
  CodeQL, 500-page performance benchmarks, wheel clean-venv install, and
  failure/recovery regression tests (`tests/test_rc_failure_recovery.py`).

## 0.60.4

Major six-phase engineering hardening release covering permanent redaction
security, text-editing integrity, scanned & digital PDF comparison, security and
reliability hardening, streaming performance, and release-readiness parity.

- **Redaction security remediation (Phase 1, `redact.py` & `redactcontent.py`).**
  Coordinate-based redactions now scrub matching bookmark titles, annotations,
  form fields, and structure-tree `/ActualText`/`/Alt`, verify the written file
  for any surviving copies, and safely handle nested Form XObjects and content
  streams.
- **Text editing integrity (Phase 2, `textedit/`).** Added
  `require_permanent=True` (refusing fallback to visual cover-ups when text
  operators cannot be cleanly removed) and `verify_visual=True` (before/after
  crop verification to detect clipping or overlap), alongside improved font and
  layout safety.
- **PDF comparison engine improvements (Phase 3, `compare.py`).** Multi-signal
  page alignment (text, visual perceptual hashes, dimensions) with confidence
  scores for digital, scanned, and mixed PDFs; streaming memory management for
  large documents; and aspect-ratio-preserving visual diffs when page sizes
  differ.
- **Security & reliability hardening (Phase 4).** Hardened password/encryption
  handling, certificate/signature validation (`certsign.py`), safe attachment
  extraction against path traversal and symlinks, temporary-file cleanup, and
  secret scrubbing in diagnostic logs (`applog.py`).
- **Performance & scalability (Phase 5, `redact.py`, `core.py`,
  `packaging/benchmark.py`).** Streaming one-page-at-a-time raster redaction
  (`_iter_render_redacted()`, cutting 30-page 150-DPI render RSS by 75% and
  speeding up end-to-end raster redaction by 15.6%), bounded concurrent
  `core.batch_run()` with per-item error isolation and cancellation, and a
  reproducible CLI benchmark harness (`packaging/benchmark.py`).
- **Product quality & CLI/GUI parity (Phase 6).** Synced `requirements.txt` with
  `pyproject.toml` (`numpy>=1.24`), added the `translate` extra to third-party
  license notices (`notices.py`), unified secret-safe OS error formatting across
  CLI and GUI (`applog.format_user_error()`), added CLI warnings when mutating
  digitally signed PDFs, and published `docs/RELEASE_READINESS.md`.

## 0.60.3

Complete offline PDF translation out of the box: bundles the CTranslate2 +
SentencePiece runtime, adds one-click hybrid language pack downloads in Settings
and the Reader, automatic English pivoting, and full Unicode/CJK PDF font
rendering.

- **Bundled offline translation runtime.** Added `ctranslate2` and
  `sentencepiece` (CPU-only wheels) to ZPDF's optional `translate` / `all` /
  `build` extras and standalone PyInstaller builds so offline translation works
  immediately after downloading a pack.
- **Hybrid one-click language pack catalog & downloader (`i18n.py`).** Settings
  → Language now includes a filterable **Download language packs…** browser that
  fetches curated SHA-256-verified packs (`zh-Hans`, `es`, `fr`, `de`, `ja`,
  `ko`, `ar`) from `ZPDF-releases` (`langpacks-v1`) and falls back to Argos
  Translate's open-source model index for 30+ additional languages. Manual file
  install also supports `.argosmodel` archives directly.
- **Reader one-click download, English pivoting & Unicode PDF fonts
  (`translate.py`).** Expanded Translate PDF and Reader **Translate this page…**
  to 18 languages, added a one-click **Download pack now** button when a pack is
  missing, added automatic `src → en → dst` pivoting when no direct pair is
  installed, and switched translated PDF pages to Unicode-capable fonts via
  `fonts.font_for()`.

## 0.60.2

Enterprise digital signatures (PAdES baseline levels, DocMDP certifying
signatures, FieldMDP form-field locking, detached `.p7s` signatures, and ETSI
XML/PDF validation reports), Smart Filing Rules GUI in Settings & Watch
folders, startup UI language auto-loading, and automated GUI wiring checks.

- **PAdES baseline levels, DocMDP & FieldMDP (`certsign.py`).** Choose PAdES
  level `B-B`, `B-T`, `B-LT`, or `B-LTA` (ETSI EN 319 142) when signing with a
  Digital ID, certify documents with DocMDP permissions (`no-changes`,
  `fill-and-sign`, or `fill-sign-comment`), lock form fields upon signing via
  FieldMDP (`lock_fields`), create and verify detached CMS/CAdES `.p7s`
  signatures (`sign_detached()` / `verify_detached()`), and export signature
  validation reports as plain text (`.txt`), formatted PDF (`.pdf`), or ETSI
  XML (`.xml`).
- **Smart Filing Rules in Settings & Watch folders.** Added the **Auto-rename &
  file results using Smart Filing Rules** toggle when adding a Watch folder,
  plus a **Smart filing rules** section in Settings to load starter rules
  (Invoices, Receipts, Contracts, Statements) and preview rules against any
  sample PDF.
- **Startup UI language & Translate icon.** Saved interface languages now load
  automatically at startup (`ZpdfI18n.load()`), and the **Translate PDF
  (offline)** tool has its own dedicated icon in the sidebar and command
  palette.

## 0.60.1

Wires offline PDF translation and language pack management into the desktop
GUI, fixes locale bundling in standalone PyInstaller builds, and adds Linux
and macOS desktop packaging & unified 3-OS release updates.

- **Offline translation & language packs in the GUI.** Added **Translate PDF
  (offline)** under **Convert** tools (side-by-side PDF, plain text `.txt`, or
  in-place PDF), **Translate this page…** in the reader's **More** menu and
  command palette (`Ctrl+K`), and **Settings → Language & offline packs** to
  switch interface language and install/remove `.zip` language packs.
- **Standalone PyInstaller locales fix.** Bundled `pdfmanager/locales` in
  `packaging/build_app.py` so standalone builds include all built-in starter
  locales (`en`, `zh-Hans`, `es`, `fr`, `de`).
- **Linux & macOS builds and 3-OS updater.** Added Linux desktop integration
  (`.desktop` entry, Nautilus/KDE actions, distro hints, `.deb` and AppDir
  packaging, SANE `scanimage` scanning), macOS `.app` `Info.plist` PDF
  associations, unsigned `.dmg` packaging + Finder Quick Actions, and OS-aware
  release asset selection in `update.py`.

## 0.60.0

Full offline Language Packs & Translation suite (UI internationalization,
starter locales, offline PDF translation in three output modes, and
language-pack OCR models and multilingual Search by Meaning), plus a CAD
form-detection fix.

- **Starter UI locales & language pack builder.** Added built-in starter
  locales for Simplified Chinese (`zh-Hans`), Spanish (`es`), French (`fr`),
  and German (`de`), catalog validation (`validate_catalog()`), RTL layout
  support, and `packaging/build_langpack.py` to build SHA-256 verified
  `ZPDF-lang-<code>-<version>.zip` packs with optional OCR and meaning-search
  assets.
- **Offline PDF translation engine.** Translate PDFs completely offline via
  CTranslate2 / SentencePiece or Argos Translate models in installed language
  packs using `pdfm translate <input.pdf> --to <lang>` or `POST /api/translate/page`
  for single-page reader translation. Supports three output modes:
  `side-by-side` (double-width facing pages), `text` (`.txt`), and `in-place`
  (translated PDF with annotation/widget text translation), while preserving
  URLs, emails, dates, numbers, and reference codes (e.g. `INV-2026-118`)
  verbatim.
- **Multilingual OCR & Search by Meaning via language packs.** OCR now resolves
  offline recognition models bundled inside installed language packs
  (`<data_dir>/locales/<code>/ocr/*.onnx`) before attempting any download, and
  accepts both BCP-47 and Tesseract language codes. Search by Meaning merges
  multilingual `words.npz` tables from installed packs and automatically
  refreshes cached page vectors when packs are installed or removed.
- **Bug fix: CAD drawing form banner.** Suppressed the false *"This looks like
  a form to fill in"* banner on plain CAD drawings in `formdetect.py`.

## 0.59.0

Compare documents side by side in the reader, crop pages visually, run print
preflight checks, automatically rename and file PDFs by content rules, and
verify published releases with Ed25519 signatures.

- **Side-by-side split view in the reader.** Press `Ctrl+\` (or choose **Split
  view** from the **More** menu or command palette) to open a second pane
  beside the active document with a draggable divider. View either a second
  open tab or an independent second view of the same document, toggle **Sync
  scroll** with **Lock offset** for revisions with extra cover pages, and
  switch panes with `F6` / `Ctrl+Shift+\`.
- **Interactive visual crop & Booklet live preview.** Choose **Crop pages
  visually…** in the reader to drag a shaded crop rectangle with corner/edge
  handles and live `in` / `mm` / `pt` dimensions across rotated pages, apply to
  this/odd/even/range/all pages with optional permanent trim and full
  Undo/Redo. The **Booklet** tool now also shows a live first-sheet preview
  alongside **Pages per sheet (N-up)**.
- **Print preflight check & safe fixes.** Run `pdfm preflight` or open **Print
  preflight check…** in the reader to inspect effective image DPI, unembedded
  or Type 3 fonts, RGB/CMYK/Spot colour spaces, transparency, page box bleed,
  mixed page sizes, hairline strokes, and rich-black small text — with
  click-to-jump page navigation and one-click **Normalize page sizes** and
  **Add 3 mm bleed + crop marks** fixes.
- **Smart auto-rename & filing rules.** Automatically rename and file PDFs
  based on dates, text, metadata, or named regex captures using `pdfm
  auto-file` (dry-run by default, `--apply` with collision suffixes and
  `--undo` rollback), `/api/filing/rules` & `/api/filing/preview`, and optional
  auto-filing on Watch folders.
- **Ed25519 release signing support.** Added `pdfmanager/relsign.py`,
  `packaging/release.py --keygen` / `--sign-key`, and signature verification in
  the built-in updater so signed releases are cryptographically verified before
  installing.
- **Internationalization (i18n) foundation.** Added `pdfmanager/i18n.py`,
  `locales/en.json`, `static/i18n.js`, RTL direction support, and offline
  language pack install/remove endpoints (`/api/i18n/*`) ready for language
  packs.

## 0.58.0

Export PDFs and review comments to Markdown, read hard scans and scanned tables
more accurately and faster with Better OCR, and check OCR results directly in
the reader.

- **PDF to Markdown export.** Convert any PDF (including headings, lists,
  tables, links, and extracted images) to clean Markdown from the **Convert**
  tool or CLI (`pdfm to-md`), with an option to include page markers.
- **Review summary export.** Export annotations and comments from a PDF as a
  structured Markdown review summary, with filters by author, annotation type,
  or page range.
- **Better OCR on hard pages.** Built-in OCR now offers a **Best accuracy**
  model tier alongside **Fast**, with automatic preprocessing (deskew,
  denoise, adaptive contrast, de-shadowing, and resolution upscaling) and
  low-confidence line retries for faint, skewed, or photographed scans.
- **Check OCR mode in the reader.** Open **Check OCR** in the reader to review
  an OCR confidence report, jump directly to low-confidence ("unsure") words
  highlighted on the page, and fix misread text in place.
- **Scanned tables & numeric refinement.** OCR now detects ruled and borderless
  tables on scanned pages, reads cells individually, refines numeric/currency
  cells, and feeds exact table grids into **PDF → Excel** and **PDF → Markdown**.
- **Faster multi-page OCR.** Multi-page OCR runs across parallel worker
  processes, skips blank or duplicate pages automatically, and uses DirectML
  GPU acceleration when available with seamless CPU fallback.
- **Architecture modularization.** Split ZPDF's largest monolithic files
  (`gui.py` → `pdfmanager/server/`, `gui.html` → declarative HTML + `static/app.css`
  and focused JS controllers, `static/reader.js` → `reader-nav.js` /
  `reader-render.js` / `reader-save.js`, `forms.py` → `pdfmanager/forms/`,
  `textedit.py` → `pdfmanager/textedit/`) with zero behaviour changes to keep
  upcoming UI and security tracks clean and maintainable.

## 0.57.0

Author and preview fillable forms in the reader, convert CAD drawings (DXF/DWG)
and manage drawing sheet sets, and reflow multi-line paragraphs around pictures,
across pages, and in CJK and right-to-left scripts.

- **Prepare form mode in the reader.** Open **Prepare form** from the
  reader's **More** menu, the command palette (`Ctrl+K`), or the
  "Make it fillable" bar on a printed form. A toolbar opens with nine field
  types — **Text**, **Multi-line**, **Checkbox**, **Radio**, **Dropdown**,
  **List**, **Date**, **Signature**, and **Button** — and every field on the
  page is shown with its name and corner handles.
- **Draw and arrange fields directly on the page.** Click or drag to add a
  field, click to select (`Shift+click` or drag a box to select several),
  drag to move, drag a corner handle to resize, nudge with the arrow keys
  (`Shift` for larger steps), `Ctrl+D` to duplicate, `Ctrl+C` / `Ctrl+V` to
  copy and paste (including onto another page), and `Delete` to remove.
- **Find fields from the bar.** **Find fields** runs ZPDF's automatic field
  finder and adds what it discovers as ordinary, editable fields. Every edit
  supports **Undo**, **Redo**, the unsaved dot, and **Save**.
- **List boxes and push buttons.** Form creation now also supports open
  **List** boxes and **Button** fields in both the reader and the Python/CLI
  engine.
- **Field properties panel.** Selecting one or more fields in **Prepare
  form** opens their properties in the right-hand panel: **General** (name,
  tooltip, required, read-only, visibility), **Look** (font, size or auto,
  text, fill and border colours, border width, alignment), **Text field**
  (default value, max length, comb boxes, multi-line, password, scroll),
  **Choices** (shown and export values, drag to reorder, default choice,
  allow typing or selecting several), **Checkbox / Radio** (tick style,
  export value, checked by default, radio group), **Format** (numbers,
  currency, percentages, dates, times, phone, ZIP/postcode, custom masks),
  **Validation** (number range or regex pattern), and **Calculation** (sum,
  product, average, min, max with **Pick fields on page**, or a formula).
  Selecting several fields edits what they share.
- **Layout help, guides, and field tables.** Dragging or resizing a field
  snaps to other fields' edges and centres as well as the page's own lines
  (hold `Alt` to move freely). With several fields selected, align edges or
  centres, match width, height, or size, or distribute evenly. With one
  field selected, **Copy to pages** duplicates it across pages (keeping one
  shared name or numbering each copy) and **Make a table** turns it into a
  grid of numbered fields.
- **Tab order and the Fields list.** In **Prepare form**, every field shows
  its tab-order number, and a **Fields** tab in the left sidebar lists all
  fields page by page. Click a field to select it, double-click to rename
  it in place, drag rows to change the tab order, or reset a page with
  **Order by rows** or **Order by columns**.
- **Preview and Check form.** Switch on **Preview** in **Prepare form** to
  try filling in the form with formatting, validation, and calculations
  running live; values typed in Preview are discarded when you switch back
  unless you choose **Keep as default values**. **Check form** scans for
  missing tooltips, duplicate names with different types, off-page or
  overlapping fields, required read-only fields, and calculations that name a
  missing field, and clicking any item selects that field on the page.
- **Form data and Fill many.** Use the **Data** menu in **Prepare form** to
  **Clear form**, **Import data** from FDF, XFDF, CSV, or JSON, or **Export
  data** in any of those formats. A new **Fill many (mail merge)** tool fills
  one PDF per row of a CSV, Excel (`.xlsx`), or JSON file and names each
  output from a chosen column.
- **Buttons, actions, and show/hide fields.** Give any **Button** field a
  label and an action — **Reset form** (all or chosen fields), **Print**,
  **Go to page**, **Open web link**, or **Send by email** (`mailto:` with
  FDF data or PDF attachment choice) — and configure any **Checkbox** to show
  or hide chosen fields when ticked. Both work live in the reader and are
  saved as standard PDF actions.
- **PDF ↔ DXF and DWG drawings.** Export vector PDFs to CAD DXF (**PDF → DXF**
  and `pdfm to-dxf`), preserving drawing scale (`/VP /Measure` or an explicit
  scale), layers, colours, line weights, arcs/circles, solid fills, and text.
  Convert `.dxf` drawings (and `.dwg` when the free ODA File Converter is
  installed) to layered, scaled PDFs with **Drawing → PDF**, `pdfm from-dxf`,
  or by opening a `.dxf` / `.dwg` directly in the reader.
- **Drawing sheet sets.** Split a multi-sheet drawing PDF into one file per
  sheet named from its title block (e.g. `A-101 Floor plan.pdf`), set PDF page
  labels to the sheet numbers, automatically add clickable links from detail
  references (`2/A-102`, `SEE A-201`) to the target sheet, and compare two
  revisions of a set matched by sheet number rather than page order
  (**Sheet sets**, **Compare sheet sets**, and `pdfm sheet-split` /
  `sheet-name` / `sheet-link` / `sheet-compare`).
- **Paragraph shape when rewriting text.** Rewriting a bulleted or numbered
  item now leaves the bullet or number in place and wraps continuation lines
  under the text; first-line indents are preserved; `Shift+click` on a second
  paragraph edits consecutive paragraphs together (with `Enter` starting a new
  paragraph at the document's own spacing and `Shift+Enter` making a soft line
  break); and unchanged leading lines before the first edited word are left
  byte-identical in the page's content stream.
- **Flow around pictures and onto the next page.** Rewritten paragraphs wrap
  shorter beside pictures or filled boxes and expand to full column width
  below them without overlapping. When a paragraph overflows the bottom of a
  page, the reader offers **Continue on the next page** (flowing overflow
  lines to the top of the next page's column and shifting its text down) or
  **Add a page** on the last page, alongside **Shrink to fit**.
- **CJK and right-to-left paragraph reflow.** Chinese, Japanese, and Korean
  paragraphs wrap between characters while keeping kinsoku punctuation rules
  and Latin words intact, and justify by spreading inter-character spacing.
  Right-to-left paragraphs (Hebrew and Arabic) fill lines right-to-left using
  Unicode bidi visual ordering while keeping embedded Latin words and numbers
  left-to-right.

## 0.56.0

Switch a form's checkboxes, radio buttons and signature fields, and change
a text field's format and total.

- **More type switches on a form's own fields.** In **Make fillable**, a
  tick box can become a group of radio buttons (one button to start with,
  keeping its name, place, whether it's required and whether it's ticked),
  and a group of radio buttons can become separate tick boxes named from
  their values. An unsigned signature field and a text box, multi-line
  text box, dropdown or list can also become one another (a filled field
  is emptied when it becomes a signature, and ZPDF says so; a signed
  signature field stays locked).
- **Format, range and total of an existing text field.** **Make fillable**
  now reads a text field's format (numbers, amounts, money, percentages,
  dates, US phone numbers and ZIP codes), its minimum and maximum, and its
  calculation (sum, product, average, minimum, maximum or a simple formula
  over field names such as `Qty * Price`), shows **Format** and **Sum of**
  when the field is clicked, and writes changes to them with the form's
  calculation order kept right. A custom script that isn't one of these is
  labelled *Custom script* and left untouched unless replaced.
- On the command line, a change in the `--edit` file also takes `"format"`,
  `"decimals"`, `"separators"`, `"currency"`, `"currency_after"`, `"date"`,
  `"min"`, `"max"` and `"calculate"`.
- **Feeder scanning that finishes the stack without stopping after a page or
  two.** **Scan** no longer lets its background scanner check open a
  Windows driver while a scan is starting, reconnects between sheets when a
  driver closes its session after each page, remembers when a driver only
  gives bitmaps, and treats `404` / `409` / `500` or an empty answer after
  the last sheet in the feeder as the end of the stack rather than a
  failure.

## 0.55.0

Turn a form's text box into a dropdown, and back.

- **A field that holds words can become another kind that does.** In
  **Make fillable**, click one of the file's own text boxes and choose
  Dropdown under Type (and give it its choices), or turn a dropdown into a
  text box, or a text box into several lines. It keeps its name, place,
  tooltip and whether it's required, and a total that adds it up still
  does.
- What it's set to is kept when the new kind can hold it: a text box
  filled with "10" stays 10 as a dropdown offering 10. If it isn't one of
  the choices, the field is emptied and ZPDF says so.
- A text box that only took numbers or dates loses that rule when it
  becomes a dropdown: its choices are the rule now.
- On the command line the `--edit` file's `"type"` also takes `"list"` (a
  list that stays open on the page).
- Tick boxes, radio buttons, buttons and signature fields still can't
  become another type.
- Fixed: **Sign** used just after a file opened sometimes placed nothing
  and said "The signature couldn't be placed": turning the picture tool on
  could redraw the page, and the signature was aimed at the page as it was
  before.

## 0.54.0

Send a form field to another page.

- **A field a form already has can go to another page.** In **Make
  fillable**, click the field and choose its new page in the bar ("Page").
  It keeps its place on the paper, its name, its look and what it's set
  to; a total that adds it up still does. If the new page is smaller and
  the field wouldn't be on it, it is moved just far enough to be, and ZPDF
  says so. On its new page it comes last when tabbing through the fields.
- One button of a group of radio buttons can go by itself: the group then
  spans two pages.
- On the command line, a change in the `--edit` file takes `"to_page"`.

## 0.53.0

Scanners that only have a TWAIN driver, through NAPS2.

- **If NAPS2 is installed, ZPDF scans with it.** NAPS2 (free, naps2.com)
  drives scanners that only come with a TWAIN driver, older 32-bit ones
  too. With it on the PC, those scanners show in the Scan page's list
  marked "through NAPS2", and Scan takes their pages: the glass, the
  feeder, both sides, colour, grey or black and white, at the resolution
  you choose. The pages then go through ZPDF's own clean-up and OCR like
  any other scan. Cancel stops NAPS2.
- ZPDF doesn't contain NAPS2 and installs nothing: without it, nothing
  changes. "My scanner isn't listed…" on the Scan page says how to get it.
- Pages scanned this way come in together when NAPS2 has finished, not one
  by one.
- **Not tried on a real TWAIN scanner yet.** It was checked on a PC with
  NAPS2 8.6.0 installed (found where its installer puts it; its words for
  a scanner that's missing or offline are understood), but that PC has no
  TWAIN scanner.
- **The list of scanners no longer waits most of a minute** when a scanner
  Windows knows is switched off or asleep. Windows names its scanners at
  once; asking each whether it has a feeder is now done beside the list.
  Until a scanner has answered, both the glass and the feeder are offered.

## 0.52.0

- **Fixed: ZPDF could close by itself when a PDF with web links was
  opened.** The check for links that show one address and go to another
  read the page's words at the same moment as something else in ZPDF used
  the same part (a thumbnail, another file), which can end the whole app.
  It now waits its turn like everything else.
- **Fixed: with Links on, a click on a link sometimes asked "Open this
  link?"** instead of opening the box to change it: on pages with many
  annotations before the link.

## 0.51.0

Sturdier markups, from the review of the code.

- **A comment can't make a markup unselectable any more.** An ellipse with
  a comment ending like "(2 of 3)", or a line with corners whose comment
  had a degree sign, looked like one of ZPDF's measurements and couldn't
  be selected or changed. Markups drawn from now on say what drew them,
  so their comments don't matter; the quantity schedule uses the same
  mark. Files from before are read as they were.
- Dragging a markup redraws its outline once a frame at most, which is
  smoother on busy pages.
- Inside: one place puts a new annotation on its page, and changing a
  markup no longer adds a spare one and takes it out again.

## 0.50.0

- **Fixed: choosing Highlight, Text or Draw marked the file as changed.**
  On a document that already has a text box (a callout, typed text),
  turning on one of these tools made Save turn red although nothing was
  changed. It no longer does; a real change is still noticed.

## 0.49.0

Nudging a markup is quicker and tidier.

- **The arrow keys move a markup the way a drag does.** Its outline moves
  at once with every press; the file is written once, when the keys have
  rested for a moment. A held key, or a run of presses, is one change and
  one step for Undo, however long the file takes to write
  (also through the pause before a held key starts repeating).
- Anything else you do meanwhile (a colour, Save, another tool, a press on
  the page, Esc) writes the presses made so far first.
- **Less waiting on large files:** after a markup is changed, the file is
  no longer sent a second time just to list its markups again.

## 0.48.0

Stamps of your own.

- **Your stamps.** In the reader's Stamp list, "Your stamps…" opens a box
  where you add stamps with your own words (CHECKED, PAID, ISSUED FOR
  TENDER…), each with its colour and with or without your name and the
  date under it. Change or remove them there too.
- They show in the Stamp list under **Yours**, next to the built-in ones,
  and are still there the next time ZPDF starts. Choosing one sets "Name
  and date" the way it was saved; a stamp you just added is the one
  chosen.
- "Your own words…" is still there for words you need once.
- ZPDF keeps up to 30 stamps of your own, in its settings on this PC.
- A stamp with many words is placed wider with a click, so the words can
  be read.

## 0.47.0

Change a markup after you've placed it.

- **Select, move and reshape.** With Shapes, Stamps or Note on in the
  annotate bar, press on a markup you made with ZPDF: handles appear on its
  corners, ends or points. Drag a handle to reshape it, or the markup
  itself to move it. It works for rectangles, ellipses, clouds (also the
  ones through clicked points), lines, arrows, lines with corners,
  callouts (the point it's about and its box), dimensions, stamps and
  notes.
- **A dimension follows its ends:** drag an end and its length is worked
  out again from the sheet's scale; the end snaps onto the drawing's lines
  as when you drew it (hold Alt to put it exactly where you let go).
- **Keys:** Delete removes the selected markup, the arrow keys nudge it (a
  point at a time, ten with Shift), Esc lets go. Each change is one step
  for Undo.
- **Colour and thickness** from the bar change the selected markup.
  Double-click a callout (or press Enter) to change its words.
- A changed markup is still the same annotation: its place in the page's
  order, who made it and when, and the replies to it are kept.
- Markups made by other programs, measurements and highlights aren't
  changed this way yet; ZPDF says so if you try through the command set.
- **From the check on Windows:** an arrow key held down moves the markup
  by every press (they're added up while the file is written) and no
  longer scrolls the page; a double-click on a callout opens its words
  (Enter does too); the bar's bin removes the selected markup; a cloud is
  taken by a press on its bumps, however thick its line; Esc while an
  arrow key is held lets go for good.
- **From a review of the code:** a drag that starts on a markup that
  isn't selected draws a new shape (a dimension continued from another's
  end), and a click selects it; the arrow keys move a markup the way the
  page is seen, also on a turned page; nudges made one after another are
  one step for Undo; a comment written on a dimension no longer becomes
  its measurement when it's moved; a dimension keeps the scale it was
  drawn with when an end is dragged; comments brought in from a file
  aren't offered for redrawing as ZPDF's own; keys pressed in the page
  list are no longer also the selected markup's; a markup stays selected
  through Undo and Redo; its keys work straight away wherever the focus
  was before it was pressed (a button of the bar, the page list).
- **Web page to PDF:** when the browser stalls and the page is tried
  again, the second try uses another installed browser (Chrome after Edge,
  say) when there is one.
- **Fixed: a network scanner failing with "error 0x80190199".** Through
  Windows' own driver for network scanners, that code is the scanner
  answering "these settings conflict". ZPDF now tries once more with only
  the settings that matter (in grey where the scanner has no black and
  white, made black and white afterwards), and if the scanner still
  refuses says so in words and points to its "(network)" entry in the
  list, where ZPDF talks to the scanner directly.
- **Fixed: a scan failing with "'NoneType' object has no attribute
  'GetType'".** A scanner's driver sometimes hands back nothing (no
  picture, no error). ZPDF now tries once more, and if that gives nothing
  either says so in plain words.

## 0.46.0

Watch folders keep working with the window closed.

- **In the background.** While a folder is being watched, closing ZPDF's
  window can leave ZPDF running as an icon in the notification area (by
  the clock). It keeps doing each file that arrives and says so from the
  icon. The first time, ZPDF asks; the answer is kept and can be changed
  under Settings → Watch folders (ask me, keep watching, or close).
- **The icon:** a click opens ZPDF again; a right-click offers Open ZPDF,
  Pause watching (or Resume) and Quit ZPDF. Opening a PDF from Explorer
  brings the window back too.
- **Start when I sign in** (Settings → Watch folders): ZPDF starts with no
  window, only the icon, so scans are picked up from the moment you sign
  in. It's off unless you turn it on, needs no administrator rights, shows
  in Windows' own Startup apps list, and is removed when ZPDF is
  uninstalled.
- Nothing runs in the background unless a folder is being watched: with no
  watch folder, or all of them paused, closing the window closes ZPDF as
  before.
- Unsaved work is never lost to this: it stays open in the hidden window,
  and Quit from the icon brings the window back to ask about it.
- While ZPDF keeps watching, Settings → Watch folders also offers **quit
  ZPDF now**, besides Quit in the icon's menu.
- For now this needs Windows and ZPDF's own window (the normal installed
  app). Where it can't work, Settings says why.
- Tried on a real PC: hiding to the icon and coming back, files done while
  hidden, opening a file from outside, start at sign-in, the icon after
  Explorer restarts. Not yet tried on one: the icon's right-click menu and
  quitting with unsaved work.

## 0.45.0

Any scanner at all: let it save into a folder, and ZPDF does the rest.

- **My scanner isn't listed…** on the Scan page. Every scanner can save
  its scans into a folder, with its own Scan button or the program that
  came with it. Tell ZPDF which folder, and each scan put there becomes a
  finished, searchable PDF in a "Done" folder inside it. It works with
  scanners ZPDF can't drive itself, such as ones that only have a TWAIN
  driver.
- **Pictures too.** A watch folder can now take the picture files scanners
  write (JPEG, PNG, BMP, and TIFF with one or many pages), not only PDFs.
  Each becomes a PDF at the scan's own size first.
- **Pages scanned together become one document.** For a scanner that
  saves a file per page: the files of one stack, arriving within a few
  seconds of each other, make one PDF, in the order they're named. ZPDF
  waits until nothing new has come before it puts them together.
- A file among them that isn't a picture is left out and said by name; the
  pages around it still make their document. "Together" goes by when the
  files came into the folder, so scans copied in by hand group the same
  way as ones a scanner writes.
- Both are also options of any watch folder under Settings → Watch folders
  (off unless you tick them, so folders you already watch behave as
  before).
- Watching still happens only while ZPDF is open; running in the
  background from the tray is the next step on the plan.

## 0.44.0

Scan from a network scanner with no driver.

- **Network scanners (AirScan).** The Scan page now also finds scanners
  and multifunction printers on your network that offer driverless
  scanning (AirScan, also called eSCL or Mopria Scan: most network devices
  of the last ten years). They're listed next to the scanners Windows
  knows, marked "(network)", and scan the same way: the glass or every
  page in the feeder, greyscale, colour or black and white.
- **Both sides of each sheet**, for a network scanner whose feeder turns
  the page.
- **Add a network scanner…** by its address (for example 192.168.1.40),
  for networks where the search finds nothing. It's remembered; **Forget
  this one** takes it off the list.
- Scanning no longer needs Windows' own scanner support to be working: the
  Scanner choice stays available, and when nothing is found ZPDF says why
  scanners with a Windows driver can't be used, if they can't.
- What goes wrong is said plainly: the feeder is empty, paper is jammed,
  the cover is open, the scanner is busy or didn't answer.
- `pdfm scan` lists and uses network scanners too; `--add ADDRESS`
  remembers one, `--duplex` scans both sides.
- Everything stays on your network: ZPDF asks the network which scanners
  are there only when you open the Scan page, and talks only to the
  scanner you choose. Settings → Privacy → When ZPDF goes online lists it.
- Not tried on a real scanner yet: it's written to the published protocol
  and tested against a simulated device.

## 0.43.0

A quantity schedule (takeoff) from what you measured on a drawing.

- **Quantities from a drawing** (under Convert, and `pdfm quantities
  plan.pdf`): one table of everything measured in a PDF, as an Excel
  workbook or a CSV file. It lists the dimension lines you placed, the
  measurements you put in the PDF, and the measurement annotations other
  programs save (Acrobat, Bluebeam and the like): sheet, kind, group, name,
  quantity, unit, an area's perimeter, the scale, the colour, who placed it
  and when.
- **Totals per group**: "Walls: 126.33 ft", "Flooring: 84.2 m²", the count
  of outlets. In the workbook they're on their own sheet, and every number
  is a number with its unit in the next column, so you can sum and price
  them.
- Each quantity is worked out again from the annotation's own points and
  scale, not copied from its words. A measurement without any scale is
  listed with its words and no number.
- In the reader, the **Measurements** panel's CSV button now saves this
  schedule, and there's an **Excel** button beside it. Both include the
  measurements still only in your list and what's already in the document,
  unsaved changes too.
- An area's cut-outs are now kept in the PDF when measurements are put into
  it, so the area can be worked out again later.
- The scale ZPDF writes with a dimension or measurement now follows the PDF
  standard exactly (the factors for distances and areas), so other programs
  read the same lengths from it. Files from earlier versions are still read
  correctly.

## 0.42.0

What ZPDF leaves on your computer, and a plain list of when it goes online.

- **Settings → Privacy.** *Remember the files I open* is the Recent list,
  where you were in each file, what ZPDF read from it, and the text kept by
  Search a folder. It says how much is kept now. Turn it off and none of it
  is kept from one use to the next.
- **Clear history** empties all of that in one click. Measurements and
  drawing scales you set are your work, not history, and stay.
- **Working files** no longer go into the shared temporary folder. Each
  running copy of ZPDF has its own folder inside ZPDF's data folder; it's
  deleted when ZPDF closes, and what a crash left behind is removed at the
  next start.
- **When ZPDF goes online**: the list is in Settings. ZPDF has no
  telemetry; it connects only to check for a new version (with a switch),
  to download an update or an OCR language when you ask, for a timestamp
  when you sign with a timestamp server, for a web address you turn into a
  PDF, and when you click a link.
- `pdfm privacy` shows the same and takes `--clear-history` and
  `--remember off`.
- **Open with Digital ID** given a mixed list now leaves out the files that
  aren't protected for people (and says so) instead of stopping. A page
  preview of such a file says what it is, not "unknown encryption method".
  A message that starts with a file's name keeps the name as it's written.

## 0.41.0

Protect a PDF for people, not with a password.

- **Encrypt for people** (under Protect): choose the certificates of the
  people who may open the file, and only they can, each with their own
  Digital ID. There's no password to send along, to forget or to be
  guessed. A certificate is the public part of someone's Digital ID: a
  .cer file they can give to anyone. Add your own too (your .cer, or your
  Digital ID with its password), or you won't be able to open the result
  yourself. Acrobat opens these files as well ("certificate security").
- **Open with Digital ID**: a PDF protected this way opens with your
  Digital ID (.pfx or .p12). You get an unprotected copy to read and work
  on; the protected file stays as it is. In the reader, such a file is no
  longer called damaged: it says what it is and offers **Open with my
  Digital ID**; the copy opens in a new tab and is kept only if you save
  it.
- ZPDF says plainly when a certificate can't be used: it has expired, or
  it's for signing only (many Digital IDs have a second certificate for
  protecting files). An ID the file wasn't protected for is refused in
  words: "wasn't protected for Eve Other".
- Every other tool, given such a file, says it's protected for particular
  people and how to open it, instead of failing or trying to repair it.
- **How long a signature stays checkable** is now said with each
  signature in Check signatures: one signed with a timestamp server stays
  checkable after the signer's certificate expires; one without says the
  date after which other programs may stop accepting it.
- Command line: `pdfm encrypt-for doc.pdf --cert ann.cer --cert bob.cer`
  and `pdfm open-with-id doc.pdf --id me.pfx`.
- Not yet: seeing who a file was protected for before opening it, limits
  on printing or copying per person, and signatures that carry their own
  proof of validity for the long term (they need the certificate's issuer
  to be reachable when signing).

## 0.40.0

Stronger protection of your own documents.

- **How strong is that password?** Add password now says so while you
  type: Very weak, Weak, Fair, Good or Strong, with a short reason ("one
  of the most common passwords", "a single word with numbers after it").
  It's an estimate worked out on this computer; nothing is sent anywhere.
  A weak password is still accepted: the choice stays yours. The command
  line says it too.
- **Old, weak encryption is named and upgraded.** PDFs protected years
  ago, or by old programs, use RC4, which no longer protects: the 40-bit
  kind is broken in minutes whatever the password. ZPDF shows "weak
  protection" for such a file (even before its password is typed) and
  offers **Protect it better** (Upgrade protection, under Protect). That
  writes the same file with AES-256, with the same password and the same
  limits on printing, copying and changes. Give the permissions password
  if the file has one, so it's kept as well; with only the password to
  open, the limits stay but their old password no longer lifts them.
- **After Black out, ZPDF checks the result** and says what it found:
  either that nothing redacted is left in the page text, the document's
  details, bookmarks, form values, comments, attached files or the file's
  inner data, that nothing is left under the boxes and that the file
  keeps no earlier version; or, if a redacted word is still somewhere
  (the title, an attachment's name or contents), exactly where, and what
  takes it out.
- **Make safe and signed files.** Taking parts out of a digitally signed
  PDF changes it, so the signature no longer verifies on the safe copy.
  ZPDF now says so, before (in the suggestion) and after (in the result),
  and reminds you that the original is untouched and still verifies. A
  signed file with nothing to take out is copied byte for byte, so its
  signature stays valid.
- **The app window no longer offers to save passwords or remember form
  entries.** Typing a PDF's password brought up the browser's "Save your
  password?" box, and a saved one would have been kept in the app's
  profile. Version 0.22.1 had turned this on for password managers; it is
  off again, together with the remembering of form entries. Keep PDF
  passwords in your password manager yourself.
- `pdfm make-safe --check` and `pdfm upgrade-protection --check` exit
  with code 2 when a file could do harm or is weakly protected, for
  scripts.
- A reader tab is announced as "unsaved" only when it is.
- Still to come in this part of the plan: encrypting for people's
  certificates instead of a password, and signatures with a trusted
  timestamp that stay checkable for years.

## 0.39.0

Safer opening of PDFs you didn't make.

- **ZPDF says what a PDF could do.** When a file is opened, the file panel
  shows a warning chip if it has:
  - scripts, or actions that start a program;
  - attached files that can run (programs, scripts, Office files with
    macros);
  - a form that sends what you type to an internet address;
  - a web address that other programs open as soon as the file opens;
  - links that show one address and go to another;
  - pointers at other files.
  Hover the chip for the details. ZPDF itself never runs a PDF's scripts
  or actions; the warning is about what other programs would do with the
  file and what you might click.
- **Make safe** (suggested first for such a file, and under Protect):
  takes those parts out and keeps the rest: pages, text, comments, forms
  with their own totals and number formats, attachments that can't run.
  It says what it took out. Links that mislead stay, since there's nothing
  to put in their place; see the next point.
- **Links show where they really go.** Clicking a link that leaves the
  document asks first and shows the real address, with the site's name in
  bold, whatever the words on the page say. It also says when the address
  isn't protected (http) or the site's name looks unusual. Turn the asking
  off in the dialog or in the reader's More menu (Ask before opening web
  links). Only web, mail and phone links are opened at all.
- **Saving attachments** (Extract pictures & files) warns about the ones
  that can run on your computer.
- A form's totals and formats made here or in Acrobat aren't counted as
  scripts to fear, and Make safe keeps them working.
- **Fixed: a PDF's own scripts could run in the reader.** The part of the
  reader that makes form totals and number formats work also ran scripts
  kept with the document, its pages or its fields, in a closed sandbox: a
  script could show a message box when the file opened, though it could
  not reach files or programs. Now only a form's own format and total
  functions run, and nothing else in a PDF does. A script dressed up as
  one of those functions (hidden in its arguments) is treated as a script:
  not run, reported, and taken out by Make safe.
- **Fixed: what you typed in one PDF's form was offered in another's.** The
  app window remembered form entries and suggested them in fields of the
  same name in other PDFs. The reader's form fields no longer use that
  memory.
- Command line: `pdfm make-safe file.pdf`, and `--check` to only list what
  the file can do.
- *Remove hidden data* is unchanged: it's for what you send out (details,
  all scripts, all attachments); Make safe is for what you receive.

## 0.38.0

Markups for drawings, in the reader's Annotate bar.

- **Shapes** has a new group, *For drawings*:
  - **Cloud, any shape:** click the corners of what changed; double-click
    or press Enter to close it. The revision cloud follows the outline you
    clicked. (The dragged, rectangular Cloud is still there.)
  - **Line with corners** and **Arrow with corners:** click each point,
    double-click or Enter to finish.
  - **Dimension:** drag from one end to the other. The ends snap to the
    drawing's lines, as measuring does (Alt: no snapping), and the line
    carries the real length from the sheet's scale, with an arrowhead at
    each end. Without a scale it says the length in inches on paper.
  - **Callout:** drag from what you're pointing at to where the words go,
    then type them. They sit in a box with a pointer.
  While drawing click by click, Backspace goes back one point, Esc gives
  up, and Shift keeps a line at a multiple of 45°.
- **Stamps** has a *Drawing review* group: Approved as noted, Revise and
  resubmit, Rejected, For information, Not for construction, with your
  name and the date like the others.
- All of these are standard PDF annotations (a cloudy polygon, a polyline,
  a callout, a dimension line with its measure), so Acrobat, Bluebeam and
  other readers show them and list them as comments. They can be deleted
  from the Comments tab and undone like other markups.
- Words in callouts and dimensions read upright on pages that are turned.
- Not yet: moving or reshaping a markup after it's placed (delete it and
  draw it again), and your own stamps with a colour.

## 0.37.0

The fields a form already has can be changed.

- **Add form fields** used to show a form's own fields striped, to look
  at only. Click one now to **move** it, **resize** it, **rename** it,
  make it **required**, or **delete** it; a text box can be switched
  between one line and several, and a dropdown's choices can be set. New
  fields are added in the same run.
- A renamed field stays unique ("Total" beside another Total becomes
  "Total 2"), and a total that added it up follows the new name. A deleted
  field is taken out of the totals that used it, and the result says so.
- Deleting is only marked until you press Run: the field turns red, and
  its ↶ keeps it after all. It says first when the field is filled in,
  signed, or used by a total.
- A changed field has a ↶ too, which puts it back as it is in the file.
- **Start over** now also clears the fields and boxes drawn on the pages.
- It works on forms from other programs too: a field shown in several
  places (each place moves by itself; the name is shared), names in a tree
  ("person.name"), settings kept on a parent. A resized field is drawn
  again at its new size with its own border and background.
- Changing the fields of a signed file makes its signature show as no
  longer valid; the result says so.
- With several files chosen, their own fields are only shown, as before:
  what's drawn goes on each file, and their fields differ.
- Command line: `pdfm form doc.pdf --edit changes.json`.
- Not yet: changing a field's type (other than text one line/several),
  moving a field to another page, the format and total of a field the
  form already has. An XFA form (Adobe LiveCycle) can't be changed.

## 0.36.0

Make searchable (OCR) reads pages it used to pass over.

- **A scan with an unreadable text layer is read again.** Some scans come
  with a text layer of signs and broken letters, left by an older program.
  ZPDF took such a page for one that already had its text and skipped it.
  It now takes the rubbish layer out and reads the page, without touching
  the page's picture. A layer that reads well is left alone, as before.
- **Scanned pictures on pages of real text are read too:** a figure, a
  pasted screenshot, or a scan with a stamp or page number typed over it.
  The words found are added over the picture; the page's own words are
  kept once. Small pictures (logos, icons) are passed over, and a photo
  adds nothing: a stray mark read in it (a horizon taken for a dash) isn't
  counted as words.
- It says what it did for each such page ("page 3: its text layer was
  unreadable and has been replaced", "page 5: read the text in 2
  pictures").
- Files with many large pictures take longer than before, since each is
  now looked into. This is the built-in engine; the Tesseract engine works
  as it did.

## 0.35.0

A setup window in place of the console.

- **Install ZPDF.cmd now opens a ZPDF Setup window**, in the app's own
  look (light and dark). It says what will be installed and where, with a
  tick box for each choice: the desktop shortcut, "Open with" for PDFs, the
  right-click menu in File Explorer, and Tesseract with Ghostscript.
- **Each step shows as it runs** (Python, ZPDF and its packages, shortcuts
  and menus, the two programs if asked for) with a progress bar, what it is
  doing now, and the full details on request. You can keep using the PC,
  and closing the window doesn't stop the installation.
- **A clear end:** "ZPDF is installed", where to find it, what became of
  Tesseract and Ghostscript, and an **Open ZPDF** button. When something
  goes wrong it says what, at which step, with **Try again** and **Copy
  details**.
- Updating an installed copy says so ("Update ZPDF") and keeps your
  settings.
- Tesseract and Ghostscript are now a box to tick (not ticked to begin
  with); the console asked at the end, with yes as the answer to Enter.
- Everything works from the keyboard, and is read out by a screen reader
  as it progresses.
- The console still does it all with `Install ZPDF.cmd -Console`, in
  automated runs, and where Microsoft Edge isn't there to draw the window;
  its steps are now numbered.

## 0.34.1

- **A word's own hyphen at a line end no longer leaves the old words in
  the file.** When a rewritten paragraph put "well-" at the end of a
  line, ZPDF's check of its own result misread that hyphen, took the
  change for a failed one and made it again the old way: the old words
  covered, and still found by Find and Copy. The check now reads a
  line-end hyphen as the hyphen it is.
- When old words do have to be covered (they stay in the file
  underneath), the reader now says so after the change, and points to
  Redact for removing text for good.

## 0.34.0

Rewritten paragraphs are set better, and keep more of what they had.

- **Lines are broken looking at the whole paragraph**, not one line at a
  time: never more lines than before, no lone short word left on the last
  line when the lines above can spare one, and in justified text the gaps
  between words stretch less and more evenly.
- **Justified paragraphs divide long words at line ends** (English, by
  the standard hyphenation patterns), which evens the gaps further and
  can save a line (a word's first part is never left alone on a line to
  do it). A word with a hyphen of its own breaks after it; the
  paragraph's last word is never divided; a file marked as another
  language is left whole. Left-aligned text is only divided where a word
  is wider than its column.
- **No old words left underneath in Word's files.** A paragraph whose
  words are in several tags (a link, a run-in heading or each line has
  its own, as Word makes them) was covered and drawn on top, so Copy and
  Find gave the old words as well as the new. The old words are now taken
  out, and each new line is written into the tag of the line it replaces.
  The same for one changed line.
- **A font, size, bold, italic or colour chosen for the whole paragraph
  goes on top of the words' own styles** instead of replacing them: a new
  font keeps bold words bold and italic words italic, and a new colour
  changes the paragraph's main colour only (a red word stays red). The
  editor shows a picked colour on the plain words at once.
- **Highlights, underlines and strike-outs follow their words**, as links
  do: across two lines when the words now wrap, and gone when their words
  are deleted.
- **Footnote marks and subscripts stay above and below the line** (the
  small 2 of a footnote, the 2 of H2O); before, they came back on the
  baseline, and a raised mark could split its paragraph in two.

## 0.33.0

Rewriting a paragraph keeps each word's own style.

- **Bold, italic and coloured words stay as they are.** When you rewrite a
  paragraph in the reader (Edit text, Shift+click a line), words that were
  bold, italic or in a colour keep that look when the paragraph is set
  again; before, the whole paragraph took the look of its first word. A
  changed word keeps the style of the word it replaced, and new words take
  the style of the word before them, as in a word processor.
- **The paragraph editor shows the styles**, and **Ctrl+B** and **Ctrl+I**
  (or the B and I buttons) make just the chosen words bold or italic, in
  that face of their own font. With no words chosen in a paragraph of one
  style, they change the whole paragraph, as before. Pasting is plain text.
- **Links follow their words.** A link over words of a rewritten paragraph
  moves with them; when its words now run over two lines the link covers
  both, and a link whose words were all deleted is removed.
- **Paragraphs set without a gap between them** (as Word does by default
  in some styles) are told apart: Shift+click takes the one you clicked,
  not both as one.
- **Shift+click opens the paragraph** also when there's a caret or a
  selection on the page; before, it could select text instead.
- **No boxes where the spaces were.** In files whose fonts hold only the
  letters used (Word's), a rewritten line or paragraph could show a small
  box for every space: Word's fonts can list their "no such letter" box
  as a space beside the real one, and ZPDF wrote the box. It now writes
  the real space, and only in a font the page already draws spaces with;
  otherwise the words are set apart as the page's own are.
- A paragraph that starts with a bold word ("Note: ...") opens in the
  editor in the look of its main words, and a paragraph rewritten once
  shows its styles again when it's edited a second time.
- A paragraph in one style is written exactly as in 0.32.

## 0.32.0

Drawings with layers. CAD programs export each drawing layer as a PDF
layer (optional content); ZPDF now shows them, prints with them and can
flatten them.

- **Layers tab in the reader.** For a PDF with layers, a **Layers** tab
  beside Pages, Bookmarks and Comments lists them in the file's order,
  nested as the file nests them (a named group's layers indented under its
  heading), each with a box to show or hide it, and
  **Show all** / **Hide all**. Locked layers can't be switched; in a radio
  group one layer stays on. The pages and thumbnails redraw at once, and
  each tab keeps its own choice when you switch tabs.
- **Measuring follows the layers.** Snapping uses only the lines of the
  layers shown, so a hidden dimension layer no longer pulls points off the
  walls. A hidden layer's words can't be selected either, and hiding a
  layer clears a selection on the page.
- **Printing follows the layers.** Print prints the layers shown.
- **Save layer visibility** makes the file open with the layers as they
  are now, in ZPDF and in Acrobat; it's a change like any other, with Undo.
- **Flatten layers** (in the reader's Layers tab, as a tool under Improve,
  and `pdfm flatten-layers`): the layers shown become part of the page,
  the hidden layers' lines, text and pictures are removed from the file,
  and the layers go, for sending a clean set.
- The page previews of a tool no longer stumble when the file is taken
  away while they load.
- Web page to PDF tries once more with a fresh browser when the browser stalls
  while starting, instead of failing after two minutes.

## 0.31.0

Mostly behind the scenes: groundwork for keeping ZPDF and what it installs
safe. The app works as before.

- **Packages checked against their hashes.** When a release carries a
  list of ZPDF's packages pinned with their hashes, the installer installs
  exactly those, and stops if a file doesn't match its hash, so a package
  replaced on PyPI can't slip in. Without the list, or on a Python other
  than the one it was made for, it installs as before. The list is made on
  Windows by `packaging/lock.py`, or by a workflow on GitHub.
- **Reporting a security problem.** `SECURITY.md` says how to report one
  privately, through GitHub, what counts, and that fixes go into the latest
  release.
- **The name and the licence.** Every source file now starts with a
  copyright line, and a test makes sure new ones do too. `TRADEMARKS.md`
  says the ZPDF name and icon are for this software only, not for other
  software or changed copies, and the README says how copies that break
  the licence are reported to GitHub.
- **Checks that run by themselves on GitHub:** Dependabot proposes
  updates to ZPDF's packages and to the workflows' actions each week;
  CodeQL scans ZPDF's Python and JavaScript for security mistakes; and
  `pip-audit` checks ZPDF's packages for known vulnerabilities each week.
- Switching tools quickly while a page preview was loading for the first time
  could leave an error behind; it no longer does.

## 0.30.0


Two ways to get work done without opening the tools first.

- **Right-click actions in File Explorer.** Right-click PDFs for a
  **ZPDF** submenu: **Combine in ZPDF**, **Make searchable (OCR)**, **Make
  smaller** and **Open in ZPDF**; right-click pictures (JPG, PNG, TIFF) for
  **Make a PDF with ZPDF**. Select several files and choose the action
  once: they all go to the one ZPDF window, as one job. The result opens as
  a new tab that isn't saved yet, and Save asks where, starting in the
  files' folder. Combine puts the files in name order ("Scan 2" before
  "Scan 10"). On Windows 11 the actions are under *Show more options*.
  The installer adds them for you only (`-NoContextMenu` leaves them out),
  uninstalling takes them away, and **Settings → Show ZPDF in the
  right-click menu** turns them off and on.
- **Watch folders in the app.** **Settings → Watch folders**: add a
  folder, choose what to do with the PDFs put in it (Make searchable, Make
  smaller, Archive as PDF/A, or a saved recipe), where the results go (a
  Done folder inside it, or next to the original with " (searchable)" and
  the like on the name) and whether the originals stay or move to an
  Originals folder. It runs while ZPDF is open, with the same engine as
  `pdfm watch`: each file is done once, also after a restart, a file still
  downloading or being copied is left until it's complete, and a note says
  when a file is done or why it couldn't be. Pause, resume or remove a
  folder from its line in Settings.
- `pdfm watch` leaves alone files still being downloaded (`.crdownload`,
  `.part` and the like beside them), Office lock files and hidden files.

Still to come: starting ZPDF with Windows, minimised, so watch folders run
without opening it.
- A file in a watch folder that is still open in another program waits and is done
  once it is closed; after a minute ZPDF says so once, instead of failing for good.
- The card for a right-click action stays 20 seconds after it finishes, and while
  you point at it.

## 0.29.0

Mostly behind the scenes: the reader now keeps each open document's state
in one place, and everything it shows about saving (the dot on a tab and
by the title, the Save button, closing a tab, Ctrl+S) follows from it, so
they can no longer disagree. The reader's code is also split into parts
(tabs, annotating, Sign, find, the text editor box, measuring). Things
that were wrong and are now right:

- **Undo after Black out** brought the words back, but Save still made a
  "(redacted)" copy and called itself "Save a redacted copy". It saves as
  usual again.
- **Undo after saving**: with annotations saved, turning, deleting or
  moving a page and then undoing it left the unsaved dot and a red Save,
  although the document was as saved.
- **Ctrl+S straight after saving** saved the file again (and offered Undo
  again) although nothing had changed; it now says "No changes to save",
  as the Save button already showed.
- **Undo of "Saved over"** put the file back, but a page change undone after
  that could show the document as saved when it wasn't.
- **Closing ZPDF or updating it** now warns about a tool's result that was
  never saved anywhere (its tab has the dot, and closing the tab already
  asked).
- **OCR with Tesseract, with both OCRmyPDF and the Tesseract program
  missing**, asked only for OCRmyPDF; it now names both, with where to get
  each. (The OCR tests also no longer depend on whether OCRmyPDF is
  installed on the computer running them.)
- **Redo after undoing a note** (or a shape, a stamp, a link, a text or
  page change): Ctrl+Z took it away, but Redo stayed off and Ctrl+Y did
  nothing. Redo now puts it back (with the dot), and Undo and Redo pick
  the same change whether from the buttons or the keys.
- **Ctrl+Z and Ctrl+Y with Select on** (for instance after Esc from Draw)
  did nothing, although the Undo and Redo buttons worked. They now do
  what the buttons do, with any tool on (not while typing in a box).
- **Closing ZPDF with the window's X** didn't warn about unsaved work (the
  warning only worked in a browser). It now asks "Close ZPDF? Your changes
  to … haven't been saved." with Yes and No (No keeps it open). Restarting
  after an update, which asks itself, doesn't ask again.
- **The unsaved dot while drawing** came only once the drawing was put
  down (Select or Esc), although Save turned red as soon as the stroke was
  drawn. The dot now shows with it.
- **The Measurements list** stayed open over the next tab after switching
  away from the measured one. It now goes with its document, and is there
  again on coming back.

## 0.28.0

Safety nets, mostly behind the scenes: checks that catch a kind of mistake
before it reaches a release.

- **When a program ZPDF can use isn't there** (Microsoft Word, LibreOffice,
  Edge or Chrome, Tesseract, Ghostscript, the built-in OCR engine, Windows
  Image Acquisition), each tool that uses it now either works another way
  or says what's missing and what to do, and the tests check every one:
  - Web page to PDF from a typed web address (no saved page added) stopped
    with "Something went wrong" before it printed anything. It prints the
    page now, and without a browser says to install Edge, Chrome or
    Chromium.
  - PDF to Word: "Microsoft Word only" without Word, and "Exact layout"
    without LibreOffice, say which converter to choose instead.
  - OCR without an engine, or with an engine that isn't installed, says to
    reinstall ZPDF or install Tesseract, instead of only a pip command; with
    Tesseract it names just what is missing (OCRmyPDF, the Tesseract
    program, or Ghostscript when OCRmyPDF wants it).
  - A program that can't start says so in words, not "[Errno 2] ...".
  - Office to PDF says which program converted the file (Word, LibreOffice
    or ZPDF itself), and says what to install when there's none.
  - Scan: without the piece of ZPDF that reaches Windows Image Acquisition,
    the Scanner choice says to reinstall ZPDF, and that photos still work.
- An import check: every part of ZPDF must load on its own, and two parts
  may not start depending on each other (a mistake that only shows when
  the app starts). Four such pairs were taken apart; five remain, listed in
  `tests/test_imports.py`.
- A weekly sweep on GitHub (`.github/workflows/sweep.yml`): every tool on a
  small made-up corpus of about 60 valid and damaged PDFs
  (`make_corpus.py --quick`), with the report kept for two weeks. It fails
  only on a crash, a hang or running out of memory, and never blocks a
  pull request.

## 0.27.0

A maturity pass: every tool was run on about 400 real-world and tricky
PDFs (pypdf's collection of sample files, documents made with LibreOffice,
Word and PowerPoint files, scans, and files damaged on purpose), and what
broke was fixed. Kinds of files that work better now:

- **Files cut short or with a broken index of objects** (a download that
  stopped, a file copied with the wrong line endings): each tool mends
  them first, as Repair does, and says so. Repair's own result now opens in
  every tool.
- **Very large pages** (posters, plans a metre or more across): Convert to
  images, PowerPoint, Word, the web page, Compare, Redact and Add form fields
  draw them with fewer pixels instead of running out of memory. A picture
  made this way still says how big the page is.
- **Pages whose crop box lies outside the page**: shown whole, as other
  viewers show them, instead of failing in PowerPoint, images, Compare,
  Trim margins, Clean up and previews.
- **A page count that is wrong in the file** (5 for one page, 0 or -1): put
  right in what every tool writes, and when signing; the reader shows such
  a file as damaged, with Repair, instead of an empty window.
- **Document details that are really another part of the file**: Sanitize
  and Document details no longer remove the pages along with the title.
- OCR with Tesseract keeps form fields, and a portfolio keeps its own
  pages: OCRmyPDF's PDF/A step (through Ghostscript) dropped the fields and
  printed the attached files instead. OCR now writes a plain PDF; PDF/A
  is its own tool.
- To Excel: a table with a lone dash or currency sign in a cell (common in
  financial reports) no longer stops it.
- To Word: control characters in a PDF's title or links no longer make a
  document Word refuses to open.
- Word, the web page, Accessibility tags and Excel: a page the table reader
  can't parse no longer stops them; Word and the web page convert the text
  without the tables.
- Sign: a page tree pyHanko trips over is signed on a mended copy (a page
  without its type), or refused in plain words.
- The PDF/A check and conversion read pages with small pictures written
  into the page itself (inline images).
- Compress and Colour cope with a picture in a compression they don't know.
- A page turned by an angle that isn't a quarter turn counts as not turned,
  as viewers show it (Sign stopped on it).
- Redact: a page of small print with thousands of matches takes seconds,
  not minutes.
- Copy text says when a file has no text (and points to OCR) instead of
  giving an empty text file; Remove password says when a file has none.
- For checking: `tests/sweep` makes the corpus and runs the sweep (see
  Tests in the README).
- Web page to PDF can no longer stall for two minutes when the browser writes a
  lot of warnings while it prints, or when it crashed and left its lock behind.

## 0.26.0

Scan: paper to a searchable PDF, in one place.

- **Scan paper** on the start screen (and Scan under Convert, or in the
  search box) opens a page of its own. The pages come from a scanner or
  from photos taken with a phone.
- On Windows, ZPDF lists the scanners Windows knows and scans from them
  through Windows Image Acquisition, which comes with Windows: no other
  program, and no PowerShell, so it works with Smart App Control. Colour,
  greyscale or black and white; 150, 200 or 300 dpi (300 greyscale to
  start with); the glass, one page each press of "Scan another page", or
  every page in the document feeder, arriving one by one. An empty
  feeder, a busy, offline or jammed scanner are said in plain words, and
  Cancel stops between pages and keeps the pages scanned so far. Where
  scanning can't work (not Windows, or no Windows Image Acquisition) the
  Scanner choice is off and says why; Photos works everywhere.
- Photos: the page's four edges are found in each photo and the page is
  flattened, cropped and straightened. A clean look evens out shadows
  and makes the paper white and the ink dark; "Keep colours" keeps
  coloured ink and stamps, "As taken" changes nothing. When the edges
  aren't clear, the whole photo is kept (only straightened), and its
  page says so.
- The pages gather in a strip as they arrive: drag them into another
  order (or Alt+arrow keys), turn them, take them out (with Undo), or scan
  one again.
- Make PDF straightens crooked pages, takes out blank ones, turns
  sideways pages upright, reads the text (the built-in OCR engine, or
  Tesseract), sizes each page from the scan's resolution (photos go on
  A4 or Letter, whichever fits their shape), compresses it as Compress
  does scans, and opens it in the reader as a new tab, not saved yet.
- From the command line: `pdfm scan` (with `--list`, `--scanner`,
  `--feeder`, `--mode`, `--dpi`) and `pdfm photos-to-pdf`.

## 0.25.0

A design pass: the same look, made consistent. The tools work as before.

- One set of type sizes everywhere, in the app and the reader: six, from
  12px to 24px. The small labels, keys and file facts that were 11px are
  12px now, easier to read, and the labels in capitals share one spacing.
  The rail's buttons are a little wider for their labels.
- One set of corners and shadows. The odd ones (a corner of 6, 10 or 12
  pixels here and there, a shadow made up on the spot) use the shared ones.
- What a search found shows in a plain banner; green is kept for a job
  done. A folder tool's preview, which hasn't changed anything yet, is
  plain too.
- A tool's description is about 70 letters a line instead of 95.
- Redact: Match upper/lower case and Whole words only sit beside the words
  they apply to, and the kinds of numbers to black out have a row of their
  own.
- After Esc closes a group of tools, the rail button under the pointer no
  longer looks like a second current group.

## 0.24.4

Small fixes. The look is the same.

- With Select, a single click on a signature or a drawing picks it, ready
  to move, resize or delete, also after you switch to another tab and
  back. (Before, it took a double-click.) Esc after picking one goes back
  to Select instead of leaving its tool on. Links, form fields and
  selecting text work as before.
- Switching tabs while a long file is still being laid out can't leave an
  error behind any more.
- Signing no longer waits several seconds before the signature appears
  when the reader is slow to switch to the picture tool.
- The automatic checks run on current versions of GitHub's actions.

## 0.24.3

Two small things from a check of 0.24.2 on Windows.

- A file you have already signed with Sign (a signature, drawing or typed
  name on its signature line) is no longer offered "Sign it" again.
- A selected picture or signature has handles at its corners only. The
  side handles squashed it out of shape, and on a small signature they
  covered the middle, where a press should move it.

## 0.24.2

Small fixes after a check of 0.24.1 on Windows. The look is the same.

- A signature put on its line stays drawn there when you sign again and
  the new one is selected, and when you zoom or turn the page. It is now
  made where it goes and at its size, instead of being made in the middle
  of the page and moved there.
- After signing, Select and Esc turn the picture tool off again.
- Signatures, drawings and other annotations added with the annotate
  tools and not saved yet can still be picked, moved, resized and deleted after you
  switch to another tab and back: the tool you were using comes back with
  the tab. The tab still shows it isn't saved, and Save writes each one
  once. (Undo can't take back what was done before the switch; delete it
  instead.)
- With Select, double-clicking a signature picks it, ready to move or
  delete.
- In the Sign dialog the name box has the focus as soon as it opens, and
  no longer offers saved names to fill in, so typing straight away keeps
  every letter.

## 0.24.1

Small fixes after a check of 0.24.0 on Windows. The look is the same.

- "Sign it" puts your signature on the file's signature line: at the
  start of the line, standing on it, about as tall as handwriting next to
  the label and never longer than the line. It still moves and resizes
  like any signature. Sign without a known line places it about 2 inches
  wide in the middle of what you see, no longer large across the page.
- Undoing your only change (or every change since the last save) takes
  away the unsaved dot and quiets Save again; Redo brings them back.
- Opening the find bar while annotating, then closing it, brings the
  annotate bar back with its tool, and the file panel stays aside. The
  same the other way round.
- In the keyboard shortcuts list, the descriptions start at the same
  place in every section.
- The delete button on a selected picture or signature shows its icon
  in the button again, not far below it.
- The corner handles of a selected picture, signature or note are larger
  and easier to catch with the mouse.
- "Sign it" straight after opening a file also finds the line (the first
  time, or in a window that had been minimized, it could put the
  signature in the middle of the view instead).
- "Use saved signature" places it at the same moderate size as a new one,
  not about 5 inches wide.
- Pressing R straight after clicking another tab turns that tab, not the
  one you left.
- Picking a font in a text edit takes its normal width: "DejaVu Sans"
  bold could come out as DejaVu Sans Condensed bold.
- Turning a page just after switching tabs turns it in the tab you
  switched to, and no longer logs an error. Page changes asked for while
  a tab is still being shown wait for it, instead of being made from the
  other tab's pages.

## 0.24.0

Sign means signing, a damaged file has a way out, and the page gets more
room, after a second review of the interface and a check on Windows. The
look is the same.

- Sign, everywhere, is your own signature: drawn, typed or a picture of
  it, placed on the page. The file panel's Sign and its "Sign it"
  suggestion open that, no longer the certificate form. Signing with a
  certificate (.pfx or .p12) is now called Digital ID, in the tool list,
  the annotate bar's More tools and Ctrl+K.
- "Sign it" is also suggested for a file with a printed place to sign: a
  "Signature" or "Sign here" line on one of its first pages.
- A file the reader can't show no longer flashes a message and leaves an
  empty reader. Its name stays where the pages would be, with "ZPDF can't
  show this file — it may be damaged.", Try to repair it (the repaired copy
  opens in a new tab, not saved yet), Remove from Recent and Close. Recent
  says "can't be opened" in red until the file changes, and a file that
  wouldn't open no longer jumps to the top of Recent.
- While the annotate bar or the find bar is open, the file panel steps
  aside so the page has the room, and comes back when they close (open it
  by hand meanwhile and it stays). Find and annotate are one row at a
  time. The note box and the text look bar stay in sight instead of
  running under the panel or the bars, and a tool's tip goes away with the
  tool.
- With one document open, the dot for unsaved changes is beside its name
  too. Ctrl+S while typing a note adds the note, then saves.
- After blacking out areas in the reader, Save writes a copy beside the
  original, "name (redacted).pdf". Replace original is there too, and asks
  first, since the original text can't be recovered. The reader's button
  says "Black out 3 areas", like the Redact page.
- Closing a tab with unsaved changes offers Save as well as Close without
  saving.
- Files kept when you switch tools are said: "2 files kept from Compress
  · Clear". The same file is never in the list twice. Redact says "Choose
  what to black out" and waits until you have; an error after a run
  starts with a capital and shows by the Run button too.
- Make it smaller is no longer suggested for a file Compress just made, or
  for one that is already small for its pages.
- Search PDFs is called Search a folder everywhere. Its results head each
  folder by the part below the folder you searched, and a copy elsewhere
  names the other copy and its folder. What you typed is still there when
  you come back to it, until ZPDF is closed.
- Tool descriptions in the rail's lists take two lines instead of being
  cut off.
- Ctrl+K knows other words: shrink, join, combine, lock, protect, unlock,
  sign. "Keyboard shortcuts" there, or ?, lists the keys ZPDF answers to.
- File names are set in Source Serif 4, which is wider and easier to read
  than before; its 1 no longer looks like l.

## 0.23.0

Clearer about saving, and easier to find your way, after a review of the
whole interface. The look is the same.

- After a tool runs in the reader's file panel, its message has Save… (the
  folder of the original is offered first), then Show in folder. A tab
  that isn't saved anywhere yet has a dot.
- Saving over a file (Ctrl+S) can be undone for 10 seconds: "Saved over
  report.pdf — Undo" puts back what the file held. (Not for files over
  64 MB.)
- Questions come up in ZPDF's own window, with buttons that say what they
  do: "Replace file", "Save as a copy", "Cancel". Cancel and Escape never
  change anything. This replaces the browser's plain boxes everywhere.
- Save is red, like Run: the one main button. Once there is a result to
  save, Run steps back.
- The annotate bar keeps its words in ordinary windows: Highlight, Note,
  Add text, Draw, Sign and Save. The rest (Underline, Strike out, Shapes,
  Stamp, Add picture, Edit text, Edit pictures, Links, Digital ID,
  Redact) are under More tools, which shows the one in use. "Picture" is
  now "Add picture" and "Pictures" "Edit pictures".
- Single keys while the annotate bar is open: H highlight, C note, A add
  text, D draw, S sign (press again to put the tool down). They are in
  each button's tooltip and in Ctrl+K.
- The rail is grouped by what you want to do: Read, Pages, Improve,
  Convert, Protect. Improve has Compress, Make searchable (OCR), Repair,
  Clean up scans, Convert colours, Flatten and the accessibility tools.
  The folder tools (Search PDFs, Organize, Rename, Find duplicates) and
  Recipes are on the start screen under "Start from several files" and
  its More, and under All. A long tool list shows that it scrolls.
- Search PDFs: once results come, the form folds into one line ("how much
  do I owe" in Documents · Edit) and the results sit under the title,
  grouped by folder, with only the matching words marked. The marks are
  easy to read in dark mode too.
- One or two files on a tool page show as a compact strip instead of a
  large, mostly empty card.
- "can't be read" is shown in red on the start screen and in the file
  panel.
- Redact's button says "Black out" ("Black out 3 matches" once they're
  found), Delete pages' "Delete pages", Remove hidden data's and
  Flatten's their own name, instead of Run.
- When a file couldn't be opened and was left out, the result says so by
  name. The command palette has one Redact, and "Redact this document" in
  the reader.

## 0.22.1

Easier to use with the keyboard and easier to see, after a review against
common accessibility guidelines. The look is unchanged.

- Text boxes and lists have borders you can see, and whatever has the
  keyboard's focus shows a clear outline, including search results and
  menus. The bar with Run at the bottom of a tool no longer hides it.
- The annotate toolbar fits the window, with Save and Close always in
  view; the Settings dialog keeps its buttons on screen in a small window.
- After searching a folder, the results come into view.
- A PDF that can't be opened is said to be damaged, in plain words, with
  a way to Repair it; it no longer counts as ready to run.
- "Skip to main content" for the keyboard; Escape closes the annotate
  bar and puts the focus back where it was after the find bar and
  password box; small buttons are easier to hit.
- Removing a file from Recent can be undone; such messages stay at least
  10 seconds, longer while the pointer is on them. The password box has a
  label and Show/Hide, and ZPDF offers to remember a PDF's password and
  fill it in next time. A missing setting is pointed out next to the field.
- "Drop files here" can be reached and opened with the keyboard.
- Windows' high contrast mode shows which tool, tab and document are
  active, and lists keep their arrows.
- The "looks like a form" suggestion no longer appears for cover pages
  with drawn artwork, or for boxes inside photos and screenshots.
- Opening a file that is already open switches to it.

## 0.22.0

- Editing text really replaces it. The old words are taken out of the
  page and the new ones written in their place, instead of being painted
  over: search finds what you wrote and not what was there, gradients,
  highlights and underlines behind the words survive, outlined and
  see-through text keeps its look, and a right-aligned number keeps its
  right edge. A change to one line changes that line only. Letters the
  document's font lacks use a matching font (serif for serif), and any
  that no font can draw are named. Every edit is checked afterwards; one
  that can't be done safely in place falls back to the old way and says
  so.
- PDF to Word follows designed pages better: a pull quote or picture
  inside a column no longer splits the columns around it, footnote marks,
  superscripts and subscripts (1st, m², CO₂) stay in their sentence, the
  labels of charts and diagrams come out as tidy lines of text, numbered
  headings are told from numbered clauses, and Chinese, Japanese and
  Korean lines join without stray spaces.
- OCR reads three-column pages column by column, keeps the rows of a
  table on a crooked scan together, keeps the spaces between words on
  grey paper, puts back spaces where Chinese meets English, and no longer
  reads the blanks of a form as text.
- The reader opens files faster (a 27 MB scan shows its first page in
  about half the time) and big files are read in pieces while the first
  page is drawn. Page thumbnails are made only when the Pages panel is
  open, and only for the pages in view. Opening a file no longer sends
  it back to ZPDF's own server for checks.
- Enter presses the main button of a reader dialog: it used to press the
  first, which cancelled the password prompt and deleted a link in the
  link editor. Enter after typing a password now opens the file.
- A big file that is moved, deleted or changed by another program while
  it is open no longer stops the reader: ZPDF says what happened and the
  tab can be closed.

## 0.21.0

- Search a folder by meaning. Ask "how much do I owe" and Search PDFs
  finds the page that says "balance due": pages are ranked by what they
  are about as well as by the words they share with your question, the
  closest first, each with the part of the page that matched. In a test
  of 44 questions worded differently from the documents, the right
  document came first 30 times (matching words alone: 19; the exact
  phrase: never).
- A name, a number or a code (an order or invoice number) finds the pages
  that have it and nothing else. "These exact words" searches for a
  phrase as before. A second copy of the same file is named beside the
  first instead of doubling every result.
- Questions are understood in English. Chinese (and Japanese and Korean)
  is matched by its characters, so searching in Chinese finds the pages
  with those words, in the best order.
- It runs on your computer: a 4 MB table of word meanings comes with
  ZPDF, nothing is sent anywhere, and a folder is prepared once and then
  searched in moments. On the command line: `pdfm search FOLDER "…" --best`.

## 0.20.0

- The panel beside an open document suggests more: "Make it fillable" for
  a form you can't type in yet, and "Sign it" for a file with an empty
  signature field.
- Tools that start from several files (Merge, Compare, Interleave, and
  the tools that turn pictures, Office files and web pages into PDFs)
  open on a large drop area that says what to add; their options appear
  once the files are there.
- One list of recent files, the same on the start screen and in the
  reader: each file with what it is, and two files of the same name told
  apart by their folder.
- Movement where it shows what happened: panels and the search box open
  softly, a running tool's bar shimmers, results and messages arrive and
  leave gently. None of it with Windows' "reduce motion" on.
- Frosted surfaces read properly in the dark theme; saving a result and
  removing a recent file say so in a short message.
- Opening a PDF while ZPDF is running brings its window to the front more
  reliably. After "Add a password", the note names the protected copy.

## 0.19.0

- A new look and a new way in. ZPDF opens on a start screen: drop or open
  a PDF, or pick up a recent file. Each recent file says what it is at a
  glance: its pages and size, and whether it is scanned, searchable,
  protected, signed or a form.
- With a document open, a panel beside it shows what the file is and what
  fits it: a scan with no text is offered "Make it searchable", a large
  file "Make it smaller", a signed one "Check its signatures". Common
  tools are one click away, with their options in the panel.
- A tool run from the panel works on the open file and opens its result
  as a new tab beside the original; nothing is uploaded again, and the
  original stays as it was. T shows and hides the panel.
- The long list of tools at the side is now a slim rail with five groups:
  Read, Pages, Convert, Protect and All. Every tool is still there, and
  the search box at the top (Ctrl+K, or /) finds any of them.
- Layered surfaces, softer corners and new type, in light and dark. The
  theme choice has moved into Settings.
- PDF → Word's built-in converter reads pages the way the other exports
  do. Columns of text come out one after the other where they were read
  straight across, headings are headings, tables are real Word tables,
  lists keep their items, and bold, italic and links are kept. Documents
  made of text, tables and scans convert well; heavily designed pages
  (brochures with panels, illustrations made of many small drawings) are
  better than before but not yet right everywhere.
- Scanned pages that have been made searchable convert properly: the
  built-in converter added stray letters to their words ("committee e
  met on Tuesday y") and flattened their tables.
- In the HTML export too, an ordinary line of a scanned page is no longer
  taken for a heading because its recognised size differs a little.
- Designed pages read better in the Word and HTML exports: a grid of
  features (a heading over a paragraph, side by side) is read one feature
  at a time, two columns of sentences aren't turned into a table, the
  labels inside a screenshot stay in the picture, page numbers are left
  out, and a bold sentence or a number is no longer a heading.

## 0.17.0

- PDF/A without Ghostscript. ZPDF now converts to PDF/A-1b, -2b and -3b
  itself: it adds what the standard asks for (the PDF/A mark, an sRGB
  colour profile, printing links), takes out what it forbids (scripts,
  protection, attachments in parts 1 and 2) and leaves the pages as they
  are. Scans and files made by browsers and Office need nothing else.
- Fonts that aren't in the file (Helvetica, Times, Courier in older PDFs)
  are embedded from the computer's own: Arial, Times New Roman and
  Courier New have the same letter widths, so no line moves. A font
  without such a stand-in stops the conversion with a message naming it.
- Ghostscript, when installed, is still used for a file ZPDF can't put
  right alone, and `pdfm pdfa --engine ghostscript` asks for it by name.
- Every converted file is checked with ZPDF's own PDF/A checks before it
  is handed over.

## 0.16.0

- Compress keeps scanned text sharp. At Medium and High, a scanned page is
  stored as a black-and-white text shape at the scan's full resolution
  over a low-resolution background, with the ink's colour kept, instead
  of being blurred down as a whole. The file is about as small as before
  and the text stays as sharp as the scan. On by default; "Keep scanned
  text sharp" in Compress, or `--no-sharp-scans`, turns it off.
- When something goes wrong, the message carries a short error ID, and
  ZPDF keeps a log of what happened (the tools run, how long they took, and
  errors), with document names and your user folder left out. Settings ›
  About › Copy diagnostics copies the details needed to report a problem;
  Open log folder shows the log.
- Damaged and unusual PDFs (cut short, broken cross-references, looping
  page trees, bad pictures and fonts, no pages, empty or oddly rotated
  pages) now get a plain message from every tool instead of an unexpected
  error, and messages name your file rather than ZPDF's working folder.
  Every tool is tested on a set of such files.
- Security: converting Word, Excel and PowerPoint files never runs their
  macros; a saved web page can't pull other files from your PC into the
  PDF it's printed to; Ghostscript runs in its safe mode; the file catalog
  can't turn a document title into a spreadsheet formula; Settings only
  accepts ZPDF's own update repositories; Windows programs are started by
  their full path.
- Performance limits: checks that memory follows a file's size, not its
  page count, for merging, compressing and OCR, and that a 200 MB file
  reaches the reader in pieces. They run when a version is released.
- PDF → Word with "Best available": if Microsoft Word fails or doesn't
  finish (on some PCs it never does when another program drives it), ZPDF's
  own converter is used instead and the result says so, rather than a wait
  of five minutes and an error. A Word that doesn't answer is closed, not
  left running out of sight.
- Web page → PDF no longer fails when Edge hands the printing to a browser
  process of its own and that one finishes a moment later.

## 0.15.1

- Updating or installing on a PC whose newest Python is one the built-in OCR
  engine has no version for yet (Python 3.15 when it came out) failed. The
  installer now picks Python 3.10 to 3.14, installing Python 3.13 for ZPDF
  if there's none, and moves an existing ZPDF onto it. If the OCR engine
  still can't be installed, ZPDF is installed or updated without it and
  says so, rather than not at all.
- Publishing a release checks first that GitHub's gh is installed and
  signed in and that git has a name and email for the version tag, and says
  plainly if the release went out but its tag didn't.

## 0.15.0

- Releases for friends: each version is published on ZPDF-releases as one
  zip with the installer, its SHA-256 beside it. No GitHub account is needed
  to download or to update.
- The Update button follows those releases and checks the download's
  checksum before installing it. No token to set up any more (a development
  copy can still follow the private repository with one).
- Licence: ZPDF is shared under the PolyForm Internal Use License 1.0.0,
  with personal use added: use it at home or at work and change it for your
  own use, but don't pass it on. It comes as is, without warranty.
- Settings has an About section with the licence and the notices of the
  software by others that ZPDF includes or installs.
- `packaging/release.py` checks a version (the tests, the app build and its
  self-test), makes the release files and publishes them.

## 0.14.0

- OCR is built in: PaddleOCR's PP-OCR models, run with ONNX Runtime, read
  scans with nothing else to install. On a hard set of test scans the mean
  character error rate is 0.3 %, against 35.6 % for Tesseract with its
  English data.
- Sideways and upside-down pages are turned upright, two-column pages are
  read column by column, and table rows stay together.
- Russian, other Cyrillic, Korean, Arabic, Persian, Hindi, Thai, Greek,
  Tamil and Telugu: the reading model for the script is fetched once and
  checked against its published checksum.
- The text layer works in every reader and keeps files fit for PDF/A.
- Clean up scans can turn pages upright without Tesseract.
- Tesseract stays as a second engine; the installer offers it and
  Ghostscript as extras.

## 0.13.0

- True redaction: what's under the boxes is removed, the rest of the page
  stays text. Also on scanned pages with OCR text.
- Accessibility: tag PDFs for screen readers and check them, like Acrobat's
  accessibility check.
- PDF to PowerPoint and PDF to HTML, with tables, lists and columns.
- Password permissions, attached files, page labels, repair and fast web view.
- Stamps in the reader, a comment summary, FDF and XFDF export and import,
  and web page to PDF.
- Form formats, totals and checks, with form scripts running in the reader.
- Text editing flows within its paragraph, with a font picker.

## 0.12.0

- Make a printed form fillable: ZPDF finds its fields by itself.
- Radio buttons, dropdowns and list boxes.
- PDF/A check.

## 0.11.0

- Edit pictures and links in the reader.
- Sign with a digital ID and check signatures.
- PDF to Excel.

## 0.10.0

- Measuring: rectangles, cut-outs, counts and angles, with names, groups
  and colours, kept with the file, as CSV, or saved into the PDF.
- Compare drawing revisions in the reader.

## 0.9.0

- Save in place (Ctrl+S) and Save as in the file's folder; big files open
  in pieces.
- Notes, comments, underline, strikeout and shapes in the reader.
- Page tools: blank pages, split at bookmarks or by size, booklets, trim
  white margins.
- Drawing scales stored in the PDF are read.

## 0.8.0 and earlier

- 0.8: measuring with path lengths, a list with totals and units, undo and
  redo; safer reading of broken files.
- 0.7: live previews, favourite tools and clearer tool pages.
- 0.6: drawing scales, true paper size, a measure tool that snaps to the
  drawing's lines, and text edits that stay in place.
- 0.5 and before: the tools, the reader, the desktop app and the Windows
  installer.
