"""Search the text of every PDF in a folder.

The text of each file is read once and kept in a small database in the
app's data folder (search.sqlite), keyed by path, size and modification
time, so searching the same folder again is quick and only new or changed
files are read again. Scans without a text layer need OCR first.
"""

import re
import sqlite3
import threading
from pathlib import Path

from pdfmanager import core, organize, progress
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
        error = "password protected" if "password" in str(exc).lower() else "can't be read"
    with conn:
        conn.execute("DELETE FROM pages WHERE path = ?", (key,))
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


def search(directory, query, recursive=False, case_sensitive=False, whole_words=False,
           limit=500):
    """Find ``query`` in the PDFs in ``directory``. Returns {"matches": [{"path",
    "page", "count", "before", "match", "after"}], "files": number searched,
    "skipped": [(path, reason)]}, one match entry per page (with the number of
    hits on it), at most ``limit``."""
    directory = Path(directory).expanduser()
    if not directory.is_dir():
        raise core.PdfManagerError(f"{directory} is not a folder")
    pattern = _pattern(query, case_sensitive, whole_words)
    files = organize.find_pdfs(directory, recursive)
    matches, skipped = [], []
    with _lock:
        conn = _db()
        try:
            for i, path in enumerate(files):
                progress.report(i, len(files), message=f"searching {path.name} "
                                                       f"({i + 1} of {len(files)})")
                texts, error = _pages(conn, path)
                if error:
                    skipped.append((str(path), error))
                    continue
                for number, text in texts:
                    found = list(pattern.finditer(text))
                    if found:
                        before, hit, after = _snippet(text, found[0])
                        matches.append({"path": str(path), "page": number, "count": len(found),
                                        "before": before, "match": hit, "after": after})
                        if len(matches) >= limit:
                            return {"matches": matches, "files": i + 1, "skipped": skipped,
                                    "limited": True}
        finally:
            conn.close()
    return {"matches": matches, "files": len(files), "skipped": skipped, "limited": False}


def forget_index():
    """Delete the cached text (e.g. to save space)."""
    with _lock:
        (data_dir() / "search.sqlite").unlink(missing_ok=True)
