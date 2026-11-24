# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tool operation specs (OPERATIONS) and execution helpers for the ZPDF local web app."""

import base64
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
from pdfmanager.server.common import (
    EXPECTED_ERRORS, UserError, _bool, _file_mentions, _log, _num, _pages,
    _safe_name, lazy_module,
)

accessibility = lazy_module("pdfmanager.accessibility")
archive = lazy_module("pdfmanager.archive")
certcrypt = lazy_module("pdfmanager.certcrypt")
certsign = lazy_module("pdfmanager.certsign")
comments = lazy_module("pdfmanager.comments")
compare = lazy_module("pdfmanager.compare")
core = lazy_module("pdfmanager.core")
filetools = lazy_module("pdfmanager.filetools")
formdetect = lazy_module("pdfmanager.formdetect")
forms = lazy_module("pdfmanager.forms")
layers = lazy_module("pdfmanager.layers")
ocr = lazy_module("pdfmanager.ocr")
office = lazy_module("pdfmanager.office")
organize = lazy_module("pdfmanager.organize")
outline = lazy_module("pdfmanager.outline")
pdfacheck = lazy_module("pdfmanager.pdfacheck")
protection = lazy_module("pdfmanager.protection")
recipes = lazy_module("pdfmanager.recipes")
redact = lazy_module("pdfmanager.redact")
risks = lazy_module("pdfmanager.risks")
scan = lazy_module("pdfmanager.scan")
search = lazy_module("pdfmanager.search")
watchfolders = lazy_module("pdfmanager.watchfolders")

# Lightweight copies of static choice tuples/dicts so OPERATIONS can be built
# without eagerly importing pypdf, cryptography, or heavy submodules at startup.
_CERT_POSITIONS = ("bottom-right", "bottom-left", "bottom-center",
                   "top-right", "top-left", "top-center")
_PDFA_LEVELS = ("1a", "1b", "2a", "2b", "2u", "3a", "3b", "3u")
_ORGANIZE_GROUPINGS = ("year", "month", "author", "pages", "size", "title-letter")
_STYLE_NAMES = {"1": "1, 2, 3", "i": "i, ii, iii", "I": "I, II, III",
                "a": "a, b, c", "A": "A, B, C", "": "no number (prefix only)"}
_CHANGES_TEXT = {
    "any": "any changes except extracting pages",
    "comments": "commenting, filling in forms and signing",
    "forms": "filling in forms and signing",
    "pages": "inserting, deleting and turning pages",
    "none": "no changes",
}

_TRANSLATE_LANGS = [
    ["en", "English"],
    ["zh-Hans", "简体中文 (Chinese Simplified)"],
    ["zh-Hant", "繁體中文 (Chinese Traditional)"],
    ["es", "Español (Spanish)"],
    ["fr", "Français (French)"],
    ["de", "Deutsch (German)"],
    ["ja", "日本語 (Japanese)"],
    ["ko", "한국어 (Korean)"],
    ["ar", "العربية (Arabic)"],
    ["pt", "Português (Portuguese)"],
    ["ru", "Русский (Russian)"],
    ["it", "Italiano (Italian)"],
    ["nl", "Nederlands (Dutch)"],
    ["pl", "Polski (Polish)"],
    ["tr", "Türkçe (Turkish)"],
    ["hi", "हिन्दी (Hindi)"],
    ["vi", "Tiếng Việt (Vietnamese)"],
    ["uk", "Українська (Ukrainian)"],
    ["af", "Afrikaans"],
    ["sq", "Shqip (Albanian)"],
    ["am", "አማርኛ (Amharic)"],
    ["hy", "Հայերեն (Armenian)"],
    ["az", "Azərbaycan (Azerbaijani)"],
    ["eu", "Euskara (Basque)"],
    ["be", "Беларуская (Belarusian)"],
    ["bn", "বাংলা (Bengali)"],
    ["bs", "Bosanski (Bosnian)"],
    ["bg", "Български (Bulgarian)"],
    ["my", "မြန်မာ (Burmese)"],
    ["ca", "Català (Catalan)"],
    ["ceb", "Cebuano"],
    ["hr", "Hrvatski (Croatian)"],
    ["cs", "Čeština (Czech)"],
    ["da", "Dansk (Danish)"],
    ["eo", "Esperanto"],
    ["et", "Eesti (Estonian)"],
    ["tl", "Filipino / Tagalog"],
    ["fi", "Suomi (Finnish)"],
    ["gl", "Galego (Galician)"],
    ["ka", "ქართული (Georgian)"],
    ["el", "Ελληνικά (Greek)"],
    ["gu", "ગુજરાતી (Gujarati)"],
    ["ht", "Kreyòl Ayisyen (Haitian Creole)"],
    ["ha", "Hausa"],
    ["he", "עברית (Hebrew)"],
    ["hu", "Magyar (Hungarian)"],
    ["is", "Íslenska (Icelandic)"],
    ["ig", "Igbo"],
    ["id", "Bahasa Indonesia (Indonesian)"],
    ["ga", "Gaeilge (Irish)"],
    ["jv", "Basa Jawa (Javanese)"],
    ["kn", "ಕನ್ನಡ (Kannada)"],
    ["kk", "Қазақша (Kazakh)"],
    ["km", "ខ្មែរ (Khmer)"],
    ["ku", "Kurdî (Kurdish)"],
    ["ky", "Кыргызча (Kyrgyz)"],
    ["lo", "ລາວ (Lao)"],
    ["la", "Latina (Latin)"],
    ["lv", "Latviešu (Latvian)"],
    ["lt", "Lietuvių (Lithuanian)"],
    ["lb", "Lëtzebuergesch (Luxembourgish)"],
    ["mk", "Македонски (Macedonian)"],
    ["ms", "Bahasa Melayu (Malay)"],
    ["ml", "മലയാളം (Malayalam)"],
    ["mt", "Malti (Maltese)"],
    ["mr", "मराठी (Marathi)"],
    ["mn", "Монгол (Mongolian)"],
    ["ne", "नेपाली (Nepali)"],
    ["no", "Norsk (Norwegian)"],
    ["ps", "پښتو (Pashto)"],
    ["fa", "فارسی (Persian)"],
    ["pa", "ਪੰਜਾਬੀ (Punjabi)"],
    ["ro", "Română (Romanian)"],
    ["sr", "Српски (Serbian)"],
    ["sd", "سنڌي (Sindhi)"],
    ["si", "සිංහල (Sinhala)"],
    ["sk", "Slovenčina (Slovak)"],
    ["sl", "Slovenščina (Slovenian)"],
    ["so", "Soomaali (Somali)"],
    ["su", "Basa Sunda (Sundanese)"],
    ["sw", "Kiswahili (Swahili)"],
    ["sv", "Svenska (Swedish)"],
    ["tg", "Тоҷикӣ (Tajik)"],
    ["ta", "தமிழ் (Tamil)"],
    ["te", "తెలుగు (Telugu)"],
    ["th", "ไทย (Thai)"],
    ["tk", "Türkmen (Turkmen)"],
    ["ur", "اردو (Urdu)"],
    ["uz", "Oʻzbek (Uzbek)"],
    ["cy", "Cymraeg (Welsh)"],
    ["xh", "isiXhosa (Xhosa)"],
    ["yi", "ייִדיש (Yiddish)"],
    ["yo", "Yorùbá (Yoruba)"],
    ["zu", "isiZulu (Zulu)"],
]


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
     "fields": [_f("mode", "Compare", "select", "both",
                   choices=[["both", "Text and visual layout (recommended)"],
                            ["text", "Text changes only (ignore layout shifts)"],
                            ["visual", "Visual / layout changes only"]]),
                _f("all_pages", "Include unchanged pages in the report", "check", False),
                _f("export_json", "Also save structured JSON diff summary", "check", False)]},
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
                   choices=[[k, v] for k, v in _STYLE_NAMES.items()]),
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
    {"id": "booklet", "live": True, "group": "Layout", "label": "Booklet", "files": "pdf",
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
                        "behind it is reduced."),
                _f("target_mb", "Target file size in MB (optional)", "number", "", min=0.1, step=0.5,
                   placeholder="e.g. 5",
                   hint="Leave blank to use the preset above, or enter a maximum size "
                        "(e.g. 5 MB for email) to auto-tune quality.")]},
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
                _f("quality", "Accuracy", "select", "",
                   choices=[["", "Use Settings default"],
                            ["standard", "Standard (fast)"],
                            ["best", "Best accuracy (slower)"]],
                   hint="Best accuracy downloads larger models once (built-in engine)."),
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
    {"id": "flatten-layers", "group": "Stamp", "label": "Flatten layers", "files": "pdf",
     "action": "Flatten layers",
     "help": "For drawings exported from CAD with layers: keep the layers that show when the "
             "file opens as part of the page, take the hidden layers' lines and text out for good, "
             "and remove the layers, so everyone sees the same clean set.",
     "fields": [_f("hide", "Also hide these layers", "text", "", placeholder="Dimensions, Notes",
                   hint="Layer names, separated by commas. Leave empty to keep the layers as the file opens."),
                _f("show", "Also show these layers", "text", "", placeholder="Walls")]},
    {"id": "form", "group": "Stamp", "label": "Add form fields", "files": "pdf", "panel": "form",
     "help": "Make a PDF fillable: Find fields picks out the lines, boxes and tick boxes of a "
             "printed form, and you can draw more where people should type, tick, choose or "
             "sign. It can then be filled in with any PDF reader, including the reader here. "
             "The fields a form already has can be moved, resized, renamed or deleted.",
     "fields": [_f("detect", "Find the fields in each file", "check", False,
                   hint="For several copies of different forms: each file gets the fields "
                        "found in it, as well as any you drew."),
                _f("font_size", "Text size", "select", "0",
                   choices=[["0", "Fit the box"], ["8", "8 pt"], ["10", "10 pt"], ["12", "12 pt"],
                            ["14", "14 pt"]]),
                _f("border", "Draw a border around each field", "check", True),
                _f("required", "Make text fields required", "check", False),
                _f("fields", "", "hidden", ""), _f("edits", "", "hidden", "")]},
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
             "and the replies) as a PDF, Markdown summary, or spreadsheet; or move "
             "comments between copies of a document with XFDF files (Acrobat reads and "
             "writes them).",
     "fields": [_f("mode", "Make", "select", "summary",
                   choices=[["summary", "A summary (PDF)"], ["markdown", "A Markdown summary (.md)"],
                            ["csv", "A spreadsheet (CSV)"],
                            ["export", "A comments file to send (XFDF)"],
                            ["import", "Add comments from an XFDF file"]]),
                _f("group_by", "Group by", "select", "page",
                   choices=[["page", "Page"], ["type", "Annotation type"], ["author", "Author"]],
                   show_if={"mode": "markdown"}),
                _f("author", "Filter by author (blank = all)", "text", "", placeholder="all",
                   show_if={"mode": ["summary", "markdown", "csv"]}),
                _f("kind", "Filter by type (blank = all)", "text", "", placeholder="e.g. Highlight, Note, Cloud",
                   show_if={"mode": ["summary", "markdown", "csv"]}),
                _f("xfdf", "Comments file (.xfdf)", "file", None, accept=".xfdf,.xml",
                   show_if={"mode": "import"})]},
    {"id": "fill-many", "group": "Stamp", "label": "Fill many (mail merge)", "files": "pdf",
     "action": "Fill forms",
     "help": "Make one filled PDF for each row of a spreadsheet (CSV, Excel, or JSON), "
             "matching column names to form fields and optionally naming each file from a column.",
     "fields": [_f("data", "Spreadsheet or data file (.csv, .xlsx, .json)", "file", None,
                   accept=".csv,.xlsx,.json"),
                _f("name_column", "Name each PDF from column (optional)", "text", "",
                   placeholder="e.g. Name or ID")]},
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
     "fields": [_f("new_password", "Password to open", "password", "", strength=True,
                   hint="Leave it empty to let anyone open the PDF, with the limits below."),
                _f("printing", "Printing", "select", "high",
                   choices=[["high", "Allowed"], ["low", "Low quality only"],
                            ["none", "Not allowed"]]),
                _f("changes", "Changes", "select", "any",
                   choices=[[k, v[0].upper() + v[1:]] for k, v in _CHANGES_TEXT.items()]),
                _f("copy", "Allow copying text and pictures", "check", True),
                _f("owner_password", "Permissions password", "password", "",
                   hint="Needed for the limits above, and different from the password to "
                        "open: it lifts the limits (and removes the protection).")]},
    {"id": "encrypt-cert", "group": "Security", "label": "Encrypt for people", "files": "pdf",
     "action": "Protect",
     "help": "Protect a PDF for particular people instead of with a password: only they can open "
             "it, with their Digital ID, and there's no password to send or to be guessed. You "
             "need each person's certificate: the public part of their Digital ID, a .cer file "
             "they can give to anyone. Add your own too, or you won't be able to open the result. "
             "Acrobat opens these files as well.",
     "fields": [_f("certificates", "Their certificates (.cer, .crt, .pem)", "file", None, multiple=True,
                   accept=".cer,.crt,.pem,.der,.pfx,.p12",
                   hint="One file per person, or one file holding several. Your own Digital ID "
                        "(.pfx) can be given here for yourself, with its password below."),
                _f("id_password", "Your Digital ID's password (only if you added it)", "password", "")]},
    {"id": "decrypt-cert", "group": "Security", "label": "Open with Digital ID", "files": "pdf",
     "action": "Open",
     "help": "Open a PDF that was protected for particular people, with your Digital ID (.pfx or "
             ".p12). You get an unprotected copy to read and work on; the protected file stays "
             "as it is.",
     "fields": [_f("pfx", "Digital ID (.pfx or .p12 file)", "file", None, accept=".pfx,.p12"),
                _f("pfx_password", "Digital ID password", "password", "")]},
    {"id": "upgrade-protection", "group": "Security", "label": "Upgrade protection", "files": "pdf",
     "action": "Upgrade protection",
     "help": "For a PDF protected years ago or by an old program: its encryption (RC4) no longer "
             "protects it. This writes the same file with AES-256, with the same password and the "
             "same limits on printing, copying and changes. Enter its password in the box above "
             "the Run button: the permissions password if it has one, so that it's kept too.",
     "fields": []},
    {"id": "decrypt", "group": "Security", "label": "Remove password", "files": "pdf",
     "help": "Enter the current password in the box above the Run button.", "fields": []},
    {"id": "redact", "group": "Security", "label": "Redact", "files": "pdf", "panel": "redact", "action": "Black out",
     "help": "Permanently black out names, numbers or areas. What's under the black boxes "
             "is taken out of the file for good.",
     "fields": [_f("terms", "Words or phrases", "text", "", placeholder="John Smith, 555-1234",
                   hint="separate several with commas"),
                _f("regex", "Custom regular expression (optional)", "text", "",
                   placeholder=r"CASE-\d{6}",
                   hint="matches any pattern using Python regular expression syntax"),
                # (how the words match beside them; the kinds of numbers on a row of their own)
                _f("case_sensitive", "Match upper/lower case", "check", False),
                _f("whole_words", "Whole words only", "check", False),
                _f("emails", "All email addresses", "check", False),
                _f("phones", "All phone numbers", "check", False),
                _f("numbers", "Card, account and ID numbers", "check", False),
                _f("ssn", "US Social Security Numbers (SSN)", "check", False),
                _f("ein", "US Employer IDs (EIN)", "check", False),
                _f("uk_ni", "UK National Insurance numbers", "check", False),
                _f("iban", "EU / International IBANs", "check", False),
                _f("credit_card", "Payment cards (Luhn-verified)", "check", False),
                _f("reason_code", "Redaction reason code (optional)", "text", "",
                   placeholder="(b)(6) or GDPR Art. 17",
                   hint="Recorded in the redaction log and stamped on rasterized boxes."),
                _f("export_log", "Also save redaction log (.json)", "check", False),
                # -- true redaction --
                _f("method", "How", "select", "remove",
                   choices=[["remove", "Remove what's under the boxes — the rest stays searchable"],
                            ["image", "Turn the pages into pictures — the most thorough"]],
                   hint="A page that can't be edited safely becomes a picture instead; the "
                        "notes say which."),
                # -- end of true redaction --
                _f("boxes", "", "hidden", "")]},
    {"id": "sec-profile", "group": "Security", "label": "Security profile", "files": "pdf",
     "action": "Apply profile",
     "help": "Apply a named bundle of security steps (Make safe, strip hidden data, watermark, "
             "and optional AES-256 permissions) in one click, recorded in the audit log.",
     "fields": [_f("profile", "Profile", "select", "Confidential",
                   choices=[["Confidential", "Confidential — Make safe, strip hidden data, CONFIDENTIAL watermark"],
                            ["Internal", "Internal — Make safe, strip hidden data, dynamic user/date watermark"],
                            ["Public Share", "Public Share — Make safe, strip hidden data & comments"]]),
                _f("user_password", "Password to open (optional)", "password", "",
                   hint="Leave blank to apply the profile without adding a password."),
                _f("owner_password", "Permissions password (optional)", "password", "")]},
    {"id": "sanitize", "group": "Security", "label": "Remove hidden data", "files": "pdf", "action": "Remove hidden data",
     "help": "Remove information that travels unseen inside a PDF before you share it.",
     "fields": [_f("metadata", "Author, title, dates and other document details", "check", True),
                _f("javascript", "Scripts and actions that run programs", "check", True),
                _f("attachments", "Attached files", "check", True),
                _f("comments", "Comments, highlights and notes", "check", False)]},
    {"id": "makesafe", "group": "Security", "label": "Make safe", "files": "pdf", "action": "Make safe",
     "help": "For a PDF from someone you don't know: take out what could do harm (scripts, actions "
             "that start programs or send what you type, attached files that can run) and keep the "
             "rest: pages, text, comments, forms with their totals. ZPDF never runs any of these "
             "itself; this is for the copy you keep or pass on.",
     "fields": []},
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
                   choices=[[p, p.replace("-", " ").capitalize()] for p in _CERT_POSITIONS]),
                _f("size", "Size", "select", "medium", show_if={"appearance": "visible"},
                   choices=[["small", "Small"], ["medium", "Medium"], ["large", "Large"]]),
                _f("pades_level", "PAdES level (ETSI EN 319 142)", "select", "B-B",
                   choices=[["B-B", "B-B (basic, offline)"],
                            ["B-T", "B-T (with RFC 3161 timestamp)"],
                            ["B-LT", "B-LT (long-term validation data)"],
                            ["B-LTA", "B-LTA (archival timestamp)"]]),
                _f("certify", "Certification (DocMDP)", "select", "",
                   choices=[["", "Approval signature (standard)"],
                            ["no-changes", "Certify: no changes allowed"],
                            ["fill-and-sign", "Certify: allow form filling & signing"],
                            ["fill-sign-comment", "Certify: allow forms, signing & comments"]]),
                _f("lock_fields", "Lock form fields after signing (FieldMDP, optional)", "text", "",
                   placeholder="e.g. all, or Amount, Vendor",
                   hint="Leave blank to leave fields unlocked, or type 'all' or field names."),
                _f("timestamp_url", "Timestamp server (optional)", "text", "",
                   placeholder="http://timestamp.digicert.com",
                   hint="A trusted clock vouches for the time of signing; required for PAdES B-T/B-LT/B-LTA.")]},
    {"id": "verify-sig", "group": "Security", "label": "Check signatures", "files": "pdf",
     "action": "Check",
     "help": "Check the digital signatures in PDFs: who signed, when, and whether the "
             "document was changed afterwards. You get a short report; nothing is changed.",
     "fields": [_f("trust", "Certificate you trust (optional)", "file", None,
                   accept=".cer,.crt,.pem,.der",
                   hint="e.g. your company's root certificate: signatures from it count as "
                        "verified"),
                _f("report_format", "Report format", "select", "txt",
                   choices=[["txt", "Plain text (.txt)"],
                            ["pdf", "PDF report (.pdf)"],
                            ["xml", "ETSI XML report (.xml)"]])]},
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
                   + [[lv, f"PDF/A-{lv}"] for lv in _PDFA_LEVELS],
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
                   hint="A table that continues on the next page is kept as one table."),
                _f("auto_ocr", "Run OCR automatically on scanned pages", "check", True,
                   hint="If a page is a scan with no text layer, ZPDF runs OCR before extracting tables.")]},
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
    {"id": "to-md", "group": "Convert", "label": "PDF → Markdown", "files": "pdf",
     "help": "Convert PDFs to clean, structured Markdown (.md) with headings, lists, links, "
             "and pipe tables — ready for notes, docs, and AI tools.",
     "fields": [_f("pages", "Pages (blank = all)", "text", "", placeholder="all"),
                _f("images", "Extract pictures to an assets folder", "check", False),
                _f("page_breaks", "Insert page separators", "check", False),
                _f("front_matter", "Include YAML metadata front matter", "check", False)]},
    {"id": "translate", "group": "Convert", "label": "Translate PDF", "files": "pdf",
     "action": "Translate",
     "help": "Translate PDFs offline using MADLAD-400 (400+ languages), OPUS-MT, or an installed language pack, "
             "or via a local/cloud OpenAI-compatible model configured under Settings → Language. Numbers, dates, "
             "URLs, emails, code blocks, and reference codes (such as INV-2026-118) stay unchanged.",
     "fields": [_f("source_lang", "From language", "select", "auto",
                   choices=[["auto", "Auto-detect"], *_TRANSLATE_LANGS]),
                _f("target_lang", "To language", "select", "es",
                   choices=list(_TRANSLATE_LANGS)),
                _f("mode", "Output layout", "select", "side-by-side",
                   choices=[["side-by-side", "Side by side (2-page spread)"],
                            ["bilingual-pages", "Bilingual book (alternating pages)"],
                            ["bilingual", "Bilingual stacked (paragraph reflow)"],
                            ["in-place", "In-place (same layout)"],
                            ["text", "Plain text (.txt)"]],
                   hint="For books with covers, images & tables, use Bilingual book or Side by side. "
                        "Bilingual stacked reflows paragraphs cleanly and keeps full-page images."),
                _f("pages", "Pages (optional)", "text", "",
                   placeholder="All pages, or e.g. 1-5, 8"),
                _f("auto_ocr", "Auto-OCR scanned pages (if no selectable text)", "check", False)]},
    # Quantities: a schedule of what was measured on a drawing (pdfmanager/quantities.py)
    {"id": "quantities", "group": "Convert", "label": "Quantities from a drawing", "files": "pdf",
     "help": "A schedule (takeoff) of everything measured on a drawing: the dimensions and measurements "
             "saved in the PDF by ZPDF or another program, each worked out from its own points and "
             "scale, with totals per group. Numbers are numbers, so they can be summed.",
     "fields": [_f("format", "Format", "select", "xlsx",
                   choices=[["xlsx", "Excel workbook (.xlsx)"], ["csv", "CSV file"]],
                   hint="Measurements still only in the reader's list aren't in the file yet: export them "
                        "from the Measurements panel, or put them in the PDF first.")]},
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
    {"id": "to-dxf", "group": "Convert", "label": "PDF → DXF", "files": "pdf",
     "help": "Export vector lines, curves, solid fills, layers and text to a CAD DXF file, "
             "preserving the drawing's scale.",
     "fields": [_f("pages", "Pages (blank = all)", "text", "", placeholder="all"),
                _f("scale", "Scale (blank = from PDF)", "text", "", placeholder="e.g. 1:100")]},
    {"id": "from-drawing", "group": "Convert", "label": "Drawing → PDF", "files": "drawing",
     "help": "Convert CAD drawings (.dxf, or .dwg if ODA File Converter is installed) to "
             "layered, scaled PDFs.",
     "fields": [_f("layout", "Layout", "text", "Model", placeholder="Model"),
                _f("paper", "Paper size", "select", "A3",
                   choices=[["A4", "A4"], ["A3", "A3"], ["A2", "A2"], ["A1", "A1"], ["A0", "A0"],
                            ["Letter", "Letter"], ["Tabloid", "Tabloid"], ["Arch D", "Arch D"]]),
                _f("scale", "Plot scale", "text", "fit", placeholder="fit or 1:100"),
                _f("monochrome", "Monochrome (black lines)", "check", False)]},
    {"id": "sheet-sets", "group": "Convert", "label": "Sheet sets", "files": "pdf",
     "help": "Work with multi-sheet architectural or engineering drawing sets: split into "
             "one file per sheet named from its title block, label pages with sheet numbers, "
             "or link detail references (e.g. 2/A-102, SEE A-201) to the target sheet.",
     "fields": [_f("action", "Action", "select", "split",
                   choices=[["split", "Split into sheets (one PDF per sheet)"],
                            ["name", "Name pages (set page labels to sheet numbers)"],
                            ["link", "Link detail references between sheets"]])]},
    {"id": "compare-sets", "group": "Convert", "label": "Compare sheet sets", "files": "pdf2", "min": 2, "max": 2,
     "help": "Compare two revisions of a drawing set sheet by sheet, matched by sheet number "
             "rather than page order. Lists changed, added and removed sheets and makes a "
             "visual overlay diff PDF of the changed sheets.",
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
                   choices=[[g, g.replace("-", " ").capitalize()] for g in _ORGANIZE_GROUPINGS]),
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

SUFFIX = {"form": "form", "media": "media", "headers": "headers", "recipe": "recipe", "redact": "redacted", "bookmarks": "bookmarks", "flatten": "flat", "sanitize": "clean", "makesafe": "safe", "upgrade-protection": "protected", "encrypt-cert": "for-people", "decrypt-cert": "opened", "pdfa": "pdfa", "clean": "clean", "extract": "extract", "delete": "edited", "reorder": "reordered",
          "insert-blank": "blanks", "booklet": "booklet", "autocrop": "trimmed",
          "reverse": "reversed", "rotate": "rotated", "resize": "resized", "crop": "cropped",
          "nup": "nup", "compress": "compressed", "color": "color", "ocr": "ocr",
          "watermark": "watermarked", "number": "numbered", "metadata": "edited",
          "encrypt": "protected", "decrypt": "unlocked"}
SUFFIX.update({"sign-cert": "signed", "verify-sig": "signatures"})  # digital ID signatures
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
        core.booklet(src, out, opts.get("size") or None, password)
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
        made = translate.translate_pdf(
            src, out, source_lang, target_lang,
            mode=mode,
            password=password,
            pages=pages_spec,
            auto_ocr=auto_ocr,
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


def _protect(outputs, password, op):
    """PDFs made from a password-protected file get the same password."""
    # A signed PDF keeps its password inside the signed file.
    # (An upgraded protection is already in place, with its two passwords.)
    if password and op not in ("decrypt", "encrypt", "pdfa", "media", "sign-cert", "upgrade-protection",
                               "encrypt-cert", "decrypt-cert"):
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
        from pypdf import PdfReader
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
    try:
        with core.open_pikepdf(path, password) as pdf:
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

    mended = []
    for index, path in enumerate(files):
        path = Path(path)
        if path.suffix.lower() == ".pdf" and _needs_mending(path, password):
            target = Path(work) / "mended" / f"{index:03d}" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with core.open_pikepdf(path, password) as pdf:
                    core.keep_size(pdf).save(target, encryption=pdf.is_encrypted or None)  # (still locked if it was)
                notes.append(f"{path.name} was damaged; it was mended first, as Repair does")
                path = target
            except Exception:  # noqa: BLE001 (it couldn't be mended: the tool tries the file as it is)
                pass
        mended.append(path)
    return mended


def _check_pages(op, files, password):
    """Each PDF has a page to work on (Repair is for files that don't open)."""
    if op in ("repair", "decrypt-cert") or OPS[op].get("files") not in ("pdf", "pdf2"):
        return  # (a file protected for people has no page to count until it's opened)
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


# --------------------------------------------------------------------------
# Thumbnails and results
# --------------------------------------------------------------------------

THUMB_WIDTH = 240


# Tools whose result can be seen before running them: page 1 of it, redrawn
# as the options change.
PREVIEW_OPS = {"watermark", "number", "headers", "resize", "nup", "booklet"}
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
        said = str(exc).replace(str(path), Path(path).name)  # the file's name, not where it's kept
        if certcrypt.is_for_people(path):
            # Not damaged and not asking for a password: it opens with a Digital ID.
            return {"pages": None, "locked": False, "error": said, "for_people": True}
        locked = "encrypted" in str(exc) or "password" in str(exc)
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
