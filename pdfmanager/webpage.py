"""PDF → HTML: one web page (.html) with everything in it (pictures as data:
URIs), no scripts.

  * "flowing": a readable page that reflows to fit any window: headings
    (from the text's size, or a short line in bold, capitals or its own
    colour over its text), paragraphs (wrapped lines joined, hyphenated
    words joined back up), lists, tables (found like PDF → Excel finds
    them; labels beside their values are not one), pictures (a drawing
    with lettering on it too), links (web addresses, and links to other
    pages as #page-N), and a marker where each page starts. Columns are
    read one after the other, past a quotation or a figure set across
    them. Running headers and footers and page numbers are left out.
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
LABELS = 6  # lines of text on a picture from which they are its labels, not the page's text
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


_FIGURE = re.compile(r"^[-+−–(]?[£$€¥]?\s?\d[\d\s.,:/%-]*\)?\s?[%£$€¥]?$")


def _table_like(table):
    """Ruled tables, and tables without lines that are a grid of values:
    most rows with two or more cells filled, few cells empty. Not columns of
    sentences, and not labels that happen to line up with what stands
    beside them (a form, the details at the top of an invoice, a column of
    totals): with only two columns, a table has a heading row over a
    column of figures."""
    if table["kind"] == "ruled":
        return True
    rows = table["rows"]
    cells = [cell for row in rows for cell in row if cell]
    if cells and sum(len(str(cell).split()) for cell in cells) >= 5 * len(cells):
        return False  # sentences side by side: columns of text, not a table
    below = [str(cell).strip() for row in rows[1:] for cell in row if cell]
    if below and sum(len(cell.split()) for cell in below) >= 3 * len(below) and \
            sum(1 for cell in below if cell[:1].islower()) >= 0.5 * len(below):
        return False  # lines that go on from the line above: narrow columns of text
    if sum(1 for row in rows if sum(1 for cell in row if cell) >= 2) < 0.8 * len(rows):
        return False
    columns = max(len(row) for row in rows)
    if len(cells) < 0.7 * columns * len(rows):
        return False  # a third of the grid empty: things set side by side
    for k in range(columns):
        column = [str(row[k]).strip() for row in rows if k < len(row) and row[k]]
        if column and sum(1 for cell in column if cell.endswith(":")) >= 0.6 * len(column):
            return False  # "Date:", "Invoice no:": labels before their values
    if columns == 2:
        figures = sum(1 for row in rows[1:] if len(row) == 2 and _FIGURE.match(str(row[1]).strip()))
        heading = not any(_FIGURE.match(str(cell).strip()) for cell in rows[0])
        return len(rows) >= 4 and heading and figures >= 0.6 * (len(rows) - 1)
    return True


class _Item:
    """Something on a page, in reading order: kind "block", "table" or "image"."""

    def __init__(self, kind, box, value):
        self.kind, self.box, self.value = kind, box, value
        self.bold = None  # of a table: which of its cells are in bold type


def _bold_cells(rows, lines):
    """[[whether the cell is set in bold]] for a table's rows, from the
    lines of text found where it is."""
    squeeze = lambda text: re.sub(r"\s+", "", text)  # noqa: E731
    bold, plain = set(), set()
    for line in lines:
        for start, end in line.pieces():
            chars = line.chars[start:end]
            (bold if all(c.bold for c in chars) else plain).add(squeeze("".join(c.text for c in chars)))
    bold.discard("")

    def made_of_bold(text):
        if text in plain:
            return False
        while text:
            part = max((b for b in bold if text.startswith(b)), key=len, default="")
            if not part:
                return False
            text = text[len(part):]
        return True

    return [[bool(cell) and made_of_bold(squeeze(str(cell))) for cell in row] for row in rows]


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
    then any white space across. Something set across some of the columns
    while another runs past it (a pull quote, a figure) doesn't cut them:
    they are read past it. ``size``: the usual text size."""
    if len(items) <= 1:
        return items
    groups = _split(items, 1, 1.8 * size)
    if not groups:
        floats = _across_columns(items, size)
        if floats:
            return _with_floats(_reading_order([i for i in items if i not in floats], size), floats)
        groups = _split(items, 0, 6)
        if groups and _rows_line_up(groups) and not _columns_of_prose(groups):
            # A form or a table: labels and values side by side are read row
            # by row, beside whatever stands in a column of its own.
            rows, joined, before = _rows_line_up(groups), [], False
            for group in groups:
                takes = any(id(item) in rows for item in group)
                if joined and takes and before:
                    joined[-1] = joined[-1] + group
                else:
                    joined.append(group)
                before = takes
            groups = joined if len(joined) > 1 else None
    groups = groups or _split(items, 1, 0.5)
    if not groups:
        # Side by side at the same height (however tall each is): left to right.
        rows = []
        for item in sorted(items, key=lambda i: (i.box[1], i.box[0])):
            row = rows[-1] if rows else None
            if row and min(row[0].box[3], item.box[3]) - item.box[1] > 0.5 * min(
                    row[0].box[3] - row[0].box[1], item.box[3] - item.box[1]):
                row.append(item)
            else:
                rows.append([item])
        return [item for row in rows for item in sorted(row, key=lambda i: i.box[0])]
    return [item for group in groups for item in _reading_order(group, size)]


def _across_columns(items, size):
    """The items that lie across two or more columns of running text while
    another column runs past them at their side."""
    prose = [i for i in items if _is_prose(i)]
    if len(prose) < 3:
        return []
    found = []
    for item in items:
        x0, y0, x1, y1 = item.box
        under = [p for p in prose if p is not item and p.box[2] - p.box[0] < 0.8 * (x1 - x0)
                 and min(x1, p.box[2]) - max(x0, p.box[0]) > 0.5 * (p.box[2] - p.box[0])
                 and (p.box[3] <= y0 + 0.5 * size or p.box[1] >= y1 - 0.5 * size)]
        if not any(a.box[2] + 6 <= b.box[0] or b.box[2] + 6 <= a.box[0]
                   for k, a in enumerate(under) for b in under[k + 1:]):
            continue
        beside = sum(max(0, min(y1, p.box[3]) - max(y0, p.box[1])) for p in prose
                     if p is not item and (p.box[2] <= x0 - 6 or p.box[0] >= x1 + 6))
        if beside >= 0.5 * (y1 - y0):
            found.append(item)
    return found


def _with_floats(ordered, floats):
    """``ordered`` with each of ``floats`` after the last item above it in
    the columns it lies across (or before the first one below it)."""
    for item in sorted(floats, key=lambda i: (i.box[1], i.box[0])):
        x0, y0, x1, y1 = item.box
        above = below = None
        for k, other in enumerate(ordered):
            if min(x1, other.box[2]) <= max(x0, other.box[0]):
                continue
            if (other.box[1] + other.box[3]) / 2 < y0:
                above = k
            elif below is None:
                below = k
        ordered.insert(above + 1 if above is not None else below if below is not None else len(ordered), item)
    return ordered


def _is_prose(item):
    """A block of running text: several lines, or one long one."""
    if item.kind != "block":
        return False
    lines = item.value.lines
    return len(lines) >= 2 or len(lines[0].text.split()) >= 6


def _columns_of_prose(groups):
    """Whether two or more of the groups hold running text: columns to read
    one after the other (a grid of features, each a heading over its
    paragraph, lines up row by row just as a form does)."""
    return sum(1 for group in groups if any(_is_prose(item) for item in group)) >= 2


def _rows_line_up(groups):
    """When most items have one in another group at the same height (the
    same top and bottom), rows, not columns: the ids of those items.
    Otherwise nothing."""
    total, matched = 0, set()
    for k, group in enumerate(groups):
        others = [i for j, g in enumerate(groups) if j != k for i in g]
        for item in group:
            total += 1
            h = item.box[3] - item.box[1]
            if any(abs(o.box[1] - item.box[1]) < 0.3 * h and abs(o.box[3] - item.box[3]) < 0.3 * h
                   for o in others):
                matched.add(id(item))
    return matched if len(matched) >= total * 0.5 else set()


def _row_pieces(lines, wrapped=()):
    """ids of the lines that sit on a row with another line far from them
    (wide white space between, compared with their length): a label and its
    value, not two columns of text. ``wrapped``: ids of the lines of
    paragraphs of three lines or more of several words, which are running
    text whatever stands beside them."""
    pieces = set()
    rows = sorted(lines, key=lambda ln: (ln.angle, ln.base))
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if b.angle != a.angle or b.base - a.base > 0.2 * a.size:
                break
            left, right = (a, b) if a.x0 <= b.x0 else (b, a)
            gap = right.x0 - left.x1
            if min(len(a.text.split()), len(b.text.split())) >= 5:
                continue  # two lines of running text: columns, however far apart
            if id(a) in wrapped or id(b) in wrapped:
                continue
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


DRAWING_PARTS = 6    # lines and shapes drawn together from which they are a drawing
DRAWING_LABELS = 3   # words on it from which it is shown as a picture, lettering and all


def _clusters(boxes, reach):
    """Boxes in groups that touch or lie within ``reach`` of each other."""
    order = sorted(range(len(boxes)), key=lambda i: boxes[i][0])
    parent = list(range(len(boxes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    active = []
    for i in order:
        a = boxes[i]
        active = [j for j in active if boxes[j][2] + reach >= a[0]]
        for j in active:
            b = boxes[j]
            if a[1] <= b[3] + reach and b[1] <= a[3] + reach:
                parent[find(i)] = find(j)
        active.append(i)
    groups = {}
    for i in range(len(boxes)):
        groups.setdefault(find(i), []).append(boxes[i])
    return list(groups.values())


def _drawings(page, lines, wrapped, taken, body):
    """Drawings made of lines and shapes with lettering on them (a flow
    diagram, a chart): [(box, the lines of text that belong to it)]. Several
    parts drawn together, more than rules and one box, with a few short
    labels on or right beside them and no running text; not where a table
    or a picture was found (``taken``, boxes)."""
    area = page.width * page.height
    shapes = [b for b in page.shapes
              if (b[2] - b[0]) * (b[3] - b[1]) < 0.5 * area and max(b[2] - b[0], b[3] - b[1]) > 1
              and b[2] > 0 and b[3] > 0 and b[0] < page.width and b[1] < page.height]
    if not DRAWING_PARTS <= len(shapes) <= 4000:
        return []
    boxed = [(ln, layout.box_on_page(ln.angle, ln.x0, ln.top, ln.x1, ln.bottom)) for ln in lines]
    found = []
    for cluster in _clusters(shapes, 3):
        if len(cluster) < DRAWING_PARTS or sum(1 for b in cluster if min(b[2] - b[0], b[3] - b[1]) > 3) < 2:
            continue
        x0, y0 = min(b[0] for b in cluster), min(b[1] for b in cluster)
        x1, y1 = max(b[2] for b in cluster), max(b[3] for b in cluster)
        if x1 - x0 < 40 or y1 - y0 < 30 or (x1 - x0) * (y1 - y0) > 0.6 * area:
            continue
        if any(min(x1, t[2]) > max(x0, t[0]) and min(y1, t[3]) > max(y0, t[1]) for t in taken):
            continue
        # Bands across the whole of it, one under the other: the tint of a
        # table's rows, not a drawing.
        if sum(1 for b in cluster if b[0] <= x0 + 1 and b[2] >= x1 - 1) >= 0.8 * len(cluster):
            continue
        within = [ln for ln, b in boxed if x0 <= (b[0] + b[2]) / 2 <= x1 and y0 <= (b[1] + b[3]) / 2 <= y1]
        if any(len(ln.text.split()) > 6 or id(ln) in wrapped for ln in within):
            continue  # text in a frame, not lettering
        labels = {id(ln) for ln in within}
        box = [x0, y0, x1, y1]
        for ln, b in boxed:
            if id(ln) not in labels:
                continue
            box = [min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3])]
        # Lettering right beside it (the figures along an axis), and a short
        # title centred over it in type no larger than the text's.
        for ln, b in boxed:
            if id(ln) in labels or id(ln) in wrapped:
                continue
            words, size = len(ln.text.split()), ln.size
            over = min(b[2], x1) - max(b[0], x0) > 0 and min(b[3], y1) - max(b[1], y0) > -1.2 * size
            beside = min(b[3], y1) - max(b[1], y0) > 0 and min(b[2], x1) - max(b[0], x0) > -1.2 * size
            title = (b[3] <= y0 + size and y0 - b[3] <= 2 * size and size <= 1.1 * body
                     and abs((b[0] + b[2]) / 2 - (x0 + x1) / 2) <= 0.15 * (x1 - x0) and b[0] > x0)
            if (words <= 3 and (over or beside) and size <= 1.1 * body) or (words <= 6 and title):
                labels.add(id(ln))
                box = [min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3])]
        if len(labels) >= DRAWING_LABELS:
            box = (max(0, box[0] - 2), max(0, box[1] - 2), min(page.width, box[2] + 2), min(page.height, box[3] + 2))
            found.append((box, [ln for ln in lines if id(ln) in labels]))
            boxed = [(ln, b) for ln, b in boxed if id(ln) not in labels]
    return found


def _block_box(block):
    return layout.box_on_page(block.angle, block.x0, block.top, block.x1, block.bottom)


_PAGE_NUMBER = re.compile(r"(?i)^(page\s*)?\d{1,4}(\s*(/|of)\s*\d{1,4})?$")


def _running(read, body):
    """ids of the lines that are running headers and footers and page
    numbers. At the very top or bottom of a page (its outer 9%): a page
    number, or the same line (digits apart) on most of three or more pages.
    Standing clear of the text (white space between): a page number up to
    13% in, the same line on most pages up to 15% in, and, at the very
    edge, a few words in smaller type than the text (a document's name, a
    web address) on a page with text enough to tell."""
    pages, counts = [], {}
    for page, _ in read:
        boxes = [(ln, layout.box_on_page(ln.angle, ln.x0, ln.top, ln.x1, ln.bottom)) for ln in page.lines]
        found = []
        for line, (x0, y0, x1, y1) in boxes:
            top = (y0 + y1) / 2 < page.height / 2
            depth = (y1 if top else page.height - y0) / page.height
            if depth > 0.15:
                continue
            slack = 0.3 * (y1 - y0)
            if top:  # to the nearest line that reaches further down the page
                gap = min((b[1] for _, b in boxes if b[3] > y1 + slack), default=page.height) - y1
            else:
                gap = y0 - max((b[3] for _, b in boxes if b[1] < y0 - slack), default=0)
            key = (top, re.sub(r"\d+", "#", " ".join(line.text.lower().split())))
            found.append((line, depth, gap / max(line.size, 1), key))
        for key in {entry[3] for entry in found}:
            counts[key] = counts.get(key, 0) + 1
        pages.append((page, found))
    total, running = len(pages), set()
    for page, found in pages:
        for line, depth, clear, key in found:
            text = " ".join(line.text.split())
            number = bool(_PAGE_NUMBER.match(text))
            often = total >= 2 and counts[key] >= max(2, total * 0.6)
            if depth <= 0.09:
                gone = (number or (often and total >= 3) or (often and clear >= 1.5)
                        or (clear >= 2.5 and line.main_size <= 0.95 * body and len(text.split()) <= 6
                            and len(page.lines) >= 8 and text[-1:] not in ".,;!?" and text[:1].isalnum()))
            else:
                gone = (number and depth <= 0.13 and clear >= 2) or (often and clear >= 1.5)
            if gone:
                running.add(id(line))
    return running


def flow(path, password=None, pages=None):
    """The document as the flowing web page has it, for another writer:
    (title, [the items of each page in reading order]). An item is a dict:
    {"kind": "heading", "level": 1-3, "segments"}, {"kind": "paragraph",
    "segments"}, {"kind": "item", "segments"} (a list item, marker and
    all), {"kind": "table", "rows", "bold" ([[whether the cell is in bold
    type]] or None)} or {"kind": "image", "image", "width" (points)}. "segments" is [(text, layout.Char with its look)], and
    "links" the page's links a Char's ``link`` indexes."""
    tables = _tables(path, password, pages)
    with layout.Document(path, password, pages) as doc:
        collected = []
        title, _ = _flowing(doc, tables, collected)
    return title or Path(path).stem, collected


def _flowing(doc, tables, collected=None):
    read = []
    sizes = {}
    for n, index in enumerate(doc.indices):
        progress.report(n, len(doc.indices), "page")
        page = doc.page(index)
        items = _page_items(doc, page, tables.get(index, []))
        read.append((page, items))
        for line in page.lines:
            sizes[round(line.main_size)] = sizes.get(round(line.main_size), 0) + len(line.chars)
    body = max(sizes, key=sizes.get) if sizes else 12
    running = _running(read, body)
    bold_body = _mostly_bold(read)
    numbers = {page.number for page, _ in read}
    out, title = [], ""
    for page, items in read:
        out.append(f'<div class="page-break" id="page-{page.number}"><span>Page {page.number}</span></div>')
        writer = _Writer(body, bold_body, numbers, page)
        todo = []
        for item in items:
            if item.kind != "block":
                todo.append((item.kind, item))
                continue
            for para in item.value.paragraphs:
                para = [ln for ln in para if id(ln) not in running]
                if para:
                    todo.append(("text", para))
        for k, (kind, value) in enumerate(todo):
            if kind == "text":
                after = todo[k + 1][1] if k + 1 < len(todo) and todo[k + 1][0] == "text" else None
                writer.paragraph(value, after)
            elif kind == "table":
                writer.table(value.value, value.bold)
            else:
                writer.image(value.value, value.box)
        out += writer.finish()
        if collected is not None:
            collected.append(writer.items)
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

    def in_table(line, boxes=None):
        x0, y0, x1, y1 = layout.box_on_page(line.angle, line.x0, line.top, line.x1, line.bottom)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        return any(b[0] - 2 <= cx <= b[2] + 2 and b[1] - 2 <= cy <= b[3] + 2 for b in boxes or table_boxes)

    for item in items:
        item.bold = _bold_cells(item.value, [ln for ln in page.lines if in_table(ln, [item.box])])

    # The words inside a picture (the labels of an app in a screenshot, of a
    # diagram) are part of it: they stay in the picture, not in the text.
    pictures = _merge_boxes(page.images)
    scanned = page.chars and all(c.hidden for c in page.chars)
    labelled, inside = set(), set()
    for box in pictures:
        if scanned or (box[2] - box[0]) * (box[3] - box[1]) > page.width * page.height * 0.6:
            continue
        within = []
        for line in page.lines:
            x0, y0, x1, y1 = layout.box_on_page(line.angle, line.x0, line.top, line.x1, line.bottom)
            if box[0] <= (x0 + x1) / 2 <= box[2] and box[1] <= (y0 + y1) / 2 <= box[3]:
                within.append(line)
        if len(within) >= LABELS:
            labelled.add(box)
            inside.update(map(id, within))

    wrapped = {id(ln) for block in page.blocks for para in block.paragraphs
               if len(para) >= 3 and sum(len(ln.text.split()) for ln in para) >= 4 * len(para) for ln in para}
    # The lettering of a drawing made of lines and shapes is part of it too:
    # the drawing is shown as a picture, lettering and all.
    drawings = []
    if not scanned:
        outside = [ln for ln in page.lines if id(ln) not in inside and not (table_boxes and in_table(ln))]
        size = statistics.median(ln.main_size for ln in page.lines) if page.lines else 12
        drawings = _drawings(page, outside, wrapped, table_boxes + pictures, size)
        for box, lettering in drawings:
            inside.update(map(id, lettering))
    pieces = _row_pieces([ln for ln in page.lines if id(ln) not in inside], wrapped)
    for block in page.blocks:
        if inside and any(id(ln) in inside for ln in block.lines):
            for line in block.lines:
                if id(line) not in inside and not (table_boxes and in_table(line)):
                    part = layout.Block(line).finish()
                    items.append(_Item("block", _block_box(part), part))
            continue
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
    with_words = None
    scale = PICTURE_DPI / 72
    if drawings:
        with_words = doc.render(page.number - 1, PICTURE_DPI)
        for box, _ in drawings:
            crop = with_words.crop(tuple(int(round(v * scale)) for v in box))
            items.append(_Item("image", box, crop))
    if not page.chars and not pictures:
        # Drawings only (or an empty page): the whole page as a picture,
        # unless it is blank.
        image = doc.render(page.number - 1, PICTURE_DPI)
        if image.convert("L").getextrema()[0] < 250:
            items.append(_Item("image", (0, 0, page.width, page.height), image))
    elif pictures:
        rendered = doc.render(page.number - 1, PICTURE_DPI, without_text=True)
        for box in pictures:
            if scanned and (box[2] - box[0]) * (box[3] - box[1]) > page.width * page.height * 0.6:
                continue  # an OCRed scan: its text is the content
            source = rendered
            if box in labelled:
                if with_words is None:
                    with_words = doc.render(page.number - 1, PICTURE_DPI)
                source = with_words
            crop = source.crop(tuple(int(round(v * scale)) for v in box))
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


_CJK = re.compile("[\u2e80-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")
_COMPOUND_FIRST = {"self", "well", "half", "all", "non", "quasi"}
_SENTENCE_END = re.compile(r"[.!?…][\"”’')\]]*$")
_OPENING_QUOTE = "“\"‘«„"


def _compound(before, after):
    """Whether a word broken at a hyphen at the end of a line keeps the
    hyphen: ``before`` is what stands before it, ``after`` the rest on the
    next line. A compound broken at its own hyphen (up-to-|date,
    up-|to-date, self-|financing), not a word hyphenated to fit the line."""
    after = after.strip(".,;:!?)”’\"'")
    if "-" in before.strip("-"):
        return True
    letters = re.sub(r"\W", "", before).lower()
    if "-" in after.strip("-") and len(letters) <= 4:
        return True
    return letters in _COMPOUND_FIRST and len(after) >= 4


class _Writer:
    """HTML for one page's items: paragraphs, headings, lists, tables, pictures."""

    def __init__(self, body, bold_body, numbers, page):
        self.body, self.bold_body, self.numbers, self.page = body, bold_body, numbers, page
        self.out, self.list_kind, self.first_heading = [], None, ""
        self.last_line = None  # (index in out, line, closing tag, heading): to join pieces
        # The same content, not as HTML, for the other writers (PDF → Word):
        # {"kind": "heading"|"paragraph"|"item"|"table"|"image", ...}; see flow().
        self.items, self._item_at = [], {}

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
                    before = "".join(t for t, _ in segments[-3:]).split()[-1][:-1]
                    after = "".join(t for t, _ in runs[:3]).split()[0]
                    if not _compound(before, after):
                        segments[-1][0] = prev[:-1]  # a hyphenated word, joined back up
                elif not prev.endswith((" ", "-", "/")) and not (_CJK.match(prev[-1]) and _CJK.match(runs[0][0][:1])):
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
            if c.raised:
                piece = f"<sup>{piece}</sup>"
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

    def _level(self, lines, after=None):
        """The level of the heading these lines are (0: not one). ``after``:
        the lines of the paragraph that follows, if text follows."""
        size = max(ln.main_size for ln in lines)
        text = " ".join(ln.text for ln in lines).strip()
        words = len(text.split())
        if len(text) > 200 or len(lines) > 3 or not any(ch.isalpha() for ch in text):
            return 0
        # A sentence in large type (a standfirst, a pull quote) is text.
        if (words >= 8 and text.endswith((".", ".”", '."', ".’"))) or \
                (text[0] in _OPENING_QUOTE and (len(lines) > 1 or _SENTENCE_END.search(text))):
            return 0
        if size > self.body * 1.5:
            return 1
        if size > self.body * 1.2:
            return 2
        chars = [c for ln in lines for c in ln.chars]
        bold = all(c.bold for c in chars) and not self.bold_body
        # Recognised text (a scan's hidden layer) is sized from its boxes,
        # a little differently line by line: only a clear step up counts.
        recognised = all(c.hidden for c in chars)
        first = lines[0]
        beside = (after is not None and after[0].angle == first.angle
                  and abs(after[0].base - first.base) <= 0.25 * first.size)  # a value beside its label
        if size > self.body * 1.08 and not recognised and (bold or (
                size > self.body * 1.14 and words <= 12 and not _SENTENCE_END.search(text) and not beside)):
            return 3
        if len(lines) > 1 or len(text) > 80 or words > 12 or beside or recognised:
            return 0
        numbered = layout.LIST_ITEM.match(text)
        introduces = self._introduces(lines, after)
        if bold:
            if numbered:
                # "3. Fees and charges" over its text, not an item of a list.
                return 3 if introduces and numbered.group(1) and not self.list_kind else 0
            if text.endswith(":"):
                return 3 if introduces and words >= 3 else 0
            if text[-1] in ".,;" or (words > 9 and not introduces):
                return 0
            if re.search(r":\s+\S", text) or re.search(r"\d[\d\s.,/-]{4,}$", text):
                return 0  # a label and its value ("Booking office: 01632 960 417")
            return 3
        if not introduces or numbered or text[-1] in ".,;:" or words > 8:
            return 0
        letters = [ch for ch in text if ch.isalpha()]
        if len(letters) >= 4 and all(ch.isupper() for ch in letters) and words >= 2:
            return 3  # a line of capitals over its text
        colors = {c.color for c in chars}
        follow = {c.color for ln in after for c in ln.chars}
        if (len(colors) == 1 and colors.isdisjoint(follow) and all(c.link is None for c in chars)
                and size > self.body * 1.03 and text[:1].isupper()):
            return 3  # a line in its own colour, a little larger, over its text
        return 0

    def _introduces(self, lines, after):
        """Whether ``after`` is running text set right under the line
        ``lines``, as under its heading."""
        if after is None:
            return False
        first, text = lines[0], " ".join(ln.text for ln in after)
        if after[0].angle != first.angle or all(c.bold for ln in after for c in ln.chars):
            return False
        if len(after) < 2 and len(text.split()) < 8:
            return False
        if layout.LIST_ITEM.match(text) or abs(after[0].x0 - first.x0) > 1.5 * first.size:
            return False
        return 0 < after[0].base - lines[-1].base <= 3.5 * first.size

    def _close_list(self):
        if self.list_kind:
            self.out.append(f"</{self.list_kind}>")
            self.list_kind = None

    def _same_row(self, first):
        """Whether the line ``first`` goes on the row of the line written
        last: on its baseline, to its right, nothing set between the two."""
        last = self.last_line[1]
        if (first.angle != last.angle or abs(first.base - last.base) > 0.25 * first.size
                or first.x0 <= last.x1 or max(first.size, last.size) > 1.2 * min(first.size, last.size)):
            return False
        for line in self.page.lines:
            if (line is not first and line is not last and line.angle == first.angle
                    and line.x0 >= last.x1 and line.x1 <= first.x0 and line.top < first.base < line.bottom):
                return False  # a masthead: a title between a line at each side
        return True

    def _unfinished(self):
        """The index in ``out`` of the paragraph that the text now coming
        may continue: the last one, or the one before a picture (and its
        caption) set into the text, when it stops in mid-sentence."""
        picture = False
        for back in range(1, min(4, len(self.out)) + 1):
            index = len(self.out) - back
            entry = self.out[index]
            if entry.startswith("<p>") and index in self._item_at:
                text = re.sub(r"<[^>]+>", "", entry).rstrip()
                if not re.search(r"[.!?:;\"”’)\]…]$", text):
                    return index if back == 1 or (picture and len(text.split()) >= 8) else None
            elif entry.startswith("<figure>"):
                picture = True
            else:
                return None
        return None

    def paragraph(self, lines, after=None):
        segments = self._segments(lines)
        text = "".join(t for t, _ in segments).strip()
        if not text:
            return
        first = lines[0]
        # A piece of a line that the page set apart (a value after its label):
        # joined to the paragraph it continues.
        if self.last_line and len(lines) == 1 and not self.list_kind and self._same_row(first):
            index, _, closing, heading = self.last_line
            self.out[index] = (self.out[index][:-len(closing)] + " "
                               + self._inline(segments, heading) + closing)
            self._item_at[index]["segments"] += [(" ", segments[0][1])] + list(segments)
            self.last_line = (index, first, closing, heading)
            return
        level = self._level(lines, after)
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
            whole = segments
            segments = _drop_marker(segments, marker.end())
            tag, body = "li", self._inline(segments)
        else:
            self._close_list()
            tag, body = "p", self._inline(segments)
        if not body:
            return
        index = self._unfinished() if tag == "p" and text[:1].islower() else None
        if index is not None:
            # The rest of a paragraph that went on in the next column, or
            # after a picture set into it.
            joined = self.out[index][:-4]
            earlier = self._item_at[index]["segments"]
            if re.search(r"[^\W\d_]-$", joined) and not _compound(
                    "".join(t for t, _ in earlier[-3:]).split()[-1][:-1], text.split()[0]):
                joined = joined[:-1]
                earlier[-1] = (earlier[-1][0].rstrip()[:-1], earlier[-1][1])
            elif not joined.endswith("-"):
                joined += " "
                earlier.append((" ", segments[0][1]))
            earlier += list(segments)
            self.out[index] = joined + body + "</p>"
            self.last_line = None
            return
        closing = f"</{tag}>"
        self.out.append(f"<{tag}>{body}{closing}")
        item = {"kind": "heading" if level else "item" if tag == "li" else "paragraph", "level": level,
                "segments": [(t, c) for t, c in (whole if tag == "li" else segments)],
                "links": self.page.links}
        self.items.append(item)
        self._item_at[len(self.out) - 1] = item
        self.last_line = ((len(self.out) - 1, lines[0], closing, bool(level))
                          if len(lines) == 1 else None)

    # -- tables and pictures ----------------------------------------------------

    def table(self, rows, bold=None):
        self._close_list()
        self.last_line = None
        head = "".join(f"<th>{_e(cell)}</th>" for cell in rows[0])
        body = "".join("<tr>" + "".join(f"<td>{_e(cell)}</td>" for cell in row) + "</tr>"
                       for row in rows[1:])
        self.out.append(f"<table>\n<thead><tr>{head}</tr></thead>\n<tbody>{body}</tbody>\n</table>")
        self.items.append({"kind": "table", "rows": rows, "bold": bold})

    def image(self, image, box):
        self._close_list()
        self.last_line = None
        w, h = (box[2] - box[0]) * PX, (box[3] - box[1]) * PX
        self.out.append(f'<figure><img src="{_data_uri(image)}" width="{round(w)}" '
                        f'height="{round(h)}" alt="Picture on page {self.page.number}"></figure>')
        self.items.append({"kind": "image", "image": image, "width": box[2] - box[0]})

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
