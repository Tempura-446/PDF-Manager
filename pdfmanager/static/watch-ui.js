/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// ---- Settings > Watch folders (watchfolders.py) ------------------------------------
let watchInfo = { folders: [], tools: [], recipes: [] }, watchLast = null;
async function loadWatch() {
  watchInfo = await jsonApi("/api/watch");
  if (watchLast === null) watchLast = watchInfo.last;  // (news from before this page isn't shown)
  renderWatch();
  return watchInfo;
}
function watchState(f) {
  const s = f.status, counts = [s.done ? `${s.done} done` : "", s.failed ? `${s.failed} couldn’t be done` : ""].filter(Boolean).join(", ");
  const head = { paused: "Paused", watching: "Watching", starting: "Starting…" }[s.state] || s.detail || s.state;
  return [head, counts, s.last ? `Last: ${s.last.message}` : ""].filter(Boolean).join(" · ");
}
let _watchDoKey = "";
let _watchListKey = "";
function renderWatch() {
  const list = $("watch-list");
  const listKey = watchInfo.folders.map((f) => `${f.id}|${f.paused ? 1 : 0}|${f.folder}|${f.describe}`).join("\n");
  if (listKey === _watchListKey && list.children.length === watchInfo.folders.length) {
    // Patch status text in-place so 2s polling never steals focus from Pause/Remove buttons.
    watchInfo.folders.forEach((f, idx) => {
      const bad = ["missing", "error", "elsewhere"].includes(f.status.state) || (f.status.last && !f.status.last.ok);
      const st = list.children[idx]?.querySelector(".wi-state");
      if (st) {
        st.textContent = watchState(f);
        st.classList.toggle("err", !!bad);
      }
    });
  } else {
    _watchListKey = listKey;
    list.replaceChildren(...watchInfo.folders.map((f) => {
      const bad = ["missing", "error", "elsewhere"].includes(f.status.state) || (f.status.last && !f.status.last.ok);
      const pause = el("button", { type: "button", class: "btn secondary pb-small", "aria-label": `${f.paused ? "Resume" : "Pause"} watching ${f.folder}`,
        onclick: () => watchCall("/api/watch/pause", { id: f.id, paused: !f.paused }) }, f.paused ? "Resume" : "Pause");
      const remove = el("button", { type: "button", class: "btn ghost pb-small", "aria-label": `Stop watching ${f.folder}`,
        onclick: () => watchCall("/api/watch/remove", { id: f.id }, `${f.folder} isn’t watched any more. Its files are left as they are.`) }, "Remove");
      return el("li", { class: "watch-item", "data-id": f.id },
        el("div", { class: "wi-text" }, el("span", { class: "wi-folder" }, f.folder), el("span", { class: "wi-what" }, f.describe),
          el("span", { class: "wi-state" + (bad ? " err" : ""), "aria-live": "polite" }, watchState(f))),
        el("div", { class: "wi-buttons" }, pause, remove));
    }));
  }
  list.hidden = !watchInfo.folders.length;
  const pick = $("watch-do");
  const doKey = watchInfo.tools.map((t) => `${t.id}:${t.label}`).join("|") + "||" + watchInfo.recipes.join("|");
  if (doKey !== _watchDoKey) {
    _watchDoKey = doKey;
    const was = pick.value;
    pick.replaceChildren(
      el("optgroup", { label: "A tool, with its usual settings" }, ...watchInfo.tools.map((t) => el("option", { value: "tool:" + t.id }, t.label))),
      ...(watchInfo.recipes.length ? [el("optgroup", { label: "A saved recipe" }, ...watchInfo.recipes.map((r) => el("option", { value: "recipe:" + r }, r)))] : []));
    if ([...pick.options].some((o) => o.value === was)) pick.value = was;
  }
  $("watch-browse").hidden = !(isDesktop() && window.pywebview.api.pick_folder);
}
async function watchCall(path, data, said = "") {
  try {
    watchInfo = await jsonApi(path, data);
    renderWatch();
    updateStatus("watch-status", said);
    return true;
  } catch (e) { updateStatus("watch-status", capital(e.message), true); return false; }
}
async function addWatch() {
  const [kind, ...rest] = $("watch-do").value.split(":");
  const folder = $("watch-folder").value.trim();
  const ok = await watchCall("/api/watch/add", { folder, [kind]: rest.join(":"), results: $("watch-results").value, originals: $("watch-originals").value,
      images: $("watch-images").checked, batch: $("watch-batch").checked, auto_file: !!$("watch-auto-file")?.checked },
    `Watching ${folder}. ${$("watch-images").checked ? "PDFs and pictures" : "PDFs"} put there from now on, and those there already, are done one by one.`);
  if (ok) {
    $("watch-folder").value = "";
    $("watch-images").checked = $("watch-batch").checked = false;
    if ($("watch-auto-file")) $("watch-auto-file").checked = false;
    scheduleWatchNews(1500);
  }
}
$("watch-add-btn").addEventListener("click", addWatch);
$("watch-folder").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addWatch(); } });  // (not Save)
$("watch-browse").addEventListener("click", async () => {
  try { const chosen = await window.pywebview.api.pick_folder(); if (chosen) $("watch-folder").value = chosen; } catch (e) { /* closed */ }
});

let filingState = { rules: [], starters: [] };
let filingEditIndex = -1;

function _filingRuleSummary(rule) {
  const c = rule.conditions || {};
  const bits = [];
  if (Array.isArray(c.contains) && c.contains.length) bits.push(`contains: ${c.contains.join(", ")}`);
  if (c.regex) bits.push(`regex: ${c.regex}`);
  if (c.filename) bits.push(`file: ${c.filename}`);
  if (c.has_date) bits.push("has date");
  return bits.length ? bits.join(" · ") : "matches any PDF";
}

function openFilingEditor(index = -1) {
  const wrap = $("filing-rule-editor");
  if (!wrap) return;
  filingEditIndex = index;
  const rule = index >= 0 ? (filingState.rules || [])[index] : null;
  const c = (rule && rule.conditions) || {};
  const title = $("filing-editor-title");
  if (title) title.textContent = rule ? `Edit rule: ${rule.name || ""}` : "Add rule";
  $("filing-rule-name").value = rule ? (rule.name || "") : "";
  $("filing-rule-contains").value = Array.isArray(c.contains) ? c.contains.join(", ") : (c.contains || "");
  $("filing-rule-regex").value = c.regex || "";
  $("filing-rule-filename").value = c.filename || "";
  $("filing-rule-has-date").checked = Boolean(c.has_date);
  $("filing-rule-template").value = rule ? (rule.template || "") : "{year}/{orig_name}.pdf";
  wrap.hidden = false;
  $("filing-rule-name").focus();
}

function closeFilingEditor() {
  const wrap = $("filing-rule-editor");
  if (wrap) wrap.hidden = true;
  filingEditIndex = -1;
}

async function saveFilingRulesList(rules, message) {
  await jsonApi("/api/filing/rules", { rules });
  await loadFilingRules();
  if (message) updateStatus("filing-status", message);
}

function renderFilingRules() {
  const list = $("filing-rules-list");
  if (!list) return;
  list.replaceChildren();
  const rules = filingState.rules || [];
  rules.forEach((rule, idx) => {
    const li = document.createElement("li");
    li.className = "watch-item";
    const info = document.createElement("div");
    info.style.flex = "1";
    const strong = document.createElement("strong");
    strong.textContent = rule.name || `Rule ${idx + 1}`;
    const meta = document.createElement("div");
    meta.className = "dlg-hint";
    meta.style.margin = "2px 0 0";
    meta.textContent = `${_filingRuleSummary(rule)} → ${rule.template || ""}`;
    info.append(strong, meta);

    const actions = document.createElement("div");
    actions.style.display = "flex";
    actions.style.gap = "4px";
    actions.style.flexWrap = "wrap";
    const mkBtn = (label, fn, disabled = false) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn secondary sm";
      b.textContent = label;
      b.disabled = disabled;
      b.addEventListener("click", fn);
      return b;
    };
    actions.append(
      mkBtn("Edit", () => openFilingEditor(idx)),
      mkBtn("↑", async () => {
        const next = rules.slice();
        [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
        try { await saveFilingRulesList(next, "Rule order updated."); } catch (err) { updateStatus("filing-status", err.message, true); }
      }, idx === 0),
      mkBtn("↓", async () => {
        const next = rules.slice();
        [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
        try { await saveFilingRulesList(next, "Rule order updated."); } catch (err) { updateStatus("filing-status", err.message, true); }
      }, idx === rules.length - 1),
      mkBtn("Delete", async () => {
        const next = rules.filter((_, i) => i !== idx);
        try {
          closeFilingEditor();
          await saveFilingRulesList(next, `Deleted rule "${rule.name || idx + 1}".`);
        } catch (err) { updateStatus("filing-status", err.message, true); }
      })
    );
    li.append(info, actions);
    list.append(li);
  });
}

async function loadFilingRules() {
  const data = await jsonApi("/api/filing/rules");
  filingState = data || { rules: [], starters: [] };
  const n = (filingState.rules || []).length;
  const sum = $("filing-rules-summary");
  if (sum) {
    sum.textContent = n
      ? `${n} smart filing rule${n === 1 ? "" : "s"} active (used by Watch folders with auto-filing enabled and pdfm auto-file).`
      : "No smart filing rules saved yet. Add a rule, load starter rules (Invoices, Receipts, Contracts, Statements), or preview on a sample PDF.";
  }
  renderFilingRules();
  return filingState;
}

$("filing-add-btn")?.addEventListener("click", () => openFilingEditor(-1));
$("filing-rule-cancel")?.addEventListener("click", closeFilingEditor);
$("filing-rule-save")?.addEventListener("click", async () => {
  const name = ($("filing-rule-name")?.value || "").trim();
  const template = ($("filing-rule-template")?.value || "").trim();
  if (!name) { updateStatus("filing-status", "Enter a rule name.", true); return; }
  if (!template) { updateStatus("filing-status", "Enter an output path template.", true); return; }
  const conditions = {};
  const containsRaw = ($("filing-rule-contains")?.value || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (containsRaw.length) conditions.contains = containsRaw;
  const regex = ($("filing-rule-regex")?.value || "").trim();
  if (regex) conditions.regex = regex;
  const filename = ($("filing-rule-filename")?.value || "").trim();
  if (filename) conditions.filename = filename;
  if ($("filing-rule-has-date")?.checked) conditions.has_date = true;
  const rule = { name, conditions, template };
  const next = (filingState.rules || []).slice();
  if (filingEditIndex >= 0 && filingEditIndex < next.length) next[filingEditIndex] = rule;
  else next.push(rule);
  try {
    closeFilingEditor();
    await saveFilingRulesList(next, `Saved rule "${name}".`);
  } catch (err) { updateStatus("filing-status", err.message, true); }
});

$("filing-load-starters")?.addEventListener("click", async () => {
  try {
    const data = await loadFilingRules();
    const starters = data.starters || [];
    if (!starters.length) return;
    if ((data.rules || []).length && typeof appAsk === "function") {
      const ok = await appAsk({
        title: "Replace existing filing rules?",
        text: `This will replace your ${data.rules.length} saved filing rule(s) with the ${starters.length} starter rules.`,
        yes: "Replace",
        no: "Cancel",
        danger: true,
      });
      if (!ok) return;
    }
    closeFilingEditor();
    await saveFilingRulesList(starters, `Loaded ${starters.length} starter filing rules.`);
  } catch (err) { updateStatus("filing-status", err.message, true); }
});
$("filing-preview-btn")?.addEventListener("click", () => $("filing-preview-file")?.click());
$("filing-preview-file")?.addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  e.target.value = "";
  if (!file) return;
  try {
    const fd = new FormData();
    fd.append("file", file, file.name);
    const res = await api("/api/filing/preview", { method: "POST", body: fd });
    const data = await res.json();
    const item = data.plan ? data.plan[0] : data;
    if (item && item.matched) {
      updateStatus("filing-status", `Matched "${item.rule}": ${file.name} → ${item.target || item.target_name || item.destination}`);
    } else {
      updateStatus("filing-status", `No rule matched ${file.name}.`);
    }
  } catch (err) { updateStatus("filing-status", err.message, true); }
});
// A note when a watched file is done or couldn't be (with the plain reason).
let watchNewsTimer = 0;
function scheduleWatchNews(ms) { clearTimeout(watchNewsTimer); watchNewsTimer = setTimeout(watchNews, ms); }
/** Several pieces of watch-folder news as one line: the one document done in
 *  its own words (how many files went into it, where it is), or how many
 *  documents; then each file that couldn't be done, in its own words once. */
function watchNewsWords(done, failed, waiting) {
  return [done.length === 1 ? done[0].message : done.length ? `${plural(done.length, "document")} done in watch folders` : "",
    ...failed.map((n) => n.message), ...waiting.map((n) => n.message)].filter(Boolean).join(". ") + ".";
}
async function watchNews() {
  try {
    if (watchLast === null) await loadWatch();
    const { news, last } = await jsonApi(`/api/watch/news?after=${watchLast}`);
    watchLast = last;
    const failed = news.filter((n) => !n.ok && !n.waiting), done = news.filter((n) => n.ok), waiting = news.filter((n) => n.waiting);
    if (news.length === 1) appToast(news[0].message, news[0].ok ? 6000 : 12000, news[0].id ? ["Open", () => openInReaderById([news[0].id])] : null);
    else if (news.length) appToast(watchNewsWords(done, failed, waiting), failed.length || waiting.length ? 12000 : 6000,
      done.length === 1 && done[0].id ? ["Open", () => openInReaderById([done[0].id])] : null);
    if ($("settings-dialog").open) await loadWatch();
  } catch (e) { /* the app is closing, or busy: try again */ }
  const open = $("settings-dialog").open;
  scheduleWatchNews(open ? 2000 : watchInfo.folders.some((f) => !f.paused) ? 4000 : 20000);
}

