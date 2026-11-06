"""Recipes: several tools run one after another as a single action, e.g.
"clean up scans → OCR → compress → add page numbers".

A recipe is ``{"name": str, "steps": [{"op": tool id, "options": {...}}]}``
where the options are the same as the tool's options in the app. Recipes
are saved in ``recipes.json`` in the app's data folder, so the app, the
command line (``pdfm recipe``) and the watch folder share them.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

from pdfmanager import core, progress
from pdfmanager.appdata import data_dir

# Tools that take one PDF and give one PDF back, and need no extra files.
STEP_OPS = ("clean", "ocr", "compress", "color", "rotate", "extract", "delete", "reorder",
            "reverse", "insert-blank", "resize", "crop", "autocrop", "nup", "booklet",
            "watermark", "number", "headers", "metadata", "bookmarks", "flatten", "sanitize",
            "redact", "pdfa")


def recipes_file():
    return data_dir() / "recipes.json"


def load_all():
    """{name: recipe}, sorted by name."""
    try:
        data = json.loads(recipes_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    recipes = data.get("recipes", []) if isinstance(data, dict) else []
    # The file is shared and hand-editable: an entry without a name is skipped, not fatal.
    valid = [r for r in recipes if isinstance(r, dict) and isinstance(r.get("name"), str) and r["name"]]
    return {r["name"]: r for r in sorted(valid, key=lambda r: r["name"].lower())}


def get(name):
    recipe = load_all().get(name)
    if recipe is None:
        names = ", ".join(load_all()) or "none saved yet"
        raise core.PdfManagerError(f"no recipe called {name!r} (recipes: {names})")
    return recipe


def check_steps(steps):
    if not isinstance(steps, list) or not steps:
        raise core.PdfManagerError("a recipe needs at least one step")
    clean = []
    for n, step in enumerate(steps, 1):
        op = step.get("op") if isinstance(step, dict) else None
        if op not in STEP_OPS:
            raise core.PdfManagerError(f"step {n}: {op!r} can't be used in a recipe")
        options = step.get("options") or {}
        if not isinstance(options, dict):
            raise core.PdfManagerError(f"step {n}: options must be a dictionary")
        if op == "bookmarks" and options.get("mode", "edit") == "edit":
            options = {**options, "mode": "detect"}  # a recipe has no list to edit
        clean.append({"op": op, "options": {str(k): str(v) for k, v in options.items()}})
    return clean


def _save_all(recipes):
    target = recipes_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps({"recipes": list(recipes.values())}, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, target)


def save(name, steps):
    name = " ".join(str(name or "").split())
    if not name:
        raise core.PdfManagerError("give the recipe a name")
    recipes = load_all()
    recipes[name] = {"name": name, "steps": check_steps(steps)}
    _save_all(recipes)
    return recipes[name]


def delete(name):
    recipes = load_all()
    if recipes.pop(name, None) is None:
        raise core.PdfManagerError(f"no recipe called {name!r}")
    _save_all(recipes)


def describe(steps):
    from pdfmanager.gui import OPS

    return " → ".join(OPS[s["op"]]["label"] for s in steps)


def run(steps, src, output, password=None, notes=None, state=None):
    """Run the steps on ``src`` and write the result to ``output``. ``state``
    is a dict the caller keeps from file to file for what continues across
    them (the next Bates number of a Headers & footers step)."""
    state = {} if state is None else state
    from pdfmanager import gui

    steps = check_steps(steps)
    notes = [] if notes is None else notes
    with tempfile.TemporaryDirectory(prefix="pdfm-recipe-") as tmp:
        current = Path(tmp) / Path(src).name
        if core.open_pdf(src, password).is_encrypted:
            core.decrypt(src, current, password or "")
        else:
            shutil.copyfile(src, current)
        for n, step in enumerate(steps, 1):
            spec = gui.OPS[step["op"]]
            with progress.section(n - 1, len(steps), f"step {n} of {len(steps)}: {spec['label']}"):
                for field in spec["fields"]:
                    if field.get("required") and not step["options"].get(field["key"], "").strip():
                        raise core.PdfManagerError(
                            f"step {n} ({spec['label']}): fill in “{field['label']}”")
                out_dir = Path(tmp) / f"step{n}"
                out_dir.mkdir()
                options = dict(step["options"])
                if step["op"] == "headers" and "_bates_next" in state:
                    options["_bates_next"] = state["_bates_next"]
                try:
                    outputs = gui._run_file_op(step["op"], current, out_dir, options, None, {}, notes)
                except gui.UserError as exc:
                    raise core.PdfManagerError(f"step {n} ({spec['label']}): {exc}") from None
                if step["op"] == "headers" and "_bates_next" in options:
                    state["_bates_next"] = options["_bates_next"]
                pdfs = [p for p in outputs if p.suffix.lower() == ".pdf"]
                if len(pdfs) != 1:
                    raise core.PdfManagerError(f"step {n} ({spec['label']}) didn't give one PDF")
                # Steps work on unprotected copies; the caller re-protects the result.
                current = pdfs[0]
        if any(s["op"] == "compress" for s in steps) and steps[-1]["op"] != "compress":
            # Steps after Compress rewrite the file without its packing.
            core.pack(current)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(current), str(output))
    return output
