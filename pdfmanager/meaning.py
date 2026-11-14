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

# Scripts the table has no sense of.
CJK = re.compile("[⺀-鿿가-힯豈-﫿ｦ-ﾟ]")

_lock = threading.Lock()
_table = None


def _load():
    global _table
    with _lock:
        if _table is None:
            with np.load(DATA) as data:
                vectors = data["vectors"].astype(np.float32) * (data["scale"].astype(np.float32)[:, None] / 127)
                pieces = json.loads(data["pieces"].tobytes().decode("utf-8"))
                joins = json.loads(data["joins"].tobytes().decode("utf-8"))
            number = {piece: i for i, piece in enumerate(pieces)}
            order = {tuple(join.split(" ", 1)): i for i, join in enumerate(joins)}
            _table = vectors, number, order
    return _table


@lru_cache(maxsize=200_000)
def _pieces(word):
    """The numbers of the pieces a word is made of (byte-pair encoding: the
    letters are joined pair by pair, the most common pair first)."""
    _, number, order = _load()
    parts = list("▁" + word)
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
