# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Constants, regex patterns, box/matrix geometry, and page view transforms."""

import re

from pdfmanager import core

# A few common languages, recognised by their most frequent short words.
LANGUAGE_WORDS = {
    "en": "the and of to in is that for it with as was on are be this by from or not",
    "de": "der die und das ist nicht ein eine zu den mit von sich des auf für im dem auch",
    "fr": "le la les et des est une un du que pour dans pas qui sur au avec ce sont",
    "es": "el la los las que y en es un una por con para del se no al como",
    "it": "il la di che e è un una per non del della sono gli le con nel anche",
    "nl": "de het een en van is dat op te in niet zijn voor met die er ook",
    "pt": "o a os as de que e do da em um uma para com não é no na",
    "sv": "och att det som en på är av för med till den har inte jag",
    "da": "og at det som en på er af for med til den har ikke jeg",
    "pl": "i w na z do się nie że to jest jak o po ale",
}
LANGUAGE_WORDS = {lang: set(words.split()) for lang, words in LANGUAGE_WORDS.items()}
LANG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$")
DEFAULT_LANG = "en"

# What the checker looks for, in the order the report lists it:
# rule -> how to fix it.
RULES = {
    "security": "Remove the password (Remove password), or protect the file again with ZPDF, "
                "which lets screen readers in.",
    "tagged": "Use Make accessible (tag); on the command line, pdfm tag.",
    "lang": "Make accessible sets it; choose the language there if it guesses wrong.",
    "title": "Set a title (Title & author, or Make accessible's Title box).",
    "title-shown": "Make accessible turns this on.",
    "scanned": "Run OCR first (Make searchable), then Make accessible.",
    "unicode": "Make the PDF again from the original document with the fonts embedded, "
               "or OCR the pages.",
    "content": "Tag the file again with Make accessible, ticking Replace existing tags.",
    "structure": "Tag the file again with Make accessible, ticking Replace existing tags.",
    "figures": "Describe each picture (Acrobat: Set Alternate Text), or give the "
               "descriptions to pdfm tag --alt.",
    "tables": "Tag the file again, or fix the table's tags in Acrobat (Table Editor).",
    "headings": "Fix the heading tags (Acrobat: Tags panel), or tag the file again.",
    "lists": "Fix the list tags (Acrobat: Tags panel), or tag the file again.",
    "links": "Tag the file again with Make accessible.",
    "annotations": "Tag the file again with Make accessible.",
    "forms": "Give each field a tooltip (Acrobat: field Properties → Tooltip).",
    "tabs": "Make accessible sets the tab order to follow the structure.",
    "bookmarks": "Add bookmarks (Bookmarks → Find headings, or `pdfm bookmarks --detect`).",
}

MANUAL_NOTE = ("Two things only a person can check: that the reading order makes sense "
               "(read it with a screen reader, or in Acrobat's Reading Order tool), and "
               "that text stands out clearly enough from its background (colour contrast).")

BOOKMARK_PAGES = 20  # longer documents should have bookmarks

BULLETS = set("•◦▪▫‣⁃●○■□►▶➢➤→◆◇✓✔✗–—-*·∙")
NUMBER_MARK = re.compile(r"^(?:\(?(?:\d{1,3}|[a-z]|[ivx]{1,4})[.)]|\((?:\d{1,3}|[a-z]|[ivx]{1,4})\))$",
                         re.I)
NUMBER_START = re.compile(r"^(?:\(?(?:\d{1,3}|[a-z]|[ivx]{1,4})[.)]|\((?:\d{1,3}|[a-z]|[ivx]{1,4})\))\s",
                          re.I)
NUMBERED_HEADING = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){0,5})\.?\s+\S")
CAPTION = re.compile(
    r"^(?:figure|fig\.?|chart|graph|image|picture|photo|illustration|diagram|map|plate|"
    r"abbildung|abb\.|bild|grafik|figura|imagen|ilustración|gráfico|figuur|afbeelding|"
    r"immagine|illustrazione|tableau|tabelle|tabla|tabella|tabel|table)\s*[\dIVX]",
    re.I)
TABLE_CAPTION = re.compile(r"^(?:table|tableau|tabelle|tabla|tabella|tabel)\s*[\dIVX]", re.I)
PAGE_NUMBER = re.compile(
    r"^(?:[-–—]\s*)?(?:(?:page|p\.|pg\.?|seite|página|pagina|pag\.|blz\.?|sida|side|strona)\s*)?"
    r"(?:\d{1,4}|[ivxlcdm]{1,7})(?:\s*(?:/|of|von|de|di|van|sur|av|z|из)\s*\d{1,4})?(?:\s*[-–—])?$",
    re.I)

_PATH_BUILD = {"m", "l", "c", "v", "y", "h", "re"}
_PATH_PAINT = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*"}
_SHOW = {"Tj", "TJ", "'", '"'}

# The standard 14 fonts and common names for them, for text widths when a
# font gives none.
_STD_FONTS = {
    "helvetica": "Helvetica", "arial": "Helvetica", "arialmt": "Helvetica",
    "times": "Times-Roman", "timesnewroman": "Times-Roman", "timesnewromanpsmt": "Times-Roman",
    "courier": "Courier", "couriernew": "Courier", "symbol": "Symbol",
    "zapfdingbats": "ZapfDingbats",
}


# --------------------------------------------------------------------------
# Small geometry helpers. Boxes are (x0, y0, x1, y1), y upwards.
# --------------------------------------------------------------------------

def _mul(a, b):
    """The matrix ``a`` then ``b`` (PDF's [a b c d e f])."""
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5])


def _apply(m, x, y):
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def _bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _box_through(m, box):
    x0, y0, x1, y1 = box
    return _bbox([_apply(m, x0, y0), _apply(m, x1, y0), _apply(m, x0, y1), _apply(m, x1, y1)])


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _overlap(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _union(boxes):
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _distance(a, b):
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return (dx * dx + dy * dy) ** 0.5


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class _View:
    """From a page's own coordinates to what is seen: the page turned by its
    /Rotate, origin at the bottom left. Layout is read in this space, so
    "left column" and "top of the page" mean what they look like."""

    def __init__(self, mediabox, rotation):
        self.box = [float(v) for v in mediabox]
        x0, y0, x1, y1 = self.box
        self.box = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
        self.rotation = core.quarter_turn(rotation)
        w, h = self.box[2] - self.box[0], self.box[3] - self.box[1]
        self.size = (h, w) if self.rotation in (90, 270) else (w, h)

    def point(self, x, y):
        bx0, by0, bx1, by1 = self.box
        if self.rotation == 90:
            return y - by0, bx1 - x
        if self.rotation == 180:
            return bx1 - x, by1 - y
        if self.rotation == 270:
            return by1 - y, x - bx0
        return x - bx0, y - by0

    def rect(self, box):
        x0, y0, x1, y1 = box
        return _bbox([self.point(x0, y0), self.point(x1, y1)])


def _inherited(page_obj, key):
    node, depth = page_obj, 0
    while node is not None and depth < 50:
        if key in node:
            return node[key]
        node = node.get("/Parent")
        depth += 1
    return None

ARTIFACT = ("artifact", None)
ARTIFACT_HEADER = ("artifact", "Header")
ARTIFACT_FOOTER = ("artifact", "Footer")
ARTIFACT_BACKGROUND = ("artifact", "Background")
UNMARKED = ("unmarked", None)   # a form tagged inside: not wrapped where it's drawn
