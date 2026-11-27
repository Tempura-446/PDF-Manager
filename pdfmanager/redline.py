# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Smart Redline Contract & Specification Diff Engine.

Compares two PDF versions at the clause/paragraph level, builds inline word-level
insertion/deletion redline spans, automatically flags material numeric, currency,
percentage, and date changes, exports clean Redline Review Reports (.pdf, .html,
.md), and optionally invokes the local LLM to explain material changes & risks.
"""

import difflib
import html
import re
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from pdfmanager import core

_MATERIAL_TOKEN_RE = re.compile(
    r"(?:"
    r"[$€£¥]\s?\d[\d,]*(?:\.\d+)?"
    r"|\b\d+(?:\.\d+)?%"
    r"|\b(?:Net|USD|EUR|GBP|CAD)\s+\d[\d,]*(?:\.\d+)?"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
    r"|\b\d+\s+(?:days?|months?|years?|weeks?|hours?|business\s+days?)\b"
    r")",
    re.IGNORECASE,
)


def _split_clauses(text):
    raw_lines = [ln.strip() for ln in str(text or "").splitlines()]
    clauses = []
    buf = []
    for ln in raw_lines:
        if not ln:
            if buf:
                clauses.append(" ".join(buf))
                buf = []
            continue
        # Start a new clause on numbered headings or bullets (e.g. "1.2 ", "Section 4", "• ")
        if buf and re.match(r"^(?:Section\s+\d|Article\s+\d|\d+(?:\.\d+)*[.)]\s+|[-•*]\s+)", ln, re.IGNORECASE):
            clauses.append(" ".join(buf))
            buf = [ln]
        else:
            buf.append(ln)
    if buf:
        clauses.append(" ".join(buf))
    return [c for c in clauses if c]


def extract_clauses(pdf_path, password=None):
    """Extract ordered clause dicts ``{page, index, text}`` from a PDF."""
    reader = core.open_pdf(pdf_path, password)
    out = []
    idx = 0
    for page_num, page in enumerate(reader.pages, 1):
        txt = page.extract_text() or ""
        for clause in _split_clauses(txt):
            idx += 1
            out.append({"page": page_num, "index": idx, "text": clause})
    return out


def _word_diff_spans(old_text, new_text):
    a = re.findall(r"\S+|\s+", old_text)
    b = re.findall(r"\S+|\s+", new_text)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    spans = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            spans.append({"op": "equal", "text": "".join(a[i1:i2])})
        elif tag == "delete":
            spans.append({"op": "delete", "text": "".join(a[i1:i2])})
        elif tag == "insert":
            spans.append({"op": "insert", "text": "".join(b[j1:j2])})
        elif tag == "replace":
            spans.append({"op": "delete", "text": "".join(a[i1:i2])})
            spans.append({"op": "insert", "text": "".join(b[j1:j2])})
    return spans


def _material_changes(old_text, new_text):
    old_nums = _MATERIAL_TOKEN_RE.findall(old_text or "")
    new_nums = _MATERIAL_TOKEN_RE.findall(new_text or "")
    if old_nums == new_nums:
        return []
    changes = []
    old_set = [t.strip() for t in old_nums]
    new_set = [t.strip() for t in new_nums]
    for tok in old_set:
        if tok not in new_set:
            changes.append(f"-{tok}")
    for tok in new_set:
        if tok not in old_set:
            changes.append(f"+{tok}")
    return changes


def compute_redline(old_pdf, new_pdf, *, password=None, old_password=None, new_password=None):
    """Compare ``old_pdf`` and ``new_pdf`` clause-by-clause and return a structured redline report."""
    old_p = Path(old_pdf)
    new_p = Path(new_pdf)
    old_clauses = extract_clauses(old_p, old_password if old_password is not None else password)
    new_clauses = extract_clauses(new_p, new_password if new_password is not None else password)

    old_texts = [c["text"] for c in old_clauses]
    new_texts = [c["text"] for c in new_clauses]
    sm = difflib.SequenceMatcher(a=old_texts, b=new_texts, autojunk=False)

    changes = []
    counts = {"added": 0, "removed": 0, "modified": 0, "material": 0}
    cid = 0

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            pair_n = min(i2 - i1, j2 - j1)
            for k in range(pair_n):
                oc = old_clauses[i1 + k]
                nc = new_clauses[j1 + k]
                cid += 1
                nums = _material_changes(oc["text"], nc["text"])
                is_mat = bool(nums)
                counts["modified"] += 1
                if is_mat:
                    counts["material"] += 1
                changes.append({
                    "id": cid,
                    "kind": "modified",
                    "old_page": oc["page"],
                    "new_page": nc["page"],
                    "old_text": oc["text"],
                    "new_text": nc["text"],
                    "spans": _word_diff_spans(oc["text"], nc["text"]),
                    "material": is_mat,
                    "numeric_changes": nums,
                })
            for k in range(i1 + pair_n, i2):
                oc = old_clauses[k]
                cid += 1
                nums = _material_changes(oc["text"], "")
                counts["removed"] += 1
                if nums:
                    counts["material"] += 1
                changes.append({
                    "id": cid,
                    "kind": "removed",
                    "old_page": oc["page"],
                    "new_page": None,
                    "old_text": oc["text"],
                    "new_text": "",
                    "spans": [{"op": "delete", "text": oc["text"]}],
                    "material": bool(nums),
                    "numeric_changes": nums,
                })
            for k in range(j1 + pair_n, j2):
                nc = new_clauses[k]
                cid += 1
                nums = _material_changes("", nc["text"])
                counts["added"] += 1
                if nums:
                    counts["material"] += 1
                changes.append({
                    "id": cid,
                    "kind": "added",
                    "old_page": None,
                    "new_page": nc["page"],
                    "old_text": "",
                    "new_text": nc["text"],
                    "spans": [{"op": "insert", "text": nc["text"]}],
                    "material": bool(nums),
                    "numeric_changes": nums,
                })
        elif tag == "delete":
            for k in range(i1, i2):
                oc = old_clauses[k]
                cid += 1
                nums = _material_changes(oc["text"], "")
                counts["removed"] += 1
                if nums:
                    counts["material"] += 1
                changes.append({
                    "id": cid,
                    "kind": "removed",
                    "old_page": oc["page"],
                    "new_page": None,
                    "old_text": oc["text"],
                    "new_text": "",
                    "spans": [{"op": "delete", "text": oc["text"]}],
                    "material": bool(nums),
                    "numeric_changes": nums,
                })
        elif tag == "insert":
            for k in range(j1, j2):
                nc = new_clauses[k]
                cid += 1
                nums = _material_changes("", nc["text"])
                counts["added"] += 1
                if nums:
                    counts["material"] += 1
                changes.append({
                    "id": cid,
                    "kind": "added",
                    "old_page": None,
                    "new_page": nc["page"],
                    "old_text": "",
                    "new_text": nc["text"],
                    "spans": [{"op": "insert", "text": nc["text"]}],
                    "material": bool(nums),
                    "numeric_changes": nums,
                })

    counts["total_changes"] = len(changes)
    return {
        "old_name": old_p.name,
        "new_name": new_p.name,
        "summary": counts,
        "changes": changes,
    }


def explain_redline_ai(report, *, model=None):
    """Ask the configured local LLM to summarize material changes and flag risks."""
    from pdfmanager import assistant

    changes = report.get("changes") or []
    if not changes:
        return {"explanation": "No textual differences were found between the two versions.", "model": ""}
    lines = []
    for ch in changes[:25]:
        mat = " [MATERIAL]" if ch.get("material") else ""
        lines.append(
            f"#{ch['id']} ({ch['kind'].upper()}{mat}, p.{ch.get('old_page') or '-'} → p.{ch.get('new_page') or '-'}):\n"
            f"  OLD: {ch.get('old_text', '')[:300]}\n"
            f"  NEW: {ch.get('new_text', '')[:300]}"
        )
    sys_prompt = (
        "You are ZPDF's offline contract and specification redline reviewer. "
        "Summarize the key differences concisely, highlight any material changes to "
        "pricing, payment terms, dates, liability, or obligations, and reference change numbers like #1, #2."
    )
    user_prompt = (
        f"Compare {report.get('old_name')} vs {report.get('new_name')}:\n\n"
        + "\n".join(lines)
    )
    text, used_model = assistant._chat(sys_prompt, user_prompt, model=model)
    return {"explanation": text, "model": used_model}


def export_redline_markdown(report, *, ai_summary=None):
    """Render a redline report as clean GitHub-Flavored Markdown."""
    s = report["summary"]
    lines = [
        f"# Redline Report: {report['old_name']} → {report['new_name']}",
        "",
        f"- **Total changes:** {s['total_changes']} "
        f"({s['modified']} modified, {s['added']} added, {s['removed']} removed, "
        f"**{s['material']} material numeric/date**)",
        "",
    ]
    if ai_summary:
        lines.extend(["## AI Change Summary", "", ai_summary.strip(), ""])
    if not report["changes"]:
        lines.append("_No textual differences found._")
        return "\n".join(lines) + "\n"
    lines.append("## Clause-by-Clause Redline")
    lines.append("")
    for ch in report["changes"]:
        badge = " **[MATERIAL]**" if ch["material"] else ""
        pg = f"p.{ch['old_page'] or '-'} → p.{ch['new_page'] or '-'}"
        lines.append(f"### #{ch['id']} · {ch['kind'].upper()}{badge} ({pg})")
        inline = []
        for sp in ch["spans"]:
            t = sp["text"]
            if sp["op"] == "delete":
                inline.append(f"~~{t}~~")
            elif sp["op"] == "insert":
                inline.append(f"**{t}**")
            else:
                inline.append(t)
        lines.append("".join(inline))
        if ch["numeric_changes"]:
            lines.append(f"- Material values: `{', '.join(ch['numeric_changes'])}`")
        lines.append("")
    return "\n".join(lines)


def export_redline_html(report, *, ai_summary=None):
    """Render a standalone styled HTML Redline Review Report."""
    s = report["summary"]
    rows = []
    for ch in report["changes"]:
        parts = []
        for sp in ch["spans"]:
            esc = html.escape(sp["text"])
            if sp["op"] == "delete":
                parts.append(f"<del>{esc}</del>")
            elif sp["op"] == "insert":
                parts.append(f"<ins>{esc}</ins>")
            else:
                parts.append(esc)
        mat_badge = '<span class="mat">MATERIAL</span>' if ch["material"] else ""
        pg = f"p.{ch['old_page'] or '-'} → p.{ch['new_page'] or '-'}"
        rows.append(
            f'<div class="item {ch["kind"]}">'
            f'<div class="meta"><strong>#{ch["id"]} {ch["kind"].upper()}</strong> {mat_badge} <span>{pg}</span></div>'
            f'<div class="body">{"".join(parts)}</div></div>'
        )
    ai_block = (
        f'<div class="ai"><h2>AI Summary</h2><pre>{html.escape(ai_summary)}</pre></div>'
        if ai_summary else ""
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Redline: {html.escape(report['old_name'])} vs {html.escape(report['new_name'])}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:900px;margin:28px auto;padding:0 16px;line-height:1.5;}"
        "del{background:#fee2e2;color:#991b1b;text-decoration:line-through;padding:0 2px;}"
        "ins{background:#dcfce7;color:#166534;text-decoration:none;font-weight:600;padding:0 2px;}"
        ".item{border:1px solid #e5e7eb;border-radius:10px;padding:12px 14px;margin:10px 0;}"
        ".meta{font-size:12px;color:#4b5563;margin-bottom:6px;display:flex;gap:8px;align-items:center;}"
        ".mat{background:#fef3c7;color:#92400e;font-weight:700;padding:1px 6px;border-radius:4px;font-size:11px;}"
        ".ai{background:#f8fafc;border:1px solid #cbd5e1;border-radius:10px;padding:12px 14px;margin:14px 0;}"
        ".ai pre{white-space:pre-wrap;font-family:inherit;margin:0;}</style></head><body>"
        f"<h1>Redline Report: {html.escape(report['old_name'])} → {html.escape(report['new_name'])}</h1>"
        f"<p><strong>{s['total_changes']} changes</strong> "
        f"({s['modified']} modified, {s['added']} added, {s['removed']} removed, {s['material']} material)</p>"
        f"{ai_block}{''.join(rows) or '<p>No textual differences found.</p>'}</body></html>"
    )


def export_redline_pdf(report, output_pdf, *, ai_summary=None):
    """Render a clean PDF Redline Review Report with red deletions and green insertions."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    out = Path(output_pdf)
    out.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    body_style = ParagraphStyle("RedlineBody", parent=styles["Normal"], fontSize=10, leading=14)
    meta_style = ParagraphStyle("RedlineMeta", parent=styles["Heading4"], fontSize=10, spaceBefore=8, spaceAfter=3)

    s = report["summary"]
    story = [
        Paragraph(
            f"<b>Redline Report:</b> {_xml_escape(report['old_name'])} → {_xml_escape(report['new_name'])}",
            styles["Title"],
        ),
        Paragraph(
            f"Total changes: <b>{s['total_changes']}</b> "
            f"({s['modified']} modified, {s['added']} added, {s['removed']} removed, "
            f"<b>{s['material']} material</b>)",
            body_style,
        ),
        Spacer(1, 10),
    ]
    if ai_summary:
        story.append(Paragraph("<b>AI Change Summary</b>", styles["Heading2"]))
        for para in str(ai_summary).splitlines():
            if para.strip():
                story.append(Paragraph(_xml_escape(para.strip()), body_style))
        story.append(Spacer(1, 8))

    if not report["changes"]:
        story.append(Paragraph("No textual differences found between the two documents.", body_style))
    else:
        for ch in report["changes"]:
            mat = " [MATERIAL]" if ch["material"] else ""
            pg = f"p.{ch['old_page'] or '-'} → p.{ch['new_page'] or '-'}"
            story.append(Paragraph(f"#{ch['id']} · {ch['kind'].upper()}{mat} ({pg})", meta_style))
            markup = []
            for sp in ch["spans"]:
                esc = _xml_escape(sp["text"])
                if sp["op"] == "delete":
                    markup.append(f'<font color="#991b1b"><strike>{esc}</strike></font>')
                elif sp["op"] == "insert":
                    markup.append(f'<font color="#166534"><b>{esc}</b></font>')
                else:
                    markup.append(esc)
            story.append(Paragraph("".join(markup), body_style))
            story.append(Spacer(1, 4))

    doc = SimpleDocTemplate(str(out), pagesize=letter, leftMargin=48, rightMargin=48, topMargin=48, bottomMargin=48)
    doc.build(story)
    return out
