# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Priority #5 Phase 1: Side-by-side split view in the reader (static/splitview.js)."""
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from pdfmanager import gui

STATIC = Path(gui.__file__).resolve().parent / "static"


def _make_pdf(path, title, pages=4):
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    for i in range(1, pages + 1):
        c.setFont("Helvetica-Bold", 24)
        c.drawString(72, 700, f"{title} — Page {i}")
        c.showPage()
    c.save()
    return path


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = url.split("#", 1)[0].rstrip("/")
    token = srv.RequestHandlerClass.token
    yield srv, base, token
    srv.shutdown()
    srv.server_close()


def test_splitview_module_and_css_wired(app):
    _, base, _ = app
    with urllib.request.urlopen(base + "/static/splitview.js") as res:
        assert res.status == 200
        js = res.read().decode("utf-8")
    assert "export const SplitView" in js
    assert "toggleSplitView()" in js
    assert "lockSplitOffset()" in js
    assert "switchSplitPane()" in js

    reader_js = (STATIC / "reader.js").read_text(encoding="utf-8")
    assert 'import { SplitView } from "./splitview.js";' in reader_js
    assert "this._buildSplitView();" in reader_js
    assert "Split view (side by side)" in reader_js

    css = (STATIC / "reader.css").read_text(encoding="utf-8")
    assert ".rd-split-divider" in css
    assert ".rd-split-pane" in css
    assert "@media (forced-colors: active)" in css


def test_splitview_in_a_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")

    srv, base, token = app
    opened = srv.RequestHandlerClass.opened
    doc_a = _make_pdf(tmp_path / "DocA.pdf", "Alpha", pages=5)
    doc_b = _make_pdf(tmp_path / "DocB.pdf", "Beta", pages=5)
    id_a = opened.add(doc_a)
    id_b = opened.add(doc_b)

    script = tmp_path / "split_check.js"
    script.write_text(r"""
const { chromium } = require("playwright");
(async () => {
  const [base, token, idA, idB] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  await page.goto(base + "/#token=" + encodeURIComponent(token) + "&open=" + encodeURIComponent(idA));
  await page.waitForFunction(() => typeof reader !== "undefined" && reader?.doc?.numPages === 5 && reader.loading.hidden, null, { timeout: 30000 });

  // Open second tab, then switch back to first tab.
  await page.evaluate(async (id) => {
    await reader.openById(id);
    await reader.showTab(reader.tabs[0]);
  }, idB);
  await page.waitForFunction(() => reader?.activeTab?.name === "DocA.pdf" && reader.loading.hidden, null, { timeout: 20000 });

  // Toggle split view via Ctrl+\
  await page.keyboard.press("Control+Backslash");
  await page.waitForFunction(() => reader?.splitOpen && reader?.splitDoc?.numPages === 5, null, { timeout: 20000 });

  const initial = await page.evaluate(() => ({
    splitOpen: reader.splitOpen,
    rightHidden: reader.splitPane.hidden,
    dividerHidden: reader.splitDivider.hidden,
    rightTitle: reader.splitTitle.textContent,
    syncChecked: reader.splitSync.checked,
  }));
  if (!initial.splitOpen || initial.rightHidden || initial.dividerHidden) {
    throw new Error("Split view did not open: " + JSON.stringify(initial));
  }
  if (!initial.rightTitle.includes("DocB.pdf")) {
    throw new Error("Expected right pane to open DocB.pdf, got: " + initial.rightTitle);
  }

  // Wait for right pane layout and test synchronized scrolling.
  await page.waitForFunction(() => reader.splitContainer.scrollHeight > reader.splitContainer.clientHeight, null, { timeout: 15000 });
  const syncedScroll = await page.evaluate(async () => {
    reader.container.scrollTop = 900;
    await new Promise((res) => setTimeout(res, 250));
    return reader.splitContainer.scrollTop;
  });
  if (!(syncedScroll > 100)) {
    throw new Error("Expected right pane to track scroll, got scrollTop=" + syncedScroll);
  }

  // Switch active pane with F6
  await page.keyboard.press("F6");
  const activeAfterF6 = await page.evaluate(() => reader._splitActivePane);
  if (activeAfterF6 !== "right") {
    throw new Error("Expected F6 to focus right pane, got: " + activeAfterF6);
  }

  // Switch right pane to same-document mode
  await page.evaluate(async () => {
    reader.splitSource.value = "__same__";
    reader.splitSource.dispatchEvent(new Event("change"));
  });
  await page.waitForFunction(() => reader.splitTitle.textContent.includes("2nd view"), null, { timeout: 15000 });

  // Close split view and verify left document remains intact
  await page.evaluate(() => reader.closeSplitView());
  const afterClose = await page.evaluate(() => ({
    splitOpen: reader.splitOpen,
    rightHidden: reader.splitPane.hidden,
    leftPages: reader.doc?.numPages,
    leftName: reader.activeTab?.name,
  }));
  if (afterClose.splitOpen || !afterClose.rightHidden || afterClose.leftPages !== 5 || afterClose.leftName !== "DocA.pdf") {
    throw new Error("Unexpected state after closeSplitView: " + JSON.stringify(afterClose));
  }

  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
""", encoding="utf-8")

    proc = subprocess.run([node, str(script), base, token, id_a, id_b], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr or proc.stdout
