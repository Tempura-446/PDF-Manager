# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Shared CLI helpers, password prompting, output naming, and per-file batch runner."""

import getpass
import glob
import json
import os
import sys
from pathlib import Path

from pypdf import PdfReader

from pdfmanager import certsign, core, filetools, protection
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




# How each per-file command names its output when -o is not given:
# (suffix, kind) where kind is "pdf", "dir", "txt" or None (no output).
OUTPUTS = {
    "info": (None, None), "metadata": ("edited", "pdf"), "text": ("text", "txt"),
    "extract": ("extract", "pdf"), "delete": ("edited", "pdf"),
    "reorder": ("reordered", "pdf"), "reverse": ("reversed", "pdf"),
    "rotate": ("rotated", "pdf"), "resize": ("resized", "pdf"), "crop": ("cropped", "pdf"),
    "nup": ("nup", "pdf"), "insert-blank": ("blanks", "pdf"), "booklet": ("booklet", "pdf"),
    "compress": ("compressed", "pdf"), "color": ("color", "pdf"),
    "encrypt": ("encrypted", "pdf"), "decrypt": ("decrypted", "pdf"), "upgrade-protection": ("protected", "pdf"), "encrypt-for": ("for-people", "pdf"), "open-with-id": ("opened", "pdf"),
    "watermark": ("watermarked", "pdf"), "number": ("numbered", "pdf"), "ocr": ("ocr", "pdf"), "clean": ("clean", "pdf"),
    "flatten": ("flat", "pdf"), "redact": ("redacted", "pdf"), "headers": ("headers", "pdf"), "form": ("form", "pdf"), "bookmarks": ("bookmarks", "pdf"), "to-word": (None, "docx"), "sanitize": ("sanitized", "pdf"), "make-safe": ("safe", "pdf"), "pdfa": ("pdfa", "pdf"),
    "to-excel": (None, "xlsx"),  # with --csv: a folder, <input>_tables
    "quantities": ("quantities", "xlsx"),  # with --csv: <input>_quantities.csv
    "split": ("split", "dir"), "to-images": ("images", "dir"), "extract-media": ("media", "dir"),
}
OUTPUTS["sign-cert"] = ("signed", "pdf")  # digital ID signatures
OUTPUTS.update({"attach": ("attached", "pdf"), "labels": ("labelled", "pdf"),
                "repair": ("repaired", "pdf")})  # file tools
OUTPUTS["comments"] = ("comments", "pdf")
OUTPUTS["flatten-layers"] = ("flat", "pdf")
# -- pptx and html --
OUTPUTS.update({"to-pptx": (None, "pptx"), "to-html": (None, "html"), "to-md": (None, "md")})
# -- end of pptx and html --
OUTPUTS["tag"] = ("tagged", "pdf")  # -- accessibility --
OUTPUTS["to-dxf"] = (None, "dxf")  # -- drawing tools --
OUTPUTS.update({"sheet-split": ("sheets", "dir"), "sheet-name": ("named", "pdf"), "sheet-link": ("linked", "pdf")})


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
    if a.command == "quantities" and a.csv:
        kind = "csv"
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
        return src.with_suffix(ext) if suffix is None else _default_output(src, suffix, ext)
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
                    "to-excel", "quantities",
                    "extract-media",
                    "pdfa"}  # PDF/A doesn't allow encryption
KEEP_UNENCRYPTED.update({"encrypt-for", "open-with-id"})  # protected for people, or opened for good
KEEP_UNENCRYPTED.add("upgrade-protection")  # protects its own output, with both passwords
KEEP_UNENCRYPTED.add("comments")  # a summary, CSV or XFDF; an import protects itself
KEEP_UNENCRYPTED.add("sign-cert")  # stays protected inside the signed file; re-saving breaks it
KEEP_UNENCRYPTED |= {"to-pptx", "to-html", "to-md", "to-dxf", "sheet-split"}  # non-PDF or directory outputs


# Commands that do not rewrite a PDF or already handle/report signatures themselves.
_NO_SIGNED_WARNING = {
    "info", "text", "to-images", "to-word", "to-excel", "to-pptx", "to-html", "to-md",
    "quantities", "extract-media", "comments", "verify-sig", "sign-cert", "redact",
    "check-pdfa", "check-access", "preflight", "risks", "inspect-protection",
}


def _reencrypt(out, password):
    """Outputs made from a password-protected file get the same password,
    so editing a protected PDF never silently produces an unprotected one."""
    core.encrypt(out, out, password)


def run_per_file(a):
    from pdfmanager import applog, risks

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
            was_signed = (
                a.command not in _NO_SIGNED_WARNING
                and Path(src).suffix.lower() == ".pdf"
                and risks.signed(src, pw)
            )
            result = a.run(a, src, out, pw)
            if pw and a.command not in KEEP_UNENCRYPTED and result is not None:
                _reencrypt(out, pw)
            if result is not None:
                if was_signed:
                    print(
                        f"warning: {src} is digitally signed; the modified copy's "
                        "signatures will show as broken",
                        file=sys.stderr,
                    )
                print(f"{src} -> {result}" if batch else f"-> {result}")
        except (core.PdfManagerError, PageRangeError, FileNotFoundError, OSError) as exc:
            if not batch:
                raise
            failures += 1
            print(f"error: {applog.format_user_error(exc, path=src)}", file=sys.stderr)
    if getattr(a, "json_records", None) is not None:
        print(json.dumps(a.json_records, default=str, indent=2))
    if batch:
        # text and info print their results; the count is status, not data.
        print(f"{len(inputs) - failures} of {len(inputs)} file(s) done",
              file=sys.stderr if a.command in ("text", "info") else sys.stdout)
    return 1 if failures else 2 if getattr(a, "risks_found", False) else 0


def _encrypt_limits(a):
    return filetools.permissions(a.printing, a.changes, not a.no_copy)
