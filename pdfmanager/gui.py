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
import hashlib
import hmac
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
import unicodedata
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from pypdf import PdfReader

from pdfmanager import (__version__, archive, compare, core, office, organize, outline, progress,
                        forms, recipes, redact, scan, search, textedit, update, viewports, markup, pageimages,
                        formdetect, pdfacheck, filetools, comments, filefacts)
from pdfmanager import certsign
from pdfmanager import accessibility  # -- accessibility --
from pdfmanager import applog
from pdfmanager import ocr  # which OCR engines are installed
from pdfmanager import scanner, scanpdf  # Scan: paper to a searchable PDF
from pdfmanager import quickactions  # Explorer's right-click actions
from pdfmanager import watchfolders  # Settings > Watch folders
from pdfmanager.appdata import data_dir, write_private
from pdfmanager.pageranges import PageRangeError

# One run's uploads. Parsing a form upload takes several times its size in
# memory, so this stays well inside what a laptop has.
MAX_UPLOAD = 1024 * 1024 * 1024

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
     "board": {"mode": "split", "field": "ranges", "every": "every", "how": "by"},
     "fields": [_f("by", "Split", "select", "every",
                   choices=[["every", "Every few pages"], ["ranges", "At the pages I choose"],
                            ["bookmarks", "At each bookmark (chapter)"],
                            ["size", "Into files no larger than…"]]),
                _f("every", "Pages per file", "number", 1, min=1, show_if={"by": "every"}),
                _f("ranges", "Page ranges, one file each", "text", "", show_if={"by": "ranges"},
                   placeholder="1-3, 4-10, 11-",
                   hint="Separate ranges with spaces or ;. With one PDF, clicking the page "
                        "previews fills this in."),
                _f("level", "Bookmarks to split at", "select", "1", show_if={"by": "bookmarks"},
                   choices=[["1", "Main bookmarks only"],
                            ["2", "Also the bookmarks one level below"]],
                   hint="Each file is named after its bookmark. Pages before the first "
                        "bookmark get a file of their own."),
                _f("max_mb", "Largest file size (MB)", "number", 10, min=0.01, step=0.5,
                   show_if={"by": "size"},
                   hint="Handy for email attachments. Each file gets as many pages as fit; a "
                        "single page larger than this gets a file of its own.")]},
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
    {"id": "delete", "group": "Pages", "label": "Delete pages", "files": "pdf", "action": "Delete pages",
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
    {"id": "insert-blank", "group": "Pages", "label": "Insert blank pages", "files": "pdf",
     "help": "Click the pages to put a blank page after. Each blank page has the size of the "
             "page before it. Choose All to get a page for notes after every page.",
     "board": {"mode": "select", "effect": "insert", "field": "after"},
     "fields": [_f("after", "After pages", "text", "", placeholder="3,7 or all",
                   hint="updates as you click; e.g. 3,7 or end or all"),
                _f("at_start", "Also before the first page", "check", False),
                _f("count", "Blank pages at each place", "number", 1, min=1, max=100)]},
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
    {"id": "labels", "group": "Pages", "label": "Page labels", "files": "pdf",
     "board": {"mode": "view"},
     "help": "Number pages the way the book does: i, ii, iii for the front pages, then 1, 2, 3. "
             "Readers show these labels and go to them (\"page iv\").",
     "fields": [_f("roman", "Front pages numbered i, ii, iii", "number", 0, min=0,
                   hint="e.g. 4 for a cover and three pages of contents; 0 for none"),
                _f("style", "Then number", "select", "1",
                   choices=[[k, v] for k, v in filetools.STYLE_NAMES.items()]),
                _f("prefix", "Prefix", "text", "", placeholder="e.g. A-"),
                _f("start", "Starting at", "number", 1, min=1),
                _f("custom", "Or write them out", "text", "", placeholder="1: Cover; 2-4: i; 5-: 1",
                   hint="Each part: the page it starts on, then its first label. "
                        "Overrides the choices above."),
                _f("clear", "Remove the page labels", "check", False)]},
    {"id": "rotate", "group": "Pages", "label": "Rotate", "files": "pdf",
     "help": "Click the pages to turn, or leave them all unselected to turn every page.",
     "board": {"mode": "select", "effect": "rotate", "field": "pages", "degrees": "degrees"},
     "fields": [_f("degrees", "Rotation", "select", "90",
                   choices=[["90", "90° clockwise"], ["180", "180°"],
                            ["270", "90° counter-clockwise"]]),
                _f("pages", "Pages (blank = all)", "text", "", placeholder="all",
                   hint="updates as you click")]},
    {"id": "resize", "live": True, "group": "Layout", "label": "Resize", "files": "pdf",
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
    {"id": "autocrop", "group": "Layout", "label": "Trim white margins", "files": "pdf",
     "help": "Cut away the empty white border around the content of each page, e.g. to read "
             "a paper or a scan more comfortably on a small screen. Blank pages are left as "
             "they are; the cut-off margins stay in the file, hidden.",
     "fields": [_f("padding", "Space to keep around the content (mm)", "number", 3.5, min=0,
                   step=0.5),
                _f("same", "Crop every page the same", "check", False,
                   hint="Keeps the pages the same size: the crop fits the content of all of "
                        "them. Otherwise each page is trimmed on its own."),
                _f("pages", "Pages (blank = all)", "text", "", placeholder="all")]},
    {"id": "nup", "live": True, "group": "Layout", "label": "Pages per sheet", "files": "pdf",
     "fields": [_f("per_sheet", "Pages per sheet", "select", "2",
                   choices=[[str(n), str(n)] for n in (2, 4, 6, 8, 9)]),
                _f("size", "Sheet size", "select", "a4",
                   choices=[[p, p.upper() if p[0] == "a" else p.capitalize()] for p in PAPER])]},
    {"id": "booklet", "group": "Layout", "label": "Booklet", "files": "pdf",
     "help": "Print a folded booklet: two pages side by side on each sheet, in the order that "
             "comes out right once the sheets are folded in the middle. Print it on both sides, "
             "flipping on the short edge, then fold the stack and staple it on the fold. Blank "
             "pages are added at the end to fill the last sheet.",
     "fields": [_f("size", "Sheet size", "select", "",
                   choices=[["", "Twice the page size"]]
                   + [[p, p.upper() if p[0] == "a" else p.capitalize()]
                      for p in ("a4", "letter", "a3", "tabloid", "legal")],
                   hint="Used sideways, with two pages on each side. Twice the page size "
                        "keeps the pages as they are: A5 pages on A4 paper, A4 on A3.")]},
    {"id": "repair", "group": "Optimize", "label": "Repair & fast web view", "files": "pdf",
     "help": "Mend a damaged PDF (one that won't open, or shows errors), and optimize it so "
             "the first page shows before the whole file has downloaded.",
     "fields": [_f("linearize", "Optimize for fast web view", "check", True)]},
    {"id": "compress", "group": "Optimize", "label": "Compress", "files": "pdf",
     "fields": [_f("level", "Compression", "select", "medium",
                   choices=[["low", "Low — lossless"],
                            ["medium", "Medium — recommended"],
                            ["high", "High — smallest file"]],
                   hint="Low keeps every image as is. Medium reduces images to 150 DPI "
                        "(good for screen and office printing); High to 96 DPI."),
                _f("sharp_scans", "Keep scanned text sharp", "check", True,
                   hint="Scanned pages keep their text at full sharpness; only the paper "
                        "behind it is reduced.")]},
    {"id": "color", "group": "Optimize", "label": "Convert colours", "files": "pdf",
     "fields": [_f("to", "Convert to", "select", "gray",
                   choices=[["gray", "Grayscale"], ["rgb", "RGB (screen)"],
                            ["cmyk", "CMYK (print)"]]),
                _f("icc", "ICC profile (optional)", "file", None, accept=".icc,.icm")]},
    {"id": "ocr", "group": "Optimize", "label": "Make searchable (OCR)", "files": "pdf",
     "help": "The built-in engine reads English, most European languages, Chinese and "
             "Japanese as they are. For Russian, Ukrainian, Korean, Arabic, Persian, Hindi, Thai, "
             "Greek, Tamil or Telugu, enter its code (rus, ukr, kor, ara, fas, hin, tha, ell, tam, "
             "tel): its reading model is fetched once. Tesseract needs the codes of all languages.",
     "fields": [_f("engine", "Engine", "select", "auto",
                   choices=[["auto", "Automatic"], ["builtin", "Built-in"], ["tesseract", "Tesseract"]],
                   hint="Automatic: the built-in engine when it's installed, else Tesseract."),
                _f("language", "Language(s)", "text", "eng", placeholder="eng+rus"),
                _f("rotate", "Turn sideways and upside-down pages upright", "check", True),
                _f("deskew", "Straighten crooked scans", "check", False),
                _f("force", "Redo pages that already have text", "check", False)]},
    {"id": "clean", "group": "Optimize", "label": "Clean up scans", "files": "pdf",
     "help": "Remove blank pages and turn sideways or upside-down pages the right way up.",
     "fields": [_f("remove_blank", "Remove blank pages", "check", True),
                _f("sensitivity", "Blank page detection", "select", "medium",
                   choices=[["low", "Strict — only completely empty pages"],
                            ["medium", "Normal — allows a few specks"],
                            ["high", "Generous — also nearly empty pages"]]),
                _f("auto_rotate", "Turn pages upright", "check", False)]},
    {"id": "watermark", "live": True, "group": "Stamp", "label": "Watermark", "files": "pdf",
     "fields": [_f("text", "Text", "text", "CONFIDENTIAL", required=True),
                _f("opacity", "Opacity", "range", 0.15, min=0.05, max=1, step=0.05, unit="%"),
                _f("font_size", "Font size", "number", 60, min=4),
                _f("angle", "Angle", "range", 45, min=-90, max=90, step=5, unit="°"),
                _f("below", "Put under the content", "check", False),
                _f("pages", "Pages (blank = all)", "text", "", placeholder="all")]},
    {"id": "number", "live": True, "group": "Stamp", "label": "Page numbers", "files": "pdf",
     "fields": [_f("format", "Format", "text", "{n} / {total}",
                   hint="{n} = page number, {total} = page count"),
                _f("position", "Position", "select", "bottom-center",
                   choices=[[p, p.replace("-", " ").capitalize()] for p in
                            ("bottom-center", "bottom-right", "bottom-left",
                             "top-center", "top-right", "top-left")]),
                _f("start", "Start at", "number", 1),
                _f("font_size", "Font size", "number", 10, min=4)]},
    {"id": "flatten", "group": "Stamp", "label": "Flatten forms & comments", "files": "pdf", "action": "Flatten",
     "help": "Make filled-in forms and comments part of the page, so they look the same "
             "everywhere and can't be changed. Links keep working.",
     "fields": [_f("forms", "Form fields", "check", True),
                _f("comments", "Comments, highlights, drawings and signatures", "check", True)]},
    {"id": "form", "group": "Stamp", "label": "Add form fields", "files": "pdf", "panel": "form",
     "help": "Make a PDF fillable: Find fields picks out the lines, boxes and tick boxes of a "
             "printed form, and you can draw more where people should type, tick, choose or "
             "sign. It can then be filled in with any PDF reader, including the reader here.",
     "fields": [_f("detect", "Find the fields in each file", "check", False,
                   hint="For several copies of different forms: each file gets the fields "
                        "found in it, as well as any you drew."),
                _f("font_size", "Text size", "select", "0",
                   choices=[["0", "Fit the box"], ["8", "8 pt"], ["10", "10 pt"], ["12", "12 pt"],
                            ["14", "14 pt"]]),
                _f("border", "Draw a border around each field", "check", True),
                _f("required", "Make text fields required", "check", False),
                _f("fields", "", "hidden", "")]},
    {"id": "headers", "live": True, "group": "Stamp", "label": "Headers & footers", "files": "pdf",
     "help": "Add text at the top or bottom of every page: a title, the date, page numbers or "
             "Bates numbers (which continue from one file to the next).",
     "fields": [_f("top_left", "Top left"), _f("top_center", "Top centre"),
                _f("top_right", "Top right", "text", "", placeholder="{date}"),
                _f("bottom_left", "Bottom left", "text", "", placeholder="{file}"),
                _f("bottom_center", "Bottom centre", "text", "", placeholder="Page {n} of {total}"),
                _f("bottom_right", "Bottom right", "text", "", placeholder="{bates}",
                   hint="{n} page · {total} pages · {file} file name · {title} · {date} · "
                        "{bates} Bates number"),
                _f("bates_prefix", "Bates prefix", "text", "", placeholder="ABC"),
                _f("bates_start", "First Bates number", "number", 1, min=0),
                _f("bates_digits", "Bates digits", "number", 6, min=1, max=12),
                _f("font_size", "Font size", "number", 9, min=4),
                _f("margin", "Distance from edge (mm)", "number", 10, min=0),
                _f("pages", "Pages (blank = all)", "text", "", placeholder="all")]},
    {"id": "metadata", "group": "Stamp", "label": "Title & author", "files": "pdf",
     "fields": [_f("title", "Title"), _f("author", "Author"), _f("subject", "Subject"),
                _f("keywords", "Keywords")]},
    # -- comments --
    {"id": "comments", "group": "Stamp", "label": "Comments: summary & export", "files": "pdf",
     "help": "List the comments of PDFs (notes, highlights, stamps…, with the words they mark "
             "and the replies) as a PDF to read or print, or as a spreadsheet; or move "
             "comments between copies of a document with XFDF files (Acrobat reads and "
             "writes them).",
     "fields": [_f("mode", "Make", "select", "summary",
                   choices=[["summary", "A summary (PDF)"], ["csv", "A spreadsheet (CSV)"],
                            ["export", "A comments file to send (XFDF)"],
                            ["import", "Add comments from an XFDF file"]]),
                _f("xfdf", "Comments file (.xfdf)", "file", None, accept=".xfdf,.xml",
                   show_if={"mode": "import"})]},
    # -- end of comments --
    # -- file tools --
    {"id": "attach", "group": "Stamp", "label": "Attach files", "files": "pdf",
     "help": "Attach files to PDFs (a spreadsheet, the original Word file, photos), or take "
             "attached files away. Readers show them in their attachments panel.",
     "fields": [_f("attach", "Files to attach", "file", None, multiple=True),
                _f("remove", "Remove attached files", "text", "",
                   placeholder="names, separated by commas"),
                _f("remove_all", "Remove all the attached files first", "check", False)]},
    {"id": "encrypt", "group": "Security", "label": "Add password", "files": "pdf",
     "help": "Protect PDFs with a password to open them, and limit what people can do with "
             "them: print, copy or change.",
     "fields": [_f("new_password", "Password to open", "password", "",
                   hint="Leave it empty to let anyone open the PDF, with the limits below."),
                _f("printing", "Printing", "select", "high",
                   choices=[["high", "Allowed"], ["low", "Low quality only"],
                            ["none", "Not allowed"]]),
                _f("changes", "Changes", "select", "any",
                   choices=[[k, v[0].upper() + v[1:]] for k, v in filetools.CHANGES_TEXT.items()]),
                _f("copy", "Allow copying text and pictures", "check", True),
                _f("owner_password", "Permissions password", "password", "",
                   hint="Needed for the limits above, and different from the password to "
                        "open: it lifts the limits (and removes the protection).")]},
    {"id": "decrypt", "group": "Security", "label": "Remove password", "files": "pdf",
     "help": "Enter the current password in the box above the Run button.", "fields": []},
    {"id": "redact", "group": "Security", "label": "Redact", "files": "pdf", "panel": "redact", "action": "Black out",
     "help": "Permanently black out names, numbers or areas. What's under the black boxes "
             "is taken out of the file for good.",
     "fields": [_f("terms", "Words or phrases", "text", "", placeholder="John Smith, 555-1234",
                   hint="separate several with commas"),
                # (how the words match beside them; the kinds of numbers on a row of their own)
                _f("case_sensitive", "Match upper/lower case", "check", False),
                _f("whole_words", "Whole words only", "check", False),
                _f("emails", "All email addresses", "check", False),
                _f("phones", "All phone numbers", "check", False),
                _f("numbers", "Card, account and ID numbers", "check", False),
                # -- true redaction --
                _f("method", "How", "select", "remove",
                   choices=[["remove", "Remove what's under the boxes — the rest stays searchable"],
                            ["image", "Turn the pages into pictures — the most thorough"]],
                   hint="A page that can't be edited safely becomes a picture instead; the "
                        "notes say which."),
                # -- end of true redaction --
                _f("boxes", "", "hidden", "")]},
    {"id": "sanitize", "group": "Security", "label": "Remove hidden data", "files": "pdf", "action": "Remove hidden data",
     "help": "Remove information that travels unseen inside a PDF before you share it.",
     "fields": [_f("metadata", "Author, title, dates and other document details", "check", True),
                _f("javascript", "Scripts and actions that run programs", "check", True),
                _f("attachments", "Attached files", "check", True),
                _f("comments", "Comments, highlights and notes", "check", False)]},
    # -- digital ID signatures (certsign.py) --
    {"id": "sign-cert", "group": "Security", "label": "Digital ID", "files": "pdf",
     "action": "Sign with Digital ID",
     "help": "Sign with your Digital ID: a certificate file (.pfx or .p12), like Acrobat's "
             "“Sign with a digital ID”. Anyone can then check who signed and that nothing "
             "changed since. Sign last: any later change except more signatures breaks it. "
             "To draw or type your signature instead, open the file and choose Sign.",
     "fields": [_f("pfx", "Digital ID (.pfx or .p12 file)", "file", None, accept=".pfx,.p12"),
                _f("pfx_password", "Digital ID password", "password", ""),
                _f("reason", "Reason", "text", "", placeholder="I approve this document"),
                _f("location", "Location", "text", "", placeholder="City"),
                _f("appearance", "Signature", "select", "visible",
                   choices=[["visible", "Visible on a page"],
                            ["invisible", "Invisible (only in the signature list)"]]),
                _f("page", "Page", "text", "last", show_if={"appearance": "visible"},
                   placeholder="last", hint="a page number, or last"),
                _f("position", "Position", "select", "bottom-right",
                   show_if={"appearance": "visible"},
                   choices=[[p, p.replace("-", " ").capitalize()] for p in certsign.POSITIONS]),
                _f("size", "Size", "select", "medium", show_if={"appearance": "visible"},
                   choices=[["small", "Small"], ["medium", "Medium"], ["large", "Large"]]),
                _f("timestamp_url", "Timestamp server (optional)", "text", "",
                   placeholder="http://timestamp.digicert.com",
                   hint="A trusted clock vouches for the time of signing; needs the internet. "
                        "Leave empty to use this computer's clock.")]},
    {"id": "verify-sig", "group": "Security", "label": "Check signatures", "files": "pdf",
     "action": "Check",
     "help": "Check the digital signatures in PDFs: who signed, when, and whether the "
             "document was changed afterwards. You get a short report; nothing is changed.",
     "fields": [_f("trust", "Certificate you trust (optional)", "file", None,
                   accept=".cer,.crt,.pem,.der",
                   hint="e.g. your company's root certificate: signatures from it count as "
                        "verified")]},
    # -- end of digital ID signatures --
    {"id": "pdfa", "group": "Convert", "label": "PDF → PDF/A (archive)", "files": "pdf",
     "help": "Convert to PDF/A, the ISO standard for documents that must stay readable for "
             "decades: fonts and colour profile are embedded.",
     "fields": [_f("level", "Version", "select", "2",
                   choices=[["2", "PDF/A-2b — recommended"], ["1", "PDF/A-1b — oldest, strictest"],
                            ["3", "PDF/A-3b — allows attachments"]])]},
    {"id": "pdfa-check", "group": "Convert", "label": "Check PDF/A", "files": "pdf",
     "action": "Check",
     "help": "Check whether PDFs meet PDF/A, the archiving standard: what breaks it and on "
             "which page. You get a short report; nothing is changed. Uses veraPDF, the "
             "reference checker, when it's installed.",
     "fields": [_f("level", "Check against", "select", "",
                   choices=[["", "What the file says"]]
                   + [[lv, f"PDF/A-{lv}"] for lv in pdfacheck.LEVELS],
                   hint="A file that doesn't say is checked against PDF/A-2b."),
                _f("engine", "Checker", "select", "auto",
                   choices=[["auto", "veraPDF if installed"], ["builtin", "ZPDF's own checks"]],
                   hint="veraPDF (free, verapdf.org) checks every rule; ZPDF's own checks "
                        "cover the ones files most often break.")]},
    # -- accessibility --
    {"id": "tag", "group": "Convert", "label": "Make accessible (tag)", "files": "pdf",
     "help": "Tag PDFs for screen readers, like Acrobat's Autotag: headings, paragraphs, lists, "
             "tables, pictures and links, in reading order (columns too), with the language "
             "and title set. Running headers and page numbers are left out of what's read. "
             "The pages look the same.",
     "fields": [_f("lang", "Language", "text", "auto", placeholder="auto",
                   hint="auto guesses it from the text; or a code such as en, de, fr, es, it, "
                        "nl, pt-BR"),
                _f("title", "Title (optional)", "text", "",
                   hint="Shown in the window's title bar. Blank: the title the file has, else "
                        "its first heading."),
                _f("force", "Replace existing tags", "check", False,
                   hint="A file that is already tagged keeps its tags unless this is ticked.")]},
    {"id": "access-check", "group": "Convert", "label": "Check accessibility", "files": "pdf",
     "action": "Check",
     "help": "Check PDFs for what keeps screen readers from reading them, like Acrobat's "
             "Accessibility Checker (the basics of PDF/UA): tags, language, title, picture "
             "descriptions, tables, headings, links, form fields and more, with how to fix "
             "each. You get a short report; nothing is changed.",
     "fields": []},
    # -- end of accessibility --
    # Scan has a page of its own (gui.html's scan page); its work is under /api/scan/.
    {"id": "scan", "group": "Convert", "label": "Scan", "files": "scan",
     "help": "Turn paper into a searchable PDF: scan the pages with a scanner, or use photos "
             "of them taken with a phone.", "fields": []},
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
    {"id": "media", "group": "Convert", "label": "Pictures & attachments", "files": "pdf",
     "help": "Save the pictures inside PDFs (in their own format, each once) and any files "
             "attached to them, as a ZIP.",
     "fields": [_f("images", "Pictures", "check", True),
                _f("attachments", "Attached files", "check", True),
                _f("min_size", "Skip pictures smaller than (pixels)", "number", 16, min=1)]},
    {"id": "to-word", "group": "Convert", "label": "PDF → Word", "files": "pdf",
     "help": "Turn PDFs into editable Word documents (.docx).",
     "fields": [_f("engine", "Converter", "select", "auto",
                   choices=[["auto", "Best available (Microsoft Word if installed)"],
                            ["basic", "Built-in — headings, paragraphs, lists, tables and pictures"],
                            ["layout", "Exact layout — needs LibreOffice; text in boxes"],
                            ["word", "Microsoft Word only"]],
                   hint="Microsoft Word gives the most editable result. The built-in "
                        "converter keeps headings, paragraphs, lists, tables and pictures; "
                        "designed pages may not come out exactly as they look.")]},
    # PDF → Excel: the tables in a PDF (pdfmanager/tables.py)
    {"id": "to-excel", "group": "Convert", "label": "PDF → Excel", "files": "pdf",
     "help": "Put the tables in PDFs into an Excel workbook, with numbers as numbers, or into "
             "CSV files. Works on PDFs with text; scanned pages need OCR first.",
     "fields": [_f("pages", "Pages (blank = all)", "text", "", placeholder="all"),
                _f("layout", "Layout", "select", "sheet-per-table",
                   choices=[["sheet-per-table", "A sheet per table"],
                            ["one-sheet", "Everything on one sheet"]]),
                _f("format", "Format", "select", "xlsx",
                   choices=[["xlsx", "Excel workbook (.xlsx)"], ["csv", "CSV files (zip)"]],
                   hint="A table that continues on the next page is kept as one table.")]},
    # -- pptx and html --
    {"id": "to-pptx", "group": "Convert", "label": "PDF → PowerPoint", "files": "pdf",
     "help": "Turn PDFs into PowerPoint slides (.pptx), one slide per page. No PowerPoint "
             "needed.",
     "fields": [_f("mode", "Slides", "select", "editable",
                   choices=[["editable", "Editable text"], ["pictures", "Pictures of the pages"]],
                   hint="Editable text: the text becomes text boxes you can change, over the "
                        "page's pictures and drawings."),
                _f("pages", "Pages (blank = all)", "text", "", placeholder="all")]},
    {"id": "to-html", "group": "Convert", "label": "PDF → HTML", "files": "pdf",
     "help": "Turn PDFs into a web page (.html) that opens in any browser. Pictures are "
             "inside the file.",
     "fields": [_f("mode", "Layout", "select", "flowing",
                   choices=[["flowing", "Flowing text"], ["exact", "Exact layout"]],
                   hint="Flowing text fits any screen: headings, paragraphs, lists, tables and "
                        "pictures. Exact layout keeps each page as it looks."),
                _f("pages", "Pages (blank = all)", "text", "", placeholder="all")]},
    # -- end of pptx and html --
    # -- web pages --
    {"id": "web", "group": "Convert", "label": "Web page → PDF", "files": "web", "url_field": "url",
     "help": "Save a web page as a PDF, as the browser prints it (links keep working): type its "
             "address, or add saved web pages (.html, .mht). Uses Microsoft Edge or Chrome.",
     "fields": [_f("url", "Web address", "text", "", placeholder="https://example.com/page")]},
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
    {"id": "search", "group": "Folders", "label": "Search a folder", "files": None,
     "action": "Search", "preview": False,
     "help": "Find text in every PDF in a folder, or ask a question: \"how much do I owe\" "
             "finds the page that says \"balance due\". Click a result to open the page in "
             "the reader. Nothing is changed and nothing leaves this computer.",
     "fields": [_f("directory", "Folder", "text", "", required=True),
                _f("query", "Search for", "text", "", required=True,
                   placeholder="Words, or a question"),
                _f("how", "Find", "select", "best",
                   choices=[["best", "The best matches (understands a question)"],
                            ["exact", "These exact words"]],
                   hint="Questions are understood in English. Chinese is matched by its words."),
                _f("recursive", "Include sub-folders", "check", True),
                _f("case_sensitive", "Match upper/lower case", "check", False,
                   show_if={"how": "exact"}),
                _f("whole_words", "Whole words only", "check", False, show_if={"how": "exact"})]},
]
OPS = {op["id"]: op for op in OPERATIONS}

SUFFIX = {"form": "form", "media": "media", "headers": "headers", "recipe": "recipe", "redact": "redacted", "bookmarks": "bookmarks", "flatten": "flat", "sanitize": "clean", "pdfa": "pdfa", "clean": "clean", "extract": "extract", "delete": "edited", "reorder": "reordered",
          "insert-blank": "blanks", "booklet": "booklet", "autocrop": "trimmed",
          "reverse": "reversed", "rotate": "rotated", "resize": "resized", "crop": "cropped",
          "nup": "nup", "compress": "compressed", "color": "color", "ocr": "ocr",
          "watermark": "watermarked", "number": "numbered", "metadata": "edited",
          "encrypt": "protected", "decrypt": "unlocked"}
SUFFIX.update({"sign-cert": "signed", "verify-sig": "signatures"})  # digital ID signatures
SUFFIX["pdfa-check"] = "pdfa-check"
SUFFIX.update({"attach": "attached", "labels": "labelled", "repair": "repaired"})  # file tools
SUFFIX["comments"] = "comments"
SUFFIX["to-excel"] = "tables"
SUFFIX.update({"tag": "tagged", "access-check": "accessibility"})  # -- accessibility --


class UserError(Exception):
    pass


# Errors whose message is shown to the user as it is.
EXPECTED_ERRORS = (UserError, core.PdfManagerError, PageRangeError, ValueError,
                   FileNotFoundError, NotADirectoryError)


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
    elif op == "insert-blank":
        after, at_start = _pages(opts, "after"), _bool(opts.get("at_start"))
        if not after and not at_start:
            raise UserError("click the pages to put blank pages after, or type them")
        core.insert_blank_pages(src, out, after, _num(opts.get("count"), int, 1),
                                at_start=at_start, password=password)
    elif op == "booklet":
        core.booklet(src, out, opts.get("size") or None, password)
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
        core.compress(src, out, level=opts.get("level") or "medium", password=password,
                      sharp_scans=_bool(opts.get("sharp_scans", True)), notes=notes)
    elif op == "color":
        icc = extra_files.get("icc")
        core.convert_color(src, out, opts.get("to") or "gray", icc, password)
    elif op == "ocr":
        core.ocr(src, out, language=opts.get("language") or "eng",
                 force=_bool(opts.get("force")), deskew=_bool(opts.get("deskew")),
                 password=password, engine=opts.get("engine") or "auto",
                 rotate=_bool(opts.get("rotate", True)))
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
        found = redact.redact(src, out, terms=terms, patterns=patterns, boxes=boxes,
                              case_sensitive=_bool(opts.get("case_sensitive")),
                              whole_words=_bool(opts.get("whole_words")), password=password,
                              method=method)
        notes.append(f"{src.name}: " + (
            f"blacked out {sum(found.values())} area{'s' if sum(found.values()) != 1 else ''} "
            f"on page{'s' if len(found) != 1 else ''} {_pages_list(list(found))}"
            + _redact_extra(found) if found
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
        recipes.run(steps, src, out, password, notes, state=opts.setdefault("_state", {}))
    elif op == "form":
        try:
            raw = json.loads(opts.get("fields") or "{}")
            fields = [{**f, "page": int(page)} for page, found in raw.items() for f in found]
        except (ValueError, TypeError, AttributeError):
            raise UserError("the fields drawn on the pages are damaged; draw them again") from None
        if _bool(opts.get("detect")):
            found = formdetect.detect(src, password)
            drawn = [(f["page"], f["rect"]) for f in fields]
            fields += [{**f, "page": page} for page, items in found.items() for f in items
                       if not any(p == page and formdetect.overlap(f["rect"], r) > 0.3
                                  for p, r in drawn)]
        if not fields:
            raise UserError("no fields: use Find fields, or drag where people should type, "
                            "tick or sign" if not _bool(opts.get("detect")) else
                            "no fields were found in this file: it needs lines, boxes or tick "
                            "boxes to fill in (draw the fields instead)")
        if _bool(opts.get("required")):
            fields = [{**f, "required": f.get("type") in ("text", "multiline")} for f in fields]
        names = forms.add_fields(src, out, fields, border=_bool(opts.get("border")),
                                 font_size=_num(opts.get("font_size"), float, 0),
                                 password=password)
        notes.append(f"{src.name}: added {len(names)} field{'s' if len(names) != 1 else ''} "
                     f"({', '.join(names[:8])}{'…' if len(names) > 8 else ''})")
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
        out = out.with_suffix(".txt")
        found = certsign.verify(src, password, [extra_files["trust"]]
                                if extra_files.get("trust") else None)
        out.write_text(certsign.report(found, src.name), encoding="utf-8")
        notes.extend([f"{src.name}: no digital signatures"] if not found else
                     [f"{src.name}, {e['field']}: {e['summary']}" for e in found])
    elif op == "pdfa":
        level = opts.get("level") or "2"
        archive.to_pdfa(src, out, level=level, password=password, notes=notes)
        try:
            checked = pdfacheck.check(out, level=f"{level}b", engine="builtin")
            notes.append(f"{src.name}: checked: {pdfacheck.summary(checked)}")
        except Exception:  # the check is extra; the file is made either way
            pass
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
                n = comments.count(found)
                if mode == "csv":
                    out = out.with_name(f"{src.stem}_comments.csv")
                    comments.to_csv(found, out)
                else:
                    comments.summary_pdf(found, out, src.name)
            notes.append(f"{src.name}: {n} comment{'s' if n != 1 else ''}")
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
        return found["images"] + found["attachments"]
    elif op == "to-images":
        return core.pdf_to_images(src, out_dir / f"{src.stem}_images",
                                  _num(opts.get("dpi"), int, 150), opts.get("format") or "png",
                                  _pages(opts), password)
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
    # -- end of pptx and html --
    elif op == "from-office":
        out = out_dir / f"{src.stem}.pdf"
        _, program = office.office_to_pdf(src, out)
        notes.append(f"{src.name}: converted with {program}")
    elif op == "web":
        out = out_dir / f"{src.stem}.pdf"
        _, program = office.html_to_pdf(src, out)
        notes.append(f"{src.name}: printed with {program}")
    else:
        raise UserError(f"unknown operation {op!r}")
    return [out]


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
                         password=password)


def _to_excel(src, out_dir, opts, password, notes):
    """PDF → Excel: a workbook, or a CSV file per table (in a folder, so the
    download is a ZIP). A file without tables gets a note instead of
    stopping the other files of the batch."""
    from pdfmanager import tables

    layout = opts.get("layout") or "sheet-per-table"
    if layout not in tables.LAYOUTS:
        raise UserError(f"unknown layout {layout!r}")
    try:
        if (opts.get("format") or "xlsx") == "csv":
            made = tables.to_csv(src, out_dir / f"{src.stem}_tables", password, _pages(opts))
            outputs = made["files"]
        else:
            made = tables.to_excel(src, out_dir / f"{src.stem}.xlsx", password, _pages(opts),
                                   mode=layout)
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


def _protect(outputs, password, op):
    """PDFs made from a password-protected file get the same password."""
    # A signed PDF keeps its password inside the signed file.
    if password and op not in ("decrypt", "encrypt", "pdfa", "media", "sign-cert"):
        for path in outputs:
            if path.suffix.lower() == ".pdf":
                core.encrypt(path, path, password)


def _was_protected(path, password):
    """Whether an input needed the typed password: only results made from
    such a file are protected again. The page sends the one password box
    with every run, also for the files in the batch that were never locked."""
    if not password or path.suffix.lower() != ".pdf":
        return False
    try:
        return PdfReader(str(path)).is_encrypted
    except Exception:
        return False  # not a readable PDF: the tool itself reports that


def _flatten_outputs(outputs, out_dir):
    """Move per-input outputs into ``out_dir``, renaming on clashes."""
    taken, flat = set(), []
    for sub, path in outputs:
        target = organize._unique(out_dir / path.relative_to(sub), taken)
        target.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target)
        flat.append(target)
    return flat


def _damaged_errors():
    """The errors the PDF libraries raise for a file they can't make sense
    of: a damaged file is the file's problem, told plainly, not a crash."""
    import pikepdf
    import pypdfium2
    from pdfminer.psexceptions import PSException
    from pdfplumber.utils.exceptions import MalformedPDFException, PdfminerException
    from PIL import Image
    from pypdf.errors import PyPdfError

    return (PyPdfError, pikepdf.PdfError, pypdfium2.PdfiumError, Image.DecompressionBombError,
            PSException, PdfminerException, MalformedPDFException)


def _plainly(exc):
    from PIL import Image

    if isinstance(exc, Image.DecompressionBombError):
        return UserError("a page is too large to work with (it would make an image of hundreds of "
                         "millions of pixels)")
    reason = " ".join(str(exc).split())[:160]
    return UserError("the PDF is damaged or unusual and couldn't be processed"
                     + (f" ({reason})" if reason else ""))


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


def _rebuildable(exc):
    try:
        return str(type(exc)("x")) == "x"
    except Exception:
        return False


def _tidy(message, work):
    """``message`` naming files, not the app's working folder: the uploads
    sit in <work>/in/<n>/<name> and the results in <work>/out/..."""
    if not work:
        return message
    for folder in {str(Path(work)), str(Path(work).resolve())}:
        message = re.sub(re.escape(folder) + r"[\\/](?:in[\\/]o?\d+|out)[\\/]", "", message)
        message = message.replace(folder, "")
    return message


def _needs_mending(path, password):
    """Whether ``path`` is damaged in a way qpdf mends and pypdf or pdfium
    don't: qpdf had to rebuild its cross-reference table (a file cut short,
    a download that stopped, a wrong index of objects), and pypdf or pdfium
    can't open it, lose its trailer or count other pages. A file all three
    read alike is left as it is."""
    import pikepdf

    try:
        with pikepdf.open(str(path), password=password or "") as pdf:
            if not any("reconstruct cross-reference" in str(w) for w in pdf.get_warnings()):
                return False
            if "/Perms" in pdf.Root or _file_mentions(path, b"/ByteRange"):
                return False  # signed: rewriting it would break the signatures
            pages = len(pdf.pages)
    except Exception:  # not readable at all, or locked: the tool says so itself
        return False
    import pypdfium2 as pdfium

    try:
        reader = core.open_pdf(path, password)
        if "/Size" not in reader.trailer or len(reader.pages) != pages:
            return True
        with core.PDFIUM_LOCK:
            doc = pdfium.PdfDocument(str(path), password=password)
            try:
                return len(doc) != pages
            finally:
                doc.close()
    except Exception:  # noqa: BLE001 (pypdf or pdfium can't read it)
        return True


def _mend_inputs(op, files, password, work, notes):
    """``files``, each damaged one replaced by a copy mended as Repair does,
    with a note saying so (Repair itself mends the file as it is)."""
    if op == "repair" or not work or OPS[op].get("files") not in ("pdf", "pdf2"):
        return files
    import pikepdf

    mended = []
    for index, path in enumerate(files):
        path = Path(path)
        if path.suffix.lower() == ".pdf" and _needs_mending(path, password):
            target = Path(work) / "mended" / f"{index:03d}" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with pikepdf.open(str(path), password=password or "") as pdf:
                    core.keep_size(pdf).save(target, encryption=pdf.is_encrypted or None)  # (still locked if it was)
                notes.append(f"{path.name} was damaged; it was mended first, as Repair does")
                path = target
            except Exception:  # noqa: BLE001 (it couldn't be mended: the tool tries the file as it is)
                pass
        mended.append(path)
    return mended


def _check_pages(op, files, password):
    """Each PDF has a page to work on (Repair is for files that don't open)."""
    if op == "repair" or OPS[op].get("files") not in ("pdf", "pdf2"):
        return
    for path in files:
        if Path(path).suffix.lower() == ".pdf" and not len(core.open_pdf(path, password).pages):
            raise UserError(f"{Path(path).name} has no pages")


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
        summary = compare.compare(files[0], files[1], out, password, password,
                                  all_pages=_bool(opts.get("all_pages")))
        changed = [c for c in summary["changes"] if c["kind"] != "same"]
        notes.append(f"{len(changed)} page{'s' if len(changed) != 1 else ''} with differences"
                     if changed else "No differences found.")
        outputs = [out]
    elif op == "web" and not files:  # a web address
        address = opts["url"].strip()
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
    if len(outputs) == 1 and op != "media" and (outputs[0].suffix != ".csv" or op == "comments"):  # CSV tables: a ZIP
        path = outputs[0]
        ctype = {".txt": "text/plain; charset=utf-8",
                 ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 ".xfdf": "application/vnd.adobe.xfdf", ".csv": "text/csv; charset=utf-8",
                 # -- pptx and html --
                 ".pptx": "application/vnd.openxmlformats-officedocument.presentationml."
                          "presentation",
                 ".html": "text/html; charset=utf-8",
                 # -- end of pptx and html --
                 }.get(path.suffix, "application/pdf")
        return path.name, path.read_bytes(), ctype
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in outputs:
            zf.write(path, path.relative_to(out_dir))
    name = f"{files[0].stem}_{SUFFIX.get(op, op)}.zip" if len(files) == 1 else f"pdfm_{op}.zip"
    return name, buf.getvalue(), "application/zip"


def default_options(op):
    """A tool's options as its form starts out (the right-click actions and
    the watch folders use them)."""
    opts = {}
    for field in OPS[op]["fields"]:
        value = field.get("default")
        if value is None or field["kind"] in ("file", "hidden"):
            continue
        opts[field["key"]] = ("true" if value else "false") if isinstance(value, bool) else str(value)
    return opts


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


def _shell_menu_available():
    """The right-click actions run the installed app's Python: Windows, and
    a copy installed with the installer (or pip)."""
    return sys.platform == "win32" and update.how_installed() == "pip"


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
        return search.search(directory, opts.get("query") or "", recursive,
                             _bool(opts.get("case_sensitive")), _bool(opts.get("whole_words")),
                             how="exact" if opts.get("how") == "exact" else "best")
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


# Tools whose result can be seen before running them: page 1 of it, redrawn
# as the options change.
PREVIEW_OPS = {"watermark", "number", "headers", "resize", "nup"}
PREVIEW_PAGES = 40  # longer documents are previewed from their first pages only


def preview_operation(op, data, filename, opts, password=None, width=960):
    """See _preview_operation; a damaged file gives a plain message, not a crash."""
    try:
        return _preview_operation(op, data, filename, opts, password, width)
    except _damaged_errors() as exc:
        raise _plainly(exc) from None


def _preview_operation(op, data, filename, opts, password=None, width=960):
    """What ``op`` makes of the first page(s) of a PDF (``data``): page 1 of
    the result as PNG bytes, ``width`` pixels wide."""
    if op not in PREVIEW_OPS:
        raise UserError(f"no preview for {op!r}")
    import pypdfium2 as pdfium

    with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
        tmp = Path(tmp)
        (tmp / "in").mkdir()
        src = tmp / "in" / _safe_name(filename or "file.pdf")
        src.write_bytes(data)
        if len(core.open_pdf(src, password).pages) > PREVIEW_PAGES:
            (tmp / "first").mkdir()
            first = tmp / "first" / src.name
            core.select_pages(src, first, "1-9", password)  # enough for any sheet of pages
            src = first
        (tmp / "work").mkdir()
        _, result, _ = run_operation(op, [src], opts, password, {}, tmp / "work", [])
        with core.PDFIUM_LOCK:
            doc = pdfium.PdfDocument(result, password=password)
            try:
                page = core.show_whole_page(doc[0])
                image = page.render(scale=width / max(1.0, page.get_width()), draw_annots=True).to_pil()
                page.close()
            finally:
                doc.close()
    out = io.BytesIO()
    image.convert("RGB").save(out, "PNG", optimize=False)
    return out.getvalue()


def inspect_pdf(path, password=None):
    """See _inspect_pdf; a damaged file gives a plain message, not a crash."""
    try:
        return _inspect_pdf(path, password)
    except _damaged_errors() as exc:
        raise _plainly(exc) from None


def _inspect_pdf(path, password=None):
    """Page count, size label and a first-page thumbnail (data URL) for the
    file cards and the result preview."""
    try:
        reader = core.open_pdf(path, password)
    except core.PdfManagerError as exc:
        locked = "encrypted" in str(exc) or "password" in str(exc)
        said = str(exc).replace(str(path), Path(path).name)  # the file's name, not where it's kept
        return {"pages": None, "locked": locked, "error": None if locked else said}
    info = {"pages": len(reader.pages), "locked": False, "error": None, "thumb": None}
    try:
        import pypdfium2 as pdfium

        with core.PDFIUM_LOCK:
            doc = pdfium.PdfDocument(str(path), password=password)
            try:
                page = core.show_whole_page(doc[0])
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

    def __init__(self, keep=5, max_bytes=256 * 1024 * 1024):
        self.keep = keep
        self.max_bytes = max_bytes
        self.items = {}
        self.lock = threading.Lock()

    def add(self, name, data):
        key = secrets.token_urlsafe(12)
        with self.lock:
            self.items[key] = (name, data)
            # Old results go when there are too many or they add up to too
            # much memory (a few big merges would otherwise stay resident
            # for the whole session); the newest one is always kept.
            while len(self.items) > 1 and (
                    len(self.items) > self.keep
                    or sum(len(d) for _, d in self.items.values()) > self.max_bytes):
                self.items.pop(next(iter(self.items)))
        return key

    def get(self, key):
        with self.lock:
            return self.items.get(key)


class WorkFolders:
    """The temporary folders of runs in progress (the uploads and partial
    results of a job). A run removes its own folder when it ends; the ones
    still here when the app closes belong to jobs cut short, and are
    removed then rather than left in the temporary folder."""

    def __init__(self):
        self.items = set()
        self.lock = threading.Lock()

    def make(self):
        path = Path(tempfile.mkdtemp(prefix="pdfm-"))
        with self.lock:
            self.items.add(path)
        return path

    def remove(self, path):
        with self.lock:
            self.items.discard(path)
        shutil.rmtree(path, ignore_errors=True)

    def clear(self):
        with self.lock:
            left = list(self.items)
        for path in left:
            self.remove(path)


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

    def knows(self, path):
        """Whether ``path`` is a file the app has open or has saved: only
        those may be shown in the file manager."""
        try:
            path = Path(path).expanduser().resolve()
        except (OSError, ValueError, TypeError):
            return False
        with self.lock:
            return path in self.items.values()


class UnsavedWork:
    """What the app's pages say is unsaved: the name of each reader tab with
    the dot (changes not saved to a file, or a tool's result not saved
    anywhere). Kept here so closing the desktop window can ask without
    asking the page (see may_close): from pywebview's closing event the
    page can't be asked (evaluate_js there can hang the window on Windows)."""

    MAX = 200

    def __init__(self):
        self._lock = threading.Lock()
        self._pages = {}  # page id -> names

    def said(self, page, names):
        names = [str(n)[:260] for n in names][:self.MAX]
        with self._lock:
            if names:
                self._pages[str(page)] = names
            else:
                self._pages.pop(str(page), None)

    def gone(self, page):
        """The page was closed or reloaded."""
        with self._lock:
            self._pages.pop(str(page), None)

    def names(self):
        with self._lock:
            return [name for names in self._pages.values() for name in names]


class SavedOver:
    """What a file held before Save wrote over it, for Undo: the last few,
    in memory, each up to LIMIT bytes (bigger files get no Undo)."""

    LIMIT = 64 * 2**20
    KEEP = 4

    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()

    def add(self, path, data, stamp):
        """Keep ``data`` (what ``path`` held); ``stamp`` is the file's stamp
        after the save, so Undo can tell whether it has changed since."""
        if data is None or len(data) > self.LIMIT:
            return None
        key = secrets.token_urlsafe(9)
        with self.lock:
            self.items[key] = (Path(path), data, stamp)
            while len(self.items) > self.KEEP:
                self.items.pop(next(iter(self.items)))
        return key

    def pop(self, key):
        with self.lock:
            return self.items.pop(key, None)


class Events:
    """Messages for the app's pages, numbered; pages ask for the ones after
    the last they've seen and wait for new ones."""

    def __init__(self):
        self.items = []
        self.cond = threading.Condition()

    def push(self, event):
        with self.cond:
            self.items.append(event)
            self.cond.notify_all()

    def wait(self, after, timeout=20):
        with self.cond:
            if len(self.items) <= after:
                self.cond.wait(timeout)
            return self.items[after:], len(self.items)


class RecentFiles:
    """PDFs recently opened in the reader from this computer, newest first,
    kept in recent.json in the app's data folder."""

    LIMIT = 30
    lock = threading.Lock()

    @staticmethod
    def _file():
        return data_dir() / "recent.json"

    @classmethod
    def load(cls):
        try:
            items = json.loads(cls._file().read_text(encoding="utf-8"))
            return [i for i in items if isinstance(i, dict) and i.get("path")]
        except (OSError, ValueError):
            return []

    @classmethod
    def _save(cls, items):
        try:
            write_private(cls._file(), json.dumps(items[:cls.LIMIT], indent=1))
        except OSError:
            pass  # a recent-files list isn't worth failing over

    @staticmethod
    def _stamp(path):
        try:
            st = Path(path).stat()
            return [st.st_size, st.st_mtime_ns]
        except OSError:
            return None

    @classmethod
    def add(cls, path):
        """To the top of the list: opened now. A file the reader couldn't
        open last time, unchanged since, stays where it is (it may open
        now; see failed())."""
        path = str(Path(path).resolve())
        with cls.lock:
            items = cls.load()
            had = next((i for i in items if os.path.normcase(i["path"]) == os.path.normcase(path)), None)
            if had and had.get("failed") and had["failed"] == cls._stamp(path):
                return
            items = [i for i in items if i is not had]
            items.insert(0, {"path": path, "opened": time.time(), "before": had.get("opened") if had else 0})
            cls._save(items)

    @classmethod
    def failed(cls, path):
        """The reader couldn't open the file: say so in Recent (until the
        file changes) and put it back where it was before this open."""
        path = str(Path(path).resolve())
        with cls.lock:
            items = cls.load()
            had = next((i for i in items if os.path.normcase(i["path"]) == os.path.normcase(path)), None)
            if had is None:
                had = {"path": path, "before": 0}
                items.append(had)
            had["failed"] = cls._stamp(path)
            if "before" in had:
                had["opened"] = had.pop("before") or 0
            items.sort(key=lambda i: i.get("opened") or 0, reverse=True)
            cls._save(items)

    @classmethod
    def is_failed(cls, item):
        return bool(item.get("failed")) and item["failed"] == cls._stamp(item["path"])

    @classmethod
    def forget(cls, path=None):
        """Drop one file (or all of them); returns the entries dropped, for restore()."""
        with cls.lock:
            items = cls.load()
            cls._save([] if path is None else [i for i in items if i["path"] != path])
            return items if path is None else [i for i in items if i["path"] == path]

    @classmethod
    def restore(cls, entries):
        """Put entries that forget() dropped back, in their places (newest first)."""
        good = [{"path": str(e["path"]), "opened": float(e.get("opened") or 0)}
                for e in entries if isinstance(e, dict) and e.get("path")]
        with cls.lock:
            items = cls.load()
            have = {os.path.normcase(i["path"]) for i in items}
            items += [e for e in good if os.path.normcase(e["path"]) not in have]
            items.sort(key=lambda i: i.get("opened") or 0, reverse=True)
            cls._save(items)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

STATIC_TYPES = {
    ".mjs": "text/javascript", ".js": "text/javascript", ".css": "text/css",
    ".svg": "image/svg+xml", ".gif": "image/gif", ".png": "image/png",
    ".wasm": "application/wasm", ".json": "application/json",
    ".woff2": "font/woff2",  # the interface's fonts (static/fonts)
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

_PARAM = re.compile(r';\s*([\w*-]+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^;\s]*))')


def _parse_multipart(content_type, body):
    """Return (fields, files) where files is a list of (field name, filename, bytes).

    A direct split of the browser's multipart/form-data body: the email
    parser took seconds and many times the upload's size in memory (5.6 s
    and 2.4 GB for a 277 MB PDF). Uploaded files come back as read-only
    views of ``body``, so they cost no copy."""
    match = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', content_type or "")
    if not content_type.lower().startswith("multipart/") or not match:
        raise UserError("expected a form upload")
    delimiter = b"--" + (match.group(1) or match.group(2)).encode("latin-1")
    view = memoryview(body).toreadonly()
    fields, files = {}, []
    start = body.find(delimiter)
    if start < 0:
        raise UserError("expected a form upload")
    while True:
        start += len(delimiter)
        if body[start:start + 2] == b"--":  # the closing delimiter
            break
        head_end = body.find(b"\r\n\r\n", start)
        end = body.find(b"\r\n" + delimiter, head_end + 4 if head_end >= 0 else start)
        if head_end < 0 or end < 0:
            raise UserError("the upload was cut short; try again")
        headers = {}
        for line in body[start:head_end].decode("utf-8", "replace").split("\r\n"):
            key, sep, value = line.partition(":")
            if sep:
                headers[key.strip().lower()] = value.strip()
        params = {}
        for key, quoted, plain in _PARAM.findall(";" + headers.get("content-disposition", "").partition(";")[2]):
            value = re.sub(r"\\(.)", r"\1", quoted) if quoted else plain
            # Browsers write ", CR and LF in names as %22, %0D and %0A.
            params[key.lower()] = re.sub(r"%(22|0D|0A)", lambda m: chr(int(m.group(1), 16)), value,
                                         flags=re.IGNORECASE)
        if "filename*" in params and "'" in params["filename*"]:  # RFC 5987: UTF-8''name
            charset, _, encoded = params["filename*"].partition("'")
            params["filename"] = urllib.parse.unquote(encoded.partition("'")[2],
                                                      encoding=charset or "utf-8", errors="replace")
        data = view[head_end + 4:end]
        if "filename" in params:
            files.append((params.get("name"), params["filename"], data))
        else:
            fields[params.get("name")] = bytes(data).decode("utf-8")
        start = end + 2
    return fields, files


def _file_stamp(path):
    """Changes whenever the file does (its time and size)."""
    info = Path(path).stat()
    return f"{info.st_mtime_ns}-{info.st_size}"


def _byte_range(header, size):
    """(first, last) byte of a one-range ``Range`` header for a file of
    ``size`` bytes; None if the range is outside the file or not one we serve."""
    match = re.fullmatch(r"\s*bytes=(\d*)-(\d*)\s*", header or "")
    if not match or not (match.group(1) or match.group(2)) or size == 0:
        return None
    if match.group(1):
        start = int(match.group(1))
        end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
    else:  # the last n bytes
        start, end = max(0, size - int(match.group(2))), size - 1
    return (start, end) if start <= end and start < size else None


def _file_mentions(path, needle):
    """Whether the bytes ``needle`` occur in the file (read in pieces, so a
    big file isn't held in memory)."""
    keep = len(needle) - 1
    tail = b""
    try:
        with Path(path).open("rb") as f:
            while True:
                chunk = f.read(1 << 22)
                if not chunk:
                    return False
                if needle in chunk or needle in tail + chunk[:keep]:
                    return True
                tail = chunk[-keep:] if keep else b""
    except OSError:
        return False


def _safe_name(name):
    name = Path(name.replace("\\", "/")).name  # drop any path the browser sent
    return re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .") or "file"


def _download_headers(name):
    """Content-Disposition and X-Pdfm-Filename for a file called ``name``.
    HTTP headers only carry latin-1, and names like "John’s report.pdf"
    or "Отчёт.pdf" don't fit: the real name goes percent-encoded (the
    filename*= form of RFC 6266; the page decodes X-Pdfm-Filename with
    decodeURIComponent) next to a plain ASCII stand-in for old clients."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    plain = re.sub(r'[\\"\x00-\x1f]', "", plain).strip()
    if not plain or plain.startswith("."):  # nothing of the name survived
        plain = "file" + Path(name).suffix
    encoded = urllib.parse.quote(name, safe="")
    return {"Content-Disposition": f'attachment; filename="{plain}"; filename*=UTF-8\'\'{encoded}',
            "X-Pdfm-Filename": encoded}


def _log(message):
    """What the app does around starting and closing, in its log (see
    applog.py; names of files are scrubbed out there)."""
    applog.log.info(message)


def _thread_stacks():
    import traceback

    names = {t.ident: t.name for t in threading.enumerate()}
    return "\n".join(f"--- {names.get(ident, ident)}\n" + "".join(traceback.format_stack(frame))
                     for ident, frame in sys._current_frames().items())


class Handler(BaseHTTPRequestHandler):
    server_version = f"pdfm/{__version__}"
    protocol_version = "HTTP/1.1"  # keep connections open between requests
    token = ""
    allowed_hosts = set()
    results = None  # ResultCache
    jobs = None  # progress.JobStore
    pages = None  # open pages: id -> when last heard from
    events = None  # Events for the pages
    instance_secret = None  # see forward_to_running_app
    instance_mode = None
    instance_extra = None
    bring_to_front = None
    quit_app = None  # closes the pywebview window (to restart after an update)
    quit_requested = False  # makes wait_until_closed return
    unsaved = None  # UnsavedWork: what closing the desktop window asks about
    opened = None  # OpenFiles
    saved_over = None  # SavedOver: Undo after Save wrote over a file
    scans = None  # scanpdf.ScanBoxes: the pages of each Scan page under way
    work = None  # WorkFolders
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
        self._start(status, ctype, len(body), headers, cache)
        self.wfile.write(body)

    def _start(self, status, ctype, length, headers=None, cache=False):
        """The status line and headers of an answer ``length`` bytes long."""
        # Headers can only carry latin-1 (see _download_headers): check them
        # before the status line is written, so a bad one can't leave a
        # half-sent answer that a second status line then gets appended to.
        for k, v in (headers or {}).items():
            try:
                f"{k}: {v}".encode("latin-1")
            except UnicodeEncodeError:
                raise RuntimeError(f"the {k} header can't be sent as it is") from None
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        # Static files may be cached but are checked (cheaply, by ETag) on every
        # load, so an updated app never runs old code.
        self.send_header("Cache-Control", "no-cache" if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _error(self, status, message):
        # The request body may not have been read; don't reuse the connection.
        self.close_connection = True
        self._send(status, {"error": message})

    def _host_ok(self):
        return self.headers.get("Host", "") in self.allowed_hosts

    def _token_ok(self):
        ok = secrets.compare_digest(self.headers.get("X-Pdfm-Token", ""), self.token)
        if ok:
            now = time.monotonic()
            type(self).last_seen = now  # a page is still open
            page = self.headers.get("X-Pdfm-Page", "")[:64]
            if page and self.pages is not None:
                self.pages[page] = now
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
        if path.startswith("/api/job/"):
            return self._job(path, "GET")
        if path.startswith("/api/facts/"):
            target = self.opened.get(path[len("/api/facts/"):]) if self.opened is not None else None
            if target is None or not target.is_file():
                return self._error(HTTPStatus.NOT_FOUND, "that file is no longer available")
            found = filefacts.remembered(target)
            made_by = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("made_by", [None])[0]
            return self._send(HTTPStatus.OK, {**found, "suggestions": filefacts.suggestions(found, made_by)})
        if path == "/api/recent":
            return self._recent()
        if path.startswith("/api/scan/"):
            try:
                return self._scan_get(path[len("/api/scan/"):])
            except EXPECTED_ERRORS as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        if path == "/api/events":
            return self._events()
        if path.startswith("/api/action/"):
            return self._action(path[len("/api/action/"):])
        if path == "/api/watch":
            return self._watch_list()
        if path == "/api/watch/news":
            query = urllib.parse.parse_qs(self.path.partition("?")[2])
            try:
                after = int(query.get("after", ["0"])[0])
            except ValueError:
                after = 0
            news, last = self.watcher.news_after(after)
            # A result can be opened in the reader from its toast.
            news = [{**item, "id": self.opened.add(item["result"])
                     if item["result"] and Path(item["result"]).is_file() else None} for item in news]
            return self._send(HTTPStatus.OK, {"news": news, "last": last})
        if path == "/api/shellmenu":
            from pdfmanager import winsetup

            available = _shell_menu_available()
            return self._send(HTTPStatus.OK, {"available": available,
                                              "on": available and winsetup.context_menu_registered()})
        if path == "/api/settings":
            return self._send(HTTPStatus.OK, update.public_settings())
        if path == "/api/update":
            return self._send(HTTPStatus.OK, {**update.check(), "can_restart": self._can_restart()})
        if path == "/api/diagnostics":
            return self._send(HTTPStatus.OK, applog.diagnostics(), "text/plain; charset=utf-8")
        if path == "/api/licence":
            from pdfmanager import notices

            return self._send(HTTPStatus.OK, notices.licence_text(), "text/plain; charset=utf-8")
        if path == "/api/notices":
            from pdfmanager import notices

            return self._send(HTTPStatus.OK, notices.third_party_notices(), "text/plain; charset=utf-8")
        if path == "/api/debug/stacks" and os.environ.get("PDFM_LOG"):
            return self._send(HTTPStatus.OK, _thread_stacks(), "text/plain; charset=utf-8")
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
        """A file opened from this computer. Big files are read in pieces
        (HTTP Range): the reader shows the first pages without waiting for
        the whole file."""
        path = self.opened.get(key) if self.opened is not None else None
        if path is None or not path.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "that file is no longer available")
        size = path.stat().st_size
        # The path is percent-encoded like the name: a folder called
        # "Документы" would otherwise be unsendable.
        headers = {"X-Pdfm-Filename": _download_headers(path.name)["X-Pdfm-Filename"],
                   "X-Pdfm-Path": urllib.parse.quote(str(path), safe=""),
                   "X-Pdfm-Stamp": _file_stamp(path), "Accept-Ranges": "bytes"}
        wanted = self.headers.get("Range")
        span = _byte_range(wanted, size) if wanted else (0, size - 1)
        if span is None:
            return self._send(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"", "application/pdf",
                              {**headers, "Content-Range": f"bytes */{size}"})
        start, end = span
        if start == 0:
            RecentFiles.add(path)
        if wanted:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        self._start(HTTPStatus.PARTIAL_CONTENT if wanted else HTTPStatus.OK, "application/pdf",
                    end - start + 1, headers)
        # The file is open only while a piece is read, never while it's being
        # sent: on Windows an open file can't be replaced (Save) or deleted,
        # and the reader may act on it as soon as the last byte arrives.
        at, left = start, end - start + 1
        while left > 0:
            with path.open("rb") as f:
                f.seek(at)
                chunk = f.read(min(left, 1 << 20))
            if not chunk:
                break
            self.wfile.write(chunk)
            at += len(chunk)
            left -= len(chunk)

    def _scales(self, body):
        """The drawing scales stored in a PDF (see viewports.py): of a file
        opened from this computer (field "id") or of an uploaded copy."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        password = fields.get("password") or None
        if fields.get("id"):
            path = self.opened.get(fields["id"]) if self.opened is not None else None
            if path is None:
                raise UserError("that file is no longer available")
            return self._send(HTTPStatus.OK, {"pages": viewports.stored_scales(path, password)})
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = Path(tmp) / "file.pdf"
            path.write_bytes(uploads[0][2])
            return self._send(HTTPStatus.OK, {"pages": viewports.stored_scales(path, password)})

    def _save_in_place(self, key, body):
        """Write the reader's copy of an opened file over the file itself:
        to a temporary file next to it first, then swapped in, so a failure
        halfway never leaves a broken file. If another program changed the
        file since it was opened, the answer is 409 unless X-Pdfm-Force."""
        path = self.opened.get(key) if self.opened is not None else None
        if path is None:
            raise UserError("this file can't be saved in place; use Save as")
        if b"%PDF" not in body[:1024]:
            raise UserError("not a PDF")
        expected = self.headers.get("X-Pdfm-Stamp", "")
        if (path.exists() and expected and _file_stamp(path) != expected
                and self.headers.get("X-Pdfm-Force") != "1"):
            return self._send(HTTPStatus.CONFLICT, {"error": f"{path.name} was changed by another "
                                                             "program", "changed": True})
        # What the file held, for Undo (not for big files: kept in memory).
        before = None
        try:
            if path.stat().st_size <= SavedOver.LIMIT:
                before = path.read_bytes()
        except OSError:
            pass
        _replace_file(path, body)
        RecentFiles.add(path)
        stamp = _file_stamp(path)
        undo = self.saved_over.add(path, before, stamp) if self.saved_over is not None else None
        return self._send(HTTPStatus.OK, {"path": str(path), "stamp": stamp, "undo": undo})

    def _unsave(self, data):
        """Put back what a file held before Save wrote over it (the "undo"
        that save gave). Refused when the file has changed since."""
        item = self.saved_over.pop(str(data.get("undo", ""))) if self.saved_over is not None else None
        if item is None:
            raise UserError("that can no longer be undone")
        path, before, stamp = item
        if not path.exists() or _file_stamp(path) != stamp:
            raise UserError(f"{path.name} has changed since it was saved, so it was left as it is")
        _replace_file(path, before)
        return self._send(HTTPStatus.OK, {"path": str(path), "stamp": _file_stamp(path)})

    def _reveal(self, data):
        """Show a file in Explorer / Finder / the file manager: only a file
        the app has open or has saved, never any path the page names."""
        target = data.get("path")
        if data.get("id") and self.opened is not None:
            target = self.opened.get(str(data["id"]))
        if not target or self.opened is None or not self.opened.knows(target):
            return self._error(HTTPStatus.FORBIDDEN, "not a file ZPDF has open")
        if not Path(target).exists():
            raise UserError("that file is no longer there")
        reveal_in_file_manager(Path(target).resolve())
        return self._send(HTTPStatus.OK, {"ok": True})

    def _recent(self):
        """Recently opened files that still exist, each with an id the
        reader can open."""
        files = []
        for item in RecentFiles.load():
            path = Path(item["path"])
            if path.is_file() and self.opened is not None:
                files.append({"id": self.opened.add(path), "name": path.name, "path": str(path),
                              "folder": str(path.parent), "opened": item.get("opened"),
                              "facts": filefacts.remembered(path), "failed": RecentFiles.is_failed(item)})
        return self._send(HTTPStatus.OK, {"files": files})

    def do_POST(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad host")
        if self.path.split("?", 1)[0] == "/api/bye":
            return self._bye()
        if self.path.split("?", 1)[0] == "/api/instance":
            length = int(self.headers.get("Content-Length") or 0)
            if length > 1_000_000:
                return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too large")
            try:
                return self._instance(self.rfile.read(length))
            except ValueError:
                return self._error(HTTPStatus.BAD_REQUEST, "bad request")
        if not self._token_ok():
            return self._error(HTTPStatus.FORBIDDEN, "missing or wrong token")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                               f"upload too large ({MAX_UPLOAD // 2**20} MB max)")
        body = self.rfile.read(length)
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/ping":  # sent regularly while the page is open
                return self._send(HTTPStatus.OK, {"ok": True})
            if path == "/api/unsaved":  # the reader's unsaved tabs changed (see UnsavedWork)
                return self._unsaved(body)
            if path == "/api/run":
                return self._run(body)
            if path == "/api/inspect":
                return self._inspect(body)
            if path == "/api/facts":
                return self._facts(body)
            if path == "/api/preview":
                fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
                if not uploads:
                    raise UserError("no file")
                op = fields.pop("op", "")
                password = fields.pop("password", "") or None
                png = preview_operation(op, uploads[0][2], uploads[0][1], fields, password)
                return self._send(HTTPStatus.OK, png, "image/png")
            if path == "/api/keep":
                return self._keep(body)
            if path == "/api/markup":
                return self._markup(body)
            if path == "/api/images/list":
                return self._images_list(body)
            if path == "/api/images/edit":
                return self._images_edit(body)
            if path == "/api/scales":
                return self._scales(body)
            if path.startswith("/api/save/"):
                return self._save_in_place(path[len("/api/save/"):], body)
            if path == "/api/unsave":  # Undo after Save wrote over a file
                return self._unsave(json.loads(body or b"{}"))
            if path == "/api/reveal":
                return self._reveal(json.loads(body or b"{}"))
            if path == "/api/outline":
                return self._outline(body)
            if path == "/api/redact/find":
                return self._redact_find(body)
            # -- true redaction --
            if path == "/api/redact/apply":
                return self._redact_apply(body)
            # -- end of true redaction --
            if path == "/api/form/fields":
                return self._form_fields(body)
            if path == "/api/form/detect":
                return self._form_detect(body)
            if path == "/api/form/fillable":
                return self._form_fillable(body)
            if path in ("/api/comments/summary", "/api/comments/export", "/api/comments/import"):
                return self._comments(path.rsplit("/", 1)[1], body)
            if path == "/api/text/edit":
                return self._text_edit(body)
            # -- text reflow --
            if path == "/api/text/fonts":
                return self._text_fonts(body)
            # -- end of text reflow --
            if path == "/api/pages/arrange":
                return self._arrange(body)
            if path == "/api/sign/cert":
                return self._sign_cert(body)
            if path == "/api/sign/verify":
                return self._sign_verify(body)
            if path.startswith("/api/scan/"):
                return self._scan_post(path[len("/api/scan/"):], body)
            if path == "/api/accessibility/check":  # -- accessibility --
                return self._access_check(body)
            if path == "/api/settings":
                return self._save_settings(body)
            if path in ("/api/watch/add", "/api/watch/pause", "/api/watch/remove"):
                data = json.loads(body or b"{}")
                if path == "/api/watch/add":
                    do = {"recipe": data["recipe"]} if data.get("recipe") else {"tool": data.get("tool")}
                    self.watcher.add(data.get("folder"), do, data.get("results") or "done",
                                     data.get("originals") or "keep")
                elif path == "/api/watch/pause":
                    self.watcher.set_paused(str(data.get("id")), data.get("paused"))
                else:
                    self.watcher.remove(str(data.get("id")))
                return self._watch_list()
            if path == "/api/shellmenu":
                from pdfmanager import winsetup

                if not _shell_menu_available():
                    raise UserError("the right-click menu is only for ZPDF installed on Windows with its installer")
                on = winsetup.set_context_menu(bool(json.loads(body or b"{}").get("on")))
                return self._send(HTTPStatus.OK, {"available": True, "on": on})
            if path == "/api/update/check":
                return self._send(HTTPStatus.OK, {**update.check(force=True),
                                                  "can_restart": self._can_restart()})
            if path == "/api/update/install":
                def install():
                    notes = []
                    return {"json": {"version": update.install(notes=notes), "note": " ".join(notes)}}

                return self._job_or_now(install)
            if path == "/api/update/restart":
                return self._restart()
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

                def folder_job():
                    result = run_folder_operation(data.get("op"), data.get("options") or {},
                                                  bool(data.get("apply")))
                    # Search results can be opened in the reader.
                    for match in result.get("matches", []):
                        match["id"] = self.opened.add(match["path"])
                    return {"json": result}

                return self._job_or_now(folder_job)
            if path.startswith("/api/job/"):
                return self._job(path, "POST")
            if path == "/api/logs/open":
                applog.open_folder()
                return self._send(HTTPStatus.OK, {"ok": True})
            if path == "/api/recent/forget":
                data = json.loads(body or b"{}")
                removed = RecentFiles.forget(data.get("path") if not data.get("all") else None)
                return self._send(HTTPStatus.OK, {"ok": True, "removed": removed})
            if path == "/api/recent/failed":  # the reader couldn't open it
                data = json.loads(body or b"{}")
                target = self.opened.get(str(data.get("id", ""))) if self.opened is not None else None
                if target is None:
                    raise UserError("that file is no longer available")
                RecentFiles.failed(target)
                return self._send(HTTPStatus.OK, {"ok": True})
            if path == "/api/recent/restore":  # Undo after a forget
                items = json.loads(body or b"{}").get("items")
                RecentFiles.restore(items if isinstance(items, list) else [])
                return self._send(HTTPStatus.OK, {"ok": True})
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        except EXPECTED_ERRORS as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # report, don't kill the server
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, applog.unexpected(exc))

    def _run(self, body):
        """Run a tool. With "X-Pdfm-Async: 1" the tool runs as a background
        job: the answer is {"job": id}; /api/job/<id> tells how far it is,
        /api/job/<id>/result gives the file and /api/job/<id>/cancel stops it."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        op = fields.pop("op", "")
        password = fields.pop("password", "") or None
        keep = self.headers.get("X-Pdfm-Keep") == "1"
        # "ids": files that are already open (see OpenFiles), to work on
        # without the page sending them back. They come before any uploads.
        opened = []
        for key in [k for k in fields.pop("ids", "").split(",") if k.strip()] if "ids" in fields else []:
            path = self.opened.get(key.strip()) if self.opened is not None else None
            if path is None or not path.is_file():
                return self._error(HTTPStatus.BAD_REQUEST, "that file is no longer available; open it again")
            opened.append(path)

        def work_on():
            work = self.work.make()
            try:
                files, extra = [], {}
                for index, source in enumerate(opened):
                    # A copy: tools never touch the file the reader has open.
                    folder = work / "in" / f"o{index:03d}"
                    folder.mkdir(parents=True)
                    files.append(Path(shutil.copy2(source, folder / _safe_name(source.name))))
                for index, (name, filename, data) in enumerate(uploads):
                    # One folder per upload keeps original names without clashes.
                    folder = work / "in" / f"{index:03d}"
                    folder.mkdir(parents=True)
                    path = folder / _safe_name(filename)
                    path.write_bytes(data)
                    if name == "files":
                        files.append(path)
                    elif data:  # extra file field, e.g. an ICC profile
                        extra.setdefault(name, path)
                        extra.setdefault(name + "[]", []).append(path)  # all of them
                notes = []
                started, outcome = time.monotonic(), "failed"
                try:
                    name, data, ctype = run_operation(op, files, fields, password, extra, work, notes)
                    outcome = "done"
                except progress.Cancelled:
                    outcome = "cancelled"
                    raise
                except EXPECTED_ERRORS as exc:
                    outcome = f"stopped: {exc}"
                    raise
                finally:
                    applog.tool_run(op, files, time.monotonic() - started, outcome)
                return {"file": (name, data, ctype, notes, keep)}
            finally:
                self.work.remove(work)

        return self._job_or_now(work_on)

    def _job_or_now(self, func):
        if self.headers.get("X-Pdfm-Async") == "1":
            job = progress.Job()
            job.expected = EXPECTED_ERRORS
            self.jobs.add(job).start(func)
            return self._send(HTTPStatus.OK, {"job": job.id})
        return self._send_result(func())

    def _send_result(self, result):
        if "json" in result:
            return self._send(HTTPStatus.OK, result["json"])
        name, data, ctype, notes, keep = result["file"]
        headers = _download_headers(name)
        if notes:
            headers["X-Pdfm-Note"] = urllib.parse.quote("\n".join(notes))
        if keep and self.results is not None:
            headers["X-Pdfm-Result-Id"] = self.results.add(name, data)
        return self._send(HTTPStatus.OK, data, ctype, headers)

    def _job(self, path, method):
        """GET /api/job/<id>, GET /api/job/<id>/result, POST /api/job/<id>/cancel."""
        job_id, _, action = path[len("/api/job/"):].partition("/")
        job = self.jobs.get(job_id)
        if job is None:
            return self._error(HTTPStatus.NOT_FOUND, "that job is no longer available")
        if method == "POST" and action == "cancel":
            job.cancel()
            return self._send(HTTPStatus.OK, job.snapshot())
        if method == "GET" and action == "result":
            if job.state == "failed":
                self.jobs.pop(job_id)
                return self._error(HTTPStatus.BAD_REQUEST, job.error)
            if job.state != "done":
                return self._error(HTTPStatus.CONFLICT, f"the job is {job.state}")
            # Forgotten only once the page has the file: if sending it
            # fails (the window was closed halfway), the page can ask again.
            self._send_result(job.result)
            self.jobs.pop(job_id)
            return None
        if method == "GET" and not action:
            return self._send(HTTPStatus.OK, job.snapshot())
        return self._error(HTTPStatus.NOT_FOUND, "not found")

    # -- Scan: pages from a scanner or phone photos, then a PDF (scanner.py, scanpdf.py) --

    def _scan_query(self):
        return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}

    def _scan_get(self, what):
        """GET /api/scan/status, /devices, /pages?box=, /thumb?box=&page=&look=."""
        query = self._scan_query()
        if what == "status":
            return self._send(HTTPStatus.OK, {"scanner": scanner.status(), "ocr": scanpdf.ocr_engine(),
                                              "upright": scanpdf.can_turn_upright(),
                                              "modes": list(scanner.MODES),
                                              "resolutions": list(scanner.RESOLUTIONS)})
        if what == "devices":
            return self._send(HTTPStatus.OK, {"devices": scanner.list_scanners()})
        box = self.scans.get(query.get("box"), create=False)
        if what == "pages":
            return self._send(HTTPStatus.OK, {"box": box.id, "pages": box.list()})
        if what == "thumb":
            look = query.get("look") or "document"
            if look not in scanpdf.LOOKS:
                raise UserError("unknown look")
            return self._send(HTTPStatus.OK, box.thumbnail(query.get("page", ""), look), "image/jpeg")
        return self._error(HTTPStatus.NOT_FOUND, "not found")

    def _scan_post(self, what, body):
        """POST /api/scan/box (a new set of pages), /acquire (from the
        scanner), /photos (phone photos), /make (the PDF), /close."""
        if what == "photos":
            return self._scan_photos(body)
        data = json.loads(body or b"{}")
        if what == "box":
            box = self.scans.get(None)
            return self._send(HTTPStatus.OK, {"box": box.id, "pages": []})
        if what == "close":
            self.scans.drop(str(data.get("box", "")))
            return self._send(HTTPStatus.OK, {"ok": True})
        box = self.scans.get(str(data.get("box", "")), create=False)
        if what == "acquire":
            mode = data.get("mode") or scanner.DEFAULT_MODE
            dpi = _num(data.get("dpi"), int, scanner.DEFAULT_DPI)
            source = data.get("source") or "flatbed"
            device = str(data.get("device") or "")
            if not device:
                raise UserError("choose a scanner first")

            def acquire_job():
                added = []
                scanner.scan(device, mode=mode, dpi=dpi, source=source,
                             on_page=lambda image, d: added.append(box.add(image, d)))
                return {"json": {"box": box.id, "pages": added}}

            return self._job_or_now(acquire_job)
        if what == "make":
            return self._scan_make(box, data)
        return self._error(HTTPStatus.NOT_FOUND, "not found")

    def _scan_photos(self, body):
        """Phone photos: each page found, flattened and kept in the box."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        box = self.scans.get(fields.get("box"), create=False)
        straighten = _bool(fields.get("straighten", True))
        photos = [(name, bytes(data)) for field, name, data in uploads if field == "files"]
        if not photos:
            raise UserError("choose one or more photos")

        def photos_job():
            from PIL import Image, UnidentifiedImageError

            added, skipped = [], []
            for n, (name, data) in enumerate(photos):
                progress.report(n, len(photos), "photo")
                try:
                    with Image.open(io.BytesIO(data)) as im:
                        im.load()
                        page, found = scanpdf.photo_page(im, straighten)
                except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
                    skipped.append(name)
                    continue
                added.append({**box.add(page.image, None, photo=True, found=found.found), "name": name})
            return {"json": {"box": box.id, "pages": added, "skipped": skipped}}

        return self._job_or_now(photos_job)

    def _scan_make(self, box, data):
        """The PDF from the box's pages, in the order and turned as asked."""
        wanted = data.get("pages") or []
        if not isinstance(wanted, list) or not wanted:
            raise UserError("there are no pages yet: scan or add some first")
        pages = [box.page(str(p.get("id", "")), _num(p.get("turn"), int, 0)) for p in wanted]
        options = {"straighten": _bool(data.get("straighten", True)),
                   "remove_blank": _bool(data.get("remove_blank", True)),
                   "upright": _bool(data.get("upright", True)),
                   "recognise": _bool(data.get("ocr", True)),
                   "look": data.get("look") or "document", "paper": data.get("paper") or "auto"}
        keep = self.headers.get("X-Pdfm-Keep") == "1"

        def make_job():
            work = self.work.make()
            notes, started, outcome = [], time.monotonic(), "failed"
            try:
                name = time.strftime("Scan %Y-%m-%d %H.%M.pdf")
                out = scanpdf.make_pdf(pages, work / "scan.pdf", notes=notes, **options)
                outcome = "done"
                return {"file": (name, out.read_bytes(), "application/pdf", notes, keep)}
            except progress.Cancelled:
                outcome = "cancelled"
                raise
            finally:
                applog.tool_run("scan", [], time.monotonic() - started, outcome)
                self.work.remove(work)

        return self._job_or_now(make_job)

    def _keep(self, body):
        """Hold a file (e.g. the reader's copy with filled-in forms) so the
        desktop window can save it with the native Save dialog."""
        _, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads or self.results is None:
            raise UserError("no file")
        _, filename, data = uploads[0]
        return self._send(HTTPStatus.OK, {"id": self.results.add(_safe_name(filename), bytes(data))})

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

    def _unsaved(self, body):
        """{"page": id, "names": [...]}: this page's unsaved tabs now."""
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            data = None
        names = data.get("names") if isinstance(data, dict) else None
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            return self._error(HTTPStatus.BAD_REQUEST, "names must be a list of file names")
        page = str(data.get("page") or self.headers.get("X-Pdfm-Page") or "")
        self.unsaved.said(page, names)
        return self._send(HTTPStatus.OK, {"ok": True})

    def _bye(self):
        """The page is being closed (or reloaded). Sent with
        navigator.sendBeacon, which can't add headers, so the token comes
        in the query string; this request can do nothing else."""
        self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 65536))
        query = urllib.parse.parse_qs(self.path.partition("?")[2])
        if not secrets.compare_digest(query.get("token", [""])[0], self.token):
            return self._error(HTTPStatus.FORBIDDEN, "missing or wrong token")
        if self.pages is not None:
            self.pages.pop(query.get("page", [""])[0], None)
        if self.unsaved is not None:
            self.unsaved.gone(query.get("page", [""])[0])
        type(self).closed_at = time.monotonic()
        return self._send(HTTPStatus.OK, {"ok": True})

    def _instance(self, body):
        """Another launch of the app hands over the files it was asked to
        open (see forward_to_running_app). It proves it's the same user
        with the secret from instance.json in the app's data folder."""
        data = json.loads(body or b"{}")
        nonce = str(data.get("nonce", ""))
        if not self.instance_secret or not nonce or not secrets.compare_digest(
                str(data.get("proof", "")), _proof(self.instance_secret, "open", nonce)):
            return self._error(HTTPStatus.FORBIDDEN, "not allowed")
        proof = _proof(self.instance_secret, "answer", nonce)
        action = data.get("action")
        if action is not None:
            return self._instance_action(action, data.get("paths") or [], proof)
        ids = []
        for path in data.get("paths") or []:
            try:
                ids.append(self.opened.add(path))
            except (OSError, ValueError):
                continue
        if self.instance_mode == "desktop":
            self.events.push({"open": ids})
            raise_window = type(self).bring_to_front  # a plain function, not a method
            if raise_window:
                # Not before answering: pywebview waits for the window's own
                # thread, and the other launch only waits a few seconds
                # before opening a second copy instead.
                threading.Thread(target=raise_window, daemon=True).start()
            return self._send(HTTPStatus.OK, {"handled": True, "proof": proof})
        url = f"http://127.0.0.1:{self.server.server_address[1]}/#token={self.token}"
        if ids:
            url += "&open=" + ",".join(ids)
        return self._send(HTTPStatus.OK, {"url": url, "mode": self.instance_mode, "proof": proof})

    def _instance_action(self, action, paths, proof):
        """Files for a right-click action (see quickactions.py): they join the
        batch of that action still gathering files, or start one. A new batch
        is shown by the app's own window (an event), else by a new window on
        it; files joining a batch need nothing more."""
        if action not in quickactions.ACTIONS or not isinstance(paths, list):
            return self._error(HTTPStatus.BAD_REQUEST, "bad request")
        batch, new = self.actions.add(action, [Path(str(p)) for p in paths])
        _log(f"action {action}: {len(paths)} file(s), batch {batch.id}{' (new)' if new else ''}")
        if self.instance_mode == "desktop":
            if new:
                self.events.push({"action": batch.id})
                raise_window = type(self).bring_to_front
                if raise_window:
                    threading.Thread(target=raise_window, daemon=True).start()
            return self._send(HTTPStatus.OK, {"handled": True, "proof": proof})
        if not new:
            return self._send(HTTPStatus.OK, {"handled": True, "proof": proof})
        url = f"http://127.0.0.1:{self.server.server_address[1]}/#token={self.token}&action={batch.id}"
        return self._send(HTTPStatus.OK, {"url": url, "mode": self.instance_mode, "proof": proof})

    def _watch_list(self):
        """Settings > Watch folders: the folders, what each is doing, and the choices."""
        _, last = self.watcher.news_after(10**9)
        return self._send(HTTPStatus.OK, {
            "folders": self.watcher.list(), "last": last,
            "tools": [{"id": k, "label": v} for k, v in watchfolders.TOOLS.items()],
            "recipes": list(recipes.load_all())})

    def _action(self, rest):
        """GET /api/action/<id>: how a right-click batch is getting on;
        /api/action/<id>/file/<n>: one of its results (given once)."""
        batch_id, _, more = rest.partition("/")
        actions = getattr(self, "actions", None)
        batch = actions.get(batch_id) if actions is not None else None
        if batch is None:
            return self._error(HTTPStatus.NOT_FOUND, "that action is no longer available")
        if not more:
            return self._send(HTTPStatus.OK, batch.snapshot())
        kind, _, n = more.partition("/")
        item = batch.outputs.pop(int(n), None) if kind == "file" and n.isdigit() else None
        if item is None:
            return self._error(HTTPStatus.NOT_FOUND, "that result is no longer available")
        name, data = item
        return self._send(HTTPStatus.OK, data, "application/pdf", _download_headers(name))

    def _events(self):
        """Long poll: events for the page (files handed over by another
        launch), after the one numbered ``after``."""
        query = urllib.parse.parse_qs(self.path.partition("?")[2])
        try:
            after = int(query.get("after", ["0"])[0])
        except ValueError:
            after = 0
        mode = type(self).instance_mode
        if mode is not None and mode != "desktop":
            # Only the app's own window gets events; other kinds open a new
            # window instead. Answer at once so the page stops asking (a
            # browser allows few connections per site).
            return self._send(HTTPStatus.OK, {"events": [], "last": after, "none": True})
        events, last = self.events.wait(after, timeout=20)
        return self._send(HTTPStatus.OK, {"events": events, "last": last})

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

    # -- true redaction --
    def _redact_apply(self, body):
        """The reader's Redact tool: remove what's under the marked boxes
        ("boxes": {"1": [[x0, y0, x1, y1]]} in PDF points from the bottom
        left of each page); sends back the redacted PDF, and in
        X-Pdfm-Redacted how many areas, on which pages, and which pages had
        to become pictures (and why)."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        boxes = _redact_boxes(fields.get("boxes"))
        if not boxes:
            raise UserError("mark something to redact first")
        method = fields.get("method") or "remove"
        if method not in redact.METHODS:
            raise UserError("choose how to redact: remove what's under the boxes, or pictures")
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "redacted.pdf"
            src.write_bytes(uploads[0][2])
            report = redact.redact(src, out, boxes=boxes, password=password, method=method)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)  # stays protected
            data = out.read_bytes()
        summary = {"areas": sum(report.values()), "pages": list(report),
                   "pictures": {str(k): v for k, v in report.pictures.items()}}
        return self._send(HTTPStatus.OK, data, "application/pdf",
                          {"X-Pdfm-Redacted": json.dumps(summary, ensure_ascii=True)})
    # -- end of true redaction --

    def _form_fields(self, body):
        """The form fields already in a PDF, for the Add form fields editor."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = Path(tmp) / "file.pdf"
            path.write_bytes(uploads[0][2])
            found = forms.existing_fields(path, fields.get("password") or None)
        return self._send(HTTPStatus.OK, {"fields": {str(p): f for p, f in found.items()}})

    def _opened_path(self, key):
        """The file opened from this computer under id ``key`` (see
        OpenFiles): what a request works on when the page names the file
        (field "id") instead of sending it back."""
        path = self.opened.get(key) if self.opened is not None else None
        if path is None or not path.is_file():
            raise UserError("that file is no longer available")
        return path

    def _form_detect(self, body):
        """The fields a flat form should have, for the Add form fields editor:
        of a file opened from this computer (field "id") or of an uploaded copy."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        opened = self._opened_path(fields["id"]) if fields.get("id") else None
        if opened is None and not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = opened
            if path is None:
                path = Path(tmp) / "file.pdf"
                path.write_bytes(uploads[0][2])
            pages = None
            if fields.get("pages"):  # e.g. "1-2": the reader's quick look at the first pages
                try:
                    first, _, last = fields["pages"].partition("-")
                    pages = range(int(first), int(last or first) + 1)
                except ValueError:
                    raise UserError("pages should look like 1-2") from None
            if pages is not None:
                # The quick look, unasked: a scan isn't offered as a form, with
                # or without its text recognised (a contents page's dot leaders
                # look like one, and a scanned form is filled in on paper).
                password = fields.get("password") or None
                seen = filefacts.remembered(path) if opened is not None and not password else filefacts.facts(path, password)
                if seen.get("scanned"):
                    return self._send(HTTPStatus.OK, {"fields": {}})
            found = formdetect.detect(path, fields.get("password") or None, pages=pages, quick=pages is not None)
        return self._send(HTTPStatus.OK, {"fields": {str(p): f for p, f in found.items()}})

    def _comments(self, what, body):
        """The reader's comment summary (PDF), XFDF export, or XFDF import
        (the changed PDF)."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "file.pdf"
            src.write_bytes(uploads[0][2])
            out = Path(tmp) / "out"
            name = fields.get("name") or "document.pdf"
            if what == "summary":
                comments.summary_pdf(comments.collect(src, password), out, name)
                return self._send(HTTPStatus.OK, out.read_bytes(), "application/pdf")
            if what == "export":
                n = comments.export_xfdf(src, out, password)
                return self._send(HTTPStatus.OK, out.read_bytes(), "application/vnd.adobe.xfdf",
                                  {"X-Pdfm-Count": str(n)})
            xfdf = next((data for key, _, data in uploads if key == "xfdf"), None)
            if xfdf is None:
                raise UserError("choose the comments file (.xfdf) to add")
            (Path(tmp) / "in.xfdf").write_bytes(xfdf)
            added, skipped = comments.import_xfdf(src, Path(tmp) / "in.xfdf", out, password)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)  # stays protected
            return self._send(HTTPStatus.OK, out.read_bytes(), "application/pdf",
                              {"X-Pdfm-Added": str(added), "X-Pdfm-Skipped": ",".join(skipped)})

    def _form_fillable(self, body):
        """The reader's "Make it fillable": the fields of a printed form added
        to it; sends back the changed PDF (X-Pdfm-Fields: how many)."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "fillable.pdf"
            src.write_bytes(uploads[0][2])
            found = formdetect.detect(src, password)
            if not found:
                raise UserError("no fields found: this page has no lines, boxes or tick boxes to "
                                "fill in. Add form fields lets you draw them")
            forms.add_fields(src, out, [{**f, "page": page} for page, items in found.items()
                                        for f in items], password=password)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)  # stays protected
            data = out.read_bytes()
        places = sum(len(items) for items in found.values())  # (a radio group: each button)
        return self._send(HTTPStatus.OK, data, "application/pdf", {"X-Pdfm-Fields": str(places)})

    def _save_settings(self, body):
        data = json.loads(body or b"{}")
        changes = {}
        if "repo" in data:
            repo = str(data["repo"] or "").strip().strip("/")
            if repo and repo not in (update.RELEASES, update.REPO):
                # Updates install whatever the repository publishes: only ZPDF's own.
                raise UserError(f"updates come from {update.RELEASES} (or {update.REPO} for development)")
            changes["update_repo"] = "" if repo == update.RELEASES else repo
        if data.get("token"):
            changes["github_token"] = str(data["token"]).strip()
        if data.get("clear_token"):
            changes["github_token"] = ""
        if "check" in data:
            changes["check_updates"] = None if data["check"] else False
        update.save_settings(**changes)
        return self._send(HTTPStatus.OK, update.public_settings())

    def _can_restart(self):
        """Can this copy close itself and start again (after an update)?"""
        mode = type(self).instance_mode
        return mode in ("app-window", "browser") or (mode == "desktop" and type(self).quit_app is not None)

    def _restart(self):
        """Start the app again: a helper waits for this copy to end."""
        cls = type(self)
        if not self._can_restart():
            raise UserError("close ZPDF and open it again to use the new version")
        update.restart_after(os.getpid())
        cls.quit_requested = True
        _log(f"restart asked for; mode {cls.instance_mode}")
        if cls.instance_mode == "desktop":
            threading.Timer(0.5, cls.quit_app).start()  # once this answer has been sent
        return self._send(HTTPStatus.OK, {"restarting": True})

    def _text_edit(self, body):
        """Change words on the reader's pages; sends back the changed PDF."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        try:
            edits = json.loads(fields.get("edits") or "[]")
        except ValueError:
            raise UserError("the changes are damaged; make them again") from None
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "edited.pdf"
            src.write_bytes(uploads[0][2])
            report = []
            textedit.edit_text(src, out, edits, password, report=report)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)  # stays protected
            data = out.read_bytes()
        # Which font each change got, for the reader to say so.
        return self._send(HTTPStatus.OK, data, "application/pdf",
                          {"X-Pdfm-Fonts": json.dumps(report, ensure_ascii=True)})

    # -- text reflow --
    def _text_fonts(self, body):
        """The fonts the reader's text editor offers: the document's own
        first, then installed ones, then the standard PDF fonts."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "file.pdf"
            src.write_bytes(uploads[0][2])
            choices = textedit.font_choices(src, fields.get("password") or None)
        return self._send(HTTPStatus.OK, choices)
    # -- end of text reflow --

    def _images_list(self, body):
        """The pictures on the reader's pages (see pageimages.py)."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src = Path(tmp) / "file.pdf"
            src.write_bytes(uploads[0][2])
            found = pageimages.list_images(src, fields.get("password") or None)
        return self._send(HTTPStatus.OK, {"pages": found})

    def _images_edit(self, body):
        """Move, resize, replace or delete pictures; sends back the changed PDF.
        The first upload is the document, "picture" uploads are new pictures
        (by their field position: picture0, picture1, ...)."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        try:
            changes = json.loads(fields.get("changes") or "[]")
        except ValueError:
            raise UserError("the changes are damaged; make them again") from None
        if not isinstance(changes, list) or not all(isinstance(c, dict) for c in changes):
            raise UserError("the changes are damaged; make them again")
        pictures = {name: bytes(data) for name, _, data in uploads[1:] if name}
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "edited.pdf"
            src.write_bytes(uploads[0][2])
            pageimages.edit_images(src, out, changes, password, pictures)
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)  # stays protected
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf")

    def _markup(self, body):
        """Add notes, underlines, strikeouts and shapes, or change, answer or
        remove comments (see markup.py); sends back the changed PDF."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        try:
            changes = json.loads(fields.get("changes") or "[]")
        except ValueError:
            raise UserError("the changes are damaged; make them again") from None
        if not isinstance(changes, list) or not all(isinstance(c, dict) for c in changes):
            raise UserError("the changes are damaged; make them again")
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "marked.pdf"
            src.write_bytes(uploads[0][2])
            added = markup.apply(src, out, changes, password, fields.get("author"))
            if password and core.open_pdf(src, password).is_encrypted:
                core.encrypt(out, out, password)  # stays protected
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf", {"X-Pdfm-Added": json.dumps(added)})

    def _arrange(self, body):
        """Reorder, turn, delete, copy or insert the reader's pages; sends back
        the new PDF. The first upload is the document, the others are PDFs
        whose pages go in."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        try:
            plan = json.loads(fields.get("plan") or "[]")
        except ValueError:
            raise UserError("the page order is damaged; try again") from None
        password = fields.get("password") or None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            paths = []
            for n, (_, _, data) in enumerate(uploads):
                path = Path(tmp) / f"{n}.pdf"
                path.write_bytes(data)
                paths.append(path)
            out = Path(tmp) / "arranged.pdf"
            core.arrange(paths[0], out, plan, inserts=paths[1:], password=password)
            if password and core.open_pdf(paths[0], password).is_encrypted:
                core.encrypt(out, out, password)  # stays protected
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf")

    def _sign_cert(self, body):
        """Sign the reader's PDF with a digital ID; sends back the signed PDF,
        and what certsign.sign() said in X-Pdfm-Signed (JSON). A missing or
        wrong digital ID password answers 400 with "code": "pfx_password"."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        named = {name: data for name, _, data in uploads}
        if "file" not in named:
            raise UserError("no file")
        if not len(named.get("pfx") or b""):
            raise UserError("choose your digital ID: a .pfx or .p12 file")
        password = fields.get("password") or None
        box = None
        if (fields.get("box") or "").strip():
            try:
                box = certsign.parse_box(json.loads(fields["box"]))
            except ValueError:
                raise UserError("the signature box is damaged; draw it again") from None
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            src, out = Path(tmp) / "file.pdf", Path(tmp) / "signed.pdf"
            src.write_bytes(named["file"])
            page = (fields.get("page") or "").strip() or None
            if page is not None and box is None and not fields.get("field"):
                page, box = certsign.place(src, page, fields.get("position") or "bottom-right",
                                           fields.get("size") or "medium", password)
            try:
                result = certsign.sign(
                    src, out, bytes(named["pfx"]), fields.get("pfx_password") or None,
                    page=page, box=box, field=(fields.get("field") or "").strip() or None,
                    reason=(fields.get("reason") or "").strip() or None,
                    location=(fields.get("location") or "").strip() or None,
                    contact=(fields.get("contact") or "").strip() or None,
                    timestamp_url=(fields.get("timestamp_url") or "").strip() or None,
                    password=password)
            except certsign.IdPasswordError as exc:
                self.close_connection = True
                return self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc),
                                                           "code": "pfx_password"})
            data = out.read_bytes()
        return self._send(HTTPStatus.OK, data, "application/pdf",
                          {"X-Pdfm-Signed": json.dumps(result, ensure_ascii=True)})

    # -- accessibility --
    def _access_check(self, body):
        """The reader's Check accessibility: accessibility.check's result."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = Path(tmp) / "file.pdf"
            path.write_bytes(uploads[0][2])
            result = accessibility.check(path, fields.get("password") or None)
        result.pop("file", None)
        return self._send(HTTPStatus.OK, result)
    # -- end of accessibility --

    def _sign_verify(self, body):
        """The digital signatures in a PDF, checked: {"signatures": [...]}
        (see certsign.verify), of a file opened from this computer (field
        "id") or of an uploaded copy. With "quick", a file that can't hold a
        signature (every one has a /ByteRange) is answered without being read
        as a PDF: what the reader asks about every file it opens."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        named = {name: data for name, _, data in uploads}
        opened = self._opened_path(fields["id"]) if fields.get("id") else None
        if opened is None and "file" not in named:
            raise UserError("no file")
        if fields.get("quick") and not (_file_mentions(opened, b"/ByteRange") if opened is not None
                                        else re.search(rb"/ByteRange", named["file"])):
            return self._send(HTTPStatus.OK, {"signatures": []})
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = opened
            if path is None:
                path = Path(tmp) / "file.pdf"
                path.write_bytes(named["file"])
            trust = None
            if len(named.get("trust") or b""):
                trust = [Path(tmp) / "trusted.cer"]
                trust[0].write_bytes(named["trust"])
            try:
                found = certsign.verify(path, fields.get("password") or None, trust)
            except EXPECTED_ERRORS as exc:
                if not fields.get("quick"):
                    raise
                # Signed, but the signatures couldn't be checked: the reader says so.
                return self._send(HTTPStatus.OK, {"signatures": [], "error": str(exc)})
        return self._send(HTTPStatus.OK, {"signatures": found})

    def _facts(self, body):
        """What a file the page holds is (see filefacts), and what to offer for it."""
        fields, uploads = _parse_multipart(self.headers.get("Content-Type", ""), body)
        if not uploads:
            raise UserError("no file")
        with tempfile.TemporaryDirectory(prefix="pdfm-") as tmp:
            path = Path(tmp) / "file.pdf"
            path.write_bytes(uploads[0][2])
            found = filefacts.facts(path, fields.get("password") or None)
        return self._send(HTTPStatus.OK, {**found, "suggestions": filefacts.suggestions(found, fields.get("made_by") or None)})

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

    def handle_error(self, request, client_address):
        # The page cancels questions it no longer needs (a document closed, a
        # tab switched) and the window may close mid-answer: a reader that has
        # gone is not a fault worth a traceback in the log.
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)

    def server_close(self):
        super().server_close()
        # Every way of ending the app comes through here. Jobs still running
        # are cancelled and given a moment to tidy up; whatever work folder is
        # then left (a job stuck in a long step) is removed all the same.
        handler = self.RequestHandlerClass
        jobs, work = getattr(handler, "jobs", None), getattr(handler, "work", None)
        if jobs is not None:
            jobs.stop()
        if work is not None:
            work.clear()
        scans = getattr(handler, "scans", None)
        if scans is not None:
            scans.clear()
        actions = getattr(handler, "actions", None)
        if actions is not None:
            actions.close()
        watcher = getattr(handler, "watcher", None)
        if watcher is not None:
            watcher.stop()


def make_server(port=0, desktop=False, open_paths=(), prefer=None, action=None):
    """Create (but don't start) the server. Returns (server, url). The
    server's result cache is ``server.RequestHandlerClass.results`` and its
    openable files ``server.RequestHandlerClass.opened``. ``open_paths``
    are PDFs to show in the reader straight away. With ``port`` 0, the
    ``prefer``red port is tried first, then any free port. ``action``:
    (right-click action, its files) for the page to run when it opens."""
    applog.setup()
    applog.install()
    token = secrets.token_urlsafe(24)
    opened = OpenFiles()
    open_ids = [opened.add(p) for p in open_paths]
    handler = type("PdfmHandler", (Handler,), {"token": token, "results": ResultCache(),
                                               "opened": opened, "allow_eval": desktop,
                                               "last_seen": time.monotonic(),
                                               "closed_at": None, "jobs": progress.JobStore(),
                                               "pages": {}, "events": Events(),
                                               "saved_over": SavedOver(), "unsaved": UnsavedWork(),
                                               "work": WorkFolders(), "scans": scanpdf.ScanBoxes()})
    handler.actions = quickactions.Batches(lambda batch: _start_action(handler, batch))
    handler.watcher = watchfolders.WatchFolders(log=_log)
    handler.watcher.start()  # (only once there's a folder to watch)
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
    if action:
        batch, _ = handler.actions.add(action[0], action[1])
        url += "&action=" + batch.id
    return server, url


def serve(port=0, open_browser=True, open_paths=(), exit_when_closed=False, action=None):
    """Run in the browser: print the link, open it, serve until Ctrl+C (or,
    with ``exit_when_closed``, until the page has been closed)."""
    server, url = make_server(port, open_paths=() if action else open_paths, prefer=APP_PORT, action=action)
    print(f"ZPDF is running at {url}")
    print("Keep this window open while you use it; press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    # Served from a thread in both cases, so that a restart after an update
    # (which asks this copy to quit) ends this one instead of leaving it
    # running next to the new copy.
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        if exit_when_closed:
            _register_instance(server, "browser")
            wait_until_closed(server.RequestHandlerClass, grace=5)
            _unregister_instance(server)
        else:
            server.RequestHandlerClass.instance_mode = "browser"  # no events to wait for
            while not server.RequestHandlerClass.quit_requested:
                time.sleep(0.5)
            _log("closing: restart asked for")
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


def wait_until_closed(handler, proc=None, idle=300, idle_handed_off=90, poll=0.5, grace=2.0,
                      handoff=5.0):
    """Block until the app's pages are gone.

    Each page says when its window closes (/api/bye) and otherwise pings the
    server every 15 seconds (less often while hidden). When the last page
    has said goodbye and no page has been heard from for ``grace`` seconds
    (a reload asks for the page again straight away), the app is closed.
    A page not heard from for ``idle`` seconds counts as gone (e.g. its
    browser was killed).

    With ``proc`` (the browser started for an app window): if it exits after
    the first ``handoff`` seconds, the window was closed. If it exits sooner,
    the browser handed the window to a copy that was already running, so only
    the pages' signals count. A browser that keeps running in the background
    after its window is closed (Edge does) doesn't keep the app alive.

    Time the computer spent asleep doesn't count: on Windows the clock
    keeps running through sleep, but the pages can't have pinged, so a
    poll that comes back much later than asked moves their timestamps on
    by the gap, and they get the usual ``idle`` seconds to be heard from."""
    started = last_poll = time.monotonic()
    exited_at = None
    pages = getattr(handler, "pages", None)
    if pages is None:
        pages = {}
    while True:
        time.sleep(poll)
        if getattr(handler, "quit_requested", False):
            _log("closing: restart asked for")
            return
        now = time.monotonic()
        gap, last_poll = now - last_poll - poll, now
        if gap > 5:
            _log(f"a poll took {gap:.0f}s longer than asked (asleep?); not counting it")
            handler.last_seen = min(handler.last_seen + gap, now)
            if handler.closed_at is not None:
                handler.closed_at = min(handler.closed_at + gap, now)
            for page, seen in list(pages.items()):
                if page in pages:  # not one that said goodbye just now
                    pages[page] = min(seen + gap, now)
        for page, seen in list(pages.items()):
            if now - seen > idle:
                pages.pop(page, None)
        closed = handler.closed_at
        if (closed is not None and not pages and handler.last_seen <= closed
                and now - closed > grace):
            _log("closing: the last page said goodbye")
            return
        quiet = now - handler.last_seen
        if proc is not None and proc.poll() is not None:
            if exited_at is None:
                exited_at = now
            if exited_at - started > handoff + poll or quiet > idle_handed_off:
                _log(f"closing: the browser exited (after {exited_at - started:.1f}s; quiet {quiet:.0f}s)")
                return
        elif quiet > idle:
            _log(f"closing: no page heard from for {quiet:.0f}s")
            return


# --------------------------------------------------------------------------
# One running copy
# --------------------------------------------------------------------------

def _instance_file():
    return data_dir() / "instance.json"


class _StartupLock:
    """Only one launch at a time looks for a running copy and registers
    itself, so files opened together (e.g. selected in Explorer and opened
    at once) all land in one app rather than each starting its own."""

    def __init__(self):
        self.fh = None

    def acquire(self, timeout=30):
        try:
            path = data_dir() / "launch.lock"
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(path, "a+b")
        except OSError:
            return
        deadline = time.monotonic() + timeout
        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.fh = fh
                return
            except OSError:
                if time.monotonic() > deadline:  # carry on without it
                    fh.close()
                    return
                time.sleep(0.1)

    def release(self):
        fh, self.fh = self.fh, None
        if fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


_startup = _StartupLock()


def _register_instance(server, mode, **extra):
    """Let later launches find this one (see forward_to_running_app)."""
    handler = server.RequestHandlerClass
    handler.instance_secret = secrets.token_urlsafe(32)
    handler.instance_mode = mode
    handler.instance_extra = {k: str(v) for k, v in extra.items()}
    _log(f"registered as {mode} on port {server.server_address[1]}")
    record = {"port": server.server_address[1], "pid": os.getpid(),
              "secret": handler.instance_secret, "mode": mode}
    try:
        write_private(_instance_file(), json.dumps(record))
    except OSError:
        pass
    _startup.release()  # later launches can find this copy now


def _unregister_instance(server):
    target = _instance_file()
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
        if record.get("pid") == os.getpid() and record.get("port") == server.server_address[1]:
            target.unlink()
    except (OSError, ValueError):
        pass


def _allow_foreground():
    """Let the running copy bring its window forward (Windows): only the
    program in front, which this launch is when a PDF was double-clicked,
    can allow another to take the foreground."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except Exception:  # noqa: BLE001 - a courtesy; the file is handed over all the same
        pass


def forward_to_running_app(paths=(), action=None):
    """If ZPDF is already running, hand it ``paths`` to open (or, with
    ``action``, to run that right-click action on) and return True, so this
    launch can end: there's only ever one copy.

    The running app either opens them itself (its own window, which comes
    to the front) or answers with an address to open, as a new window in
    its Edge/Chrome copy or a new browser tab."""
    try:
        record = json.loads(_instance_file().read_text(encoding="utf-8"))
        port, secret = int(record["port"]), str(record["secret"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    # Neither side sends the secret: each proves it knows it (anything else
    # could be listening on that port after a crash left instance.json behind).
    nonce = secrets.token_urlsafe(16)
    message = {"nonce": nonce, "proof": _proof(secret, "open", nonce),
               "paths": [str(Path(p).resolve()) for p in paths]}
    if action:
        message["action"] = action
    body = json.dumps(message).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/instance", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Host": f"127.0.0.1:{port}"})
    # Straight to this computer: a system or http_proxy proxy must not see it.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    _allow_foreground()
    try:
        with opener.open(request, timeout=5) as res:
            answer = json.loads(res.read())
    except (OSError, ValueError):
        return False  # not running any more (or something else has the port)
    if not isinstance(answer, dict) or not secrets.compare_digest(
            str(answer.get("proof", "")), _proof(secret, "answer", nonce)):
        return False  # not ZPDF
    if answer.get("handled"):
        return True
    url = str(answer.get("url") or "")
    if not url.startswith(f"http://127.0.0.1:{port}/#token="):
        return False
    if answer.get("mode") == "app-window":
        # The same kind of window, with this computer's browser and the
        # running copy's profile (both found here, not taken from the answer).
        browser = find_app_browser()
        if browser:
            try:
                subprocess.Popen([browser, f"--app={url}", f"--user-data-dir={_window_profile(port)}",
                                  "--no-first-run", "--no-default-browser-check"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return True
            except OSError:
                pass
    webbrowser.open(url)
    return True


def _proof(secret, purpose, nonce):
    """HMAC proof that a side knows the instance secret."""
    return hmac.new(secret.encode(), f"{purpose} {nonce}".encode(), hashlib.sha256).hexdigest()


def _window_profile(port):
    """The browser profile of the app window served on ``port``."""
    return data_dir() / ("window" if port == APP_PORT else f"window-{port}")


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

    def save_result(self, key, suggested_name, near=None):
        """Ask where to save a result and write it. Returns the path, or
        None if the user cancelled. The dialog starts in the folder of the
        opened file ``near`` (its id), if given, else the home folder."""
        import webview

        item = self._results.get(key)
        if item is None:
            raise ValueError("that result has expired; run the tool again")
        name, data = item
        beside = self._opened.get(near) if near else None
        folder = beside.parent if beside is not None and beside.parent.is_dir() else Path.home()
        filename = suggested_name or name
        chosen = self._window.create_file_dialog(
            webview.FileDialog.SAVE, directory=str(folder), save_filename=filename,
            file_types=_save_types(filename))
        if not chosen:
            return None
        target = Path(chosen if isinstance(chosen, str) else chosen[0])
        target.write_bytes(data)
        if self._opened is not None:
            self._opened.add(target)  # (so it can be shown in its folder)
        return str(target)

    def pick_folder(self):
        import webview

        chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER,
                                                 directory=str(Path.home()))
        return (chosen if isinstance(chosen, str) else chosen[0]) if chosen else None

    def reveal(self, path):
        """Show a saved file in Explorer / Finder / the file manager (one
        the app has open or has saved)."""
        if self._opened is not None and not self._opened.knows(path):
            raise ValueError("not a file ZPDF has open")
        reveal_in_file_manager(path)


def _replace_file(path, data):
    """Write ``data`` over ``path``: to a temporary file next to it first,
    then swapped in, so a failure halfway never leaves a broken file."""
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.zpdf-saving")
    try:
        temp.write_bytes(data)
        try:
            shutil.copymode(path, temp)
        except OSError:
            pass
        os.replace(temp, path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise UserError(f"could not save {path.name}: it may be open in another program, "
                        f"or read-only ({exc.strerror or exc})") from None


def reveal_in_file_manager(path):
    import subprocess
    import sys

    path = Path(path)
    if sys.platform == "win32":
        explorer = str(Path(os.environ.get("SystemRoot") or r"C:\Windows") / "explorer.exe")
        subprocess.Popen([explorer, "/select,", str(path)])
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


def run_desktop(port=0, open_paths=(), action=None):
    """Open ZPDF in its own window (needs pywebview). Returns False if
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
    server, url = make_server(port, desktop=True, open_paths=() if action else open_paths, prefer=APP_PORT,
                              action=action)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    handler = server.RequestHandlerClass
    api = DesktopApi(handler.results, handler.opened)
    _register_instance(server, "desktop")
    try:
        import webview

        window = webview.create_window("ZPDF", url, js_api=api, width=1180, height=820,
                                       min_size=(400, 560), background_color="#f6f6f3")
        api._window = window
        _allow_autofill(window)
        handler.bring_to_front = lambda: _raise_window(window)
        handler.quit_app = lambda: _close_on_purpose(handler, window)
        try:
            # Closing with the window's X: ask first when anything is unsaved
            # (WebView2 fires no beforeunload when the window itself closes).
            window.events.closing += lambda: may_close(handler, lambda text: _ask_to_close(window, text))
        except AttributeError as exc:  # pywebview before 4 has no closing event
            _log(f"launch: no closing event to warn about unsaved work: {exc!r}")
        # Keep the window's cache and settings between launches (pywebview
        # otherwise starts every time like a private browser window).
        options = {}
        debug_port = _debug_port()
        if debug_port:  # for tests (pywebview 5.3+; its settings aren't a plain dict)
            try:
                webview.settings["REMOTE_DEBUGGING_PORT"] = debug_port
            except (AttributeError, KeyError, TypeError):
                pass
        if "storage_path" in inspect.signature(webview.start).parameters:
            storage = data_dir() / "webview"
            storage.mkdir(parents=True, exist_ok=True)
            options = {"private_mode": False, "storage_path": str(storage)}
        webview.start(**options)
        _unregister_instance(server)
    except Exception as exc:  # e.g. a missing or blocked web engine
        print(f"Could not open the app window ({exc}); trying another way.")
        _unregister_instance(server)
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


def close_question(names):
    """What closing ZPDF asks with the files ``names`` unsaved (None: nothing to ask)."""
    if not names:
        return None
    if len(names) == 1:
        which = f"Your changes to “{names[0]}” haven't been saved."
    elif len(names) <= 3:
        which = f"Your changes to {len(names)} files haven't been saved: " + ", ".join(names) + "."
    else:
        which = f"Your changes to {len(names)} files haven't been saved."
    return "Close ZPDF? " + which


def may_close(handler, ask):
    """pywebview's closing event (False keeps the window open). Closing on
    purpose (to start again after an update, which asked about unsaved work
    itself) never asks. Otherwise, with anything unsaved (as the page last
    told the server), ``ask(text)``: True closes anyway. Whatever goes wrong
    asking, the window closes (it must never be impossible to close)."""
    if handler.quit_requested:
        return True
    question = close_question(handler.unsaved.names() if handler.unsaved is not None else [])
    if not question:
        return True
    try:
        answer = bool(ask(question))
    except Exception as exc:  # noqa: BLE001
        _log(f"closing: couldn't ask about unsaved work: {exc!r}")
        return True
    _log(f"closing with unsaved work: {'closed' if answer else 'kept open'}")
    return answer


def _close_on_purpose(handler, window):
    """Close the window without asking (see may_close)."""
    handler.quit_requested = True
    window.destroy()


MB_YESNO, MB_ICONWARNING, MB_DEFBUTTON2, MB_SETFOREGROUND, MB_TOPMOST, IDYES = 0x4, 0x30, 0x100, 0x10000, 0x40000, 6


def _ask_to_close(window, text):
    """Ask ``text`` with Yes and No (No the default): True for Yes.

    pywebview runs the closing event on the window's own thread and waits
    for it, so this mustn't wait for that thread: on Windows a plain
    MessageBoxW (it runs its own message loop; owned by the window, when its
    handle can be had, so it sits on top of it). Elsewhere pywebview's own
    dialog, unless this is the GUI's main thread, where it would wait for
    itself (GTK, Cocoa, Qt): the window then closes as before."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        try:
            owner = int(window.native.Handle.ToInt64())
        except Exception:  # noqa: BLE001 - no WinForms form to own it
            owner = None
        flags = MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 | (0 if owner else MB_TOPMOST | MB_SETFOREGROUND)
        box = ctypes.windll.user32.MessageBoxW
        box.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
        box.restype = ctypes.c_int
        return box(owner, text, "ZPDF", flags) == IDYES
    confirm = getattr(window, "create_confirmation_dialog", None)
    if confirm is None or threading.current_thread() is threading.main_thread():
        return True
    return bool(confirm("Close ZPDF?", text))


def _allow_autofill(window):
    """Let WebView2 offer to save and fill in passwords (autocomplete=
    "current-password") and other form entries: both are off by default in
    an app's WebView2, and pywebview leaves them so. Windows' EdgeChromium
    backend only: pywebview runs its before_show event on the window's own
    thread right after making the WebView2 control (window.native.browser.
    webview); its CoreWebView2 is ready later, in CoreWebView2Initialization
    Completed, where the settings are set. Anything missing or failing is
    logged and the window opens as before."""
    if sys.platform != "win32":
        return

    def on_ready(sender, args):
        try:
            if args.IsSuccess:
                settings = sender.CoreWebView2.Settings
                settings.IsPasswordAutosaveEnabled = True
                settings.IsGeneralAutofillEnabled = True
        except Exception as exc:  # noqa: BLE001 - an older WebView2 lacks these settings
            _log(f"launch: couldn't turn on password autofill: {exc!r}")

    def before_show(window):
        try:
            control = window.native.browser.webview
            if getattr(control, "CoreWebView2", None) is not None:  # ready already
                on_ready(control, type("Ready", (), {"IsSuccess": True})())
            else:
                control.CoreWebView2InitializationCompleted += on_ready
        except Exception as exc:  # noqa: BLE001 - another backend (no WebView2 control)
            _log(f"launch: no WebView2 settings to turn on autofill: {exc!r}")

    try:
        window.events.before_show += before_show
    except AttributeError as exc:  # pywebview before 5 has no before_show event
        _log(f"launch: no before_show event for autofill: {exc!r}")


SAVE_TYPES = {".pdf": "PDF files (*.pdf)", ".zip": "ZIP archives (*.zip)", ".docx": "Word documents (*.docx)",
              ".xlsx": "Excel workbooks (*.xlsx)", ".pptx": "PowerPoint presentations (*.pptx)",
              ".html": "Web pages (*.html)", ".png": "PNG pictures (*.png)", ".jpg": "JPEG pictures (*.jpg)",
              ".txt": "Text files (*.txt)", ".csv": "CSV files (*.csv)"}


def _save_types(name):
    """The Save dialog's file type list for a file of this name: its own kind
    first (Windows shows that, not "All files"), then all files."""
    kind = SAVE_TYPES.get(Path(name).suffix.lower())
    return (kind, "All files (*.*)") if kind else ("All files (*.*)",)


def _debug_port():
    """PDFM_DEBUG_PORT: let tests drive the window over the DevTools protocol."""
    try:
        return int(os.environ.get("PDFM_DEBUG_PORT", "")) or None
    except ValueError:
        return None


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


def run_app_window(port=0, open_paths=(), action=None):
    """Open ZPDF as an Edge/Chrome app window. Returns False if no
    such browser is installed. Results are saved like downloads."""
    browser = find_app_browser()
    if not browser:
        return False
    server, url = make_server(port, open_paths=() if action else open_paths, prefer=APP_PORT, action=action)
    # A browser profile of its own keeps the window separate from the user's
    # normal browsing, so closing the app never touches their other windows.
    # A second copy of the app (on another port) gets a temporary profile of
    # its own, so each copy only ever closes its own browser.
    primary = server.server_address[1] == APP_PORT
    profile = _window_profile(server.server_address[1])
    try:
        profile.mkdir(parents=True, exist_ok=True)
        # PDFM_BROWSER_ARGS: more options for the browser (for tests and
        # troubleshooting), and PDFM_DEBUG_PORT a remote debugging port.
        extra = os.environ.get("PDFM_BROWSER_ARGS", "").split()
        if _debug_port():
            extra.append(f"--remote-debugging-port={_debug_port()}")
        proc = subprocess.Popen(
            [browser, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
             "--no-default-browser-check", "--window-size=1180,820", *extra],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        server.server_close()
        return False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _register_instance(server, "app-window", browser=browser, profile=profile)
    _log(f"app window: {browser} on port {server.server_address[1]}, profile {profile}")
    try:
        wait_until_closed(server.RequestHandlerClass, proc)
    except KeyboardInterrupt:
        pass
    _unregister_instance(server)
    _log("app window: instance file removed")
    server.shutdown()
    server.server_close()
    _log("app window: server stopped")
    _close_browser(proc, profile, Path(browser).name)
    _log("app window: browser closed")
    if not primary:
        shutil.rmtree(profile, ignore_errors=True)
    return True


def _profile_processes(profile, exe=None):
    """Ids of browser processes running with this profile (Edge and Chrome
    may keep running in the background after their last window closes).
    ``exe`` (e.g. "msedge.exe") narrows the search, which is much quicker
    on Windows."""
    marker = f"--user-data-dir={profile}"
    if sys.platform == "win32":
        # The whole argument (a prefix would also match "...window-9000").
        pattern = re.sub(r"([\\.^$|?*+()\[\]{}])", r"\\\1", marker) + '("|\\s|$)'
        where = f"-Filter \"Name = '{exe}'\" " if exe and re.fullmatch(r"[\w.-]+", exe) else ""
        script = (f"Get-CimInstance Win32_Process {where}| Where-Object {{ $_.CommandLine -and "
                  "($_.CommandLine -match $env:PDFM_PROFILE_RE) } | "
                  "ForEach-Object { $_.ProcessId }")
        started = time.monotonic()
        try:
            out = subprocess.run([office.powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", script],
                                 capture_output=True, text=True, timeout=20,
                                 env={**os.environ, "PDFM_PROFILE_RE": pattern},
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            _log(f"profile processes: {exc}")
            return []
        pids = [int(p) for p in out.stdout.split() if p.isdigit()]
        _log(f"profile processes ({exe or 'any'}): {pids} in {time.monotonic() - started:.1f}s"
             + (f"; stderr: {out.stderr.strip()[:200]}" if out.stderr.strip() else ""))
        return pids
    pids = []
    for entry in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = entry.read_bytes().decode("utf-8", "replace").split("\0")
            if marker in args:
                pids.append(int(entry.parent.name))
        except (OSError, ValueError):
            continue
    return pids


def _close_browser(proc, profile, exe=None):
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
    for pid in _profile_processes(profile, exe):
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _win32_bring_forward(title):
    """Bring this program's window called ``title`` to the front through
    Windows itself: nothing is asked of the window's own thread. True when
    a window was found."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    mine = os.getpid()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == mine and user32.IsWindowVisible(hwnd):
            text = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, text, 512)
            if text.value == title:
                found.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    for hwnd in found:
        if user32.IsIconic(hwnd):
            user32.ShowWindowAsync(hwnd, 9)  # SW_RESTORE, without waiting for the window
        user32.SetForegroundWindow(hwnd)
    return bool(found)


def _raise_window(window):
    """Bring the pywebview window to the front (a file was handed to it).
    This runs on the server's thread. On Windows the window is raised
    through the system: pywebview's own calls wait for the window's thread,
    and the window froze when that thread was busy opening the file."""
    if sys.platform == "win32":
        try:
            if _win32_bring_forward(window.title):
                return
        except Exception:  # noqa: BLE001 - raising the window is a courtesy
            return
        return
    try:
        window.restore()
        window.show()
        window.on_top = True
        window.on_top = False
    except Exception:
        pass


def _rename_old_install():
    """Shortcuts made before the app was called ZPDF get the new name and icon."""
    from pdfmanager import update, winsetup

    if update.how_installed() != "pip":
        return
    try:
        if winsetup.rename_old_install():
            _log("launch: renamed the shortcuts of an earlier install")
    except Exception as exc:  # noqa: BLE001 - the app starts all the same
        _log(f"launch: renaming the old shortcuts failed: {exc!r}")


def launch(open_paths=(), port=0, action=None):
    """Open the app the best way available: its own pywebview window, else
    an Edge/Chrome app window, else a browser tab. Returns once closed. If
    the app is already running, it gets the files instead. ``action``: a
    right-click action to run on the files (quickactions.ACTIONS) rather
    than opening them."""
    _startup.acquire()
    applog.setup()
    applog.started(update.how_installed())
    _log(f"launch: {list(map(str, open_paths))} port {port}" + (f" action {action}" if action else ""))
    try:
        if not port and forward_to_running_app(open_paths, action=action):
            _log("launch: handed to the running copy")
            return
        if sys.platform == "win32":
            threading.Thread(target=_rename_old_install, daemon=True).start()
        # PDFM_WINDOW=desktop, app-window or browser uses only that kind of
        # window (for tests and troubleshooting).
        choice = os.environ.get("PDFM_WINDOW", "").strip().lower()
        todo = (action, list(open_paths)) if action else None
        if choice in ("", "desktop") and run_desktop(port=port, open_paths=open_paths, action=todo):
            return
        if choice in ("", "app-window") and run_app_window(port=port, open_paths=open_paths, action=todo):
            return
        serve(port=port, open_paths=open_paths, exit_when_closed=True, action=todo)
    finally:
        _startup.release()


def main_app(argv=None):
    """Entry point for the `pdfm-app` launcher. PDFs given as arguments
    (e.g. by "Open with…" or a file association) open in the reader."""
    args = sys.argv[1:] if argv is None else argv
    launch([a for a in args if Path(a).suffix.lower() == ".pdf" and Path(a).is_file()])
