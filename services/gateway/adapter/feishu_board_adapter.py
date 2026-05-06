from __future__ import annotations

import json
import unicodedata
from typing import Any

import httpx

from services.agent.agents import repair_board_ir, validate_board_ir
from services.gateway.app.config import get_settings
from services.gateway.app.oauth_tokens import get_any_valid_user_access_token, get_valid_user_access_token

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
        board_ir, repair_warnings = repair_board_ir(board_ir)
        warnings.extend(repair_warnings)
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
    openapi_result = _create_board_document_openapi(ir, draft, dsl, options, warnings, folder_token)
    if not options.get("use_lark_cli_fallback"):
        return openapi_result
    warnings.extend(openapi_result.get("warnings") or [])
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
    openapi_result = _append_board_to_document_openapi(document_id, draft, dsl, options, warnings)
    if not options.get("use_lark_cli_fallback"):
        return openapi_result
    warnings.extend(openapi_result.get("warnings") or [])
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
    openapi_result = _update_whiteboard_openapi(whiteboard_token, draft, dsl, options, warnings)
    if not options.get("use_lark_cli_fallback"):
        return openapi_result
    warnings.extend(openapi_result.get("warnings") or [])
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


def _create_board_document_openapi(
    ir: dict,
    draft: dict,
    dsl: str,
    options: dict,
    warnings: list[str],
    folder_token: str,
) -> dict:
    client = _FeishuBoardOpenApi.from_options(options)
    if not client:
        return adapter_error("FEISHU_USER_TOKEN_MISSING", "A Feishu user_access_token is required for OpenAPI board publishing.", [], [])
    title = f"{_artifact_file_name(ir)} - Board"
    created = client.create_document(folder_token, title)
    if created.get("ok") is not True:
        return adapter_error("FEISHU_BOARD_API_FAILED", "OpenAPI docx create for board failed.", created, warnings)
    document_id = str(created.get("document_id") or "")
    board_block = client.create_board_block(document_id)
    if board_block.get("ok") is not True:
        return adapter_error("FEISHU_BOARD_API_FAILED", "OpenAPI board block create failed.", board_block, warnings)
    whiteboard_token = str(board_block.get("whiteboard_token") or "")
    update_result = _update_whiteboard_with_client(client, whiteboard_token, draft, dsl, options)
    if update_result.get("ok") is not True:
        return adapter_error("FEISHU_BOARD_API_FAILED", "OpenAPI whiteboard update failed.", update_result, warnings)
    return {
        "ok": True,
        "mode": "create",
        "document_id": document_id,
        "url": _docx_url(client.base_url, document_id),
        "whiteboard_token": whiteboard_token,
        "board_draft": draft,
        "whiteboard_dsl": dsl,
        "whiteboard_update_result": update_result,
        "warnings": warnings,
    }


def _append_board_to_document_openapi(document_id: str, draft: dict, dsl: str, options: dict, warnings: list[str]) -> dict:
    client = _FeishuBoardOpenApi.from_options(options)
    if not client:
        return adapter_error("FEISHU_USER_TOKEN_MISSING", "A Feishu user_access_token is required for OpenAPI board publishing.", [], [])
    board_block = client.create_board_block(document_id)
    if board_block.get("ok") is not True:
        return adapter_error("FEISHU_BOARD_API_FAILED", "OpenAPI board block append failed.", board_block, warnings)
    whiteboard_token = str(board_block.get("whiteboard_token") or "")
    update_result = _update_whiteboard_with_client(client, whiteboard_token, draft, dsl, options)
    if update_result.get("ok") is not True:
        return adapter_error("FEISHU_BOARD_API_FAILED", "OpenAPI whiteboard update failed.", update_result, warnings)
    return {
        "ok": True,
        "mode": "append",
        "document_id": document_id,
        "whiteboard_token": whiteboard_token,
        "board_draft": draft,
        "whiteboard_dsl": dsl,
        "whiteboard_update_result": update_result,
        "warnings": warnings,
    }


def _update_whiteboard_openapi(whiteboard_token: str, draft: dict, dsl: str, options: dict, warnings: list[str]) -> dict:
    client = _FeishuBoardOpenApi.from_options(options)
    if not client:
        return adapter_error("FEISHU_USER_TOKEN_MISSING", "A Feishu user_access_token is required for OpenAPI board publishing.", [], [])
    update_result = _update_whiteboard_with_client(client, whiteboard_token, draft, dsl, options)
    if update_result.get("ok") is not True:
        return adapter_error("FEISHU_BOARD_API_FAILED", "OpenAPI whiteboard update failed.", update_result, warnings)
    return {"ok": True, "mode": "update", "whiteboard_token": whiteboard_token, "board_draft": draft, "whiteboard_dsl": dsl, "whiteboard_update_result": update_result, "warnings": warnings}


def _update_whiteboard_with_client(client: "_FeishuBoardOpenApi", whiteboard_token: str, draft: dict, dsl: str, options: dict) -> dict:
    update_result = client.update_whiteboard_from_dsl(whiteboard_token, dsl)
    if update_result.get("ok") is True or not options.get("use_mermaid_fallback"):
        return update_result
    mermaid_result = client.update_whiteboard_from_mermaid(whiteboard_token, _board_draft_to_mermaid(draft))
    if mermaid_result.get("ok") is True:
        mermaid_result["fallback"] = "mermaid"
    return mermaid_result


class _FeishuBoardOpenApi:
    def __init__(self, base_url: str, user_access_token: str) -> None:
        self.base_url = base_url.rstrip("/") or "https://open.feishu.cn"
        self.user_access_token = user_access_token.strip()

    @classmethod
    def from_options(cls, options: dict) -> "_FeishuBoardOpenApi | None":
        token = str(options.get("user_access_token") or env_value("FEISHU_USER_ACCESS_TOKEN") or "").strip()
        settings = get_settings()
        if not token:
            user_id = str(
                options.get("authorized_user_id")
                or env_value("FEISHU_AUTHORIZED_USER_ID")
                or env_value("FEISHU_USER_OPEN_ID")
                or ""
            ).strip()
            if user_id:
                token = get_valid_user_access_token(settings, user_id)
            else:
                _, token = get_any_valid_user_access_token(settings)
        if not token:
            return None
        return cls(str(settings.lark_base_url or "https://open.feishu.cn"), token)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.user_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def create_document(self, folder_token: str, title: str) -> dict:
        response = httpx.post(
            f"{self.base_url}/open-apis/docx/v1/documents",
            headers=self._headers(),
            json={"folder_token": folder_token, "title": title[:800] or "Board"},
            timeout=60.0,
        )
        return _openapi_result(response, document_id_path=("data", "document", "document_id"))

    def page_block_id(self, document_id: str) -> str:
        response = httpx.get(
            f"{self.base_url}/open-apis/docx/v1/documents/{document_id}/blocks",
            headers=self._headers(),
            params={"page_size": 80},
            timeout=60.0,
        )
        data = _json_or_empty(response)
        if data.get("code", 0) != 0:
            return document_id
        for item in (data.get("data") or {}).get("items") or []:
            if isinstance(item, dict) and item.get("block_type") == 1 and item.get("block_id"):
                return str(item.get("block_id"))
        return document_id

    def create_board_block(self, document_id: str) -> dict:
        parent_id = self.page_block_id(document_id)
        response = httpx.post(
            f"{self.base_url}/open-apis/docx/v1/documents/{document_id}/blocks/{parent_id}/children",
            headers=self._headers(),
            json={"children": [{"block_type": 43, "board": {}}], "index": -1},
            timeout=60.0,
        )
        result = _openapi_result(response)
        if result.get("ok") is not True:
            return result
        token = _extract_board_token(_json_or_empty(response)) or self.find_board_token(document_id)
        if not token:
            return {"ok": False, "code": "BOARD_TOKEN_MISSING", "response": _json_or_empty(response)}
        return {"ok": True, "whiteboard_token": token, "response": _json_or_empty(response)}

    def find_board_token(self, document_id: str) -> str:
        response = httpx.get(
            f"{self.base_url}/open-apis/docx/v1/documents/{document_id}/blocks",
            headers=self._headers(),
            params={"page_size": 80},
            timeout=60.0,
        )
        return _extract_board_token(_json_or_empty(response))

    def update_whiteboard_from_mermaid(self, whiteboard_token: str, mermaid: str) -> dict:
        response = httpx.post(
            f"{self.base_url}/open-apis/board/v1/whiteboards/{whiteboard_token}/nodes/plantuml",
            headers=self._headers(),
            json={
                "plant_uml_code": mermaid,
                "syntax_type": 2,
                "diagram_type": 6,
            },
            timeout=120.0,
        )
        return _openapi_result(response)

    def update_whiteboard_from_dsl(self, whiteboard_token: str, dsl: str) -> dict:
        nodes = _whiteboard_dsl_to_openapi_nodes(dsl)
        if not nodes:
            return {"ok": False, "code": "WHITEBOARD_DSL_EMPTY", "msg": "whiteboard DSL did not produce any nodes"}
        response = httpx.post(
            f"{self.base_url}/open-apis/board/v1/whiteboards/{whiteboard_token}/nodes",
            headers=self._headers(),
            json={"nodes": nodes},
            timeout=120.0,
        )
        result = _openapi_result(response)
        if result.get("ok") is True:
            result["node_count"] = len(nodes)
        return result


def _json_or_empty(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _openapi_result(response: httpx.Response, document_id_path: tuple[str, ...] = ()) -> dict:
    data = _json_or_empty(response)
    if response.status_code >= 400 or data.get("code", 0) != 0:
        return {
            "ok": False,
            "status_code": response.status_code,
            "code": data.get("code"),
            "msg": data.get("msg"),
            "response": data,
        }
    result = {"ok": True, "status_code": response.status_code, "response": data}
    if document_id_path:
        value: Any = data
        for key in document_id_path:
            value = value.get(key) if isinstance(value, dict) else None
        result["document_id"] = str(value or "")
    return result


def _extract_board_token(value: Any) -> str:
    if isinstance(value, dict):
        board = value.get("board")
        if isinstance(board, dict) and board.get("token"):
            return str(board.get("token"))
        for nested in value.values():
            token = _extract_board_token(nested)
            if token:
                return token
    elif isinstance(value, list):
        for item in value:
            token = _extract_board_token(item)
            if token:
                return token
    return ""


def _docx_url(base_url: str, document_id: str) -> str:
    if "larksuite" in base_url.lower():
        return f"https://www.larksuite.com/docx/{document_id}"
    return f"https://www.feishu.cn/docx/{document_id}"


def _board_document_markdown(draft: dict) -> str:
    title = (draft.get("metadata") or {}).get("title") or "Board"
    return f"# {title} - Board\n\n<whiteboard type=\"blank\"></whiteboard>\n\n```mermaid\n{_board_draft_to_mermaid(draft)}\n```\n"


def _board_draft_to_mermaid(draft: dict) -> str:
    lines = ["flowchart TD"]
    ids: dict[str, str] = {}
    for index, node in enumerate(draft.get("nodes", []), start=1):
        if not isinstance(node, dict):
            continue
        node_id = f"N{index}"
        ids[str(node.get("id"))] = node_id
        title = str(node.get("title") or node.get("type") or node_id).strip()
        body = str(node.get("text") or "").strip()
        label = _mermaid_label("\n".join(part for part in (title, body) if part))
        lines.append(f'  {node_id}["{label}"]')
    for edge in draft.get("edges", []):
        if isinstance(edge, dict) and str(edge.get("from")) in ids and str(edge.get("to")) in ids:
            lines.append(f"  {ids[str(edge['from'])]} --> {ids[str(edge['to'])]}")
    if len(lines) == 1:
        lines.append('  N1["Board"]')
    return "\n".join(lines)


def _mermaid_label(value: str) -> str:
    text = str(value or "").replace('"', "'")
    text = " ".join(part.strip() for part in text.splitlines() if part.strip())
    if len(text) > 120:
        text = text[:117] + "..."
    return text or "Board"


def _text_width(text: str) -> int:
    width = 0
    for char in str(text or ""):
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def _wrap_text(value: str, max_width: int = 36, max_lines: int = 6) -> list[str]:
    if max_lines <= 0:
        return []
    lines: list[str] = []
    for raw_line in str(value or "").splitlines():
        pending = _wrap_line(raw_line.strip(), max_width)
        for line in pending:
            lines.append(line)
            if len(lines) >= max_lines:
                break
        if len(lines) >= max_lines:
            break
    if len(lines) == max_lines and _text_width("\n".join(str(value or "").splitlines())) > sum(_text_width(line) for line in lines):
        lines[-1] = _clip_text(lines[-1], max_width - 3) + "..."
    return [line for line in lines if line.strip()]


def _wrap_line(value: str, max_width: int) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for token in _wrap_tokens(text):
        token_width = _text_width(token)
        current_width = _text_width(current)
        if not current:
            if token_width <= max_width:
                current = token
            else:
                chunks = _split_long_token(token, max_width)
                lines.extend(chunks[:-1])
                current = chunks[-1] if chunks else ""
            continue
        joiner = "" if _is_cjk_token(token) or current.endswith(("，", "。", "；", "：", "、", "/", "→", "-", "+")) else " "
        candidate = f"{current}{joiner}{token}"
        if _text_width(candidate) <= max_width:
            current = candidate
            continue
        lines.append(current)
        current = token
        if token_width > max_width:
            chunks = _split_long_token(token, max_width)
            lines.extend(chunks[:-1])
            current = chunks[-1] if chunks else ""
    if current:
        lines.append(current)
    return lines


def _wrap_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    current = ""
    for char in text:
        if char.isspace():
            if current:
                tokens.append(current)
                current = ""
            continue
        is_cjk = unicodedata.east_asian_width(char) in {"W", "F"}
        if is_cjk:
            if current:
                tokens.append(current)
                current = ""
            tokens.append(char)
        else:
            current += char
    if current:
        tokens.append(current)
    return tokens


def _is_cjk_token(token: str) -> bool:
    return len(token) == 1 and unicodedata.east_asian_width(token) in {"W", "F"}


def _split_long_token(token: str, max_width: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    width = 0
    for char in str(token or ""):
        char_width = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if current and width + char_width > max_width:
            chunks.append(current)
            current = char
            width = char_width
        else:
            current += char
            width += char_width
    if current:
        chunks.append(current)
    return chunks


def _wrap_text_for_box(value: str, width_px: int, font_size: int, max_lines: int) -> list[str]:
    avg_char_px = max(7, int(font_size * 0.72))
    max_width = max(18, int(width_px / avg_char_px))
    return _wrap_text(value, max_width=max_width, max_lines=max_lines)


def _clip_text(value: str, max_width: int) -> str:
    clipped = ""
    width = 0
    for char in str(value or "").strip():
        char_width = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if width + char_width > max_width:
            break
        clipped += char
        width += char_width
    return clipped


CARD_TEXT_LEFT_PAD = 16
CARD_TEXT_RIGHT_PAD = 12
CARD_TITLE_TOP_PAD = 16
CARD_DESC_TOP_GAP = 34
CARD_BODY_TOP_NUDGE = -3
CARD_TEXT_WIDTH = 580 - CARD_TEXT_LEFT_PAD - CARD_TEXT_RIGHT_PAD
TITLE_LINE_PX = 23
TITLE_BOX_EXTRA_PX = 14
PAGE_TITLE_BOX_EXTRA_PX = 18
BODY_LINE_PX = 21
BODY_BOX_EXTRA_PX = 10
BODY_ITEM_GAP = 7
BODY_ITEM_MAX_LINES = 2
DESC_LINE_PX = 18
DESC_BOX_EXTRA_PX = 10
CARD_BODY_BOTTOM_PAD = 10


def _whiteboard_dsl_to_openapi_nodes(dsl: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(dsl)
    except json.JSONDecodeError:
        return []
    nodes: list[dict[str, Any]] = []
    theme = payload.get("theme") if isinstance(payload.get("theme"), dict) else {}
    for index, node in enumerate(payload.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or "")
        if node_type == "text":
            nodes.append(_text_box_node(node, index, theme, z_index=20))
        elif node_type == "frame":
            nodes.extend(_dsl_frame_to_openapi_nodes(node, index, theme))
    return nodes


def _base_shape_node(node_id: str, x: int, y: int, width: int, height: int, *, fill: str, border: str, border_width: str, z_index: int) -> dict:
    return {
        "id": node_id,
        "type": "composite_shape",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "z_index": z_index,
        "style": {
            "fill_color": fill,
            "border_color": border,
            "border_width": border_width,
        },
        "composite_shape": {"type": "round_rect"},
    }


def _text_box_node(node: dict, index: int, theme: dict, *, z_index: int = 20) -> dict:
    fill = str(node.get("fillColor") or node.get("background") or theme.get("background") or theme.get("surface") or "#F8FAFC")
    out = _base_shape_node(
        str(node.get("id") or f"text_{index + 1}"),
        int(node.get("x") or 0),
        int(node.get("y") or 0),
        int(node.get("width") or 800),
        int(node.get("height") or 60),
        fill=fill,
        border=fill,
        border_width="extra_narrow",
        z_index=z_index,
    )
    out["text"] = {
        "text": str(node.get("text") or ""),
        "font_size": int(node.get("fontSize") or 13),
        "text_color": str(node.get("textColor") or theme.get("text") or "#0F172A"),
        "horizontal_align": str(node.get("textAlign") or "left"),
        "vertical_align": str(node.get("verticalAlign") or "top"),
    }
    if node.get("fontWeight"):
        out["text"]["font_weight"] = str(node.get("fontWeight"))
    return out


def _card_background_node(node: dict, index: int, theme: dict) -> dict:
    border_width = node.get("borderWidth") if isinstance(node.get("borderWidth"), str) else "extra_narrow"
    return _base_shape_node(
        f"{node.get('id') or f'frame_{index + 1}'}_bg",
        int(node.get("x") or 0),
        int(node.get("y") or 0),
        int(node.get("width") or 560),
        int(node.get("height") or 260),
        fill=str(node.get("fillColor") or theme.get("surface") or "#FFFFFF"),
        border=str(node.get("borderColor") or "#D8E0EA"),
        border_width=border_width,
        z_index=1 + index * 10,
    )


def _accent_bar_node(node: dict, index: int, theme: dict) -> dict:
    x = int(node.get("x") or 0)
    y = int(node.get("y") or 0)
    accent = str(node.get("accentColor") or theme.get("accent") or "#2F6BFF")
    return _base_shape_node(
        f"{node.get('id') or f'frame_{index + 1}'}_accent",
        x,
        y,
        4,
        int(node.get("height") or 260),
        fill=accent,
        border=accent,
        border_width="extra_narrow",
        z_index=2 + index * 10,
    )


def _dsl_frame_to_openapi_nodes(node: dict, index: int, theme: dict) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [_card_background_node(node, index, theme), _accent_bar_node(node, index, theme)]
    for child_index, child in enumerate(node.get("children") or []):
        if not isinstance(child, dict) or child.get("type") != "text":
            continue
        text_node = {**child}
        text_node.setdefault("fillColor", node.get("fillColor") or theme.get("surface") or "#FFFFFF")
        out.append(_text_box_node(text_node, index * 100 + child_index, theme, z_index=3 + index * 10 + child_index))
    return out


def _dsl_text_to_openapi_node(node: dict, index: int, theme: dict) -> dict:
    return {
        "id": str(node.get("id") or f"text_{index + 1}"),
        "type": "composite_shape",
        "x": int(node.get("x") or 0),
        "y": int(node.get("y") or 0),
        "width": int(node.get("width") or 800),
        "height": int(node.get("height") or 60),
        "style": {
            "fill_color": str(theme.get("background") or "#F8FAFC"),
            "border_color": str(theme.get("background") or "#F8FAFC"),
            "border_width": "extra_narrow",
        },
        "composite_shape": {"type": "round_rect"},
        "text": {
            "text": str(node.get("text") or ""),
            "font_size": int(node.get("fontSize") or 18),
            "text_color": str(node.get("textColor") or theme.get("text") or "#0F172A"),
        },
    }


def _dsl_frame_to_openapi_node(node: dict, index: int, theme: dict) -> dict:
    text = _frame_text(node)
    return {
        "id": str(node.get("id") or f"frame_{index + 1}"),
        "type": "composite_shape",
        "x": int(node.get("x") or 0),
        "y": int(node.get("y") or 0),
        "width": int(node.get("width") or 560),
        "height": int(node.get("height") or 260),
        "style": {
            "fill_color": str(node.get("fillColor") or theme.get("surface") or "#FFFFFF"),
            "border_color": str(node.get("borderColor") or theme.get("accent") or "#2F6BFF"),
            "border_width": "narrow",
        },
        "composite_shape": {"type": "round_rect"},
        "text": {
            "text": text,
            "font_size": 14,
            "text_color": str(theme.get("text") or "#0F172A"),
            "horizontal_align": "left",
            "vertical_align": "top",
        },
    }


def _frame_text(node: dict) -> str:
    parts: list[str] = []
    for child in node.get("children") or []:
        if not isinstance(child, dict) or child.get("type") != "text":
            continue
        text = str(child.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts) or str(node.get("title") or "Section")


def board_draft_to_whiteboard_dsl(draft: dict) -> str:
    title = str((draft.get("metadata") or {}).get("title") or "Board")
    theme = draft.get("theme") if isinstance(draft.get("theme"), dict) else {}
    nodes: list[dict[str, Any]] = []
    nodes.append(_dsl_title_node("title", title, 80, 56, theme, 30))
    y = 156
    for index, node in enumerate(draft.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        col = index % 2
        row = index // 2
        card_x = 80 + col * 628
        card_y = y + row * 300
        card_title = _clip_text(str(node.get("title") or node.get("type") or "Section"), 40)
        body_lines = _wrap_text(str(node.get("text") or ""), max_width=38, max_lines=7)
        nodes.append(
            {
                "id": str(node.get("id") or f"n_{index + 1}"),
                "type": "frame",
                "x": card_x,
                "y": card_y,
                "width": 580,
                "height": 250,
                "layout": "vertical",
                "gap": 8,
                "padding": 16,
                "fillColor": str(theme.get("surface") or "#FFFFFF"),
                "borderColor": str(theme.get("accent") or "#2F6BFF"),
                "borderWidth": 1,
                "borderRadius": 12,
                "children": [
                    {
                        "id": f"{node.get('id')}_title",
                        "type": "text",
                        "x": card_x + 18,
                        "y": card_y + 16,
                        "width": 540,
                        "height": TITLE_LINE_PX + TITLE_BOX_EXTRA_PX,
                        "text": card_title,
                        "fontSize": 18,
                        "textColor": str(theme.get("accent") or "#2F6BFF"),
                        "textAlign": "left",
                    },
                    {
                        "id": f"{node.get('id')}_text",
                        "type": "text",
                        "x": card_x + 18,
                        "y": card_y + 56,
                        "width": 540,
                        "height": 170,
                        "text": "\n".join(body_lines),
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
        "theme": theme,
        "nodes": nodes,
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
        text = "\n".join(_section_lines(section)) or str(section.get("description") or "")
        draft["nodes"].append({"id": section_id, "type": f"section_{kind}", "x": 0, "y": y, "title": title, "text": text})
        y += 220
    return draft


def board_ir_to_whiteboard_dsl(board_ir: dict) -> str:
    board = board_ir if isinstance(board_ir, dict) else {}
    theme = board.get("theme") if isinstance(board.get("theme"), dict) else {}
    nodes: list[dict[str, Any]] = [_dsl_title_node("title", str(board.get("title") or "Board"), 80, 56, theme, 30)]
    subtitle = str(board.get("subtitle") or "").strip()
    if subtitle:
        nodes.append(_dsl_title_node("subtitle", _clip_text(subtitle, 96), 80, 112, theme, 16, muted=True))
    for index, section in enumerate(board.get("sections") or []):
        if not isinstance(section, dict):
            continue
        kind = str(section.get("kind") or "overview")
        col = index % 2
        row = index // 2
        card_x = 80 + col * 628
        card_y = 188 + row * 328
        nodes.append(_section_to_dsl_frame(section, index, kind, card_x, card_y, theme))
    payload = {
        "version": 2,
        "metadata": {"title": str(board.get("title") or "Board"), "generatedFrom": "PlanB board_ir"},
        "theme": theme,
        "nodes": nodes,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _dsl_title_node(node_id: str, text: str, x: int, y: int, theme: dict, font_size: int, muted: bool = False) -> dict[str, Any]:
    height = int(font_size * 1.28) + (PAGE_TITLE_BOX_EXTRA_PX if font_size >= 24 else TITLE_BOX_EXTRA_PX)
    return {
        "id": node_id,
        "type": "text",
        "x": x,
        "y": y,
        "width": 1180,
        "height": height,
        "text": text,
        "fontSize": font_size,
        "textColor": str((theme.get("muted") or "#64748B") if muted else (theme.get("text") or "#0F172A")),
        "textAlign": "left",
    }


def _section_to_dsl_frame(section: dict, index: int, kind: str, x: int, y: int, theme: dict) -> dict[str, Any]:
    accent = str(section.get("accent") or theme.get("accent") or "#2F6BFF")
    title = _clip_text(str(section.get("title") or kind), 40)
    description = str(section.get("description") or "").strip()
    max_items = 5 if kind in {"overview", "cards", "summary"} else 4
    body_blocks = _section_display_blocks(section, kind, max_blocks=max_items)
    height = 286 if kind not in {"table", "flow", "timeline"} else 308
    if len(body_blocks) >= max_items:
        height = 340
    children = [
        {
            "id": f"s{index}_title",
            "type": "text",
            "x": x + CARD_TEXT_LEFT_PAD,
            "y": y + CARD_TITLE_TOP_PAD,
            "width": CARD_TEXT_WIDTH,
            "height": TITLE_LINE_PX + TITLE_BOX_EXTRA_PX,
            "text": title,
            "fontSize": 17,
            "textColor": accent,
            "textAlign": "left",
            "fontWeight": "bold",
        }
    ]
    body_y = y + 58
    if description:
        desc_lines = _wrap_text_for_box(description, CARD_TEXT_WIDTH, 12, 2)
        desc_height = DESC_LINE_PX * max(1, len(desc_lines)) + DESC_BOX_EXTRA_PX
        children.append(
            {
                "id": f"s{index}_desc",
                "type": "text",
                "x": x + CARD_TEXT_LEFT_PAD,
                "y": body_y,
                "width": CARD_TEXT_WIDTH,
                "height": desc_height,
                "text": "\n".join(desc_lines),
                "fontSize": 12,
                "textColor": str(theme.get("muted") or "#64748B"),
                "textAlign": "left",
            }
        )
        body_y += desc_height + 4
    body_y += CARD_BODY_TOP_NUDGE
    children.extend(_section_item_nodes(body_blocks, index, x + CARD_TEXT_LEFT_PAD, body_y, CARD_TEXT_WIDTH, height - (body_y - y) - CARD_BODY_BOTTOM_PAD, theme))
    return {
        "id": str(section.get("id") or f"section_{index + 1}"),
        "type": "frame",
        "x": x,
        "y": y,
        "width": 580,
        "height": height,
        "layout": "vertical",
        "gap": 8,
        "padding": 18,
        "fillColor": str(theme.get("surface") or "#FFFFFF"),
        "borderColor": "#D8E0EA",
        "accentColor": accent,
        "borderWidth": "extra_narrow",
        "borderRadius": 12,
        "children": children,
    }


def _section_item_nodes(blocks: list[str], section_index: int, x: int, y: int, width: int, height: int, theme: dict) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    cursor_y = y
    for item_index, block in enumerate(blocks):
        wrapped = _wrap_text_for_box(block, width, 13, BODY_ITEM_MAX_LINES)
        if not wrapped:
            continue
        block_height = BODY_LINE_PX * len(wrapped) + BODY_BOX_EXTRA_PX
        if cursor_y + block_height > y + height:
            remaining = y + height - cursor_y
            min_height = BODY_LINE_PX + BODY_BOX_EXTRA_PX
            if remaining < min_height:
                break
            wrapped = _wrap_text_for_box(block, width, 13, 1)
            if not wrapped:
                break
            wrapped[-1] = _clip_text(wrapped[-1], max(1, _text_width(wrapped[-1]) - 3)) + "..."
            block_height = min_height
        nodes.append(
            {
                "id": f"s{section_index}_body_{item_index}",
                "type": "text",
                "x": x,
                "y": cursor_y,
                "width": width,
                "height": block_height,
                "text": "\n".join(wrapped),
                "fontSize": 13,
                "textColor": str(theme.get("text") or "#0F172A"),
                "textAlign": "left",
            }
        )
        cursor_y += block_height + BODY_ITEM_GAP
    return nodes


def _section_display_blocks(section: dict, kind: str, max_blocks: int) -> list[str]:
    raw_blocks = _section_lines_for_board(section, kind)
    blocks: list[str] = []
    for block in raw_blocks:
        wrapped = _wrap_text_for_box(block, CARD_TEXT_WIDTH, 13, BODY_ITEM_MAX_LINES)
        if not wrapped:
            continue
        blocks.append("\n".join(wrapped))
        if len(blocks) >= max_blocks:
            break
    return blocks or ["No content available."]


def _section_lines_for_board(section: dict, kind: str) -> list[str]:
    lines: list[str] = []
    if kind in {"overview", "cards", "summary"}:
        for item in section.get("items") or []:
            text = str(item).strip()
            if text:
                lines.append(f"- {text}")
    elif kind == "flow":
        for index, node in enumerate(section.get("nodes") or []):
            if not isinstance(node, dict):
                continue
            label = str(node.get("label") or node.get("id") or "").strip()
            body = str(node.get("body") or "").strip()
            text = label if not body else f"{label}: {body}"
            if text:
                lines.append(f"{index + 1}. {text}")
    elif kind == "metrics":
        for item in section.get("metrics") or []:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            value = str(item.get("value") or "").strip()
            note = str(item.get("note") or "").strip()
            text = " ".join(part for part in (value, note) if part)
            if label or text:
                lines.append(f"{label}: {text}".strip(": "))
    elif kind == "table":
        columns = [str(col).strip() for col in section.get("columns") or [] if str(col).strip()]
        if columns:
            lines.append("Columns: " + " / ".join(columns[:5]))
        for row in section.get("rows") or []:
            if not isinstance(row, list):
                continue
            cells = [str(cell).strip() or "TBD" for cell in row]
            pairs = []
            for idx, cell in enumerate(cells[: len(columns) or 4]):
                if columns and idx < len(columns):
                    pairs.append(f"{columns[idx]}: {cell}")
                else:
                    pairs.append(cell)
            if pairs:
                lines.append("; ".join(pairs))
    elif kind == "timeline":
        for event in section.get("events") or []:
            if not isinstance(event, dict):
                continue
            date = str(event.get("date") or "").strip()
            title = str(event.get("title") or "").strip()
            body = str(event.get("body") or "").strip()
            head = " ".join(part for part in (date, title) if part)
            line = f"{head}: {body}" if body else head
            if line.strip():
                lines.append(line.strip())
    return [line for line in lines if line.strip()]


def _section_lines(section: dict) -> list[str]:
    kind = str(section.get("kind") or "overview")
    lines: list[str] = []
    if kind in {"overview", "cards", "summary"}:
        lines = [str(item) for item in section.get("items") or [] if str(item).strip()]
    elif kind == "flow":
        labels = [str(node.get("label") or node.get("id") or "") for node in section.get("nodes") or [] if isinstance(node, dict)]
        lines = [f"{index + 1}. {label}" for index, label in enumerate(labels) if label]
    elif kind == "metrics":
        lines = [f"{item.get('label', '')}: {item.get('value', '')} {item.get('note', '')}".strip() for item in section.get("metrics") or [] if isinstance(item, dict)]
    elif kind == "table":
        columns = [str(col) for col in section.get("columns") or []]
        if columns:
            lines.append(" | ".join(columns))
        for row in section.get("rows") or []:
            if isinstance(row, list):
                lines.append(" | ".join(str(cell) for cell in row))
    elif kind == "timeline":
        lines = [f"{event.get('date', '')} - {event.get('title', '')}: {event.get('body', '')}".strip() for event in section.get("events") or [] if isinstance(event, dict)]
    description = str(section.get("description") or "").strip()
    if description:
        lines.insert(0, description)
    return [line for line in lines if line.strip()]


def _edge_pair(edge: Any) -> tuple[str, str]:
    if isinstance(edge, dict):
        return str(edge.get("from")), str(edge.get("to"))
    if isinstance(edge, list) and len(edge) >= 2:
        return str(edge[0]), str(edge[1])
    return "", ""
