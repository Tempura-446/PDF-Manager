"""Where pdfium puts a letter, and how wide a font's letters are: the
small pieces textedit.py and textflow.py both read text with (kept here so
the two don't have to import each other)."""

import ctypes

from pdfmanager import fonts


def _raw():
    import pypdfium2.raw as raw
    return raw


def _origin(textpage, index):
    x, y = ctypes.c_double(), ctypes.c_double()
    _raw().FPDFText_GetCharOrigin(textpage.raw, index, ctypes.byref(x), ctypes.byref(y))
    return x.value, y.value


def _widths(font):
    """A function giving a code's width in thousandths of the font size."""
    font = font.get_object()
    if font.get("/Subtype") == "/Type0":
        try:
            cid = font["/DescendantFonts"][0].get_object()
        except (KeyError, IndexError):
            return lambda code: 500
        default, table = float(cid.get("/DW", 1000)), {}
        w = list(cid.get("/W", []))
        i = 0
        while i + 1 < len(w):
            first = int(w[i])
            if isinstance(w[i + 1], list):
                for k, width in enumerate(w[i + 1]):
                    table[first + k] = float(width)
                i += 2
            elif i + 2 < len(w):
                for c in range(first, int(w[i + 1]) + 1):
                    table[c] = float(w[i + 2])
                i += 3
            else:
                break
        return lambda code: table.get(int.from_bytes(code, "big"), default)
    first = int(font.get("/FirstChar", 0))
    widths = [float(v) for v in font.get("/Widths", [])]
    descriptor = font.get("/FontDescriptor")
    missing = float(descriptor.get_object().get("/MissingWidth", 0) or 0) if descriptor is not None else 0.0
    if not widths:  # a standard font without widths in the file
        name = fonts.strip_subset(font.get("/BaseFont", "Helvetica"))
        try:
            from reportlab.pdfbase import pdfmetrics
            pdfmetrics.getFont(name)
            # A code's letter by the font's own encoding (MacRoman, renamed
            # codes), not by guessing: an accent is as wide as its letter.
            try:
                letters = {code: letter for letter, code in fonts.pdf_font_codes(font).items()}
            except Exception:  # noqa: BLE001 - an encoding that can't be read
                letters = {}
            return lambda code: pdfmetrics.stringWidth(
                letters.get(bytes(code)) or code.decode("cp1252", "replace"), name, 1000)
        except Exception:  # noqa: BLE001 - not a standard font
            return lambda code: 500
    return lambda code: widths[code[0] - first] if 0 <= code[0] - first < len(widths) else missing
