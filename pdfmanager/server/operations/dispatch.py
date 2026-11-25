# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Single-file operation dispatcher and domain-specific tool helpers."""

import logging
import json
import re
import shutil
from pathlib import Path

from pdfmanager import filefacts, privacy
from pdfmanager.server.common import UserError, _bool, _num, _pages, _safe_name
from pdfmanager.server.operations._deps import (
    accessibility, archive, certcrypt, certsign, comments, core, filetools,
    formdetect, forms, layers, ocr, office, outline, pdfacheck, protection,
    recipes, redact, risks, scan,
)
from pdfmanager.server.operations.catalog import SUFFIX
from pdfmanager.server.operations.repair import _protect

SUFFIX["pdfa-check"] = "pdfa-check"
SUFFIX.update({"attach": "attached", "labels": "labelled", "repair": "repaired"})  # file tools
SUFFIX["comments"] = "comments"
SUFFIX["flatten-layers"] = "flat"
SUFFIX["to-excel"] = "tables"
SUFFIX["sec-profile"] = "protected"
SUFFIX.update({"tag": "tagged", "access-check": "accessibility"})  # -- accessibility --




# Errors whose message is shown to the user as it is.


# --------------------------------------------------------------------------
# Running operations
# --------------------------------------------------------------------------







def _run_file_op(op, src, out_dir, opts, password, extra_files, notes=None):
    """Run a single-input operation. Returns a list of output paths.
    Messages for the user (e.g. which pages were removed) go in ``notes``."""
    out = out_dir / f"{src.stem}_{SUFFIX.get(op, op)}.pdf"
    notes = [] if notes is None else notes
    if op == "extract":
        core.select_pages(src, out, opts["pages"], password)
    elif op == "delete":
        core.delete_pages(src, out, opts["pages"], password)
    elif op == "reorder":
        core.select_pages(src, out, opts["order"], password)
    elif op == "reverse":
        core.reverse(src, out, password)
    elif op == "rotate":
        core.rotate(src, out, _num(opts.get("degrees"), int, 90), _pages(opts), password)
    elif op == "resize":
        size = opts.get("size") or "a4"
        kwargs = {}
        if size == "scale":
            kwargs["scale"] = _num(opts.get("scale"), float, 1.0)
        else:
            kwargs["size"] = opts.get("custom") if size == "custom" else size
            if not kwargs["size"]:
                raise UserError("enter a custom size such as 210x297mm")
        core.resize(src, out, fit=opts.get("fit") or "fit", pages=_pages(opts),
                    auto_orient=_bool(opts.get("auto_orient", True)), password=password,
                    **kwargs)
    elif op == "crop":
        mm = [_num(opts.get(k), float, 0) * core.POINTS_PER_MM
              for k in ("left", "bottom", "right", "top")]
        core.crop(src, out, mm, _pages(opts), password)
    elif op == "nup":
        core.n_up(src, out, _num(opts.get("per_sheet"), int, 2), opts.get("size") or "a4",
                  password)
    elif op == "insert-blank":
        after, at_start = _pages(opts, "after"), _bool(opts.get("at_start"))
        if not after and not at_start:
            raise UserError("click the pages to put blank pages after, or type them")
        core.insert_blank_pages(src, out, after, _num(opts.get("count"), int, 1),
                                at_start=at_start, password=password)
    elif op == "booklet":
        core.booklet(
            src,
            out,
            opts.get("size") or None,
            password,
            binding=opts.get("binding") or "ltr",
            signature_pages=_num(opts.get("signature"), int, 0),
            creep_mm=_num(opts.get("creep_mm"), float, 0.0),
        )
        from pypdf import PdfReader
        sheets = len(PdfReader(str(out)).pages) // 2
        notes.append(f"{src.name}: {sheets} sheet{'s' if sheets != 1 else ''} of paper. Print on "
                     "both sides, flipping on the short edge, then fold in the middle.")
    elif op == "autocrop":
        report = core.trim_margins(src, out, padding=_num(opts.get("padding"), float, 3.5)
                                   * core.POINTS_PER_MM, same=_bool(opts.get("same")),
                                   pages=_pages(opts), password=password)
        if report["blank"]:
            many = len(report["blank"]) > 1
            notes.append(f"{src.name}: page{'s' if many else ''} {_pages_list(report['blank'])} "
                         f"{'are' if many else 'is'} blank and {'were' if many else 'was'} "
                         f"left as {'they are' if many else 'it is'}")
    elif op == "compress":
        raw_target = str(opts.get("target_mb") or "").strip()
        try:
            target_mb = float(raw_target) if raw_target else None
        except ValueError:
            raise UserError("target file size must be a positive number in MB") from None
        stats = {}
        core.compress(src, out, level=opts.get("level") or "medium", password=password,
                      sharp_scans=_bool(opts.get("sharp_scans", True)), notes=notes,
                      target_mb=target_mb, stats=stats)
        if target_mb is not None and stats.get("original_bytes"):
            notes.append(
                f"{src.name}: {core._fmt_bytes(stats['original_bytes'])} → "
                f"{core._fmt_bytes(stats['output_bytes'])} (-{stats['saved_pct']:.1f}%)"
            )
    elif op == "color":
        icc = extra_files.get("icc")
        core.convert_color(src, out, opts.get("to") or "gray", icc, password)
    elif op == "ocr":
        report = {}
        core.ocr(src, out, language=opts.get("language") or "eng",
                 force=_bool(opts.get("force")), deskew=_bool(opts.get("deskew")),
                 password=password, engine=opts.get("engine") or "auto",
                 rotate=_bool(opts.get("rotate", True)),
                 quality=opts.get("quality") or None, report=report)
        notes.extend(ocr.format_report(report))
    elif op == "clean":
        auto_rotate = _bool(opts.get("auto_rotate"))
        if auto_rotate and not (scan.find_tesseract() or ocr.builtin_available()):
            raise UserError("turning pages upright needs the built-in OCR engine or the "
                            "Tesseract program; install one (see the OCR tool) or untick that option")
        report = scan.clean(src, out, remove_blank=_bool(opts.get("remove_blank", True)),
                            sensitivity=opts.get("sensitivity") or "medium",
                            auto_rotate=auto_rotate, password=password)
        notes.append(_clean_note(src.name, report))
    elif op == "bookmarks":
        mode = opts.get("mode") or "edit"
        if mode == "detect":
            items = outline.detect_headings(src, password)
            notes.append(f"{src.name}: {len(items)} bookmark{'s' if len(items) != 1 else ''} "
                         "created from headings" if items else
                         f"{src.name}: no headings found (is it a scan? run OCR first)")
        elif mode == "clear":
            items = []
        else:
            try:
                items = json.loads(opts.get("outline") or "[]")
            except ValueError:
                raise UserError("the bookmark list is damaged; reload the file") from None
        outline.set_outline(src, out, items, password)
    elif op == "redact":
        terms, patterns, boxes = _redact_request(opts)
        if not (terms or patterns or boxes):
            raise UserError("enter words to black out, tick a kind of number, or draw boxes "
                            "on the pages")
        # -- true redaction --
        method = opts.get("method") or "remove"
        if method not in redact.METHODS:
            raise UserError("choose how to redact: remove what's under the boxes, or pictures")
        # -- end of true redaction --
        reason_code = (opts.get("reason_code") or "").strip() or None
        found = redact.redact(src, out, terms=terms, patterns=patterns, boxes=boxes,
                              case_sensitive=_bool(opts.get("case_sensitive")),
                              whole_words=_bool(opts.get("whole_words")), password=password,
                              method=method, reason_code=reason_code)
        notes.append(f"{src.name}: " + (
            f"blacked out {sum(found.values())} area{'s' if sum(found.values()) != 1 else ''} "
            f"on page{'s' if len(found) != 1 else ''} {_pages_list(list(found))}"
            + _redact_extra(found) if found
            else "nothing matched, so nothing was blacked out"))
        if getattr(found, "proof", None):
            notes.append(redact.proof_words(found.proof))
        if _bool(opts.get("export_log")):
            log_out = out_dir / f"{src.stem}_redaction_log.json"
            redact.export_redaction_log(found, log_out, fmt="json")
            return [out, log_out]
    elif op == "sec-profile":
        from pdfmanager import secprofiles

        prof_name = opts.get("profile") or "Confidential"
        res = secprofiles.apply_profile(
            src, out, prof_name,
            password=password,
            user_password=(opts.get("user_password") or "").strip() or None,
            owner_password=(opts.get("owner_password") or "").strip() or None,
        )
        notes.append(f"{src.name}: applied {res['profile']} ({', '.join(res['steps']) or 'no changes'})")
    elif op == "recipe":
        try:
            steps = json.loads(opts.get("steps") or "[]")
        except ValueError:
            raise UserError("the recipe is damaged; build it again") from None
        if not steps:
            raise UserError("add at least one step to the recipe")
        name = re.sub(r"[^\w-]+", "-", (opts.get("recipe_name") or "").strip()).strip("-").lower()
        if name:
            out = out_dir / f"{src.stem}_{name}.pdf"
        recipes.run(steps, src, out, password, notes, state=opts.setdefault("_state", {}))
    elif op == "form":
        try:
            raw = json.loads(opts.get("fields") or "{}")
            fields = [{**f, "page": int(page)} for page, found in raw.items() for f in found]
        except (ValueError, TypeError, AttributeError):
            raise UserError("the fields drawn on the pages are damaged; draw them again") from None
        try:
            edits = json.loads(opts.get("edits") or "[]")
            if not isinstance(edits, list) or not all(isinstance(e, dict) for e in edits):
                raise ValueError
        except ValueError:
            raise UserError("the changes to the form's fields are damaged; make them again") from None
        name, edited = src.name, None
        if edits:
            # The form's own fields first; new ones are then added to the result.
            edited = out.with_name(out.stem + ".fields-edited.pdf")
            done = forms.edit_fields(src, edited, edits, password=password)
            bits = [f"changed {done['changed']}" if done["changed"] else "",
                    f"deleted {done['deleted']}" if done["deleted"] else ""]
            notes.append(f"{name}: {' and '.join(b for b in bits if b) or 'changed none'} "
                         "of the form's own fields")
            notes.extend(done["notes"])
            src = edited
        try:
            _form_op(src, out, fields, opts, password, notes, name, bool(edits))
        finally:
            if edited is not None and edited != out:
                edited.unlink(missing_ok=True)
    elif op == "headers":
        # Bates numbers continue from file to file: the next number is kept
        # in the options between files.
        first = _num(opts.get("_bates_next"), int, None)
        if first is None:
            first = _num(opts.get("bates_start"), int, 1)
        opts["_bates_next"] = core.headers_footers(
            src, out, {spot: opts.get(spot) or "" for spot in core.HEADER_SPOTS},
            font_size=_num(opts.get("font_size"), float, 9),
            margin_mm=_num(opts.get("margin"), float, 10), pages=_pages(opts),
            bates_prefix=opts.get("bates_prefix") or "", bates_start=first,
            bates_digits=_num(opts.get("bates_digits"), int, 6), password=password)
    elif op == "flatten":
        with_fields, with_comments = _bool(opts.get("forms", True)), _bool(opts.get("comments", True))
        if not with_fields and not with_comments:
            raise UserError("choose form fields, comments or both")
        archive.flatten(src, out, forms=with_fields, comments=with_comments, password=password)
    elif op == "flatten-layers":
        hidden = layers.hidden_names(src, opts.get("hide"), opts.get("show"), password)
        result = layers.flatten_layers(src, out, hidden=hidden, password=password)
        if notes is not None and result["removed"]:
            notes.append(f"{src.name}: removed the hidden layer{'s' if len(result['removed']) > 1 else ''} "
                         + ", ".join(result["removed"]) + ".")
    elif op == "sanitize":
        counts = archive.sanitize(
            src, out, **{k: _bool(opts.get(k, d)) for k, d in
                         (("metadata", True), ("javascript", True), ("attachments", True),
                          ("comments", False))}, password=password)
        found = [f"{n} {label}" for key, label in (("metadata", "document details"),
                                                  ("javascript", "scripts/actions"),
                                                  ("attachments", "attachments"),
                                                  ("comments", "comments"))
                 if (n := counts[key])]
        notes.append(f"{src.name}: removed " + (", ".join(found) if found else "nothing (none found)"))
    elif op == "encrypt-cert":
        chosen = extra_files.get("certificates[]") or ([extra_files["certificates"]] if extra_files.get("certificates") else [])
        if not chosen:
            raise UserError("choose the certificates of the people who may open it (.cer files), "
                            "and yours to open it yourself")
        names = certcrypt.encrypt_for(src, out, chosen, password=password,
                                      id_password=opts.get("id_password") or None)
        notes.append(f"{src.name}: protected for {', '.join(names)}. Only their Digital IDs open it: "
                     "there's no password.")
    elif op == "decrypt-cert":
        if not extra_files.get("pfx"):
            raise UserError("choose your Digital ID: a .pfx or .p12 file")
        notes.append(f"{src.name}: " + certcrypt.open_with_id(src, out, extra_files["pfx"],
                                                             opts.get("pfx_password") or None))
    elif op == "upgrade-protection":
        notes.append(f"{src.name}: {protection.upgrade(src, out, password)}")
    elif op == "makesafe":
        removed = risks.make_safe(src, out, password=password)
        notes.append(f"{src.name}: took out {risks.summary(removed)}")
        if sum(removed.values()) and risks.signed(src, password):
            notes.append(risks.SIGNED_NOTE)
        left = [r for r in risks.check(out, password)["risks"]]
        notes.extend(f"Still there: {r['detail']}" for r in left)
    elif op == "sign-cert":
        result = _sign_cert_op(src, out, opts, password, extra_files)
        notes.append(f"{src.name}: signed by {result['signer']}"
                     + (f" on page {result['page']}" if result["visible"] else
                        " (invisible signature)")
                     + (", with a timestamp" if result["timestamp"] else ""))
    elif op == "pdfa-check":
        out = out.with_suffix(".txt")
        result = pdfacheck.check(src, level=opts.get("level") or None, password=password,
                                 engine=opts.get("engine") or "auto")
        out.write_text(pdfacheck.report(result, src.name), encoding="utf-8")
        notes.append(f"{src.name}: {pdfacheck.summary(result)}")
    # -- accessibility --
    elif op == "tag":
        result = accessibility.autotag(src, out, password=password,
                                       lang=(opts.get("lang") or "").strip() or None,
                                       title=(opts.get("title") or "").strip() or None,
                                       force=_bool(opts.get("force")))
        notes.append(f"{src.name}: {accessibility.tag_summary(result)}")
    elif op == "access-check":
        out = out.with_suffix(".txt")
        result = accessibility.check(src, password=password)
        out.write_text(accessibility.report(result, src.name), encoding="utf-8")
        notes.append(f"{src.name}: {accessibility.summary(result)}")
    # -- end of accessibility --
    elif op == "verify-sig":
        found = certsign.verify(src, password, [extra_files["trust"]]
                                if extra_files.get("trust") else None)
        fmt = (opts.get("report_format") or "txt").strip().lower()
        if fmt == "xml":
            out = out.with_suffix(".xml")
            certsign.export_validation_xml(found, out, src.name)
        elif fmt == "pdf":
            out = out.with_suffix(".pdf")
            certsign.export_validation_pdf(found, out, src.name)
        else:
            out = out.with_suffix(".txt")
            out.write_text(certsign.report(found, src.name), encoding="utf-8")
        notes.extend([f"{src.name}: no digital signatures"] if not found else
                     [f"{src.name}, {e['field']}: {e['summary']}" + (f" {e['lasting']}" if e.get("lasting") else "")
                      for e in found])
    elif op == "pdfa":
        level = opts.get("level") or "2"
        archive.to_pdfa(src, out, level=level, password=password, notes=notes)
        try:
            checked = pdfacheck.check(out, level=f"{level}b", engine="builtin")
            notes.append(f"{src.name}: checked: {pdfacheck.summary(checked)}")
        except Exception as _exc:  # the check is extra; the file is made either way
            logging.getLogger("zpdf").debug("dispatch._run_file_op: %s", _exc)
    elif op == "watermark":
        core.watermark(src, out, text=opts.get("text") or "CONFIDENTIAL",
                       opacity=_num(opts.get("opacity"), float, 0.15),
                       font_size=_num(opts.get("font_size"), float, 60),
                       angle=_num(opts.get("angle"), float, 45),
                       below=_bool(opts.get("below")), pages=_pages(opts), password=password)
    elif op == "number":
        core.number_pages(src, out, fmt=opts.get("format") or "{n} / {total}",
                          position=opts.get("position") or "bottom-center",
                          font_size=_num(opts.get("font_size"), float, 10),
                          start=_num(opts.get("start"), int, 1), password=password)
    elif op == "metadata":
        fields = {k: opts.get(k) or None for k in ("title", "author", "subject", "keywords")}
        core.set_metadata(src, out, password, **fields)
    elif op == "encrypt":
        perms = filetools.permissions(opts.get("printing") or "high", opts.get("changes") or "any",
                                      _bool(opts.get("copy", True)))
        limited = filetools.restricts(perms)
        if not opts.get("new_password") and not limited:
            raise UserError("enter a password to open it, or choose what to limit")
        core.encrypt(src, out, opts.get("new_password") or "", opts.get("owner_password") or None,
                     password=password, permissions=perms if limited else None)
        notes.append(f"{out.name}: " + ("opens with its password" if opts.get("new_password")
                                       else "anyone can open it")
                     + (f"; {filetools.describe(perms)}" if limited else ""))
    # -- comments --
    elif op == "comments":
        mode = opts.get("mode") or "summary"
        if mode == "import":
            if not extra_files.get("xfdf"):
                raise UserError("choose the comments file (.xfdf) to add")
            added, skipped = comments.import_xfdf(src, extra_files["xfdf"], out, password)
            notes.append(f"{src.name}: added {added} comment{'s' if added != 1 else ''}"
                         + (f" (couldn't add: {', '.join(skipped)})" if skipped else ""))
        else:
            if mode == "export":
                out = out.with_name(f"{src.stem}.xfdf")
                n = comments.export_xfdf(src, out, password)
            else:
                found = comments.collect(src, password)
                found = comments.filter_comments(
                    found,
                    author=(opts.get("author") or "").strip() or None,
                    kind=(opts.get("kind") or "").strip() or None,
                )
                n = comments.count(found)
                if mode == "markdown":
                    group_by = opts.get("group_by") or "page"
                    if group_by not in comments.GROUP_MODES:
                        raise UserError(f"unknown grouping {group_by!r}")
                    out = out.with_name(f"{src.stem}_comments.md")
                    comments.to_markdown(found, out, title=src.name, group_by=group_by)
                elif mode == "csv":
                    out = out.with_name(f"{src.stem}_comments.csv")
                    comments.to_csv(found, out)
                else:
                    comments.summary_pdf(found, out, src.name)
            notes.append(f"{src.name}: {n} comment{'s' if n != 1 else ''}")
    elif op == "fill-many":
        if not extra_files.get("data"):
            raise UserError("choose a spreadsheet or data file (.csv, .xlsx, .json)")
        made = forms.fill_many(src, extra_files["data"], out_dir / f"{src.stem}_filled",
                               name_column=(opts.get("name_column") or "").strip() or None,
                               password=password)
        notes.append(f"{src.name}: filled {len(made)} cop{'y' if len(made) == 1 else 'ies'}")
        return made
    # -- end of comments --
    # -- file tools --
    elif op == "attach":
        added, removed = filetools.attach(src, out, extra_files.get("attach[]") or [],
                                          remove=[n for n in (opts.get("remove") or "").split(",")],
                                          remove_all=_bool(opts.get("remove_all")),
                                          password=password)
        now = [a["name"] for a in filetools.attachments(out)]
        notes.append(f"{src.name}: " + "; ".join(
            bit for bit in (f"attached {', '.join(added)}" if added else "",
                            f"removed {', '.join(removed)}" if removed else "") if bit)
            + (f" (now attached: {', '.join(now)})" if now else " (no attached files now)"))
    elif op == "labels":
        if _bool(opts.get("clear")):
            labels = []
        elif (opts.get("custom") or "").strip():
            labels = filetools.parse_labels(opts["custom"])
        else:
            roman = _num(opts.get("roman"), int, 0)
            rest = (opts.get("style") or "1", opts.get("prefix") or "", _num(opts.get("start"), int, 1))
            labels = ([(1, "i", "", 1)] if roman else []) + [(roman + 1, *rest)]
            total = len(core.open_pdf(src, password).pages)
            labels = [lab for lab in labels if lab[0] <= total]
        filetools.set_page_labels(src, out, labels, password)
        shown = filetools.page_labels(out)
        notes.append(f"{src.name}: " + ("page labels removed" if not shown else
                                        "pages labelled " + ", ".join(shown[:6])
                                        + ("…" if len(shown) > 6 else "")))
    elif op == "repair":
        report = filetools.repair(src, out, linearize=_bool(opts.get("linearize", True)),
                                  password=password)
        fixed = len(report["warnings"])
        notes.append(f"{src.name}: " + (f"mended {fixed} problem{'s' if fixed != 1 else ''}"
                                        if fixed else "no damage found")
                     + ("; optimized for fast web view" if report["linearized"] else ""))
    # -- end of file tools --
    elif op == "decrypt":
        if not password:
            if not core.open_pdf(src).is_encrypted:
                raise UserError(f"{src.name} has no password or restrictions to remove")
            raise UserError("enter the PDF's current password")
        core.decrypt(src, out, password)
    elif op == "split":
        by = opts.get("by", "ranges")
        folder = out_dir / f"{src.stem}_split"
        if by == "bookmarks":
            return core.split(src, folder, bookmarks=_num(opts.get("level"), int, 1),
                              password=password)
        if by == "size":
            mb = _num(opts.get("max_mb"), float, 10)
            if mb <= 0:
                raise UserError("the largest file size must be more than 0 MB")
            found = []
            files = core.split(src, folder, max_size=int(mb * 1_000_000), password=password,
                               notes=found)
            notes.extend(f"{src.name}: {note}" for note in found)
            return files
        ranges = [r for r in re.split(r"[;\s]+", opts.get("ranges") or "") if r] \
            if by == "ranges" else []
        return core.split(src, folder, every=None if ranges else _num(opts.get("every"), int, 1),
                          ranges=ranges or None, password=password)
    elif op == "media":
        images, attachments = _bool(opts.get("images", True)), _bool(opts.get("attachments", True))
        if not images and not attachments:
            raise UserError("choose pictures, attached files or both")
        found = core.extract_media(src, out_dir / f"{src.stem}_media", images, attachments,
                                   _num(opts.get("min_size"), int, 16), password)
        notes.append(f"{src.name}: {len(found['images'])} picture"
                     f"{'s' if len(found['images']) != 1 else ''}, {len(found['attachments'])} "
                     f"attached file{'s' if len(found['attachments']) != 1 else ''}")
        can_run = [Path(p).name for p in found["attachments"] if risks.runnable(Path(p).name)]
        if can_run:
            notes.append(f"Careful: {', '.join(can_run[:5])}{'…' if len(can_run) > 5 else ''} can run "
                         "on your computer. Open "
                         f"{'it' if len(can_run) == 1 else 'them'} only if you trust who sent this PDF.")
        return found["images"] + found["attachments"]
    elif op == "to-images":
        return core.pdf_to_images(src, out_dir / f"{src.stem}_images",
                                  _num(opts.get("dpi"), int, 150), opts.get("format") or "png",
                                  _pages(opts), password)
    elif op == "pdf-to-cbz":
        from pdfmanager import cbz

        out = out_dir / f"{src.stem}.cbz"
        manga_rtl = _bool(opts.get("manga_rtl"))
        res = cbz.pdf_to_cbz(
            src,
            out,
            password=password,
            dpi=_num(opts.get("dpi"), float, 150.0),
            image_format=opts.get("format") or "webp",
            comic_info={
                "Title": src.stem,
                "Manga": "YesAndRightToLeft" if manga_rtl else "No",
            },
        )
        notes.append(f"{src.name}: exported {res['pages']} page(s) to CBZ ({res['format']}).")
    elif op == "text":
        out = out.with_suffix(".txt")
        if not core.extract_text(src, out, None, password).strip():
            # Not an empty text file: said, and (with other files) left out.
            out.unlink()
            notes.append(f"{src.name} has no text to copy: its pages are pictures or drawings. "
                         "If they are scans, Make searchable (OCR) reads the text first")
            return []
    elif op == "to-word":
        out = out_dir / f"{src.stem}.docx"
        said = len(notes)
        _, engine = office.pdf_to_word(src, out, opts.get("engine") or "auto", password, notes=notes)
        if engine == "basic" and (opts.get("engine") or "auto") == "auto" and len(notes) == said:
            notes.append("Converted with the built-in converter. For a more editable "
                         "result, install Microsoft Word.")
    elif op == "to-excel":
        return _to_excel(src, out_dir, opts, password, notes)
    elif op == "quantities":
        from pdfmanager import quantities

        found = quantities.schedule(src, password)
        notes.append(f"{src.name}: {quantities.said(found)}")
        if not found["items"]:
            return []
        if (opts.get("format") or "xlsx") == "csv":
            return [quantities.write_csv(found, out_dir / f"{src.stem} quantities.csv")]
        return [quantities.write_xlsx(found, out_dir / f"{src.stem} quantities.xlsx", src.name)]
    # -- pptx and html --
    elif op == "to-pptx":
        from pdfmanager import slides

        mode = opts.get("mode") or "editable"
        if mode not in slides.MODES:
            raise UserError(f"unknown kind of slides {mode!r}")
        out = out_dir / f"{src.stem}.pptx"
        made = slides.pdf_to_pptx(src, out, password, _pages(opts), mode=mode)
        notes.append(f"{src.name}: {made['slides']} slide{'s' if made['slides'] != 1 else ''}")
    elif op == "to-html":
        from pdfmanager import webpage

        mode = opts.get("mode") or "flowing"
        if mode not in webpage.MODES:
            raise UserError(f"unknown layout {mode!r}")
        out = out_dir / f"{src.stem}.html"
        webpage.pdf_to_html(src, out, password, _pages(opts), mode=mode)
    elif op == "to-md":
        from pdfmanager import markdown_export

        out = out_dir / f"{src.stem}.md"
        made = markdown_export.pdf_to_markdown(
            src, out, password=password, pages=_pages(opts),
            extract_images=bool(opts.get("images")),
            page_breaks=bool(opts.get("page_breaks")),
            front_matter=bool(opts.get("front_matter")),
        )
        summary = f"{made['pages']} page{'s' if made['pages'] != 1 else ''}"
        if made["tables"]:
            summary += f", {made['tables']} table{'s' if made['tables'] != 1 else ''}"
        if made["images"]:
            summary += f", {made['images']} image{'s' if made['images'] != 1 else ''}"
        if made["textless"]:
            summary += " (no text found — run OCR first)"
        notes.append(f"{src.name}: {summary}")
    elif op == "translate":
        from pdfmanager import translate

        source_lang = opts.get("source_lang") or "auto"
        target_lang = opts.get("target_lang") or "es"
        mode = opts.get("mode") or "side-by-side"
        if mode not in translate.MODES:
            raise UserError(f"unknown translation layout {mode!r}")
        ext = ".txt" if mode == "text" else ".pdf"
        safe_lang = _safe_name(re.sub(r"[^A-Za-z0-9_-]+", "-", str(target_lang)).strip("-") or "out")
        out = out_dir / f"{src.stem}_{safe_lang}{ext}"
        pages_spec = str(opts.get("pages") or "").strip() or None
        auto_ocr = _bool(opts.get("auto_ocr", False))
        manga_ai_opt = _bool(opts.get("manga_ai", False))
        made = translate.translate_pdf(
            src, out, source_lang, target_lang,
            mode=mode,
            password=password,
            pages=pages_spec,
            auto_ocr=auto_ocr,
            manga_ai=manga_ai_opt,
        )
        eff_src = made.get("src_lang") or source_lang
        src_note = f"auto→{eff_src}" if source_lang == "auto" else eff_src
        empty = int(made.get("empty_pages") or 0)
        extra = []
        if made.get("ocr_applied"):
            extra.append("OCR applied")
        if empty >= int(made.get("pages") or 0) and empty > 0:
            extra.append(
                "no selectable text found — enable Auto-OCR or run Make searchable (OCR) first"
            )
        elif empty > 0:
            extra.append(
                f"{empty} image-only page{'s' if empty != 1 else ''} kept as-is"
            )
        extra_note = ("; " + "; ".join(extra)) if extra else ""
        notes.append(
            f"{src.name}: translated {made['pages']} page{'s' if made['pages'] != 1 else ''} "
            f"({src_note} → {target_lang}, {made['mode']}{extra_note})"
        )
    # -- end of pptx and html --
    elif op == "from-office":
        out = out_dir / f"{src.stem}.pdf"
        _, program = office.office_to_pdf(src, out)
        notes.append(f"{src.name}: converted with {program}")
    elif op == "web":
        out = out_dir / f"{src.stem}.pdf"
        _, program = office.html_to_pdf(src, out)
        notes.append(f"{src.name}: printed with {program}")
    elif op == "to-dxf":
        from pdfmanager import dxf

        out = out_dir / f"{src.stem}.dxf"
        pages = _pages(opts)
        if pages:
            pages = core.parse_pages(pages, core.open_pdf(src, password).get_num_pages())
        made = dxf.pdf_to_dxf(src, out, pages=pages, scale=(opts.get("scale") or "").strip() or None, password=password)
        notes.append(f"{src.name}: {made['entities']} entities ({made['units']})")
    elif op == "from-drawing":
        from pdfmanager import dxf

        out = out_dir / f"{src.stem}.pdf"
        made = dxf.drawing_to_pdf(
            src, out,
            layout=(opts.get("layout") or "Model").strip() or "Model",
            paper=(opts.get("paper") or "A3").strip() or "A3",
            scale=(opts.get("scale") or "fit").strip() or "fit",
            monochrome=_bool(opts.get("monochrome")),
        )
        notes.append(f"{src.name}: {len(made['layers'])} layers, {made['paper']}, {made['scale']}")
    elif op == "sheet-sets":
        from pdfmanager import sheetsets

        action = (opts.get("action") or "split").strip()
        if action == "split":
            made = sheetsets.split_sheets(src, out_dir, password=password)
            notes.append(f"{src.name}: split into {len(made)} sheet{'s' if len(made) != 1 else ''}")
            return [m["output"] for m in made]
        if action == "name":
            out = out_dir / f"{src.stem}_named.pdf"
            made = sheetsets.name_pages(src, out, password=password)
            notes.append(f"{src.name}: labelled {len(made['sheets'])} pages")
        elif action == "link":
            out = out_dir / f"{src.stem}_linked.pdf"
            made = sheetsets.link_sheets(src, out, password=password)
            notes.append(f"{src.name}: added {made['links']} cross-sheet link{'s' if made['links'] != 1 else ''}")
        else:
            raise UserError(f"unknown sheet-set action {action!r}")
    else:
        raise UserError(f"unknown operation {op!r}")
    return [out]


def _form_op(src, out, fields, opts, password, notes, name, edited):
    """Add the drawn (and found) fields to ``src``. ``edited``: ``src`` is
    the file with its own fields already changed, which is a result even
    with nothing to add."""
    if _bool(opts.get("detect")):
        found = formdetect.detect(src, password)
        drawn = [(f["page"], f["rect"]) for f in fields]
        fields = fields + [{**f, "page": page} for page, items in found.items() for f in items
                           if not any(p == page and formdetect.overlap(f["rect"], r) > 0.3
                                      for p, r in drawn)]
    if not fields:
        if edited:
            shutil.copyfile(src, out)
            return
        raise UserError("no fields: use Find fields, or drag where people should type, "
                        "tick or sign" if not _bool(opts.get("detect")) else
                        "no fields were found in this file: it needs lines, boxes or tick "
                        "boxes to fill in (draw the fields instead)")
    if _bool(opts.get("required")):
        fields = [{**f, "required": f.get("type") in ("text", "multiline")} for f in fields]
    names = forms.add_fields(src, out, fields, border=_bool(opts.get("border")),
                             font_size=_num(opts.get("font_size"), float, 0),
                             password=password)
    notes.append(f"{name}: added {len(names)} field{'s' if len(names) != 1 else ''} "
                 f"({', '.join(names[:8])}{'…' if len(names) > 8 else ''})")


def _sign_cert_op(src, out, opts, password, extra_files):
    """The Sign with a digital ID tool on one PDF."""
    pfx = extra_files.get("pfx")
    if not pfx:
        raise UserError("choose your digital ID: a .pfx or .p12 file")
    page = box = None
    if (opts.get("appearance") or "visible") == "visible":
        page, box = certsign.place(src, opts.get("page") or "last",
                                   opts.get("position") or "bottom-right",
                                   opts.get("size") or "medium", password)
    return certsign.sign(src, out, pfx, opts.get("pfx_password") or None, page=page, box=box,
                         reason=(opts.get("reason") or "").strip() or None,
                         location=(opts.get("location") or "").strip() or None,
                         timestamp_url=(opts.get("timestamp_url") or "").strip() or None,
                         password=password,
                         pades_level=(opts.get("pades_level") or "B-B").strip(),
                         certify=(opts.get("certify") or "").strip() or None,
                         lock_fields=(opts.get("lock_fields") or "").strip() or None)


def _to_excel(src, out_dir, opts, password, notes):
    """PDF → Excel: a workbook, or a CSV file per table (in a folder, so the
    download is a ZIP). A file without tables gets a note instead of
    stopping the other files of the batch."""
    from pdfmanager import tables

    layout = opts.get("layout") or "sheet-per-table"
    if layout not in tables.LAYOUTS:
        raise UserError(f"unknown layout {layout!r}")
    auto_ocr = _bool(opts.get("auto_ocr", True))
    try:
        if (opts.get("format") or "xlsx") == "csv":
            made = tables.to_csv(src, out_dir / f"{src.stem}_tables", password, _pages(opts),
                                 auto_ocr=auto_ocr)
            outputs = made["files"]
        else:
            made = tables.to_excel(src, out_dir / f"{src.stem}.xlsx", password, _pages(opts),
                                   mode=layout, auto_ocr=auto_ocr)
            outputs = [made["output"]]
    except tables.NoTablesFound as exc:
        notes.append(f"{src.name}: {exc}")
        return []
    notes.append(f"{src.name}: {made['tables']} table{'s' if made['tables'] != 1 else ''}")
    return outputs


def _redact_request(opts):
    """(terms, patterns, boxes) from the Redact tool's options. Boxes come
    from the page editor as {"1": [[x0, y0, x1, y1], ...]} (1-based pages)."""
    terms = [t.strip() for t in re.split(r"[,;\n]", opts.get("terms") or "") if t.strip()]
    patterns = [redact.PRESETS[k] for k in redact.PRESETS if _bool(opts.get(k))]
    custom_rx = (opts.get("regex") or "").strip()
    if custom_rx:
        patterns.append(custom_rx)
    try:
        raw = json.loads(opts.get("boxes") or "{}")
        boxes = {int(page) - 1: [tuple(float(v) for v in box) for box in found]
                 for page, found in raw.items() if found}
    except (ValueError, TypeError, AttributeError):
        raise UserError("the boxes drawn on the pages are damaged; draw them again") from None
    return terms, patterns, boxes


# -- true redaction --
def _redact_extra(report):
    """What the notes add to "blacked out N areas": pages that became
    pictures although the content was to be removed, and bookmarks."""
    pictures = getattr(report, "pictures", {})
    bits = []
    if pictures:
        reasons = sorted(set(pictures.values()))
        bits.append(f"page{'s' if len(pictures) != 1 else ''} {_pages_list(list(pictures))} "
                    f"became {'a picture' if len(pictures) == 1 else 'pictures'} "
                    f"({'; '.join(reasons)})")
    if getattr(report, "bookmarks", 0):
        n = report.bookmarks
        bits.append(f"blacked out the words in {n} bookmark{'s' if n != 1 else ''}")
    return "".join("; " + b for b in bits)


def _redact_boxes(raw):
    """{0-based page: [(x0, y0, x1, y1)]} from {"1": [[x0, y0, x1, y1]]}."""
    try:
        raw = json.loads(raw or "{}")
        boxes = {int(page) - 1: [tuple(float(v) for v in box) for box in found]
                 for page, found in raw.items() if found}
    except (ValueError, TypeError, AttributeError):
        raise UserError("the boxes drawn on the pages are damaged; draw them again") from None
    if any(len(box) != 4 for found in boxes.values() for box in found):
        raise UserError("the boxes drawn on the pages are damaged; draw them again")
    return boxes
# -- end of true redaction --


def _pages_list(numbers):
    return ", ".join(map(str, numbers)) if len(numbers) <= 12 else f"{len(numbers)} pages"


def _clean_note(name, report):
    removed, rotated = report["removed"], report["rotated"]
    bits = []
    if removed:
        bits.append(f"removed blank page{'s' if len(removed) > 1 else ''} {_pages_list(removed)}")
    if rotated:
        bits.append(f"turned page{'s' if len(rotated) > 1 else ''} {_pages_list(list(rotated))}")
    return f"{name}: " + ("; ".join(bits) if bits else "nothing needed changing")

