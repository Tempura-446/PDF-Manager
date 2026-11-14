# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""The maturity sweep from pytest, only when asked for:

    python3 -m pytest tests/sweep --sweep CORPUS [--sweep-out DIR] [--sweep-reader N]

CORPUS is a folder made by make_corpus.py. Fails when any run crashed, hung,
ran out of memory or died; the report (results.csv, summary.txt,
tracebacks.txt) is written to --sweep-out (default: a "sweep-report" folder
next to CORPUS). With --sweep-reader N, N of the files are also opened in
the reader (pdf.js in a browser, through Playwright for Node.js); without
Node.js and Playwright that part is left out and the report says so."""
import shutil
import subprocess
from pathlib import Path

import pytest

import run_sweep


def _playwright_for_node():
    node = shutil.which("node")
    return bool(node) and subprocess.run([node, "-e", "require('playwright')"], capture_output=True,
                                         cwd=str(run_sweep.HERE)).returncode == 0


@pytest.mark.sweep
def test_the_sweep(request):
    corpus = request.config.getoption("--sweep")
    if not corpus:
        pytest.skip("the sweep runs with --sweep CORPUS")
    corpus = Path(corpus)
    out = Path(request.config.getoption("--sweep-out") or corpus.parent / "sweep-report")
    tasks, manifest = run_sweep.plan(corpus)
    results = run_sweep._run(tasks, out, jobs=4, timeout=120, memory_mb=3072, show=False)
    label = ""
    reader = request.config.getoption("--sweep-reader")
    if reader and not _playwright_for_node():
        label = "(the reader part was left out: Playwright for Node.js isn't installed)"
    elif reader:
        files = sorted(map(str, corpus.glob("*.pdf")))
        if len(files) > reader:
            files = [files[int(i * len(files) / reader)] for i in range(reader)]
        for f, r in run_sweep.run_reader(files, out).items():
            results.append({"file": f, "task": "reader", "kind": manifest.get(Path(f).name, {}).get("kind", "?"),
                            "class": r["class"], "message": r.get("message", ""), "seconds": r.get("seconds", 0)})
    counts = run_sweep.write_report(results, out, label)
    bad = [r for r in results if r["class"] in ("crash", "hang", "oom", "died")]
    assert not bad, f"{len(bad)} runs went wrong; see {out / 'summary.txt'}"
    assert counts
