# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Show why PDF → Word reads a page the way it does: for each page where a
phrase is found, how often each reader sees it (pdfium, used for the text;
pdfplumber, used to find tables), what was left out as a copy, the tables
found, and what the converter makes of the page. Prints only numbers and
the text of that page; nothing is written or sent anywhere.

    python packaging/word_diagnose.py "file.pdf" "HUDSON BEIGE"
    python packaging/word_diagnose.py "file.pdf" --form    (why it looks like a form)
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pdfmanager import layout, tables, webpage  # noqa: E402


def main(path, phrase):
    import pdfplumber
    import pypdfium2 as pdfium

    key = phrase.lower()
    doc = pdfium.PdfDocument(path)
    hits = []
    for i in range(len(doc)):
        tp = doc[i].get_textpage()
        text = tp.get_text_range()
        if key in text.lower():
            hits.append((i, text.lower().count(key)))
        tp.close()
    doc.close()
    print(f"{path}: '{phrase}' on pages {[i + 1 for i, _ in hits]}")
    with pdfplumber.open(path) as pl:
        for index, seen in hits[:3]:
            print(f"\n=== page {index + 1}: pdfium sees it {seen}x")
            page = pl.pages[index]
            plain = page.extract_text() or ""
            print(f"pdfplumber sees it {plain.lower().count(key)}x; {len(page.chars)} chars, "
                  f"{len(page.images)} images, {len(page.rects)} rects, {len(page.curves)} curves")
            first = phrase.strip()[0]
            starts = [c for c in page.chars if c["text"] == first]
            for c in starts[:40]:
                after = "".join(o["text"] for o in page.chars
                                if abs(o["top"] - c["top"]) < 1 and 0 <= o["x0"] - c["x0"] < 9 * c["size"])
                if after.lower().startswith(key[: min(len(key), 6)]):
                    print(f"  at x={c['x0']:.1f} top={c['top']:.1f} size={c['size']:.1f} font={c['fontname']} "
                          f"fill={c.get('non_stroking_color')} stroke={c.get('stroking_color')} "
                          f"upright={c.get('upright')} matrix={tuple(round(v, 2) for v in c['matrix'])}")
            fonts = Counter((c["fontname"], round(c["size"], 1)) for c in page.chars)
            print("  fonts:", fonts.most_common(8))
    with layout.Document(path) as doc, webpage._Tables(path, None) as found:
        for index, _ in hits[:3]:
            page = doc.page(index)
            print(f"\n--- page {index + 1} as the converter reads it")
            print(f"  {len(page.chars)} chars kept, {len(page.dropped)} left out as copies or off the page")
            print("  left out (first 30):", "".join(t for t, _, _ in page.dropped[:30]))
            hidden = sum(1 for c in page.chars if getattr(c, "hidden", False))
            print(f"  hidden chars kept: {hidden}; pictures: {len(page.images)}; shapes: {len(page.shapes)}")
            for box, rows in found.get(index, page):
                print(f"  table at {tuple(round(v) for v in box)}: {len(rows)} rows x {max(map(len, rows), default=0)} cols")
                for row in rows[:6]:
                    print("    |", " | ".join((cell or "")[:30].replace("\n", " / ") for cell in row))
            for line in page.lines:
                text = "".join(c.text for c in line.chars)
                if key[:6] in text.lower():
                    print(f"  line at base={line.base:.1f} x={line.x0:.1f}-{line.x1:.1f}: {text[:80]}")


def form(path):
    """What the reader's quick form look finds on the first two pages."""
    from pdfmanager import formdetect

    found = formdetect.detect(path, pages=[1, 2], quick=True)
    for page, fields in sorted(found.items()):
        kinds = Counter(f["type"] for f in fields)
        print(f"page {page}: {len(fields)} places to fill in {dict(kinds)}")
        for f in fields[:12]:
            print(f"  {f['type']:9} at {tuple(round(v) for v in f['rect'])}  named {f.get('name')!r}")
    if not found:
        print("no places to fill in found")


if __name__ == "__main__":
    if sys.argv[2:3] == ["--form"]:
        form(sys.argv[1])
    else:
        main(*sys.argv[1:3])
