"""Hostile and broken PDFs for the robustness sweep (test_robustness.py).

Each is written byte by byte, so the defect is exactly the one named: a
file cut short, a cross-reference table pointing nowhere, a page tree that
loops, a stream that won't decompress, and so on.
"""
import zlib
from pathlib import Path

from reportlab.pdfgen import canvas


def _pdf(objects, root=1, trailer_extra="", fix_xref=True):
    """A PDF from ``objects`` (bodies of objects 1..n, as text or bytes)."""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        body = body.encode("latin-1") if isinstance(body, str) else body
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset if fix_xref else 999999:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root {root} 0 R {trailer_extra}>>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def _stream(dictionary, data):
    return f"<< {dictionary} /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"


def _page_objects(page="/Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R",
                  content=b"BT /F1 24 Tf 72 700 Td (Hello) Tj ET", extra=()):
    return ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            f"<< {page} /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>",
            _stream("", content), *extra]


def good(path):
    c = canvas.Canvas(str(path))
    for n in range(3):
        c.drawString(72, 720, f"Page {n + 1}: the quick brown fox")
        c.showPage()
    c.save()
    return path


def build(folder):
    """Write the corpus into ``folder``; {name: path}."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    files = {}

    def write(name, data):
        files[name] = folder / f"{name}.pdf"
        files[name].write_bytes(data)

    reference = good(folder / "_reference.pdf").read_bytes()
    write("empty", b"")
    write("not_a_pdf", b"Hello, I am a text file wearing a .pdf name.\n")
    write("truncated", reference[: len(reference) // 2])
    write("xref_points_nowhere", _pdf(_page_objects(), fix_xref=False))
    write("startxref_wrong", _pdf(_page_objects()).replace(b"startxref\n", b"startxref\n9").replace(
        b"%%EOF", b"%%EOF"))
    write("missing_content_object",
          _pdf(_page_objects(page="/Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 99 0 R")))
    write("page_tree_loop", _pdf([
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R 2 0 R] /Count 2 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>"]))
    write("bad_flate_stream", _pdf(_page_objects()[:3] + [
        _stream("/Filter /FlateDecode", b"\x78\x9c this is not deflate data at all \x00\xff")]))
    jpeg_garbage = b"\xff\xd8\xff\xe0" + b"\x00garbage" * 50
    write("broken_image", _pdf(_page_objects(
        page="/Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
             "/Resources << /XObject << /Im1 5 0 R >> >>",
        content=b"q 300 0 0 300 72 400 cm /Im1 Do Q",
        extra=[_stream("/Type /XObject /Subtype /Image /Width 640 /Height 480 /ColorSpace /DeviceRGB "
                       "/BitsPerComponent 8 /Filter /DCTDecode", jpeg_garbage)])))
    write("absurd_image_size", _pdf(_page_objects(
        page="/Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
             "/Resources << /XObject << /Im1 5 0 R >> >>",
        content=b"q 300 0 0 300 72 400 cm /Im1 Do Q",
        extra=[_stream("/Type /XObject /Subtype /Image /Width 2000000 /Height 2000000 /ColorSpace /DeviceGray "
                       "/BitsPerComponent 8 /Filter /FlateDecode", zlib.compress(b"\x00" * 1000))])))
    write("broken_font", _pdf([
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        _stream("", b"BT /F1 24 Tf 72 700 Td (Hello) Tj ET"),
        "<< /Type /Font /Subtype /TrueType /BaseFont /Broken /FirstChar 32 /LastChar 126 "
        "/Widths [600] /FontDescriptor 6 0 R >>",
        "<< /Type /FontDescriptor /FontName /Broken /Flags 32 /FontBBox [0 0 1000 1000] /ItalicAngle 0 "
        "/Ascent 800 /Descent -200 /CapHeight 700 /StemV 80 /FontFile2 7 0 R >>",
        _stream("", b"not a truetype font"),
    ]))
    write("self_drawing_form", _pdf(_page_objects(
        page="/Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
             "/Resources << /XObject << /Fm1 5 0 R >> >>",
        content=b"/Fm1 Do",
        extra=[_stream("/Type /XObject /Subtype /Form /BBox [0 0 100 100] "
                       "/Resources << /XObject << /Fm1 5 0 R >> >>", b"/Fm1 Do")])))
    write("zero_pages", _pdf(["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [] /Count 0 >>"]))
    write("huge_page", _pdf(_page_objects(page="/Type /Page /Parent 2 0 R /MediaBox [0 0 14400 14400] "
                                               "/Contents 4 0 R")))
    write("zero_size_page", _pdf(_page_objects(page="/Type /Page /Parent 2 0 R /MediaBox [0 0 0 0] "
                                                    "/Contents 4 0 R")))
    write("odd_rotation", _pdf(_page_objects(page="/Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                                                  "/Rotate 45 /Contents 4 0 R")))
    write("inverted_mediabox", _pdf(_page_objects(page="/Type /Page /Parent 2 0 R /MediaBox [612 792 0 0] "
                                                       "/Contents 4 0 R")))
    write("javascript_on_open", _pdf(["<< /Type /Catalog /Pages 2 0 R /OpenAction 5 0 R >>",
                                      *_page_objects()[1:], "<< /S /JavaScript /JS (app.alert\\(1\\)) >>"]))
    # Encrypted with a password the tools aren't given.
    from pypdf import PdfWriter

    writer = PdfWriter(clone_from=str(folder / "_reference.pdf"))
    writer.encrypt("unknown-password", "owner-secret", algorithm="AES-256")
    writer.write(folder / "encrypted_unknown_password.pdf")
    files["encrypted_unknown_password"] = folder / "encrypted_unknown_password.pdf"
    # Many pages (blank, so it's quick to make).
    many = PdfWriter()
    for _ in range(300):
        many.add_blank_page(612, 792)
    many.write(folder / "many_pages.pdf")
    files["many_pages"] = folder / "many_pages.pdf"
    return files


NAMES = ["empty", "not_a_pdf", "truncated", "xref_points_nowhere", "startxref_wrong", "missing_content_object",
         "page_tree_loop", "bad_flate_stream", "broken_image", "absurd_image_size", "broken_font",
         "self_drawing_form", "zero_pages", "huge_page", "zero_size_page", "odd_rotation", "inverted_mediabox",
         "javascript_on_open", "encrypted_unknown_password", "many_pages"]
