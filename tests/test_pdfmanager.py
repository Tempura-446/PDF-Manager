import csv
import os

import pytest
from PIL import Image
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4, letter, landscape
from reportlab.pdfgen import canvas

from pdfmanager import core, organize
from pdfmanager.cli import main
from pdfmanager.pageranges import PageRangeError, parse_page_ranges


def make_pdf(path, pages=3, size=A4, title=None, author=None):
    c = canvas.Canvas(str(path), pagesize=size)
    if title:
        c.setTitle(title)
    if author:
        c.setAuthor(author)
    for i in range(pages):
        c.drawString(72, 72, f"Page {i + 1}")
        c.showPage()
    c.save()
    return path


def page_texts(path):
    return [p.extract_text().strip() for p in PdfReader(str(path)).pages]


def page_size(path, i=0):
    box = PdfReader(str(path)).pages[i].mediabox
    return round(float(box.width)), round(float(box.height))


@pytest.fixture
def doc(tmp_path):
    return make_pdf(tmp_path / "doc.pdf", pages=5)


# -- page ranges -----------------------------------------------------------

@pytest.mark.parametrize("spec,expected", [
    ("1-3,5", [0, 1, 2, 4]),
    ("7-", [6, 7, 8, 9]),
    ("-2", [0, 1]),
    ("end", [9]),
    ("3-1", [2, 1, 0]),
    ("odd", [0, 2, 4, 6, 8]),
    ("even", [1, 3, 5, 7, 9]),
    ("all", list(range(10))),
    ("2,2", [1, 1]),
])
def test_parse_page_ranges(spec, expected):
    assert parse_page_ranges(spec, 10) == expected


@pytest.mark.parametrize("spec", ["0", "11", "abc", "", "1-20"])
def test_parse_page_ranges_errors(spec):
    with pytest.raises(PageRangeError):
        parse_page_ranges(spec, 10)


# -- core ------------------------------------------------------------------

def test_info(doc):
    data = core.info(doc)
    assert data["pages"] == 5
    assert data["page_sizes"] == {"A4": 5}


def test_merge_with_page_selection(tmp_path, doc):
    other = make_pdf(tmp_path / "other.pdf", pages=2)
    out = core.merge([doc, (other, "2")], tmp_path / "m.pdf")
    assert page_texts(out) == ["Page 1", "Page 2", "Page 3", "Page 4", "Page 5", "Page 2"]
    assert [o.title for o in PdfReader(str(out)).outline] == ["doc", "other"]


def test_split_each_page(tmp_path, doc):
    files = core.split(doc, tmp_path / "out")
    assert [f.name for f in files] == [f"doc_{i}.pdf" for i in range(1, 6)]
    assert page_texts(files[3]) == ["Page 4"]


def test_split_every_and_ranges(tmp_path, doc):
    files = core.split(doc, tmp_path / "a", every=2)
    assert [len(PdfReader(str(f)).pages) for f in files] == [2, 2, 1]
    files = core.split(doc, tmp_path / "b", ranges=["1-2", "3-"])
    assert [page_texts(f) for f in files] == [["Page 1", "Page 2"], ["Page 3", "Page 4", "Page 5"]]


def test_extract_delete_reorder_reverse(tmp_path, doc):
    assert page_texts(core.select_pages(doc, tmp_path / "e.pdf", "2,4")) == ["Page 2", "Page 4"]
    assert page_texts(core.delete_pages(doc, tmp_path / "d.pdf", "1-3")) == ["Page 4", "Page 5"]
    assert page_texts(core.select_pages(doc, tmp_path / "r.pdf", "5,1-4"))[0] == "Page 5"
    assert page_texts(core.reverse(doc, tmp_path / "v.pdf"))[0] == "Page 5"
    with pytest.raises(core.PdfManagerError):
        core.delete_pages(doc, tmp_path / "x.pdf", "all")


def test_interleave(tmp_path):
    front = make_pdf(tmp_path / "f.pdf", pages=2)
    back = make_pdf(tmp_path / "b.pdf", pages=2)
    out = core.interleave(front, back, tmp_path / "i.pdf")
    assert page_texts(out) == ["Page 1", "Page 2", "Page 2", "Page 1"]


def test_rotate(tmp_path, doc):
    out = core.rotate(doc, tmp_path / "r.pdf", 90, pages="2")
    rotations = [p.rotation for p in PdfReader(str(out)).pages]
    assert rotations == [0, 90, 0, 0, 0]


def test_overwrite_input_in_place(doc):
    core.rotate(doc, doc, 180)
    assert PdfReader(str(doc)).pages[0].rotation == 180


def test_resize_to_paper_size(tmp_path):
    src = make_pdf(tmp_path / "letter.pdf", pages=1, size=letter)
    out = core.resize(src, tmp_path / "a4.pdf", size="a4")
    assert page_size(out) == (595, 842)
    assert page_texts(out) == ["Page 1"]


def test_resize_keeps_landscape(tmp_path):
    src = make_pdf(tmp_path / "land.pdf", pages=1, size=landscape(letter))
    assert page_size(core.resize(src, tmp_path / "o.pdf", size="a4")) == (842, 595)
    out = core.resize(src, tmp_path / "p.pdf", size="a4", auto_orient=False)
    assert page_size(out) == (595, 842)


def test_resize_rotated_page(tmp_path, doc):
    rotated = core.rotate(doc, tmp_path / "rot.pdf", 90)
    out = core.resize(rotated, tmp_path / "o.pdf", size="a5")
    # A rotated A4 page is landscape, so it becomes landscape A5.
    assert page_size(out) == (595, 420)


def test_resize_scale(tmp_path, doc):
    assert page_size(core.resize(doc, tmp_path / "s.pdf", scale=0.5)) == (298, 421)


@pytest.mark.parametrize("spec,expected", [
    ("a4", (595, 842)),
    ("A4-landscape", (842, 595)),
    ("100x200mm", (283, 567)),
    ("8.5x11in", (612, 792)),
    ("300x400", (300, 400)),
])
def test_parse_paper_size(spec, expected):
    w, h = core.parse_paper_size(spec)
    assert (round(w), round(h)) == expected


def test_crop(tmp_path, doc):
    out = core.crop(doc, tmp_path / "c.pdf", (10, 20, 30, 40))
    assert page_size(out) == (595 - 40, 842 - 60)


def test_n_up(tmp_path, doc):
    out = core.n_up(doc, tmp_path / "n.pdf", per_sheet=2)
    reader = PdfReader(str(out))
    assert len(reader.pages) == 3
    assert page_size(out) == (842, 595)
    assert "Page 1" in reader.pages[0].extract_text() and "Page 2" in reader.pages[0].extract_text()


def test_compress(tmp_path, doc):
    out = core.compress(doc, tmp_path / "c.pdf")
    assert page_texts(out) == page_texts(doc)


def test_encrypt_decrypt(tmp_path, doc):
    enc = core.encrypt(doc, tmp_path / "e.pdf", "secret")
    assert PdfReader(str(enc)).is_encrypted
    with pytest.raises(core.PdfManagerError):
        core.info(enc)
    with pytest.raises(core.PdfManagerError):
        core.info(enc, password="wrong")
    dec = core.decrypt(enc, tmp_path / "d.pdf", "secret")
    assert not PdfReader(str(dec)).is_encrypted
    assert page_texts(dec) == page_texts(doc)


def test_metadata(tmp_path, doc):
    out = core.set_metadata(doc, tmp_path / "m.pdf", title="Report", author="Ann")
    data = core.info(out)
    assert (data["title"], data["author"]) == ("Report", "Ann")


def test_watermark_and_numbers(tmp_path, doc):
    out = core.watermark(doc, tmp_path / "w.pdf", text="DRAFT", pages="1")
    texts = page_texts(out)
    assert "DRAFT" in texts[0] and "DRAFT" not in texts[1]
    out = core.number_pages(doc, tmp_path / "n.pdf", fmt="p{n}/{total}")
    assert "p3/5" in page_texts(out)[2]


def test_images_round_trip(tmp_path):
    img = tmp_path / "pic.png"
    Image.new("RGB", (200, 100), "red").save(img)
    out = core.images_to_pdf([img, img], tmp_path / "pics.pdf", size="a4")
    assert len(PdfReader(str(out)).pages) == 2
    assert page_size(out) == (842, 595)
    pytest.importorskip("pypdfium2")
    files = core.pdf_to_images(out, tmp_path / "imgs", dpi=36)
    assert len(files) == 2 and all(f.exists() for f in files)


# -- organize --------------------------------------------------------------

@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    make_pdf(lib / "a.pdf", pages=1, title="Alpha", author="Ann")
    make_pdf(lib / "b.pdf", pages=12, title="Beta", author="Bob")
    make_pdf(lib / "c.pdf", pages=3, author="Ann")
    (lib / "broken.pdf").write_bytes(b"not a pdf")
    return lib


def test_organize_by_author(tmp_path, library):
    dest = tmp_path / "sorted"
    plan = organize.organize(library, by="author", dest=dest)
    assert len(plan) == 4
    assert sorted(p.name for p in (dest / "Ann").iterdir()) == ["a.pdf", "c.pdf"]
    assert (dest / "Bob" / "b.pdf").exists()
    assert (dest / "Unreadable" / "broken.pdf").exists()
    assert (library / "a.pdf").exists()  # copied, not moved


def test_organize_move_dry_run_and_pages(library):
    plan = organize.organize(library, by="pages", move=True, dry_run=True)
    assert {dst.parent.name for _, dst in plan} == {"1 page", "2-5 pages", "11-25 pages", "Unreadable"}
    assert not (library / "1 page").exists()
    organize.organize(library, by="pages", move=True)
    assert not (library / "a.pdf").exists()
    assert (library / "11-25 pages" / "b.pdf").exists()
    # Running again finds nothing new at the top level.
    assert organize.organize(library, by="pages", move=True) == []


def test_rename(library):
    plan = organize.rename(library, pattern="{author} - {title}")
    names = sorted(dst.name for _, dst in plan)
    assert names == ["Ann - Alpha.pdf", "Ann - c.pdf", "Bob - Beta.pdf", "Unknown - broken.pdf"]
    assert (library / "Bob - Beta.pdf").exists()


def test_rename_collision(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    make_pdf(lib / "x.pdf", title="Same")
    make_pdf(lib / "y.pdf", title="Same")
    organize.rename(lib, pattern="{title}")
    assert sorted(p.name for p in lib.iterdir()) == ["Same (2).pdf", "Same.pdf"]


def test_catalog(tmp_path, library):
    out, count = organize.catalog(library, tmp_path / "cat.csv")
    assert count == 4
    rows = list(csv.DictReader(open(out)))
    assert {r["file"].split(os.sep)[-1]: r["pages"] for r in rows}["b.pdf"] == "12"


# -- CLI -------------------------------------------------------------------

def test_cli_end_to_end(tmp_path, doc, capsys):
    other = make_pdf(tmp_path / "other.pdf", pages=2)
    merged = tmp_path / "merged.pdf"
    assert main(["merge", str(doc), f"{other}:1", "-o", str(merged)]) == 0
    assert len(PdfReader(str(merged)).pages) == 6

    assert main(["resize", str(merged), "-s", "letter"]) == 0
    assert page_size(tmp_path / "merged_resized.pdf") == (612, 792)

    assert main(["split", str(merged), "-n", "3", "-d", str(tmp_path / "parts")]) == 0
    assert len(list((tmp_path / "parts").iterdir())) == 2

    assert main(["crop", str(doc), "10", "-o", str(tmp_path / "c.pdf")]) == 0
    assert main(["info", str(doc), "--json"]) == 0
    assert '"pages": 5' in capsys.readouterr().out


def test_cli_reports_errors(tmp_path, doc, capsys):
    assert main(["extract", str(doc), "9"]) == 1
    assert "out of range" in capsys.readouterr().err
    assert main(["info", str(tmp_path / "missing.pdf")]) == 1
