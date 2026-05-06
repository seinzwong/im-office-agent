from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

IR_SCHEMA_VERSION = "0.2.0"
ALLOWED_BLOCK_KINDS = {
    "cover",
    "split",
    "flow",
    "metrics",
    "cards",
    "table",
    "timeline",
    "image",
}

DEFAULT_THEME: dict[str, str] = {
    "name": "Executive Blue",
    "accent": "#2F6BFF",
    "accent2": "#7C3AED",
    "background": "#F8FAFC",
    "surface": "#FFFFFF",
    "text": "#0F172A",
    "muted": "#64748B",
    "success": "#16A34A",
    "warning": "#F97316",
    "fontFace": "Aptos",
}


def ensure_ir_defaults(ir: dict) -> dict:
    """Return a copy of IR with non-critical missing fields filled."""
    out = deepcopy(ir) if isinstance(ir, dict) else {}
    out.setdefault("schemaVersion", IR_SCHEMA_VERSION)
    out.setdefault("docId", "task_ir")
    meta = out.setdefault("meta", {})
    if not isinstance(meta, dict):
        meta = {}
        out["meta"] = meta
    meta.setdefault("title", "Generated Artifact")
    meta.setdefault("subtitle", "")
    meta.setdefault("owner", "Agent")
    meta.setdefault("date", date.today().isoformat())
    meta.setdefault("audience", "")

    theme = out.setdefault("theme", {})
    if not isinstance(theme, dict):
        theme = {}
        out["theme"] = theme
    for key, value in DEFAULT_THEME.items():
        theme.setdefault(key, value)

    assets = out.setdefault("assets", {})
    if not isinstance(assets, dict):
        out["assets"] = {}

    blocks = out.setdefault("blocks", [])
    if not isinstance(blocks, list):
        out["blocks"] = []
        blocks = out["blocks"]
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        block.setdefault("id", f"block_{index + 1}")
        block.setdefault("title", str(block.get("id") or f"Block {index + 1}"))
        block.setdefault("description", "")
        block.setdefault("intent", "")
        if not isinstance(block.get("sourceRefs"), list):
            block["sourceRefs"] = _string_list(block.get("sourceRefs") or block.get("source_refs"))
        kind = block.get("kind")
        if kind == "split":
            block.setdefault("points", [])
        elif kind == "flow":
            block.setdefault("nodes", [])
            block.setdefault("edges", [])
        elif kind == "metrics":
            block.setdefault("items", [])
        elif kind == "cards":
            block.setdefault("cards", [])
            block["cards"] = _normalize_cards(block["cards"])
        elif kind == "table":
            block.setdefault("columns", [])
            block.setdefault("rows", [])
            block["columns"] = _string_list(block["columns"])
            block["rows"] = _normalize_table_rows(block["rows"], len(block["columns"]))
        elif kind == "timeline":
            block.setdefault("events", [])
            block["events"] = _normalize_events(block["events"])
    return out


def validate_ir(ir: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(ir, dict):
        return ["IR must be an object."]
    if ir.get("schemaVersion") != IR_SCHEMA_VERSION:
        errors.append('schemaVersion must be "0.2.0".')
    meta = ir.get("meta")
    if not isinstance(meta, dict) or not str(meta.get("title") or "").strip():
        errors.append("meta.title is required.")
    blocks = ir.get("blocks")
    if not isinstance(blocks, list):
        errors.append("blocks must be an array.")
        return errors

    seen_ids: set[str] = set()
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            errors.append(f"blocks[{index}] must be an object.")
            continue
        block_id = str(block.get("id") or "").strip()
        kind = str(block.get("kind") or "").strip()
        title = str(block.get("title") or "").strip()
        if not block_id or not kind or not title:
            errors.append(f"blocks[{index}] must contain id, kind, and title.")
        if block_id:
            if block_id in seen_ids:
                errors.append(f"Duplicate block id: {block_id}.")
            seen_ids.add(block_id)
        if kind not in ALLOWED_BLOCK_KINDS:
            errors.append(f"Unsupported block kind: {kind}.")
        elif not _block_has_renderable_content(block):
            errors.append(f"Block {block_id or index} has no renderable content for kind {kind}.")
        if _requires_table_intent(block) and kind != "table":
            errors.append(f"Block {block_id or index} intent {block.get('intent')} must use kind table.")
        if not isinstance(block.get("sourceRefs"), list):
            errors.append(f"Block {block_id or index} sourceRefs must be an array.")
        if kind == "flow":
            _validate_flow(block, block_id or f"blocks[{index}]", errors)
        elif kind == "table":
            if not isinstance(block.get("columns"), list) or not isinstance(block.get("rows"), list):
                errors.append(f"Table block {block_id} columns and rows must be arrays.")
            elif not any(str(column).strip() for column in block.get("columns", [])):
                errors.append(f"Table block {block_id} columns must contain at least one header.")
            else:
                width = len(block.get("columns", []))
                for row_index, row in enumerate(block.get("rows", [])):
                    if not isinstance(row, list):
                        errors.append(f"Table block {block_id} rows[{row_index}] must be an array.")
                    elif len(row) != width:
                        errors.append(f"Table block {block_id} rows[{row_index}] must match columns length.")
        elif kind == "cards" and not isinstance(block.get("cards"), list):
            errors.append(f"Cards block {block_id} cards must be an array.")
        elif kind == "cards":
            for card_index, card in enumerate(block.get("cards", [])):
                if not isinstance(card, dict):
                    errors.append(f"Cards block {block_id} cards[{card_index}] must be an object.")
                elif not (str(card.get("title") or "").strip() or str(card.get("body") or "").strip()):
                    errors.append(f"Cards block {block_id} cards[{card_index}] must contain title or body.")
        elif kind == "metrics" and not isinstance(block.get("items"), list):
            errors.append(f"Metrics block {block_id} items must be an array.")
        elif kind == "timeline" and not isinstance(block.get("events"), list):
            errors.append(f"Timeline block {block_id} events must be an array.")
    return errors


def _validate_flow(block: dict[str, Any], block_id: str, errors: list[str]) -> None:
    nodes = block.get("nodes")
    edges = block.get("edges", [])
    if not isinstance(nodes, list):
        errors.append(f"Flow block {block_id} nodes must be an array.")
        return
    if not isinstance(edges, list):
        errors.append(f"Flow block {block_id} edges must be an array.")
        return
    node_ids = {str(node.get("id")) for node in nodes if isinstance(node, dict) and node.get("id")}
    for edge in edges:
        if isinstance(edge, dict):
            source = edge.get("from")
            target = edge.get("to")
        elif isinstance(edge, list) and len(edge) >= 2:
            source = edge[0]
            target = edge[1]
        else:
            errors.append(f"Flow block {block_id} has an invalid edge.")
            continue
        if str(source) not in node_ids or str(target) not in node_ids:
            errors.append(f"Flow block {block_id} edge references missing node id.")


def _block_has_renderable_content(block: dict[str, Any]) -> bool:
    kind = str(block.get("kind") or "").strip()
    if kind == "cover":
        return bool(str(block.get("subtitle") or block.get("kicker") or block.get("title") or "").strip())
    if kind == "split":
        return bool(_non_empty_list(block.get("points")))
    if kind == "flow":
        return bool(_non_empty_list(block.get("nodes")))
    if kind == "metrics":
        return bool(_non_empty_list(block.get("items")))
    if kind == "cards":
        return bool(_non_empty_list(block.get("cards")))
    if kind == "table":
        return bool(_non_empty_list(block.get("columns")) and (_non_empty_list(block.get("rows")) or str(block.get("caption") or block.get("description") or "").strip()))
    if kind == "timeline":
        return bool(_non_empty_list(block.get("events")))
    if kind == "image":
        return bool(str(block.get("caption") or block.get("image") or "").strip())
    return False


def _non_empty_list(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0


def _requires_table_intent(block: dict[str, Any]) -> bool:
    return str(block.get("intent") or "").strip().lower() in {"actions", "risks", "comparison", "metrics"}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_cards(value: Any) -> list[dict[str, Any]]:
    output = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        card = dict(item)
        card.setdefault("title", "")
        card.setdefault("body", "")
        if not isinstance(card.get("meta"), list):
            card["meta"] = _string_list(card.get("meta"))
        card["meta"] = [_clean_meta_item(meta) for meta in card.get("meta", [])]
        card["meta"] = [meta for meta in card["meta"] if meta]
        output.append(card)
    return output


def _clean_meta_item(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lower = text.lower()
    if "待确认" in text or "unknown" in lower or lower == "n/a":
        return ""
    if "http://" in lower or "https://" in lower:
        return ""
    if "message:" in lower or "source:" in lower or "om_" in lower:
        return ""
    if len(text) > 48:
        return ""
    return text


def _normalize_events(value: Any) -> list[dict[str, Any]]:
    output = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        event = dict(item)
        event.setdefault("date", "")
        event.setdefault("title", "")
        event.setdefault("body", "")
        event.setdefault("owner", "")
        event.setdefault("status", "")
        output.append(event)
    return output


def _normalize_table_rows(value: Any, width: int) -> list[list[str]]:
    if not isinstance(value, list):
        return []
    rows = []
    for row in value:
        if isinstance(row, list):
            cells = [str(cell).strip() or "待确认" for cell in row]
        elif isinstance(row, dict):
            cells = [str(cell).strip() or "待确认" for cell in row.values()]
        else:
            cells = [str(row).strip()]
        if not any(cell.strip() for cell in cells):
            continue
        if width > 0:
            cells = (cells + ["待确认"] * width)[:width]
        rows.append(cells)
    return rows
