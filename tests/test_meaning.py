"""Search by meaning: the model (meaning.py) and the "best" way to search a
folder (search.py)."""

import json
import textwrap
from pathlib import Path

import numpy as np
import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from pdfmanager import core, meaning, search

CORPUS = json.loads((Path(__file__).parent / "meaning_corpus.json").read_text(encoding="utf-8"))


def write_pdf(path, *pages, font="Helvetica"):
    """A PDF with one page per text, wrapped into lines."""
    if font != "Helvetica":
        pdfmetrics.registerFont(UnicodeCIDFont(font))
    c = canvas.Canvas(str(path), pagesize=A4)
    for text in pages:
        c.setFont(font, 11)
        lines = textwrap.wrap(text, 30 if font != "Helvetica" else 85) or [""]
        for i, line in enumerate(lines):
            c.drawString(60, 780 - 15 * i, line)
        c.showPage()
    c.save()
    return path


@pytest.fixture
def folder(tmp_path):
    made = tmp_path / "docs"
    made.mkdir()
    return made


def first(result):
    return Path(result["matches"][0]["path"]).name if result["matches"] else None


# -- the model ---------------------------------------------------------------------

def test_a_vector_has_length_one_and_like_texts_are_close():
    owe = meaning.embed("how much do I owe")
    assert owe.shape == (meaning.SIZE,) and abs(float(np.linalg.norm(owe)) - 1) < 1e-5
    due = float(owe @ meaning.embed("the balance due on the account is 40 pounds"))
    cats = float(owe @ meaning.embed("cats sleep in the afternoon sun"))
    assert due > cats + 0.1


def test_text_the_model_does_not_know_gives_no_vector():
    assert not meaning.embed("").any()
    assert not meaning.embed("发票金额").any()
    assert meaning.embed("invoice 发票").any(), "the English part still counts"


def test_a_long_page_is_cut_into_overlapping_passages():
    text = " ".join(f"word{i}" for i in range(200))
    parts = meaning.passages(text)
    assert parts[0][0] == 0 and parts[-1][1] == len(text) and len(parts) == 3
    assert parts[1][0] < parts[0][1], "they overlap"
    assert meaning.passages("   ") == []
    vectors = meaning.embed_page(text)
    assert vectors.shape == (3, meaning.SIZE)
    assert np.abs(meaning.unpack(meaning.pack(vectors)) - vectors).max() < 0.01
    assert meaning.embed_page("").shape == (0, meaning.SIZE)


def test_odd_words_are_survived():
    for text in ("\x00\x01", "a" * 5000, "🙂 🙂", "▁▁", "ﬁnancial naïve"):
        assert meaning.embed(text).shape == (meaning.SIZE,)


def test_meaning_finds_what_the_words_miss():
    """The measurement the feature was decided on: 44 questions, written without
    the documents' words, over 42 short documents."""
    entries = [(f"{i}.pdf", 1, text, meaning.unpack(meaning.pack(meaning.embed_page(text))))
               for i, text in enumerate(CORPUS["docs"])]
    top, three = 0, 0
    for question, right in CORPUS["queries"]:
        order = [m["path"] for m in search._best(entries, question, 500)[0]]
        place = order.index(f"{right}.pdf") if f"{right}.pdf" in order else 99
        top += place == 0
        three += place < 3
    assert top >= 29 and three >= 37, (top, three)   # words alone: 19 and 24


# -- searching a folder ------------------------------------------------------------

def test_a_question_finds_the_page_that_answers_it(folder):
    write_pdf(folder / "statement.pdf", "Statement of account. The balance due is 240.00 and must "
              "reach us by the end of March.")
    write_pdf(folder / "garden.pdf", "Planting plan for the border: lavender, sage and three roses.")
    write_pdf(folder / "minutes.pdf", "Minutes of the residents' meeting. The roof repair was approved.")
    exact = search.search(folder, "how much do I owe")
    assert exact["matches"] == [] and exact["how"] == "exact"
    best = search.search(folder, "how much do I owe", how="best")
    assert first(best) == "statement.pdf" and best["meaning"] and best["how"] == "best"
    hit = best["matches"][0]
    assert hit["page"] == 1 and hit["match"] and not hit["words"]
    assert hit["match"] in "Statement of account. The balance due is 240.00 and must reach us by the end of March."
    assert first(search.search(folder, "what flowers are we putting in", how="best")) == "garden.pdf"


def test_the_right_page_of_a_long_file(folder):
    filler = "The committee discussed the minutes of the previous meeting and agreed them. " * 12
    write_pdf(folder / "pack.pdf", filler, filler + " The boiler was serviced by the heating engineer "
              "and the gas safety certificate was renewed.", filler)
    best = search.search(folder, "annual check of the central heating", how="best")
    assert (first(best), best["matches"][0]["page"]) == ("pack.pdf", 2)


def test_keywords_put_pages_with_the_words_first_and_show_all_of_them(folder):
    for i in range(25):
        write_pdf(folder / f"letter{i:02}.pdf", f"Letter {i} about the weather, the garden and the holidays.")
    write_pdf(folder / "a.pdf", "Quotation for Mr Okonkwo: replace the garage door.")
    write_pdf(folder / "b.pdf", "Receipt. Received from Mr Okonkwo the sum of 80.00.")
    best = search.search(folder, "Okonkwo", how="best")
    assert {Path(m["path"]).name for m in best["matches"][:2]} == {"a.pdf", "b.pdf"}
    assert all(m["words"] and m["match"].lower() == "okonkwo" for m in best["matches"][:2])
    assert len(best["matches"]) == 2, "and nothing that is merely the least unlike"
    about = search.search(folder, "sunshine and rain during our trip away", how="best")
    assert len(about["matches"]) == search.CLOSEST, "a question shows the closest pages, and no more"
    assert search._terms("the plumber's bill")[0].pattern.endswith("plumber")
    assert len(search._terms("what is the plumber's bill")) == 2


def test_chinese_is_matched_by_its_characters(folder):
    write_pdf(folder / "fapiao.pdf", "增值税发票 金额合计 三千元 请于月底前付款", font="STSong-Light")
    write_pdf(folder / "hetong.pdf", "房屋租赁合同 租金每月五千元 押金两个月", font="STSong-Light")
    write_pdf(folder / "english.pdf", "An invoice for services rendered.")
    best = search.search(folder, "发票金额", how="best")
    assert [Path(m["path"]).name for m in best["matches"]] == ["fapiao.pdf"]
    assert best["meaning"] is False
    assert best["matches"][0]["match"] in ("发票", "票金", "金额", "票 金")
    assert first(search.search(folder, "租金", how="best")) == "hetong.pdf"
    assert search.search(folder, "火车", how="best")["matches"] == []


def test_vectors_are_made_once_and_again_when_the_file_changes(folder, monkeypatch):
    path = write_pdf(folder / "a.pdf", "The balance due is 240.00.", "Second page about roses.")
    made = []
    embed_page = meaning.embed_page
    monkeypatch.setattr(meaning, "embed_page", lambda text: made.append(text) or embed_page(text))
    search.search(folder, "balance")  # an exact search makes none
    assert made == []
    search.search(folder, "money owed", how="best")
    assert len(made) == 2
    search.search(folder, "roses", how="best")
    assert len(made) == 2
    write_pdf(path, "Now a letter about the roof and nothing else at all.")
    assert first(search.search(folder, "roof", how="best")) == "a.pdf"
    assert len(made) == 3
    conn = search._db()
    assert conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 1
    conn.close()


def test_best_reports_unreadable_files_and_empty_folders(folder):
    assert search.search(folder, "anything", how="best")["matches"] == []
    (folder / "junk.pdf").write_bytes(b"junk")
    write_pdf(folder / "blank.pdf", "")
    result = search.search(folder, "anything at all", how="best")
    assert result["matches"] == [] and result["skipped"] == [(str(folder / "junk.pdf"), "can't be read")]
    with pytest.raises(core.PdfManagerError):
        search.search(folder, "x", how="sideways")
    with pytest.raises(core.PdfManagerError):
        search.search(folder, "  ", how="best")


def test_the_limit_holds(folder):
    for i in range(6):
        write_pdf(folder / f"{i}.pdf", f"Invoice number {i} for plumbing work.")
    result = search.search(folder, "invoice", how="best", limit=4)
    assert len(result["matches"]) == 4 and result["limited"]
