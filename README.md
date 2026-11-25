# ZPDF

**A local-first PDF studio — desktop app, `pdfm` CLI, and Python library — for Windows, macOS, and Linux.**

ZPDF runs **100% on your own machine** with zero telemetry, no cloud uploads, and no subscriptions. It combines a fast, distraction-free PDF reader and editor with capabilities rarely found together in any offline tool: **press-room CMYK preflight**, **CAD blueprint takeoff & DXF conversion**, **offline book & manga translation**, **a local AI document assistant**, **true permanent redaction**, **PDF/A & PDF/UA compliance**, and **full CLI/watch-folder automation**.

[Why ZPDF](#why-zpdf) · [Install](#install) · [Core Strengths](#core-strengths) · [Desktop App & Reader](#desktop-app--reader) · [Command Line](#command-line) · [Python Library](#python-library) · [Privacy & Security](#privacy--security) · [Tests & Releasing](#tests-benchmarks--releasing) · [Licence](#licence)

---

## Why ZPDF?

Most PDF tools force a trade-off: pay recurring subscriptions for bloated desktop suites, upload confidential documents to cloud websites, or settle for bare-bones merge/split utilities. ZPDF is built to eliminate that trade-off:

| Strength | What ZPDF Delivers |
| --- | --- |
| **Verifiable Offline Privacy** | Everything runs locally on `127.0.0.1` with a per-session random auth token and **zero telemetry**. Includes **true permanent redaction** (content-stream & Form XObject removal + post-write verification), AES-256 & certificate encryption, digital ID signing, SHA-256 tamper-evident audit logs, and enterprise admin policies. |
| **Press-Room CMYK Preflight (No Ghostscript)** | PDF/X-1a & PDF/X-4 audits, recursive Form XObject inspection, true **C/M/Y/K plate separation & TAC heatmap**, a **live ink-density eyedropper** (`C% M% Y% K% · TAC%`), spot-ink & registration-color audits, and **one-click pure-Python press fixes** (white overprint, rich-black text, TAC clamping, hairlines, RGB → CMYK). |
| **CAD, Blueprints & AEC Workflows** | Native **PDF ↔ DXF** conversion, **calibrated scale measuring & quantity takeoff** with vector snapping & a close-up magnifier (exporting to Excel/CSV or PDF annotations), CAD layer toggling & flattening, sheet-set splitting/naming/auto-linking/comparing, and red/blue **visual revision overlays**. |
| **Offline Book, Document & Manga Translation** | Commercial-safe **Apache-2.0 MADLAD-400**, **OPUS-MT**, or your own local LLM (**Ollama / LM Studio**). Includes automatic **vertical CJK (*tategaki*)** support with furigana stripping, **Manga / Comic Speech-Bubble Mode v2** (shape-aware oval/rect/polygon masks, dark bubbles, comic lettering), `bilingual-pages` book export, and a **live bilingual reading pane**. |
| **Local AI Document Assistant** | **Ask this PDF** with clickable `[p. N]` page citations, **Document / Chapter Summarizer** (Key Bullets, Executive, Study Notes), and **Structured Table / Invoice / Key-Value Extraction** to JSON & CSV — powered by your own Ollama, LM Studio, or OpenAI-compatible endpoint. |
| **Book & Manga Publishing Studio** | Lossless **`.cbz` ↔ PDF** conversion (with `ComicInfo.xml` & RTL manga direction), one-shot `.cbz` speech-bubble translation, **LTR & RTL booklet imposition** with multi-signature sections (8/16/32) & creep compensation, and a **POD spine & full-wrap cover calculator**. |
| **Built-in Multi-Script OCR & Typo Editor** | Shipped **PP-OCR / ONNX** engine (multi-column, vertical CJK, dark-banner inverted retries, ligature cleanup, warm worker pool) + optional Tesseract/OCRmyPDF. Fix any recognized word in place with **Check OCR** & **Find & Replace** directly on the invisible text layer — without touching page pixels. |
| **True Automation Parity** | One modular engine powers the **Desktop App**, **65+ `pdfm` CLI commands**, the **Python library**, saved multi-step **Recipes**, background **Watch Folders** (with system tray support), **Smart Auto-Filing**, and **Local Semantic Folder Search**. |

---

## Install

### Desktop Builds (Windows, macOS, Linux)

Download the latest release (`v0.75.0`) from [**ZPDF-releases**](https://github.com/Tempura-446/ZPDF-releases/releases/latest) — no GitHub account required. Every artifact includes a matching `.sha256` checksum, and installed copies include an in-app **Update** button (**Settings → Updates**) that verifies SHA-256 before updating.

| Platform | Recommended Download | Alternative / Portable | Notes |
| --- | --- | --- | --- |
| **Windows (x64)** | `ZPDF-<ver>-windows-standalone.zip` *(no Python needed)* | `ZPDF-<ver>-windows.zip` *(source installer)* | Unzip and run `ZPDF.exe`, or run `Install ZPDF.cmd` for Start menu shortcuts, "Open with", File Explorer right-click menu, and an entry in *Settings → Apps* (no admin rights required). |
| **macOS (Apple Silicon / Intel)** | `ZPDF-<ver>-macos.dmg` | `ZPDF-<ver>-macos.zip` | Drag **ZPDF.app** to Applications. Because the app is not yet Apple-notarized, if Gatekeeper warns on first launch, run `xattr -cr /Applications/ZPDF.app`. |
| **Linux (x86_64)** | `zpdf_<ver>_amd64.deb` *(Debian / Ubuntu)* | `ZPDF-<ver>-linux.zip` *(portable archive)* | Debian/Ubuntu: `sudo apt install ./zpdf_<ver>_amd64.deb`. Portable: unzip and run `./ZPDF`. |

<details>
<summary><strong>Windows source installer options &amp; Smart App Control notes</strong></summary>

1. Download **`ZPDF-<version>-windows.zip`** from [ZPDF-releases](https://github.com/Tempura-446/ZPDF-releases/releases/latest), unzip it, and double-click **`Install ZPDF.cmd`** (choose *More info → Run anyway* if SmartScreen prompts; it is a plain text script invoking `packaging/windows/install.ps1`).
2. A **ZPDF Setup** window opens (drawn by Microsoft Edge, local-only) where you can choose Start menu & desktop shortcuts, "Open with" / Default apps, File Explorer right-click actions, and optional **Tesseract + Ghostscript** extras (~150 MB via `winget`, adding a second OCR engine and fallback PDF/A converter).
3. It installs Python from python.org if missing, installs ZPDF into `%LOCALAPPDATA%\PDF Manager\app` with no administrator rights, and runs under PSF-signed `pythonw.exe` (so Windows **Smart App Control** allows it even where unsigned standalone `.exe` builds are blocked).
4. Supported flags: `-Console` (terminal mode), `-NoContextMenu`, `-WithExtras`, `-NoExtras`. Running the installer again updates the installed copy.

</details>

### With Python (`pip`)

Requires **Python 3.10 or newer**:

```bash
pip install -e ".[all]"              # everything: desktop window, built-in OCR, offline translation & Tesseract OCRmyPDF
pip install -e ".[app,ocr,translate]" # desktop window + built-in OCR + offline translation
pip install -e ".[app,ocr]"          # desktop window + built-in OCR
pip install -e ".[app]"              # desktop window only (~175 MB smaller)
pip install -e .                     # CLI + Python library (GUI opens in your browser)
```

On Linux, the native desktop window also uses a web engine: `pip install "pywebview[qt]"` (or GTK packages; see [pywebview installation](https://pywebview.flowrl.com/guide/installation.html)). Without it, `pdfm gui` automatically falls back to an app window in Edge/Chrome/Chromium/Brave or your default browser.

### Building a Standalone App from Source

```bash
pip install -e ".[build]"
python packaging/build_app.py        # outputs standalone bundle into dist/
```

---

## Core Strengths

### Local AI Document Assistant

Run private AI workflows over sensitive PDFs using your own local **Ollama**, **LM Studio**, or any OpenAI-compatible endpoint (configured once in **Settings**; all requests honor ZPDF's loopback/HTTPS & enterprise network policies):

- **Ask this PDF:** Lightweight local lexical page ranking fits large multi-hundred-page PDFs into local 7B model context windows and returns answers with **clickable `[p. N]` page citation chips** that jump straight to the source page.
- **Document & Chapter Summarizer:** Summarize full documents or custom page ranges in **Key Bullets**, **Executive**, or **Study Notes** styles with page citations.
- **Structured Extraction:** Pull invoices, forms, and tables into clean **JSON** or **CSV** with one-click Copy / Download buttons.

Accessible from the Reader **More** menu and Command Palette (`Ctrl+K`).

---

### Offline PDF, Book & Manga Translation

Translate entire PDFs, books, and comics offline with zero cloud API fees or data leaks:

- **Multiple engines:** Commercial-safe **Apache-2.0 MADLAD-400** (400+ languages), fast **OPUS-MT** language packs, or your own local/remote LLM. Protects inline `` `code` ``, semantic versions (`v1.2.3`), `snake_case` identifiers, and currency amounts during neural translation.
- **Layout modes:** `side-by-side`, `bilingual-pages` (book-focused alternating pages), `bilingual`, `in-place` (column-aware reading order, Unicode de-hyphenation, bold subheading separation, progressive line-leading tightening), `manga`, and `text` — plus a **Live Bilingual Reading Pane** inside the Reader.
- **Vertical CJK (*Tategaki*) Books:** Automatic detection of vertical Japanese and Chinese layouts, furigana/ruby stripping, vertical punctuation normalization, and top-to-bottom / right-to-left paragraph merging.
- **Manga / Comic Speech-Bubble Mode v2:** 100% offline connected-component bubble detector that classifies rectangular narration boxes, oval speech bubbles, and irregular/spiky shout bursts; handles **dark/inverted bubbles** with white text; uses a **zero-OCR fast path** for digital manga; strips furigana; erases with Gaussian-feathered masks that preserve artwork and outlines; and typesets with elliptical/balanced comic wrapping, bold shouts, and italic thoughts.

```bash
pdfm translate novel_jp.pdf --from ja --to en --mode bilingual-pages
pdfm translate chapter01.pdf --from ja --to en --mode manga
```

---

### Press-Room CMYK Preflight & Output Preview

Built for print production without requiring Acrobat Pro or Ghostscript:

- **PDF/X-1a & PDF/X-4 Compliance (`--profile standard|pdfx-1a|pdfx-4`):** Audits ISO 15930 rules — `GTS_PDFX` `/OutputIntents`, `/Info /Trapped`, `/TrimBox`/`/ArtBox`, unembedded fonts, low-DPI images, bleed, hairlines, encryption/JavaScript prohibitions, and transparency rules.
- **Recursive Form XObject Inspection:** Traverses nested `/Subtype /Form` XObjects (from InDesign, Illustrator, Figma, Canva) with CTM composition to catch hidden RGB logos, low-DPI placed art, >300% TAC fills, hairlines, and white overprint.
- **Overprint, TAC & Spot-Ink Audit:** Flags critical **white overprint** hazards (white text/vectors that would vanish on press), small pure-black knockouts, over-limit **Total Ink Coverage (TAC)** across vectors and CMYK images, >2 spot inks, and accidental `/Separation /All` (400% registration ink).
- **True CMYK Plate Separation, TAC Heatmap & Live Eyedropper:** Inside the Reader's Output Preview, inspect individual **C, M, Y, and K plates**, view a quantitative **TAC Heatmap**, and hover anywhere to read live **`C% · M% · Y% · K%` and `TAC%`** under your cursor.
- **One-Click Pure-Python Press Fixes:** Fix white overprint, convert small rich-black text (`<= 12 pt`) to crisp `100% K` with overprint, clamp TAC with GCR preservation, thicken hairlines (`< 0.25 pt`), convert `DeviceRGB` vectors & images to `DeviceCMYK`, and attach PDF/X `OutputIntent` — all in pure Python.

```bash
pdfm preflight brochure.pdf --profile pdfx-4 --max-tac 300
pdfm color brochure.pdf --to cmyk --icc FOGRA39.icc
```

---

### CAD, Blueprints, Takeoff & Sheet Sets

Purpose-built tools for architects, engineers, and estimators — comparable to Bluebeam workflows at zero subscription cost:

- **Native PDF ↔ DXF:** Export vector drawings from PDFs to CAD `.dxf` (`to-dxf`), or plot `.dxf` (and `.dwg` via ODA File Converter) into layered, scaled PDFs (`from-dxf`).
- **Calibrated Measuring & Quantity Takeoff (`M`):** Reads embedded CAD/Bluebeam `/VP /Measure` scales or title-block scales (`1/4" = 1'-0"`, `1:100`, etc.), or calibrate from any known dimension. Supports **True paper size** and **Zoom to scale**.
- **Exact Vector Snapping & Magnifier:** Snaps to endpoints, intersections, midpoints, perpendiculars, and line edges read directly from the PDF's vector geometry; a **magnifier** + `Tab` cycling handles dense lines.
- **Full Takeoff Toolkit:** Distance, Path (wall runs), Area, Rectangle, **Cut-outs** (stairs/shafts subtracted for net area), Count (fixtures/outlets), and Angle — organized into named, color-coded **Groups** with subtotals. Export as an **Excel Quantity Schedule** (`pdfm quantities`), CSV, clipboard, or standard PDF measurement annotations (**Into PDF**).
- **Drawing Markups & CAD Layers:** Revision clouds, polylines/arrows, snapping dimensions, callouts, and AEC review stamps (*Approved as noted*, *Revise and resubmit*, *Not for construction*). Toggle nested/locked/radio **PDF Layers (OCGs)** (snapping & printing respect visible layers), save default layer visibility, or permanently **Flatten layers** (`pdfm flatten-layers`).
- **Sheet-Set Automation & Revision Overlay:** Split drawing sets by title-block number (`sheet-split`), label pages (`sheet-name`), auto-link detail callouts like `2/A-102` (`sheet-link`), compare two drawing sets sheet by sheet (`sheet-compare`), or overlay revisions in the Reader (**black** = unchanged, **red** = old, **blue** = new).

```bash
pdfm to-dxf floorplan.pdf --scale 1:100
pdfm from-dxf site.dxf --paper A1 --scale 1:100
pdfm sheet-link permit_set.pdf
pdfm sheet-compare rev1.pdf rev2.pdf -o diff_overlay.pdf
pdfm quantities plan.pdf                    # Excel quantity schedule
```

---

### Book & Manga Publishing Studio

- **CBZ Comic & Manga Archives:** Convert `.cbz` ↔ PDF with natural page sorting (`page_2.png` before `page_10.png`), lossless monochrome/grayscale preservation for crisp kanji & screentones, `ComicInfo.xml` metadata, zip-slip / decompression-bomb protection, and optional right-to-left (`/Direction /R2L`) viewer flags.
- **Booklet Imposition v2:** Saddle-stitch imposition supporting both **Western LTR** and **Japanese/Manga RTL** binding (`--binding ltr|rtl`), **multi-signature sections** (`--signature 8|16|32`), and **creep compensation** (`--creep` mm).
- **POD Spine & Cover Calculator:** Compute exact spine width and full-wrap cover dimensions with built-in paper stock presets (`manga_60gsm`, `uncoated_80gsm`, `white_90gsm`, `cream_90gsm`, `gloss_115gsm`, `matte_130gsm`) or custom caliper.

```bash
pdfm cbz-to-pdf volume01.cbz --rtl
pdfm pdf-to-cbz volume01.pdf --format webp --dpi 150
pdfm booklet zine.pdf --binding rtl --signature 16 --creep 0.25
pdfm spine 240 --paper cream_90gsm --trim-w 148 --trim-h 210
```

---

### Built-in OCR, Scanning & In-Place OCR Typo Editor

- **Zero-Setup Built-in OCR:** Ships with PP-OCR on ONNX Runtime — reads English, European languages, Chinese, and Japanese out of the box (plus Russian, Ukrainian, Korean, Arabic, Persian, Hindi, Thai, Greek, Tamil, Telugu via one-time SHA-256-verified ~10 MB script packs). Handles auto-rotation, multi-column splitting, upright vertical CJK (*tategaki*), border-safe background flattening, 2× unsharp + inverted-polarity retries on dark banners, ligature normalization, and a warm worker pool. **Tesseract / OCRmyPDF** remains available as a second engine (`--engine tesseract`).
- **In-Place OCR Typo Editor (`Check OCR`):** Inspect low-confidence words or switch to **All words** mode to click-edit *any* invisible OCR word — or run inline **Find & Replace** across the invisible text layer with proportional horizontal scaling and full Undo/Redo, without ever altering page pixels.
- **Scan Paper & Phone Photos:** Scan directly from Windows WIA scanners, driverless network scanners (**AirScan / eSCL / Mopria** with duplex support), TWAIN scanners via **NAPS2**, any scanner's watch folder, or **phone photos** (automatic four-corner perspective flattening, shadow cleanup, deskew, blank-page removal, OCR, and sharp-scan compression).

```bash
pdfm ocr scan.pdf                           # built-in OCR (add --language rus, kor, ara, ...)
pdfm clean scan.pdf --rotate                # remove blank pages & auto-rotate upright
pdfm scan --feeder --mode colour --dpi 300 -o pile.pdf
pdfm photos-to-pdf IMG_01.jpg IMG_02.jpg -o receipt.pdf --look colour
```

---

### Permanent Redaction, Digital Signatures & Enterprise Security

- **True Permanent Redaction (`redact`):** Removes underlying text operators from content streams and Form XObjects, blacks out covered pixels inside embedded images, strips intersecting annotations/links/form fields and structure-tree `/ActualText`/`/Alt`, scrubs bookmark titles, and runs a **post-write verification check** reporting if any redacted term remains anywhere in the file. Pages that cannot be safely vector-rewritten automatically fall back to rasterization (`--method image` forces full rasterization).
- **Digital ID Signing & Verification (`sign-cert`, `verify`):** Sign with `.pfx`/`.p12` certificates or the Windows certificate store (visible or invisible, incremental append so prior signatures stay valid, optional RFC 3161 TSA timestamping) and verify integrity, certificate trust, and post-signing modifications via `pyHanko`.
- **Encryption & Malware Disarming:** AES-256 password encryption with granular permissions (and automatic password retention on edits), **certificate encryption for recipients** (`encrypt-for` / `open-with-id`), weak RC4-to-AES-256 upgrades (`upgrade-protection`), metadata/script stripping (`sanitize`), and **`make-safe`** to disarm untrusted PDFs (stripping scripts, launch/URI actions, and executable attachments while preserving text, comments, and form calculations).
- **Enterprise Governance:** Reusable **Security Profiles** (`pdfm profile`), a **SHA-256 hash-chained tamper-evident Audit Log** (`pdfm audit verify`), and **Enterprise Administrator Policies** (`pdfm policy`).

```bash
pdfm redact contract.pdf -t "John Smith" --find emails --find phones
pdfm sign-cert contract.pdf --pfx me.pfx --page last --timestamp http://timestamp.digicert.com
pdfm verify contract_signed.pdf
pdfm make-safe untrusted_invoice.pdf
pdfm audit verify
```

---

### Standards Compliance: PDF/A & PDF/UA Accessibility

- **PDF/A Archiving (`pdfa`, `check-pdfa`):** Converts to **PDF/A-1b, -2b, or -3b** directly in ZPDF with zero external installs (embedding matching-metric system fonts so lines never reflow, attaching sRGB output intents, and removing forbidden constructs; falls back to Ghostscript when needed). `check-pdfa` validates against ISO 19005 with exact clause numbers (and automatically delegates to **veraPDF** when installed).
- **PDF/UA Accessibility (`tag`, `check-access`):** Auto-tags PDFs for screen readers (headings without skipped levels, paragraphs, lists, tables with header cells, figure captions, links, form fields, reading order, artifact decoration for headers/footers, document language, title, and tab order) and runs an Acrobat-style Accessibility Checker reporting every issue and how to fix it.

```bash
pdfm pdfa report.pdf --level 2
pdfm check-pdfa *.pdf
pdfm tag report.pdf --lang en
pdfm check-access *.pdf
```

---

### Conversions

All built-in conversions work offline with no cloud services:

- **PDF → Word (`to-word`):** Uses Microsoft Word when installed, or ZPDF's built-in layout converter (headings, paragraphs, lists, tables, pictures, links in reading order) or LibreOffice (`--engine layout`).
- **PDF → Excel / CSV (`to-excel`):** Detects ruled and borderless tables (joining multi-page tables and dropping repeated headers), writes real typed numbers/currencies/percentages, and adds a linked **Summary** sheet.
- **PDF → PowerPoint (`to-pptx`):** One slide per page with **editable text boxes** over the non-text background (or `--pictures`), preserving fonts, styles, alignment, rotation, and links — no Office needed.
- **PDF → HTML (`to-html`) & Markdown (`to-md`):** Single-file HTML with embedded images (flowing reflowable text or `--exact` visual layout) and structured Markdown with optional YAML front matter (`--front-matter`) and extracted image assets (`--images`).
- **Office, Web & Images → PDF:** `from-office` (Word/Excel/PowerPoint/ODF/RTF via Office or LibreOffice), `web` (URLs & `.html`/`.mht` via Edge/Chrome), `from-images` (auto-oriented photos & multi-page TIFFs), `to-images` (PNG/JPG rendering).

---

### Automation: Recipes, Watch Folders, Auto-Filing & Semantic Search

- **Multi-Step Recipes (`recipe`):** Chain any sequence of tools (e.g., *Clean up scans → OCR → Compress → Page numbers*), save by name, and reuse across the GUI, CLI, and Watch Folders.
- **Watch Folders (`watch` & Settings → Watch folders):** Automatically process PDFs dropped into scanner inboxes or Downloads with OCR, compression, PDF/A, or any saved Recipe. Waits until downloads/copies finish (`.crdownload`/`.part`-safe), tracks processed files across restarts, and can stay running in the **Windows system tray** (with optional Start-at-sign-in).
- **Smart Auto-Filing (`auto-file`):** Content-rule-based renaming and folder routing with dry-run preview by default and one-command batch `--undo`.
- **Local Semantic Folder Search (`search`):** Search every PDF in a folder tree by exact phrase or natural-language question (e.g. `"how much do I owe"` finds `"balance due"` using a bundled 4 MB local semantic table + SQLite text index — 100% offline).

```bash
pdfm recipe save "Scans" clean ocr language=eng compress level=high number "format=Page {n}"
pdfm recipe run "Scans" *.pdf -o done/
pdfm watch ~/Scans/inbox --dest ~/PDFs --by month --recipe "Scans" --skip-duplicates
pdfm search ~/Documents "how much do I owe" --best -R
pdfm auto-file ~/Inbox --apply
```

---

## Desktop App & Reader

Start the desktop app with **`pdfm-app`** (no terminal window), **`pdfm gui`**, or **`pdfm view report.pdf`**. It opens in a native window (`pywebview`), falling back seamlessly to an Edge/Chrome app window or browser tab.

- **Fast Streaming Reader (`pdf.js`):** Built on bundled offline `pdf.js`. Large PDFs (>4 MB) stream on demand — a 277 MB drawing set shows page 1 in ~1 second using a few MB of RAM.
- **4 Reading Themes:** Switch between **Light**, **Smart Night**, **Warm Sepia**, and **E-Ink High Contrast**, synced across split-view panes.
- **Split View & Interactive Side-by-Side Diff Viewer:** Compare two PDFs or two pages side by side with synced scrolling, diff highlights, and a change jumper — or open the **Live Bilingual Reading Pane** for instant page translation.
- **Workspace Sessions:** Save, restore, and switch named multi-tab snapshots (open files, active tab, page positions, zoom, rotation, and split-view layout).
- **Direct Text & Picture Editing (`E`):** Edit lines or `Shift+click` to rewrite full paragraphs with column-aware reflow, preserved bold/italic/color/links/superscripts, embedded fonts, `Shrink to fit`, and optional `require_permanent` & `verify_visual` checks. Move, resize, replace, or delete embedded pictures in place.
- **Interactive Forms & Calculations:** Auto-detect fields on printed or scanned forms (**Make it fillable**), draw/edit text, multiline, checkbox, radio, dropdown, and signature fields, and run Acrobat-compatible validation, formatting, and calculations (`sum`, `average`, `product`, `min`, `max`).
- **Full Annotation & Page Suite:** Highlights, notes, freehand drawing, shapes, custom reusable stamps, XFDF comment export/import & PDF summary reports, visual drag-and-drop page reordering/extract/delete/rotate/insert, visual + keyboard crop & redaction, live previews for watermarks/headers/Bates numbers/n-up, and **Ctrl+K** Command Palette.
- **File Explorer Integration (Windows):** Right-click PDFs or images in Explorer for *Combine in ZPDF*, *Make searchable (OCR)*, *Make smaller*, *Open in ZPDF*, or *Make a PDF with ZPDF* (on Windows 11, under *Show more options* / `Shift+right-click`).
- **Mobile & Touch Companion:** Built-in installable **PWA shell** (`manifest.webmanifest` + static-only `sw.js` that bypasses `/api/*`), touch-first swipe/double-tap reader with a 44px floating mobile bottom dock, and a ready-to-build **Android Kotlin WebView companion** in [`mobile/android/`](mobile/android/).

<details>
<summary><strong>Reader keyboard shortcuts</strong></summary>

| Key | Action |
| --- | --- |
| `Ctrl/⌘+O` | Open a PDF |
| `Ctrl/⌘+K` or `/` | Command palette (tools, actions, page numbers, zoom) |
| `Ctrl/⌘+F`, `Enter` / `Shift+Enter`, `Esc` | Find, next / previous match, close |
| `Ctrl/⌘ +` / `−` / `0` | Zoom in / out / automatic |
| `N` or `J`, `P` or `K`, `Home`, `End` | Next, previous, first, last page |
| `R`, `Shift+R` | Rotate view clockwise / counter-clockwise |
| `E` | Edit text on / off |
| `M` | Measure on / off (`Esc` stops, `Backspace` undoes point, `Shift` straight, `Alt` free) |
| `H`, `C`, `A`, `D`, `S` | Highlight, Note, Add text, Draw, Sign (when Annotate bar is open) |
| `T` | Show / hide the file & tools panel |
| `Ctrl/⌘+Z` / `Ctrl/⌘+Y` | Undo / redo |
| `Ctrl/⌘+S` / `Ctrl/⌘+Shift+S` / `Ctrl/⌘+P` | Save, Save as, Print |

</details>

---

## Command Line

Every per-file command accepts **multiple files and wildcards** (`pdfm compress *.pdf`). With one input, `-o` names the output file; with several, `-o` is an output folder preserving original names. Without `-o`, results are written beside each input as `<name>_<command>.pdf`.

Page ranges are 1-based: `1-3,5` · `7-` · `-3` · `end` · `5-1` (reversed) · `odd` · `even` · `all`. Run `pdfm <command> -h` for full options.

### Compression Levels (`pdfm compress`)

| Level | What it does | Best for |
| --- | --- | --- |
| `low` | Lossless: recompresses streams, packs uncompressed fonts/images, strips ASCII85/hex wrappers, deduplicates objects, and repacks with `qpdf` object streams | Archiving, print masters |
| `medium` *(default)* | Also downsamples images above **150 DPI** (JPEG q75) and splits scanned pages into sharp full-res text + low-res paper + color ink layers | Everyday sharing, office printing |
| `high` | Images reduced to **96 DPI** (JPEG q50) with sharp-scan layer compression (`--no-sharp-scans` disables) | Email, web, smallest files |

<details>
<summary><strong>Full CLI command reference (65+ commands)</strong></summary>

#### App, Reading & Inspection
| Command | What it does | Example |
| --- | --- | --- |
| `gui` | Open the desktop app | `pdfm gui` |
| `view` | Open PDF(s) directly in the reader | `pdfm view report.pdf` |
| `info` | Page count, page sizes, metadata (`--json`) | `pdfm info *.pdf` |
| `text` | Extract plain text | `pdfm text doc.pdf -o doc.txt` |
| `compare` | Visual & text diff report between two PDFs | `pdfm compare v1.pdf v2.pdf` |
| `comments` | List, summarize (PDF/Markdown), CSV, or XFDF export/import | `pdfm comments doc.pdf --summary` |
| `privacy` | Inspect/clear local history and network policy | `pdfm privacy --clear-history` |

#### Pages, Layout & Publishing
| Command | What it does | Example |
| --- | --- | --- |
| `merge` | Combine PDFs (optionally picking page ranges) | `pdfm merge a.pdf b.pdf:1-3 -o all.pdf` |
| `split` | Split per page, every N pages, by range, bookmark, or max MB | `pdfm split book.pdf --bookmarks` · `--max-size 10` |
| `extract` / `delete` | Copy or remove selected pages | `pdfm extract doc.pdf -p 2,4-6` |
| `reorder` / `reverse` | Rearrange, repeat, or reverse pages | `pdfm reorder doc.pdf --order 3,1,2,4-` |
| `insert-blank` | Insert blank pages after specific pages or every page | `pdfm insert-blank slides.pdf --after all` |
| `interleave` | Merge separately scanned fronts and backs | `pdfm interleave fronts.pdf backs.pdf -o book.pdf` |
| `rotate` / `resize` | Rotate pages or resize (`a0`–`a6`, `letter`, `210x297mm`, `--scale`) | `pdfm resize doc.pdf -s a4` |
| `crop` | Trim margins (mm) or auto-trim white margins (`--auto`) | `pdfm crop doc.pdf --auto --padding 3` |
| `nup` | 2/4/6/8/9 pages per sheet (preserves internal links) | `pdfm nup slides.pdf -n 4` |
| `booklet` | LTR/RTL saddle-stitch imposition, multi-signature & creep | `pdfm booklet zine.pdf --binding rtl --signature 16` |
| `spine` | Print-on-demand spine width & full-wrap cover calculator | `pdfm spine 240 --paper cream_90gsm --trim-w 148 --trim-h 210` |
| `watermark` / `number` | Text/PDF watermarks and page numbers | `pdfm watermark doc.pdf -t CONFIDENTIAL` |
| `headers` | Headers, footers & continuous Bates numbers | `pdfm headers *.pdf --bottom-right "{bates}" --bates ABC` |
| `bookmarks` / `labels` | Edit/detect bookmarks; set page labels (`i, ii, 1, 2`) | `pdfm bookmarks doc.pdf --detect` |
| `metadata` / `attach` | View/edit metadata; add/list/remove embedded attachments | `pdfm attach report.pdf --add data.xlsx` |
| `form` | Auto-detect, create, list, or edit fillable form fields & calculations | `pdfm form printed.pdf --detect` |
| `flatten` / `repair` | Flatten forms & annotations; mend damaged PDFs / fast web view | `pdfm repair broken.pdf` |

#### OCR, Translation, Print Preflight & Compliance
| Command | What it does | Example |
| --- | --- | --- |
| `ocr` | Make scans searchable (built-in PP-OCR or `--engine tesseract`) | `pdfm ocr scan.pdf --language rus` |
| `clean` | Remove blank pages and auto-rotate scans upright | `pdfm clean scan.pdf --rotate` |
| `scan` / `photos-to-pdf` | Scan from WIA/AirScan/NAPS2 or flatten phone photos to searchable PDF | `pdfm photos-to-pdf IMG_*.jpg -o receipt.pdf` |
| `translate` | Offline PDF/book/manga translation (`side-by-side`, `bilingual-pages`, `in-place`, `manga`, `text`) | `pdfm translate book.pdf --from ja --to en --mode bilingual-pages` |
| `compress` / `color` | Compress (`low`/`medium`/`high`) or convert grayscale/RGB/CMYK + ICC | `pdfm compress *.pdf -l high -o small/` |
| `preflight` | Print-production CMYK & PDF/X-1a/4 preflight (`--profile`, `--max-tac`) | `pdfm preflight brochure.pdf --profile pdfx-4` |
| `pdfa` / `check-pdfa` | Convert to PDF/A-1b/2b/3b and validate against ISO 19005 (or veraPDF) | `pdfm pdfa doc.pdf --level 2` |
| `tag` / `check-access` | Auto-tag PDFs for screen readers and audit PDF/UA accessibility | `pdfm tag report.pdf --lang en` |

#### CAD, Blueprints & Sheet Sets
| Command | What it does | Example |
| --- | --- | --- |
| `to-dxf` / `from-dxf` | Convert vector PDFs ↔ CAD DXF (and DWG via ODA) | `pdfm to-dxf plan.pdf --scale 1:100` |
| `quantities` | Export drawing measurements & takeoff schedule to Excel/CSV | `pdfm quantities plan.pdf` |
| `flatten-layers` | List, selectively hide/show, and permanently flatten CAD layers (OCGs) | `pdfm flatten-layers plan.pdf --hide Notes` |
| `sheet-split` / `sheet-name` | Split drawing sets or set page labels from title-block sheet numbers | `pdfm sheet-split set.pdf` |
| `sheet-link` / `sheet-compare` | Auto-link detail callouts (`2/A-102`) or compare drawing sets sheet-by-sheet | `pdfm sheet-compare rev1.pdf rev2.pdf -o diff.pdf` |

#### Conversions
| Command | What it does | Example |
| --- | --- | --- |
| `to-word` / `to-excel` | Convert to `.docx` or extract tables to `.xlsx`/CSV | `pdfm to-excel report.pdf -p 2-5` |
| `to-pptx` / `to-html` / `to-md` | Convert to editable `.pptx`, single-file `.html`, or structured `.md` | `pdfm to-md report.pdf --images --front-matter` |
| `cbz-to-pdf` / `pdf-to-cbz` | Convert comic/manga `.cbz` archives ↔ PDF | `pdfm cbz-to-pdf vol1.cbz --rtl` |
| `from-office` / `web` | Convert Office docs or web pages (`.html`/URL) to PDF | `pdfm web https://example.com` |
| `from-images` / `to-images` / `extract-media` | Build PDFs from images, render pages to PNG/JPG, or extract media/attachments | `pdfm from-images *.jpg -o photos.pdf -s a4` |

#### Security, Redaction & Governance
| Command | What it does | Example |
| --- | --- | --- |
| `redact` | Permanent content-stream/image redaction + post-write verification | `pdfm redact doc.pdf -t "John Smith" --find emails` |
| `encrypt` / `decrypt` | AES-256 password protection & permissions (preserved across edits) | `pdfm encrypt doc.pdf --no-copy` |
| `encrypt-for` / `open-with-id` | Certificate-based encryption for specific recipients (`.cer`) | `pdfm encrypt-for doc.pdf --cert ann.cer` |
| `upgrade-protection` | Upgrade weak RC4 encryption to AES-256 keeping passwords/limits | `pdfm upgrade-protection old.pdf` |
| `sign-cert` / `verify` | Sign with `.pfx`/`.p12` Digital ID (with optional TSA) and verify | `pdfm sign-cert doc.pdf --pfx me.pfx --page last` |
| `sanitize` / `make-safe` | Strip metadata/scripts/attachments or disarm untrusted PDFs | `pdfm make-safe invoice.pdf` |
| `profile` / `audit` / `policy` | Apply security profiles, verify SHA-256 audit log, inspect admin policies | `pdfm audit verify` |

#### Batch, Folders & Automation
| Command | What it does | Example |
| --- | --- | --- |
| `recipe` | Save, list, and run multi-step tool pipelines | `pdfm recipe run "Scans" *.pdf -o done/` |
| `watch` | Watch a folder and auto-process incoming PDFs (`--once` for cron/Task Scheduler) | `pdfm watch ~/Scans --recipe "Scans"` |
| `auto-file` | Content-rule-based renaming & filing (`--apply`, `--undo`) | `pdfm auto-file ~/Inbox --apply` |
| `search` | Full-text and local semantic search (`--best`) across folder trees | `pdfm search ~/PDFs "balance due" --best -R` |
| `organize` / `rename` | Sort PDFs by year/month/author/pages/size or rename from metadata | `pdfm organize ~/Downloads --by year --dest ~/PDFs --move` |
| `duplicates` / `catalog` | Find/delete byte-identical PDFs or export a CSV folder inventory | `pdfm duplicates ~/PDFs -R` |
| `setup-windows` | Manage Windows shortcuts, "Open with", and uninstall registration | `pdfm setup-windows` |

</details>

---

## Python Library

```python
from pdfmanager import core, organize, preflight

core.merge(["a.pdf", ("b.pdf", "1-3")], "out.pdf")
core.resize("letter.pdf", "a4.pdf", size="a4")
core.compress("scan.pdf", "small.pdf", level="high")
core.convert_color("report.pdf", "print.pdf", "cmyk", icc_profile="printer.icc")
core.split("book.pdf", "chapters", ranges=["1-10", "11-"])
core.booklet("zine.pdf", "zine_booklet.pdf", binding="rtl", signature=16, creep_mm=0.25)

report = preflight.check("brochure.pdf", profile="pdfx-4", max_tac=300.0)
organize.organize("inbox", by="author", dest="library", move=True, duplicates="skip")
```

---

## Privacy & Security

- **Local-First & Zero Telemetry:** Nothing about you or your documents is ever sent anywhere. The embedded server binds exclusively to `127.0.0.1` with a per-session random token. Working files stay in ZPDF's private data folder and are cleaned up on completion/exit/crash recovery.
- **Explicit Network Boundaries:** ZPDF only accesses the network when you ask: optional update checks (toggleable in Settings), downloading an update or OCR/translation model pack (verified against SHA-256), RFC 3161 signing timestamps, `web` URL-to-PDF conversion, clicking an external link, or calling a user-configured AI/translation endpoint.
- **History Control:** **Settings → Privacy** (or `pdfm privacy --clear-history` / `--remember off`) controls recent files, reading positions, and the local folder search index.
- **Security Hardening & Honest Limitations:**
  - **Permanent redaction vs. visual covering:** `redact` (`method="remove"`) strips content-stream & Form XObject operators, blacks out covered image pixels, scrubs annotations/bookmarks/structure text, and verifies the written file; review the `still_in` report before sharing sensitive documents, and use `--method image` when text is baked into raster scans.
  - **Text editing integrity:** `edit_text` supports `require_permanent=True` (refusing visual cover-up fallback) and `verify_visual=True` (before/after overlap/clipping checks).
  - **Digital signatures:** Mutating a signed PDF invalidates prior cryptographic signatures; both GUI and CLI warn before editing signed files. Online OCSP/CRL revocation checks require explicit opt-in.
  - **Password & path hygiene:** Passwords and private-key passphrases are scrubbed from logs (`logs/zpdf.log`) and errors; attachment and CBZ extraction guard against path traversal, symlinks, and decompression bombs.
  - **Code signing:** Windows and macOS binaries are not yet Authenticode-signed or Apple-notarized (SmartScreen / Gatekeeper may warn on first launch). See [`docs/RELEASE_READINESS.md`](docs/RELEASE_READINESS.md) for the full competitive matrix and release-readiness assessment.

---

## Tests, Benchmarks & Releasing

```bash
pip install -e ".[test]"
pytest                             # full test suite
pytest --perf                      # memory & streaming performance limits
python3 packaging/benchmark.py --pages 10 100
```

- **Robustness & Maturity Sweep:** `tests/test_robustness.py` tests every tool against malformed and hostile PDFs (`tests/badpdfs.py`), ensuring graceful errors with zero crashes or hangs. `tests/sweep/make_corpus.py` and `run_sweep.py` run isolated time- and memory-bounded sweeps across hundreds of real-world PDFs (plus `tests/test_imports.py` for circular-import prevention and `tests/test_missing_programs.py` for graceful degradation).
- **Releasing:** Maintainers publish to [ZPDF-releases](https://github.com/Tempura-446/ZPDF-releases/releases) via `python packaging/release.py` (`--check`, `--dry-run`, or full publish), reading the version from `pdfmanager/__init__.py` and release notes from `CHANGELOG.md`. Optional hash-pinned dependencies (`packaging/windows/requirements-lock.txt`, generated via `packaging/lock.py`) enforce `pip install --require-hashes`. See [`docs/pc-runner.md`](docs/pc-runner.md) for running full Windows CI locally.

---

## Licence

ZPDF's source code is public so you can read it, but it is not open source. It is licensed under the [PolyForm Internal Use License 1.0.0](https://polyformproject.org/licenses/internal-use/1.0.0), with an added permission for personal use: you may use it, at home or at work, and change it for your own use, but not pass it on to anyone else, publish your own version of it, or sell it. To use ZPDF, download it from [ZPDF-releases](https://github.com/Tempura-446/ZPDF-releases/releases). It comes as is, without warranty. See [LICENSE](LICENSE) for the full terms.

The name ZPDF and its icon are for this software only; see [TRADEMARKS.md](TRADEMARKS.md). To report a security problem, see [SECURITY.md](SECURITY.md).

### Copies that break the licence

If you find a copy of ZPDF on GitHub that someone has published, passed on or sold against the licence (a fork with its own releases, say, or ZPDF under another name), the owner can ask GitHub to take it down with a DMCA takedown notice: see [GitHub's DMCA Takedown Policy](https://docs.github.com/en/site-policy/content-removal-policies/dmca-takedown-policy) and the form at <https://github.com/contact/dmca>. A fork made only to read the code or to suggest a change here is fine and needs nothing. If you spot such a copy, open an issue with the link, or tell the owner privately.
