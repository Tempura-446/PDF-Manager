# ZPDF v0.69.0 — Print-Production CMYK Preflight v2

**Date:** 2026-11-24  
**Target Release:** `v0.69.0`  
**Status:** Approved

---

## 1. Goal
Upgrade ZPDF's print preflight (`pdfmanager/preflight.py`, CLI `preflight`, HTTP `/api/preflight/*`, and Reader `checkPreflight()` UI) into a full commercial **Print-Production CMYK Preflight & Output Preview Suite** with zero new dependencies (100% pure Python + NumPy + Pillow + `pypdf` + PDFium):

1. **PDF/X-1a & PDF/X-4 Compliance Profiles (`profile="standard" | "pdfx-1a" | "pdfx-4"`)**:
   - Inspects `/OutputIntents` for a valid `GTS_PDFX` entry (`/OutputConditionIdentifier` or `/DestOutputProfile`).
   - Checks `/Info` dictionary `/Trapped` key (`/True` or `/False`), `/TrimBox` or `/ArtBox` presence (and prohibits both on the same page per ISO 15930), encryption prohibition, active JavaScript/launch actions, RGB colour prohibition (`error` in `pdfx-1a` and `pdfx-4` without ICC intent), and live transparency prohibition in `pdfx-1a` (`error`, vs allowed in `pdfx-4`).
2. **Overprint & Knockout Audit**:
   - Tracks `/ExtGState` `/OP`, `/op`, and `/OPM` parameters across graphics state saves/restores (`q`/`Q` + `gs`).
   - Flags **`white_overprint` (`error`)** whenever white vector fills/strokes or white text (`0/0/0/0 k`, `1/1/1 rg`, `1 g`) are drawn with overprint enabled — preventing the classic press failure where white elements vanish completely on print.
   - Flags **`black_knockout_text` (`warning`)** when pure-K small body text (`<= 12 pt`, `0/0/0/1 k`) explicitly sets `/op false` (knocking out underlying ink and risking white halos on press).
3. **Total Ink / Area Coverage (TAC) Check (`max_tac=300.0`)**:
   - Audits both vector CMYK fills/strokes (`(C + M + Y + K) * 100`) and embedded CMYK `Image` XObjects (computing peak and high-percentile ink coverage).
   - Reports document `max_tac_pct` and per-page `max_tac_pct`, flagging **`high_tac`** (`warning`, or `error` when `> max_tac + 40`) with bounding `rect` for oversized CMYK image hot-spots.
4. **Safe One-Click Print Fixes (`preflight.fix`)**:
   - `fix_white_overprint=True`: neutralizes `/OP` / `/op` overprint before white vector/text draws or clears `/OP`/`/op` in offending ExtGStates when safe.
   - `add_pdfx_intent=True`: writes `/Trapped /False` in `/Info`, ensures `/TrimBox` is set on every page, and attaches a standard `GTS_PDFX` `/OutputIntents` entry (`CGATS TR 001` / SWOP coated).
5. **True CMYK Plate Separation & TAC Heatmap Preview (`render_plate_preview` + `POST /api/preflight/preview` + Reader UI)**:
   - Rewrites single-page PDF colour operators (`k`/`K` exact CMYK channel, `g`/`G` → K plate only, `rg`/`RG` → GCR separation) and separates Image XObjects per channel (`c`, `m`, `y`, `k`) before rendering via PDFium (`core.render_scale` OOM-guarded).
   - Pure-K text (`0/0/0/1 k`) cleanly disappears on C/M/Y plates and appears 100% on the K plate, whereas rich-black or RGB black appears across all 4 plates.
   - `plate="tac"` renders all 4 plates (`c`, `m`, `y`, `k`), sums true `C + M + Y + K` percentage per pixel, and overlays amber (`max_tac - 30 .. max_tac`) and vivid red (`> max_tac`) ink-coverage hot-zones over a dimmed page preview.
   - Integrated directly into the Reader's **Print preflight** dialog (`reader.js` + `reader.css`) with profile selector (`Standard`, `PDF/X-1a`, `PDF/X-4`), TAC limit (`280%`, `300%`, `320%`), interactive **Output Preview** plate toggles (**Composite**, **Cyan**, **Magenta**, **Yellow**, **Black (K)**, **TAC Heatmap**), and one-click fixes (**Fix white overprint**, **Add PDF/X OutputIntent**).
