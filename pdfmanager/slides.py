"""PDF → PowerPoint (.pptx), without PowerPoint or any other program.

One slide per page. Two ways:

  * "editable": the slide's background is the page drawn without its text
    (pictures, lines, shading and all), and the text is put back on top as
    real PowerPoint text boxes in the same places: a box per block of
    lines (a paragraph, a heading, a table cell), with the font (mapped to
    a common one: Helvetica → Arial, Times → Times New Roman, Courier →
    Courier New), size, bold, italic, colour, alignment and line spacing,
    and the links. Lines of a paragraph are joined, so the text rewraps
    when it is edited. Invisible text (an OCRed scan's searchable layer)
    stays invisible but can be selected, over the scan.
  * "pictures": each slide is a picture of the page.

A presentation has one slide size: the size of most of the pages. Pages of
another size are scaled to fit their slide, centred. Slides are between 1
and 56 inches wide and high (PowerPoint's limits); larger pages are scaled
down.

The .pptx is written by hand (Office Open XML), so no extra package is
needed; it opens in PowerPoint, Keynote, LibreOffice and Google Slides.
"""

import os
import re
import zipfile
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape, quoteattr

from pdfmanager import core, layout, progress

MODES = ("editable", "pictures")
EMU = 12700                     # per point
MIN_SLIDE, MAX_SLIDE = 72, 56 * 72
BACKGROUND_DPI = 150
PICTURE_DPI = 200
SLACK = 1.03                    # text boxes in other fonts are this much wider than the text

_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")
NS = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def _x(text):
    return escape(_BAD.sub("", text))


def _emu(points):
    return int(round(points * EMU))


# --------------------------------------------------------------------------
# The fixed parts
# --------------------------------------------------------------------------

def _content_types(slides, extensions):
    parts = "".join(
        f'<Override PartName="/ppt/slides/slide{n}.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' for n in range(1, slides + 1))
    defaults = "".join(f'<Default Extension="{ext}" ContentType="image/{ext}"/>'
                       for ext in sorted(extensions))
    return (HEAD + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>' + defaults +
            '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
            '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
            '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
            '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
            '<Override PartName="/ppt/presProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"/>'
            '<Override PartName="/ppt/viewProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml"/>'
            '<Override PartName="/ppt/tableStyles.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
            + parts + '</Types>')


ROOT_RELS = (
    HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="ppt/presentation.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/'
    'metadata/core-properties" Target="docProps/core.xml"/>'
    f'<Relationship Id="rId3" Type="{REL}/extended-properties" Target="docProps/app.xml"/>'
    '</Relationships>')

_EMPTY_TREE = ('<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/>'
               '</p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
               '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>')

MASTER = (
    HEAD + f'<p:sldMaster {NS}><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/>'
    '</p:bgRef></p:bg>' + _EMPTY_TREE + '</p:spTree></p:cSld>'
    '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
    'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" '
    'folHlink="folHlink"/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/>'
    '</p:sldLayoutIdLst><p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz="4400"/></a:lvl1pPr>'
    '</p:titleStyle><p:bodyStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:bodyStyle>'
    '<p:otherStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:otherStyle></p:txStyles>'
    '</p:sldMaster>')

MASTER_RELS = (
    HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    f'<Relationship Id="rId1" Type="{REL}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
    f'<Relationship Id="rId2" Type="{REL}/theme" Target="../theme/theme1.xml"/></Relationships>')

LAYOUT = (HEAD + f'<p:sldLayout {NS} type="blank" preserve="1"><p:cSld name="Blank">'
          + _EMPTY_TREE + '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
          '</p:sldLayout>')

LAYOUT_RELS = (
    HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    f'<Relationship Id="rId1" Type="{REL}/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
    '</Relationships>')


def _theme():
    colors = [("dk1", '<a:sysClr val="windowText" lastClr="000000"/>'),
              ("lt1", '<a:sysClr val="window" lastClr="FFFFFF"/>'),
              ("dk2", '<a:srgbClr val="44546A"/>'), ("lt2", '<a:srgbClr val="E7E6E6"/>'),
              ("accent1", '<a:srgbClr val="4472C4"/>'), ("accent2", '<a:srgbClr val="ED7D31"/>'),
              ("accent3", '<a:srgbClr val="A5A5A5"/>'), ("accent4", '<a:srgbClr val="FFC000"/>'),
              ("accent5", '<a:srgbClr val="5B9BD5"/>'), ("accent6", '<a:srgbClr val="70AD47"/>'),
              ("hlink", '<a:srgbClr val="0563C1"/>'), ("folHlink", '<a:srgbClr val="954F72"/>')]
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    line = '<a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
    font = '<a:latin typeface="{0}"/><a:ea typeface=""/><a:cs typeface=""/>'
    return (HEAD + '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'name="Office Theme"><a:themeElements><a:clrScheme name="Office">'
            + "".join(f"<a:{k}>{v}</a:{k}>" for k, v in colors) + '</a:clrScheme>'
            '<a:fontScheme name="Office"><a:majorFont>' + font.format("Calibri Light") +
            '</a:majorFont><a:minorFont>' + font.format("Calibri") + '</a:minorFont></a:fontScheme>'
            '<a:fmtScheme name="Office"><a:fillStyleLst>' + fill * 3 + '</a:fillStyleLst>'
            '<a:lnStyleLst>' + line * 3 + '</a:lnStyleLst><a:effectStyleLst>'
            + '<a:effectStyle><a:effectLst/></a:effectStyle>' * 3 + '</a:effectStyleLst>'
            '<a:bgFillStyleLst>' + fill * 3 + '</a:bgFillStyleLst></a:fmtScheme></a:themeElements>'
            '<a:objectDefaults/><a:extraClrSchemeLst/></a:theme>')


PRES_PROPS = HEAD + f'<p:presentationPr {NS}/>'
VIEW_PROPS = (HEAD + f'<p:viewPr {NS}><p:normalViewPr/><p:slideViewPr><p:cSldViewPr>'
              '<p:cViewPr varScale="1"><p:scale><a:sx n="100" d="100"/><a:sy n="100" d="100"/>'
              '</p:scale><p:origin x="0" y="0"/></p:cViewPr></p:cSldViewPr></p:slideViewPr>'
              '<p:gridSpacing cx="76200" cy="76200"/></p:viewPr>')
TABLE_STYLES = (HEAD + '<a:tblStyleLst xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/'
                'main" def="{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"/>')


def _presentation(count, width, height):
    ids = "".join(f'<p:sldId id="{255 + n}" r:id="rId{n + 10}"/>' for n in range(1, count + 1))
    return (HEAD + f'<p:presentation {NS} saveSubsetFonts="1"><p:sldMasterIdLst>'
            '<p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            f'<p:sldIdLst>{ids}</p:sldIdLst><p:sldSz cx="{_emu(width)}" cy="{_emu(height)}"/>'
            '<p:notesSz cx="6858000" cy="9144000"/><p:defaultTextStyle><a:lvl1pPr>'
            '<a:defRPr sz="1800"/></a:lvl1pPr></p:defaultTextStyle></p:presentation>')


def _presentation_rels(count):
    slides = "".join(f'<Relationship Id="rId{n + 10}" Type="{REL}/slide" '
                     f'Target="slides/slide{n}.xml"/>' for n in range(1, count + 1))
    return (HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{REL}/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
            f'<Relationship Id="rId2" Type="{REL}/theme" Target="theme/theme1.xml"/>'
            f'<Relationship Id="rId3" Type="{REL}/presProps" Target="presProps.xml"/>'
            f'<Relationship Id="rId4" Type="{REL}/viewProps" Target="viewProps.xml"/>'
            f'<Relationship Id="rId5" Type="{REL}/tableStyles" Target="tableStyles.xml"/>'
            + slides + '</Relationships>')


def _core(title):
    return (HEAD + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
            'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:title>{_x(title)}</dc:title><dc:creator>ZPDF</dc:creator></cp:coreProperties>')


def _app(count):
    return (HEAD + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
            f'extended-properties"><Application>ZPDF</Application><Slides>{count}</Slides>'
            '</Properties>')


# --------------------------------------------------------------------------
# Slides
# --------------------------------------------------------------------------

class _Slide:
    """One slide's shapes and relationships."""

    def __init__(self, number):
        self.number, self.shapes, self.rels, self.next_id, self.background = number, [], [], 2, None
        self.rels.append(f'<Relationship Id="rId1" Type="{REL}/slideLayout" '
                         'Target="../slideLayouts/slideLayout1.xml"/>')

    def rel(self, kind, target, external=False):
        rid = f"rId{len(self.rels) + 1}"
        mode = ' TargetMode="External"' if external else ""
        self.rels.append(f'<Relationship Id="{rid}" Type="{REL}/{kind}" '
                         f'Target={quoteattr(target)}{mode}/>')
        return rid

    def shape_id(self):
        self.next_id += 1
        return self.next_id - 1

    def xml(self):
        bg = ""
        if self.background:
            bg = ('<p:bg><p:bgPr><a:blipFill dpi="0" rotWithShape="1">'
                  f'<a:blip r:embed="{self.background}"/><a:srcRect/><a:stretch><a:fillRect/>'
                  '</a:stretch></a:blipFill><a:effectLst/></p:bgPr></p:bg>')
        return (HEAD + f'<p:sld {NS}><p:cSld>{bg}' + _EMPTY_TREE + "".join(self.shapes)
                + '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')

    def rels_xml(self):
        return (HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                'relationships">' + "".join(self.rels) + '</Relationships>')


def _picture(slide, rid, x, y, w, h, name):
    n = slide.shape_id()
    slide.shapes.append(
        f'<p:pic><p:nvPicPr><p:cNvPr id="{n}" name={quoteattr(name)}/><p:cNvPicPr>'
        '<a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
        f'<p:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
        f'<p:spPr><a:xfrm><a:off x="{_emu(x)}" y="{_emu(y)}"/><a:ext cx="{_emu(w)}" cy="{_emu(h)}"/>'
        '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>')


# Links keep the text's own colour in PowerPoint (not the theme's link colour).
_LINK_COLOR = ('<a:extLst><a:ext uri="{A12FA001-AC4F-418D-AE19-62706E023703}">'
               '<ahyp:hlinkClr xmlns:ahyp="http://schemas.microsoft.com/office/drawing/2018/'
               'hyperlinkcolor" val="tx"/></a:ext></a:extLst>')


def _run(text, char, scale, link_rids, lang):
    # A raised mark (a footnote's) is superscript and a lowered one subscript,
    # which PowerPoint shows at two thirds of the run's size.
    raised = getattr(char, "raised", 0)
    size = max(100, min(400000, int(round(char.size * (1.5 if raised else 1) * scale * 100))))
    alpha = '<a:alpha val="0"/>' if char.hidden else ""
    fill = f'<a:solidFill><a:srgbClr val="{layout.hex_color(char.color)}">{alpha}</a:srgbClr></a:solidFill>'
    face = quoteattr(char.family)
    link = link_rids.get(char.link, "")
    attrs = f'{lang}sz="{size}" b="{int(char.bold)}" i="{int(char.italic)}" dirty="0"'
    if raised:
        attrs += ' baseline="30000"' if raised > 0 else ' baseline="-25000"'
    return (f'<a:r><a:rPr {attrs}>{fill}<a:latin typeface={face}/><a:ea typeface={face}/>'
            f'<a:cs typeface={face}/>{link}</a:rPr><a:t>{_x(text)}</a:t></a:r>')


def _paragraph_runs(lines, marker=None):
    """The runs of a paragraph's lines, joined with spaces (none after a
    hyphen). ``marker``: a list item's marker ends at this character of the
    first line, followed by a tab."""
    runs = []
    for k, line in enumerate(lines):
        if k == 0 and marker:
            pieces = [[t, c] for t, c in line.runs(0, marker)]
            pieces += [["\t", line.chars[marker - 1]]] + [[t, c] for t, c in line.runs(marker)]
        else:
            pieces = [[t, c] for t, c in line.runs()]
        if k and pieces and runs and not runs[-1][0].endswith("-"):
            runs[-1][0] += " "
        runs += pieces
    return runs


def _text_box(slide, block, place, scale, link_rids, lang):
    """A text box for a block. ``place`` maps page points to slide points."""
    size = block.size
    ascent, descent = layout.metrics(block.lines[0].family)
    # Exact line spacing (a single line too: proportional spacing differs
    # between programs). The extra room goes above the text, so the first
    # baseline is a line spacing less the descent below the top of the box.
    spacing = block.spacing or round(size * 1.2, 2)
    first = spacing - (descent - 0.025) * size
    line_spacing = f'<a:spcPts val="{min(158400, max(0, int(round(spacing * scale * 100))))}"/>'
    top = block.lines[0].base - first
    height = (block.lines[-1].base - block.lines[0].base) + first + descent * size
    width = block.x1 - block.x0
    # Room for text that comes out a little wider than in the PDF (another
    # font; kerning and rounding even in the fonts made to the same widths:
    # Arial, Times New Roman, Courier New). Too little room pushes the last
    # word of a line down and the paragraph out of its box; too much lets a
    # short word move up a line, which does less harm.
    standard = all(c.family in layout.METRICS for ln in block.lines for c in ln.chars)
    extra = width * ((1.01 if standard else SLACK) - 1) + 0.3
    x0 = block.x0 - (extra if block.align == "r" else extra / 2 if block.align == "ctr" else 0)
    width += extra
    cx, cy = layout.to_page(block.angle, x0 + width / 2, top + height / 2)
    cx, cy = place(cx, cy)
    w, h = width * scale, height * scale
    rot = f' rot="{int(block.angle * 60000)}"' if block.angle else ""
    wrap = "square" if any(len(p) > 1 for p in block.paragraphs) else "none"
    paragraphs = []
    for para in block.paragraphs:
        indent, marker = "", None
        if block.align in ("l", "just"):
            first_x = para[0].x0 - block.x0
            rest_x = min(ln.x0 for ln in para[1:]) - block.x0 if len(para) > 1 else first_x
            found = para[0].marker()
            if found:  # a hanging indent: the marker, a tab, the text
                marker, rest_x = found[0], found[1] - block.x0
            if first_x > 0.5 or rest_x > 0.5:
                indent = f' marL="{_emu(rest_x * scale)}" indent="{_emu((first_x - rest_x) * scale)}"'
        algn = block.align if not (block.align == "just" and len(para) == 1) else "l"
        runs = "".join(_run(t, c, scale, link_rids, lang) for t, c in _paragraph_runs(para, marker))
        end = max(100, int(round(para[-1].size * scale * 100)))
        paragraphs.append(
            f'<a:p><a:pPr algn="{algn}"{indent}><a:lnSpc>{line_spacing}</a:lnSpc>'
            '<a:spcBef><a:spcPts val="0"/></a:spcBef><a:spcAft><a:spcPts val="0"/></a:spcAft>'
            f'<a:buNone/></a:pPr>{runs}<a:endParaRPr sz="{end}" dirty="0"/></a:p>')
    n = slide.shape_id()
    name = f"Text {n - 1}"
    slide.shapes.append(
        f'<p:sp><p:nvSpPr><p:cNvPr id="{n}" name="{name}"/><p:cNvSpPr txBox="1"/><p:nvPr/>'
        f'</p:nvSpPr><p:spPr><a:xfrm{rot}><a:off x="{_emu(cx - w / 2)}" y="{_emu(cy - h / 2)}"/>'
        f'<a:ext cx="{_emu(w)}" cy="{_emu(h)}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/>'
        '</a:prstGeom><a:noFill/></p:spPr><p:txBody>'
        f'<a:bodyPr wrap="{wrap}" lIns="0" tIns="0" rIns="0" bIns="0" rtlCol="0" anchor="t">'
        '<a:noAutofit/></a:bodyPr><a:lstStyle/>' + "".join(paragraphs) + '</p:txBody></p:sp>')


def _slide_size(sizes):
    """The size most pages have (the first page of that size, to the point),
    kept within PowerPoint's limits."""
    counts, first = {}, {}
    for w, h in sizes:
        key = (round(w), round(h))
        counts[key] = counts.get(key, 0) + 1
        first.setdefault(key, (w, h))
    w, h = first[max(counts, key=counts.get)]
    factor = min(1.0, MAX_SLIDE / w, MAX_SLIDE / h)
    factor = max(factor, MIN_SLIDE / w, MIN_SLIDE / h)
    return min(max(w * factor, MIN_SLIDE), MAX_SLIDE), min(max(h * factor, MIN_SLIDE), MAX_SLIDE)


def pdf_to_pptx(path, output, password=None, pages=None, mode="editable"):
    """Convert a PDF to a PowerPoint presentation (see the module docstring).
    Returns {"output", "slides", "text_boxes"}."""
    if mode not in MODES:
        raise core.PdfManagerError(f"mode must be one of {', '.join(MODES)}")
    output = Path(output)
    if output.is_dir():
        raise core.PdfManagerError(f"{output} is a folder; give the name of the file to write")
    output.parent.mkdir(parents=True, exist_ok=True)
    editable = mode == "editable"
    with layout.Document(path, password, pages, without_text=editable) as doc:
        partial = output.with_name(output.name + ".part")
        try:
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as z:
                count, boxes, size, extensions = _write_slides(z, doc, editable)
                title = doc.title or Path(path).stem
                z.writestr("[Content_Types].xml", _content_types(count, extensions))
                z.writestr("_rels/.rels", ROOT_RELS)
                z.writestr("docProps/core.xml", _core(title))
                z.writestr("docProps/app.xml", _app(count))
                z.writestr("ppt/presentation.xml", _presentation(count, *size))
                z.writestr("ppt/_rels/presentation.xml.rels", _presentation_rels(count))
                z.writestr("ppt/presProps.xml", PRES_PROPS)
                z.writestr("ppt/viewProps.xml", VIEW_PROPS)
                z.writestr("ppt/tableStyles.xml", TABLE_STYLES)
                z.writestr("ppt/theme/theme1.xml", _theme())
                z.writestr("ppt/slideMasters/slideMaster1.xml", MASTER)
                z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", MASTER_RELS)
                z.writestr("ppt/slideLayouts/slideLayout1.xml", LAYOUT)
                z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", LAYOUT_RELS)
            os.replace(partial, output)
        finally:
            partial.unlink(missing_ok=True)
    return {"output": output, "slides": count, "text_boxes": boxes}


def _write_slides(z, doc, editable):
    """Write the slides and their pictures into the zip, a page at a time.
    Returns (slides, text boxes, slide size, picture file types)."""
    sizes = [doc.size(index) for index in doc.indices]
    width, height = _slide_size(sizes)
    numbers = {index + 1: k + 1 for k, index in enumerate(doc.indices)}
    lang = (f"lang={quoteattr(doc.lang)} "
            if re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8}){0,3}", doc.lang) else "")
    boxes, extensions = 0, set()
    for k, index in enumerate(doc.indices):
        progress.report(k, len(doc.indices), "page")
        number = k + 1
        page = doc.page(index)
        slide = _Slide(number)
        scale = min(width / page.width, height / page.height)
        dx, dy = (width - page.width * scale) / 2, (height - page.height * scale) / 2
        with_text = editable and doc.has_background(index)
        image = doc.render(index, BACKGROUND_DPI if with_text else PICTURE_DPI,
                           without_text=with_text)
        data, ext = layout.encode(image)
        extensions.add(ext)
        z.writestr(f"ppt/media/page{number}.{ext}", data)
        rid = slide.rel("image", f"../media/page{number}.{ext}")
        if with_text and abs(dx) < 0.5 and abs(dy) < 0.5:
            slide.background = rid  # the page fills the slide
        else:
            _picture(slide, rid, dx, dy, page.width * scale, page.height * scale,
                     f"Page {page.number}")
        if with_text:
            link_rids = {}
            for i, link in enumerate(page.links):
                if "uri" in link:
                    uri = quote(link["uri"], safe=":/?#[]@!$&'()*+,;=%~")  # spaces, accents
                    target = slide.rel("hyperlink", uri, external=True)
                    link_rids[i] = f'<a:hlinkClick r:id="{target}">{_LINK_COLOR}</a:hlinkClick>'
                elif link["page"] in numbers:
                    target = slide.rel("slide", f"slide{numbers[link['page']]}.xml")
                    link_rids[i] = (f'<a:hlinkClick r:id="{target}" action="ppaction://'
                                    f'hlinksldjump">{_LINK_COLOR}</a:hlinkClick>')
            place = lambda x, y: (dx + x * scale, dy + y * scale)  # noqa: E731
            for block in page.blocks:
                _text_box(slide, block, place, scale, link_rids, lang)
                boxes += 1
        z.writestr(f"ppt/slides/slide{number}.xml", slide.xml())
        z.writestr(f"ppt/slides/_rels/slide{number}.xml.rels", slide.rels_xml())
    return len(doc.indices), boxes, (width, height), extensions
