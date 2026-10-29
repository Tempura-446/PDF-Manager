"""A small local web app: ``pdfm gui`` serves a page on 127.0.0.1 and shows
it in its own window (pywebview, or else Edge/Chrome in app mode) or in the
browser. Uses only the standard library.

Uploads are processed in a temporary folder and the result is sent back as
a download. Folder tools (organize, rename, duplicates) work on paths on
this computer. Every API call must carry the random token printed at
start-up, and only localhost Host headers are accepted, so other web pages
and other machines can't drive the server.
"""

import base64
import email.parser
import email.policy
import hashlib
import importlib.util
import inspect
import io
import json
import os
import re
import secrets
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from pdfmanager import (__version__, archive, compare, core, office, organize, outline, recipes,
                        redact, scan)
from pdfmanager.appdata import data_dir
from pdfmanager.pageranges import PageRangeError

MAX_UPLOAD = 1024 * 1024 * 1024  # 1 GB

PAPER = ["a4", "letter", "legal", "a3", "a5", "a6", "tabloid", "executive"]


def _f(key, label, kind="text", default=None, **extra):
    return {"key": key, "label": label, "kind": kind, "default": default, **extra}


# Operation specs. The page builds its forms from these.
#   files: "pdf" (one or more PDFs), "pdf1" (exactly one), "pdf2" (two),
#          "images", or None (folder tools)
OPERATIONS = [
    {"id": "reader", "group": "Read", "label": "PDF reader", "files": "reader",
     "help": "Read PDFs: search, zoom, bookmarks, forms and printing.", "fields": []},
    {"id": "merge", "group": "Combine", "label": "Merge", "files": "pdf", "min": 2,
     "help": "Combine PDFs in the order listed. Drag to reorder.",
     "fields": [_f("bookmarks", "Add a bookmark per file", "check", True)]},
    {"id": "split", "group": "Combine", "label": "Split", "files": "pdf",
     "help": "Split into several files (downloaded as a ZIP). With one PDF, click the page "
             "previews where each new file should start.",
     "board": {"mode": "split", "field": "ranges", "every": "every"},
     "fields": [_f("every", "Pages per file", "number", 1, min=1),
                _f("ranges", "…or ranges (one file each)", "text", "",
                   placeholder="1-3, 4-10, 11-", hint="separate ranges with spaces or ;")]},
    {"id": "interleave", "group": "Combine", "label": "Interleave scans", "files": "pdf2",
     "help": "Combine separately scanned front sides (first file) and back sides "
             "(second file).",
     "fields": [_f("reverse_back", "Back sides are in reverse order", "check", True)]},
    {"id": "compare", "group": "Combine", "label": "Compare", "files": "pdf2",
     "help": "Find what changed between two versions: add the original first, then the "
             "changed version. You get a report with the differences marked.",
     "fields": [_f("all_pages", "Include unchanged pages in the report", "check", False)]},
    {"id": "extract", "group": "Pages", "label": "Extract pages", "files": "pdf",
     "help": "Click the pages to copy into a new PDF, or type them.",
     "board": {"mode": "select", "effect": "keep", "field": "pages"},
     "fields": [_f("pages", "Pages", "text", "", placeholder="1-3,5", required=True,
                   hint="updates as you click; e.g. 1-3,5 or odd")]},
    {"id": "delete", "group": "Pages", "label": "Delete pages", "files": "pdf",
     "help": "Click the pages to remove, or type them.",
     "board": {"mode": "select", "effect": "remove", "field": "pages"},
     "fields": [_f("pages", "Pages", "text", "", placeholder="2,4-6", required=True,
                   hint="updates as you click; e.g. 2,4-6 or end")]},
    {"id": "reorder", "group": "Pages", "label": "Reorder pages", "files": "pdf",
     "help": "Drag the page previews into the new order, or type it. Pages can also "
             "be duplicated or left out.",
     "board": {"mode": "order", "field": "order"},
     "fields": [_f("order", "New order", "text", "", placeholder="3,1,2,4-", required=True,
                   hint="updates as you drag; e.g. 3,1,2,4- or 5-1")]},
    {"id": "reverse", "group": "Pages", "label": "Reverse order", "files": "pdf",
     "help": "Put the pages in reverse order (last page first).",
     "board": {"mode": "view", "effect": "reverse"}, "fields": []},
    {"id": "bookmarks", "group": "Pages", "label": "Bookmarks", "files": "pdf", "panel": "bookmarks",
     "help": "Add, rename and arrange a PDF's bookmarks, or create them from its headings.",
     "fields": [_f("mode", "Bookmarks", "select", "edit",
                   choices=[["edit", "Use the list above"],
                            ["detect", "Create from headings"],
                            ["clear", "Remove all bookmarks"]]),
                _f("outline", "", "hidden", "")]},
    {"id": "rotate", "group": "Pages", "label": "Rotate", "files": "pdf",
     "help": "Click the pages to turn, or leave them all unselected to turn every page.",
     "board": {"mode": "select", "effect": "rotate", "field": "pages", "degrees": "degrees"},
     "fields": [_f("degrees", "Rotation", "select", "90",
                   choices=[["90", "90° clockwise"], ["180", "180°"],
                            ["270", "90° counter-clockwise"]]),
                _f("pages", "Pages (blank = all)", "text", "", placeholder="all",
                   hint="updates as you click")]},
    {"id": "resize", "group": "Layout", "label": "Resize", "files": "pdf",
     "fields": [_f("size", "Paper size", "select", "a4",
                   choices=[[p, p.upper() if p[0] == "a" else p.capitalize()] for p in PAPER]
                   + [["custom", "Custom…"], ["scale", "Scale by factor…"]]),
                _f("custom", "Custom size", "text", "", placeholder="210x297mm or 8.5x11in",
                   show_if={"size": "custom"}),
                _f("scale", "Scale factor", "number", 1.0, min=0.05, step=0.05,
                   show_if={"size": "scale"}),
                _f("fit", "Fit", "select", "fit",
                   choices=[["fit", "Fit (keep proportions)"], ["fill", "Fill (may crop)"],
                            ["stretch", "Stretch"], ["none", "Don't scale"]]),
                _f("auto_orient", "Keep landscape pages landscape", "check", True),
                _f("pages", "Pages (blank = all)", "text", "", placeholder="all")]},
    {"id": "crop", "group": "Layout", "label": "Crop margins", "files": "pdf", "panel": "crop",
     "help": "Trim the edges of pages. With one PDF, drag the edges of the box on the page "
             "preview, or type the margins.",
     "fields": [_f("top", "Top (mm)", "number", 10, min=0),
                _f("right", "Right (mm)", "number", 10, min=0),
                _f("bottom", "Bottom (mm)", "number", 10, min=0),
                _f("left", "Left (mm)", "number", 10, min=0),
                _f("pages", "Pages (blank = all)", "text", "", placeholder="all")]},
    {"id": "nup", "group": "Layout", "label": "Pages per sheet", "files": "pdf",
     "fields": [_f("per_sheet", "Pages per sheet", "select", "2",
                   choices=[[str(n), str(n)] for n in (2, 4, 6, 8, 9)]),
                _f("size", "Sheet size", "select", "a4",
                   choices=[[p, p.upper() if p[0] == "a" else p.capitalize()] for p in PAPER])]},
    {"id": "compress", "group": "Optimize", "label": "Compress", "files": "pdf",
     "fields": [_f("level", "Compression", "select", "medium",
                   choices=[["low", "Low — lossless"],
                            ["medium", "Medium — recommended"],
                            ["high", "High — smallest file"]],
                   hint="Low keeps every image as is. Medium reduces images to 150 DPI "
                        "(good for screen and office printing); High to 96 DPI.")]},
    {"id": "color", "group": "Optimize", "label": "Convert colours", "files": "pdf",
     "fields": [_f("to", "Convert to", "select", "gray",
                   choices=[["gray", "Grayscale"], ["rgb", "RGB (screen)"],
                            ["cmyk", "CMYK (print)"]]),
                _f("icc", "ICC profile (optional)", "file", None, accept=".icc,.icm")]},
    {"id": "ocr", "group": "Optimize", "label": "Make searchable (OCR)", "files": "pdf",
     "fields": [_f("language", "Language(s)", "text", "eng", placeholder="eng+deu"),
                _f("deskew", "Straighten crooked scans", "check", False),
                _f("force", "Redo pages that already have text", "check", False)]},
    {"id": "clean", "group": "Optimize", "label": "Clean up scans", "files": "pdf",
     "help": "Remove blank pages and turn sideways or upside-down pages the right way up.",
     "fields": [_f("remove_blank", "Remove blank pages", "check", True),
                _f("sensitivity", "Blank page detection", "select", "medium",
                   choices=[["low", "Strict — only completely empty pages"],
                            ["medium", "Normal — allows a few specks"],
                            ["high", "Generous — also nearly empty pages"]]),
                _f("auto_rotate", "Turn pages upright (needs Tesseract)", "check", False)]},
    {"id": "watermark", "group": "Stamp", "label": "Watermark", "files": "pdf",
     "fields": [_f("text", "Text", "text", "CONFIDENTIAL", required=True),
                _f("opacity", "Opacity", "number", 0.15, min=0.01, max=1, step=0.05),
                _f("font_size", "Font size", "number", 60, min=4),
                _f("angle", "Angle", "number", 45),
                _f("below", "Put under the content", "check", False),
                _f("pages", "Pages (blank = all)", "text", "", placeholder="all")]},
    {"id": "number", "group": "Stamp", "label": "Page numbers", "files": "pdf",
     "fields": [_f("format", "Format", "text", "{n} / {total}",
                   hint="{n} = page number, {total} = page count"),
                _f("position", "Position", "select", "bottom-center",
                   choices=[[p, p.replace("-", " ").capitalize()] for p in
                            ("bottom-center", "bottom-right", "bottom-left",
                             "top-center", "top-right", "top-left")]),
                _f("start", "Start at", "number", 1),
                _f("font_size", "Font size", "number", 10, min=4)]},
    {"id": "flatten", "group": "Stamp", "label": "Flatten forms & comments", "files": "pdf",
     "help": "Make filled-in forms and comments part of the page, so they look the same "
             "everywhere and can't be changed. Links keep working.",
     "fields": [_f("forms", "Form fields", "check", True),
                _f("comments", "Comments, highlights, drawings and signatures", "check", True)]},
    {"id": "metadata", "group": "Stamp", "label": "Title & author", "files": "pdf",
     "fields": [_f("title", "Title"), _f("author", "Author"), _f("subject", "Subject"),
                _f("keywords", "Keywords")]},
    {"id": "encrypt", "group": "Security", "label": "Add password", "files": "pdf",
     "fields": [_f("new_password", "New password", "password", "", required=True)]},
    {"id": "decrypt", "group": "Security", "label": "Remove password", "files": "pdf",
     "help": "Enter the current password in the box above the Run button.", "fields": []},
    {"id": "redact", "group": "Security", "label": "Redact", "files": "pdf", "panel": "redact",
     "help": "Permanently black out names, numbers or areas. Pages with redactions become "
             "pictures, so what's under the black boxes is really gone.",
     "fields": [_f("terms", "Words or phrases", "text", "", placeholder="John Smith, 555-1234",
                   hint="separate several with commas"),
                _f("emails", "All email addresses", "check", False),
                _f("phones", "All phone numbers", "check", False),
                _f("numbers", "Card, account and ID numbers", "check", False),
                _f("case_sensitive", "Match upper/lower case", "check", False),
                _f("whole_words", "Whole words only", "check", False),
                _f("boxes", "", "hidden", "")]},
    {"id": "sanitize", "group": "Security", "label": "Remove hidden data", "files": "pdf",
     "help": "Remove information that travels unseen inside a PDF before you share it.",
     "fields": [_f("metadata", "Author, title, dates and other document details", "check", True),
                _f("javascript", "Scripts and actions that run programs", "check", True),
                _f("attachments", "Attached files", "check", True),
                _f("comments", "Comments, highlights and notes", "check", False)]},
    {"id": "pdfa", "group": "Convert", "label": "PDF → PDF/A (archive)", "files": "pdf",
     "help": "Convert to PDF/A, the ISO standard for documents that must stay readable for "
             "decades: fonts and colour profile are embedded. Needs Ghostscript.",
     "fields": [_f("level", "Version", "select", "2",
                   choices=[["2", "PDF/A-2b — recommended"], ["1", "PDF/A-1b — oldest, strictest"],
                            ["3", "PDF/A-3b — allows attachments"]])]},
    {"id": "from-images", "group": "Convert", "label": "Images → PDF", "files": "images",
     "fields": [_f("size", "Page size", "select", "",
                   choices=[["", "Same as image"]] +
                   [[p, p.upper() if p[0] == "a" else p.capitalize()] for p in PAPER]),
                _f("margin", "Margin (mm)", "number", 0, min=0)]},
    {"id": "to-images", "group": "Convert", "label": "PDF → images", "files": "pdf",
     "fields": [_f("format", "Format", "select", "png", choices=[["png", "PNG"], ["jpg", "JPG"]]),
                _f("dpi", "Resolution (DPI)", "number", 150, min=36, max=600),
                _f("pages", "Pages (blank = all)", "text", "", placeholder="all")]},
    {"id": "text", "group": "Convert", "label": "PDF → text", "files": "pdf", "fields": []},
    {"id": "to-word", "group": "Convert", "label": "PDF → Word", "files": "pdf",
     "help": "Turn PDFs into editable Word documents (.docx).",
     "fields": [_f("engine", "Converter", "select", "auto",
                   choices=[["auto", "Best available (Microsoft Word if installed)"],
                            ["basic", "Built-in — paragraphs, headings and pictures"],
                            ["layout", "Exact layout — needs LibreOffice; text in boxes"],
                            ["word", "Microsoft Word only"]],
                   hint="Microsoft Word gives the most editable result. The built-in "
                        "converter turns tables and columns into plain paragraphs.")]},
    {"id": "from-office", "group": "Convert", "label": "Office → PDF", "files": "office",
     "help": "Convert Word, Excel, PowerPoint, OpenDocument, RTF or text files to PDF. "
             "Uses Microsoft Office if installed, otherwise LibreOffice.",
     "fields": []},
    {"id": "recipe", "group": "Automate", "label": "Recipes", "files": "pdf", "panel": "recipe",
     "help": "Run several tools one after another, e.g. clean up scans → OCR → compress. "
             "Save a recipe to use it again here, from the command line or in a watch folder.",
     "fields": [_f("steps", "", "hidden", ""), _f("recipe_name", "", "hidden", "")]},
    {"id": "organize", "group": "Folders", "label": "Organize folder", "files": None,
     "help": "Sort the PDFs in a folder on this computer into sub-folders.",
     "fields": [_f("directory", "Folder", "text", "", placeholder="/Users/me/Downloads",
                   required=True),
                _f("dest", "Put sub-folders in (blank = same folder)", "text", ""),
                _f("by", "Group by", "select", "year",
                   choices=[[g, g.replace("-", " ").capitalize()] for g in organize.GROUPINGS]),
                _f("duplicates", "Identical files", "select", "keep",
                   choices=[["keep", "Organize anyway"], ["skip", "Skip"],
                            ["folder", "Put in a Duplicates folder"]]),
                _f("move", "Move instead of copy", "check", False),
                _f("recursive", "Include sub-folders", "check", False)]},
    {"id": "rename", "group": "Folders", "label": "Rename from metadata", "files": None,
     "fields": [_f("directory", "Folder", "text", "", required=True),
                _f("pattern", "Pattern", "text", "{date:%Y-%m-%d} {title}",
                   hint="{title} {author} {pages} {date:%Y-%m-%d} {stem}"),
                _f("recursive", "Include sub-folders", "check", False)]},
    {"id": "duplicates", "group": "Folders", "label": "Find duplicates", "files": None,
     "fields": [_f("directory", "Folder", "text", "", required=True),
                _f("recursive", "Include sub-folders", "check", False),
                _f("delete", "Delete duplicates (keeps the oldest copy)", "check", False)]},
]
OPS = {op["id"]: op for op in OPERATIONS}

SUFFIX = {"recipe": "recipe", "redact": "redacted", "bookmarks": "bookmarks", "flatten": "flat", "sanitize": "clean", "pdfa": "pdfa", "clean": "clean", "extract": "extract", "delete": "edited", "reorder": "reordered",
          "reverse": "reversed", "rotate": "rotated", "resize": "resized", "crop": "cropped",
          "nup": "nup", "compress": "compressed", "color": "color", "ocr": "ocr",
          "watermark": "watermarked", "number": "numbered", "metadata": "edited",
          "encrypt": "protected", "decrypt": "unlocked"}


class UserError(Exception):
    pass


# --------------------------------------------------------------------------
# Running operations
# --------------------------------------------------------------------------

def _bool(v):
    return str(v).lower() in ("1", "true", "on", "yes")


def _num(v, cast=float, default=None):
    if v in (None, ""):
        return default
    try:
        return cast(v)
    except ValueError:
        raise UserError(f"not a number: {v!r}") from None


def _pages(opts, key="pages"):
    return (opts.get(key) or "").strip() or None


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
    elif op == "compress":
        core.compress(src, out, level=opts.get("level") or "medium", password=password)
    elif op == "color":
        icc = extra_files.get("icc")
        core.convert_color(src, out, opts.get("to") or "gray", icc, password)
    elif op == "ocr":
        core.ocr(src, out, language=opts.get("language") or "eng",
                 force=_bool(opts.get("force")), deskew=_bool(opts.get("deskew")),
                 password=password)
    elif op == "clean":
        auto_rotate = _bool(opts.get("auto_rotate"))
        if auto_rotate and not scan.find_tesseract():
            raise UserError("turning pages upright needs the Tesseract program; install it "
                            "(see the OCR tool) or untick that option")
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
        found = redact.redact(src, out, terms=terms, patterns=patterns, boxes=boxes,
                              case_sensitive=_bool(opts.get("case_sensitive")),
                              whole_words=_bool(opts.get("whole_words")), password=password)
        notes.append(f"{src.name}: " + (
            f"blacked out {sum(found.values())} area{'s' if sum(found.values()) != 1 else ''} "
            f"on page{'s' if len(found) != 1 else ''} {_pages_list(list(found))}" if found
            else "nothing matched, so nothing was blacked out"))
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
        recipes.run(steps, src, out, password, notes)
    elif op == "flatten":
        forms, comments = _bool(opts.get("forms", True)), _bool(opts.get("comments", True))
        if not forms and not comments:
            raise UserError("choose form fields, comments or both")
        archive.flatten(src, out, forms=forms, comments=comments, password=password)
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
    elif op == "pdfa":
        if not archive.find_ghostscript():
            raise UserError("PDF/A needs Ghostscript: install it from "
                            "https://ghostscript.com/releases/gsdnld.html and try again")
        archive.to_pdfa(src, out, level=opts.get("level") or "2", password=password)
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
        if not opts.get("new_password"):
            raise UserError("enter a new password")
        core.encrypt(src, out, opts["new_password"], password=password)
    elif op == "decrypt":
        if not password:
            raise UserError("enter the PDF's current password")
        core.decrypt(src, out, password)
    elif op == "split":
        ranges = [r for r in re.split(r"[;\s]+", opts.get("ranges") or "") if r]
        folder = out_dir / f"{src.stem}_split"
        return core.split(src, folder, every=None if ranges else _num(opts.get("every"), int, 1),
                          ranges=ranges or None, password=password)
    elif op == "to-images":
        return core.pdf_to_images(src, out_dir / f"{src.stem}_images",
                                  _num(opts.get("dpi"), int, 150), opts.get("format") or "png",
                                  _pages(opts), password)
    elif op == "text":
        out = out.with_suffix(".txt")
        core.extract_text(src, out, None, password)
    elif op == "to-word":
        out = out_dir / f"{src.stem}.docx"
        _, engine = office.pdf_to_word(src, out, opts.get("engine") or "auto", password)
        if engine == "basic" and (opts.get("engine") or "auto") == "auto":
            notes.append("Converted with the built-in converter. For a more editable "
                         "result, install Microsoft Word.")
    elif op == "from-office":
        out = out_dir / f"{src.stem}.pdf"
        office.office_to_pdf(src, out)
    else:
        raise UserError(f"unknown operation {op!r}")
    return [out]


def _redact_request(opts):
    """(terms, patterns, boxes) from the Redact tool's options. Boxes come
    from the page editor as {"1": [[x0, y0, x1, y1], ...]} (1-based pages)."""
    terms = [t.strip() for t in re.split(r"[,;\n]", opts.get("terms") or "") if t.strip()]
    patterns = [redact.PRESETS[k] for k in redact.PRESETS if _bool(opts.get(k))]
    try:
        raw = json.loads(opts.get("boxes") or "{}")
        boxes = {int(page) - 1: [tuple(float(v) for v in box) for box in found]
                 for page, found in raw.items() if found}
    except (ValueError, TypeError, AttributeError):
        raise UserError("the boxes drawn on the pages are damaged; draw them again") from None
    return terms, patterns, boxes


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


def _protect(outputs, password, op):
    """PDFs made from a password-protected file get the same password."""
    if password and op not in ("decrypt", "encrypt", "pdfa"):
        for path in outputs:
            if path.suffix.lower() == ".pdf":
                core.encrypt(path, path, password)


def _flatten_outputs(outputs, out_dir):
    """Move per-input outputs into ``out_dir``, renaming on clashes."""
    taken, flat = set(), []
    for sub, path in outputs:
        target = organize._unique(out_dir / path.relative_to(sub), taken)
        target.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target)
        flat.append(target)
    return flat


def run_operation(op, files, opts, password=None, extra_files=None, work=None, notes=None):
    """Run ``op`` on uploaded ``files`` (paths in the temporary folder
    ``work``). Returns (filename, bytes, content type) for the download;
    messages for the user are appended to ``notes``."""
    spec = OPS.get(op)
    if spec is None:
        raise UserError(f"unknown operation {op!r}")
    for field in spec["fields"]:
        if field.get("required") and not str(opts.get(field["key"]) or "").strip():
            raise UserError(f"please fill in “{field['label']}”")
    if not files:
        raise UserError("add at least one file")
    if len(files) < spec.get("min", 1):
        raise UserError(f"add at least {spec['min']} files")
    out_dir = Path(work or files[0].parent) / "out"
    out_dir.mkdir()
    extra_files = extra_files or {}

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
        summary = compare.compare(files[0], files[1], out, password, password,
                                  all_pages=_bool(opts.get("all_pages")))
        changed = [c for c in summary["changes"] if c["kind"] != "same"]
        notes.append(f"{len(changed)} page{'s' if len(changed) != 1 else ''} with differences"
                     if changed else "No differences found.")
        outputs = [out]
    elif op == "from-images":
        out = out_dir / (files[0].stem + ".pdf" if len(files) == 1 else "images.pdf")
        core.images_to_pdf(files, out, opts.get("size") or None,
                           _num(opts.get("margin"), float, 0) * core.POINTS_PER_MM)
        outputs = [out]
    else:
        outputs = []
        for index, src in enumerate(files):
            # Each input gets its own output folder; flatten afterwards.
            sub = out_dir / f"{index:03d}"
            sub.mkdir()
            outputs.extend((sub, p) for p in _run_file_op(op, src, sub, opts, password,
                                                           extra_files, notes))
        outputs = _flatten_outputs(outputs, out_dir)

    _protect(outputs, password, op)
    if len(outputs) == 1:
        path = outputs[0]
        ctype = {".txt": "text/plain; charset=utf-8",
                 ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 }.get(path.suffix, "application/pdf")
        return path.name, path.read_bytes(), ctype
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in outputs:
            zf.write(path, path.relative_to(out_dir))
    name = f"{files[0].stem}_{SUFFIX.get(op, op)}.zip" if len(files) == 1 else f"pdfm_{op}.zip"
    return name, buf.getvalue(), "application/zip"


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


# --------------------------------------------------------------------------
# Thumbnails and results
# --------------------------------------------------------------------------

THUMB_WIDTH = 240


def inspect_pdf(path, password=None):
    """Page count, size label and a first-page thumbnail (data URL) for the
    file cards and the result preview."""
    try:
        reader = core.open_pdf(path, password)
    except core.PdfManagerError as exc:
        locked = "encrypted" in str(exc) or "password" in str(exc)
        return {"pages": None, "locked": locked, "error": None if locked else str(exc)}
    info = {"pages": len(reader.pages), "locked": False, "error": None, "thumb": None}
    try:
        import pypdfium2 as pdfium

        with core.PDFIUM_LOCK:
            doc = pdfium.PdfDocument(str(path), password=password)
            try:
                page = doc[0]
                scale = THUMB_WIDTH / max(page.get_width(), 1)
                image = page.render(scale=min(scale, 2.0)).to_pil().convert("RGB")
            finally:
                doc.close()
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=82)
        info["thumb"] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        pass  # thumbnails are a nicety; never fail because of them
    return info


class ResultCache:
    """Keeps the last few results so the desktop window can save them with a
    native Save dialog (browsers download them directly instead)."""

    def __init__(self, keep=5):
        self.keep = keep
        self.items = {}
        self.lock = threading.Lock()

    def add(self, name, data):
        key = secrets.token_urlsafe(12)
        with self.lock:
            self.items[key] = (name, data)
            while len(self.items) > self.keep:
                self.items.pop(next(iter(self.items)))
        return key

    def get(self, key):
        with self.lock:
            return self.items.get(key)


class OpenFiles:
    """Files on this computer the reader may load: ones given on the command
    line ("Open with…") or picked in the desktop Open dialog. The page can
    only ask for these by id, never for an arbitrary path."""

    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()

    def add(self, path):
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"no such file: {path}")
        key = secrets.token_urlsafe(9)
        with self.lock:
            self.items[key] = path
        return key

    def get(self, key):
        with self.lock:
            return self.items.get(key)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

STATIC_TYPES = {
    ".mjs": "text/javascript", ".js": "text/javascript", ".css": "text/css",
    ".svg": "image/svg+xml", ".gif": "image/gif", ".png": "image/png",
    ".wasm": "application/wasm", ".json": "application/json",
}


def _page_csp(allow_eval):
    """Content-Security-Policy for the app page. pdf.js needs workers,
    WebAssembly (JPEG 2000 / JBIG2 decoders) and fonts built from data;
    pywebview's bridge additionally needs eval() in the desktop window."""
    scripts = "'self' 'unsafe-inline' 'wasm-unsafe-eval'" + (" 'unsafe-eval'" if allow_eval else "")
    return ("default-src 'self'; "
            f"script-src {scripts}; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; "
            "worker-src 'self' blob:; connect-src 'self' data: blob:; "
            "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

def _parse_multipart(content_type, body):
    """Return (fields, files) where files is a list of (field name, filename, bytes)."""
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    if not message.is_multipart():
        raise UserError("expected a form upload")
    fields, files = {}, []
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        data = part.get_payload(decode=True) or b""
        if filename is not None:
            files.append((name, filename, data))
        else:
            fields[name] = data.decode("utf-8")
    return fields, files


def _safe_name(name):
    name = Path(name.replace("\\", "/")).name  # drop any path the browser sent
    return re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .") or "file"


class Handler(BaseHTTPRequestHandler):
    server_version = f"pdfm/{__version__}"
    protocol_version = "HTTP/1.1"  # keep connections open between requests
    token = ""
    allowed_hosts = set()
    results = None  # ResultCache
    opened = None  # OpenFiles
    # pywebview's JavaScript bridge needs eval(); only allowed in the desktop window.
    allow_eval = False

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    # -- helpers -----------------------------------------------------------

    def _send(self, status, body, ctype="application/json", headers=None, cache=False):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Static files may be cached but are checked (cheaply, by ETag) on every
        # load, so an updated app never runs old code.
        self.send_header("Cache-Control", "no-cache" if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        # The request body may not have been read; don't reuse the connection.
        self.close_connection = True
        self._send(status, {"error": message})

    def _host_ok(self):
        return self.headers.get("Host", "") in self.allowed_hosts

    def _token_ok(self):
        ok = secrets.compare_digest(self.headers.get("X-Pdfm-Token", ""), self.token)
        if ok:
            type(self).last_seen = time.monotonic()  # the page is still open
        return ok

    # -- routes ------------------------------------------------------------

    def do_GET(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad host")
        path = self.path.split("?", 1)[0]
        if path == "/":
            page = resources.files("pdfmanager").joinpath("gui.html").read_text("utf-8")
            return self._send(HTTPStatus.OK, page, "text/html; charset=utf-8",
                              {"Content-Security-Policy": _page_csp(self.allow_eval)})
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if not self._token_ok():
            return self._error(HTTPStatus.FORBIDDEN, "missing or wrong token")
        if path == "/api/operations":
            return self._send(HTTPStatus.OK, {"operations": OPERATIONS, "version": __version__})
        if path == "/api/recipes":
            return self._send(HTTPStatus.OK, {"recipes": list(recipes.load_all().values()),
                                              "step_ops": list(recipes.STEP_OPS)})
        if path.startswith("/api/file/"):
            return self._open_file(path[len("/api/file/"):])
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def _static(self, rel):
        """Serve the app's own static files (the reader and pdf.js)."""
        parts = rel.split("/")
        if any(p in ("", ".", "..") or "\\" in p or ":" in p for p in parts):
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        cached = _STATIC_CACHE.get(rel)
        if cached is None:
            target = resources.files("pdfmanager").joinpath("static", *parts)
            if not target.is_file():
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            data = target.read_bytes()
            cached = _STATIC_CACHE[rel] = (data, '"%s"' % hashlib.blake2b(data, digest_size=8).hexdigest())
        data, etag = cached
        if etag in self.headers.get("If-None-Match", ""):
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return None
        ctype = STATIC_TYPES.get(Path(rel).suffix.lower(), "application/octet-stream")
        return self._send(HTTPStatus.OK, data, ctype, {"ETag": etag}, cache=True)

    def _open_file(self, key):
        path = self.opened.get(key) if self.opened is not None else None
        if path is None or not path.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "that file is no longer available")
        quoted = path.name.replace('"', "")
        return self._send(HTTPStatus.OK, path.read_bytes(), "application/pdf",
                          {"X-Pdfm-Filename": quoted, "X-Pdfm-Path": str(path)})

    def do_POST(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad host")
        if self.path.split("?", 1)[0] == "/api/bye":
            return self._bye()
        if not self._token_ok():
            return self._error(HTTPStatus.FORBIDDEN, "missing or wrong token")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "upload too large (1 GB max)")
        body = self.rfile.read(length)
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/ping":  # sent regularly while the page is open
                return self._send(HTTPStatus.OK, {"ok": True})
            if path == "/api/run":
                return self._run(body)
            if path == "/api/inspect":
                return self._inspect(body)
            if path == "/api/keep":
                return self._keep(body)
            if path == "/api/outline":
                return self._outline(body)
            if path == "/api/redact/find":
                return self._redact_find(body)
            if path == "/api/recipes/save":
                data = json.loads(body or b"{}")
                saved = recipes.save(data.get("name"), data.get("steps"))
                return self._send(HTTPStatus.OK, {"recipe": saved,
                                                  "recipes": list(recipes.load_all().values())})
            if path == "/api/recipes/delete":
                recipes.delete(json.loads(body or b"{}").get("name"))
                return self._send(HTTPStatus.OK, {"recipes": list(recipes.load_all().values())})
            if path == "/api/folder":
                data = json.loads(body or b"{}")
                result = run_folder_operation(data.get("op"), data.get("options") or {},
                                              bool(data.get("apply")))
                return self._send(HTTPStatus.OK, result)
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        except (UserError, core.PdfManagerError, PageRangeError, ValueError,
                FileNotFoundError, NotADirectoryError) as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # report, don't kill the server
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR,
                               f"{type(exc).__name__}: {exc}")

    def _run(self, body):
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        op = fields.pop("op", "")
        password = fields.pop("password", "") or None
        work = Path(tempfile.mkdtemp(prefix="pdfm-"))
        try:
            files, extra = [], {}
            for index, (name, filename, data) in enumerate(uploads):
                # One folder per upload keeps original names without clashes.
                folder = work / "in" / f"{index:03d}"
                folder.mkdir(parents=True)
                path = folder / _safe_name(filename)
                path.write_bytes(data)
                if name == "files":
                    files.append(path)
                elif data:  # extra file field, e.g. an ICC profile
                    extra[name] = path
            notes = []
            name, data, ctype = run_operation(op, files, fields, password, extra, work, notes)
            quoted = name.replace('"', "")
            headers = {"Content-Disposition": f'attachment; filename="{quoted}"',
                       "X-Pdfm-Filename": quoted}
            if notes:
                headers["X-Pdfm-Note"] = urllib.parse.quote("\n".join(notes))
            if self.headers.get("X-Pdfm-Keep") == "1" and self.results is not None:
                headers["X-Pdfm-Result-Id"] = self.results.add(name, data)
            return self._send(HTTPStatus.OK, data, ctype, headers)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _keep(self, body):
        """Hold a file (e.g. the reader's copy with filled-in forms) so the
        desktop window can save it with the native Save dialog."""
        _, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads or self.results is None:
            raise UserError("no file")
        _, filename, data = uploads[0]
        return self._send(HTTPStatus.OK, {"id": self.results.add(_safe_name(filename), data)})

    def _outline(self, body):
        """The bookmarks of an uploaded PDF, or (detect=1) suggestions from
        its headings."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = Path(tmp) / "file.pdf"
            path.write_bytes(uploads[0][2])
            password = fields.get("password") or None
            pages = len(core.open_pdf(path, password).pages)
            items = (outline.detect_headings(path, password) if _bool(fields.get("detect"))
                     else outline.get_outline(path, password))
        return self._send(HTTPStatus.OK, {"items": items, "pages": pages})

    def _bye(self):
        """The page is being closed (or reloaded). Sent with
        navigator.sendBeacon, which can't add headers, so the token comes
        in the query string; this request can do nothing else."""
        self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 65536))
        token = urllib.parse.parse_qs(self.path.partition("?")[2]).get("token", [""])[0]
        if not secrets.compare_digest(token, self.token):
            return self._error(HTTPStatus.FORBIDDEN, "missing or wrong token")
        type(self).closed_at = time.monotonic()
        return self._send(HTTPStatus.OK, {"ok": True})

    def _redact_find(self, body):
        """Where the Redact tool's words and kinds of numbers are found, so
        the page editor can show them before anything is blacked out."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        terms, patterns, _ = _redact_request({k: v for k, v in fields.items() if k != "boxes"})
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = Path(tmp) / "file.pdf"
            path.write_bytes(uploads[0][2])
            found = redact.find_text(path, terms, patterns,
                                     case_sensitive=_bool(fields.get("case_sensitive")),
                                     whole_words=_bool(fields.get("whole_words")),
                                     password=fields.get("password") or None)
        return self._send(HTTPStatus.OK, {"boxes": {str(p + 1): [list(b) for b in boxes]
                                                    for p, boxes in found.items()}})

    def _inspect(self, body):
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = Path(tmp) / "file.pdf"
            path.write_bytes(uploads[0][2])
            info = inspect_pdf(path, fields.get("password") or None)
        return self._send(HTTPStatus.OK, info)


# Static files (the reader and pdf.js), read once: rel path -> (bytes, ETag).
_STATIC_CACHE = {}

# The app uses this port when it's free, so the page keeps the same address
# between launches and the window's cache and settings (theme, reading
# positions, a saved signature) carry over. Another free port is used if
# it's taken, e.g. by a second copy of the app.
APP_PORT = 47821


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR would let a second copy of the app take the same
    # port; ask for the port exclusively there instead.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # HTTPServer.server_bind looks up the computer's full name, which can
        # take seconds on some Windows networks; it isn't needed here.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def make_server(port=0, desktop=False, open_paths=(), prefer=None):
    """Create (but don't start) the server. Returns (server, url). The
    server's result cache is ``server.RequestHandlerClass.results`` and its
    openable files ``server.RequestHandlerClass.opened``. ``open_paths``
    are PDFs to show in the reader straight away. With ``port`` 0, the
    ``prefer``red port is tried first, then any free port."""
    token = secrets.token_urlsafe(24)
    opened = OpenFiles()
    open_ids = [opened.add(p) for p in open_paths]
    handler = type("PdfmHandler", (Handler,), {"token": token, "results": ResultCache(),
                                               "opened": opened, "allow_eval": desktop,
                                               "last_seen": time.monotonic(),
                                               "closed_at": None})
    server = None
    if not port and prefer:
        try:
            server = _Server(("127.0.0.1", prefer), handler)
        except OSError:
            server = None  # in use
    if server is None:
        server = _Server(("127.0.0.1", port), handler)
    real_port = server.server_address[1]
    handler.allowed_hosts = {f"127.0.0.1:{real_port}", f"localhost:{real_port}"}
    url = f"http://127.0.0.1:{real_port}/#token={token}"
    if open_ids:
        url += "&open=" + ",".join(open_ids)
    return server, url


def serve(port=0, open_browser=True, open_paths=(), exit_when_closed=False):
    """Run in the browser: print the link, open it, serve until Ctrl+C (or,
    with ``exit_when_closed``, until the page has been closed)."""
    server, url = make_server(port, open_paths=open_paths, prefer=APP_PORT)
    print(f"PDF Manager is running at {url}")
    print("Keep this window open while you use it; press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        if exit_when_closed:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            wait_until_closed(server.RequestHandlerClass, grace=20)
            server.shutdown()
        else:
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def wait_until_closed(handler, proc=None, idle=300, idle_handed_off=90, poll=0.5, grace=2.0):
    """Block until the page is gone.

    When its window closes, the page says so (/api/bye); if nothing is heard
    from it within ``grace`` seconds after that (a reload asks for the page
    again straight away), it's gone. The page also pings the server every
    15 seconds (less often while hidden), which updates
    ``handler.last_seen``; after ``idle`` seconds without a ping, the page
    counts as gone too (e.g. the browser was killed).

    With ``proc`` (the browser started for an app window): if it exits after
    the first few seconds, the window was closed. If it exits sooner, the
    browser handed the window to a copy that was already running, so only
    the page's signals count. A browser that keeps running in the background
    after its window is closed (Edge does) doesn't keep the app alive."""
    started = time.monotonic()
    while True:
        time.sleep(poll)
        now = time.monotonic()
        closed = handler.closed_at
        if closed is not None and handler.last_seen <= closed and now - closed > grace:
            return
        quiet = now - handler.last_seen
        if proc is not None and proc.poll() is not None:
            if now - started > 5 + poll or quiet > idle_handed_off:
                return
        elif quiet > idle:
            return


class DesktopApi:
    """Functions the page can call in the desktop window (window.pywebview.api)."""

    def __init__(self, results, opened=None):
        self._results = results
        self._opened = opened
        self._window = None

    def open_pdf(self):
        """Pick a PDF with the native Open dialog for the reader. Returns
        {"id", "name"} or None if cancelled."""
        import webview

        chosen = self._window.create_file_dialog(
            webview.FileDialog.OPEN, directory=str(Path.home()),
            file_types=("PDF files (*.pdf)", "All files (*.*)"))
        if not chosen:
            return None
        path = Path(chosen if isinstance(chosen, str) else chosen[0])
        return {"id": self._opened.add(path), "name": path.name}

    def save_result(self, key, suggested_name):
        """Ask where to save a result and write it. Returns the path, or
        None if the user cancelled."""
        import webview

        item = self._results.get(key)
        if item is None:
            raise ValueError("that result has expired; run the tool again")
        name, data = item
        chosen = self._window.create_file_dialog(
            webview.FileDialog.SAVE, directory=str(Path.home()),
            save_filename=suggested_name or name)
        if not chosen:
            return None
        target = Path(chosen if isinstance(chosen, str) else chosen[0])
        target.write_bytes(data)
        return str(target)

    def pick_folder(self):
        import webview

        chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER,
                                                 directory=str(Path.home()))
        return (chosen if isinstance(chosen, str) else chosen[0]) if chosen else None

    def reveal(self, path):
        """Show a saved file in Explorer / Finder / the file manager."""
        reveal_in_file_manager(path)


def reveal_in_file_manager(path):
    import subprocess
    import sys

    path = Path(path)
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if path.is_file() else path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def desktop_available():
    return importlib.util.find_spec("webview") is not None


def _pywebview_version():
    try:
        from importlib.metadata import version

        return version("pywebview")
    except Exception:
        return "unknown"


def _window_note():
    return data_dir() / "window-error.txt"


def run_desktop(port=0, open_paths=()):
    """Open PDF Manager in its own window (needs pywebview). Returns False if
    no window could be opened, so the caller can fall back to the browser.

    If the window can't open (for example Windows Smart App Control blocks
    a library pywebview needs), the reason is saved and this pywebview
    version isn't tried again, so the user doesn't see the error at every
    start. Installing another pywebview version tries again."""
    if not desktop_available():
        return False
    note, version = _window_note(), _pywebview_version()
    try:
        if note.read_text(encoding="utf-8").splitlines()[0] == version:
            return False
    except (OSError, IndexError):
        pass
    server, url = make_server(port, desktop=True, open_paths=open_paths, prefer=APP_PORT)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    handler = server.RequestHandlerClass
    api = DesktopApi(handler.results, handler.opened)
    try:
        import webview

        window = webview.create_window("PDF Manager", url, js_api=api, width=1180, height=820,
                                       min_size=(400, 560), background_color="#f6f6f3")
        api._window = window
        # Keep the window's cache and settings between launches (pywebview
        # otherwise starts every time like a private browser window).
        options = {}
        if "storage_path" in inspect.signature(webview.start).parameters:
            storage = data_dir() / "webview"
            storage.mkdir(parents=True, exist_ok=True)
            options = {"private_mode": False, "storage_path": str(storage)}
        webview.start(**options)
    except Exception as exc:  # e.g. a missing or blocked web engine
        print(f"Could not open the app window ({exc}); trying another way.")
        try:
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(f"{version}\n{type(exc).__name__}: {exc}\n", encoding="utf-8")
        except OSError:
            pass
        server.shutdown()
        server.server_close()
        return False
    server.shutdown()
    server.server_close()
    return True


def find_app_browser():
    """A Chromium-based browser that can show the app as a window of its
    own, without tabs or an address bar: Edge (part of Windows), Chrome,
    Chromium or Brave. Returns its path or None."""
    if sys.platform == "win32":
        bases = [os.environ.get(k) for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        apps = ["Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe",
                "BraveSoftware/Brave-Browser/Application/brave.exe"]
        for app in apps:
            for base in filter(None, bases):
                if (Path(base) / app).is_file():
                    return str(Path(base) / app)
        return None
    if sys.platform == "darwin":
        for app in ("Microsoft Edge", "Google Chrome", "Chromium", "Brave Browser"):
            path = Path("/Applications") / f"{app}.app" / "Contents" / "MacOS" / app
            if path.is_file():
                return str(path)
        return None
    for name in ("microsoft-edge", "microsoft-edge-stable", "google-chrome", "google-chrome-stable",
                 "chromium", "chromium-browser", "brave-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def run_app_window(port=0, open_paths=()):
    """Open PDF Manager as an Edge/Chrome app window. Returns False if no
    such browser is installed. Results are saved like downloads."""
    browser = find_app_browser()
    if not browser:
        return False
    server, url = make_server(port, open_paths=open_paths, prefer=APP_PORT)
    # A browser profile of its own keeps the window separate from the user's
    # normal browsing, so closing the app never touches their other windows.
    # A second copy of the app (on another port) gets a temporary profile of
    # its own, so each copy only ever closes its own browser.
    primary = server.server_address[1] == APP_PORT
    profile = data_dir() / ("window" if primary else f"window-{server.server_address[1]}")
    try:
        profile.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            [browser, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
             "--no-default-browser-check", "--window-size=1180,820"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        server.server_close()
        return False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        wait_until_closed(server.RequestHandlerClass, proc)
    except KeyboardInterrupt:
        pass
    server.shutdown()
    server.server_close()
    _close_browser(proc, profile)
    if not primary:
        shutil.rmtree(profile, ignore_errors=True)
    return True


def _profile_processes(profile):
    """Ids of browser processes running with this profile (Edge and Chrome
    may keep running in the background after their last window closes)."""
    marker = f"--user-data-dir={profile}"
    if sys.platform == "win32":
        script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
                  "$_.CommandLine.Contains($env:PDFM_PROFILE) } | "
                  "ForEach-Object { $_.ProcessId }")
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                 capture_output=True, text=True, timeout=20,
                                 env={**os.environ, "PDFM_PROFILE": marker},
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        except (OSError, subprocess.TimeoutExpired):
            return []
        return [int(p) for p in out.split() if p.isdigit()]
    pids = []
    for entry in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            if marker in entry.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace"):
                pids.append(int(entry.parent.name))
        except (OSError, ValueError):
            continue
    return pids


def _close_browser(proc, profile):
    """Stop the browser that showed the app window once the window is gone."""
    try:
        proc.wait(timeout=1.5)  # usually it quits by itself
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    # A browser started earlier with this profile (e.g. kept running in the
    # background by Edge) may still be there.
    for pid in _profile_processes(profile):
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def launch(open_paths=(), port=0):
    """Open the app the best way available: its own pywebview window, else
    an Edge/Chrome app window, else a browser tab. Returns once closed."""
    if run_desktop(port=port, open_paths=open_paths):
        return
    if run_app_window(port=port, open_paths=open_paths):
        return
    serve(port=port, open_paths=open_paths, exit_when_closed=True)


def main_app(argv=None):
    """Entry point for the `pdfm-app` launcher. PDFs given as arguments
    (e.g. by "Open with…" or a file association) open in the reader."""
    args = sys.argv[1:] if argv is None else argv
    launch([a for a in args if Path(a).suffix.lower() == ".pdf" and Path(a).is_file()])
