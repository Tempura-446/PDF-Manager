# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Where a word may be divided at the end of a line: Liang's algorithm
with TeX's patterns for American English (static/hyphen/hyph-en-us.tex,
by Gerard D.C. Kuiken and Donald Knuth; its notice is in notices.py).

Only English is divided: other languages need their own patterns, and a
word divided by another language's rules reads wrong."""
import functools
import re
from pathlib import Path

PATTERNS = Path(__file__).resolve().parent / "static" / "hyphen" / "hyph-en-us.tex"
LEFT, RIGHT = 2, 3  # letters kept before and after a division
_WORD = re.compile(r"[A-Za-z]+\Z")


@functools.lru_cache(maxsize=1)
def _tables():
    """({letters: the numbers between them}, {word: its divisions}) from the patterns file."""
    text = re.sub(r"%.*", "", PATTERNS.read_text(encoding="ascii"))
    patterns, exceptions = {}, {}
    block = re.search(r"\\patterns\{(.*?)\}", text, re.S)
    for pattern in (block.group(1).split() if block else ()):
        letters = re.sub(r"\d", "", pattern)
        numbers, i = [0] * (len(letters) + 1), 0
        for ch in pattern:
            if ch.isdigit():
                numbers[i] = int(ch)
            else:
                i += 1
        patterns[letters] = numbers
    block = re.search(r"\\hyphenation\{(.*?)\}", text, re.S)
    for word in (block.group(1).split() if block else ()):
        plain, at, points = word.replace("-", ""), 0, []
        for ch in word:
            if ch == "-":
                points.append(at)
            else:
                at += 1
        exceptions[plain] = points
    return patterns, exceptions


def english(language):
    """Whether ``language`` (a PDF's /Lang, "en-US"; None: not said) is English."""
    return language is None or str(language).strip().lower().split("-")[0].split("_")[0] in ("", "en")


@functools.lru_cache(maxsize=4096)
def _points(word):
    patterns, exceptions = _tables()
    low = word.lower()
    if low in exceptions:
        found = exceptions[low]
    else:
        padded = "." + low + "."
        levels = [0] * (len(padded) + 1)
        for i in range(len(padded)):
            for j in range(i + 1, len(padded) + 1):
                numbers = patterns.get(padded[i:j])
                if numbers:
                    for k, n in enumerate(numbers):
                        if n > levels[i + k]:
                            levels[i + k] = n
        found = [p for p in range(1, len(low)) if levels[p + 1] % 2]
    return tuple(p for p in found if LEFT <= p <= len(low) - RIGHT)


def points(word, language=None):
    """The places ``word`` may be divided: [letters before the division].
    None for anything but a plain English word (letters only)."""
    if not english(language) or len(word) < LEFT + RIGHT or not _WORD.match(word):
        return []
    try:
        return list(_points(word))
    except OSError:  # the patterns aren't there: nothing is divided
        return []
