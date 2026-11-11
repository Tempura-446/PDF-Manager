"""Tables in a PDF → an Excel workbook or CSV files (like Acrobat's Export to Excel).

How tables are found, on each page with text:

  * Ruled tables (cell borders drawn as lines or boxes) by pdfplumber's
    line-based detection: the most reliable kind. Tables ruled only between
    the rows get their open left and right sides closed first; a "ruled"
    table whose cells hold stacks of values (columns ruled, rows not) is
    left to the next step.
  * Borderless tables (columns lined up with white space between them) by
    looking for white-space "rivers" that run down several consecutive lines
    of the page's words, outside the ruled tables. Dot leaders count as
    white space. pdfplumber's own text-alignment strategy was tried for this
    and not used: it cuts words in half ("Na|me") and turns ordinary
    paragraphs into "tables".

Cells are cleaned (wrapped lines joined with a space, spaces trimmed, empty
cells kept as ""), and tables with fewer than 2 rows or 2 columns are
dropped. A borderless table needs at least 3 lines, and blocks of text that
read like prose (long cells) are not taken for tables.

A table that continues on the next page is joined to the previous one when
it is the last table on its page and the next one is the first on the next
page, both sit at the page break (the end of the first page, the start of
the next), and their columns are the same in number and position. A header
row repeated on the new page is dropped. Anything else stays a separate
table: two tables of the same shape on consecutive pages that don't meet at
the page break are not joined.

Only text PDFs work: a scanned page is a picture without text and needs OCR
first. Cells spanning several columns come out in their first column.

Coordinates in the results are PDF points with the origin at the bottom left
of the page, before the page's rotation, like the rest of ZPDF.
"""

import csv
import re
import statistics
from decimal import Decimal
from pathlib import Path

from pdfmanager import core, progress
from pdfmanager.pageranges import parse_page_ranges

LAYOUTS = ("sheet-per-table", "one-sheet")

# pdfplumber settings for ruled tables (lines and the edges of boxes).
RULED = {"vertical_strategy": "lines", "horizontal_strategy": "lines",
         "snap_tolerance": 3, "join_tolerance": 3, "intersection_tolerance": 3}

MIN_ALIGNED_LINES = 3      # a borderless table needs this many lines
SEGMENT_GAP = 1.0          # white space between columns, in font sizes
MAX_LINE_GAP = 2.2         # a larger gap between lines (in line heights) ends a table
PROSE_CELL_CHARS = 32      # cells this long on average read like prose, not a table


class NoTablesFound(core.PdfManagerError):
    """The PDF has no tables (or no text at all)."""


# --------------------------------------------------------------------------
# Reading the PDF
# --------------------------------------------------------------------------

def _open(path, password):
    """Open with pdfplumber. ZPDF's own checks first, for the usual messages
    about unreadable files and passwords."""
    import pdfplumber
    from pdfminer.pdfdocument import PDFPasswordIncorrect

    core.open_pdf(path, password)
    # A file with only an owner password opens with an empty password.
    for candidate in dict.fromkeys([password or "", ""]):
        try:
            return pdfplumber.open(str(path), password=candidate)
        except PDFPasswordIncorrect:
            continue
        except Exception as exc:  # pdfminer's parsing errors
            raise core.PdfManagerError(f"{path} can't be read for tables ({exc})") from None
    raise core.PdfManagerError(f"wrong password for {path}")


def _clean(cell):
    """One line of text from a cell: wrapped lines joined with a space (a word
    hyphenated at the end of a line is joined back up)."""
    text = ""
    for line in str(cell or "").splitlines():
        line = " ".join(line.split())
        if not line:
            continue
        if text.endswith("-") and line[:1].islower() and text[-2:-1].isalpha():
            text = text[:-1] + line
        else:
            text = f"{text} {line}" if text else line
    return text


def _tidy(rows):
    """Clean the cells; drop empty rows and columns. None when what's left
    isn't a table (fewer than 2 rows or columns)."""
    rows = [[_clean(c) for c in row] for row in rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        return None
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    keep = [i for i in range(width) if any(row[i] for row in rows)]
    rows = [[row[i] for i in keep] for row in rows]
    if len(rows) < 2 or len(keep) < 2:
        return None
    return rows


def _pdf_bbox(page, box):
    """pdfplumber's (x0, top, x1, bottom) — top-left origin, after the page's
    rotation — as PDF (x0, y0, x1, y1): bottom-left origin, unrotated."""
    mx0, my0, mx1, my1 = (float(v) for v in page.page_obj.mediabox)
    rotation = page.rotation
    points = []
    for u in (box[0], box[2]):
        for top in (box[1], box[3]):
            v = float(page.height) - top
            if rotation == 90:
                points.append((mx1 - v, u + my0))
            elif rotation == 180:
                points.append((mx1 - u, my1 - v))
            elif rotation == 270:
                points.append((v + mx0, my1 - u))
            else:
                points.append((u + mx0, v + my0))
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return tuple(round(v, 2) for v in (min(xs), min(ys), max(xs), max(ys)))


# --------------------------------------------------------------------------
# Borderless tables: columns of words with white space between them
# --------------------------------------------------------------------------

def _lines(words):
    """Group words into lines, top to bottom; each line's words left to right."""
    lines = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        height = word["bottom"] - word["top"]
        for line in reversed(lines[-3:]):
            # same line: the word's middle is within the line's height
            middle = (word["top"] + word["bottom"]) / 2
            if line["top"] - height * 0.1 <= middle <= line["bottom"] + height * 0.1:
                line["words"].append(word)
                line["top"] = min(line["top"], word["top"])
                line["bottom"] = max(line["bottom"], word["bottom"])
                break
        else:
            lines.append({"top": word["top"], "bottom": word["bottom"], "words": [word]})
    for line in lines:
        line["words"].sort(key=lambda w: w["x0"])
        size = statistics.median(w.get("size") or (w["bottom"] - w["top"]) for w in line["words"])
        line["size"] = size
        # Segments: runs of words with less than a column's worth of space between them.
        # Dot leaders ("Total . . . . 12.5") are white space between columns.
        segments, leader = [], False
        for word in line["words"]:
            text = word["text"]
            if _LEADER.match(text):
                leader = True
                continue
            text = _LEADER_END.sub("", text) or text
            if segments and not leader and word["x0"] - segments[-1]["x1"] < size * SEGMENT_GAP:
                segments[-1]["x1"] = max(segments[-1]["x1"], word["x1"])
                segments[-1]["text"] += " " + text
            else:
                segments.append({"x0": word["x0"], "x1": word["x1"], "text": text})
            leader = bool(_LEADER_END.search(word["text"]))
        line["segments"] = segments
    return [line for line in lines if line["segments"]]


_LEADER = re.compile(r"^\s*(?:[.·…_]\s*){4,}$")       # ". . . ." or "......"
_LEADER_END = re.compile(r"(?:\s*[.·…]){4,}\s*$")     # "manslaughter ......."


def _runs(lines):
    """Consecutive lines that may form a table: lines with two or more
    segments, with single-segment lines (wrapped cells, section rows) only
    between them, and no large vertical gap."""
    runs, current, pending = [], [], []
    previous = None
    for line in lines:
        gap_ok = previous is not None and \
            line["top"] - previous["bottom"] <= MAX_LINE_GAP * max(line["size"], previous["size"])
        if current and not gap_ok:
            runs.append(current)
            current, pending = [], []
        if len(line["segments"]) >= 2:
            current.extend(pending)
            current.append(line)
            pending = []
        elif current:
            pending.append(line)
            if len(pending) > 2:  # three plain lines in a row: the table has ended
                runs.append(current)
                current, pending = [], []
        previous = line
    if current:
        runs.append(current)
    # Rows are evenly spaced: a gap much larger than the usual one between
    # lines (e.g. above a page footer) ends the table.
    split = []
    for run in runs:
        gaps = [b["top"] - a["bottom"] for a, b in zip(run, run[1:])]
        usual = statistics.median(gaps) if gaps else 0
        part = [run[0]]
        for line, gap in zip(run[1:], gaps):
            if gap > max(usual * 2, line["size"]):
                split.append(part)
                part = []
            part.append(line)
        split.append(part)
    return [run for run in split if len(run) >= MIN_ALIGNED_LINES]


def _columns(run):
    """Column x-ranges of a run of lines: where the lines' segments are, with
    the white space that runs down all of them as the gaps between columns.
    In longer runs a gap may be crossed by a tenth of the lines (headings
    that span two columns)."""
    multi = [line for line in run if len(line["segments"]) >= 2]
    edges = sorted({round(v, 1) for line in multi for s in line["segments"] for v in (s["x0"], s["x1"])})
    allowed = max(1, len(multi) // 10) if len(multi) >= 6 else 0
    columns = []
    for a, b in zip(edges, edges[1:]):
        middle = (a + b) / 2
        covered = sum(1 for line in multi
                      if any(s["x0"] <= middle <= s["x1"] for s in line["segments"]))
        if covered > allowed:
            if columns and columns[-1][1] == a:
                columns[-1][1] = b
            else:
                columns.append([a, b])
    return columns


def _column_of(segment, columns):
    best, best_overlap = None, 0
    for i, (a, b) in enumerate(columns):
        overlap = min(b, segment["x1"]) - max(a, segment["x0"])
        if overlap > best_overlap:
            best, best_overlap = i, overlap
    if best is None:  # in a gap: the nearest column
        middle = (segment["x0"] + segment["x1"]) / 2
        best = min(range(len(columns)),
                   key=lambda i: min(abs(columns[i][0] - middle), abs(columns[i][1] - middle)))
    return best


def _aligned_table(run):
    """The table in a run of lines, or None when it doesn't look like one."""
    columns = _columns(run)
    if len(columns) < 2:
        return None
    rows, tops = [], []
    for line in run:
        cells = [""] * len(columns)
        for segment in line["segments"]:
            i = _column_of(segment, columns)
            cells[i] = f"{cells[i]} {segment['text']}".strip()
        rows.append(cells)
        tops.append(line["top"])
    # Lines closer together than the rows are wrapped text: an empty first
    # cell joins the line to the row above.
    pitches = [b - a for a, b in zip(tops, tops[1:])]
    pitch = statistics.median(pitches)
    merged = []
    for i, cells in enumerate(rows):
        if merged and not cells[0] and pitches[i - 1] < pitch * 0.85:
            merged[-1] = [f"{a} {b}".strip() for a, b in zip(merged[-1], cells)]
        else:
            merged.append(cells)
    # A heading row of two lines whose one-line cells stand on the lower
    # line: the upper line (its first cell empty) is the start of that row.
    # Only when both lines are in bold and the rows under them are not: a
    # heading row over a first row of figures looks the same otherwise.
    def bold(line):
        return all("bold" in str(w.get("fontname", "")).lower() for w in line["words"])

    if (len(merged) > 2 and len(run) > 2 and not merged[0][0] and merged[1][0] and pitches[0] < pitch * 0.85
            and all(b for a, b in zip(merged[0], merged[1]) if a)
            and bold(run[0]) and bold(run[1]) and not bold(run[2])):
        merged[1] = [f"{a} {b}".strip() for a, b in zip(merged[0], merged[1])]
        del merged[0]
    # Mostly one cell per row isn't a table.
    if sum(1 for cells in merged if sum(1 for c in cells if c) >= 2) < max(2, len(merged) // 2):
        return None
    filled = [c for cells in merged for c in cells if c]
    if sum(len(c) for c in filled) / len(filled) > PROSE_CELL_CHARS:
        return None  # columns of prose
    rows = _tidy(merged)
    if rows is None or len(rows) < 2:
        return None
    words = [w for line in run for w in line["words"]]
    bbox = (min(w["x0"] for w in words), min(w["top"] for w in words),
            max(w["x1"] for w in words), max(w["bottom"] for w in words))
    return {"bbox": bbox, "rows": rows, "columns": [c[0] for c in columns], "kind": "aligned"}


# --------------------------------------------------------------------------
# Finding tables
# --------------------------------------------------------------------------

def _inside(word, box):
    x = (word["x0"] + word["x1"]) / 2
    y = (word["top"] + word["bottom"]) / 2
    return box[0] - 1 <= x <= box[2] + 1 and box[1] - 1 <= y <= box[3] + 1


def _closing_edges(page):
    """Vertical edges at both ends of "ladders" of horizontal rules (three or
    more of the same length, evenly spaced): tables ruled between the rows
    whose left and right sides are open still form closed cells."""
    rules = sorted((e for e in page.edges if e["orientation"] == "h" and e["x1"] - e["x0"] > 30),
                   key=lambda e: (round(e["x0"]), round(e["x1"]), e["top"]))
    groups = []
    for edge in rules:
        group = groups[-1] if groups else None
        if group and abs(group[-1]["x0"] - edge["x0"]) <= 2 and abs(group[-1]["x1"] - edge["x1"]) <= 2:
            if edge["top"] - group[-1]["top"] > 0.5:
                group.append(edge)
        else:
            groups.append([edge])
    closing = []
    for group in groups:
        # split where the gap is much larger than the usual one (a page's
        # header and footer rules aren't table rows)
        gaps = [b["top"] - a["top"] for a, b in zip(group, group[1:])]
        if not gaps:
            continue
        usual = statistics.median(gaps)
        ladder = [group[0]]
        for edge, gap in zip(group[1:], gaps):
            if gap > usual * 3:
                closing += _ladder_sides(ladder)
                ladder = []
            ladder.append(edge)
        closing += _ladder_sides(ladder)
    return closing


def _ladder_sides(ladder):
    if len(ladder) < 3:
        return []
    top, bottom = ladder[0]["top"], ladder[-1]["top"]
    x0 = min(e["x0"] for e in ladder)
    x1 = max(e["x1"] for e in ladder)
    return [{"object_type": "line", "x0": x, "x1": x, "top": top, "bottom": bottom,
             "width": 0, "height": bottom - top} for x in (x0, x1)]


def _stacked(raw_rows):
    """Whether a "ruled" table is really columns without lines between the
    rows: two or more cells in one row each holding a stack of short values
    (many rows run together)."""
    for row in raw_rows:
        stacks = 0
        for cell in row:
            lines = [line for line in str(cell or "").splitlines() if line.strip()]
            if len(lines) >= 3 and statistics.median(len(line) for line in lines) <= 15:
                stacks += 1  # short lines: values, not a paragraph
        if stacks >= 2:
            return True
    return False


def _page_tables(page):
    """Tables on a pdfplumber page, top to bottom (bbox in pdfplumber's
    coordinates)."""
    found = []
    settings = dict(RULED, explicit_vertical_lines=_closing_edges(page))
    for table in page.find_tables(settings):
        raw = table.extract()
        rows = None if _stacked(raw) else _tidy(raw)
        if rows:
            columns = sorted({round(cell[0], 1) for cell in table.cells})
            found.append({"bbox": table.bbox, "rows": rows, "columns": columns, "kind": "ruled"})
    words = [w for w in page.extract_words(extra_attrs=["size", "fontname"])
             if w.get("upright", True) and not any(_inside(w, t["bbox"]) for t in found)]
    for run in _runs(_lines(words)):
        table = _aligned_table(run)
        if table:
            found.append(table)
    found.sort(key=lambda t: (t["bbox"][1], t["bbox"][0]))
    return found


def _continues(prev, table, prev_size, size):
    """Whether ``table`` (first on its page) continues ``prev`` (last on the
    page before): it starts at the top of its page, ``prev`` ends at the
    bottom of its, and the columns are the same."""
    if len(prev["rows"][0]) != len(table["rows"][0]) or len(prev["columns"]) != len(table["columns"]):
        return False
    if prev["bbox"][3] < prev_size[1] * 0.6 or table["bbox"][1] > size[1] * 0.4:
        return False
    tolerance = max(4.0, size[0] * 0.03)
    return all(abs(a - b) <= tolerance for a, b in zip(prev["columns"], table["columns"]))


def _scan(path, password, pages):
    """(tables as find_tables returns them, numbers of the pages searched
    that have no text, number of pages searched)."""
    results, textless = [], []
    with _open(path, password) as pdf:
        total = len(pdf.pages)
        indices = list(dict.fromkeys(parse_page_ranges(pages, total) if pages else range(total)))
        last = None  # (table, page size, page index, result) of the last table on the page before
        for n, index in enumerate(indices):
            progress.report(n, len(indices), "page")
            page = pdf.pages[index]
            size = (float(page.width), float(page.height))
            if not page.chars:
                textless.append(index + 1)
                last = None
                continue
            found = _page_tables(page)
            page.close()  # pdfplumber's cached layout of the page
            for i, table in enumerate(found):
                prev = last if i == 0 and last and last[2] == index - 1 else None
                if prev and _continues(prev[0], table, prev[1], size):
                    rows = table["rows"]
                    target = prev[3]
                    if rows[0] == target["rows"][0]:
                        rows = rows[1:]  # the header, repeated
                    target["rows"].extend(rows)
                    target["pages"].append(index + 1)
                    continue
                results.append({"page": index + 1, "pages": [index + 1],
                                "bbox": _pdf_bbox(page, table["bbox"]), "rows": table["rows"],
                                "kind": table["kind"]})
            if found:
                # the last table on this page, for joining with the next page
                table = found[-1]
                last = (table, size, index, results[-1])
            else:
                last = None
    return results, textless, len(indices)


def find_tables(path, password=None, pages=None):
    """The tables in a PDF: a list of {"page" (1-based), "pages" (all the
    pages a table continued over), "bbox" (x0, y0, x1, y1 in PDF points,
    bottom-left origin, on its first page), "rows" (lists of cell text),
    "kind" ("ruled" or "aligned")}. ``pages`` limits the search, e.g. "2-5"."""
    return _scan(path, password, pages)[0]


def _found_or_explain(path, password, pages):
    """find_tables, or NoTablesFound saying why there are none."""
    tables, textless, searched = _scan(path, password, pages)
    if tables:
        return tables
    ocr = "scanned pages need OCR first (Make searchable)"
    if textless and len(textless) == searched:
        raise NoTablesFound(f"no tables were found; the pages have no text — {ocr}")
    if len(textless) == 1:
        raise NoTablesFound(f"no tables were found; page {textless[0]} has no text — {ocr}")
    if textless:
        listed = ", ".join(map(str, textless[:10])) + (" …" if len(textless) > 10 else "")
        raise NoTablesFound(f"no tables were found; pages {listed} have no text — {ocr}")
    raise NoTablesFound("no tables were found")


def _page_text(table):
    """"3", or "3-4" for a table continued over pages."""
    pages = table["pages"]
    return f"{pages[0]}-{pages[-1]}" if len(pages) > 1 else str(pages[0])


def _numbered(tables):
    """(number on its page, table): tables are numbered from 1 on each page."""
    counts = {}
    for table in tables:
        counts[table["page"]] = counts.get(table["page"], 0) + 1
        yield counts[table["page"]], table


# --------------------------------------------------------------------------
# Numbers
# --------------------------------------------------------------------------

_NUMBER = re.compile(r"^(?P<int>\d{1,3}(?:,\d{3})+|\d+)?(?:\.(?P<frac>\d+))?(?P<pct>%)?$")
_EURO_STYLE = re.compile(r"^(?P<int>\d{1,3}(?:\.\d{3})+),(?P<frac>\d+)(?P<pct>%)?$")  # 1.234,50
_MINUS = "-−–"


def parse_number(text):
    """(value, Excel number format) for text that is clearly a number, else
    None. Understands 1,234.50, (12) and -12, 7.5%, $ € £ before or after
    the number, and 1.234,50. Text with leading zeros (00123, ZIP codes and
    IDs) and very long digit strings stay text."""
    s = text.strip().replace(" ", " ")
    s = re.sub(r"(?<=\d) (?=\.\d+%?$)", "", s)  # "2,799 .2" (a gap in the PDF)
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    if s[:1] in _MINUS or s[:1] == "+":
        negative, s = negative or s[0] in _MINUS, s[1:].strip()
    currency = ""
    if s[-1:] in "$€£" and s[:1] not in "$€£":  # 12 €
        currency, s = s[-1], s[:-1].strip()
    if s[:1] in "$€£":
        currency, s = currency or s[0], s[1:].strip()
        if s[:1] in _MINUS:  # $-12
            negative, s = True, s[1:].strip()
    match = _NUMBER.match(s) or _EURO_STYLE.match(s)
    if not match or not (match["int"] or match["frac"]):
        return None
    whole, frac = match["int"] or "", match["frac"] or ""
    digits = whole.replace(",", "").replace(".", "")
    if len(digits) > 1 and digits.startswith("0"):
        return None  # 00123: a code, not a number
    if len(digits) + len(frac) > 15:
        return None  # longer than Excel keeps exactly
    exact = Decimal(f"{digits or '0'}.{frac or '0'}")
    if match["pct"]:
        exact /= 100
    if negative:
        exact = -exact
    value = int(exact) if exact == exact.to_integral_value() and not frac else float(exact)
    grouped = "," in whole or "." in whole
    fmt = ("#,##0" if grouped else "0") + ("." + "0" * len(frac) if frac else "")
    if match["pct"]:
        fmt += "%"
    if currency:
        fmt = f'"{currency}"{fmt}'
    if text.strip().startswith("("):
        fmt = f"{fmt};({fmt})"
    if fmt == "0":
        fmt = "General"
    return value, fmt


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def _sheet_title(name, taken):
    name = re.sub(r"[\[\]:*?/\\]", "-", name)[:31]
    title, n = name, 2
    while title.lower() in taken:
        suffix = f" ({n})"
        title, n = name[:31 - len(suffix)] + suffix, n + 1
    taken.add(title.lower())
    return title


def _put(sheet, row, column, text, bold=False):
    """Write one cell: a number when the text is clearly one, else the text
    (never as a formula)."""
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Font

    cell = sheet.cell(row=row, column=column)
    number = parse_number(text) if text else None
    if number:
        cell.value, cell.number_format = number
    elif text:
        cell.value = ILLEGAL_CHARACTERS_RE.sub("", text)
        cell.data_type = "s"  # "=..." from a PDF stays text
    if bold:
        cell.font = Font(bold=True)
    return cell


def _fit_columns(sheet, widths):
    from openpyxl.utils import get_column_letter

    for i, width in widths.items():
        sheet.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 8), 60)


def _write_rows(sheet, rows, start, widths):
    """Rows from ``start``, the first one bold (the header). Returns the next free row."""
    from openpyxl.styles import Alignment

    for r, cells in enumerate(rows):
        for c, text in enumerate(cells, 1):
            cell = _put(sheet, start + r, c, text, bold=r == 0)
            if len(text) > 58:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            widths[c] = max(widths.get(c, 0), min(len(text), 58))
    return start + len(rows)


def _title(number, table):
    """"Page 3, table 1" ("Pages 3-4, table 1" for one continued over pages)."""
    pages = _page_text(table)
    return f"Page{'s' if '-' in pages else ''} {pages}, table {number}"


def to_excel(path, output, password=None, pages=None, mode="sheet-per-table"):
    """Write the PDF's tables to an Excel workbook (.xlsx): a Summary sheet,
    then a sheet per table ("p3 table 1") or, with mode "one-sheet", all
    tables on one sheet under a title row each. The first row of each table
    is its header (bold; frozen on its own sheet). Returns {"output",
    "tables", "sheets"}. Raises NoTablesFound (a PdfManagerError) when the
    PDF has no tables."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.worksheet.hyperlink import Hyperlink

    if mode not in LAYOUTS:
        raise core.PdfManagerError(f"layout must be one of {', '.join(LAYOUTS)}")
    output = Path(output)
    if output.is_dir():
        raise core.PdfManagerError(f"{output} is a folder; give the name of the workbook to write")
    tables = list(_numbered(_found_or_explain(path, password, pages)))

    book = Workbook()
    summary = book.active
    summary.title = "Summary"
    taken = {"summary"}
    _put(summary, 1, 1, f"Tables found in {Path(path).name}").font = Font(bold=True, size=13)
    for c, text in enumerate(["Sheet", "Page", "Table", "Rows", "Columns"], 1):
        _put(summary, 3, c, text, bold=True)
    sheets = []
    if mode == "one-sheet":
        sheet = book.create_sheet(_sheet_title("Tables", taken))
        sheets.append(sheet.title)
        row, widths = 1, {}
    for n, (number, table) in enumerate(tables):
        rows = table["rows"]
        if mode == "one-sheet":
            _put(sheet, row, 1, _title(number, table)).font = Font(bold=True, size=12)
            start = row
            row = _write_rows(sheet, rows, row + 1, widths) + 1
        else:
            sheet = book.create_sheet(_sheet_title(f"p{_page_text(table)} table {number}", taken))
            sheets.append(sheet.title)
            start, widths = 1, {}
            _write_rows(sheet, rows, 1, widths)
            sheet.freeze_panes = "A2"
            _fit_columns(sheet, widths)
        line = 4 + n
        link = _put(summary, line, 1, sheet.title)
        link.hyperlink = Hyperlink(ref=link.coordinate, location=f"'{sheet.title}'!A{start}")
        link.style = "Hyperlink"
        _put(summary, line, 2, _page_text(table))
        for c, value in enumerate([number, len(rows), len(rows[0])], 3):
            summary.cell(row=line, column=c, value=value)
    if mode == "one-sheet":
        _fit_columns(sheet, widths)
    summary.freeze_panes = "A4"
    _fit_columns(summary, {1: max(len(s) for s in ["Sheet", *sheets]), 2: 6, 3: 5, 4: 4, 5: 7})
    output.parent.mkdir(parents=True, exist_ok=True)
    book.save(output)
    return {"output": output, "tables": len(tables), "sheets": sheets}


def _csv_safe(text):
    """Text that Excel won't run as a formula when it opens the CSV file."""
    if text[:1] in ("=", "+", "-", "@", "\t", "\r") and parse_number(text) is None:
        return "'" + text
    return text


def to_csv(path, outdir, password=None, pages=None):
    """Write each table to its own CSV file in ``outdir`` (UTF-8 with a BOM, so
    Excel reads accents and symbols right), named <name>_p3_table1.csv.
    Returns {"files", "tables"}. Raises NoTablesFound when there are none."""
    tables = list(_numbered(_found_or_explain(path, password, pages)))
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    files = []
    for number, table in tables:
        target = outdir / f"{Path(path).stem}_p{_page_text(table)}_table{number}.csv"
        with open(target, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerows([[_csv_safe(c) for c in row] for row in table["rows"]])
        files.append(target)
    return {"files": files, "tables": len(files)}
