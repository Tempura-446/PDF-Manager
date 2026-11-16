# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.47.0 (markup.py): a markup changed after it's placed. Moved, reshaped,
recoloured or reworded, it is drawn again by the tool that drew it and
stays the same annotation: its place, its name, its maker and its replies."""

import pytest
from pypdf import PdfReader

from pdfmanager import core, markup
from test_construction_markups import drawn, ink, sheet

QUARTER = {"ratio": 48, "units": "ft", "label": "1/4\" = 1'-0\""}


def ident(ref):
    """pdf.js's name for an annotation (None for one written inside the page's list, like a note's popup)."""
    return f"{ref.idnum}R" if hasattr(ref, "idnum") else None


def page_annots(path):
    reader = PdfReader(str(path))
    refs = list(reader.pages[0]["/Annots"])
    return refs, [r.get_object() for r in refs]


def placed(tmp_path, *changes):
    """A sheet with these markups on it: (path, [id of each])."""
    out = tmp_path / "placed.pdf"
    markup.apply(sheet(tmp_path / "sheet.pdf"), out, [{"op": "add", "page": 1, **c} for c in changes], author="Ada")
    refs, _ = page_annots(out)
    return out, [ident(r) for r in refs]


def changed(tmp_path, src, *changes, name="changed.pdf"):
    out = tmp_path / name
    markup.apply(src, out, [{"op": "change", "page": 1, **c} for c in changes], author="Bob")
    return out, page_annots(out)[1]


def test_a_rectangle_moved_resized_and_recoloured_stays_the_same_annotation(tmp_path):
    src, (box, line) = placed(tmp_path, {"kind": "rect", "rect": [100, 500, 200, 560], "color": "#d52b1e", "width": 2,
                                         "text": "Check this"},
                              {"kind": "line", "points": [[100, 400], [300, 400]]})
    before = page_annots(src)[1][0]
    out, (rect, other) = changed(tmp_path, src, {"id": box, "move": [50, -100]})
    assert rect["/Subtype"] == "/Square" and other["/Subtype"] == "/Line"          # same place in the page's order
    assert [round(float(v)) for v in rect["/Rect"]] == [149, 399, 251, 461]        # the box and half its line width
    assert rect["/NM"] == before["/NM"] and rect["/T"] == "Ada" and rect["/CreationDate"] == before["/CreationDate"]
    assert rect["/Contents"] == "Check this" and rect["/M"] != ""
    assert ink(out, [148, 398, 252, 462]) > 0.01 and ink(out, [98, 498, 202, 562]) == 0   # drawn there, gone from here
    assert len(page_annots(out)[0]) == 2                                            # nothing added beside it

    out, (rect, _) = changed(tmp_path, src, {"id": box, "rect": [120, 480, 320, 600], "color": "#1f4fd8", "width": 4,
                                             "text": "Bigger"}, name="b.pdf")
    assert [round(float(v)) for v in rect["/Rect"]] == [118, 478, 322, 602]
    assert [round(float(c), 2) for c in rect["/C"]] == [0.12, 0.31, 0.85] and float(rect["/BS"]["/W"]) == 4
    assert rect["/Contents"] == "Bigger" and "4 w" in drawn(rect)


def test_each_kind_reads_back_as_what_drew_it(tmp_path):
    shapes = [
        {"kind": "rect", "rect": [100, 700, 180, 760]},
        {"kind": "ellipse", "rect": [200, 700, 280, 760]},
        {"kind": "cloud", "rect": [300, 700, 420, 760]},
        {"kind": "polycloud", "points": [[100, 600], [200, 620], [180, 680], [90, 660]]},
        {"kind": "line", "points": [[100, 500], [200, 520]]},
        {"kind": "arrow", "points": [[220, 500], [320, 520]]},
        {"kind": "polyline", "points": [[100, 400], [160, 440], [220, 400]], "arrow": "both"},
        {"kind": "callout", "tip": [120, 300], "at": [220, 340], "text": "Fire-rated door"},
        {"kind": "dimension", "points": [[100, 200], [244, 200]], "value": "8'-0\"", "scale": QUARTER},
        {"kind": "stamp", "rect": [300, 180, 460, 230], "label": "APPROVED"},
        {"kind": "note", "at": [480, 760], "text": "A note"},
    ]
    src, ids = placed(tmp_path, *shapes)
    annots = page_annots(src)[1]
    seen = [markup.describe(a) for a in annots if a.get("/Subtype") != "/Popup"]
    assert [s["kind"] for s in seen] == [s["kind"] for s in shapes] and set(markup.CHANGEABLE) == {s["kind"] for s in shapes}
    for want, got in zip(shapes, seen):
        for key in ("rect", "points", "tip", "at"):
            if key in want and want["kind"] not in ("stamp",) and not (key == "at" and want["kind"] == "callout"):
                flat = lambda v: [round(float(n), 1) for p in v for n in (p if isinstance(p, list) else [p])]  # noqa: E731
                assert flat(got[key]) == flat(want[key]), (want["kind"], key)
    by = {s["kind"]: s for s in seen}
    assert by["polyline"]["arrow"] == "both" and by["dimension"]["value"] == "8'-0\""
    assert by["dimension"]["scale"]["units"] == "ft" and round(by["dimension"]["scale"]["ratio"]) == 48
    assert by["callout"]["text"] == "Fire-rated door" and by["callout"]["tip"] == [120.0, 300.0]


def test_points_move_one_at_a_time(tmp_path):
    src, (cloud, poly, arrow) = placed(
        tmp_path, {"kind": "polycloud", "points": [[100, 600], [200, 620], [180, 680], [90, 660]]},
        {"kind": "polyline", "points": [[100, 400], [160, 440], [220, 400]], "arrow": "end"},
        {"kind": "arrow", "points": [[100, 300], [200, 300]]})
    out, (c, p, a) = changed(tmp_path, src,
                             {"id": cloud, "points": [[100, 600], [260, 600], [180, 680], [90, 660]]},
                             {"id": poly, "points": [[100, 400], [160, 470], [220, 400], [280, 430]]},
                             {"id": arrow, "points": [[100, 300], [200, 360]]})
    assert [float(v) for v in c["/Vertices"]][2:4] == [260, 600] and "/BE" in c
    assert len(p["/Vertices"]) == 8 and [str(e) for e in p["/LE"]] == ["/None", "/ClosedArrow"]
    assert [float(v) for v in a["/L"]] == [100, 300, 200, 360] and [str(e) for e in a["/LE"]][1] == "/ClosedArrow"
    assert float(c["/Rect"][2]) > 260 and float(a["/Rect"][3]) > 360                 # each rectangle follows its shape


def test_a_box_cloud_given_corners_becomes_a_cloud_of_points(tmp_path):
    src, (cloud,) = placed(tmp_path, {"kind": "cloud", "rect": [100, 500, 300, 600]})
    out, (c,) = changed(tmp_path, src, {"id": cloud, "points": [[100, 500], [300, 500], [340, 640], [100, 600]]})
    assert len(c["/Vertices"]) == 8 and float(c["/Vertices"][4]) == 340 and "/BE" in c
    out, (c,) = changed(tmp_path, src, {"id": cloud, "rect": [100, 500, 360, 600]}, name="r.pdf")   # still a box: its rect
    assert sorted({float(v) for v in c["/Vertices"][0::2]}) == [100, 360]


def test_a_dimension_takes_its_new_words_with_its_new_ends(tmp_path):
    src, (dim,) = placed(tmp_path, {"kind": "dimension", "points": [[100, 300], [244, 300]], "value": "8'-0\"",
                                    "scale": QUARTER})
    out, (d,) = changed(tmp_path, src, {"id": dim, "points": [[100, 300], [316, 300]], "value": "12'-0\""})
    assert [float(v) for v in d["/L"]] == [100, 300, 316, 300] and d["/Contents"] == "12'-0\"" and "(12'-0\")" in drawn(d)
    assert d["/IT"] == "/LineDimension" and d["/Measure"]["/R"] == QUARTER["label"]   # its scale goes with it
    with pytest.raises(core.PdfManagerError, match="new words"):
        changed(tmp_path, src, {"id": dim, "points": [[100, 300], [400, 300]]}, name="x.pdf")
    out, (d,) = changed(tmp_path, src, {"id": dim, "move": [0, 50]}, name="m.pdf")    # moved whole: the same length
    assert [float(v) for v in d["/L"]] == [100, 350, 244, 350] and d["/Contents"] == "8'-0\""


def test_a_callout_moves_its_tip_its_box_or_its_words(tmp_path):
    src, (call,) = placed(tmp_path, {"kind": "callout", "tip": [120, 300], "at": [220, 340], "text": "Fire-rated door"})
    out, (c,) = changed(tmp_path, src, {"id": call, "tip": [80, 260]})
    assert [float(v) for v in c["/CL"]][:2] == [80, 260] and c["/Contents"] == "Fire-rated door"
    out, (c,) = changed(tmp_path, src, {"id": call, "text": "60-minute fire door, self-closing"}, name="t.pdf")
    assert c["/Contents"] == "60-minute fire door, self-closing" and "(60-minute fire door," in drawn(c)
    out, (c,) = changed(tmp_path, src, {"id": call, "at": [300, 420]}, name="a.pdf")
    assert float(c["/Rect"][3]) > 420 and [float(v) for v in c["/CL"]][:2] == [120, 300]


def test_stamps_and_notes_move_and_a_stamp_resizes(tmp_path):
    src, ids = placed(tmp_path, {"kind": "stamp", "rect": [300, 180, 460, 230], "label": "APPROVED"},
                      {"kind": "note", "at": [480, 760], "text": "A note"})
    stamp, note = ids[0], ids[1]
    look = drawn(page_annots(src)[1][0])
    out, annots = changed(tmp_path, src, {"id": stamp, "rect": [100, 400, 420, 500]}, {"id": note, "move": [-100, -60]})
    s, n, popup = annots[0], annots[1], annots[2]
    assert [float(v) for v in s["/Rect"]] == [100, 400, 420, 500] and drawn(s) == look   # the same drawing, fitted
    assert [float(v) for v in n["/Rect"]] == [380, 680, 400, 700] and n["/Contents"] == "A note"
    assert popup["/Subtype"] == "/Popup" and float(popup["/Rect"][0]) == 400            # its popup goes with it
    assert ink(out, [100, 400, 420, 500]) > 0.01
    with pytest.raises(core.PdfManagerError, match="bigger"):
        changed(tmp_path, src, {"id": stamp, "rect": [100, 400, 104, 404]}, name="x.pdf")


def test_replies_stay_with_a_markup_that_changes(tmp_path):
    src, (box,) = placed(tmp_path, {"kind": "rect", "rect": [100, 500, 200, 560], "text": "Check this"})
    replied = tmp_path / "replied.pdf"
    markup.apply(src, replied, [{"op": "reply", "page": 1, "id": box, "text": "Done"}], author="Bob")
    box = ident(page_annots(replied)[0][0])                       # (a rewritten file numbers its objects anew)
    out, annots = changed(tmp_path, replied, {"id": box, "move": [40, 0]})
    rect, reply = annots
    assert reply["/Contents"] == "Done" and reply["/IRT"].get_object()["/NM"] == rect["/NM"]
    assert round(float(rect["/Rect"][0])) == 139


def test_what_cant_be_changed_says_so(tmp_path):
    src, ids = placed(tmp_path, {"kind": "underline", "rects": [[72, 795, 140, 810]]},
                      {"kind": "measure", "shape": "distance", "points": [[100, 300], [300, 300]], "value": "10 ft"},
                      {"kind": "rect", "rect": [100, 500, 200, 560]})
    for which in ids[:2]:
        with pytest.raises(core.PdfManagerError, match="ZPDF's own tools"):
            changed(tmp_path, src, {"id": which, "move": [5, 5]}, name="x.pdf")
    with pytest.raises(core.PdfManagerError, match="can't be found"):
        changed(tmp_path, src, {"id": "999R", "move": [5, 5]}, name="x.pdf")
    with pytest.raises(core.PdfManagerError, match="bigger"):
        changed(tmp_path, src, {"id": ids[2], "rect": [100, 500, 100.2, 500.2]}, name="x.pdf")
    # another program's annotation (no ZPDF name on it) is left alone
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, TextStringObject

    w = PdfWriter(clone_from=str(src))
    w.pages[0]["/Annots"][2].get_object()[NameObject("/NM")] = TextStringObject("ABC-123")
    w.write(str(tmp_path / "foreign.pdf"))
    theirs = ident(page_annots(tmp_path / "foreign.pdf")[0][2])
    with pytest.raises(core.PdfManagerError, match="ZPDF's own tools"):
        changed(tmp_path, tmp_path / "foreign.pdf", {"id": theirs, "move": [5, 5]}, name="x.pdf")


def test_the_file_holds_no_spare_copy(tmp_path):
    src, (box,) = placed(tmp_path, {"kind": "rect", "rect": [100, 500, 200, 560]})
    out, _ = changed(tmp_path, src, {"id": box, "move": [10, 10]})
    again, _ = changed(tmp_path, out, {"id": ident(page_annots(out)[0][0]), "move": [10, 10]}, name="again.pdf")
    data = again.read_bytes()
    assert data.count(b"/Subtype /Square") == 1 and len(page_annots(again)[0]) == 1
