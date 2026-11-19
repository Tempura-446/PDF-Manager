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
function renderWatch() {
  const list = $("watch-list");
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
  list.hidden = !watchInfo.folders.length;
  const pick = $("watch-do"), was = pick.value;
  pick.replaceChildren(
    el("optgroup", { label: "A tool, with its usual settings" }, ...watchInfo.tools.map((t) => el("option", { value: "tool:" + t.id }, t.label))),
    ...(watchInfo.recipes.length ? [el("optgroup", { label: "A saved recipe" }, ...watchInfo.recipes.map((r) => el("option", { value: "recipe:" + r }, r)))] : []));
  if ([...pick.options].some((o) => o.value === was)) pick.value = was;
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
      images: $("watch-images").checked, batch: $("watch-batch").checked },
    `Watching ${folder}. ${$("watch-images").checked ? "PDFs and pictures" : "PDFs"} put there from now on, and those there already, are done one by one.`);
  if (ok) { $("watch-folder").value = ""; $("watch-images").checked = $("watch-batch").checked = false; scheduleWatchNews(1500); }
}
$("watch-add-btn").addEventListener("click", addWatch);
$("watch-folder").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addWatch(); } });  // (not Save)
$("watch-browse").addEventListener("click", async () => {
  try { const chosen = await window.pywebview.api.pick_folder(); if (chosen) $("watch-folder").value = chosen; } catch (e) { /* closed */ }
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

