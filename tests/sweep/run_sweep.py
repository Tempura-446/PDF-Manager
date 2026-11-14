# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""The maturity sweep: every tool on every file of a corpus (see
make_corpus.py), each run in a child process of its own with a time limit
and a memory limit, and the result sorted into

    ok        it worked, and the result checks out
    refused   it stopped with a message for the user (EXPECTED_ERRORS)
    crash     an unexpected exception (the traceback is kept)
    hang      still running at the time limit
    oom       past the memory limit (MemoryError, or killed)
    died      the child process died some other way (a signal)
    wrong     it "worked" but the result is wrong: a PDF that pypdf or
              pdfium can't read, a page count that changed when it
              shouldn't, the text gone when it shouldn't be

Besides the tools: the file facts (filefacts.facts), search indexing
(search.search on a folder holding the file), the reader's quick look
(gui.inspect_pdf) and a tool preview (gui.preview_operation); and with
--reader N, N of the files opened in the reader (pdf.js in Chromium, through
Playwright for Node.js), looking for errors on the page.

    python3 tests/sweep/run_sweep.py CORPUS OUT [--jobs 4] [--tools a,b]
        [--files GLOB] [--timeout 120] [--memory-mb 3072] [--reader 40]

CORPUS is a folder made by make_corpus.py (with its manifest.csv). OUT gets
results.csv (one row per run), summary.txt and tracebacks.txt. With
--rerun OUT/results.csv only the runs that weren't ok are run again.

From pytest: python3 -m pytest tests/sweep --sweep CORPUS (see test_sweep.py).
"""
import argparse
import csv
import fnmatch
import json
import multiprocessing as mp
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from collections import Counter, defaultdict
from multiprocessing.connection import wait
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE.parent))

CLASSES = ("ok", "refused", "crash", "hang", "oom", "died", "wrong")
# Tools whose result has the input's pages, one for one.
SAME_PAGES = {"rotate", "resize", "crop", "autocrop", "repair", "compress", "color", "ocr", "watermark", "number",
              "flatten", "form", "headers", "metadata", "attach", "encrypt", "decrypt", "redact", "sanitize",
              "sign-cert", "pdfa", "tag", "labels", "bookmarks"}
# ... and keep its text (what pdfium reads off the pages).
SAME_TEXT = {"rotate", "resize", "repair", "compress", "color", "ocr", "watermark", "number", "flatten", "headers",
             "metadata", "attach", "encrypt", "decrypt", "sanitize", "sign-cert", "pdfa", "tag", "labels",
             "bookmarks", "merge", "interleave", "reverse", "insert-blank", "split"}
SLOW = {"ocr", "clean", "to-images", "to-word", "to-excel", "to-pptx", "to-html", "tag", "access-check",
        "autocrop", "redact", "compare", "pdfa", "pdfa-check", "text", "media", "form", "compress", "color",
        "sanitize", "flatten", "nup", "booklet"}
MANY_PAGES = 300  # files longer than this skip the SLOW tools
OPTIONS = {
    "extract": {"pages": "1"}, "delete": {"pages": "1"}, "reorder": {"order": "1"},
    "to-word": {"engine": "basic"}, "encrypt": {"new_password": "sweep-pass"},
    "redact": {"terms": "the", "emails": "true"}, "watermark": {"text": "DRAFT"},
    "recipe": {"steps": json.dumps([{"op": "rotate", "opts": {"degrees": "90"}},
                                    {"op": "number", "opts": {}}])},
    "sign-cert": {"pfx_password": "sweep"}, "bookmarks": {"mode": "auto"}, "insert-blank": {"after": "1"},
    "headers": {"top_right": "{n} of {total}", "bates_prefix": "SW"}, "form": {"detect": "true"},
}
NOT_SWEPT = {"reader", "scan", "from-images", "web", "from-office", "organize", "rename", "duplicates", "search"}
EXTRA_TASKS = ("facts", "search", "inspect", "preview")


def tools():
    from pdfmanager import gui

    return [op for op in gui.OPERATIONS if op.get("files") in ("pdf", "pdf2") and op["id"] not in NOT_SWEPT]


def options(op):
    opts = {}
    for field in op["fields"]:
        if field["kind"] == "file":
            continue
        if field.get("default") not in (None, ""):
            value = field["default"]
            opts[field["key"]] = ("true" if value else "false") if isinstance(value, bool) else str(value)
    opts.update(OPTIONS.get(op["id"], {}))
    for field in op["fields"]:
        if field.get("required") and not opts.get(field["key"]):
            opts[field["key"]] = "1"
    return opts


# ---------------------------------------------------------------------------
# In the child process
# ---------------------------------------------------------------------------

def _pdfium_look(path, max_pages=30, password=""):
    """(pages, [text of the first max_pages pages]) as pdfium reads them."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path), password=password)
    try:
        n = len(doc)
        texts = []
        for i in range(min(n, max_pages)):
            page = doc[i]
            tp = page.get_textpage()
            texts.append("".join(tp.get_text_range().split()))
            tp.close()
            page.close()
        return n, texts
    finally:
        doc.close()


def _pypdf_pages(path, password=""):
    from pypdf import PdfReader

    r = PdfReader(str(path))
    if r.is_encrypted:
        r.decrypt(password)
    return len(r.pages)


def _check_pdf(path, op, before):
    """What's wrong with the PDF ``path`` made by ``op`` from a file that
    looked like ``before`` (pages, texts), or ''."""
    password = OPTIONS["encrypt"]["new_password"] if op == "encrypt" else ""
    try:
        n_pypdf = _pypdf_pages(path, password)
    except Exception as exc:
        return f"pypdf can't read the result: {type(exc).__name__}: {exc}"[:300]
    try:
        n, texts = _pdfium_look(path, password=password)
    except Exception as exc:
        return f"pdfium can't read the result: {type(exc).__name__}: {exc}"[:300]
    if n != n_pypdf:
        return f"pypdf counts {n_pypdf} pages, pdfium {n}"
    if before is None:
        return ""
    pages_before, texts_before = before
    if op in SAME_PAGES and n != pages_before:
        return f"{pages_before} pages became {n}"
    if op in SAME_TEXT and op not in ("split",):
        had, has = sum(map(len, texts_before)), sum(map(len, texts[:len(texts_before)]))
        if op in ("reverse", "insert-blank"):  # the same pages, moved
            has = sum(map(len, texts))
        if had >= 40 and has < 0.6 * had:
            return f"text lost: {had} letters before, {has} after"
    return ""


def _check_output(name, data, op, before, out_dir):
    """'' when the downloaded result (``name``, ``data``) checks out."""
    import io
    import zipfile

    path = out_dir / name
    path.write_bytes(data)
    if not data:
        return "an empty result"
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _check_pdf(path, op, before)
    if suffix == ".zip":
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return "a ZIP that isn't one"
        if not zf.namelist():
            return "an empty ZIP"
        for member in zf.namelist():
            if member.lower().endswith(".pdf"):
                target = out_dir / "zip" / member
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member))
                problem = _check_pdf(target, "split-part", None)
                if problem:
                    return f"{member}: {problem}"
        if op == "split" and before:
            total = 0
            for member in zf.namelist():
                if member.lower().endswith(".pdf"):
                    total += _pypdf_pages(out_dir / "zip" / member)
            if total != before[0]:
                return f"split into {total} pages from {before[0]}"
        return ""
    if suffix == ".docx":
        try:
            import docx
        except ImportError:  # not installed here: the result isn't checked
            return ""

        try:
            d = docx.Document(str(path))
        except Exception as exc:
            return f"Word can't read the result: {exc}"[:300]
        text = "".join("".join(p.text.split()) for p in d.paragraphs)
        text += "".join("".join(c.text.split()) for t in d.tables for row in t.rows for c in row.cells)
        if before and sum(map(len, before[1])) >= 200 and len(text) < 0.3 * sum(map(len, before[1])):
            return f"text lost: {sum(map(len, before[1]))} letters in the PDF, {len(text)} in the document"
        return ""
    if suffix == ".xlsx":
        try:
            import openpyxl
        except ImportError:  # not installed here: the result isn't checked
            return ""

        try:
            openpyxl.load_workbook(str(path))
        except Exception as exc:
            return f"the spreadsheet can't be read: {exc}"[:300]
        return ""
    if suffix == ".pptx":
        try:
            import pptx
        except ImportError:  # not installed here: the result isn't checked
            return ""

        try:
            pptx.Presentation(str(path))
        except Exception as exc:
            return f"the presentation can't be read: {exc}"[:300]
        return ""
    if suffix == ".txt" and op == "text" and before:
        text = "".join(data.decode("utf-8", "replace").split())
        had = sum(map(len, before[1]))
        if had >= 40 and len(text) < 0.6 * min(had, sum(map(len, before[1]))):
            return f"text lost: {had} letters, {len(text)} in the text file"
    return ""


def _child(task, conn, memory_mb):
    """Run one task; send back {"class", "message", "seconds", ...}."""
    import resource

    os.setsid()
    # Tesseract's own threads, times the jobs running at once, would make
    # OCR look hung on a busy machine.
    os.environ["OMP_THREAD_LIMIT"] = "1"
    if memory_mb:
        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    started = time.monotonic()
    result = {"class": "ok", "message": ""}
    try:
        from pdfmanager import appdata, gui

        work = Path(task["work"])
        work.mkdir(parents=True, exist_ok=True)
        data_dir = work / "appdata"
        data_dir.mkdir(exist_ok=True)
        (data_dir / "settings.json").write_text('{"check_updates": false}', encoding="utf-8")
        appdata._data_dir = lambda: data_dir
        src = Path(task["file"])
        before = None
        try:
            before = _pdfium_look(src)
        except Exception:
            pass
        kind = task["task"]
        try:
            if kind == "facts":
                from pdfmanager import filefacts

                found = filefacts.facts(src)
                result["message"] = json.dumps({k: found.get(k) for k in ("pages", "error", "protected")})
                if before and found.get("pages") not in (None, before[0]):
                    result.update({"class": "wrong", "message": f"facts say {found.get('pages')} pages, pdfium "
                                                               f"{before[0]}"})
            elif kind == "search":
                from pdfmanager import search

                folder = work / "searchdir"
                folder.mkdir()
                shutil.copy(src, folder / src.name)
                found = search.search(folder, "the", how="exact")
                result["message"] = json.dumps(found)[:200]
                found = search.search(folder, "invoice paid", how="best")
            elif kind == "inspect":
                found = gui.inspect_pdf(src)
                result["message"] = json.dumps({k: v for k, v in found.items() if k != "thumb"})[:200]
                if found.get("error") and before:
                    result.update({"class": "wrong", "message": f"pdfium reads it, inspect says: {found['error']}"})
            elif kind == "preview":
                gui.preview_operation("watermark", src.read_bytes(), src.name, {"text": "DRAFT"})
            else:
                op = gui.OPS[kind]
                files = [src]
                if op.get("files") == "pdf2" or op.get("min", 1) >= 2:
                    files.append(Path(task["second"]))
                opts = options(op)
                extra = {}
                if kind == "sign-cert":
                    extra = {"pfx": Path(task["pfx"]), "pfx[]": [Path(task["pfx"])]}
                if kind == "attach":
                    note = work / "note.txt"
                    note.write_text("attached by the sweep\n", encoding="utf-8")
                    extra = {"attach": note, "attach[]": [note]}
                job = work / "job"
                job.mkdir()
                # The server copies uploads into <work>/in/<n>/; so does this.
                copies = []
                for i, f in enumerate(files):
                    (job / "in" / f"{i:03d}").mkdir(parents=True)
                    copies.append(Path(shutil.copy(f, job / "in" / f"{i:03d}" / f.name)))
                notes = []
                name, data, ctype = gui.run_operation(kind, copies, opts, None, extra, job, notes)
                result["message"] = "; ".join(notes)[:300]
                out = work / "result"
                out.mkdir()
                if before and len(files) == 2 and kind in ("merge", "interleave"):
                    # The first file's text is checked (at the start of a merge); interleaved pages aren't.
                    before = (before[0], before[1]) if kind == "merge" else None
                problem = _check_output(name, data, kind, before, out)
                if problem:
                    result.update({"class": "wrong", "message": problem})
        except gui.EXPECTED_ERRORS as exc:
            result.update({"class": "refused", "message": str(exc)[:500]})
        except MemoryError:
            result.update({"class": "oom", "message": "MemoryError"})
        except BaseException as exc:  # noqa: BLE001 (that's what's looked for)
            result.update({"class": "crash", "message": f"{type(exc).__name__}: {exc}"[:500],
                           "traceback": traceback.format_exc()[-6000:]})
    except BaseException as exc:  # noqa: BLE001
        result.update({"class": "crash", "message": f"(sweep) {type(exc).__name__}: {exc}"[:500],
                       "traceback": traceback.format_exc()[-6000:]})
    result["seconds"] = round(time.monotonic() - started, 2)
    try:
        conn.send(result)
    finally:
        conn.close()
    os._exit(0)


# ---------------------------------------------------------------------------
# In the parent
# ---------------------------------------------------------------------------

def _page_count(path):
    """Pages of ``path`` (or 0), asked of a child so a bad file can't hurt the sweep."""
    code = ("import sys, pypdfium2 as p\ntry:\n d = p.PdfDocument(sys.argv[1], password='')\n print(len(d))\n"
            "except Exception:\n print(0)\n")
    try:
        out = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, timeout=60)
        return int(out.stdout.strip() or 0)
    except Exception:
        return 0


def plan(corpus, only_tools=None, only_files=None):
    manifest = {}
    with open(corpus / "manifest.csv", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            manifest[row["file"]] = row
    files = sorted(p for p in corpus.glob("*.pdf") if p.name in manifest or not manifest)
    if only_files:
        files = [p for p in files if any(fnmatch.fnmatch(p.name, g) for g in only_files)]
    tasks = []
    names = [op["id"] for op in tools()] + list(EXTRA_TASKS)
    if only_tools:
        names = [n for n in names if n in only_tools]
    for path in files:
        pages = _page_count(path)
        for name in names:
            if pages > MANY_PAGES and name in SLOW:
                continue
            tasks.append({"file": str(path), "task": name, "pages": pages,
                          "kind": manifest.get(path.name, {}).get("kind", "valid")})
    return tasks, manifest


def _fixtures(out):
    """The second file for two-file tools and a digital ID for Sign."""
    from reportlab.pdfgen import canvas

    fx = out / "fixtures"
    fx.mkdir(parents=True, exist_ok=True)
    second = fx / "second.pdf"
    c = canvas.Canvas(str(second))
    for n in range(3):
        c.drawString(72, 720, f"Second file, page {n + 1}: the quick brown fox")
        c.showPage()
    c.save()
    pfx = fx / "sweep.pfx"
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Sweep Tester")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=365)).sign(key, hashes.SHA256()))
    pfx.write_bytes(pkcs12.serialize_key_and_certificates(
        b"id", key, cert, None, serialization.BestAvailableEncryption(b"sweep")))
    return second, pfx


def _run(tasks, out, jobs, timeout, memory_mb, show=True):
    out.mkdir(parents=True, exist_ok=True)
    second, pfx = _fixtures(out)
    scratch = Path(tempfile.mkdtemp(prefix="sweep-", dir=out))
    ctx = mp.get_context("fork")
    pending = list(enumerate(tasks))
    running = {}  # sentinel -> (index, process, conn, started, limit)
    results = [None] * len(tasks)
    done = 0
    started_all = time.monotonic()
    while pending or running:
        while pending and len(running) < jobs:
            index, task = pending.pop(0)
            task = dict(task, work=str(scratch / f"t{index}"), second=str(second), pfx=str(pfx))
            here, there = ctx.Pipe(duplex=False)
            proc = ctx.Process(target=_child, args=(task, there, memory_mb), daemon=True)
            proc.start()
            there.close()
            per_page = 1.0 if task["task"] in ("ocr", "clean", "redact", "pdfa", "compress") else 0.3
            limit = timeout + per_page * min(task.get("pages", 0), 2000)
            running[proc.sentinel] = (index, proc, here, time.monotonic(), limit)
        ready = wait(list(running) + [r[2] for r in running.values()], timeout=1.0)
        now = time.monotonic()
        for key in list(running):
            index, proc, conn, began, limit = running[key]
            result = None
            if conn in ready or key in ready:
                try:
                    if conn.poll():
                        result = conn.recv()
                except (EOFError, OSError):
                    result = None
                if result is None:
                    proc.join(5)
                    if proc.exitcode is None:
                        continue
                    code = proc.exitcode
                    result = {"class": "oom" if code in (-signal.SIGKILL, -signal.SIGSEGV) and memory_mb else "died",
                              "message": f"exit code {code}", "seconds": round(now - began, 2)}
            elif now - began > limit:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    proc.kill()
                result = {"class": "hang", "message": f"still running after {limit:.0f} s",
                          "seconds": round(now - began, 2)}
            else:
                continue
            proc.join(5)
            conn.close()
            del running[key]
            shutil.rmtree(scratch / f"t{index}", ignore_errors=True)
            results[index] = dict(tasks[index], **result)
            done += 1
            if show and (done % 50 == 0 or result["class"] in ("crash", "hang", "oom", "died", "wrong")):
                t = tasks[index]
                print(f"[{done}/{len(tasks)} {time.monotonic() - started_all:.0f}s] {result['class']:7} "
                      f"{t['task']:12} {Path(t['file']).name}: {result.get('message', '')[:150]}", flush=True)
    shutil.rmtree(scratch, ignore_errors=True)
    return results


# ---------------------------------------------------------------------------
# The reader, in a browser
# ---------------------------------------------------------------------------

READER_SERVER = r"""
import json, sys, threading, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from pdfmanager import appdata, gui
data = Path(sys.argv[2]); data.mkdir(parents=True, exist_ok=True)
(data / "settings.json").write_text('{"check_updates": false}')
appdata._data_dir = lambda: data
srv, url = gui.make_server()
threading.Thread(target=srv.serve_forever, daemon=True).start()
opened = srv.RequestHandlerClass.opened
ids = {p: opened.add(Path(p)) for p in sys.argv[3:]}
print(json.dumps({"url": url, "ids": ids}), flush=True)
sys.stdin.read()
"""


def run_reader(files, out, per_file=45):
    """{file: {"class", "message"}} for each file opened in the reader."""
    node = shutil.which("node")
    if not node:
        return {}
    server = subprocess.Popen([sys.executable, "-c", READER_SERVER, str(ROOT), str(out / "reader-data"),
                               *map(str, files)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        info = json.loads(server.stdout.readline())
        jobs = [{"file": f, "id": i} for f, i in info["ids"].items()]
        (out / "reader-jobs.json").write_text(json.dumps(jobs))
        run = subprocess.run([node, str(HERE / "reader_sweep.js"), info["url"], str(out / "reader-jobs.json"),
                              str(per_file * 1000)], capture_output=True, text=True,
                             timeout=per_file * len(jobs) + 120, cwd=str(HERE))
        if run.returncode:
            print("reader sweep failed:", run.stderr[-2000:], file=sys.stderr)
            return {}
        return json.loads(run.stdout)
    finally:
        server.stdin.close()
        server.terminate()
        server.wait(10)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

FIELDS = ["file", "kind", "task", "class", "seconds", "message"]


def write_report(results, out, label=""):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "results.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow(dict(r, file=Path(r["file"]).name))
    with open(out / "tracebacks.txt", "w", encoding="utf-8") as fh:
        for r in results:
            if r.get("traceback"):
                fh.write(f"==== {r['task']} on {Path(r['file']).name}\n{r['traceback']}\n")
    counts = Counter(r["class"] for r in results)
    lines = [f"ZPDF sweep {label}".strip(), f"{len(results)} runs on {len({r['file'] for r in results})} files", ""]
    lines += [f"  {c:8} {counts.get(c, 0)}" for c in CLASSES]
    by_kind = defaultdict(Counter)
    for r in results:
        by_kind[r.get("kind", "?")][r["class"]] += 1
    lines += ["", "By kind of file:"]
    for kind, c in sorted(by_kind.items()):
        lines.append(f"  {kind:8} " + "  ".join(f"{k} {c.get(k, 0)}" for k in CLASSES))
    by_task = defaultdict(Counter)
    for r in results:
        by_task[r["task"]][r["class"]] += 1
    lines += ["", "Problems by tool (crash/hang/oom/died/wrong):"]
    for task, c in sorted(by_task.items(), key=lambda kv: -sum(kv[1][k] for k in CLASSES[2:])):
        bad = sum(c[k] for k in CLASSES[2:])
        if bad:
            lines.append(f"  {task:14} " + "  ".join(f"{k} {c[k]}" for k in CLASSES[2:] if c[k]))
    refused_valid = [r for r in results if r["class"] == "refused" and r.get("kind") == "valid"]
    lines += ["", f"Refusals of valid files: {len(refused_valid)}"]
    msgs = Counter((r["task"], r["message"][:120]) for r in refused_valid)
    for (task, msg), n in msgs.most_common(60):
        lines.append(f"  {n:4}  {task}: {msg}")
    problems = Counter((r["task"], r["class"], r["message"][:140]) for r in results if r["class"] in CLASSES[2:])
    lines += ["", "Problems, most frequent first:"]
    for (task, cls, msg), n in problems.most_common(200):
        lines.append(f"  {n:4}  {cls:6} {task}: {msg}")
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description="ZPDF's maturity sweep (see the module's notes)")
    ap.add_argument("corpus", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--memory-mb", type=int, default=3072)
    ap.add_argument("--tools", help="comma-separated tool ids (and facts, search, inspect, preview)")
    ap.add_argument("--files", help="comma-separated globs of corpus file names")
    ap.add_argument("--rerun", type=Path, help="a results.csv: run again only what wasn't ok there")
    ap.add_argument("--reader", type=int, default=0, help="open this many files in the reader (0: none, -1: all)")
    ap.add_argument("--label", default="")
    args = ap.parse_args(argv)
    only_tools = set(args.tools.split(",")) if args.tools else None
    only_files = args.files.split(",") if args.files else None
    tasks, manifest = plan(args.corpus, only_tools, only_files)
    previous = []
    if args.rerun:
        with open(args.rerun, encoding="utf-8") as fh:
            previous = list(csv.DictReader(fh))
        if args.reader:  # the reader is looked at again below
            previous = [r for r in previous if r["task"] != "reader"]
        again = {(r["file"], r["task"]) for r in previous if r["class"] != "ok"}
        tasks = [t for t in tasks if (Path(t["file"]).name, t["task"]) in again]
    print(f"{len(tasks)} runs", flush=True)
    results = _run(tasks, args.out, args.jobs, args.timeout, args.memory_mb)
    if args.rerun:  # the rows that were ok before stay
        rerun = {(Path(r["file"]).name, r["task"]) for r in results}
        kept = [dict(r, file=str(args.corpus / r["file"])) for r in previous if (r["file"], r["task"]) not in rerun]
        results = kept + results
    if args.reader:
        files = sorted({r["file"] for r in results if Path(r["file"]).is_file()}) or sorted(
            map(str, args.corpus.glob("*.pdf")))
        if args.reader > 0 and len(files) > args.reader:
            step = len(files) / args.reader
            files = [files[int(i * step)] for i in range(args.reader)]
        seen = run_reader(files, args.out)
        for f, r in seen.items():
            results.append({"file": f, "task": "reader", "kind": manifest.get(Path(f).name, {}).get("kind", "?"),
                            "class": r["class"], "message": r.get("message", ""), "seconds": r.get("seconds", 0)})
    counts = write_report(results, args.out, args.label)
    print((args.out / "summary.txt").read_text(encoding="utf-8")[:3000])
    return 0 if not any(counts.get(c) for c in ("crash", "hang", "oom", "died")) else 1


if __name__ == "__main__":
    sys.exit(main())
