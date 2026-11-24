# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""PDF/UA accessibility checker, structure-tree audit, and text reporting."""

import re
from collections import Counter
from pathlib import Path

from pdfmanager import core, progress
from pdfmanager.accessibility.geometry import (
    BOOKMARK_PAGES, LANG_RE, MANUAL_NOTE, RULES, _PATH_PAINT, _SHOW,
    _area, _inherited, _num, _union,
)
from pdfmanager.accessibility.tagger import (
    PLACEHOLDER_TITLES, _annotations, _is_tagged, _open_pdfium, _open_pikepdf, _title_of,
)



# --------------------------------------------------------------------------
# Checking
# --------------------------------------------------------------------------

def _plural(n, word, plural=None):
    return f"{n} {word if n == 1 else (plural or word + 's')}"


class _Found:
    """Problems found, per rule and kind, with the pages they're on."""

    def __init__(self):
        self.items = {}

    def add(self, rule, kind, page=None, count=1):
        item = self.items.setdefault((rule, kind), {"rule": rule, "message": kind,
                                                    "fix": RULES[rule], "count": 0,
                                                    "pages": set()})
        item["count"] += count
        if page:
            item["pages"].add(page)

    def list(self):
        order = list(RULES)
        return [{**i, "pages": sorted(i["pages"])}
                for i in sorted(self.items.values(), key=lambda i: order.index(i["rule"]))]


def _kids(elem):
    import pikepdf

    k = elem.get("/K")
    if k is None:
        return []
    return list(k) if isinstance(k, pikepdf.Array) else [k]


def _is_elem(obj):
    import pikepdf

    return isinstance(obj, pikepdf.Dictionary) and "/S" in obj and \
        str(obj.get("/Type", "/StructElem")) == "/StructElem"


def _attributes(elem):
    import pikepdf

    found = {}
    attrs = elem.get("/A")
    if attrs is None:
        return found
    for a in (list(attrs) if isinstance(attrs, pikepdf.Array) else [attrs]):
        if isinstance(a, pikepdf.Dictionary):
            for key, value in a.items():
                found[key] = value
    return found


class _TreeFacts:
    """What the structure tree says, read once: figures without alternate
    text, tables, headings, lists, and the annotations it tags."""

    def __init__(self, pdf, page_numbers):
        self.page_numbers = page_numbers
        self.figures = []          # pages of figures without alternate text
        self.tables_no_header = []
        self.tables_irregular = []
        self.headings = []         # (level, page) in document order
        self.lists = []            # pages of badly built lists
        self.link_objrs, self.form_objrs, self.objrs = set(), set(), set()
        tree = pdf.Root.StructTreeRoot
        roles = tree.get("/RoleMap")
        self.roles = {str(k): str(v) for k, v in roles.items()} if roles is not None else {}
        self.walk(tree)

    def std(self, name):
        name = str(name)
        for _ in range(10):
            if name not in self.roles or self.roles[name] == name:
                break
            name = self.roles[name]
        return name.lstrip("/")

    def page_of(self, elem, inherited):
        pg = elem.get("/Pg")
        if pg is not None:
            return self.page_numbers.get(pg.objgen, inherited)
        return inherited

    def walk(self, tree):
        import pikepdf

        seen = set()
        stack = [(k, None, None) for k in reversed(_kids(tree))]
        while stack:
            elem, page, parent = stack.pop()
            if not _is_elem(elem):
                continue
            key = elem.objgen if elem.is_indirect else id(elem)
            if key in seen or len(seen) > 500000:
                continue
            seen.add(key)
            S = self.std(elem.S)
            page = self.page_of(elem, page)
            kids = _kids(elem)
            kid_types = []
            for kid in kids:
                if _is_elem(kid):
                    kid_types.append(self.std(kid.S))
                elif isinstance(kid, pikepdf.Dictionary) and str(kid.get("/Type", "")) == "/OBJR":
                    target = kid.get("/Obj")
                    if target is not None and target.is_indirect:
                        self.objrs.add(target.objgen)
                        if S == "Link":
                            self.link_objrs.add(target.objgen)
                        elif S == "Form":
                            self.form_objrs.add(target.objgen)
                    if page is None and kid.get("/Pg") is not None:
                        page = self.page_numbers.get(kid.Pg.objgen)
            if S == "Figure":
                alt = str(elem.get("/Alt") or "").strip()
                actual = str(elem.get("/ActualText") or "").strip()
                if not alt and not actual:
                    self.figures.append(page)
            elif S == "Table":
                self.table(elem, page)
            elif re.fullmatch(r"H[1-6]", S):
                self.headings.append((int(S[1]), page))
            elif S == "L":
                if any(t not in ("LI", "L", "Caption", "Artifact", "NonStruct") for t in kid_types):
                    self.lists.append(page)
            elif S == "LI":
                if parent != "L" or any(t not in ("Lbl", "LBody") for t in kid_types):
                    self.lists.append(page)
            elif S in ("Lbl", "LBody") and parent != "LI":
                self.lists.append(page)
            for kid in reversed(kids):
                if _is_elem(kid):
                    stack.append((kid, page, S))

    def table(self, elem, page):
        rows, has_header = [], False
        groups = [elem]
        while groups:
            node = groups.pop(0)
            for kid in _kids(node):
                if not _is_elem(kid):
                    continue
                S = self.std(kid.S)
                if S == "TR":
                    rows.append(kid)
                elif S in ("THead", "TBody", "TFoot"):
                    groups.append(kid)
        widths, taken = [], {}
        for r, row in enumerate(rows):
            col = 0
            for kid in _kids(row):
                if not _is_elem(kid):
                    continue
                S = self.std(kid.S)
                if S not in ("TH", "TD"):
                    continue
                has_header = has_header or S == "TH"
                while taken.get((r, col)):
                    col += 1
                attrs = _attributes(kid)
                colspan = max(1, int(_num(attrs.get("/ColSpan", 1), 1)))
                rowspan = max(1, int(_num(attrs.get("/RowSpan", 1), 1)))
                for dr in range(rowspan):
                    for dc in range(colspan):
                        taken[(r + dr, col + dc)] = True
                col += colspan
            widths.append(max([c + 1 for (rr, c) in taken if rr == r], default=0))
        if not has_header:
            self.tables_no_header.append(page)
        if len(set(w for w in widths if w)) > 1:
            self.tables_irregular.append(page)


def _number_tree(node, out, depth=0):
    import pikepdf

    if not isinstance(node, pikepdf.Dictionary) or depth > 30:
        return out
    nums = node.get("/Nums")
    if isinstance(nums, pikepdf.Array):
        items = list(nums)
        for k in range(0, len(items) - 1, 2):
            try:
                out[int(items[k])] = items[k + 1]
            except (TypeError, ValueError):
                continue
    for kid in node.get("/Kids") or []:
        _number_tree(kid, out, depth + 1)
    return out


_PAINT = _SHOW | _PATH_PAINT | {"Do", "sh"}


def _content_marks(stream, resources, depth=0, seen=None):
    """(number of drawing operations outside marked content that tags them
    or marks them as artifacts, MCIDs found) for a page or form."""
    import pikepdf

    seen = set() if seen is None else seen
    try:
        instructions = pikepdf.parse_content_stream(stream)
    except (pikepdf.PdfError, ValueError, TypeError):
        return 0, set()
    props = resources.get("/Properties") if isinstance(resources, pikepdf.Dictionary) else None
    stack, untagged, mcids = [], 0, set()
    for ins in instructions:
        covered = "real" in stack or "artifact" in stack
        if isinstance(ins, pikepdf.ContentStreamInlineImage):
            untagged += 0 if covered else 1
            continue
        op = str(ins.operator)
        if op == "BMC":
            tag = str(ins.operands[0]) if ins.operands else ""
            stack.append("artifact" if tag == "/Artifact" else None)
        elif op == "BDC":
            tag = str(ins.operands[0]) if ins.operands else ""
            p = ins.operands[1] if len(ins.operands) > 1 else None
            if isinstance(p, pikepdf.Name) and props is not None:
                p = props.get(str(p))
            if tag == "/Artifact":
                stack.append("artifact")
            elif isinstance(p, pikepdf.Dictionary) and "/MCID" in p:
                stack.append("real")
                try:
                    mcids.add(int(p.MCID))
                except (TypeError, ValueError):
                    pass
            else:
                stack.append(None)
        elif op == "EMC":
            if stack:
                stack.pop()
        elif op in _PAINT and not covered:
            if op == "Do" and ins.operands:
                try:
                    xobj = resources.XObject[ins.operands[0]]
                except (AttributeError, KeyError, TypeError, ValueError):
                    xobj = None
                if xobj is not None and str(xobj.get("/Subtype", "")) == "/Form":
                    key = xobj.objgen if xobj.is_indirect else id(xobj)
                    if depth < 6 and key not in seen:
                        seen.add(key)
                        inner, _ = _content_marks(xobj, xobj.get("/Resources") or resources,
                                                  depth + 1, seen)
                        untagged += inner
                    continue
            untagged += 1
    return untagged, mcids


def _font_unmapped(font):
    """Whether a font has no way to say which characters it draws."""
    import pikepdf

    try:
        if "/ToUnicode" in font:
            return False
        subtype = str(font.get("/Subtype", ""))
        if subtype == "/Type0":
            encoding = str(font.get("/Encoding", ""))
            if encoding.startswith("/Uni"):
                return False
            info = font.DescendantFonts[0].get("/CIDSystemInfo")
            ordering = str(info.get("/Ordering", "")) if info is not None else ""
            return ordering not in ("Japan1", "GB1", "CNS1", "Korea1", "KR")
        encoding = font.get("/Encoding")
        if isinstance(encoding, pikepdf.Dictionary):
            names = [str(d) for d in encoding.get("/Differences", []) if isinstance(d, pikepdf.Name)]
            odd = [n for n in names
                   if re.fullmatch(r"/(?:g|G|glyph|index|cid|c|C|a)\d+|/[A-Z]{1,2}\d{1,3}", n)]
            if names and len(odd) > len(names) / 2:
                return True
        if subtype == "/Type3":
            return encoding is None
        if subtype == "/TrueType" and encoding is None:
            descriptor = font.get("/FontDescriptor")
            flags = int(_num(descriptor.get("/Flags", 0))) if descriptor is not None else 0
            return bool(flags & 4)  # symbolic: the codes are glyph positions
    except (AttributeError, IndexError, TypeError, ValueError, pikepdf.PdfError):
        return False
    return False


def _page_fonts(resources, depth=0, seen=None):
    """(name, font) of the fonts a page or form uses, with those of its forms."""
    import pikepdf

    seen = set() if seen is None else seen
    out = []
    if not isinstance(resources, pikepdf.Dictionary) or depth > 5:
        return out
    try:
        for name, font in (resources.get("/Font") or {}).items():
            if isinstance(font, pikepdf.Dictionary):
                out.append((str(font.get("/BaseFont", name)).lstrip("/").split("+")[-1], font))
        for _, xobj in (resources.get("/XObject") or {}).items():
            if isinstance(xobj, pikepdf.Stream) and str(xobj.get("/Subtype", "")) == "/Form":
                key = xobj.objgen if xobj.is_indirect else id(xobj)
                if key not in seen:
                    seen.add(key)
                    out += _page_fonts(xobj.get("/Resources"), depth + 1, seen)
    except (AttributeError, pikepdf.PdfError):
        pass
    return out


def _field_has_tooltip(widget):
    node, depth = widget, 0
    while node is not None and depth < 20:
        if str(node.get("/TU") or "").strip():
            return True
        node = node.get("/Parent")
        depth += 1
    return False


def _text_pages(path, password, count):
    """Per page: (characters, characters without a Unicode mapping, share of
    the page covered by pictures when it has no text), from pdfium."""
    import pypdfium2.raw as raw

    out = []
    has_error_check = hasattr(raw, "FPDFText_HasUnicodeMapError")
    with core.PDFIUM_LOCK:
        doc = _open_pdfium(path, password)
        try:
            for i in range(min(len(doc), count)):
                page = doc[i]
                textpage = page.get_textpage()
                chars = errors = 0
                for k in range(textpage.count_chars()):
                    if raw.FPDFText_IsGenerated(textpage, k) == 1:
                        continue
                    chars += 1
                    if has_error_check and raw.FPDFText_HasUnicodeMapError(textpage, k) == 1:
                        errors += 1
                covered = 0.0
                if chars == 0:
                    w, h = page.get_size()
                    boxes = []
                    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=3):
                        try:
                            boxes.append(obj.get_bounds())
                        except Exception:  # a picture pdfium can't place
                            continue
                    if boxes:
                        covered = min(1.0, _area(_union(boxes)) / max(w * h, 1))
                out.append((chars, errors, covered))
                textpage.close()
                page.close()
        finally:
            doc.close()
    return out, has_error_check


def check(path, password=None):
    """Check a PDF for what keeps it from being accessible (see RULES).
    Returns {"file", "pages", "tagged", "lang", "title", "passed",
    "issues": [{"rule", "message", "fix", "count", "pages"}], "note"}."""
    import pikepdf

    path = Path(path)
    core.open_pdf(path, password)  # the usual messages about unreadable files and passwords
    found = _Found()
    pdf = _open_pikepdf(path, password)
    with pdf:
        pages = list(pdf.pages)
        total = len(pages)
        numbers = {p.obj.objgen: i + 1 for i, p in enumerate(pages)}
        root = pdf.Root
        tagged = _is_tagged(pdf)
        mark = root.get("/MarkInfo")
        marked = bool(mark is not None and mark.get("/Marked", False))
        lang = str(root.get("/Lang") or "").strip()
        title = _title_of(pdf)
        prefs = root.get("/ViewerPreferences")
        shown = bool(prefs is not None and prefs.get("/DisplayDocTitle", False))

        if pdf.is_encrypted:
            try:
                blocked = not pdf.allow.accessibility
            except AttributeError:
                blocked = False
            if blocked:
                found.add("security", "security")
        if not tagged:
            found.add("tagged", "untagged")
        elif not marked:
            found.add("tagged", "unmarked")
        if not lang:
            found.add("lang", "lang")
        if not title:
            found.add("title", "title")
        if not shown:
            found.add("title-shown", "title-shown")

        facts = None
        parent_tree = {}
        if tagged:
            try:
                facts = _TreeFacts(pdf, numbers)
                parent_tree = _number_tree(root.StructTreeRoot.get("/ParentTree"), {})
            except (pikepdf.PdfError, AttributeError, TypeError, ValueError, RecursionError):
                found.add("structure", "unreadable")
                facts = None

        unmapped_fonts = {}
        for i, page in enumerate(pages):
            progress.report(i, total, "checking page")
            number = i + 1
            obj = page.obj
            resources = _inherited(obj, "/Resources")
            annots = [a for _, a in _annotations(obj)
                      if str(a.get("/Subtype", "")) not in ("/Popup", "/PrinterMark", "/TrapNet")
                      and not int(_num(a.get("/F", 0))) & 2]
            if annots and str(obj.get("/Tabs", "")) != "/S":
                found.add("tabs", "tabs", number)
            for annot in annots:
                subtype = str(annot.get("/Subtype", ""))
                key = annot.objgen if annot.is_indirect else None
                if subtype == "/Widget":
                    if not _field_has_tooltip(annot):
                        found.add("forms", "tooltip", number)
                    if facts is not None and key not in facts.form_objrs:
                        found.add("forms", "untagged", number)
                elif subtype == "/Link":
                    if facts is not None:
                        if key not in facts.link_objrs:
                            found.add("links", "untagged", number)
                        if not str(annot.get("/Contents") or "").strip():
                            found.add("links", "contents", number)
                elif facts is not None and key not in facts.objrs:
                    found.add("annotations", "untagged", number)
            for name, font in _page_fonts(resources):
                if _font_unmapped(font):
                    unmapped_fonts.setdefault(name, set()).add(number)
            if tagged:
                untagged, mcids = _content_marks(page, resources)
                if untagged:
                    found.add("content", "content", number, untagged)
                if mcids:
                    sp = obj.get("/StructParents")
                    entry = parent_tree.get(int(sp)) if sp is not None else None
                    ok = isinstance(entry, pikepdf.Array) and all(
                        m < len(entry) and isinstance(entry[m], pikepdf.Dictionary) for m in mcids)
                    if not ok:
                        found.add("structure", "structure", number)

        text, has_error_check = _text_pages(path, password, total)
        for i, (chars, errors, covered) in enumerate(text):
            if chars == 0 and covered >= 0.3:
                found.add("scanned", "scanned", i + 1)
            if errors and errors >= max(1, chars * 0.01):
                found.add("unicode", "unicode", i + 1)
        if not has_error_check:
            for where in unmapped_fonts.values():
                for number in where:
                    found.add("unicode", "unicode", number)
        unicode_pages = {p for item in found.items.values() if item["rule"] == "unicode"
                         for p in item["pages"]}
        fonts = sorted(name for name, where in unmapped_fonts.items() if where & unicode_pages)

        if facts is not None:
            for page in facts.figures:
                found.add("figures", "figures", page)
            for page in facts.tables_no_header:
                found.add("tables", "no-header", page)
            for page in facts.tables_irregular:
                found.add("tables", "irregular", page)
            previous = 0
            for level, page in facts.headings:
                if previous == 0 and level != 1:
                    found.add("headings", "first", page)
                elif level > previous + 1:
                    found.add("headings", "skip", page)
                previous = level
            for page in facts.lists:
                found.add("lists", "lists", page)
        outline = root.get("/Outlines")
        if total > BOOKMARK_PAGES and (outline is None or outline.get("/First") is None):
            found.add("bookmarks", "bookmarks")

    issues = [_worded(item, fonts, total) for item in found.list()]
    return {"file": str(path), "pages": total, "tagged": tagged, "lang": lang, "title": title,
            "passed": not issues, "issues": issues, "note": MANUAL_NOTE}


def _worded(item, fonts, total):
    """An issue with its message put into words, from what was counted."""
    n, pages = item["count"], item["pages"]
    on = _plural(len(pages), "page")
    are = "is" if n == 1 else "are"
    have = "has" if n == 1 else "have"
    messages = {
        "security": "The security settings stop screen readers from reading the text.",
        "untagged": "The document isn't tagged: screen readers can't tell its headings, "
                    "paragraphs, lists, tables and pictures apart, or the order they're read in.",
        "unmarked": "The document has tags but doesn't say so (MarkInfo), so some readers "
                    "ignore them.",
        "lang": "The document's language isn't set, so screen readers may read it with the "
                "wrong voice.",
        "title": "The document has no title.",
        "title-shown": "The window shows the file name instead of the document's title "
                       "(DisplayDocTitle is off).",
        "tabs": "On pages with links, fields or comments, the Tab key doesn't follow the "
                "document's structure.",
        "tooltip": f"{_plural(n, 'form field')} {have} no tooltip: the name a screen reader "
                   "reads out for the field.",
        "contents": f"{_plural(n, 'link')} {have} no description for screen readers (Contents).",
        "content": f"Some content on {on} is neither tagged nor marked as decoration "
                   f"(artifact), so screen readers skip it or read it out of order "
                   f"({_plural(n, 'drawing operation')}).",
        "structure": f"The tags don't match the page content on {on} (the structure tree is "
                     "damaged).",
        "unreadable": "The structure tree can't be read (it's damaged).",
        "scanned": f"{_plural(len(pages), 'page is a scanned picture', 'pages are scanned pictures')}"
                   " with no text: there's nothing for a screen reader to read.",
        "unicode": "Some text can't be turned into characters (its font has no Unicode "
                   "mapping), so screen readers read it as gibberish"
                   + (f" (fonts: {', '.join(fonts[:5])})" if fonts else "") + ".",
        "figures": f"{_plural(n, 'figure')} {have} no alternate text: a description read out "
                   "instead of the picture.",
        "no-header": f"{_plural(n, 'table')} {have} no header cells (TH), so the columns have "
                     "no names.",
        "irregular": f"{_plural(n, 'table')} {have} rows with different numbers of cells.",
        "first": "The first heading isn't a level 1 heading (H1).",
        "skip": f"Headings skip a level {_plural(n, 'time')} (e.g. H1 followed by H3).",
        "lists": f"{_plural(n, 'list')} {are}n't built right: a list (L) holds items (LI), each "
                 "with a label (Lbl) and a body (LBody).",
        "bookmarks": f"The document has {total} pages but no bookmarks to find your way around.",
    }
    if item["message"] == "untagged" and item["rule"] != "tagged":
        thing = {"forms": "form field", "links": "link",
                 "annotations": "comment or other annotation"}[item["rule"]]
        plural = {"annotations": "comments or other annotations"}.get(item["rule"])
        what = {"forms": " (as a Form element)", "links": " (as a Link element holding the link)",
                "annotations": ""}[item["rule"]]
        message = f"{_plural(n, thing, plural)} {are}n't tagged{what}."
    else:
        message = messages.get(item["message"], item["message"])
    return {**item, "message": message}


def summary(result):
    """One line: "no accessibility problems found" or "3 accessibility
    problems (...)"."""
    if result["passed"]:
        return "no accessibility problems found"
    n = len(result["issues"])
    first = result["issues"][0]["message"].split(":")[0].rstrip(".")
    return f"{n} accessibility problem{'s' if n != 1 else ''} ({first}…)"


def report(result, name):
    """A plain-text report."""
    lines = [f"Accessibility check: {name}", ""]
    lines.append(f"Pages: {result['pages']}")
    lines.append(f"Tagged: {'Yes' if result['tagged'] else 'No'}")
    lines.append(f"Language: {result['lang'] or '(not set)'}")
    lines.append(f"Title: {result['title'] or '(none)'}")
    n = len(result["issues"])
    lines.append(f"Result: {'no problems found' if result['passed'] else _plural(n, 'problem')}")
    if result["issues"]:
        lines += ["", "Problems:"]
        for item in result["issues"]:
            where = ""
            if item["pages"]:
                shown = ", ".join(str(p) for p in item["pages"][:10])
                where = f" (page{'s' if len(item['pages']) > 1 else ''} {shown}" \
                        f"{'…' if len(item['pages']) > 10 else ''})"
            lines.append(f"  - {item['message']}{where}")
            lines.append(f"    How to fix: {item['fix']}")
    if result.get("note"):
        lines += ["", result["note"]]
    return "\n".join(lines) + "\n"
