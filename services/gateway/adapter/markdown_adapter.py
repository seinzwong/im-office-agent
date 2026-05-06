from __future__ import annotations

from .ir_schema import ensure_ir_defaults


def ir_to_markdown(ir: dict) -> str:
    ir = ensure_ir_defaults(ir)
    meta = ir["meta"]
    lines: list[str] = [f"# {meta['title']}", ""]
    if meta.get("subtitle"):
        lines.extend([f"> {meta['subtitle']}", ""])
    facts = [
        ("Owner", meta.get("owner")),
        ("Date", meta.get("date")),
        ("Audience", meta.get("audience")),
    ]
    for label, value in facts:
        if value:
            lines.append(f"- **{label}:** {value}")
    if len(lines) > 2:
        lines.append("")

    for block in ir.get("blocks", []):
        if not isinstance(block, dict):
            continue
        lines.extend(_block_to_markdown(block))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _block_to_markdown(block: dict) -> list[str]:
    kind = block.get("kind")
    title = str(block.get("title") or "")
    out = [f"## {title}"]
    if block.get("description"):
        out.append(f"> {block['description']}")
    if kind == "cover":
        if block.get("kicker"):
            out.append(f"**{block['kicker']}**")
        if block.get("subtitle"):
            out.append(str(block["subtitle"]))
        return _with_source_refs(out, block)
    if kind == "split":
        out.extend(f"- {point}" for point in block.get("points", []))
        return _with_source_refs(out, block)
    if kind == "flow":
        if block.get("caption"):
            out.append(str(block["caption"]))
        out.extend(["```mermaid", "flowchart LR"])
        for node in block.get("nodes", []):
            if isinstance(node, dict):
                out.append(f"  {node.get('id')}[\"{node.get('label', node.get('id'))}\"]")
        for edge in block.get("edges", []):
            if isinstance(edge, list) and len(edge) >= 2:
                out.append(f"  {edge[0]} --> {edge[1]}")
            elif isinstance(edge, dict):
                out.append(f"  {edge.get('from')} --> {edge.get('to')}")
        out.append("```")
        return _with_source_refs(out, block)
    if kind == "metrics":
        rows = [["指标", "数值", "说明"]]
        rows.extend([[item.get("label", ""), item.get("value", ""), item.get("note", "")] for item in block.get("items", []) if isinstance(item, dict)])
        out.extend(_markdown_table(rows))
        return _with_source_refs(out, block)
    if kind == "cards":
        for card in block.get("cards", []):
            if isinstance(card, dict):
                out.append(f"### {card.get('title', '')}")
                meta = " | ".join(_display_meta_items(card.get("meta", [])))
                if meta:
                    out.append(meta)
                if card.get("body"):
                    out.append(str(card.get("body", "")))
        return _with_source_refs(out, block)
    if kind == "table":
        if block.get("caption"):
            out.append(str(block.get("caption") or ""))
        out.extend(_markdown_table([block.get("columns", []), *block.get("rows", [])]))
        return _with_source_refs(out, block)
    if kind == "timeline":
        for event in block.get("events", []):
            if isinstance(event, dict):
                meta = " | ".join(str(event.get(key) or "").strip() for key in ("owner", "status") if str(event.get(key) or "").strip())
                suffix = f" ({meta})" if meta else ""
                out.append(f"- **{event.get('date', '')} {event.get('title', '')}:** {event.get('body', '')}{suffix}")
        return _with_source_refs(out, block)
    if kind == "image":
        caption = block.get("caption") or title
        image = block.get("image") or ""
        out.append(f"![{caption}]({image})" if image else str(caption))
        return _with_source_refs(out, block)
    return _with_source_refs(out, block)


def _markdown_table(rows: list[list]) -> list[str]:
    if not rows:
        return []
    width = max(len(row) for row in rows if isinstance(row, list))
    normalized = [[str(cell) for cell in list(row) + [""] * (width - len(row))] for row in rows if isinstance(row, list)]
    if not normalized:
        return []
    return [
        "| " + " | ".join(normalized[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
        *["| " + " | ".join(row) + " |" for row in normalized[1:]],
    ]


def _with_source_refs(lines: list[str], block: dict) -> list[str]:
    refs = [_display_source_ref(item) for item in block.get("sourceRefs", [])] if isinstance(block.get("sourceRefs"), list) else []
    refs = [item for item in refs if item]
    if refs:
        lines.append("> 来源：" + " | ".join(refs))
    return lines


def _display_meta_items(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_display_meta_item(raw) for raw in value) if item]


def _display_meta_item(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lower = text.lower()
    if "待确认" in text or "unknown" in lower or lower == "n/a":
        return ""
    if "http://" in lower or "https://" in lower:
        return "文档链接"
    if "message:" in lower or "source:" in lower or "om_" in lower:
        return ""
    return text if len(text) <= 48 else ""


def _display_source_ref(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lower = text.lower()
    if "http://" in lower or "https://" in lower:
        return "文档链接"
    if "message:" in lower or "om_" in lower:
        return "消息"
    return text if len(text) <= 40 else text[:37] + "..."
