# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""How fast the two engines draw a page: pdf.js (the reader's, in a
browser through Node's Playwright) and PDFium (pypdfium2), on five kinds
of page. A report, not a test: it decides whether drawing the reader's
pages with PDFium is worth building (docs/superpowers/plans/
2026-11-14-native-rendering.md).

    python tests/renderbench.py [--out report.md] [--keep FOLDER] [--headed]

For each page: the first draw at 100 % (reading the page included) and a
second draw at 200 % (the page already read), in milliseconds, the median
of three runs. Run it on the PC the app is used on: a headless browser
draws without the graphics card, a real window with it (--headed).
"""
import argparse
import json
import math
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
CSS = 96 / 72  # the reader's 100 %: CSS pixels per point


def text_report(path):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=letter)
    words = ("the quarterly report shows steady growth in every region and the northern office led the way "
             "with a record year for new customers while costs stayed level").split()
    rng = random.Random(1)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 740, "Quarterly report")
    y = 710
    while y > 60:
        c.setFont("Times-Roman", 10)
        c.drawString(72, y, " ".join(rng.choice(words) for _ in range(16)))
        y -= 12
    c.save()


def scan(path):
    import io

    import pikepdf
    from PIL import Image
    noise = Image.effect_noise((255, 330), 40).resize((2550, 3300), Image.BICUBIC).convert("RGB")
    data = io.BytesIO()
    noise.save(data, "JPEG", quality=80)
    pdf = pikepdf.new()
    image = pikepdf.Stream(pdf, data.getvalue(), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image, Width=2550,
                           Height=3300, ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8, Filter=pikepdf.Name.DCTDecode)
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=image))
    page.Contents = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Im0 Do Q")
    pdf.save(path)


def brochure(path):
    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=letter)
    c.linearGradient(0, 0, 612, 792, ((0.1, 0.2, 0.5), (0.9, 0.5, 0.2)))
    rng = random.Random(2)
    for _ in range(400):  # see-through shapes over the gradient
        c.setFillColorRGB(rng.random(), rng.random(), rng.random(), alpha=0.3)
        c.circle(rng.uniform(0, 612), rng.uniform(0, 792), rng.uniform(10, 80), stroke=0, fill=1)
    photo = Image.effect_noise((300, 200), 60).resize((1200, 800), Image.BICUBIC).convert("RGB")
    for k in range(3):
        c.drawImage(ImageReader(photo), 60 + k * 170, 500, 150, 100)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 40)
    c.drawString(60, 680, "Autumn collection")
    c.save()


def drawing(path, segments, width_in, height_in):
    """A sheet of ``segments`` short lines (walls, hatching) and some labels."""
    from reportlab.pdfgen import canvas
    w, h = width_in * 72, height_in * 72
    c = canvas.Canvas(str(path), pagesize=(w, h), pageCompression=1)
    rng = random.Random(3)
    c.setLineWidth(0.25)
    path_ = c.beginPath()
    for n in range(segments):
        x, y, a = rng.uniform(20, w - 20), rng.uniform(20, h - 20), rng.uniform(0, math.tau)
        d = rng.uniform(2, 30)
        path_.moveTo(x, y)
        path_.lineTo(x + d * math.cos(a), y + d * math.sin(a))
        if n % 2000 == 1999:
            c.drawPath(path_)
            path_ = c.beginPath()
    c.drawPath(path_)
    c.setFont("Helvetica", 8)
    for n in range(300):
        c.drawString(rng.uniform(20, w - 80), rng.uniform(20, h - 20), f"ROOM {n}")
    c.save()


SAMPLES = [
    ("Text report (Letter)", "text.pdf", text_report),
    ("Scanned page (300 dpi JPEG)", "scan.pdf", scan),
    ("Brochure (gradient, 400 see-through shapes)", "brochure.pdf", brochure),
    ("Drawing, 200,000 segments (24 x 36 in)", "drawing.pdf", lambda p: drawing(p, 200_000, 36, 24)),
    ("Big sheet, 50,000 segments (36 x 48 in)", "sheet.pdf", lambda p: drawing(p, 50_000, 48, 36)),
]


def pdfium_times(path, runs=3):
    """[(first draw at 100 %, second at 200 %)] in ms, each run with the file opened afresh."""
    import pypdfium2 as pdfium
    out = []
    for _ in range(runs):
        doc = pdfium.PdfDocument(str(path))
        try:
            page = doc[0]
            t0 = time.perf_counter()
            page.render(scale=CSS).close()
            t1 = time.perf_counter()
            page.render(scale=CSS * 2).close()
            t2 = time.perf_counter()
            out.append(((t1 - t0) * 1000, (t2 - t1) * 1000))
            page.close()
        finally:
            doc.close()
    return out


def median(pairs):
    return [statistics.median(p[k] for p in pairs) for k in (0, 1)]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", help="write the table here as well (Markdown)")
    parser.add_argument("--keep", help="keep the sample files in this folder")
    parser.add_argument("--headed", action="store_true", help="a real browser window (uses the graphics card)")
    args = parser.parse_args()
    folder = Path(args.keep or tempfile.mkdtemp(prefix="zpdf-bench-"))
    folder.mkdir(parents=True, exist_ok=True)
    for _, name, make in SAMPLES:
        make(folder / name)

    rows = {name: {"pdfium": median(pdfium_times(folder / name))} for _, name, _ in SAMPLES}

    node = shutil.which("node")
    if node and subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=folder).returncode == 0:
        from pdfmanager import gui
        srv, url = gui.make_server()
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            opened = srv.RequestHandlerClass.opened
            cfg = {"files": {name: opened.add(folder / name) for _, name, _ in SAMPLES}, "scale": CSS,
                   "headed": args.headed}
            run = subprocess.run([node, str(HERE / "renderbench.js"), url, json.dumps(cfg)], capture_output=True,
                                 text=True, encoding="utf-8", timeout=1800)
        finally:
            srv.shutdown()
            srv.server_close()
        if run.returncode == 0:
            for name, pairs in json.loads(run.stdout).items():
                rows[name]["pdfjs"] = median(pairs)
        else:
            print("pdf.js could not be timed:", run.stderr[-600:], file=sys.stderr)
    else:
        print("pdf.js was not timed: this needs Node.js with Playwright.", file=sys.stderr)

    lines = ["| Page | pdf.js first, 100 % | pdf.js again, 200 % | PDFium first, 100 % | PDFium again, 200 % | PDFium faster (first / again) |",
             "|---|---|---|---|---|---|"]
    for label, name, _ in SAMPLES:
        r = rows[name]
        js = r.get("pdfjs")
        cells = [f"{v:.0f} ms" for v in js] if js else ["not timed"] * 2
        cells += [f"{v:.0f} ms" for v in r["pdfium"]]
        ratio = " / ".join(f"{a / max(b, 0.5):.1f}x" for a, b in zip(js, r["pdfium"])) if js else ""
        lines.append(f"| {label} | " + " | ".join(cells) + f" | {ratio} |")
    table = "\n".join(lines)
    print(table)
    if args.out:
        Path(args.out).write_text(table + "\n", encoding="utf-8")
    if not args.keep:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
