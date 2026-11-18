/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Field properties panel in the right-hand file panel when fields are
// selected in Prepare form mode.

import { el } from "./readerkit.js";

const TYPE_LABELS = {
  text: "Text box",
  multiline: "Multi-line text",
  checkbox: "Checkbox",
  radio: "Radio button",
  dropdown: "Dropdown",
  list: "List box",
  signature: "Signature",
  button: "Button",
};

const STANDARD_FONTS = [
  "Helvetica", "Helvetica-Bold", "Helvetica-Oblique",
  "Times", "Times-Bold", "Times-Italic",
  "Courier", "Courier-Bold", "Courier-Oblique",
];

function shared(items, key) {
  if (!items.length) return { value: undefined, mixed: false };
  const first = items[0][key];
  for (let i = 1; i < items.length; i++) {
    if (items[i][key] !== first) return { value: undefined, mixed: true };
  }
  return { value: first, mixed: false };
}

function row(label, control) {
  return el("label", { class: "fp-prop-row" },
    el("span", { class: "fp-prop-lbl" }, label),
    control);
}

function checkRow(label, checked, mixed, onChange) {
  const cb = el("input", { type: "checkbox" });
  cb.checked = !!checked;
  cb.indeterminate = !!mixed;
  cb.addEventListener("change", () => onChange(cb.checked));
  return el("label", { class: "fp-prop-check" }, cb, el("span", {}, label));
}

function selectEl(options, value, mixed, onChange) {
  const s = el("select", { class: "fp-prop-in" });
  if (mixed) s.append(el("option", { value: "__mixed__", disabled: true, selected: true }, "— Mixed —"));
  for (const [val, lbl] of options) {
    const opt = el("option", { value: String(val) }, lbl);
    if (!mixed && String(val) === String(value ?? "")) opt.selected = true;
    s.append(opt);
  }
  s.addEventListener("change", () => {
    if (s.value !== "__mixed__") onChange(s.value);
  });
  return s;
}

function section(title, ...children) {
  const body = el("div", { class: "fp-prop-body" }, ...children);
  return el("details", { class: "fp-prop-sec", open: true },
    el("summary", { class: "fp-prop-head" }, title),
    body);
}

export function renderFormProps(host, selected, allPages, reader) {
  if (!selected?.length) {
    reader._formPickingFor = null;
    reader.setFormPickMode?.(null);
    host.replaceChildren();
    return;
  }
  const selKey = selected.length === 1 ? `${selected[0].page}:${selected[0].index}` : null;
  if (reader._formPickingFor && reader._formPickingFor !== selKey) {
    reader._formPickingFor = null;
    reader.setFormPickMode?.(null);
  }

  const multi = selected.length > 1;
  const first = selected[0];
  const types = new Set(selected.map((f) => f.type));
  const allText = [...types].every((t) => t === "text" || t === "multiline");
  const allChoice = [...types].every((t) => t === "dropdown" || t === "list");
  const allBtn = [...types].every((t) => t === "checkbox" || t === "radio");
  const allSig = [...types].every((t) => t === "signature");

  const apply = (patch, { keepNames = null } = {}) => {
    const changes = selected.map((f) => ({ page: f.page, index: f.index, ...patch }));
    reader.editFormFields(changes, keepNames ? { keepNames } : { keepSelection: true });
  };

  const headText = multi
    ? `${selected.length} fields selected`
    : (TYPE_LABELS[first.type] || first.type);

  const frag = document.createDocumentFragment();
  frag.append(el("div", { class: "fp-prop-title" },
    el("strong", {}, "Field properties"),
    el("span", { class: "fp-fact flag" }, headText)));

  // ---- General ----
  const gen = [];
  if (!multi) {
    const nameIn = el("input", { class: "fp-prop-in", type: "text", value: first.name || "" });
    nameIn.addEventListener("change", () => {
      const v = nameIn.value.trim();
      if (v && v !== first.name) apply({ name: v }, { keepNames: [v] });
    });
    gen.push(row("Name", nameIn));
  }
  const tip = shared(selected, "tooltip");
  const tipIn = el("input", {
    class: "fp-prop-in", type: "text",
    value: tip.mixed ? "" : (tip.value || ""),
    placeholder: tip.mixed ? "Mixed" : "Shown on hover / screen readers",
  });
  tipIn.addEventListener("change", () => apply({ tooltip: tipIn.value }));
  gen.push(row("Tooltip", tipIn));

  const req = shared(selected, "required");
  const ro = shared(selected, "readonly");
  gen.push(el("div", { class: "fp-prop-checks" },
    checkRow("Required", req.value, req.mixed, (v) => apply({ required: v })),
    checkRow("Read-only", ro.value, ro.mixed, (v) => apply({ readonly: v }))));

  const vis = shared(selected, "visibility");
  gen.push(row("Visibility", selectEl([
    ["visible", "Visible"],
    ["hidden", "Hidden"],
    ["noprint", "Visible, don't print"],
    ["printonly", "Hidden, print only"],
  ], vis.value || "visible", vis.mixed, (v) => apply({ visibility: v }))));
  frag.append(section("General", ...gen));

  // ---- Look ----
  if (!allSig) {
    const look = [];
    const fnt = shared(selected, "font");
    const fontSel = el("select", { class: "fp-prop-in" });
    const populateFonts = (choices) => {
      const cur = fnt.mixed ? "__mixed__" : (fnt.value || "Helvetica");
      fontSel.replaceChildren();
      if (fnt.mixed) fontSel.append(el("option", { value: "__mixed__", disabled: true, selected: true }, "— Mixed —"));
      const addGroup = (label, list) => {
        if (!list?.length) return;
        const g = el("optgroup", { label });
        for (const name of list) {
          const opt = el("option", { value: name }, name);
          if (!fnt.mixed && name.toLowerCase() === String(cur).toLowerCase()) opt.selected = true;
          g.append(opt);
        }
        fontSel.append(g);
      };
      if (choices?.document?.length) addGroup("In this document", choices.document);
      addGroup("Standard", STANDARD_FONTS);
      if (choices?.installed?.length) addGroup("Installed", choices.installed);
      if (cur && !fnt.mixed && ![...fontSel.options].some((o) => o.value.toLowerCase() === String(cur).toLowerCase())) {
        fontSel.prepend(el("option", { value: cur, selected: true }, cur));
      }
    };
    populateFonts(reader.activeTab?.formFonts?.choices || null);
    reader.formFonts?.().then((choices) => { if (choices && fontSel.isConnected) populateFonts(choices); });
    fontSel.addEventListener("change", () => {
      if (fontSel.value !== "__mixed__") apply({ font: fontSel.value });
    });
    look.push(row("Font", fontSel));

    const fsz = shared(selected, "font_size");
    const sizeMode = selectEl([
      ["0", "Auto"], ["8", "8 pt"], ["9", "9 pt"], ["10", "10 pt"], ["11", "11 pt"],
      ["12", "12 pt"], ["14", "14 pt"], ["16", "16 pt"], ["18", "18 pt"], ["24", "24 pt"],
    ], fsz.value ?? 0, fsz.mixed, (v) => apply({ font_size: Number(v) }));
    if (!fsz.mixed && ![0, 8, 9, 10, 11, 12, 14, 16, 18, 24].includes(Number(fsz.value))) {
      sizeMode.append(el("option", { value: String(fsz.value), selected: true }, `${fsz.value} pt`));
    }
    look.push(row("Size", sizeMode));

    const tc = shared(selected, "text_color");
    const tcIn = el("input", { type: "color", class: "fp-prop-color", value: tc.value || "#000000" });
    tcIn.addEventListener("change", () => apply({ text_color: tcIn.value }));
    look.push(row("Text colour", tcIn));

    const fc = shared(selected, "fill_color");
    const fillOn = el("input", { type: "checkbox" });
    fillOn.checked = !fc.mixed && !!fc.value;
    fillOn.indeterminate = fc.mixed;
    const fcIn = el("input", { type: "color", class: "fp-prop-color", value: fc.value || "#ffffff" });
    fcIn.disabled = !fillOn.checked;
    fillOn.addEventListener("change", () => {
      fcIn.disabled = !fillOn.checked;
      apply({ fill_color: fillOn.checked ? fcIn.value : "" });
    });
    fcIn.addEventListener("change", () => {
      fillOn.checked = true;
      apply({ fill_color: fcIn.value });
    });
    look.push(row("Fill", el("span", { class: "fp-prop-inline" }, fillOn, fcIn)));

    const bc = shared(selected, "border_color");
    const borderOn = el("input", { type: "checkbox" });
    borderOn.checked = !bc.mixed && !!bc.value;
    borderOn.indeterminate = bc.mixed;
    const bcIn = el("input", { type: "color", class: "fp-prop-color", value: bc.value || "#737373" });
    bcIn.disabled = !borderOn.checked;
    borderOn.addEventListener("change", () => {
      bcIn.disabled = !borderOn.checked;
      apply({ border_color: borderOn.checked ? bcIn.value : "", border_width: borderOn.checked ? 1 : 0 });
    });
    bcIn.addEventListener("change", () => {
      borderOn.checked = true;
      apply({ border_color: bcIn.value });
    });
    look.push(row("Border", el("span", { class: "fp-prop-inline" }, borderOn, bcIn)));

    const bw = shared(selected, "border_width");
    const bwIn = el("input", {
      class: "fp-prop-in", type: "number", min: "0", max: "12", step: "0.5",
      value: bw.mixed ? "" : String(bw.value ?? 1),
      placeholder: bw.mixed ? "Mixed" : "",
    });
    bwIn.addEventListener("change", () => {
      const v = Number(bwIn.value);
      if (!Number.isNaN(v)) apply({ border_width: Math.max(0, v) });
    });
    look.push(row("Border width", bwIn));

    const aln = shared(selected, "align");
    look.push(row("Alignment", selectEl([
      ["left", "Left"], ["center", "Center"], ["right", "Right"],
    ], aln.value || "left", aln.mixed, (v) => apply({ align: v }))));

    frag.append(section("Look", ...look));
  }

  // ---- Text fields ----
  if (allText) {
    const txt = [];
    const dv = shared(selected, "default_value");
    const dvIn = el("input", {
      class: "fp-prop-in", type: "text",
      value: dv.mixed ? "" : (dv.value || ""),
      placeholder: dv.mixed ? "Mixed" : "",
    });
    dvIn.addEventListener("change", () => apply({ default_value: dvIn.value }));
    txt.push(row("Default value", dvIn));

    const ml = shared(selected, "max_length");
    const mlIn = el("input", {
      class: "fp-prop-in", type: "number", min: "0", step: "1",
      value: ml.mixed ? "" : String(ml.value || ""),
      placeholder: ml.mixed ? "Mixed" : "Unlimited",
    });
    mlIn.addEventListener("change", () => apply({ max_length: Math.max(0, parseInt(mlIn.value || "0", 10) || 0) }));
    txt.push(row("Max length", mlIn));

    const comb = shared(selected, "comb");
    const multiLine = shared(selected.map((f) => ({ is_ml: f.type === "multiline" })), "is_ml");
    const pw = shared(selected, "password");
    const scr = shared(selected, "scroll");
    txt.push(el("div", { class: "fp-prop-checks" },
      checkRow("Comb (one box per character)", comb.value, comb.mixed, (v) => apply({ comb: v })),
      checkRow("Multi-line", multiLine.value, multiLine.mixed, (v) => apply({ type: v ? "multiline" : "text" })),
      checkRow("Password", pw.value, pw.mixed, (v) => apply({ password: v })),
      checkRow("Scroll long text", scr.value ?? true, scr.mixed, (v) => apply({ scroll: v }))));

    frag.append(section("Text field", ...txt));
  }

  // ---- Choices (dropdown / list) ----
  if (allChoice && !multi) {
    const ch = [];
    let items = (first.choices || (first.options || []).map((o) => ({ label: o, value: o }))).map((c) => ({ ...c }));
    const listEl = el("div", { class: "fp-prop-choices" });
    let dragIdx = -1;

    const commitChoices = () => apply({ choices: items });
    const renderChoices = () => {
      listEl.replaceChildren();
      items.forEach((c, idx) => {
        const lblIn = el("input", { class: "fp-prop-in", type: "text", value: c.label, placeholder: "Shown" });
        const valIn = el("input", { class: "fp-prop-in", type: "text", value: c.value, placeholder: "Export" });
        const delBtn = el("button", { class: "rd-btn", type: "button", title: "Remove choice" }, "×");
        lblIn.addEventListener("change", () => {
          const oldLbl = c.label;
          c.label = lblIn.value;
          if (c.value === oldLbl) { c.value = c.label; valIn.value = c.value; }
          commitChoices();
        });
        valIn.addEventListener("change", () => { c.value = valIn.value; commitChoices(); });
        delBtn.addEventListener("click", () => {
          if (items.length <= 1 && !first.allow_custom) return;
          items.splice(idx, 1);
          renderChoices();
          commitChoices();
        });
        const r = el("div", { class: "fp-prop-choicerow", draggable: "true" },
          el("span", { class: "fp-prop-drag", title: "Drag to reorder" }, "⋮⋮"),
          lblIn, valIn, delBtn);
        r.addEventListener("dragstart", () => { dragIdx = idx; r.classList.add("dragging"); });
        r.addEventListener("dragend", () => { dragIdx = -1; r.classList.remove("dragging"); });
        r.addEventListener("dragover", (e) => e.preventDefault());
        r.addEventListener("drop", (e) => {
          e.preventDefault();
          if (dragIdx < 0 || dragIdx === idx) return;
          const [moved] = items.splice(dragIdx, 1);
          items.splice(idx, 0, moved);
          renderChoices();
          commitChoices();
        });
        listEl.append(r);
      });
    };
    renderChoices();

    const addChoiceBtn = el("button", { class: "rd-btn", type: "button" }, "+ Add choice");
    addChoiceBtn.addEventListener("click", () => {
      items.push({ label: `Choice ${items.length + 1}`, value: `Choice ${items.length + 1}` });
      renderChoices();
      commitChoices();
    });
    ch.push(el("div", { class: "fp-prop-choicehdr" }, el("span", {}, "Shown"), el("span", {}, "Export"), el("span", {})), listEl, addChoiceBtn);

    const defOpts = [["", "— None —"], ...items.map((c) => [c.value, c.label])];
    ch.push(row("Default choice", selectEl(defOpts, Array.isArray(first.default_choice) ? (first.default_choice[0] || "") : (first.default_choice || ""), false,
      (v) => apply({ default_choice: v }))));
    if (first.type === "dropdown") {
      ch.push(checkRow("Allow typing a new value", first.allow_custom, false, (v) => apply({ allow_custom: v })));
    } else {
      ch.push(checkRow("Allow selecting several", first.multiselect, false, (v) => apply({ multiselect: v })));
    }
    frag.append(section("Choices", ...ch));
  }

  // ---- Checkbox & Radio ----
  if (allBtn) {
    const btn = [];
    const ts = shared(selected, "tick_style");
    btn.push(row("Tick style", selectEl([
      ["check", "Check (✔)"], ["cross", "Cross (✘)"], ["dot", "Dot (●)"], ["square", "Square (■)"],
    ], ts.value || (first.type === "radio" ? "dot" : "check"), ts.mixed, (v) => apply({ tick_style: v }))));

    if (!multi) {
      const evIn = el("input", { class: "fp-prop-in", type: "text", value: first.export_value || "Yes" });
      evIn.addEventListener("change", () => {
        if (evIn.value.trim()) apply({ export_value: evIn.value.trim() });
      });
      btn.push(row("Export value", evIn));
    }
    const dc = shared(selected, "default_checked");
    btn.push(checkRow("Checked by default", dc.value, dc.mixed, (v) => apply({ default_checked: v })));

    if ([...types].every((t) => t === "radio")) {
      const grp = shared(selected, "group");
      const grpIn = el("input", {
        class: "fp-prop-in", type: "text",
        value: grp.mixed ? "" : (grp.value || first.name || ""),
        placeholder: grp.mixed ? "Mixed" : "",
      });
      grpIn.addEventListener("change", () => {
        const g = grpIn.value.trim();
        if (g) apply({ group: g }, { keepNames: [g] });
      });
      btn.push(row("Radio group", grpIn));
    }
    frag.append(section(first.type === "radio" ? "Radio button" : "Checkbox", ...btn));
  }

  // ---- Format, Validation, Calculation (text fields) ----
  if (allText) {
    const fmt = [];
    const fmtType = shared(selected, "format");
    const curFmt = fmtType.mixed ? "" : (fmtType.value || "");
    const fmtSel = selectEl([
      ["", "None"],
      ["number", "Number"],
      ["amount", "Amount (2 decimals)"],
      ["currency", "Currency"],
      ["percent", "Percentage"],
      ["date", "Date"],
      ["time", "Time"],
      ["phone", "Phone (US)"],
      ["zip", "ZIP / Postcode"],
      ["mask", "Custom mask"],
    ], curFmt, fmtType.mixed, (v) => apply({ format: v }));
    fmt.push(row("Format", fmtSel));

    if (!fmtType.mixed && ["number", "amount", "currency", "percent"].includes(curFmt)) {
      const decIn = el("input", { class: "fp-prop-in", type: "number", min: "0", max: "6", value: String(first.decimals ?? (curFmt === "amount" || curFmt === "currency" ? 2 : 0)) });
      decIn.addEventListener("change", () => apply({ decimals: Math.max(0, Math.min(6, parseInt(decIn.value || "0", 10) || 0)) }));
      fmt.push(row("Decimals", decIn));
      fmt.push(row("Separators", selectEl([
        ["1,234.56", "1,234.56"], ["1.234,56", "1.234,56"],
      ], first.separators || "1,234.56", false, (v) => apply({ separators: v }))));
    }
    if (!fmtType.mixed && ["number", "amount", "currency"].includes(curFmt)) {
      fmt.push(row("Negative", selectEl([
        ["0", "-123"], ["1", "Red -123"], ["2", "(123)"], ["3", "Red (123)"],
      ], String(first.neg_style ?? 0), false, (v) => apply({ neg_style: Number(v) }))));
    }
    if (!fmtType.mixed && curFmt === "currency") {
      const symIn = el("input", { class: "fp-prop-in", type: "text", value: first.currency || "$" });
      symIn.addEventListener("change", () => apply({ currency: symIn.value || "$" }));
      fmt.push(row("Symbol", symIn));
      fmt.push(checkRow("Symbol after number", first.currency_after, false, (v) => apply({ currency_after: v })));
    }
    if (!fmtType.mixed && curFmt === "date") {
      const dIn = el("input", { class: "fp-prop-in", type: "text", value: first.date || "dd/mm/yyyy", placeholder: "dd/mm/yyyy" });
      dIn.addEventListener("change", () => apply({ format: "date", date: dIn.value || "dd/mm/yyyy" }));
      fmt.push(row("Date pattern", dIn));
    }
    if (!fmtType.mixed && curFmt === "time") {
      const tIn = el("input", { class: "fp-prop-in", type: "text", value: first.time || "HH:MM", placeholder: "HH:MM" });
      tIn.addEventListener("change", () => apply({ format: "time", time: tIn.value || "HH:MM" }));
      fmt.push(row("Time pattern", tIn));
    }
    if (!fmtType.mixed && curFmt === "mask") {
      const mIn = el("input", { class: "fp-prop-in", type: "text", value: first.mask || "999-99-9999", placeholder: "999-99-9999" });
      mIn.addEventListener("change", () => { if (mIn.value) apply({ format: "mask", mask: mIn.value }); });
      fmt.push(row("Mask", mIn));
    }
    frag.append(section("Format", ...fmt));

    // ---- Validation ----
    const val = [];
    const curValKind = first.pattern ? "pattern" : (first.min !== undefined || first.max !== undefined) ? "range" : "";
    const valSel = selectEl([
      ["", "None"], ["range", "Number range"], ["pattern", "Pattern (regex)"],
    ], curValKind, false, (v) => {
      if (!v) apply({ min: "", max: "", pattern: "" });
      else if (v === "range") apply({ pattern: "", min: first.min ?? 0, max: first.max ?? 100 });
      else apply({ min: "", max: "", pattern: first.pattern || "[A-Za-z0-9]+" });
    });
    val.push(row("Validation", valSel));
    if (curValKind === "range") {
      const minIn = el("input", { class: "fp-prop-in", type: "number", value: first.min ?? "", placeholder: "Any" });
      const maxIn = el("input", { class: "fp-prop-in", type: "number", value: first.max ?? "", placeholder: "Any" });
      minIn.addEventListener("change", () => apply({ min: minIn.value === "" ? "" : Number(minIn.value) }));
      maxIn.addEventListener("change", () => apply({ max: maxIn.value === "" ? "" : Number(maxIn.value) }));
      val.push(row("Minimum", minIn), row("Maximum", maxIn));
    } else if (curValKind === "pattern") {
      const patIn = el("input", { class: "fp-prop-in", type: "text", value: first.pattern || "", placeholder: "^[A-Z]{2}\\d+$" });
      patIn.addEventListener("change", () => apply({ pattern: patIn.value }));
      val.push(row("Pattern", patIn));
    }
    frag.append(section("Validation", ...val));

    // ---- Calculation (single field) ----
    if (!multi) {
      const calc = [];
      const curCalc = first.calculate || null;
      const curOp = curCalc?.op || "";
      const allNames = [];
      for (const pList of Object.values(allPages || {})) {
        for (const f of pList) {
          if (f.name && f.name !== first.name && !allNames.includes(f.name) && (f.type === "text" || f.type === "multiline")) {
            allNames.push(f.name);
          }
        }
      }
      const calcSel = selectEl([
        ["", "None"],
        ["sum", "Sum of fields"],
        ["product", "Product of fields"],
        ["average", "Average of fields"],
        ["min", "Minimum of fields"],
        ["max", "Maximum of fields"],
        ["formula", "Formula (e.g. Qty * Price)"],
      ], curOp === "custom" ? "" : curOp, false, (v) => {
        if (!v) apply({ calculate: "" });
        else if (v === "formula") apply({ calculate: { op: "formula", formula: allNames.slice(0, 2).join(" + ") || "0" } });
        else {
          const picked = (curCalc?.fields?.length ? curCalc.fields : allNames.slice(0, 2));
          if (picked.length) apply({ calculate: { op: v, fields: picked } });
        }
      });
      calc.push(row("Calculate", calcSel));

      if (["sum", "product", "average", "min", "max"].includes(curOp)) {
        const chosen = new Set(curCalc?.fields || []);
        let picking = reader._formPickingFor === selKey;
        const pickBtn = el("button", { class: "rd-btn", type: "button", "aria-pressed": String(picking) },
          picking ? "Done picking" : "Pick fields on page");
        const onPick = (f) => {
          if (f.name === first.name) return;
          if (chosen.has(f.name)) chosen.delete(f.name);
          else chosen.add(f.name);
          if (chosen.size) apply({ calculate: { op: curOp, fields: [...chosen] } });
        };
        if (picking) reader.setFormPickMode?.(onPick);
        pickBtn.addEventListener("click", () => {
          picking = !picking;
          reader._formPickingFor = picking ? selKey : null;
          pickBtn.setAttribute("aria-pressed", String(picking));
          pickBtn.textContent = picking ? "Done picking" : "Pick fields on page";
          reader.setFormPickMode?.(picking ? onPick : null);
        });
        const list = el("div", { class: "fp-prop-calcfields" });
        for (const n of allNames) {
          list.append(checkRow(n, chosen.has(n), false, (on) => {
            if (on) chosen.add(n);
            else chosen.delete(n);
            if (chosen.size) apply({ calculate: { op: curOp, fields: [...chosen] } });
          }));
        }
        calc.push(pickBtn, list);
      } else if (curOp === "formula") {
        const fIn = el("input", { class: "fp-prop-in", type: "text", value: curCalc?.formula || "", placeholder: "Qty * Price" });
        const fApply = el("button", { class: "rd-btn primary", type: "button" }, "Apply");
        const commitFormula = () => {
          const expr = fIn.value.trim();
          if (expr) apply({ calculate: { op: "formula", formula: expr } });
        };
        fApply.addEventListener("click", commitFormula);
        fIn.addEventListener("keydown", (e) => { if (e.key === "Enter") commitFormula(); });
        calc.push(row("Formula", el("span", { class: "fp-prop-inline" }, fIn, fApply)));
      }
      frag.append(section("Calculation", ...calc));
    }
  }

  host.replaceChildren(frag);
}
