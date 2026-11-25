# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for v0.74.0 Local AI Document Assistant (Ask, Summarize, Structured Extract)."""

from __future__ import annotations

import json
from pathlib import Path

from reportlab.pdfgen import canvas


def _make_sample_pdf(path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.drawString(72, 720, "Invoice Number: INV-2026-042")
    c.drawString(72, 700, "Customer: Acme Robotics Corp")
    c.drawString(72, 680, "Total Amount Due: $1,250.00")
    c.showPage()
    c.drawString(72, 720, "Chapter 2: Warranty Terms")
    c.drawString(72, 700, "All hardware carries a 24-month limited warranty.")
    c.showPage()
    c.save()
    return path


def test_select_relevant_pages_and_parse_citations():
    from pdfmanager import assistant

    pages = [
        {"page": 1, "text": "Invoice INV-2026-042 Total $1250 Acme Robotics"},
        {"page": 2, "text": "Warranty terms 24 months limited hardware coverage"},
        {"page": 3, "text": "Shipping policy standard ground delivery"},
    ]
    picked = assistant.select_relevant_pages(pages, "What is the warranty period?", max_pages=2)
    assert picked[0]["page"] == 2
    assert len(picked) == 2

    cites = assistant.parse_citations("Covered for 24 months [p. 2] and billed on [Page 1], see [p. 2].", max_page=3)
    assert cites == [1, 2]


def test_ask_summarize_and_extract_with_mocked_llm(tmp_path, monkeypatch):
    from pdfmanager import assistant, i18n

    pdf = _make_sample_pdf(tmp_path / "doc.pdf")
    calls = []

    def fake_post(url, payload, *, headers=None, timeout=180):
        calls.append((url, payload))
        user_msg = payload["messages"][-1]["content"]
        if "QUESTION:" in user_msg:
            content = "The warranty lasts 24 months [p. 2] for Acme Robotics [p. 1]."
        elif "SUMMARIZE" in payload["messages"][0]["content"].upper():
            content = "- Invoice INV-2026-042 for $1,250.00 [p. 1]\n- 24-month hardware warranty [p. 2]"
        else:
            content = json.dumps([
                {"field": "Invoice Number", "value": "INV-2026-042", "page": 1},
                {"field": "Total Amount Due", "value": "$1,250.00", "page": 1},
            ])
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(i18n, "_http_post_json", fake_post)

    ans = assistant.ask_pdf(pdf, "How long is the warranty?", api_url="http://localhost:11434/v1", model="qwen2.5:7b")
    assert "24 months" in ans["answer"]
    assert ans["citations"] == [1, 2]
    assert ans["model"] == "qwen2.5:7b"

    summ = assistant.summarize_pdf(pdf, style="bullets", api_url="http://localhost:11434/v1", model="qwen2.5:7b")
    assert "INV-2026-042" in summ["summary"]
    assert summ["citations"] == [1, 2]

    ext = assistant.extract_structured(
        pdf, schema="key_values", fmt="csv",
        api_url="http://localhost:11434/v1", model="qwen2.5:7b",
    )
    assert len(ext["data"]) == 2
    assert "Invoice Number" in ext["csv"]
    assert "INV-2026-042" in ext["csv"]
    assert len(calls) == 3
