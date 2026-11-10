"""Command-line interface: ``pdfm <command> ...`` (or ``python -m pdfmanager``)."""

import argparse
import getpass
import glob
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

from pypdf import PdfReader

from pdfmanager import (__version__, archive, compare, core, imaging, office, organize, outline,
                        forms, recipes, redact, scan, search)
from pdfmanager import certsign, comments, filetools, formdetect, pdfacheck
from pdfmanager import accessibility  # -- accessibility --
from pdfmanager import ocr  # the OCR engine ids
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
        if os.path.exists(pattern):  # a real name, even with [ ] in it
            paths.append(pattern)
            continue
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

    def __init__(self, given, given_first=False):
        found = (("--password", given), (PASSWORD_ENV, os.environ.get(PASSWORD_ENV)))
        self.known = [p for _, p in found if p]
        self.sources = [name for name, p in found if p]
        # decrypt: a PDF anyone can open but with limits needs its
        # permissions password, so a given password goes before "".
        self.given_first = given_first

    def for_file(self, path):
        try:
            reader = PdfReader(str(path))
        except Exception:
            return None  # let the command report the real error
        if not reader.is_encrypted:
            return None
        for candidate in ([*self.known, ""] if self.given_first else ["", *self.known]):
            if reader.decrypt(candidate):
                return candidate
        if not sys.stdin.isatty():
            if self.sources:  # a password was supplied; it's simply wrong
                raise core.PdfManagerError(
                    f"wrong password for {path} (tried {' and '.join(self.sources)})")
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
    if not sys.stdin.isatty():
        raise core.PdfManagerError("no terminal to ask for a password; pass --user-password")
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
    if a.auto == bool(a.margins):
        raise core.PdfManagerError("give the margins to trim (-m) or --auto, not both")
    if not a.auto:
        return core.crop(src, out, _crop_margins(a.margins), a.pages, pw)
    report = core.trim_margins(src, out, padding=a.padding * core.POINTS_PER_MM,
                               same=a.same, pages=a.pages, password=pw)
    blank = report["blank"]
    if blank:
        pages = ", ".join(map(str, blank))
        return f"{out}  (page{'s' if len(blank) > 1 else ''} {pages} " \
               f"{'are' if len(blank) > 1 else 'is'} blank and {'were' if len(blank) > 1 else 'was'} left as is)"
    return out


def run_nup(a, src, out, pw):
    return core.n_up(src, out, a.per_sheet, a.size, pw)


def run_insert_blank(a, src, out, pw):
    if not a.after and not a.at_start:
        raise core.PdfManagerError("say where the blank pages go: --after PAGES and/or --at-start")
    return core.insert_blank_pages(src, out, a.after, a.count, at_start=a.at_start, password=pw)


def run_booklet(a, src, out, pw):
    result = core.booklet(src, out, a.size, pw)
    sheets = len(PdfReader(str(result)).pages) // 2
    return (f"{result}  ({sheets} sheet{'s' if sheets != 1 else ''}: print on both sides, "
            "flipping on the short edge, then fold in the middle)")


def run_compress(a, src, out, pw):
    before = Path(src).stat().st_size  # before: the output may replace the input
    notes = []
    result = core.compress(src, out, level=a.level, image_quality=a.image_quality,
                           max_dpi=a.max_dpi, password=pw, sharp_scans=a.sharp_scans, notes=notes)
    after = Path(result).stat().st_size
    pct = (1 - after / before) * 100 if before else 0
    return (f"{result}  ({_human_size(before)} -> {_human_size(after)}, {pct:.1f}% smaller"
            + "".join(f"; {note}" for note in notes) + ")")


def run_color(a, src, out, pw):
    return core.convert_color(src, out, a.to, a.icc, pw)


def _encrypt_limits(a):
    return filetools.permissions(a.printing, a.changes, not a.no_copy)


def run_encrypt(a, src, out, pw):
    perms = _encrypt_limits(a)
    limited = filetools.restricts(perms)
    core.encrypt(src, out, a.user_password or "", a.owner_password, pw,
                 permissions=perms if limited else None)
    return f"{out}  ({filetools.describe(perms)})" if limited else out


# -- file tools --
def run_attach(a, src, out, pw):
    if a.list or not (a.add or a.remove or a.remove_all):
        found = filetools.attachments(src, pw)
        for item in found:
            size = f"{item['size']:,} bytes" if item["size"] is not None else "damaged"
            print(f"{item['name']}  ({size})" + (f"  {item['description']}" if item["description"] else ""))
        if not found:
            print("no attached files")
        return None
    added, removed = filetools.attach(src, out, a.add or [], a.remove or [], a.remove_all, pw,
                                      description=a.description or "")
    bits = ([f"attached {', '.join(added)}"] if added else []) + \
        ([f"removed {', '.join(removed)}"] if removed else [])
    return f"{out}  ({'; '.join(bits)})"


def run_labels(a, src, out, pw):
    if not a.set and not a.clear:
        labels = filetools.page_labels(src, pw)
        print("no page labels" if labels is None else
              "\n".join(f"{n}: {label}" for n, label in enumerate(labels, 1)))
        return None
    filetools.set_page_labels(src, out, [] if a.clear else filetools.parse_labels(a.set), pw)
    shown = filetools.page_labels(out)
    return f"{out}  ({'labels removed' if not shown else ', '.join(shown[:8]) + ('…' if len(shown) > 8 else '')})"


def run_repair(a, src, out, pw):
    report = filetools.repair(src, out, linearize=not a.no_linearize, password=pw)
    for warning in report["warnings"]:
        print(f"mended: {warning}")
    return f"{out}  ({len(report['warnings'])} problem(s) mended" + \
        (", fast web view)" if report["linearized"] else ")")
# -- end of file tools --


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
    try:
        a.format.format(n=1, total=1)
    except (KeyError, IndexError, ValueError):
        raise core.PdfManagerError(
            f"--format {a.format!r} can only use {{n}} and {{total}}") from None
    return core.number_pages(src, out, fmt=a.format, position=a.position,
                             font_size=a.font_size, start=a.start, pages=a.pages, password=pw)


def run_ocr(a, src, out, pw):
    return core.ocr(src, out, language=a.language, force=a.force, deskew=a.deskew, password=pw,
                    engine=a.engine, rotate=not a.keep_orientation)


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
    notes = []
    archive.to_pdfa(src, out, level=a.level, password=pw, engine=a.engine, notes=notes)
    said = "".join(f"; {note}" for note in notes)
    try:
        checked = pdfacheck.summary(pdfacheck.check(out, level=f"{a.level}b", engine="builtin"))
    except Exception:  # the check is extra; the file is made either way
        return f"{out}  ({said[2:]})" if said else out
    return f"{out}  (checked: {checked}{said})"


def cmd_check_pdfa(a):
    inputs = _expand(a.inputs)
    passwords = _Passwords(a.password)
    ok, records = True, []
    for src in inputs:
        try:
            result = pdfacheck.check(src, level=a.level, password=passwords.for_file(src),
                                     engine=a.engine)
        except (core.PdfManagerError, OSError) as exc:
            if len(inputs) == 1:
                raise
            ok = False
            message = str(exc)
            print(f"error: {message if str(src) in message else f'{src}: {message}'}",
                  file=sys.stderr)
            continue
        ok = ok and result["passed"]
        if a.json:
            records.append(result)
        else:
            print(("\n" if src != inputs[0] else "") + pdfacheck.report(result, str(src)), end="")
    if a.json:
        print(json.dumps(records, indent=2, ensure_ascii=False))
    return 0 if ok else 1


# -- digital ID signatures (certsign.py) --

PFX_PASSWORD_ENV = "PDFM_PFX_PASSWORD"


def _pfx_password(a):
    """The digital ID's password: --pfx-password, then PDFM_PFX_PASSWORD,
    then none (an ID without a password), then a prompt. Asked once per run."""
    if getattr(a, "pfx_checked", False):
        return a.pfx_password
    given = a.pfx_password if a.pfx_password is not None else os.environ.get(PFX_PASSWORD_ENV)
    try:
        certsign.load_id(a.pfx, given)
    except certsign.IdPasswordError:
        if given is not None:
            raise
        if not sys.stdin.isatty():
            raise core.PdfManagerError(f"{a.pfx} is protected; pass --pfx-password or set "
                                       f"{PFX_PASSWORD_ENV}") from None
        for _ in range(3):
            candidate = getpass.getpass(f"Password for the digital ID {Path(a.pfx).name}: ")
            try:
                certsign.load_id(a.pfx, candidate)
            except certsign.IdPasswordError:
                print("wrong password", file=sys.stderr)
                continue
            given = candidate
            break
        else:
            raise core.PdfManagerError(f"wrong password for {a.pfx}") from None
    a.pfx_password, a.pfx_checked = given, True
    return given


def run_sign_cert(a, src, out, pw):
    pfx_password = _pfx_password(a)
    if a.field and (a.page or a.box):
        raise core.PdfManagerError("use --field, or --page (and --box), not both")
    page = box = None
    if a.box:
        if not a.page:
            raise core.PdfManagerError("--box needs --page: the page the signature goes on")
        page, box = a.page, certsign.parse_box(a.box)
    elif a.page:
        page, box = certsign.place(src, a.page, a.position, a.size, pw)
    result = certsign.sign(src, out, a.pfx, pfx_password, page=page, box=box, field=a.field,
                           reason=a.reason, location=a.location, contact=a.contact,
                           timestamp_url=a.timestamp, password=pw)
    where = f"on page {result['page']}" if result["visible"] else "invisible"
    return (f"{out}  (signed by {result['signer']}, {where}"
            f"{', with a timestamp' if result['timestamp'] else ''})")


def cmd_verify(a):
    inputs = _expand(a.inputs)
    passwords = _Passwords(a.password)
    ok, records = True, []
    for src in inputs:
        try:
            found = certsign.verify(src, passwords.for_file(src), a.trust)
        except (core.PdfManagerError, OSError) as exc:
            if len(inputs) == 1:
                raise
            ok = False
            message = str(exc)
            print(f"error: {message if str(src) in message else f'{src}: {message}'}",
                  file=sys.stderr)
            continue
        ok = ok and certsign.all_valid(found)
        if a.json:
            records.append({"file": str(src), "signatures": found})
        else:
            print(("\n" if src != inputs[0] else "") + certsign.report(found, str(src)), end="")
    if a.json:
        print(json.dumps(records, indent=2, ensure_ascii=False))
    return 0 if ok else 1

# -- end of digital ID signatures --


# -- accessibility --

def run_tag(a, src, out, pw):
    alt = None
    if a.alt:
        try:
            alt = json.loads(Path(a.alt).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise core.PdfManagerError(f"can't read the descriptions in {a.alt} ({exc})") from None
        if not isinstance(alt, dict):
            raise core.PdfManagerError(f'{a.alt} should hold {{"page": ["description", ...]}}')
    result = accessibility.autotag(src, out, password=pw, lang=a.lang, title=a.title, alt=alt,
                                   force=a.force)
    return f"{out}  ({accessibility.tag_summary(result)})"


def cmd_check_access(a):
    inputs = _expand(a.inputs)
    passwords = _Passwords(a.password)
    ok, records = True, []
    for src in inputs:
        try:
            result = accessibility.check(src, password=passwords.for_file(src))
        except (core.PdfManagerError, OSError) as exc:
            if len(inputs) == 1:
                raise
            ok = False
            message = str(exc)
            print(f"error: {message if str(src) in message else f'{src}: {message}'}",
                  file=sys.stderr)
            continue
        ok = ok and result["passed"]
        if a.json:
            records.append(result)
        else:
            print(("\n" if src != inputs[0] else "") + accessibility.report(result, str(src)), end="")
    if a.json:
        print(json.dumps(records, indent=2, ensure_ascii=False))
    return 0 if ok else 1

# -- end of accessibility --


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


def run_form(a, src, out, pw):
    fields = [forms.parse_field(spec) for spec in a.field or []]
    if a.fields:
        data = json.loads(Path(a.fields).read_text(encoding="utf-8-sig"))
        fields += data if isinstance(data, list) else [
            {**f, "page": int(page)} for page, items in data.items() for f in items]
    if a.detect:
        found = formdetect.detect(src, pw)
        given = [(f.get("page", 1), f["rect"]) for f in fields]
        fields += [{**f, "page": page} for page, items in found.items() for f in items
                   if not any(p == page and formdetect.overlap(f["rect"], r) > 0.3
                              for p, r in given)]
        if not fields:
            raise core.PdfManagerError("no fields found: the form needs lines, boxes or tick "
                                       "boxes to fill in")
    if not fields:
        found = forms.existing_fields(src, pw)
        for page, items in found.items():
            for f in items:
                x0, y0, x1, y1 = f["rect"]
                print(f"{f['type']}:{page}:{x0:g},{y0:g},{x1:g},{y1:g}:{f['name']}")
        if not found:
            print("no form fields")
        return None
    names = forms.add_fields(src, out, fields, border=not a.no_border, font_size=a.font_size,
                             password=pw)
    return f"{out}  ({len(names)} field{'s' if len(names) != 1 else ''}: {', '.join(names)})"


def run_to_word(a, src, out, pw):
    notes = []
    result, engine = office.pdf_to_word(src, out, engine=a.engine, password=pw, notes=notes)
    how = {"word": "Microsoft Word", "basic": "built-in converter",
           "layout": "LibreOffice, exact layout"}[engine]
    return f"{result}  ({how})" + "".join(f"\n  note: {note}" for note in notes)


def run_to_excel(a, src, out, pw):
    from pdfmanager import tables

    if a.csv:
        made = tables.to_csv(src, out, password=pw, pages=a.pages)
        n = made["tables"]
        return f"{out}  ({n} table{'s' if n != 1 else ''}, a CSV file each)"
    made = tables.to_excel(src, out, password=pw, pages=a.pages,
                           mode="one-sheet" if a.one_sheet else "sheet-per-table")
    n = made["tables"]
    return f"{made['output']}  ({n} table{'s' if n != 1 else ''})"


# -- pptx and html --
def run_to_pptx(a, src, out, pw):
    from pdfmanager import slides

    made = slides.pdf_to_pptx(src, out, password=pw, pages=a.pages, mode=a.mode)
    n = made["slides"]
    return f"{made['output']}  ({n} slide{'s' if n != 1 else ''}, {a.mode})"


def run_to_html(a, src, out, pw):
    from pdfmanager import webpage

    made = webpage.pdf_to_html(src, out, password=pw, pages=a.pages, mode=a.mode)
    n = made["pages"]
    return f"{made['output']}  ({n} page{'s' if n != 1 else ''}, {a.mode})"
# -- end of pptx and html --


def cmd_from_office(a):
    failures = 0
    files = _expand(a.inputs)
    plan = []
    for src in files:
        if len(files) == 1 and a.output:
            out = Path(a.output)
        else:
            folder = Path(a.output) if a.output else Path(src).parent
            folder.mkdir(parents=True, exist_ok=True)
            out = folder / (Path(src).stem + ".pdf")
        plan.append((src, out))
    _check_outputs(plan)
    for src, out in plan:
        try:
            result, program = office.office_to_pdf(src, out)
            print(f"{src} -> {result}  ({program})")
        except (core.PdfManagerError, OSError) as exc:
            if len(files) == 1:
                raise
            failures += 1
            print(f"error: {src}: {exc}", file=sys.stderr)
    return 1 if failures else 0


# -- comments --
def run_comments(a, src, out, pw):
    if a.import_xfdf:
        added, skipped = comments.import_xfdf(src, a.import_xfdf, out, pw)
        if pw:
            _reencrypt(out, pw)
        return f"{out}  (added {added} comment{'s' if added != 1 else ''}" + \
            (f"; couldn't add {', '.join(skipped)})" if skipped else ")")
    if a.export:
        target = Path(a.output) if a.output and not _is_folder(a.output) else out.with_suffix(".xfdf")
        n = comments.export_xfdf(src, target, pw)
        return f"{target}  ({n} comment{'s' if n != 1 else ''})"
    found = comments.collect(src, pw)
    if a.csv:
        target = Path(a.output) if a.output and not _is_folder(a.output) else out.with_suffix(".csv")
        comments.to_csv(found, target)
        return f"{target}  ({comments.count(found)} comments)"
    if a.summary:
        comments.summary_pdf(found, out, Path(src).name)
        return f"{out}  ({comments.count(found)} comments)"
    for c in found:  # without options: list them
        print(f"p. {c['page']}  {c['kind']}" + (f"  {c['author']}" if c["author"] else "")
              + (f"  “{c['marked']}”" if c["marked"] else "") + (f": {c['text']}" if c["text"] else ""))
        for r in c["replies"]:
            print(f"    reply{('  ' + r['author']) if r['author'] else ''}: {r['text']}")
    if not found:
        print("no comments")
    return None
# -- end of comments --


# -- web pages --
def cmd_web(a):
    """Print web pages (addresses or saved .html files) to PDF."""
    failures = 0
    sources = []
    for item in a.inputs:
        sources += [item] if office._web_address(item) else [str(p) for p in _expand([item])]
    for src in sources:
        address = office._web_address(src)
        if a.output and len(sources) == 1:
            out = Path(a.output)
        else:
            folder = Path(a.output) if a.output else (Path.cwd() if address else Path(src).parent)
            folder.mkdir(parents=True, exist_ok=True)
            stem = (re.sub(r"[^\w.-]+", "-", re.sub(r"(?i)^https?://(www\.)?", "", address)).strip("-.")[:60]
                    if address else Path(src).stem) or "page"
            out = folder / f"{stem}.pdf"
        try:
            result, program = office.html_to_pdf(src, out)
            print(f"{src} -> {result}  ({program})")
        except (core.PdfManagerError, OSError) as exc:
            if len(sources) == 1:
                raise
            failures += 1
            print(f"error: {src}: {exc}", file=sys.stderr)
    return 1 if failures else 0
# -- end of web pages --


def run_redact(a, src, out, pw):
    patterns = list(a.pattern or []) + [redact.PRESETS[p] for p in a.find or []]
    if not (a.text or patterns or a.box):
        raise core.PdfManagerError("say what to redact: --text, --pattern, --find or --box")
    # -- true redaction --
    method = getattr(a, "method", None) or "remove"
    # -- end of true redaction --
    found = redact.redact(src, out, terms=a.text or [], patterns=patterns, boxes=a.box or [],
                          case_sensitive=a.case_sensitive, whole_words=a.whole_words,
                          dpi=a.dpi, password=pw, method=method)
    if not found:
        return f"{out}  (nothing found to redact)"
    # -- true redaction --
    extra = "".join(f"; page {page} became a picture ({why})"
                    for page, why in getattr(found, "pictures", {}).items())
    if getattr(found, "bookmarks", 0):
        extra += f"; blacked out the words in {found.bookmarks} bookmark(s)"
    # -- end of true redaction --
    return f"{out}  ({sum(found.values())} area(s) on page(s) {', '.join(map(str, found))}{extra})"


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


def run_media(a, src, out, pw):
    if a.no_images and a.no_attachments:
        raise core.PdfManagerError("--no-images and --no-attachments leave nothing to do")
    found = core.extract_media(src, out, images=not a.no_images,
                               attachments=not a.no_attachments, min_size=a.min_size, password=pw)
    return (f"{len(found['images'])} picture(s), {len(found['attachments'])} attachment(s) "
            f"in {out}")


def run_text(a, src, out, pw):
    text = core.extract_text(src, out, a.pages, pw)
    if out is None:
        print(text)
        return None
    return out


def run_split(a, src, out, pw):
    notes = []
    files = core.split(src, out, every=a.every, ranges=a.ranges, prefix=a.prefix, password=pw,
                       bookmarks=a.level if a.bookmarks else None,
                       max_size=int(a.max_size * 1_000_000) if a.max_size else None, notes=notes)
    for note in notes:
        print(f"note: {src}: {note}")
    for f in files if pw else ():
        _reencrypt(f, pw)
    return f"{len(files)} file(s) in {out}"


def run_to_images(a, src, out, pw):
    from PIL import Image

    if Image.registered_extensions().get("." + a.format.lower()) not in Image.SAVE:
        raise core.PdfManagerError(f"unknown image format {a.format!r}; use png, jpg, tiff, ...")
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
    if a.json and getattr(a, "json_records", None) is not None:
        a.json_records.append(data)  # a batch prints one JSON array at the end
    elif a.json:
        print(json.dumps(data, default=str, indent=2))
    else:
        _print_info(data)


# How each per-file command names its output when -o is not given:
# (suffix, kind) where kind is "pdf", "dir", "txt" or None (no output).
OUTPUTS = {
    "info": (None, None), "metadata": ("edited", "pdf"), "text": ("text", "txt"),
    "extract": ("extract", "pdf"), "delete": ("edited", "pdf"),
    "reorder": ("reordered", "pdf"), "reverse": ("reversed", "pdf"),
    "rotate": ("rotated", "pdf"), "resize": ("resized", "pdf"), "crop": ("cropped", "pdf"),
    "nup": ("nup", "pdf"), "insert-blank": ("blanks", "pdf"), "booklet": ("booklet", "pdf"),
    "compress": ("compressed", "pdf"), "color": ("color", "pdf"),
    "encrypt": ("encrypted", "pdf"), "decrypt": ("decrypted", "pdf"),
    "watermark": ("watermarked", "pdf"), "number": ("numbered", "pdf"), "ocr": ("ocr", "pdf"), "clean": ("clean", "pdf"),
    "flatten": ("flat", "pdf"), "redact": ("redacted", "pdf"), "headers": ("headers", "pdf"), "form": ("form", "pdf"), "bookmarks": ("bookmarks", "pdf"), "to-word": (None, "docx"), "sanitize": ("sanitized", "pdf"), "pdfa": ("pdfa", "pdf"),
    "to-excel": (None, "xlsx"),  # with --csv: a folder, <input>_tables
    "split": ("split", "dir"), "to-images": ("images", "dir"), "extract-media": ("media", "dir"),
}
OUTPUTS["sign-cert"] = ("signed", "pdf")  # digital ID signatures
OUTPUTS.update({"attach": ("attached", "pdf"), "labels": ("labelled", "pdf"),
                "repair": ("repaired", "pdf")})  # file tools
OUTPUTS["comments"] = ("comments", "pdf")
# -- pptx and html --
OUTPUTS.update({"to-pptx": (None, "pptx"), "to-html": (None, "html")})
# -- end of pptx and html --
OUTPUTS["tag"] = ("tagged", "pdf")  # -- accessibility --


def _is_batch(patterns, inputs):
    """Several inputs, or a wildcard: a script's `*.pdf -o folder/` keeps
    writing into the folder even on a day when only one file matches."""
    return len(inputs) > 1 or any(glob.has_magic(p) and not os.path.exists(p) for p in patterns)


def _is_folder(output):
    """-o names a folder when it ends with a separator or is one already."""
    return bool(output) and (output.endswith(("/", os.sep)) or Path(output).is_dir())


def _output_for(a, src, batch):
    suffix, kind = OUTPUTS[a.command]
    if a.command == "to-excel" and a.csv:
        suffix, kind = "tables", "dir"
    if kind is None:
        return None
    if a.command == "metadata" and a.output is None:
        return src  # metadata edits happen in place by default
    if a.command == "text" and a.output is None:
        return None  # print to the terminal
    src = Path(src)
    ext = ".pdf" if kind == "pdf" else f".{kind}"  # docx, xlsx, pptx, html, txt
    if kind == "dir":
        base = Path(a.output) if a.output else src.parent
        return base / f"{src.stem}_{suffix}" if (batch or not a.output) else base
    if not batch and not _is_folder(a.output):
        if a.output:
            return Path(a.output)
        return src.with_suffix(ext) if suffix is None else _default_output(src, suffix)
    if a.output:  # -o is a folder; keep the original file names
        folder = Path(a.output)
        if folder.exists() and not folder.is_dir():
            raise core.PdfManagerError("with several input files, -o must be a folder")
        folder.mkdir(parents=True, exist_ok=True)
        if folder.resolve() != src.parent.resolve():
            return folder / (src.stem + ext)
    return src.with_suffix(ext) if suffix is None else _default_output(src, suffix, ext)


def _check_outputs(plan):
    """Refuse before anything is written when two inputs would end up in the
    same file (report.pdf from two folders), or an output would replace
    another input (x.pdf -> x_edited.pdf, itself an input)."""
    inputs = {Path(src).resolve(): src for src, _ in plan}
    outputs = {}
    for src, out in plan:
        if out is None or Path(out) == Path(src):
            continue  # nothing written, or deliberately in place
        key = Path(out).resolve()
        if key in outputs:
            raise core.PdfManagerError(
                f"{outputs[key]} and {src} would both be written to {out}; "
                "use -o with another folder or rename one of them")
        outputs[key] = src
        if key in inputs and inputs[key] != src:
            raise core.PdfManagerError(
                f"{src} would be written to {out}, which is also an input; use -o FOLDER")


# Commands whose output should not inherit the input's password.
# (split re-encrypts its own output files.)
KEEP_UNENCRYPTED = {"decrypt", "encrypt", "info", "text", "to-images", "split", "to-word",
                    "to-excel",
                    "extract-media",
                    "pdfa"}  # PDF/A doesn't allow encryption
KEEP_UNENCRYPTED.add("comments")  # a summary, CSV or XFDF; an import protects itself
KEEP_UNENCRYPTED.add("sign-cert")  # stays protected inside the signed file; re-saving breaks it
KEEP_UNENCRYPTED |= {"to-pptx", "to-html"}  # -- pptx and html -- (not PDFs)


def _reencrypt(out, password):
    """Outputs made from a password-protected file get the same password,
    so editing a protected PDF never silently produces an unprotected one."""
    core.encrypt(out, out, password)


def run_per_file(a):
    inputs = _expand(a.inputs)
    batch = _is_batch(a.inputs, inputs)
    passwords = _Passwords(a.password, given_first=a.command == "decrypt")
    if a.command == "encrypt" and not a.user_password \
            and not (filetools.restricts(_encrypt_limits(a)) and a.owner_password):
        a.user_password = _ask_new_password()
    plan = [(src, _output_for(a, src, batch)) for src in inputs]
    _check_outputs(plan)
    if batch and a.command == "info" and a.json:
        a.json_records = []
    failures = 0
    for src, out in plan:
        try:
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
    if getattr(a, "json_records", None) is not None:
        print(json.dumps(a.json_records, default=str, indent=2))
    if batch:
        # text and info print their results; the count is status, not data.
        print(f"{len(inputs) - failures} of {len(inputs)} file(s) done",
              file=sys.stderr if a.command in ("text", "info") else sys.stdout)
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
                           limit=a.limit)
    for m in result["matches"]:
        more = f" (+{m['count'] - 1} more)" if m["count"] > 1 else ""
        print(f"{m['path']}, page {m['page']}: {m['before']}[{m['match']}]{m['after']}{more}")
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
        print("Removed the shortcuts and the Open with and Settings > Apps entries.")
        return 0
    for line in winsetup.install(a.app_dir, desktop=not a.no_desktop, open_with=not a.no_open_with):
        print(f"added {line}")
    return 0


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

def _positive(kind):
    def check(value):
        number = kind(value)
        if number <= 0:
            raise argparse.ArgumentTypeError("must be greater than 0")
        return number
    return check


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
    g.add_argument("-b", "--bookmarks", action="store_true",
                   help="one file per bookmark (chapter), named after it; pages before the "
                        "first bookmark get a file of their own")
    g.add_argument("--max-size", type=_positive(float), metavar="MB",
                   help="files of at most this many MB (1 MB = 1,000,000 bytes), as many "
                        "pages in each as fit")
    sp.add_argument("--level", type=int, choices=[1, 2], default=1,
                    help="with --bookmarks: 1 = top-level bookmarks only (default), "
                         "2 = also the ones under them")
    sp.add_argument("--prefix", help="output filename prefix (default: input name; with "
                                     "--bookmarks: none)")

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

    sp = per_file("crop", run_crop, "trim page margins, or the white margins (--auto)")
    sp.add_argument("-m", "--margins", nargs="+", metavar="MM", type=float,
                    help="margins in mm: ALL | VERTICAL HORIZONTAL | TOP RIGHT BOTTOM LEFT")
    sp.add_argument("--auto", action="store_true",
                    help="trim the white margins around the content of each page instead "
                         "(blank pages are left as they are)")
    sp.add_argument("--padding", type=float, default=3.5, metavar="MM",
                    help="with --auto: space to keep around the content (default: 3.5 mm)")
    sp.add_argument("--same", action="store_true",
                    help="with --auto: crop every page the same, enough for all their content")
    sp.add_argument("-p", "--pages", help=PAGES_HELP + " (default: all)")

    sp = per_file("nup", run_nup, "print several pages per sheet")
    sp.add_argument("-n", "--per-sheet", type=int, default=2, choices=[2, 4, 6, 8, 9])
    sp.add_argument("-s", "--size", default="a4", help="sheet size (default: a4)")

    sp = per_file("booklet", run_booklet,
                  "arrange pages to print a booklet: double-sided, flip on the short edge, "
                  "fold in the middle")
    sp.add_argument("-s", "--size",
                    help="sheet size, e.g. a4 or letter, used landscape with two pages side by "
                         "side (default: twice the page size)")

    sp = per_file("insert-blank", run_insert_blank,
                  "insert blank pages (e.g. a page for notes after every page)")
    sp.add_argument("--after", metavar="PAGES",
                    help="pages to put blank pages after, e.g. 3,7 · end · all (after every "
                         "page) · 0 (before the first page)")
    sp.add_argument("--at-start", action="store_true", help="also put blank pages before page 1")
    sp.add_argument("-c", "--count", type=_positive(int), default=1,
                    help="blank pages at each place (default: 1)")

    sp = per_file("compress", run_compress, "reduce file size")
    sp.add_argument("-l", "--level", default="medium",
                    choices=["low", "medium", "high", "large"],
                    help="low: lossless only; medium (default): images max 150 DPI, "
                         "JPEG quality 75; high/large: images max 96 DPI, quality 50")
    sp.add_argument("-q", "--image-quality", type=int, metavar="1-95",
                    help="override the level's JPEG quality")
    sp.add_argument("--max-dpi", type=int, help="override the level's image resolution")
    sp.add_argument("--no-sharp-scans", dest="sharp_scans", action="store_false",
                    help="compress scanned pages like other pictures, instead of keeping "
                         "their text at full sharpness over a reduced background")

    sp = per_file("color", run_color, "convert colours to grayscale, RGB or CMYK")
    sp.add_argument("--to", required=True, choices=imaging.COLOR_TARGETS)
    sp.add_argument("--icc", metavar="PROFILE.icc",
                    help="convert images through this ICC profile and embed it as the "
                         "output intent (e.g. your printer's CMYK profile)")

    sp = per_file("encrypt", run_encrypt, "password-protect PDFs (AES-256), and limit printing, "
                                          "copying or changes")
    sp.add_argument("--user-password", help="password to open (prompted if omitted; may be "
                                            "left out when limits and --owner-password are set)")
    sp.add_argument("--owner-password", help="permissions password: lifts the limits")
    sp.add_argument("--printing", choices=filetools.PRINTING, default="high",
                    help="allow printing (high), low-quality printing only, or none")
    sp.add_argument("--changes", choices=filetools.CHANGES, default="any",
                    help="what may be changed: any, comments (and forms, signing), forms "
                         "(filling in, signing), pages (insert, delete, turn) or none")
    sp.add_argument("--no-copy", action="store_true", help="don't allow copying text and pictures")

    # -- file tools --
    sp = per_file("attach", run_attach, "attach files to PDFs, remove them, or list them "
                                        "(without options)")
    sp.add_argument("--add", action="append", metavar="FILE", help="a file to attach (repeat)")
    sp.add_argument("--remove", action="append", metavar="NAME",
                    help="remove the attached file of this name (repeat)")
    sp.add_argument("--remove-all", action="store_true", help="remove all attached files")
    sp.add_argument("--description", help="a description for the files added")
    sp.add_argument("--list", action="store_true", help="list the attached files")

    sp = per_file("labels", run_labels, "set or remove page labels (i, ii, iii then 1, 2, 3); "
                                        "without options: list them")
    sp.add_argument("--set", metavar="SPEC",
                    help='e.g. "1: Cover; 2-4: i; 5-: 1; 30-: A-1" (the page each part starts on, '
                         "then its first label)")
    sp.add_argument("--clear", action="store_true", help="remove the page labels")

    sp = per_file("repair", run_repair, "mend damaged PDFs and optimize them for fast web view")
    sp.add_argument("--no-linearize", action="store_true",
                    help="don't optimize for fast web view (just repair)")
    # -- end of file tools --

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

    sp = per_file("ocr", run_ocr, "make scanned PDFs searchable")
    sp.add_argument("--engine", choices=ocr.ENGINES, default="auto",
                    help="auto (default: the built-in engine when installed, else Tesseract), "
                         "builtin or tesseract")
    sp.add_argument("--language", default="eng",
                    help="language(s) as Tesseract codes, e.g. eng+deu; the built-in engine reads "
                         "English, European languages, Chinese and Japanese as they are, and "
                         "fetches a model once for rus, ukr, kor, ara, fas, hin, tha, ell, tam, tel")
    sp.add_argument("--keep-orientation", action="store_true",
                    help="don't turn sideways or upside-down pages upright")
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

    # -- digital ID signatures (certsign.py) --
    sp = per_file("sign-cert", run_sign_cert,
                  "sign with a digital ID (a .pfx or .p12 certificate file), like Acrobat's "
                  "Sign with a digital ID")
    sp.add_argument("--pfx", required=True, metavar="FILE", help="the digital ID (.pfx or .p12)")
    sp.add_argument("--pfx-password", help=f"its password (better: let pdfm prompt, or set "
                                           f"{PFX_PASSWORD_ENV})")
    sp.add_argument("--page", help="page for a visible signature (a number, or last); without "
                                   "--page or --field the signature is invisible")
    sp.add_argument("--box", metavar="X0,Y0,X1,Y1",
                    help="where on --page, in points from the bottom-left corner (default: "
                         "--position and --size)")
    sp.add_argument("--position", choices=certsign.POSITIONS, default="bottom-right",
                    help="where on --page without --box (default: bottom-right)")
    sp.add_argument("--size", choices=list(certsign.SIZES), default="medium",
                    help="size of the signature without --box (default: medium)")
    sp.add_argument("--field", metavar="NAME", help="sign this empty signature field")
    sp.add_argument("--reason", help="e.g. 'I approve this document'")
    sp.add_argument("--location", help="where you sign, e.g. a city")
    sp.add_argument("--contact", help="how to reach you")
    sp.add_argument("--timestamp", metavar="URL",
                    help="RFC 3161 timestamp server, e.g. http://timestamp.digicert.com")

    sp = other("verify", cmd_verify,
               "check the digital signatures in PDFs (exit code 0 when every file has "
               "signatures and all are valid)")
    sp.add_argument("inputs", nargs="+", metavar="input", help="PDF(s) to check")
    sp.add_argument("--password", help=f"password for encrypted input (better: let pdfm "
                                       f"prompt, or set {PASSWORD_ENV})")
    sp.add_argument("--trust", action="append", metavar="CERT",
                    help="a certificate you trust (.cer/.crt/.pem), e.g. your company's root; "
                         "repeat for more")
    sp.add_argument("--json", action="store_true", help="print JSON")
    # -- end of digital ID signatures --

    sp = per_file("redact", run_redact,
                  "permanently black out text or areas (what's under them is removed)")
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
    sp.add_argument("--dpi", type=_positive(int), default=200,
                    help="resolution of pages turned into pictures")
    # -- true redaction --
    sp.add_argument("--method", choices=list(redact.METHODS), default="remove",
                    help="remove: take out what's under the boxes, the rest of the page stays "
                         "searchable text (default; pages that can't be edited safely become "
                         "pictures); image: turn every redacted page into a picture")
    # -- end of true redaction --

    sp = per_file("form", run_form,
                  "add fillable form fields (without --field: list the fields already there)")
    sp.add_argument("-f", "--field", action="append", metavar="TYPE:PAGE:X0,Y0,X1,Y1[:NAME]",
                    help=f"add a field ({', '.join(forms.TYPES)}), in points from the bottom-left "
                         "corner, e.g. text:1:72,700,300,720:Name (repeat for more); a radio "
                         "button's NAME is GROUP=VALUE, a dropdown's NAME=CHOICE|CHOICE|...")
    sp.add_argument("--detect", action="store_true",
                    help="find the fields of a printed form (its lines, boxes and tick boxes) "
                         "and add them")
    sp.add_argument("--fields", metavar="FILE",
                    help='fields from a JSON file: [{"type", "page", "rect": [x0, y0, x1, y1], '
                         '"name"}]')
    sp.add_argument("--font-size", type=float, default=0,
                    help="text size in the fields (default: fit single-line fields to the box)")
    sp.add_argument("--no-border", action="store_true", help="don't draw a border around fields")

    sp = per_file("pdfa", run_pdfa, "convert to PDF/A for long-term archiving")
    sp.add_argument("--engine", choices=archive.PDFA_ENGINES, default="auto",
                    help="auto (default): ZPDF's own converter, Ghostscript for what it can't "
                         "put right; builtin; ghostscript")
    sp.add_argument("--level", choices=archive.PDFA_LEVELS, default="2",
                    help="PDF/A-1b, -2b (default) or -3b")

    sp = other("check-pdfa", cmd_check_pdfa,
               "check whether PDFs meet PDF/A (exit code 0 when every file passes); uses "
               "veraPDF when it's installed")
    sp.add_argument("inputs", nargs="+", metavar="input", help="PDF(s) to check")
    sp.add_argument("--level", choices=pdfacheck.LEVELS,
                    help="the level to check against (default: the one the file says, else 2b)")
    sp.add_argument("--engine", choices=pdfacheck.ENGINES, default="auto",
                    help="veraPDF (when installed) or ZPDF's own checks (builtin)")
    sp.add_argument("--password", help=f"password for encrypted input (better: let pdfm "
                                       f"prompt, or set {PASSWORD_ENV})")
    sp.add_argument("--json", action="store_true", help="print JSON")

    # -- accessibility --
    sp = per_file("tag", run_tag,
                  "make PDFs accessible: tag their headings, paragraphs, lists, tables, figures "
                  "and links for screen readers (like Acrobat's Autotag); the pages look the same")
    sp.add_argument("--lang", help="the document's language, e.g. en, de, fr-CA (default: "
                                   "guessed from the text)")
    sp.add_argument("--title", help="the document's title (default: the one it has, else its "
                                    "first heading)")
    sp.add_argument("--alt", metavar="FILE",
                    help='descriptions of the figures, from a JSON file: {"3": ["the first '
                         'figure on page 3", "the second"]}')
    sp.add_argument("--force", action="store_true",
                    help="replace the tags of a file that is already tagged (otherwise they're "
                         "kept)")

    sp = other("check-access", cmd_check_access,
               "check PDFs for what keeps screen readers from reading them (PDF/UA basics, like "
               "Acrobat's Accessibility Checker; exit code 0 when no file has problems)")
    sp.add_argument("inputs", nargs="+", metavar="input", help="PDF(s) to check")
    sp.add_argument("--password", help=f"password for encrypted input (better: let pdfm "
                                       f"prompt, or set {PASSWORD_ENV})")
    sp.add_argument("--json", action="store_true", help="print JSON")
    # -- end of accessibility --

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

    sp = per_file("to-excel", run_to_excel,
                  "export the tables in PDFs to Excel workbooks (.xlsx) or CSV files",
                  out_help="output .xlsx (default: <input>.xlsx), or with --csv a folder (default: "
                           "<input>_tables); with several inputs, a folder")
    sp.add_argument("-p", "--pages", help=PAGES_HELP)
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--one-sheet", action="store_true",
                   help="put all tables on one sheet, one under the other (default: a sheet "
                        "per table)")
    g.add_argument("--csv", action="store_true",
                   help="write a CSV file per table instead of a workbook")

    sp = per_file("comments", run_comments,
                  "list the comments in PDFs, or make a summary (PDF), a spreadsheet (CSV) or an "
                  "XFDF file of them; or add comments from an XFDF file")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--summary", action="store_true", help="a PDF listing the comments page by page")
    g.add_argument("--csv", action="store_true", help="a spreadsheet of the comments")
    g.add_argument("--export", action="store_true", help="an XFDF file of the comments (Acrobat reads it)")
    g.add_argument("--import", dest="import_xfdf", metavar="XFDF", help="add the comments of an XFDF file")

    sp = other("web", cmd_web, "save web pages as PDFs, as the browser prints them (needs "
                               "Microsoft Edge, Chrome or Chromium)")
    sp.add_argument("inputs", nargs="+", metavar="address-or-file",
                    help="web addresses (https://…) or saved web pages (.html, .mht)")
    sp.add_argument("-o", "--output", help="output file; with several inputs, a folder")
    # -- pptx and html --
    sp = per_file("to-pptx", run_to_pptx,
                  "convert PDFs to PowerPoint presentations (.pptx), a slide per page",
                  out_help="output .pptx (default: <input>.pptx); with several inputs, a folder")
    sp.add_argument("-p", "--pages", help=PAGES_HELP)
    sp.add_argument("--pictures", dest="mode", action="store_const", const="pictures",
                    default="editable",
                    help="make each slide a picture of the page (default: editable text boxes "
                         "over the page's pictures and drawings)")

    sp = per_file("to-html", run_to_html, "convert PDFs to web pages (.html, pictures inside)",
                  out_help="output .html (default: <input>.html); with several inputs, a folder")
    sp.add_argument("-p", "--pages", help=PAGES_HELP)
    sp.add_argument("--exact", dest="mode", action="store_const", const="exact",
                    default="flowing",
                    help="keep each page as it looks, text over a picture of the page "
                         "(default: flowing text with headings, lists, tables and pictures)")
    # -- end of pptx and html --

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

    sp = per_file("extract-media", run_media,
                  "save the pictures and attached files inside PDFs",
                  out_help="output folder (default: <input>_media)")
    sp.add_argument("--no-images", action="store_true", help="only attached files")
    sp.add_argument("--no-attachments", action="store_true", help="only pictures")
    sp.add_argument("--min-size", type=int, default=16,
                    help="skip pictures smaller than this many pixels (default: 16)")

    sp = per_file("to-images", run_to_images, "render pages to image files",
                  out_help="output folder (default: <input>_images)")
    sp.add_argument("--dpi", type=_positive(int), default=150)
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

    sp = other("search", cmd_search, "find text in all the PDFs in a folder")
    sp.add_argument("directory")
    sp.add_argument("query")
    sp.add_argument("-R", "--recursive", action="store_true")
    sp.add_argument("--case-sensitive", action="store_true")
    sp.add_argument("--whole-words", action="store_true")
    sp.add_argument("--limit", type=int, default=500, help="most pages to list (default: 500)")

    sp = other("watch", cmd_watch,
               "watch an inbox folder and process new PDFs automatically")
    sp.add_argument("inbox")
    sp.add_argument("--dest", required=True, help="where processed files go")
    sp.add_argument("--by", choices=organize.GROUPINGS, help="sort into sub-folders")
    sp.add_argument("--rename", metavar="PATTERN", help="rename, e.g. '{date:%%Y-%%m-%%d} {title}'")
    sp.add_argument("--compress", choices=["low", "medium", "high", "large"])
    sp.add_argument("--no-sharp-scans", dest="sharp_scans", action="store_false",
                    help="with --compress: don't keep scanned text at full sharpness")
    sp.add_argument("--clean", action="store_true", help="remove blank pages")
    sp.add_argument("--rotate", action="store_true",
                    help="turn sideways/upside-down pages upright (needs Tesseract)")
    sp.add_argument("--ocr", action="store_true", help="make scans searchable")
    sp.add_argument("--engine", choices=ocr.ENGINES, default="auto", help="OCR engine (see pdfm ocr)")
    sp.add_argument("--recipe", metavar="NAME", help="run a saved recipe on each file")
    sp.add_argument("--language", default="eng", help="OCR language(s) (see pdfm ocr)")
    sp.add_argument("--skip-duplicates", action="store_true",
                    help="don't process files identical to one already in --dest")
    sp.add_argument("--delete-originals", action="store_true",
                    help="delete originals instead of moving them to <inbox>/originals")
    sp.add_argument("--interval", type=_positive(float), default=5, help="seconds between checks")
    sp.add_argument("--once", action="store_true", help="process what's there now and exit")

    sp = other("setup-windows", cmd_setup_windows,
               "Windows: add Start menu and desktop shortcuts, Open with for PDFs and an "
               "uninstall entry (used by the installer)")
    sp.add_argument("--remove", action="store_true", help="take them away again")
    sp.add_argument("--no-desktop", action="store_true", help="no desktop shortcut")
    sp.add_argument("--no-open-with", action="store_true", help='don\'t add to "Open with"')
    sp.add_argument("--app-dir", help="the installed app's folder (default: found from Python)")

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


def _leading_dash_ranges(argv):
    """argparse takes '-3,5' for an option name, but '-3' is the page range
    1 to 3; spell such a value out as '1-3,5' so `-p -3,5` works."""
    return ["1" + arg if re.match(r"-\d", arg) and not re.fullmatch(r"-\d*\.?\d+", arg) else arg
            for arg in argv]


def main(argv=None):
    # Windows encodes a redirected stdout in the ANSI code page, which can't
    # hold every character a PDF or a file name has; print those escaped
    # instead of stopping the command.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    argv = sys.argv[1:] if argv is None else list(argv)
    args = build_parser().parse_args(_leading_dash_ranges(argv))
    # pypdf logs a warning for every small defect it repairs; only show errors.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        return args.func(args) or 0
    except (core.PdfManagerError, PageRangeError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
