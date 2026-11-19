# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Page-level in-place and fallback text rewriting, verification, and content-stream drawing."""

import ctypes
import difflib
import math
import os
import re
import statistics
import tempfile
import unicodedata
from pathlib import Path

from pypdf import Transformation
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject

from pdfmanager import core, fonts
from .extract import (
    ALIGNS, BASE14_NAMES, FIT_LIMIT, INSTALLED, LOOKS, MARKUPS, MOVED, PAD, READER_FONTS, SAME, SIMILAR, STANDARD, _CORE, _Covers, _OwnFont, _Unsure, _across, _advance, _attr_look, _background, _box, _can_draw, _char_styles, _chars, _choose, _default_style, _down, _draws_space, _embedded, _face, _fit, _font_advance, _font_data, _font_handle, _has_letter, _is_embedded, _is_pdfa, _letters, _link_at, _look, _metrics, _names_of, _nearest_char, _nonspace, _old_words, _options, _origin, _own_font, _page_fonts, _parse, _place, _private, _raw, _restyle, _rows, _rows_with_marks, _rtl, _same_colour, _set_rises, _similar, _skip, _space_fonts, _spacing, _style, _take, _text_of, _unit, _widths,
)
from .reflow_ops import (
    _BIDI_MIRROR, _BULLET_RE, _KINSOKU_NO_END, _KINSOKU_NO_START, _alignment, _bidi_visual, _cjk_tokens, _detect_indent, _divisions, _free_span, _has_cjk, _is_cjk, _leading_unchanged, _para_spacing, _pieces_of, _split_seq, _strip_seq, _wrap, _wrap_cjk, _wrap_marked, _wrap_seq, _wrap_seq_marked,
)

class _Page:
    """One page being changed: what pdfium reads of it and what gets drawn."""

    def __init__(self, pdf_page, page, doc=None, number=0, writer=None, pdfa=False, old_way=False):
        self.old_way = old_way  # every change covered and drawn on top, none made in the drawing
        self.touched = []       # boxes around the words the changes were about
        self.bands = []         # the lines changed: (x, y of a point on it, its direction, its height)
        self.texts = []         # (line's place in bands or None, new words): to be found on the page once written
        self.strict = True      # only lines were changed: nothing new may show up anywhere else
        self.pdf_page, self.page = pdf_page, page
        self.doc, self.number, self.writer, self.pdfa = doc, number, writer, pdfa
        self.textpage = pdf_page.get_textpage()
        self.image = pdf_page.render(scale=1).to_pil().convert("RGB")
        self.cache = {}
        self.hidden = _Covers(pdf_page)
        self.covers = []  # (box, colour)
        self.notes = {}   # for the report on the change being made: wider, taller, moved, ...
        self.runs = []    # {style, own, name, entries: [(letter, x, y)]}
        self._flow, self._faces = None, {}
        self._segs = None
        self.spot = None    # where the change being made writes its new words in the drawing
        self.spots = None   # ... line by line, for a paragraph: [(place, the old line's first letter)]
        self.line_no = None  # the line of the paragraph being written
        self.legacy = False  # the change being made is done the old way (covered and drawn on top)
        self.spills = []    # [(page_index, _Page_or_None, runs_for_new_page_or_None)] for cross-page overflow

    @property
    def flow(self):
        """The page's text as lines, for moving it (see textflow), or None."""
        if self._flow is None and self.writer is not None:
            from pdfmanager import textflow

            box = self.page.cropbox
            self._flow = textflow.Flow(self.page, self.writer, self.textpage, self.hidden,
                                       (float(box.left), float(box.bottom), float(box.right), float(box.top)))
        return self._flow

    def cover(self, box):
        self.covers.append((box, _background(self.pdf_page, self.image, box)))
        self.notes["covered"] = True  # the old words are still in the file, under the box

    # -- changing the page's own drawing ---------------------------------------------------
    def start(self):
        """Before each change."""
        self.notes, self.spot, self.legacy = {}, None, self.old_way
        self.spots, self.line_no, self.written_text = None, None, None
        self._first_run = len(self.runs)

    def touch(self, letters):
        # The line the letters are on: a point on it, its direction, its height.
        style = max((L["style"] for L in letters), key=lambda st: st["size"])
        self.bands.append((*letters[0]["origin"], *_unit(letters[0]["style"]["matrix"]), style["size"]))
        boxes = [L["loose"] for L in letters]
        self.touched.append((min(b[0] for b in boxes) - 1, min(b[1] for b in boxes) - 1,
                             max(b[2] for b in boxes) + 1, max(b[3] for b in boxes) + 1))

    def expected(self):
        """What the page must read as once written: {"letters": the
        letters no change was about, each where it should now be, in its
        font and size [(letter, x, y, font, size)] (those on lines moved by
        a paragraph that grew or shrank where they moved to), "bands": the
        lines that were changed, "strict": whether nothing else may have
        appeared off those lines (only lines were changed), "texts":
        [(which line or None, the new words)] to find again}."""
        raw = _raw()
        buf, flags = ctypes.create_string_buffer(256), ctypes.c_int()
        moved = self._flow.moved_letters if self._flow is not None else {}
        out = []
        for i in range(self.textpage.count_chars()):
            loose = self.textpage.get_charbox(i, loose=True)
            ch = self.textpage.get_text_range(i, 1)
            if loose[2] <= loose[0] or loose[3] <= loose[1] or not ch or ch.isspace():
                continue
            x, y = _origin(self.textpage, i)
            cx, cy = (loose[0] + loose[2]) / 2, (loose[1] + loose[3]) / 2
            if any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in self.touched) \
                    or self.hidden.hides(self.textpage, i, loose):
                continue
            raw.FPDFText_GetFontInfo(self.textpage.raw, i, buf, len(buf), ctypes.byref(flags))
            out.append((ch, x, y + moved.get(i, 0.0), buf.value, raw.FPDFText_GetFontSize(self.textpage.raw, i)))
        return {"letters": out, "bands": list(self.bands), "strict": self.strict and not moved,
                "texts": list(self.texts)}

    def take(self, rows, paragraph=False, text=None):
        """Take the old words (rows of letters) out of the page's drawing,
        and note where they were drawn, for the new ones to be written
        there. False (nothing changed) when that can't be done for sure:
        the old words are covered instead."""
        flow = self.flow
        if flow is None or self.legacy or not flow.content:
            return False
        everything = [L for row in rows for L in row]
        saved = flow.checkpoint()
        spot = flow.glyph(everything[0]) if flow.uncover(everything) else None
        # Tagged text: everything new is written at the first letter's place,
        # so the old words must all be in one tag (else the others' tags
        # would be left empty), and a tag's replacement text must be theirs.
        one_line = text if text is not None and len(rows) == 1 and "\n" not in text else None
        # The words may be in several tags (a link, a line or a run with its
        # own): each new line is then written at the old line it replaces, so
        # the words stay spread over the tags in reading order, and none of
        # the old words is left in the file underneath.
        retag = flow.tags(everything, one_line, one=False) if spot is not None else None
        spots = [(flow.glyph(row[0]), row[0]) for row in rows] if paragraph and retag is not None else None
        if retag is None:
            done = False
        elif paragraph:
            done = flow.take_out(rows)
        else:
            done = flow.remove(everything)
        if not done:
            flow.rollback(saved)
            return False
        flow.retag.update(retag)
        if spot is not None:
            self.spot = (spot, everything[0])
            self.spots = spots if spots and all(sp is not None for sp, _ in spots) else None
        return True

    def take_replaced(self, letters, text):
        """:meth:`take` for a line read as a tag's replacement text (see
        textflow's ``replaced_line``)."""
        flow = self.flow
        if flow is None or self.legacy or not flow.content or "\n" in text or _rtl(text):
            return False
        saved = flow.checkpoint()
        spot = flow.replaced_line(letters[0], sum(len(L["char"]) for L in letters), text)
        if spot is None:
            flow.rollback(saved)
            return False
        self.spot = (spot, letters[0])
        return True

    def settle(self):
        """After each change: its new words go into the drawing where the
        old ones were (when those were taken out of it), not on top of the
        page."""
        if self.spot is None:
            return
        left = []
        for run in self.runs[self._first_run:]:
            spot, first = self.spot
            if self.spots and run.get("line") is not None:
                spot, first = self.spots[min(run["line"], len(self.spots) - 1)]
            colour = None if _same_colour(run["style"]["color"], first["style"]["color"]) else run["style"]["color"]
            if not self.flow.insert(spot, True, {**run, "same": False, "color": colour}):
                left.append(run)
        self.runs[self._first_run:] = left

    def ready(self):
        """Fonts that aren't the document's own, for words written in the
        drawing: put in the file (cut down to the letters used), so the
        words are text like the rest: found by a search, copied in order."""
        if self._flow is None:
            return
        groups = {}
        for run in self._flow.pending():
            if run["own"] is None:
                groups.setdefault(run["name"], []).append(run)
        for name, runs in groups.items():
            letters = {c for run in runs for e in run["entries"] for c in e[0]}
            own = _embedded(self.writer, name, letters)
            for run in runs:
                if own is not None and own.can_write("".join(c for e in run["entries"] for c in e[0])):
                    run["own"] = own

    def run(self, style, font, entries):
        own, name = font[:2]
        left_out = self.notes.get("missing", "")
        entries = [e for e in entries if (not e[0].isspace() or not (own and own.space)) and e[0] not in left_out]
        if not any(not e[0].isspace() for e in entries):
            return
        last = self.runs[-1] if self.runs else None
        if last and last["style"] is style and last["own"] is not None and own is not None \
                and last["own"].font.get_object() is own.font.get_object() \
                and abs(_across(style, last["entries"][0], entries[0])) < 0.01:  # on the same line
            last["entries"] += entries  # one piece of text: copied, found and edited again as one
        else:
            self.runs.append({"style": style, "own": own, "name": name, "entries": entries, "line": self.line_no})

    def close(self):
        self.textpage.close()
        self.pdf_page.close()

    # -- which font ----------------------------------------------------------------------
    def _choose(self, style, text):
        """:func:`_choose`, kept embedded in a PDF/A file, and never a font
        that would only be in the reader's hands."""
        def choose(letters):
            return self._archival(_choose(self.page, style, letters), style, letters)
        return self._drawable(choose(text), text, choose)

    def _shown(self, text):
        """``text`` without the letters noted as missing (no font draws them)."""
        left_out = self.notes.get("missing", "")
        return "".join(ch for ch in text if ch not in left_out) if left_out else text

    def _drawable(self, font, text, choose):
        """``font`` unless it is one a reader must supply itself (no font
        here has all the letters): then the font that draws most of them,
        and the letters no font here can draw are noted as "missing" and
        left out, rather than written in a font that may show nothing."""
        private = {ch for ch in text if _private(ch) and not _can_draw(font, ch)}
        if private:  # only the document's own font may stand for these
            self.notes["missing"] = "".join(sorted(set(self.notes.get("missing", "")) | private))
        if font[0] or font[1] not in READER_FONTS:
            return font
        letters = sorted({ch for ch in text if not ch.isspace()})
        each = {ch: choose(ch) for ch in letters}
        missing = {ch for ch, f in each.items() if not f[0] and f[1] in READER_FONTS}
        best = None
        for candidate in {id(f[0]) if f[0] else f[1]: f for ch, f in each.items() if ch not in missing}.values():
            drawn = {ch for ch in letters if ch not in missing and _can_draw(candidate, ch)}
            if best is None or len(drawn) > len(best[1]):
                best = (candidate, drawn)
        if best is None:
            best = (choose("a"), set())
        missing = set(letters) - best[1]
        if missing:
            self.notes["missing"] = "".join(sorted(set(self.notes.get("missing", "")) | missing))
        return best[0]

    def _pieces(self, style, font, text):
        """Which font writes which letter of new words on a line whose own
        font lacks some: {letter: font}, the line's own font for the
        letters it has and ``font`` (or another, letter by letter) for the
        rest; None when one font writes them all."""
        letters = sorted({ch for ch in text if not ch.isspace()})
        if font[2] == SAME or not letters:
            return None
        has = [ch for ch in letters if _own_font(self.page, style, ch)]
        own = _own_font(self.page, style, "".join(has)) if has else None
        by = {}
        if own:
            mine = (own, None, SAME, fonts.display_name(style["font"]))
            by.update({ch: mine for ch in has})
        blind = set()
        for ch in letters:
            if ch not in by and not _can_draw(font, ch):
                other = self._choose(style, ch)
                if _can_draw(other, ch):
                    by[ch] = other
                else:
                    blind.add(ch)
        if blind:  # no font here draws them: named, not written in a font that shows something else
            self.notes["missing"] = "".join(sorted(set(self.notes.get("missing", "")) | blind))
        return by or None

    def _font(self, style, base, text, opts):
        """The font for new words: the one asked for in ``opts`` (a family,
        or bold or italic other than ``base``, the old words' look), else
        like the old words."""
        letters = "".join(ch for ch in text if not ch.isspace())
        asked = opts.get("font")
        if asked is None and style["bold"] == base["bold"] and style["italic"] == base["italic"]:
            return self._choose(style, text)
        family = asked or fonts.family_name(base["font"]) or ""
        if family and fonts.family_key(family) == fonts.family_key(base["font"]):
            kind = style["family"]  # the old words' kind (a name like CMR10 doesn't tell)
        else:
            kind = fonts.kind_of(family)
        def choose(wanted):
            return self._archival(self._pick(family, kind, style, wanted), style, wanted)
        font = self._drawable(choose(letters), letters, choose)
        if family:
            self.notes["asked"] = family
            self.notes["matched"] = font[2] in (SAME, INSTALLED, STANDARD) and "reason" not in self.notes
        return font

    def _pick(self, family, kind, style, letters):
        bold, italic = style["bold"], style["italic"]
        key = fonts.family_key(family) if family else ""
        if key:
            own = self._document_font(key, bold, italic, letters)
            if own:
                return own, None, SAME, fonts.display_name(own.font.get_object().get("/BaseFont", family))
            name = fonts.installed_family(family, bold, italic, letters)
            if name:
                return None, name, INSTALLED, fonts.display_name(fonts.file_name(name))
            if key in fonts.STANDARD_FAMILIES:
                name = fonts.font_for(letters or "a", fonts.STANDARD_FAMILIES[key], bold, italic)
                return None, name, STANDARD if name in BASE14_NAMES else SIMILAR, \
                    fonts.display_name(fonts.file_name(name))
        name = _similar(letters, kind, bold, italic)
        return None, name, SIMILAR, fonts.display_name(fonts.file_name(name))

    def _archival(self, font, style, letters):
        """In a PDF/A file every font is embedded: a standard font, which
        would be the reader's own copy, gives way to an installed one."""
        if not self.pdfa or font[0] or font[1] not in BASE14_NAMES:
            return font
        kind = "serif" if font[1].startswith("Times") else "mono" if font[1].startswith("Courier") else "sans"
        bold, italic = "Bold" in font[1], "Italic" in font[1] or "Oblique" in font[1]
        name = fonts.embedded_like(kind, bold, italic, "".join(ch for ch in letters if not ch.isspace()))
        if not name:
            return font
        self.notes["reason"] = "pdfa"
        return None, name, SIMILAR, fonts.display_name(fonts.file_name(name))

    def _document_font(self, key, bold, italic, letters):
        """One of the document's fonts of family ``key`` in that face, able to
        write ``letters`` (its copy may hold only some letters): an
        :class:`_OwnFont`, or None. This page's fonts first."""
        if self.writer is None:
            numbers = [self.number]
        else:
            numbers = [self.number] + [n for n in range(len(self.writer.pages)) if n != self.number][:40]
        seen = set()
        for number in numbers:
            page = self.page if number == self.number else self.writer.pages[number]
            for font in _page_fonts(page):
                obj = font.get_object()
                if id(obj) in seen or obj.get("/Subtype") == "/Type3":
                    continue
                seen.add(id(obj))
                name = fonts.strip_subset(obj.get("/BaseFont", ""))
                if fonts.family_key(name) != key or _face(obj) != (bool(bold), bool(italic)):
                    continue
                if self.pdfa and fonts.font_program(obj) is None:
                    continue  # the reader's own copy: not for a PDF/A file
                try:
                    own = _OwnFont(font, fonts.pdf_font_codes(font))
                except Exception:  # noqa: BLE001 - a font dictionary we can't read
                    continue
                if own.codes and own.can_write(letters) and self._draws(number, obj, letters):
                    return own
        return None

    def _draws(self, number, font, letters):
        """Whether the document's copy of ``font`` really has the letters'
        shapes (a copy cut down to some letters keeps empty ones for the
        rest), as pdfium sees it drawn on page ``number``."""
        name = str(font.get("/BaseFont", ""))
        if not letters:
            return True
        if number == self.number:
            textpage, close = self.textpage, None
        elif self.doc is not None:
            close = self.doc[number]
            textpage = close.get_textpage()
        else:
            return False
        try:
            faces = self._faces.get(number)
            if faces is None:
                faces = self._faces[number] = {}
                buf, flags = ctypes.create_string_buffer(256), ctypes.c_int()
                for i in range(textpage.count_chars()):
                    _raw().FPDFText_GetFontInfo(textpage.raw, i, buf, len(buf), ctypes.byref(flags))
                    faces.setdefault(buf.value.decode("latin-1").lstrip("/"), i)
            index = faces.get(name.lstrip("/"))
            if index is None:
                index = next((i for n, i in faces.items() if fonts.strip_subset(n) == fonts.strip_subset(name)), None)
            if index is None:  # not drawn there: only a whole font surely has the letters
                return fonts.strip_subset(name) == name.lstrip("/")
            handle = _font_handle(textpage, index)
            if handle and fonts.font_program(font) is None and not _is_embedded(handle):
                return True  # the reader's own copy of the font: every letter of its encoding
            return bool(handle) and all(_has_letter(handle, ch) for ch in set(letters))
        finally:
            if close is not None:
                textpage.close()
                close.close()

    # -- the three kinds of change --------------------------------------------------------
    def add(self, x, y, text, opts=None):
        """New words whose top left is at (x, y), looking like the nearest text
        unless ``opts`` say otherwise (and wrapped in its "width", if given)."""
        opts = opts or {}
        near = _nearest_char(self.textpage, x, y)
        style = dict(_style(self.textpage, near, self.cache)) if near is not None else \
            _default_style((x, y - 14, x + 100, y))
        k = style["size"] / style["raw_size"]
        style.update(matrix=(k, 0.0, 0.0, k), angle=0.0)
        base, style = style, _restyle(style, opts)
        font = self._font(style, base, text, opts)
        text = self._shown(text)
        lines, ends, room = text.split("\n"), None, opts.get("width")
        if room:
            width, space = _metrics(font[0], font[1], style)
            lines, ends = [], set()
            for part in text.split("\n"):
                lines += [ln.strip() for ln in _wrap(part, room, lambda t: _place(t, 0.0, width, 0.0, space)[1])]
                ends.add(len(lines) - 1)
        self._set(lines, (x, y - style["size"] * 0.8), style, font, 0.0, None,
                  style["size"] * 1.2, opts.get("align"), room, ends)
        return font

    def line(self, rect, text, old, opts=None):
        """Change the words of one line."""
        opts = opts or {}
        letters = _letters(self.textpage, self._one_line(_chars(self.textpage, rect), rect, old), self.cache,
                           self.hidden)
        if letters is None:  # nothing there (e.g. a scan): the box as drawn
            self.touched.append(tuple(rect))
            self.bands.append((rect[0], (rect[1] + rect[3]) / 2, 1.0, 0.0, rect[3] - rect[1]))
            base = _default_style(rect)
            style = _restyle(base, opts)
            self.cover(rect)
            font = self._font(style, base, text, opts)
            text = self._shown(text)
            self._set(text.split("\n"), (rect[0], rect[1] + (rect[3] - rect[1]) * 0.22), style, font,
                      0.0, None, style["size"] * 1.2, opts.get("align"), rect[2] - rect[0])
            return font
        self.touch(letters)
        if len(letters) > 1 and all(L["origin"] == letters[0]["origin"] for L in letters):
            # Read as a tag's replacement text, every letter at the line's
            # start: there are no letters to match up, the line is written again.
            return self._redo(letters, [letters], text, 0.0, None, opts, replaced=True)
        tc, word = _spacing([letters])
        if any(key in opts for key in LOOKS):  # a new look: the line written again in it
            return self._redo(letters, [letters], text, tc, word, opts)
        count = sum(len(L["char"]) for L in letters)
        before = old if old is not None and _nonspace(old) == count else _text_of(letters)
        if before == text:
            return (None, None, SAME, fonts.display_name(letters[0]["style"]["font"]))
        # A change of one line changes that line only: a longer line stays
        # one line (and says "wider" when it reaches what's beside it), a
        # line taken away leaves its space. Wrapping is for paragraph changes.
        if "\n" in text or not text.strip():
            return self._redo(letters, [letters], text, tc, word)
        try:
            return self._respell(letters, before, text, tc, word)
        except _Unsure:
            return self._redo(letters, [letters], text, tc, word)

    def _segments(self):
        """Every level line of the page, cut where words are far apart (a
        column, a table cell): [{x0, x1, y, size, idx, letters}]."""
        if self._segs is None:
            from pdfmanager import textflow

            self._segs = []
            for line in textflow._lines([L for L in self.flow.letters if L[4]]):
                pieces = [[line[0]]]
                for a, b in zip(line, line[1:]):
                    if b[1][0] - a[1][2] > 1.2 * max(a[3], b[3]):
                        pieces.append([])
                    pieces[-1].append(b)
                for piece in pieces:
                    self._segs.append({"x0": piece[0][1][0], "x1": max(L[1][2] for L in piece), "y": piece[0][2][1],
                                       "size": statistics.median(L[3] for L in piece), "idx": {L[0] for L in piece},
                                       "letters": piece})
        return self._segs

    def _context(self, letters):
        """How the line sits among the others: "justify" (a line above or
        below starts and ends where it does), "right" or "center" (others
        end, or are centred, where it is, and none starts where it does),
        else "left"."""
        style = letters[0]["style"]
        if self.flow is None or self.legacy or abs(style["angle"]) >= 1:
            return "left"
        mine = {L["index"] for L in letters}
        size = max(L["style"]["size"] for L in letters)
        tol = max(0.75, 0.08 * size)
        x0, x1 = min(L["loose"][0] for L in letters), max(L["loose"][2] for L in letters)
        y = letters[0]["origin"][1]
        left = right = centre = both = 0
        for seg in self._segments():
            if seg["idx"] & mine:
                continue
            dl, dr = abs(seg["x0"] - x0), abs(seg["x1"] - x1)
            dm = abs((seg["x0"] + seg["x1"]) / 2 - (x0 + x1) / 2)
            near = abs(seg["y"] - y) < 9 * size
            if dl < tol and dr < tol:
                both += abs(seg["y"] - y) < 3.2 * size and abs(seg["size"] - size) < 0.5
            elif dl < tol:
                left += 1
            elif dr < tol and dl > 3 * tol:
                right += near
            elif dm < tol and dl > 3 * tol:
                centre += near
        if not left and right:
            return "right"
        if not left and centre:
            return "center"
        if both:
            return "justify"
        crop = self.flow.crop
        if not left and abs((x0 + x1) / 2 - (crop[0] + crop[2]) / 2) < 1.0 and x0 - crop[0] > 3 * size:
            return "center"  # alone on the page, in its middle
        return "left"

    def _beside(self, letters):
        """How far the line may reach: (left, right), the nearest words on
        either side of it on the page, else the page's edges."""
        x0, x1 = min(L["loose"][0] for L in letters), max(L["loose"][2] for L in letters)
        y0, y1 = min(L["loose"][1] for L in letters), max(L["loose"][3] for L in letters)
        mine = {L["index"] for L in letters}
        crop = self.flow.crop
        left, right = crop[0], crop[2]
        for i, loose, _, size, _ in self.flow.letters:
            if i in mine or loose[3] <= y0 + 0.2 * (y1 - y0) or loose[1] >= y1 - 0.2 * (y1 - y0):
                continue
            if loose[0] >= x1 - 0.5:
                right = min(right, loose[0] - 0.25 * size)
            elif loose[2] <= x0 + 0.5:
                left = max(left, loose[2] + 0.25 * size)
        return left, right

    def _one_line(self, chars, rect, old):
        """The letters of ``chars`` on the one line meant: a box drawn a
        little tall also catches letters of the line above or below (on a
        level page; lines at an angle are left as found)."""
        raw = _raw()
        m = raw.FS_MATRIX()
        for i in chars:
            raw.FPDFText_GetMatrix(self.textpage.raw, i, ctypes.byref(m))
            if abs(m.b) > abs(m.a) * 0.02 or m.a <= 0:
                return chars
        # Lines, not the raised and lowered letters within one (H2O, E=mc2):
        # those sit less than a line apart.
        rows = []
        for i in sorted(chars, key=lambda i: -_origin(self.textpage, i)[1]):
            y, size = _origin(self.textpage, i)[1], max(1.0, raw.FPDFText_GetFontSize(self.textpage.raw, i))
            if rows and rows[-1][0] - y < 0.7 * max(size, rows[-1][1]):
                rows[-1][2].append(i)
            else:
                rows.append([y, size, [i]])
            rows[-1][0], rows[-1][1] = y, max(size, rows[-1][1])
        if len(rows) < 2:
            return chars
        rows = [sorted(members) for _, _, members in rows]

        def count(row):
            return sum(1 for i in row if self.textpage.get_text_range(i, 1).strip()
                       and not self.hidden.hides(self.textpage, i, self.textpage.get_charbox(i, loose=True)))

        if old is not None:
            exact = [row for row in rows if count(row) == _nonspace(old)]
            if len(exact) == 1:
                return exact[0]
        middle = (rect[1] + rect[3]) / 2

        def off(row):
            boxes = [self.textpage.get_charbox(i, loose=True) for i in row]
            return abs((min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2 - middle)

        return min(rows, key=off)

    def paragraph(self, rect, text, opts=None):
        """Write a paragraph again, wrapped in the old one's width; the
        paragraphs below it in its column move up or down by the change in
        its height (see textflow), unless ``opts`` has "flow": False."""
        opts = opts or {}
        chars = _chars(self.textpage, rect, across=3.0)
        rows = [r for r in (_letters(self.textpage, row, self.cache, self.hidden)
                            for row in _rows_with_marks(self.textpage, chars)) if r]
        if not rows:
            return self.line(rect, text, None, opts)
        _set_rises(rows)
        tc, word = _spacing(rows)
        base = rows[0][0]["style"]
        if abs(base["angle"]) >= 1:
            everything = [L for row in rows for L in row]
            self.touch(everything)
            return self._redo(rows[0], rows, text, tc, word, opts)
        u, down = _unit(base["matrix"]), _down(base["matrix"])
        orig_right = max(row[-1]["s"] + row[-1]["adv"] for row in rows)
        lead, para_gap = _para_spacing(rows, down, base["size"] * 1.2)
        marker_count, marker_prefix, left, first_indent = (
            _detect_indent(rows, base["size"], orig_right)
            if opts.get("indent", True)
            else (0, "", min(row[0]["s"] for row in rows), 0.0)
        )
        marks = opts.get("runs")
        if marker_count > 0 and text.strip():
            mt = marker_prefix.rstrip()
            if text.startswith(mt) and len(text) > len(mt) and text[len(mt)].isspace():
                cut = len(mt)
                while cut < len(text) and text[cut] == " ":
                    cut += 1
                rows = [rows[0][marker_count:]] + rows[1:]
                text = text[cut:]
                if marks and len(marks) >= cut:
                    marks = marks[cut:]
            else:
                marker_count = 0
                left = min(row[0]["s"] for row in rows)
        align_rows = list(rows)
        align_first_indent = first_indent
        room = max(orig_right - left, base["size"] * 4)
        # Re-set only from the first changed line when possible, leaving earlier lines byte-identical.
        if (opts.get("keep_unchanged", True)
                and len(rows) >= 2
                and text.strip()
                and "\n\n" not in text
                and not any(k in opts for k in ("font", "size", "bold", "italic", "color", "align"))
                and not (_rtl(text) or any(_rtl(L["char"]) for row in rows for L in row))
                and self.flow is not None and not self.flow.flowed and not self.legacy):
            use_marks = marks if marks and len(marks) == len(text) else None
            keep_head, text_pos = _leading_unchanged(rows, text, use_marks)
            if keep_head > 0 and text[text_pos:].strip():
                rows = rows[keep_head:]
                text = text[text_pos:]
                if marks and len(marks) >= text_pos:
                    marks = marks[text_pos:]
                first_indent = 0.0
        everything = [L for row in rows for L in row]
        self.written_text = text
        self.touch(everything)
        first = rows[0][0]
        flow = opts.get("flow", True) and self.flow is not None and not self.flow.flowed
        self.legacy = self.legacy or _rtl(text) or any(_rtl(L["char"]) for L in everything)
        taken = self.flow is not None and not self.flow.flowed and self.take(rows, paragraph=True, text=text)
        if not taken:
            self.cover(_box(everything))
        if not text.strip():  # taken away: what's below comes up into its place
            if taken:
                found = self.flow.column(rows, lead)
                if found["lines"] and self.flow.move(found, max(L["loose"][3] for L in rows[0]) - found["lines"][0][3]):
                    self.notes["moved"] = len(found["lines"])
            return None, None, SAME, fonts.display_name(base["font"])
        flow = flow and (taken or True)
        style = _restyle(base, opts)
        font = self._font(style, base, text, opts)
        # Words in other styles than the first (bold, italic, a colour) keep theirs.
        links = self._links_over(everything)
        looks = (_char_styles(rows, text, (lambda L: _link_at(links, L)) if links else None)
                 if opts.get("styles", True) and not (_rtl(text) or any(_rtl(L["char"]) for L in everything))
                 else None)
        if marks and len(marks) == len(text):  # the editor's bold and italic, letter by letter
            looks = looks or [(base, None, 0.0)] * len(text)
            made, was = {}, {}
            for n, ((st, lk, up), (bold, italic)) in enumerate(zip(looks, marks)):
                if (bold, italic) != (st["bold"], st["italic"]):
                    key = (id(st), bold, italic)
                    if key not in made:
                        made[key] = _restyle(st, {"bold": bold, "italic": italic})
                        was[id(made[key])] = st
                    looks[n] = (made[key], lk, up)
        else:
            was = {}
        reps, fonts_by, scaled_by, linked = {}, {}, {}, {}
        if looks:
            keys = [_attr_look(a) for a in looks]
            for (st, _, _), key in zip(looks, keys):
                reps.setdefault(key, st)
            # A look chosen for the whole paragraph goes on top of the words'
            # own: a font keeps bold words bold, bold keeps italic words
            # italic, and a colour replaces the paragraph's main colour only.
            whole = {k_: opts[k_] for k_ in ("font", "bold", "italic") if k_ in opts}
            usual = None
            if "color" in opts:
                import collections
                usual = collections.Counter(tuple(round(v, 3) for v in a[0]["color"])
                                            for a, ch in zip(looks, text) if not ch.isspace()).most_common(1)[0][0]
            for key, rep in list(reps.items()):
                letters_of = "".join(ch for ch, k_ in zip(text, keys) if k_ == key)
                before = was.get(id(rep), rep)
                change = {k_: v for k_, v in whole.items() if k_ != "font"}
                if usual is not None and tuple(round(v, 3) for v in rep["color"]) == usual:
                    change["color"] = opts["color"]
                if change:
                    rep = reps[key] = _restyle(rep, change)
                if whole or before is not rep and (before["bold"], before["italic"]) != (rep["bold"], rep["italic"]):
                    # Another face than the words had (asked for here, or made in the editor).
                    said = {k_: self.notes[k_] for k_ in ("asked", "matched") if k_ in self.notes}
                    fonts_by[key] = self._font(rep, before, letters_of,
                                               {"bold": rep["bold"], "italic": rep["italic"],
                                                **({"font": whole["font"]} if "font" in whole else {})})
                    if "font" not in whole:
                        for k_ in ("asked", "matched"):
                            self.notes.pop(k_, None)
                        self.notes.update(said)
                else:
                    fonts_by[key] = self._choose(rep, letters_of)
        left_out = self.notes.get("missing", "")
        content = [(ch, key) for ch, key in zip(text, keys) if ch not in left_out] if looks else None
        text = self._shown(text)
        plain = not any(key in opts for key in ("font", "bold", "italic"))  # the old words' font
        was = font if plain else _choose(self.page, base, text)
        natural = _metrics(was[0], was[1], base)[1] + 2 * tc
        is_rtl = _rtl(text)
        align = opts.get("align") or _alignment(
            align_rows, left, orig_right, word, natural, base["size"], first_indent=align_first_indent
        )
        if is_rtl and not opts.get("align") and align == "left":
            align = "right"
        top = max(L["loose"][3] for L in rows[0])
        ascent = (top - first["origin"][1]) if abs(down[1]) > 0.5 else base["size"] * 0.8
        old_span = ((rows[-1][0]["origin"][0] - first["origin"][0]) * down[0]
                    + (rows[-1][0]["origin"][1] - first["origin"][1]) * down[1])
        obstacles = (
            self.flow.obstacles(left, orig_right, top + base["size"], float(self.page.cropbox.bottom), base["size"])
            if opts.get("wrap_pictures", True) and self.flow is not None and self.flow.content and abs(down[1]) > 0.5
            else []
        )

        simple = opts.get("breaks") == "simple"  # line by line, as before 0.34
        # Words are divided at line ends (English only) when every font here can draw the hyphen.
        language = None
        try:
            language = self.writer._root_object.get("/Lang") if self.writer is not None else None
        except Exception:  # noqa: BLE001 - a catalogue that can't be read: taken as English
            language = None
        divide = None
        if not simple and opts.get("hyphens", True) and "-" not in self.notes.get("missing", "") \
                and all(_can_draw(f, "-") for f in (fonts_by.values() if content else [font])):
            def divide(word, language=language):
                return _divisions(word, str(language) if language is not None else None)

        def scaled(key, k):
            """The style of the words that look like ``key``, at ``k`` times their size."""
            if (key, k) not in scaled_by:
                rep = reps[key]
                st = _restyle(rep, {"size": rep["size"] * k} if k != 1 else {})
                scaled_by[key, k] = (st, _metrics(fonts_by[key][0], fonts_by[key][1], st)[0])
            return scaled_by[key, k]

        def _split_paras_seq(seq):
            """Split a styled-character sequence into paragraphs on blank lines (\\n\\n)."""
            parts = _split_seq(seq, "\n")
            paras, cur = [], []
            for p in parts:
                if not any(not ch.isspace() for ch, _ in p):
                    if cur:
                        paras.append(cur)
                        cur = []
                else:
                    cur.append(p)
            if cur or not paras:
                paras.append(cur or [[]])
            return paras

        def _make_slot(y_start, lead_k, col_room, fi, sz, use_obs):
            """Per-line (extra_x_offset, line_width) accounting for first-line indent and pictures."""
            def slot(i):
                s_off, s_room = 0.0, col_room
                if use_obs:
                    y_line = y_start - i * lead_k
                    sl, sr = _free_span(y_line, left, left + col_room, sz, obstacles)
                    if sr - sl >= sz * 3:
                        s_off, s_room = sl - left, sr - sl
                if i == 0 and fi > 0:
                    s_off += fi
                    s_room = max(s_room - fi, sz * 2)
                return s_off, s_room
            return slot

        def layout(style, text=content or text, room=room, tight=1.0, indent=True):
            """The new lines: ([(text, word gap, offset, ends a part, y_off, line_tc)],
            letter width, letter spacing, line spacing, drop, growth). With words
            in several styles, ``text`` and each line's text are lists of (letter, look)."""
            if isinstance(text, list):
                return layout_runs(style, text, room, tight, indent=indent)
            k = style["size"] / base["size"]
            tight = min(1.0, max(tight, style["size"] * 1.02 / (lead * k)))  # lines never closer than their size
            width, space = _metrics(font[0], font[1], style)
            tck = tc * k
            gap = space + 2 * tck if align == "justify" or word is None or not plain else word * k
            lead_k = lead * k * tight
            pgap_k = para_gap * k * tight
            drop = (k - 1) * ascent  # a bigger size grows down from the old top
            use_obs = bool(obstacles) and indent

            def measure(line):
                return _place(line, 0.0, width, tck, gap)[1] - tck

            lines, y_off = [], 0.0
            paras = [p for p in text.split("\n\n")] if "\n\n" in text else [text]
            for p_idx, para in enumerate(paras):
                if p_idx > 0:
                    y_off += pgap_k
                subparts = para.split("\n")
                for sp_idx, part in enumerate(subparts):
                    fi = (first_indent * k
                          if indent and sp_idx == 0 and first_indent > 0 and align in ("left", "justify")
                          else 0.0)
                    slot = _make_slot(first["origin"][1] - drop - y_off, lead_k, room, fi, style["size"], use_obs)
                    room_arg = (lambda i, sl=slot: sl(i)[1]) if (fi > 0 or use_obs) else room
                    wrapped = _wrap_marked(part, room_arg, measure, justify=align == "justify", greedy=simple, divide=divide)
                    for j, (line, end) in enumerate(wrapped):
                        line, g, line_tc = line.strip(), gap, tck
                        if is_rtl:
                            line = _bidi_visual(line, rtl=True)
                        s_off, line_room = slot(j) if (fi > 0 or use_obs) else (0.0, room)
                        off = s_off
                        spaces = line.count(" ")
                        if align == "justify" and j < len(wrapped) - 1:
                            if _has_cjk(line) and spaces == 0 and len(line) > 1:
                                extra_tc = (line_room - measure(line)) / (len(line) - 1)
                                if 0 <= extra_tc <= style["size"] * 1.5:
                                    line_tc = tck + extra_tc
                            elif spaces:
                                extra = (line_room - measure(line)) / spaces
                                if 0 <= extra <= style["size"] * 2:
                                    g = gap + extra
                        elif align in ("center", "right") or (is_rtl and align == "justify" and j == len(wrapped) - 1):
                            free = line_room - measure(line)
                            off = s_off + (free / 2 if align == "center" else free)
                        is_para_end = j == len(wrapped) - 1 and sp_idx == len(subparts) - 1 and p_idx < len(paras) - 1
                        end_mark = "para" if is_para_end else (True if j == len(wrapped) - 1 else end or False)
                        lines.append((line, g, off, end_mark, y_off, line_tc))
                        y_off += lead_k
            grow = drop + (lines[-1][4] if lines else 0.0) - old_span + 0.22 * (style["size"] - base["size"])
            return lines, width, tck, lead_k, drop, grow

        def layout_runs(style, seq, room, tight, indent=True):
            k = style["size"] / base["size"]
            tight = min(1.0, max(tight, style["size"] * 1.02 / (lead * k)))
            width, space = _metrics(font[0], font[1], style)
            tck = tc * k
            gap = space + 2 * tck if align == "justify" or word is None else word * k
            lead_k = lead * k * tight
            pgap_k = para_gap * k * tight
            drop = (k - 1) * ascent
            use_obs = bool(obstacles) and indent

            def measure(line):
                pen = 0.0
                for ch, key in line:
                    pen += gap - tck if ch.isspace() else scaled(key, k)[1](ch) + tck
                return pen - tck

            lines, y_off = [], 0.0
            paras = _split_paras_seq(seq)
            for p_idx, subparts in enumerate(paras):
                if p_idx > 0:
                    y_off += pgap_k
                for sp_idx, part in enumerate(subparts):
                    fi = (first_indent * k
                          if indent and sp_idx == 0 and first_indent > 0 and align in ("left", "justify")
                          else 0.0)
                    slot = _make_slot(first["origin"][1] - drop - y_off, lead_k, room, fi, style["size"], use_obs)
                    room_arg = (lambda i, sl=slot: sl(i)[1]) if (fi > 0 or use_obs) else room
                    wrapped = _wrap_seq_marked(part, room_arg, measure, justify=align == "justify", greedy=simple, divide=divide)
                    for j, (line, end) in enumerate(wrapped):
                        line, g, line_tc = _strip_seq(line), gap, tck
                        s_off, line_room = slot(j) if (fi > 0 or use_obs) else (0.0, room)
                        off = s_off
                        spaces = sum(1 for ch, _ in line if ch == " ")
                        plain_line = "".join(ch for ch, _ in line)
                        if align == "justify" and j < len(wrapped) - 1:
                            if _has_cjk(plain_line) and spaces == 0 and len(line) > 1:
                                extra_tc = (line_room - measure(line)) / (len(line) - 1)
                                if 0 <= extra_tc <= style["size"] * 1.5:
                                    line_tc = tck + extra_tc
                            elif spaces:
                                extra = (line_room - measure(line)) / spaces
                                if 0 <= extra <= style["size"] * 2:
                                    g = gap + extra
                        elif align in ("center", "right"):
                            free = line_room - measure(line)
                            off = s_off + (free / 2 if align == "center" else free)
                        is_para_end = j == len(wrapped) - 1 and sp_idx == len(subparts) - 1 and p_idx < len(paras) - 1
                        end_mark = "para" if is_para_end else (True if j == len(wrapped) - 1 else end or False)
                        lines.append((line, g, off, end_mark, y_off, line_tc))
                        y_off += lead_k
            grow = drop + (lines[-1][4] if lines else 0.0) - old_span + 0.22 * (style["size"] - base["size"])
            return lines, width, tck, lead_k, drop, grow

        def rejoin(part):
            """Lines joined into one text again (for running on into the next column)."""
            # (A line's fourth part: "para" at a paragraph break, True at the end of a part,
            # "soft" where a word was divided there, "hard" after a word's own hyphen.)
            if not content:
                return "".join((ln[0][:-1] if ln[3] == "soft" else ln[0])
                               + ("\n\n" if ln[3] == "para" else "\n" if ln[3] is True else "" if ln[3] else " ")
                               for ln in part).rstrip()
            out = []
            for ln in part:
                out += ln[0][:-1] if ln[3] == "soft" else ln[0]
                key_ref = ln[0][-1][1] if ln[0] else content[0][1]
                if ln[3] == "para":
                    out.extend([("\n", key_ref), ("\n", key_ref)])
                elif not ln[3] or ln[3] is True:
                    out.append(("\n" if ln[3] else " ", key_ref))
            return _strip_seq(out)

        def emit(line, start, width, tck, g, at, target=self):
            """Write one line from ``start`` along it; ``at(s)`` is the page point s along."""
            if not content:
                placed, _ = _place(line, start, width, tck, g)
                target.run(style, font, [(ch, *at(s)) for ch, s in placed])
                return
            k = style["size"] / base["size"]
            pen, group, current = start, [], None
            for ch, key in line:
                if ch.isspace():
                    group.append((" ", *at(pen)))
                    pen += g - tck
                    continue
                if key != current and current is not None:
                    target.run(scaled(current, k)[0], fonts_by[current], group)
                    group = []
                current = key
                px, py = at(pen)
                if key[-2]:  # set above or below the line
                    px, py = px - down[0] * key[-2] * k, py - down[1] * key[-2] * k
                group.append((ch, px, py))
                st, w = scaled(key, k)
                if target is self and key[-1] is not None:  # in links or markups: where their words are now
                    (x0, y0), (x1, _) = at(pen), at(pen + w(ch))
                    for index in key[-1]:
                        linked.setdefault(index, []).append((x0, y0, x1, st["size"]))
                pen += w(ch) + tck
            if current is not None:
                target.run(scaled(current, k)[0], fonts_by[current], group)

        def squeeze(found, style, current):
            """Nothing below can move, so the paragraph has to end above
            what follows it: the smallest change that does (lines a little
            closer, then the size down a little at a time), or the most
            that's sane when even that isn't enough. (style, layout,
            whether it now fits)."""
            tops = [b[3] for b in found["lines"][:1]] + ([found["barrier"][3]] if found["barrier"] else [])
            allowed = found["bottom"] - max(tops + [found["limit"]]) - 0.05 * lead
            if current[5] <= max(allowed, 0.5):
                return style, current, True
            best = (style, current)
            for step in range(0, 13):  # down to seven tenths of the size
                smaller = style if step == 0 else _restyle(style, {"size": style["size"] * (1 - 0.025 * step)})
                for tight in ((1.0, 0.93) if step else (0.93,)):
                    best = (smaller, layout(smaller, tight=tight))
                    if best[1][5] <= allowed:
                        return *best, True
            return *best, False

        lines, width, tck, lead_k, drop, grow = layout(style)
        if obstacles:
            self.notes["wrapped_picture"] = True
        more, nxt = [], None  # lines that run on at the top of the next column
        if flow and abs(grow) > 0.5:
            found = self.flow.column(rows, lead)
            fits = grow <= 0 or found["low"] - grow >= found["limit"] - 0.01
            if not fits and not found["lines"] and len(lines) > 1:
                # The last paragraph of its column: what doesn't fit runs on
                # into the next column, whose text moves down to make room.
                nxt = self.flow.next_column(found, lead)
                for keep in range(len(lines) - 1, 0, -1) if nxt else ():
                    here = drop + lines[keep - 1][4] - old_span + 0.22 * (style["size"] - base["size"])
                    if found["low"] - here < found["limit"] - 0.01:
                        continue
                    words = rejoin(lines[keep:])
                    more = layout(style, words, max(nxt["x1"] - nxt["x0"], base["size"] * 4), indent=False)[0]
                    more_span = (more[-1][4] - more[0][4]) if more else 0.0
                    push = more_span + 1.5 * lead_k
                    if nxt["low"] - push >= nxt["limit"] - 0.01 and self.flow.move(nxt, -push):
                        lines, grow, fits = lines[:keep], here, True
                        self.notes["moved"] = len(nxt["lines"])
                        self.notes["linked"] = len(more)
                    else:
                        more = []
                    break
            if not fits and not found["lines"] and len(lines) > 1 and nxt is None and self.writer is not None:
                has_next = self.number + 1 < len(self.writer.pages)
                if has_next:
                    self.notes["can_next_page"] = True
                else:
                    self.notes["add_page"] = True
                if opts.get("fit") == "next_page" and has_next and self.doc is not None:
                    for keep in range(len(lines) - 1, 0, -1):
                        here = drop + lines[keep - 1][4] - old_span + 0.22 * (style["size"] - base["size"])
                        if found["low"] - here < found["limit"] - 0.01:
                            continue
                        words = rejoin(lines[keep:])
                        next_page = self.writer.pages[self.number + 1]
                        next_work = _Page(self.doc[self.number + 1], next_page, self.doc,
                                          self.number + 1, self.writer, self.pdfa)
                        next_work.strict = False
                        nxt_col = next_work.flow.top_column(left, orig_right, lead) if next_work.flow else None
                        if nxt_col:
                            col_w = max(nxt_col["x1"] - nxt_col["x0"], base["size"] * 4)
                            more_next = layout(style, words, col_w, indent=False)[0]
                            more_span = (more_next[-1][4] - more_next[0][4]) if more_next else 0.0
                            push = more_span + 1.5 * lead_k
                            moved_ok = (not nxt_col["lines"]) or (
                                nxt_col["low"] - push >= nxt_col["limit"] - 0.01
                                and next_work.flow.move(nxt_col, -push)
                            )
                            if more_next and moved_ok:
                                base_y = more_next[0][4]
                                for mn, (ml, mg, moff, _, my, mtc) in enumerate(more_next):
                                    next_work.line_no = mn
                                    emit(ml, moff, width, mtc, mg,
                                         lambda s, dy=my - base_y, nc=nxt_col: (nc["x0"] + s, nc["baseline"] - dy),
                                         target=next_work)
                                next_work.line_no = None
                                plain_more = words if isinstance(words, str) else "".join(ch for ch, _ in words)
                                next_work.texts.append((None, plain_more))
                                self.spills.append((self.number + 1, next_work, None))
                                self.written_text = rejoin(lines[:keep])
                                if isinstance(self.written_text, list):
                                    self.written_text = "".join(ch for ch, _ in self.written_text)
                                lines, grow, fits = lines[:keep], here, True
                                self.notes["next_page"] = len(more_next)
                                self.notes["moved_next_page"] = len(nxt_col["lines"])
                                self.notes.pop("can_next_page", None)
                                break
                        next_work.close()
                        break
                elif opts.get("fit") == "add_page" and not has_next:
                    for keep in range(len(lines) - 1, 0, -1):
                        here = drop + lines[keep - 1][4] - old_span + 0.22 * (style["size"] - base["size"])
                        if found["low"] - here < found["limit"] - 0.01:
                            continue
                        words = rejoin(lines[keep:])
                        more_new = layout(style, words, room, indent=False)[0]
                        if more_new:
                            new_runs = []

                            class _Collector:
                                def run(self_, st, f, entries):
                                    own, name = f[:2]
                                    new_runs.append({"style": st, "own": own, "name": name, "entries": entries})

                            top_y = float(self.page.cropbox.top) - 72.0 - lead_k * 0.8
                            base_y = more_new[0][4]
                            col_target = _Collector()
                            for ml, mg, moff, _, my, mtc in more_new:
                                emit(ml, left + moff, width, mtc, mg,
                                     lambda s, dy=my - base_y: (s, top_y - dy),
                                     target=col_target)
                            self.spills.append((len(self.writer.pages), None, new_runs))
                            self.written_text = rejoin(lines[:keep])
                            if isinstance(self.written_text, list):
                                self.written_text = "".join(ch for ch, _ in self.written_text)
                            lines, grow, fits = lines[:keep], here, True
                            self.notes["added_page"] = len(more_new)
                            self.notes.pop("add_page", None)
                        break
            if not fits and opts.get("fit") == "shrink":
                for step in range(1, 11):  # down to three quarters of the size, a little at a time
                    smaller = _restyle(style, {"size": style["size"] * (1 - 0.025 * step)})
                    attempt = layout(smaller)
                    if found["low"] - attempt[5] >= found["limit"] - 0.01:
                        style, (lines, width, tck, lead_k, drop, grow), fits = smaller, attempt, True
                        self.notes["size"] = round(smaller["size"], 2)
                        break
            if not fits:
                self.notes["overflow"] = True  # nothing moves: never pushed off the page
                # ... and never drawn over what's below it either.
                was = style["size"]
                style, (lines, width, tck, lead_k, drop, grow), _ = squeeze(found, style, (lines, width, tck, lead_k, drop, grow))
                if style["size"] != was:
                    self.notes["size"] = round(style["size"], 2)
            elif grow < 0 and not taken:
                pass  # the old words are covered: what's below can't come up under the cover
            elif self.flow.move(found, -grow):
                if found["lines"]:
                    self.notes["moved"] = self.notes.get("moved", 0) + len(found["lines"])
            elif grow > 0:
                self.notes["taller"] = True
        elif grow > 0.5:
            # Runs on below where it ended, and nothing moves: kept above what follows it.
            fitted = False
            if self.flow is not None and self.flow.content:
                was = style["size"]
                style, (lines, width, tck, lead_k, drop, grow), fitted = squeeze(
                    self.flow.column(rows, lead), style, (lines, width, tck, lead_k, drop, grow))
                if style["size"] != was:
                    self.notes["size"] = round(style["size"], 2)
            self.notes["taller"] = True
            del fitted  # said either way: it takes more lines than it did
        for n, (line, g, off, _, y_off, line_tc) in enumerate(lines):
            d = drop + y_off
            point = (first["origin"][0] + u[0] * (-first["s"]) + down[0] * d,
                     first["origin"][1] + u[1] * (-first["s"]) + down[1] * d)
            self.line_no = n
            emit(line, left + off, width, line_tc, g, lambda s, p=point: (p[0] + u[0] * s, p[1] + u[1] * s))
        more_base = more[0][4] if more else 0.0
        for n, (line, g, off, _, y_off, line_tc) in enumerate(more):  # level text only: the paragraph is level
            self.line_no = len(lines) + n
            emit(line, off, width, line_tc, g, lambda s, dy=y_off - more_base: (nxt["x0"] + s, nxt["baseline"] - dy))
        self.line_no = None
        if looks and links:
            self._move_links(links, linked)
        return font

    def _links_over(self, letters):
        """The page's links and text markups (highlight, underline, strike
        out) over any of ``letters``: [(annotation, its boxes: one per line
        from /QuadPoints, else its /Rect)]."""
        annots = self.page.get("/Annots") if self.page is not None else None
        if not annots:
            return []
        found = []
        for ref in annots.get_object():
            annot = ref.get_object()
            if annot.get("/Subtype") not in MARKUPS:
                continue
            try:
                r = [float(v) for v in annot["/Rect"]]
                q = [float(v) for v in annot.get("/QuadPoints", [])]
            except (KeyError, TypeError, ValueError):
                continue
            boxes = [(min(q[i:i + 8:2]), min(q[i + 1:i + 8:2]), max(q[i:i + 8:2]), max(q[i + 1:i + 8:2]))
                     for i in range(0, len(q) - 7, 8)]
            boxes = boxes or [(min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3]))]
            if any(_link_at([(annot, boxes)], L) is not None for L in letters):
                found.append((annot, boxes))
        return found

    def _move_links(self, links, linked):
        """Each link around its words' new places: one box per line (as
        /QuadPoints when the words now wrap), or the link goes when its
        words are gone."""
        for n, (annot, _) in enumerate(links):
            boxes = linked.get(n)
            if not boxes:
                annots = self.page["/Annots"].get_object()
                for i in range(len(annots) - 1, -1, -1):
                    if annots[i].get_object() is annot:
                        del annots[i]
                continue
            lines = {}
            for x0, y, x1, size in boxes:
                key = next((k for k in lines if abs(k - y) < size * 0.5), y)
                lines.setdefault(key, []).append((x0, x1, size))
            quads, rects = [], []
            for y, row in sorted(lines.items(), key=lambda kv: -kv[0]):
                size = max(r[2] for r in row)
                a, b = min(r[0] for r in row), max(r[1] for r in row)
                lo, hi = y - 0.25 * size, y + 0.9 * size
                rects.append((a, lo, b, hi))
                quads += [a, hi, b, hi, a, lo, b, lo]
            union = (min(r[0] for r in rects), min(r[1] for r in rects),
                     max(r[2] for r in rects), max(r[3] for r in rects))
            annot[NameObject("/Rect")] = ArrayObject([FloatObject(round(v, 3)) for v in union])
            markup = annot.get("/Subtype") != "/Link"
            if len(rects) > 1 or "/QuadPoints" in annot or markup:
                annot[NameObject("/QuadPoints")] = ArrayObject([FloatObject(round(v, 3)) for v in quads])
            if markup and "/AP" in annot:
                del annot["/AP"]  # its picture was of the old place: readers draw it from the boxes

    # -- helpers ------------------------------------------------------------------------
    def _set(self, lines, origin, style, font, tc, word, lead, align=None, room=None, ends=None):
        """Lines of new words from ``origin`` (the first one's baseline start),
        set left, centred or right in ``room`` (else in the widest line), or
        justified but for the lines in ``ends``."""
        width, space = _metrics(font[0], font[1], style)
        gap = word if word is not None else space + 2 * tc
        u, down = _unit(style["matrix"]), _down(style["matrix"])

        def measure(line):
            return _place(line, 0.0, width, tc, gap)[1] - tc

        if align in ("center", "right", "justify") and not room:
            room = max((measure(line) for line in lines), default=0.0)
        for n, line in enumerate(lines):
            off, g = 0.0, gap
            if align in ("center", "right"):
                free = room - measure(line)
                off = free / 2 if align == "center" else free
            elif align == "justify" and ends is not None and n not in ends and line.count(" "):
                extra = (room - measure(line)) / line.count(" ")
                if 0 <= extra <= style["size"] * 2:
                    g = gap + extra
            placed, _ = _place(line, off, width, tc, g)
            x, y = origin[0] + down[0] * n * lead, origin[1] + down[1] * n * lead
            self.run(style, font, [(ch, x + u[0] * s, y + u[1] * s) for ch, s in placed])

    def _redo(self, letters, rows, text, tc, word, opts=None, replaced=False):
        """Cover the old words and set the new ones from where they began."""
        opts = opts or {}
        everything = [L for row in rows for L in row]
        self.legacy = self.legacy or _rtl(text) or any(_rtl(L["char"]) for L in everything)
        if not (self.take_replaced(letters, text) if replaced else self.take(rows, text=text)):
            self.cover(_box(everything, everything))
        base = letters[0]["style"]
        style = _restyle(base, opts)
        font = self._font(style, base, text, opts)
        text = self._shown(text)
        k = style["size"] / base["size"]
        if text.strip():
            room = None
            if opts.get("align"):
                u = _unit(base["matrix"])
                room = max(L["s"] + L["adv"] for L in letters) - letters[0]["s"]
                room = room if room > 0 and abs(u[1]) < 1e-6 else None
            same = not any(key in opts for key in ("font", "bold", "italic"))
            self._set(text.split("\n"), letters[0]["origin"], style, font, tc * k,
                      word * k if word is not None and same else None, style["size"] * 1.2, opts.get("align"), room)
        return font

    def _natural(self, letters, tc):
        """The width of a space in the line's own font (with the line's
        letter spacing), or None when the font has none to measure."""
        style = letters[len(letters) // 2]["style"]
        if self.flow is not None and not self.legacy and self.flow.content:
            for L in (letters[len(letters) // 2], letters[0], letters[-1]):
                natural = self.flow.space_width(L)
                if natural is not None:
                    return natural + tc
        width = ctypes.c_float()
        try:
            if style["handle"] and _raw().FPDFFont_GetGlyphWidth(style["handle"], 32, style["raw_size"],
                                                                  ctypes.byref(width)) and width.value > 0:
                return width.value * (math.hypot(*style["matrix"][:2]) or 1.0) + 2 * tc
        except (AttributeError, TypeError, ValueError, OverflowError):
            pass
        return None

    def _respell(self, letters, before, after, tc, word):
        """Change a line, leaving what didn't change where it was: only the
        letters from the first change on are new, and those after the last
        change move by the difference in width."""
        p = 0
        while p < min(len(before), len(after)) and before[p] == after[p]:
            p += 1
        s = 0
        while s < min(len(before), len(after)) - p and before[-1 - s] == after[-1 - s]:
            s += 1
        # Words put in between two others: the space before them is theirs
        # ("required| and enforced| within", not "required |and enforced |within"),
        # so they look like the word they follow (bold after a bold word).
        at = _take(letters, _nonspace(before[:p]))
        if at and at < len(letters) and letters[at - 1]["style"] is not letters[at]["style"]:
            # ... when that word stands out from the line (bold, coloured) and
            # the one after doesn't: between a plain word and one that
            # stands out, they go with the one that stands out too.
            usual = max({id(L["style"]): L["style"] for L in letters}.values(),
                        key=lambda st: sum(1 for L in letters if L["style"] is st))
            if letters[at]["style"] is usual:
                while 0 < p == len(before) - s and before[p - 1].isspace() and len(after) - s > p \
                        and after[len(after) - s - 1].isspace() and _nonspace(before[:p]):
                    p, s = p - 1, s + 1
        count = sum(len(L["char"]) for L in letters)
        keep = _take(letters, _nonspace(before[:p]))
        tail = _take(letters, count - _nonspace(before[len(before) - s:]))
        if keep is None or tail is None or tail < keep:
            raise _Unsure
        u = _unit(letters[0]["style"]["matrix"])
        middle = after[p:len(after) - s]
        if p < len(before) and not before[p].isspace() and keep < len(letters):
            anchor = letters[keep]
            start = anchor["s"]
        elif keep > 0:
            anchor = letters[keep - 1]
            start = anchor["s"] + anchor["adv"] + tc
        else:
            anchor = letters[0]
            start = anchor["s"]
        style = anchor["style"]
        font = self._choose(style, middle)
        width, space = _metrics(font[0], font[1], style)
        # Only the letters the line's own font lacks are written in another.
        by = self._pieces(style, font, middle)
        if by:
            widths = {id(f): _metrics(f[0], f[1], style) for f in by.values()}
            plain = width
            first = next((f for f in by.values() if f[2] == SAME), None)
            if first:
                space = widths[id(first)][1]

            def width(ch):
                return widths[id(by[ch])][0](ch) if ch in by else plain(ch)
        left_out = self.notes.get("missing", "")
        if left_out:
            middle = "".join(ch for ch in middle if ch not in left_out)
        gap = word if word is not None else space + 2 * tc
        placed, pen = _place(middle, start, width, tc, gap)
        shift = 0.0
        if tail < len(letters):
            b = len(before) - s
            if before[b].isspace() and tail > 0:
                was = letters[tail - 1]["s"] + letters[tail - 1]["adv"] + tc
            else:
                was = letters[tail]["s"]
            shift = pen - was
        # A justified line (word gaps wider than a space) keeps its right
        # edge: the gaps after the change take up the difference.
        moves = [shift] * (len(letters) - tail)
        # Justified: its word gaps are wider than a space of the line's own
        # font (not of the font the new words got), and those gaps are real
        # spaces stretched (word spacing), or a line above or below starts
        # and ends where this one does. Gaps set as moves between the words
        # of a ragged line don't make it justified.
        context = self._context(letters)
        natural = self._natural(letters, tc)
        own_space = natural if natural is not None else space + 2 * tc
        between = [L for L in letters[1:] if L.get("space_before")]
        real = bool(between) and sum(1 for L in between if L.get("explicit_space")) * 2 > len(between)
        if natural is None:
            stretched = word is not None and context == "justify"
        else:
            stretched = word is not None and word - natural > max(0.2, natural * 0.08) \
                and (real or context == "justify")
        old_end = letters[-1]["s"] + letters[-1]["adv"]
        growth = shift if tail < len(letters) else pen - tc - old_end
        gaps = [k for k, L in enumerate(letters[tail:]) if L.get("space_before")]
        absorbed = abs(shift) > MOVED and stretched and bool(gaps)  # the gaps after the change take it up
        if absorbed:
            each = shift / len(gaps)
            narrowest = min(letters[tail + k]["s"] - letters[tail + k - 1]["s"] - letters[tail + k - 1]["adv"]
                            for k in gaps if tail + k > 0) if any(tail + k > 0 for k in gaps) else word
            # Words can come closer only so far (and spread only so far).
            each = min(each, max(0.0, narrowest - own_space * 0.7))
            each = max(each, word - own_space * 4)
            if shift - each * len(gaps) > 1.0:
                self.notes["wider"] = True  # still past its right edge: rewriting the paragraph re-wraps it
            if each:
                done = 0
                for k in range(len(moves)):
                    done += k in gaps
                    moves[k] = shift - each * done
        # A line set to the right keeps its right end, a centred one its
        # middle: the words before the change move left to make room.
        part = 0.0
        anchored = context in ("right", "center") and abs(growth) > MOVED and self.flow is not None and not absorbed
        if anchored:
            part = growth if context == "right" else growth / 2
        elif growth > 0.5 and not absorbed and self.flow is not None and not self.legacy \
                and abs(letters[0]["style"]["angle"]) < 1:
            if old_end + growth > self._beside(letters)[1]:
                self.notes["wider"] = True  # into the words beside it, or off the page
        ox, oy = anchor["origin"]

        def at(d):
            return ox + u[0] * (d - anchor["s"]), oy + u[1] * (d - anchor["s"])

        def entries(group, text, moves, partial=False):
            """The letters of ``group`` (spelling ``text``, or its start when
            ``partial``) as they are, each moved along by its ``moves``."""
            out, i, used = [], 0, 0
            for ch in text:
                if partial and i == len(group):
                    break
                if ch.isspace():
                    if out and not out[-1][0].isspace():
                        last = out[-1]
                        out.append((" ", last[1] + u[0] * (last[3] + tc), last[2] + u[1] * (last[3] + tc),
                                    0.0, last[4]))
                    continue
                if i >= len(group):
                    raise _Unsure
                used += 1
                if used == len(group[i]["char"]):
                    L, move = group[i], moves[i]
                    out.append((L["char"], L["origin"][0] + u[0] * move, L["origin"][1] + u[1] * move,
                                L["adv"], L["style"]))
                    i, used = i + 1, 0
            if i != len(group) or used:
                raise _Unsure
            return out

        def runs(items):
            """Consecutive letters drawn the same way: [(style, [entries], font)]."""
            out = []
            for item in items:
                if out and out[-1][0] is item[4]:
                    out[-1][1].append(item)
                else:
                    out.append((item[4], [item]))
            return [(st, group, self._choose(st, "".join(e[0] for e in group))) for st, group in out]

        # Drawn again with the change: the letters joined to it in the same
        # look (so the line stays one piece of text, for copying and the next
        # change) and every letter that moves. A piece in another look that
        # stays put is left alone, uncovered: drawn again exactly over
        # itself, a reader such as pdfium takes the copy for a duplicate.
        moved = abs(shift) > MOVED
        start = keep
        while start > 0 and letters[start - 1]["style"] is style:
            start -= 1
        end = len(letters) if moved else tail
        while not moved and end < len(letters) and letters[end]["style"] is style:
            end += 1
        head_runs = runs(entries(letters[start:keep], _skip(after[:p], sum(len(L["char"]) for L in letters[:start])),
                                 [0.0] * (keep - start)))
        rest_runs = runs(entries(letters[tail:end], after[len(after) - s:], moves, partial=True))
        if not all(f[0] for _, _, f in head_runs):  # not to be had in its own font: stays as it is
            start, head_runs = keep, []
        if not moved and not all(f[0] for _, _, f in rest_runs):
            end, rest_runs = tail, []
        new = [(ch, *at(d), 0.0, style) for ch, d in placed]
        writes = any(not ch.isspace() for ch in middle)
        same = (None, None, SAME, fonts.display_name(style["font"]))

        def pieces(shifted):
            """The new letters in runs of one font each: [(font, entries)]."""
            out = []
            for ch, d in placed:
                if ch.isspace() and out:
                    f = out[-1][0]  # a space goes with the word before it
                else:
                    f = (by or {}).get(ch, font)
                if not out or out[-1][0] is not f:
                    out.append((f, []))
                out[-1][1].append((ch, *at(d - shifted)))
            return out

        if not self.legacy and not _rtl(before + after) and self._in_place(
                letters, keep, tail, anchor, before, p, s, pieces(part) if writes else [], style,
                [m - part for m in moves], -part, growth - part, after):
            if anchored and (letters[0]["s"] - part < self._beside(letters)[0]
                             or old_end + growth - part > self._beside(letters)[1]):
                self.notes["wider"] = True
            return font if writes else same
        covered = letters[start:end]
        if not covered and not writes:
            return font
        if covered:
            self.cover(_box(covered, letters, letters[start - 1] if start > 0 else None,
                            letters[end] if end < len(letters) else None))
        for st, group, f in head_runs:
            self.run(st, f, [e[:3] for e in group])
        for f, group in pieces(0.0) if new else ():  # continues the run before it on the line, for copying
            self.run(style, f, group)
        for st, group, f in rest_runs:
            self.run(st, f, [e[:3] for e in group])
        if writes:
            return font
        worst = next((f for _, _, f in rest_runs if not f[0]), None) if moved else None
        return worst or (None, None, SAME, fonts.display_name(style["font"]))


    def _in_place(self, letters, keep, tail, anchor, before, p, s, pieces, style, moves, head=0.0, after=0.0,
                  text=None):
        """Make a line's change in the page's own drawing: the letters
        that go are taken out of their pieces of text, the new ones written
        there, and the letters after them moved along. Nothing is covered
        and nothing drawn twice. False (nothing changed) when any of that
        can't be done for sure."""
        flow = self.flow
        if flow is None or not flow.content:
            return False
        saved = flow.checkpoint()
        ok = flow.uncover(letters)
        spot = flow.glyph(anchor) if ok else None
        retag = flow.tags(letters, text) if spot is not None else None
        ok = retag is not None and flow.remove(letters[keep:tail])
        if ok:
            flow.retag.update(retag)
        if ok and keep > 0 and tail < len(letters):
            # Spaces that went with the old words (not those kept before and after them).
            old = before[p:len(before) - s]
            lead = len(before[:p]) - len(before[:p].rstrip())
            trail = len(before[len(before) - s:]) - len(before[len(before) - s:].lstrip())
            flow.remove_spaces(letters[keep - 1], letters[tail], lead, trail, sum(ch.isspace() for ch in old))
        for L, move in list(zip(letters[tail:], moves)) + [(L, head) for L in letters[:keep]]:
            if ok and abs(move) > MOVED:
                ok = flow.shift(L, move)
        ahead = anchor is letters[keep] if keep < len(letters) else False
        for font, entries in pieces:
            own = font[0]
            if own is not None and own.space:
                entries = [e for e in entries if not e[0].isspace()]
            if ok and entries:
                ok = flow.insert(spot, ahead, {"own": own, "name": font[1], "style": style, "entries": entries,
                                               "same": True, "color": None})
        if not ok:
            flow.rollback(saved)
            return False
        if abs(letters[0]["style"]["angle"]) < 1:
            # A mark starts where a letter starts and ends where one ends.
            starts = [(L["s"], head) for L in letters[:keep]] + [(L["s"], m) for L, m in zip(letters[tail:], moves)]
            ends = [(L["s"] + L["adv"], head) for L in letters[:keep]] \
                + [(L["s"] + L["adv"], m) for L, m in zip(letters[tail:], moves)]
            if keep < tail:  # where the old words began and ended
                starts.append((letters[keep]["s"], head))
                ends.append((letters[tail - 1]["s"] + letters[tail - 1]["adv"], moves[0] if len(moves) else after))

            def delta(x, tol, right=False):
                """How far the letter edge at ``x`` moved with the change."""
                edges = ends if right else starts
                at, move = min(edges, key=lambda e: abs(e[0] - x)) if edges else (x + 2 * tol, 0.0)
                return move if abs(at - x) <= tol else None

            flow.follow(letters, spot, delta)
        return True


def edit_text(path, output, edits, password=None, report=None):
    """Change words on pages. ``edits`` is a list of {"page" (1-based), and
    either "rect": [x0, y0, x1, y1] around the old words in PDF points (with
    "paragraph": true to rewrite the lines in it as one paragraph that
    wraps; else "old" may give the words as shown, to match the new ones
    against), or "at": [x, y] for new words whose top left is there (and
    "width" to wrap them in); and "text": the new words (empty to take the
    old ones away)}.

    A change of one line changes that line only. A line set to the right
    or centred keeps its right end or its middle; one that grows into
    what's beside it (or off the page) is reported as "wider"; one taken
    away leaves its space. Only a paragraph change wraps and moves text.

    How the new words look can be chosen too: "font" (a family: one of the
    document's, an installed one, or Helvetica, Times or Courier), "size"
    (points), "bold", "italic", "color" ([r, g, b] from 0 to 1) and "align"
    ("left", "center", "right", "justify"). A rewritten paragraph that gets
    taller or shorter moves the text below it in its column ("flow": false
    to leave it); when that doesn't fit on the page nothing moves and the
    report says "overflow", unless "fit": "shrink" lets the paragraph's
    size come down a little to fit. A paragraph that grows where nothing
    below it can move is never drawn over what follows: it is set smaller
    and tighter, as little as needed, and the report gives its "size".

    Returns how many edits were made; ``report``, a list, gets for each the
    font used ("same", "installed", "standard" or "similar") and its name,
    with "asked" (the family asked for) and "matched" (whether it was the
    one used), "moved" (lines moved), "overflow", "size" (shrunk to),
    "wider" and "taller" when they apply; "covered" when the old words
    could only be covered (they are still in the file under the cover), and
    "missing" (the letters no font here can draw, left out)."""
    output = Path(output)
    if output.is_dir():
        raise core.PdfManagerError(f"{output} is a folder; give the name of the PDF to write")
    if output.parent and not output.parent.exists():
        output.parent.mkdir(parents=True, exist_ok=True)
    # The changes are made in the pages' own drawing. Before the result is
    # kept it is read back: every letter no change was about must still be
    # there, where it was. If one isn't (a drawing this misreads), the
    # changes are made the old way instead, which touches nothing: the old
    # words covered, the new ones drawn on top.
    fd, tmp = tempfile.mkstemp(prefix=output.name + ".", suffix=".check", dir=output.parent)
    os.close(fd)

    def attempt(some, old_way):
        """Make ``some`` changes; (how many, the notes, whether what was
        written reads back as it should)."""
        notes = []
        try:
            count, expect = _edit(path, tmp, some, password, notes, old_way)
            return count, notes, old_way is True or _intact(tmp, expect)
        except core.PdfManagerError:
            raise  # a change that can't be made either way (no such page, ...)
        except Exception:  # noqa: BLE001 - a drawing the new way can't handle
            if old_way is True:
                raise
            return 0, notes, False

    try:
        count, notes, sound = attempt(edits, frozenset())
        if not sound and len(edits) > 1:
            # Which of the changes it was: each is tried alone, and only
            # those that fail are made the old way.
            order = _order(edits)
            bad = frozenset(n for n, k in enumerate(order) if not attempt([edits[k]], frozenset())[2])
            if bad:
                count, notes, sound = attempt(edits, bad)
        if not sound:
            count, notes, _ = attempt(edits, True)
        os.replace(tmp, output)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    if report is not None:
        report += notes
    return count


def _order(edits):
    """The edits' indices in the order they are made: page by page (in the
    order the pages first come up), each page's in the order given."""
    pages = []
    for edit in edits:
        if int(edit.get("page", 0)) not in pages:
            pages.append(int(edit.get("page", 0)))
    return sorted(range(len(edits)), key=lambda k: pages.index(int(edits[k].get("page", 0))))


def _intact(path, expect):
    """Whether the file written reads back as it should (see
    :meth:`_Page.expected`): every letter no change was about is there
    once, at its place, in its font and size; where only lines were
    changed, those letters read in the order they did and no other letter
    has appeared anywhere but on the changed lines; and
    the new words can be read on the page."""
    import collections

    import pypdfium2 as pdfium

    raw = _raw()
    try:
        with core.PDFIUM_LOCK:
            doc = pdfium.PdfDocument(str(path))
            try:
                for number, want in expect.items():
                    page = doc[number]
                    textpage = page.get_textpage()
                    try:
                        covers = _Covers(page)
                        buf, flags = ctypes.create_string_buffer(256), ctypes.c_int()
                        have, everything = {}, []
                        for i in range(textpage.count_chars()):
                            ch = textpage.get_text_range(i, 1)
                            loose = textpage.get_charbox(i, loose=True)
                            if not ch or ch.isspace() or loose[2] <= loose[0] or loose[3] <= loose[1] \
                                    or covers.hides(textpage, i, loose):
                                continue
                            x, y = _origin(textpage, i)
                            raw.FPDFText_GetFontInfo(textpage.raw, i, buf, len(buf), ctypes.byref(flags))
                            letter = [ch, x, y, buf.value, raw.FPDFText_GetFontSize(textpage.raw, i), False,
                                      len(everything)]
                            everything.append(letter)
                            have.setdefault((ch, math.floor(x), math.floor(y)), []).append(letter)
                        last = -1
                        for ch, x, y, font, size in want["letters"]:
                            match = next((L for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                                          for L in have.get((ch, math.floor(x) + dx, math.floor(y) + dy), ())
                                          if not L[5] and abs(x - L[1]) < 0.15 and abs(y - L[2]) < 0.15
                                          and L[3] == font and abs(L[4] - size) < 0.05), None)
                            if match is None:
                                return False
                            match[5] = True
                            if want["strict"] and match[6] < last:
                                return False  # read in another order than before
                            last = match[6]
                        new = [L for L in everything if not L[5]]

                        def on(band, L):
                            """Whether the letter is on that line (however long the line now is)."""
                            x, y, ux, uy, size = band
                            return abs((L[2] - y) * ux - (L[1] - x) * uy) <= size + 1

                        if want["strict"] and any(not any(on(band, L) for band in want["bands"]) for L in new):
                            return False
                        for which, text in want["texts"]:
                            there = collections.Counter(L[0] for L in new if which is None or on(want["bands"][which], L))
                            # (pdfium reads a hyphen that ends a line as U+FFFE or U+0002, and a soft
                            # hyphen may come back as U+00AD: a word's own hyphen there is still its hyphen.)
                            read = unicodedata.normalize("NFKC", "".join(there.elements()))
                            for marker in ("\ufffe", "\u0002", "\u00ad"):
                                read = read.replace(marker, "-")
                            asked = collections.Counter(unicodedata.normalize("NFKC", "".join(text.split())))
                            if asked - collections.Counter(read):
                                return False
                    finally:
                        textpage.close()
                        page.close()
            finally:
                doc.close()
    except Exception:  # noqa: BLE001 - a file that can't be read back isn't one to keep
        return False
    return True


def _edit(path, output, edits, password, report, old_way):
    """Make the changes (those numbered in ``old_way``, or all if it is
    True, by covering); (how many, what each changed page must read as:
    {page index: :meth:`_Page.expected`})."""
    import pypdfium2 as pdfium

    reader = core.open_pdf(path, password)
    by_page = _parse(edits, len(reader.pages))
    pdfa = _is_pdfa(reader)
    expect, made = {}, 0
    writer = core.clone_document(reader)
    # Pages can share one content stream (after Compress, or a duplicated
    # page): the change must land on this page only.
    shared = core._shared_objects(writer)
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path), password=password)
        try:
            for number, page_edits in by_page.items():
                page = writer.pages[number - 1]
                core._own_contents(page, shared)
                work = _Page(doc[number - 1], page, doc, number - 1, writer, pdfa)
                try:
                    for kind, rect, text, old, opts in page_edits:
                        work.old_way = old_way is True or bool(old_way and made in old_way)
                        made += 1
                        lines = len(work.bands)
                        work.start()
                        if kind == "add":
                            font = work.add(rect[0], rect[1], text, opts)
                        elif kind == "paragraph":
                            font = work.paragraph(rect, text, opts)
                        else:
                            font = work.line(rect, text, old, opts)
                        work.settle()
                        work.strict = work.strict and kind == "line"
                        check_text = work.written_text if work.written_text is not None else text
                        if not work.legacy and kind != "add" and check_text.strip():
                            shown = "".join(ch for ch in check_text if ch not in work.notes.get("missing", ""))
                            line = kind == "line" and "\n" not in check_text and len(work.bands) > lines
                            work.texts.append((len(work.bands) - 1 if line else None, shown))
                        if report is not None:
                            drawn = any(not ch.isspace() for ch in text)
                            report.append({"font": font[3] if drawn else "", "kind": font[2] if drawn else SAME,
                                           **work.notes})
                    if work.flow is not None and work.flow.changed():
                        work.ready()
                        work.flow.write(writer, page)
                        work.runs += work.flow.orphans
                    _draw(writer, page, work.covers, work.runs)
                    if old_way is not True:
                        expect[number - 1] = work.expected()
                    for spill_idx, spill_work, new_runs in work.spills:
                        if spill_work is not None:
                            try:
                                spill_page = writer.pages[spill_idx]
                                core._own_contents(spill_page, shared)
                                if spill_work.flow is not None and spill_work.flow.changed():
                                    spill_work.ready()
                                    spill_work.flow.write(writer, spill_page)
                                    spill_work.runs += spill_work.flow.orphans
                                _draw(writer, spill_page, spill_work.covers, spill_work.runs)
                                if old_way is not True:
                                    expect[spill_idx] = spill_work.expected()
                            finally:
                                spill_work.close()
                        elif new_runs is not None:
                            new_page = writer.add_blank_page(
                                width=float(page.mediabox.width),
                                height=float(page.mediabox.height),
                            )
                            _draw(writer, new_page, [], new_runs)
                finally:
                    work.close()
        finally:
            doc.close()
    core._write(writer, output)
    return sum(len(p) for p in by_page.values()), expect


def paragraph_runs(path, page, rect, text, password=None):
    """How the words of ``text`` (a paragraph's words as the reader shows
    them) look on page ``page`` in ``rect``, for the reader's paragraph
    editor: [{"text", "bold", "italic", "color": [r, g, b]}], in order,
    their texts making up ``text``."""
    import pypdfium2 as pdfium

    reader = core.open_pdf(path, password)
    if not 1 <= int(page) <= len(reader.pages):
        raise core.PdfManagerError(f"page {page} doesn't exist")
    x0, y0, x1, y1 = (float(v) for v in rect)
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        raise core.PdfManagerError("the box around the words isn't on the page")
    rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path), password=password)
        try:
            pdf_page = doc[int(page) - 1]
            textpage = pdf_page.get_textpage()
            try:
                cache, hidden = {}, _Covers(pdf_page)  # (old words under an earlier change don't count)
                chars = _chars(textpage, rect, across=3.0)
                rows = [r for r in (_letters(textpage, row, cache, hidden) for row in _rows_with_marks(textpage, chars)) if r]
                if not rows:
                    return [{"text": text, "bold": False, "italic": False, "color": [0, 0, 0]}] if text else []
                looks = _char_styles(rows, text) or [(rows[0][0]["style"], None, 0.0)] * len(text)
            finally:
                textpage.close()
                pdf_page.close()
        finally:
            doc.close()
    import collections

    runs, styles = [], [st for st, _, _ in looks]
    common = collections.Counter(_look(st) for st, ch in zip(styles, text) if not ch.isspace()).most_common(1)
    common = common[0][0] if common else None
    for n, ch in enumerate(text):  # a space between words in two styles goes with the usual style
        if ch.isspace() and n + 1 < len(text) and _look(styles[n + 1]) == common:
            styles[n] = styles[n + 1]
    for ch, st in zip(text, styles):
        look = (st["bold"], st["italic"], [round(v, 4) for v in st["color"]])
        if runs and (runs[-1]["bold"], runs[-1]["italic"], runs[-1]["color"]) == look:
            runs[-1]["text"] += ch
        else:
            runs.append({"text": ch, "bold": look[0], "italic": look[1], "color": look[2]})
    return runs


def font_choices(path, password=None):
    """The fonts the reader offers for edited words: {"document": the
    families the document uses, "installed": common installed ones, "more":
    the other installed ones, "standard": the standard PDF fonts, "pdfa":
    whether the file is PDF/A}. A family is listed once, in the first group
    that has it (with the installed font's spelling, "DejaVu Sans")."""
    reader = core.open_pdf(path, password)
    found = {}
    for page in reader.pages[:200]:
        try:
            for font in _page_fonts(page):
                obj = font.get_object()
                if obj.get("/Subtype") != "/Type3":
                    name = fonts.family_name(obj.get("/BaseFont", ""))
                    if name and name.isprintable():
                        found.setdefault(fonts.family_key(name), name)
        except Exception:  # noqa: BLE001 - a page whose resources can't be read
            continue
    common, rest = fonts.installed_families()
    spelled = {fonts.family_key(n): n for n in common + rest}
    spelled.update({"helvetica": "Helvetica", "times": "Times", "courier": "Courier"})
    document = sorted((spelled.get(k, n) for k, n in found.items()), key=str.lower)

    def others(names):
        return [n for n in names if fonts.family_key(n) not in found]
    return {"document": document, "installed": others(common), "more": others(rest),
            "standard": others(["Helvetica", "Times", "Courier"]), "pdfa": _is_pdfa(reader)}


# ---- drawing --------------------------------------------------------------------------------

def _draw(writer, page, covers, runs):
    box = page.mediabox
    left, bottom = float(box.left), float(box.bottom)
    others = [r for r in runs if not r["own"]]

    def draw(c, w, h):
        for (x0, y0, x1, y1), colour in covers:
            c.setFillColorRGB(*colour)
            c.rect(x0 - left, y0 - bottom, x1 - x0, y1 - y0, fill=1, stroke=0)
        for run in others:
            style = run["style"]
            a, b, cc, d = style["matrix"]
            for ch, x, y in run["entries"]:
                if ch.isspace():
                    continue
                c.saveState()
                c.transform(a, b, cc, d, x - left, y - bottom)
                c.setFillColorRGB(*style["color"])
                c.setFont(run["name"], style["raw_size"])
                c.drawString(0, 0, ch)
                c.restoreState()

    if covers or others:
        page.merge_transformed_page(core._overlay_page(float(box.width), float(box.height), draw),
                                    Transformation().translate(left, bottom))
    own = [r for r in runs if r["own"]]
    if own:
        _write_with_own_fonts(writer, page, own)


def _num(v):
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


def _write_with_own_fonts(writer, page, runs):
    """Add the words drawn with the document's fonts as one more content
    stream, after everything else on the page; each letter exactly where
    it was placed."""
    resources = page.get("/Resources")
    resources = DictionaryObject() if resources is None else resources.get_object()
    page[NameObject("/Resources")] = resources
    font_dict = resources.get("/Font")
    font_dict = DictionaryObject() if font_dict is None else font_dict.get_object()
    resources[NameObject("/Font")] = font_dict
    ops, names = ["q", "BT", "0 Tc", "0 Tw", "100 Tz", "0 Ts", "0 Tr"], {}
    for run in runs:
        own, style = run["own"], run["style"]
        key = id(own.font.get_object())
        if key not in names:
            n = 1
            while f"/ZPDF{n}" in font_dict:
                n += 1
            names[key] = f"/ZPDF{n}"
            font_dict[NameObject(names[key])] = own.font
        a, b, c, d = style["matrix"]
        scale = (math.hypot(a, b) or 1.0) * style["raw_size"]
        u = _unit(style["matrix"])
        entries = run["entries"]
        x0, y0 = entries[0][1], entries[0][2]
        ops += [" ".join(_num(v) for v in style["color"]) + " rg",
                f"{names[key]} {_num(style['raw_size'])} Tf",
                " ".join(_num(v) for v in (a, b, c, d, x0, y0)) + " Tm"]
        parts, pen = [], 0.0
        for ch, x, y in entries:
            if ch.isspace() and own.space:
                continue  # no space in the font: the next letter's place makes it
            s = (x - x0) * u[0] + (y - y0) * u[1]
            move = (s - pen) / scale * 1000  # in thousandths of the font size
            if abs(move) > 0.001:
                parts.append(_num(-move))
            code = b"".join(own.codes[c] for c in ch)
            parts.append("<" + code.hex() + ">")
            pen = s + sum(own.width_of(own.codes[c]) for c in ch) / 1000 * scale
        ops.append("[" + " ".join(parts) + "] TJ")
    ops += ["ET", "Q"]

    def stream(text):
        obj = DecodedStreamObject()
        obj.set_data(text.encode("latin-1"))
        return writer._add_object(obj)

    contents = page.get("/Contents")
    if contents is None:
        parts = []
    elif isinstance(contents.get_object(), ArrayObject):
        parts = list(contents.get_object())
    else:
        parts = [contents if hasattr(contents, "idnum") else writer._add_object(contents)]
    # The page's own drawing in q ... Q, so whatever it leaves set (a
    # transformation, a colour) doesn't move or tint the new words.
    page[NameObject("/Contents")] = ArrayObject([stream("q"), *parts, stream("Q"), stream("\n".join(ops))])

