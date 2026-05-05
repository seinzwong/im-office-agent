from __future__ import annotations

from typing import Any

from .ir_schema import ensure_ir_defaults, validate_ir
from .runtime import adapter_error, env_value, extract_folder_token, run_lark_cli


def ir_to_feishu_board_draft(ir: dict) -> dict:
    ir = ensure_ir_defaults(ir)
    draft = {
        "metadata": {"title": ir["meta"]["title"], "source_doc_id": ir.get("docId")},
        "nodes": [],
        "edges": [],
        "theme": ir.get("theme", {}),
    }
    y = 0
    for block in ir.get("blocks", []):
        if not isinstance(block, dict):
            continue
        y = _append_block_nodes(draft, block, y)
    return draft


def publish_ir_to_feishu_board(ir: dict, options: dict) -> dict:
    warnings: list[str] = []
    normalized = ensure_ir_defaults(ir)
    errors = validate_ir(normalized)
    if errors:
        return adapter_error("IR_VALIDATION_FAILED", "IR validation failed", errors, warnings)

    options = options or {}
    mode = str(options.get("mode") or "create")
    if mode not in {"create", "append", "update"}:
        return adapter_error("INVALID_PUBLISH_MODE", "Board mode must be create, append, or update.", [mode], warnings)

    draft = ir_to_feishu_board_draft(normalized)
    if options.get("dry_run", True):
        return {"ok": True, "mode": "dry_run", "board_draft": draft, "warnings": warnings}

    if mode == "create":
        return _create_board_document(normalized, draft, options, warnings)
    if mode == "append":
        document_id = str(options.get("document_id") or "").strip()
        if not document_id:
            return adapter_error("INVALID_PUBLISH_MODE", "Board append mode requires document_id.", [], warnings)
        return _append_board_to_document(document_id, draft, options, warnings)
    return _update_existing_whiteboard(draft, options, warnings)


class FeishuBoardClient:
    def create_or_update(self, draft: dict, options: dict) -> dict:
        mode = str(options.get("mode") or "update")
        if mode == "update":
            return _update_existing_whiteboard(draft, options, [])
        return adapter_error("UNSUPPORTED_OPERATION", "Use publish_ir_to_feishu_board for create/append modes.")


def _append_block_nodes(draft: dict, block: dict, y: int) -> int:
    kind = block.get("kind")
    if kind == "flow":
        positions: dict[str, str] = {}
        for index, flow_node in enumerate(block.get("nodes", [])):
            if not isinstance(flow_node, dict):
                continue
            node_id = f"{block.get('id')}_{flow_node.get('id')}"
            positions[str(flow_node.get("id"))] = node_id
            draft["nodes"].append(
                {
                    "id": node_id,
                    "type": "flow_node",
                    "x": index * 260,
                    "y": y,
                    "text": flow_node.get("label") or flow_node.get("id"),
                    "source_block_id": block.get("id"),
                }
            )
        for edge in block.get("edges", []):
            source, target = _edge_pair(edge)
            if source in positions and target in positions:
                draft["edges"].append({"from": positions[source], "to": positions[target], "source_block_id": block.get("id")})
        return y + 180

    items = _block_items(block)
    for index, item in enumerate(items):
        draft["nodes"].append(
            {
                "id": f"{block.get('id')}_{index}",
                "type": kind or "text",
                "x": index * 260,
                "y": y,
                "title": item.get("title") or block.get("title"),
                "text": item.get("text") or item.get("body") or block.get("title"),
                "source_block_id": block.get("id"),
            }
        )
    return y + 180


def _block_items(block: dict) -> list[dict]:
    kind = block.get("kind")
    if kind == "cover":
        return [{"title": block.get("title"), "text": block.get("subtitle") or block.get("title")}]
    if kind == "split":
        return [{"title": block.get("title"), "text": point} for point in block.get("points", [])] or [{"title": block.get("title")}]
    if kind == "metrics":
        return [{"title": item.get("label"), "text": f"{item.get('value', '')} {item.get('note', '')}"} for item in block.get("items", []) if isinstance(item, dict)]
    if kind == "cards":
        return [card for card in block.get("cards", []) if isinstance(card, dict)]
    if kind == "table":
        return [{"title": block.get("title"), "text": " | ".join(str(col) for col in block.get("columns", []))}]
    if kind == "timeline":
        return [{"title": event.get("title"), "text": f"{event.get('date', '')}: {event.get('body', '')}"} for event in block.get("events", []) if isinstance(event, dict)]
    if kind == "image":
        return [{"title": block.get("title"), "text": block.get("caption") or block.get("image")}]
    return [{"title": block.get("title")}]


def _create_board_document(ir: dict, draft: dict, options: dict, warnings: list[str]) -> dict:
    folder_token = extract_folder_token(options.get("folder_token") or env_value("FEISHU_DOC_FOLDER_TOKEN"))
    if not folder_token:
        return adapter_error("FEISHU_CONFIG_MISSING", "FEISHU_DOC_FOLDER_TOKEN is required for board create.", [], warnings)
    result = run_lark_cli(
        [
            "docs",
            "+create",
            "--title",
            f"{ir['meta']['title']} - Board",
            "--folder-token",
            folder_token,
            "--markdown",
            _board_document_markdown(draft),
            "--as",
            str(options.get("as") or "user"),
        ]
    )
    if not result["ok"]:
        return adapter_error("FEISHU_BOARD_API_FAILED", "lark-cli docs +create for board failed.", result, warnings)
    data = result["json"].get("data") or result["json"]
    return {
        "ok": True,
        "mode": "create",
        "document_id": data.get("doc_id") or data.get("document_id") or data.get("token"),
        "url": data.get("url") or data.get("open_url"),
        "board_draft": draft,
        "cli_stdout": result["stdout"],
        "warnings": warnings,
    }


def _append_board_to_document(document_id: str, draft: dict, options: dict, warnings: list[str]) -> dict:
    result = run_lark_cli(
        [
            "docs",
            "+update",
            "--doc",
            document_id,
            "--mode",
            "append",
            "--markdown",
            _board_document_markdown(draft),
            "--as",
            str(options.get("as") or "user"),
        ]
    )
    if not result["ok"]:
        return adapter_error("FEISHU_BOARD_API_FAILED", "lark-cli docs +update for board append failed.", result, warnings)
    return {"ok": True, "mode": "append", "document_id": document_id, "board_draft": draft, "cli_stdout": result["stdout"], "warnings": warnings}


def _update_existing_whiteboard(draft: dict, options: dict, warnings: list[str]) -> dict:
    whiteboard_token = str(options.get("whiteboard_token") or env_value("FEISHU_WHITEBOARD_TOKEN") or env_value("LARK_WHITEBOARD_TOKEN")).strip()
    if not whiteboard_token:
        return adapter_error("FEISHU_CONFIG_MISSING", "whiteboard_token is required for board update mode.", [], warnings)
    result = run_lark_cli(
        [
            "whiteboard",
            "+update",
            "--whiteboard-token",
            whiteboard_token,
            "--input_format",
            "mermaid",
            "--source",
            "-",
            "--as",
            str(options.get("as") or "user"),
            "--yes",
        ],
        stdin=_board_draft_to_mermaid(draft),
    )
    if not result["ok"]:
        return adapter_error("FEISHU_BOARD_API_FAILED", "lark-cli whiteboard +update failed.", result, warnings)
    return {"ok": True, "mode": "update", "whiteboard_token": whiteboard_token, "board_draft": draft, "cli_stdout": result["stdout"], "warnings": warnings}


def _board_document_markdown(draft: dict) -> str:
    title = (draft.get("metadata") or {}).get("title") or "Board"
    return f"# {title} - Board\n\n<whiteboard type=\"blank\"></whiteboard>\n\n```mermaid\n{_board_draft_to_mermaid(draft)}\n```\n"


def _board_draft_to_mermaid(draft: dict) -> str:
    lines = ["flowchart LR"]
    ids: dict[str, str] = {}
    for index, node in enumerate(draft.get("nodes", []), start=1):
        if not isinstance(node, dict):
            continue
        node_id = f"N{index}"
        ids[str(node.get("id"))] = node_id
        label = str(node.get("text") or node.get("title") or node.get("type") or node_id).replace('"', "'")
        lines.append(f'  {node_id}["{label}"]')
    for edge in draft.get("edges", []):
        if isinstance(edge, dict) and str(edge.get("from")) in ids and str(edge.get("to")) in ids:
            lines.append(f"  {ids[str(edge['from'])]} --> {ids[str(edge['to'])]}")
    if len(lines) == 1:
        lines.append('  N1["Board"]')
    return "\n".join(lines)


def _edge_pair(edge: Any) -> tuple[str, str]:
    if isinstance(edge, dict):
        return str(edge.get("from")), str(edge.get("to"))
    if isinstance(edge, list) and len(edge) >= 2:
        return str(edge[0]), str(edge[1])
    return "", ""
