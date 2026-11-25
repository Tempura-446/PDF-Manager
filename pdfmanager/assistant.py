# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Optional Local AI Document Assistant for ZPDF.

Reuses the user's configured Ollama / LM Studio / OpenAI-compatible LLM
endpoint from ``settings.json`` and delegates HTTP calls exclusively through
``pdfmanager.i18n._http_post_json`` (preserving loopback-HTTP allowance,
HTTPS-only remote enforcement, redirect sanitization, and network policy).

Capabilities:
- ``ask_pdf``: Question answering grounded in ranked PDF pages with ``[p. N]``
  citations.
- ``summarize_pdf``: Full-document or page-range summaries (bullets, executive,
  study notes) with page citations.
- ``extract_structured``: Key-value or table extraction to JSON and CSV.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from pdfmanager import core, i18n, update

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}", re.IGNORECASE)
_CITE_RE = re.compile(r"\[(?:p{1,2}\.?|page)\s*([0-9,\s\-]+)\]", re.IGNORECASE)
_STOPWORDS = frozenset({
    "the", "and", "for", "that", "this", "with", "from", "what", "when",
    "where", "which", "who", "how", "why", "are", "was", "were", "have",
    "has", "had", "does", "did", "can", "could", "would", "should", "about",
    "into", "your", "their", "there", "here", "some", "any", "all",
})


def _llm_config(api_url: str | None = None, model: str | None = None, api_key: str | None = None):
    cfg = update.settings()
    url = (api_url or cfg.get("translate_api_url") or "http://localhost:11434/v1").strip().rstrip("/")
    if not url:
        url = "http://localhost:11434/v1"
    mdl = (model or cfg.get("translate_model") or "qwen2.5:7b").strip()
    key = api_key if api_key is not None else (cfg.get("translate_api_key") or "")
    return url, mdl, str(key).strip()


def _chat(
    system_prompt: str,
    user_prompt: str,
    *,
    api_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.2,
    timeout: int = 180,
) -> tuple[str, str]:
    url, mdl, key = _llm_config(api_url, model, api_key)
    endpoint = url if url.endswith("/chat/completions") else f"{url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": mdl,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    resp = i18n._http_post_json(endpoint, payload, headers=headers, timeout=timeout)
    try:
        content = resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise core.PdfManagerError("Local AI endpoint returned an unexpected response format.") from None
    return str(content or "").strip(), mdl


def extract_pages(path: str | Path, password: str | None = None, pages_spec: str | None = None) -> list[dict]:
    """Extract 1-indexed page text from ``path`` (optionally filtered by ``pages_spec``)."""
    reader = core.open_pdf(path, password)
    total = len(reader.pages)
    if pages_spec and str(pages_spec).strip():
        indices = core.parse_pages(str(pages_spec).strip(), total)
    else:
        indices = list(range(total))
    out = []
    for idx in indices:
        txt = (reader.pages[idx].extract_text() or "").strip()
        out.append({"page": idx + 1, "text": txt})
    return out


def select_relevant_pages(
    pages: list[dict],
    query: str,
    *,
    max_pages: int = 6,
    max_chars: int = 18000,
) -> list[dict]:
    """Rank pages by lightweight lexical overlap with ``query`` and trim to budget."""
    q_tokens = {t.lower() for t in _TOKEN_RE.findall(query or "") if t.lower() not in _STOPWORDS}
    scored = []
    for pos, pg in enumerate(pages):
        txt = pg.get("text") or ""
        if not q_tokens:
            score = 0.0
        else:
            p_tokens = {t.lower() for t in _TOKEN_RE.findall(txt)}
            overlap = len(q_tokens & p_tokens)
            score = overlap + (0.15 if txt else 0.0)
        scored.append((score, -pos, pg))
    scored.sort(reverse=True)
    picked = []
    used = 0
    for _score, _neg_pos, pg in scored[: max(1, int(max_pages))]:
        txt = pg.get("text") or ""
        remain = max_chars - used
        if remain <= 0:
            break
        snippet = txt[:remain]
        picked.append({"page": pg["page"], "text": snippet})
        used += len(snippet)
    return picked


def parse_citations(text: str, max_page: int | None = None) -> list[int]:
    """Extract sorted, unique 1-indexed page numbers from ``[p. N]`` / ``[Page N]`` tags."""
    found: set[int] = set()
    for match in _CITE_RE.finditer(text or ""):
        for num_str in re.findall(r"\d+", match.group(1)):
            n = int(num_str)
            if n >= 1 and (max_page is None or n <= max_page):
                found.add(n)
    return sorted(found)


def _format_context(pages: list[dict], max_chars: int = 18000) -> str:
    chunks = []
    used = 0
    for pg in pages:
        body = (pg.get("text") or "").strip()
        if not body:
            continue
        header = f"[Page {pg['page']}]\n"
        room = max_chars - used - len(header)
        if room <= 0:
            break
        chunk = header + body[:room]
        chunks.append(chunk)
        used += len(chunk) + 2
    return "\n\n".join(chunks)


def ask_pdf(
    path: str | Path,
    question: str,
    *,
    password: str | None = None,
    api_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    max_pages: int = 6,
) -> dict:
    """Answer ``question`` using the most relevant pages of ``path`` with page citations."""
    q = (question or "").strip()
    if not q:
        raise core.PdfManagerError("Enter a question to ask about this PDF.")
    all_pages = extract_pages(path, password=password)
    picked = select_relevant_pages(all_pages, q, max_pages=max_pages)
    context = _format_context(picked)
    if not context:
        raise core.PdfManagerError("This PDF has no selectable text yet. Run OCR first to ask questions.")

    system_prompt = (
        "You are ZPDF's offline document assistant. Answer the user's question strictly "
        "from the provided PDF page excerpts. Cite supporting pages inline as [p. N]. "
        "If the answer is not in the excerpts, say so plainly."
    )
    user_prompt = f"DOCUMENT EXCERPTS:\n{context}\n\nQUESTION: {q}"
    answer, mdl = _chat(system_prompt, user_prompt, api_url=api_url, model=model, api_key=api_key)
    citations = parse_citations(answer, max_page=len(all_pages))
    return {
        "answer": answer,
        "citations": citations,
        "pages_used": [p["page"] for p in picked],
        "model": mdl,
    }


def summarize_pdf(
    path: str | Path,
    *,
    password: str | None = None,
    pages_spec: str | None = None,
    style: str = "bullets",
    api_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> dict:
    """Summarize ``path`` (or a page range) in bullets, executive, or study style."""
    pages = extract_pages(path, password=password, pages_spec=pages_spec)
    context = _format_context(pages, max_chars=22000)
    if not context:
        raise core.PdfManagerError("No selectable text found in the chosen pages. Run OCR first.")

    style_key = (style or "bullets").strip().lower()
    style_hint = {
        "bullets": "Provide concise bullet points covering the key facts, decisions, and numbers.",
        "executive": "Write a tight 1-paragraph executive summary followed by 3–5 key takeaways.",
        "study": "Write structured study notes with key concepts, definitions, and takeaways.",
    }.get(style_key, "Provide concise bullet points covering the key facts.")

    system_prompt = (
        "You are ZPDF's offline document assistant. Summarize the provided PDF pages accurately. "
        f"{style_hint} Cite page numbers inline as [p. N]."
    )
    user_prompt = f"PDF PAGES:\n{context}"
    summary, mdl = _chat(system_prompt, user_prompt, api_url=api_url, model=model, api_key=api_key)
    max_p = max((p["page"] for p in pages), default=1)
    return {
        "summary": summary,
        "citations": parse_citations(summary, max_page=max_p),
        "pages_used": [p["page"] for p in pages if p.get("text")],
        "model": mdl,
    }


def _strip_json_fences(raw: str) -> str:
    txt = (raw or "").strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", txt)
        txt = re.sub(r"\s*```$", "", txt)
    return txt.strip()


def _rows_to_csv(data) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    if isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
        headers: list[str] = []
        for row in data:
            for k in row.keys():
                if k not in headers:
                    headers.append(str(k))
        writer.writerow(headers)
        for row in data:
            writer.writerow([row.get(h, "") for h in headers])
    elif isinstance(data, dict):
        writer.writerow(["field", "value"])
        for k, v in data.items():
            writer.writerow([k, json.dumps(v) if isinstance(v, (dict, list)) else v])
    else:
        writer.writerow(["value"])
        writer.writerow([json.dumps(data) if not isinstance(data, str) else data])
    return buf.getvalue()


def extract_structured(
    path: str | Path,
    *,
    schema: str = "key_values",
    fields: str | None = None,
    fmt: str = "json",
    pages_spec: str | None = None,
    password: str | None = None,
    api_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> dict:
    """Extract structured key-value pairs or tables into JSON and CSV."""
    pages = extract_pages(path, password=password, pages_spec=pages_spec)
    context = _format_context(pages, max_chars=20000)
    if not context:
        raise core.PdfManagerError("No selectable text found in the chosen pages. Run OCR first.")

    schema_key = (schema or "key_values").strip().lower()
    fields_hint = f" Focus specifically on these fields: {fields.strip()}." if (fields and fields.strip()) else ""
    if schema_key == "table":
        schema_prompt = (
            "Extract the primary tabular data as a JSON array of flat objects (one object per row, "
            "consistent column keys)." + fields_hint
        )
    else:
        schema_prompt = (
            "Extract important key-value metadata and facts as a JSON array of objects with keys "
            '{"field", "value", "page"}.' + fields_hint
        )

    system_prompt = (
        "You are ZPDF's structured data extractor. Return ONLY valid JSON (no markdown fences, "
        "no commentary). " + schema_prompt
    )
    raw, mdl = _chat(
        system_prompt, f"PDF PAGES:\n{context}",
        api_url=api_url, model=model, api_key=api_key, temperature=0.0,
    )
    cleaned = _strip_json_fences(raw)
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        parsed = [{"field": "raw_output", "value": raw}]

    csv_text = _rows_to_csv(parsed)
    return {
        "data": parsed,
        "csv": csv_text,
        "format": "csv" if (fmt or "").strip().lower() == "csv" else "json",
        "model": mdl,
    }
