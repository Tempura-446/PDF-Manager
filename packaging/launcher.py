"""Entry point for the standalone app built by build_app.py.

``"PDF Manager" --self-test RESULT_FILE`` checks that the bundle is complete
(PDF reading and writing, rendering, compression, the window library and
the built-in server) without opening a window, writes "ok" or the error to
RESULT_FILE and exits with 0 or 1. The build workflow runs it on every build.
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

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "test.pdf"
            c = canvas.Canvas(str(src))
            c.drawString(72, 720, "PDF Manager self-test")
            c.showPage()
            c.save()
            info = gui.inspect_pdf(src)
            assert info["pages"] == 1 and info["thumb"], f"inspect failed: {info}"
            core.compress(src, Path(tmp) / "small.pdf", level="high")
            core.convert_color(src, Path(tmp) / "gray.pdf", "gray")

        server, url = gui.make_server()
        Thread(target=server.serve_forever, daemon=True).start()
        base = url.split("#")[0]
        page = urllib.request.urlopen(base).read()
        # The reader and its bundled pdf.js must be in the bundle too.
        for asset in ("static/reader.js", "static/pdfjs/build/pdf.min.mjs",
                      "static/pdfjs/build/pdf.worker.min.mjs", "static/pdfjs/web/pdf_viewer.mjs"):
            assert urllib.request.urlopen(base + asset).status == 200, f"missing {asset}"
        server.shutdown()
        assert b"PDF Manager" in page, "server did not serve the app page"
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
