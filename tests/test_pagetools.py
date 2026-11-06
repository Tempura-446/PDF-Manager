"""Tests for the page tools added in 0.9: blank pages, splitting at
bookmarks and by size, booklets and trimming white margins."""

import os
import random
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4, A5, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdfmanager import core, gui, outline, recipes
from pdfmanager.cli import main


def numbered_pdf(path, pages=5, size=A4, sizes=None, bookmarks=()):
    """Pages that say "Page 1", "Page 2", ... ``bookmarks`` are
    (title, 1-based page, level)."""
    c = canvas.Canvas(str(path), pagesize=size)
    marks = {}
    for page, title, level in bookmarks:
        marks.setdefault(page, []).append((title, level))
    for n in range(1, pages + 1):
        if sizes and n in sizes:
            c.setPageSize(sizes[n])
        c.setFont("Helvetica", 24)
        c.drawString(72, 72, f"Page {n}")
        for k, (title, level) in enumerate(marks.get(n, [])):
            key = f"m{n}_{k}"
            c.bookmarkPage(key)
            c.addOutlineEntry(title, key, level=level)
        c.showPage()
    c.save()
    return path


def page_texts(path):
    return [(p.extract_text() or "").strip() for p in PdfReader(str(path)).pages]


def size_of(page):
    return round(float(page.mediabox.width)), round(float(page.mediabox.height))


def noise_pdf(path, pages, pixels=180, big=None):
    """Pages that each hold a picture of random noise (which can't be
    compressed), so every page adds about the same to the file size.
    Page ``big`` (1-based) gets a much larger picture."""
    rng = random.Random(1)
    c = canvas.Canvas(str(path), pagesize=A4)
    for n in range(1, pages + 1):
        side = pixels * 4 if n == big else pixels
        data = bytes(rng.getrandbits(8) for _ in range(side * side * 3))
        image = Image.frombytes("RGB", (side, side), data)
        c.drawImage(ImageReader(image), 72, 300, 300, 300)
        c.drawString(72, 72, f"Page {n}")
        c.showPage()
    c.save()
    return path


def work_dir(tmp_path, name="work"):
    work = tmp_path / name
    work.mkdir()
    return work


# -- insert blank pages ---------------------------------------------------------------

def test_blank_pages_go_after_the_pages_given(tmp_path):
    src = numbered_pdf(tmp_path / "doc.pdf", 5)
    out = core.insert_blank_pages(src, tmp_path / "out.pdf", "2,end", count=2)
    assert page_texts(out) == ["Page 1", "Page 2", "", "", "Page 3", "Page 4", "Page 5", "", ""]


def test_blank_pages_have_the_size_of_the_page_before(tmp_path):
    a5_wide = landscape(A5)
    src = numbered_pdf(tmp_path / "doc.pdf", 3, sizes={2: a5_wide, 3: A4})
    out = core.insert_blank_pages(src, tmp_path / "out.pdf", "2", at_start=True)
    pages = PdfReader(str(out)).pages
    assert page_texts(out) == ["", "Page 1", "Page 2", "", "Page 3"]
    assert size_of(pages[0]) == size_of(pages[1]) == (595, 842)  # like the first page
    assert size_of(pages[3]) == (round(a5_wide[0]), round(a5_wide[1]))


def test_blank_page_after_a_turned_page_is_shown_the_same_way(tmp_path):
    src = numbered_pdf(tmp_path / "doc.pdf", 2)
    writer = PdfWriter(clone_from=str(src))
    writer.pages[0].rotate(90)
    writer.write(tmp_path / "turned.pdf")
    out = core.insert_blank_pages(tmp_path / "turned.pdf", tmp_path / "out.pdf", "1")
    assert size_of(PdfReader(str(out)).pages[1]) == (842, 595)  # landscape, as page 1 is shown


def test_blank_page_after_every_page_keeps_bookmarks(tmp_path):
    src = numbered_pdf(tmp_path / "doc.pdf", 3, bookmarks=[(1, "One", 0), (3, "Three", 0)])
    out = core.insert_blank_pages(src, tmp_path / "out.pdf", "all")
    assert page_texts(out) == ["Page 1", "", "Page 2", "", "Page 3", ""]
    assert [(b["title"], b["page"]) for b in outline.get_outline(out)] == [("One", 1), ("Three", 5)]


def test_blank_pages_need_a_place(tmp_path):
    src = numbered_pdf(tmp_path / "doc.pdf", 2)
    with pytest.raises(core.PdfManagerError, match="after which pages"):
        core.insert_blank_pages(src, tmp_path / "out.pdf", "")
    with pytest.raises(core.PdfManagerError, match="between 1 and 100"):
        core.insert_blank_pages(src, tmp_path / "out.pdf", "1", count=0)
    assert core.insert_blank_pages(src, tmp_path / "start.pdf", "0")
    assert page_texts(tmp_path / "start.pdf") == ["", "Page 1", "Page 2"]


def test_cli_insert_blank(tmp_path, capsys):
    src = numbered_pdf(tmp_path / "doc.pdf", 3)
    assert main(["insert-blank", str(src), "--after", "1,3", "--count", "2"]) == 0
    assert page_texts(tmp_path / "doc_blanks.pdf") == ["Page 1", "", "", "Page 2", "Page 3", "", ""]
    assert main(["insert-blank", str(src)]) == 1
    assert "--after" in capsys.readouterr().err


def test_gui_insert_blank(tmp_path):
    src = numbered_pdf(tmp_path / "doc.pdf", 3)
    name, data, _ = gui.run_operation("insert-blank", [src], {"after": "2", "count": "1"},
                                      work=work_dir(tmp_path))
    assert name == "doc_blanks.pdf"
    assert [(p.extract_text() or "").strip() for p in PdfReader(BytesIO(data)).pages] == \
        ["Page 1", "Page 2", "", "Page 3"]
    with pytest.raises(gui.UserError, match="click the pages"):
        gui.run_operation("insert-blank", [src], {"after": ""}, work=work_dir(tmp_path, "w2"))


# -- split at bookmarks ----------------------------------------------------------------

BOOK = [(2, "Intro", 0), (3, "Background", 1), (4, "Results: part 1/2", 0), (6, "Intro", 0)]


def test_split_at_bookmarks(tmp_path):
    src = numbered_pdf(tmp_path / "book.pdf", 7, bookmarks=BOOK)
    files = core.split(src, tmp_path / "parts", bookmarks=1)
    assert [f.name for f in files] == ["00 Opening pages.pdf", "01 Intro.pdf",
                                       "02 Results_ part 1-2.pdf", "03 Intro.pdf"]
    assert [page_texts(f) for f in files] == [
        ["Page 1"], ["Page 2", "Page 3"], ["Page 4", "Page 5"], ["Page 6", "Page 7"]]


def test_split_at_second_level_bookmarks(tmp_path):
    src = numbered_pdf(tmp_path / "book.pdf", 7, bookmarks=BOOK)
    files = core.split(src, tmp_path / "parts", bookmarks=2)
    assert [f.name for f in files] == ["00 Opening pages.pdf", "01 Intro.pdf", "02 Background.pdf",
                                       "03 Results_ part 1-2.pdf", "04 Intro.pdf"]
    assert [len(PdfReader(str(f)).pages) for f in files] == [1, 1, 1, 2, 2]


def test_split_at_bookmarks_without_opening_pages_and_same_titles(tmp_path):
    marks = [(1, "Chapter", 0), (2, "Chapter", 0), (3, "CON", 0)]
    src = numbered_pdf(tmp_path / "book.pdf", 3, bookmarks=marks)
    names = [f.name for f in core.split(src, tmp_path / "parts", bookmarks=1, prefix="b")]
    assert names == ["b_01 Chapter.pdf", "b_02 Chapter.pdf", "b_03 CON.pdf"]
    assert core._unique_filename("x", {"x.pdf"}) == "x (2).pdf"
    assert core._title_filename("  a/b\\c: d?.  ") == "a-b-c_ d_"
    assert len(core._title_filename("é" * 200).encode()) <= 80


def test_split_at_bookmarks_needs_bookmarks(tmp_path):
    src = numbered_pdf(tmp_path / "plain.pdf", 3)
    with pytest.raises(core.PdfManagerError, match="no bookmarks to split at"):
        core.split(src, tmp_path / "parts", bookmarks=1)


def test_cli_split_at_bookmarks(tmp_path, capsys):
    src = numbered_pdf(tmp_path / "book.pdf", 7, bookmarks=BOOK)
    assert main(["split", str(src), "--bookmarks", "--level", "2"]) == 0
    folder = tmp_path / "book_split"
    assert sorted(p.name for p in folder.iterdir())[:2] == ["00 Opening pages.pdf", "01 Intro.pdf"]
    assert len(list(folder.iterdir())) == 5


def test_gui_split_at_bookmarks(tmp_path):
    src = numbered_pdf(tmp_path / "book.pdf", 7, bookmarks=BOOK)
    name, data, ctype = gui.run_operation("split", [src], {"by": "bookmarks", "level": "1"},
                                          work=work_dir(tmp_path))
    assert ctype == "application/zip" and name == "book_split.zip"
    names = zipfile.ZipFile(BytesIO(data)).namelist()
    assert names == ["book_split/00 Opening pages.pdf", "book_split/01 Intro.pdf",
                     "book_split/02 Results_ part 1-2.pdf", "book_split/03 Intro.pdf"]


# -- split by size -----------------------------------------------------------------------

def test_split_by_size_stays_under_the_limit(tmp_path, monkeypatch):
    src = noise_pdf(tmp_path / "scan.pdf", 40)
    per_page = os.path.getsize(src) / 40
    limit = int(per_page * 6.5)
    tries = []
    real = core._pages_bytes
    monkeypatch.setattr(core, "_pages_bytes", lambda r, i: tries.append(1) or real(r, i))
    files = core.split(src, tmp_path / "parts", max_size=limit)
    readers = [PdfReader(str(f)) for f in files]
    assert all(f.stat().st_size <= limit for f in files)
    # Every page once, in order.
    texts = [(p.extract_text() or "").strip() for r in readers for p in r.pages]
    assert texts == [f"Page {n}" for n in range(1, 41)]
    # Each file holds as many pages as fit: one more page would be too much.
    reader = PdfReader(str(src))
    first = 0
    for r in readers[:-1]:
        count = len(r.pages)
        assert len(real(reader, range(first, first + count + 1))) > limit
        first += count
    assert 5 <= max(len(r.pages) for r in readers) <= 7
    # Estimated, not tried page by page.
    assert len(tries) <= 4 * len(files)
    assert files[0].name == "scan_01-06.pdf" or files[0].name.startswith("scan_01-")


def test_split_by_size_puts_a_large_page_on_its_own(tmp_path):
    src = noise_pdf(tmp_path / "scan.pdf", 6, big=3)
    limit = int(os.path.getsize(src) * 0.4)  # the large page alone is more than this
    notes = []
    files = core.split(src, tmp_path / "parts", max_size=limit, notes=notes)
    counts = [len(PdfReader(str(f)).pages) for f in files]
    big = [f for f in files if f.stat().st_size > limit]
    assert len(big) == 1 and page_texts(big[0]) == ["Page 3"]
    assert sum(counts) == 6
    assert len(notes) == 1 and notes[0].startswith("page 3 alone is")


def test_split_by_size_with_a_tiny_limit(tmp_path):
    src = numbered_pdf(tmp_path / "doc.pdf", 2)
    with pytest.raises(core.PdfManagerError, match="at least 1 KB"):
        core.split(src, tmp_path / "parts", max_size=10)


def test_gui_split_by_size(tmp_path):
    src = noise_pdf(tmp_path / "scan.pdf", 12)
    mb = os.path.getsize(src) / 3.5 / 1e6
    notes = []
    name, data, _ = gui.run_operation("split", [src], {"by": "size", "max_mb": f"{mb:.4f}"},
                                      work=work_dir(tmp_path), notes=notes)
    archive = zipfile.ZipFile(BytesIO(data))
    sizes = [info.file_size for info in archive.infolist()]
    assert len(sizes) == 4 and max(sizes) <= mb * 1e6
    assert notes == []


def test_cli_split_by_size(tmp_path):
    src = noise_pdf(tmp_path / "scan.pdf", 8)
    mb = os.path.getsize(src) / 2.5 / 1e6
    assert main(["split", str(src), "--max-size", f"{mb:.4f}", "-o", str(tmp_path / "parts")]) == 0
    files = sorted((tmp_path / "parts").iterdir())
    assert len(files) == 3 and all(f.stat().st_size <= mb * 1e6 for f in files)


# -- booklet -------------------------------------------------------------------------------

def side_texts(page):
    """The text on the left and right half of a booklet side."""
    halves = {"left": [], "right": []}
    middle = float(page.mediabox.width) / 2

    def visit(text, cm, tm, *_):
        if text.strip():
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            halves["left" if x < middle else "right"].append(text.strip())

    page.extract_text(visitor_text=visit)
    return " ".join(halves["left"]), " ".join(halves["right"])


def test_booklet_order_and_padding(tmp_path):
    src = numbered_pdf(tmp_path / "doc.pdf", 6, size=A5)
    out = core.booklet(src, tmp_path / "booklet.pdf")
    pages = PdfReader(str(out)).pages
    assert len(pages) == 4  # 8 pages (2 blank) on 2 sheets, both sides
    assert [side_texts(p) for p in pages] == [
        ("", "Page 1"), ("Page 2", ""), ("Page 6", "Page 3"), ("Page 4", "Page 5")]
    # Twice the page size: two A5 pages make an A4 landscape sheet.
    assert all(size_of(p) == (round(2 * A5[0]), round(A5[1])) for p in pages)


def test_booklet_on_a_chosen_paper_size(tmp_path):
    src = numbered_pdf(tmp_path / "doc.pdf", 8)
    out = core.booklet(src, tmp_path / "booklet.pdf", size="letter")
    pages = PdfReader(str(out)).pages
    assert len(pages) == 4 and all(size_of(p) == (792, 612) for p in pages)
    assert side_texts(pages[0]) == ("Page 8", "Page 1")
    assert side_texts(pages[1]) == ("Page 2", "Page 7")


def test_booklet_keeps_links_to_other_pages(tmp_path):
    src = numbered_pdf(tmp_path / "doc.pdf", 4)
    writer = PdfWriter(clone_from=str(src))
    from pypdf.annotations import Link
    writer.add_annotation(0, Link(rect=(70, 70, 200, 100), target_page_index=2))
    writer.write(tmp_path / "linked.pdf")
    out = PdfReader(str(core.booklet(tmp_path / "linked.pdf", tmp_path / "booklet.pdf")))
    # Page 1 is on side 1; page 3 is on side 2 (the back of the sheet).
    link = [a.get_object() for a in out.pages[0]["/Annots"]][0]
    assert out.get_page_number(link["/Dest"][0].get_object()) == 1


def test_cli_booklet(tmp_path, capsys):
    src = numbered_pdf(tmp_path / "doc.pdf", 5)
    assert main(["booklet", str(src), "-s", "a3"]) == 0
    assert "2 sheets" in capsys.readouterr().out
    pages = PdfReader(str(tmp_path / "doc_booklet.pdf")).pages
    assert len(pages) == 4 and size_of(pages[0]) == (1191, 842)


def test_gui_booklet(tmp_path):
    src = numbered_pdf(tmp_path / "doc.pdf", 4, size=A5)
    notes = []
    name, data, _ = gui.run_operation("booklet", [src], {"size": ""}, work=work_dir(tmp_path),
                                      notes=notes)
    assert name == "doc_booklet.pdf" and len(PdfReader(BytesIO(data)).pages) == 2
    assert notes and "1 sheet" in notes[0] and "short edge" in notes[0]


# -- trim white margins ------------------------------------------------------------------

def box_pdf(path, boxes, rotate=None):
    """A4 pages with a black rectangle (x0, y0, x1, y1 in points) on each;
    None makes a blank page."""
    c = canvas.Canvas(str(path), pagesize=A4)
    for box in boxes:
        if box:
            x0, y0, x1, y1 = box
            c.rect(x0, y0, x1 - x0, y1 - y0, stroke=0, fill=1)
        c.showPage()
    c.save()
    if rotate:
        writer = PdfWriter(clone_from=str(path))
        for i, degrees in rotate.items():
            writer.pages[i].rotate(degrees)
        writer.write(path)
    return path


def crop_of(page):
    return [round(float(v)) for v in page.cropbox]


def close(a, b, tolerance=2.5):
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def test_trim_white_margins_each_page(tmp_path):
    src = box_pdf(tmp_path / "doc.pdf", [(100, 200, 300, 500), (50, 60, 500, 700), None])
    report = core.trim_margins(src, tmp_path / "out.pdf", padding=10)
    assert report == {"trimmed": [1, 2], "blank": [3]}
    pages = PdfReader(str(tmp_path / "out.pdf")).pages
    assert close(crop_of(pages[0]), [90, 190, 310, 510])
    assert close(crop_of(pages[1]), [40, 50, 510, 710])
    assert crop_of(pages[2]) == [0, 0, 595, 842]  # blank: left alone
    assert all([round(float(v)) for v in p.mediabox] == [0, 0, 595, 842] for p in pages)


def test_trim_white_margins_the_same_for_every_page(tmp_path):
    src = box_pdf(tmp_path / "doc.pdf", [(100, 200, 300, 500), (150, 100, 400, 450)])
    core.trim_margins(src, tmp_path / "out.pdf", padding=0, same=True)
    pages = PdfReader(str(tmp_path / "out.pdf")).pages
    assert close(crop_of(pages[0]), [100, 100, 400, 500])
    assert crop_of(pages[0]) == crop_of(pages[1])


def test_trim_white_margins_of_turned_pages(tmp_path):
    src = box_pdf(tmp_path / "doc.pdf", [(100, 200, 300, 500)] * 4,
                  rotate={1: 90, 2: 180, 3: 270})
    core.trim_margins(src, tmp_path / "out.pdf", padding=5)
    for page in PdfReader(str(tmp_path / "out.pdf")).pages:
        # The crop box is in the page's own coordinates, whichever way it's shown.
        assert close(crop_of(page), [95, 195, 305, 505]), (page.rotation, crop_of(page))


def test_trim_white_margins_never_grows_past_the_page(tmp_path):
    src = box_pdf(tmp_path / "doc.pdf", [(0, 0, 595, 842), (5, 5, 590, 837)])
    core.trim_margins(src, tmp_path / "out.pdf", padding=20, pages="1-2")
    for page in PdfReader(str(tmp_path / "out.pdf")).pages:
        assert crop_of(page) == [0, 0, 595, 842]


def test_cli_crop_auto(tmp_path, capsys):
    src = box_pdf(tmp_path / "doc.pdf", [(100, 200, 300, 500), None])
    assert main(["crop", str(src), "--auto", "--padding", "0"]) == 0
    assert "page 2 is blank" in capsys.readouterr().out
    page = PdfReader(str(tmp_path / "doc_cropped.pdf")).pages[0]
    assert close(crop_of(page), [100, 200, 300, 500])
    assert main(["crop", str(src)]) == 1
    assert main(["crop", str(src), "--auto", "-m", "10"]) == 1


def test_gui_trim_white_margins(tmp_path):
    src = box_pdf(tmp_path / "doc.pdf", [(100, 200, 300, 500), None])
    notes = []
    name, data, _ = gui.run_operation("autocrop", [src], {"padding": "0", "same": "false"},
                                      work=work_dir(tmp_path), notes=notes)
    assert name == "doc_trimmed.pdf"
    assert close(crop_of(PdfReader(BytesIO(data)).pages[0]), [100, 200, 300, 500])
    assert notes == ["doc.pdf: page 2 is blank and was left as it is"]


# -- wiring --------------------------------------------------------------------------------

def test_new_tools_can_be_recipe_steps(tmp_path):
    steps = [{"op": "autocrop", "options": {"padding": "2"}},
             {"op": "insert-blank", "options": {"after": "all"}},
             {"op": "booklet", "options": {}}]
    src = box_pdf(tmp_path / "doc.pdf", [(100, 200, 300, 500)] * 2)
    out = recipes.run(steps, src, tmp_path / "out.pdf")
    assert len(PdfReader(str(out)).pages) == 2  # 4 pages -> one sheet, both sides


def test_new_tools_are_in_the_app():
    ops = {op["id"]: op for op in gui.OPERATIONS}
    assert ops["insert-blank"]["group"] == "Pages" and ops["insert-blank"]["board"]["mode"] == "select"
    assert [c[0] for c in next(f for f in ops["split"]["fields"] if f["key"] == "by")["choices"]] == \
        ["every", "ranges", "bookmarks", "size"]
    html = Path(gui.__file__).with_name("gui.html").read_text(encoding="utf-8")
    for op in ("insert-blank", "booklet", "autocrop"):
        key = f'"{op}"' if "-" in op else op
        assert op in ops and f"{key}: '<" in html and f'{key}: "' in html  # an icon and a tagline
