# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Search the text of every PDF in a folder.

The text of each file is read once and kept in a small database in the
app's data folder (search.sqlite), keyed by path, size and modification
time, so searching the same folder again is quick and only new or changed
files are read again. Scans without a text layer need OCR first.
"""

import math
import re
import sqlite3
import threading
from pathlib import Path

from pdfmanager import core, meaning, organize, progress
from pdfmanager.appdata import data_dir, touch_private

_lock = threading.Lock()


def _db():
    target = data_dir() / "search.sqlite"
    touch_private(target)  # it holds the text of every PDF searched
    conn = sqlite3.connect(target, timeout=30)
    conn.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, size INTEGER, "
                 "mtime REAL, pages INTEGER, error TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS pages (path TEXT, page INTEGER, text TEXT, "
                 "PRIMARY KEY (path, page))")
    conn.execute("CREATE TABLE IF NOT EXISTS vectors (path TEXT, page INTEGER, model INTEGER, "
                 "data BLOB, PRIMARY KEY (path, page))")
    return conn


def _extract(path):
    """[(page number, text)] for a PDF, read with pdfium."""
    import pypdfium2 as pdfium

    texts = []
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path))
        try:
            for number, page in enumerate(doc, 1):
                textpage = page.get_textpage()
                texts.append((number, " ".join(textpage.get_text_range().split())))
                textpage.close()
        finally:
            doc.close()
    return texts


def _pages(conn, path):
    """The (cached) text of a file's pages, or an error message."""
    stat = path.stat()
    key = str(path)
    row = conn.execute("SELECT size, mtime, error FROM files WHERE path = ?", (key,)).fetchone()
    if row and row[0] == stat.st_size and abs(row[1] - stat.st_mtime) < 1e-6:
        if row[2]:
            return [], row[2]
        return conn.execute("SELECT page, text FROM pages WHERE path = ? ORDER BY page",
                            (key,)).fetchall(), None
    error = None
    try:
        texts = _extract(path)
    except Exception as exc:  # unreadable or password-protected
        texts = []
        message = str(exc).lower()
        error = "password protected" if "password" in message else "can't be read"
        # Only a fault in the file itself is worth remembering; a file that is
        # locked, on a share that dropped out or failed to read may well open
        # next time.
        if not isinstance(exc, RuntimeError) or "file access" in message:
            return texts, error
    with conn:
        conn.execute("DELETE FROM pages WHERE path = ?", (key,))
        conn.execute("DELETE FROM vectors WHERE path = ?", (key,))
        conn.executemany("INSERT INTO pages VALUES (?, ?, ?)", [(key, n, t) for n, t in texts])
        conn.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?)",
                     (key, stat.st_size, stat.st_mtime, len(texts), error))
    return texts, error


def _pattern(query, case_sensitive=False, whole_words=False):
    words = query.split()
    if not words:
        raise core.PdfManagerError("enter something to search for")
    body = r"\s+".join(re.escape(w) for w in words)  # any spacing or line break between words
    if whole_words:
        body = rf"(?<!\w){body}(?!\w)"
    return re.compile(body, 0 if case_sensitive else re.IGNORECASE)


def _snippet(text, match, width=70):
    start, end = max(0, match.start() - width), min(len(text), match.end() + width)
    before, found, after = text[start:match.start()], text[match.start():match.end()], text[match.end():end]
    return ("…" if start else "") + before, found, after + ("…" if end < len(text) else "")


def _vectors(conn, path, texts):
    """{page number: one vector per passage} for a file, made once and kept."""
    key = str(path)
    kept = {page: meaning.unpack(data) for page, data in conn.execute(
        "SELECT page, data FROM vectors WHERE path = ? AND model = ?", (key, meaning.VERSION))}
    missing = [(number, text) for number, text in texts if number not in kept]
    if missing:
        made = [(number, meaning.pack(meaning.embed_page(text))) for number, text in missing]
        with conn:
            conn.executemany("INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?)",
                             [(key, number, meaning.VERSION, data) for number, data in made])
        kept.update((number, meaning.unpack(data)) for number, data in made)
    return kept


# Words that say nothing about what a question is after.
STOP = frozenset("""a an and are as at be by can did do does for from how i in is it me my of on
or our that the this to was we were what when where which who why with you your much many
there their they have has had will would should could about""".split())

WORDS = 0.1       # how much sharing the question's words adds to a page's likeness
KEYWORDS = 0.5    # ... when the search is a few keywords and the page has them all
CLOSEST = 20      # pages shown for their meaning alone, at most
FLOOR = 0.15      # ... and no page less like the question than this
TERMS = 16        # words of a question that are counted
NEAR = 0.5        # ... each at least this fraction as close as the closest page
_RUN = re.compile(f"{meaning.CJK.pattern}+")


def _terms(query):
    """The question as patterns to count on a page: its words (matching the
    start of a word, so "invoice" finds "invoices"), and for Chinese, Japanese
    and Korean, which have no spaces, each pair of neighbouring characters."""
    terms = {}
    for run in _RUN.findall(query):
        for pair in ([run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]):
            terms.setdefault(pair, re.compile(r"\s*".join(re.escape(c) for c in pair)))
    for word in re.findall(r"[^\W_]*\d[^\W_]*(?:[.,]\d+)*|[^\W\d_]+",
                           re.sub(r"['’]s\b", " ", _RUN.sub(" ", query))):
        if any(c.isdigit() for c in word):  # a number or a code is matched whole: 3.5 is not 35 or 3.55
            terms.setdefault(word.lower(), re.compile(
                rf"(?<![^\W_])(?<!\d[.,]){re.escape(word)}(?![.,]?\d)(?![^\W_])", re.IGNORECASE))
        elif word.lower() not in STOP and len(word) > 1:
            terms.setdefault(word.lower(), re.compile(rf"\b{re.escape(word)}", re.IGNORECASE))
    return list(terms.values())[:TERMS]


def _word_scores(pages, terms):
    """How well each page matches the question's words (BM25), and whether it
    has every one of them."""
    count = len(pages)
    scores, hits, complete = [0.0] * count, [0] * count, [bool(terms)] * count
    if not count or not terms:
        return scores, hits, complete
    average = sum(len(text) for text in pages) / count or 1
    for term in terms:
        found = [len(term.findall(text)) for text in pages]
        having = sum(1 for n in found if n)
        if not having:
            complete = [False] * count
            continue
        rarity = math.log(1 + (count - having + 0.5) / (having + 0.5))
        for i, n in enumerate(found):
            if n:
                scores[i] += rarity * n * 2.2 / (n + 1.2 * (0.25 + 0.75 * len(pages[i]) / average))
                hits[i] += n
            else:
                complete[i] = False
    return scores, hits, complete


def _best_snippet(text, terms, question, vectors, keywords):
    """The part of a page to show, and whether it is a word of the search. For
    keywords: around the first of them found in the closest passage. For a
    question: the dozen words of that passage closest to it in meaning."""
    parts = meaning.passages(text)
    if not parts:
        return ("", "", ""), False
    best = 0
    if question is not None and len(vectors) == len(parts):
        best = int((vectors @ question).argmax())
    start, end = parts[best]
    for area in ((start, end), (0, len(text))) if keywords or question is None else ():
        for term in terms:
            found = term.search(text, *area)
            if found:
                return _snippet(text, found), True
    if question is None:
        return ("", "", ""), False
    words = [m.span() for m in re.finditer(r"\S+", text[start:end])]
    words = [(a, min(b, a + 40)) for a, b in words]  # a run without spaces isn't a word to show
    top, where = -2.0, (0, min(12, len(words)))
    for first in range(0, max(1, len(words) - 11), 4):
        last = min(first + 12, len(words))
        likeness = float(meaning.embed(text[start + words[first][0]:start + words[last - 1][1]]) @ question)
        if likeness > top:
            top, where = likeness, (first, last)
    first = start + words[where[0]][0]
    found = re.compile(r"(?s).*").match(text, first, min(start + words[where[1] - 1][1], first + 160))
    return _snippet(text, found), False


def _best(entries, query, limit):
    """Rank pages for a question. ``entries`` is [(path, page number, text,
    vectors)]; returns the match entries, best first."""
    terms = _terms(query)
    # A code or a number (SA353569347) has no meaning to compare, only itself.
    question = meaning.embed(" ".join(w for w in query.split() if not any(c.isdigit() for c in w)))
    if not question.any():
        question = None  # nothing the model knows (Chinese, a number): the words decide
    scores, hits, complete = _word_scores([text for _, _, text, _ in entries], terms)
    most = max(scores, default=0) or 1
    keywords = len(query.split()) <= 3
    ranked = []
    for i, (path, number, text, vectors) in enumerate(entries):
        if question is None:
            if scores[i]:
                ranked.append((scores[i] / most, True, i))
            continue
        likeness = float((vectors @ question).max()) if len(vectors) else 0.0
        if not scores[i] and likeness <= 0:
            continue
        score = likeness + WORDS * scores[i] / most + (KEYWORDS if keywords and complete[i] else 0)
        ranked.append((score, bool(scores[i]), i, likeness))
    ranked.sort(key=lambda r: (-r[0], r[2]))
    # Every page that has the words, and beyond those the closest in meaning:
    # a few, and only those in the same league as the closest of all.
    # A few keywords that some pages have in full want those pages, not likenesses.
    closest = max((r[3] for r in ranked if len(r) > 3), default=0)
    found_whole = keywords and any(complete)
    chosen, by_meaning = [], 0
    for score, has_words, i, *likeness in ranked:
        if not has_words:
            if found_whole or by_meaning >= CLOSEST or likeness[0] < max(FLOOR, NEAR * closest):
                continue
            by_meaning += 1
        chosen.append((score, i))
    matches = []
    for score, i in chosen[:limit]:
        path, number, text, vectors = entries[i]
        (before, hit, after), word = _best_snippet(text, terms, question, vectors, keywords)
        matches.append({"path": str(path), "page": number, "count": max(1, hits[i]),
                        "before": before, "match": hit, "after": after,
                        "words": bool(hits[i]), "find": hit if word else "",
                        "score": round(score, 3)})
    return matches, len(chosen) > limit, question is not None


def search(directory, query, recursive=False, case_sensitive=False, whole_words=False,
           limit=500, how="exact"):
    """Find ``query`` in the PDFs in ``directory``. Returns {"matches": [{"path",
    "page", "count", "before", "match", "after"}], "files": number searched,
    "skipped": [(path, reason)], "limited", "how"}, one match entry per page,
    at most ``limit``.

    ``how`` is "exact" (the words as typed, as one phrase; in the order the
    files are found) or "best" (pages ranked by what they are about and the
    words they share with the query, best first; "meaning" in the result says
    whether the query's meaning could be used, which it can't for Chinese)."""
    directory = Path(directory).expanduser()
    if not directory.is_dir():
        raise core.PdfManagerError(f"{directory} is not a folder")
    if how not in ("exact", "best"):
        raise core.PdfManagerError(f"unknown way to search {how!r}")
    pattern = _pattern(query, case_sensitive, whole_words)
    if how == "best":
        meaning.embed(query)  # load the table now: a fault in it is not a fault in a PDF
    files = organize.find_pdfs(directory, recursive)
    matches, skipped, entries, same = [], [], [], {}
    with _lock:
        conn = _db()
        try:
            for i, path in enumerate(files):
                progress.report(i, len(files), message=f"searching {path.name} "
                                                       f"({i + 1} of {len(files)})")
                try:
                    texts, error = _pages(conn, path)
                    vectors = _vectors(conn, path, texts) if how == "best" and not error else {}
                except FileNotFoundError:
                    error = "removed while searching"
                except OSError:
                    error = "can't be read"
                if error:
                    skipped.append((str(path), error))
                    continue
                if how == "best":
                    # A second copy of a file (the same text on every page) is named, not listed again.
                    whole = hash(tuple(text for _, text in texts))
                    if texts and any(text for _, text in texts) and whole in same:
                        same[whole][1].append(str(path))
                        continue
                    same[whole] = (str(path), [])
                    entries.extend((path, number, text, vectors[number]) for number, text in texts)
                    continue
                for number, text in texts:
                    found = list(pattern.finditer(text))
                    if found:
                        before, hit, after = _snippet(text, found[0])
                        matches.append({"path": str(path), "page": number, "count": len(found),
                                        "before": before, "match": hit, "after": after})
                        if len(matches) >= limit:
                            return {"matches": matches, "files": i + 1, "skipped": skipped,
                                    "limited": True, "how": how}
        finally:
            conn.close()
    result = {"matches": matches, "files": len(files), "skipped": skipped, "limited": False,
              "how": how}
    if how == "best":
        result["matches"], result["limited"], result["meaning"] = _best(entries, query, limit)
        copies = dict(same.values())
        for match in result["matches"]:
            match["also"] = copies.get(match["path"], [])
    return result


def forget_index():
    """Delete the cached text (e.g. to save space)."""
    with _lock:
        (data_dir() / "search.sqlite").unlink(missing_ok=True)
