from __future__ import annotations

import json
from typing import Any

from services.agent.agents import validate_board_ir

from .ir_schema import ensure_ir_defaults, validate_ir
from .runtime import adapter_error, env_value, extract_folder_token, run_lark_cli


def ir_to_feishu_board_draft(ir: dict) -> dict:
    ir = ensure_ir_defaults(ir)
    draft = {
        "metadata": {"title": ir["meta"].get("file_name") or ir["meta"]["title"], "source_doc_id": ir.get("docId")},
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

    board_ir = options.get("board_ir") if isinstance(options.get("board_ir"), dict) else None
    if board_ir:
        board_errors = validate_board_ir(board_ir)
        if board_errors:
            return adapter_error("BOARD_IR_VALIDATION_FAILED", "BoardIR validation failed.", board_errors, warnings)
    draft = board_ir_to_feishu_board_draft(board_ir) if board_ir else ir_to_feishu_board_draft(normalized)
    dsl = board_ir_to_whiteboard_dsl(board_ir) if board_ir else board_draft_to_whiteboard_dsl(draft)
    if options.get("dry_run", True):
        return {"ok": True, "mode": "dry_run", "board_draft": draft, "whiteboard_dsl": dsl, "warnings": warnings}

    if mode == "create":
        return _create_board_document(normalized, draft, dsl, options, warnings)
    if mode == "append":
        document_id = str(options.get("document_id") or "").strip()
        if not document_id:
            return adapter_error("INVALID_PUBLISH_MODE", "Board append mode requires document_id.", [], warnings)
        return _append_board_to_document(document_id, draft, dsl, options, warnings)
    return _update_existing_whiteboard(draft, dsl, options, warnings)


class FeishuBoardClient:
    def create_or_update(self, draft: dict, options: dict) -> dict:
        mode = str(options.get("mode") or "update")
        if mode == "update":
            return _update_existing_whiteboard(draft, board_draft_to_whiteboard_dsl(draft), options, [])
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


def _create_board_document(ir: dict, draft: dict, dsl: str, options: dict, warnings: list[str]) -> dict:
    folder_token = extract_folder_token(options.get("folder_token") or env_value("FEISHU_DOC_FOLDER_TOKEN"))
    if not folder_token:
        return adapter_error("FEISHU_CONFIG_MISSING", "FEISHU_DOC_FOLDER_TOKEN is required for board create.", [], warnings)
    result = run_lark_cli(
        [
            "docs",
            "+create",
            "--title",
            f"{_artifact_file_name(ir)} - Board",
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
    whiteboard_token = str(options.get("whiteboard_token") or "").strip()
    update_result = None
    if whiteboard_token:
        update_result = _update_existing_whiteboard(draft, dsl, options, warnings)
    else:
        warnings.append("WHITEBOARD_TOKEN_MISSING; created doc with blank board and preview markdown only.")
    return {
        "ok": True,
        "mode": "create",
        "document_id": data.get("doc_id") or data.get("document_id") or data.get("token"),
        "url": data.get("url") or data.get("open_url"),
        "board_draft": draft,
        "whiteboard_dsl": dsl,
        "whiteboard_update_result": update_result,
        "cli_stdout": result["stdout"],
        "warnings": warnings,
    }


def _artifact_file_name(ir: dict) -> str:
    meta = ir.get("meta") if isinstance(ir.get("meta"), dict) else {}
    return str(meta.get("file_name") or meta.get("title") or "Generated Artifact").strip() or "Generated Artifact"


def _append_board_to_document(document_id: str, draft: dict, dsl: str, options: dict, warnings: list[str]) -> dict:
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
    whiteboard_token = str(options.get("whiteboard_token") or "").strip()
    update_result = None
    if whiteboard_token:
        update_result = _update_existing_whiteboard(draft, dsl, options, warnings)
    else:
        warnings.append("WHITEBOARD_TOKEN_MISSING; appended blank board and preview markdown only.")
    return {
        "ok": True,
        "mode": "append",
        "document_id": document_id,
        "board_draft": draft,
        "whiteboard_dsl": dsl,
        "whiteboard_update_result": update_result,
        "cli_stdout": result["stdout"],
        "warnings": warnings,
    }


def _update_existing_whiteboard(draft: dict, dsl: str, options: dict, warnings: list[str]) -> dict:
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
            "dsl",
            "--source",
            "-",
            "--as",
            str(options.get("as") or "user"),
            "--yes",
        ],
        stdin=dsl,
    )
    if not result["ok"]:
        return adapter_error("FEISHU_BOARD_API_FAILED", "lark-cli whiteboard +update failed.", result, warnings)
    return {"ok": True, "mode": "update", "whiteboard_token": whiteboard_token, "board_draft": draft, "whiteboard_dsl": dsl, "cli_stdout": result["stdout"], "warnings": warnings}


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


def board_draft_to_whiteboard_dsl(draft: dict) -> str:
    title = str((draft.get("metadata") or {}).get("title") or "Board")
    theme = draft.get("theme") if isinstance(draft.get("theme"), dict) else {}
    nodes: list[dict[str, Any]] = []
    y = 180
    for index, node in enumerate(draft.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        nodes.append(
            {
                "id": str(node.get("id") or f"n_{index + 1}"),
                "type": "frame",
                "x": 80 + (index % 4) * 320,
                "y": y + (index // 4) * 180,
                "width": 280,
                "height": 132,
                "layout": "vertical",
                "gap": 6,
                "padding": 16,
                "fillColor": str(theme.get("surface") or "#FFFFFF"),
                "borderColor": str(theme.get("accent") or "#2F6BFF"),
                "borderWidth": 1,
                "borderRadius": 12,
                "children": [
                    {
                        "id": f"{node.get('id')}_title",
                        "type": "text",
                        "text": str(node.get("title") or node.get("type") or "Section"),
                        "fontSize": 15,
                        "textColor": str(theme.get("accent") or "#2F6BFF"),
                        "textAlign": "left",
                    },
                    {
                        "id": f"{node.get('id')}_text",
                        "type": "text",
                        "text": str(node.get("text") or ""),
                        "fontSize": 13,
                        "textColor": str(theme.get("text") or "#0F172A"),
                        "textAlign": "left",
                    },
                ],
            }
        )
    payload = {
        "version": 2,
        "metadata": {"title": title, "generatedFrom": "PlanB board_draft fallback"},
        "nodes": [
            {
                "id": "root",
                "type": "frame",
                "x": 0,
                "y": 0,
                "width": 1440,
                "height": "fit-content",
                "layout": "vertical",
                "gap": 18,
                "padding": 28,
                "fillColor": str(theme.get("background") or "#F8FAFC"),
                "borderColor": "#CBD5E1",
                "borderWidth": 1,
                "borderRadius": 20,
                "children": [
                    {"id": "title", "type": "text", "text": title, "fontSize": 30, "textColor": str(theme.get("text") or "#0F172A"), "textAlign": "left"},
                    *nodes,
                ],
            }
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def board_ir_to_feishu_board_draft(board_ir: dict) -> dict:
    board = board_ir if isinstance(board_ir, dict) else {}
    draft = {
        "metadata": {"title": str(board.get("file_name") or board.get("title") or "Board"), "source_doc_id": ""},
        "nodes": [],
        "edges": [],
        "theme": board.get("theme") if isinstance(board.get("theme"), dict) else {},
    }
    y = 0
    for section in board.get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("id") or f"s_{len(draft['nodes']) + 1}")
        kind = str(section.get("kind") or "overview")
        title = str(section.get("title") or section_id)
        draft["nodes"].append({"id": section_id, "type": f"section_{kind}", "x": 0, "y": y, "title": title, "text": str(section.get("description") or "")})
        y += 220
    return draft


def board_ir_to_whiteboard_dsl(board_ir: dict) -> str:
    board = board_ir if isinstance(board_ir, dict) else {}
    theme = board.get("theme") if isinstance(board.get("theme"), dict) else {}
    children: list[dict[str, Any]] = [
        {
            "id": "title",
            "type": "text",
            "width": "fill-container",
            "height": "fit-content",
            "text": str(board.get("title") or "Board"),
            "fontSize": 32,
            "textColor": str(theme.get("text") or "#0F172A"),
            "textAlign": "left",
        },
        {
            "id": "subtitle",
            "type": "text",
            "width": "fill-container",
            "height": "fit-content",
            "text": str(board.get("subtitle") or ""),
            "fontSize": 16,
            "textColor": str(theme.get("muted") or "#64748B"),
            "textAlign": "left",
        },
    ]
    for index, section in enumerate(board.get("sections") or []):
        if not isinstance(section, dict):
            continue
        kind = str(section.get("kind") or "overview")
        section_children = [
            {"id": f"s{index}_title", "type": "text", "width": "fill-container", "height": "fit-content", "text": str(section.get("title") or kind), "fontSize": 20, "textColor": str(section.get("accent") or theme.get("accent") or "#2F6BFF"), "textAlign": "left"},
            {"id": f"s{index}_desc", "type": "text", "width": "fill-container", "height": "fit-content", "text": str(section.get("description") or ""), "fontSize": 13, "textColor": str(theme.get("muted") or "#64748B"), "textAlign": "left"},
        ]
        lines: list[str] = []
        if kind in {"overview", "cards", "summary"}:
            lines = [str(item) for item in section.get("items") or [] if str(item).strip()]
        elif kind == "flow":
            labels = [str(node.get("label") or node.get("id") or "") for node in section.get("nodes") or [] if isinstance(node, dict)]
            lines = [f"{i + 1}. {label}" for i, label in enumerate(labels)]
        elif kind == "metrics":
            lines = [f"{m.get('label', '')}: {m.get('value', '')} {m.get('note', '')}".strip() for m in section.get("metrics") or [] if isinstance(m, dict)]
        elif kind == "table":
            columns = [str(col) for col in section.get("columns") or []]
            if columns:
                lines.append(" | ".join(columns))
            for row in section.get("rows") or []:
                if isinstance(row, list):
                    lines.append(" | ".join(str(cell) for cell in row))
        elif kind == "timeline":
            lines = [f"{event.get('date', '')} - {event.get('title', '')}: {event.get('body', '')}".strip() for event in section.get("events") or [] if isinstance(event, dict)]
        section_children.append(
            {
                "id": f"s{index}_body",
                "type": "text",
                "width": "fill-container",
                "height": "fit-content",
                "text": "\n".join(line for line in lines if line.strip()),
                "fontSize": 13,
                "textColor": str(theme.get("text") or "#0F172A"),
                "textAlign": "left",
            }
        )
        children.append(
            {
                "id": str(section.get("id") or f"section_{index + 1}"),
                "type": "frame",
                "width": "fill-container",
                "height": "fit-content",
                "layout": "vertical",
                "gap": 8,
                "padding": 18,
                "fillColor": str(theme.get("surface") or "#FFFFFF"),
                "borderColor": str(section.get("accent") or theme.get("accent") or "#2F6BFF"),
                "borderWidth": 1,
                "borderRadius": 12,
                "children": section_children,
            }
        )
    payload = {
        "version": 2,
        "metadata": {"title": str(board.get("title") or "Board"), "generatedFrom": "PlanB board_ir"},
        "nodes": [
            {
                "id": "root",
                "type": "frame",
                "x": 0,
                "y": 0,
                "width": 1440,
                "height": "fit-content",
                "layout": "vertical",
                "gap": 16,
                "padding": 28,
                "fillColor": str(theme.get("background") or "#F8FAFC"),
                "borderColor": "#CBD5E1",
                "borderWidth": 1,
                "borderRadius": 20,
                "children": children,
            }
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _edge_pair(edge: Any) -> tuple[str, str]:
    if isinstance(edge, dict):
        return str(edge.get("from")), str(edge.get("to"))
    if isinstance(edge, list) and len(edge) >= 2:
        return str(edge[0]), str(edge[1])
    return "", ""
