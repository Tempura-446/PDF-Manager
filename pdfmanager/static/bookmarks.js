// Bookmark editor for the "Bookmarks" tool.
//
//   const editor = new BookmarkEditor(host, { load(detect) -> {items, pages}, onChange(items) });
//   await editor.open(fileKey)   // loads the file's bookmarks (once per file)
//
// Items are {title, page (1-based), level (0 = top)}. Every change is
// reported with onChange, which the page writes into the tool's hidden
// "outline" field as JSON.

const ICON = {
  up: '<path d="m18 15-6-6-6 6"/>', down: '<path d="m6 9 6 6 6-6"/>',
  left: '<path d="m15 18-6-6 6-6"/>', right: '<path d="m9 18 6-6-6-6"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>', plus: '<path d="M12 5v14M5 12h14"/>',
  wand: '<path d="m15 4 5 5L9 20H4v-5z"/><path d="M13 6l5 5"/>',
  reset: '<path d="M4 11a8 8 0 1 1 2.3 5.7"/><path d="M4 4v7h7"/>',
};
function icon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", "i"); s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICON[name];
  return s;
}
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

export class BookmarkEditor {
  constructor(host, { load, onChange }) {
    this.load = load;
    this.onChange = onChange || (() => {});
    this.items = []; this.pages = 0; this.key = null; this.original = [];
    const button = (label, name, fn, title) => el("button", { type: "button", class: "btn secondary pb-small", title, onclick: fn }, icon(name), label);
    this.status = el("span", { class: "pb-status" });
    this.detectBtn = button("Detect from headings", "wand", () => this.#fetch(true), "Suggest bookmarks from the document's headings (replaces the list)");
    this.toolbar = el("div", { class: "pb-toolbar" }, this.status,
      el("div", { class: "pb-tools" }, this.detectBtn,
        button("Add", "plus", () => this.#add(), "Add a bookmark at the end"),
        button("Undo changes", "reset", () => this.#set(structuredClone(this.original)), "Go back to the file's own bookmarks")));
    this.list = el("ol", { class: "bm-list", "aria-label": "Bookmarks" });
    this.message = el("div", { class: "pb-message" });
    this.root = el("div", { class: "bm-editor" }, this.toolbar, this.list, this.message);
    host.append(this.root);
    this.#render();
  }

  /** Load the bookmarks of a file (identified by ``key``) unless already shown. */
  async open(key) {
    if (this.key === key) return;
    this.key = key;
    await this.#fetch(false);
  }

  reset() { this.key = null; this.items = []; this.original = []; this.pages = 0; this.#render(); }

  async #fetch(detect) {
    const key = this.key;
    this.message.textContent = detect ? "Looking for headings…" : "Loading bookmarks…";
    this.message.hidden = false;
    this.detectBtn.disabled = true;
    try {
      const data = await this.load(detect);
      if (key !== this.key) return;
      this.pages = data.pages;
      if (!detect) this.original = structuredClone(data.items);
      this.#say("");
      this.#set(data.items);
      if (detect && !data.items.length) this.#say("No headings found. If this is a scan, run “Make searchable (OCR)” first.");
    } catch (e) {
      if (key === this.key) this.#say(e.message.includes("encrypted") || e.message.includes("password")
        ? "Enter the PDF's password under Options to see its bookmarks." : `Couldn't read the bookmarks: ${e.message}`);
    } finally { this.detectBtn.disabled = false; }
  }

  #say(text) { this.message.textContent = text; this.message.hidden = !text; }

  #set(items) { this.items = items; this.#render(); this.onChange(this.items); }

  #changed() { this.#render(); this.onChange(this.items); }

  #add() {
    const last = this.items[this.items.length - 1];
    this.items.push({ title: "", page: last ? last.page : 1, level: last ? last.level : 0 });
    this.#changed();
    const inputs = this.list.querySelectorAll(".bm-title");
    inputs[inputs.length - 1]?.focus();
  }

  /** Move item i (with its children) up or down past its neighbour block. */
  #move(i, dir) {
    const block = (k) => { let e = k + 1; while (e < this.items.length && this.items[e].level > this.items[k].level) e++; return e; };
    const end = block(i), mine = this.items.slice(i, end);
    if (dir < 0) {
      let p = i - 1;
      while (p > 0 && this.items[p].level > this.items[i].level) p--;
      if (p < 0 || i === 0) return;
      this.items.splice(i, mine.length);
      this.items.splice(p, 0, ...mine);
      this.#changed();
      this.#focus(p, ".bm-up");
    } else {
      if (end >= this.items.length) return;
      const nextEnd = block(end);
      this.items.splice(i, mine.length);
      const at = nextEnd - mine.length;
      this.items.splice(at, 0, ...mine);
      this.#changed();
      this.#focus(at, ".bm-down");
    }
  }

  #indent(i, delta) {
    const item = this.items[i];
    const max = i === 0 ? 0 : this.items[i - 1].level + 1;
    const level = Math.max(0, Math.min(max, item.level + delta));
    if (level === item.level) return;
    const diff = level - item.level;
    // Children move along with their parent.
    let e = i + 1;
    while (e < this.items.length && this.items[e].level > item.level) e++;
    for (let k = i; k < e; k++) this.items[k].level += diff;
    this.#changed();
    this.#focus(i, delta < 0 ? ".bm-out" : ".bm-in");
  }

  #remove(i) {
    const level = this.items[i].level;
    this.items.splice(i, 1);
    // Promote the children of a removed bookmark one level.
    for (let k = i; k < this.items.length && this.items[k].level > level; k++) this.items[k].level--;
    this.#changed();
    this.#focus(Math.min(i, this.items.length - 1), ".bm-title");
  }

  #focus(i, selector) { this.list.children[i]?.querySelector(selector)?.focus(); }

  #render() {
    const rows = this.items.map((item, i) => {
      const title = el("input", { class: "bm-title", type: "text", value: item.title, placeholder: "Bookmark title", "aria-label": `Title of bookmark ${i + 1}`, spellcheck: "true" });
      title.addEventListener("input", () => { item.title = title.value; this.onChange(this.items); });
      const page = el("input", { class: "bm-page", type: "number", min: 1, max: this.pages || null, value: item.page, "aria-label": `Page of bookmark ${i + 1}` });
      page.addEventListener("input", () => { item.page = Number(page.value) || 1; this.onChange(this.items); });
      const b = (cls, name, label, fn, disabled) => el("button", { type: "button", class: "chip-btn " + cls, title: label, "aria-label": label, disabled, onclick: fn }, icon(name));
      return el("li", { class: "bm-row", style: `--level: ${item.level}` },
        title, el("label", { class: "bm-page-wrap" }, "p.", page),
        el("div", { class: "bm-actions" },
          b("bm-out", "left", "Move out a level", () => this.#indent(i, -1), item.level === 0),
          b("bm-in", "right", "Move in a level (under the one above)", () => this.#indent(i, 1), i === 0 || item.level > this.items[i - 1].level),
          b("bm-up", "up", "Move up", () => this.#move(i, -1), i === 0),
          b("bm-down", "down", "Move down", () => this.#move(i, 1), i === this.items.length - 1),
          b("bm-x", "x", "Remove", () => this.#remove(i), false)));
    });
    this.list.replaceChildren(...rows);
    const n = this.items.length;
    this.status.textContent = this.key ? `${n} bookmark${n === 1 ? "" : "s"}${this.pages ? ` · ${this.pages} pages` : ""}` : "";
    this.toolbar.hidden = !this.key;
    const empty = "No bookmarks yet. Detect them from the headings, or add your own.";
    if (this.key && !n && !this.message.textContent) this.#say(empty);
    else if (n && this.message.textContent === empty) this.#say("");
  }
}
