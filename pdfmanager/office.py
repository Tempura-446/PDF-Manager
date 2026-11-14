# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Word and Office conversion.

PDF → Word (.docx), by the first available of:
  * Microsoft Word (Windows): opens the PDF and reflows it into an editable
    document, the best result;
  * the built-in converter: headings, paragraphs, lists, tables and
    pictures in reading order, columns one after the other, with bold,
    italic and links kept. It reads the page as PDF → HTML does
    (webpage.flow), scanned pages with a text layer included;
  * LibreOffice ("layout" engine): keeps the exact look, but every line
    becomes a separate text box.

Office → PDF (Word, Excel, PowerPoint, OpenDocument, RTF, text) uses
Microsoft Office on Windows, otherwise LibreOffice. Plain text files can
always be converted.

Office is driven through PowerShell (part of Windows), so no extra Python
packages are needed.
"""

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from pdfmanager import core, fonts, progress

WORD_TYPES = {".doc", ".docx", ".docm", ".dot", ".dotx", ".rtf", ".odt", ".wpd", ".txt"}
EXCEL_TYPES = {".xls", ".xlsx", ".xlsm", ".xlsb", ".ods", ".csv"}
POWERPOINT_TYPES = {".ppt", ".pptx", ".pptm", ".pps", ".ppsx", ".odp"}
OFFICE_TYPES = WORD_TYPES | EXCEL_TYPES | POWERPOINT_TYPES
HTML_TYPES = {".html", ".htm", ".xhtml", ".mht", ".mhtml"}
TO_WORD_ENGINES = ("auto", "word", "basic", "layout")
TIMEOUT = 300


# --------------------------------------------------------------------------
# Finding the programs
# --------------------------------------------------------------------------

def _has_com(prog_id):
    if sys.platform != "win32":
        return False
    import winreg

    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + "\\CLSID"))
        return True
    except OSError:
        return False


def has_word():
    return _has_com("Word.Application")


def find_libreoffice():
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    candidates = []
    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
            if base:
                candidates.append(Path(base) / "LibreOffice" / "program" / "soffice.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def available():
    """What this computer can use, for the app to explain choices."""
    return {"word": has_word(), "excel": _has_com("Excel.Application"),
            "powerpoint": _has_com("PowerPoint.Application"),
            "libreoffice": bool(find_libreoffice())}


# --------------------------------------------------------------------------
# Running Office and LibreOffice
# --------------------------------------------------------------------------

# Start-Office makes the program and writes down which process it is (the
# file named by PDFM_PIDS), so that one, and no Word the user has open, can
# be stopped if it never answers: a stopped script leaves its Word running.
# (No double quotes in these scripts: they travel on a command line.)
_PS_PREFIX = r"""$ErrorActionPreference = 'Stop'
function Start-Office($progId, $exe) {
  $named = 'Name=' + [char]39 + $exe + [char]39
  $before = @()
  try { $before = @(Get-CimInstance Win32_Process -Filter $named | ForEach-Object { $_.ProcessId }) } catch {}
  $app = New-Object -ComObject $progId
  try {
    $mine = @(Get-CimInstance Win32_Process -Filter $named |
      Where-Object { ($before -notcontains $_.ProcessId) -and ($_.CommandLine -match 'automation') } |
      ForEach-Object { $_.ProcessId })
    if ($env:PDFM_PIDS -and $mine.Count) { Set-Content -LiteralPath $env:PDFM_PIDS -Value ($mine -join ' ') }
  } catch {}
  return ,$app
}
"""

PS_PDF_TO_WORD = _PS_PREFIX + r"""
$word = Start-Office 'Word.Application' 'WINWORD.EXE'
$word.AutomationSecurity = 3  # msoAutomationSecurityForceDisable: no macros
$word.WordBasic.DisableAutoMacros(1)
$word.Visible = $false
$word.DisplayAlerts = 0
try {
  $doc = $word.Documents.Open($env:PDFM_IN, $false, $true, $false)
  $doc.SaveAs2($env:PDFM_OUT, 16)
  $doc.Close($false)
} finally { $word.Quit() }
"""

PS_TO_PDF = {
    "word": _PS_PREFIX + r"""
$app = Start-Office 'Word.Application' 'WINWORD.EXE'
$app.AutomationSecurity = 3  # msoAutomationSecurityForceDisable: no macros
$app.WordBasic.DisableAutoMacros(1)
$app.Visible = $false
$app.DisplayAlerts = 0
try {
  $doc = $app.Documents.Open($env:PDFM_IN, $false, $true, $false)
  $doc.ExportAsFixedFormat($env:PDFM_OUT, 17)
  $doc.Close($false)
} finally { $app.Quit() }
""",
    "excel": _PS_PREFIX + r"""
$app = Start-Office 'Excel.Application' 'EXCEL.EXE'
$app.AutomationSecurity = 3  # msoAutomationSecurityForceDisable: no macros
$app.EnableEvents = $false   # no Workbook_Open
$app.AskToUpdateLinks = $false
$app.Visible = $false
$app.DisplayAlerts = $false
try {
  $book = $app.Workbooks.Open($env:PDFM_IN, 0, $true)
  $book.ExportAsFixedFormat(0, $env:PDFM_OUT)
  $book.Close($false)
} finally { $app.Quit() }
""",
    "powerpoint": _PS_PREFIX + r"""
$app = Start-Office 'PowerPoint.Application' 'POWERPNT.EXE'
$app.AutomationSecurity = 3  # msoAutomationSecurityForceDisable: no macros
try {
  $show = $app.Presentations.Open($env:PDFM_IN, -1, 0, 0)
  $show.SaveAs($env:PDFM_OUT, 32)
  $show.Close()
} finally { $app.Quit() }
""",
}


class TookTooLong(core.PdfManagerError):
    """A program didn't finish in time and was stopped."""


def _run(command, env=None, what="the conversion", timeout=None):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout or TIMEOUT,
                                env={**os.environ, **(env or {})},
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise TookTooLong(f"{what} took too long and was stopped") from None
    except OSError as exc:
        raise core.PdfManagerError(f"{what} could not start: {_start_failure(command, exc)}") from None
    return result


def _start_failure(command, exc):
    """Why a program didn't start, in words (not "[Errno 2] ...")."""
    if isinstance(exc, FileNotFoundError):
        return f"{Path(command[0]).name} wasn't found on this computer"
    if isinstance(exc, PermissionError):
        return f"this computer didn't allow {Path(command[0]).name} to run"
    return " ".join(str(exc.strerror or exc).split()) or type(exc).__name__


def _system_exe(name):
    """A Windows program by its full path: a bare name can be found in the
    current folder first."""
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return "\\".join([root.rstrip("\\"), "System32", name])


def _stop_processes(pids):
    """Stop the processes (Windows): an Office program a script started and
    that never answered."""
    for pid in pids:
        try:
            subprocess.run([_system_exe("taskkill.exe"), "/F", "/T", "/PID", str(int(pid))],
                           capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass


def powershell_exe():
    """PowerShell by its full path on Windows: a bare name can be found in
    the current folder first."""
    if sys.platform == "win32":
        return _system_exe("WindowsPowerShell\\v1.0\\powershell.exe")
    return "powershell"


def _powershell(script, src, dst, what, timeout=None):
    """Run an Office script. If it doesn't finish in time, the Office program
    it started is stopped too, not left running out of sight."""
    with tempfile.TemporaryDirectory(prefix="pdfm-office-") as tmp:
        started = Path(tmp) / "started.txt"
        try:
            result = _run([powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-Command", script],
                          env={"PDFM_IN": str(Path(src).resolve()), "PDFM_OUT": str(Path(dst).resolve()),
                               "PDFM_PIDS": str(started)},
                          what=what, timeout=timeout)
        except TookTooLong:
            try:
                pids = started.read_text(encoding="utf-8-sig", errors="replace").split()
            except OSError:
                pids = []
            _stop_processes(pids)
            raise
    if result.returncode or not Path(dst).exists():
        detail = " ".join((result.stderr or result.stdout).split())[:300]
        raise core.PdfManagerError(f"{what} failed: {detail or 'no output'}")
    return dst


def _libreoffice(src, dst, target, import_filter=None):
    soffice = find_libreoffice()
    if not soffice:
        raise core.PdfManagerError(
            "this needs LibreOffice (free, https://www.libreoffice.org) or Microsoft Office")
    with tempfile.TemporaryDirectory(prefix="pdfm-lo-") as tmp:
        tmp = Path(tmp)
        # A profile of its own, so it works while LibreOffice is open.
        profile = (tmp / "profile").resolve().as_uri()
        command = [soffice, f"-env:UserInstallation={profile}", "--headless", "--norestore"]
        if import_filter:
            command.append(f"--infilter={import_filter}")
        command += ["--convert-to", target, "--outdir", str(tmp / "out"), str(src)]
        result = _run(command, what="LibreOffice")
        produced = list((tmp / "out").glob("*")) if (tmp / "out").exists() else []
        if not produced:
            detail = " ".join((result.stderr or result.stdout).split())[-300:]
            raise core.PdfManagerError(f"LibreOffice could not convert {Path(src).name}: {detail}")
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(produced[0]), str(dst))
    return dst


# --------------------------------------------------------------------------
# PDF → Word
# --------------------------------------------------------------------------

# Set when Word didn't finish importing a PDF: on some computers it never
# does when it's automated. "Best available" then uses ZPDF's own converter
# for the rest of this run of the app instead of waiting each time.
_word_stalled = False


def _word_time_limit(pages):
    """Seconds Word gets to import a PDF of ``pages`` pages when it was
    chosen for the user ("Best available")."""
    return min(TIMEOUT, 60 + 2 * pages)


def pdf_to_word(path, output, engine="auto", password=None, notes=None):
    """Convert a PDF to an editable Word document. Returns (output, engine
    used): "word", "basic" or "layout". With "auto", Word is used when it's
    installed; if it fails or doesn't finish, ZPDF's own converter is used
    and ``notes`` (a list) is told why."""
    global _word_stalled
    if engine not in TO_WORD_ENGINES:
        raise core.PdfManagerError(f"engine must be one of {', '.join(TO_WORD_ENGINES)}")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pdfm-word-") as tmp:
        src = Path(path)
        reader = core.open_pdf(path, password)
        if reader.is_encrypted:
            src = Path(tmp) / "input.pdf"
            core.decrypt(path, src, password or "")
        if engine == "word":
            if not has_word():
                raise core.PdfManagerError(
                    "Microsoft Word isn't installed on this computer: choose the Built-in converter "
                    "(or Best available) instead, or install Word and try again")
            return _powershell(PS_PDF_TO_WORD, src, output, "Microsoft Word"), "word"
        if engine == "auto" and has_word() and not _word_stalled:
            try:
                pages = len(reader.pages)
            except Exception:  # an unusual file: Word gets the shortest time
                pages = 0
            try:
                return _powershell(PS_PDF_TO_WORD, src, output, "Microsoft Word",
                                   timeout=_word_time_limit(pages)), "word"
            except core.PdfManagerError as exc:
                _word_stalled = _word_stalled or isinstance(exc, TookTooLong)
                output.unlink(missing_ok=True)
                if notes is not None:
                    notes.append(f"{exc}, so the built-in converter was used instead."[:400])
        elif engine == "auto" and has_word() and notes is not None:
            notes.append("Microsoft Word didn't finish a conversion earlier, so the built-in "
                         "converter was used. Choose \"Microsoft Word only\" to try Word again.")
        if engine == "layout":
            if not find_libreoffice():
                raise core.PdfManagerError(
                    "Exact layout needs LibreOffice, which isn't installed on this computer: install it "
                    "(free, https://www.libreoffice.org) and try again, or choose the Built-in converter")
            return _libreoffice(src, output, "docx:MS Word 2007 XML", "writer_pdf_import"), "layout"
        return basic_docx(src, output), "basic"


def basic_docx(path, output, password=None):
    """The built-in PDF → Word conversion (see the module docstring)."""
    from pdfmanager import webpage

    title, pages = webpage.flow(path, password)
    return _write_docx(pages, output, title=title)


# -- a minimal .docx writer -------------------------------------------------------------

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="jpeg" ContentType="image/jpeg"/>
<Default Extension="png" ContentType="image/png"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""


def _styles():
    heading = ""
    for level, size in ((1, 36), (2, 30), (3, 26)):
        heading += (
            f'<w:style w:type="paragraph" w:styleId="Heading{level}"><w:name w:val="heading {level}"/>'
            '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:uiPriority w:val="9"/><w:qFormat/>'
            f'<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="{level - 1}"/></w:pPr>'
            f'<w:rPr><w:b/><w:sz w:val="{size}"/></w:rPr></w:style>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" '
            'w:cs="Calibri"/><w:sz w:val="22"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>'
            '<w:spacing w:after="160" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
            + heading +
            '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/>'
            '<w:basedOn w:val="Normal"/><w:uiPriority w:val="34"/><w:qFormat/>'
            '<w:pPr><w:spacing w:after="60"/><w:ind w:left="454" w:hanging="340"/></w:pPr></w:style>'
            '<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/>'
            '<w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>'
            '</w:styles>')


def _image_xml(rid, n, cx, cy):
    return (
        f'<w:p><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{n}" name="Picture {n}"/>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:nvPicPr><pic:cNvPr id="{n}" name="image{n}"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')


_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")  # not allowed in XML


def _text_xml(text):
    return f'<w:t xml:space="preserve">{escape(_XML_BAD.sub("", text))}</w:t>'


def _runs_xml(segments, links, heading, link_id):
    """The runs of a paragraph: bold, italic and typewriter type as on the
    page, links to the web as links."""
    out, open_link = [], None
    for text, char in segments:
        if not text:
            continue
        look = ""
        if char.bold and not heading:
            look += "<w:b/>"
        if char.italic:
            look += "<w:i/>"
        if getattr(char, "raised", 0):
            look += f'<w:vertAlign w:val="{"superscript" if char.raised > 0 else "subscript"}"/>'
        if char.family == "Courier New":
            look += '<w:rFonts w:ascii="Courier New" w:hAnsi="Courier New" w:cs="Courier New"/>'
        link = links[char.link] if char.link is not None and char.link < len(links) else None
        uri = link.get("uri") if link else None
        if uri != open_link:
            if open_link:
                out.append("</w:hyperlink>")
            if uri:
                out.append(f'<w:hyperlink r:id="{link_id(uri)}">')
            open_link = uri
        if uri:
            look = '<w:rStyle w:val="Hyperlink"/>' + look
        out.append(f"<w:r>{f'<w:rPr>{look}</w:rPr>' if look else ''}{_text_xml(text)}</w:r>")
    if open_link:
        out.append("</w:hyperlink>")
    return "".join(out)


_FIGURE = re.compile(r"^[-+−–(]?[£$€¥]?\s?\d[\d\s.,:/%-]*\)?\s?[%£$€¥]?$")


def _table_xml(rows, width_twips, bold=None):
    """A table. ``bold``: [[whether the cell is in bold type on the page]].
    The first row is a heading row (in bold, repeated on each page) when it
    is in bold on the page, or is plainly one: words over rows of figures."""
    columns = max(len(row) for row in rows)
    each = width_twips // max(1, columns)
    edge = "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
                   for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    out = [f'<w:tbl><w:tblPr><w:tblW w:w="{each * columns}" w:type="dxa"/><w:tblBorders>{edge}</w:tblBorders>'
           '<w:tblCellMar><w:left w:w="100" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar>'
           '</w:tblPr><w:tblGrid>' + f'<w:gridCol w:w="{each}"/>' * columns + '</w:tblGrid>']

    def figures(row):
        return sum(1 for cell in row if _FIGURE.match(str(cell or "").strip()))

    def in_bold(r, k):
        return bool(bold) and r < len(bold) and k < len(bold[r]) and bold[r][k]

    filled = [k for k, cell in enumerate(rows[0]) if str(cell or "").strip()]
    heading = bool(filled) and (all(in_bold(0, k) for k in filled) or (
        len(rows) > 1 and not figures(rows[0]) and any(figures(row) for row in rows[1:])))
    for number, row in enumerate(rows):
        cells = list(row) + [""] * (columns - len(row))
        head = heading and number == 0
        out.append("<w:tr>" + ("<w:trPr><w:tblHeader/></w:trPr>" if head else "") + "".join(
            f'<w:tc><w:tcPr><w:tcW w:w="{each}" w:type="dxa"/></w:tcPr><w:p><w:pPr><w:spacing w:after="0"/>'
            f'</w:pPr><w:r>{"<w:rPr><w:b/></w:rPr>" if head or in_bold(number, k) else ""}'
            f'{_text_xml(str(cell or ""))}</w:r></w:p></w:tc>' for k, cell in enumerate(cells)) + "</w:tr>")
    out.append("</w:tbl><w:p/>")  # Word wants a paragraph after a table
    return "".join(out)


def _write_docx(pages, output, title=""):
    """A .docx from the items of each page (webpage.flow)."""
    emu_per_pt, max_width_pt = 12700, 451  # A4 with 2.5 cm margins
    body, rels, media = [], [], []
    picture = 0
    link_ids = {}

    def link_id(uri):
        if uri not in link_ids:
            link_ids[uri] = f"rIdLink{len(link_ids) + 1}"
            target = escape(_XML_BAD.sub("", uri), {chr(34): "&quot;"})
            rels.append(f'<Relationship Id="{link_ids[uri]}" Type="http://schemas.openxmlformats.org/'
                        f'officeDocument/2006/relationships/hyperlink" Target="{target}" '
                        'TargetMode="External"/>')
        return link_ids[uri]

    for number, items in enumerate(pages):
        if number:
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        for item in items:
            kind = item["kind"]
            if kind in ("heading", "paragraph", "item"):
                style = (f"Heading{item['level']}" if kind == "heading" else
                         "ListParagraph" if kind == "item" else "")
                runs = _runs_xml(item["segments"], item.get("links") or [], kind == "heading", link_id)
                if runs:
                    body.append("<w:p>" + (f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else "")
                                + runs + "</w:p>")
            elif kind == "table":
                if item["rows"]:
                    body.append(_table_xml(item["rows"], int(max_width_pt * 20), item.get("bold")))
            else:
                image = item["image"].convert("RGB")
                picture += 1
                buf = io.BytesIO()
                image.save(buf, "JPEG", quality=88)
                name = f"image{picture}.jpeg"
                media.append((name, buf.getvalue()))
                rid = f"rIdImg{picture}"
                rels.append(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/'
                            f'officeDocument/2006/relationships/image" Target="media/{name}"/>')
                width = min(item["width"], max_width_pt)
                height = width * image.height / image.width
                body.append(_image_xml(rid, picture, int(width * emu_per_pt), int(height * emu_per_pt)))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        '<w:body>' + "".join(body) +
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1418" w:right="1418" '
        'w:bottom="1418" w:left="1418" w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>'
        '</w:body></w:document>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/'
                '2006/relationships/styles" Target="styles.xml"/>' + "".join(rels) + '</Relationships>')
    core_props = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:title>{escape(_XML_BAD.sub("", title))}</dc:title><dc:creator>ZPDF</dc:creator></cp:coreProperties>')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("docProps/core.xml", core_props)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", _styles())
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        for name, data in media:
            z.writestr(f"word/media/{name}", data)
    return output


# --------------------------------------------------------------------------
# Office → PDF
# --------------------------------------------------------------------------

def _app_for(suffix):
    if suffix in WORD_TYPES:
        return "word", "Word.Application"
    if suffix in EXCEL_TYPES:
        return "excel", "Excel.Application"
    return "powerpoint", "PowerPoint.Application"


def _wrap(line, font, size, room):
    """Pieces of ``line`` that each fit in ``room`` points."""
    from reportlab.pdfbase import pdfmetrics

    if font.startswith("Courier"):  # every character is 0.6 em wide
        chars = int(room / (size * 0.6))
        return [line[i:i + chars] for i in range(0, max(len(line), 1), chars)]
    pieces, start, width = [], 0, 0.0
    for i, char in enumerate(line):
        w = pdfmetrics.stringWidth(char, font, size)
        if width + w > room and i > start:
            pieces.append(line[start:i])
            start, width = i, 0.0
        width += w
    pieces.append(line[start:])
    return pieces


def text_to_pdf(path, output):
    """Plain text to PDF (A4, 10 pt monospace), no other program needed."""
    from reportlab.lib.pagesizes import A4

    raw = Path(path).read_bytes()
    encodings = ("utf-16",) if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ()
    for encoding in encodings + ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    c = core.pdf_canvas(str(output), pagesize=A4)
    c.setTitle(Path(path).stem)
    width, height = A4
    margin, size = 56, 10
    y = height - margin
    for line in text.expandtabs(4).splitlines() or [""]:
        # Courier only has Western letters; other alphabets need an installed font.
        font = fonts.font_for(line, "mono")
        for piece in _wrap(line, font, size, width - 2 * margin):
            if y < margin:
                c.showPage()
                y = height - margin
            c.setFont(font, size)
            c.drawString(margin, y, piece)
            y -= size * 1.3
    c.save()
    return output


def office_to_pdf(path, output):
    """Convert a Word, Excel, PowerPoint, OpenDocument, RTF, text or web
    page (.html) file to PDF. Returns (output, program used)."""
    path, output = Path(path), Path(output)
    suffix = path.suffix.lower()
    if suffix in HTML_TYPES:
        return html_to_pdf(path, output)
    if suffix not in OFFICE_TYPES:
        raise core.PdfManagerError(
            f"{path.name}: can't convert {suffix or 'this'} files (try Word, Excel, PowerPoint, "
            "OpenDocument, RTF or text files)")
    output.parent.mkdir(parents=True, exist_ok=True)
    app, prog_id = _app_for(suffix)
    if _has_com(prog_id) and suffix not in (".odt", ".ods", ".odp", ".wpd"):
        name = {"word": "Microsoft Word", "excel": "Microsoft Excel",
                "powerpoint": "Microsoft PowerPoint"}[app]
        return _powershell(PS_TO_PDF[app], path, output, name), name
    if find_libreoffice():
        return _libreoffice(path, output, "pdf"), "LibreOffice"
    if suffix == ".txt":
        return text_to_pdf(path, output), "ZPDF"
    raise core.PdfManagerError(
        f"converting {suffix} files needs Microsoft Office or LibreOffice, and neither is installed "
        "on this computer: install LibreOffice (free, https://www.libreoffice.org) and try again")


# --------------------------------------------------------------------------
# Web pages -> PDF
# --------------------------------------------------------------------------

def find_browser():
    """A Chromium-based browser to print web pages with: Edge (on every
    Windows), Chrome or Chromium; or None."""
    for name in ("msedge", "microsoft-edge", "google-chrome", "chrome", "chromium",
                 "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    candidates = []
    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"),
                     os.environ.get("LOCALAPPDATA")):
            if base:
                candidates += [Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                               Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"]
    elif sys.platform == "darwin":
        candidates += [Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                       Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                       Path("/Applications/Chromium.app/Contents/MacOS/Chromium")]
    else:  # Playwright's Chromium, where it's installed (development, CI)
        pw = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "/opt/pw-browsers")
        candidates += sorted(pw.glob("chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"))
        candidates += sorted(pw.glob("chromium-*/chrome-linux*/chrome"))
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def _web_address(source):
    """A web address as typed ("example.com/page") -> a full one, or None
    when ``source`` is a file."""
    text = str(source).strip()
    if re.match(r"(?i)^https?://", text):
        return text
    if Path(text).suffix.lower() in HTML_TYPES or Path(text).exists():
        return None  # a file name
    if re.match(r"(?i)^(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)+(:\d+)?(/\S*)?$", text):
        return "https://" + text
    return None


def _profile_in_use(profile):
    """Whether a browser is still running on ``profile`` (its user data
    folder): Chromium keeps a lock file there while it runs."""
    if profile is None:
        return False
    for name in ("lockfile", "SingletonLock"):  # Windows; Linux and macOS
        try:
            os.lstat(profile / name)
            if name == "SingletonLock" and not _lock_holder_alive(profile / name):
                continue  # left behind by a browser that crashed
            return True
        except FileNotFoundError:
            continue
        except OSError:  # there, and held too tightly to look at
            return True
    return False


def _lock_holder_alive(lock):
    """Whether the browser named by a Linux/macOS SingletonLock (a link to
    "host-pid") still runs; True when that can't be told."""
    try:
        pid = int(os.readlink(lock).rsplit("-", 1)[1])
    except (OSError, ValueError, IndexError):
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # there, but not ours to signal
        return True
    return True


def _whole_pdf(target):
    """Whether the file at ``target`` ends as a PDF does."""
    try:
        size = target.stat().st_size
        if size <= 100:
            return False
        with open(target, "rb") as f:
            f.seek(max(0, size - 1024))
            return b"%%EOF" in f.read()
    except OSError:  # not there, or still being written
        return False


# After the program that was started ends well, how long the PDF may still
# take to appear: Edge can hand the work to a browser of its own and leave.
HANDED_OVER = 10


def _print_with(command, target, timeout, profile=None):
    """Run the browser until ``target`` is a whole PDF. A browser can stay
    open after printing (waiting on the network, its updater), so it's
    closed once the file is complete rather than waited for. And the
    program that was started can end before the PDF is written, having
    handed the printing to a browser running apart from it: the file is
    waited for as long as a browser is using ``profile``."""
    import time

    # What the browser says goes to a file, not a pipe: a pipe nobody reads
    # until the end fills up (64 KB) with its chatter (D-Bus, GPU warnings)
    # and the browser then stops, waiting to write, until it's timed out.
    said = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=said,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        said.close()
        raise core.PdfManagerError(f"the browser could not start: {_start_failure(command, exc)}. "
                                   "Reinstall Microsoft Edge or Google Chrome and try again") from None
    deadline, last, ended = time.monotonic() + timeout, -1, None
    try:
        while time.monotonic() < deadline:
            if target.exists():
                size = target.stat().st_size
                if size == last and _whole_pdf(target):
                    return
                last = size
            if proc.poll() is not None:
                ended = ended or time.monotonic()
                if proc.returncode or not (time.monotonic() - ended < HANDED_OVER or _profile_in_use(profile)):
                    break
            time.sleep(0.25)
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        try:
            said.seek(0, os.SEEK_END)
            said.seek(max(0, said.tell() - 4096))
            err = said.read()
        except OSError:
            err = b""
        said.close()
    if _whole_pdf(target):
        return
    detail = " ".join(err.decode("utf-8", "replace").split())[-300:]
    raise core.PdfManagerError("the page couldn't be printed"
                               + (" (it took too long)" if time.monotonic() >= deadline else "")
                               + (f": {detail}" if detail else ""))


def _stop_browsers_using(folder):
    """Stop any browser still running with ``folder`` (its name, which is
    random, so no other program has it) on its command line (Windows): one
    that was handed the printing isn't reached by stopping the program that
    was started."""
    if sys.platform != "win32":
        return
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
              "$_.CommandLine.Contains($env:PDFM_DIR) -and $_.ProcessId -ne $PID } | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    try:
        subprocess.run([powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", script],
                       env={**os.environ, "PDFM_DIR": Path(folder).name}, capture_output=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass


def _remove_browser_folder(folder):
    """Delete the browser's scratch folder. A browser still closing keeps
    files in it open for a moment (on Windows they can't be deleted then):
    it's stopped and the folder tried again, and a folder that still can't
    go is left to the system's own clean-up rather than failing the print."""
    import time

    for attempt in range(6):
        try:
            shutil.rmtree(folder)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 0:
                _stop_browsers_using(folder)
            time.sleep(0.5)
    shutil.rmtree(folder, ignore_errors=True)


# A saved page is printed with no scripts, frames or objects, and only its
# own kind of files (pictures, styles, fonts): a page someone sent can't put
# other files of this computer into the PDF. (Chromium already keeps .mht
# archives to themselves, with scripts off.)
_LOCAL_PAGE_POLICY = ("default-src 'none'; img-src data: file:; style-src 'unsafe-inline' file:; "
                      "font-src data: file:")


def _locked_copy(page, copy):
    """A copy of ``page`` at ``copy`` that starts with the policy above and a
    <base> pointing at the page's folder, so its own pictures still show."""
    data = page.read_bytes()
    head = (f'<meta http-equiv="Content-Security-Policy" content="{_LOCAL_PAGE_POLICY}">'
            f'<base href="{page.resolve().parent.as_uri()}/">')
    for bom, codec in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if data.startswith(bom):
            copy.write_bytes(bom + head.encode(codec) + data[len(bom):])
            return copy
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    copy.write_bytes(bom + head.encode("ascii") + data[len(bom):])
    return copy


def html_to_pdf(source, output, timeout=120):
    """Print a web page (an address, or a saved .html/.mht file) to PDF the
    way the browser prints it: with a Chromium browser (Edge, Chrome), else
    LibreOffice (simpler layout). Returns (output, program used)."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    address = _web_address(source)
    if address is None:
        path = Path(source)
        if not path.is_file():
            raise core.PdfManagerError(f"{source} is neither a web address nor a file")
        if path.suffix.lower() not in HTML_TYPES:
            raise core.PdfManagerError(f"{path.name} isn't a web page (.html, .htm, .mht)")
        address = path.resolve().as_uri()
        local = path
    elif re.match(r"(?i)^https?://(localhost|127\.|0\.0\.0\.0|\[?::1)", address):
        raise core.PdfManagerError("addresses on this computer (localhost) can't be printed")
    else:
        local = None
    browser = find_browser()
    if browser:
        tmp = Path(tempfile.mkdtemp(prefix="pdfm-web-"))
        try:
            target, profile = tmp / "page.pdf", tmp / "profile"
            if local is not None and local.suffix.lower() in (".html", ".htm", ".xhtml"):
                address = _locked_copy(local, tmp / f"page{local.suffix.lower()}").as_uri()
            shell = "headless-shell" in Path(browser).name
            command = [browser, "--headless" if shell else "--headless=new", "--disable-gpu",
                       "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                       "--disable-sync", "--disable-background-networking",
                       "--disable-component-update", "--no-pdf-header-footer",
                       "--run-all-compositor-stages-before-draw", "--virtual-time-budget=10000",
                       f"--user-data-dir={profile}", f"--print-to-pdf={target}"]
            if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
                command.append("--no-sandbox")  # Chromium refuses to run as root otherwise
            command.append(address)
            _print_with(command, target, timeout, profile)
            shutil.move(str(target), str(output))
        finally:
            _remove_browser_folder(tmp)
        stem = Path(browser).stem.lower()
        return output, ("Microsoft Edge" if "edge" in stem else
                        "Google Chrome" if "google" in str(browser).lower() else "Chromium")
    if address.startswith("file:") and find_libreoffice():
        _libreoffice(Path(source), output, "pdf:writer_web_pdf_Export")
        return output, "LibreOffice"
    if address.startswith("file:"):
        raise core.PdfManagerError(
            "printing web pages needs Microsoft Edge, Google Chrome or Chromium, and none was found on "
            "this computer: install one of them (or LibreOffice, for a simpler layout) and try again")
    raise core.PdfManagerError(
        "printing a web address needs Microsoft Edge, Google Chrome or Chromium, and none was found "
        "on this computer: install one of them and try again")
