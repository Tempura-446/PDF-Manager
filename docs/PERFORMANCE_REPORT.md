# ZPDF / PDF-Manager — Performance Benchmark Report (Phase 7 / Stage E)

> **Test Environment**: Linux `6.18.57` x86_64, 2 vCPUs, Python `3.12.16`, `pypdf 7.1.1`, `pypdfium2 5.15.2`, `pikepdf 10.18.2`, `Pillow 13.0.0`, `numpy 2.5.5`.
> **Reproducible command**: `python3 packaging/benchmark.py --pages 10 --repeats 1 --json` (and `--pages 10 100 --workloads compare redact_remove` / `--pages 500 --workloads compare`).

---

## 1. Baseline 10-Page Workload Suite (`v0.61.1`, `92b917a`)

| Workload | Pages | Median (s) | Throughput (pages/s) | Peak Python (MB) | RSS (MB) | Output (KB) |
|---|---:|---:|---:|---:|---:|---:|
| `compare` | 10 | 2.3466 | 4.26 | 11.20 | 85.25 | 213.1 |
| `compare_scanned` | 10 | 0.6769 | 14.77 | 4.50 | 85.25 | 20.0 |
| `redact_remove` | 10 | 3.8631 | 2.59 | 3.26 | 85.25 | 7.9 |
| `redact_image(150dpi)` | 10 | 1.1518 | 8.68 | 19.08 | 109.68 | 967.1 |
| `textedit` | 10 | 5.4560 | 1.83 | 3.89 | 109.68 | 15.1 |
| `batch(8x10p,w=1)` | 80 | 28.3202 | 2.82 | 2.64 | 109.68 | 7.9 |
| `batch(8x10p,w=2)` | 80 | 27.4913 | 2.91 | 2.85 | 109.68 | 7.9 |

---

## 2. Multi-Scale Benchmarks (`10` → `100` → `500` Pages, Post-Remediation `447a664`)

| Workload | Pages | Median (s) | Throughput (pages/s) | Peak Python (MB) | RSS (MB) | Output (KB) |
|---|---:|---:|---:|---:|---:|---:|
| `compare` | 10 | 2.3407 | 4.27 | 11.20 | 82.14 | 213.1 |
| `compare` | 100 | 4.7214 | 21.18 | 6.99 | 98.06 | 1,944.8 |
| `compare` | 500 | 25.1121 | 19.91 | 44.92 | 130.25 | 9,848.6 |
| `redact_remove` | 10 | 3.8535 | 2.60 | 3.26 | 82.14 | 7.9 |
| `redact_remove` | 100 | 33.9346 | 2.95 | 4.11 | 98.06 | 71.0 |

### Key Scaling Observations
- **`compare` memory control (CMP-002)**: Across a 50× increase in page count (10 → 500 pages), process RSS increased by only `48.1 MB` (`82.14 MB` → `130.25 MB`), confirming lazy on-demand rendering rather than holding all page bitmaps in memory.
- **`redact_remove` memory control**: Across a 10× increase (10 → 100 pages), peak Python memory remained flat (`3.26 MB` → `4.11 MB`) and throughput remained steady (`2.60` → `2.95 pages/s`).
- **1,000-page benchmark**: Not executed in this 2-vCPU VM due to wall-clock budget; linear extrapolation from 100→500 pages predicts ~50s and ~170 MB RSS for `compare` on 1,000 digital pages.

---

## 3. Targeted Optimization: `ocr._looks_grey()` (`PERF-001`)

- **Change**: Replaced deprecated Pillow 14 `band.getdata()` + Python `zip()` loop in `pdfmanager/ocr.py:_looks_grey()` with vectorized NumPy `int16` channel difference checks.
- **Measured before vs. after** (50 iterations on 1200×1600 RGB image):
  - **Before**: `32.082 ms` / call (emitted Pillow 14 `DeprecationWarning`)
  - **After**: `24.328 ms` / call (`1.3×` speedup, **zero** deprecation warnings; remaining time is dominated by Pillow's `resize((200, 200))`)
- **Accuracy trade-off**: None — exact same per-pixel `< 8` RGB channel tolerance.

---

## 4. Known Performance Limitations

- **GIL-bound batch threading**: `core.batch_run` defaults to `ThreadPoolExecutor`; pure-Python `pypdf` workloads (`batch(8x10p, w=2)`) achieve ~`1.03×` speedup on 2 threads because of the Python GIL. Opt-in `core.batch_run(..., processes=True)` uses a spawned `ProcessPoolExecutor` to bypass the GIL for CPU-bound batches, while native PDFium/ONNX/C-extension workloads scale across cores (`tests/test_ocr_phase5.py::test_parallel_workers_faster_and_identical_to_single_worker` passed in `16.65s` on an idle 2-vCPU host).
- **OCR engine benchmarks**: `rapidocr`, `onnxruntime`, and `ocrmypdf` are optional extras not installed in the minimal Linux VM; OCR integration tests are validated in the dedicated GitHub Actions `OCR, PDF/A and Office (Tesseract, Ghostscript, LibreOffice)` CI job.

---

## 5. Performance Optimization Sprint (Post-v0.61.4)

Targeted `cProfile`-driven optimizations across `pdfmanager/redactcontent.py`, `pdfmanager/textedit/rewrite.py`, and `pdfmanager/glyphs.py` (zero accuracy or security trade-offs; all 134 redaction/textedit/reflow/perf tests passing):

| Workload | Before | After | Speedup | What Changed |
|---|---:|---:|---:|---|
| `redact_remove` (50 dense pages, warm, no tracemalloc) | `7.538s` | **`1.971s`** | **`3.82×`** | Vertical-span fast-path in `_text()`, global bbox reject in `_Areas`/`_center_in()`, streamlined `_chars()` & `_same_places()` |
| `redact_remove` (100p, `benchmark.py` w/ tracemalloc) | `33.935s` (98 MB RSS) | **`19.686s`** (62 MB RSS) | **`1.72×`** | Same as above (lower peak RSS too) |
| `textedit.edit_text` (10 dense pages, warm, no tracemalloc) | `1.872s` | **`0.543s`** | **`3.45×`** | Replaced per-char `get_text_range(i, 1)` FFI/UTF-16 decoding with `FPDFText_GetUnicode`, cached `pypdfium2.raw`, reused `ctypes.c_double` origin buffers, `(0, 0)`-first lookup in `_intact()` |
| `core.merge` (400 dense pages) | `0.228s` | `0.228s` | Already ~1,750 p/s | No rewrite needed |
| `core.rotate` (200 dense pages) | `0.109s` | `0.109s` | Already ~1,835 p/s | No rewrite needed |
