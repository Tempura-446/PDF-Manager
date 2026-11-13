// The reader's find bar: words in the document (pdf.js's find controller
// highlights them), the count, case and whole words; one tool row at a time
// with the annotate bar. A part of Reader (see Finding below).

import { el, svg, viewer } from "./readerkit.js";

/** Reader's find bar, as a class it extends (see Measuring in measuring.js). */
export const Finding = (Base) => class extends Base {
  /** Make the find bar (this.findbar and its parts), from #build. */
  _buildFind() {
    const b = (name, title, attrs = {}) => el("button", { class: "rd-btn", type: "button", title, "aria-label": title, html: svg(name), ...attrs });
    // Find bar
    this.findInput = el("input", { type: "search", placeholder: "Find in document", "aria-label": "Find in document", autocomplete: "off", spellcheck: "false" });
    this.findPrevBtn = b("up", "Previous match (Shift+Enter)");
    this.findNextBtn = b("down", "Next match (Enter)");
    this.findCase = el("input", { type: "checkbox" });
    this.findWord = el("input", { type: "checkbox" });
    this.findAll = el("input", { type: "checkbox", checked: true });
    this.findStatus = el("span", { class: "rd-findstatus", "aria-live": "polite" });
    this.findCloseBtn = b("x", "Close find bar (Esc)");
    this.findbar = el("div", { class: "rd-findbar", hidden: true },
      this.findInput, this.findPrevBtn, this.findNextBtn,
      el("label", {}, this.findAll, "Highlight all"),
      el("label", {}, this.findCase, "Match case"),
      el("label", {}, this.findWord, "Whole words"),
      this.findStatus, el("span", { style: "flex:1" }), this.findCloseBtn);
  }

  /** What its parts do, from #wire. */
  _wireFind() {
    this.findInput.addEventListener("input", () => this._find(""));
    this.findInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); this._find("again", e.shiftKey); }
      if (e.key === "Escape") { e.preventDefault(); this.closeFind(); }
    });
    this.findNextBtn.addEventListener("click", () => this._find("again", false));
    this.findPrevBtn.addEventListener("click", () => this._find("again", true));
    this.findCase.addEventListener("change", () => this._find("casesensitivitychange"));
    this.findWord.addEventListener("change", () => this._find("entirewordchange"));
    this.findAll.addEventListener("change", () => this._find("highlightallchange"));
    this.findCloseBtn.addEventListener("click", () => this.closeFind());
  }

  // ---- find -----------------------------------------------------------------
  openFind() {
    // One tool row at a time: the annotate bar gives way (what was drawn
    // stays, unsaved changes too) and comes back when find closes.
    // (The find bar shows first, so the file panel stays aside meanwhile.)
    const wasHidden = this.findbar.hidden;
    this.findbar.hidden = false;
    if (!this.annotbar.hidden) {
      const mode = this.editMode;
      this.gaveWay = null;
      this.closeAnnotate();
      this.gaveWay = { bar: "annotate", mode };
    } else if (wasHidden) this.gaveWay = null;
    this._endHint();
    this._stepAside();
    this.findBtn.setAttribute("aria-pressed", "true");
    const selected = String(window.getSelection() || "").trim();
    if (selected && selected.length < 100 && !selected.includes("\n")) this.findInput.value = selected;
    this.findInput.focus();
    this.findInput.select();
    if (this.findInput.value) this._find("");
  }
  closeFind() {
    if (this.findbar.hidden) return;
    const back = this.gaveWay?.bar === "annotate" && this.doc ? this.gaveWay : null;
    this.gaveWay = null;
    this.findbar.hidden = true;
    this.findBtn.setAttribute("aria-pressed", "false");
    if (back) {  // the annotate bar it opened over, with its tool
      this.openAnnotate();
      if (back.mode && back.mode !== "NONE") this._setEditMode(back.mode);
    }
    this._stepAside();
    if (this.eventBus) this.eventBus.dispatch("findbarclose", { source: this });
    this.findBtn.focus({ preventScroll: true });  // back where it was opened from
  }
  _find(type, previous = false) {
    if (!this.eventBus) return;
    this.eventBus.dispatch("find", {
      source: this, type, query: this.findInput.value,
      caseSensitive: this.findCase.checked, entireWord: this.findWord.checked,
      highlightAll: this.findAll.checked, findPrevious: previous, matchDiacritics: false,
    });
  }
  _findResult(state, matchesCount) {
    const { FindState } = viewer;
    const query = this.findInput.value;
    this.findInput.classList.toggle("notfound", state === FindState.NOT_FOUND && !!query);
    if (!query) { this.findStatus.textContent = ""; return; }
    if (state === FindState.NOT_FOUND) { this.findStatus.textContent = "No matches"; return; }
    if (state === FindState.PENDING) { this.findStatus.textContent = "Searching…"; return; }
    const { current = 0, total = 0 } = matchesCount || {};
    if (total) this.findStatus.textContent = `${current || "–"} of ${total}${total >= 1000 ? "+" : ""}`;
    if (state === FindState.WRAPPED) this.toast("Reached the end — continued from the other end", 1500);
  }
};
