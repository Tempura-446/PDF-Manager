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


# ---- the document's own fonts ---------------------------------------------------------

def _hex_codes(token):
    return bytes.fromhex(token.decode("latin-1"))


def _utf16(data):
    try:
        return data.decode("utf-16-be")
    except UnicodeDecodeError:
        return ""


def parse_to_unicode(data):
    """{code bytes: text} from a ToUnicode CMap's bfchar and bfrange lists."""
    import re

    found = {}
    hexes = rb"<([0-9A-Fa-f\s]*)>"
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(hexes + rb"\s*" + hexes, block):
            src, dst = _hex_codes(re.sub(rb"\s", b"", src)), _hex_codes(re.sub(rb"\s", b"", dst))
            found[src] = _utf16(dst)
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for lo, hi, rest in re.findall(hexes + rb"\s*" + hexes + rb"\s*(<[0-9A-Fa-f\s]*>|\[[^\]]*\])", block):
            lo, hi = re.sub(rb"\s", b"", lo), re.sub(rb"\s", b"", hi)
            size, first, last = len(lo) // 2, int(lo or b"0", 16), int(hi or b"0", 16)
            if last < first or last - first > 0xFFFF:
                continue
            if rest.startswith(b"["):
                targets = [_hex_codes(re.sub(rb"\s", b"", t)) for t in re.findall(hexes, rest)]
                for i, dst in enumerate(targets[:last - first + 1]):
                    found[(first + i).to_bytes(size, "big")] = _utf16(dst)
                continue
            dst = _hex_codes(re.sub(rb"\s", b"", rest[1:-1]))
            if not dst:
                continue
            base, tail = int.from_bytes(dst[-2:], "big"), dst[:-2]
            for i in range(last - first + 1):
                if base + i > 0xFFFF:
                    break
                found[(first + i).to_bytes(size, "big")] = _utf16(tail + (base + i).to_bytes(2, "big"))
    return found


def _glyph_char(name):
    """The letter a glyph name stands for (Adobe's glyph list, uniXXXX, uXXXX)."""
    try:
        from pypdf._codecs import adobe_glyphs
        if "/" + name in adobe_glyphs:
            return adobe_glyphs["/" + name]
    except ImportError:
        pass
    for prefix, digits in (("uni", 4), ("u", 4)):
        hexa = name[len(prefix):]
        if name.startswith(prefix) and len(hexa) >= digits:
            try:
                return chr(int(hexa[:6] if prefix == "u" else hexa[:4], 16))
            except ValueError:
                return ""
    return name if len(name) == 1 else ""


_BASE_CODECS = {"/WinAnsiEncoding": "cp1252", "/MacRomanEncoding": "mac_roman", "/StandardEncoding": "ascii"}


def pdf_font_codes(font):
    """{letter: code bytes} for the letters a PDF font dictionary can surely
    show: the ones its ToUnicode map names (a font cut down to the letters a
    document uses still has those) and, for a complete or standard font,
    every letter of its encoding. Empty if the font can't be reused."""
    font = font.get_object()
    subtype = font.get("/Subtype")
    if subtype == "/Type3":
        return {}
    name = str(font.get("/BaseFont", ""))
    subset = len(name) > 8 and name[7] == "+" and name[1:7].isupper()
    codes = {}
    if subtype == "/Type0":
        if str(font.get("/Encoding", "")).endswith("-V"):
            return {}  # vertical writing
    else:
        if name.lstrip("/") in ("Symbol", "ZapfDingbats"):
            return {}
        encoding = font.get("/Encoding")
        encoding = encoding.get_object() if encoding is not None else None
        base, differences = None, []
        if isinstance(encoding, dict):
            base = encoding.get("/BaseEncoding")
            differences = encoding.get("/Differences", [])
        elif encoding is not None:
            base = encoding
        if not subset:
            codec = _BASE_CODECS.get(str(base) if base is not None else
                                     ("/StandardEncoding" if subtype == "/Type1" else "/WinAnsiEncoding"))
            if codec:
                for code in range(32, 256):
                    try:
                        codes.setdefault(bytes([code]).decode(codec), bytes([code]))
                    except UnicodeDecodeError:
                        pass
        code = 0
        for item in differences:
            if isinstance(item, int):
                code = item
                continue
            letter = _glyph_char(str(item).lstrip("/"))
            if letter and 0 <= code < 256:
                codes[letter] = bytes([code])  # a renamed code wins over the base encoding
            code += 1
    if "/ToUnicode" in font:
        try:
            for code, text in parse_to_unicode(font["/ToUnicode"].get_object().get_data()).items():
                if len(text) == 1 and code:
                    codes.setdefault(text, code)
        except Exception:  # noqa: BLE001 - a broken map: the encoding alone
            pass
    if subtype == "/Type0" and "/ToUnicode" not in font:
        return {}
    return codes


def font_program(font):
    """The embedded font file's bytes, or None."""
    font = font.get_object()
    if font.get("/Subtype") == "/Type0":
        try:
            font = font["/DescendantFonts"][0].get_object()
        except (KeyError, IndexError):
            return None
    descriptor = font.get("/FontDescriptor")
    if descriptor is None:
        return None
    descriptor = descriptor.get_object()
    for key in ("/FontFile2", "/FontFile3", "/FontFile"):
        if key in descriptor:
            try:
                return descriptor[key].get_object().get_data()
            except Exception:  # noqa: BLE001
                return None
    return None


def file_name(name):
    """The font file's name for a font this module registered with reportlab
    (``PdfmFont2`` → ``segoeui``); other names as they are."""
    for key, loaded in _loaded.items():
        if loaded and loaded[0] == name:
            return Path(key[0] if isinstance(key, tuple) else key).stem
    return name


def display_name(name):
    """``TimesNewRomanPS-BoldMT`` reads as ``Times New Roman Bold``."""
    import re

    name = strip_subset(name).replace(",", "-")
    family, _, style = name.partition("-")
    family = re.sub(r"(PS)?MT$|PS$", "", family)
    style = re.sub(r"(PS)?MT$", "", style)
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", family + (" " + style if style else ""))
    return " ".join(words.split()) or name


def strip_subset(name):
    name = str(name).lstrip("/")
    return name[7:] if len(name) > 7 and name[6] == "+" and name[:6].isupper() else name


# ---- fonts installed on this computer --------------------------------------------------

FONT_DIRS = [
    Path(r"C:\Windows\Fonts"),
    Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts",
    Path("/Library/Fonts"), Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"),
    Path.home() / "Library" / "Fonts",
    Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".local" / "share" / "fonts",
    Path.home() / ".fonts",
]
STYLE_WORDS = ("bold", "italic", "oblique", "regular", "roman", "book", "normal", "semibold",
               "demibold", "light", "medium", "black", "heavy", "condensed", "it")
_index = None


def font_key(name):
    """``Arial-BoldMT``, ``Arial,Bold`` and ``Arial Bold`` all give ``arialbold``."""
    import re

    key = re.sub(r"[^a-z0-9]", "", strip_subset(name).lower())
    for tail in ("psmt", "mt", "ps"):
        if key.endswith(tail) and len(key) > len(tail) + 2:
            key = key[: -len(tail)]
            break
    return key


def _sfnt_names(f, offset):
    """(PostScript name, full name, family, style, TrueType outlines?) of the
    font at ``offset`` in the open .ttf/.ttc file ``f``."""
    import struct

    def read(at, size):
        f.seek(at)
        data = f.read(size)
        if len(data) < size:
            raise ValueError("truncated font")
        return data

    count = struct.unpack(">H", read(offset + 4, 2))[0]
    directory = read(offset + 12, 16 * count)
    tables = {}
    for i in range(count):
        tag, _, start, length = struct.unpack_from(">4sIII", directory, 16 * i)
        tables[tag] = (start, length)
    if b"name" not in tables:
        return None
    start, length = tables[b"name"]
    data = read(start, min(length, 1 << 20))
    _, n, strings = struct.unpack_from(">HHH", data, 0)
    names = {}
    for i in range(n):
        platform, _, language, name_id, size, at = struct.unpack_from(">HHHHHH", data, 6 + 12 * i)
        if name_id not in (1, 2, 4, 6, 16, 17):
            continue
        raw = data[strings + at: strings + at + size]
        if platform in (0, 3):
            if platform == 3 and language != 0x409 and name_id in names:
                continue
            names[name_id] = raw.decode("utf-16-be", "replace")
        elif platform == 1 and name_id not in names:
            names[name_id] = raw.decode("mac_roman", "replace")
    return (names.get(6, ""), names.get(4, ""), names.get(16) or names.get(1, ""),
            names.get(17) or names.get(2, ""), b"glyf" in tables)


def _font_index():
    """[(key, full-name key, family key, bold, italic, path, index in a .ttc)]
    for the TrueType fonts installed."""
    global _index
    if _index is not None:
        return _index
    import struct

    found, seen = [], set()
    for folder in FONT_DIRS:
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if path.suffix.lower() not in (".ttf", ".ttc") or path in seen:
                continue
            seen.add(path)
            try:
                with open(path, "rb") as f:
                    head = f.read(12)
                    if head[:4] == b"ttcf":
                        count = struct.unpack(">I", head[8:12])[0]
                        offsets = struct.unpack(f">{min(count, 64)}I", f.read(4 * min(count, 64)))
                    else:
                        offsets = (0,)
                    for number, offset in enumerate(offsets):
                        names = _sfnt_names(f, offset)
                        if not names or not names[4]:
                            continue  # reportlab can only embed TrueType outlines
                        postscript, full, family, style, _ = names
                        low = style.lower()
                        found.append((font_key(postscript), font_key(full), font_key(family),
                                      any(w in low for w in ("bold", "black", "heavy")),
                                      "italic" in low or "oblique" in low, str(path), number, family))
            except (OSError, struct.error, ValueError):
                continue
    _index = found
    return found


def installed_font(name, bold=False, italic=False, text=""):
    """A reportlab font name for the installed font called ``name`` (as a
    PDF names it), able to show ``text``; or None."""
    key = font_key(name)
    if not key:
        return None
    family = key
    for word in sorted(STYLE_WORDS, key=len, reverse=True):
        if family.endswith(word) and len(family) > len(word) + 2:
            family = family[: -len(word)]
    index = _font_index()
    exact = [e for e in index if key in (e[0], e[1])]
    same = [e for e in index if e[2] == family and e[3] == bold and e[4] == italic]
    upright = [e for e in index if e[2] == family and e[3] == bold]  # closer than another family
    for entry in exact + same + upright:
        name = _register(entry[5], entry[6])
        if name and {ord(ch) for ch in text if not ch.isspace()} <= _loaded[(entry[5], entry[6])][1]:
            return name
    return None


def _register(path, number=0):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if (path, number) not in _loaded:
        name = "PdfmFont" + str(len(_loaded))
        try:
            font = TTFont(name, path, subfontIndex=number)
            pdfmetrics.registerFont(font)
            _loaded[(path, number)] = (name, set(font.face.charToGlyph))
        except Exception:  # noqa: BLE001 - not a font reportlab can use
            _loaded[(path, number)] = None
    loaded = _loaded[(path, number)]
    return loaded[0] if loaded else None


# ---- fonts the reader can pick for edited words ------------------------------------------

# Offered first in the reader's font list, when installed (Windows, Mac and
# Linux names, and the look-alikes Linux ships).
COMMON_FAMILIES = (
    "Arial", "Helvetica Neue", "Calibri", "Cambria", "Candara", "Century Gothic", "Consolas", "Constantia",
    "Corbel", "Courier New", "Garamond", "Georgia", "Palatino Linotype", "Segoe UI", "Tahoma",
    "Times New Roman", "Trebuchet MS", "Verdana", "Liberation Sans", "Liberation Serif", "Liberation Mono",
    "Carlito", "Caladea", "DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono", "Noto Sans", "Noto Serif",
    "Open Sans", "Roboto", "Lato", "Source Sans Pro", "Ubuntu", "FreeSans", "FreeSerif", "FreeMono",
)
# The standard PDF fonts every reader has, offered by family (bold and
# italic are picked apart).
STANDARD_FAMILIES = {"helvetica": "sans", "times": "serif", "courier": "mono"}
# Installed fonts to embed in place of a standard one (a PDF/A file may not
# rely on the reader's own copy), best first.
EMBEDDABLE = {
    "sans": ("Arial", "Liberation Sans", "Arimo", "Helvetica", "FreeSans", "DejaVu Sans", "Noto Sans"),
    "serif": ("Times New Roman", "Liberation Serif", "Tinos", "FreeSerif", "DejaVu Serif", "Noto Serif"),
    "mono": ("Courier New", "Liberation Mono", "Cousine", "FreeMono", "DejaVu Sans Mono", "Noto Mono"),
}
# Words naming a face of a family, not a family ("Light", "Black" can be
# families of their own: Arial Black).
_FAMILY_STYLE = ("bolditalic", "boldoblique", "bold", "italic", "oblique", "regular")


def family_key(name):
    """The family of a font as a PDF or the reader names it, as a key:
    ``TimesNewRomanPS-BoldItalicMT``, ``Times New Roman`` and
    ``ABCDEF+TimesNewRoman,Bold`` all give ``timesnewroman``."""
    name = strip_subset(name).replace(",", "-").strip("-")
    key = font_key(name.split("-")[0])
    changed = True
    while changed:  # "ArialBold", "TimesRoman" → "arial", "times"
        changed = False
        for word in _FAMILY_STYLE:
            if key.endswith(word) and len(key) > len(word) + 2:
                key, changed = key[: -len(word)], True
                break
    return key


def family_name(name):
    """The family of a font as people know it: ``TimesNewRomanPS-BoldMT`` →
    ``Times New Roman``, ``Times-Roman`` → ``Times``."""
    name = strip_subset(name).replace(",", "-").strip("-")
    words = display_name(name.split("-")[0]).split() if name else []
    while len(words) > 1 and words[-1].lower() in _FAMILY_STYLE:
        words.pop()
    return " ".join(words)


def font_style(name):
    """(bold, italic) as a font's name says."""
    low = strip_subset(name).lower()
    return any(n in low for n in BOLD_NAMES), "italic" in low or "oblique" in low


def kind_of(name):
    """The kind of type a family name suggests: "serif", "mono" or "sans"."""
    low = str(name).lower()
    if any(n in low for n in MONO_NAMES):
        return "mono"
    if any(n in low for n in SERIF_NAMES) and "sans" not in low:
        return "serif"
    return "sans"


def installed_family(family, bold=False, italic=False, text=""):
    """A reportlab font name for the installed font of ``family`` (any
    spelling) in that weight and slant, able to show ``text``; or None.
    Unlike :func:`installed_font` a regular face doesn't stand in for a
    bold one: bold was asked for."""
    key = family_key(family)
    if not key:
        return None
    needed = {ord(ch) for ch in text if not ch.isspace()}
    for entry in _font_index():
        if family_key(entry[7]) != key or (entry[3], entry[4]) != (bold, italic):
            continue
        name = _register(entry[5], entry[6])
        if name and needed <= _loaded[(entry[5], entry[6])][1]:
            return name
    return None


def embedded_like(kind, bold=False, italic=False, text=""):
    """An installed font (else reportlab's own Vera) to embed in place of
    the standard font of that kind: a reportlab font name, or None."""
    for family in EMBEDDABLE.get(kind, EMBEDDABLE["sans"]):
        name = installed_family(family, bold, italic, text)
        if name:
            return name
    import reportlab

    vera = Path(reportlab.__file__).parent / "fonts" / {
        (False, False): "Vera.ttf", (True, False): "VeraBd.ttf", (False, True): "VeraIt.ttf",
        (True, True): "VeraBI.ttf"}[(bool(bold), bool(italic))]
    name = _register(str(vera)) if vera.is_file() else None
    if name and {ord(ch) for ch in text if not ch.isspace()} <= _loaded[(str(vera), 0)][1]:
        return name
    return None


def installed_families():
    """The installed font families the reader offers: (common ones, the
    others), each a sorted list of names."""
    names = {}
    for entry in _font_index():
        family = entry[7]
        if family and family.isprintable() and not family.startswith("."):
            names.setdefault(family_key(family), family)
    common = {family_key(f) for f in COMMON_FAMILIES}
    first = sorted((n for k, n in names.items() if k in common), key=str.lower)
    rest = sorted((n for k, n in names.items() if k not in common), key=str.lower)
    return first, rest
