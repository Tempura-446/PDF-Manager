# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Reproducible performance benchmark harness for ZPDF (Phase 5 / P-001).

Generates deterministic digital and scanned PDF fixtures in a temporary
directory and measures wall-clock time, throughput (pages/s), peak Python
heap memory (tracemalloc), process RSS peak (POSIX), and output disk size
across representative workloads:

  - compare          : digital PDF comparison (10% modified + 1 inserted page)
  - compare_scanned  : image-only scanned PDF comparison
  - redact_remove    : content-stream text redaction
  - redact_image     : rasterized page redaction (streaming O(1) page memory)
  - textedit         : text replacement across pages
  - batch            : multi-document batch processing via core.batch_run()

Usage:
    python3 packaging/benchmark.py
    python3 packaging/benchmark.py --pages 10 100 --workloads compare redact_image batch
    python3 packaging/benchmark.py --pages 10 100 500 --repeats 3 --json
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

try:
    import resource
except ImportError:  # pragma: no cover (Windows)
    resource = None

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pdfmanager
from pdfmanager import compare, core, redact, textedit


ALL_WORKLOADS = (
    "compare",
    "compare_scanned",
    "redact_remove",
    "redact_image",
    "textedit",
    "batch",
)


def _rss_mb() -> float | None:
    if resource is None:
        return None
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB; macOS reports bytes.
    if sys.platform == "darwin":
        return usage / (1024 * 1024)
    return usage / 1024


def make_digital_pdf(path: Path, num_pages: int, *, variant: str = "base") -> Path:
    """Create a deterministic ``num_pages``-page text PDF."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    for i in range(num_pages):
        page_no = i + 1
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, height - 72, f"ZPDF Benchmark Document — Page {page_no} of {num_pages}")
        c.setFont("Helvetica", 11)
        y = height - 110
        for line in range(18):
            if variant == "modified" and (i % 10 == 0) and line == 3:
                text = f"Line {line + 1}: REVISED paragraph text for page {page_no}."
            elif line == 5:
                text = f"Line {line + 1}: Record contains CONFIDENTIAL_TOKEN-{page_no:04d} for redaction."
            else:
                text = (
                    f"Line {line + 1}: Deterministic sample content for ZPDF benchmark "
                    f"page {page_no} paragraph {line + 1}."
                )
            c.drawString(72, y, text)
            y -= 20
        c.showPage()
        if variant == "modified" and page_no == max(1, num_pages // 2):
            c.setFont("Helvetica-Bold", 14)
            c.drawString(72, height - 72, "Inserted Addendum Page")
            c.setFont("Helvetica", 11)
            c.drawString(72, height - 110, "This page was inserted in the revised document.")
            c.showPage()
    c.save()
    return path


def make_scanned_pdf(path: Path, num_pages: int, *, variant: str = "base") -> Path:
    """Create a deterministic image-only scanned PDF (no extractable text)."""
    from PIL import Image, ImageDraw
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    for i in range(num_pages):
        img = Image.new("L", (420, 540), 255)
        draw = ImageDraw.Draw(img)
        # Distinct geometric pattern per page so visual alignment has real signal.
        top = 30 + (i * 17) % 180
        draw.rectangle([30, top, 390, top + 28], fill=20)
        for row in range(6):
            y = 240 + row * 38
            w = 140 + ((i * 29 + row * 41) % 220)
            draw.rectangle([30, y, 30 + w, y + 14], fill=40)
        if variant == "modified" and (i % 5 == 0):
            draw.rectangle([260, 480, 390, 515], fill=0)
        c.drawImage(ImageReader(img), 0, 0, width, height)
        img.close()
        c.showPage()
    c.save()
    return path


def _measure(fn, pages: int, repeats: int = 1) -> dict:
    """Run ``fn()`` ``repeats`` times and return timing/memory metrics."""
    durations = []
    peak_py_mbs = []
    out_bytes = 0
    for _ in range(repeats):
        gc.collect()
        tracemalloc.start()
        t0 = time.perf_counter()
        out_path = fn()
        elapsed = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        durations.append(elapsed)
        peak_py_mbs.append(peak / (1024 * 1024))
        if isinstance(out_path, Path) and out_path.exists():
            out_bytes = out_path.stat().st_size
    med_s = statistics.median(durations)
    return {
        "pages": pages,
        "repeats": repeats,
        "median_s": round(med_s, 4),
        "min_s": round(min(durations), 4),
        "max_s": round(max(durations), 4),
        "pages_per_s": round(pages / med_s, 2) if med_s > 0 else 0.0,
        "peak_py_mb": round(max(peak_py_mbs), 2),
        "rss_mb": round(_rss_mb(), 2) if _rss_mb() is not None else None,
        "output_kb": round(out_bytes / 1024, 1),
    }


def run_benchmarks(
    page_counts: list[int],
    workloads: list[str],
    *,
    repeats: int = 1,
    dpi: int = 150,
    batch_docs: int = 8,
) -> dict:
    """Execute selected workloads for each page count and return structured results."""
    env = {
        "zpdf_version": getattr(pdfmanager, "__version__", "unknown"),
        "python": platform.python_version(),
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "default_workers": core.default_workers(),
    }
    rows = []
    with tempfile.TemporaryDirectory(prefix="zpdf_bench_") as tmp:
        root = Path(tmp)
        for pages in page_counts:
            digital_a = make_digital_pdf(root / f"digital_{pages}_a.pdf", pages, variant="base")
            digital_b = None
            scanned_a = None
            scanned_b = None

            if "compare" in workloads:
                digital_b = make_digital_pdf(
                    root / f"digital_{pages}_b.pdf", pages, variant="modified"
                )
                out = root / f"compare_{pages}.pdf"
                res = _measure(
                    lambda: (compare.compare(digital_a, digital_b, out), out)[1],
                    pages=pages,
                    repeats=repeats,
                )
                rows.append({"workload": "compare", **res})

            if "compare_scanned" in workloads:
                scanned_a = make_scanned_pdf(
                    root / f"scanned_{pages}_a.pdf", pages, variant="base"
                )
                scanned_b = make_scanned_pdf(
                    root / f"scanned_{pages}_b.pdf", pages, variant="modified"
                )
                out = root / f"compare_scanned_{pages}.pdf"
                res = _measure(
                    lambda: (compare.compare(scanned_a, scanned_b, out), out)[1],
                    pages=pages,
                    repeats=repeats,
                )
                rows.append({"workload": "compare_scanned", **res})

            if "redact_remove" in workloads:
                out = root / f"redact_remove_{pages}.pdf"
                res = _measure(
                    lambda: (
                        redact.redact(
                            digital_a, out, terms=["CONFIDENTIAL_TOKEN"], method="remove"
                        ),
                        out,
                    )[1],
                    pages=pages,
                    repeats=repeats,
                )
                rows.append({"workload": "redact_remove", **res})

            if "redact_image" in workloads:
                out = root / f"redact_image_{pages}.pdf"
                res = _measure(
                    lambda: (
                        redact.redact(
                            digital_a, out, terms=["CONFIDENTIAL_TOKEN"],
                            method="image", dpi=dpi,
                        ),
                        out,
                    )[1],
                    pages=pages,
                    repeats=repeats,
                )
                rows.append({"workload": f"redact_image({dpi}dpi)", **res})

            if "textedit" in workloads:
                out = root / f"textedit_{pages}.pdf"
                edits = [
                    {
                        "page": p + 1,
                        "at": [72, 120],
                        "width": 400,
                        "text": f"Benchmark annotation on page {p + 1}",
                    }
                    for p in range(pages)
                ]
                res = _measure(
                    lambda: (textedit.edit_text(digital_a, out, edits), out)[1],
                    pages=pages,
                    repeats=repeats,
                )
                rows.append({"workload": "textedit", **res})

            if "batch" in workloads:
                docs = [
                    make_digital_pdf(root / f"batch_{pages}_{idx}.pdf", pages)
                    for idx in range(batch_docs)
                ]
                total_pages = pages * batch_docs
                for workers in sorted({1, core.default_workers()}):
                    def _run_batch(w=workers):
                        def _one(src_path: Path):
                            dst = src_path.with_suffix(f".w{w}.out.pdf")
                            redact.redact(
                                src_path, dst, terms=["CONFIDENTIAL_TOKEN"], method="remove"
                            )
                            return dst

                        outcomes = core.batch_run(docs, _one, max_workers=w)
                        assert all(o["ok"] for o in outcomes)
                        return outcomes[-1]["result"]

                    res = _measure(_run_batch, pages=total_pages, repeats=repeats)
                    rows.append({"workload": f"batch({batch_docs}x{pages}p,w={workers})", **res})

    return {"environment": env, "results": rows}


def format_markdown(report: dict) -> str:
    env = report["environment"]
    lines = [
        f"Environment: ZPDF {env['zpdf_version']} | Python {env['python']} | "
        f"{env['os']} ({env['machine']}) | CPUs={env['cpu_count']} "
        f"(default_workers={env['default_workers']})",
        "",
        "| Workload | Pages | Median (s) | Pages/s | Peak Py Mem (MB) | RSS (MB) | Output (KB) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in report["results"]:
        rss = f"{r['rss_mb']:.1f}" if r["rss_mb"] is not None else "n/a"
        lines.append(
            f"| {r['workload']} | {r['pages']} | {r['median_s']:.3f} | "
            f"{r['pages_per_s']:.1f} | {r['peak_py_mb']:.2f} | {rss} | {r['output_kb']:.1f} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ZPDF reproducible performance benchmark harness.")
    parser.add_argument(
        "--pages", type=int, nargs="+", default=[10, 100],
        help="Page counts to benchmark (default: 10 100).",
    )
    parser.add_argument(
        "--workloads", nargs="+", choices=ALL_WORKLOADS, default=list(ALL_WORKLOADS),
        help="Subset of workloads to run.",
    )
    parser.add_argument(
        "--repeats", type=int, default=1,
        help="Number of measured runs per workload (reports median).",
    )
    parser.add_argument(
        "--dpi", type=int, default=150,
        help="DPI for redact_image workload (default: 150).",
    )
    parser.add_argument(
        "--batch-docs", type=int, default=8,
        help="Number of documents in the batch workload (default: 8).",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit machine-readable JSON instead of a Markdown table.",
    )
    args = parser.parse_args(argv)
    report = run_benchmarks(
        args.pages,
        args.workloads,
        repeats=max(1, args.repeats),
        dpi=args.dpi,
        batch_docs=max(1, args.batch_docs),
    )
    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        print(format_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
