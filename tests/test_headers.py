# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Every source file of ZPDF's own starts with the copyright line, so a new
file can't be added without it. Software by others is left as it came."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTICE = ("Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 "
          "with a personal-use permission; see LICENSE.")
SOURCE = {".py", ".js", ".mjs", ".css", ".html", ".ps1", ".cmd"}

# Not ZPDF's own, so no ZPDF header: each keeps its own licence (see notices.py).
EXCLUDED = {
    "pdfmanager/static/pdfjs",  # pdf.js, Apache License 2.0 (Mozilla), vendored by packaging/update_pdfjs.py
}
# Folders that aren't the project's source: tools' and builds' output, other checkouts.
SKIPPED_DIRS = {"__pycache__", "build", "dist", "node_modules", "venv", "env"}


def source_files():
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT)
        parts = rel.parts
        if any(p.startswith(".") or p in SKIPPED_DIRS or p.endswith(".egg-info") for p in parts[:-1]):
            continue
        if any(rel.as_posix() == e or rel.as_posix().startswith(e + "/") for e in EXCLUDED):
            continue
        if path.is_file() and path.suffix.lower() in SOURCE:
            yield rel


def first_lines(path, count=3):
    return path.read_text(encoding="utf-8-sig").splitlines()[:count]


def test_every_source_file_has_the_copyright_line():
    files = list(source_files())
    assert len(files) > 150 and Path("pdfmanager/gui.py") in files
    missing = [str(f) for f in files if not any(NOTICE in line for line in first_lines(ROOT / f))]
    assert not missing, "add the copyright line (see tests/test_headers.py) to: " + ", ".join(missing)


def test_the_line_is_a_comment_in_each_language():
    expected = {".py": f"# {NOTICE}", ".ps1": f"# {NOTICE}", ".cmd": f"rem {NOTICE}",
                ".js": f"/* {NOTICE} */", ".mjs": f"/* {NOTICE} */", ".css": f"/* {NOTICE} */",
                ".html": f"<!-- {NOTICE} -->"}
    for rel in source_files():
        assert expected[rel.suffix.lower()] in first_lines(ROOT / rel), rel


def test_module_docstrings_still_come_first():
    """The line is a comment above the docstring, so modules keep their __doc__."""
    for rel in source_files():
        if rel.suffix == ".py":
            text = (ROOT / rel).read_text(encoding="utf-8")
            if '"""' in text.split("\n", 2)[1][:3]:
                assert ast.get_docstring(ast.parse(text)), rel


def test_vendored_files_are_left_alone():
    pdfjs = ROOT / "pdfmanager" / "static" / "pdfjs"
    for path in pdfjs.rglob("*.mjs"):
        assert "Tempura-446" not in path.read_text(encoding="utf-8", errors="replace")[:2000], path
