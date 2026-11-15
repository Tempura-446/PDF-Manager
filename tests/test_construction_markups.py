# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.38.0 (markup.py): the markups builders and engineers put on drawings:
a revision cloud around any outline, a line or arrow with corners, a
callout (words in a box with a pointer), a dimension line carrying the
drawing's real length, and the review stamps of a submittal. Each is a
standard annotation, with the keys other programs read."""

import re

import pypdfium2 as pdfium
import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from pdfmanager import core, markup


def sheet(path, rotate=0):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawString(72, 800, "Sheet A-101")
    c.showPage()
    c.save()
    if rotate:
        w = PdfWriter(clone_from=str(path))
        w.pages[0].rotate(rotate)
        w.write(str(path))
    return path


def added(tmp_path, change, rotate=0):
    src, out = sheet(tmp_path / "sheet.pdf", rotate), tmp_path / "out.pdf"
    markup.apply(src, out, [{"op": "add", "page": 1, **change}], author="Ada")
    annots = [a.get_object() for a in PdfReader(str(out)).pages[0]["/Annots"]]
    return out, annots


def drawn(annot):
    return annot["/AP"]["/N"].get_data().decode("latin-1")


def inside(rect, x, y, slack=0.5):
    x0, y0, x1, y1 = (float(v) for v in rect)
    return x0 - slack <= x <= x1 + slack and y0 - slack <= y <= y1 + slack


def ink(path, box):
    """The share of dark pixels in ``box`` (PDF points) of the rendered page."""
    page = pdfium.PdfDocument(str(path))[0]
    image = page.render(scale=2).to_pil().convert("L")
    height = page.get_height()
    x0, y0, x1, y1 = box
    crop = image.crop((int(x0 * 2), int((height - y1) * 2), int(x1 * 2), int((height - y0) * 2)))
    data = list(crop.getdata())
    return sum(1 for v in data if v < 200) / max(1, len(data))


def test_a_cloud_around_any_outline(tmp_path):
    points = [[100, 500], [300, 520], [340, 640], [200, 700], [90, 600]]
    out, (cloud,) = added(tmp_path, {"kind": "polycloud", "points": points, "color": "#d52b1e", "width": 2})
    assert cloud["/Subtype"] == "/Polygon" and cloud["/BE"]["/S"] == "/C"
    assert [float(v) for v in cloud["/Vertices"]] == [v for p in points for v in p]
    assert all(inside(cloud["/Rect"], x, y) for x, y in points)
    assert drawn(cloud).count(" c") >= 20              # scallops, not straight sides
    assert cloud["/T"] == "Ada"
    # the bumps bulge out of the outline whichever way round it was clicked
    backwards, (other,) = added(tmp_path, {"kind": "polycloud", "points": points[::-1], "width": 2})
    assert [round(float(v), 1) for v in other["/Rect"]] == [round(float(v), 1) for v in cloud["/Rect"]]
    assert ink(out, [80, 490, 350, 710]) > 0.005
    with pytest.raises(core.PdfManagerError, match="three"):
        added(tmp_path, {"kind": "polycloud", "points": points[:2]})


def test_a_line_with_corners_and_arrowheads(tmp_path):
    points = [[100, 400], [200, 400], [200, 300], [320, 300]]
    _, (line,) = added(tmp_path, {"kind": "polyline", "points": points, "width": 1.5})
    assert line["/Subtype"] == "/PolyLine" and "/LE" not in line
    assert [float(v) for v in line["/Vertices"]] == [v for p in points for v in p]
    assert drawn(line).count(" l") == 3 and " f" not in drawn(line)
    _, (arrow,) = added(tmp_path, {"kind": "polyline", "points": points, "arrow": "end"})
    assert [str(v) for v in arrow["/LE"]] == ["/None", "/ClosedArrow"] and " f" in drawn(arrow)
    _, (both,) = added(tmp_path, {"kind": "polyline", "points": points, "arrow": "both"})
    assert [str(v) for v in both["/LE"]] == ["/ClosedArrow", "/ClosedArrow"]
    assert drawn(both).count(" h f") == 2
    with pytest.raises(core.PdfManagerError, match="two"):
        added(tmp_path, {"kind": "polyline", "points": points[:1]})


def test_a_callout(tmp_path):
    out, (note,) = added(tmp_path, {"kind": "callout", "tip": [150, 500], "at": [300, 600],
                                    "text": "Move this door 300 mm to the left (see RFI 12)", "color": "#1f4fd8"})
    assert note["/Subtype"] == "/FreeText" and note["/IT"] == "/FreeTextCallout"
    assert note["/Contents"] == "Move this door 300 mm to the left (see RFI 12)"
    leader = [float(v) for v in note["/CL"]]
    assert len(leader) == 6 and leader[:2] == [150, 500]
    assert note["/LE"] == "/ClosedArrow"
    assert "/DA" in note and "Tf" in note["/DA"]
    rect = [float(v) for v in note["/Rect"]]
    assert inside(rect, 150, 500) and inside(rect, 300, 600)
    # the words sit in a box to the right of the point they were put at, on more than one line
    x0, y0, x1, y1 = rect
    inner = [float(v) for v in note["/RD"]]
    box = [x0 + inner[0], y0 + inner[1], x1 - inner[2], y1 - inner[3]]
    assert box[0] >= 299 and box[2] - box[0] <= 200 and drawn(note).count(" Tj") >= 2
    assert "(Move this door" in drawn(note)
    assert ink(out, box) > 0.02 and ink(out, [150, 500, 290, 560]) > 0.001      # words, and the leader
    # pointing right to left: the box is on the left of the point
    _, (left,) = added(tmp_path, {"kind": "callout", "tip": [400, 500], "at": [250, 560], "text": "Check"})
    r = [float(v) for v in left["/Rect"]]
    assert r[0] < 250 and inside(r, 400, 500)
    with pytest.raises(core.PdfManagerError, match="words"):
        added(tmp_path, {"kind": "callout", "tip": [1, 1], "at": [50, 50], "text": "  "})


def test_a_callout_reads_upright_on_a_turned_page(tmp_path):
    _, (note,) = added(tmp_path, {"kind": "callout", "tip": [150, 500], "at": [300, 600], "text": "Turned"},
                       rotate=90)
    content = drawn(note)
    assert " cm" in content and "(Turned)" in content
    assert "0 1 -1 0" in content                       # the same turn the stamps use on such a page


def test_a_dimension_line(tmp_path):
    scale = {"ratio": 48, "units": "ft", "label": "1/4\" = 1'-0\""}
    out, (dim,) = added(tmp_path, {"kind": "dimension", "points": [[100, 300], [244, 300]],
                                   "value": "8'-0\"", "scale": scale})
    assert dim["/Subtype"] == "/Line" and dim["/IT"] == "/LineDimension"
    assert [float(v) for v in dim["/L"]] == [100, 300, 244, 300]
    assert [str(v) for v in dim["/LE"]] == ["/ClosedArrow", "/ClosedArrow"]
    assert dim["/Cap"] is True or bool(dim["/Cap"])
    assert dim["/Contents"] == "8'-0\"" and "(8'-0\")" in drawn(dim)
    measure = dim["/Measure"]
    assert measure["/Subtype"] == "/RL" and measure["/R"] == "1/4\" = 1'-0\""
    # 2 inches of paper at 1/4" = 1': 144 points x the factor = 8 feet
    assert abs(144 * float(measure["/D"][0]["/C"]) - 8) < 1e-6
    assert drawn(dim).count(" h f") == 2               # an arrowhead at each end
    assert ink(out, [100, 296, 244, 318]) > 0.02
    # without a scale it is still a dimension, with the words given
    _, (plain,) = added(tmp_path, {"kind": "dimension", "points": [[100, 300], [100, 500]], "value": "2.78 in"})
    assert "/Measure" not in plain and plain["/Contents"] == "2.78 in"
    with pytest.raises(core.PdfManagerError, match="longer"):
        added(tmp_path, {"kind": "dimension", "points": [[100, 300], [100.2, 300]], "value": "0"})


def test_a_dimension_reads_the_right_way_up(tmp_path):
    # drawn right to left: the words still read left to right
    _, (dim,) = added(tmp_path, {"kind": "dimension", "points": [[244, 300], [100, 300]], "value": "8'-0\""})
    matrix = re.search(r"Tf ((?:[-\d.]+ ){6})Tm \(8'-0\"\)", drawn(dim))
    a, b = (float(v) for v in matrix.group(1).split()[:2])
    assert a > 0.99 and abs(b) < 0.01                       # not turned over
    # a line going up the page: the words run up it, as on a drawing
    _, (up,) = added(tmp_path, {"kind": "dimension", "points": [[100, 300], [100, 500]], "value": "2 m"})
    a, b = (float(v) for v in re.search(r"Tf ((?:[-\d.]+ ){6})Tm", drawn(up)).group(1).split()[:2])
    assert abs(a) < 0.01 and b > 0.99


def test_review_stamps(tmp_path):
    for label in ("APPROVED AS NOTED", "REVISE AND RESUBMIT", "REJECTED", "FOR INFORMATION",
                  "NOT FOR CONSTRUCTION"):
        assert label in markup.STAMP_COLORS
    _, (stamp,) = added(tmp_path, {"kind": "stamp", "rect": [300, 100, 500, 160],
                                   "label": "Revise and resubmit", "dated": True})
    assert stamp["/Subtype"] == "/Stamp" and "(REVISE AND RESUBMIT)" in drawn(stamp)
    assert "(by Ada, " in drawn(stamp)
    red = [round(float(c), 2) for c in stamp["/C"]]
    assert red[0] > 0.7 and red[1] < 0.3
    _, (ok,) = added(tmp_path, {"kind": "stamp", "rect": [300, 100, 500, 160], "label": "APPROVED AS NOTED"})
    assert [round(float(c), 2) for c in ok["/C"]][1] > 0.4          # green


def test_they_are_deleted_like_other_markups(tmp_path):
    src, out = sheet(tmp_path / "sheet.pdf"), tmp_path / "out.pdf"
    ids = markup.apply(src, out, [
        {"op": "add", "page": 1, "kind": "polycloud", "points": [[100, 500], [300, 520], [200, 700]]},
        {"op": "add", "page": 1, "kind": "callout", "tip": [150, 500], "at": [300, 600], "text": "Here"}])
    assert len(ids) == 2
    markup.apply(out, tmp_path / "less.pdf", [{"op": "delete", "page": 1, "id": ids[0]}])
    left = [a.get_object()["/Subtype"] for a in PdfReader(str(tmp_path / "less.pdf")).pages[0]["/Annots"]]
    assert left == ["/FreeText"]
