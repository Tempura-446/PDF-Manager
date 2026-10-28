"""Command-line interface: ``pdfm <command> ...`` (or ``python -m pdfmanager``)."""

import argparse
import getpass
import json
import sys
from pathlib import Path

from pdfmanager import __version__, core, organize
from pdfmanager.pageranges import PageRangeError

PAGES_HELP = "pages, e.g. '1-3,5', '7-', 'odd', 'even', 'end' (1-based)"


def _default_output(path, suffix):
    p = Path(path)
    return p.with_name(f"{p.stem}_{suffix}.pdf")


def _human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _print_plan(plan, verb, dry_run):
    for src, dst in plan:
        print(f"{'would ' + verb if dry_run else verb}: {src} -> {dst}")
    print(f"{len(plan)} file(s) {'to ' + verb if dry_run else 'done'}")


def _merge_inputs(items):
    """Accept 'file.pdf' or 'file.pdf:1-3' for per-file page selection."""
    result = []
    for item in items:
        path, sep, spec = item.rpartition(":")
        if sep and spec and not Path(item).exists() and Path(path).exists():
            result.append((path, spec))
        else:
            result.append(item)
    return result


def cmd_info(a):
    for path in a.files:
        data = core.info(path, a.password)
        if a.json:
            print(json.dumps(data, default=str, indent=2))
            continue
        print(f"{data['file']}")
        print(f"  pages:     {data['pages']}")
        print(f"  file size: {_human_size(data['file_size'])}")
        print("  page size: " + ", ".join(f"{k} ({v})" for k, v in data["page_sizes"].items()))
        for key in ("title", "author", "subject", "creator", "producer", "created", "modified"):
            if data[key]:
                print(f"  {key + ':':10} {data[key]}")
        if data["encrypted"]:
            print("  encrypted: yes")


def cmd_merge(a):
    out = core.merge(_merge_inputs(a.inputs), a.output, bookmarks=not a.no_bookmarks,
                     password=a.password)
    print(f"merged {len(a.inputs)} file(s) -> {out}")


def cmd_split(a):
    files = core.split(a.input, a.outdir or Path(a.input).stem + "_split", every=a.every,
                       ranges=a.ranges, prefix=a.prefix, password=a.password)
    for f in files:
        print(f)
    print(f"wrote {len(files)} file(s)")


def cmd_extract(a):
    out = core.select_pages(a.input, a.output or _default_output(a.input, "extract"), a.pages,
                            a.password)
    print(f"-> {out}")


def cmd_delete(a):
    out = core.delete_pages(a.input, a.output or _default_output(a.input, "edited"), a.pages,
                            a.password)
    print(f"-> {out}")


def cmd_reorder(a):
    out = core.select_pages(a.input, a.output or _default_output(a.input, "reordered"), a.order,
                            a.password)
    print(f"-> {out}")


def cmd_reverse(a):
    print(f"-> {core.reverse(a.input, a.output or _default_output(a.input, 'reversed'), a.password)}")


def cmd_interleave(a):
    print(f"-> {core.interleave(a.front, a.back, a.output, not a.no_reverse, a.password)}")


def cmd_rotate(a):
    out = core.rotate(a.input, a.output or _default_output(a.input, "rotated"), a.degrees,
                      a.pages, a.password)
    print(f"-> {out}")


def cmd_resize(a):
    out = core.resize(a.input, a.output or _default_output(a.input, "resized"), size=a.size,
                      scale=a.scale, fit=a.fit, pages=a.pages,
                      auto_orient=not a.keep_orientation, password=a.password)
    print(f"-> {out}")


def cmd_crop(a):
    vals = [float(v) * core.POINTS_PER_MM for v in a.margins]
    if len(vals) == 1:
        vals *= 4
    elif len(vals) == 2:  # vertical, horizontal (like CSS)
        vals = [vals[1], vals[0], vals[1], vals[0]]
    elif len(vals) != 4:
        raise core.PdfManagerError("give 1, 2 or 4 margin values")
    else:  # top right bottom left (CSS order) -> left bottom right top
        t, r, b, l = vals
        vals = [l, b, r, t]
    out = core.crop(a.input, a.output or _default_output(a.input, "cropped"), vals, a.pages,
                    a.password)
    print(f"-> {out}")


def cmd_nup(a):
    out = core.n_up(a.input, a.output or _default_output(a.input, f"{a.per_sheet}up"),
                    a.per_sheet, a.size, a.password)
    print(f"-> {out}")


def cmd_compress(a):
    out = core.compress(a.input, a.output or _default_output(a.input, "compressed"),
                        a.image_quality, a.password)
    before, after = Path(a.input).stat().st_size, Path(out).stat().st_size
    pct = (1 - after / before) * 100 if before else 0
    print(f"-> {out}  ({_human_size(before)} -> {_human_size(after)}, {pct:.0f}% smaller)")


def _ask_password(prompt, confirm=False):
    pw = getpass.getpass(prompt)
    if confirm and getpass.getpass("Repeat password: ") != pw:
        raise core.PdfManagerError("passwords do not match")
    return pw


def cmd_encrypt(a):
    user_pw = a.user_password or _ask_password("New password: ", confirm=True)
    out = core.encrypt(a.input, a.output or _default_output(a.input, "encrypted"), user_pw,
                       a.owner_password, a.password)
    print(f"-> {out}")


def cmd_decrypt(a):
    pw = a.password or _ask_password("Password: ")
    print(f"-> {core.decrypt(a.input, a.output or _default_output(a.input, 'decrypted'), pw)}")


def cmd_metadata(a):
    fields = dict(title=a.title, author=a.author, subject=a.subject, keywords=a.keywords)
    if not any(v is not None for v in fields.values()):
        return cmd_info(argparse.Namespace(files=[a.input], password=a.password, json=False))
    out = core.set_metadata(a.input, a.output or a.input, a.password, **fields)
    print(f"-> {out}")


def cmd_watermark(a):
    out = core.watermark(a.input, a.output or _default_output(a.input, "watermarked"),
                         text=a.text, stamp_pdf=a.stamp, opacity=a.opacity,
                         font_size=a.font_size, angle=a.angle, pages=a.pages,
                         below=a.below, password=a.password)
    print(f"-> {out}")


def cmd_number(a):
    out = core.number_pages(a.input, a.output or _default_output(a.input, "numbered"),
                            fmt=a.format, position=a.position, font_size=a.font_size,
                            start=a.start, pages=a.pages, password=a.password)
    print(f"-> {out}")


def cmd_text(a):
    text = core.extract_text(a.input, a.output, a.pages, a.password)
    if a.output:
        print(f"-> {a.output}")
    else:
        print(text)


def cmd_from_images(a):
    margin = a.margin * core.POINTS_PER_MM
    print(f"-> {core.images_to_pdf(a.images, a.output, a.size, margin)}")


def cmd_to_images(a):
    files = core.pdf_to_images(a.input, a.outdir or Path(a.input).stem + "_images", a.dpi,
                               a.format, a.pages, a.password)
    print(f"wrote {len(files)} image(s) to {files[0].parent if files else a.outdir}")


def cmd_organize(a):
    plan = organize.organize(a.directory, a.by, a.dest, a.move, a.recursive, a.dry_run)
    _print_plan(plan, "move" if a.move else "copy", a.dry_run)


def cmd_rename(a):
    plan = organize.rename(a.directory, a.pattern, a.recursive, a.dry_run)
    _print_plan(plan, "rename", a.dry_run)


def cmd_catalog(a):
    out, count = organize.catalog(a.directory, a.output, a.recursive)
    print(f"catalogued {count} PDF(s) -> {out}")


def build_parser():
    p = argparse.ArgumentParser(
        prog="pdfm",
        description="Manage PDF files: merge, split, resize, rotate, compress, organize and more.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add(name, func, help_, *, inp=True, out=True):
        sp = sub.add_parser(name, help=help_, description=help_)
        if inp:
            sp.add_argument("input", help="input PDF")
        if out:
            sp.add_argument("-o", "--output", help="output PDF (default: <input>_<command>.pdf)")
        sp.add_argument("--password", help="password for encrypted input")
        sp.set_defaults(func=func)
        return sp

    sp = add("info", cmd_info, "show page count, sizes and metadata", inp=False, out=False)
    sp.add_argument("files", nargs="+")
    sp.add_argument("--json", action="store_true", help="print JSON")

    sp = add("merge", cmd_merge, "combine PDFs into one", inp=False, out=False)
    sp.add_argument("inputs", nargs="+", help="PDFs to merge; use file.pdf:1-3 to pick pages")
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("--no-bookmarks", action="store_true",
                    help="don't add a bookmark for each merged file")

    sp = add("split", cmd_split, "split a PDF into several files", out=False)
    sp.add_argument("-d", "--outdir", help="output directory (default: <input>_split)")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("-n", "--every", type=int, help="pages per output file (default: 1)")
    g.add_argument("-r", "--ranges", nargs="+", metavar="RANGE",
                   help="one output file per range, e.g. -r 1-3 4-10 11-")
    sp.add_argument("--prefix", help="output filename prefix (default: input name)")

    sp = add("extract", cmd_extract, "copy selected pages into a new PDF")
    sp.add_argument("pages", help=PAGES_HELP)

    sp = add("delete", cmd_delete, "remove pages")
    sp.add_argument("pages", help=PAGES_HELP)

    sp = add("reorder", cmd_reorder, "rearrange pages (pages can be repeated or dropped)")
    sp.add_argument("order", help="new page order, e.g. '3,1,2,4-'")

    add("reverse", cmd_reverse, "reverse page order")

    sp = add("interleave", cmd_interleave,
             "merge separately scanned front and back sides", inp=False, out=False)
    sp.add_argument("front")
    sp.add_argument("back")
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("--no-reverse", action="store_true",
                    help="back pages are already in order (not reversed)")

    sp = add("rotate", cmd_rotate, "rotate pages")
    sp.add_argument("degrees", type=int, help="90, 180, 270 or -90")
    sp.add_argument("-p", "--pages", help=PAGES_HELP + " (default: all)")

    sp = add("resize", cmd_resize, "change page size (e.g. to A4 or Letter) or scale pages")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("-s", "--size",
                   help="a4, a3, a5, letter, legal, ..., '210x297mm', '8.5x11in'; "
                        "append -landscape to force landscape")
    g.add_argument("--scale", type=float, help="scale factor, e.g. 0.5")
    sp.add_argument("--fit", choices=["fit", "fill", "stretch", "none"], default="fit",
                    help="how content is placed on the new size (default: fit)")
    sp.add_argument("--keep-orientation", action="store_true",
                    help="don't rotate the target size to match landscape pages")
    sp.add_argument("-p", "--pages", help=PAGES_HELP + " (default: all)")

    sp = add("crop", cmd_crop, "trim page margins")
    sp.add_argument("margins", nargs="+",
                    help="margins in mm: ALL | VERTICAL HORIZONTAL | TOP RIGHT BOTTOM LEFT")
    sp.add_argument("-p", "--pages", help=PAGES_HELP + " (default: all)")

    sp = add("nup", cmd_nup, "print several pages per sheet")
    sp.add_argument("-n", "--per-sheet", type=int, default=2, choices=[2, 4, 6, 8, 9])
    sp.add_argument("-s", "--size", default="a4", help="sheet size (default: a4)")

    sp = add("compress", cmd_compress, "reduce file size")
    sp.add_argument("-q", "--image-quality", type=int, metavar="1-95",
                    help="also re-encode images as JPEG at this quality (lossy)")

    sp = add("encrypt", cmd_encrypt, "password-protect a PDF (AES-256)")
    sp.add_argument("--user-password", help="password to open (prompted if omitted)")
    sp.add_argument("--owner-password", help="password for full permissions")

    add("decrypt", cmd_decrypt, "remove password protection")

    sp = add("metadata", cmd_metadata, "show or set title/author/subject/keywords")
    sp.add_argument("--title")
    sp.add_argument("--author")
    sp.add_argument("--subject")
    sp.add_argument("--keywords")

    sp = add("watermark", cmd_watermark, "stamp text or another PDF onto pages")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("-t", "--text", help="watermark text, e.g. CONFIDENTIAL")
    g.add_argument("--stamp", help="PDF whose first page is overlaid")
    sp.add_argument("--opacity", type=float, default=0.15)
    sp.add_argument("--font-size", type=float, default=60)
    sp.add_argument("--angle", type=float, default=45)
    sp.add_argument("--below", action="store_true", help="place under the page content")
    sp.add_argument("-p", "--pages", help=PAGES_HELP + " (default: all)")

    sp = add("number", cmd_number, "add page numbers")
    sp.add_argument("--format", default="{n} / {total}", help="default: '{n} / {total}'")
    sp.add_argument("--position", default="bottom-center",
                    choices=["bottom-center", "bottom-right", "bottom-left",
                             "top-center", "top-right", "top-left"])
    sp.add_argument("--font-size", type=float, default=10)
    sp.add_argument("--start", type=int, default=1, help="first number (default: 1)")
    sp.add_argument("-p", "--pages", help=PAGES_HELP + " (default: all)")

    sp = add("text", cmd_text, "extract text", out=False)
    sp.add_argument("-o", "--output", help="write to a .txt file instead of the terminal")
    sp.add_argument("-p", "--pages", help=PAGES_HELP)

    sp = add("from-images", cmd_from_images, "save images (JPG, PNG, ...) as a PDF",
             inp=False, out=False)
    sp.add_argument("images", nargs="+")
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("-s", "--size", help="fit images onto this paper size (default: image size)")
    sp.add_argument("--margin", type=float, default=0, help="margin in mm")

    sp = add("to-images", cmd_to_images, "render pages to image files", out=False)
    sp.add_argument("-d", "--outdir", help="output directory (default: <input>_images)")
    sp.add_argument("--dpi", type=int, default=150)
    sp.add_argument("-f", "--format", default="png", help="png, jpg, ... (default: png)")
    sp.add_argument("-p", "--pages", help=PAGES_HELP)

    sp = add("organize", cmd_organize, "sort PDFs in a folder into sub-folders",
             inp=False, out=False)
    sp.add_argument("directory")
    sp.add_argument("--by", choices=organize.GROUPINGS, default="year")
    sp.add_argument("--dest", help="where to create the sub-folders (default: the directory)")
    sp.add_argument("--move", action="store_true", help="move files instead of copying")
    sp.add_argument("-R", "--recursive", action="store_true")
    sp.add_argument("-n", "--dry-run", action="store_true", help="only show what would happen")

    sp = add("rename", cmd_rename, "rename PDFs from their metadata", inp=False, out=False)
    sp.add_argument("directory")
    sp.add_argument("--pattern", default="{date:%Y-%m-%d} {title}",
                    help="fields: {title} {author} {pages} {date:%%Y-%%m-%%d} {stem}")
    sp.add_argument("-R", "--recursive", action="store_true")
    sp.add_argument("-n", "--dry-run", action="store_true")

    sp = add("catalog", cmd_catalog, "write a CSV inventory of a folder of PDFs",
             inp=False, out=False)
    sp.add_argument("directory")
    sp.add_argument("-o", "--output", default="catalog.csv")
    sp.add_argument("-R", "--recursive", action="store_true")

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (core.PdfManagerError, PageRangeError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
