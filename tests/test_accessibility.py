"""Accessible PDFs: tagging (like Acrobat's Autotag) and the accessibility
check (like Acrobat's Accessibility Checker)."""

import io
import json
import shutil
import subprocess
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import Image as PILImage
from PIL import ImageChops
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (BaseDocTemplate, Frame, FrameBreak, Image, ListFlowable,
                                ListItem, NextPageTemplate, PageBreak, PageTemplate, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

from pdfmanager import accessibility, core, gui
from pdfmanager.cli import main
from test_more import app, call, multipart  # noqa: F401  (app is a fixture)


def make_report(path):
    """Two pages: a title, numbered headings, paragraphs, a link, a bulleted
    list, a ruled table, a picture with a caption, a running header and page
    numbers; page 2 has a heading over two columns."""
    ss = getSampleStyleSheet()
    W, H = A4

    def furniture(c, doc):
        c.saveState()
        c.setFont("Helvetica", 9)
        c.drawString(20 * mm, H - 12 * mm, "ACME Quarterly Report")
        c.drawRightString(W - 20 * mm, 10 * mm, f"Page {doc.page}")
        c.restoreState()

    one = PageTemplate("one", [Frame(20 * mm, 20 * mm, W - 40 * mm, H - 45 * mm)], onPage=furniture)
    width = (W - 50 * mm) / 2
    two = PageTemplate("two", [Frame(20 * mm, H - 50 * mm, W - 40 * mm, 25 * mm),
                               Frame(20 * mm, 20 * mm, width, H - 72 * mm),
                               Frame(30 * mm + width, 20 * mm, width, H - 72 * mm)],
                       onPage=furniture)
    picture = io.BytesIO()
    PILImage.new("RGB", (120, 80), (200, 30, 30)).save(picture, "PNG")
    picture.seek(0)
    body = ss["BodyText"]
    story = [
        Paragraph("Annual Widget Study", ss["Title"]),
        Paragraph("1 Introduction", ss["Heading1"]),
        Paragraph("Widgets are small devices used in many places. This study looks at how "
                  "widgets are made, sold and used across the country, and what people think "
                  "of them. It is the third study of its kind.", body),
        Paragraph('More details are on <a href="https://example.com/widgets" color="blue">our '
                  'website</a> for anyone who wants them.', body),
        ListFlowable([ListItem(Paragraph("Widgets are cheap to make.", body)),
                      ListItem(Paragraph("Widgets last a long time.", body)),
                      ListItem(Paragraph("Most people own at least three.", body))],
                     bulletType="bullet", start="•"),
        Paragraph("1.1 Sales", ss["Heading2"]),
        Paragraph("Sales grew in every region. The table below shows the numbers.", body),
        Table([["Region", "2023", "2024"], ["North", "120", "150"], ["South", "90", "130"],
               ["West", "75", "80"]], colWidths=[50 * mm, 30 * mm, 30 * mm],
              style=TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")])),
        Spacer(1, 6 * mm),
        Image(picture, width=60 * mm, height=40 * mm),
        Paragraph("Figure 1: Widget sales by colour", ss["Italic"]),
        NextPageTemplate("two"), PageBreak(),
        Paragraph("2 Methods", ss["Heading1"]),
        FrameBreak(),
    ]
    story += [Paragraph(f"Left column paragraph {i}. " + "We asked people about widgets. " * 6, body)
              for i in (1, 2, 3)]
    story.append(FrameBreak())
    story += [Paragraph(f"Right column paragraph {i}. " + "They told us many things. " * 6, body)
              for i in (1, 2, 3)]
    BaseDocTemplate(str(path), pagesize=A4, pageTemplates=[one, two]).build(story)
    return path


@pytest.fixture(scope="module")
def report_pdf(tmp_path_factory):
    return make_report(tmp_path_factory.mktemp("access") / "report.pdf")


@pytest.fixture(scope="module")
def tagged(report_pdf):
    out = report_pdf.with_name("report_tagged.pdf")
    result = accessibility.autotag(report_pdf, out)
    return out, result


def mcid_texts(pdf):
    """{(page index, MCID): what's drawn inside it}: text (as WinAnsi), or
    "<image>"; MCID None for artifacts and untagged content."""
    found = {}
    for i, page in enumerate(pdf.pages):
        stack = []
        for ins in pikepdf.parse_content_stream(page):
            mcid = next((m for m in reversed(stack) if m is not None), None)
            if isinstance(ins, pikepdf.ContentStreamInlineImage):
                found.setdefault((i, mcid), []).append("<image>")
                continue
            op = str(ins.operator)
            if op == "BDC":
                props = ins.operands[1]
                stack.append(int(props.MCID) if isinstance(props, pikepdf.Dictionary)
                             and "/MCID" in props else None)
            elif op == "BMC":
                stack.append(None)
            elif op == "EMC":
                stack.pop()
            elif op in ("Tj", "TJ", "'", '"'):
                strings = ins.operands[0] if op == "TJ" else ins.operands[-1:]
                found.setdefault((i, mcid), []).append(
                    "".join(bytes(s).decode("cp1252") for s in strings
                            if isinstance(s, pikepdf.String)))
            elif op == "Do":
                found.setdefault((i, mcid), []).append("<image>")
    return {key: "".join(parts) for key, parts in found.items()}


def kids(elem):
    k = elem.get("/K")
    return [] if k is None else list(k) if isinstance(k, pikepdf.Array) else [k]


def tree(pdf):
    """The structure tree in document order: [(depth, type, own text, element)]."""
    texts = mcid_texts(pdf)
    pages = {p.obj.objgen: i for i, p in enumerate(pdf.pages)}
    out = []

    def walk(elem, depth):
        page = pages.get(elem.Pg.objgen) if "/Pg" in elem else None
        text = "".join(texts.get((page, int(k)), "") for k in kids(elem) if isinstance(k, int))
        out.append((depth, str(elem.S)[1:], " ".join(text.split()), elem))
        for kid in kids(elem):
            if isinstance(kid, pikepdf.Dictionary) and "/S" in kid:
                walk(kid, depth + 1)

    walk(pdf.Root.StructTreeRoot.K, 0)
    return out


def same_look(a, b):
    """Pages that render differently (1-based)."""
    da, db = pdfium.PdfDocument(str(a)), pdfium.PdfDocument(str(b))
    try:
        assert len(da) == len(db)
        return [i + 1 for i in range(len(da))
                if ImageChops.difference(da[i].render(scale=1.5).to_pil().convert("RGB"),
                                         db[i].render(scale=1.5).to_pil().convert("RGB")).getbbox()]
    finally:
        da.close()
        db.close()


# -- tagging --------------------------------------------------------------------

def test_tags_follow_the_layout_in_reading_order(tagged):
    out, result = tagged
    assert result["tagged"] and result["lang"] == "en" and not result["figures_without_alt"]
    assert result["title"] == "Annual Widget Study"
    assert result["elements"]["L"] == 1 and result["elements"]["Table"] == 1
    with pikepdf.open(out) as pdf:
        items = tree(pdf)
        top = [(s, text) for depth, s, text, _ in items if depth == 1]
        assert [s for s, _ in top] == ["H1", "H1", "P", "P", "L", "H2", "P", "Table", "Figure",
                                       "Caption", "H1", "P", "P", "P", "P", "P", "P"]
        assert top[0][1] == "Annual Widget Study" and top[1][1] == "1 Introduction"
        assert top[5][1] == "1.1 Sales" and top[10][1] == "2 Methods"
        # the left column is read before the right one
        assert [text.split(".")[0] for _, text in top[11:]] == \
            [f"{side} column paragraph {i}" for side in ("Left", "Right") for i in (1, 2, 3)]
        # running header and page numbers are artifacts, not read
        everything = " ".join(text for _, _, text, _ in items)
        assert "ACME" not in everything and "Page 1" not in everything
        texts = mcid_texts(pdf)
        assert "ACME Quarterly Report" in texts[(0, None)] and "Page 2" in texts[(1, None)]

        # the list: items with a label and a body
        lst = next(e for _, s, _, e in items if s == "L")
        assert str(lst.A.ListNumbering) == "/Disc"
        items_ = [k for k in kids(lst)]
        assert [str(li.S) for li in items_] == ["/LI"] * 3
        for li, text in zip(items_, ["Widgets are cheap to make.", "Widgets last a long time.",
                                     "Most people own at least three."]):
            lbl, body = kids(li)
            assert str(lbl.S) == "/Lbl" and str(body.S) == "/LBody"
            assert [t for _, s, t, e in items if e == body][0] == text
            assert len([t for _, s, t, e in items if e == lbl][0]) == 1  # the bullet

        # the table: rows of cells, the first row header cells for the columns
        table = next(e for _, s, _, e in items if s == "Table")
        rows = [[(str(c.S)[1:], [t for _, _, t, e in items if e == c][0]) for c in kids(tr)]
                for tr in kids(table)]
        assert rows == [[("TH", "Region"), ("TH", "2023"), ("TH", "2024")],
                        [("TD", "North"), ("TD", "120"), ("TD", "150")],
                        [("TD", "South"), ("TD", "90"), ("TD", "130")],
                        [("TD", "West"), ("TD", "75"), ("TD", "80")]]
        assert str(kids(kids(table)[0])[0].A.Scope) == "/Column"

        # the figure: described by its caption, with its picture inside
        figure = next(e for _, s, _, e in items if s == "Figure")
        assert str(figure.Alt) == "Figure 1: Widget sales by colour"
        assert [t for _, _, t, e in items if e == figure] == ["<image>"]
        assert len(figure.A.BBox) == 4

        # the link: inside its paragraph, with the annotation and its text
        para, link = next((e, k) for _, s, _, e in items if s == "P"
                          for k in kids(e) if isinstance(k, pikepdf.Dictionary)
                          and str(k.get("/S")) == "/Link")
        assert [t for _, _, t, e in items if e == link] == ["our website"]
        objr = [k for k in kids(link) if isinstance(k, pikepdf.Dictionary) and k.get("/Type") == "/OBJR"]
        annot = pdf.pages[0].Annots[0]
        assert objr[0].Obj.objgen == annot.objgen and str(annot.Contents) == "our website"
        assert [t for _, _, t, e in items if e == para][0].startswith("More details are on")

        root = pdf.Root
        assert root.MarkInfo.Marked is True and str(root.Lang) == "en"
        assert root.ViewerPreferences.DisplayDocTitle is True
        assert str(pdf.docinfo.Title) == "Annual Widget Study"
        assert pdf.open_metadata()["dc:title"] == "Annual Widget Study"
        assert all(str(p.obj.Tabs) == "/S" for p in pdf.pages)


def test_mcids_parent_tree_and_annotations_agree(tagged):
    out, _ = tagged
    with pikepdf.open(out) as pdf:
        tree_root = pdf.Root.StructTreeRoot
        parents = accessibility._number_tree(tree_root.ParentTree, {})
        assert int(tree_root.ParentTreeNextKey) == len(parents)
        owners = {}
        for _, _, _, elem in tree(pdf):
            for kid in kids(elem):
                if isinstance(kid, int):
                    owners[(elem.Pg.objgen, kid)] = elem
            for kid in kids(elem):  # each kid element points back at its parent
                if isinstance(kid, pikepdf.Dictionary) and "/S" in kid:
                    assert kid.P.objgen == elem.objgen
        for page in pdf.pages:
            untagged, mcids = accessibility._content_marks(page, page.Resources)
            assert untagged == 0
            assert mcids == set(range(len(mcids)))  # numbered from 0, none missing
            entry = parents[int(page.obj.StructParents)]
            assert len(entry) == len(mcids)
            for mcid in mcids:  # the ParentTree names the element that holds each MCID
                assert entry[mcid].objgen == owners[(page.obj.objgen, mcid)].objgen
        annot = pdf.pages[0].Annots[0]
        link = parents[int(annot.StructParent)]
        assert str(link.S) == "/Link"
        assert any(isinstance(k, pikepdf.Dictionary) and k.get("/Type") == "/OBJR"
                   and k.Obj.objgen == annot.objgen for k in kids(link))


def test_pages_look_the_same_after_tagging(tagged, report_pdf, tmp_path):
    out, _ = tagged
    assert same_look(report_pdf, out) == []
    # pages drawn as form XObjects (two to a sheet), turned pages, text set sideways
    nup, turned = tmp_path / "nup.pdf", tmp_path / "turned.pdf"
    core.n_up(report_pdf, nup, 2, "a4")
    core.rotate(report_pdf, turned, 90)
    for src in (nup, turned):
        result = accessibility.autotag(src, tmp_path / f"{src.stem}_tagged.pdf")
        assert same_look(src, tmp_path / f"{src.stem}_tagged.pdf") == []
        assert accessibility.check(tmp_path / f"{src.stem}_tagged.pdf")["passed"], src
    assert result["elements"]["H1"] == 3 and result["elements"]["L"] == 1  # turned: read upright


def test_existing_marked_content_optional_content_and_inline_images(tmp_path):
    src = tmp_path / "marked.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(300, 300))
    page = pdf.pages[0]
    font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name.Helvetica))
    layer = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name=pikepdf.String("L")))
    pdf.Root.OCProperties = pikepdf.Dictionary(OCGs=[layer], D=pikepdf.Dictionary(ON=[layer]))
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font),
                                        Properties=pikepdf.Dictionary(oc1=layer))
    page.Contents = pdf.make_stream(
        b"/OC /oc1 BDC BT /F1 18 Tf 30 250 Td (Layered heading) Tj ET EMC\n"
        b"/Span <</MCID 7>> BDC BT /F1 10 Tf 30 220 Td (Old marked text here.) Tj () Tj ET EMC\n"
        b"q 40 0 0 40 30 150 cm BI /W 2 /H 2 /CS /G /BPC 8 ID \x00\xff\xff\x00 EI Q\n"
        b"BT /F1 10 Tf 30 120 Td [(Kerned) -200 (text)] TJ 0 -14 Td (second line) Tj ET\n")
    pdf.save(src)
    out = tmp_path / "marked_tagged.pdf"
    result = accessibility.autotag(src, out, alt={"1": ["A small chequered square"]})
    assert same_look(src, out) == []
    with pikepdf.open(out) as pdf:
        data = pdf.pages[0].Contents.read_bytes()
        assert b"/OC /oc1 BDC" in data and b"/Span" not in data  # layers stay, old tags go
        assert [(s, t) for d, s, t, _ in tree(pdf) if d == 1] == [
            ("H1", "Layered heading"), ("P", "Old marked text here."), ("Figure", "<image>"),
            ("P", "Kernedtextsecond line")]
        figure = next(e for _, s, _, e in tree(pdf) if s == "Figure")
        assert str(figure.Alt) == "A small chequered square"
    assert result["figures_without_alt"] == []
    assert accessibility.check(out)["passed"]


def test_checker_finds_problems_and_none_once_tagged(tagged, report_pdf):
    out, _ = tagged
    before = accessibility.check(report_pdf)
    rules = [i["rule"] for i in before["issues"]]
    assert rules == ["tagged", "lang", "title", "title-shown", "tabs"]
    assert not before["passed"] and not before["tagged"] and before["pages"] == 2
    assert next(i for i in before["issues"] if i["rule"] == "tabs")["pages"] == [1]
    assert accessibility.summary(before).startswith("5 accessibility problems (The document "
                                                    "isn't tagged")
    text = accessibility.report(before, "report.pdf")
    assert "Tagged: No" in text and "How to fix: Use Make accessible (tag)" in text
    assert "reading order" in text  # what only a person can judge

    after = accessibility.check(out)
    assert after["passed"] and after["issues"] == [] and after["tagged"]
    assert after["lang"] == "en" and after["title"] == "Annual Widget Study"
    assert accessibility.summary(after) == "no accessibility problems found"
    assert "Result: no problems found" in accessibility.report(after, "x.pdf")


def test_checker_explains_what_is_wrong_with_tags(tagged, tmp_path):
    out, _ = tagged
    broken = tmp_path / "broken.pdf"
    with pikepdf.open(out) as pdf:
        items = tree(pdf)
        figure = next(e for _, s, _, e in items if s == "Figure")
        del figure["/Alt"]
        next(e for _, s, _, e in items if s == "H2").S = pikepdf.Name.H4   # H1 -> H4
        for _, s, _, e in items:
            if s == "TH":
                e.S = pikepdf.Name.TD
        first_row = kids(next(e for _, s, _, e in items if s == "Table"))[0]
        first_row.K = pikepdf.Array(kids(first_row)[:2])                   # a short row
        li = next(e for _, s, _, e in items if s == "LI")
        kids(li)[1].S = pikepdf.Name.P                                     # LBody -> P
        del pdf.pages[0].Annots[0]["/Contents"]
        del pdf.Root["/Lang"]
        pdf.pages[0].obj.Tabs = pikepdf.Name.R
        page = pdf.pages[1]                                                # untagged text
        page.contents_add(pdf.make_stream(b"BT /F1 12 Tf 100 100 Td (Stray) Tj ET"), prepend=False)
        if "/F1" not in page.Resources.Font:
            page.Resources.Font.F1 = pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica))
        pdf.save(broken)
    result = accessibility.check(broken)
    found = {i["rule"]: i for i in result["issues"]}
    assert set(found) == {"lang", "content", "figures", "tables", "headings", "lists", "links",
                          "tabs"}
    assert found["figures"]["pages"] == [1] and "1 figure has no alternate text" in found["figures"]["message"]
    assert found["content"]["pages"] == [2]
    messages = " ".join(i["message"] for i in result["issues"])
    assert "no header cells" in messages and "different numbers of cells" in messages
    assert "skip a level" in messages and "1 link has no description" in messages
    assert "1 list isn't built right" in messages

    # a ParentTree that doesn't match the content
    damaged = tmp_path / "damaged.pdf"
    with pikepdf.open(out) as pdf:
        pdf.Root.StructTreeRoot.ParentTree = pdf.make_indirect(pikepdf.Dictionary(Nums=[]))
        pdf.save(damaged)
    found = {i["rule"]: i for i in accessibility.check(damaged)["issues"]}
    assert found["structure"]["pages"] == [1, 2] and "damaged" in found["structure"]["message"]


def test_checker_finds_scans_fields_locks_and_missing_bookmarks(tmp_path):
    src = tmp_path / "form.pdf"
    c = canvas.Canvas(str(src), pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 760, "Application form")
    c.setFont("Helvetica", 11)
    c.drawString(72, 720, "Name:")
    c.acroForm.textfield(name="name", x=130, y=712, width=200, height=20)
    c.drawString(72, 690, "Email:")
    c.acroForm.textfield(name="email", tooltip="Your email address", x=130, y=682, width=200,
                         height=20)
    c.showPage()
    scan = io.BytesIO()
    PILImage.new("L", (400, 560), 230).save(scan, "PNG")
    scan.seek(0)
    from reportlab.lib.utils import ImageReader

    c.drawImage(ImageReader(scan), 0, 0, *A4)
    c.showPage()
    c.save()
    found = {i["rule"]: i for i in accessibility.check(src)["issues"]}
    assert found["scanned"]["pages"] == [2] and "OCR" in found["scanned"]["fix"]
    assert found["forms"]["count"] == 1 and "no tooltip" in found["forms"]["message"]

    out = tmp_path / "form_tagged.pdf"
    result = accessibility.autotag(src, out)
    assert result["elements"]["Form"] == 2 and result["figures_without_alt"] == [2]
    assert same_look(src, out) == []
    with pikepdf.open(out) as pdf:
        forms = [e for _, s, _, e in tree(pdf) if s == "Form"]
        widgets = [a for a in pdf.pages[0].Annots if a.Subtype == "/Widget"]
        assert {k.Obj.objgen for f in forms for k in kids(f)} == {w.objgen for w in widgets}
    found = {i["rule"] for i in accessibility.check(out)["issues"]}
    assert found == {"scanned", "figures", "forms"}  # what tagging can't fix

    # security that locks screen readers out (only older encryption can: PDF 2.0
    # always lets them in)
    locked = tmp_path / "locked.pdf"
    with pikepdf.open(out) as pdf:
        pdf.save(locked, encryption=pikepdf.Encryption(
            owner="owner", user="", R=3, aes=False, metadata=False,
            allow=pikepdf.Permissions(accessibility=False, extract=False)))
    found = {i["rule"]: i for i in accessibility.check(locked)["issues"]}
    assert "screen readers" in found["security"]["message"]

    # a long document without bookmarks; tagging adds them from the headings
    long = tmp_path / "long.pdf"
    ss = getSampleStyleSheet()
    story = []
    for n in range(1, 23):
        story += [Paragraph(f"{n} Chapter {n}", ss["Heading1"]), Paragraph("Text. " * 20, ss["BodyText"]),
                  PageBreak()]
    SimpleDocTemplate(str(long)).build(story)
    assert "bookmarks" in {i["rule"] for i in accessibility.check(long)["issues"]}
    result = accessibility.autotag(long, tmp_path / "long_tagged.pdf")
    assert result["bookmarks"] == 22 and "added 22 bookmarks" in accessibility.tag_summary(result)
    assert accessibility.check(tmp_path / "long_tagged.pdf")["passed"]


def test_language_title_and_options(report_pdf, tmp_path):
    assert accessibility.guess_language("Der Bericht ist nicht fertig, und die Zahlen sind "
                                        "auch noch nicht da. Das ist für uns ein Problem.") == "de"
    assert accessibility.guess_language("Le rapport n'est pas prêt et les chiffres ne sont pas "
                                        "là. C'est un problème pour nous.") == "fr"
    assert accessibility.guess_language("El informe no está listo y los números no están en "
                                        "la mesa. Es un problema para la empresa.") == "es"
    assert accessibility.guess_language("12 34 56") == "en"
    out = tmp_path / "set.pdf"
    accessibility.autotag(report_pdf, out, lang="de-CH", title="Widgets 2024")
    with pikepdf.open(out) as pdf:
        assert str(pdf.Root.Lang) == "de-CH" and str(pdf.docinfo.Title) == "Widgets 2024"
    with pytest.raises(core.PdfManagerError, match="isn't a language code"):
        accessibility.autotag(report_pdf, out, lang="german!")
    with pytest.raises(core.PdfManagerError, match="isn't a page number"):
        accessibility.autotag(report_pdf, out, alt={"first": ["x"]})


def test_encrypted_and_already_tagged_files(tagged, report_pdf, tmp_path):
    locked = tmp_path / "locked.pdf"
    core.encrypt(report_pdf, locked, "secret")
    with pytest.raises(core.PdfManagerError, match="encrypted"):
        accessibility.autotag(locked, tmp_path / "x.pdf")
    with pytest.raises(core.PdfManagerError, match="wrong password"):
        accessibility.check(locked, password="nope")
    out = tmp_path / "locked_tagged.pdf"
    result = accessibility.autotag(locked, out, password="secret")
    assert result["tagged"] and same_look(report_pdf, out) == []
    assert accessibility.check(out)["passed"]
    assert not accessibility.check(locked, password="secret")["tagged"]
    # protected again the way the app does it, the tags survive
    core.encrypt(out, out, "secret")
    assert accessibility.check(out, password="secret")["passed"]

    # already tagged: the tags are kept, unless replaced
    tagged_pdf, _ = tagged
    kept = tmp_path / "kept.pdf"
    result = accessibility.autotag(tagged_pdf, kept, lang="fr")
    assert not result["tagged"] and result["lang"] == "fr"
    assert accessibility.tag_summary(result).startswith("already tagged: kept its tags")
    with pikepdf.open(tagged_pdf) as a, pikepdf.open(kept) as b:
        assert [(d, s, t) for d, s, t, _ in tree(a)] == [(d, s, t) for d, s, t, _ in tree(b)]
        assert str(b.Root.Lang) == "fr"
    again = tmp_path / "again.pdf"
    result = accessibility.autotag(tagged_pdf, again, force=True)
    assert result["tagged"] and result["lang"] == "en"
    assert same_look(tagged_pdf, again) == [] and accessibility.check(again)["passed"]
    with pikepdf.open(again) as pdf:
        data = pdf.pages[0].Contents.read_bytes()
        assert data.count(b"/MCID 0 ") == 1  # old tags were replaced, not nested
        assert [s for d, s, _, _ in tree(pdf) if d == 1][:4] == ["H1", "H1", "P", "P"]


# -- command line, app and reader ---------------------------------------------

def test_cli_tag_and_check_access(report_pdf, tmp_path, capsys):
    src = tmp_path / "doc.pdf"
    shutil.copy(report_pdf, src)
    assert main(["check-access", str(src)]) == 1
    assert "The document isn't tagged" in capsys.readouterr().out
    alt = tmp_path / "alt.json"
    alt.write_text(json.dumps({"1": ["Red block showing sales"]}))
    assert main(["tag", str(src), "--lang", "en-GB", "--alt", str(alt)]) == 0
    printed = capsys.readouterr().out
    assert "doc_tagged.pdf" in printed and "1 table" in printed and "language en-GB" in printed
    out = tmp_path / "doc_tagged.pdf"
    with pikepdf.open(out) as pdf:
        assert str(next(e for _, s, _, e in tree(pdf) if s == "Figure").Alt) == "Red block showing sales"
    assert main(["check-access", str(out)]) == 0
    assert "Result: no problems found" in capsys.readouterr().out
    assert main(["check-access", str(out), str(src), "--json"]) == 1
    records = json.loads(capsys.readouterr().out)
    assert [r["passed"] for r in records] == [True, False]
    assert records[1]["issues"][0]["rule"] == "tagged"
    assert main(["tag", str(src), "--lang", "not a language", "-o", str(tmp_path / "x.pdf")]) == 1
    assert "isn't a language code" in capsys.readouterr().err
    alt.write_text("[1, 2]")
    assert main(["tag", str(src), "--alt", str(alt), "-o", str(tmp_path / "x.pdf")]) == 1


def test_tools_in_the_app(report_pdf, tmp_path):
    ops = {op["id"]: op for op in gui.OPERATIONS}
    assert ops["tag"]["group"] == ops["access-check"]["group"] == "Convert"
    assert {f["key"] for f in ops["tag"]["fields"]} == {"lang", "title", "force"}
    work = tmp_path / "work"
    work.mkdir()
    notes = []
    name, data, _ = gui.run_operation("tag", [report_pdf], {"lang": "auto", "title": "", "force": ""},
                                      notes=notes, work=work)
    assert name == "report_tagged.pdf" and data[:5] == b"%PDF-"
    assert notes == ["report.pdf: tagged 4 headings, 9 paragraphs, 1 list, 1 table, 1 figure, "
                     "1 link; language en"]
    (tmp_path / "work2").mkdir()
    notes = []
    name, data, _ = gui.run_operation("access-check", [report_pdf], {}, notes=notes,
                                      work=tmp_path / "work2")
    assert name == "report_accessibility.txt" and b"Accessibility check: report.pdf" in data
    assert notes[0].startswith("report.pdf: 5 accessibility problems")
    html = (Path(gui.__file__).parent / "gui.html").read_text(encoding="utf-8")
    assert '"access-check": \'<' in html and "\n  tag: '<" in html  # icons
    assert '"access-check": "See what keeps screen readers out' in html


def test_reader_checks_accessibility(tagged, report_pdf, app):
    base, token, _ = app
    for path, passed in ((report_pdf, False), (tagged[0], True)):
        body, headers = multipart({"password": ""}, [("file", path)])
        status, _, res = call(base, token, "/api/accessibility/check", body, headers)
        result = json.loads(res)
        assert status == 200 and result["passed"] is passed and "file" not in result
    assert result["lang"] == "en" and result["note"]
    js = (Path(gui.__file__).parent / "static" / "reader.js").read_text(encoding="utf-8")
    assert '["Tagged (accessible)", tagged ? "Yes" : "No"]' in js and "checkAccessibility()" in js


def test_pdfjs_reads_the_structure_tree(tagged, app, tmp_path):
    """The reader's pdf.js finds the tags: roles, alternate text, the link."""
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True,
                                  cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    base, _, _ = app
    script = tmp_path / "tree.js"
    script.write_text("""
const { chromium } = require("playwright");
(async () => {
  const [base, file] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto(base + "/");
  const data = require("fs").readFileSync(file).toString("base64");
  const out = await page.evaluate(async ({ base, data }) => {
    const pdfjs = await import(base + "/static/pdfjs/build/pdf.min.mjs");
    pdfjs.GlobalWorkerOptions.workerSrc = base + "/static/pdfjs/build/pdf.worker.min.mjs";
    const doc = await pdfjs.getDocument({ data: Uint8Array.from(atob(data), (c) => c.charCodeAt(0)) }).promise;
    const { info } = await doc.getMetadata();
    const mark = await doc.getMarkInfo();
    const tree = await (await doc.getPage(1)).getStructTree();
    return { lang: info.Language, marked: mark instanceof Map ? mark.get("Marked") : mark.Marked, tree };
  }, { base, data });
  console.log(JSON.stringify(out));
  await browser.close();
})();
""")
    run = subprocess.run([node, str(script), base, str(tagged[0])], capture_output=True, text=True,
                         timeout=120)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert out["lang"] == "en" and out["marked"] is True
    document = out["tree"]["children"][0]
    assert document["role"] == "Document"
    roles = [kid["role"] for kid in document["children"]]
    assert roles == ["H1", "H1", "P", "P", "L", "H2", "P", "Table", "Figure", "Caption"]
    figure = document["children"][8]
    assert figure["alt"] == "Figure 1: Widget sales by colour"
    link = next(k for k in document["children"][3]["children"] if k.get("role") == "Link")
    assert any(k.get("type") == "annotation" for k in link["children"])
