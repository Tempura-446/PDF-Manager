# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Vision-LLM (VLM) OCR and visual page translation via OpenAI-compatible APIs.

Works with local vision models (Ollama qwen2.5vl / gemma3 / llama3.2-vision,
LM Studio) or any OpenAI-compatible BYOK endpoint configured in ZPDF Settings.
Pure Python stdlib (urllib + base64); zero extra dependencies.
"""

import base64
import io
import json
import urllib.error
import urllib.request

from pdfmanager import core, update

_DEFAULT_OCR_PROMPT = (
    "Transcribe all readable text from this page image accurately. "
    "Preserve headings, paragraphs, and tables in clean Markdown. "
    "Output only the transcribed content with no preamble."
)


def _encode_pil_png(pil_img, max_dim=1600):
    img = pil_img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / float(max(w, h))
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _resolve_vlm_config(api_url=None, model=None, api_key=None):
    cfg = update.settings()
    url = (api_url or cfg.get("translate_api_url") or "http://localhost:11434/v1").strip().rstrip("/")
    mdl = (model or cfg.get("translate_model") or "qwen2.5vl:7b").strip()
    key = api_key if api_key is not None else (cfg.get("translate_api_key") or "")
    if not url.startswith(("http://", "https://")):
        raise core.PdfManagerError("Vision-LLM API URL must start with http:// or https://")
    return url, mdl, key


def vlm_ocr_image(
    pil_img,
    *,
    prompt=None,
    translate_to="",
    api_url=None,
    model=None,
    api_key=None,
    timeout=120,
):
    """Send a PIL image to an OpenAI-compatible Vision-LLM and return the text response."""
    url, mdl, key = _resolve_vlm_config(api_url=api_url, model=model, api_key=api_key)
    b64 = _encode_pil_png(pil_img)

    if prompt:
        user_prompt = str(prompt)
    elif translate_to:
        user_prompt = (
            f"Read all text and speech bubbles on this page image in natural reading order "
            f"and translate everything cleanly into {translate_to}. "
            f"Preserve dialogue breaks and structure. Output only the translation."
        )
    else:
        user_prompt = _DEFAULT_OCR_PROMPT

    payload = {
        "model": mdl,
        "temperature": 0.1,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
    }

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    endpoint = url if url.endswith("/chat/completions") else f"{url}/chat/completions"
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:240]
        raise core.PdfManagerError(f"Vision-LLM request failed (HTTP {exc.code}): {detail}") from None
    except Exception as exc:
        raise core.PdfManagerError(f"could not reach Vision-LLM at {endpoint}: {exc}") from None

    try:
        data = json.loads(raw)
        text = data["choices"][0]["message"]["content"]
    except Exception:
        raise core.PdfManagerError("unexpected response format from Vision-LLM") from None
    return str(text or "").strip()


def vlm_ocr_pdf_page(
    pdf_path,
    page_num,
    *,
    password=None,
    translate_to="",
    prompt=None,
    api_url=None,
    model=None,
    api_key=None,
    dpi=150,
):
    """Render a single PDF page and run Vision-LLM OCR or visual translation on it."""
    import pypdfium2 as pdfium

    p_idx = max(0, int(page_num) - 1)
    try:
        doc = pdfium.PdfDocument(str(pdf_path), password=password or None)
    except Exception as exc:
        raise core.PdfManagerError(f"could not open PDF for Vision-LLM: {exc}") from None
    try:
        if p_idx >= len(doc):
            raise core.PdfManagerError(f"page {page_num} out of range (document has {len(doc)} pages)")
        page = doc[p_idx]
        try:
            pil_img = page.render(scale=float(dpi) / 72.0).to_pil().convert("RGB")
        finally:
            page.close()
    finally:
        doc.close()

    text = vlm_ocr_image(
        pil_img,
        prompt=prompt,
        translate_to=translate_to,
        api_url=api_url,
        model=model,
        api_key=api_key,
    )
    url, mdl, _ = _resolve_vlm_config(api_url=api_url, model=model, api_key=api_key)
    return {
        "page": p_idx + 1,
        "model": mdl,
        "endpoint": url,
        "translate_to": translate_to,
        "text": text,
    }
