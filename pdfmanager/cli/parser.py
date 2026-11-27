# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""CLI argument parser construction and main() entrypoint."""

import argparse
import logging
import sys
import time
from pathlib import Path

from pdfmanager import (
    __version__, archive, certsign, core, forms, imaging, ocr,
    organize, outline, protection, redact, scan,
)
from pdfmanager.pageranges import PageRangeError
from pdfmanager.cli.common import PAGES_HELP, PASSWORD_ENV, run_per_file
from pdfmanager.cli.commands_edit import *  # noqa: F401, F403
from pdfmanager.cli.commands_convert import *  # noqa: F401, F403
from pdfmanager.cli.commands_multi import *  # noqa: F401, F403


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

def _positive(kind):
    def check(value):
        number = kind(value)
        if number <= 0:
            raise argparse.ArgumentTypeError("must be greater than 0")
        return number
    return check


def build_parser():
    p = argparse.ArgumentParser(
        prog="pdfm",
        description="Manage PDF files: merge, split, resize, rotate, compress, convert "
                    "colours, OCR, organize and more. Run `pdfm gui` for a graphical app.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def per_file(name, run, help_, out_help=None):
        """A command that processes one or more input PDFs independently."""
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.add_argument("inputs", nargs="+", metavar="input",
                        help="input PDF(s); wildcards like *.pdf are allowed")
        sp.add_argument("-o", "--output", help=out_help or (
            "output file (default: <input>_<command>.pdf); with several inputs, "
            "a folder to write them into"))
        sp.add_argument("--password", help=f"password for encrypted input (better: let pdfm "
                                           f"prompt, or set {PASSWORD_ENV})")
        sp.set_defaults(func=run_per_file, run=run)
        return sp

    def other(name, func, help_):
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.set_defaults(func=func)
        return sp

    sp = per_file("info", run_info, "show page count, sizes and metadata")
    sp.add_argument("--json", action="store_true", help="print JSON")

    sp = other("merge", cmd_merge, "combine PDFs into one")
    sp.add_argument("inputs", nargs="+", help="PDFs to merge; use file.pdf:1-3 to pick pages")
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("--no-bookmarks", action="store_true",
                    help="don't add a bookmark for each merged file")
    sp.add_argument("--password")

    sp = per_file("split", run_split, "split a PDF into several files",
                  out_help="output folder (default: <input>_split)")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("-n", "--every", type=int, help="pages per output file (default: 1)")
    g.add_argument("-r", "--ranges", nargs="+", metavar="RANGE",
                   help="one output file per range, e.g. -r 1-3 4-10 11-")
    g.add_argument("-b", "--bookmarks", action="store_true",
                   help="one file per bookmark (chapter), named after it; pages before the "
                        "first bookmark get a file of their own")
    g.add_argument("--max-size", type=_positive(float), metavar="MB",
                   help="files of at most this many MB (1 MB = 1,000,000 bytes), as many "
                        "pages in each as fit")
    sp.add_argument("--level", type=int, choices=[1, 2], default=1,
                    help="with --bookmarks: 1 = top-level bookmarks only (default), "
                         "2 = also the ones under them")
    sp.add_argument("--prefix", help="output filename prefix (default: input name; with "
                                     "--bookmarks: none)")

    sp = per_file("extract", run_extract, "copy selected pages into a new PDF")
    sp.add_argument("-p", "--pages", required=True, help=PAGES_HELP)

    sp = per_file("delete", run_delete, "remove pages")
    sp.add_argument("-p", "--pages", required=True, help=PAGES_HELP)

    sp = per_file("reorder", run_reorder, "rearrange pages (pages can be repeated or dropped)")
    sp.add_argument("--order", required=True, help="new page order, e.g. '3,1,2,4-'")

    per_file("reverse", run_reverse, "reverse page order")

    sp = other("interleave", cmd_interleave, "merge separately scanned front and back sides")
    sp.add_argument("front")
    sp.add_argument("back")
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("--no-reverse", action="store_true",
                    help="back pages are already in order (not reversed)")
    sp.add_argument("--password")

    sp = per_file("rotate", run_rotate, "rotate pages")
    sp.add_argument("-d", "--degrees", type=int, required=True, help="90, 180, 270 or -90")
    sp.add_argument("-p", "--pages", help=PAGES_HELP + " (default: all)")

    sp = per_file("resize", run_resize, "change page size (e.g. to A4 or Letter) or scale pages")
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

    sp = per_file("crop", run_crop, "trim page margins, or the white margins (--auto)")
    sp.add_argument("-m", "--margins", nargs="+", metavar="MM", type=float,
                    help="margins in mm: ALL | VERTICAL HORIZONTAL | TOP RIGHT BOTTOM LEFT")
    sp.add_argument("--auto", action="store_true",
                    help="trim the white margins around the content of each page instead "
                         "(blank pages are left as they are)")
    sp.add_argument("--padding", type=float, default=3.5, metavar="MM",
                    help="with --auto: space to keep around the content (default: 3.5 mm)")
    sp.add_argument("--same", action="store_true",
                    help="with --auto: crop every page the same, enough for all their content")
    sp.add_argument("-p", "--pages", help=PAGES_HELP + " (default: all)")

    sp = per_file("nup", run_nup, "print several pages per sheet")
    sp.add_argument("-n", "--per-sheet", type=int, default=2, choices=[2, 4, 6, 8, 9])
    sp.add_argument("-s", "--size", default="a4", help="sheet size (default: a4)")

    sp = per_file("booklet", run_booklet,
                  "arrange pages to print a booklet: double-sided, flip on the short edge, "
                  "fold in the middle (supports RTL manga binding, signatures, and creep)")
    sp.add_argument("-s", "--size",
                    help="sheet size, e.g. a4 or letter, used landscape with two pages side by "
                         "side (default: twice the page size)")
    sp.add_argument("--binding", choices=("ltr", "rtl"), default="ltr",
                    help="binding direction: ltr (Western) or rtl (Manga / Japanese) (default: ltr)")
    sp.add_argument("--signature", type=int, default=0,
                    help="pages per folded signature (0 = single booklet, or 8, 16, 32)")
    sp.add_argument("--creep", type=float, default=0.0,
                    help="creep compensation in mm for inner nested sheets (default: 0.0)")

    sp = per_file("insert-blank", run_insert_blank,
                  "insert blank pages (e.g. a page for notes after every page)")
    sp.add_argument("--after", metavar="PAGES",
                    help="pages to put blank pages after, e.g. 3,7 · end · all (after every "
                         "page) · 0 (before the first page)")
    sp.add_argument("--at-start", action="store_true", help="also put blank pages before page 1")
    sp.add_argument("-c", "--count", type=_positive(int), default=1,
                    help="blank pages at each place (default: 1)")

    sp = per_file("compress", run_compress, "reduce file size")
    sp.add_argument("-l", "--level", default="medium",
                    choices=["low", "medium", "high", "large"],
                    help="low: lossless only; medium (default): images max 150 DPI, "
                         "JPEG quality 75; high/large: images max 96 DPI, quality 50")
    sp.add_argument("-q", "--image-quality", type=int, metavar="1-95",
                    help="override the level's JPEG quality")
    sp.add_argument("--max-dpi", type=int, help="override the level's image resolution")
    sp.add_argument("--no-sharp-scans", dest="sharp_scans", action="store_false",
                    help="compress scanned pages like other pictures, instead of keeping "
                         "their text at full sharpness over a reduced background")
    sp.add_argument("--target-mb", type=float, metavar="MB",
                    help="automatically tune quality/DPI to fit within this maximum size in MB")

    sp = per_file("color", run_color, "convert colours to grayscale, RGB or CMYK")
    sp.add_argument("--to", required=True, choices=imaging.COLOR_TARGETS)
    sp.add_argument("--icc", metavar="PROFILE.icc",
                    help="convert images through this ICC profile and embed it as the "
                         "output intent (e.g. your printer's CMYK profile)")

    sp = per_file("encrypt", run_encrypt, "password-protect PDFs (AES-256), and limit printing, "
                                          "copying or changes")
    sp.add_argument("--user-password", help="password to open (prompted if omitted; may be "
                                            "left out when limits and --owner-password are set)")
    sp.add_argument("--owner-password", help="permissions password: lifts the limits")
    sp.add_argument("--printing", choices=filetools.PRINTING, default="high",
                    help="allow printing (high), low-quality printing only, or none")
    sp.add_argument("--changes", choices=filetools.CHANGES, default="any",
                    help="what may be changed: any, comments (and forms, signing), forms "
                         "(filling in, signing), pages (insert, delete, turn) or none")
    sp.add_argument("--no-copy", action="store_true", help="don't allow copying text and pictures")

    # -- file tools --
    sp = per_file("attach", run_attach, "attach files to PDFs, remove them, or list them "
                                        "(without options)")
    sp.add_argument("--add", action="append", metavar="FILE", help="a file to attach (repeat)")
    sp.add_argument("--remove", action="append", metavar="NAME",
                    help="remove the attached file of this name (repeat)")
    sp.add_argument("--remove-all", action="store_true", help="remove all attached files")
    sp.add_argument("--description", help="a description for the files added")
    sp.add_argument("--list", action="store_true", help="list the attached files")

    sp = per_file("labels", run_labels, "set or remove page labels (i, ii, iii then 1, 2, 3); "
                                        "without options: list them")
    sp.add_argument("--set", metavar="SPEC",
                    help='e.g. "1: Cover; 2-4: i; 5-: 1; 30-: A-1" (the page each part starts on, '
                         "then its first label)")
    sp.add_argument("--clear", action="store_true", help="remove the page labels")

    sp = per_file("repair", run_repair, "mend damaged PDFs and optimize them for fast web view")
    sp.add_argument("--no-linearize", action="store_true",
                    help="don't optimize for fast web view (just repair)")
    # -- end of file tools --

    per_file("decrypt", run_decrypt, "remove password protection")

    sp = per_file("metadata", run_metadata, "show or set title/author/subject/keywords",
                  out_help="output file (default: edit in place)")
    sp.add_argument("--title")
    sp.add_argument("--author")
    sp.add_argument("--subject")
    sp.add_argument("--keywords")

    sp = per_file("watermark", run_watermark, "stamp text or another PDF onto pages")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("-t", "--text", help="watermark text, e.g. CONFIDENTIAL")
    g.add_argument("--stamp", help="PDF whose first page is overlaid")
    sp.add_argument("--opacity", type=float, default=0.15)
    sp.add_argument("--font-size", type=float, default=60)
    sp.add_argument("--angle", type=float, default=45)
    sp.add_argument("--below", action="store_true", help="place under the page content")
    sp.add_argument("-p", "--pages", help=PAGES_HELP + " (default: all)")

    sp = per_file("number", run_number, "add page numbers")
    sp.add_argument("--format", default="{n} / {total}", help="default: '{n} / {total}'")
    sp.add_argument("--position", default="bottom-center",
                    choices=["bottom-center", "bottom-right", "bottom-left",
                             "top-center", "top-right", "top-left"])
    sp.add_argument("--font-size", type=float, default=10)
    sp.add_argument("--start", type=int, default=1, help="first number (default: 1)")
    sp.add_argument("-p", "--pages", help=PAGES_HELP + " (default: all)")

    sp = per_file("headers", run_headers,
                  "add headers and footers, e.g. a title, the date or Bates numbers")
    for spot in core.HEADER_SPOTS:
        sp.add_argument("--" + spot.replace("_", "-"), dest=spot, metavar="TEXT",
                        help=f"text {spot.replace('_', ' ')}" if spot != "top_left" else
                        "text top left; any text may use {n} (page), {total}, {file}, {title}, "
                        "{date} and {bates}")
    sp.add_argument("--font-size", type=float, default=9)
    sp.add_argument("--margin", type=float, default=10, help="distance from the edge in mm")
    sp.add_argument("--start", type=int, default=1, help="first page number (default: 1)")
    sp.add_argument("--bates", metavar="PREFIX", help="Bates prefix for {bates}, e.g. ABC")
    sp.add_argument("--bates-start", type=int, default=1, help="first Bates number")
    sp.add_argument("--bates-digits", type=int, default=6, help="digits (default: 6)")
    sp.add_argument("-p", "--pages", help=PAGES_HELP + " (default: all)")

    sp = per_file("ocr", run_ocr, "make scanned PDFs searchable")
    sp.add_argument("--engine", choices=ocr.ENGINES, default="auto",
                    help="auto (default: the built-in engine when installed, else Tesseract), "
                         "builtin or tesseract")
    sp.add_argument("--quality", choices=ocr.QUALITIES, default=None,
                    help="standard (built-in, fast) or best (larger models, fetched once, slower); "
                         "default: Settings")
    sp.add_argument("--language", default="eng",
                    help="language(s) as Tesseract codes, e.g. eng+deu; the built-in engine reads "
                         "English, European languages, Chinese and Japanese as they are, and "
                         "fetches a model once for rus, ukr, kor, ara, fas, hin, tha, ell, tam, tel")
    sp.add_argument("--keep-orientation", action="store_true",
                    help="don't turn sideways or upside-down pages upright")
    sp.add_argument("--force", action="store_true", help="redo pages that already have text")
    sp.add_argument("--deskew", action="store_true", help="straighten crooked scans")

    sp = per_file("clean", run_clean, "clean up scans: remove blank pages, turn pages upright")
    sp.add_argument("--keep-blank", action="store_true", help="don't remove blank pages")
    sp.add_argument("--sensitivity", choices=list(scan.BLANK_THRESHOLDS), default="medium",
                    help="blank page detection: low (only empty pages), medium (default), "
                         "high (also nearly empty pages)")
    sp.add_argument("--rotate", action="store_true",
                    help="turn sideways/upside-down pages upright (needs Tesseract)")

    sp = per_file("flatten", run_flatten,
                  "make form fields and comments part of the page (links keep working)")
    sp.add_argument("--forms-only", action="store_true", help="leave comments as they are")
    sp.add_argument("--comments-only", action="store_true", help="leave form fields as they are")

    sp = per_file("flatten-layers", run_flatten_layers,
                  "for drawings with layers: keep the layers that show as part of the page, "
                  "remove the hidden layers' content, and remove the layers")
    sp.add_argument("--hide", action="append", default=[], metavar="NAME",
                    help="hide this layer too (repeat for more, or separate names with commas)")
    sp.add_argument("--show", action="append", default=[], metavar="NAME",
                    help="show this layer too, though the file opens with it hidden")
    sp.add_argument("--list", action="store_true", help="only list the layers and whether they show")

    sp = per_file("encrypt-for", run_encrypt_for,
                  "protect a PDF for particular people: only their Digital IDs open it (no password)")
    sp.add_argument("--cert", action="append", metavar="FILE",
                    help="a person's certificate (.cer, .crt, .pem; repeat for each person). Your own "
                         "Digital ID (.pfx) can be given for yourself, with --id-password")
    sp.add_argument("--id-password", help="the password of a Digital ID given with --cert")

    sp = per_file("open-with-id", run_open_with_id,
                  "open a PDF protected for particular people with your Digital ID; writes an "
                  "unprotected copy")
    sp.add_argument("--id", required=True, metavar="FILE", help="your Digital ID (.pfx or .p12)")
    sp.add_argument("--id-password", help="its password (asked for if left out)")

    sp = per_file("upgrade-protection", run_upgrade_protection,
                  "protect a PDF that has old, weak encryption (RC4) with AES-256 instead, keeping "
                  "its password and limits; give the permissions password if it has one")
    sp.add_argument("--check", action="store_true",
                    help="only say how each file is protected (exit code 2 when one is weak)")

    sp = per_file("make-safe", run_make_safe,
                  "take out what could do harm in a PDF from someone else (scripts, actions that "
                  "start programs or send what's typed, attachments that can run); keeps the rest")
    sp.add_argument("--check", action="store_true",
                    help="only say what the file can do, without writing a copy (exit code 2 "
                         "when a file has something that could do harm, 0 when none does)")

    sp = per_file("sanitize", run_sanitize,
                  "remove hidden data: document details, scripts, attached files")
    sp.add_argument("--keep-metadata", action="store_true",
                    help="keep author, title, dates and other document details")
    sp.add_argument("--keep-javascript", action="store_true", help="keep scripts and actions")
    sp.add_argument("--keep-attachments", action="store_true", help="keep attached files")
    sp.add_argument("--comments", action="store_true",
                    help="also remove comments, highlights and notes")

    # -- digital ID signatures (certsign.py) --
    sp = per_file("sign-cert", run_sign_cert,
                  "sign with a digital ID (a .pfx or .p12 certificate file), like Acrobat's "
                  "Sign with a digital ID")
    sp.add_argument("--pfx", required=True, metavar="FILE", help="the digital ID (.pfx or .p12)")
    sp.add_argument("--pfx-password", help=f"its password (better: let pdfm prompt, or set "
                                           f"{PFX_PASSWORD_ENV})")
    sp.add_argument("--page", help="page for a visible signature (a number, or last); without "
                                   "--page or --field the signature is invisible")
    sp.add_argument("--box", metavar="X0,Y0,X1,Y1",
                    help="where on --page, in points from the bottom-left corner (default: "
                         "--position and --size)")
    sp.add_argument("--position", choices=certsign.POSITIONS, default="bottom-right",
                    help="where on --page without --box (default: bottom-right)")
    sp.add_argument("--size", choices=list(certsign.SIZES), default="medium",
                    help="size of the signature without --box (default: medium)")
    sp.add_argument("--field", metavar="NAME", help="sign this empty signature field")
    sp.add_argument("--reason", help="e.g. 'I approve this document'")
    sp.add_argument("--location", help="where you sign, e.g. a city")
    sp.add_argument("--contact", help="how to reach you")
    sp.add_argument("--timestamp", metavar="URL",
                    help="RFC 3161 timestamp server, e.g. http://timestamp.digicert.com")

    sp = other("verify", cmd_verify,
               "check the digital signatures in PDFs (exit code 0 when every file has "
               "signatures and all are valid)")
    sp.add_argument("inputs", nargs="+", metavar="input", help="PDF(s) to check")
    sp.add_argument("--password", help=f"password for encrypted input (better: let pdfm "
                                       f"prompt, or set {PASSWORD_ENV})")
    sp.add_argument("--trust", action="append", metavar="CERT",
                    help="a certificate you trust (.cer/.crt/.pem), e.g. your company's root; "
                         "repeat for more")
    sp.add_argument("--json", action="store_true", help="print JSON")
    # -- end of digital ID signatures --

    sp = per_file("redact", run_redact,
                  "permanently black out text or areas (what's under them is removed)")
    sp.add_argument("-t", "--text", action="append", metavar="TEXT",
                    help="text to black out (repeat for more)")
    sp.add_argument("--pattern", action="append", metavar="REGEX",
                    help="regular expression to black out, e.g. '\\d{3}-\\d{2}-\\d{4}'")
    sp.add_argument("--find", action="append", choices=list(redact.PRESETS),
                    help="also black out all emails, phone numbers or long numbers")
    sp.add_argument("--box", action="append", metavar="PAGE:X0,Y0,X1,Y1",
                    help="black out an area, in points from the bottom-left corner")
    sp.add_argument("--case-sensitive", action="store_true")
    sp.add_argument("--whole-words", action="store_true")
    sp.add_argument("--dpi", type=_positive(int), default=200,
                    help="resolution of pages turned into pictures")
    # -- true redaction --
    sp.add_argument("--method", choices=list(redact.METHODS), default="remove",
                    help="remove: take out what's under the boxes, the rest of the page stays "
                         "searchable text (default; pages that can't be edited safely become "
                         "pictures); image: turn every redacted page into a picture")
    # -- end of true redaction --

    sp = per_file("form", run_form,
                  "add fillable form fields (without --field: list the fields already there)")
    sp.add_argument("-f", "--field", action="append", metavar="TYPE:PAGE:X0,Y0,X1,Y1[:NAME]",
                    help=f"add a field ({', '.join(forms.TYPES)}), in points from the bottom-left "
                         "corner, e.g. text:1:72,700,300,720:Name (repeat for more); a radio "
                         "button's NAME is GROUP=VALUE, a dropdown's NAME=CHOICE|CHOICE|...")
    sp.add_argument("--detect", action="store_true",
                    help="find the fields of a printed form (its lines, boxes and tick boxes) "
                         "and add them")
    sp.add_argument("--fields", metavar="FILE",
                    help='fields from a JSON file: [{"type", "page", "rect": [x0, y0, x1, y1], '
                         '"name"}]')
    sp.add_argument("--edit", metavar="FILE",
                    help='change the fields the form has, from a JSON file: [{"page", "index" '
                         '(the field\'s place on its page, from 0, in the order listed without '
                         '--field), and any of "rect", "name", "tooltip", "required", '
                         '"readonly", "type", "options", "format", "decimals", "separators", '
                         '"currency", "currency_after", "date", "min", "max", "calculate", '
                         '"to_page", "delete"}]')
    sp.add_argument("--font-size", type=float, default=0,
                    help="text size in the fields (default: fit single-line fields to the box)")
    sp.add_argument("--no-border", action="store_true", help="don't draw a border around fields")

    sp = per_file("pdfa", run_pdfa, "convert to PDF/A for long-term archiving")
    sp.add_argument("--engine", choices=archive.PDFA_ENGINES, default="auto",
                    help="auto (default): ZPDF's own converter, Ghostscript for what it can't "
                         "put right; builtin; ghostscript")
    sp.add_argument("--level", choices=archive.PDFA_LEVELS, default="2",
                    help="PDF/A-1b, -2b (default) or -3b")

    sp = other("check-pdfa", cmd_check_pdfa,
               "check whether PDFs meet PDF/A (exit code 0 when every file passes); uses "
               "veraPDF when it's installed")
    sp.add_argument("inputs", nargs="+", metavar="input", help="PDF(s) to check")
    sp.add_argument("--level", choices=pdfacheck.LEVELS,
                    help="the level to check against (default: the one the file says, else 2b)")
    sp.add_argument("--engine", choices=pdfacheck.ENGINES, default="auto",
                    help="veraPDF (when installed) or ZPDF's own checks (builtin)")
    sp.add_argument("--password", help=f"password for encrypted input (better: let pdfm "
                                       f"prompt, or set {PASSWORD_ENV})")
    sp.add_argument("--json", action="store_true", help="print JSON")

    # -- accessibility --
    sp = per_file("tag", run_tag,
                  "make PDFs accessible: tag their headings, paragraphs, lists, tables, figures "
                  "and links for screen readers (like Acrobat's Autotag); the pages look the same")
    sp.add_argument("--lang", help="the document's language, e.g. en, de, fr-CA (default: "
                                   "guessed from the text)")
    sp.add_argument("--title", help="the document's title (default: the one it has, else its "
                                    "first heading)")
    sp.add_argument("--alt", metavar="FILE",
                    help='descriptions of the figures, from a JSON file: {"3": ["the first '
                         'figure on page 3", "the second"]}')
    sp.add_argument("--force", action="store_true",
                    help="replace the tags of a file that is already tagged (otherwise they're "
                         "kept)")

    sp = other("check-access", cmd_check_access,
               "check PDFs for what keeps screen readers from reading them (PDF/UA basics, like "
               "Acrobat's Accessibility Checker; exit code 0 when no file has problems)")
    sp.add_argument("inputs", nargs="+", metavar="input", help="PDF(s) to check")
    sp.add_argument("--password", help=f"password for encrypted input (better: let pdfm "
                                       f"prompt, or set {PASSWORD_ENV})")
    sp.add_argument("--json", action="store_true", help="print JSON")
    # -- end of accessibility --

    sp = other("preflight", cmd_preflight,
               "check PDFs for commercial print readiness (CMYK/RGB, TAC ink limits, overprint, PDF/X-1a & PDF/X-4, "
               "image DPI, unembedded fonts, bleed, page sizes, hairlines; exit code 0 when no errors)")
    sp.add_argument("inputs", nargs="+", metavar="input", help="PDF(s) to check")
    sp.add_argument("--profile", choices=("standard", "pdfx-1a", "pdfx-4"), default="standard",
                    help="compliance profile: standard, pdfx-1a, or pdfx-4 (default: standard)")
    sp.add_argument("--max-tac", type=float, default=300.0,
                    help="maximum Total Ink Coverage %% for CMYK vectors and images (default: 300)")
    sp.add_argument("--min-dpi", type=float, default=300, help="warning DPI threshold for images (default: 300)")
    sp.add_argument("--error-dpi", type=float, default=150, help="error DPI threshold for images (default: 150)")
    sp.add_argument("--min-bleed", type=float, default=3.0, help="minimum bleed in mm when TrimBox is set (default: 3.0)")
    sp.add_argument("--password", help=f"password for encrypted input (better: let pdfm "
                                       f"prompt, or set {PASSWORD_ENV})")
    sp.add_argument("--json", action="store_true", help="print JSON")

    sp = other("auto-file", cmd_auto_file,
               "automatically rename and file PDFs by content rules (dry-run by default; pass --apply to move)")
    sp.add_argument("inputs", nargs="*", metavar="input", help="PDF file(s) or folder(s) to file")
    sp.add_argument("--rules", metavar="FILE", help="JSON/YAML rules file (default: built-in starter rules)")
    sp.add_argument("--out-dir", metavar="DIR", help="base destination folder (default: each file's folder)")
    sp.add_argument("--apply", action="store_true", help="apply renames/moves (without this, prints dry-run preview)")
    sp.add_argument("--undo", action="store_true", help="undo the last applied auto-file batch")
    sp.add_argument("--undo-log", metavar="FILE", help="custom path for the undo manifest")
    sp.add_argument("--password", help=f"password for encrypted input (or set {PASSWORD_ENV})")
    sp.add_argument("--json", action="store_true", help="print JSON")

    sp = other("translate", cmd_translate,
               "translate a PDF offline using an installed language pack (side-by-side, bilingual-pages, bilingual, in-place, manga, or text)")
    sp.add_argument("input", metavar="input.pdf", help="PDF to translate")
    sp.add_argument("--from", dest="from_lang", default="en", help="source language code (default: en)")
    sp.add_argument("--to", dest="to_lang", required=True, help="target language code (e.g. fr, es, de, zh-Hans)")
    sp.add_argument("--mode", choices=("side-by-side", "bilingual-pages", "bilingual", "in-place", "manga", "text"), default="side-by-side",
                    help="output layout mode (default: side-by-side)")
    sp.add_argument("-o", "--output", help="output file path")
    sp.add_argument("--password", help=f"password for encrypted input (or set {PASSWORD_ENV})")
    sp.add_argument("--json", action="store_true", help="print JSON")

    def cmd_translate_manga_batch(a):
        from pdfmanager.translate import manga as manga_mod

        out_dir = a.output or str(Path(a.inputs[0]).resolve().parent / f"translated-{a.to_lang}")
        res = manga_mod.translate_manga_batch(
            a.inputs,
            out_dir,
            src_lang=a.from_lang,
            dst_lang=a.to_lang,
            export_format=a.format,
            manga_ai=not a.no_ai,
            password=a.password,
        )
        if a.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"Translated {res['succeeded']}/{res['succeeded'] + res['failed']} chapter(s) "
                  f"({res['total_pages']} pages) → {out_dir}")
        return 0 if res["succeeded"] > 0 else 1

    sp = other("translate-manga-batch", cmd_translate_manga_batch,
               "batch-translate a folder or list of .cbz/.cbr/.pdf manga chapters offline")
    sp.add_argument("inputs", nargs="+", metavar="input", help="manga files (.cbz, .cbr, .pdf) or folders")
    sp.add_argument("--from", dest="from_lang", default="ja", help="source language code (default: ja)")
    sp.add_argument("--to", dest="to_lang", default="en", help="target language code (default: en)")
    sp.add_argument("--format", choices=("cbz", "pdf"), default="cbz", help="output format (default: cbz)")
    sp.add_argument("--no-ai", action="store_true", help="disable Enhanced Manga AI ONNX models")
    sp.add_argument("-o", "--output", help="output folder path")
    sp.add_argument("--password", help=f"password for encrypted PDFs (or set {PASSWORD_ENV})")
    sp.add_argument("--json", action="store_true", help="print JSON")

    sp = other("cbz-to-pdf", cmd_cbz_to_pdf,
               "convert a .cbz comic/manga archive to a PDF (natural page sort + optional RTL viewer)")
    sp.add_argument("input", metavar="input.cbz", help="CBZ archive to convert")
    sp.add_argument("-o", "--output", help="output PDF path")
    sp.add_argument("--rtl", action="store_true", help="set right-to-left manga viewer direction")
    sp.add_argument("--json", action="store_true", help="print JSON")

    sp = other("pdf-to-cbz", cmd_pdf_to_cbz,
               "export a PDF as a .cbz comic/manga archive (WebP, JPEG, or PNG pages)")
    sp.add_argument("input", metavar="input.pdf", help="PDF to export")
    sp.add_argument("-o", "--output", help="output CBZ path")
    sp.add_argument("--dpi", type=float, default=150.0, help="render DPI (default: 150)")
    sp.add_argument("--format", choices=("webp", "jpg", "png"), default="webp", help="image format (default: webp)")
    sp.add_argument("--quality", type=int, default=88, help="WebP/JPEG quality 50-100 (default: 88)")
    sp.add_argument("--password", help=f"password for encrypted input (or set {PASSWORD_ENV})")
    sp.add_argument("--json", action="store_true", help="print JSON")

    sp = other("spine", cmd_spine,
               "calculate book spine width and full-wrap cover dimensions for print-on-demand")
    sp.add_argument("pages", type=int, help="total interior page count")
    sp.add_argument("--paper", choices=tuple(core.PAPER_CALIPER_MM), default="white_90gsm",
                    help="paper stock preset (default: white_90gsm)")
    sp.add_argument("--caliper", type=float, help="custom sheet thickness in mm")
    sp.add_argument("--cover-mm", type=float, default=0.35, help="cover stock allowance in mm (default: 0.35)")
    sp.add_argument("--trim-w", type=float, help="trim page width in mm (to compute full cover wrap)")
    sp.add_argument("--trim-h", type=float, help="trim page height in mm (to compute full cover wrap)")
    sp.add_argument("--bleed-mm", type=float, default=3.0, help="cover bleed in mm (default: 3.0)")
    sp.add_argument("--json", action="store_true", help="print JSON")

    sp = per_file("bookmarks", run_bookmarks,
                  "list, set or detect bookmarks (without options: list them)")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--set", metavar="FILE",
                   help="replace the bookmarks with those in FILE: one per line, "
                        "'- Title (p. 3)', indented two spaces per level (or JSON)")
    g.add_argument("--detect", action="store_true",
                   help="create bookmarks from the document's headings")
    g.add_argument("--clear", action="store_true", help="remove all bookmarks")
    sp.add_argument("-n", "--dry-run", action="store_true",
                    help="with --detect: only print what would be added")

    sp = per_file("text", run_text, "extract text",
                  out_help="write to a .txt file instead of the terminal")
    sp.add_argument("-p", "--pages", help=PAGES_HELP)

    sp = other("compare", cmd_compare, "compare two versions of a PDF and write a report")
    sp.add_argument("old", help="the original")
    sp.add_argument("new", help="the changed version")
    sp.add_argument("-o", "--output", help="report file (default: <old>_vs_<new>.pdf)")
    sp.add_argument("--all-pages", action="store_true",
                    help="show unchanged pages in the report too")
    sp.add_argument("--mode", choices=compare.MODES, default="both",
                    help="compare both text and visual layout (default), text only, or visual only")
    sp.add_argument("--json", action="store_true",
                    help="print structured JSON diff summary (skips PDF creation unless -o is given)")
    sp.add_argument("--password")

    sp = per_file("to-word", run_to_word, "convert PDFs to editable Word documents (.docx)",
                  out_help="output .docx (default: <input>.docx); with several inputs, a folder")
    sp.add_argument("--engine", choices=office.TO_WORD_ENGINES, default="auto",
                    help="auto (default): Microsoft Word if installed, else the built-in "
                         "converter; word; basic (built-in: paragraphs, headings, pictures); "
                         "layout (LibreOffice: exact look, text in boxes)")

    sp = per_file("quantities", run_quantities,
                  "a schedule (takeoff) of what was measured on a drawing: the dimensions and measurements "
                  "saved in the PDF, with totals per group, as an Excel workbook or a CSV file",
                  out_help="output .xlsx or .csv (default: <input>_quantities.xlsx); with several inputs, a folder")
    sp.add_argument("--csv", action="store_true", help="write a CSV file instead of a workbook")

    sp = per_file("to-excel", run_to_excel,
                  "export the tables in PDFs to Excel workbooks (.xlsx) or CSV files",
                  out_help="output .xlsx (default: <input>.xlsx), or with --csv a folder (default: "
                           "<input>_tables); with several inputs, a folder")
    sp.add_argument("-p", "--pages", help=PAGES_HELP)
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--one-sheet", action="store_true",
                   help="put all tables on one sheet, one under the other (default: a sheet "
                        "per table)")
    g.add_argument("--csv", action="store_true",
                   help="write a CSV file per table instead of a workbook")

    sp = per_file("to-dxf", run_to_dxf,
                  "export vector drawings in PDFs to CAD DXF files (.dxf)",
                  out_help="output .dxf (default: <input>.dxf); with several inputs, a folder")
    sp.add_argument("-p", "--pages", help=PAGES_HELP)
    sp.add_argument("--scale", help="drawing scale such as 1:100 (default: read from the PDF's /VP /Measure)")

    sp = other("from-dxf", cmd_from_dxf,
               "convert CAD drawings (.dxf, or .dwg with ODA File Converter) to layered, scaled PDFs")
    sp.add_argument("inputs", nargs="+", metavar="drawing", help="DXF or DWG files")
    sp.add_argument("-o", "--output", help="output .pdf; with several inputs, a folder")
    sp.add_argument("--layout", default="Model", help="layout name (default: Model)")
    sp.add_argument("--paper", default="A3", help="paper size (A4, A3, A2, A1, A0, Letter, Tabloid, Arch D)")
    sp.add_argument("--scale", default="fit", help="plot scale: fit, 1:50, 1:100, etc. (default: fit)")
    sp.add_argument("--monochrome", action="store_true", help="draw all lines and text in black")

    per_file("sheet-split", run_sheet_split,
             "split a drawing set into one PDF per sheet named from its title block",
             out_help="output folder (default: <input>_sheets)")
    per_file("sheet-name", run_sheet_name,
             "set PDF page labels from each sheet's title-block number (e.g. A-101)")
    per_file("sheet-link", run_sheet_link,
             "add clickable links from detail/sheet references (e.g. 2/A-102, SEE A-201) to the target sheet")
    sp = other("sheet-compare", cmd_sheet_compare,
               "compare two versions of a drawing set sheet by sheet, matched by sheet number")
    sp.add_argument("old", help="first (older) drawing set PDF")
    sp.add_argument("new", help="second (newer) drawing set PDF")
    sp.add_argument("-o", "--output", help="optional overlay diff PDF of changed sheets")
    sp.add_argument("-P", "--password", help="password if the PDFs are encrypted")

    sp = per_file("comments", run_comments,
                  "list the comments in PDFs, or make a summary (PDF, Markdown), a spreadsheet (CSV) or an "
                  "XFDF file of them; or add comments from an XFDF file")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--summary", action="store_true", help="a PDF listing the comments page by page")
    g.add_argument("--markdown", "--md", dest="markdown", action="store_true",
                   help="a Markdown (.md) review summary of the comments")
    g.add_argument("--csv", action="store_true", help="a spreadsheet of the comments")
    g.add_argument("--export", action="store_true", help="an XFDF file of the comments (Acrobat reads it)")
    g.add_argument("--import", dest="import_xfdf", metavar="XFDF", help="add the comments of an XFDF file")
    sp.add_argument("--group-by", choices=comments.GROUP_MODES, default="page",
                    help="how to group comments in --markdown (default: page)")
    sp.add_argument("--author", help="only include comments whose author contains this text")
    sp.add_argument("--kind", help="only include comments of this type (e.g. Highlight, Note, Cloud)")
    sp.add_argument("-p", "--pages", help=PAGES_HELP)

    sp = other("privacy", cmd_privacy, "what ZPDF keeps on this computer and when it goes online; "
                                       "clear the history, or stop keeping one")
    sp.add_argument("--clear-history", action="store_true",
                    help="empty the Recent list, what was read from your files and the text kept by search")
    sp.add_argument("--remember", choices=("on", "off"),
                    help="keep a history of the files you open (on, the default) or none (off, which also clears it)")

    sp = other("web", cmd_web, "save web pages as PDFs, as the browser prints them (needs "
                               "Microsoft Edge, Chrome or Chromium)")
    sp.add_argument("inputs", nargs="+", metavar="address-or-file",
                    help="web addresses (https://…) or saved web pages (.html, .mht)")
    sp.add_argument("-o", "--output", help="output file; with several inputs, a folder")
    # -- pptx and html --
    sp = per_file("to-pptx", run_to_pptx,
                  "convert PDFs to PowerPoint presentations (.pptx), a slide per page",
                  out_help="output .pptx (default: <input>.pptx); with several inputs, a folder")
    sp.add_argument("-p", "--pages", help=PAGES_HELP)
    sp.add_argument("--pictures", dest="mode", action="store_const", const="pictures",
                    default="editable",
                    help="make each slide a picture of the page (default: editable text boxes "
                         "over the page's pictures and drawings)")

    sp = per_file("to-html", run_to_html, "convert PDFs to web pages (.html, pictures inside)",
                  out_help="output .html (default: <input>.html); with several inputs, a folder")
    sp.add_argument("-p", "--pages", help=PAGES_HELP)
    sp.add_argument("--exact", dest="mode", action="store_const", const="exact",
                    default="flowing",
                    help="keep each page as it looks, text over a picture of the page "
                         "(default: flowing text with headings, lists, tables and pictures)")
    # -- end of pptx and html --

    sp = per_file("to-md", run_to_md, "convert PDFs to structured Markdown (.md)",
                  out_help="output .md (default: <input>.md); with several inputs, a folder")
    sp.add_argument("-p", "--pages", help=PAGES_HELP)
    sp.add_argument("--images", action="store_true",
                    help="extract page pictures as PNGs into <name>_assets/ and link them")
    sp.add_argument("--page-breaks", action="store_true",
                    help="insert page separator comments and horizontal rules between pages")
    sp.add_argument("--front-matter", action="store_true",
                    help="prepend YAML metadata front matter (title, author, pages, source)")

    sp = other("from-office", cmd_from_office,
               "convert Word, Excel, PowerPoint, OpenDocument, RTF or text files to PDF "
               "(needs Microsoft Office or LibreOffice; text files always work)")
    sp.add_argument("inputs", nargs="+")
    sp.add_argument("-o", "--output", help="output PDF, or a folder for several inputs "
                                           "(default: next to each input)")

    sp = other("recipe", cmd_recipe, "save and run recipes: several tools in a row")
    sp.add_argument("action", choices=["list", "show", "save", "run", "delete"])
    sp.add_argument("name", nargs="?", help="recipe name")
    sp.add_argument("args", nargs="*", metavar="ARG",
                    help="save: steps and their options, e.g. clean ocr language=eng compress "
                         "level=high; run: the PDF(s)")
    sp.add_argument("-o", "--output", help="run: output file, or a folder for several inputs")
    sp.add_argument("--password")

    sp = other("from-images", cmd_from_images, "save images (JPG, PNG, ...) as a PDF")
    sp.add_argument("images", nargs="+")
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("-s", "--size", help="fit images onto this paper size (default: image size)")
    sp.add_argument("--margin", type=float, default=0, help="margin in mm")

    def scan_cleanup(sp):
        sp.add_argument("--no-ocr", action="store_true", help="don't make it searchable")
        sp.add_argument("--language", default="eng", help="OCR language(s), e.g. eng+deu (default eng)")
        sp.add_argument("--keep-blank", action="store_true", help="keep blank pages")
        sp.add_argument("--no-straighten", action="store_true", help="don't straighten crooked pages")
        sp.add_argument("--no-upright", action="store_true", help="don't turn sideways pages upright")

    sp = other("scan", cmd_scan, "scan paper with a scanner into a searchable PDF (Windows)")
    sp.add_argument("-o", "--output", help="the PDF to make")
    sp.add_argument("--list", action="store_true", help="list the scanners and stop")
    sp.add_argument("--scanner", help="which scanner (part of its name, or its id from --list); "
                                      "default: the first")
    sp.add_argument("--mode", choices=["grey", "colour", "bw"], default="grey",
                    help="greyscale (default), colour or black and white")
    sp.add_argument("--dpi", type=int, default=300, choices=[150, 200, 300], help="resolution (default 300)")
    sp.add_argument("--feeder", action="store_true",
                    help="scan every page in the document feeder (default: one page at a time from the glass)")
    sp.add_argument("--duplex", action="store_true",
                    help="with --feeder: both sides of each sheet (network scanners that can)")
    sp.add_argument("--add", metavar="ADDRESS",
                    help="remember a network scanner by its address (e.g. 192.168.1.40); it needs no driver "
                         "(AirScan / eSCL)")
    scan_cleanup(sp)

    sp = other("photos-to-pdf", cmd_photos_to_pdf,
               "make a searchable PDF from phone photos of pages (finds and flattens each page)")
    sp.add_argument("photos", nargs="+", help="the photos (JPG or PNG), in page order")
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("--look", choices=["document", "colour", "photo"], default="document",
                    help="document: grey with white paper (default); colour: white paper, colours kept; "
                         "photo: as taken")
    sp.add_argument("--paper", choices=["auto", "a4", "letter"], default="auto",
                    help="page size (default: A4 or Letter, whichever the page's shape is closer to)")
    scan_cleanup(sp)

    sp = per_file("extract-media", run_media,
                  "save the pictures and attached files inside PDFs",
                  out_help="output folder (default: <input>_media)")
    sp.add_argument("--no-images", action="store_true", help="only attached files")
    sp.add_argument("--no-attachments", action="store_true", help="only pictures")
    sp.add_argument("--min-size", type=int, default=16,
                    help="skip pictures smaller than this many pixels (default: 16)")

    sp = per_file("to-images", run_to_images, "render pages to image files",
                  out_help="output folder (default: <input>_images)")
    sp.add_argument("--dpi", type=_positive(int), default=150)
    sp.add_argument("-f", "--format", default="png", help="png, jpg, ... (default: png)")
    sp.add_argument("-p", "--pages", help=PAGES_HELP)

    sp = other("organize", cmd_organize, "sort PDFs in a folder into sub-folders")
    sp.add_argument("directory")
    sp.add_argument("--by", choices=organize.GROUPINGS, default="year")
    sp.add_argument("--dest", help="where to create the sub-folders (default: the directory)")
    sp.add_argument("--move", action="store_true", help="move files instead of copying")
    sp.add_argument("--duplicates", choices=organize.DUPLICATE_MODES, default="keep",
                    help="what to do with files identical to one already organized: keep "
                         "(default), skip, or folder (put in a Duplicates folder)")
    sp.add_argument("-R", "--recursive", action="store_true")
    sp.add_argument("-n", "--dry-run", action="store_true", help="only show what would happen")

    sp = other("rename", cmd_rename, "rename PDFs from their metadata")
    sp.add_argument("directory")
    sp.add_argument("--pattern", default="{date:%Y-%m-%d} {title}",
                    help="fields: {title} {author} {pages} {date:%%Y-%%m-%%d} {stem}")
    sp.add_argument("-R", "--recursive", action="store_true")
    sp.add_argument("-n", "--dry-run", action="store_true")

    sp = other("catalog", cmd_catalog, "write a CSV inventory of a folder of PDFs")
    sp.add_argument("directory")
    sp.add_argument("-o", "--output", default="catalog.csv")
    sp.add_argument("-R", "--recursive", action="store_true")

    sp = other("duplicates", cmd_duplicates, "find identical PDFs in a folder")
    sp.add_argument("directory")
    sp.add_argument("-R", "--recursive", action="store_true")
    sp.add_argument("--delete", action="store_true", help="delete all but the oldest copy")

    sp = other("search", cmd_search, "find text in all the PDFs in a folder")
    sp.add_argument("directory")
    sp.add_argument("query")
    sp.add_argument("-R", "--recursive", action="store_true")
    sp.add_argument("--case-sensitive", action="store_true")
    sp.add_argument("--whole-words", action="store_true")
    sp.add_argument("--best", action="store_true",
                    help="rank pages by what they are about as well as their words, best first: "
                         "\"how much do I owe\" finds \"balance due\" (English; Chinese by its words)")
    sp.add_argument("--limit", type=int, default=500, help="most pages to list (default: 500)")

    sp = other("watch", cmd_watch,
               "watch an inbox folder and process new PDFs automatically")
    sp.add_argument("inbox")
    sp.add_argument("--dest", required=True, help="where processed files go")
    sp.add_argument("--by", choices=organize.GROUPINGS, help="sort into sub-folders")
    sp.add_argument("--rename", metavar="PATTERN", help="rename, e.g. '{date:%%Y-%%m-%%d} {title}'")
    sp.add_argument("--compress", choices=["low", "medium", "high", "large"])
    sp.add_argument("--no-sharp-scans", dest="sharp_scans", action="store_false",
                    help="with --compress: don't keep scanned text at full sharpness")
    sp.add_argument("--clean", action="store_true", help="remove blank pages")
    sp.add_argument("--rotate", action="store_true",
                    help="turn sideways/upside-down pages upright (needs Tesseract)")
    sp.add_argument("--ocr", action="store_true", help="make scans searchable")
    sp.add_argument("--engine", choices=ocr.ENGINES, default="auto", help="OCR engine (see pdfm ocr)")
    sp.add_argument("--recipe", metavar="NAME", help="run a saved recipe on each file")
    sp.add_argument("--language", default="eng", help="OCR language(s) (see pdfm ocr)")
    sp.add_argument("--skip-duplicates", action="store_true",
                    help="don't process files identical to one already in --dest")
    sp.add_argument("--delete-originals", action="store_true",
                    help="delete originals instead of moving them to <inbox>/originals")
    sp.add_argument("--interval", type=_positive(float), default=5, help="seconds between checks")
    sp.add_argument("--once", action="store_true", help="process what's there now and exit")

    sp = other("setup-windows", cmd_setup_windows,
               "Windows: add Start menu and desktop shortcuts, Open with for PDFs and an "
               "uninstall entry (used by the installer)")
    sp.add_argument("--remove", action="store_true", help="take them away again")
    sp.add_argument("--no-desktop", action="store_true", help="no desktop shortcut")
    sp.add_argument("--no-open-with", action="store_true", help='don\'t add to "Open with"')
    sp.add_argument("--no-context-menu", action="store_true",
                    help="don't add ZPDF's actions to Explorer's right-click menu")
    sp.add_argument("--app-dir", help="the installed app's folder (default: found from Python)")

    for name, help_ in (("gui", "open the graphical app in its own window (or your browser "
                                "if no window can be opened)"),
                        ("view", "read PDFs in the app's PDF reader")):
        sp = other(name, cmd_gui, help_)
        sp.add_argument("files", nargs="*" if name == "gui" else "+",
                        help="PDF(s) to open in the reader")
        sp.add_argument("--browser", action="store_true",
                        help="use the web browser, not a window")
        sp.add_argument("--port", type=int, default=0, help="port (default: any free port)")
        sp.add_argument("--no-browser", action="store_true",
                        help="just start the server and print its link")
        sp.add_argument("--lan", action="store_true",
                        help="allow Android Companion / LAN devices on your local network to connect")
        if name == "gui":
            from pdfmanager.quickactions import ACTIONS

            sp.add_argument("--background", action="store_true",
                            help="start with no window, only the icon in the notification area, so watch "
                                 "folders work (Windows; what \"start when I sign in\" runs)")
            sp.add_argument("--action", choices=sorted(ACTIONS),
                            help="run a right-click action on the files in the app (Explorer's "
                                 "menu uses it; files given together go into one job)")

    def cmd_profile(a):
        from pdfmanager import secprofiles

        if a.profile_cmd == "list":
            for prof in secprofiles.list_profiles():
                kind = "built-in" if prof.get("builtin") else "custom"
                desc = prof.get("description") or ""
                print(f"{prof['name']} ({kind})" + (f" — {desc}" if desc else ""))
            return 0
        if a.profile_cmd == "apply":
            res = secprofiles.apply_profile(
                a.input,
                a.output,
                a.name,
                password=a.password,
                user_password=a.user_password,
                owner_password=a.owner_password,
            )
            print(f"-> {res['output']}  (profile {res['profile']}: {', '.join(res['steps']) or 'no changes'})")
            return 0
        if a.profile_cmd == "export":
            out = secprofiles.export_profile(a.name, a.output)
            print(f"-> {out}")
            return 0
        if a.profile_cmd == "import":
            prof = secprofiles.import_profile(a.file)
            print(f"imported security profile {prof['name']!r}")
            return 0
        return 1

    sp = other("profile", cmd_profile, "list, apply, export, or import saved security profiles")
    psub = sp.add_subparsers(dest="profile_cmd", required=True)
    psub.add_parser("list", help="list available security profiles")
    p_apply = psub.add_parser("apply", help="apply a security profile to a PDF")
    p_apply.add_argument("name", help="security profile name (e.g. Confidential, Internal, Public Share)")
    p_apply.add_argument("input", help="source PDF")
    p_apply.add_argument("-o", "--output", required=True, help="output PDF")
    p_apply.add_argument("--password", help="password to open the source PDF")
    p_apply.add_argument("--user-password", help="password to open the output PDF")
    p_apply.add_argument("--owner-password", help="permissions password for the output PDF")
    p_exp = psub.add_parser("export", help="export a security profile to a JSON file")
    p_exp.add_argument("name", help="profile name")
    p_exp.add_argument("-o", "--output", required=True, help="destination JSON file")
    p_imp = psub.add_parser("import", help="import a security profile from a JSON file")
    p_imp.add_argument("file", help="profile JSON file")

    def cmd_audit(a):
        from pdfmanager import auditlog

        if a.audit_cmd == "show":
            entries = auditlog.read_entries(action=a.action, limit=a.limit)
            if a.json:
                print(json.dumps(entries, indent=2, ensure_ascii=False))
            else:
                for e in entries:
                    print(f"{e.get('timestamp')}  {e.get('action')}  {e.get('status')}  "
                          f"{e.get('actor')}  {e.get('target') or '-'}")
            return 0
        if a.audit_cmd == "verify":
            res = auditlog.verify_chain()
            if res["valid"]:
                print(f"audit log verified OK ({res['count']} entries)")
                return 0
            print(f"audit log integrity FAILED ({len(res['errors'])} error(s) in {res['count']} entries):",
                  file=sys.stderr)
            for err in res["errors"]:
                print(f"  line {err['line']}: {err['reason']}", file=sys.stderr)
            return 2
        if a.audit_cmd == "export":
            out = auditlog.export_entries(a.output, fmt=a.format, action=a.action)
            print(f"-> {out}")
            return 0
        return 1

    sp = other("audit", cmd_audit, "inspect, verify, or export the tamper-evident security audit log")
    asub = sp.add_subparsers(dest="audit_cmd", required=True)
    a_show = asub.add_parser("show", help="show recent audit log entries")
    a_show.add_argument("--action", help="filter by action name (e.g. redact, profile_applied)")
    a_show.add_argument("--limit", type=int, default=50, help="maximum entries to show")
    a_show.add_argument("--json", action="store_true", help="output JSON")
    asub.add_parser("verify", help="verify SHA-256 hash chain integrity of the audit log")
    a_exp = asub.add_parser("export", help="export the audit log to JSON or CSV")
    a_exp.add_argument("-o", "--output", required=True, help="destination file")
    a_exp.add_argument("--format", choices=["json", "csv"], default="json")
    a_exp.add_argument("--action", help="filter by action name")

    def cmd_policy(a):
        from pdfmanager import policy

        snap = policy.snapshot()
        if a.json:
            print(json.dumps(snap, indent=2, ensure_ascii=False))
        else:
            for key, info in snap.items():
                lock_tag = " [locked]" if info["locked"] else ""
                print(f"{key}: {info['value']!r}{lock_tag}")
        return 0

    sp = other("policy", cmd_policy, "show effective enterprise administrator policies")
    psub2 = sp.add_subparsers(dest="policy_cmd", required=True)
    pol_show = psub2.add_parser("show", help="show effective policies and locked status")
    pol_show.add_argument("--json", action="store_true", help="output JSON")

    return p


_RANGE_FLAGS = frozenset({"-p", "--pages", "--ranges", "--remove"})
_LEADING_DASH_RANGE_RE = re.compile(r"^-\d+(?:[,-]\d+)*$")


def _leading_dash_ranges(argv):
    """argparse takes '-3,5' for an option name, but '-3' is the page range
    1 to 3; spell such a value out as '1-3,5' when it follows a page-range
    flag so `-p -3,5` works without corrupting filenames like `-2026-report.pdf`."""
    out = []
    prev_is_range_flag = False
    for arg in argv:
        if prev_is_range_flag and _LEADING_DASH_RANGE_RE.match(arg):
            out.append("1" + arg)
            prev_is_range_flag = False
            continue
        out.append(arg)
        prev_is_range_flag = arg in _RANGE_FLAGS
    return out


def main(argv=None):
    # Windows encodes a redirected stdout in the ANSI code page, which can't
    # hold every character a PDF or a file name has; print those escaped
    # instead of stopping the command.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    argv = sys.argv[1:] if argv is None else list(argv)
    args = build_parser().parse_args(_leading_dash_ranges(argv))
    # pypdf logs a warning for every small defect it repairs; only show errors.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    from pdfmanager import applog, progress

    try:
        return args.func(args) or 0
    except progress.Cancelled:
        print("error: operation cancelled", file=sys.stderr)
        return 130
    except (core.PdfManagerError, PageRangeError, OSError, ValueError, KeyError) as exc:
        print(f"error: {applog.format_user_error(exc)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
