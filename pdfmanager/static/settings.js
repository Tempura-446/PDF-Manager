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
  if ($("set-translate-engine")) {
    const eng = s.translate_engine || "offline";
    $("set-translate-engine").value = eng;
    $("set-translate-llm-wrap").hidden = eng !== "llm";
    $("set-translate-url").value = s.translate_api_url || "http://localhost:11434/v1";
    $("set-translate-model").value = s.translate_model || "qwen2.5:7b";
    $("set-translate-key").value = "";
    $("set-translate-key-state").textContent = s.translate_key_set ? "· saved" : "";
    $("set-translate-key-clear").hidden = !s.translate_key_set;
  }
  if ($("set-translate-glossary")) {
    $("set-translate-glossary").value = s.translate_glossary || "";
  }
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
  updateStatus("lang-status", "");
  updateStatus("watch-status", "");
  updateStatus("filing-status", "");
  try { await loadLanguages(); } catch (e) { updateStatus("lang-status", e.message, true); }
  try { await loadAiModels(); } catch (e) { if ($("ai-models-status")) updateStatus("ai-models-status", e.message, true); }
  try { await loadWatch(); } catch (e) { updateStatus("watch-status", e.message, true); }
  try { if (typeof loadFilingRules === "function") await loadFilingRules(); } catch (e) { updateStatus("filing-status", e.message, true); }
  try {
    const menu = await jsonApi("/api/shellmenu");
    $("shell-menu").hidden = !menu.available;
    $("set-shell-menu").checked = !!menu.on;
  } catch (e) { $("shell-menu").hidden = true; }
  updateStatus("audit-status", "");
  try {
    const polRes = await jsonApi("/api/policy");
    const pols = polRes.policies || {};
    const locked = Object.entries(pols).filter(([, info]) => info.locked).map(([k, info]) => `${k}=${JSON.stringify(info.value)}`);
    if ($("policy-status")) {
      $("policy-status").textContent = locked.length
        ? `Managed by administrator policy: ${locked.join(", ")}.`
        : "No administrator policies active.";
    }
    const updatesLockedOff = Boolean((pols.disable_updates?.locked && pols.disable_updates?.value)
      || (pols.disable_network?.locked && pols.disable_network?.value));
    $("set-check").disabled = updatesLockedOff;
    $("set-check-now").disabled = updatesLockedOff;
    if (updatesLockedOff) updateStatus("set-status", "Updates are disabled by administrator policy.");
  } catch (e) { /* policy endpoint optional */ }
  $("settings-dialog").showModal();
}

function renderLanguageState(info) {
  const sel = $("set-language");
  if (!sel || !info) return;
  const langs = info.languages || [{ code: "en", name: "English", automatic: false }];
  sel.replaceChildren(...langs.map((l) => {
    const opt = document.createElement("option");
    opt.value = l.code;
    opt.textContent = l.automatic ? `${l.name} (${l.code} — automatic)` : `${l.name} (${l.code})`;
    return opt;
  }));
  sel.value = info.lang && info.lang !== "auto" ? info.lang : "en";
  const list = $("set-packs-list");
  const packs = info.packs || [];
  if (!packs.length) {
    const li = document.createElement("li");
    li.className = "dlg-hint";
    li.textContent = "No extra language packs installed yet.";
    list.replaceChildren(li);
    return;
  }
  list.replaceChildren(...packs.map((p) => {
    const li = document.createElement("li");
    li.className = "watch-item";
    li.style.alignItems = "center";
    const caps = [
      p.has_translate && (p.pairs?.length ? `translate: ${p.pairs.join(", ")}` : "translate"),
      p.has_ocr && "OCR",
      p.has_meaning && "search",
    ].filter(Boolean);
    const sz = Number(p.size_bytes || 0);
    const szText = sz >= 1_000_000_000 ? `${(sz / 1_000_000_000).toFixed(1)} GB`
      : (sz >= 1_000_000 ? `${Math.round(sz / 1_000_000)} MB`
        : (sz >= 1_000 ? `${Math.round(sz / 1_000)} KB` : ""));
    const metaParts = [szText, caps.join("; ")].filter(Boolean);
    const infoBox = document.createElement("div");
    infoBox.className = "wi-text";
    const titleEl = document.createElement("div");
    titleEl.className = "wi-folder";
    titleEl.textContent = `${p.name} (${p.code})`;
    if (/^nllb-/i.test(p.code || "")) {
      const badge = document.createElement("span");
      badge.className = "set-badge retired";
      badge.textContent = "Retired · safe to remove";
      titleEl.append(badge);
    }
    infoBox.append(titleEl);
    if (metaParts.length) {
      const metaEl = document.createElement("div");
      metaEl.className = "wi-what";
      metaEl.textContent = metaParts.join(" · ");
      infoBox.append(metaEl);
    }
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "btn secondary sm";
    rm.textContent = "Remove";
    rm.addEventListener("click", async () => {
      try {
        if (typeof appAsk === "function") {
          const ok = await appAsk({
            title: `Remove ${p.name}?`,
            text: `This deletes the downloaded offline pack (${p.code}) from disk. You can re-download active packs later.`,
            yes: "Remove",
            no: "Cancel",
            danger: true,
          });
          if (!ok) return;
        }
        rm.disabled = true;
        rm.textContent = "Removing…";
        await jsonApi("/api/i18n/packs/remove", { code: p.code });
        await loadLanguages();
        if (langCatalogCache) renderLangCatalog(langCatalogCache);
        updateStatus("lang-status", `Removed ${p.code}.`);
      } catch (err) {
        rm.disabled = false;
        rm.textContent = "Remove";
        updateStatus("lang-status", err.message || String(err), true);
      }
    });
    li.append(infoBox, rm);
    return li;
  }));
}

// Settings section tabs (General · Language · Automation · Privacy · About)
(function wireSettingsTabs() {
  const bar = $("set-tabs");
  if (!bar) return;
  const tabs = [...bar.querySelectorAll('[role="tab"]')];
  const panes = [...document.querySelectorAll("#settings-dialog .set-pane")];
  const activate = (name, focusTab = false) => {
    for (const t of tabs) {
      const on = t.dataset.tab === name;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      if (on && focusTab) t.focus();
    }
    for (const p of panes) p.hidden = p.dataset.pane !== name;
    const form = $("settings-form");
    if (form) form.scrollTop = 0;
  };
  bar.addEventListener("click", (e) => {
    const t = e.target.closest('[role="tab"]');
    if (t) activate(t.dataset.tab);
  });
  bar.addEventListener("keydown", (e) => {
    const t = e.target.closest('[role="tab"]');
    if (!t) return;
    const idx = tabs.indexOf(t);
    let next = -1;
    if (e.key === "ArrowRight") next = (idx + 1) % tabs.length;
    else if (e.key === "ArrowLeft") next = (idx - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    if (next >= 0) {
      e.preventDefault();
      activate(tabs[next].dataset.tab, true);
    }
  });
  activate("general");
})();

$("set-translate-unload")?.addEventListener("click", async () => {
  try {
    const res = await jsonApi("/api/translate/unload", {});
    const n = Number(res?.unloaded || 0);
    const bytes = Number(res?.freed_bytes || 0);
    if (!n) {
      updateStatus("lang-status", "No translation models are currently loaded in RAM.");
    } else {
      const sz = bytes >= 1_000_000_000 ? `${(bytes / 1_000_000_000).toFixed(1)} GB`
        : (bytes >= 1_000_000 ? `${Math.round(bytes / 1_000_000)} MB` : "");
      updateStatus("lang-status", `Unloaded ${n} model${n === 1 ? "" : "s"} from RAM${sz ? ` (~${sz} freed)` : ""}.`);
    }
  } catch (err) {
    updateStatus("lang-status", err.message || String(err), true);
  }
});

let langCatalogCache = null;
let langDlBusy = false;

function renderLangCatalog(catalog) {
  const list = $("set-pack-catalog");
  if (!list) return;
  const q = ($("set-pack-filter")?.value || "").trim().toLowerCase();
  const items = (catalog?.packs || []).filter((p) =>
    !q || p.code.toLowerCase().includes(q) || (p.name || "").toLowerCase().includes(q)
  );
  if (!items.length) {
    const li = document.createElement("li");
    li.className = "dlg-hint";
    li.textContent = q ? "No matching language packs." : "No downloadable packs available right now.";
    list.replaceChildren(li);
    return;
  }
  const formatPackSize = (bytes, approx) => {
    const n = Number(bytes || 0);
    if (!n || n <= 0) return "";
    const prefix = approx ? "~" : "";
    if (n >= 1_000_000_000) return `${prefix}${(n / 1_000_000_000).toFixed(1)} GB`;
    if (n >= 1_000_000) return `${prefix}${Math.round(n / 1_000_000)} MB`;
    return `${prefix}${Math.max(1, Math.round(n / 1_000))} KB`;
  };
  const setAllDownloadBtns = (disabled) => {
    for (const b of list.querySelectorAll("button.lang-dl-btn")) b.disabled = disabled;
  };
  list.replaceChildren(...items.map((p) => {
    const li = document.createElement("li");
    li.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:8px;";
    const label = document.createElement("span");
    const srcTag = p.source === "madlad" ? "MADLAD-400 universal · Apache-2.0"
      : (p.source === "opus" ? "OPUS-MT fast pair · CC-BY-4.0"
        : (p.source === "zpdf" ? "ZPDF pack" : "Argos offline model · MIT/CC0"));
    const sizeText = formatPackSize(p.size_bytes, p.size_approx);
    const sizePart = sizeText ? ` · ${sizeText}` : "";
    const pairsText = p.available_pairs?.length ? ` · ${p.available_pairs.join(", ")}` : "";
    label.textContent = `${p.name} (${p.code}) — ${srcTag}${sizePart}${pairsText}`;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "link-btn lang-dl-btn";
    const idleLabel = p.has_translate ? "Re-download" : (sizeText ? `Download (${sizeText})` : "Download");
    btn.textContent = idleLabel;
    btn.disabled = langDlBusy;
    btn.addEventListener("click", async () => {
      if (langDlBusy) return;
      langDlBusy = true;
      setAllDownloadBtns(true);
      btn.textContent = "Downloading…";
      const dlWrap = $("lang-dl-wrap"), dlBar = $("lang-dl-bar"), dlCancel = $("lang-dl-cancel");
      let activeJobId = null, wasCancelled = false;
      const setBar = (frac) => {
        if (!dlBar) return;
        const pct = Math.max(0, Math.min(100, Math.round((frac || 0) * 100)));
        dlBar.setAttribute("aria-valuenow", String(pct));
        dlBar.firstElementChild.style.width = `${pct}%`;
      };
      if (dlCancel) {
        dlCancel.disabled = false;
        dlCancel.textContent = "Cancel";
        dlCancel.onclick = async () => {
          if (!activeJobId) return;
          dlCancel.disabled = true;
          dlCancel.textContent = "Cancelling…";
          try { await api(`/api/job/${activeJobId}/cancel`, { method: "POST" }); } catch (e) { /* already ended */ }
        };
      }
      const langDlUi = {
        start(id) {
          activeJobId = id;
          if (dlWrap) dlWrap.hidden = false;
          setBar(0);
        },
        message(m) { updateStatus("lang-status", m); },
        progress(f) { setBar(f); },
        cancelled() { wasCancelled = true; },
        end() {
          activeJobId = null;
          if (dlWrap) dlWrap.hidden = true;
          setBar(0);
        },
      };
      updateStatus("lang-status", `Downloading ${p.name}${sizeText ? ` (${sizeText})` : ""}…`);
      try {
        let res;
        if (typeof runJob === "function") {
          const resp = await runJob("/api/i18n/pack/download", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code: p.code, source: p.source }),
          }, langDlUi);
          if (!resp || wasCancelled) {
            btn.textContent = idleLabel;
            updateStatus("lang-status", `Cancelled downloading ${p.name}.`);
            return;
          }
          res = await resp.json();
        } else {
          res = await jsonApi("/api/i18n/pack/download", { code: p.code, source: p.source });
        }
        await loadLanguages();
        const fresh = await jsonApi("/api/i18n/catalog");
        langCatalogCache = fresh;
        langDlBusy = false;
        renderLangCatalog(fresh);
        const pairs = res.installed_pairs?.length ? ` (${res.installed_pairs.join(", ")})` : "";
        updateStatus("lang-status", `Installed ${p.name}${pairs}. Ready for offline translation.`);
      } catch (err) {
        btn.textContent = idleLabel;
        updateStatus("lang-status", err.message, true);
      } finally {
        langDlBusy = false;
        setAllDownloadBtns(false);
      }
    });
    li.append(label, btn);
    return li;
  }));
}

async function loadLanguages() {
  const info = await jsonApi("/api/i18n");
  renderLanguageState(info);
  return info;
}

$("set-language")?.addEventListener("change", async (e) => {
  try {
    const info = await jsonApi("/api/i18n/language", { language: e.target.value });
    renderLanguageState(info);
    if (window.ZpdfI18n?.load) await window.ZpdfI18n.load(token);
    updateStatus("lang-status", `Language set to ${e.target.value}.`);
  } catch (err) { updateStatus("lang-status", err.message, true); }
});

$("set-pack-browse")?.addEventListener("click", async () => {
  const wrap = $("set-pack-catalog-wrap");
  if (!wrap) return;
  if (!wrap.hidden && langCatalogCache) {
    wrap.hidden = true;
    return;
  }
  wrap.hidden = false;
  updateStatus("lang-status", "Checking available language packs…");
  try {
    langCatalogCache = await jsonApi("/api/i18n/catalog");
    renderLangCatalog(langCatalogCache);
    const total = langCatalogCache.packs?.length || 0;
    updateStatus("lang-status", total ? `${total} languages available for offline download.` : "Catalog unavailable (offline?).");
  } catch (err) {
    updateStatus("lang-status", err.message, true);
  }
});

$("set-pack-filter")?.addEventListener("input", () => {
  if (langCatalogCache) renderLangCatalog(langCatalogCache);
});

$("set-pack-install")?.addEventListener("click", () => $("set-pack-file")?.click());
$("set-pack-file")?.addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  e.target.value = "";
  if (!file) return;
  try {
    const fd = new FormData();
    fd.append("file", file, file.name);
    const res = await api("/api/i18n/packs/install", { method: "POST", body: fd });
    const data = await res.json();
    await loadLanguages();
    updateStatus("lang-status", `Installed language pack: ${data.installed?.name || data.installed?.code}.`);
  } catch (err) { updateStatus("lang-status", err.message, true); }
});
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
  if (typeof appAsk === "function") {
    const ok = await appAsk({
      title: "Clear history?",
      text: "This forgets recent files and saved reading positions on this computer.",
      yes: "Clear history",
      no: "Cancel",
      danger: true,
    });
    if (!ok) return;
  }
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
if ($("set-translate-engine")) {
  $("set-translate-engine").addEventListener("change", async (e) => {
    const eng = e.target.value;
    $("set-translate-llm-wrap").hidden = eng !== "llm";
    try {
      await jsonApi("/api/settings", { translate_engine: eng });
      updateStatus("lang-status", eng === "llm" ? "Smart AI translation enabled." : "Built-in offline translation selected.");
    } catch (err) { updateStatus("lang-status", err.message, true); }
  });
  $("set-translate-save").addEventListener("click", async () => {
    const payload = {
      translate_engine: $("set-translate-engine").value,
      translate_api_url: $("set-translate-url").value.trim(),
      translate_model: $("set-translate-model").value.trim(),
    };
    const key = $("set-translate-key").value.trim();
    if (key) payload.translate_api_key = key;
    try {
      const s = await jsonApi("/api/settings", payload);
      $("set-translate-key").value = "";
      $("set-translate-key-state").textContent = s.translate_key_set ? "· saved" : "";
      $("set-translate-key-clear").hidden = !s.translate_key_set;
      updateStatus("lang-status", "AI translation settings saved.");
    } catch (err) { updateStatus("lang-status", err.message, true); }
  });
  $("set-translate-test")?.addEventListener("click", async () => {
    const btn = $("set-translate-test");
    btn.disabled = true;
    updateStatus("lang-status", "Testing AI translation connection…");
    try {
      const payload = {
        translate_api_url: $("set-translate-url").value.trim(),
        translate_model: $("set-translate-model").value.trim(),
      };
      const key = $("set-translate-key").value.trim();
      if (key) payload.translate_api_key = key;
      const res = await jsonApi("/api/translate/test", payload);
      updateStatus("lang-status", `Connected to ${res.model} (${res.ms} ms) — sample: "${res.sample}"`);
    } catch (err) {
      updateStatus("lang-status", err.message, true);
    } finally {
      btn.disabled = false;
    }
  });
  $("set-translate-key-clear").addEventListener("click", async () => {
    try {
      await jsonApi("/api/settings", { clear_translate_key: true });
      $("set-translate-key-state").textContent = "";
      $("set-translate-key-clear").hidden = true;
      updateStatus("lang-status", "AI translation API key removed.");
    } catch (err) { updateStatus("lang-status", err.message, true); }
  });
}
if ($("set-translate-glossary-save")) {
  $("set-translate-glossary-save").addEventListener("click", async () => {
    try {
      const gloss = $("set-translate-glossary").value.trim();
      await jsonApi("/api/settings", { translate_glossary: gloss });
      updateStatus("lang-status", gloss ? "Translation glossary saved." : "Translation glossary cleared.");
    } catch (err) { updateStatus("lang-status", err.message, true); }
  });
}
async function saveSettings() {
  const data = { check: $("set-check").checked, repo: $("set-repo").value.trim(),
                 ocr_quality: $("set-ocr-quality").value, ocr_gpu: $("set-ocr-gpu").checked };
  if ($("set-token").value.trim()) data.token = $("set-token").value.trim();
  if ($("set-translate-engine")) {
    data.translate_engine = $("set-translate-engine").value;
    if ($("set-translate-url")) data.translate_api_url = $("set-translate-url").value.trim();
    if ($("set-translate-model")) data.translate_model = $("set-translate-model").value.trim();
    const tKey = $("set-translate-key")?.value.trim();
    if (tKey) data.translate_api_key = tKey;
  }
  if ($("set-translate-glossary")) {
    data.translate_glossary = $("set-translate-glossary").value.trim();
  }
  const s = await jsonApi("/api/settings", data);
  $("set-token").value = "";
  $("set-token-state").textContent = s.token_set ? "· saved" : "";
  $("set-token-clear").hidden = !s.token_set;
  if ($("set-translate-key")) {
    $("set-translate-key").value = "";
    $("set-translate-key-state").textContent = s.translate_key_set ? "· saved" : "";
    $("set-translate-key-clear").hidden = !s.translate_key_set;
  }
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
$("show-audit")?.addEventListener("click", async () => {
  $("text-title").textContent = "Security audit log";
  $("text-body").textContent = "Loading…";
  $("text-dialog").showModal();
  try {
    const data = await jsonApi("/api/audit");
    const integrity = data.integrity || { valid: true, count: 0, errors: [] };
    const head = integrity.valid
      ? `Chain integrity: OK (${integrity.count} entries)\n\n`
      : `Chain integrity: FAILED (${(integrity.errors || []).length} error(s))\n\n`;
    const lines = (data.entries || []).map((e) =>
      `${e.timestamp}  ${e.action}  [${e.status}]  ${e.actor}  ${e.target || "-"}`);
    $("text-body").textContent = head + (lines.length ? lines.join("\n") : "No security events recorded yet.");
  } catch (e) { $("text-body").textContent = "Couldn't load audit log: " + e.message; }
  $("text-body").scrollTop = 0;
});
$("verify-audit")?.addEventListener("click", async () => {
  updateStatus("audit-status", "Verifying SHA-256 hash chain…");
  try {
    const data = await jsonApi("/api/audit");
    const integrity = data.integrity || { valid: true, count: 0, errors: [] };
    if (integrity.valid) {
      updateStatus("audit-status", `Audit log verified OK (${integrity.count} entries).`);
    } else {
      updateStatus("audit-status", `Integrity FAILED: ${(integrity.errors || []).length} tampered or broken entry(ies).`, true);
    }
  } catch (e) { updateStatus("audit-status", e.message, true); }
});
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
window.ZpdfI18n?.load?.(token)?.catch?.(() => {});
load();

async function loadAiModels() {
  const list = $("set-ai-models-list");
  if (!list) return;
  const info = await jsonApi("/api/models/status");
  renderAiModels(info.models || []);
}

function renderAiModels(models) {
  const list = $("set-ai-models-list");
  if (!list) return;
  list.replaceChildren(...models.map((m) => {
    const li = document.createElement("li");
    li.className = "watch-item";
    li.style.alignItems = "center";
    const infoBox = document.createElement("div");
    infoBox.className = "wi-text";
    const title = document.createElement("div");
    title.style.fontWeight = "600";
    const szMb = m.size_bytes > 0 ? `${Math.max(1, Math.round(m.size_bytes / 1_000_000))} MB` : `~${m.approx_mb} MB`;
    title.textContent = `${m.name} (${m.installed ? `Installed · ${szMb}` : `Not installed · ${szMb}`})`;
    const desc = document.createElement("div");
    desc.className = "dlg-hint";
    desc.style.margin = "2px 0 0";
    desc.textContent = m.description;
    infoBox.append(title, desc);

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = m.installed ? "btn secondary pb-small" : "btn pb-small";
    btn.textContent = m.installed ? "Remove" : "Download";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      if (m.installed) {
        updateStatus("ai-models-status", `Removing ${m.name}…`);
        try {
          const res = await jsonApi("/api/models/delete", { id: m.id });
          renderAiModels(res.models || []);
          updateStatus("ai-models-status", `Removed ${m.name}.`);
        } catch (err) {
          updateStatus("ai-models-status", err.message || String(err), true);
          btn.disabled = false;
        }
        return;
      }

      const dlWrap = $("ai-dl-wrap"), dlBar = $("ai-dl-bar"), dlCancel = $("ai-dl-cancel");
      let activeJobId = null, wasCancelled = false;
      const setBar = (frac) => {
        if (!dlBar) return;
        const pct = Math.max(0, Math.min(100, Math.round((frac || 0) * 100)));
        dlBar.setAttribute("aria-valuenow", String(pct));
        dlBar.firstElementChild.style.width = `${pct}%`;
      };
      if (dlCancel) {
        dlCancel.disabled = false;
        dlCancel.textContent = "Cancel";
        dlCancel.onclick = async () => {
          if (!activeJobId) return;
          wasCancelled = true;
          dlCancel.disabled = true;
          dlCancel.textContent = "Cancelling…";
          try { await jsonApi(`/api/job/${activeJobId}/cancel`, {}); } catch {}
        };
      }
      const aiDlUi = {
        start(id) {
          activeJobId = id;
          if (dlWrap) dlWrap.hidden = false;
          setBar(0);
        },
        message(msg) { updateStatus("ai-models-status", msg); },
        progress(f) { setBar(f); },
        cancelled() { wasCancelled = true; },
        end() {
          activeJobId = null;
          if (dlWrap) dlWrap.hidden = true;
          setBar(0);
        },
      };
      updateStatus("ai-models-status", `Downloading ${m.name} (~${m.approx_mb} MB)…`);
      try {
        let res;
        if (typeof runJob === "function") {
          const resp = await runJob("/api/models/download", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: m.id }),
          }, aiDlUi);
          if (!resp || wasCancelled) {
            updateStatus("ai-models-status", `Cancelled downloading ${m.name}.`);
            btn.disabled = false;
            return;
          }
          res = await resp.json();
        } else {
          res = await jsonApi("/api/models/download", { id: m.id });
        }
        renderAiModels(res.models || []);
        updateStatus("ai-models-status", `Installed ${m.name}.`);
      } catch (err) {
        updateStatus("ai-models-status", err.message || String(err), true);
        btn.disabled = false;
      }
    });
    li.append(infoBox, btn);
    return li;
  }));
}
