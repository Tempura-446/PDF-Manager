# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Per-file CLI commands: page ops, compression, security, OCR, archive, signing, accessibility, translate."""

import getpass
import json
from pypdf import PdfReader
import os
import sys
from pathlib import Path

from pdfmanager import (
    accessibility, archive, certcrypt, certsign, core, filetools, filing,
    imaging, ocr, pdfacheck, preflight, protection, risks, scan, translate,
)
from pdfmanager.cli.commands_convert import _print_info
from pdfmanager.cli.common import (
    PASSWORD_ENV, _Passwords, _ask_new_password, _default_output,
    _encrypt_limits, _expand, _human_size, _print_plan,
)


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
    result = core.booklet(
        src,
        out,
        a.size,
        pw,
        binding=getattr(a, "binding", "ltr"),
        signature_pages=getattr(a, "signature", 0),
        creep_mm=getattr(a, "creep", 0.0),
    )
    sheets = len(PdfReader(str(result)).pages) // 2
    return (f"{result}  ({sheets} sheet{'s' if sheets != 1 else ''}: print on both sides, "
            "flipping on the short edge, then fold in the middle)")


def run_compress(a, src, out, pw):
    before = Path(src).stat().st_size  # before: the output may replace the input
    notes = []
    result = core.compress(src, out, level=a.level, image_quality=a.image_quality,
                           max_dpi=a.max_dpi, password=pw, sharp_scans=a.sharp_scans,
                           notes=notes, target_mb=getattr(a, "target_mb", None))
    after = Path(result).stat().st_size
    pct = (1 - after / before) * 100 if before else 0
    return (f"{result}  ({_human_size(before)} -> {_human_size(after)}, {pct:.1f}% smaller"
            + "".join(f"; {note}" for note in notes) + ")")


def run_color(a, src, out, pw):
    return core.convert_color(src, out, a.to, a.icc, pw)




def run_encrypt_for(a, src, out, pw):
    names = certcrypt.encrypt_for(src, out, a.cert or [], password=pw, id_password=a.id_password)
    return f"{out}  (protected for {', '.join(names)}; only their Digital IDs open it)"


def run_open_with_id(a, src, out, pw):
    password = a.id_password
    if password is None and sys.stdin.isatty():
        password = getpass.getpass("Digital ID password: ")
    return f"{out}  ({certcrypt.open_with_id(src, out, a.id, password)})"


def run_upgrade_protection(a, src, out, pw):
    if a.check:
        seen = protection.info(src)
        print(f"{src}: " + (f"{seen['method']}" + (f" (weak: {seen['advice']})" if seen["weak"] else "")
                           if seen["protected"] else "not protected"))
        if seen["weak"]:
            a.risks_found = True  # exit code 2, for scripts
        return None
    return f"{out}  ({protection.upgrade(src, out, pw)})"


def run_encrypt(a, src, out, pw):
    if a.user_password and not getattr(a, "_strength_said", False):
        a._strength_said = True
        rated = protection.strength(a.user_password)
        if rated["score"] <= 1:
            print(f"note: that password is {rated['label'].lower()}. {rated['advice']}", file=sys.stderr)
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
    from pdfmanager import ocr

    report = {}
    res = core.ocr(src, out, language=a.language, force=a.force, deskew=a.deskew, password=pw,
                   engine=a.engine, rotate=not a.keep_orientation, quality=a.quality, report=report)
    summary = ocr.format_report(report)
    if summary:
        return f"{res}  ({' '.join(summary)})"
    return res


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


def run_flatten_layers(a, src, out, pw):
    from pdfmanager import layers

    if a.list:
        found = layers.info(src, pw)
        off = set(found["off"])
        for item in found["layers"]:
            print(f"{'hidden' if item['id'] in off else 'shown '}  {item['name']}")
        return None
    hidden = layers.hidden_names(src, a.hide, a.show, pw)
    result = layers.flatten_layers(src, out, hidden=hidden, password=pw)
    return f"{out}  (removed: {', '.join(result['removed']) or 'nothing, every layer shows'})"


def run_sanitize(a, src, out, pw):
    counts = archive.sanitize(src, out, metadata=not a.keep_metadata,
                              javascript=not a.keep_javascript,
                              attachments=not a.keep_attachments, comments=a.comments,
                              password=pw)
    found = ", ".join(f"{n} {k}" for k, n in counts.items() if n) or "nothing found"
    return f"{out}  (removed: {found})"


def run_make_safe(a, src, out, pw):
    if a.check:
        seen = risks.check(src, pw)
        if not seen["risks"]:
            print(f"{src}: nothing that could do harm")
        if seen["risks"]:
            a.risks_found = True  # the exit code says so (2), for scripts
        for risk in seen["risks"]:
            print(f"{src}: [{risk['severity']}] {risk['title']}: {risk['detail']}")
            for example in risk["examples"]:
                print(f"    {example}")
        return None
    removed = risks.make_safe(src, out, password=pw)
    if sum(removed.values()) and risks.signed(src, pw):
        print(f"  note: {risks.SIGNED_NOTE}")
    return f"{out}  (took out {risks.summary(removed)})"


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


def cmd_preflight(a):
    inputs = _expand(a.inputs)
    passwords = _Passwords(a.password)
    ok, records = True, []
    for src in inputs:
        try:
            result = preflight.check(
                src,
                password=passwords.for_file(src),
                min_dpi=a.min_dpi,
                error_dpi=a.error_dpi,
                min_bleed_mm=a.min_bleed,
                max_tac=getattr(a, "max_tac", 300.0),
                profile=getattr(a, "profile", "standard"),
            )
        except (core.PdfManagerError, OSError) as exc:
            if len(inputs) == 1:
                raise
            ok = False
            message = str(exc)
            print(f"error: {message if str(src) in message else f'{src}: {message}'}", file=sys.stderr)
            continue
        ok = ok and result["passed"]
        if a.json:
            records.append({"file": str(src), **result})
        else:
            print(("\n" if src != inputs[0] else "") + preflight.report(result, str(src)), end="")
    if a.json:
        print(json.dumps(records[0] if len(records) == 1 else records, indent=2, ensure_ascii=False))
    return 0 if ok else 1


def cmd_auto_file(a):
    if a.undo:
        res = filing.undo_filing(undo_path=a.undo_log)
        if a.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"Restored {res['restored']} file(s).")
        return 0
    if not a.inputs:
        raise core.PdfManagerError("provide at least one PDF file or folder (or use --undo)")
    pdf_files = []
    for raw in a.inputs:
        p = Path(raw)
        if p.is_dir():
            pdf_files.extend(sorted(p.glob("*.pdf")))
        else:
            pdf_files.append(p)
    if not pdf_files:
        raise core.PdfManagerError("no PDF files found")
    rules = filing.load_rules(a.rules) if a.rules else filing.STARTER_RULES
    plan = filing.plan_filing(pdf_files, rules, base_dir=a.out_dir, password=a.password)
    if a.apply:
        applied = filing.apply_filing(plan, undo_path=a.undo_log, password=a.password)
        if a.json:
            print(json.dumps({"plan": plan, **applied}, indent=2, ensure_ascii=False))
        else:
            for item in plan:
                if item["matched"]:
                    print(f"{item['src']} -> {item['dst']}  [{item['rule']}]")
            print(f"Applied: moved {applied['moved']} file(s).")
    else:
        if a.json:
            print(json.dumps({"dry_run": True, "plan": plan}, indent=2, ensure_ascii=False))
        else:
            for item in plan:
                if item["matched"]:
                    print(f"[DRY RUN] {item['src']} -> {item['dst']}  [{item['rule']}]")
                else:
                    print(f"[SKIP]    {item['src']}  ({item['reason']})")
    return 0


def cmd_translate(a):
    src = Path(a.input)
    if not src.is_file():
        raise core.PdfManagerError(f"file not found: {src}")
    ext = ".txt" if a.mode == "text" else ".pdf"
    out = Path(a.output) if a.output else src.with_name(f"{src.stem}_{a.to_lang}{ext}")
    res = translate.translate_pdf(
        src, out, a.from_lang, a.to_lang, mode=a.mode, password=a.password,
    )
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(f"Translated {res['pages']} page(s) ({res['mode']}) -> {res['output']}")
    return 0


def cmd_cbz_to_pdf(a):
    from pdfmanager import cbz

    src = Path(a.input)
    out = Path(a.output) if a.output else src.with_suffix(".pdf")
    res = cbz.cbz_to_pdf(src, out, rtl_viewer=bool(a.rtl))
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(f"Converted {res['pages']} CBZ page(s) -> {res['output']}")
    return 0


def cmd_pdf_to_cbz(a):
    from pdfmanager import cbz

    src = Path(a.input)
    out = Path(a.output) if a.output else src.with_suffix(".cbz")
    res = cbz.pdf_to_cbz(
        src,
        out,
        password=a.password,
        dpi=a.dpi,
        image_format=a.format,
        quality=a.quality,
    )
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(f"Exported {res['pages']} page(s) ({res['format']}) -> {res['output']}")
    return 0


def cmd_spine(a):
    res = core.spine_width_mm(
        a.pages,
        paper=a.paper,
        caliper_mm=a.caliper,
        cover_mm=a.cover_mm,
        trim_w_mm=a.trim_w,
        trim_h_mm=a.trim_h,
        bleed_mm=a.bleed_mm,
    )
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        msg = f"Spine width ({res['pages']} pages, {res['paper']}): {res['spine_mm']:.2f} mm ({res['spine_in']:.3f} in)"
        if "cover_width_mm" in res:
            msg += f" | Full wrap cover: {res['cover_width_mm']:.2f} × {res['cover_height_mm']:.2f} mm"
        print(msg)
    return 0

