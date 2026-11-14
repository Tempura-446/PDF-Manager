# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""0.34.0 (textedit.py): a rewritten paragraph's lines are broken looking
at the whole paragraph, not line by line: no lone short word left on the
last line when the lines above can spare one, more even word gaps in
justified text, never more lines than breaking line by line would give."""
import random

from pdfmanager import textedit

GAP = 5.0


def measure_of(widths):
    """A measure for words named by their index ("0 1 2"): the widths plus a gap between words."""
    def measure(line):
        words = [w for w in line.split(" ") if w != ""]
        return sum(widths[int(w)] for w in words) + GAP * max(len(line.split(" ")) - 1, 0)
    return measure


def lines_of(widths, room, **how):
    text = " ".join(str(i) for i in range(len(widths)))
    return [[int(w) for w in line.split()] for line in textedit._wrap(text, room, measure_of(widths), **how)]


def test_no_lone_word_on_the_last_line():
    widths = [30, 30, 30, 30, 30, 30, 10]
    greedy = lines_of(widths, 100, greedy=True)
    assert greedy == [[0, 1, 2], [3, 4, 5], [6]]
    best = lines_of(widths, 100)
    assert len(best) == 3 and len(best[-1]) >= 2, best
    assert [w for line in best for w in line] == list(range(7))


def test_never_wider_than_the_room_or_more_lines_than_line_by_line():
    rnd = random.Random(4)
    for _ in range(200):
        widths = [rnd.choice((8, 12, 20, 26, 33, 41, 60)) for _ in range(rnd.randrange(1, 70))]
        room = rnd.choice((90, 120, 200, 310))
        for justify in (False, True):
            greedy, best = lines_of(widths, room, greedy=True), lines_of(widths, room, justify=justify)
            assert [w for line in best for w in line] == list(range(len(widths)))
            assert len(best) <= len(greedy), (widths, room)
            for line in best:
                natural = sum(widths[w] for w in line) + GAP * (len(line) - 1)
                assert natural <= room + 1e-6 or len(line) == 1, (line, natural, room)


def stretch(lines, widths, room):
    """How far the word gaps of the justified lines (all but the last) are
    pulled from their natural width: the sum of the squares."""
    return sum(((room - sum(widths[w] for w in line) - GAP * (len(line) - 1)) / (len(line) - 1)) ** 2
               for line in lines[:-1] if len(line) > 1)


def test_justified_gaps_are_more_even_than_line_by_line():
    """Better in most paragraphs, the same where line by line was already
    the best, and worse only where a lone last word was taken up."""
    rnd = random.Random(11)
    better = worse = 0
    for _ in range(300):
        widths = [rnd.choice((10, 14, 18, 22, 27, 35, 48)) for _ in range(rnd.randrange(40, 120))]
        room = rnd.choice((180, 240, 300))
        greedy, best = lines_of(widths, room, greedy=True), lines_of(widths, room, justify=True)
        a, b = stretch(greedy, widths, room), stretch(best, widths, room)
        better += b < a - 1e-9
        if b > a + 1e-9:
            worse += 1
            assert len(greedy[-1]) == 1 or len(best) < len(greedy) or \
                sum(widths[w] for w in greedy[-1]) < room * 0.12, (greedy, best)
    assert better >= 180 and worse <= 30, (better, worse)


def test_a_word_wider_than_the_room_gets_a_line_to_itself():
    assert lines_of([20, 150, 20, 20], 100) == [[0], [1], [2, 3]]


def test_one_word_and_no_words():
    assert textedit._wrap("7", 100, lambda line: 10.0) == ["7"]
    assert textedit._wrap("", 100, lambda line: 0.0) == [""]


def test_a_very_long_paragraph_is_broken_line_by_line():
    widths = [20] * 2500
    assert lines_of(widths, 100) == lines_of(widths, 100, greedy=True)
