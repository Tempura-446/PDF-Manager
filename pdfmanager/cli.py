"""Command-line interface: ``pdfm <command> ...`` (or ``python -m pdfmanager``)."""

import argparse
import getpass
import glob
import json
import logging
import os
import sys
from pathlib import Path

from pypdf import PdfReader

from pdfmanager import (__version__, archive, compare, core, imaging, office, organize, outline,
                        recipes, redact, scan)
from pdfmanager.pageranges import PageRangeError

PAGES_HELP = "pages, e.g. '1-3,5', '7-', 'odd', 'even', 'end' (1-based)"
PASSWORD_ENV = "PDFM_PASSWORD"


def _default_output(path, suffix, ext=".pdf"):
    p = Path(path)
    return p.with_name(f"{p.stem}_{suffix}{ext}")


def _human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _print_plan(plan, verb, dry_run):
    for src, dst in plan:
        if dst is None:
            print(f"{'would skip' if dry_run else 'skipped'} duplicate: {src}")
        else:
            print(f"{'would ' + verb if dry_run else verb}: {src} -> {dst}")
    count = sum(1 for _, dst in plan if dst is not None)
    print(f"{count} file(s) {'to ' + verb if dry_run else 'done'}")


def _expand(patterns):
    """Expand wildcards ourselves so `*.pdf` also works on Windows."""
    paths = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern)) if glob.has_magic(pattern) else []
        if glob.has_magic(pattern) and not matches:
            raise core.PdfManagerError(f"no files match {pattern!r}")
        paths.extend(matches or [pattern])
    return paths


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------

class _Passwords:
    """Find the password for each encrypted input: --password, then the
    PDFM_PASSWORD environment variable, then passwords already typed in
    this run, then an interactive prompt. Passwords given on the command
    line end up in shell history, so prompting is preferred."""

    def __init__(self, given):
        self.known = [p for p in (given, os.environ.get(PASSWORD_ENV)) if p]

    def for_file(self, path):
        try:
            reader = PdfReader(str(path))
        except Exception:
            return None  # let the command report the real error
        if not reader.is_encrypted:
            return None
        for candidate in ["", *self.known]:
            if reader.decrypt(candidate):
                return candidate
        if not sys.stdin.isatty():
            raise core.PdfManagerError(
                f"{path} is encrypted; pass --password or set {PASSWORD_ENV}")
        for _ in range(3):
            candidate = getpass.getpass(f"Password for {Path(path).name}: ")
            if reader.decrypt(candidate):
                self.known.append(candidate)
                return candidate
            print("wrong password", file=sys.stderr)
        raise core.PdfManagerError(f"wrong password for {path}")


def _ask_new_password():
    pw = getpass.getpass("New password: ")
    if getpass.getpass("Repeat password: ") != pw:
        raise core.PdfManagerError("passwords do not match")
    if not pw:
        raise core.PdfManagerError("password must not be empty")
    return pw


# --------------------------------------------------------------------------
# Per-file commands. Each takes (args, input path, output path, password).
# --------------------------------------------------------------------------

def run_extract(a, src, out, pw):
    return core.select_pages(src, out, a.pages, pw)


def run_delete(a, src, out, pw):
    return core.delete_pages(src, out, a.pages, pw)


def run_reorder(a, src, out, pw):
    return core.select_pages(src, out, a.order, pw)


def run_reverse(a, src, out, pw):
    return core.reverse(src, out, pw)


def run_rotate(a, src, out, pw):
    return core.rotate(src, out, a.degrees, a.pages, pw)


def run_resize(a, src, out, pw):
    return core.resize(src, out, size=a.size, scale=a.scale, fit=a.fit, pages=a.pages,
                       auto_orient=not a.keep_orientation, password=pw)


def _crop_margins(values):
    vals = [float(v) * core.POINTS_PER_MM for v in values]
    if len(vals) == 1:
        return vals * 4
    if len(vals) == 2:  # vertical, horizontal (like CSS)
        return [vals[1], vals[0], vals[1], vals[0]]
    if len(vals) == 4:  # top right bottom left (CSS order) -> left bottom right top
        t, r, b, l = vals
        return [l, b, r, t]
    raise core.PdfManagerError("give 1, 2 or 4 margin values")


def run_crop(a, src, out, pw):
    return core.crop(src, out, _crop_margins(a.margins), a.pages, pw)


def run_nup(a, src, out, pw):
    return core.n_up(src, out, a.per_sheet, a.size, pw)


def run_compress(a, src, out, pw):
    result = core.compress(src, out, level=a.level, image_quality=a.image_quality,
                           max_dpi=a.max_dpi, password=pw)
    before, after = Path(src).stat().st_size, Path(result).stat().st_size
    pct = (1 - after / before) * 100 if before else 0
    return f"{result}  ({_human_size(before)} -> {_human_size(after)}, {pct:.1f}% smaller)"


def run_color(a, src, out, pw):
    return core.convert_color(src, out, a.to, a.icc, pw)


def run_encrypt(a, src, out, pw):
    return core.encrypt(src, out, a.user_password, a.owner_password, pw)


def run_decrypt(a, src, out, pw):
    if pw is None:
        raise core.PdfManagerError(f"{src} is not encrypted")
    return core.decrypt(src, out, pw)


def run_metadata(a, src, out, pw):
    fields = dict(title=a.title, author=a.author, subject=a.subject, keywords=a.keywords)
    if all(v is None for v in fields.values()):
        _print_info(core.info(src, pw))
        return None
    return core.set_metadata(src, out, pw, **fields)


def run_watermark(a, src, out, pw):
    return core.watermark(src, out, text=a.text, stamp_pdf=a.stamp, opacity=a.opacity,
                          font_size=a.font_size, angle=a.angle, pages=a.pages,
                          below=a.below, password=pw)


def run_number(a, src, out, pw):
    return core.number_pages(src, out, fmt=a.format, position=a.position,
                             font_size=a.font_size, start=a.start, pages=a.pages, password=pw)


def run_ocr(a, src, out, pw):
    return core.ocr(src, out, language=a.language, force=a.force, deskew=a.deskew, password=pw)


def run_clean(a, src, out, pw):
    report = scan.clean(src, out, remove_blank=not a.keep_blank, sensitivity=a.sensitivity,
                        auto_rotate=a.rotate, password=pw)
    bits = []
    if report["removed"]:
        bits.append("removed blank pages " + ", ".join(map(str, report["removed"])))
    if report["rotated"]:
        bits.append("turned " + ", ".join(f"page {n} by {d}°" for n, d in report["rotated"].items()))
    return f"{out}  ({'; '.join(bits) or 'nothing needed changing'})"


def run_flatten(a, src, out, pw):
    if a.forms_only and a.comments_only:
        raise core.PdfManagerError("choose --forms-only or --comments-only, not both")
    return archive.flatten(src, out, forms=not a.comments_only, comments=not a.forms_only,
                           password=pw)


def run_sanitize(a, src, out, pw):
    counts = archive.sanitize(src, out, metadata=not a.keep_metadata,
                              javascript=not a.keep_javascript,
                              attachments=not a.keep_attachments, comments=a.comments,
                              password=pw)
    found = ", ".join(f"{n} {k}" for k, n in counts.items() if n) or "nothing found"
    return f"{out}  (removed: {found})"


def run_pdfa(a, src, out, pw):
    return archive.to_pdfa(src, out, level=a.level, password=pw)


def run_bookmarks(a, src, out, pw):
    if a.set and a.detect:
        raise core.PdfManagerError("use --set or --detect, not both")
    if a.set:
        text = Path(a.set).read_text(encoding="utf-8-sig")
        items = (json.loads(text) if a.set.lower().endswith(".json") else outline.from_text(text))
    elif a.detect:
        items = outline.detect_headings(src, pw)
        if a.dry_run:
            print(outline.to_text(items) or "no headings found", end="")
            return None
    elif a.clear:
        items = []
    else:
        items = outline.get_outline(src, pw)
        print(outline.to_text(items) or "no bookmarks", end="")
        return None
    outline.set_outline(src, out, items, pw)
    return f"{out}  ({len(items)} bookmark{'s' if len(items) != 1 else ''})"


def run_to_word(a, src, out, pw):
    result, engine = office.pdf_to_word(src, out, engine=a.engine, password=pw)
    how = {"word": "Microsoft Word", "basic": "built-in converter",
           "layout": "LibreOffice, exact layout"}[engine]
    return f"{result}  ({how})"


def cmd_from_office(a):
    failures = 0
    files = _expand(a.inputs)
    for src in files:
        if len(files) == 1 and a.output:
            out = Path(a.output)
        else:
            folder = Path(a.output) if a.output else Path(src).parent
            folder.mkdir(parents=True, exist_ok=True)
            out = folder / (Path(src).stem + ".pdf")
        try:
            result, program = office.office_to_pdf(src, out)
            print(f"{src} -> {result}  ({program})")
        except (core.PdfManagerError, OSError) as exc:
            if len(files) == 1:
                raise
            failures += 1
            print(f"error: {src}: {exc}", file=sys.stderr)
    return 1 if failures else 0


def run_redact(a, src, out, pw):
    patterns = list(a.pattern or []) + [redact.PRESETS[p] for p in a.find or []]
    if not (a.text or patterns or a.box):
        raise core.PdfManagerError("say what to redact: --text, --pattern, --find or --box")
    found = redact.redact(src, out, terms=a.text or [], patterns=patterns, boxes=a.box or [],
                          case_sensitive=a.case_sensitive, whole_words=a.whole_words,
                          dpi=a.dpi, password=pw)
    if not found:
        return f"{out}  (nothing found to redact)"
    return f"{out}  ({sum(found.values())} area(s) on page(s) {', '.join(map(str, found))})"


def run_headers(a, src, out, pw):
    texts = {spot: getattr(a, spot) for spot in core.HEADER_SPOTS}
    # Bates numbers continue from file to file in a batch.
    first = getattr(a, "bates_next", a.bates_start)
    a.bates_next = core.headers_footers(
        src, out, texts, font_size=a.font_size, margin_mm=a.margin, pages=a.pages, start=a.start,
        bates_prefix=a.bates or "", bates_start=first, bates_digits=a.bates_digits, password=pw)
    if any("{bates}" in (t or "") for t in texts.values()):
        width = a.bates_digits
        return f"{out}  (Bates {a.bates or ''}{first:0{width}d}–{a.bates or ''}{a.bates_next - 1:0{width}d})"
    return out


def run_text(a, src, out, pw):
    text = core.extract_text(src, out, a.pages, pw)
    if out is None:
        print(text)
        return None
    return out


def run_split(a, src, out, pw):
    files = core.split(src, out, every=a.every, ranges=a.ranges, prefix=a.prefix, password=pw)
    for f in files if pw else ():
        _reencrypt(f, pw)
    return f"{len(files)} file(s) in {out}"


def run_to_images(a, src, out, pw):
    files = core.pdf_to_images(src, out, a.dpi, a.format, a.pages, pw)
    return f"{len(files)} image(s) in {out}"


def _print_info(data):
    print(f"{data['file']}")
    print(f"  pages:     {data['pages']}")
    print(f"  file size: {_human_size(data['file_size'])}")
    print("  page size: " + ", ".join(f"{k} ({v})" for k, v in data["page_sizes"].items()))
    for key in ("title", "author", "subject", "creator", "producer", "created", "modified"):
        if data[key]:
            print(f"  {key + ':':10} {data[key]}")
    if data["encrypted"]:
        print("  encrypted: yes")


def run_info(a, src, out, pw):
    data = core.info(src, pw)
    if a.json:
        print(json.dumps(data, default=str, indent=2))
    else:
        _print_info(data)


# How each per-file command names its output when -o is not given:
# (suffix, kind) where kind is "pdf", "dir", "txt" or None (no output).
OUTPUTS = {
    "info": (None, None), "metadata": ("edited", "pdf"), "text": (None, "txt"),
    "extract": ("extract", "pdf"), "delete": ("edited", "pdf"),
    "reorder": ("reordered", "pdf"), "reverse": ("reversed", "pdf"),
    "rotate": ("rotated", "pdf"), "resize": ("resized", "pdf"), "crop": ("cropped", "pdf"),
    "nup": ("nup", "pdf"), "compress": ("compressed", "pdf"), "color": ("color", "pdf"),
    "encrypt": ("encrypted", "pdf"), "decrypt": ("decrypted", "pdf"),
    "watermark": ("watermarked", "pdf"), "number": ("numbered", "pdf"), "ocr": ("ocr", "pdf"), "clean": ("clean", "pdf"),
    "flatten": ("flat", "pdf"), "redact": ("redacted", "pdf"), "headers": ("headers", "pdf"), "bookmarks": ("bookmarks", "pdf"), "to-word": (None, "docx"), "sanitize": ("sanitized", "pdf"), "pdfa": ("pdfa", "pdf"),
    "split": ("split", "dir"), "to-images": ("images", "dir"),
}


def _output_for(a, src, batch):
    suffix, kind = OUTPUTS[a.command]
    if kind is None:
        return None
    if a.command == "metadata" and a.output is None:
        return src  # metadata edits happen in place by default
    if a.command == "text" and a.output is None:
        return None  # print to the terminal
    src = Path(src)
    if kind == "docx":
        if not batch:
            return Path(a.output) if a.output else src.with_suffix(".docx")
        folder = Path(a.output) if a.output else src.parent
        folder.mkdir(parents=True, exist_ok=True)
        return folder / (src.stem + ".docx")
    if kind == "dir":
        base = Path(a.output) if a.output else src.parent
        return base / f"{src.stem}_{suffix}" if (batch or not a.output) else base
    if not batch:
        return Path(a.output) if a.output else _default_output(src, suffix)
    if a.output:  # batch: -o is a folder; keep the original file names
        folder = Path(a.output)
        if folder.exists() and not folder.is_dir():
            raise core.PdfManagerError("with several input files, -o must be a folder")
        folder.mkdir(parents=True, exist_ok=True)
        if folder.resolve() != src.parent.resolve():
            return folder / (src.stem + (".txt" if kind == "txt" else ".pdf"))
    return _default_output(src, suffix, ".txt" if kind == "txt" else ".pdf")


# Commands whose output should not inherit the input's password.
# (split re-encrypts its own output files.)
KEEP_UNENCRYPTED = {"decrypt", "encrypt", "info", "text", "to-images", "split", "to-word",
                    "pdfa"}  # PDF/A doesn't allow encryption


def _reencrypt(out, password):
    """Outputs made from a password-protected file get the same password,
    so editing a protected PDF never silently produces an unprotected one."""
    core.encrypt(out, out, password)


def run_per_file(a):
    inputs = _expand(a.inputs)
    batch = len(inputs) > 1
    passwords = _Passwords(a.password)
    if a.command == "encrypt" and not a.user_password:
        a.user_password = _ask_new_password()
    failures = 0
    for src in inputs:
        try:
            out = _output_for(a, src, batch)
            pw = passwords.for_file(src)
            result = a.run(a, src, out, pw)
            if pw and a.command not in KEEP_UNENCRYPTED and result is not None:
                _reencrypt(out, pw)
            if result is not None:
                print(f"{src} -> {result}" if batch else f"-> {result}")
        except (core.PdfManagerError, PageRangeError, FileNotFoundError, OSError) as exc:
            if not batch:
                raise
            failures += 1
            message = str(exc)
            print(f"error: {message if str(src) in message else f'{src}: {message}'}",
                  file=sys.stderr)
    if batch:
        print(f"{len(inputs) - failures} of {len(inputs)} file(s) done")
    return 1 if failures else 0


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
    pws = {passwords.for_file(i[0] if isinstance(i, tuple) else i) for i in inputs} - {None}
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
    out = a.output or _default_output(a.old, f"vs_{Path(a.new).stem}")
    summary = compare.compare(a.old, a.new, out, passwords.for_file(a.old),
                              passwords.for_file(a.new), all_pages=a.all_pages)
    changes = [c for c in summary["changes"] if c["kind"] != "same"]
    for c in changes:
        where = (f"page {c['new']} added" if c["kind"] == "added" else
                 f"page {c['old']} removed" if c["kind"] == "removed" else
                 f"page {c['old']}" + (f" (now {c['new']})" if c["old"] != c["new"] else ""))
        detail = "; ".join([f"- {w}" for w in c["removed"][:3]] + [f"+ {w}" for w in c["added"][:3]])
        print(f"{where}: {detail or 'layout or pictures changed'}" if c["kind"] == "changed"
              else where)
    print(f"{len(changes)} page(s) differ" if changes else "no differences")
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
        failures = 0
        for src in files:
            try:
                if len(files) == 1 and a.output:
                    out = Path(a.output)
                elif a.output:
                    Path(a.output).mkdir(parents=True, exist_ok=True)
                    out = Path(a.output) / Path(src).name
                else:
                    out = _default_output(src, slug)
                pw = passwords.for_file(src)
                notes = []
                recipes.run(steps, src, out, pw, notes)
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
        remove_blank=a.clean, auto_rotate=a.rotate,
        recipe=recipes.get(a.recipe)["steps"] if a.recipe else None,
        ocr=a.ocr, ocr_language=a.language, skip_duplicates=a.skip_duplicates,
        keep_originals=not a.delete_originals)
    if a.once:
        state = watch._State()
        watch.poll(settings, state)  # first poll only records sizes
        watch.poll(settings, state)
        return
    watch.watch(settings, interval=a.interval)


def cmd_gui(a):
    from pdfmanager import gui

    paths = _expand(getattr(a, "files", None) or [])
    for path in paths:
        if not Path(path).is_file():
            raise core.PdfManagerError(f"no such file: {path}")
    if a.browser or a.no_browser:
        gui.serve(port=a.port, open_browser=not a.no_browser, open_paths=paths)
    else:
        gui.launch(paths, port=a.port)


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="pdfm",
        description="Manage PDF files: merge, split, resize, rotate, compress, convert "
                    "colours, OCR, organize and more. Run `pdfm gui` for a graphical app.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def per_file(name, run, help_, out_help=None):
        """A command that processes one or more input PDFs independently."""
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.add_argument("inputs", nargs="+", metavar="input",
                        help="input PDF(s); wildcards like *.pdf are allowed")
        sp.add_argument("-o", "--output", help=out_help or (
            "output file (default: <input>_<command>.pdf); with several inputs, "
            "a folder to write them into"))
        sp.add_argument("--password", help=f"password for encrypted input (better: let pdfm "
                                           f"prompt, or set {PASSWORD_ENV})")
        sp.set_defaults(func=run_per_file, run=run)
        return sp

    def other(name, func, help_):
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.set_defaults(func=func)
        return sp

    sp = per_file("info", run_info, "show page count, sizes and metadata")
    sp.add_argument("--json", action="store_true", help="print JSON")

    sp = other("merge", cmd_merge, "combine PDFs into one")
    sp.add_argument("inputs", nargs="+", help="PDFs to merge; use file.pdf:1-3 to pick pages")
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("--no-bookmarks", action="store_true",
                    help="don't add a bookmark for each merged file")
    sp.add_argument("--password")

    sp = per_file("split", run_split, "split a PDF into several files",
                  out_help="output folder (default: <input>_split)")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("-n", "--every", type=int, help="pages per output file (default: 1)")
    g.add_argument("-r", "--ranges", nargs="+", metavar="RANGE",
                   help="one output file per range, e.g. -r 1-3 4-10 11-")
    sp.add_argument("--prefix", help="output filename prefix (default: input name)")

    sp = per_file("extract", run_extract, "copy selected pages into a new PDF")
    sp.add_argument("-p", "--pages", required=True, help=PAGES_HELP)

    sp = per_file("delete", run_delete, "remove pages")
    sp.add_argument("-p", "--pages", required=True, help=PAGES_HELP)

    sp = per_file("reorder", run_reorder, "rearrange pages (pages can be repeated or dropped)")
    sp.add_argument("--order", required=True, help="new page order, e.g. '3,1,2,4-'")

    per_file("reverse", run_reverse, "reverse page order")

    sp = other("interleave", cmd_interleave, "merge separately scanned front and back sides")
    sp.add_argument("front")
    sp.add_argument("back")
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("--no-reverse", action="store_true",
                    help="back pages are already in order (not reversed)")
    sp.add_argument("--password")

    sp = per_file("rotate", run_rotate, "rotate pages")
    sp.add_argument("-d", "--degrees", type=int, required=True, help="90, 180, 270 or -90")
    sp.add_argument("-p", "--pages", help=PAGES_HELP + " (default: all)")

    sp = per_file("resize", run_resize, "change page size (e.g. to A4 or Letter) or scale pages")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("-s", "--size",
                   help="a4, a3, a5, letter, legal, ..., '210x297mm', '8.5x11in'; "
                        "append -landscape to force landscape")
    g.add_argument("--scale", type=float, help="scale factor, e.g. 0.5")
    sp.add_argument("--fit", choices=["fit", "fill", "stretch", "none"], default="fit",
                    help="how content is placed on the new size (default: fit)")
    sp.add_argument("--keep-orientation", action="store_true",
                    help="don't rotate the target size to match landscape pages")
    sp.add_argument("-p", "--pages", help=PAGES_HELP + " (default: all)")

    sp = per_file("crop", run_crop, "trim page margins")
    sp.add_argument("-m", "--margins", nargs="+", required=True, metavar="MM",
                    help="margins in mm: ALL | VERTICAL HORIZONTAL | TOP RIGHT BOTTOM LEFT")
    sp.add_argument("-p", "--pages", help=PAGES_HELP + " (default: all)")

    sp = per_file("nup", run_nup, "print several pages per sheet")
    sp.add_argument("-n", "--per-sheet", type=int, default=2, choices=[2, 4, 6, 8, 9])
    sp.add_argument("-s", "--size", default="a4", help="sheet size (default: a4)")

    sp = per_file("compress", run_compress, "reduce file size")
    sp.add_argument("-l", "--level", default="medium",
                    choices=["low", "medium", "high", "large"],
                    help="low: lossless only; medium (default): images max 150 DPI, "
                         "JPEG quality 75; high/large: images max 96 DPI, quality 50")
    sp.add_argument("-q", "--image-quality", type=int, metavar="1-95",
                    help="override the level's JPEG quality")
    sp.add_argument("--max-dpi", type=int, help="override the level's image resolution")

    sp = per_file("color", run_color, "convert colours to grayscale, RGB or CMYK")
    sp.add_argument("--to", required=True, choices=imaging.COLOR_TARGETS)
    sp.add_argument("--icc", metavar="PROFILE.icc",
                    help="convert images through this ICC profile and embed it as the "
                         "output intent (e.g. your printer's CMYK profile)")

    sp = per_file("encrypt", run_encrypt, "password-protect PDFs (AES-256)")
    sp.add_argument("--user-password", help="password to open (prompted if omitted)")
    sp.add_argument("--owner-password", help="password for full permissions")

    per_file("decrypt", run_decrypt, "remove password protection")

    sp = per_file("metadata", run_metadata, "show or set title/author/subject/keywords",
                  out_help="output file (default: edit in place)")
    sp.add_argument("--title")
    sp.add_argument("--author")
    sp.add_argument("--subject")
    sp.add_argument("--keywords")

    sp = per_file("watermark", run_watermark, "stamp text or another PDF onto pages")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("-t", "--text", help="watermark text, e.g. CONFIDENTIAL")
    g.add_argument("--stamp", help="PDF whose first page is overlaid")
    sp.add_argument("--opacity", type=float, default=0.15)
    sp.add_argument("--font-size", type=float, default=60)
    sp.add_argument("--angle", type=float, default=45)
    sp.add_argument("--below", action="store_true", help="place under the page content")
    sp.add_argument("-p", "--pages", help=PAGES_HELP + " (default: all)")

    sp = per_file("number", run_number, "add page numbers")
    sp.add_argument("--format", default="{n} / {total}", help="default: '{n} / {total}'")
    sp.add_argument("--position", default="bottom-center",
                    choices=["bottom-center", "bottom-right", "bottom-left",
                             "top-center", "top-right", "top-left"])
    sp.add_argument("--font-size", type=float, default=10)
    sp.add_argument("--start", type=int, default=1, help="first number (default: 1)")
    sp.add_argument("-p", "--pages", help=PAGES_HELP + " (default: all)")

    sp = per_file("headers", run_headers,
                  "add headers and footers, e.g. a title, the date or Bates numbers")
    for spot in core.HEADER_SPOTS:
        sp.add_argument("--" + spot.replace("_", "-"), dest=spot, metavar="TEXT",
                        help=f"text {spot.replace('_', ' ')}" if spot != "top_left" else
                        "text top left; any text may use {n} (page), {total}, {file}, {title}, "
                        "{date} and {bates}")
    sp.add_argument("--font-size", type=float, default=9)
    sp.add_argument("--margin", type=float, default=10, help="distance from the edge in mm")
    sp.add_argument("--start", type=int, default=1, help="first page number (default: 1)")
    sp.add_argument("--bates", metavar="PREFIX", help="Bates prefix for {bates}, e.g. ABC")
    sp.add_argument("--bates-start", type=int, default=1, help="first Bates number")
    sp.add_argument("--bates-digits", type=int, default=6, help="digits (default: 6)")
    sp.add_argument("-p", "--pages", help=PAGES_HELP + " (default: all)")

    sp = per_file("ocr", run_ocr, "make scanned PDFs searchable (needs OCRmyPDF + Tesseract)")
    sp.add_argument("--language", default="eng", help="Tesseract language(s), e.g. eng+deu")
    sp.add_argument("--force", action="store_true", help="redo pages that already have text")
    sp.add_argument("--deskew", action="store_true", help="straighten crooked scans")

    sp = per_file("clean", run_clean, "clean up scans: remove blank pages, turn pages upright")
    sp.add_argument("--keep-blank", action="store_true", help="don't remove blank pages")
    sp.add_argument("--sensitivity", choices=list(scan.BLANK_THRESHOLDS), default="medium",
                    help="blank page detection: low (only empty pages), medium (default), "
                         "high (also nearly empty pages)")
    sp.add_argument("--rotate", action="store_true",
                    help="turn sideways/upside-down pages upright (needs Tesseract)")

    sp = per_file("flatten", run_flatten,
                  "make form fields and comments part of the page (links keep working)")
    sp.add_argument("--forms-only", action="store_true", help="leave comments as they are")
    sp.add_argument("--comments-only", action="store_true", help="leave form fields as they are")

    sp = per_file("sanitize", run_sanitize,
                  "remove hidden data: document details, scripts, attached files")
    sp.add_argument("--keep-metadata", action="store_true",
                    help="keep author, title, dates and other document details")
    sp.add_argument("--keep-javascript", action="store_true", help="keep scripts and actions")
    sp.add_argument("--keep-attachments", action="store_true", help="keep attached files")
    sp.add_argument("--comments", action="store_true",
                    help="also remove comments, highlights and notes")

    sp = per_file("redact", run_redact,
                  "permanently black out text or areas (redacted pages become images)")
    sp.add_argument("-t", "--text", action="append", metavar="TEXT",
                    help="text to black out (repeat for more)")
    sp.add_argument("--pattern", action="append", metavar="REGEX",
                    help="regular expression to black out, e.g. '\\d{3}-\\d{2}-\\d{4}'")
    sp.add_argument("--find", action="append", choices=list(redact.PRESETS),
                    help="also black out all emails, phone numbers or long numbers")
    sp.add_argument("--box", action="append", metavar="PAGE:X0,Y0,X1,Y1",
                    help="black out an area, in points from the bottom-left corner")
    sp.add_argument("--case-sensitive", action="store_true")
    sp.add_argument("--whole-words", action="store_true")
    sp.add_argument("--dpi", type=int, default=200, help="resolution of redacted pages")

    sp = per_file("pdfa", run_pdfa, "convert to PDF/A for long-term archiving (needs Ghostscript)")
    sp.add_argument("--level", choices=archive.PDFA_LEVELS, default="2",
                    help="PDF/A-1b, -2b (default) or -3b")

    sp = per_file("bookmarks", run_bookmarks,
                  "list, set or detect bookmarks (without options: list them)")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--set", metavar="FILE",
                   help="replace the bookmarks with those in FILE: one per line, "
                        "'- Title (p. 3)', indented two spaces per level (or JSON)")
    g.add_argument("--detect", action="store_true",
                   help="create bookmarks from the document's headings")
    g.add_argument("--clear", action="store_true", help="remove all bookmarks")
    sp.add_argument("-n", "--dry-run", action="store_true",
                    help="with --detect: only print what would be added")

    sp = per_file("text", run_text, "extract text",
                  out_help="write to a .txt file instead of the terminal")
    sp.add_argument("-p", "--pages", help=PAGES_HELP)

    sp = other("compare", cmd_compare, "compare two versions of a PDF and write a report")
    sp.add_argument("old", help="the original")
    sp.add_argument("new", help="the changed version")
    sp.add_argument("-o", "--output", help="report file (default: <old>_vs_<new>.pdf)")
    sp.add_argument("--all-pages", action="store_true",
                    help="show unchanged pages in the report too")
    sp.add_argument("--password")

    sp = per_file("to-word", run_to_word, "convert PDFs to editable Word documents (.docx)",
                  out_help="output .docx (default: <input>.docx); with several inputs, a folder")
    sp.add_argument("--engine", choices=office.TO_WORD_ENGINES, default="auto",
                    help="auto (default): Microsoft Word if installed, else the built-in "
                         "converter; word; basic (built-in: paragraphs, headings, pictures); "
                         "layout (LibreOffice: exact look, text in boxes)")

    sp = other("from-office", cmd_from_office,
               "convert Word, Excel, PowerPoint, OpenDocument, RTF or text files to PDF "
               "(needs Microsoft Office or LibreOffice; text files always work)")
    sp.add_argument("inputs", nargs="+")
    sp.add_argument("-o", "--output", help="output PDF, or a folder for several inputs "
                                           "(default: next to each input)")

    sp = other("recipe", cmd_recipe, "save and run recipes: several tools in a row")
    sp.add_argument("action", choices=["list", "show", "save", "run", "delete"])
    sp.add_argument("name", nargs="?", help="recipe name")
    sp.add_argument("args", nargs="*", metavar="ARG",
                    help="save: steps and their options, e.g. clean ocr language=eng compress "
                         "level=high; run: the PDF(s)")
    sp.add_argument("-o", "--output", help="run: output file, or a folder for several inputs")
    sp.add_argument("--password")

    sp = other("from-images", cmd_from_images, "save images (JPG, PNG, ...) as a PDF")
    sp.add_argument("images", nargs="+")
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("-s", "--size", help="fit images onto this paper size (default: image size)")
    sp.add_argument("--margin", type=float, default=0, help="margin in mm")

    sp = per_file("to-images", run_to_images, "render pages to image files",
                  out_help="output folder (default: <input>_images)")
    sp.add_argument("--dpi", type=int, default=150)
    sp.add_argument("-f", "--format", default="png", help="png, jpg, ... (default: png)")
    sp.add_argument("-p", "--pages", help=PAGES_HELP)

    sp = other("organize", cmd_organize, "sort PDFs in a folder into sub-folders")
    sp.add_argument("directory")
    sp.add_argument("--by", choices=organize.GROUPINGS, default="year")
    sp.add_argument("--dest", help="where to create the sub-folders (default: the directory)")
    sp.add_argument("--move", action="store_true", help="move files instead of copying")
    sp.add_argument("--duplicates", choices=organize.DUPLICATE_MODES, default="keep",
                    help="what to do with files identical to one already organized: keep "
                         "(default), skip, or folder (put in a Duplicates folder)")
    sp.add_argument("-R", "--recursive", action="store_true")
    sp.add_argument("-n", "--dry-run", action="store_true", help="only show what would happen")

    sp = other("rename", cmd_rename, "rename PDFs from their metadata")
    sp.add_argument("directory")
    sp.add_argument("--pattern", default="{date:%Y-%m-%d} {title}",
                    help="fields: {title} {author} {pages} {date:%%Y-%%m-%%d} {stem}")
    sp.add_argument("-R", "--recursive", action="store_true")
    sp.add_argument("-n", "--dry-run", action="store_true")

    sp = other("catalog", cmd_catalog, "write a CSV inventory of a folder of PDFs")
    sp.add_argument("directory")
    sp.add_argument("-o", "--output", default="catalog.csv")
    sp.add_argument("-R", "--recursive", action="store_true")

    sp = other("duplicates", cmd_duplicates, "find identical PDFs in a folder")
    sp.add_argument("directory")
    sp.add_argument("-R", "--recursive", action="store_true")
    sp.add_argument("--delete", action="store_true", help="delete all but the oldest copy")

    sp = other("watch", cmd_watch,
               "watch an inbox folder and process new PDFs automatically")
    sp.add_argument("inbox")
    sp.add_argument("--dest", required=True, help="where processed files go")
    sp.add_argument("--by", choices=organize.GROUPINGS, help="sort into sub-folders")
    sp.add_argument("--rename", metavar="PATTERN", help="rename, e.g. '{date:%%Y-%%m-%%d} {title}'")
    sp.add_argument("--compress", choices=["low", "medium", "high", "large"])
    sp.add_argument("--clean", action="store_true", help="remove blank pages")
    sp.add_argument("--rotate", action="store_true",
                    help="turn sideways/upside-down pages upright (needs Tesseract)")
    sp.add_argument("--ocr", action="store_true", help="make scans searchable")
    sp.add_argument("--recipe", metavar="NAME", help="run a saved recipe on each file")
    sp.add_argument("--language", default="eng", help="OCR language(s)")
    sp.add_argument("--skip-duplicates", action="store_true",
                    help="don't process files identical to one already in --dest")
    sp.add_argument("--delete-originals", action="store_true",
                    help="delete originals instead of moving them to <inbox>/originals")
    sp.add_argument("--interval", type=float, default=5, help="seconds between checks")
    sp.add_argument("--once", action="store_true", help="process what's there now and exit")

    for name, help_ in (("gui", "open the graphical app in its own window (or your browser "
                                "if no window can be opened)"),
                        ("view", "read PDFs in the app's PDF reader")):
        sp = other(name, cmd_gui, help_)
        sp.add_argument("files", nargs="*" if name == "gui" else "+",
                        help="PDF(s) to open in the reader")
        sp.add_argument("--browser", action="store_true",
                        help="use the web browser, not a window")
        sp.add_argument("--port", type=int, default=0, help="port (default: any free port)")
        sp.add_argument("--no-browser", action="store_true",
                        help="just start the server and print its link")

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    # pypdf logs a warning for every small defect it repairs; only show errors.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        return args.func(args) or 0
    except (core.PdfManagerError, PageRangeError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
