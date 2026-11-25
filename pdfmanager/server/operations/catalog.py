# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Declarative ZPDF operation catalog (OPERATIONS, OPS, SUFFIX, defaults)."""

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
     "help": "Print a folded booklet or multi-signature book: two pages side by side on each sheet, "
             "supporting both Western left-to-right and Manga/Japanese right-to-left binding, "
             "multi-signature sections, and creep compensation.",
     "fields": [_f("size", "Sheet size", "select", "",
                   choices=[["", "Twice the page size"]]
                   + [[p, p.upper() if p[0] == "a" else p.capitalize()]
                      for p in ("a4", "letter", "a3", "tabloid", "legal")],
                   hint="Used sideways, with two pages on each side. Twice the page size "
                        "keeps the pages as they are: A5 pages on A4 paper, A4 on A3."),
                _f("binding", "Binding direction", "select", "ltr",
                   choices=[["ltr", "Left-to-right (Western book)"],
                            ["rtl", "Right-to-left (Manga / Japanese)"]]),
                _f("signature", "Signature size", "select", "0",
                   choices=[["0", "Single saddle-stitch booklet"],
                            ["8", "8-page signatures"],
                            ["16", "16-page signatures"],
                            ["32", "32-page signatures"]]),
                _f("creep_mm", "Creep compensation (mm)", "number", "0", min=0, max=10, step=0.25)]},
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
    {"id": "pdf-to-cbz", "group": "Convert", "label": "PDF → CBZ (Comic/Manga)", "files": "pdf",
     "help": "Export a PDF as a .cbz comic or manga archive with WebP, JPEG, or PNG pages and ComicInfo.xml metadata.",
     "fields": [_f("format", "Image format", "select", "webp",
                   choices=[["webp", "WebP (recommended — compact & sharp)"],
                            ["jpg", "JPEG (universal)"],
                            ["png", "PNG (lossless)"]]),
                _f("dpi", "Resolution (DPI)", "number", 150, min=72, max=300),
                _f("manga_rtl", "Tag as Right-to-Left Manga in ComicInfo.xml", "check", False)]},
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
                            ["manga", "Manga / Comic (speech bubbles)"],
                            ["text", "Plain text (.txt)"]],
                   hint="For books with covers, images & tables, use Bilingual book or Side by side. "
                        "Vertical CJK (tategaki) is detected automatically. Use Manga / Comic for speech bubbles."),
                _f("pages", "Pages (optional)", "text", "",
                   placeholder="All pages, or e.g. 1-5, 8"),
                _f("auto_ocr", "Auto-OCR scanned pages (if no selectable text)", "check", False),
                _f("manga_ai", "Enhanced Manga AI (neural bubbles, manga-ocr, LaMa inpainting)", "check", False,
                   hint="Manga mode only: downloads optional Apache-2.0 ONNX models once for borderless text, "
                        "Japanese manga fonts, and screentone inpainting.")]},
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
