"""Fonts for text the app writes onto pages (headers, text edits).

The standard PDF fonts (Helvetica, Times, Courier) need no font file but
only have Western letters; other words are written with an installed
TrueType font that has them, or a font that PDF readers supply themselves
for Chinese, Japanese and Korean.
"""

import unicodedata
from pathlib import Path

BASE14 = {  # (family, bold, italic) -> standard PDF font
    ("sans", False, False): "Helvetica", ("sans", True, False): "Helvetica-Bold",
    ("sans", False, True): "Helvetica-Oblique", ("sans", True, True): "Helvetica-BoldOblique",
    ("serif", False, False): "Times-Roman", ("serif", True, False): "Times-Bold",
    ("serif", False, True): "Times-Italic", ("serif", True, True): "Times-BoldItalic",
    ("mono", False, False): "Courier", ("mono", True, False): "Courier-Bold",
    ("mono", False, True): "Courier-Oblique", ("mono", True, True): "Courier-BoldOblique",
}
SERIF_NAMES = ("times", "serif", "roman", "georgia", "garamond", "cambria", "minion", "book",
               "palatino", "baskerville", "caslon", "didot", "song", "ming", "batang")
MONO_NAMES = ("courier", "mono", "consol", "menlo", "inconsolata", "typewriter")
BOLD_NAMES = ("bold", "black", "heavy", "semibold", "demi")
# Fonts with many scripts, for words the standard fonts can't show. Only
# TrueType fonts work here (not the CFF-based .otf/.ttc ones).
UNICODE_FONTS = [
    r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\msgothic.ttc",
    r"C:\Windows\Fonts\malgun.ttf", r"C:\Windows\Fonts\Nirmala.ttf", r"C:\Windows\Fonts\seguisym.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
]
_loaded = {}


def font_for(text, family="sans", bold=False, italic=False):
    """A reportlab font name able to show ``text``, close to the style."""
    try:
        text.encode("cp1252")
        return BASE14[(family, bold, italic)]
    except UnicodeEncodeError:
        pass
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    needed = {ord(ch) for ch in text if not ch.isspace()}
    for path in UNICODE_FONTS:
        if not Path(path).is_file():
            continue
        if path not in _loaded:
            name = "PdfmFont" + str(len(_loaded))
            try:
                font = TTFont(name, path, subfontIndex=0) if path.lower().endswith(".ttc") \
                    else TTFont(name, path)
                pdfmetrics.registerFont(font)
                _loaded[path] = (name, set(font.face.charToGlyph))
            except Exception:  # not a TrueType font reportlab can use
                _loaded[path] = None
        if _loaded[path] and needed <= _loaded[path][1]:
            return _loaded[path][0]
    # Chinese, Japanese and Korean without a suitable font installed: fonts
    # that PDF readers supply themselves.
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    names = " ".join(unicodedata.name(ch, "") for ch in text)
    cid = ("HYSMyeongJo-Medium" if "HANGUL" in names else
           "HeiseiMin-W3" if "HIRAGANA" in names or "KATAKANA" in names else "STSong-Light")
    if cid not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(cid))
    return cid
