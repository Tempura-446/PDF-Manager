"""PDFs with layers (optional content), as CAD programs export them, for the
0.32.0 tests: "Walls" (locked), "Dimensions" nested under it, and a radio
group "Options" of "Scheme A" (on) and "Scheme B" (off).

On the page (612 x 792 pt):
  Walls       a line y=150 from x=100 to 500, and the text "WALLS" at (100, 300)
  Dimensions  a line y=600 from x=100 to 500, the text "DIMENSION 3600" at
              (100, 400), and a form XObject drawn with /OC Dimensions that
              says "DIMFORM" at (100, 500)
  Scheme A    the text "SCHEME A" at (350, 300)
  Scheme B    the text "SCHEME B" at (350, 350)
  (no layer)  the text "TITLE BLOCK" at (100, 50)
"""
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name


def layered_pdf(path, *, dimensions_on=True):
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    ocg = lambda name: pdf.make_indirect(Dictionary(Type=Name.OCG, Name=pikepdf.String(name)))  # noqa: E731
    walls, dims, scheme_a, scheme_b = ocg("Walls"), ocg("Dimensions"), ocg("Scheme A"), ocg("Scheme B")
    form = pdf.make_stream(b"BT /F1 18 Tf 100 500 Td (DIMFORM) Tj ET",
                           Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, 612, 792],
                           Resources=Dictionary(Font=Dictionary(F1=font)), OC=dims)
    content = b"\n".join([
        b"q 0 0 0 RG 3 w",
        b"/OC /L0 BDC 100 150 m 500 150 l S BT /F1 24 Tf 100 300 Td (WALLS) Tj ET EMC",
        b"/OC /L1 BDC 0 0 1 RG 100 600 m 500 600 l S BT /F1 24 Tf 100 400 Td (DIMENSION 3600) Tj ET EMC",
        b"/Fm1 Do",
        b"/OC /L2 BDC BT /F1 18 Tf 350 300 Td (SCHEME A) Tj ET EMC",
        b"/OC /L3 BDC BT /F1 18 Tf 350 350 Td (SCHEME B) Tj ET EMC",
        b"Q",
        b"BT /F1 12 Tf 100 50 Td (TITLE BLOCK) Tj ET",
    ])
    page = pikepdf.Page(Dictionary(
        Type=Name.Page, MediaBox=[0, 0, 612, 792],
        Resources=Dictionary(Font=Dictionary(F1=font), XObject=Dictionary(Fm1=form),
                             Properties=Dictionary(L0=walls, L1=dims, L2=scheme_a, L3=scheme_b)),
        Contents=pdf.make_stream(content)))
    pdf.pages.append(page)
    off = [scheme_b] + ([] if dimensions_on else [dims])
    pdf.Root.OCProperties = Dictionary(
        OCGs=Array([walls, dims, scheme_a, scheme_b]),
        D=Dictionary(Name=pikepdf.String("Default"), BaseState=Name.ON, OFF=Array(off),
                     Order=Array([walls, Array([dims]), Array([pikepdf.String("Options"), scheme_a, scheme_b])]),
                     Locked=Array([walls]), RBGroups=Array([Array([scheme_a, scheme_b])])))
    pdf.save(str(path))
    return Path(path)


def plain_pdf(path):
    """One page with a line and some text, and no layers."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    pdf.pages.append(pikepdf.Page(Dictionary(
        Type=Name.Page, MediaBox=[0, 0, 612, 792], Resources=Dictionary(Font=Dictionary(F1=font)),
        Contents=pdf.make_stream(b"q 3 w 100 150 m 500 150 l S Q BT /F1 24 Tf 100 300 Td (NO LAYERS) Tj ET"))))
    pdf.save(str(path))
    return Path(path)
