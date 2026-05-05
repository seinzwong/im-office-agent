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
    if kind == "cover":
        out = [f"## {title}"]
        if block.get("kicker"):
            out.append(f"**{block['kicker']}**")
        if block.get("subtitle"):
            out.append(str(block["subtitle"]))
        return out
    if kind == "split":
        return [f"## {title}", *[f"- {point}" for point in block.get("points", [])]]
    if kind == "flow":
        out = [f"## {title}"]
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
        return out
    if kind == "metrics":
        rows = [["Metric", "Value", "Note"]]
        rows.extend([[item.get("label", ""), item.get("value", ""), item.get("note", "")] for item in block.get("items", []) if isinstance(item, dict)])
        return [f"## {title}", *_markdown_table(rows)]
    if kind == "cards":
        out = [f"## {title}"]
        for card in block.get("cards", []):
            if isinstance(card, dict):
                out.extend([f"### {card.get('title', '')}", str(card.get("body", ""))])
        return out
    if kind == "table":
        return [f"## {title}", *_markdown_table([block.get("columns", []), *block.get("rows", [])])]
    if kind == "timeline":
        out = [f"## {title}"]
        for event in block.get("events", []):
            if isinstance(event, dict):
                out.append(f"- **{event.get('date', '')} {event.get('title', '')}:** {event.get('body', '')}")
        return out
    if kind == "image":
        caption = block.get("caption") or title
        image = block.get("image") or ""
        return [f"## {title}", f"![{caption}]({image})" if image else str(caption)]
    return [f"## {title}"]


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
