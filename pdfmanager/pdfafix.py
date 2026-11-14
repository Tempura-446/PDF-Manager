# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
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
    """A function giving the width (thousandths) the file's font has for a
    code: from its /Widths (its MissingWidth outside them), or for a
    standard font without them the widths every reader knows. None when
    neither is known."""
    if "/Widths" in font and "/FirstChar" in font:
        first = int(font.FirstChar)
        table = {first + i: float(w) for i, w in enumerate(font.Widths)}
        descriptor = font.get("/FontDescriptor")
        missing = float(descriptor.get("/MissingWidth", 0)) if descriptor is not None else 0.0
        return lambda code: table.get(code, missing)
    from reportlab.pdfbase import pdfmetrics

    name = fonts.strip_subset(str(font.get("/BaseFont", "")).lstrip("/"))
    try:
        widths = pdfmetrics.getFont(name).widths
    except Exception:
        return None
    # reportlab's table is in WinAnsi order: by character, for any encoding.
    by_char = {bytes([code]).decode("cp1252", "ignore"): w for code, w in enumerate(widths)}
    chars = _code_chars(font)
    return lambda code: float(by_char[chars[code]]) if chars.get(code) in by_char else None


def _used_fonts(pdf):
    """{object number: (font, the character codes shown with it)} for the
    fonts text is shown with, across pages, the forms they draw and the
    looks of annotations. A font written straight into a resource
    dictionary is made an object of its own, so it can be told apart.
    (Text in patterns and the like isn't looked for: a font used only
    there is left as it is, and the check afterwards says so.)"""
    import pikepdf

    used, seen = {}, set()

    def walk(stream, resources, depth=0):
        if depth > 8 or not isinstance(resources, pikepdf.Dictionary):
            return
        font_of = resources.get("/Font")
        font_of = font_of if isinstance(font_of, pikepdf.Dictionary) else None
        current = None
        try:
            instructions = pikepdf.parse_content_stream(stream)
        except Exception:
            return
        for operands, operator in instructions:
            op = str(operator)
            if op == "Tf" and operands:
                key = str(operands[0])
                current = font_of.get(key) if font_of is not None else None
                if isinstance(current, pikepdf.Dictionary) and not current.is_indirect:
                    current = font_of[key] = pdf.make_indirect(current)
                if not isinstance(current, pikepdf.Dictionary):
                    current = None
                else:  # chosen, even if nothing is shown with it here
                    used.setdefault(current.objgen, (current, set()))
            elif op in ("Tj", "'", '"', "TJ") and current is not None and operands:
                codes = used.setdefault(current.objgen, (current, set()))[1]
                last = operands[-1]
                for piece in (last if isinstance(last, pikepdf.Array) else [last]):
                    if isinstance(piece, pikepdf.String):
                        codes.update(bytes(piece))
            elif op == "Do" and operands:
                forms = resources.get("/XObject")
                form = forms.get(str(operands[0])) if isinstance(forms, pikepdf.Dictionary) else None
                if isinstance(form, pikepdf.Stream) and form.get("/Subtype") == "/Form" \
                        and form.objgen not in seen:
                    seen.add(form.objgen)
                    walk(form, form.get("/Resources", resources), depth + 1)

    form_fonts = pdf.Root.get("/AcroForm")
    form_fonts = form_fonts.get("/DR") if isinstance(form_fonts, pikepdf.Dictionary) else None
    for page in pdf.pages:
        walk(page, page.get("/Resources"))
        for annot in page.get("/Annots") or []:
            looks = annot.get("/AP") if isinstance(annot, pikepdf.Dictionary) else None
            for look in (looks.values() if isinstance(looks, pikepdf.Dictionary) else []):
                for stream in ([look] if isinstance(look, pikepdf.Stream) else
                               list(look.values()) if isinstance(look, pikepdf.Dictionary) else []):
                    if isinstance(stream, pikepdf.Stream):
                        walk(stream, stream.get("/Resources", form_fonts))
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


def _sfnt_tables(program):
    """{table name: offset} of a TrueType font program."""
    import struct

    return {program[12 + 16 * i:16 + 16 * i]: struct.unpack(">I", program[20 + 16 * i:24 + 16 * i])[0]
            for i in range(struct.unpack(">H", program[4:6])[0])}


def _true_metrics(face):
    """Put right the advance widths reportlab keeps for subsetting. A font
    lists widths for its first glyphs only when the rest share the last
    one (every glyph of a fixed-width font); reportlab keeps that shared
    width in thousandths, not the font's own units, and would write it
    into the subset so: Courier New's letters came out 293 wide inside
    the font and 600 in the PDF, which PDF/A doesn't allow."""
    import struct

    try:
        listed = struct.unpack(">H", face.get_table("hhea")[34:36])[0]
        shared = struct.unpack(">H", face.get_table("hmtx")[4 * (listed - 1):4 * listed - 2])[0]
    except Exception:
        return
    for glyph in range(listed, len(face.hmetrics)):
        face.hmetrics[glyph] = (shared, face.hmetrics[glyph][1])


def _program_agrees(program, widths):
    """Whether the subset gives every code the width the dictionary will say."""
    import struct

    try:
        tables = _sfnt_tables(program)
        units = struct.unpack(">H", program[tables[b"head"] + 18:tables[b"head"] + 20])[0]
        first, count = struct.unpack(">HH", program[tables[b"cmap"] + 18:tables[b"cmap"] + 22])
        glyphs = struct.unpack(f">{count}H", program[tables[b"cmap"] + 22:tables[b"cmap"] + 22 + 2 * count])
        listed = struct.unpack(">H", program[tables[b"hhea"] + 34:tables[b"hhea"] + 36])[0]
        for code, said in enumerate(widths):
            glyph = glyphs[code - first]
            at = tables[b"hmtx"] + 4 * min(glyph, listed - 1)
            if abs(struct.unpack(">H", program[at:at + 2])[0] * 1000 / units - said) > 1:
                return False
        return True
    except Exception:
        return False


def _embed(pdf, font, codes):
    """Embed an installed font for the simple font ``font``, shown with the
    character ``codes``; returns the family of the font used. Raises
    ValueError, saying why, unless every one of those codes is known to
    come out as the same letter, as wide as it was."""
    import struct

    import pikepdf
    from reportlab.pdfbase.ttfonts import TTFontFile

    name = fonts.strip_subset(str(font.get("/BaseFont", "")).lstrip("/")) or "a font"
    descriptor = font.get("/FontDescriptor")
    flags = int(descriptor.get("/Flags", 0)) if isinstance(descriptor, pikepdf.Dictionary) else 0
    if flags & 4 or (font.get("/Subtype") == "/TrueType" and "/Encoding" not in font):
        # Its codes mean what the font's own table says, and that font isn't here.
        raise ValueError("it has its own table of letters, which only the font itself knows")
    found = stand_in(name)
    if found is None:
        raise ValueError("no font with the same letter widths is installed")
    chars = _code_chars(font)
    face = TTFontFile(found[0], subfontIndex=found[1])
    family = face.familyName.decode("latin-1", "replace") if isinstance(face.familyName, bytes) \
        else str(face.familyName)
    try:
        licence = struct.unpack(">H", face.get_table("OS/2")[8:10])[0]
    except Exception:
        licence = 0
    if licence & 0x0002 or licence & 0x0100:
        raise ValueError(f"the installed {family} doesn't allow being put into documents")
    original = _original_widths(font)
    if original is None:
        raise ValueError("the widths of its letters aren't known")

    def width_of(char):
        return round(face.charWidths.get(ord(char), face.defaultWidth))

    space = width_of(" ") if 0x20 in face.charToGlyph else 0
    widths = [space] * 256  # codes with no letter show the stand-in's space
    for code, char in chars.items():
        if len(char) == 1 and ord(char) in face.charToGlyph:
            widths[code] = width_of(char)
    # Chosen but not seen showing anything (it may, where the engine doesn't
    # look): held to the plain letters, digits and signs.
    for code in sorted(codes) or [c for c in range(32, 127) if c in chars]:
        char = chars.get(code)
        if not char or len(char) != 1:
            raise ValueError(f"it shows a letter (code {code}) the engine can't name")
        if ord(char) not in face.charToGlyph:
            raise ValueError(f"the installed {family} has no {char!r}")
        was = original(code)
        if was is None or abs(widths[code] - was) > WIDTH_SLACK:
            raise ValueError(f"the installed {family} has letters of other widths, which would "
                             "move the text")
    subset = [ord(chars[code]) if code in chars and len(chars[code]) == 1
              and ord(chars[code]) in face.charToGlyph else 0x20 for code in range(256)]
    _true_metrics(face)
    program = face.makeSubset(subset)
    if not _program_agrees(program, widths):
        raise ValueError(f"the installed {family} couldn't be put into the file with its widths right")
    tag = "".join(chr(65 + (sum(program[i::6]) + i) % 26) for i in range(6))
    ps_name = face.name.decode("latin-1", "replace") if isinstance(face.name, bytes) else str(face.name)
    base = pikepdf.Name(f"/{tag}+{ps_name}".replace(" ", ""))
    file = pdf.make_stream(program, Length1=len(program))
    new_descriptor = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.FontDescriptor, FontName=base, Flags=4,  # symbolic: its own code table
        FontBBox=[round(v) for v in face.bbox], ItalicAngle=face.italicAngle,
        Ascent=round(face.ascent), Descent=round(face.descent), CapHeight=round(face.capHeight),
        StemV=round(face.stemV), FontFile2=file))
    for key in ("/Encoding", "/FontDescriptor", "/Widths", "/FirstChar", "/LastChar", "/ToUnicode"):
        if key in font:
            del font[key]
    font.Type, font.Subtype, font.BaseFont = pikepdf.Name.Font, pikepdf.Name.TrueType, base
    font.FirstChar, font.LastChar, font.Widths = 0, 255, widths
    font.FontDescriptor = new_descriptor
    font.ToUnicode = pdf.make_stream(_to_unicode(chars))
    return family


def _embed_fonts(pdf, notes, reasons):
    """Embed the fonts text is shown with that aren't in the file. A font
    that isn't seen to be used is left alone: the check afterwards objects
    if it is used after all (in a pattern, say)."""
    for font, codes in _used_fonts(pdf).values():
        if font.get("/Subtype") not in ("/Type1", "/MMType1", "/TrueType"):
            continue  # Type 0 fonts without their file, Type 3: the check reports them
        descriptor = font.get("/FontDescriptor")
        if descriptor is not None and any(k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3")):
            continue
        name = fonts.strip_subset(str(font.get("/BaseFont", "")).lstrip("/")) or "a font"
        try:
            family = _embed(pdf, font, codes)
        except Exception as exc:
            reasons.append(f"the font {name} isn't embedded and can't be: {exc}")
        else:
            notes.append(f"embedded {family} for the font {name}")


# --------------------------------------------------------------------------
# The rest of the file
# --------------------------------------------------------------------------

def _cid_sets(pdf):
    """PDF/A-1: a subset of a CID font says which glyphs it holds (CIDSet).
    Browsers leave that out. For TrueType outlines, where the PDF's
    numbers are the font's own glyph numbers, it is every glyph in the
    embedded program."""
    import struct

    import pikepdf

    for font in pdf.objects:
        if not (isinstance(font, pikepdf.Dictionary) and font.get("/Subtype") == "/CIDFontType2"):
            continue
        descriptor = font.get("/FontDescriptor")
        mapping = font.get("/CIDToGIDMap")
        if not isinstance(descriptor, pikepdf.Dictionary) or "/FontFile2" not in descriptor \
                or not (mapping is None or mapping == "/Identity"):
            continue  # another numbering: left for the check to judge
        try:
            program = descriptor.FontFile2.read_bytes()
            maxp = _sfnt_tables(program)[b"maxp"]
            glyphs = struct.unpack(">H", program[maxp + 4:maxp + 6])[0]
        except Exception:
            continue
        bits = bytearray(-(-glyphs // 8))
        for glyph in range(glyphs):
            bits[glyph // 8] |= 0x80 >> (glyph % 8)
        descriptor.CIDSet = pdf.make_stream(bytes(bits))


HIDDEN = 1 | 2 | 32   # invisible, hidden, not shown on screen


def _annotations(pdf, notes, reasons):
    """Annotations print, show only their normal look, and none is hidden.
    A hidden comment is taken out (the page looks the same without it); a
    hidden form field can't be, nor shown: that stops the conversion."""
    import pikepdf

    removed = 0
    for page in pdf.pages:
        annots = page.get("/Annots")
        if not isinstance(annots, pikepdf.Array):
            continue
        keep = []
        for annot in annots:
            if not isinstance(annot, pikepdf.Dictionary):
                keep.append(annot)
                continue
            flags = int(annot.get("/F", 0))
            if flags & HIDDEN and annot.get("/Subtype") != "/Popup":
                if annot.get("/Subtype") == "/Widget":
                    reasons.append("a form field is hidden, which PDF/A doesn't allow (showing it "
                                   "would change the page)")
                    keep.append(annot)
                else:
                    removed += 1
                continue
            annot.F = (flags | 4) & ~(HIDDEN | 256)
            looks = annot.get("/AP")
            if isinstance(looks, pikepdf.Dictionary):
                for key in [k for k in looks.keys() if k != "/N"]:
                    del looks[key]
            keep.append(annot)
        if len(keep) != len(annots):
            page.Annots = pikepdf.Array(keep)
    if removed:
        notes.append(f"removed {removed} hidden comment{'s' if removed != 1 else ''} "
                     "(PDF/A allows none)")


def _attachments(pdf):
    """PDF/A-3: every attached file says what it is and is tied to the document."""
    import mimetypes

    import pikepdf

    specs = []
    for name, attached in pdf.attachments.items():
        spec = attached.obj
        if "/AFRelationship" not in spec:
            spec.AFRelationship = pikepdf.Name.Unspecified
        files = spec.get("/EF")
        for key in (files.keys() if isinstance(files, pikepdf.Dictionary) else []):
            stream = files[key]
            if isinstance(stream, pikepdf.Stream) and "/Subtype" not in stream:
                kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
                stream.Subtype = pikepdf.Name("/" + kind)
        specs.append(spec if spec.is_indirect else pdf.make_indirect(spec))
    if specs:
        tied = pdf.Root.get("/AF")
        tied = list(tied) if isinstance(tied, pikepdf.Array) else []
        have = {a.objgen for a in tied if a.is_indirect}
        pdf.Root.AF = pikepdf.Array(tied + [s for s in specs if s.objgen not in have])


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
            try:
                obj.write(obj.read_bytes())  # stored again without it (packed with Flate on saving)
            except pikepdf.PdfError:
                pass  # can't be unpacked here: the check afterwards says so


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
        value = str(old[key]) if old is not None and key in old else ""
        # No control characters: XML has no way to write them, and a title runs on one line.
        value = " ".join("".join(ch if ch.isprintable() or ch in "\r\n\t" else "" for ch in value).split())
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
    from pypdf.errors import PyPdfError

    level = str(level)
    if level not in LEVELS:
        raise core.PdfManagerError("PDF/A level must be 1, 2 or 3")
    try:
        return _convert(path, Path(output), level, password, [] if notes is None else notes)
    except pikepdf.PasswordError:
        raise core.PdfManagerError(f"{Path(path).name} is password-protected; give its password") from None
    except (PyPdfError, pikepdf.PdfError) as exc:
        raise core.PdfManagerError(f"{Path(path).name} is damaged or unusual and couldn't be "
                                   f"converted ({exc})") from None


def _convert(path, output, level, password, notes):
    import pikepdf

    from pdfmanager import archive, pdfacheck

    with tempfile.TemporaryDirectory(prefix="pdfm-pdfa-") as tmp:
        work, result = Path(tmp) / "work.pdf", Path(tmp) / "result.pdf"
        reader = core.open_pdf(path, password)
        if not len(reader.pages):
            raise core.PdfManagerError(f"{Path(path).name} has no pages")
        if reader.is_encrypted:
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
            core.convert_color(work, work, "rgb", images="cmyk")
            notes.append("brought CMYK colours to RGB")
        reasons = []
        with pikepdf.open(work) as pdf:
            _embed_fonts(pdf, notes, reasons)
            _annotations(pdf, notes, reasons)
            if reasons:
                raise CannotConvert(reasons)
            _streams(pdf)
            if level == "3":
                _attachments(pdf)
            if level == "1":
                _cid_sets(pdf)
            _output_intent(pdf)
            _metadata(pdf, level, output.stem)
            pdf.save(result, min_version="1.4" if level == "1" else "1.7",
                     force_version="1.4" if level == "1" else None,
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
