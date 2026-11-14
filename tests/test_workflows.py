# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""The weekly sweep's workflow (.github/workflows/sweep.yml): it parses,
runs only on its schedule or by hand (never on pull requests, so it can't
become a required check), stays short, keeps its report, and uses the same
actions as the tests' workflow."""
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _load(name):
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1 reads a bare `on` as true
    return data


def _actions(data):
    return {step["uses"].split("@")[0]: step["uses"].split("@")[1]
            for job in data["jobs"].values() for step in job["steps"] if "uses" in step}


def test_the_sweep_runs_weekly_or_by_hand_only():
    sweep = _load("sweep.yml")
    assert set(sweep["on"]) == {"schedule", "workflow_dispatch"}
    cron = sweep["on"]["schedule"][0]["cron"].split()
    assert len(cron) == 5 and cron[4] != "*", "once a week"
    job = sweep["jobs"]["sweep"]
    assert job["runs-on"] == "ubuntu-latest" and job["timeout-minutes"] <= 25


def test_the_sweep_uses_the_same_actions_as_the_tests():
    tests, sweep = _actions(_load("tests.yml")), _actions(_load("sweep.yml"))
    for name in ("actions/checkout", "actions/setup-python"):
        assert sweep[name] == tests[name]
    # (Dependabot moves them together; the build's upload is the one to match.)
    assert sweep["actions/upload-artifact"] == _actions(_load("build-app.yml"))["actions/upload-artifact"]


def test_the_sweep_makes_a_quick_corpus_and_keeps_the_report():
    steps = _load("sweep.yml")["jobs"]["sweep"]["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    assert re.search(r"make_corpus\.py \S+ --quick", commands)
    assert "pytest" in commands and "--sweep " in commands and "--sweep-out" in commands
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact"))
    assert upload["if"] == "always()" and upload["with"]["retention-days"] == 14
    for name in ("summary.txt", "results.csv", "tracebacks.txt"):
        assert name in upload["with"]["path"]
    browser = next(step for step in steps if step.get("name") == "Let the browser use its sandbox (Linux)")
    tests_step = next(step for step in _load("tests.yml")["jobs"]["test"]["steps"]
                      if step.get("name") == "Let the browser use its sandbox (Linux)")
    assert browser["run"] == tests_step["run"]
