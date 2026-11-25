# ZPDF v0.70.0 — Print-Production CMYK Preflight v3 (Press-Room Grade)

**Date:** 2026-11-24  
**Target Release:** `v0.70.0`  
**Status:** Approved

---

## 1. Goal
Upgrade ZPDF's CMYK preflight engine (`pdfmanager/preflight.py`, HTTP `/api/preflight/*`, CLI, and Reader Output Preview UI) to press-room-grade **v3**, 100% pure Python + NumPy + Pillow + `pypdf` (zero Ghostscript required for fixes, zero new dependencies):

1. **Recursive Form XObject (`/Subtype /Form`) Inspection & Plate Separation**:
   - Recursively descend into nested Form XObjects (`/Fm0 Do`) with composed CTM (`form_matrix * ctm`), Form-local `/Resources`, and indirect-ref cycle guards across `check()`, `fix()`, and `render_plate_preview()`.
2. **Pure-Python One-Click CMYK Press Fixes (Zero Ghostscript Required)**:
   - `fix_rich_black_text=True`: converts small rich-black text (`<= 12 pt`, `sum(cmyk) >= 2.4`, `K >= 0.5`) to pure `0 0 0 1 k` with `/op true` overprint.
   - `clamp_tac=300.0`: GCR-preserving ink-limit clamping on both CMYK vector fills/strokes and embedded CMYK `Image` XObjects (retains K and proportionally reduces C/M/Y so `C+M+Y+K <= max_tac`).
   - `fix_hairlines=0.25`: bumps hairline strokes (`0 < eff_w < min_stroke_pt`) up to `min_stroke_pt` in content streams (and nested Form XObjects).
   - `convert_rgb_to_cmyk=True`: converts `DeviceRGB` vector colour ops (`rg`/`RG` → `k`/`K` via GCR) and `DeviceRGB` Image XObjects to `DeviceCMYK` directly in Python.
3. **Spot Ink Inventory & Registration-Colour Hazard Check**:
   - Returns `spot_colors: list[str]` listing all named `Separation` / `DeviceN` inks (excluding `All`/`None`).
   - Flags `registration_color` (`error`) whenever `/Separation /All` (400% registration ink) is used inside the page's `/TrimBox` (or `/MediaBox` when no `/TrimBox`).
   - Flags `many_spot_colors` (`warning`) when a document uses more than 2 distinct spot inks.
4. **Live Ink-Density Eyedropper (`POST /api/preflight/inkmap` + Reader UI) & One-Click "Fix All Press Issues"**:
   - `preflight.compute_ink_grid(path, password=None, *, page=1, dpi=72)` returns a compact base64-encoded 4-channel `(C, M, Y, K)` `uint8` grid (`width`, `height`, `cmyk_b64`) for the Reader's Output Preview so hovering over any pixel displays live **`C% · M% · Y% · K% · TAC%`** under the cursor with zero extra round-trips!
   - Adds a one-click **"Fix all press issues"** button in the Reader Print Preflight dialog applying `fix_white_overprint`, `fix_rich_black_text`, `fix_hairlines`, `clamp_tac`, `convert_rgb_to_cmyk`, and `add_pdfx_intent` atomically.
