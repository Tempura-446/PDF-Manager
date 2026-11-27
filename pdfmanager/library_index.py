# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Persistent SQLite FTS5 Library Index and Offline Bookshelf RAG.

Maintains an incremental ``mtime_ns``-cached SQLite FTS5 full-text index across
all PDFs and EPUBs in configured Bookshelf folders for sub-20 ms BM25-ranked
search and grounded local-LLM "Ask Your Bookshelf" answers with citations.
"""

import logging
import re
import sqlite3
import threading
import zipfile
from pathlib import Path

from pdfmanager import appdata, core, epub

_LOG = logging.getLogger("zpdf")
_DB_LOCK = threading.Lock()
_FTS5_AVAILABLE = None

_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for",
    "from", "how", "i", "in", "is", "it", "me", "my", "of", "on", "or",
    "say", "says", "tell", "that", "the", "this", "to", "what", "when",
    "where", "which", "who", "why", "with", "about", "into", "can", "could",
})


def _db_path():
    return appdata.data_dir() / "library-index.sqlite"


def _natural_key(text):
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", str(text))]


def _connect():
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=15)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn):
    global _FTS5_AVAILABLE
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS books (
            path TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            name TEXT NOT NULL,
            stem TEXT NOT NULL,
            ext TEXT NOT NULL,
            folder TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            size_bytes INTEGER NOT NULL,
            page_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    if _FTS5_AVAILABLE is not False:
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5("
                "path UNINDEXED, page UNINDEXED, content, tokenize='unicode61')"
            )
            _FTS5_AVAILABLE = True
        except sqlite3.OperationalError as exc:
            _LOG.debug("library_index: FTS5 unavailable, using plain table fallback: %s", exc)
            _FTS5_AVAILABLE = False
    if not _FTS5_AVAILABLE:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pages_plain ("
            "path TEXT NOT NULL, page INTEGER NOT NULL, content TEXT NOT NULL)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pages_plain_path ON pages_plain(path)")
    conn.commit()


def _extract_pdf_pages(path):
    from pypdf import PdfReader

    out = []
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    return out
            except Exception as exc:  # noqa: BLE001
                _LOG.debug("library_index._extract_pdf_pages decrypt %s: %s", path, exc)
                return out
        for page_idx, page in enumerate(reader.pages, 1):
            try:
                txt = " ".join((page.extract_text() or "").split())
            except Exception as exc:  # noqa: BLE001
                _LOG.debug("library_index._extract_pdf_pages %s #%d: %s", path, page_idx, exc)
                continue
            if txt:
                out.append((page_idx, txt))
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("library_index._extract_pdf_pages %s: %s", path, exc)
    return out


def _extract_epub_pages(path):
    out = []
    if not zipfile.is_zipfile(path):
        return out
    try:
        with zipfile.ZipFile(path, "r") as zf:
            members = sorted(
                (
                    inf for inf in zf.infolist()
                    if not inf.is_dir()
                    and inf.file_size <= 8 * 1024 * 1024
                    and Path(inf.filename).suffix.lower() in (".xhtml", ".html", ".htm")
                ),
                key=lambda inf: _natural_key(inf.filename),
            )
            for chap_idx, inf in enumerate(members, 1):
                try:
                    raw = zf.read(inf).decode("utf-8", errors="ignore")
                    parser = epub._ChapterParser()
                    parser.feed(raw)
                    parser.close()
                    txt = " ".join(t for tag, t in parser.blocks if tag != "img" and t)
                except Exception as exc:  # noqa: BLE001
                    _LOG.debug("library_index._extract_epub_pages %s %s: %s", path, inf.filename, exc)
                    continue
                if txt:
                    out.append((chap_idx, txt))
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("library_index._extract_epub_pages %s: %s", path, exc)
    return out


def sync_index(items):
    """Incrementally synchronize the SQLite index with ``items`` (from Bookshelf).

    Only new or modified PDFs/EPUBs (by ``mtime_ns`` + ``size_bytes``) are parsed.
    """
    candidates = {}
    for it in items or ():
        ext = str(it.get("ext") or "").lower()
        if ext not in ("pdf", "epub"):
            continue
        p = Path(it["path"])
        try:
            st = p.stat()
        except OSError:
            continue
        candidates[it["path"]] = (it, int(st.st_mtime_ns), int(st.st_size))

    indexed = 0
    skipped = 0
    removed = 0
    with _DB_LOCK:
        conn = _connect()
        try:
            existing = {
                row["path"]: (int(row["mtime_ns"]), int(row["size_bytes"]))
                for row in conn.execute("SELECT path, mtime_ns, size_bytes FROM books")
            }
            table = "pages_fts" if _FTS5_AVAILABLE else "pages_plain"

            stale = [path for path in existing if path not in candidates]
            for path in stale:
                conn.execute("DELETE FROM books WHERE path = ?", (path,))
                conn.execute(f"DELETE FROM {table} WHERE path = ?", (path,))
                removed += 1

            for path, (it, mtime_ns, size_bytes) in candidates.items():
                prev = existing.get(path)
                if prev == (mtime_ns, size_bytes):
                    skipped += 1
                    continue
                ext = str(it.get("ext") or "").lower()
                pages = _extract_pdf_pages(path) if ext == "pdf" else _extract_epub_pages(path)
                conn.execute("DELETE FROM books WHERE path = ?", (path,))
                conn.execute(f"DELETE FROM {table} WHERE path = ?", (path,))
                conn.execute(
                    "INSERT INTO books(path, book_id, name, stem, ext, folder, mtime_ns, size_bytes, page_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (path, it["id"], it["name"], it["stem"], it["ext"], it["folder"],
                     mtime_ns, size_bytes, len(pages)),
                )
                if pages:
                    conn.executemany(
                        f"INSERT INTO {table}(path, page, content) VALUES (?, ?, ?)",
                        [(path, pg, txt) for pg, txt in pages],
                    )
                indexed += 1

            conn.commit()
            total_books = int(conn.execute("SELECT COUNT(*) FROM books").fetchone()[0])
            total_pages = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            conn.close()

    return {
        "indexed": indexed,
        "skipped": skipped,
        "removed": removed,
        "total_books": total_books,
        "total_pages": total_pages,
        "engine": "sqlite-fts5" if _FTS5_AVAILABLE else "sqlite-like",
    }


def index_status():
    """Return current index statistics without triggering a rescan."""
    p = _db_path()
    if not p.is_file():
        return {"total_books": 0, "total_pages": 0, "db_bytes": 0, "engine": "sqlite-fts5"}
    with _DB_LOCK:
        conn = _connect()
        try:
            table = "pages_fts" if _FTS5_AVAILABLE else "pages_plain"
            total_books = int(conn.execute("SELECT COUNT(*) FROM books").fetchone()[0])
            total_pages = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            conn.close()
    try:
        db_bytes = int(p.stat().st_size)
    except OSError:
        db_bytes = 0
    return {
        "total_books": total_books,
        "total_pages": total_pages,
        "db_bytes": db_bytes,
        "engine": "sqlite-fts5" if _FTS5_AVAILABLE else "sqlite-like",
    }


def _make_snippet(text, terms, radius=72):
    clean = " ".join(str(text or "").split())
    if not clean:
        return ("", "", "")
    lower = clean.lower()
    best_pos = -1
    best_len = 0
    for term in terms:
        t = term.lower()
        if not t:
            continue
        pos = lower.find(t)
        if pos >= 0 and (best_pos < 0 or pos < best_pos):
            best_pos = pos
            best_len = len(t)
    if best_pos < 0:
        return ("", clean[:radius * 2] + ("…" if len(clean) > radius * 2 else ""), "")
    start = max(0, best_pos - radius)
    end = min(len(clean), best_pos + best_len + radius)
    before = ("…" if start > 0 else "") + clean[start:best_pos]
    match = clean[best_pos:best_pos + best_len]
    after = clean[best_pos + best_len:end] + ("…" if end < len(clean) else "")
    return (before, match, after)


def _fts_query_tokens(raw_query, *, use_or=False):
    tokens = re.findall(r"[^\s\"'()*:]+", str(raw_query or "").strip())
    if use_or:
        filtered = [t for t in tokens if t.lower() not in _STOP_WORDS and len(t) >= 2]
        if filtered:
            tokens = filtered
    if not tokens:
        return None, []
    quoted = ['"' + t.replace('"', '""') + '"' for t in tokens[:12]]
    expr = (" OR " if use_or else " ").join(quoted)
    return expr, tokens


def search_fts(query, items, *, limit=50, max_per_book=3, use_or=False):
    """Search the persistent SQLite FTS5 library index with BM25 ranking."""
    q = str(query or "").strip()
    if len(q) < 2:
        return {"query": q, "results": [], "scanned_books": 0, "truncated": False, "engine": "sqlite-fts5"}

    stats = sync_index(items)
    cap = max(1, min(200, int(limit or 50)))
    per_book = max(1, min(10, int(max_per_book or 3)))
    fts_expr, tokens = _fts_query_tokens(q, use_or=use_or)
    if not fts_expr:
        return {"query": q, "results": [], "scanned_books": stats["total_books"], "truncated": False, "engine": stats["engine"]}

    results = []
    truncated = False
    seen_per_book = {}
    with _DB_LOCK:
        conn = _connect()
        try:
            if _FTS5_AVAILABLE:
                rows = conn.execute(
                    """
                    SELECT b.book_id, b.name, b.stem, b.ext, b.folder, b.path,
                           f.page, f.content, bm25(pages_fts) AS rank
                    FROM pages_fts AS f
                    JOIN books AS b ON b.path = f.path
                    WHERE pages_fts MATCH ?
                    ORDER BY rank ASC
                    LIMIT ?
                    """,
                    (fts_expr, cap * 4),
                ).fetchall()
            else:
                like = f"%{q.lower()}%"
                rows = conn.execute(
                    """
                    SELECT b.book_id, b.name, b.stem, b.ext, b.folder, b.path,
                           p.page, p.content, 0.0 AS rank
                    FROM pages_plain AS p
                    JOIN books AS b ON b.path = p.path
                    WHERE LOWER(p.content) LIKE ?
                    LIMIT ?
                    """,
                    (like, cap * 4),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            _LOG.debug("library_index.search_fts query failed (%s): %s", fts_expr, exc)
            rows = []
        finally:
            conn.close()

    snippet_terms = [q] + tokens
    for row in rows:
        bid = row["book_id"]
        cnt = seen_per_book.get(bid, 0)
        if cnt >= per_book:
            continue
        seen_per_book[bid] = cnt + 1
        before, match, after = _make_snippet(row["content"], snippet_terms)
        results.append({
            "id": bid,
            "name": row["name"],
            "stem": row["stem"],
            "ext": row["ext"],
            "folder": row["folder"],
            "path": row["path"],
            "page": int(row["page"]),
            "before": before,
            "match": match,
            "after": after,
            "excerpt": str(row["content"])[:900],
        })
        if len(results) >= cap:
            truncated = True
            break

    return {
        "query": q,
        "results": results,
        "scanned_books": stats["total_books"],
        "truncated": truncated,
        "engine": stats["engine"],
    }


def ask_library(query, items, *, top_k=6, model=None):
    """Answer a question grounded across all Bookshelf PDFs & EPUBs using
    SQLite FTS5 retrieval + the configured local LLM (Ollama / OpenAI-compatible)."""
    from pdfmanager import assistant

    q = str(query or "").strip()
    if len(q) < 2:
        raise core.PdfManagerError("enter a question to ask your Bookshelf")

    k = max(1, min(12, int(top_k or 6)))
    # Try exact phrase/AND first; fall back to BM25 OR-ranked content tokens for natural questions.
    found = search_fts(q, items, limit=k, max_per_book=2, use_or=False)
    hits = found["results"]
    if len(hits) < 2:
        broad = search_fts(q, items, limit=k, max_per_book=2, use_or=True)
        seen = {(h["id"], h["page"]) for h in hits}
        for h in broad["results"]:
            if (h["id"], h["page"]) not in seen:
                hits.append(h)
                seen.add((h["id"], h["page"]))
            if len(hits) >= k:
                break

    if not hits:
        return {
            "query": q,
            "answer": "No matching passages were found in your Bookshelf folders for that question.",
            "model": "",
            "citations": [],
            "scanned_books": found["scanned_books"],
        }

    context_blocks = []
    citations = []
    for idx, h in enumerate(hits, 1):
        excerpt = h.get("excerpt") or f"{h['before']}{h['match']}{h['after']}"
        context_blocks.append(f"[{idx}] {h['stem']} ({h['ext']}, page {h['page']}):\n{excerpt}")
        citations.append({
            "num": idx,
            "id": h["id"],
            "name": h["name"],
            "stem": h["stem"],
            "ext": h["ext"],
            "page": h["page"],
            "snippet": f"{h['before']}{h['match']}{h['after']}",
        })

    system_prompt = (
        "You are ZPDF's offline Bookshelf research assistant. Answer the user's question "
        "using ONLY the provided excerpts from their local library. Cite sources inline "
        "using bracket numbers like [1], [2]. If the excerpts don't contain the answer, say so clearly."
    )
    user_prompt = (
        f"Question: {q}\n\n"
        "Library excerpts:\n" + "\n\n".join(context_blocks) + "\n\n"
        "Provide a clear, concise answer with inline [1] citations."
    )
    answer, used_model = assistant._chat(system_prompt, user_prompt, model=model)
    return {
        "query": q,
        "answer": answer,
        "model": used_model,
        "citations": citations,
        "scanned_books": found["scanned_books"],
    }
