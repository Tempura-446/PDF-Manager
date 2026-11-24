# ZPDF v0.68.0 — Manga / Comic Speech-Bubble Mode v2

**Date:** 2026-11-24  
**Target Release:** `v0.68.0`  
**Status:** Approved

---

## 1. Goal
Upgrade ZPDF's offline Manga / Comic speech-bubble translation engine (`pdfmanager/translate/manga.py`) from v1 (light oval bubbles + raster-only OCR) to **v2**:

1. **Shape-Aware Interior Mask Cleaning (`shape`: `"rect" | "oval" | "polygon"`)**:
   - Classify each detected bubble by fill solidity & corner occupancy:
     - Rectangular narration boxes (`rect`) → crisp inset rounded-rect cleaning.
     - Standard oval/ellipse bubbles (`oval`) → crisp inset ellipse cleaning.
     - Irregular / spiky / cloud bubbles (`polygon`) → pixel-accurate eroded interior mask (hole-filled over text ink, eroded inward by 3–5 px) composited as a transparent RGBA patch so zero ink bleeds onto the artist's outline or artwork.
2. **Dark / Inverted Speech Bubbles (`inverted=True`)**:
   - Detect both standard light bubbles (`gray >= 232`, dark ink `< 140`) and dramatic dark/black bubbles (`gray <= 45`, bright white ink `>= 190`).
   - Clean dark bubbles with their sampled dark interior color and typeset translated dialogue in crisp white (`text_rgb = (0.96, 0.96, 0.98)`).
3. **Digital Manga PDF Zero-OCR Fast Path**:
   - When a `pdfplumber` page has selectable glyphs inside a bubble's PDF bounding box, extract them directly (applying vertical CJK RTL ordering + furigana stripping automatically when vertical) without invoking OCR.
4. **Manga OCR Pre-Processing (`preprocess_bubble_for_ocr`)**:
   - Auto-inverts dark/inverted bubbles to black-on-white, upscales small crops (`min_side < 160 px`) 2× with `LANCZOS`, normalizes contrast, and adds a clean 12 px white border before OCR.
5. **Comic Lettering & Reading-Order Polish**:
   - Auto-select reading direction within horizontal bands (`rtl` for manga `ja`/`zh`/`ko`, `ltr` for Western comics `en`/`fr`/`es`/etc. via `reading_dir` parameter).
   - Balanced multi-line comic wrapping (`_wrap_balanced`) to prevent ugly single-word orphan lines inside bubbles.
   - Accurate inscribed text box per shape (`rect` uses ~84% width/height vs `oval` ~68%), giving narration boxes and narrow vertical bubbles maximum breathing room.

All algorithms remain **100% offline, commercial-safe, pure Python + NumPy + Pillow + ReportLab** (zero OpenCV, zero GPL/AGPL).
