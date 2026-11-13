"""The maturity sweep from pytest, only when asked for:

    python3 -m pytest tests/sweep --sweep CORPUS [--sweep-out DIR]

CORPUS is a folder made by make_corpus.py. Fails when any run crashed, hung,
ran out of memory or died; the report (results.csv, summary.txt) is written
to --sweep-out (default: a "sweep-report" folder next to CORPUS)."""
from pathlib import Path

import pytest

import run_sweep


@pytest.mark.sweep
def test_the_sweep(request):
    corpus = request.config.getoption("--sweep")
    if not corpus:
        pytest.skip("the sweep runs with --sweep CORPUS")
    corpus = Path(corpus)
    out = Path(request.config.getoption("--sweep-out") or corpus.parent / "sweep-report")
    tasks, manifest = run_sweep.plan(corpus)
    results = run_sweep._run(tasks, out, jobs=4, timeout=120, memory_mb=3072, show=False)
    counts = run_sweep.write_report(results, out)
    bad = [r for r in results if r["class"] in ("crash", "hang", "oom", "died")]
    assert not bad, f"{len(bad)} runs went wrong; see {out / 'summary.txt'}"
    assert counts
