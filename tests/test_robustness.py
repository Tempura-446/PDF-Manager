# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Robustness sweep: every tool in the app, and the reader's opening and
previews, run on every hostile file in badpdfs.py. Each run must either
work or stop with a message for the user (the app's expected errors),
within a time limit: never an unexpected exception, never a hang."""
import threading
import time

import pytest

import badpdfs
from pdfmanager import gui

TIME_LIMIT = 90  # seconds for one tool on one file
# Tools that read every page closely; the 300-page file is left out for them
# so the sweep stays quick (they're timed on big files in the --perf checks).
SLOW_ON_MANY_PAGES = {"ocr", "clean", "to-images", "to-word", "to-excel", "to-pptx", "to-html", "tag",
                      "access-check", "autocrop", "redact", "compare", "pdfa", "pdfa-check", "text", "media",
                      "form", "compress", "color", "sanitize", "flatten"}
REQUIRED = {"pages": "1", "order": "1"}
# ZPDF's own code is what's swept, not the programs it can hand a file to: on
# a PC with Word, "Best available" would run Word on every file.
FIXED = {"to-word": {"engine": "basic"}}

TOOLS = [op for op in gui.OPERATIONS if op.get("files") in ("pdf", "pdf2")]


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    folder = tmp_path_factory.mktemp("badpdfs")
    files = badpdfs.build(folder)
    files["_good"] = badpdfs.good(folder / "good.pdf")
    return files


def _options(op):
    opts = {}
    for field in op["fields"]:
        if field["kind"] == "file":
            continue
        if field.get("default") not in (None, ""):
            value = field["default"]
            opts[field["key"]] = ("true" if value else "false") if isinstance(value, bool) else str(value)
        if field.get("required") and not opts.get(field["key"]):
            opts[field["key"]] = REQUIRED.get(field["key"], "1")
    opts.update(FIXED.get(op["id"], {}))
    return opts


def _run(func, *args):
    """(outcome, error) of ``func(*args)`` within TIME_LIMIT: outcome is
    'ok', 'stopped' (an expected error), 'crashed' or 'hung'."""
    box = {}

    def target():
        try:
            func(*args)
            box["outcome"] = "ok"
        except gui.EXPECTED_ERRORS as exc:
            box["outcome"], box["error"] = "stopped", exc
        except BaseException as exc:  # noqa: BLE001 (that's the point)
            box["outcome"], box["error"] = "crashed", exc

    thread = threading.Thread(target=target, daemon=True)
    started = time.monotonic()
    thread.start()
    thread.join(TIME_LIMIT)
    if thread.is_alive():
        return "hung", f"still running after {time.monotonic() - started:.0f} s"
    return box["outcome"], box.get("error")


@pytest.mark.parametrize("name", badpdfs.NAMES)
def test_every_tool_survives_the_file(corpus, name, tmp_path):
    bad, good = corpus[name], corpus["_good"]
    problems = []
    for op in TOOLS:
        if name == "many_pages" and op["id"] in SLOW_ON_MANY_PAGES:
            continue
        files = [bad, good] if op.get("files") == "pdf2" or op.get("min", 1) >= 2 else [bad]
        work = tmp_path / op["id"]
        work.mkdir()
        outcome, error = _run(gui.run_operation, op["id"], files, _options(op), None, {}, work, [])
        if outcome in ("crashed", "hung"):
            problems.append(f"{op['id']}: {outcome}: {type(error).__name__ if not isinstance(error, str) else ''} "
                            f"{error}")
    for what, func in (("inspect", gui.inspect_pdf),):
        outcome, error = _run(func, bad)
        if outcome in ("crashed", "hung"):
            problems.append(f"reader {what}: {outcome}: {type(error).__name__} {error}")
    data = bad.read_bytes()
    outcome, error = _run(gui.preview_operation, "rotate", data, bad.name, {"degrees": "90"})
    if outcome in ("crashed", "hung"):
        problems.append(f"preview: {outcome}: {type(error).__name__} {error}")
    assert not problems, f"{name}:\n  " + "\n  ".join(problems)


def test_messages_name_the_file_not_the_apps_working_folder(corpus, tmp_path):
    work = tmp_path / "work"
    (work / "in" / "000").mkdir(parents=True)
    src = work / "in" / "000" / "empty.pdf"
    src.write_bytes(b"")
    with pytest.raises(gui.EXPECTED_ERRORS) as caught:
        gui.run_operation("repair", [src], {}, None, {}, work, [])
    said = str(caught.value)
    assert said.startswith("empty.pdf") and str(work) not in said and "/in/000/" not in said


def test_an_ocr_failure_without_words_still_says_something(tmp_path, monkeypatch):
    import sys
    import types

    from pdfmanager import core, ocr

    fake = types.ModuleType("ocrmypdf")
    exceptions = types.ModuleType("ocrmypdf.exceptions")

    class ExitCodeException(Exception):
        exit_code = 2

    exceptions.ExitCodeException = ExitCodeException
    exceptions.MissingDependencyError = type("Missing", (Exception,), {})

    def broken(*args, **kwargs):
        raise ExitCodeException()

    fake.ocr = broken
    monkeypatch.setitem(sys.modules, "ocrmypdf", fake)
    monkeypatch.setitem(sys.modules, "ocrmypdf.exceptions", exceptions)
    monkeypatch.setattr(ocr.scan, "add_programs_to_path", lambda: None)
    monkeypatch.setattr(ocr, "_tesseract_languages", lambda: set())
    with pytest.raises(core.PdfManagerError) as caught:
        ocr._run_tesseract(tmp_path / "a.pdf", tmp_path / "b.pdf", "eng", False, False)
    said = str(caught.value)
    assert said.startswith("OCR failed: ") and len(said) > len("OCR failed: ") + 10
