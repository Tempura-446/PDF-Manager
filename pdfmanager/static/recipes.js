// Recipe builder for the "Recipes" tool: pick a saved recipe or build one
// step by step (each step is a tool with its options), save it by name.
//
//   const builder = new RecipeBuilder(host, { ops, api, onChange(steps, name) });
//   await builder.refresh()   // loads the saved recipes

const ICON = {
  up: '<path d="m18 15-6-6-6 6"/>', down: '<path d="m6 9 6 6 6-6"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>', plus: '<path d="M12 5v14M5 12h14"/>',
  save: '<path d="M5 3h11l5 5v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M7 3v6h8V3M7 21v-7h10v7"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
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

// Suggestions for an empty builder.
const STARTERS = [
  ["Scans: clean up, OCR, compress", [{ op: "clean", options: {} }, { op: "ocr", options: {} }, { op: "compress", options: { level: "medium" } }]],
  ["Ready to share: flatten, remove hidden data, compress", [{ op: "flatten", options: {} }, { op: "sanitize", options: {} }, { op: "compress", options: { level: "high" } }]],
  ["Archive: OCR, bookmarks from headings, PDF/A", [{ op: "ocr", options: {} }, { op: "bookmarks", options: { mode: "detect" } }, { op: "pdfa", options: {} }]],
];

let uid = 0;

export class RecipeBuilder {
  constructor(host, { ops, api, onChange }) {
    this.ops = ops;          // [{id, label, fields}] usable as steps
    this.api = api;          // {list(), save(name, steps), remove(name)}
    this.onChange = onChange || (() => {});
    this.saved = [];
    this.steps = [];
    this.name = "";

    this.picker = el("select", { class: "rc-picker", "aria-label": "Saved recipes" });
    this.picker.addEventListener("change", () => this.#pick(this.picker.value));
    this.nameInput = el("input", { type: "text", class: "rc-name", placeholder: "Recipe name", "aria-label": "Recipe name" });
    this.nameInput.addEventListener("input", () => { this.name = this.nameInput.value; this.#emit(); });
    this.saveBtn = el("button", { type: "button", class: "btn secondary pb-small", onclick: () => this.#save() }, icon("save"), "Save");
    this.deleteBtn = el("button", { type: "button", class: "btn secondary pb-small", onclick: () => this.#remove() }, icon("trash"), "Delete");
    this.list = el("ol", { class: "rc-steps", "aria-label": "Steps" });
    this.addSelect = el("select", { class: "rc-add", "aria-label": "Add a step" },
      el("option", { value: "" }, "Add a step…"), ...ops.map((op) => el("option", { value: op.id }, op.label)));
    this.addSelect.addEventListener("change", () => {
      if (!this.addSelect.value) return;
      this.steps.push({ op: this.addSelect.value, options: {}, key: ++uid });
      this.addSelect.value = "";
      this.#changed();
    });
    this.message = el("div", { class: "pb-message", "aria-live": "polite" });
    this.starters = el("div", { class: "rc-starters" });
    this.root = el("div", { class: "recipe-builder" },
      el("div", { class: "pb-toolbar" }, this.picker,
        el("div", { class: "pb-tools" }, this.nameInput, this.saveBtn, this.deleteBtn)),
      this.list, this.starters, el("div", { class: "rc-footer" }, this.addSelect), this.message);
    host.append(this.root);
    this.#render();
  }

  async refresh() {
    try { this.saved = (await this.api.list()).recipes; } catch (e) { this.saved = []; }
    this.#renderPicker();
  }

  /** Re-send the current recipe (the form is rebuilt when switching tools). */
  emit() { this.#emit(); }

  #renderPicker() {
    this.picker.replaceChildren(el("option", { value: "" }, this.saved.length ? "New recipe…" : "No saved recipes yet"),
      ...this.saved.map((r) => el("option", { value: r.name, selected: r.name === this.name }, r.name)));
    this.picker.disabled = !this.saved.length;
    this.deleteBtn.disabled = !this.saved.some((r) => r.name === this.name);
  }

  #pick(name) {
    const recipe = this.saved.find((r) => r.name === name);
    this.name = recipe ? recipe.name : "";
    this.nameInput.value = this.name;
    this.steps = recipe ? recipe.steps.map((s) => ({ op: s.op, options: { ...s.options }, key: ++uid })) : [];
    this.#say("");
    this.#changed();
  }

  async #save() {
    const name = this.nameInput.value.trim();
    if (!name) { this.#say("Give the recipe a name first."); this.nameInput.focus(); return; }
    if (!this.steps.length) { this.#say("Add at least one step."); return; }
    try {
      const data = await this.api.save(name, this.#plain());
      this.saved = data.recipes;
      this.name = data.recipe.name;
      this.#renderPicker();
      this.#say(`Saved “${this.name}”. Use it from the command line with: pdfm recipe run "${this.name}" files…`);
    } catch (e) { this.#say(e.message); }
  }

  async #remove() {
    if (!this.name || !confirm(`Delete the recipe “${this.name}”?`)) return;
    try {
      this.saved = (await this.api.remove(this.name)).recipes;
      this.#say(`Deleted “${this.name}”.`);
      this.name = ""; this.nameInput.value = "";
      this.#renderPicker();
    } catch (e) { this.#say(e.message); }
  }

  #say(text) { this.message.textContent = text; this.message.hidden = !text; }

  #plain() { return this.steps.map((s) => ({ op: s.op, options: { ...s.options } })); }

  #emit() { this.onChange(this.#plain(), this.name); }

  #changed() { this.#render(); this.#emit(); }

  #render() {
    this.#renderPicker();
    const ops = Object.fromEntries(this.ops.map((o) => [o.id, o]));
    this.list.replaceChildren(...this.steps.map((step, i) => {
      const op = ops[step.op];
      const b = (name, label, fn, disabled) => el("button", { type: "button", class: "chip-btn", title: label, "aria-label": label, disabled, onclick: fn }, icon(name));
      const fields = el("div", { class: "rc-fields" });
      this.#fields(step, op, fields);
      return el("li", { class: "rc-step" },
        el("div", { class: "rc-head" },
          el("span", { class: "rc-num" }, String(i + 1)), el("strong", {}, op ? op.label : step.op),
          el("div", { class: "rc-actions" },
            b("up", "Move up", () => { this.steps.splice(i - 1, 0, ...this.steps.splice(i, 1)); this.#changed(); }, i === 0),
            b("down", "Move down", () => { this.steps.splice(i + 1, 0, ...this.steps.splice(i, 1)); this.#changed(); }, i === this.steps.length - 1),
            b("x", "Remove step", () => { this.steps.splice(i, 1); this.#changed(); }, false))),
        fields.childElementCount ? fields : null);
    }));
    this.starters.replaceChildren();
    if (!this.steps.length) {
      this.starters.append(el("span", { class: "rc-starters-label" }, "Start from:"),
        ...STARTERS.map(([label, steps]) => el("button", { type: "button", class: "btn secondary pb-small",
          onclick: () => { this.steps = steps.map((s) => ({ op: s.op, options: { ...s.options }, key: ++uid })); this.#changed(); } }, label)));
    }
  }

  /** The options of one step, built from the tool's field list. */
  #fields(step, op, host) {
    if (!op) return;
    const visible = (f) => !f.show_if || Object.entries(f.show_if).every(([k, v]) => String(step.options[k] ?? op.fields.find((x) => x.key === k)?.default ?? "") === v);
    for (const f of op.fields) {
      if (f.kind === "hidden" || f.kind === "file" || f.kind === "password") continue;
      let choices = f.choices;
      if (op.id === "bookmarks" && f.key === "mode") {
        choices = f.choices.filter(([v]) => v !== "edit");  // a recipe has no list to edit
        if (!step.options.mode) step.options.mode = "detect";
      }
      const id = `rc-${step.key}-${f.key}`;
      const value = step.options[f.key] ?? (f.kind === "check" ? (f.default ? "true" : "false") : f.default ?? "");
      let input;
      if (f.kind === "select") {
        input = el("select", { id }, ...choices.map(([v, label]) => el("option", { value: v, selected: String(value) === v }, label)));
      } else if (f.kind === "check") {
        input = el("input", { id, type: "checkbox", checked: value === "true" || value === true });
      } else {
        input = el("input", { id, type: f.kind === "number" || f.kind === "range" ? "number" : "text", value, placeholder: f.placeholder || "",
          min: f.min, max: f.max, step: f.step || (f.kind === "number" ? "any" : null), spellcheck: "false" });
      }
      const update = () => {
        step.options[f.key] = f.kind === "check" ? String(input.checked) : input.value;
        if (op.fields.some((x) => x.show_if)) this.#changed(); else this.#emit();
      };
      input.addEventListener(f.kind === "select" || f.kind === "check" ? "change" : "input", update);
      if (!visible(f)) continue;
      host.append(f.kind === "check"
        ? el("div", { class: "field check" }, input, el("label", { for: id }, f.label))
        : el("div", { class: "field" }, el("label", { for: id }, f.label), input));
    }
  }
}
