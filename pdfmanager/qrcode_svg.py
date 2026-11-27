# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Pure-Python QR code SVG renderer backed by ReportLab's built-in QR encoder.

Zero external dependencies or network calls. Produces crisp, scannable SVG
images for ZPDF's LAN "Connect Phone" wireless scanner and file-drop pairing.
"""

from reportlab.graphics.barcode.qr import QrCodeWidget


def make_qr_svg(text, *, scale=6, border=4, dark="#111827", light="#ffffff"):
    """Encode ``text`` as a QR code and return a standalone SVG string."""
    payload = str(text or "")
    if not payload:
        raise ValueError("QR code text cannot be empty")
    qr = QrCodeWidget(payload).qr
    qr.make()
    n = qr.getModuleCount()
    s = max(1, int(scale))
    b = max(0, int(border))
    dim = (n + 2 * b) * s

    parts = []
    for r in range(n):
        y = (r + b) * s
        for c in range(n):
            if qr.isDark(r, c):
                x = (c + b) * s
                parts.append(f"M{x},{y}h{s}v{s}h-{s}z")
    path_d = "".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" '
        f'width="{dim}" height="{dim}" shape-rendering="crispEdges" role="img" aria-label="QR Code">'
        f'<rect width="100%" height="100%" fill="{light}"/>'
        f'<path d="{path_d}" fill="{dark}"/>'
        f"</svg>"
    )
