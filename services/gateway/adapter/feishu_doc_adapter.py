from __future__ import annotations

from typing import Any

import httpx

from services.gateway.config import get_settings

from .ir_schema import ensure_ir_defaults, validate_ir
from .markdown_adapter import ir_to_markdown
from .runtime import adapter_error, env_value, extract_folder_token, run_lark_cli


def ir_to_feishu_doc_blocks(ir: dict) -> list[dict]:
    ir = ensure_ir_defaults(ir)
    meta = ir["meta"]
    blocks: list[dict] = [{"type": "heading", "text": meta["title"], "level": 1}]
    if meta.get("subtitle"):
        blocks.append({"type": "quote", "text": meta["subtitle"]})
    info = " | ".join(value for value in [meta.get("owner"), meta.get("date"), meta.get("audience")] if value)
    if info:
        blocks.append({"type": "paragraph", "text": info})
    blocks.append({"type": "divider"})
    for block in ir.get("blocks", []):
        if isinstance(block, dict):
            blocks.extend(_block_to_doc_blocks(block))
    return blocks


def feishu_doc_blocks_to_openapi_children(blocks: list[dict]) -> list[dict]:
    children = []
    for block in blocks:
        block_type = block.get("type")
        text = str(block.get("text") or "")
        if block_type == "heading":
            level = max(1, min(9, int(block.get("level") or 1)))
            children.append(_text_block(level + 2, f"heading{level}", text))
        elif block_type == "paragraph":
            children.append(_text_block(2, "text", text))
        elif block_type == "bullet":
            children.append(_text_block(12, "bullet", text))
        elif block_type == "quote":
            children.append(_text_block(15, "quote", text))
        elif block_type == "code":
            children.append({"block_type": 14, "code": {"elements": [_text_element(text)]}})
        elif block_type == "table":
            rows = block.get("rows", [])
            children.append({"block_type": 31, "table": {"property": {"row_size": len(rows), "column_size": _table_width(rows)}}})
        elif block_type == "divider":
            children.append({"block_type": 22, "divider": {}})
        else:
            children.append(_text_block(2, "text", text))
    return children


def publish_ir_to_feishu_doc(ir: dict, options: dict) -> dict:
    warnings: list[str] = []
    normalized = ensure_ir_defaults(ir)
    errors = validate_ir(normalized)
    if errors:
        return adapter_error("IR_VALIDATION_FAILED", "IR validation failed", errors, warnings)

    options = options or {}
    mode = str(options.get("mode") or "create")
    if mode not in {"create", "append", "replace"}:
        return adapter_error("INVALID_PUBLISH_MODE", "Doc mode must be create, append, or replace.", [mode], warnings)
    if mode == "replace":
        return adapter_error("UNSUPPORTED_OPERATION", "Doc replace is not supported in v1.", [], warnings)

    draft_blocks = ir_to_feishu_doc_blocks(normalized)
    children = feishu_doc_blocks_to_openapi_children(draft_blocks)
    if options.get("dry_run", True):
        return {
            "ok": True,
            "mode": "dry_run",
            "document_id": options.get("document_id"),
            "blocks_preview": draft_blocks,
            "openapi_children_preview": children,
            "markdown_fallback": ir_to_markdown(normalized),
            "warnings": warnings,
        }

    config = _doc_config()
    explicit_user_token = str(options.get("user_access_token") or env_value("FEISHU_USER_ACCESS_TOKEN") or "").strip()
    explicit_tenant_token = str(options.get("tenant_access_token") or env_value("FEISHU_TENANT_ACCESS_TOKEN") or "").strip()
    cli_user_mode = str(options.get("auth_mode") or env_value("FEISHU_DOC_AUTH_MODE")).strip().lower() == "cli_user"
    if cli_user_mode and not explicit_user_token:
        return _publish_doc_with_lark_cli_user(normalized, options, config, warnings)
    if not (explicit_user_token or explicit_tenant_token) and (not config["app_id"] or not config["app_secret"]):
        warnings.append("Feishu token/app credentials missing; fallback to dry_run.")
        return {"ok": True, "mode": "dry_run", "blocks_preview": draft_blocks, "warnings": warnings}

    try:
        client = FeishuDocClient(
            config["app_id"],
            config["app_secret"],
            config["base_url"],
            user_access_token=explicit_user_token,
            tenant_access_token=explicit_tenant_token,
        )
        document_id = _resolve_document_id(client, normalized, options, config, mode, warnings)
        if not document_id:
            return adapter_error("FEISHU_DOC_API_FAILED", "document_id is missing.", [], warnings)
        client.create_blocks(document_id, children)
        result = {
            "ok": True,
            "mode": mode,
            "document_id": document_id,
            "auth_mode": client.auth_mode,
            "warnings": warnings,
        }
        if options.get("include_whiteboard"):
            result["whiteboard_result"] = append_blank_whiteboard_to_doc(document_id, str(options.get("as") or "user"))
        return result
    except Exception as exc:  # noqa: BLE001
        return adapter_error("FEISHU_DOC_API_FAILED", str(exc), [], warnings)


def _publish_doc_with_lark_cli_user(ir: dict, options: dict, config: dict, warnings: list[str]) -> dict:
    folder_token = extract_folder_token(options.get("folder_token") or config["folder_token"])
    if not folder_token:
        return adapter_error("FEISHU_DOC_FOLDER_TOKEN_MISSING", "folder_token is required for cli_user publishing.", [], warnings)
    result = run_lark_cli(
        [
            "docs",
            "+create",
            "--title",
            ir["meta"]["title"],
            "--folder-token",
            folder_token,
            "--markdown",
            ir_to_markdown(ir),
            "--as",
            "user",
        ],
        timeout=180,
    )
    if not result["ok"]:
        return adapter_error("FEISHU_DOC_API_FAILED", "lark-cli docs +create failed.", result, warnings)
    data = result["json"].get("data") or result["json"]
    return {
        "ok": True,
        "mode": "create",
        "auth_mode": "cli_user",
        "document_id": data.get("doc_id") or data.get("document_id") or data.get("token"),
        "url": data.get("doc_url") or data.get("url") or data.get("open_url"),
        "warnings": warnings,
    }


def append_blank_whiteboard_to_doc(document_id: str, identity: str = "user") -> dict:
    result = run_lark_cli(
        [
            "docs",
            "+update",
            "--doc",
            document_id,
            "--mode",
            "append",
            "--markdown",
            "## Board\n<whiteboard type=\"blank\"></whiteboard>\n",
            "--as",
            identity,
        ]
    )
    if not result["ok"]:
        return {"ok": False, "error": result}
    return {"ok": True, "stdout": result["stdout"]}


class FeishuDocClient:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        base_url: str = "https://open.feishu.cn",
        *,
        user_access_token: str = "",
        tenant_access_token: str = "",
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url.rstrip("/")
        self._user_access_token = user_access_token.strip()
        self._tenant_access_token: str | None = tenant_access_token.strip() or None

    @property
    def auth_mode(self) -> str:
        return "user_access_token" if self._user_access_token else "tenant_access_token"

    def authorization_header(self) -> dict[str, str]:
        if self._user_access_token:
            return {"Authorization": f"Bearer {self._user_access_token}"}
        return {"Authorization": f"Bearer {self.get_tenant_access_token()}"}

    def get_tenant_access_token(self) -> str:
        if self._tenant_access_token:
            return self._tenant_access_token
        response = httpx.post(
            f"{self.base_url}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=30.0,
        )
        _raise_for_feishu_error(response)
        data = response.json()
        token = data.get("tenant_access_token") or (data.get("data") or {}).get("tenant_access_token")
        if not token:
            raise RuntimeError(f"tenant_access_token missing: {data}")
        self._tenant_access_token = str(token)
        return self._tenant_access_token

    def create_document(self, title: str, folder_token: str) -> dict:
        response = httpx.post(
            f"{self.base_url}/open-apis/docx/v1/documents",
            headers=self.authorization_header(),
            json={"folder_token": folder_token, "title": title},
            timeout=30.0,
        )
        _raise_for_feishu_error(response)
        data = response.json()
        if data.get("code") not in (None, 0):
            raise RuntimeError(str(data))
        return data.get("data") or {}

    def create_blocks(self, document_id: str, children: list[dict]) -> dict:
        response = httpx.post(
            f"{self.base_url}/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children",
            headers=self.authorization_header(),
            json={"children": children, "index": -1},
            timeout=30.0,
        )
        _raise_for_feishu_error(response)
        data = response.json()
        if data.get("code") not in (None, 0):
            raise RuntimeError(str(data))
        return data.get("data") or {}


def _resolve_document_id(client: FeishuDocClient, ir: dict, options: dict, config: dict, mode: str, warnings: list[str]) -> str:
    if mode == "append":
        return str(options.get("document_id") or "").strip()
    folder_token = extract_folder_token(options.get("folder_token") or config["folder_token"])
    if not folder_token:
        warnings.append("FEISHU_DOC_FOLDER_TOKEN missing.")
        return ""
    return _extract_document_id(client.create_document(ir["meta"]["title"], folder_token))


def _block_to_doc_blocks(block: dict) -> list[dict]:
    kind = block.get("kind")
    title = str(block.get("title") or "")
    out: list[dict] = [{"type": "heading", "text": title, "level": 2, "source_refs": {"block_id": block.get("id")}}]
    if kind == "cover":
        out.extend({"type": "paragraph", "text": str(block[key])} for key in ("kicker", "subtitle") if block.get(key))
    elif kind == "split":
        out.extend({"type": "bullet", "text": str(point)} for point in block.get("points", []))
    elif kind == "flow":
        out.append({"type": "code", "language": "mermaid", "text": _mermaid(block)})
    elif kind == "metrics":
        out.append({"type": "table", "rows": [["Metric", "Value", "Note"], *[[i.get("label", ""), i.get("value", ""), i.get("note", "")] for i in block.get("items", []) if isinstance(i, dict)]]})
    elif kind == "cards":
        for card in block.get("cards", []):
            if isinstance(card, dict):
                out.extend([{"type": "heading", "text": str(card.get("title", "")), "level": 3}, {"type": "paragraph", "text": str(card.get("body", ""))}])
    elif kind == "table":
        out.append({"type": "table", "rows": [block.get("columns", []), *block.get("rows", [])]})
    elif kind == "timeline":
        out.extend({"type": "bullet", "text": f"{event.get('date', '')} {event.get('title', '')}: {event.get('body', '')}"} for event in block.get("events", []) if isinstance(event, dict))
    elif kind == "image":
        out.append({"type": "paragraph", "text": f"[Image] {block.get('caption') or block.get('image') or ''}"})
    return out


def _mermaid(block: dict) -> str:
    lines = ["flowchart LR"]
    for node in block.get("nodes", []):
        if isinstance(node, dict):
            lines.append(f"  {node.get('id')}[\"{node.get('label', node.get('id'))}\"]")
    for edge in block.get("edges", []):
        if isinstance(edge, list) and len(edge) >= 2:
            lines.append(f"  {edge[0]} --> {edge[1]}")
        elif isinstance(edge, dict):
            lines.append(f"  {edge.get('from')} --> {edge.get('to')}")
    return "\n".join(lines)


def _doc_config() -> dict[str, str]:
    settings = get_settings()
    return {
        "app_id": env_value("FEISHU_APP_ID", settings.lark_app_id),
        "app_secret": env_value("FEISHU_APP_SECRET", settings.lark_app_secret),
        "folder_token": env_value("FEISHU_DOC_FOLDER_TOKEN", settings.artifacts_drive_folder_token),
        "base_url": env_value("FEISHU_BASE_URL", settings.lark_base_url),
    }


def _text_block(block_type: int, key: str, text: str) -> dict:
    return {"block_type": block_type, key: {"elements": [_text_element(text)]}}


def _text_element(text: str) -> dict:
    return {"text_run": {"content": text, "text_element_style": {}}}


def _table_width(rows: list) -> int:
    return max([len(row) for row in rows if isinstance(row, list)] or [1])


def _extract_document_id(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    document = value.get("document")
    candidates = [
        value.get("document_id"),
        value.get("token"),
        value.get("file_token"),
        document.get("document_id") if isinstance(document, dict) else None,
        document.get("token") if isinstance(document, dict) else None,
    ]
    return next((str(candidate).strip() for candidate in candidates if str(candidate or "").strip()), "")


def _raise_for_feishu_error(response: httpx.Response) -> None:
    try:
        data = response.json()
    except Exception:
        data = response.text
    if response.status_code >= 400:
        raise RuntimeError(f"Feishu HTTP {response.status_code}: {data}")
