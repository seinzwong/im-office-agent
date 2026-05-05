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
        elif kind == "table":
            block.setdefault("columns", [])
            block.setdefault("rows", [])
        elif kind == "timeline":
            block.setdefault("events", [])
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
        if kind == "flow":
            _validate_flow(block, block_id or f"blocks[{index}]", errors)
        elif kind == "table":
            if not isinstance(block.get("columns"), list) or not isinstance(block.get("rows"), list):
                errors.append(f"Table block {block_id} columns and rows must be arrays.")
        elif kind == "cards" and not isinstance(block.get("cards"), list):
            errors.append(f"Cards block {block_id} cards must be an array.")
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
