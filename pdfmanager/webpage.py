"""PDF → HTML: one web page (.html) with everything in it (pictures as data:
URIs), no scripts.

  * "flowing": a readable page that reflows to fit any window: headings
    (from the text's size, or a short bold line), paragraphs (wrapped
    lines joined, hyphenated words joined back up), lists, tables (found
    like PDF → Excel finds them), pictures, links (web addresses, and links
    to other pages as #page-N), and a marker where each page starts. Two
    columns are read one after the other. Running headers and footers
    (the same line at the top or bottom of most pages) are left out.
  * "exact": each page as it looks: the page drawn without its text as a
    picture, with the text on top in place, so it can be selected,
    searched and copied. Pages scale to fit the window.

The language (``<html lang>``) is the PDF's own, else "en"; the title is the
PDF's title, else its first heading, else the file name.
"""

import base64
import html
import re
import statistics
from pathlib import Path

from pdfmanager import core, layout, progress

MODES = ("flowing", "exact")
PICTURE_DPI = 150
PX = 4 / 3  # CSS pixels per point
# Where a line's baseline sits in a CSS box with line-height 1, in font sizes.
CSS_BASELINE = {"Arial": 0.85, "Times New Roman": 0.84, "Courier New": 0.77}
SPACE_WIDTH = {"Arial": 0.278, "Times New Roman": 0.25, "Courier New": 0.6}  # font sizes
CSS_FONTS = {"Arial": "Arial,'Liberation Sans',Helvetica,sans-serif",
             "Times New Roman": "'Times New Roman','Liberation Serif',Times,serif",
             "Courier New": "'Courier New','Liberation Mono',Courier,monospace"}
_LANG = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8}){0,3}")
_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff￾￿]")


def _e(text):
    return html.escape(_BAD.sub("", text), quote=True)


def _data_uri(image):
    data, kind = layout.encode(image)
    return f"data:image/{kind};base64," + base64.b64encode(data).decode("ascii")


def _num(value):
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _font_stack(family):
    if family in CSS_FONTS:
        return CSS_FONTS[family]
    generic = "serif" if "serif" in family.lower() and "sans" not in family.lower() else "sans-serif"
    name = re.sub(r"[^\w .-]", "", family).strip()
    return f"'{name}',{generic}" if name else generic


def _href(link, numbers):
    """The href of a link, or None when it points nowhere in this file."""
    if link is None:
        return None
    if "uri" in link:
        return link["uri"]
    return f"#page-{link['page']}" if link["page"] in numbers else None


def _document(title, lang, style, body):
    return ("<!DOCTYPE html>\n"
            f'<html lang="{_e(lang)}">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<meta name="generator" content="ZPDF">\n'
            f"<title>{_e(title)}</title>\n<style>{style}</style>\n</head>\n"
            f"<body>\n{body}\n</body>\n</html>\n")


def pdf_to_html(path, output, password=None, pages=None, mode="flowing"):
    """Convert a PDF to a single web page (see the module docstring).
    Returns {"output", "pages"}."""
    if mode not in MODES:
        raise core.PdfManagerError(f"mode must be one of {', '.join(MODES)}")
    output = Path(output)
    if output.is_dir():
        raise core.PdfManagerError(f"{output} is a folder; give the name of the file to write")
    tables = _tables(path, password, pages) if mode == "flowing" else {}
    with layout.Document(path, password, pages) as doc:
        lang = doc.lang if _LANG.fullmatch(doc.lang) else "en"
        if mode == "exact":
            title, body, fonts = _exact(doc)
            style = EXACT_CSS + fonts
        else:
            title, body = _flowing(doc, tables)
            style = FLOWING_CSS
        count = len(doc.indices)
    title = title or Path(path).stem
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_document(title, lang, style, body), encoding="utf-8")
    return {"output": output, "pages": count}


# --------------------------------------------------------------------------
# Exact layout
# --------------------------------------------------------------------------

EXACT_CSS = """
html{background:#e8e9eb}
body{margin:0;padding:16px 8px}
.page{position:relative;margin:0 auto 16px;background:#fff;overflow:hidden;
  container-type:inline-size;box-shadow:0 1px 4px rgba(0,0,0,.25)}
.page>img{position:absolute;inset:0;width:100%;height:100%;user-select:none}
.page>span{position:absolute;white-space:pre;line-height:1;transform-origin:0 0;color:#000}
.page>a.area{position:absolute;display:block}
.page a{color:inherit}
@media print{html{background:none}body{padding:0}
  .page{box-shadow:none;margin:0;break-after:page}}
"""


def _exact(doc):
    """(title, body, CSS for the fonts used)."""
    pages, numbers, fonts = [], {i + 1 for i in doc.indices}, {}

    def font_class(family):
        if family not in fonts:
            fonts[family] = f"f{len(fonts)}"
        return fonts[family]

    for n, index in enumerate(doc.indices):
        progress.report(n, len(doc.indices), "page")
        page = doc.page(index)
        w, h = page.width, page.height
        image = doc.render(index, PICTURE_DPI, without_text=True)
        parts = [f'<section class="page" id="page-{page.number}" aria-label="Page {page.number}" '
                 f'style="max-width:{_num(w * PX)}px;aspect-ratio:{_num(w)}/{_num(h)}">',
                 f'<img src="{_data_uri(image)}" alt="">']
        linked = set()
        for line in page.lines if doc.has_background(index) else []:
            size, family = line.size, line.family
            k = CSS_BASELINE.get(family, 0.85)
            # A span for each part of the line between wide white space (a
            # bullet and its text, cells of a table), placed where it starts.
            pieces = line.pieces()
            for n_piece, (start, end) in enumerate(pieces):
                x, y = layout.to_page(line.angle, line.chars[start].x0, line.base - k * size)
                style = (f"left:{_num(x / w * 100)}%;top:{_num(y / h * 100)}%;"
                         f"font-size:{_num(size / w * 100)}cqw")
                if line.angle:
                    style += f";transform:rotate({line.angle}deg)"
                # Spaces as wide as in the PDF (justified text).
                chars = line.chars[start:end]
                spaces = sum(1 for c in chars[1:] if c.space)
                if spaces and family in SPACE_WIDTH:
                    natural = sum(c.x1 - c.x0 for c in chars) + spaces * SPACE_WIDTH[family] * size
                    extra = (chars[-1].x1 - chars[0].x0 - natural) / spaces
                    if abs(extra) > 0.2:
                        style += f";word-spacing:{_num(extra / w * 100)}cqw"
                runs = []
                for text, c in line.runs(start, end):
                    runs.append(_exact_run(text, c, size, w, family, font_class, page, numbers,
                                           linked))
                if n_piece < len(pieces) - 1:
                    runs.append(" ")  # copied text keeps the space
                parts.append(f'<span class="{font_class(family)}" style="{style}">'
                             f'{"".join(runs)}</span>')
        for i, link in enumerate(page.links):
            href = _href(link, numbers)
            if href and i not in linked:  # a link on a picture: a clickable area
                x0, y0, x1, y1 = link["box"]
                parts.append(f'<a class="area" href="{_e(href)}" aria-label="{_e(href)}" style="'
                             f'left:{_num(x0 / w * 100)}%;top:{_num(y0 / h * 100)}%;'
                             f'width:{_num((x1 - x0) / w * 100)}%;'
                             f'height:{_num((y1 - y0) / h * 100)}%"></a>')
        parts.append("</section>")
        pages.append("\n".join(parts))
    css = "".join(f".{cls}{{font-family:{_font_stack(family)}}}\n" for family, cls in fonts.items())
    return doc.title, "<main>\n" + "\n".join(pages) + "\n</main>", css


def _exact_run(text, c, size, w, family, font_class, page, numbers, linked):
    css = []
    if abs(c.size - size) > 0.05:
        css.append(f"font-size:{_num(c.size / w * 100)}cqw")
    if c.bold:
        css.append("font-weight:bold")
    if c.italic:
        css.append("font-style:italic")
    if c.hidden:
        css.append("color:transparent")
    elif c.color != (0, 0, 0):
        css.append(f"color:#{layout.hex_color(c.color)}")
    cls = f' class="{font_class(c.family)}"' if c.family != family else ""
    attr = f' style="{";".join(css)}"' if css else ""
    piece = f"<span{cls}{attr}>{_e(text)}</span>" if cls or attr else _e(text)
    href = _href(page.links[c.link] if c.link is not None else None, numbers)
    if href:
        linked.add(c.link)
        piece = f'<a href="{_e(href)}">{piece}</a>'
    return piece


# --------------------------------------------------------------------------
# Flowing text
# --------------------------------------------------------------------------

FLOWING_CSS = """
html{background:#fff;color:#1f2328}
body{margin:0;font:17px/1.6 Georgia,"Times New Roman",serif}
main{max-width:46rem;margin:0 auto;padding:2rem 1.25rem 4rem}
h1,h2,h3{font-family:system-ui,-apple-system,"Segoe UI",Arial,sans-serif;line-height:1.25;
  margin:1.5em 0 .5em}
h1{font-size:1.9em}h2{font-size:1.45em}h3{font-size:1.15em}
p,ul,ol{margin:0 0 .9em}
a{color:#0b57d0}
code{font-size:.9em}
figure{margin:1.4em 0}
img{max-width:100%;height:auto}
table{border-collapse:collapse;margin:1.4em 0;font-size:.92em;display:block;overflow-x:auto}
th,td{border:1px solid #c8ccd1;padding:.3em .6em;text-align:left;vertical-align:top}
th{background:#f2f3f5}
.page-break{display:flex;align-items:center;gap:.75em;margin:2.5em 0 1.5em;color:#6b7280;
  font:12px/1 system-ui,-apple-system,"Segoe UI",Arial,sans-serif}
.page-break::before,.page-break::after{content:"";flex:1;border-top:1px solid #d6d9dd}
.page-break:first-child{margin-top:0}
@media print{.page-break{break-before:page;margin:0}
  .page-break:first-child{break-before:auto}.page-break span,.page-break::before,
  .page-break::after{display:none}}
"""


def _tables(path, password, pages):
    """{page index: [(PDF box, rows)]}: the tables on each page, as PDF → Excel
    finds them."""
    from pdfmanager import tables
    from pdfmanager.pageranges import parse_page_ranges

    found = {}
    with tables._open(path, password) as pdf:
        total = len(pdf.pages)
        for index in dict.fromkeys(parse_page_ranges(pages, total) if pages else range(total)):
            page = pdf.pages[index]
            if page.chars:
                found[index] = [(tables._pdf_bbox(page, t["bbox"]), t["rows"])
                                for t in tables._page_tables(page) if _table_like(t)]
            page.close()
    return found


def _table_like(table):
    """Ruled tables, and tables without lines whose rows mostly have two or
    more cells filled (not a few labels that happen to line up)."""
    if table["kind"] == "ruled":
        return True
    rows = table["rows"]
    return sum(1 for row in rows if sum(1 for cell in row if cell) >= 2) >= 0.8 * len(rows)


class _Item:
    """Something on a page, in reading order: kind "block", "table" or "image"."""

    def __init__(self, kind, box, value):
        self.kind, self.box, self.value = kind, box, value


def _split(items, axis, min_gap):
    """Items in groups with white space between them along ``axis`` (0: side
    by side, 1: one above the other), or None."""
    items = sorted(items, key=lambda i: i.box[axis])
    groups, end = [[items[0]]], items[0].box[axis + 2]
    for item in items[1:]:
        if item.box[axis] - end >= min_gap:
            groups.append([])
        groups[-1].append(item)
        end = max(end, item.box[axis + 2])
    return groups if len(groups) > 1 else None


def _reading_order(items, size=12):
    """A recursive XY cut: a wide band of white space across the page first
    (between sections), then columns side by side, read one after the other,
    then any white space across. ``size``: the usual text size."""
    if len(items) <= 1:
        return items
    groups = _split(items, 1, 1.8 * size)
    if not groups:
        groups = _split(items, 0, 6)
        if groups and _rows_line_up(groups):
            groups = None  # a form or a table: labels and values side by side, read row by row
    groups = groups or _split(items, 1, 0.5)
    if not groups:
        return sorted(items, key=lambda i: (i.box[1], i.box[0]))
    return [item for group in groups for item in _reading_order(group, size)]


def _rows_line_up(groups):
    """Whether most items have one in another group at the same height (the
    same top and bottom): rows, not columns."""
    total = matched = 0
    for k, group in enumerate(groups):
        others = [i for j, g in enumerate(groups) if j != k for i in g]
        for item in group:
            total += 1
            h = item.box[3] - item.box[1]
            if any(abs(o.box[1] - item.box[1]) < 0.3 * h and abs(o.box[3] - item.box[3]) < 0.3 * h
                   for o in others):
                matched += 1
    return matched >= total * 0.5


def _row_pieces(lines):
    """ids of the lines that sit on a row with another line far from them
    (wide white space between, compared with their length): a label and its
    value, not two columns of text."""
    pieces = set()
    rows = sorted(lines, key=lambda ln: (ln.angle, ln.base))
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if b.angle != a.angle or b.base - a.base > 0.2 * a.size:
                break
            left, right = (a, b) if a.x0 <= b.x0 else (b, a)
            gap = right.x0 - left.x1
            if gap > 0 and gap > 0.5 * min(a.x1 - a.x0, b.x1 - b.x0):
                pieces.update((id(a), id(b)))
    return pieces


def _merge_boxes(boxes):
    boxes = [list(b) for b in boxes]
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]:
                    boxes[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    del boxes[j]
                    merged = True
                    break
            if merged:
                break
    return [tuple(b) for b in boxes]


def _block_box(block):
    return layout.box_on_page(block.angle, block.x0, block.top, block.x1, block.bottom)


def _edge_key(line, page):
    """The text of a line at the very top or bottom of a page, digits
    blanked (page numbers), to find running headers and footers."""
    x0, y0, x1, y1 = layout.box_on_page(line.angle, line.x0, line.top, line.x1, line.bottom)
    if y1 < page.height * 0.09 or y0 > page.height * 0.91:
        return re.sub(r"\d+", "#", " ".join(line.text.lower().split()))
    return None


def _flowing(doc, tables):
    read = []
    sizes, keys = {}, {}
    for n, index in enumerate(doc.indices):
        progress.report(n, len(doc.indices), "page")
        page = doc.page(index)
        items = _page_items(doc, page, tables.get(index, []))
        read.append((page, items))
        for line in page.lines:
            sizes[round(line.main_size)] = sizes.get(round(line.main_size), 0) + len(line.chars)
        for key in {_edge_key(line, page) for line in page.lines} - {None}:
            keys[key] = keys.get(key, 0) + 1
    body = max(sizes, key=sizes.get) if sizes else 12
    repeated = {k for k, count in keys.items() if len(read) >= 3 and count >= len(read) * 0.6}
    bold_body = _mostly_bold(read)
    numbers = {page.number for page, _ in read}
    out, title = [], ""
    for page, items in read:
        out.append(f'<div class="page-break" id="page-{page.number}"><span>Page {page.number}</span></div>')
        writer = _Writer(body, bold_body, numbers, page)
        for item in items:
            if item.kind == "block":
                lines = [ln for ln in item.value.lines if _edge_key(ln, page) not in repeated]
                if lines:
                    writer.block(item.value, lines)
            elif item.kind == "table":
                writer.table(item.value)
            else:
                writer.image(item.value, item.box)
        out += writer.finish()
        title = title or writer.first_heading
    return doc.title or title, "<main>\n" + "\n".join(out) + "\n</main>"


def _mostly_bold(read):
    bold = total = 0
    for page, _ in read:
        for c in page.chars:
            total += 1
            bold += c.bold
    return total and bold > total * 0.6


def _page_items(doc, page, page_tables):
    """The blocks, tables and pictures of a page, in reading order."""
    geometry = page.geometry
    items = []
    table_boxes = []
    for pdf_box, rows in page_tables:
        box = geometry.box(*pdf_box)
        table_boxes.append(box)
        items.append(_Item("table", box, rows))

    def in_table(line):
        x0, y0, x1, y1 = layout.box_on_page(line.angle, line.x0, line.top, line.x1, line.bottom)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        return any(b[0] - 2 <= cx <= b[2] + 2 and b[1] - 2 <= cy <= b[3] + 2 for b in table_boxes)

    pieces = _row_pieces(page.lines)
    for block in page.blocks:
        cut = table_boxes and any(in_table(ln) for ln in block.lines)
        if cut or all(len(p) == 1 for p in block.paragraphs) or \
                sum(id(ln) in pieces for ln in block.lines) * 2 > len(block.lines):
            # A line at a time: what a table leaves of a block, lines that
            # are paragraphs of their own, and labels beside their values
            # (then read row by row).
            for line in block.lines:
                if not (cut and in_table(line)):
                    part = layout.Block(line).finish()
                    items.append(_Item("block", _block_box(part), part))
        else:
            items.append(_Item("block", _block_box(block), block))
    pictures = _merge_boxes(page.images)
    scanned = page.chars and all(c.hidden for c in page.chars)
    if not page.chars and not pictures:
        # Drawings only (or an empty page): the whole page as a picture,
        # unless it is blank.
        image = doc.render(page.number - 1, PICTURE_DPI)
        if image.convert("L").getextrema()[0] < 250:
            items.append(_Item("image", (0, 0, page.width, page.height), image))
    elif pictures:
        rendered = doc.render(page.number - 1, PICTURE_DPI, without_text=True)
        scale = PICTURE_DPI / 72
        for box in pictures:
            if scanned and (box[2] - box[0]) * (box[3] - box[1]) > page.width * page.height * 0.6:
                continue  # an OCRed scan: its text is the content
            crop = rendered.crop(tuple(int(round(v * scale)) for v in box))
            items.append(_Item("image", box, crop))
    # Read in the direction of most of the text (a page of turned text is
    # read along its lines).
    angles = {}
    for c in page.chars:
        angles[c.angle] = angles.get(c.angle, 0) + 1
    angle = max(angles, key=angles.get) if angles else 0
    size = statistics.median(ln.size for ln in page.lines) if page.lines else 12
    if angle:
        u, n = layout.direction(angle)
        shown = {}
        for item in items:
            shown[id(item)] = item.box
            x0, y0, x1, y1 = item.box
            points = [(x * u[0] + y * u[1], x * n[0] + y * n[1]) for x in (x0, x1) for y in (y0, y1)]
            item.box = (min(p[0] for p in points), min(p[1] for p in points),
                        max(p[0] for p in points), max(p[1] for p in points))
        items = _reading_order(items, size)
        for item in items:
            item.box = shown[id(item)]
        return items
    return _reading_order(items, size)


class _Writer:
    """HTML for one page's items: paragraphs, headings, lists, tables, pictures."""

    def __init__(self, body, bold_body, numbers, page):
        self.body, self.bold_body, self.numbers, self.page = body, bold_body, numbers, page
        self.out, self.list_kind, self.first_heading = [], None, ""
        self.last_line = None  # (index in out, line, closing tag, heading): to join pieces

    # -- text -----------------------------------------------------------------

    def _segments(self, lines):
        """[(text, Char)] for lines joined into a paragraph."""
        segments = []
        for k, line in enumerate(lines):
            runs = [[t, c] for t, c in line.runs()]
            if k and segments and runs:
                prev = segments[-1][0]
                if (prev.endswith("-") and runs[0][0][:1].islower() and len(prev) > 1
                        and prev[-2].isalpha()):
                    segments[-1][0] = prev[:-1]  # a hyphenated word, joined back up
                elif not prev.endswith((" ", "-", "/")):
                    segments[-1][0] = prev + " "
            segments += runs
        return segments

    def _inline(self, segments, heading=False):
        colors = {}
        for text, c in segments:
            colors[c.color] = colors.get(c.color, 0) + len(text)
        main = max(colors, key=colors.get) if colors else None
        parts, open_href = [], None
        for text, c in segments:
            href = _href(self.page.links[c.link] if c.link is not None else None, self.numbers)
            if href != open_href:
                if open_href:
                    parts.append("</a>")
                if href:
                    parts.append(f'<a href="{_e(href)}">')
                open_href = href
            stripped = text.strip()
            lead, trail = text[:len(text) - len(text.lstrip())], text[len(text.rstrip()):]
            if not stripped:
                parts.append(_e(text))
                continue
            piece = _e(stripped)
            if c.family == "Courier New":
                piece = f"<code>{piece}</code>"
            if c.italic:
                piece = f"<em>{piece}</em>"
            if c.bold and not heading and not self.bold_body:
                piece = f"<strong>{piece}</strong>"
            # Colour that stands out from the rest of the paragraph (and shows
            # on white).
            if not href and c.color != main and min(c.color) < 200 and not c.hidden:
                piece = f'<span style="color:#{layout.hex_color(c.color)}">{piece}</span>'
            parts.append(_e(lead) + piece + _e(trail))
        if open_href:
            parts.append("</a>")
        html_text = "".join(parts)
        # Tags opened and closed around single spaces: tidy up.
        return re.sub(r"</(strong|em|code)>(\s*)<\1>", r"\2", html_text).strip()

    def _level(self, lines):
        size = max(ln.main_size for ln in lines)
        text = " ".join(ln.text for ln in lines)
        if len(text) > 200 or len(lines) > 3:
            return 0
        if size > self.body * 1.5:
            return 1
        if size > self.body * 1.2:
            return 2
        if size > self.body * 1.08:
            return 3
        bold = all(c.bold for ln in lines for c in ln.chars)
        if bold and not self.bold_body and len(lines) == 1 and len(text) <= 80 \
                and text[-1:] not in ".,;:" and not layout.LIST_ITEM.match(text):
            return 3
        return 0

    def _close_list(self):
        if self.list_kind:
            self.out.append(f"</{self.list_kind}>")
            self.list_kind = None

    def block(self, block, lines):
        keep = set(map(id, lines))
        for para in block.paragraphs:
            para = [ln for ln in para if id(ln) in keep]
            if para:
                self.paragraph(para)

    def paragraph(self, lines):
        segments = self._segments(lines)
        text = "".join(t for t, _ in segments).strip()
        if not text:
            return
        first = lines[0]
        # A piece of a line that the page set apart (a value after its label):
        # joined to the paragraph it continues.
        if (self.last_line and len(lines) == 1 and not self.list_kind
                and first.angle == self.last_line[1].angle
                and abs(first.base - self.last_line[1].base) <= 0.25 * first.size
                and first.x0 > self.last_line[1].x1):
            index, _, closing, heading = self.last_line
            self.out[index] = (self.out[index][:-len(closing)] + " "
                               + self._inline(segments, heading) + closing)
            self.last_line = (index, first, closing, heading)
            return
        level = self._level(lines)
        marker = layout.LIST_ITEM.match(text)
        if level:
            self._close_list()
            tag = f"h{level}"
            body = self._inline(segments, heading=True)
            self.first_heading = self.first_heading or re.sub(r"<[^>]+>", "", body)
        elif marker:
            ordered = bool(marker.group(1))
            kind = "ol" if ordered else "ul"
            if self.list_kind != kind:
                self._close_list()
                start = ""
                if ordered and marker.group(1).isdigit() and marker.group(1) != "1":
                    start = f' start="{int(marker.group(1))}"'
                self.out.append(f"<{kind}{start}>")
                self.list_kind = kind
            segments = _drop_marker(segments, marker.end())
            tag, body = "li", self._inline(segments)
        else:
            self._close_list()
            tag, body = "p", self._inline(segments)
        if not body:
            return
        last = self.out[-1] if self.out else ""
        if (tag == "p" and text[:1].islower() and last.startswith("<p>")
                and not re.search(r"[.!?:;\"”’)\]]</p>$", last)):
            # The rest of a paragraph that went on in the next column.
            joined = last[:-4]
            if re.search(r"[^\W\d_]-$", joined):
                joined = joined[:-1]
            else:
                joined += " "
            self.out[-1] = joined + body + "</p>"
            self.last_line = None
            return
        closing = f"</{tag}>"
        self.out.append(f"<{tag}>{body}{closing}")
        self.last_line = ((len(self.out) - 1, lines[0], closing, bool(level))
                          if len(lines) == 1 else None)

    # -- tables and pictures ----------------------------------------------------

    def table(self, rows):
        self._close_list()
        self.last_line = None
        head = "".join(f"<th>{_e(cell)}</th>" for cell in rows[0])
        body = "".join("<tr>" + "".join(f"<td>{_e(cell)}</td>" for cell in row) + "</tr>"
                       for row in rows[1:])
        self.out.append(f"<table>\n<thead><tr>{head}</tr></thead>\n<tbody>{body}</tbody>\n</table>")

    def image(self, image, box):
        self._close_list()
        self.last_line = None
        w, h = (box[2] - box[0]) * PX, (box[3] - box[1]) * PX
        self.out.append(f'<figure><img src="{_data_uri(image)}" width="{round(w)}" '
                        f'height="{round(h)}" alt="Picture on page {self.page.number}"></figure>')

    def finish(self):
        self._close_list()
        return self.out


def _drop_marker(segments, length):
    """Segments without the first ``length`` characters (a list marker)."""
    out, skip = [], length
    for text, c in segments:
        if skip >= len(text):
            skip -= len(text)
            continue
        out.append((text[skip:], c))
        skip = 0
    return out
