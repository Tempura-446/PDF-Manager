# Engine 4: search by meaning

## Purpose

Search PDFs found a phrase as typed and nothing else. People ask for what
they want in their own words ("how much do I owe"), and the document says
it in its words ("balance due"). This engine ranks pages by what they are
about, so the question finds the page.

## What was measured first

42 short documents of the kinds people keep, and 44 questions written
without the documents' words (`tests/meaning_corpus.json`).

| way to search | right document first | in the top 3 |
|---|---|---|
| the exact phrase (ZPDF before) | 0 | 0 |
| matching words (BM25) | 19 | 24 |
| meaning, with the words as a small bonus (this engine) | 30 | 38 |

## The model

A table of 32,000 word pieces, each with 128 numbers; a text's vector is
the average of its pieces' vectors (`pdfmanager/meaning.py`). The table
is a shortened copy of WordLlama's "l2_supercat" (MIT): the first 128 of
256 numbers, one byte each, 4.1 MB with its vocabulary
(`pdfmanager/static/meaning/words.npz`, built by
`packaging/make_meaning_data.py`). Halving the numbers and the bytes cost
nothing on the measurement; 64 numbers did.

Only NumPy is needed. The word-piece splitting (byte-pair encoding) is
ZPDF's own code. Loading takes 0.1 s; 5,000 pages are prepared in under
2 s and ranked in about half a second.

## Searching

`search.search(..., how="best")`:

- Each page is cut into overlapping passages of 90 words, one vector
  each, kept in `search.sqlite` beside the page's text (table `vectors`,
  about 1 KB a page) and made again only when the file changes.
- A page's likeness to the question is that of its closest passage.
  Sharing the question's words adds a little (0.1 at most); when the
  search is one to three keywords, pages with all of them come first.
- Shown: every page that has the words, then up to 20 pages close in
  meaning, each at least half as close as the closest.
- When the search is one to three keywords and some page has them all,
  only pages with the words are shown. Words with a digit in them (codes,
  numbers) are matched whole and left out of the question's vector.
- The snippet for keywords is around the first of them in the closest
  passage; for a question it is the dozen words of that passage closest
  in meaning.
- A file whose text is the same as one already read is not listed again;
  the first carries its path in `also`.

`how="exact"` is the search as it was. The app uses "best" unless "These
exact words" is chosen; `pdfm search` uses exact unless `--best`.

## Languages

The table knows English. Chinese, Japanese and Korean have no vectors, so
their characters are left out of a page's vector and matched as words:
each pair of neighbouring characters is a term. A Chinese question finds
the pages with those words, best first, and the result says it was
matched by words. A question in one language does not find a document in
another. A multilingual table would fix both and can replace this one
(`meaning.VERSION` marks stored vectors as out of date).

## Not in this design

- Answering the question in words (that needs a language model).
- Search by meaning inside the reader's Find box.
