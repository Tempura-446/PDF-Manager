/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// ---- settings and updates ---------------------------------------------------------
// The app checks GitHub for a newer version (at most twice a day); an
// "Update" button appears at the foot of the rail when there is one.
const jsonApi = async (path, data) => (await api(path, data === undefined ? {} : {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })).json();
let updateInfo = null;
function showUpdate(info) {
  updateInfo = info;
  $("update-btn").hidden = !info.available;
  if (info.available) $("update-label").textContent = $("update-btn").title = `Update to ${info.latest}`;
}
async function checkForUpdates() {
  try { showUpdate(await jsonApi("/api/update")); } catch (e) { /* offline: try again next time */ }
}
function updateStatus(id, text, error = false) { $(id).textContent = text; $(id).classList.toggle("err", error); }

async function openSettings() {
  const s = await jsonApi("/api/settings");
  $("settings-version").textContent = `You have version ${$("version").textContent.replace("Version ", "")}` +
    ({ source: " (running from a source folder: update it with git pull)", frozen: " (the standalone app: download a new one to update it)" }[s.how] || ".");
  $("set-check").checked = s.check;
  $("set-ocr-quality").value = s.ocr_quality || "standard";
  $("ocr-gpu-wrap").hidden = !s.ocr_gpu_supported;
  $("set-ocr-gpu").checked = !!s.ocr_gpu;
  showPrivacy(s);
  showBehind(s);
  updateStatus("behind-status", "");
  updateStatus("privacy-status", "");
  $("set-online").replaceChildren(...(s.online || []).map((o) => {
    const li = document.createElement("li"), b = document.createElement("b");
    b.textContent = o.what;
    li.append(b, `: ${o.when}. Sends ${o.sends}.` + (o.switch ? ` Switch: ${o.switch}.` : ""));
    return li;
  }));
  $("set-repo").value = s.repo;
  $("set-token").value = "";
  $("set-token-state").textContent = s.token_set ? "· saved" : "";
  $("set-token-clear").hidden = !s.token_set;
  updateStatus("set-status", "");
  try { await loadWatch(); } catch (e) { updateStatus("watch-status", e.message, true); }
  updateStatus("watch-status", "");
  try {
    const menu = await jsonApi("/api/shellmenu");
    $("shell-menu").hidden = !menu.available;
    $("set-shell-menu").checked = !!menu.on;
  } catch (e) { $("shell-menu").hidden = true; }
  $("settings-dialog").showModal();
}
$("set-shell-menu").addEventListener("change", async (e) => {
  const want = e.target.checked;
  try {
    const menu = await jsonApi("/api/shellmenu", { on: want });
    e.target.checked = !!menu.on;
    updateStatus("set-status", menu.on ? "ZPDF is in the right-click menu." : "ZPDF was taken out of the right-click menu.");
  } catch (err) { e.target.checked = !want; updateStatus("set-status", err.message, true); }
});
// Clear history, the window's part: where you were in every file. Returns how many.
function forgetPlaces() {
  let n = 0;
  try {
    for (const key of Object.keys(localStorage)) if (key.startsWith("pdfm-pos:")) { localStorage.removeItem(key); n++; }
  } catch (e) { /* storage unavailable */ }
  return n;
}
// Watching with the window closed: what closing does, and starting at sign-in
// (where this copy can do it; else the reason is said and the choices are off).
function showBehind(s) {
  const b = s.background || { available: false, reason: "" };
  $("set-background").value = b.keep || "ask";
  $("set-login").checked = !!b.login;
  $("set-background").disabled = $("set-login").disabled = !b.available;
  $("watch-behind-why").hidden = b.available || !b.reason;
  $("watch-behind-why").textContent = b.available ? "" : b.reason;
  $("watch-behind-quit").hidden = !(b.available && b.keep === "yes");
}
$("set-quit").addEventListener("click", async () => {
  try { await jsonApi("/api/quit", {}); updateStatus("behind-status", "Quitting ZPDF…"); }
  catch (err) { updateStatus("behind-status", capital(err.message), true); }
});
$("set-background").addEventListener("change", async (e) => {
  try {
    const s = await jsonApi("/api/settings", { background: e.target.value });
    showBehind(s);
    updateStatus("behind-status", { ask: "ZPDF will ask the next time you close it while a folder is watched.",
      yes: "Closing the window will keep ZPDF watching; its icon by the clock opens or quits it.",
      no: "Closing the window will close ZPDF." }[s.background.keep]);
  } catch (err) { updateStatus("behind-status", capital(err.message), true); openSettings().catch(() => {}); }
});
$("set-login").addEventListener("change", async (e) => {
  try {
    const s = await jsonApi("/api/settings", { login: e.target.checked });
    showBehind(s);
    updateStatus("behind-status", s.background.login
      ? "ZPDF will start in the background when you sign in. Windows lists it under Settings → Apps → Startup."
      : "ZPDF won't start by itself when you sign in.");
  } catch (err) { e.target.checked = !e.target.checked; updateStatus("behind-status", capital(err.message), true); }
});
// What's remembered, said under the switch; the reader keeps (or not) where you were.
function showPrivacy(s) {
  $("set-remember").checked = s.remember;
  window.zpdfForgetFiles = !s.remember;
  const h = s.history || {}, kept = [];
  if (h.recent) kept.push(`${h.recent} in the Recent list`);
  if (h.facts) kept.push(`${h.facts} file${h.facts === 1 ? "" : "s"} read`);
  if (h.search) kept.push("text from Search a folder");
  $("set-history").textContent = !s.remember
    ? "Off: no Recent list, and nothing about your files is kept from one use to the next."
    : "The Recent list, where you were in each file, what ZPDF read from it, and the text kept by Search a folder." +
      (kept.length ? ` Kept now: ${kept.join(", ")}.` : " Nothing is kept right now.");
  $("set-clear-history").disabled = !s.remember;
}
$("set-remember").addEventListener("change", async (e) => {
  try {
    const s = await jsonApi("/api/settings", { remember: e.target.checked });
    if (!s.remember) forgetPlaces();
    showPrivacy(s);
    updateStatus("privacy-status", s.remember ? "ZPDF remembers the files you open again." : "History cleared; ZPDF won't keep one.");
    if (inHome()) renderHome().catch(() => {});
  } catch (err) { e.target.checked = !e.target.checked; updateStatus("privacy-status", err.message, true); }
});
$("set-clear-history").addEventListener("click", async () => {
  try {
    const s = await jsonApi("/api/history/clear", {});
    const places = forgetPlaces();
    const cleared = [...(s.cleared || []), ...(places ? ["where you were in your files"] : [])];
    showPrivacy(s);
    updateStatus("privacy-status", cleared.length ? `Cleared: ${cleared.join(", ")}.` : "There was nothing to clear.");
    if (inHome()) renderHome().catch(() => {});
  } catch (err) { updateStatus("privacy-status", err.message, true); }
});
jsonApi("/api/settings").then((s) => { window.zpdfForgetFiles = !s.remember; }).catch(() => {});
$("set-ocr-quality").addEventListener("change", async (e) => {
  try {
    await jsonApi("/api/settings", { ocr_quality: e.target.value });
  } catch (err) { updateStatus("set-status", err.message, true); }
});
$("set-ocr-gpu").addEventListener("change", async (e) => {
  try {
    await jsonApi("/api/settings", { ocr_gpu: e.target.checked });
  } catch (err) { updateStatus("set-status", err.message, true); }
});
async function saveSettings() {
  const data = { check: $("set-check").checked, repo: $("set-repo").value.trim(),
                 ocr_quality: $("set-ocr-quality").value, ocr_gpu: $("set-ocr-gpu").checked };
  if ($("set-token").value.trim()) data.token = $("set-token").value.trim();
  const s = await jsonApi("/api/settings", data);
  $("set-token").value = "";
  $("set-token-state").textContent = s.token_set ? "· saved" : "";
  $("set-token-clear").hidden = !s.token_set;
  return s;
}
$("settings-btn").addEventListener("click", () => openSettings().catch((e) => showError(e.message)));
async function showText(title, path) {
  $("text-title").textContent = title;
  $("text-body").textContent = "Loading…";
  $("text-dialog").showModal();
  try { $("text-body").textContent = await (await api(path)).text(); }
  catch (e) { $("text-body").textContent = "Couldn't load it: " + e.message; }
  $("text-body").scrollTop = 0;
}
$("show-licence").addEventListener("click", () => showText("Licence", "/api/licence"));
$("show-notices").addEventListener("click", () => showText("Third-party notices", "/api/notices"));
$("copy-diagnostics").addEventListener("click", async () => {
  try {
    const text = await (await api("/api/diagnostics")).text();
    try {
      await navigator.clipboard.writeText(text);
      updateStatus("set-status", "Diagnostics copied: paste them into a message.");
    } catch (e) {  // no clipboard here: show them to copy by hand
      $("text-title").textContent = "Diagnostics";
      $("text-body").textContent = text;
      $("text-dialog").showModal();
    }
  } catch (e) { updateStatus("set-status", e.message, true); }
});
$("open-logs").addEventListener("click", async () => {
  try { await jsonApi("/api/logs/open", {}); }
  catch (e) { updateStatus("set-status", e.message, true); }
});
$("set-cancel").addEventListener("click", () => $("settings-dialog").close());
$("settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await saveSettings(); $("settings-dialog").close(); checkForUpdates(); }
  catch (err) { updateStatus("set-status", err.message, true); }
});
$("set-token-clear").addEventListener("click", async () => {
  try { await jsonApi("/api/settings", { clear_token: true }); $("set-token-state").textContent = ""; $("set-token-clear").hidden = true; updateStatus("set-status", "The token was removed."); }
  catch (err) { updateStatus("set-status", err.message, true); }
});
$("set-check-now").addEventListener("click", async () => {
  updateStatus("set-status", "Checking…");
  try {
    await saveSettings();
    const info = await jsonApi("/api/update/check", {});
    showUpdate(info);
    if (info.error) updateStatus("set-status", "Couldn't check: " + info.error, true);
    else if (info.available) updateStatus("set-status", `Version ${info.latest} is available: use the Update button at the bottom of the rail.`);
    else updateStatus("set-status", `You have the newest version (${info.latest}).`);
  } catch (err) { updateStatus("set-status", err.message, true); }
});

$("update-btn").addEventListener("click", () => {
  const info = updateInfo;
  if (!info) return;
  $("update-text").textContent = info.how === "pip"
    ? `Version ${info.latest} is available (you have ${info.current}). It's downloaded from GitHub and installed, then ZPDF starts again. Save your work in the reader first.`
    : info.how === "source" ? `Version ${info.latest} is available (you have ${info.current}). This copy runs from a source folder: update it with git pull.`
    : `Version ${info.latest} is available (you have ${info.current}). Download the new standalone app from GitHub to update.`;
  $("update-go").hidden = info.how !== "pip";
  $("update-go").disabled = false;
  $("update-later").textContent = info.how === "pip" ? "Later" : "Close";
  $("update-progress").hidden = true;
  updateStatus("update-status", "");
  $("update-dialog").showModal();
});
$("update-later").addEventListener("click", () => $("update-dialog").close());
$("update-go").addEventListener("click", async () => {
  if (reader && reader.hasUnsavedChanges() && await appAsk("Update with unsaved changes?", "The reader has changes that haven't been saved: updating restarts ZPDF and they'd be lost.",
    [["cancel", "Cancel"], ["update", "Update anyway", true]]) !== "update") return;
  const go = $("update-go"), bar = $("update-progress");
  go.disabled = true; $("update-later").disabled = true; bar.hidden = false;
  try {
    const { job } = await (await api("/api/update/install", { method: "POST", headers: { "X-Pdfm-Async": "1" } })).json();
    let state;
    for (let wait = 300; ; wait = Math.min(wait * 1.4, 1000)) {
      await new Promise((r) => setTimeout(r, wait));
      state = await (await api(`/api/job/${job}`)).json();
      if (state.message) updateStatus("update-status", state.message);
      bar.firstElementChild.style.width = `${Math.max(8, Math.round(state.fraction * 100))}%`;
      if (state.state !== "running") break;
    }
    const { version, note } = await (await api(`/api/job/${job}/result`)).json();  // throws with the error if it failed
    bar.firstElementChild.style.width = "100%";
    const said = note ? ` ${note}` : "";
    if (!updateInfo.can_restart) {
      updateStatus("update-status", `Version ${version} is installed.${said} Close ZPDF and open it again to use it.`);
      $("update-later").disabled = false; $("update-later").textContent = "Close"; go.hidden = true;
      return;
    }
    if (note) {  // give it time to be read before the window goes
      updateStatus("update-status", `Version ${version} is installed.${said} Starting it in a moment…`);
      await new Promise((r) => setTimeout(r, 12000));
    }
    updateStatus("update-status", `Version ${version} is installed. Starting it…`);
    restarting = true;
    await api("/api/update/restart", { method: "POST" });
    // A browser tab can't close itself: say where the new copy is.
    setTimeout(() => updateStatus("update-status", "ZPDF has started again in a new window. You can close this one."), 8000);
  } catch (e) {
    updateStatus("update-status", "The update didn't work: " + (e.message || e), true);
    go.disabled = false; $("update-later").disabled = false;
  }
});
let restarting = false;

// Tell the server the page is still open; it shuts down some time after the
// last window running the app has closed.
const ping = () => { if (token) api("/api/ping", { method: "POST" }).catch(() => {}); };
// Tell the server when the window closes, so the app stops right away.
window.addEventListener("pagehide", () => { if (token) navigator.sendBeacon(`/api/bye?token=${encodeURIComponent(token)}&page=${pageId}`); });

// Warn before closing the window with unsaved changes in the reader.
window.addEventListener("beforeunload", (e) => { if (!restarting && reader && reader.hasUnsavedChanges()) { e.preventDefault(); e.returnValue = ""; } });

// Files handed over by another launch of the app (e.g. a PDF opened from
// Explorer while the app is already running) arrive as events.
async function listen() {
  // Where this window got to, kept across a reload of the page (for this run
  // of the app: the token is new each time), so the files handed over
  // before the reload aren't opened again.
  const seen = "pdfm-events:" + token;
  let after = 0;
  try { after = Number(sessionStorage.getItem(seen)) || 0; } catch (e) { after = 0; }
  for (;;) {
    try {
      const data = await (await api(`/api/events?after=${after}`)).json();
      if (data.none) return;  // not the app's own window: nothing will come
      after = data.last;
      try { sessionStorage.setItem(seen, String(after)); } catch (e) { /* not kept */ }
      for (const event of data.events) {
        if (event.open && event.open.length) await openInReaderById(event.open);
        if (event.action) runAction(event.action);  // (not awaited: its files are still arriving)
      }
    } catch (e) { await new Promise((r) => setTimeout(r, 3000)); }
  }
}
async function openInReaderById(ids, goto = null) {
  await select("reader");
  for (const id of ids) {  // each in its own tab
    // (The file this page was started with is already on its way: see the top of the page.)
    const early = window.zpdfEarly;
    if (early) window.zpdfEarly = null;
    await reader.openById(id, goto, early && early.id === id ? early.first : null);
  }
}
setInterval(ping, 15000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) ping(); });

// In the desktop window, re-render once pywebview's API is ready (adds Browse… buttons).
window.addEventListener("pywebviewready", () => { if (current && current.fields) renderForm(); });
load();
