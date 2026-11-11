"""What is on a page and where, for the PowerPoint and HTML exports.

  * The text with its look: each character's font (mapped to a common font:
    Helvetica → Arial, Times → Times New Roman, Courier → Courier New),
    size, bold, italic and colour, and where it is on the page as shown.
  * Characters → lines → blocks (lines that belong together: the same size,
    evenly spaced, one under the other) → paragraphs, with the alignment.
  * The page drawn without its text: the text-showing operators are taken
    out of a copy of the page (pikepdf), which is then drawn (pypdfium2).
    Text that clips (a picture shaped like letters) stays in the drawing.
  * Links: web addresses and links to other pages.

Coordinates are points from the top left of the page as shown (its crop box
and rotation applied), y downwards, like a picture of the page. Angles are
degrees clockwise. Invisible text (the searchable layer of an OCRed scan)
is read, marked ``hidden``.
"""

import bisect
import ctypes
import io
import math
import re
import statistics
from pathlib import Path

from pdfmanager import core
from pdfmanager.pageranges import parse_page_ranges

LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
BULLETS = "•◦▪▫‣⁃●○■□►▸–-*·"
LIST_ITEM = re.compile(r"^(?:[" + re.escape(BULLETS) + r"]\s+|\(?(\d{1,3}|[a-zA-Z])[.)]\s+)")
GAP = 1.5  # font sizes of white space that split a line into pieces
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿]")


# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------

# Ascent and descent of the fonts the text is shown in, in font sizes.
METRICS = {"Arial": (0.905, 0.212), "Times New Roman": (0.891, 0.216),
           "Courier New": (0.833, 0.23)}
GENERIC = {"Arial": "sans-serif", "Times New Roman": "serif", "Courier New": "monospace"}

_FAMILIES = {
    "helvetica": "Arial", "arial": "Arial", "helveticaneue": "Arial", "arialnarrow": "Arial Narrow",
    "liberationsans": "Arial", "nimbussans": "Arial", "nimbussansl": "Arial",
    "times": "Times New Roman", "timesnewroman": "Times New Roman", "timesroman": "Times New Roman",
    "liberationserif": "Times New Roman", "nimbusroman": "Times New Roman",
    "nimbusromno9l": "Times New Roman",
    "courier": "Courier New", "couriernew": "Courier New", "liberationmono": "Courier New",
    "nimbusmono": "Courier New", "nimbusmonol": "Courier New",
    "symbol": "Times New Roman", "zapfdingbats": "Segoe UI Symbol", "dingbats": "Segoe UI Symbol",
    "dejavusans": "DejaVu Sans", "dejavuserif": "DejaVu Serif", "dejavusansmono": "DejaVu Sans Mono",
    "segoeui": "Segoe UI", "msgothic": "MS Gothic", "msmincho": "MS Mincho",
}
_SUBSET = re.compile(r"^[A-Z]{6}\+")
_BOLD = re.compile(r"bold|black|heavy|semibold|demi|ultra|extrab", re.I)
_ITALIC = re.compile(r"italic|oblique|inclined|slanted|(?:^|[a-z])It$", re.I)
_MEANINGLESS = re.compile(r"^(?:[A-Z]{0,3}\d+(?:_\d+)?|CIDFont.*|Identity.*|Font\d*|)$", re.I)
_TEX = re.compile(r"^(?:CM|LM|SF|EU|MS[AB]M)[A-Z]*\d*$|^LM(?:Roman|Sans|Mono)", re.I)
FIXED_PITCH, SERIF, ITALIC, FORCE_BOLD = 1, 2, 64, 0x40000


def font_look(name, flags=0, weight=0):
    """(family, bold, italic) for a PDF font name like
    "ABCDEF+TimesNewRomanPS-BoldItalicMT": the family as a common font
    (Arial, Times New Roman, Courier New, or the font's own name)."""
    name = _SUBSET.sub("", name or "").strip()
    family, _, style = name.replace(",", "-").partition("-")
    family, style = family.strip(), style.strip()
    if not style:  # "ArialBold", "TimesBoldItalic"
        m = re.match(r"^(.+?)((?:Semi|Demi|Extra|Ultra)?(?:Bold|Black|Heavy)?"
                     r"(?:Italic|Oblique)?)$", family)
        if m and m.group(2):
            family, style = m.group(1), m.group(2)
    bold = bool(_BOLD.search(style)) or weight >= 600 or bool(flags & FORCE_BOLD)
    italic = bool(_ITALIC.search(style)) or bool(flags & ITALIC)
    if _TEX.match(family):
        upper = family.upper()
        bold = bold or "BX" in upper or "BOLD" in upper or upper.startswith("CMB")
        italic = italic or any(k in upper for k in ("TI", "SL", "MI", "ITALIC"))
        common = ("Courier New" if "TT" in upper or "MONO" in upper else
                  "Arial" if "SS" in upper or "SANS" in upper else "Times New Roman")
        return common, bold, italic
    key = re.sub(r"(PSMT|PS|MT|Std|LT)$", "", family.replace(" ", "")).lower()
    if key in _FAMILIES:
        return _FAMILIES[key], bold, italic
    if _MEANINGLESS.match(family) or len(key) < 3:
        common = ("Courier New" if flags & FIXED_PITCH else
                  "Times New Roman" if flags & SERIF else "Arial")
        return common, bold, italic
    family = re.sub(r"(PSMT|PS|MT)$", "", family)
    family = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", family).strip()
    return family, bold, italic


def metrics(family):
    return METRICS.get(family, (0.9, 0.22))


# --------------------------------------------------------------------------
# Characters, lines, blocks
# --------------------------------------------------------------------------

class Char:
    __slots__ = ("text", "x0", "x1", "base", "size", "angle", "family", "bold", "italic",
                 "color", "hidden", "link", "space", "box", "raised")

    def style(self):
        return (self.family, round(self.size * 2) / 2, self.bold, self.italic, self.color,
                self.hidden, self.link, self.raised)


class Line:
    """Characters on one baseline. x0/x1 and base are along and across the
    line's direction (for upright text: x and the baseline's y)."""

    def __init__(self, char):
        self.chars = [char]
        self.angle = char.angle
        self.x0, self.x1, self.base, self.size = char.x0, char.x1, char.base, char.size

    def accepts(self, char):
        size = max(self.size, char.size)
        # A character may overlap the one before (ligatures, kerning); a
        # wide gap starts another piece (the next column, a value after its
        # label), placed on its own.
        if char.angle != self.angle or char.x0 < self.x0 - 0.4 * size:
            return False
        if abs(char.base - self.base) <= 0.45 * size:
            # Much smaller type on a baseline of its own (a date beside a
            # masthead's title) is no nearer than its own size makes it.
            apart = (abs(char.base - self.base) > 0.1 * size
                     and min(self.size, char.size) < 0.6 * size and not self._shifted(char))
            return char.x0 - self.x1 <= GAP * (min(self.size, char.size) if apart else size)
        return self._shifted(char) == 1 or self._marked(char)

    def _shifted(self, char):
        """1 when ``char`` is a small raised character right after the
        line's own type (a footnote mark, an exponent), -1 when a small
        lowered one (the 2 of CO2), else 0. Small means clearly smaller than
        the type it follows, and that type more than one letter (after a
        drop capital comes the text, not a mark)."""
        last = self.chars[-1]
        gap = char.x0 - self.x1
        if last.raised and abs(char.size - last.size) + abs(char.base - last.base) < 0.1 * last.size:
            return last.raised if gap <= 0.3 * self.size else 0  # the mark's next figure
        if last.raised or char.size > 0.86 * last.size or not -0.1 * last.size <= gap <= 0.3 * last.size:
            return 0
        if len(self.chars) < 2 or abs(self.chars[-2].size - last.size) > 0.05 * last.size:
            return 0
        rise = last.base - char.base
        if 0.2 * last.size <= rise <= 0.75 * last.size and rise < 1.3 * char.size:
            return 1
        if -0.45 * last.size <= rise <= -0.08 * last.size:
            return -1
        return 0

    def _marked(self, char):
        """Whether the line so far is a raised mark that ``char``, in
        larger type and lower, follows closely: a footnote's number before
        its text."""
        if len(self.chars) > 3 or any(c.size > 0.86 * char.size for c in self.chars):
            return False
        rise, gap = char.base - self.base, char.x0 - self.x1
        return (0.2 * char.size <= rise <= 0.75 * char.size and rise < 1.3 * self.size
                and -0.1 * char.size <= gap <= 0.3 * char.size)

    def add(self, char):
        shift = self._shifted(char)
        if shift:
            char.raised = shift
            if char.x0 - self.x1 <= 0.12 * self.size:
                char.space = False  # set against its word, however pdfium read the gap
        elif self._marked(char):
            for c in self.chars:
                c.raised = 1
        elif self.chars[-1].raised and char.x0 - self.x1 <= 0.12 * char.size:
            char.space = False  # right after the mark: no space, as before it
        if not shift and not char.space and char.x0 - self.x1 > 0.25 * max(char.size, self.size):
            char.space = True
        self.chars.append(char)
        self.x0, self.x1 = min(self.x0, char.x0), max(self.x1, char.x1)
        if char.size > self.size * 1.05:  # the line's own baseline is its largest text's
            self.size, self.base = char.size, char.base

    def merge(self, other):
        first = other.chars[0]
        if other.x0 - self.x1 > 0.1 * self.size:
            first.space = True
        self.chars += other.chars
        self.x1 = max(self.x1, other.x1)
        if other.size > self.size * 1.05:
            self.size, self.base = other.size, other.base

    def first_word_width(self):
        end = self.chars[0].x1
        for c in self.chars[1:]:
            if c.space:
                break
            end = max(end, c.x1)
        return end - self.chars[0].x0

    @property
    def text(self):
        return "".join((" " if c.space and i else "") + c.text for i, c in enumerate(self.chars))

    def runs(self, start=0, end=None):
        """[(text, a Char with the run's look)]: the line (or its characters
        ``start`` to ``end``) in runs of the same look."""
        out = []
        for i, c in enumerate(self.chars[start:end]):
            text = (" " if c.space and i else "") + c.text
            if out and out[-1][1].style() == c.style():
                out[-1][0] += text
            elif out and text.startswith(" "):
                out[-1][0] += " "  # the space between two looks goes with the first
                out.append([text[1:], c])
            else:
                out.append([text, c])
        return [(t, c) for t, c in out if t]

    def pieces(self, gap=0.8):
        """(start, end) of the parts of the line between white space wider
        than ``gap`` font sizes (more than a space between words)."""
        out, start = [], 0
        for k in range(1, len(self.chars)):
            c = self.chars[k]
            if c.space and c.x0 - self.chars[k - 1].x1 > gap * self.main_size:
                out.append((start, k))
                start = k
        return out + [(start, len(self.chars))]

    def marker(self):
        """When the line starts with a list marker (a bullet, "1.", "a)")
        set apart from its text: (index of the text's first character, its
        x). Otherwise None."""
        if not LIST_ITEM.match(self.text):
            return None
        for k in range(1, len(self.chars)):
            c = self.chars[k]
            if c.space:
                return (k, c.x0) if c.x0 - self.chars[k - 1].x1 > 0.5 * self.main_size else None
        return None

    @property
    def main_size(self):
        """The size of most of the line's characters."""
        counts = {}
        for c in self.chars:
            counts[round(c.size, 1)] = counts.get(round(c.size, 1), 0) + 1
        return max(counts, key=counts.get)

    @property
    def family(self):
        counts = {}
        for c in self.chars:
            counts[c.family] = counts.get(c.family, 0) + 1
        return max(counts, key=counts.get)

    @property
    def top(self):
        return self.base - metrics(self.family)[0] * self.size

    @property
    def bottom(self):
        return self.base + metrics(self.family)[1] * self.size

    @property
    def hidden(self):
        return all(c.hidden for c in self.chars)


class Block:
    """Lines that belong together, one under the other, evenly spaced.
    ``paragraphs`` are lists of lines; ``align`` is "l", "r", "ctr" or
    "just"; ``spacing`` the distance between baselines."""

    def __init__(self, line):
        self.lines = [line]
        self.angle, self.size = line.angle, line.size
        self.x0, self.x1 = line.x0, line.x1
        self.spacing = None
        self.paragraphs, self.align = [[line]], "l"

    def fits(self, line):
        last = self.lines[-1]
        size = max(line.size, self.size)
        gap = line.base - last.base
        if line.angle != self.angle or not 0.6 * size <= gap <= 1.6 * size:
            return None
        if abs(line.size - self.size) > 0.15 * self.size:
            return None
        if min(line.x1, self.x1) - max(line.x0, self.x0) <= 0:
            return None
        if self.spacing and not 0.8 * self.spacing <= gap <= 1.25 * self.spacing:
            return None
        if line.top < last.bottom - 0.6 * size:
            return None
        return gap

    def add(self, line):
        gap = line.base - self.lines[-1].base
        self.spacing = self.spacing or gap
        self.lines.append(line)
        self.x0, self.x1 = min(self.x0, line.x0), max(self.x1, line.x1)

    def finish(self):
        """Split into paragraphs and find the alignment."""
        lines, size = self.lines, self.size
        if len(lines) > 1:
            gaps = [b.base - a.base for a, b in zip(lines, lines[1:])]
            self.spacing = statistics.median(gaps)
        tol = max(1.5, 0.3 * size)
        spread = lambda values: max(values) - min(values) if values else 0  # noqa: E731
        lefts, rights = [ln.x0 for ln in lines], [ln.x1 for ln in lines]
        centres = [(ln.x0 + ln.x1) / 2 for ln in lines]
        centred = len(lines) > 1 and spread(centres) <= tol < min(spread(lefts), spread(rights))
        width = max(ln.x1 - ln.x0 for ln in lines)
        # Centred lines mostly shorter than the widest by a word or more are
        # lines of their own (a programme, an address), each of them.
        short = [ln.x1 - ln.x0 + 0.3 * size + nxt.first_word_width() <= width + 0.1 * size
                 for ln, nxt in zip(lines, lines[1:])]
        apart = centred and sum(short) * 2 >= len(short)
        paragraphs, cur = [], [lines[0]]
        for prev, line in zip(lines, lines[1:]):
            # A line ends its paragraph when the next line's first word would
            # have fit at its end, the next line is a list item or indented
            # (a new paragraph's first line), or the size changes.
            fits = prev.x1 + 0.3 * size + line.first_word_width() <= self.x1 + 0.1 * size
            listed = bool(LIST_ITEM.match(line.text))
            indented = (line.x0 - prev.x0 > 0.8 * size and prev.x0 - self.x0 < 0.5 * size
                        and not LIST_ITEM.match(cur[0].text))
            resized = abs(line.main_size - prev.main_size) > 0.05 * prev.main_size
            if centred:
                # Centred lines: the next line's first word would have fit
                # when the line is that much shorter than the widest; a
                # sentence that goes on (a small letter, a quotation still
                # open) is one paragraph wherever its lines end.
                text = " ".join(ln.text for ln in cur)
                fits = ((apart or prev.x1 - prev.x0 + 0.3 * size + line.first_word_width() <= width + 0.1 * size)
                        and not (line.text[:1].islower() or text.count("“") > text.count("”")))
                indented = False
            if fits or listed or indented or resized:
                paragraphs.append(cur)
                cur = []
            cur.append(line)
        paragraphs.append(cur)
        self.paragraphs = paragraphs
        inner = [ln.x1 for p in paragraphs for ln in p[:-1]]
        if len(lines) == 1:
            self.align = "l"
        elif spread(lefts) <= tol:
            self.align = "just" if len(inner) >= 2 and spread(inner + [self.x1]) <= tol else "l"
        elif spread(rights) <= tol:
            self.align = "r"
        elif spread(centres) <= tol:
            self.align = "ctr"
        else:
            self.align = "just" if len(inner) >= 2 and spread(inner + [self.x1]) <= tol else "l"
        return self

    @property
    def top(self):
        return self.lines[0].top

    @property
    def bottom(self):
        return self.lines[-1].bottom


def _merge_pieces(lines, rules=None):
    """Join pieces of one line that the PDF drew separately (or out of order)."""
    lines.sort(key=lambda ln: (ln.angle, ln.base, ln.x0))
    out = []
    for line in lines:
        for prev in reversed(out[-8:]):
            size = max(prev.size, line.size)
            if (prev.angle == line.angle and abs(prev.base - line.base) <= 0.2 * size
                    and -0.3 * size <= line.x0 - prev.x1 <= 0.6 * size
                    and not _ruled_between(rules, prev, line.chars[0])):
                prev.merge(line)
                break
        else:
            out.append(line)
    return out


def _ruled_between(rules, line, char):
    """Whether a drawn upright line (a table's border) separates ``char``
    from the end of ``line`` (upright text only)."""
    if not rules or line.angle != 0:
        return False
    xs = rules[0]
    lo, hi = sorted((line.x1, char.x0))
    for k in range(bisect.bisect_left(xs, lo - 0.5), bisect.bisect_right(xs, hi + 0.5)):
        _, y0, y1 = rules[1][k]
        if y0 - 1 <= char.base <= y1 + 1:
            return True
    return False


def lines_of(chars, rules=()):
    """Characters (in the order the PDF draws them) → lines. ``rules``:
    upright lines drawn on the page, [(x, top, bottom)]: text on both sides
    of one is in two cells of a table, not one line."""
    rules = sorted(rules)
    rules = ([r[0] for r in rules], rules) if rules else None
    lines, cur = [], None
    for char in chars:
        if cur is not None and cur.accepts(char) and not _ruled_between(rules, cur, char):
            cur.add(char)
        else:
            cur = Line(char)
            lines.append(cur)
    return _merge_pieces(lines, rules)


def blocks_of(lines):
    """Lines → blocks, finished (paragraphs and alignment found)."""
    blocks = []
    for line in sorted(lines, key=lambda ln: (ln.angle, ln.base, ln.x0)):
        best, best_gap = None, None
        for block in blocks[-40:]:
            gap = block.fits(line)
            if gap is not None and (best_gap is None or gap < best_gap):
                best, best_gap = block, gap
        if best is None:
            best = Block(line)
            # Lines taken for a block that were paragraphs of one line each
            # (set further apart than the lines of one paragraph): the last
            # starts a block with the line that follows it closely.
            for block in blocks[-40:]:
                gap = line.base - block.lines[-1].base
                if (len(block.lines) >= 2 and gap < 0.8 * block.spacing
                        and block.lines[-1].base - block.lines[-2].base > 1.15 * gap):
                    start = Block(block.lines[-1])
                    if start.fits(line) is not None:
                        block.lines.pop()
                        block.x0 = min(ln.x0 for ln in block.lines)
                        block.x1 = max(ln.x1 for ln in block.lines)
                        if len(block.lines) == 1:
                            block.spacing = None
                        start.add(line)
                        best = start
                        break
            blocks.append(best)
        else:
            best.add(line)
    return [b.finish() for b in blocks]


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def direction(angle):
    """Unit vectors along and across text at ``angle``: (u, n)."""
    a = math.radians(angle)
    u = (math.cos(a), math.sin(a))
    return u, (-u[1], u[0])


def to_page(angle, x, y):
    """A point in a line's own coordinates (along, across) → page coordinates."""
    u, n = direction(angle)
    return x * u[0] + y * n[0], x * u[1] + y * n[1]


def box_on_page(angle, x0, top, x1, bottom):
    """The upright box around a line-coordinates box, on the page."""
    points = [to_page(angle, x, y) for x in (x0, x1) for y in (top, bottom)]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


class Geometry:
    """PDF user space → the page as shown (crop box and rotation applied)."""

    def __init__(self, bbox, rotation):
        self.x0, self.y0, self.x1, self.y1 = bbox
        self.rotation = rotation % 360
        w, h = self.x1 - self.x0, self.y1 - self.y0
        self.width, self.height = (h, w) if self.rotation in (90, 270) else (w, h)

    def point(self, x, y):
        u, v = x - self.x0, self.y1 - y
        w, h = self.x1 - self.x0, self.y1 - self.y0
        r = self.rotation
        return ((u, v) if r == 0 else (h - v, u) if r == 90 else
                (w - u, h - v) if r == 180 else (v, w - u))

    def vector(self, dx, dy):
        du, dv = dx, -dy
        r = self.rotation
        return ((du, dv) if r == 0 else (-dv, du) if r == 90 else
                (-du, -dv) if r == 180 else (dv, -du))

    def box(self, x0, y0, x1, y1):
        points = [self.point(x, y) for x in (x0, x1) for y in (y0, y1)]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return min(xs), min(ys), max(xs), max(ys)


# --------------------------------------------------------------------------
# Reading pages
# --------------------------------------------------------------------------

TEXT_SHOWING = {"Tj", "TJ", "'", '"'}


class Page:
    """One page, read: ``number`` (1-based), ``width``/``height`` as shown,
    ``chars``, ``lines``, ``blocks``, ``links`` ([{"box", "uri" or "page"}]),
    ``images`` (boxes of the pictures on it), ``geometry``."""

    def __init__(self, number, geometry, chars, links, images, rules=(), shapes=()):
        self.number, self.geometry = number, geometry
        self.width, self.height = geometry.width, geometry.height
        self.images = images
        self.shapes = list(shapes)  # boxes of what is drawn with lines and fills
        self.chars, self.links = chars, links
        self.lines = lines_of(chars, rules)
        self.blocks = blocks_of(self.lines)


def _strip_text(pdf, page):
    """Take the text-showing operators out of a page and the forms it
    draws. Text that clips (render modes 4-7) is kept."""
    import pikepdf

    done = set()

    def strip(holder, is_page):
        ops = pikepdf.parse_content_stream(holder)
        out, mode, stack = [], 0, []
        for item in ops:
            if isinstance(item, pikepdf.ContentStreamInlineImage):
                out.append(item)
                continue
            operands, op = item.operands, str(item.operator)
            if op == "q":
                stack.append(mode)
            elif op == "Q":
                mode = stack.pop() if stack else 0
            elif op == "Tr" and operands:
                try:
                    mode = int(operands[0])
                except (TypeError, ValueError):
                    pass
            elif op in TEXT_SHOWING and mode < 4:
                if op == "'":
                    out.append(([], pikepdf.Operator("T*")))
                elif op == '"' and len(operands) == 3:
                    out += [([operands[0]], pikepdf.Operator("Tw")),
                            ([operands[1]], pikepdf.Operator("Tc")), ([], pikepdf.Operator("T*"))]
                continue
            out.append(item)
        data = pikepdf.unparse_content_stream(out)
        if is_page:
            holder.obj.Contents = pdf.make_stream(data)
        else:
            holder.write(data)
        resources = (holder.obj if is_page else holder).get("/Resources")
        xobjects = resources.get("/XObject") if resources is not None else None
        if xobjects is None:
            return
        for _, xobj in xobjects.items():
            key = xobj.objgen
            if (isinstance(xobj, pikepdf.Stream) and xobj.get("/Subtype") == "/Form"
                    and key not in done):
                done.add(key)
                strip(xobj, False)

    strip(page, True)


class Document:
    """A PDF opened for reading its pages; use as a context manager (it holds
    pypdfium2's lock). ``indices``: the 0-based pages chosen."""

    def __init__(self, path, password=None, pages=None, without_text=True):
        self.path, self.password, self.without_text = Path(path), password, without_text
        core.open_pdf(path, password)  # the usual messages for damaged files and passwords
        self.pages = pages

    def __enter__(self):
        import pikepdf
        import pypdfium2 as pdfium

        core.PDFIUM_LOCK.acquire()
        self.doc = self.bare = None
        try:
            try:
                self.doc = pdfium.PdfDocument(str(self.path), password=self.password)
            except pdfium.PdfiumError as exc:
                raise core.PdfManagerError(f"{self.path} can't be read ({exc})") from None
            total = len(self.doc)
            self.indices = list(dict.fromkeys(parse_page_ranges(self.pages, total)
                                              if self.pages else range(total)))
            meta = self.doc.get_metadata_dict(skip_empty=True)
            self.title = " ".join((meta.get("Title") or "").split())
            self.lang, self.stripped = "", set()
            try:
                with pikepdf.open(self.path, password=self.password or "") as pdf:
                    lang = pdf.Root.get("/Lang")
                    self.lang = str(lang).strip() if lang is not None else ""
                    if self.without_text:
                        for index in self.indices:
                            try:
                                _strip_text(pdf, pdf.pages[index])
                                self.stripped.add(index)
                            except Exception:  # a damaged page: drawn as it is
                                pass
                        buf = io.BytesIO()
                        pdf.save(buf)
                        self.bare = pdfium.PdfDocument(buf.getvalue())
            except pikepdf.PdfError:
                self.bare = None
            return self
        except BaseException:
            self._close()
            raise

    def _close(self):
        for doc in (self.bare, self.doc):
            if doc is not None:
                doc.close()
        core.PDFIUM_LOCK.release()

    def __exit__(self, *exc):
        self._close()

    def page(self, index):
        """Read page ``index`` (0-based)."""
        page = self.doc[index]
        try:
            geometry = Geometry(page.get_bbox(), page.get_rotation())
            textpage = page.get_textpage()
            try:
                chars = _chars(textpage, geometry)
                links = _links(self.doc, page, geometry, len(self.doc))
            finally:
                textpage.close()
            images = _images(page, geometry)
            rules, shapes = _rules(page, geometry)
        finally:
            page.close()
        for link_index, link in enumerate(links):
            x0, y0, x1, y1 = link["box"]
            for c in chars:
                cx, cy = (c.box[0] + c.box[2]) / 2, (c.box[1] + c.box[3]) / 2
                if x0 - 1 <= cx <= x1 + 1 and y0 - 1 <= cy <= y1 + 1:
                    c.link = link_index
        return Page(index + 1, geometry, chars, links, images, rules, shapes)

    def size(self, index):
        """(width, height) of page ``index`` as shown."""
        page = self.doc[index]
        try:
            geometry = Geometry(page.get_bbox(), page.get_rotation())
        finally:
            page.close()
        return geometry.width, geometry.height

    def has_background(self, index):
        """Whether the page can be drawn without its text."""
        return self.bare is not None and index in self.stripped

    def render(self, index, dpi, without_text=False):
        """The page as a PIL image (RGB), with or without its text."""
        doc = self.bare if without_text and self.has_background(index) else self.doc
        page = doc[index]
        try:
            return page.render(scale=dpi / 72, may_draw_forms=True).to_pil().convert("RGB")
        finally:
            page.close()


def _chars(textpage, geometry):
    import pypdfium2.raw as raw

    text = textpage.get_text_range()
    count = min(len(text), textpage.count_chars())
    chars, space = [], False
    name = ctypes.create_string_buffer(256)
    flags = ctypes.c_int()
    rgba = [ctypes.c_uint() for _ in range(4)]
    matrix = raw.FS_MATRIX()
    loose = raw.FS_RECTF()
    ox, oy = ctypes.c_double(), ctypes.c_double()
    fonts = {}
    for i in range(count):
        ch = text[i]
        if ch.isspace() or ch == "\x00":
            space = True
            continue
        obj = raw.FPDFText_GetTextObject(textpage, i)
        if not obj:
            continue
        mode = raw.FPDFTextObj_GetTextRenderMode(obj)
        if mode >= 4:
            continue  # clipping text: part of the drawing
        # pdfium marks a hyphen at the end of a line (\x02, or \ufffe)
        ch = "-" if ch in ("\x02", "\ufffe") else LIGATURES.get(ch, ch)
        ch = _CONTROL.sub("", ch)
        if not ch:
            continue
        x0, x1, y0, y1 = (ctypes.c_double() for _ in range(4))
        raw.FPDFText_GetCharBox(textpage, i, *(ctypes.byref(v) for v in (x0, x1, y0, y1)))
        raw.FPDFText_GetMatrix(textpage, i, ctypes.byref(matrix))
        raw.FPDFText_GetCharOrigin(textpage, i, ctypes.byref(ox), ctypes.byref(oy))
        scale = math.sqrt(abs(matrix.a * matrix.d - matrix.b * matrix.c)) or 1.0
        size = raw.FPDFText_GetFontSize(textpage, i) * scale
        if size <= 0.5:
            continue
        dx, dy = geometry.vector(matrix.a, matrix.b)
        angle = round(math.degrees(math.atan2(dy, dx))) % 360
        for snap in (0, 90, 180, 270, 360):
            if abs(angle - snap) <= 2:
                angle = snap % 360
        raw.FPDFText_GetFontInfo(textpage, i, name, 256, ctypes.byref(flags))
        weight = raw.FPDFText_GetFontWeight(textpage, i)
        key = (name.value, flags.value, weight)
        if key not in fonts:
            fonts[key] = font_look(name.value.decode("latin-1", "replace"), flags.value, weight)
        if mode == 1:
            raw.FPDFText_GetStrokeColor(textpage, i, *(ctypes.byref(v) for v in rgba))
        else:
            raw.FPDFText_GetFillColor(textpage, i, *(ctypes.byref(v) for v in rgba))
        box = geometry.box(x0.value, y0.value, x1.value, y1.value)
        u, n = direction(angle)
        along = [px * u[0] + py * u[1] for px in (box[0], box[2]) for py in (box[1], box[3])]
        px, py = geometry.point(ox.value, oy.value)
        c = Char()
        c.text, c.box, c.size, c.angle = ch, box, size, angle
        # From where the glyph starts (its origin) to where the next one
        # would (its advance), not its ink: text set at the same place starts
        # at the same origin, and a line is as wide as when it was set.
        start = px * u[0] + py * u[1]
        c.x0, c.x1 = min(along), max(along)
        if raw.FPDFText_GetLooseCharBox(textpage, i, ctypes.byref(loose)):
            lbox = geometry.box(loose.left, loose.bottom, loose.right, loose.top)
            span = [qx * u[0] + qy * u[1] for qx in (lbox[0], lbox[2]) for qy in (lbox[1], lbox[3])]
            if max(span) - min(span) > 0.05 and abs(min(span) - start) < 0.5 * size:
                c.x0, c.x1 = start, max(span)
        if c.x1 - c.x0 < 0.05:  # an empty box (a glyph without outline)
            c.x0, c.x1 = start, start + size * 0.3
        elif abs(start - c.x0) < 0.5 * size:
            c.x0 = min(start, c.x1 - 0.05)
        c.base = px * n[0] + py * n[1]
        c.family, c.bold, c.italic = fonts[key]
        c.color = tuple(min(255, v.value) for v in rgba[:3])
        c.hidden = mode in (3, 7) or rgba[3].value == 0
        c.link, c.space, c.raised = None, space, 0  # raised: 1 a raised mark, -1 a lowered one
        space = False
        chars.append(c)
    return chars


def _images(page, geometry, smallest=24):
    """Boxes of the pictures on a page, at least ``smallest`` points wide and
    high, cut to the page."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    boxes = []
    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=3):
        try:
            x0, y0, x1, y1 = geometry.box(*obj.get_bounds())
        except pdfium.PdfiumError:
            continue
        x0, y0 = max(x0, 0), max(y0, 0)
        x1, y1 = min(x1, geometry.width), min(y1, geometry.height)
        if x1 - x0 >= smallest and y1 - y0 >= smallest:
            boxes.append((x0, y0, x1, y1))
    return boxes


def _rules(page, geometry):
    """(upright lines drawn on a page (table borders, the sides of boxes):
    [(x, top, bottom)], the boxes of everything drawn with lines and fills)."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    rules, shapes = [], []
    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH], max_depth=3):
        try:
            x0, y0, x1, y1 = geometry.box(*obj.get_bounds())
        except pdfium.PdfiumError:
            continue
        shapes.append((x0, y0, x1, y1))
        if y1 - y0 < 4:
            continue
        if x1 - x0 <= 3:
            rules.append(((x0 + x1) / 2, y0, y1))
        elif raw.FPDFPath_CountSegments(obj) in (4, 5):  # a rectangle: its two sides
            rules += [(x0, y0, y1), (x1, y0, y1)]
    return rules, shapes


_SAFE_URI = re.compile(r"^(https?|mailto|ftp|tel):", re.I)


def _links(doc, page, geometry, total):
    """Link annotations: [{"box", "uri"}] or [{"box", "page" (1-based)}]."""
    import pypdfium2.raw as raw

    links = []
    pos = ctypes.c_int(0)
    link = raw.FPDF_LINK()
    rect = raw.FS_RECTF()
    while raw.FPDFLink_Enumerate(page, ctypes.byref(pos), ctypes.byref(link)):
        if not raw.FPDFLink_GetAnnotRect(link, ctypes.byref(rect)):
            continue
        box = geometry.box(rect.left, rect.bottom, rect.right, rect.top)
        dest = raw.FPDFLink_GetDest(doc, link)
        action = raw.FPDFLink_GetAction(link)
        uri = None
        if action:
            kind = raw.FPDFAction_GetType(action)
            if kind == raw.PDFACTION_URI:
                size = raw.FPDFAction_GetURIPath(doc, action, None, 0)
                buf = ctypes.create_string_buffer(size)
                raw.FPDFAction_GetURIPath(doc, action, buf, size)
                uri = buf.value.decode("utf-8", "replace").strip()
            elif kind == raw.PDFACTION_GOTO:
                dest = raw.FPDFAction_GetDest(doc, action)
        if uri:
            if _SAFE_URI.match(uri):
                links.append({"box": box, "uri": uri})
        elif dest:
            target = raw.FPDFDest_GetDestPageIndex(doc, dest)
            if 0 <= target < total:
                links.append({"box": box, "page": target + 1})
    return links


# --------------------------------------------------------------------------
# Pictures
# --------------------------------------------------------------------------

def encode(image, quality=85):
    """(bytes, "png" or "jpeg"): whichever is smaller."""
    png = io.BytesIO()
    image.save(png, "PNG", optimize=True)
    jpeg = io.BytesIO()
    image.convert("RGB").save(jpeg, "JPEG", quality=quality, optimize=True)
    if jpeg.tell() < png.tell():
        return jpeg.getvalue(), "jpeg"
    return png.getvalue(), "png"


def hex_color(rgb):
    return "%02X%02X%02X" % tuple(rgb)
