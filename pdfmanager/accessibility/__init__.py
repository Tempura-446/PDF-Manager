# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Accessible PDFs: tags for screen readers, and a check of what stands in
their way (like Acrobat's Autotag and Accessibility Checker).

A *tagged* PDF carries, next to what's drawn on its pages, a structure tree
that says what each piece of content is (heading, paragraph, list, table,
figure, link) and in which order it reads. Screen readers, reflow on small
screens and conversions to Word or HTML rely on it; without it they guess
from the drawing and get columns, tables and running headers wrong. PDF/UA
(ISO 14289) is the standard for accessible PDFs.

    result = autotag(path, output)          # writes a tagged copy
    result = check(path)                    # what keeps it from being accessible
    print(report(result, path.name))

Tagging (``autotag``), page by page:

  * The layout is read from the page's text (pypdfium2: each character's
    box, size and weight) and its tables (pdfplumber, as for PDF → Excel).
    Text is put in reading order by cutting the page at its widest gaps of
    white space ("XY cut"): a gutter splits columns (left before right), a
    band across the page splits top from bottom.
  * Lines become paragraphs; larger or bold short ones become headings
    (H1-H6 by size, or by numbering such as "1.2"; levels never skip);
    lines that start with a bullet or a number become lists (Lbl + LBody);
    tables get rows and cells, with the first row as header cells (TH);
    pictures become figures, described by a caption next to them
    ("Figure 3: ...") or by the text given in ``alt``; text repeated at the
    top or bottom of the pages, and page numbers, become artifacts (not
    read out).
  * The page's drawing operators stay as they are and are wrapped in
    marked content: ``/P <</MCID 3>> BDC ... EMC`` around what belongs to an
    element, ``/Artifact BMC ... EMC`` around decoration (rules, borders,
    backgrounds, running headers). Each operator goes to the element whose
    text it overlaps most, found by following the page's graphics and text
    state (positions, fonts and their widths). Nothing drawn changes.
  * The structure tree, its ParentTree (from every MCID back to its
    element), /StructParents on pages and /StructParent on links, form
    fields and comments (tagged as Link, Form and Annot elements) are built
    with pikepdf, with MarkInfo /Marked, the language (/Lang), the title
    (shown in the window's title bar: DisplayDocTitle) and a tab order that
    follows the structure (/Tabs /S).

A form XObject drawn once (some programs draw each page as one) is tagged
inside, with its own /StructParents; one drawn several times is tagged as
a whole where it's drawn. Vector drawings (charts made of lines) are marked
as decoration, not figures. A file that is already tagged keeps its tags
(only the language, title and tab order are filled in) unless ``force`` is
given.

Checking (``check``) covers the basics of PDF/UA that Acrobat's checker
looks at (see RULES). Reading order and colour contrast can only be judged
by a person; the report says so.

``check`` result: {"file", "pages", "tagged", "lang", "title", "passed",
"issues": [{"rule", "message", "fix", "count", "pages"}], "note"}.
"""


from pdfmanager.accessibility.geometry import (
    BOOKMARK_PAGES, BULLETS, CAPTION, DEFAULT_LANG, LANG_RE, LANGUAGE_WORDS,
    MANUAL_NOTE, NUMBER_MARK, NUMBER_START, NUMBERED_HEADING, PAGE_NUMBER,
    RULES, TABLE_CAPTION, _View, _area, _bbox, _box_through, _distance,
    _inherited, _mul, _num, _overlap, _union,
)
from pdfmanager.accessibility.scanner import (
    DO_NAMES, _Font, _Scanner, _Unit, _form_has_images, _rewrite,
    _single_use_forms, _standard_font, _strip_marks, _strip_xobjects,
)
from pdfmanager.accessibility.layout import (
    _Block, _Item, _Line, _Seg, _aligned_grid, _body_size, _continues,
    _gaps, _lines, _mark_page_furniture, _marker, _may_have_tables,
    _normalise, _page_segments, _plumber_tables, _reading_order, _rows,
    _ruled_grid, _text_blocks, _text_direction, guess_language,
)
from pdfmanager.accessibility.tagger import (
    ARTIFACT, ARTIFACT_BACKGROUND, ARTIFACT_FOOTER, ARTIFACT_HEADER,
    PLACEHOLDER_TITLES, UNMARKED, _Elem, _PageTagger, _Targets,
    _add_bookmarks, _annot_box, _annotations, _clear_tags, _figures,
    _finish_document, _first_drawn, _grow, _heading_levels, _heading_style,
    _is_tagged, _link_text, _open_pdfium, _open_pikepdf, _parse_alt,
    _prune, _sample_text, _save, _set_title, _step, _tag_pages, _title_of,
    _unview, _walk, _write_tree, autotag, tag_summary,
)
from pdfmanager.accessibility.checker import (
    _Found, _TreeFacts, _attributes, _content_marks, _field_has_tooltip,
    _font_unmapped, _is_elem, _kids, _number_tree, _page_fonts, _plural,
    _text_pages, _worded, check, report, summary,
)
