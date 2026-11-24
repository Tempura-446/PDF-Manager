# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Structure tree construction, per-page autotagging, and PDF writing."""

import re
from collections import Counter
from io import BytesIO
from pathlib import Path

from pdfmanager import core, progress
from pdfmanager.accessibility.geometry import (
    ARTIFACT, ARTIFACT_BACKGROUND, ARTIFACT_FOOTER, ARTIFACT_HEADER, UNMARKED,
    BOOKMARK_PAGES, CAPTION, DEFAULT_LANG, LANG_RE, NUMBERED_HEADING, PAGE_NUMBER, TABLE_CAPTION,
    _View, _area, _bbox, _distance, _inherited, _num, _overlap, _union,
)
from pdfmanager.accessibility.layout import (
    _Block, _Item, _Line, _Seg, _aligned_grid, _body_size, _lines,
    _mark_page_furniture, _may_have_tables, _page_segments, _plumber_tables,
    _reading_order, _ruled_grid, _text_blocks, _text_direction, guess_language,
)
from pdfmanager.accessibility.scanner import (
    _Scanner, _form_has_images, _rewrite, _single_use_forms, _strip_marks, _strip_xobjects,
)



# --------------------------------------------------------------------------
# The structure, as Python objects first
# --------------------------------------------------------------------------

class _Elem:
    """A structure element before it's written: type, page, kids (other
    elements, ("mcid", n) and ("objr", annotation)), attributes."""

    def __init__(self, S, page=None, inline=False):
        self.S, self.page, self.kids = S, page, []
        self.inline = inline     # kids sorted in content order (MCIDs and Links inside text)
        self.alt = None
        self.attrs = None
        self.style = None        # heading candidate: (size, bold)
        self.depth = None        # heading numbering depth ("1.2" -> 2)
        self.text = ""
        self.ref = None

    def add(self, kid):
        self.kids.append(kid)
        return kid




class _Targets:
    """Boxes of the elements that hold a page's content, to find the one
    a drawing operator belongs to; boxes are bucketed by height."""

    BAND = 24.0

    def __init__(self):
        self.items = []    # (box, key)
        self.bands = {}

    def add(self, box, key):
        if box is None or box[2] <= box[0] or box[3] <= box[1]:
            return
        index = len(self.items)
        self.items.append((box, key))
        for band in range(int(box[1] // self.BAND), int(box[3] // self.BAND) + 1):
            self.bands.setdefault(band, []).append(index)

    def best(self, box):
        """The key whose boxes overlap ``box`` most; else the nearest one."""
        if not self.items:
            return None
        scores = {}
        if box[3] >= box[1]:
            for band in range(int(box[1] // self.BAND), int(box[3] // self.BAND) + 1):
                for index in self.bands.get(band, ()):
                    tbox, key = self.items[index]
                    area = _overlap(box, tbox)
                    if area > 0:
                        scores[id(key)] = (scores.get(id(key), (0, key))[0] + area, key)
        if scores:
            return max(scores.values(), key=lambda v: v[0])[1]
        tbox, key = min(self.items, key=lambda item: _distance(box, item[0]))
        return key


# --------------------------------------------------------------------------
# Tagging one page
# --------------------------------------------------------------------------

def _annotations(page_obj):
    import pikepdf

    annots = page_obj.get("/Annots")
    if not isinstance(annots, pikepdf.Array):
        return []
    found = []
    for i, annot in enumerate(annots):
        if not isinstance(annot, pikepdf.Dictionary):
            continue
        found.append((i, annot))
    return found


def _annot_box(annot, view):
    try:
        rect = [float(v) for v in annot.Rect]
        return view.rect((min(rect[0], rect[2]), min(rect[1], rect[3]),
                          max(rect[0], rect[2]), max(rect[1], rect[3])))
    except (AttributeError, TypeError, ValueError, IndexError):
        return None


def _figures(units, segs, size, view):
    """Pictures on the page: figures, decoration and backgrounds. Pictures
    that touch are one figure. Returns [{"box", "units", "segs", "kind"}]
    with kind "figure", "decoration" or "background"."""
    w, h = size
    page_box = (0, 0, w, h)
    found = []
    for unit in units:
        if unit.kind not in ("image", "form-image") or unit.box is None:
            continue
        box = view.rect(unit.box)
        if unit.kind == "form-image" and any(
                s.role is None and _overlap(s.box, box) > 0.5 * _area(s.box) for s in segs):
            unit.kind = "form"  # a form with text in it (e.g. a whole page): not a picture
            continue
        clipped = (max(box[0], 0), max(box[1], 0), min(box[2], w), min(box[3], h))
        if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
            found.append({"box": box, "units": [unit], "segs": [], "kind": "decoration"})
            continue
        for fig in found:
            if fig["kind"] == "figure" and _distance(fig["box"], clipped) <= 2:
                fig["box"] = _union([fig["box"], clipped])
                fig["units"].append(unit)
                break
        else:
            found.append({"box": clipped, "units": [unit], "segs": [], "kind": "figure"})
    for fig in found:
        if fig["kind"] != "figure":
            continue
        box = fig["box"]
        if box[2] - box[0] < 16 or box[3] - box[1] < 16:
            fig["kind"] = "decoration"
            continue
        inside = [s for s in segs if s.role is None and _overlap(s.box, box) > 0.6 * _area(s.box)]
        words = sum(len(s.text.split()) for s in inside)
        text_area = sum(_area(s.box) for s in inside)
        if _area(box) >= 0.5 * _area(page_box) and inside:
            fig["kind"] = "background"
        elif words > 30 or text_area > 0.3 * _area(box):
            fig["kind"] = "background"
        else:
            fig["segs"] = inside
    return found


class _PageTagger:
    """Works out, for one page, the elements in reading order and which of
    them each drawing operator belongs to."""

    def __init__(self, number, page, view, segs, units, tables_found, body):
        self.number, self.page, self.view = number, page, view
        self.segs, self.units, self.tables = segs, units, tables_found
        self.body = body
        self.size = view.size
        self.targets = _Targets()
        self.elements = []        # top-level, in reading order
        self.figures = []         # figure elements, in reading order
        self.fallback = None
        self.links = []           # (index, annotation, box) of links
        self.link_texts = {}      # index -> the text under the link
        self.link_elems = {}      # index -> (annotation, Link element)
        self.annot_elems = []     # (index, annotation, Form or Annot element)

    def run(self, annots):
        figures = _figures(self.units, self.segs, self.size, self.view)
        absorbed = {id(s) for f in figures for s in f["segs"]}
        tables_kept = []
        for table in self.tables:
            if table["rows"]:
                table["grid"] = _ruled_grid(table["rows"])
            else:
                table["grid"], used = _aligned_grid(table["box"], self.segs)
                if used:
                    table["box"] = _union(s.box for s in used)
            if table["grid"]:
                tables_kept.append(table)
        self.tables = tables_kept
        table_boxes = [t["box"] for t in self.tables]
        items = []
        for seg in self.segs:
            if seg.role in ("header", "footer"):
                key = ARTIFACT_HEADER if seg.role == "header" else ARTIFACT_FOOTER
                self.targets.add(seg.box, key)
                continue
            if id(seg) in absorbed:
                continue
            if any(_overlap(seg.box, t) > 0.5 * _area(seg.box) for t in table_boxes):
                continue
            items.append(_Item("seg", seg.box, seg, seg.size))
        for table in self.tables:
            items.append(_Item("table", table["box"], table, self.body))
        for fig in figures:
            if fig["kind"] == "figure":
                items.append(_Item("figure", fig["box"], fig, self.body))
        ordered = _reading_order(items, self.body)
        self._build(ordered, figures)
        self._place_annotations(annots)
        self._assign()
        return self

    # -- elements in reading order ------------------------------------------

    def _build(self, ordered, figures):
        run = []
        blocks = []   # ("text", _Block) / ("table", t) / ("figure", f)

        def flush():
            if run:
                for block in _text_blocks(_lines(run), self.body):
                    blocks.append(("text", block))
                run.clear()

        for item, path in ordered:
            if item.kind == "seg":
                run.append((item.obj, path))
            else:
                flush()
                blocks.append((item.kind, item.obj))
        flush()
        captions = self._captions(blocks)
        lists = []    # the open lists (see _list_item)
        for i, (kind, obj) in enumerate(blocks):
            if kind != "text" or obj.kind != "li":
                lists = []
            if kind == "text":
                if obj.kind == "li":
                    self._list_item(obj, lists)
                    continue
                elem = _Elem("Caption" if i in captions else "P", self.number, inline=True)
                elem.text = obj.text
                if i not in captions:
                    elem.style = _heading_style(obj, self.body)
                    m = NUMBERED_HEADING.match(elem.text)
                    if m and elem.style:
                        elem.depth = m.group(1).count(".") + 1
                for line in obj.lines:
                    self.targets.add((line.x0, line.y0, line.x1, line.y1), elem)
                self.elements.append(elem)
            elif kind == "table":
                self.elements.append(self._table(obj))
            elif kind == "figure":
                elem = _Elem("Figure", self.number, inline=True)
                elem.attrs = {"BBox": obj["box"]}
                caption = captions.get(("figure", i))
                if caption is not None:
                    elem.alt = blocks[caption][1].text
                self.targets.add(obj["box"], elem)
                for unit in obj["units"]:
                    unit.key = elem
                self.figures.append(elem)
                self.elements.append(elem)
        for fig in figures:
            key = ARTIFACT_BACKGROUND if fig["kind"] == "background" else ARTIFACT
            if fig["kind"] != "figure":
                for unit in fig["units"]:
                    unit.key = key

    def _captions(self, blocks):
        """Text blocks that caption a figure or table next to them:
        {block index: True, ("figure", figure index): caption index}."""
        found = {}
        for i, (kind, obj) in enumerate(blocks):
            if kind not in ("figure", "table"):
                continue
            box = obj["box"]
            pattern = TABLE_CAPTION if kind == "table" else CAPTION
            for j in (i + 1, i - 1, i + 2, i - 2):
                if not 0 <= j < len(blocks) or j in found:
                    continue
                k, block = blocks[j]
                if k != "text" or block.kind != "p" or len(block.text) > 400:
                    continue
                bb = block.box
                near = _distance(bb, box) <= 3 * max(block.size, 8)
                if near and pattern.match(block.text):
                    found[j] = True
                    found[(kind, i)] = j
                    break
        return found

    def _list_item(self, block, lists):
        """Add a list item: to the open list at its indent, a new list inside
        the last item when it's indented further, or a new list. ``lists``:
        the open lists, outermost first: (L element, marker kind, label x,
        text x)."""
        size = block.size
        kind = block.marker[0]
        numbering = {"ListNumbering": "Decimal" if kind == "number" else "Disc"}
        while lists and block.label_x < lists[-1][2] - 0.5 * size:
            lists.pop()
        if lists and block.label_x >= lists[-1][3] - 0.5 * size and lists[-1][0].kids:
            # deeper than the open list's text: a list inside its last item
            sub = _Elem("L", self.number)
            sub.attrs = numbering
            lists[-1][0].kids[-1].kids[-1].add(sub)
            lists.append((sub, kind, block.label_x, block.text_x))
        elif not lists or abs(block.label_x - lists[-1][2]) > 0.5 * size or lists[-1][1] != kind:
            top = _Elem("L", self.number)
            top.attrs = numbering
            if len(lists) > 1:   # a list next to the previous one at the same depth
                lists.pop()
                lists[-1][0].kids[-1].kids[-1].add(top)
                lists.append((top, kind, block.label_x, block.text_x))
            else:
                self.elements.append(top)
                lists[:] = [(top, kind, block.label_x, block.text_x)]
        li = lists[-1][0].add(_Elem("LI", self.number))
        marker_seg, chars = block.marker[1], block.marker[2]
        first = block.lines[0].segs[0]
        split = None
        if marker_seg is None and 0 < chars <= len(first.rights):
            split = first.rights[chars - 1]
        if marker_seg is not None or split is not None:
            lbl = li.add(_Elem("Lbl", self.number, inline=True))
            self.targets.add(marker_seg.box if marker_seg is not None else
                             (first.x0, first.y0, split, first.y1), lbl)
        body = li.add(_Elem("LBody", self.number, inline=True))
        body.text = block.text
        for line in block.lines:
            for seg in line.segs:
                if seg is marker_seg:
                    continue
                if seg is first and split is not None:
                    self.targets.add((split, seg.y0, seg.x1, seg.y1), body)
                else:
                    self.targets.add(seg.box, body)

    def _table(self, table):
        elem = _Elem("Table", self.number)
        elem.attrs = {"BBox": table["box"]}
        grid = table["grid"]
        for r, row in enumerate(grid):
            tr = elem.add(_Elem("TR", self.number))
            for cell in row:
                td = tr.add(_Elem("TH" if r == 0 else "TD", self.number, inline=True))
                attrs = {}
                if r == 0:
                    attrs["Scope"] = "Column"
                if cell["colspan"] > 1:
                    attrs["ColSpan"] = cell["colspan"]
                if cell["rowspan"] > 1:
                    attrs["RowSpan"] = cell["rowspan"]
                td.attrs = attrs or None
                if cell["box"] is not None:
                    self.targets.add(cell["box"], td)
        return elem

    # -- annotations ----------------------------------------------------------

    def _place_annotations(self, annots):
        boxes = []
        for elem in self.elements:
            box = self._elem_box(elem)
            boxes.append(box)
        for index, annot in annots:
            subtype = str(annot.get("/Subtype", ""))
            flags = int(_num(annot.get("/F", 0)))
            if subtype in ("/Popup", "/PrinterMark", "/TrapNet") or flags & (2 | 32):
                continue
            box = _annot_box(annot, self.view)
            if box is None:
                continue
            if subtype == "/Link":
                self.links.append((index, annot, box))
                continue
            elem = _Elem("Form" if subtype == "/Widget" else "Annot", self.number)
            elem.add(("objr", index))
            self.annot_elems.append((index, annot, elem))
            # after the element it overlaps or sits nearest to
            at = len(self.elements)
            if boxes:
                scores = [(_overlap(box, b) if b else 0, -_distance(box, b) if b else -1e9)
                          for b in boxes]
                at = max(range(len(boxes)), key=lambda k: scores[k]) + 1
            self.elements.insert(at, elem)
            boxes.insert(at, box)

    def _elem_box(self, elem):
        boxes = [box for box, key in self.targets.items if key is elem]
        if boxes:
            return _union(boxes)
        for kid in elem.kids:
            if isinstance(kid, _Elem):
                box = self._elem_box(kid)
                if box:
                    return box
        return None

    # -- operators -> elements ----------------------------------------------

    def _assign(self):
        w, h = self.size
        view = self.view
        texts = []
        for unit in self.units:
            if unit.key is not None:
                continue
            if unit.kind == "text":
                texts.append(unit)
                if unit.blank:
                    continue
                unit.key = self.targets.best(view.rect(unit.box)) or self._fallback()
            elif unit.kind in ("path", "shading", "image", "form-image"):
                unit.key = ARTIFACT
                if unit.kind == "path" and unit.box is not None:
                    box = view.rect(unit.box)
                    for fig in self.figures:
                        fbox = fig.attrs["BBox"]
                        if _overlap(box, fbox) >= 0.9 * max(_area(box), 0.01) and _area(box) > 0:
                            unit.key = fig
                            break
            elif unit.kind == "tagged-form":
                unit.key = UNMARKED
            elif unit.kind == "form":
                box = view.rect(unit.box) if unit.box else (0, 0, w, h)
                key = self.targets.best(box)
                has_text = any(_overlap(seg.box, box) > 0 for seg in self.segs)
                unit.key = key if (key is not None and has_text) else ARTIFACT
        # blank text joins its neighbour in the same text object
        for k, unit in enumerate(texts):
            if unit.key is not None:
                continue
            near = [u for u in texts[max(0, k - 1):k] + texts[k + 1:k + 2] if u.key is not None
                    and u.in_text == unit.in_text]
            unit.key = near[0].key if near else ARTIFACT
        self._links(texts)

    def _fallback(self):
        if self.fallback is None:
            self.fallback = _Elem("P", self.number, inline=True)
            self.elements.append(self.fallback)
        return self.fallback

    def _links(self, texts):
        hosts = {}
        for index, annot, box in self.links:
            inside = [u for u in texts if u.key is not None and isinstance(u.key, _Elem)
                      and u.key.S != "Link" and _area(self.view.rect(u.box)) > 0
                      and _overlap(self.view.rect(u.box), _grow(box, 1))
                      >= 0.5 * _area(self.view.rect(u.box))]
            link = _Elem("Link", self.number, inline=True)
            link.text = self.link_texts.get(index, "")
            if inside:
                host = Counter(id(u.key) for u in inside).most_common(1)[0][0]
                host = next(u.key for u in inside if id(u.key) == host)
                for unit in inside:
                    if unit.key is host:
                        unit.key = link
            else:
                host = self.targets.best(box)
                if not isinstance(host, _Elem):
                    host = None
            link.add(("objr", index))
            if host is None:
                self.elements.append(link)
            else:
                host.add(link)
            hosts[index] = (annot, link)
        self.link_elems = hosts


def _grow(box, by):
    return (box[0] - by, box[1] - by, box[2] + by, box[3] + by)


def _heading_style(block, body):
    """(size, bold) when a block looks like a heading, else None."""
    text = block.text
    if len(text) > 200 or len(block.lines) > 3 or not re.search(r"[^\W\d_]", text):
        return None
    if PAGE_NUMBER.match(text):
        return None
    size = round(block.size * 2) / 2
    if block.size >= body * 1.12:
        return (size, block.bold)
    if block.bold and len(block.lines) <= 2 and len(text) <= 120 and block.size >= body * 0.9 \
            and not text.endswith((".", ",", ";")):
        return (size, True)
    return None


def _heading_levels(elements):
    """Turn heading candidates into H1-H6: bigger (and bolder) first,
    numbering ("1.2") wins, and no level is skipped."""
    styles = sorted({e.style for e in elements if e.style}, key=lambda s: (-s[0], not s[1]))
    rank = {style: i + 1 for i, style in enumerate(styles)}
    previous = 0
    for elem in elements:
        if not elem.style:
            continue
        level = elem.depth or rank[elem.style]
        level = max(1, min(level, previous + 1, 6))
        elem.S = f"H{level}"
        previous = level


def _walk(elements):
    for elem in elements:
        yield elem
        yield from _walk([k for k in elem.kids if isinstance(k, _Elem)])



# --------------------------------------------------------------------------
# Opening
# --------------------------------------------------------------------------

def _open_pikepdf(path, password, data=None):
    import pikepdf

    source = BytesIO(data) if data is not None else str(path)
    for candidate in dict.fromkeys([password or "", ""]):
        try:
            if isinstance(source, BytesIO):
                source.seek(0)
            return pikepdf.open(source, password=candidate)
        except pikepdf.PasswordError:
            continue
        except pikepdf.PdfError as exc:
            raise core.PdfManagerError(f"{path} can't be read ({exc})") from None
    if password:
        raise core.PdfManagerError(f"wrong password for {path}")
    raise core.PdfManagerError(f"{path} is encrypted; supply a password")


def _open_pdfium(path, password):
    import pypdfium2 as pdfium

    last = None
    for candidate in dict.fromkeys([password or None, None]):
        try:
            return pdfium.PdfDocument(str(path), password=candidate)
        except pdfium.PdfiumError as exc:
            last = exc
    raise core.PdfManagerError(f"{path} can't be read ({last})")


def _is_tagged(pdf):
    root = pdf.Root
    tree = root.get("/StructTreeRoot")
    return tree is not None and tree.get("/K") is not None


PLACEHOLDER_TITLES = {"", "(anonymous)", "anonymous", "untitled", "(untitled)", "title",
                      "document", "untitled document"}


def _title_of(pdf):
    """The title in the document's information or XMP metadata (not
    placeholders such as "untitled")."""
    title = ""
    try:
        info = pdf.docinfo
        if "/Title" in info:
            title = str(info.Title).strip()
    except Exception:
        title = ""
    if title.lower() in PLACEHOLDER_TITLES:
        title = ""
    if not title:
        try:
            meta = pdf.open_metadata()
            title = (meta.get("dc:title") or "").strip()
        except Exception:
            title = ""
    return "" if title.lower() in PLACEHOLDER_TITLES else title


def _set_title(pdf, title):
    import pikepdf

    pdf.docinfo["/Title"] = pikepdf.String(title)
    try:
        with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
            meta["dc:title"] = title
    except Exception:  # damaged XMP: the information dictionary still has it
        pass


def _finish_document(pdf, lang, title):
    """What a tagged document needs besides its tags: Marked, language,
    title shown in the window, tab order following the structure."""
    import pikepdf

    root = pdf.Root
    root.MarkInfo = pikepdf.Dictionary(Marked=True)
    root.Lang = pikepdf.String(lang)
    prefs = root.get("/ViewerPreferences")
    if not isinstance(prefs, pikepdf.Dictionary):
        prefs = pikepdf.Dictionary()
        root.ViewerPreferences = prefs
    prefs.DisplayDocTitle = True
    if title:
        _set_title(pdf, title)
    for page in pdf.pages:
        page.obj.Tabs = pikepdf.Name.S


def _parse_alt(alt):
    """{page: [texts]} with int pages, from the caller's or JSON's form."""
    out = {}
    for page, texts in (alt or {}).items():
        try:
            number = int(page)
        except (TypeError, ValueError):
            raise core.PdfManagerError(f"alt text: {page!r} isn't a page number") from None
        if isinstance(texts, str):
            texts = [texts]
        out[number] = [str(t) if t is not None else "" for t in texts]
    return out


# --------------------------------------------------------------------------
# Tagging
# --------------------------------------------------------------------------

def autotag(path, output, password=None, lang=None, title=None, alt=None, force=False):
    """Write a tagged copy of ``path`` to ``output`` (see the module
    docstring). ``lang``: a language code such as "en" or "de-CH" (None or
    "auto": guessed from the text); ``title``: the document's title (else
    the one it has, else its first heading); ``alt``: descriptions of the
    pictures, {page number: [text of the 1st figure, 2nd, ...]};
    ``force``: replace tags the file already has. The copy isn't encrypted.

    Returns {"pages", "tagged" (False when existing tags were kept),
    "elements": {type: count}, "lang", "title", "figures_without_alt":
    [page numbers], "untagged_pages": [pages whose content couldn't be read]}."""
    path, output = Path(path), Path(output)
    if lang and str(lang).strip().lower() not in ("", "auto"):
        lang = str(lang).strip()
        if not LANG_RE.match(lang):
            raise core.PdfManagerError(f"{lang!r} isn't a language code; use one like en, de, "
                                       "fr or pt-BR")
    else:
        lang = None
    alt = _parse_alt(alt)
    core.open_pdf(path, password)  # the usual messages about unreadable files and passwords
    data = path.read_bytes()
    pdf = _open_pikepdf(path, password, data)
    with pdf:
        result = {"file": str(path), "pages": len(pdf.pages), "tagged": True, "elements": {},
                  "figures_without_alt": [], "untagged_pages": []}
        if _is_tagged(pdf) and not force:
            lang = lang or str(pdf.Root.get("/Lang") or "").strip() \
                or guess_language(_sample_text(path, password, 30))
            title = title or _title_of(pdf) or path.stem
            _finish_document(pdf, lang, title)
            result.update(tagged=False, lang=lang, title=title)
            _save(pdf, output)
            return result
        _clear_tags(pdf)
        elements, pages_info, sample = _tag_pages(pdf, path, password, alt, result)
        lang = lang or str(pdf.Root.get("/Lang") or "").strip() or guess_language(sample)
        title = (title or _title_of(pdf) or next((e.text for e in _walk(elements)
                                                  if e.S == "H1" and e.text), "")
                 or path.stem)
        elements = _write_tree(pdf, elements, pages_info)
        _finish_document(pdf, lang, title[:300])
        counts = Counter(e.S for e in _walk(elements))
        result.update(lang=lang, title=title[:300], elements=dict(counts),
                      bookmarks=_add_bookmarks(pdf, elements))
        _save(pdf, output)
    return result


def _add_bookmarks(pdf, elements):
    """Bookmarks from the headings (H1-H3) of a long document that has none,
    as accessible documents should. Returns how many were added."""
    import pikepdf

    if len(pdf.pages) <= BOOKMARK_PAGES:
        return 0
    heads = [(int(e.S[1]), " ".join(e.text.split())[:200], e.page) for e in _walk(elements)
             if re.fullmatch(r"H[1-3]", e.S) and e.text.strip() and e.page is not None]
    if len(heads) < 2:
        return 0
    try:
        with pdf.open_outline() as outline:
            if outline.root:
                return 0
            stack = [(0, outline.root)]
            for level, title, page in heads:
                item = pikepdf.OutlineItem(title, page)
                while stack[-1][0] >= level:
                    stack.pop()
                stack[-1][1].append(item)
                stack.append((level, item.children))
    except (pikepdf.PdfError, pikepdf.OutlineStructureError):
        return 0
    return len(heads)


def _save(pdf, output):
    output = Path(output)
    if output.is_dir():
        raise core.PdfManagerError(f"{output} is a folder; give the name of the PDF to write")
    output.parent.mkdir(parents=True, exist_ok=True)
    buffer = BytesIO()
    pdf.save(buffer, min_version="1.7", encryption=False)
    output.write_bytes(buffer.getvalue())


def _sample_text(path, password, pages):
    with core.PDFIUM_LOCK:
        doc = _open_pdfium(path, password)
        try:
            parts = []
            for i in range(min(len(doc), pages)):
                textpage = doc[i].get_textpage()
                parts.append(textpage.get_text_range())
            return " ".join(parts)
        finally:
            doc.close()


def _clear_tags(pdf):
    """Take away the tags a file has (for ``force``), and the markings
    that only tagging uses, so new tags don't mix with old ones."""
    root = pdf.Root
    for key in ("/StructTreeRoot", "/MarkInfo"):
        if key in root:
            del root[key]
    for page in pdf.pages:
        obj = page.obj
        if "/StructParents" in obj:
            del obj["/StructParents"]
        for _, annot in _annotations(obj):
            if "/StructParent" in annot:
                del annot["/StructParent"]


def _step(done, pages, what):
    """Progress: reading the pages, then tagging them (two passes)."""
    progress.report(fraction_in_part=done / (2 * max(pages, 1)))
    progress.say(f"{what} page {done % max(pages, 1) + 1} of {pages}")


def _tag_pages(pdf, path, password, alt, result):
    """Read every page's layout and tag it. Returns (top-level elements in
    reading order, per-page writing info, text sample for the language)."""
    import pikepdf

    pages = []
    with core.PDFIUM_LOCK:
        doc = _open_pdfium(path, password)
        try:
            for i, page in enumerate(pdf.pages):
                _step(i, len(pdf.pages), "reading")
                obj = page.obj
                pdfium_page = doc[i]
                textpage = pdfium_page.get_textpage()
                turn = _text_direction(textpage)
                if turn is None:
                    turn = int(_num(_inherited(obj, "/Rotate"), 0))
                view = _View(_inherited(obj, "/MediaBox") or [0, 0, 612, 792], turn)
                links = {}
                for index, annot in _annotations(obj):
                    if str(annot.get("/Subtype", "")) == "/Link":
                        try:
                            r = [float(v) for v in annot.Rect]
                            text = textpage.get_text_bounded(min(r[0], r[2]), min(r[1], r[3]),
                                                             max(r[0], r[2]), max(r[1], r[3]))
                            links[index] = " ".join(text.split())
                        except Exception:  # a damaged rectangle: described by its address
                            pass
                pages.append({"view": view, "size": view.size, "links": links,
                              "segs": _page_segments(textpage, view)})
                textpage.close()
                pdfium_page.close()
        finally:
            doc.close()
    _mark_page_furniture(pages)
    body = _body_size(pages)
    sample = " ".join(s.text for p in pages[:30] for s in p["segs"] if s.role is None)

    from pdfmanager import tables

    fonts, forms = {}, {}
    inside = _single_use_forms(pdf)
    stripped = set(inside)  # their old marks go when they're tagged inside
    elements, infos = [], []
    try:
        plumber = tables._open(path, password)
    except core.PdfManagerError:
        plumber = None
    try:
        for i, page in enumerate(pdf.pages):
            _step(len(pdf.pages) + i, len(pdf.pages), "tagging")
            info = pages[i]
            obj = page.obj
            resources = _inherited(obj, "/Resources") or pikepdf.Dictionary()
            try:
                instructions = pikepdf.parse_content_stream(page)
            except (pikepdf.PdfError, ValueError, TypeError):
                result["untagged_pages"].append(i + 1)
                infos.append(None)
                continue
            units = _Scanner(resources, fonts, forms, inside).run(instructions)
            found = []
            if plumber is not None and _may_have_tables(info["segs"], units):
                try:
                    ppage = plumber.pages[i]
                    found = _plumber_tables(ppage, info["view"])
                    ppage.close()
                except Exception:  # pdfplumber trips on some damaged pages
                    found = []
            tagger = _PageTagger(i, page, info["view"], info["segs"], units, found, body)
            tagger.link_texts = info["links"]
            tagger.run(_annotations(obj))
            texts = alt.get(i + 1, [])
            for n, fig in enumerate(tagger.figures):
                if n < len(texts) and texts[n]:
                    fig.alt = texts[n]
                if not fig.alt:
                    result["figures_without_alt"].append(i + 1)
            elements.extend(tagger.elements)
            infos.append({"units": units, "tagger": tagger})
            _strip_xobjects((resources.get("/XObject") or {}), stripped)
            for unit in units:
                if unit.kind == "tagged-form":  # the forms these draw are tagged as a whole
                    _strip_xobjects((unit.xobj.get("/Resources") or {}).get("/XObject") or {},
                                    stripped)
    finally:
        if plumber is not None:
            tables.close(plumber)
    _heading_levels([e for e in _walk(elements) if e.S == "P"])
    pages_info = {"infos": infos}
    return elements, pages_info, sample


def _write_tree(pdf, elements, pages_info):
    """Rewrite the pages' content with marked content and build the
    structure tree, ParentTree and /StructParent(s) entries. Returns the
    top-level elements that hold something."""
    import pikepdf

    Name, Dictionary, Array = pikepdf.Name, pikepdf.Dictionary, pikepdf.Array
    parent_tree = []          # [(key, [element of each MCID] or ("annot", element))]
    next_key = 0
    drawn = [0]               # the order content is drawn in, for ordering the kids
    for i, page in enumerate(pdf.pages):
        info = pages_info["infos"][i]
        if info is None:
            continue
        instructions = pikepdf.parse_content_stream(page)
        count = [0]    # MCIDs are numbered across the page and the forms tagged inside
        owners = {}    # stream (None: the page) -> {MCID: element}

        def opener(stream, count=count, owners=owners):
            mine = owners.setdefault(stream.objgen if stream is not None else None, {})

            def open_mc(key):
                if isinstance(key, _Elem):
                    mcid = count[0]
                    count[0] += 1
                    drawn[0] += 1
                    mine[mcid] = key
                    key.add(("mcid", mcid, drawn[0], stream))
                    return pikepdf.ContentStreamInstruction(
                        [Name("/" + key.S), Dictionary(MCID=mcid)], pikepdf.Operator("BDC"))
                if key[1] in ("Header", "Footer"):
                    props = Dictionary(Type=Name.Pagination, Subtype=Name("/" + key[1]))
                    return pikepdf.ContentStreamInstruction([Name.Artifact, props],
                                                            pikepdf.Operator("BDC"))
                if key[1] == "Background":
                    return pikepdf.ContentStreamInstruction(
                        [Name.Artifact, Dictionary(Type=Name.Background)], pikepdf.Operator("BDC"))
                return pikepdf.ContentStreamInstruction([Name.Artifact], pikepdf.Operator("BMC"))
            return open_mc

        inner = {}
        for unit in info["units"]:
            if unit.stream is not None:
                inner.setdefault(unit.stream.objgen, []).append(unit)
        forms = []

        def on_form(unit, inner=inner, opener=opener, forms=forms):
            form = unit.xobj
            new = _rewrite(pikepdf.parse_content_stream(form), inner.get(form.objgen, []),
                           opener(form))
            form.write(pikepdf.unparse_content_stream(new))
            forms.append(form)

        # (written now that headings have their levels: the tag names H1...)
        new = _rewrite(instructions, [u for u in info["units"] if u.stream is None],
                       opener(None), on_form)
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(new))
        # The page's entry also names what its forms hold (their MCIDs don't
        # clash with its own): pdf.js finds a page's tags from that entry only.
        owners["page"] = {m: e for found in owners.values() for m, e in found.items()}
        for stream, key in [(page.obj, "page")] + [(form, form.objgen) for form in forms]:
            if "/StructParents" in stream:
                del stream["/StructParents"]
            mine = owners.get(key)
            if mine:
                stream.StructParents = next_key
                parent_tree.append((next_key, [mine.get(m) for m in range(max(mine) + 1)]))
                next_key += 1
        tagger = info["tagger"]
        annots = page.obj.get("/Annots")
        made = [(index, elem) for index, _, elem in tagger.annot_elems] + \
            [(index, link) for index, (_, link) in tagger.link_elems.items()]
        for index, elem in made:
            annot = annots[index]
            if not annot.is_indirect:
                annot = pdf.make_indirect(annot)
                annots[index] = annot
            annot.StructParent = next_key
            if str(annot.get("/Subtype", "")) == "/Link" and not str(annot.get("/Contents", "")):
                annot.Contents = pikepdf.String(_link_text(elem, annot))
            parent_tree.append((next_key, ("annot", elem)))
            elem.kids = [("objr-ref", annot) if isinstance(k, tuple) and k[0] == "objr" else k
                         for k in elem.kids]
            next_key += 1

    root_elem = _Elem("Document")
    root_elem.kids = list(elements)
    tree = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    pages = list(pdf.pages)

    def build(elem, parent):
        d = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + elem.S), P=parent))
        elem.ref = d
        if elem.page is not None:
            d.Pg = pages[elem.page].obj
        if elem.alt:
            d.Alt = pikepdf.String(elem.alt)
        attrs = elem.attrs or {}
        layout = {}
        table = {}
        listing = {}
        for k, v in attrs.items():
            if k == "BBox":
                layout["BBox"] = _unview(pages_info, elem.page, v)
            elif k in ("Scope", "ColSpan", "RowSpan"):
                table[k] = v
            elif k == "ListNumbering":
                listing[k] = v
        attr_dicts = []
        if layout and layout["BBox"] is not None:
            attr_dicts.append(Dictionary(O=Name.Layout, BBox=Array(layout["BBox"])))
        if table:
            t = Dictionary(O=Name.Table)
            for k, v in table.items():
                t[Name("/" + k)] = Name("/" + v) if isinstance(v, str) else v
            attr_dicts.append(t)
        if listing:
            attr_dicts.append(Dictionary(O=Name.List, ListNumbering=Name("/" + listing["ListNumbering"])))
        if attr_dicts:
            d.A = attr_dicts[0] if len(attr_dicts) == 1 else Array(attr_dicts)
        kids = elem.kids
        if elem.inline:
            def order(kid):
                if isinstance(kid, tuple) and kid[0] == "mcid":
                    return (0, kid[2])
                if isinstance(kid, _Elem):
                    first = _first_drawn(kid)
                    return (0, first + 0.5) if first is not None else (1, 0)
                return (1, 0)
            kids = sorted(kids, key=order)
        out = Array()
        for kid in kids:
            if isinstance(kid, _Elem):
                out.append(build(kid, d))
            elif kid[0] == "mcid" and kid[3] is None:
                out.append(kid[1])
            elif kid[0] == "mcid":  # in a form XObject drawn on the page
                out.append(Dictionary(Type=Name.MCR, Pg=pages[elem.page].obj, Stm=kid[3],
                                      MCID=kid[1]))
            elif kid[0] == "objr-ref":
                out.append(Dictionary(Type=Name.OBJR, Obj=kid[1], Pg=pages[elem.page].obj))
        d.K = out
        return d

    # drop elements that ended up with nothing in them
    root_elem.kids = _prune(root_elem.kids)
    doc = build(root_elem, tree)
    tree.K = doc
    nums = Array()
    for key, value in parent_tree:
        nums.append(key)
        if isinstance(value, list):
            nums.append(Array([elem.ref if elem is not None and elem.ref is not None else None
                               for elem in value]))
        else:
            nums.append(value[1].ref if value[1].ref is not None else doc)
    tree.ParentTree = pdf.make_indirect(Dictionary(Nums=nums))
    tree.ParentTreeNextKey = next_key
    pdf.Root.StructTreeRoot = tree
    return root_elem.kids


def _first_drawn(elem):
    """When the first content of an element (or its kids) is drawn."""
    found = [k[2] for k in elem.kids if isinstance(k, tuple) and k[0] == "mcid"]
    for kid in elem.kids:
        if isinstance(kid, _Elem):
            sub = _first_drawn(kid)
            if sub is not None:
                found.append(sub)
    return min(found) if found else None


def _prune(kids):
    """Elements without content are left out (cells and annotations stay)."""
    out = []
    for kid in kids:
        if not isinstance(kid, _Elem):
            out.append(kid)
            continue
        kid.kids = _prune(kid.kids)
        if kid.kids or kid.S in ("TD", "TH", "TR", "Table"):
            out.append(kid)
    return out


def _unview(pages_info, page_index, box):
    """A box in seen coordinates back in the page's own."""
    info = pages_info["infos"][page_index] if page_index is not None else None
    if info is None:
        return None
    view = info["tagger"].view
    bx0, by0, bx1, by1 = view.box
    x0, y0, x1, y1 = box
    corners = []
    for vx, vy in ((x0, y0), (x1, y1)):
        if view.rotation == 90:
            corners.append((bx1 - vy, vx + by0))
        elif view.rotation == 180:
            corners.append((bx1 - vx, by1 - vy))
        elif view.rotation == 270:
            corners.append((vy + bx0, by1 - vx))
        else:
            corners.append((vx + bx0, vy + by0))
    return [round(v, 2) for v in _bbox(corners)]


def _link_text(elem, annot):
    """A description for a link: its text, else where it goes."""
    action = annot.get("/A")
    uri = ""
    try:
        if action is not None and "/URI" in action:
            uri = str(action.URI)
    except Exception:
        uri = ""
    return (elem.text or uri or "Link")[:300]


def tag_summary(result):
    """One line on what ``autotag`` did."""
    if not result.get("tagged"):
        return (f"already tagged: kept its tags; language {result['lang']}"
                + (f", title {result['title']!r}" if result.get("title") else ""))
    counts = result.get("elements", {})
    heads = sum(n for s, n in counts.items() if re.fullmatch(r"H\d", s))
    parts = []
    for n, word in ((heads, "heading"), (counts.get("P", 0), "paragraph"),
                    (counts.get("L", 0), "list"), (counts.get("Table", 0), "table"),
                    (counts.get("Figure", 0), "figure"), (counts.get("Link", 0), "link"),
                    (counts.get("Form", 0), "form field")):
        if n:
            parts.append(f"{n} {word}{'s' if n != 1 else ''}")
    line = "tagged " + (", ".join(parts) if parts else "the pages") + f"; language {result['lang']}"
    missing = result.get("figures_without_alt") or []
    if missing:
        pages = sorted(set(missing))
        line += (f"; {len(missing)} figure{'s' if len(missing) != 1 else ''} without a "
                 f"description (page{'s' if len(pages) != 1 else ''} "
                 f"{', '.join(map(str, pages[:10]))})")
    if result.get("bookmarks"):
        line += f"; added {result['bookmarks']} bookmarks"
    if result.get("untagged_pages"):
        line += f"; pages not tagged (unreadable content): {', '.join(map(str, result['untagged_pages']))}"
    return line
