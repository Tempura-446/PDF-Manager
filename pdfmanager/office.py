"""Word and Office conversion.

PDF → Word (.docx), by the first available of:
  * Microsoft Word (Windows): opens the PDF and reflows it into an editable
    document, the best result;
  * the built-in converter: paragraphs, headings and pictures in reading
    order (tables and columns become plain paragraphs);
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

_PS_PREFIX = "$ErrorActionPreference = 'Stop'\n"

PS_PDF_TO_WORD = _PS_PREFIX + r"""
$word = New-Object -ComObject Word.Application
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
$app = New-Object -ComObject Word.Application
$app.Visible = $false
$app.DisplayAlerts = 0
try {
  $doc = $app.Documents.Open($env:PDFM_IN, $false, $true, $false)
  $doc.ExportAsFixedFormat($env:PDFM_OUT, 17)
  $doc.Close($false)
} finally { $app.Quit() }
""",
    "excel": _PS_PREFIX + r"""
$app = New-Object -ComObject Excel.Application
$app.Visible = $false
$app.DisplayAlerts = $false
try {
  $book = $app.Workbooks.Open($env:PDFM_IN, 0, $true)
  $book.ExportAsFixedFormat(0, $env:PDFM_OUT)
  $book.Close($false)
} finally { $app.Quit() }
""",
    "powerpoint": _PS_PREFIX + r"""
$app = New-Object -ComObject PowerPoint.Application
try {
  $show = $app.Presentations.Open($env:PDFM_IN, -1, 0, 0)
  $show.SaveAs($env:PDFM_OUT, 32)
  $show.Close()
} finally { $app.Quit() }
""",
}


def _run(command, env=None, what="the conversion"):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT,
                                env={**os.environ, **(env or {})},
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise core.PdfManagerError(f"{what} took too long and was stopped") from None
    except OSError as exc:
        raise core.PdfManagerError(f"{what} could not start: {exc}") from None
    return result


def _powershell(script, src, dst, what):
    result = _run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-Command", script],
                  env={"PDFM_IN": str(Path(src).resolve()), "PDFM_OUT": str(Path(dst).resolve())},
                  what=what)
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

def pdf_to_word(path, output, engine="auto", password=None):
    """Convert a PDF to an editable Word document. Returns (output, engine
    used): "word", "basic" or "layout"."""
    if engine not in TO_WORD_ENGINES:
        raise core.PdfManagerError(f"engine must be one of {', '.join(TO_WORD_ENGINES)}")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pdfm-word-") as tmp:
        src = Path(path)
        if core.open_pdf(path, password).is_encrypted:
            src = Path(tmp) / "input.pdf"
            core.decrypt(path, src, password or "")
        if engine == "word" or (engine == "auto" and has_word()):
            if not has_word():
                raise core.PdfManagerError("Microsoft Word isn't installed on this computer")
            return _powershell(PS_PDF_TO_WORD, src, output, "Microsoft Word"), "word"
        if engine == "layout":
            return _libreoffice(src, output, "docx:MS Word 2007 XML", "writer_pdf_import"), "layout"
        return basic_docx(src, output), "basic"


def _page_content(page, textpage):
    """Lines of text and pictures on a page, top to bottom:
    ("text", top, font size, bold, text) and ("image", top, PIL image, width_pt)."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    height = page.get_height()
    runs = []
    for i in range(textpage.count_rects()):
        left, bottom, right, top = textpage.get_rect(i)
        text = textpage.get_text_bounded(left, bottom, right, top)
        if not text.strip():
            continue
        index = textpage.get_index((left + right) / 2, (bottom + top) / 2, 2, 2)
        size, bold = top - bottom, False
        if index is not None and index >= 0:
            size = raw.FPDFText_GetFontSize(textpage, index) or size
            bold = raw.FPDFText_GetFontWeight(textpage, index) >= 600
        runs.append([height - top, round(size, 1), bold, left, text.strip(), top - bottom])
    runs.sort(key=lambda r: (round(r[0] / 3), r[3]))
    lines = []
    for top, size, bold, left, text, h in runs:
        last = lines[-1] if lines else None
        if last and abs(last[1] - top) < max(h, last[5]) * 0.5:
            last[4] += " " + text
            last[2] = max(last[2], size)
            last[3] = last[3] and bold
        else:
            lines.append(["text", top, size, bold, text, h])
    content = [tuple(line[:5]) for line in lines]
    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=2):
        try:
            left, bottom, right, top = obj.get_bounds()
            if right - left < 24 or top - bottom < 24:
                continue  # rules, bullets, tiny decorations
            image = obj.get_bitmap(render=True).to_pil().convert("RGB")
        except (pdfium.PdfiumError, OSError, ValueError):
            continue
        content.append(("image", height - top, image, right - left))
    content.sort(key=lambda item: item[1])
    return content


def _paragraphs(content, body):
    """Join lines into paragraphs. Yields ("p", text, level) — level 1-3 for
    headings, 0 for body text — and ("image", PIL image, width_pt)."""
    para, para_size, para_bottom = [], 0, None
    for item in content:
        if item[0] == "image":
            if para:
                yield ("p", " ".join(para), 0)
                para = []
            yield ("image", item[2], item[3])
            continue
        _, top, size, bold, text = item
        level = (1 if size > body * 1.5 else 2 if size > body * 1.2 else
                 3 if size > body * 1.08 or (bold and len(text) <= 80 and text[-1:] not in ".,;:")
                 else 0)
        if level:
            if para:
                yield ("p", " ".join(para), 0)
                para = []
            yield ("p", text, level)
            para_bottom = top + size
            continue
        gap = top - para_bottom if para_bottom is not None else 0
        if para and (gap > para_size * 0.9 or abs(size - para_size) > 0.6):
            yield ("p", " ".join(para), 0)
            para = []
        if para and para[-1].endswith("-") and text[:1].islower():
            para[-1] = para[-1][:-1] + text  # re-join a hyphenated word
        else:
            para.append(text)
        para_size, para_bottom = size, top + size
    if para:
        yield ("p", " ".join(para), 0)


def basic_docx(path, output, password=None):
    """The built-in PDF → Word conversion (see the module docstring)."""
    import pypdfium2 as pdfium

    blocks = []  # per page
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path), password=password)
        try:
            pages = []
            heights = {}
            for number, page in enumerate(doc):
                progress.report(number, len(doc), "page")
                textpage = page.get_textpage()
                content = _page_content(page, textpage)
                textpage.close()
                if not any(c[0] == "text" for c in content) and not any(c[0] == "image" for c in content):
                    # e.g. vector drawings only: keep the page as a picture
                    content = [("image", 0, page.render(scale=2).to_pil().convert("RGB"),
                                page.get_width())]
                pages.append((content, page.get_height()))
                for c in content:
                    if c[0] == "text":
                        heights[c[2]] = heights.get(c[2], 0) + len(c[4])
        finally:
            doc.close()
    body = max(heights, key=heights.get) if heights else 12
    # Running headers and footers (the same text near the top or bottom of
    # most pages, page numbers included) don't belong in the flowing text.
    def edge_key(item, height):
        if item[0] == "text" and (item[1] < height * 0.08 or item[1] > height * 0.9):
            return re.sub(r"\d+", "#", item[4].lower())
        return None

    counts = {}
    for content, height in pages:
        for key in {edge_key(c, h) for c, h in ((c, height) for c in content)} - {None}:
            counts[key] = counts.get(key, 0) + 1
    repeated = {k for k, n in counts.items() if len(pages) >= 3 and n >= len(pages) * 0.6}
    for content, height in pages:
        content = [c for c in content if edge_key(c, height) not in repeated]
        blocks.append(list(_paragraphs(content, body)))
    return _write_docx(blocks, output, title=Path(path).stem)


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
            + heading + '</w:styles>')


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


_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _write_docx(pages, output, title=""):
    emu_per_pt, max_width_pt = 12700, 451  # A4 with 2.5 cm margins
    body, rels, media = [], [], []
    picture = 0
    for number, blocks in enumerate(pages):
        if number:
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        for block in blocks:
            if block[0] == "p":
                _, text, level = block
                style = f'<w:pPr><w:pStyle w:val="Heading{level}"/></w:pPr>' if level else ""
                text = escape(_XML_BAD.sub("", text))
                body.append(f'<w:p>{style}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>')
            else:
                _, image, width_pt = block
                picture += 1
                buf = io.BytesIO()
                image.save(buf, "JPEG", quality=88)
                name = f"image{picture}.jpeg"
                media.append((name, buf.getvalue()))
                rid = f"rIdImg{picture}"
                rels.append(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/'
                            f'officeDocument/2006/relationships/image" Target="media/{name}"/>')
                width = min(width_pt, max_width_pt)
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
        f'<dc:title>{escape(title)}</dc:title><dc:creator>ZPDF</dc:creator></cp:coreProperties>')
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
    """Convert a Word, Excel, PowerPoint, OpenDocument, RTF or text file to
    PDF. Returns (output, program used)."""
    path, output = Path(path), Path(output)
    suffix = path.suffix.lower()
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
        f"converting {suffix} files needs Microsoft Office or LibreOffice "
        "(free: https://www.libreoffice.org)")
