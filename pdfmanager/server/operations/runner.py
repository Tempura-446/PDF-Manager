# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Multi-file, quick-action, and folder operation orchestration."""

import io
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from pdfmanager import applog, filefacts, privacy, progress, quickactions
from pdfmanager.server.common import EXPECTED_ERRORS, UserError, _bool, _log, _num, _pages, _safe_name
from pdfmanager.server.operations._deps import (
    certcrypt, compare, core, filetools, ocr, office, organize, protection, recipes, scan, search, watchfolders,
)
from pdfmanager.server.operations.catalog import OPS, SUFFIX, default_options
from pdfmanager.server.operations.dispatch import _run_file_op
from pdfmanager.server.operations.repair import (
    _check_pages, _damaged_errors, _flatten_outputs, _mend_inputs,
    _needs_mending, _plainly, _protect, _rebuildable, _tidy, _was_protected,
)

def run_operation(op, files, opts, password=None, extra_files=None, work=None, notes=None):
    """Run ``op`` on uploaded ``files`` (paths in the temporary folder
    ``work``). Returns (filename, bytes, content type) for the download;
    messages for the user are appended to ``notes``."""
    try:
        try:
            return _run_operation(op, files, opts, password, extra_files, work, notes)
        except _damaged_errors() as exc:
            raise _plainly(exc) from None
        except NotImplementedError as exc:
            if "filter" not in str(exc).lower():  # (pypdf: "Unsupported filter /...")
                raise
            raise UserError("part of the PDF is compressed in a way that can't be read "
                            f"({' '.join(str(exc).split())[:80]}); it may be damaged") from None
    except EXPECTED_ERRORS as exc:
        tidy = _tidy(str(exc), work)
        if tidy == str(exc):
            raise
        raise (type(exc)(tidy) if _rebuildable(exc) else UserError(tidy)) from None


def _run_operation(op, files, opts, password=None, extra_files=None, work=None, notes=None):
    spec = OPS.get(op)
    if spec is None:
        raise UserError(f"unknown operation {op!r}")
    for field in spec["fields"]:
        if field.get("required") and not str(opts.get(field["key"]) or "").strip():
            raise UserError(f"please fill in “{field['label']}”")
    url_field = spec.get("url_field")
    typed = bool(url_field and str(opts.get(url_field) or "").strip())
    if not files and not typed:
        raise UserError("add at least one file" if not url_field else
                        "type a web address, or add saved web pages")
    if files and len(files) < spec.get("min", 1):  # (a typed address needs no file)
        raise UserError(f"add at least {spec['min']} files")
    notes = [] if notes is None else notes
    # A PDF protected for particular people isn't damaged: it's opened with a
    # Digital ID (that tool takes it as it is), and no other tool can read it.
    people = [Path(f).name for f in files if Path(f).suffix.lower() == ".pdf" and certcrypt.is_for_people(f)]
    if op == "decrypt-cert":
        # (Files that aren't protected this way are left out, and said; with
        # none that is, the tool's own words say what it's for.)
        plain = [f for f in files if Path(f).name not in people]
        if people and plain:
            notes.append("left out (not protected for particular people): " + ", ".join(Path(f).name for f in plain))
            files = [f for f in files if Path(f).name in people]
        return _run_files(op, list(files), opts, password, extra_files, work, notes)
    if people:
        raise UserError(f"{people[0]} {certcrypt.NOT_READABLE}")
    mended = _mend_inputs(op, files, password, work, notes)
    damaged = [Path(a).name for a, b in zip(files, mended) if a != b]
    if not damaged:
        return _run_files(op, mended, opts, password, extra_files, work, notes)
    try:
        return _run_files(op, mended, opts, password, extra_files, work, notes)
    except (AttributeError, TypeError, KeyError, IndexError, AssertionError, RecursionError) as exc:
        # What's left of a damaged file can still hold things no PDF should
        # (a page box of null, a link without its kind): said, not a crash.
        raise UserError(f"{', '.join(damaged)} {'is' if len(damaged) == 1 else 'are'} too damaged for this tool, "
                        f"even mended ({type(exc).__name__}: {' '.join(str(exc).split())[:80]})") from None


def _run_files(op, files, opts, password, extra_files, work, notes):
    """_run_operation, once damaged inputs are mended."""
    _check_pages(op, files, password)
    out_dir = Path(work or (files[0].parent if files else tempfile.mkdtemp(prefix="pdfm-"))) / "out"
    out_dir.mkdir()
    extra_files = extra_files or {}
    protect = None  # the outputs made from a password-protected input

    if op == "merge":
        out = out_dir / "merged.pdf"
        core.merge(files, out, bookmarks=_bool(opts.get("bookmarks", True)), password=password)
        outputs = [out]
    elif op == "interleave":
        if len(files) != 2:
            raise UserError("add exactly two files: front sides, then back sides")
        out = out_dir / f"{files[0].stem}_interleaved.pdf"
        core.interleave(files[0], files[1], out, _bool(opts.get("reverse_back", True)), password)
        outputs = [out]
    elif op == "compare":
        if len(files) != 2:
            raise UserError("add exactly two files: the original, then the changed version")
        out = out_dir / f"{files[0].stem}_vs_{files[1].stem}.pdf"
        mode = opts.get("mode") or "both"
        summary = compare.compare(files[0], files[1], out, password, password,
                                  all_pages=_bool(opts.get("all_pages")), mode=mode)
        notes.append(compare.format_summary(summary))
        outputs = [out]
        if _bool(opts.get("export_json")):
            json_out = out_dir / f"{files[0].stem}_vs_{files[1].stem}_diff.json"
            json_out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            outputs.append(json_out)
    elif op == "compare-sets":
        from pdfmanager import sheetsets

        if len(files) != 2:
            raise UserError("add exactly two drawing sets: the older version, then the newer version")
        out = out_dir / f"{files[0].stem}_vs_{files[1].stem}_sheets.pdf"
        res = sheetsets.compare_sets(files[0], files[1], out_pdf=out, password_v1=password, password_v2=password)
        parts = []
        if res["changed"]:
            parts.append("Changed: " + ", ".join(x["sheet"] for x in res["changed"]))
        if res["added"]:
            parts.append("Added: " + ", ".join(x["sheet"] for x in res["added"]))
        if res["removed"]:
            parts.append("Removed: " + ", ".join(x["sheet"] for x in res["removed"]))
        notes.append("; ".join(parts) if parts else "No differences found between sheet sets.")
        outputs = [res["output"]] if res["output"] else []
    elif op == "web" and not files:  # a web address
        address = opts["url"].strip()
        if office._web_address(address) is None:
            raise UserError("enter a web address starting with http:// or https:// (or upload a saved .html/.mht file)")
        name = re.sub(r"[^\w.-]+", "-", re.sub(r"(?i)^https?://(www\.)?", "", address)).strip("-.")[:60]
        out = out_dir / f"{name or 'page'}.pdf"
        _, program = office.html_to_pdf(address, out)
        notes.append(f"{address}: printed with {program}")
        outputs = [out]
    elif op == "from-images":
        out = out_dir / (files[0].stem + ".pdf" if len(files) == 1 else "images.pdf")
        core.images_to_pdf(files, out, opts.get("size") or None,
                           _num(opts.get("margin"), float, 0) * core.POINTS_PER_MM)
        outputs = [out]
    else:
        outputs, locked = [], []
        for index, src in enumerate(files):
            # Each input gets its own output folder; flatten afterwards.
            sub = out_dir / f"{index:03d}"
            sub.mkdir()
            label = f"{src.name} ({index + 1} of {len(files)})" if len(files) > 1 else src.name
            with progress.section(index, len(files), label):
                made = _run_file_op(op, src, sub, opts, password, extra_files, notes)
            outputs.extend((sub, p) for p in made)
            locked.extend([_was_protected(src, password)] * len(made))
        outputs = _flatten_outputs(outputs, out_dir)  # keeps the order
        protect = [path for path, was in zip(outputs, locked) if was]
    if protect is None:
        # One result from several inputs: protected if any of them was.
        protect = outputs if any(_was_protected(src, password) for src in files) else []

    if not outputs:
        raise UserError("nothing was found to save" + (f" ({'; '.join(notes)})" if notes else ""))
    _protect(protect, password, op)
    if len(outputs) == 1 and op != "media" and (outputs[0].suffix != ".csv" or op in ("comments", "quantities")):  # CSV tables: a ZIP
        path = outputs[0]
        ctype = {".txt": "text/plain; charset=utf-8",
                 ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 ".xfdf": "application/vnd.adobe.xfdf", ".csv": "text/csv; charset=utf-8",
                 # -- pptx and html --
                 ".pptx": "application/vnd.openxmlformats-officedocument.presentationml."
                          "presentation",
                 ".html": "text/html; charset=utf-8",
                 ".dxf": "application/dxf",
                 # -- end of pptx and html --
                 }.get(path.suffix, "application/pdf")
        return path.name, path.read_bytes(), ctype
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in outputs:
            zf.write(path, path.relative_to(out_dir))
    name = f"{files[0].stem}_{SUFFIX.get(op, op)}.zip" if len(files) == 1 else f"pdfm_{op}.zip"
    return name, buf.getvalue(), "application/zip"


def run_action(action, paths, work):
    """Run a right-click action (quickactions.ACTIONS) on ``paths`` with the
    tool's default options, in the temporary folder ``work``. Returns
    (results, failed): each result {"name", "data", "sources", "notes"}, each
    failure {"name", "error"} (a file that couldn't be used, or, for a tool
    run file by file, one that failed while the others went on)."""
    spec = quickactions.ACTIONS[action]
    failed, usable = [], []
    for path in sorted((Path(p) for p in paths), key=quickactions.natural_key):
        why = quickactions.refuse_reason(action, path)
        if why:
            failed.append({"name": path.name, "error": why})
        else:
            usable.append(path)

    def copies(group, tag):
        # Copies: a tool never touches the file Explorer gave it.
        made = []
        for index, source in enumerate(group):
            folder = work / "in" / f"{tag}{index:03d}"
            folder.mkdir(parents=True)
            made.append(Path(shutil.copy2(source, folder / _safe_name(source.name))))
        return made

    results = []
    if spec["together"]:
        if len(usable) < spec["min"]:
            told = "; ".join(f"{f['name']}: {f['error']}" for f in failed)
            raise UserError((f"{spec['label']} needs at least {spec['min']} PDFs: select them together "
                             f"and choose {spec['label']} in ZPDF again" if spec["min"] > 1 else
                             "there was nothing to make a PDF from") + (f" ({told})" if told else ""))
        notes, sub = [], work / "run"
        sub.mkdir()
        name, data, _ = run_operation(spec["op"], copies(usable, "t"), default_options(spec["op"]),
                                      work=sub, notes=notes)
        if len(usable) > 1:
            name = f"{usable[0].stem} (combined).pdf"
        results.append({"name": name, "data": data, "sources": usable, "notes": notes})
    else:
        for index, path in enumerate(usable):
            notes, sub = [], work / f"run{index:03d}"
            sub.mkdir()
            with progress.section(index, len(usable), path.name if len(usable) > 1 else ""):
                try:
                    name, data, _ = run_operation(spec["op"], copies([path], f"f{index}-"),
                                                  default_options(spec["op"]), work=sub, notes=notes)
                except progress.Cancelled:
                    raise
                except EXPECTED_ERRORS as exc:
                    failed.append({"name": path.name, "error": str(exc)})
                    continue
                except Exception as exc:  # noqa: BLE001 - one bad file doesn't stop the others
                    failed.append({"name": path.name, "error": applog.unexpected(exc)})
                    continue
            results.append({"name": name, "data": data, "sources": [path], "notes": notes})
    if not results:
        raise UserError("; ".join(f"{f['name']}: {f['error']}" for f in failed) or "no files were given")
    return results, failed


def _start_action(handler, batch):
    """Run a right-click batch whose files are all in, as a job the page follows."""
    job = progress.Job()
    job.expected = EXPECTED_ERRORS
    batch.job = job
    handler.jobs.add(job)

    def work_on():
        work = handler.work.make()
        started, outcome = time.monotonic(), "failed"
        try:
            results, failed = run_action(batch.action, batch.paths, work)
            outcome = "done"
            out = []
            for n, result in enumerate(results):
                batch.outputs[n] = (result["name"], result["data"])
                sources = result["sources"]
                first = sources[0]
                out.append({"n": n, "name": result["name"], "size": len(result["data"]),
                            "before": sum(p.stat().st_size for p in sources if p.exists()),
                            "from": first.name if len(sources) == 1 else
                            f"{first.name} and {len(sources) - 1} more",
                            "from_id": handler.opened.add(first), "count": len(sources),
                            "notes": result["notes"]})
            return {"json": {"batch": batch.id, "action": batch.action, "op": quickactions.ACTIONS[batch.action]["op"],
                             "label": quickactions.ACTIONS[batch.action]["label"],
                             "results": out, "failed": failed}}
        except progress.Cancelled:
            outcome = "cancelled"
            raise
        finally:
            applog.tool_run("action-" + batch.action, batch.paths, time.monotonic() - started, outcome)
            handler.work.remove(work)
            batch.finished_at = time.monotonic()

    job.start(work_on)




def run_folder_operation(op, opts, apply):
    """Folder tools. With ``apply`` false nothing on disk changes."""
    directory = (opts.get("directory") or "").strip()
    if not directory:
        raise UserError("enter a folder")
    directory = Path(directory).expanduser()
    recursive = _bool(opts.get("recursive"))
    if op == "organize":
        plan = organize.organize(directory, opts.get("by") or "year",
                                 Path(opts["dest"]).expanduser() if opts.get("dest") else None,
                                 _bool(opts.get("move")), recursive, dry_run=not apply,
                                 duplicates=opts.get("duplicates") or "keep")
        rows = [[str(s), str(d) if d else "skipped (duplicate)"] for s, d in plan]
    elif op == "rename":
        plan = organize.rename(directory, opts.get("pattern") or "{date:%Y-%m-%d} {title}",
                               recursive, dry_run=not apply)
        rows = [[str(s), str(d)] for s, d in plan]
    elif op == "search":
        try:
            return search.search(directory, opts.get("query") or "", recursive,
                                 _bool(opts.get("case_sensitive")), _bool(opts.get("whole_words")),
                                 how="exact" if opts.get("how") == "exact" else "best")
        finally:
            if not privacy.remembering():
                search.forget_index()  # (the text read for this search isn't kept)
    elif op == "duplicates":
        rows = []
        for keep, *extra in organize.find_duplicates(directory, recursive):
            for path in extra:
                if apply and _bool(opts.get("delete")):
                    path.unlink()
                    rows.append([str(path), f"deleted (same as {keep.name})"])
                else:
                    rows.append([str(path), f"duplicate of {keep}"])
    else:
        raise UserError(f"unknown operation {op!r}")
    return {"rows": rows, "applied": apply}
