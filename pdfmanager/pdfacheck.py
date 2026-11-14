# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Check whether a PDF meets PDF/A, the ISO standard for archiving.

Two ways to check:

  * veraPDF, the reference validator (free, from verapdf.org), when it's
    installed: it checks every rule of the standard.
  * ZPDF's own checks otherwise: the rules files most often break, read
    straight from the file's structure (header and trailer, XMP metadata,
    fonts, colour and output intent, transparency, annotations, actions,
    forms, attached files, optional content and size limits).

    result = check(path)              # the level the file claims, else 2b
    result = check(path, level="1b")
    print(report(result, path.name))

``result``: {"level": "2b", "claimed": "2B" or None, "passed": bool,
"engine": "veraPDF 1.28" or "ZPDF", "issues": [{"clause", "message",
"count", "pages"}], "note": text}. Each issue names the clause of
ISO 19005 it breaks.
"""

import datetime
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from pdfmanager import core, progress

LEVELS = ("1a", "1b", "2a", "2b", "2u", "3a", "3b", "3u")
DEFAULT_LEVEL = "2b"
ENGINES = ("auto", "verapdf", "builtin")

# The clause each rule comes from, in part 1 (ISO 19005-1) and in parts 2
# and 3 (ISO 19005-2, -3, which share their numbering).
CLAUSE = {
    "header": ("6.1.2", "6.1.2"), "trailer": ("6.1.3", "6.1.3"), "encrypt": ("6.1.3", "6.1.3"),
    "eof": ("6.1.3", "6.1.3"), "stream": ("6.1.7", "6.1.7.1"), "filter": ("6.1.10", "6.1.7.2"),
    "limits": ("6.1.12", "6.1.13"), "attachments": ("6.1.11", "6.8"),
    "optional": ("6.1.13", "6.9"), "intent": ("6.2.2", "6.2.3"), "colour": ("6.2.3", "6.2.4.3"),
    "image": ("6.2.4", "6.2.8"), "xobject": ("6.2.5", "6.2.9"), "extgstate": ("6.2.8", "6.2.5"),
    "rendering": ("6.2.9", "6.2.6"), "font-embed": ("6.3.4", "6.2.11.4"),
    "font-subset": ("6.3.5", "6.2.11.4"), "unicode": ("6.3.8", "6.2.11.7"),
    "transparency": ("6.4", "6.2.10"), "annot-type": ("6.5.2", "6.3.1"),
    "annot-flags": ("6.5.3", "6.3.2"), "annot-look": ("6.5.3", "6.3.3"),
    "action": ("6.6.1", "6.5.1"), "trigger": ("6.6.2", "6.5.2"), "metadata": ("6.7.2", "6.6.2.1"),
    "info": ("6.7.3", "6.6.2.3"), "pdfaid": ("6.7.11", "6.6.4"), "structure": ("6.8", "6.7"),
    "form": ("6.9", "6.4"),
}

STANDARD_INTENTS = {"/RelativeColorimetric", "/AbsoluteColorimetric", "/Perceptual", "/Saturation"}
ANNOTS_1 = {"/Text", "/Link", "/FreeText", "/Line", "/Square", "/Circle", "/Highlight",
            "/Underline", "/Squiggly", "/StrikeOut", "/Stamp", "/Ink", "/Popup", "/Widget",
            "/PrinterMark", "/TrapNet"}
ANNOTS_NOT_2 = {"/3D", "/Sound", "/Screen", "/Movie", "/RichMedia"}
ACTIONS_NOT_1 = {"/Launch", "/Sound", "/Movie", "/ResetForm", "/ImportData", "/JavaScript"}
ACTIONS_NOT_2 = ACTIONS_NOT_1 | {"/Hide", "/SetOCGState", "/Rendition", "/Trans", "/GoTo3DView",
                                 "/RichMediaExecute"}
NAMED_OK = {"/NextPage", "/PrevPage", "/FirstPage", "/LastPage"}
BLEND_MODES = {"/Normal", "/Compatible", "/Multiply", "/Screen", "/Overlay", "/Darken",
               "/Lighten", "/ColorDodge", "/ColorBurn", "/HardLight", "/SoftLight",
               "/Difference", "/Exclusion", "/Hue", "/Saturation", "/Color", "/Luminosity"}
STANDARD_ENCODINGS = {"/WinAnsiEncoding", "/MacRomanEncoding", "/StandardEncoding"}

NS = {"rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
      "dc": "http://purl.org/dc/elements/1.1/", "xmp": "http://ns.adobe.com/xap/1.0/",
      "pdf": "http://ns.adobe.com/pdf/1.3/", "pdfaid": "http://www.aiim.org/pdfa/ns/id/"}

BUILTIN_NOTE = ("ZPDF's own checks cover the rules files most often break. For a full check "
                "of every rule, install veraPDF (free, verapdf.org): ZPDF uses it when it's there.")


class _Issues:
    def __init__(self, part):
        self.part = part
        self.found = {}

    def add(self, rule, message, page=None):
        clause = CLAUSE[rule][0 if self.part == 1 else 1]
        item = self.found.setdefault((clause, message), {"clause": clause, "message": message,
                                                         "count": 0, "pages": set()})
        item["count"] += 1
        if page:
            item["pages"].add(page)

    def items(self):
        def order(item):
            return [int(n) for n in item["clause"].split(".")], item["message"]
        return [{**i, "pages": sorted(i["pages"])} for i in sorted(self.found.values(), key=order)]


# --------------------------------------------------------------------------
# XMP metadata
# --------------------------------------------------------------------------

def _xmp_value(root, prefix, name):
    """A property of the XMP packet, written as an element or an attribute;
    for a list (title, creator) its first entry."""
    tag = f"{{{NS[prefix]}}}{name}"
    for desc in root.iter(f"{{{NS['rdf']}}}Description"):
        if tag in desc.attrib:
            return desc.attrib[tag].strip()
        node = desc.find(tag)
        if node is not None:
            item = node.find(".//rdf:li", NS)
            return ((item.text if item is not None else node.text) or "").strip()
    return None


def _claimed(xmp_bytes):
    """The PDF/A level an XMP packet claims ("2B"), or None."""
    try:
        root = ET.fromstring(xmp_bytes)
    except ET.ParseError:
        text = xmp_bytes.decode("utf-8", "replace")
        part = re.search(r"pdfaid:part(?:>|=[\"'])\s*(\d)", text)
        conf = re.search(r"pdfaid:conformance(?:>|=[\"'])\s*([ABUabu])", text)
        return (part.group(1) + (conf.group(1).upper() if conf else "")) if part else None
    part = _xmp_value(root, "pdfaid", "part")
    conf = _xmp_value(root, "pdfaid", "conformance") or ""
    return (part + conf.upper()) if part else None


def _pdf_date(value):
    """D:YYYYMMDDHHmmSSOHH'mm' -> datetime (aware when it has a zone)."""
    m = re.match(r"(?:D:)?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+-])?(\d{2})?'?(\d{2})?",
                 str(value).strip())
    if not m:
        return None
    y, mo, d, h, mi, s = (int(v) if v else default for v, default in
                          zip(m.groups()[:6], (0, 1, 1, 0, 0, 0)))
    try:
        when = datetime.datetime(y, mo, d, h, mi, s)
    except ValueError:
        return None
    sign = m.group(7)
    if sign in ("Z", "z"):
        return when.replace(tzinfo=datetime.timezone.utc)
    if sign in ("+", "-") and m.group(8):
        offset = datetime.timedelta(hours=int(m.group(8)), minutes=int(m.group(9) or 0))
        return when.replace(tzinfo=datetime.timezone(offset if sign == "+" else -offset))
    return when


def _xmp_date(value):
    """An XMP date (YYYY, YYYY-MM-DD, ...THH:MM[:SS[.s]][zone]) -> datetime."""
    m = re.match(r"(\d{4})(?:-(\d\d)(?:-(\d\d)(?:T(\d\d):(\d\d)(?::(\d\d)(?:\.\d+)?)?"
                 r"(Z|[+-]\d\d:\d\d)?)?)?)?$", str(value).strip())
    if not m:
        return None
    y, mo, d, h, mi, s = (int(v) if v else default for v, default in
                          zip(m.groups()[:6], (0, 1, 1, 0, 0, 0)))
    try:
        when = datetime.datetime(y, mo, d, h, mi, s)
    except ValueError:
        return None
    zone = m.group(7)
    if zone == "Z":
        return when.replace(tzinfo=datetime.timezone.utc)
    if zone:
        sign = 1 if zone[0] == "+" else -1
        offset = datetime.timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6]))
        return when.replace(tzinfo=datetime.timezone(sign * offset))
    return when


def _same_time(a, b):
    if a is None or b is None:
        return a is b
    if (a.tzinfo is None) != (b.tzinfo is None):
        a, b = a.replace(tzinfo=None), b.replace(tzinfo=None)
    return abs((a - b).total_seconds()) < 1


# --------------------------------------------------------------------------
# The built-in checker
# --------------------------------------------------------------------------

class _Checker:
    def __init__(self, pdf, path, part, conformance):
        import pikepdf

        self.pk = pikepdf
        self.pdf, self.path = pdf, Path(path)
        self.part, self.conf = part, conformance
        self.issues = _Issues(part)
        self.seen = set()          # streams and dictionaries already checked
        self.fonts = set()
        self.oi_space = None       # colour space of the PDF/A output intent profile
        self.page_transparent = False

    def add(self, rule, message, page=None):
        self.issues.add(rule, message, page)

    def run(self):
        self.file_structure()
        self.metadata()
        self.output_intent()
        self.document_level()
        pages = self.pdf.pages
        for number, page in enumerate(pages, 1):
            progress.report(number - 1, len(pages), "checking page")
            self.page(page, number)
        self.objects()
        return self.issues.items()

    # -- the file itself --------------------------------------------------

    def file_structure(self):
        with open(self.path, "rb") as f:
            head = f.read(1024)
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 1024))
            tail = f.read()
        m = re.match(rb"%PDF-(\d\.\d)(\r\n|\r|\n)", head)
        if not m:
            self.add("header", "The file doesn't start with a PDF header (%PDF-1.n) at its first byte.")
        else:
            second = head[m.end():].split(b"\n", 1)[0].split(b"\r", 1)[0]
            if not (second.startswith(b"%") and sum(b > 127 for b in second[1:]) >= 4):
                self.add("header", "The header isn't followed by a comment of four or more "
                                   "non-text bytes, which marks the file as binary.")
        end = tail.rfind(b"%%EOF")
        if end < 0 or tail[end + 5:] not in (b"", b"\n", b"\r", b"\r\n"):
            self.add("eof", "There is data after the file's last %%EOF marker.")
        if self.pdf.is_encrypted:
            self.add("encrypt", "The file is encrypted (password-protected). PDF/A files can't be.")
        if "/ID" not in self.pdf.trailer:
            self.add("trailer", "The file has no ID in its trailer.")

    # -- metadata -----------------------------------------------------------

    def metadata(self):
        root = self.pdf.Root
        meta = root.get("/Metadata")
        if meta is None or not isinstance(meta, self.pk.Stream):
            self.add("pdfaid", "The file has no XMP metadata, so it doesn't say it's PDF/A.")
            return
        if "/Filter" in meta:
            self.add("metadata", "The XMP metadata is compressed; it must be stored as plain text.")
        try:
            data = meta.read_bytes()
            xmp = ET.fromstring(data)
        except (ET.ParseError, self.pk.PdfError):
            self.add("metadata", "The XMP metadata isn't well-formed XML.")
            return
        part = _xmp_value(xmp, "pdfaid", "part")
        conf = (_xmp_value(xmp, "pdfaid", "conformance") or "").upper()
        if not part:
            self.add("pdfaid", "The XMP metadata doesn't say the file is PDF/A (no pdfaid:part).")
        elif part != str(self.part):
            self.add("pdfaid", f"The file says it's PDF/A-{part}{conf.lower()}, not "
                               f"PDF/A-{self.part}{self.conf}.")
        elif conf != self.conf.upper():
            self.add("pdfaid", f"The file says its conformance level is {conf or 'missing'}, not "
                               f"{self.conf.upper()}.")
        info = self.pdf.trailer.get("/Info")
        if info is None:
            return
        pairs = (("/Title", "dc", "title"), ("/Author", "dc", "creator"),
                 ("/Subject", "dc", "description"), ("/Keywords", "pdf", "Keywords"),
                 ("/Creator", "xmp", "CreatorTool"), ("/Producer", "pdf", "Producer"))
        for key, prefix, name in pairs:
            if key not in info:
                continue
            value = str(info[key]).strip()
            xmp_value = _xmp_value(xmp, prefix, name)
            if value and value != (xmp_value or ""):
                self.add("info", f"The document's {key[1:].lower()} isn't the same in its "
                                 "information and in its XMP metadata.")
        for key, name in (("/CreationDate", "CreateDate"), ("/ModDate", "ModifyDate")):
            if key in info and str(info[key]).strip():
                if not _same_time(_pdf_date(info[key]), _xmp_date(_xmp_value(xmp, "xmp", name) or "")):
                    label = "creation" if key == "/CreationDate" else "modification"
                    self.add("info", f"The {label} date isn't the same in the document "
                                     "information and in its XMP metadata.")

    # -- output intent -------------------------------------------------------

    def output_intent(self):
        profiles = []
        for oi in self.pdf.Root.get("/OutputIntents") or []:
            if not isinstance(oi, self.pk.Dictionary) or oi.get("/S") != "/GTS_PDFA1":
                continue
            profile = oi.get("/DestOutputProfile")
            if profile is None:
                continue
            profiles.append(profile)
            try:
                data = profile.read_bytes()[:128]
            except (self.pk.PdfError, AttributeError):
                data = b""
            if len(data) < 40 or data[36:40] != b"acsp":
                self.add("intent", "The output intent's colour profile isn't a valid ICC profile.")
                continue
            space = data[16:20].decode("latin-1").strip()
            self.oi_space = {"RGB": "RGB", "CMYK": "CMYK", "GRAY": "Gray"}.get(space, space)
            major = data[8]
            if self.part == 1 and major > 2:
                self.add("intent", f"The output intent's colour profile is ICC version {major}; "
                                   "PDF/A-1 needs version 2.")
            if self.part >= 2 and major > 4:
                self.add("intent", f"The output intent's colour profile is ICC version {major}.")
            if data[12:16] not in (b"prtr", b"mntr"):
                self.add("intent", "The output intent's colour profile isn't a printer or "
                                   "screen profile.")
        if len({p.objgen if p.is_indirect else id(p) for p in profiles}) > 1:
            self.add("intent", "The file has more than one PDF/A output intent profile.")

    # -- the catalog ---------------------------------------------------------

    def document_level(self):
        root = self.pdf.Root
        if "/AA" in root:
            self.add("trigger", "The document runs actions when it's opened, saved or printed.")
        if "/OpenAction" in root and isinstance(root.OpenAction, self.pk.Dictionary):
            self.action(root.OpenAction, None)
        names = root.get("/Names")
        if names is not None and "/JavaScript" in names:
            self.add("action", "The document contains JavaScript.")
        if names is not None and "/EmbeddedFiles" in names:
            self.embedded(names.EmbeddedFiles)
        form = root.get("/AcroForm")
        if form is not None:
            if form.get("/NeedAppearances") is True:
                self.add("form", "The form asks readers to draw its fields themselves "
                                 "(NeedAppearances), so they can look different everywhere.")
            if self.part >= 2 and "/XFA" in form:
                self.add("form", "The form is an XFA form (Adobe LiveCycle).")
            if self.part >= 2:
                self.fields(form.get("/Fields") or [], 0)
        if self.part >= 2 and root.get("/NeedsRendering") is True:
            self.add("form", "The document needs to be rendered from XFA (NeedsRendering).")
        if "/OCProperties" in root:
            if self.part == 1:
                self.add("optional", "The document has layers (optional content).")
            else:
                self.layers(root.OCProperties)
        outlines = root.get("/Outlines")
        if outlines is not None:
            self.outline(outlines.get("/First"), 0)
        if self.conf == "a":
            mark = root.get("/MarkInfo")
            if mark is None or mark.get("/Marked") is not True or "/StructTreeRoot" not in root:
                self.add("structure", "The document isn't tagged (level A needs its logical "
                                      "structure: headings, paragraphs, reading order).")
            if not str(root.get("/Lang", "")).strip():
                self.add("structure", "The document's language isn't set.")

    def fields(self, fields, depth):
        for field in fields:
            if depth > 30 or not isinstance(field, self.pk.Dictionary) or self._seen(field):
                continue
            if "/AA" in field:
                self.add("trigger", "A form field runs actions (when it's typed in, left or "
                                    "calculated).")
            self.fields(field.get("/Kids") or [], depth + 1)

    def layers(self, props):
        for group in props.get("/OCGs") or []:
            if isinstance(group, self.pk.Dictionary) and "/Name" not in group:
                self.add("optional", "A layer has no name.")
        configs = [props.get("/D")] + list(props.get("/Configs") or [])
        for config in configs:
            if not isinstance(config, self.pk.Dictionary):
                continue
            if "/Name" not in config:
                self.add("optional", "A layer configuration has no name.")
            if "/AS" in config:
                self.add("optional", "A layer configuration turns layers on or off by itself "
                                     "(AS).")

    def outline(self, item, depth):
        count = 0
        while isinstance(item, self.pk.Dictionary) and count < 100000 and not self._seen(item):
            if "/A" in item:
                self.action(item.A, None)
            if depth < 30:
                self.outline(item.get("/First"), depth + 1)
            item = item.get("/Next")
            count += 1

    def embedded(self, tree, depth=0):
        if depth > 30 or not isinstance(tree, self.pk.Dictionary):
            return
        names = tree.get("/Names") or []
        for i in range(1, len(names), 2):
            self.attachment(names[i], None)
        for kid in tree.get("/Kids") or []:
            self.embedded(kid, depth + 1)

    def attachment(self, spec, page):
        if self.part == 1:
            self.add("attachments", "The document has attached files.", page)
            return
        if not isinstance(spec, self.pk.Dictionary):
            return
        stream = self._entry(spec.get("/EF"), "/F")
        if self.part == 2:
            try:
                data = stream.read_bytes() if stream is not None else b""
            except self.pk.PdfError:
                data = b""
            if not data.startswith(b"%PDF") or b"pdfaid" not in data:
                self.add("attachments", "An attached file isn't a PDF/A file (PDF/A-2 allows only "
                                        "those; PDF/A-3 allows any).", page)
        else:
            if "/AFRelationship" not in spec:
                self.add("attachments", "An attached file doesn't say how it relates to the "
                                        "document (AFRelationship).", page)
            if "/F" not in spec or "/UF" not in spec:
                self.add("attachments", "An attached file's name isn't given both ways "
                                        "(F and UF).", page)
            if stream is not None and "/Subtype" not in stream:
                self.add("attachments", "An attached file doesn't give its type (MIME).", page)

    def action(self, action, page, depth=0):
        if depth > 20 or not isinstance(action, self.pk.Dictionary):
            return
        kind = action.get("/S")
        forbidden = ACTIONS_NOT_1 if self.part == 1 else ACTIONS_NOT_2
        if kind in forbidden:
            what = {"/JavaScript": "runs JavaScript", "/Launch": "starts a program",
                    "/ResetForm": "clears the form", "/ImportData": "imports form data",
                    "/Sound": "plays a sound", "/Movie": "plays a movie"}.get(
                        str(kind), f"is a {str(kind)[1:]} action")
            self.add("action", f"A link or button {what}.", page)
        elif kind == "/Named" and action.get("/N") not in NAMED_OK:
            self.add("action", f"A link or button uses the menu command {str(action.get('/N'))[1:]}.",
                     page)
        nxt = action.get("/Next")
        for follow in (nxt if isinstance(nxt, self.pk.Array) else [nxt] if nxt is not None else []):
            self.action(follow, page, depth + 1)

    # -- pages -----------------------------------------------------------------

    def _entry(self, holder, key):
        """holder[key] when holder is a dictionary, else None."""
        return holder.get(key) if isinstance(holder, self.pk.Dictionary) else None

    def _names(self, value):
        """A name or an array of names (a filter, a blend mode) as strings."""
        items = value if isinstance(value, self.pk.Array) else [value]
        return [str(v) for v in items if v is not None]

    def _seen(self, obj):
        key = obj.objgen if obj.is_indirect else None
        if key is None or key == (0, 0):
            return False
        if key in self.seen:
            return True
        self.seen.add(key)
        return False

    def page(self, page, number):
        obj = page.obj
        self.page_transparent = False
        if self.part >= 2 and "/AA" in obj:
            self.add("trigger", "A page runs actions when it's opened or closed.", number)
        group = obj.get("/Group")
        if isinstance(group, self.pk.Dictionary) and group.get("/S") == "/Transparency":
            if self.part == 1:
                self.add("transparency", "A page is drawn as a transparency group.", number)
            else:
                self.page_transparent = True
        resources = obj.get("/Resources")
        try:
            instructions = self.pk.parse_content_stream(page, self.OPERATORS)
        except self.pk.PdfError:
            instructions = []
        self.content(instructions, resources, number)
        self.annotations(obj.get("/Annots") or [], number)
        if self.part >= 2 and self.page_transparent and self.oi_space is None:
            if not (isinstance(group, self.pk.Dictionary) and "/CS" in group):
                self.add("transparency", "A page uses transparency but neither it nor the "
                                         "document says which colours to blend in (no output "
                                         "intent, no group colour space).", number)

    OPERATORS = "rg RG k K g G cs CS Tf Do gs sh ri BI ID EI"

    def content(self, instructions, resources, page):
        resources = resources if isinstance(resources, self.pk.Dictionary) else self.pk.Dictionary()
        defaults = set()
        spaces = resources.get("/ColorSpace")
        if isinstance(spaces, self.pk.Dictionary):
            defaults = {k[len("/Default"):] for k in spaces.keys() if k.startswith("/Default")}
        used = set()
        for instruction in instructions:
            if isinstance(instruction, self.pk.ContentStreamInlineImage):
                self.inline_image(instruction.iimage, used, page)
                continue
            op = str(instruction.operator)
            args = instruction.operands
            if op in ("rg", "RG"):
                used.add("RGB")
            elif op in ("k", "K"):
                used.add("CMYK")
            elif op in ("g", "G"):
                used.add("Gray")
            elif op in ("cs", "CS") and args:
                used |= self.colour_space(args[0], resources)
            elif op == "Tf" and args:
                font = self._entry(resources.get("/Font"), str(args[0]))
                if font is not None:
                    self.font(font, page)
            elif op == "Do" and args:
                xobject = self._entry(resources.get("/XObject"), str(args[0]))
                if xobject is not None:
                    used |= self.xobject(xobject, resources, page)
            elif op == "gs" and args:
                state = self._entry(resources.get("/ExtGState"), str(args[0]))
                if state is not None:
                    self.ext_gstate(state, page)
            elif op == "sh" and args:
                shading = self._entry(resources.get("/Shading"), str(args[0]))
                if isinstance(shading, (self.pk.Dictionary, self.pk.Stream)):
                    used |= self.colour_space(shading.get("/ColorSpace"), resources)
            elif op == "ri" and args and str(args[0]) not in STANDARD_INTENTS:
                self.add("rendering", f"Drawing uses the rendering intent {str(args[0])[1:]}.", page)
        patterns = resources.get("/Pattern")
        if isinstance(patterns, self.pk.Dictionary):
            for name in list(patterns.keys()):
                used |= self.pattern(patterns[name], resources, page)
        self.device_colours(used - defaults, page)

    def device_colours(self, used, page):
        for space in sorted(used):
            if space == "Gray" and self.oi_space is None:
                self.add("colour", "Grey is used without saying which grey (no output intent or "
                                   "colour profile).", page)
            elif space in ("RGB", "CMYK") and self.oi_space != space:
                why = ("there's no output intent" if self.oi_space is None else
                       f"the output intent is {self.oi_space}")
                self.add("colour", f"{space} colours are used but {why}: they need a colour "
                                   f"profile to say which {space}.", page)

    def colour_space(self, space, resources, depth=0):
        """The device colour spaces a colour space comes down to."""
        if space is None or depth > 10:
            return set()
        if isinstance(space, self.pk.Name):
            name = str(space)
            if name in ("/DeviceRGB", "/RGB"):
                return {"RGB"}
            if name in ("/DeviceCMYK", "/CMYK"):
                return {"CMYK"}
            if name in ("/DeviceGray", "/G"):
                return {"Gray"}
            if name == "/Pattern":
                return set()
            named = resources.get("/ColorSpace") if isinstance(resources, self.pk.Dictionary) else None
            if isinstance(named, self.pk.Dictionary) and name in named:
                return self.colour_space(named[name], resources, depth + 1)
            return set()
        if isinstance(space, self.pk.Array) and len(space):
            family = str(space[0])
            if family == "/Indexed" and len(space) > 1:
                return self.colour_space(space[1], resources, depth + 1)
            if family in ("/Separation", "/DeviceN") and len(space) > 2:
                return self.colour_space(space[2], resources, depth + 1)
            if family == "/Pattern" and len(space) > 1:
                return self.colour_space(space[1], resources, depth + 1)
        return set()

    def inline_image(self, image, used, page):
        image = getattr(image, "obj", image)  # pikepdf's PdfInlineImage: its dictionary, names spelled out
        used |= self.colour_space(image.get("/CS", image.get("/ColorSpace")), self.pk.Dictionary())
        filters = image.get("/F", image.get("/Filter"))
        names = self._names(filters)
        if "/LZW" in names or "/LZWDecode" in names:
            self.add("filter", "A picture is compressed with LZW, which PDF/A doesn't allow.", page)
        if image.get("/I", image.get("/Interpolate")) is True:
            self.add("image", "A picture asks to be smoothed when enlarged (Interpolate).", page)

    def xobject(self, xobject, resources, page):
        if not isinstance(xobject, self.pk.Stream) or self._seen(xobject):
            return set()
        kind = xobject.get("/Subtype")
        if kind == "/Image":
            return self.image(xobject, resources, page)
        if kind == "/PS":
            self.add("xobject", "The page contains PostScript code.", page)
            return set()
        if kind != "/Form":
            return set()
        if "/Ref" in xobject:
            self.add("xobject", "The page shows content from another file (a reference XObject).", page)
        if "/OPI" in xobject or xobject.get("/Subtype2") == "/PS" or "/PS" in xobject:
            self.add("xobject", "The page contains PostScript or OPI print instructions.", page)
        group = xobject.get("/Group")
        if isinstance(group, self.pk.Dictionary) and group.get("/S") == "/Transparency":
            if self.part == 1:
                self.add("transparency", "Part of the page is drawn as a transparency group.", page)
            else:
                self.page_transparent = True
        inner = xobject.get("/Resources", resources)
        try:
            instructions = self.pk.parse_content_stream(xobject, self.OPERATORS)
        except self.pk.PdfError:
            instructions = []
        self.content(instructions, inner, page)
        return set()

    def image(self, image, resources, page):
        if image.get("/Interpolate") is True:
            self.add("image", "A picture asks to be smoothed when enlarged (Interpolate).", page)
        if "/Alternates" in image:
            self.add("image", "A picture has alternate versions.", page)
        if "/OPI" in image:
            self.add("image", "A picture has OPI print instructions.", page)
        if "/Intent" in image and str(image.Intent) not in STANDARD_INTENTS:
            self.add("rendering", f"A picture uses the rendering intent {str(image.Intent)[1:]}.", page)
        if "/SMask" in image:
            if self.part == 1:
                self.add("transparency", "A picture has transparency (a soft mask).", page)
            else:
                self.page_transparent = True
        filters = image.get("/Filter")
        names = self._names(filters)
        if self.part == 1 and "/JPXDecode" in names:
            self.add("image", "A picture is JPEG 2000, which PDF/A-1 doesn't allow.", page)
        if image.get("/ImageMask") is True:
            return set()
        if "/JPXDecode" in names and "/ColorSpace" not in image:
            return set()  # its colour profile is in the JPEG 2000 data
        return self.colour_space(image.get("/ColorSpace"), resources)

    def pattern(self, pattern, resources, page):
        if not isinstance(pattern, (self.pk.Dictionary, self.pk.Stream)) or self._seen(pattern):
            return set()
        if pattern.get("/PatternType") == 2:
            shading = pattern.get("/Shading")
            return self.colour_space(shading.get("/ColorSpace"), resources) \
                if isinstance(shading, (self.pk.Dictionary, self.pk.Stream)) else set()
        if isinstance(pattern, self.pk.Stream):
            try:
                instructions = self.pk.parse_content_stream(pattern, self.OPERATORS)
            except self.pk.PdfError:
                instructions = []
            self.content(instructions, pattern.get("/Resources", resources), page)
        return set()

    def ext_gstate(self, state, page):
        if not isinstance(state, self.pk.Dictionary) or self._seen(state):
            return
        if "/TR" in state:
            self.add("extgstate", "The drawing changes colours with a transfer function (TR).", page)
        if "/TR2" in state and state.TR2 != "/Default":
            self.add("extgstate", "The drawing changes colours with a transfer function (TR2).", page)
        if "/HTP" in state:
            self.add("extgstate", "The drawing sets halftone details (HTP).", page)
        if "/RI" in state and str(state.RI) not in STANDARD_INTENTS:
            self.add("rendering", f"Drawing uses the rendering intent {str(state.RI)[1:]}.", page)
        smask = state.get("/SMask")
        alpha = [float(state[k]) for k in ("/CA", "/ca") if k in state]
        blend = state.get("/BM")
        blends = self._names(blend)
        transparent = (smask is not None and smask != "/None") or any(a < 1 for a in alpha) \
            or any(b not in ("/Normal", "/Compatible") for b in blends)
        if self.part == 1 and transparent:
            self.add("transparency", "The page uses transparency (see-through colours, soft "
                                     "masks or blend modes), which PDF/A-1 doesn't allow.", page)
        elif transparent:
            self.page_transparent = True
        if self.part >= 2 and any(b not in BLEND_MODES for b in blends):
            self.add("transparency", "The page uses a blend mode PDF/A doesn't know.", page)

    def font(self, font, page):
        if not isinstance(font, self.pk.Dictionary):
            return
        key = font.objgen if font.is_indirect else id(font)
        if key in self.fonts:
            return
        self.fonts.add(key)
        kind = font.get("/Subtype")
        name = str(font.get("/BaseFont", "")).lstrip("/") or "a font"
        name = re.sub(r"^[A-Z]{6}\+", "", name)
        if kind == "/Type3":
            if self.conf in ("u", "a") and "/ToUnicode" not in font:
                self.add("unicode", f"The text in {name} can't be read as Unicode "
                                    "(no ToUnicode map).", page)
            return
        target = font
        if kind == "/Type0":
            kids = font.get("/DescendantFonts")
            target = kids[0] if isinstance(kids, self.pk.Array) and len(kids) else None
        desc = target.get("/FontDescriptor") if isinstance(target, self.pk.Dictionary) else None
        files = [k for k in ("/FontFile", "/FontFile2", "/FontFile3")
                 if isinstance(desc, self.pk.Dictionary) and k in desc]
        if not files:
            self.add("font-embed", f"The font {name} isn't embedded.", page)
        elif self.part == 1 and kind == "/Type0" and "/CIDSet" not in desc \
                and re.match(r"^[A-Z]{6}\+", str(font.get("/BaseFont", "")).lstrip("/")):
            self.add("font-subset", f"The subset of the font {name} doesn't list its glyphs "
                                    "(CIDSet).", page)
        elif self.part == 1 and files == ["/FontFile"] and "/CharSet" not in desc \
                and re.match(r"^[A-Z]{6}\+", str(font.get("/BaseFont", "")).lstrip("/")):
            self.add("font-subset", f"The subset of the font {name} doesn't list its characters "
                                    "(CharSet).", page)
        if self.conf in ("u", "a") and "/ToUnicode" not in font:
            encoding = font.get("/Encoding")
            if isinstance(encoding, self.pk.Dictionary):
                encoding = encoding.get("/BaseEncoding", "/StandardEncoding")
            simple = kind in ("/Type1", "/MMType1", "/TrueType") and str(encoding) in STANDARD_ENCODINGS
            if not simple:
                self.add("unicode", f"The text in {name} can't be read as Unicode (no ToUnicode map).",
                         page)

    def annotations(self, annots, page):
        allowed = ANNOTS_1 if self.part == 1 else None
        for annot in annots:
            if not isinstance(annot, self.pk.Dictionary):
                continue
            kind = annot.get("/Subtype")
            label = str(kind)[1:] if kind is not None else "unknown"
            if kind not in allowed if allowed is not None else kind in ANNOTS_NOT_2:
                self.add("annot-type", f"The page has a {label} annotation, which PDF/A-{self.part} "
                                       "doesn't allow.", page)
            if kind == "/FileAttachment" and "/FS" in annot:
                self.attachment(annot.FS, page)
            popup = kind == "/Popup"
            flags = int(annot.get("/F", 0))
            if not (popup and self.part >= 2):
                if "/F" not in annot or not flags & 4:
                    self.add("annot-flags", "An annotation (a comment, link or field) isn't set to "
                                            "print.", page)
                if flags & (1 | 2 | 32 | 256):
                    self.add("annot-flags", "An annotation is hidden.", page)
            if self.part == 1 and "/CA" in annot and float(annot.CA) != 1:
                self.add("annot-flags", "An annotation is see-through.", page)
            ap = annot.get("/AP")
            if self.part == 1 and isinstance(ap, self.pk.Dictionary) and set(ap.keys()) - {"/N"}:
                self.add("annot-look", "An annotation has a look for when it's pressed or hovered "
                                       "(only the normal one is allowed).", page)
            if self.part >= 2 and kind not in ("/Popup", "/Link") and not (
                    isinstance(ap, self.pk.Dictionary) and "/N" in ap):
                rect = [float(v) for v in annot.get("/Rect", [0, 0, 0, 0])]
                if len(rect) == 4 and rect[2] != rect[0] and rect[3] != rect[1]:
                    self.add("annot-look", f"A {label} annotation has no look of its own "
                                           "(appearance stream).", page)
            if "/A" in annot:
                if self.part == 1 and kind == "/Widget":
                    self.add("action", "A form field runs an action when it's clicked.", page)
                else:
                    self.action(annot.A, page)
            if "/AA" in annot and (self.part == 1 or kind == "/Widget"):
                self.add("trigger", "An annotation runs actions (when the mouse goes over it, "
                                    "it's clicked or it's focused).", page)
            if isinstance(ap, self.pk.Dictionary):
                for look in ap.values():
                    streams = [look] if isinstance(look, self.pk.Stream) else \
                        list(look.values()) if isinstance(look, self.pk.Dictionary) else []
                    for stream in streams:
                        if isinstance(stream, self.pk.Stream):
                            self.xobject_form(stream, page)

    def xobject_form(self, stream, page):
        """An appearance stream: drawn like a Form XObject."""
        if self._seen(stream):
            return
        try:
            instructions = self.pk.parse_content_stream(stream, self.OPERATORS)
        except self.pk.PdfError:
            instructions = []
        self.content(instructions, stream.get("/Resources"), page)

    # -- every object ------------------------------------------------------------

    def objects(self):
        part1 = self.part == 1
        max_string = 65535 if part1 else 32767
        max_real = 32767 if part1 else 3.403e38
        count = 0
        for obj in self.pdf.objects:
            count += 1
            if count > 300000:
                break
            try:
                self._limits(obj, part1, max_string, max_real, 0)
                if isinstance(obj, self.pk.Stream):
                    self._stream(obj)
            except (self.pk.PdfError, ValueError, TypeError):
                continue
        if len(self.pdf.objects) > 8388607:
            self.add("limits", "The file has more objects than PDF/A allows (8,388,607).")

    def _stream(self, stream):
        if any(k in stream for k in ("/F", "/FFilter", "/FDecodeParms")) \
                and stream.get("/Type") != "/EmbeddedFile":
            self.add("stream", "A stream's data is kept in another file.")
        filters = stream.get("/Filter")
        names = self._names(filters)
        if "/LZWDecode" in names:
            self.add("filter", "Part of the file is compressed with LZW, which PDF/A doesn't allow.")
        if self.part >= 2 and "/Crypt" in names:
            self.add("filter", "Part of the file is encrypted (Crypt filter).")

    def _limits(self, obj, part1, max_string, max_real, depth):
        if depth > 12:
            return
        pk = self.pk
        if isinstance(obj, pk.Name):
            if len(str(obj).encode("utf-8")) - 1 > 127:
                self.add("limits", "A name in the file is longer than 127 bytes.")
        elif isinstance(obj, pk.String):
            if len(bytes(obj)) > max_string:
                self.add("limits", f"A text string in the file is longer than {max_string:,} bytes.")
        elif isinstance(obj, (pk.Dictionary, pk.Stream)):
            items = obj.items() if isinstance(obj, pk.Dictionary) else obj.stream_dict.items()
            items = list(items)
            if part1 and len(items) > 4095:
                self.add("limits", "A dictionary in the file has more than 4,095 entries.")
            for key, value in items:
                if len(key.encode("utf-8")) - 1 > 127:
                    self.add("limits", "A name in the file is longer than 127 bytes.")
                if not getattr(value, "is_indirect", False):
                    self._limits(value, part1, max_string, max_real, depth + 1)
        elif isinstance(obj, pk.Array):
            if part1 and len(obj) > 8191:
                self.add("limits", "An array in the file has more than 8,191 entries.")
            for value in obj:
                if not getattr(value, "is_indirect", False):
                    self._limits(value, part1, max_string, max_real, depth + 1)
        else:
            try:
                number = obj.as_int() if hasattr(obj, "as_int") else None
            except (TypeError, ValueError):
                number = None
            if isinstance(obj, int) or (number is not None and isinstance(number, int)):
                value = int(obj)
                if not -2 ** 31 <= value <= 2 ** 31 - 1:
                    self.add("limits", "A whole number in the file is out of PDF/A's range.")
            elif isinstance(obj, float) or type(obj).__name__ == "Decimal":
                if abs(float(obj)) > max_real:
                    self.add("limits", f"A number in the file is bigger than {max_real:g}.")


def _builtin(path, level, password):
    import pikepdf

    part, conformance = int(level[0]), level[1]
    try:
        pdf = pikepdf.open(path, password=password or "")
    except pikepdf.PasswordError:
        raise core.PdfManagerError(f"{path} is password-protected: give its password") from None
    except pikepdf.PdfError as exc:
        raise core.PdfManagerError(f"{path} can't be read ({exc})") from None
    with pdf:
        return _Checker(pdf, path, part, conformance).run()


# --------------------------------------------------------------------------
# veraPDF
# --------------------------------------------------------------------------

def find_verapdf():
    """The veraPDF command, if it's installed (on the PATH, or where its
    installer puts it)."""
    for name in ("verapdf", "verapdf.bat"):
        found = shutil.which(name)
        if found:
            return found
    home = Path.home()
    places = [home / "verapdf", Path("/opt/verapdf"), Path("/usr/local/verapdf"),
              home / "Applications" / "verapdf"]
    if sys.platform == "win32":
        from pdfmanager.winprograms import program_dirs

        places = [home / "veraPDF", home / "verapdf"] + [base / "veraPDF" for base in program_dirs()]
    for place in places:
        for name in ("verapdf.bat", "verapdf") if sys.platform == "win32" else ("verapdf",):
            if (place / name).is_file():
                return str(place / name)
    return None


def parse_verapdf(xml_text):
    """veraPDF's machine-readable report -> (passed, issues, version, profile)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise core.PdfManagerError(f"veraPDF's report couldn't be read ({exc})") from None
    local = lambda el: el.tag.rsplit("}", 1)[-1]
    version = next((el.get("version") for el in root.iter() if local(el) == "releaseDetails"
                    and el.get("id") in ("core", "gui", "apps")), None)
    report = next((el for el in root.iter() if local(el) == "validationReport"), None)
    if report is None:
        problem = next((el for el in root.iter() if local(el) == "exceptionMessage"), None)
        if problem is None:  # (an element without children counts as false: no "or")
            problem = next((el for el in root.iter() if local(el) == "taskException"), None)
        text = (problem.text or problem.get("message") or "") if problem is not None else ""
        text = f": {text.strip()}" if text.strip() else ""
        raise core.PdfManagerError(f"veraPDF couldn't check the file{text}")
    issues = []
    for rule in report.iter():
        if local(rule) != "rule" or rule.get("status") != "failed":
            continue
        description = next((el.text for el in rule if local(el) == "description"), "") or ""
        pages = set()
        for el in rule.iter():
            if local(el) == "context" and el.text:
                pages |= {int(n) + 1 for n in re.findall(r"pages\[(\d+)\]", el.text)}
        issues.append({"clause": rule.get("clause", ""), "message": " ".join(description.split()),
                       "count": int(rule.get("failedChecks") or 1), "pages": sorted(pages)})
    passed = report.get("isCompliant") == "true"
    return passed, issues, version, report.get("profileName", "")


def _verapdf(path, level, exe):
    command = [exe, "--format", "mrr", "--maxfailuresdisplayed", "5"]
    command += ["--flavour", level] if level else []
    command.append(str(path))
    try:
        run = subprocess.run(command, capture_output=True, text=True, timeout=900,
                             encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise core.PdfManagerError(f"veraPDF failed: {exc}") from None
    text = run.stdout[run.stdout.find("<"):] if "<" in run.stdout else ""
    if not text:
        detail = (run.stderr or "").strip().splitlines()[-2:]
        raise core.PdfManagerError("veraPDF failed" + (": " + " ".join(detail) if detail else ""))
    return parse_verapdf(text)


# --------------------------------------------------------------------------
# Checking
# --------------------------------------------------------------------------

def check(path, level=None, password=None, engine="auto"):
    """Check a PDF against PDF/A ``level`` ("2b"...; default: the level the
    file claims, else 2b). ``engine``: "auto" (veraPDF when installed),
    "verapdf" or "builtin"."""
    path = Path(path)
    if engine not in ENGINES:
        raise core.PdfManagerError(f"unknown checker {engine!r}; use {', '.join(ENGINES)}")
    reader = core.open_pdf(path, password)  # the usual messages for a damaged file
    claimed = None
    try:
        xmp = reader.xmp_metadata
        if xmp is not None:
            claimed = _claimed(xmp.stream.get_data())
    except Exception:  # damaged metadata: the checks report it
        claimed = None
    if level:
        level = str(level).lower().replace("pdf/a-", "").replace("-", "")
        if level not in LEVELS:
            raise core.PdfManagerError(f"unknown PDF/A level {level!r}; use one of {', '.join(LEVELS)}")
    else:
        level = claimed.lower() if claimed and claimed.lower() in LEVELS else DEFAULT_LEVEL
    result = {"file": str(path), "level": level, "claimed": claimed}
    exe = find_verapdf() if engine in ("auto", "verapdf") else None
    if engine == "verapdf" and not exe:
        raise core.PdfManagerError("veraPDF isn't installed (it's free from verapdf.org)")
    if exe and not reader.is_encrypted:
        passed, issues, version, _profile = _verapdf(path, level, exe)
        engine_name = f"veraPDF {version}" if version else "veraPDF"
        result.update(passed=passed, issues=issues, engine=engine_name, note="")
        return result
    issues = _builtin(path, level, password)
    result.update(passed=not issues, issues=issues, engine="ZPDF", note=BUILTIN_NOTE)
    return result


def summary(result):
    """One line: "passes PDF/A-2b" or "fails PDF/A-2b: 3 problems (...)"."""
    name = f"PDF/A-{result['level']}"
    if result["passed"]:
        return f"passes {name}" + (" (ZPDF's checks)" if result["engine"] == "ZPDF" else
                                   f" ({result['engine']})")
    n = len(result["issues"])
    first = result["issues"][0]["message"].rstrip(".") if result["issues"] else ""
    return f"fails {name}: {n} problem{'s' if n != 1 else ''}" + (f" ({first}…)" if first else "")


def report(result, name):
    """A plain-text report."""
    lines = [f"PDF/A check: {name}", ""]
    claimed = result.get("claimed")
    lines.append(f"Checked against: PDF/A-{result['level']}"
                 + ("" if claimed else " (the file doesn't say it's PDF/A)")
                 + (f" (the file says PDF/A-{claimed.lower()})" if claimed and
                    claimed.lower() != result["level"] else ""))
    lines.append(f"Checked by: {result['engine']}")
    lines.append(f"Result: {'PASSES' if result['passed'] else 'FAILS'}")
    if result["issues"]:
        lines += ["", "Problems:"]
        for item in result["issues"]:
            where = ""
            if item["pages"]:
                shown = ", ".join(str(p) for p in item["pages"][:10])
                where = f" (page{'s' if len(item['pages']) > 1 else ''} {shown}" \
                        f"{'…' if len(item['pages']) > 10 else ''})"
            times = f" ×{item['count']}" if item["count"] > 1 else ""
            lines.append(f"  [{item['clause']}] {item['message']}{where}{times}")
        lines += ["", "PDF → PDF/A converts the file and fixes most of these (fonts, colours, "
                      "transparency, metadata)."]
    if result.get("note"):
        lines += ["", result["note"]]
    return "\n".join(lines) + "\n"
