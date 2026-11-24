# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Multi-input, scan, folder, recipe, watch, Windows setup, and GUI CLI commands."""

import json
import re
import sys
from pathlib import Path

from pdfmanager import compare, core, organize, recipes, scan, search
from pdfmanager.pageranges import PageRangeError
from pdfmanager.cli.common import (
    _Passwords, _ask_new_password, _check_outputs, _default_output, _expand, _human_size, _is_batch, _is_folder, _print_plan, _reencrypt,
)



# --------------------------------------------------------------------------
# Multi-input and folder commands
# --------------------------------------------------------------------------

def _merge_inputs(items):
    """Accept 'file.pdf' or 'file.pdf:1-3' for per-file page selection."""
    result = []
    for item in items:
        path, sep, spec = item.rpartition(":")
        if sep and spec and not Path(item).exists() and Path(path).exists():
            result.append((path, spec))
        else:
            result.extend(_expand([item]))
    return result


def cmd_merge(a):
    inputs = _merge_inputs(a.inputs)
    passwords = _Passwords(a.password)
    # All inputs share one password argument in core.merge; check each up front.
    # Files with only an owner password open with the empty password, which
    # core.open_pdf always tries, so they can go with any password.
    pws = {passwords.for_file(i[0] if isinstance(i, tuple) else i) for i in inputs} - {None, ""}
    if len(pws) > 1:
        raise core.PdfManagerError("encrypted inputs use different passwords; decrypt them first")
    pw = pws.pop() if pws else None
    out = core.merge(inputs, a.output, bookmarks=not a.no_bookmarks, password=pw)
    if pw:
        _reencrypt(out, pw)
    print(f"merged {len(inputs)} file(s) -> {out}")


def cmd_interleave(a):
    passwords = _Passwords(a.password)
    pw = passwords.for_file(a.front) or passwords.for_file(a.back)
    out = core.interleave(a.front, a.back, a.output, not a.no_reverse, pw)
    if pw:
        _reencrypt(out, pw)
    print(f"-> {out}")


def cmd_compare(a):
    passwords = _Passwords(a.password)
    mode = getattr(a, "mode", "both") or "both"
    if getattr(a, "json", False) and not a.output:
        summary = compare.compare_summary(
            a.old, a.new, passwords.for_file(a.old), passwords.for_file(a.new),
            all_pages=a.all_pages, mode=mode,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0
    out = a.output or _default_output(a.old, f"vs_{Path(a.new).stem}")
    summary = compare.compare(a.old, a.new, out, passwords.for_file(a.old),
                              passwords.for_file(a.new), all_pages=a.all_pages, mode=mode)
    if getattr(a, "json", False):
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0
    changes = [c for c in summary["changes"] if c["kind"] != "same"]
    for c in changes:
        where = (f"page {c['new']} added" if c["kind"] == "added" else
                 f"page {c['old']} removed" if c["kind"] == "removed" else
                 f"page {c['old']}" + (f" (now {c['new']})" if c["old"] != c["new"] else ""))
        detail = "; ".join([f"- {w}" for w in c["removed"][:3]] + [f"+ {w}" for w in c["added"][:3]])
        print(f"{where}: {detail or 'layout or pictures changed'}" if c["kind"] == "changed"
              else where)
    counts = summary.get("counts") or {}
    bits = [f"{counts[k]} {k}" for k in ("changed", "added", "removed") if counts.get(k)]
    suffix = f" ({', '.join(bits)})" if bits else ""
    print(f"{len(changes)} page(s) differ{suffix}" if changes else "no differences")
    print(f"-> {out}")


def _parse_steps(tokens):
    """['clean', 'ocr', 'language=deu', 'compress', 'level=high'] -> steps."""
    steps = []
    for token in tokens:
        key, sep, value = token.partition("=")
        if sep:
            if not steps:
                raise core.PdfManagerError(f"{token!r} comes before any step")
            steps[-1]["options"][key.strip().replace("-", "_")] = value
        else:
            steps.append({"op": token, "options": {}})
    return steps


def cmd_recipe(a):
    state = {}  # carried from file to file (Bates numbers)
    if a.action != "list" and not a.name:
        raise core.PdfManagerError(f"pdfm recipe {a.action} needs a recipe name")
    if a.action == "list":
        saved = recipes.load_all()
        for name, recipe in saved.items():
            print(f"{name}: {recipes.describe(recipe['steps'])}")
        if not saved:
            print("no recipes yet; create one with: pdfm recipe save NAME clean ocr compress "
                  "level=high")
        print(f"(saved in {recipes.recipes_file()})")
    elif a.action == "show":
        recipe = recipes.get(a.name)
        for n, step in enumerate(recipe["steps"], 1):
            options = " ".join(f"{k}={v}" for k, v in step["options"].items())
            print(f"{n}. {step['op']} {options}".rstrip())
    elif a.action == "save":
        recipe = recipes.save(a.name, _parse_steps(a.args))
        print(f"saved {recipe['name']!r}: {recipes.describe(recipe['steps'])}")
    elif a.action == "delete":
        recipes.delete(a.name)
        print(f"deleted {a.name!r}")
    else:  # run
        steps = recipes.get(a.name)["steps"]
        files = _expand(a.args)
        if not files:
            raise core.PdfManagerError("name the PDF(s) to run the recipe on")
        passwords = _Passwords(a.password)
        slug = "".join(c if c.isalnum() else "-" for c in a.name.lower()).strip("-") or "recipe"
        plan = []
        for src in files:
            if a.output and (_is_batch(a.args, files) or _is_folder(a.output)):
                folder = Path(a.output)
                folder.mkdir(parents=True, exist_ok=True)
                # Never replace the inputs: in their own folder, use the default name.
                out = (folder / Path(src).name if folder.resolve() != Path(src).parent.resolve()
                       else _default_output(src, slug))
            else:
                out = Path(a.output) if a.output else _default_output(src, slug)
            plan.append((src, out))
        _check_outputs(plan)
        failures = 0
        for src, out in plan:
            try:
                pw = passwords.for_file(src)
                notes = []
                recipes.run(steps, src, out, pw, notes, state=state)
                if pw:
                    _reencrypt(out, pw)
                print(f"{src} -> {out}" + (f"  ({'; '.join(notes)})" if notes else ""))
            except (core.PdfManagerError, PageRangeError, OSError) as exc:
                if len(files) == 1:
                    raise
                failures += 1
                print(f"error: {src}: {exc}", file=sys.stderr)
        return 1 if failures else 0


def cmd_from_images(a):
    margin = a.margin * core.POINTS_PER_MM
    print(f"-> {core.images_to_pdf(_expand(a.images), a.output, a.size, margin)}")


def _scan_options(a, notes):
    return dict(straighten=not a.no_straighten, remove_blank=not a.keep_blank, upright=not a.no_upright,
                recognise=not a.no_ocr, language=a.language, notes=notes)


def _made(out, notes):
    for note in notes:
        print(f"   {note}")
    print(f"-> {out}")


def cmd_scan(a):
    """Scan from a scanner (Windows): one page from the glass at a time,
    or every page in the feeder, then a searchable PDF."""
    from pdfmanager import scanner, scanpdf

    if a.add:
        from pdfmanager import escl

        device = escl.add(a.add)
        print(f"added {device['name']}\n   {device['id']}")
        if not a.output:
            return 0
    found = scanner.all_scanners()
    if a.list:
        if not found:
            print(scanner.NONE_FOUND)
        for s in found:
            print(f"{s['name']}{' (feeder' + (', both sides' if s.get('duplex') else '') + ')' if s['feeder'] else ''}\n   {s['id']}")
        return 0
    if not a.output:
        raise core.PdfManagerError("say where to save the PDF with -o (or use --list to see the scanners)")
    if not found:
        raise core.PdfManagerError(scanner.NONE_FOUND)
    if a.scanner:
        wanted = a.scanner.lower()
        picked = [s for s in found if s["id"].lower() == wanted] or \
            [s for s in found if wanted in s["name"].lower()]
        if not picked:
            raise core.PdfManagerError(f"no scanner matches {a.scanner!r}; see pdfm scan --list")
        device = picked[0]
    else:
        device = found[0]
    source = "feeder" if a.feeder else "flatbed"
    pages = []
    print(f"Scanning with {device['name']} ({a.mode}, {a.dpi} dpi, {'document feeder' if a.feeder else 'flatbed'})")
    while True:
        got = scanner.scan_any(device["id"], mode=a.mode, dpi=a.dpi, source=source, duplex=a.duplex)
        pages += [scanpdf.Page(image, dpi) for image, dpi in got]
        print(f"   {len(pages)} page{'s' if len(pages) != 1 else ''} so far")
        if a.feeder or not sys.stdin.isatty():
            break
        answer = input("Put the next page on the glass and press Enter, or type d and Enter when done: ")
        if answer.strip().lower().startswith(("d", "q", "n")):
            break
    notes = []
    out = scanpdf.make_pdf(pages, a.output, **_scan_options(a, notes))
    _made(out, notes)
    return 0


def cmd_photos_to_pdf(a):
    """Phone photos of pages: each page found, flattened and cleaned up,
    then a searchable PDF."""
    from pdfmanager import scanpdf

    pages = []
    for path in _expand(a.photos):
        page, found = scanpdf.photo_page(path, straighten=not a.no_straighten)
        if not found.found:
            print(f"   {Path(path).name}: no page edges found ({found.why}); kept as photographed")
        pages.append(page)
    notes = []
    out = scanpdf.make_pdf(pages, a.output, look=a.look, paper=a.paper, **_scan_options(a, notes))
    _made(out, notes)
    return 0


def cmd_organize(a):
    plan = organize.organize(a.directory, a.by, a.dest, a.move, a.recursive, a.dry_run,
                             a.duplicates)
    _print_plan(plan, "move" if a.move else "copy", a.dry_run)


def cmd_rename(a):
    plan = organize.rename(a.directory, a.pattern, a.recursive, a.dry_run)
    _print_plan(plan, "rename", a.dry_run)


def cmd_catalog(a):
    out, count = organize.catalog(a.directory, a.output, a.recursive)
    print(f"catalogued {count} PDF(s) -> {out}")


def cmd_search(a):
    result = search.search(a.directory, a.query, a.recursive, a.case_sensitive, a.whole_words,
                           limit=a.limit, how="best" if a.best else "exact")
    for m in result["matches"]:
        more = f" (+{m['count'] - 1} more)" if m["count"] > 1 and not a.best else ""
        print(f"{m['path']}, page {m['page']}: {m['before']}[{m['match']}]{m['after']}{more}")
        for copy in m.get("also", ()):
            print(f"    the same file: {copy}")
    for path, reason in result["skipped"]:
        print(f"skipped {path}: {reason}", file=sys.stderr)
    files = len({m["path"] for m in result["matches"]})
    print(f"{len(result['matches'])} page(s) in {files} of {result['files']} file(s)"
          + (" (stopped at the limit)" if result["limited"] else ""))
    return 0 if result["matches"] else 1


def cmd_duplicates(a):
    groups = organize.find_duplicates(a.directory, a.recursive)
    removed = 0
    for group in groups:
        keep, *extra = group
        print(f"keep:      {keep}")
        for path in extra:
            if a.delete:
                path.unlink()
                removed += 1
                print(f"deleted:   {path}")
            else:
                print(f"duplicate: {path}")
    if not groups:
        print("no duplicates found")
    elif a.delete:
        print(f"deleted {removed} duplicate(s)")
    else:
        print(f"{sum(len(g) - 1 for g in groups)} duplicate(s); use --delete to remove them "
              "(the oldest copy is kept)")


def cmd_watch(a):
    from pdfmanager import watch

    settings = watch.WatchSettings(
        inbox=a.inbox, dest=a.dest, by=a.by, rename=a.rename, compress=a.compress,
        sharp_scans=a.sharp_scans,
        remove_blank=a.clean, auto_rotate=a.rotate,
        recipe=recipes.get(a.recipe)["steps"] if a.recipe else None,
        ocr=a.ocr, ocr_language=a.language, ocr_engine=a.engine, skip_duplicates=a.skip_duplicates,
        keep_originals=not a.delete_originals)
    if a.once:
        state = watch.once(settings, settle=min(a.interval, 2))
        return 1 if state.failed else 0
    watch.watch(settings, interval=a.interval)


def cmd_setup_windows(a):
    from pdfmanager import winsetup

    if a.remove:
        winsetup.remove()
        print("Removed the shortcuts, the right-click actions and the Open with and Settings > Apps entries.")
        return 0
    for line in winsetup.install(a.app_dir, desktop=not a.no_desktop, open_with=not a.no_open_with,
                                 context_menu=not a.no_context_menu):
        print(f"added {line}")
    return 0


def cmd_gui(a):
    from pdfmanager import gui

    paths = _expand(getattr(a, "files", None) or [])
    for path in paths:
        if not Path(path).is_file():
            raise core.PdfManagerError(f"no such file: {path}")
    action = getattr(a, "action", None)
    if a.browser or a.no_browser:
        gui.serve(port=a.port, open_browser=not a.no_browser, open_paths=paths,
                  action=(action, paths) if action else None)
    else:
        gui.launch(paths, port=a.port, action=action, background_start=bool(getattr(a, "background", False)))

