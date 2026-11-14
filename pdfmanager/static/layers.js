// The reader's Layers tab: the layers (optional content groups) of a drawing
// exported from CAD, in the file's own nested order, each with a box to show
// or hide it. Locked layers can't be switched, and in a radio group only one
// layer is on. What shows is pdf.js's OptionalContentConfig (the viewer's
// own: toggling a layer redraws the pages), and the rest of the reader
// follows it: the thumbnails, snapping when measuring (snap.js leaves out
// the lines of hidden layers), the text layer (a hidden layer's words can't
// be selected) and printing. "Save layer visibility" writes the choice into
// the file (the way it opens), and "Flatten layers" bakes the shown layers
// in and removes the hidden ones' content; both are changes like any other
// (Undo, the unsaved dot, Save). Each tab keeps its own choice of layers.
//
// The nested order and the locked layers come from the server (layers.py):
// pdf.js reads neither /Locked nor an /Order where layers nest under a
// layer. Without the server, the order is pdf.js's and nothing is locked.

import { el, pdfjs } from "./readerkit.js";
import { markedContent } from "./snap.js";

const LOCK = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';


/** Reader's layers, as a class it extends (see Measuring in measuring.js). */
export const Layering = (Base) => class extends Base {
  _buildLayers() {
    this.layersTab = el("button", { class: "rd-tab rd-layerstab", type: "button", role: "tab", "aria-selected": "false", hidden: true }, "Layers");
    this.layersList = el("ul", { class: "rd-layers", "aria-label": "Layers" });
    const action = (label, title, fn) => el("button", { type: "button", class: "rd-linkbtn", title, onclick: fn }, label);
    this.layersShowAll = action("Show all", "Show every layer that can be switched", () => this._layersAll(true));
    this.layersHideAll = action("Hide all", "Hide every layer that can be switched (a radio group keeps one on)", () => this._layersAll(false));
    this.layersSave = el("button", { type: "button", class: "rd-btn", title: "Make the file open with these layers shown and hidden, in ZPDF, Acrobat and other readers",
      onclick: () => this._saveLayerVisibility() }, "Save layer visibility");
    this.layersFlatten = el("button", { type: "button", class: "rd-btn", title: "Make the layers shown part of the page and remove the hidden ones' content, for sending a clean set",
      onclick: () => this._flattenLayers() }, "Flatten layers…");
    this.layersPanel = el("div", { class: "rd-panel rd-layerspanel", role: "tabpanel", hidden: true, "aria-label": "Layers" },
      el("div", { class: "rd-commentactions" }, this.layersShowAll, this.layersHideAll),
      this.layersList,
      el("div", { class: "rd-layeractions" }, this.layersSave, this.layersFlatten));
    this.layersTab.addEventListener("click", () => { this.layersWanted = true; this._showSidebarTab("layers"); });
    // (Layers picked stays picked: a tab without layers shows Pages, and the next with layers shows Layers again.)
    for (const other of [this.thumbsTab, this.outlineTab, this.commentsTab]) other?.addEventListener("click", () => { this.layersWanted = false; });
    this.layersList.addEventListener("change", (e) => {
      const input = e.target.closest?.("input[data-layer]");
      if (input) this._toggleLayer(input.dataset.layer, input.checked);
    });
    this.layers = null;  // { doc, config, tree, locked, rb, traces }
  }

  /** The layers of the document just shown: the tab appears when it has any. */
  async _loadLayers() {
    const doc = this.doc, tab = this.activeTab;
    this.layers = null;
    if (!doc || !tab) return this.#setLayersTab(false);
    let config = null;
    try {
      await this.pdfViewer.firstPagePromise;
      if (doc !== this.doc) return;
      config = await this.pdfViewer.optionalContentConfigPromise;
    } catch (e) { config = null; }
    if (doc !== this.doc) return;
    if (!config || !config.getOrder()) return this.#setLayersTab(false);
    const ids = [...config].map(([id]) => id);
    // This tab's own choice, from before it was switched away from (or changed).
    let changed = false;
    if (tab.layers) {
      ids.forEach((id, i) => {
        const want = tab.layers.get(`${i}:${config.getGroup(id).name}`);
        if (want !== undefined && want !== config.getGroup(id).visible) { config.setVisibility(id, want, false); changed = true; }
      });
    }
    const rb = new Map();  // layer -> its radio groups
    for (const id of ids) {
      const groups = config.getGroup(id).rbGroups || [];
      if (groups.length) rb.set(id, groups);
    }
    this.layers = { doc, tab, config, ids, rb, locked: new Set(), tree: this.#treeFromPdfjs(config.getOrder(), config), traces: new Map() };
    this.#wireTextLayers();
    this.#setLayersTab(true);
    this.#renderLayers();
    if (changed) this.#applyLayers();
    // The nested order and the locked layers, from the server.
    const found = await this.#layerInfo(tab);
    if (doc !== this.doc || !this.layers || !found?.layers?.length) return;
    if (!found.layers.every((l) => config.getGroup(l.id))) return;  // (not the same objects: keep pdf.js's)
    this.layers.locked = new Set(found.locked || []);
    this.layers.tree = found.order || this.layers.tree;
    this.#renderLayers();
  }

  _closeLayers() {
    this.layers = null;
    this.#setLayersTab(false);
    this.layersList?.replaceChildren();
  }

  /** Whether content marked with a pdf.js optional-content object shows,
   *  as a function (null: the document has no layers). */
  _layerVisibility() {
    const l = this.layers;
    if (!l || l.doc !== this.doc) return null;
    return (oc) => l.config.isVisible(oc);
  }

  /** The layers shown, for drawing a page outside the viewer (thumbnails);
   *  undefined: pdf.js's default. */
  _layerConfigPromise() {
    const l = this.layers;
    return l && l.doc === this.doc ? Promise.resolve(l.config) : undefined;
  }

  /** The layers shown, for printing (pdf.js wants a configuration made for
   *  printing: the one on screen, copied); undefined without layers. */
  _printLayers() {
    const l = this.layers, doc = this.doc;
    if (!l || l.doc !== doc) return undefined;
    return doc.getOptionalContentConfig({ intent: "print" }).then((print) => {
      for (const [id, group] of l.config) print.setVisibility(id, group.visible, false);
      return print;
    });
  }

  /** The ids of the layers hidden now. */
  _hiddenLayers() {
    const l = this.layers;
    return l ? l.ids.filter((id) => !l.config.getGroup(id).visible) : [];
  }

  _toggleLayer(id, on) {
    const l = this.layers;
    if (!l || l.locked.has(id) || !l.config.getGroup(id)) return this.#renderLayers();
    // A radio group keeps one layer on.
    if (!on && l.rb.has(id)) return this.#renderLayers();
    l.config.setVisibility(id, on, true);
    this.#applyLayers();
  }

  _layersAll(on) {
    const l = this.layers;
    if (!l) return;
    for (const id of l.ids) {
      if (l.locked.has(id)) continue;
      if (l.rb.has(id)) {
        // Show all: a radio group with none on gets its first layer on; Hide all leaves it.
        if (on && !l.rb.get(id).some((g) => [...g].some((m) => l.config.getGroup(m)?.visible))) l.config.setVisibility(id, true, true);
        continue;
      }
      l.config.setVisibility(id, on, false);
    }
    this.#applyLayers();
  }

  _saveLayerVisibility() {
    if (!this.layers) return;
    const hidden = this._hiddenLayers();
    return this._changeDocument((bytes, tab) => this._post("/api/layers/save", bytes, tab, { hidden: JSON.stringify(hidden) }), {
      failure: "Could not save the layers: ",
      after: () => this.toast("The file now opens with these layers. Save it to keep that.", 5000),
    });
  }

  async _flattenLayers() {
    if (!this.layers) return;
    const hidden = this._hiddenLayers();
    const names = hidden.map((id) => this.layers.config.getGroup(id)?.name || id);
    if (await this.ask("Flatten layers?",
      [names.length ? `The hidden layers (${names.join(", ")}) are removed for good; the ones shown become part of the page.`
        : "Every layer shows: they all become part of the page.",
      "The document has no layers after this. Undo takes it back."],
      [["cancel", "Cancel"], ["flatten", "Flatten layers", true]]) !== "flatten") return;
    return this._changeDocument((bytes, tab) => this._post("/api/layers/flatten", bytes, tab, { hidden: JSON.stringify(hidden) }), {
      failure: "Could not flatten the layers: ",
    });
  }

  /** The layers changed: the pages, thumbnails, snapping and text layer follow, and the tab keeps the choice. */
  #applyLayers() {
    const l = this.layers;
    if (!l || l.doc !== this.doc) return;
    l.tab.layers = new Map(l.ids.map((id, i) => [`${i}:${l.config.getGroup(id).name}`, l.config.getGroup(id).visible]));
    this.pdfViewer.optionalContentConfigPromise = Promise.resolve(l.config);
    this._redrawThumbs?.();
    const visible = this._layerVisibility();
    for (const g of l.tab.geometry?.values() || []) g?.setVisibility?.(visible);
    for (const n of this.#textLayerPages()) this.#hideText(n);
    this.#renderLayers();
  }

  #setLayersTab(on) {
    if (!this.layersTab) return;
    this.layersTab.hidden = !on;
    if (!on && !this.layersPanel.hidden) this._showSidebarTab("thumbs");
    if (on && this.layersWanted && this.layersPanel.hidden) this._showSidebarTab("layers");

  }

  #treeFromPdfjs(order, config) {
    const walk = (items) => items.map((item) => (typeof item === "string"
      ? { id: item, name: config.getGroup(item)?.name, children: [] }
      : { label: item.name, children: walk(item.order || []) }));
    const tree = walk(order || []);
    // (pdf.js puts layers missing from /Order under a heading without a name.)
    return tree.flatMap((n) => (n.label === null && !n.id ? n.children : [n]));
  }

  #renderLayers() {
    const l = this.layers;
    if (!l) { this.layersList.replaceChildren(); return; }
    const rbName = new Map();  // a radio group -> the name its inputs share
    const build = (nodes) => nodes.map((node) => {
      if (!node.id) {
        return el("li", { class: "rd-layerhead" }, el("span", { class: "rd-layerlabel" }, node.label || "Layers"),
          el("ul", {}, ...build(node.children || [])));
      }
      const group = l.config.getGroup(node.id);
      if (!group) return el("li", { hidden: true });
      const locked = l.locked.has(node.id), groups = l.rb.get(node.id) || [];
      let name = null;
      if (groups.length === 1) {
        if (!rbName.has(groups[0])) rbName.set(groups[0], `rd-rb-${rbName.size + 1}`);
        name = rbName.get(groups[0]);
      }
      const input = el("input", { type: name ? "radio" : "checkbox", name, "data-layer": node.id, checked: group.visible, disabled: locked });
      const label = el("label", { class: "rd-layer" + (locked ? " locked" : ""),
        title: locked ? "Locked in the file: it can't be switched" : groups.length ? "One of a group: showing it hides the others" : null },
      input, el("span", {}, group.name || node.name || "(unnamed layer)"),
      locked ? el("span", { class: "rd-layerlock", title: "Locked", html: LOCK }) : null);
      return el("li", {}, label, node.children?.length ? el("ul", {}, ...build(node.children)) : null);
    });
    this.layersList.replaceChildren(...build(l.tree));
    // (Radio inputs, and anything another change switched: as the configuration says.)
    for (const input of this.layersList.querySelectorAll("input[data-layer]")) input.checked = !!l.config.getGroup(input.dataset.layer)?.visible;
  }

  async #layerInfo(tab) {
    try {
      const body = new FormData();
      if (tab.fileId && !tab.edited) body.append("id", tab.fileId);
      else {
        const bytes = tab === this.activeTab ? await this._fullBytes() : tab.bytes;
        if (!bytes) return null;
        body.append("file", new Blob([bytes], { type: "application/pdf" }), "file.pdf");
      }
      if (tab.password) body.append("password", tab.password);
      const res = await fetch("/api/layers", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
      return res.ok ? await res.json() : null;
    } catch (e) {
      return null;
    }
  }

  // ---- the text layer: a hidden layer's words can't be selected ----------------------------
  // pdf.js's text layer has a span for each marked-content sequence but
  // doesn't say which are layers, so they are matched, in order, with the
  // sequences of the page's operator list (which does). When the counts
  // don't agree, the page's text layer is left as it is.

  #wireTextLayers() {
    if (this.layersWired || !this.eventBus) return;
    this.layersWired = true;
    this.eventBus.on("textlayerrendered", ({ pageNumber }) => { if (this.layers) this.#hideText(pageNumber); });
  }

  #textLayerPages() {
    return [...this.viewerEl.querySelectorAll(".page[data-page-number] .textLayer")]
      .map((t) => Number(t.closest(".page").dataset.pageNumber));
  }

  async #hideText(n) {
    const l = this.layers, doc = this.doc;
    if (!l || l.doc !== doc) return;
    let traces = l.traces.get(n);
    if (!traces) {
      try {
        const ops = await (await doc.getPage(n)).getOperatorList({ annotationMode: pdfjs.AnnotationMode.DISABLE });
        traces = [markedContent(ops, pdfjs.OPS, true), markedContent(ops, pdfjs.OPS, false)];
      } catch (e) { traces = [[], []]; }
      if (l !== this.layers) return;
      l.traces.set(n, traces);
      if (l.traces.size > 16) l.traces.delete(l.traces.keys().next().value);
    }
    const layer = this.viewerEl.querySelector(`.page[data-page-number="${n}"] .textLayer`);
    if (!layer) return;
    const spans = layer.querySelectorAll("span.markedContent");
    const seq = traces.find((t) => t.length === spans.length);
    if (!seq) return;
    spans.forEach((span, i) => span.classList.toggle("rd-oc-hidden", !seq[i].every((oc) => l.config.isVisible(oc))));
  }
};
