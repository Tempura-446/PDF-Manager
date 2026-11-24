# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Per-file CLI commands: bookmarks, forms, office, CAD/DXF, sheetsets, comments, privacy, redact, info."""

import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader

from pdfmanager import (
    comments, core, formdetect, forms, office, outline, redact,
)
from pdfmanager.cli.common import (
    _Passwords, _check_outputs, _default_output, _expand, _human_size, _is_batch, _is_folder, _reencrypt,
)


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
    if a.edit:
        if a.field or a.fields or a.detect:
            raise core.PdfManagerError("--edit changes the fields a form has; add fields in "
                                       "another run")
        changes = json.loads(Path(a.edit).read_text(encoding="utf-8-sig"))
        if not isinstance(changes, list):
            raise core.PdfManagerError(f"{a.edit} must hold a list of changes")
        done = forms.edit_fields(src, out, changes, password=pw)
        for note in done["notes"]:
            print(f"  note: {note}")
        return f"{out}  (changed {done['changed']}, deleted {done['deleted']})"
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


def run_to_dxf(a, src, out, pw):
    from pdfmanager import dxf

    pages = core.parse_pages(a.pages, core.open_pdf(src, pw).get_num_pages()) if a.pages else None
    made = dxf.pdf_to_dxf(src, out, pages=pages, scale=a.scale, password=pw)
    return f"{out}  ({made['entities']} entities, {made['units']})"


def cmd_from_dxf(a):
    from pdfmanager import dxf

    inputs = _expand(a.inputs)
    batch = _is_batch(a.inputs, inputs)
    for src in inputs:
        p = Path(src)
        if a.output and not batch and not _is_folder(a.output):
            out = Path(a.output)
        else:
            folder = Path(a.output) if a.output else p.parent
            folder.mkdir(parents=True, exist_ok=True)
            out = folder / f"{p.stem}.pdf"
        made = dxf.drawing_to_pdf(p, out, layout=a.layout, paper=a.paper, scale=a.scale, monochrome=a.monochrome)
        print(f"Wrote {out}  ({len(made['layers'])} layers, {made['paper']}, {made['scale']})")


def run_sheet_split(a, src, out, pw):
    from pdfmanager import sheetsets

    made = sheetsets.split_sheets(src, out, password=pw)
    return f"{out}  ({len(made)} sheet{'s' if len(made) != 1 else ''})"


def run_sheet_name(a, src, out, pw):
    from pdfmanager import sheetsets

    made = sheetsets.name_pages(src, out, password=pw)
    return f"{out}  ({len(made['sheets'])} pages labelled)"


def run_sheet_link(a, src, out, pw):
    from pdfmanager import sheetsets

    made = sheetsets.link_sheets(src, out, password=pw)
    return f"{out}  ({made['links']} cross-sheet link{'s' if made['links'] != 1 else ''})"


def cmd_sheet_compare(a):
    from pdfmanager import sheetsets

    res = sheetsets.compare_sets(a.old, a.new, out_pdf=a.output, password_v1=a.password, password_v2=a.password)
    print(f"Changed ({len(res['changed'])}): " + (", ".join(x["sheet"] for x in res["changed"]) or "none"))
    print(f"Added ({len(res['added'])}): " + (", ".join(x["sheet"] for x in res["added"]) or "none"))
    print(f"Removed ({len(res['removed'])}): " + (", ".join(x["sheet"] for x in res["removed"]) or "none"))
    print(f"Unchanged ({len(res['unchanged'])})")
    if res["output"]:
        print(f"Wrote overlay diff to {res['output']}")


def run_quantities(a, src, out, pw):
    from pdfmanager import quantities

    found = quantities.schedule(src, pw)
    if not found["items"]:
        raise core.PdfManagerError(f"{Path(src).name}: {quantities.said(found)}")
    if a.csv or str(out).lower().endswith(".csv"):
        quantities.write_csv(found, out)
    else:
        quantities.write_xlsx(found, out, Path(src).name)
    return f"{out}  ({quantities.said(found)})"


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


def run_to_md(a, src, out, pw):
    from pdfmanager import markdown_export

    made = markdown_export.pdf_to_markdown(
        src, out, password=pw, pages=a.pages,
        extract_images=a.images, page_breaks=a.page_breaks, front_matter=a.front_matter,
    )
    n = made["pages"]
    parts = [f"{n} page{'s' if n != 1 else ''}"]
    if made["tables"]:
        parts.append(f"{made['tables']} table{'s' if made['tables'] != 1 else ''}")
    if made["images"]:
        parts.append(f"{made['images']} image{'s' if made['images'] != 1 else ''}")
    if made["textless"]:
        parts.append("no text found — scanned pages need OCR first")
    return f"{made['output']}  ({', '.join(parts)})"


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
    found = comments.filter_comments(
        found,
        author=getattr(a, "author", None),
        kind=getattr(a, "kind", None),
        pages=getattr(a, "pages", None),
    )
    if getattr(a, "markdown", False):
        target = Path(a.output) if a.output and not _is_folder(a.output) else out.with_suffix(".md")
        comments.to_markdown(found, target, title=Path(src).name, group_by=getattr(a, "group_by", "page"))
        return f"{target}  ({comments.count(found)} comments)"
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
def cmd_privacy(a):
    from pdfmanager import privacy

    if a.remember:
        privacy.set_remembering(a.remember == "on")
    if a.clear_history:
        cleared = privacy.clear_history()
        print("cleared: " + ", ".join(cleared) if cleared else "there was nothing to clear")
    kept = privacy.history()
    if not privacy.remembering():
        print("History: off. Nothing about the files you open is kept.")
    else:
        print(f"History: on. Kept now: {kept['recent']} in the Recent list, {kept['facts']} file(s) read"
              + (", text from Search a folder." if kept["search"] else "."))
    print(f"Working files: {privacy.temp_root()} (deleted when ZPDF closes; leftovers are swept at the next start).")
    print("ZPDF has no telemetry. It goes online only for:")
    for o in privacy.ONLINE:
        print(f"  - {o['what']}: {o['when']}. Sends {o['sends']}.")
    return 0


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
    if getattr(found, "proof", None):
        print(f"  {redact.proof_words(found.proof)}")
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
