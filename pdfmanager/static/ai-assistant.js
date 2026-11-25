/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Local AI Document Assistant modal for ZPDF Reader (Ask this PDF with clickable
// page citations, Document/Chapter Summary, and Structured Table/Key-Value Extraction).
// Uses the user's configured Ollama / LM Studio / OpenAI-compatible endpoint in Settings.

import { el, svg } from "./readerkit.js";

export async function openAiAssistant(reader, initialTab = "ask") {
  if (!reader?.doc || !reader?.activeTab) {
    reader?.toast?.("Open a PDF first to use the Local AI Assistant.");
    return;
  }

  const overlay = el("div", {
    class: "rd-modal-backdrop",
    role: "dialog",
    "aria-modal": "true",
    "aria-label": "Local AI Document Assistant",
  });
  Object.assign(overlay.style, {
    position: "fixed", inset: "0", zIndex: "1250", background: "rgb(0 0 0 / .45)",
    display: "grid", placeItems: "center", padding: "16px",
  });

  const card = el("div", { class: "rd-modal-card" });
  Object.assign(card.style, {
    width: "min(640px, 100%)", maxHeight: "86vh", overflowY: "auto",
    background: "var(--surface)", color: "var(--text)", borderRadius: "12px",
    border: "1px solid var(--border)", boxShadow: "var(--shadow-pop)", padding: "18px 20px",
    display: "flex", flexDirection: "column", gap: "12px",
  });

  const close = () => overlay.remove();
  const header = el("div", { style: "display:flex;align-items:center;justify-content:space-between;gap:8px" },
    el("div", {},
      el("strong", { style: "font-size:15px" }, "Local AI Document Assistant"),
      el("div", { style: "font-size:12px;color:var(--muted)" }, "Uses your local Ollama / LM Studio endpoint from Settings.")),
    el("button", { type: "button", class: "rd-btn compact", title: "Close", html: svg("x"), onclick: close }));

  let mode = ["ask", "summarize", "extract"].includes(initialTab) ? initialTab : "ask";
  const tabBtns = {};
  const tabBar = el("div", { style: "display:flex;gap:6px;border-bottom:1px solid var(--border);padding-bottom:8px" });
  for (const [id, label] of [["ask", "Ask this PDF"], ["summarize", "Summarize"], ["extract", "Extract Table / KV"]]) {
    const b = el("button", { type: "button", class: "rd-btn compact" }, label);
    b.addEventListener("click", () => switchTab(id));
    tabBtns[id] = b;
    tabBar.append(b);
  }

  const controls = el("div", { style: "display:flex;flex-direction:column;gap:8px" });
  const outputWrap = el("div", { style: "display:flex;flex-direction:column;gap:8px" });
  const citeBar = el("div", { style: "display:flex;flex-wrap:wrap;gap:6px" });
  const outputPre = el("pre", {
    style: "margin:0;white-space:pre-wrap;word-break:break-word;font-family:inherit;font-size:13.5px;line-height:1.5;padding:12px;border-radius:8px;background:var(--bg);border:1px solid var(--border);min-height:96px;max-height:42vh;overflow:auto",
  }, "Ready. Choose a prompt above and click Run.");
  const exportBar = el("div", { style: "display:flex;gap:8px;align-items:center" });

  let lastExtract = null;

  const renderCitations = (citations) => {
    citeBar.replaceChildren();
    if (!Array.isArray(citations) || !citations.length) return;
    citeBar.append(el("span", { style: "font-size:12px;color:var(--muted);align-self:center" }, "Jump to cited page:"));
    for (const p of citations) {
      const chip = el("button", { type: "button", class: "rd-btn compact" }, `p. ${p}`);
      chip.addEventListener("click", () => {
        reader.goToPage(p);
        close();
      });
      citeBar.append(chip);
    }
  };

  const renderExportButtons = () => {
    exportBar.replaceChildren();
    if (!lastExtract) return;
    const copyBtn = el("button", { type: "button", class: "rd-btn compact" }, "Copy");
    copyBtn.addEventListener("click", async () => {
      const txt = lastExtract.format === "csv" ? lastExtract.csv : JSON.stringify(lastExtract.data, null, 2);
      await navigator.clipboard?.writeText(txt).catch(() => undefined);
      reader.toast("Copied extracted data to clipboard.");
    });
    const dlJson = el("button", { type: "button", class: "rd-btn compact" }, "Download JSON");
    dlJson.addEventListener("click", () => _downloadText(JSON.stringify(lastExtract.data, null, 2), "extracted.json", "application/json"));
    const dlCsv = el("button", { type: "button", class: "rd-btn compact" }, "Download CSV");
    dlCsv.addEventListener("click", () => _downloadText(lastExtract.csv || "", "extracted.csv", "text/csv"));
    exportBar.append(copyBtn, dlJson, dlCsv);
  };

  const runPost = async (endpoint, fields) => {
    outputPre.textContent = "Thinking locally…";
    citeBar.replaceChildren();
    exportBar.replaceChildren();
    lastExtract = null;
    try {
      const bytes = await reader._currentBytes();
      const res = await reader._post(endpoint, bytes, reader.activeTab, fields);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || res.statusText);
      return data;
    } catch (e) {
      outputPre.textContent = `Could not reach local AI endpoint: ${e.message || e}\n\nTip: Configure Ollama or LM Studio in Settings → AI / Translation.`;
      return null;
    }
  };

  const switchTab = (next) => {
    mode = next;
    for (const [id, b] of Object.entries(tabBtns)) {
      b.setAttribute("aria-pressed", String(id === mode));
      b.style.fontWeight = id === mode ? "600" : "";
    }
    controls.replaceChildren();
    const inputStyle = "width:100%;padding:8px 10px;border:1px solid var(--border-strong);border-radius:6px;background:var(--bg);color:var(--text);box-sizing:border-box";

    if (mode === "ask") {
      const qInput = el("input", { type: "text", placeholder: "Ask a question about this PDF (e.g. What are the warranty terms?)", style: inputStyle });
      const runBtn = el("button", { type: "button", class: "rd-btn" }, "Ask");
      const go = async () => {
        const q = (qInput.value || "").trim();
        if (!q) return;
        const data = await runPost("/api/ai/ask", { question: q });
        if (!data) return;
        outputPre.textContent = data.answer || "(no answer)";
        renderCitations(data.citations);
      };
      runBtn.addEventListener("click", go);
      qInput.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      controls.append(el("div", { style: "display:flex;gap:8px" }, qInput, runBtn));
      setTimeout(() => qInput.focus(), 10);
    } else if (mode === "summarize") {
      const pagesInput = el("input", { type: "text", placeholder: "Pages (optional, e.g. 1-5 or leave blank for all)", style: inputStyle });
      const styleSel = el("select", { style: "padding:7px 10px;border:1px solid var(--border-strong);border-radius:6px;background:var(--bg);color:var(--text)" },
        el("option", { value: "bullets" }, "Key Bullets"),
        el("option", { value: "executive" }, "Executive Summary"),
        el("option", { value: "study" }, "Study Notes"));
      const runBtn = el("button", { type: "button", class: "rd-btn" }, "Summarize");
      runBtn.addEventListener("click", async () => {
        const data = await runPost("/api/ai/summarize", { pages: pagesInput.value.trim(), style: styleSel.value });
        if (!data) return;
        outputPre.textContent = data.summary || "(empty summary)";
        renderCitations(data.citations);
      });
      controls.append(el("div", { style: "display:flex;gap:8px;flex-wrap:wrap" }, pagesInput, styleSel, runBtn));
    } else {
      const schemaSel = el("select", { style: "padding:7px 10px;border:1px solid var(--border-strong);border-radius:6px;background:var(--bg);color:var(--text)" },
        el("option", { value: "key_values" }, "Key-Value Pairs"),
        el("option", { value: "table" }, "Table Rows"));
      const fmtSel = el("select", { style: "padding:7px 10px;border:1px solid var(--border-strong);border-radius:6px;background:var(--bg);color:var(--text)" },
        el("option", { value: "json" }, "JSON"),
        el("option", { value: "csv" }, "CSV"));
      const fieldsInput = el("input", { type: "text", placeholder: "Fields to extract (optional, e.g. Invoice Number, Date, Total)", style: inputStyle });
      const runBtn = el("button", { type: "button", class: "rd-btn" }, "Extract");
      runBtn.addEventListener("click", async () => {
        const data = await runPost("/api/ai/extract", {
          schema: schemaSel.value, format: fmtSel.value, fields: fieldsInput.value.trim(),
        });
        if (!data) return;
        lastExtract = data;
        outputPre.textContent = data.format === "csv" ? (data.csv || "") : JSON.stringify(data.data, null, 2);
        renderExportButtons();
      });
      controls.append(
        fieldsInput,
        el("div", { style: "display:flex;gap:8px;align-items:center" }, schemaSel, fmtSel, runBtn));
    }
  };

  outputWrap.append(citeBar, outputPre, exportBar);
  card.append(header, tabBar, controls, outputWrap);
  overlay.append(card);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  document.body.append(overlay);
  switchTab(mode);
}

function _downloadText(text, filename, mime) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
