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
            rows = _normalize_table_rows(block.get("rows", []))
            if rows:
                children.append(
                    {
                        "block_type": 31,
                        "table": {"property": {"row_size": len(rows), "column_size": _table_width(rows), "header_row": True}},
                        "_table_rows": rows,
                    }
                )
            elif block.get("caption"):
                children.append(_text_block(2, "text", str(block.get("caption") or "")))
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
            "url": _docx_url(config["base_url"], document_id),
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
        parent_id = self.page_block_id(document_id)
        results: list[dict] = []
        simple_batch: list[dict] = []

        def flush_simple() -> None:
            if not simple_batch:
                return
            results.append(self._create_child_blocks(document_id, parent_id, simple_batch))
            simple_batch.clear()

        for child in children:
            if _is_internal_table_child(child):
                flush_simple()
                try:
                    results.append(self._create_table_descendants(document_id, parent_id, child))
                except Exception:
                    fallback = _table_fallback_children(child)
                    if fallback:
                        results.append(self._create_child_blocks(document_id, parent_id, fallback))
            else:
                simple_batch.append(_strip_internal_keys(child))
        flush_simple()
        return {"results": results}

    def _create_child_blocks(self, document_id: str, parent_id: str, children: list[dict]) -> dict:
        response = httpx.post(
            f"{self.base_url}/open-apis/docx/v1/documents/{document_id}/blocks/{parent_id}/children",
            headers=self.authorization_header(),
            json={"children": children, "index": -1},
            timeout=30.0,
        )
        _raise_for_feishu_error(response)
        data = response.json()
        if data.get("code") not in (None, 0):
            raise RuntimeError(str(data))
        return data.get("data") or {}

    def _create_table_descendants(self, document_id: str, parent_id: str, table_child: dict) -> dict:
        descendant_payload = _table_descendant_payload(table_child)
        response = httpx.post(
            f"{self.base_url}/open-apis/docx/v1/documents/{document_id}/blocks/{parent_id}/descendant",
            headers=self.authorization_header(),
            json=descendant_payload,
            timeout=30.0,
        )
        _raise_for_feishu_error(response)
        data = response.json()
        if data.get("code") not in (None, 0):
            raise RuntimeError(str(data))
        return data.get("data") or {}

    def page_block_id(self, document_id: str) -> str:
        response = httpx.get(
            f"{self.base_url}/open-apis/docx/v1/documents/{document_id}/blocks",
            headers=self.authorization_header(),
            params={"page_size": 80},
            timeout=30.0,
        )
        if _is_docx_read_permission_error(response):
            return document_id
        _raise_for_feishu_error(response)
        data = response.json()
        if data.get("code") not in (None, 0):
            return document_id
        items = (data.get("data") or {}).get("items") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            block = item.get("block")
            if isinstance(block, dict) and block.get("block_type") == 1:
                block_id = str(block.get("block_id") or "").strip()
                if block_id:
                    return block_id
        return document_id


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
    if block.get("description"):
        out.append({"type": "quote", "text": str(block.get("description") or "")})
    if kind == "cover":
        out.extend({"type": "paragraph", "text": str(block[key])} for key in ("kicker", "subtitle") if block.get(key))
    elif kind == "split":
        out.extend({"type": "bullet", "text": str(point)} for point in block.get("points", []))
    elif kind == "flow":
        out.append({"type": "code", "language": "mermaid", "text": _mermaid(block)})
    elif kind == "metrics":
        rows = [["指标", "数值", "说明"], *[[i.get("label", ""), i.get("value", ""), i.get("note", "")] for i in block.get("items", []) if isinstance(i, dict)]]
        out.append(_table_doc_block(rows, str(block.get("caption") or "")))
    elif kind == "cards":
        for card in block.get("cards", []):
            if isinstance(card, dict):
                out.append({"type": "heading", "text": str(card.get("title", "")), "level": 3})
                meta = " | ".join(_display_meta_items(card.get("meta", [])))
                if meta:
                    out.append({"type": "paragraph", "text": meta})
                if card.get("body"):
                    out.append({"type": "paragraph", "text": str(card.get("body", ""))})
    elif kind == "table":
        if block.get("caption"):
            out.append({"type": "paragraph", "text": str(block.get("caption") or "")})
        out.append(_table_doc_block([block.get("columns", []), *block.get("rows", [])], str(block.get("caption") or "")))
    elif kind == "timeline":
        out.extend({"type": "bullet", "text": _timeline_event_text(event)} for event in block.get("events", []) if isinstance(event, dict))
    elif kind == "image":
        out.append({"type": "paragraph", "text": f"[Image] {block.get('caption') or block.get('image') or ''}"})
    out.extend(_source_ref_blocks(block.get("sourceRefs")))
    return out


def _table_doc_block(rows: list[list], caption: str = "") -> dict:
    normalized = _normalize_table_rows(rows)
    return {
        "type": "table",
        "rows": normalized,
        "caption": caption,
    } if normalized else {"type": "paragraph", "text": caption}


def _timeline_event_text(event: dict) -> str:
    meta = " | ".join(str(event.get(key) or "").strip() for key in ("owner", "status") if str(event.get(key) or "").strip())
    prefix = f"{event.get('date', '')} {event.get('title', '')}".strip()
    body = str(event.get("body") or "").strip()
    text = f"{prefix}: {body}" if body else prefix
    return f"{text} ({meta})" if meta else text


def _source_ref_blocks(value: Any) -> list[dict]:
    refs = [_display_source_ref(item) for item in value] if isinstance(value, list) else []
    refs = [item for item in refs if item]
    if not refs:
        return []
    return [{"type": "quote", "text": "来源：" + " | ".join(refs)}]


def _display_meta_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_display_meta_item(raw) for raw in value) if item]


def _display_meta_item(value: Any) -> str:
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


def _display_source_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lower = text.lower()
    if "http://" in lower or "https://" in lower:
        return "文档链接"
    if "message:" in lower:
        return "消息"
    if "om_" in lower:
        return "消息"
    return text if len(text) <= 40 else text[:37] + "..."


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


def _normalize_table_rows(rows: Any) -> list[list[str]]:
    if not isinstance(rows, list):
        return []
    width = _table_width(rows)
    normalized = []
    for row in rows:
        if not isinstance(row, list):
            continue
        cells = [str(cell).strip() or "待确认" for cell in row]
        normalized.append((cells + ["待确认"] * width)[:width])
    return [row for row in normalized if any(cell.strip() for cell in row)]


def _is_internal_table_child(child: dict) -> bool:
    return child.get("block_type") == 31 and isinstance(child.get("_table_rows"), list)


def _strip_internal_keys(child: dict) -> dict:
    return {key: value for key, value in child.items() if not str(key).startswith("_")}


def _table_descendant_payload(table_child: dict) -> dict:
    rows = _normalize_table_rows(table_child.get("_table_rows"))
    row_size = len(rows)
    column_size = _table_width(rows)
    table_id = "table_1"
    descendants: list[dict] = [
        {
            "block_id": table_id,
            "block_type": 31,
            "table": {"property": {"row_size": row_size, "column_size": column_size, "header_row": True}},
            "children": [
                f"cell_{row_index}_{column_index}"
                for row_index in range(row_size)
                for column_index in range(column_size)
            ],
        }
    ]
    for row_index, row in enumerate(rows):
        for column_index, text in enumerate(row):
            cell_id = f"cell_{row_index}_{column_index}"
            text_id = f"text_{row_index}_{column_index}"
            descendants.append({"block_id": cell_id, "block_type": 32, "children": [text_id]})
            descendants.append({"block_id": text_id, **_text_block(2, "text", text)})
    return {"children_id": [table_id], "descendants": descendants, "index": -1}


def _table_fallback_children(table_child: dict) -> list[dict]:
    rows = _normalize_table_rows(table_child.get("_table_rows"))
    return [_text_block(14, "code", "\n".join(_markdown_table(rows)))] if rows else []


def _markdown_table(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    width = _table_width(rows)
    normalized = [(row + [""] * width)[:width] for row in rows]
    return [
        "| " + " | ".join(normalized[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
        *["| " + " | ".join(row) + " |" for row in normalized[1:]],
    ]


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


def _docx_url(base_url: str, document_id: str) -> str:
    if not document_id:
        return ""
    if "larksuite" in (base_url or "").lower():
        return f"https://www.larksuite.com/docx/{document_id}"
    return f"https://www.feishu.cn/docx/{document_id}"


def _raise_for_feishu_error(response: httpx.Response) -> None:
    try:
        data = response.json()
    except Exception:
        data = response.text
    if response.status_code >= 400:
        raise RuntimeError(f"Feishu HTTP {response.status_code}: {data}")


def _is_docx_read_permission_error(response: httpx.Response) -> bool:
    if response.status_code != 400:
        return False
    try:
        data = response.json()
    except Exception:
        return False
    if not isinstance(data, dict) or data.get("code") != 99991679:
        return False
    violations = ((data.get("error") or {}).get("permission_violations") or [])
    subjects = {
        str(item.get("subject") or "")
        for item in violations
        if isinstance(item, dict)
    }
    return bool({"docx:document", "docx:document:readonly"} & subjects)
