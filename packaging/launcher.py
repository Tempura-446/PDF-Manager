# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Entry point for the standalone app built by build_app.py.

``"ZPDF" --self-test RESULT_FILE`` checks that the bundle is complete
(PDF reading and writing, rendering, compression, signing, tables, form
fields, the PDF/A check, OCR's built-in engine, the window library and the
built-in server)
without opening a window, writes "ok" or the error to RESULT_FILE and exits
with 0 or 1. The build workflow runs it on every build.
"""
import multiprocessing
import sys


def self_test(result_file):
    import os
    import tempfile
    import traceback
    import urllib.request
    from pathlib import Path
    from threading import Thread

    try:
        import importlib

        from reportlab.pdfgen import canvas

        # The window library and its platform backend (WebView2 on Windows,
        # Cocoa on macOS, Qt/GTK on Linux) must be bundled. Qt needs no
        # display for this check.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        importlib.import_module("webview.guilib").initialize()

        from pdfmanager import core, gui

        def pdfacheck_builtin(path):
            from pdfmanager import pdfacheck

            return pdfacheck.check(path, level="2b", engine="builtin")

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "test.pdf"
            c = canvas.Canvas(str(src))
            c.drawString(72, 720, "ZPDF self-test")
            c.showPage()
            c.save()
            info = gui.inspect_pdf(src)
            assert info["pages"] == 1 and info["thumb"], f"inspect failed: {info}"
            core.compress(src, Path(tmp) / "small.pdf", level="high")
            # The scan compression engine (NumPy, and Pillow writing Group 4).
            from PIL import Image, ImageDraw

            sheet = Image.new("RGB", (1240, 1754), (250, 248, 240))
            draw = ImageDraw.Draw(sheet)
            for row in range(12):
                draw.text((120, 150 + row * 60), "ZPDF self-test: a scanned page of text", fill=(20, 20, 20))
                draw.line((120, 180 + row * 60, 900, 180 + row * 60), fill=(20, 20, 20), width=3)
            sheet.save(Path(tmp) / "sheet.jpg", quality=85, dpi=(150, 150))
            scanned = core.images_to_pdf([Path(tmp) / "sheet.jpg"], Path(tmp) / "scan.pdf")
            sharp = []
            core.compress(scanned, Path(tmp) / "scan-small.pdf", level="high", notes=sharp)
            assert sharp and b"/CCITTFaxDecode" in (Path(tmp) / "scan-small.pdf").read_bytes(), \
                f"scan compression not used: {sharp}"
            # The built-in PDF/A engine, on that scan and on a page of a standard font
            # (embedded from Windows' own Arial).
            from pdfmanager import archive

            for name in ("scan-small.pdf", "test.pdf"):
                made, how = archive.to_pdfa(Path(tmp) / name, Path(tmp) / ("a-" + name), engine="builtin",
                                            with_engine=True)
                again = pdfacheck_builtin(made)
                assert how == "builtin" and again["passed"], f"PDF/A of {name} failed: {again['issues']}"
            core.convert_color(src, Path(tmp) / "gray.pdf", "gray")
            # The libraries the later tools need: pyHanko (signing), pdfplumber
            # and openpyxl (PDF -> Excel), pikepdf (the PDF/A check), pypdfium2
            # and Pillow (finding form fields).
            importlib.import_module("pyhanko.sign.signers")
            importlib.import_module("pyhanko.sign.validation")
            from pdfmanager import certsign, formdetect, forms, pdfacheck, tables

            assert certsign and tables.find_tables(src) == [], "table finding failed"
            importlib.import_module("openpyxl").Workbook()
            form = Path(tmp) / "form.pdf"
            c = canvas.Canvas(str(form))
            c.drawString(72, 700, "Name:")
            c.line(120, 698, 400, 698)
            c.rect(72, 650, 10, 10)
            c.drawString(88, 651, "I agree")
            c.save()
            found = formdetect.detect(form)
            assert len(found.get(1, [])) == 2, f"form fields not found: {found}"
            forms.add_fields(form, Path(tmp) / "fillable.pdf", [{**f, "page": 1} for f in found[1]])
            checked = pdfacheck.check(src, engine="builtin")
            assert not checked["passed"] and checked["issues"], "PDF/A check failed"
            # 0.13.0: tagging for screen readers, PowerPoint and HTML, repair.
            from pdfmanager import accessibility, filetools, slides, webpage

            accessibility.autotag(src, Path(tmp) / "tagged.pdf")
            assert accessibility.check(Path(tmp) / "tagged.pdf"), "accessibility check failed"
            slides.pdf_to_pptx(src, Path(tmp) / "test.pptx")
            webpage.pdf_to_html(src, Path(tmp) / "test.html")
            filetools.repair(src, Path(tmp) / "repaired.pdf")
            for name in ("test.pptx", "test.html", "repaired.pdf"):
                assert (Path(tmp) / name).stat().st_size, f"{name} is empty"
            # 0.14.0: OCR's built-in engine, with its models and ONNX Runtime's
            # libraries, reads a scan of the test text.
            from PIL import Image, ImageDraw, ImageFont

            picture = Image.new("L", (600, 200), 255)
            try:
                font = ImageFont.truetype("DejaVuSans.ttf", 48)
            except OSError:
                try:
                    font = ImageFont.load_default(size=48)
                except TypeError:  # Pillow before 10.1 has only the one small size
                    font = ImageFont.load_default()
            ImageDraw.Draw(picture).text((40, 70), "ZPDF self-test", font=font, fill=0)
            picture.save(Path(tmp) / "scan.png", dpi=(300, 300))
            scanned = core.images_to_pdf([Path(tmp) / "scan.png"], Path(tmp) / "scan.pdf")
            core.ocr(scanned, Path(tmp) / "ocr.pdf", engine="builtin")
            assert "ZPDF" in core.extract_text(Path(tmp) / "ocr.pdf"), "OCR read nothing from the scan"
            # Language models are fetched from the addresses in rapidocr's own
            # list, which must be in the bundle.
            from pdfmanager import ocr

            assert ocr._model_source("korean")[0].endswith(".onnx"), "rapidocr's model list is missing"

        server, url = gui.make_server()
        Thread(target=server.serve_forever, daemon=True).start()
        base = url.split("#")[0]
        page = urllib.request.urlopen(base).read()
        # The reader and its bundled pdf.js must be in the bundle too.
        for asset in ("static/reader.js", "static/pdfjs.js", "static/pages.js", "static/regions.js",
                      "static/bookmarks.js", "static/recipes.js", "static/files.js", "static/measure.js", "static/snap.js",
                      "static/overlay.js", "static/tabstate.js", "static/readerkit.js", "static/measuring.js", "static/textedit-ui.js",
                      "static/annotate.js", "static/sign.js", "static/find.js", "static/tabs.js",
                      "static/pdfjs/build/pdf.min.mjs",
                      "static/pdfjs/build/pdf.sandbox.min.mjs", "static/pdfjs/wasm/quickjs-eval.wasm",
                      "static/pdfjs/build/pdf.worker.min.mjs", "static/pdfjs/web/pdf_viewer.mjs"):
            assert urllib.request.urlopen(base + asset).status == 200, f"missing {asset}"
        server.shutdown()
        assert b"ZPDF" in page, "server did not serve the app page"
        Path(result_file).write_text("ok")
        return 0
    except Exception:
        Path(result_file).write_text(traceback.format_exc())
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        sys.exit(self_test(sys.argv[2]))
    from pdfmanager.gui import main_app

    main_app()
