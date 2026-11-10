"""The built-in PDF/A engine: makes a PDF conform to PDF/A-1b, -2b or -3b
without Ghostscript, by putting right what the standard asks for and
leaving the pages as they are:

- the file says it is PDF/A (XMP metadata, agreeing with the document
  information), and names a colour profile (sRGB) for its colours;
- scripts and, for parts 1 and 2, attached files are taken out;
- protection is removed (PDF/A files can't be encrypted);
- CMYK device colours are brought to RGB, so one profile covers the file;
- links and comments are set to print, none hidden;
- LZW-packed streams are repacked, pictures aren't asked to be smoothed;
- fonts that aren't in the file are embedded from the computer's own, but
  only a font whose letters are as wide as the original's, so no line moves.

What it can't put right (a font with no such stand-in, transparency in
PDF/A-1) stops it with CannotConvert, which says what stood in the way.
The result is checked with pdfacheck's own rules before it is handed over.
"""
import datetime
import shutil
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from pdfmanager import core, fonts

LEVELS = ("1", "2", "3")
# Installed fonts with the same letter widths as the standard PDF fonts.
SAME_WIDTHS = {
    "helvetica": ("Arial", "Liberation Sans", "Arimo", "Nimbus Sans"),
    "arial": ("Arial", "Liberation Sans", "Arimo"),
    "times": ("Times New Roman", "Liberation Serif", "Tinos", "Nimbus Roman"),
    "timesnewroman": ("Times New Roman", "Liberation Serif", "Tinos"),
    "courier": ("Courier New", "Liberation Mono", "Cousine", "Nimbus Mono PS"),
    "couriernew": ("Courier New", "Liberation Mono", "Cousine"),
}
WIDTH_SLACK = 2   # thousandths of the type size a stand-in's letter may differ by


class CannotConvert(core.PdfManagerError):
    """The file can't be made PDF/A by ZPDF alone; ``reasons`` says why."""

    def __init__(self, reasons):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------

def stand_in(name):
    """(path, number in a collection) of an installed TrueType font to embed
    for the font a PDF calls ``name``: the font itself when it's installed,
    else one made to the same widths. None when there is none."""
    bold, italic = fonts.font_style(name)
    key, family = fonts.font_key(name), fonts.family_key(name)
    index = fonts._font_index()
    for entry in index:
        if key and key in (entry[0], entry[1]):
            return entry[5], entry[6]
    for wanted in (family,) + tuple(SAME_WIDTHS.get(family, ())):
        wanted = fonts.family_key(wanted)
        for entry in index:
            if fonts.family_key(entry[7]) == wanted and (entry[3], entry[4]) == (bold, italic):
                return entry[5], entry[6]
    return None


def _code_chars(font):
    """{character code: Unicode character} of a simple font, from its
    encoding and the changes made to it."""
    from reportlab.pdfbase import _fontdata

    import pikepdf

    encoding = font.get("/Encoding")
    base, changes = encoding, None
    if isinstance(encoding, pikepdf.Dictionary):
        base, changes = encoding.get("/BaseEncoding"), encoding.get("/Differences")
    table = {"/WinAnsiEncoding": "WinAnsiEncoding", "/MacRomanEncoding": "MacRomanEncoding"}.get(
        str(base) if base is not None else "", "StandardEncoding")
    names = list(_fontdata.encodings[table])
    code = 0
    for item in (changes if changes is not None else []):
        if isinstance(item, pikepdf.Name):
            if 0 <= code < 256:
                names[code] = str(item).lstrip("/")
            code += 1
        else:
            code = int(item)
    chars = {}
    for code, glyph in enumerate(names):
        char = fonts._glyph_char(glyph) if glyph else None
        if char:
            chars[code] = char
    return chars


def _original_widths(font):
    """{code: width in thousandths} the file's font has: its /Widths, or
    for a standard font without them the widths every reader knows."""
    if "/Widths" in font and "/FirstChar" in font:
        first = int(font.FirstChar)
        return {first + i: float(w) for i, w in enumerate(font.Widths)}
    from reportlab.pdfbase import pdfmetrics

    name = fonts.strip_subset(str(font.get("/BaseFont", "")).lstrip("/"))
    try:
        widths = pdfmetrics.getFont(name).widths
    except Exception:
        return None
    # reportlab's table is in WinAnsi order: by character, for any encoding.
    by_char = {bytes([code]).decode("cp1252", "ignore"): w for code, w in enumerate(widths)}
    return {code: float(by_char[char]) for code, char in _code_chars(font).items() if char in by_char}


def _used_codes(pdf):
    """{font object key: the character codes shown with it} across pages,
    the forms they draw and the looks of annotations."""
    import pikepdf

    used, seen = {}, set()

    def walk(stream, resources, depth=0):
        if depth > 8 or resources is None:
            return
        font_of = resources.get("/Font") or {}
        current = None
        try:
            instructions = pikepdf.parse_content_stream(stream)
        except Exception:
            return
        for operands, operator in instructions:
            op = str(operator)
            if op == "Tf" and operands:
                current = font_of.get(str(operands[0]))
            elif op in ("Tj", "'", '"', "TJ") and current is not None and operands:
                key = current.objgen if current.is_indirect else id(current)
                codes = used.setdefault(key, set())
                last = operands[-1]
                for piece in (last if isinstance(last, pikepdf.Array) else [last]):
                    if isinstance(piece, pikepdf.String):
                        codes.update(bytes(piece))
            elif op == "Do" and operands:
                form = (resources.get("/XObject") or {}).get(str(operands[0]))
                if form is not None and form.get("/Subtype") == "/Form" and form.objgen not in seen:
                    seen.add(form.objgen)
                    walk(form, form.get("/Resources", resources), depth + 1)

    for page in pdf.pages:
        walk(page, page.get("/Resources"))
        for annot in page.get("/Annots") or []:
            looks = annot.get("/AP") if isinstance(annot, pikepdf.Dictionary) else None
            for look in (looks.values() if isinstance(looks, pikepdf.Dictionary) else []):
                for stream in ([look] if isinstance(look, pikepdf.Stream) else
                               list(look.values()) if isinstance(look, pikepdf.Dictionary) else []):
                    if isinstance(stream, pikepdf.Stream):
                        walk(stream, stream.get("/Resources"))
    return used


def _to_unicode(chars):
    def utf16(char):
        data = char.encode("utf-16-be")
        return data.hex().upper()

    lines = [f"<{code:02X}> <{utf16(char)}>" for code, char in sorted(chars.items())]
    body = ""
    for start in range(0, len(lines), 100):
        chunk = lines[start:start + 100]
        body += f"{len(chunk)} beginbfchar\n" + "\n".join(chunk) + "\nendbfchar\n"
    return ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
            "1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
            + body + "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n").encode("ascii")


def _embed(pdf, font, codes):
    """Embed an installed font for the simple font ``font``; returns the
    name of the font used, or raises ValueError saying why none could be."""
    import pikepdf
    from reportlab.pdfbase.ttfonts import TTFontFile

    name = fonts.strip_subset(str(font.get("/BaseFont", "")).lstrip("/")) or "a font"
    found = stand_in(name)
    if found is None:
        raise ValueError("no font with the same letter widths is installed")
    chars = _code_chars(font)
    face = TTFontFile(found[0], subfontIndex=found[1])
    original = _original_widths(font)
    widths = [0] * 256
    for code in range(256):
        char = chars.get(code)
        if char and len(char) == 1 and ord(char) in face.charToGlyph:
            widths[code] = round(face.charWidths.get(ord(char), face.defaultWidth))
    for code in sorted(codes):
        char = chars.get(code)
        if not char or char.isspace() and code not in (32,):
            continue
        if len(char) != 1 or ord(char) not in face.charToGlyph:
            raise ValueError(f"the installed {face.familyName.decode('latin-1', 'replace')} "
                             f"has no {char!r}")
        if original is None or abs(widths[code] - original.get(code, widths[code])) > WIDTH_SLACK:
            raise ValueError("the installed font of that name has letters of other widths, "
                             "which would move the text")
    subset = [ord(chars[code]) if code in chars and len(chars[code]) == 1 else 0x20 for code in range(256)]
    program = face.makeSubset(subset)
    tag = "".join(chr(65 + (sum(program[i::6]) + i) % 26) for i in range(6))
    ps_name = face.name.decode("latin-1", "replace") if isinstance(face.name, bytes) else str(face.name)
    base = pikepdf.Name(f"/{tag}+{ps_name}".replace(" ", ""))
    file = pdf.make_stream(program, Length1=len(program))
    descriptor = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.FontDescriptor, FontName=base, Flags=4,  # symbolic: its own code table
        FontBBox=[round(v) for v in face.bbox], ItalicAngle=face.italicAngle,
        Ascent=round(face.ascent), Descent=round(face.descent), CapHeight=round(face.capHeight),
        StemV=round(face.stemV), FontFile2=file))
    for key in ("/Encoding", "/FontDescriptor", "/Widths", "/FirstChar", "/LastChar", "/ToUnicode"):
        if key in font:
            del font[key]
    font.Type, font.Subtype, font.BaseFont = pikepdf.Name.Font, pikepdf.Name.TrueType, base
    font.FirstChar, font.LastChar, font.Widths = 0, 255, widths
    font.FontDescriptor = descriptor
    font.ToUnicode = pdf.make_stream(_to_unicode({c: ch for c, ch in chars.items() if ch}))
    return face.familyName.decode("latin-1", "replace") if isinstance(face.familyName, bytes) \
        else str(face.familyName)


def _all_fonts(pdf):
    import pikepdf

    seen = set()
    for obj in pdf.objects:
        if isinstance(obj, pikepdf.Dictionary) and obj.get("/Type") == "/Font":
            seen.add(obj.objgen)
            yield obj
    for obj in pdf.objects:  # fonts written straight into a resource dictionary
        holder = obj.get("/Font") if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)) else None
        if isinstance(holder, pikepdf.Dictionary):
            for font in holder.values():
                if isinstance(font, pikepdf.Dictionary) and not font.is_indirect:
                    yield font


def _embed_fonts(pdf, notes, reasons):
    used = None
    for font in _all_fonts(pdf):
        kind = font.get("/Subtype")
        if kind not in ("/Type1", "/MMType1", "/TrueType"):
            continue  # Type 0 fonts without their file, Type 3: the check reports them
        descriptor = font.get("/FontDescriptor")
        if descriptor is not None and any(k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3")):
            continue
        name = fonts.strip_subset(str(font.get("/BaseFont", "")).lstrip("/")) or "a font"
        if used is None:
            used = _used_codes(pdf)
        codes = used.get(font.objgen if font.is_indirect else id(font), set())
        try:
            family = _embed(pdf, font, codes)
        except Exception as exc:
            reasons.append(f"the font {name} isn't embedded and can't be: {exc}")
        else:
            notes.append(f"embedded {family} for the font {name}")


# --------------------------------------------------------------------------
# The rest of the file
# --------------------------------------------------------------------------

def _annotations(pdf):
    import pikepdf

    for page in pdf.pages:
        for annot in page.get("/Annots") or []:
            if isinstance(annot, pikepdf.Dictionary):
                annot.F = (int(annot.get("/F", 0)) | 4) & ~(1 | 2 | 32 | 256)


def _streams(pdf):
    import pikepdf

    for obj in pdf.objects:
        if not isinstance(obj, pikepdf.Stream):
            continue
        if obj.get("/Subtype") == "/Image" and obj.get("/Interpolate") is not None:
            del obj["/Interpolate"]
        filters = obj.get("/Filter")
        names = [str(f) for f in filters] if isinstance(filters, pikepdf.Array) else [str(filters)]
        if "/LZWDecode" in names:
            obj.write(obj.read_bytes())  # stored again without it (packed with Flate on saving)


def _output_intent(pdf):
    import pikepdf

    for intent in pdf.Root.get("/OutputIntents") or []:
        if isinstance(intent, pikepdf.Dictionary) and intent.get("/S") == "/GTS_PDFA1" \
                and "/DestOutputProfile" in intent:
            return
    from pdfmanager import archive

    profile = pdf.make_stream(archive.srgb_v2_profile(), N=3)
    pdf.Root.OutputIntents = pikepdf.Array([pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.OutputIntent, S=pikepdf.Name.GTS_PDFA1,
        OutputConditionIdentifier=pikepdf.String("sRGB"), Info=pikepdf.String("sRGB IEC61966-2.1"),
        DestOutputProfile=profile))])


def _xmp_date(when):
    text = when.strftime("%Y-%m-%dT%H:%M:%S")
    if when.tzinfo is None:
        return text
    offset = when.utcoffset()
    minutes = int(offset.total_seconds() // 60)
    return text + ("Z" if minutes == 0 else f"{'+' if minutes > 0 else '-'}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}")


def _pdf_date(when):
    text = when.strftime("D:%Y%m%d%H%M%S")
    if when.tzinfo is None:
        return text
    minutes = int(when.utcoffset().total_seconds() // 60)
    return text + ("Z" if minutes == 0 else f"{'+' if minutes > 0 else '-'}{abs(minutes) // 60:02d}'{abs(minutes) % 60:02d}'")


def _metadata(pdf, level, fallback_title):
    """The document information and an XMP packet that says the same, and
    that the file is PDF/A."""
    import pikepdf

    from pdfmanager import __version__, pdfacheck

    old = pdf.trailer.get("/Info")
    values = {}
    for key in ("/Title", "/Author", "/Subject", "/Keywords", "/Creator"):
        value = str(old[key]).strip() if old is not None and key in old else ""
        if value:
            values[key] = value
    values.setdefault("/Title", fallback_title)
    values["/Producer"] = f"ZPDF {__version__}"
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    created = pdfacheck._pdf_date(old["/CreationDate"]) if old is not None and "/CreationDate" in old else None
    created = created or now
    info = pikepdf.Dictionary({key: pikepdf.String(value) for key, value in values.items()})
    info.CreationDate, info.ModDate = pikepdf.String(_pdf_date(created)), pikepdf.String(_pdf_date(now))
    pdf.trailer.Info = pdf.make_indirect(info)

    def alt(tag, key):
        return (f"<{tag}><rdf:Alt><rdf:li xml:lang=\"x-default\">{escape(values[key])}</rdf:li>"
                f"</rdf:Alt></{tag}>") if key in values else ""

    parts = [
        alt("dc:title", "/Title"), alt("dc:description", "/Subject"),
        f"<dc:creator><rdf:Seq><rdf:li>{escape(values['/Author'])}</rdf:li></rdf:Seq></dc:creator>"
        if "/Author" in values else "",
        f"<pdf:Keywords>{escape(values['/Keywords'])}</pdf:Keywords>" if "/Keywords" in values else "",
        f"<pdf:Producer>{escape(values['/Producer'])}</pdf:Producer>",
        f"<xmp:CreatorTool>{escape(values['/Creator'])}</xmp:CreatorTool>" if "/Creator" in values else "",
        f"<xmp:CreateDate>{_xmp_date(created)}</xmp:CreateDate>",
        f"<xmp:ModifyDate>{_xmp_date(now)}</xmp:ModifyDate>",
        f"<xmp:MetadataDate>{_xmp_date(now)}</xmp:MetadataDate>",
        f"<pdfaid:part>{level}</pdfaid:part><pdfaid:conformance>B</pdfaid:conformance>",
    ]
    packet = ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
              '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
              '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
              '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
              'xmlns:pdf="http://ns.adobe.com/pdf/1.3/" xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
              'xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">\n'
              + "\n".join(p for p in parts if p)
              + "\n</rdf:Description>\n</rdf:RDF>\n</x:xmpmeta>\n" + " " * 2000 + '\n<?xpacket end="w"?>')
    pdf.Root.Metadata = pikepdf.Stream(pdf, packet.encode("utf-8"), Type=pikepdf.Name.Metadata,
                                       Subtype=pikepdf.Name.XML)


def convert(path, output, level="2", password=None, notes=None):
    """Make ``path`` PDF/A-``level``b at ``output``. ``notes`` (a list) is
    told what was changed. Raises CannotConvert when something the standard
    forbids can't be put right here."""
    import pikepdf

    from pdfmanager import archive, pdfacheck

    level = str(level)
    if level not in LEVELS:
        raise core.PdfManagerError("PDF/A level must be 1, 2 or 3")
    notes = [] if notes is None else notes
    output = Path(output)
    with tempfile.TemporaryDirectory(prefix="pdfm-pdfa-") as tmp:
        work, result = Path(tmp) / "work.pdf", Path(tmp) / "result.pdf"
        if core.open_pdf(path, password).is_encrypted:
            core.decrypt(path, work, password or "")
            notes.append("removed the password protection (PDF/A files can't have any)")
        else:
            shutil.copyfile(path, work)
        counts = archive.sanitize(work, work, metadata=False, javascript=True, attachments=level != "3")
        if counts.get("javascript"):
            notes.append("removed scripts and actions")
        if counts.get("attachments"):
            notes.append(f"removed {counts['attachments']} attached file"
                         f"{'s' if counts['attachments'] != 1 else ''} (PDF/A-{level} allows none)")
        before = pdfacheck.check(work, level=f"{level}b", engine="builtin")["issues"]
        if any(i["message"].startswith("CMYK colours are used") for i in before):
            core.convert_color(work, work, "rgb")
            notes.append("brought CMYK colours to RGB")
        reasons = []
        with pikepdf.open(work) as pdf:
            _embed_fonts(pdf, notes, reasons)
            if reasons:
                raise CannotConvert(reasons)
            _annotations(pdf)
            _streams(pdf)
            _output_intent(pdf)
            _metadata(pdf, level, output.stem)
            pdf.remove_unreferenced_resources()
            pdf.save(result, min_version="1.4" if level == "1" else "1.7", force_version="1.4" if level == "1" else None,
                     object_stream_mode=(pikepdf.ObjectStreamMode.disable if level == "1"
                                         else pikepdf.ObjectStreamMode.generate),
                     compress_streams=True)
        left = pdfacheck.check(result, level=f"{level}b", engine="builtin")["issues"]
        if left:
            reasons = [i["message"].rstrip(".") for i in left]
            if level == "1" and any("transparen" in r or "see-through" in r for r in reasons):
                reasons.append("PDF/A-1 allows no transparency: choose PDF/A-2")
            raise CannotConvert(reasons)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(result), str(output))
    return output
