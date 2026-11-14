# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Import guard: every module imports on its own, and the modules that
import each other don't become more.

Several pairs of modules use each other; they are kept from going round in
a circle while Python loads them only by importing inside functions. A new
top-level import in the wrong place (or a module that only works because
another was imported first) would break the app at start-up in a way the
other tests, which import everything in one process, never notice. So each
module is imported in a fresh interpreter of its own here.

The second test lists the two-way pairs (a imports b and b imports a,
anywhere in the file) and fails when a new one appears, so the coupling can
only shrink."""
import ast
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "pdfmanager"
# Modules that do something when imported rather than just define things.
NOT_IMPORTED = {"__main__"}

# The pairs of modules that use each other today, each kept from cycling at
# import time by an import inside a function. Remove a pair when the two no
# longer need each other; never add one (see the failure message for how).
ALLOWED_PAIRS = {
    ("core", "ocr"),         # core.ocr() is the public entry; ocr builds on core
    ("gui", "recipes"),      # recipes runs gui's tools; gui lists the recipes
    ("gui", "update"),       # the relaunch helper starts the app again
    ("archive", "pdfafix"),  # PDF/A conversion; pdfafix uses archive's sanitize and colour profile
    ("ocr", "scan"),         # ocr finds Tesseract through scan; scan's auto-rotate reads with ocr
}


def _modules():
    return sorted(p.stem for p in PACKAGE.glob("*.py") if p.stem not in NOT_IMPORTED)


def _import_alone(name):
    code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
            f"import pdfmanager.{name}" if name != "__init__" else
            f"import sys; sys.path.insert(0, {str(ROOT)!r}); import pdfmanager")
    try:
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                              timeout=120, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        return name, "timed out after 120 s"
    return name, None if done.returncode == 0 else (done.stderr or done.stdout or f"exit {done.returncode}")


def test_every_module_imports_on_its_own():
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_import_alone, _modules()))
    failed = [(name, err) for name, err in results if err]
    assert not failed, "\n\n".join(
        f"`import pdfmanager.{name}` fails in a fresh interpreter:\n{err.strip()}" for name, err in failed)


def _imported_modules(path, modules):
    """The package's modules that ``path`` imports, at the top or in a function."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names = []
        if isinstance(node, ast.ImportFrom):
            if node.level:
                names += [node.module.split(".")[0]] if node.module else [a.name for a in node.names]
            elif node.module == "pdfmanager":
                names += [a.name for a in node.names]
            elif node.module and node.module.startswith("pdfmanager."):
                names.append(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            names += [a.name.split(".")[1] for a in node.names if a.name.startswith("pdfmanager.")]
        found.update(n for n in names if n in modules)
    found.discard(path.stem)
    return found


def two_way_pairs():
    modules = {p.stem for p in PACKAGE.glob("*.py")}
    uses = {m: _imported_modules(PACKAGE / f"{m}.py", modules) for m in modules}
    return {tuple(sorted((a, b))) for a in uses for b in uses[a] if a in uses.get(b, ())}


def _key(pair):
    return tuple(sorted(pair))


def test_no_new_two_way_imports():
    pairs = two_way_pairs()
    new = sorted(pairs - {_key(p) for p in ALLOWED_PAIRS})
    assert not new, (
        "These modules now import each other: "
        + ", ".join(f"{a} <-> {b}" for a, b in new)
        + ".\nTwo modules that use each other only load because one of the imports sits inside a "
        "function, and are one top-level import away from an import cycle. Instead, move what both "
        "need into a small module of its own that imports neither (as glyphs.py, winprograms.py and "
        "pdfpermissions.py were made), or pass the function in from the caller. Don't add the pair "
        "to ALLOWED_PAIRS in tests/test_imports.py.")


def test_allowed_pairs_are_still_needed():
    gone = sorted({_key(p) for p in ALLOWED_PAIRS} - two_way_pairs())
    assert not gone, ("These modules no longer import each other; remove them from ALLOWED_PAIRS in "
                      "tests/test_imports.py so the pair can't come back unnoticed: "
                      + ", ".join(f"{a} <-> {b}" for a, b in gone))


@pytest.mark.parametrize("pair", sorted(_key(p) for p in ALLOWED_PAIRS))
def test_allowed_pairs_import_in_either_order(pair):
    a, b = pair
    for first, second in ((a, b), (b, a)):
        code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
                f"import pdfmanager.{first}; import pdfmanager.{second}")
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                              timeout=120, cwd=str(ROOT))
        assert done.returncode == 0, f"importing {first} then {second} fails:\n{done.stderr}"
