# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Turn text into a short list of numbers that says what it is about.

Two texts about the same thing get similar numbers even when they share
no words ("how much do I owe" and "balance due"), which is what lets the
folder search answer a question instead of only matching its words.

The model is a table: one vector of 128 numbers for each of 32,000 word
pieces (static/meaning/words.npz, made by packaging/make_meaning_data.py
from the WordLlama project's data). A text's vector is the average of its
pieces' vectors. No network, no other library than NumPy, and a page
takes well under a millisecond.

The table knows English. Chinese, Japanese and Korean text is left out
of the vector; search.py matches those by their characters instead.
"""

import hashlib
import json
import re
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent / "static" / "meaning" / "words.npz"
VERSION = 1        # stored with every vector; raise it when the table changes
SIZE = 128
PASSAGE = 90       # words in a passage of a page
STEP = 60          # a passage starts every so many words, so they overlap

# Scripts the built-in English table has no sense of.
CJK = re.compile("[⺀-鿿가-힯豈-﫿ｦ-ﾟ]")

_lock = threading.Lock()
_table = None


def pack_table_paths():
    """Return sorted ``words.npz`` Paths from installed language packs."""
    try:
        from pdfmanager import appdata
        root = appdata.data_dir() / "locales"
    except Exception:
        return []
    if not root.is_dir():
        return []
    found = []
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        for cand in (sub / "meaning" / "words.npz", sub / "words.npz"):
            if cand.is_file():
                found.append(cand)
                break
    return found


def table_version():
    """Integer model version for ``search.sqlite`` vector caching.
    Returns ``VERSION`` when only the built-in English table is active, or a
    deterministic positive 31-bit integer incorporating installed pack tables."""
    packs = pack_table_paths()
    if not packs:
        return VERSION
    h = hashlib.sha256()
    h.update(f"v{VERSION}".encode("ascii"))
    for p in packs:
        try:
            st = p.stat()
            h.update(f"|{p.parent.parent.name if p.parent.name == 'meaning' else p.parent.name}:{st.st_size}:{int(st.st_mtime)}".encode("utf-8"))
        except OSError:
            continue
    return (int.from_bytes(h.digest()[:4], "big") & 0x7FFFFFFF) or (VERSION + 1)


def reset():
    """Clear the cached embedding table so newly installed/removed language packs take effect."""
    global _table
    with _lock:
        _table = None
    _pieces.cache_clear()


def _read_npz(path):
    with np.load(path) as data:
        vectors = data["vectors"].astype(np.float32) * (data["scale"].astype(np.float32)[:, None] / 127)
        pieces = json.loads(data["pieces"].tobytes().decode("utf-8"))
        joins = json.loads(data["joins"].tobytes().decode("utf-8"))
    return vectors, pieces, joins


def _load():
    global _table
    with _lock:
        if _table is None:
            vectors, pieces, joins = _read_npz(DATA)
            number = {piece: i for i, piece in enumerate(pieces)}
            order = {tuple(join.split(" ", 1)): i for i, join in enumerate(joins)}
            vec_chunks = [vectors]
            next_idx = len(vectors)
            has_cjk = False
            for pack_path in pack_table_paths():
                try:
                    p_vecs, p_pieces, p_joins = _read_npz(pack_path)
                except Exception:
                    continue
                if p_vecs.ndim != 2 or p_vecs.shape[1] != SIZE:
                    continue
                vec_chunks.append(p_vecs)
                for offset, piece in enumerate(p_pieces):
                    number[piece] = next_idx + offset
                    if not has_cjk and CJK.search(piece):
                        has_cjk = True
                next_idx += len(p_vecs)
                base_rank = len(order)
                for offset, join in enumerate(p_joins):
                    pair = tuple(join.split(" ", 1))
                    if len(pair) == 2 and pair not in order:
                        order[pair] = base_rank + offset
            merged_vectors = vec_chunks[0] if len(vec_chunks) == 1 else np.vstack(vec_chunks)
            _table = merged_vectors, number, order, has_cjk
    return _table


@lru_cache(maxsize=200_000)
def _pieces(word):
    """The numbers of the pieces a word is made of (byte-pair encoding: the
    letters are joined pair by pair, the most common pair first)."""
    _, number, order, _ = _load()
    whole = "▁" + word
    if whole in number:
        return (number[whole],)
    parts = list(whole)
    while len(parts) > 1:
        best, where = None, -1
        for i in range(len(parts) - 1):
            rank = order.get((parts[i], parts[i + 1]))
            if rank is not None and (best is None or rank < best):
                best, where = rank, i
        if best is None:
            break
        parts[where:where + 2] = [parts[where] + parts[where + 1]]
    return tuple(number[p] for p in parts if p in number)


def _words(text):
    _, _, _, has_cjk = _load()
    if has_cjk:
        return text.split()
    return CJK.sub(" ", text).split()


def _vector(words):
    ids = [i for word in words for i in _pieces(word[:40])]
    if not ids:
        return np.zeros(SIZE, np.float32)
    vector = _load()[0][ids].mean(axis=0)
    length = float(np.linalg.norm(vector))
    return vector / length if length else vector


def embed(text):
    """The vector of a short text (a question): 128 numbers, length 1, or all
    zeros when there is nothing in it the table knows."""
    return _vector(_words(text))


def passages(text):
    """A page's text as overlapping passages: [(start, end)] in characters."""
    spans = [m.span() for m in re.finditer(r"\S+", text)]
    if not spans:
        return []
    found = []
    for first in range(0, len(spans), STEP):
        last = min(first + PASSAGE, len(spans)) - 1
        found.append((spans[first][0], spans[last][1]))
        if last == len(spans) - 1:
            break
    return found


def embed_page(text):
    """One vector per passage of a page, as an array of shape (passages, 128)."""
    parts = passages(text)
    if not parts:
        return np.zeros((0, SIZE), np.float32)
    return np.stack([_vector(_words(text[a:b])) for a, b in parts])


def pack(vectors):
    """Vectors as bytes for the search index (one byte a number)."""
    return np.round(np.clip(vectors, -1, 1) * 127).astype(np.int8).tobytes()


def unpack(data):
    return np.frombuffer(data, dtype=np.int8).reshape(-1, SIZE).astype(np.float32) / 127
