"""
飞书开放平台 HTTP 调用（tenant_access_token），替代 lark-cli 子进程。
"""
from __future__ import annotations

import logging
import json
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger(__name__)

_tenant_cache: dict[str, Any] = {"token": "", "exp": 0.0}


def _api_base(s: Settings) -> str:
    return (s.lark_base_url or "https://open.feishu.cn").rstrip("/")


def _docx_view_base(s: Settings) -> str:
    if "larksuite" in (s.lark_base_url or "").lower():
        return "https://www.larksuite.com/docx/"
    return "https://www.feishu.cn/docx/"


def docx_open_url(s: Settings, document_id: str) -> str:
    doc_id = (document_id or "").strip()
    if not doc_id:
        return ""
    return f"{_docx_view_base(s).rstrip('/')}/{doc_id}"


def get_tenant_access_token(s: Settings) -> str:
    if not (s.lark_app_id and s.lark_app_secret):
        raise ValueError("LARK_APP_ID / LARK_APP_SECRET 未配置")
    now = time.time()
    if _tenant_cache["token"] and now < float(_tenant_cache["exp"]) - 120:
        return str(_tenant_cache["token"])
    base = _api_base(s)
    r = httpx.post(
        f"{base}/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": s.lark_app_id, "app_secret": s.lark_app_secret},
        timeout=30.0,
    )
    r.raise_for_status()
    j = r.json()
    if j.get("code", 0) not in (0, None):
        raise RuntimeError(f"tenant_access_token: code={j.get('code')} msg={j.get('msg')}")
    tok = str(j.get("tenant_access_token") or "")
    if not tok:
        raise RuntimeError("tenant_access_token 响应无 token")
    exp = int(j.get("expire", 7200))
    _tenant_cache["token"] = tok
    _tenant_cache["exp"] = now + max(60, exp)
    return tok


def drive_list_folder_files(s: Settings, folder_token: str) -> list[dict[str, Any]]:
    """
    列出文件夹下一层文件。返回飞书 `files` 数组中的原始 dict（含 token/name/type/url 等）。
    """
    base = _api_base(s)
    token = get_tenant_access_token(s)
    headers = {"Authorization": f"Bearer {token}"}
    out: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        params: dict[str, Any] = {"folder_token": folder_token.strip(), "page_size": 200}
        if page_token:
            params["page_token"] = page_token
        r = httpx.get(f"{base}/open-apis/drive/v1/files", params=params, headers=headers, timeout=60.0)
        j = r.json()
        if j.get("code", 0) != 0:
            log.warning(
                "drive/v1/files: code=%s msg=%s folder=%s...",
                j.get("code"),
                j.get("msg"),
                folder_token[:12],
            )
            break
        data = j.get("data") or {}
        files = data.get("files") or []
        if isinstance(files, list):
            out.extend([x for x in files if isinstance(x, dict)])
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
        if not page_token:
            break
    return out


def reply_text_to_message(s: Settings, message_id: str, text: str) -> dict[str, Any]:
    """Reply to a Feishu/Lark message with a text message."""
    mid = (message_id or "").strip()
    if not mid:
        return {"ok": False, "error": "message_id is required"}
    base = _api_base(s)
    token = get_tenant_access_token(s)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    r = httpx.post(
        f"{base}/open-apis/im/v1/messages/{mid}/reply",
        headers=headers,
        json={
            "msg_type": "text",
            "content": json.dumps({"text": text or ""}, ensure_ascii=False),
        },
        timeout=30.0,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code", 0) != 0:
        raise RuntimeError(f"message reply: code={data.get('code')} msg={data.get('msg')}")
    return data.get("data") or {}


def list_chat_messages(
    s: Settings,
    chat_id: str,
    start_unix: int,
    end_unix: int,
    limit: int = 200,
) -> list[dict[str, str]]:
    """Fetch readable text messages from a chat in chronological order."""
    cid = (chat_id or "").strip()
    if not cid:
        return []
    remaining = max(1, min(int(limit or 200), 200))
    base = _api_base(s)
    token = get_tenant_access_token(s)
    headers = {"Authorization": f"Bearer {token}"}
    out: list[dict[str, str]] = []
    page_token: str | None = None
    while remaining > 0:
        params: dict[str, Any] = {
            "container_id_type": "chat",
            "container_id": cid,
            "start_time": str(int(start_unix)),
            "end_time": str(int(end_unix)),
            "page_size": min(50, remaining),
        }
        if page_token:
            params["page_token"] = page_token
        r = httpx.get(
            f"{base}/open-apis/im/v1/messages",
            params=params,
            headers=headers,
            timeout=60.0,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(f"message list: code={data.get('code')} msg={data.get('msg')}")
        body = data.get("data") or {}
        items = body.get("items") or []
        if not isinstance(items, list):
            break
        for item in items:
            if not isinstance(item, dict):
                continue
            text = _message_plain_text(item)
            if not text:
                continue
            out.append(
                {
                    "message_id": str(item.get("message_id") or item.get("id") or f"msg_{len(out) + 1}"),
                    "sender": _message_sender(item),
                    "timestamp": _message_timestamp(item),
                    "text": text,
                }
            )
            remaining -= 1
            if remaining <= 0:
                break
        if remaining <= 0 or not body.get("has_more"):
            break
        page_token = str(body.get("page_token") or "")
        if not page_token:
            break
    return sorted(out, key=lambda item: item.get("timestamp") or "")


def _message_plain_text(item: dict[str, Any]) -> str:
    content = item.get("body", {}).get("content") if isinstance(item.get("body"), dict) else item.get("content")
    parsed = _parse_message_content(content)
    text = str(parsed.get("text") or "").strip()
    if text:
        return text
    post = parsed.get("post")
    if isinstance(post, dict):
        return _post_plain_text(post)
    return ""


def _parse_message_content(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if isinstance(content, str) and content.strip().startswith("{"):
        try:
            value = json.loads(content)
            return value if isinstance(value, dict) else {}
        except Exception:  # noqa: BLE001
            return {"text": content}
    if isinstance(content, str):
        return {"text": content}
    return {}


def _post_plain_text(post: dict[str, Any]) -> str:
    zh_cn = post.get("zh_cn") if isinstance(post.get("zh_cn"), dict) else {}
    content = zh_cn.get("content")
    lines: list[str] = []
    for line in content if isinstance(content, list) else []:
        pieces: list[str] = []
        for node in line if isinstance(line, list) else []:
            if not isinstance(node, dict):
                continue
            tag = node.get("tag")
            if tag == "text":
                pieces.append(str(node.get("text") or ""))
            elif tag == "at":
                pieces.append(str(node.get("user_name") or node.get("user_id") or ""))
        joined = "".join(pieces).strip()
        if joined:
            lines.append(joined)
    return "\n".join(lines).strip()


def _message_sender(item: dict[str, Any]) -> str:
    sender = item.get("sender") if isinstance(item.get("sender"), dict) else {}
    sender_id = sender.get("id") if isinstance(sender.get("id"), dict) else sender.get("sender_id")
    if isinstance(sender_id, dict):
        for key in ("open_id", "user_id", "union_id"):
            value = str(sender_id.get(key) or "").strip()
            if value:
                return value
    for key in ("sender_type", "id"):
        value = str(sender.get(key) or "").strip()
        if value:
            return value
    return "unknown"


def _message_timestamp(item: dict[str, Any]) -> str:
    raw = str(item.get("create_time") or item.get("update_time") or "").strip()
    try:
        value = int(raw)
        if value > 10_000_000_000:
            value = value // 1000
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except Exception:
        return raw


def _xmlish_to_plain(xml: str, max_len: int = 120_000) -> str:
    t = re.sub(r"<[^>]+>", "\n", xml)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if len(t) > max_len:
        t = t[: max_len - 12] + "\n…（已截断）"
    return t or "（无正文）"


def _chunk_text(s: str, step: int = 3500) -> list[str]:
    if not s:
        return ["（无正文）"]
    return [s[i : i + step] for i in range(0, len(s), step)]


def _docx_page_block_id(s: Settings, document_id: str, headers: dict[str, str]) -> str:
    base = _api_base(s)
    r = httpx.get(
        f"{base}/open-apis/docx/v1/documents/{document_id}/blocks",
        headers=headers,
        params={"page_size": 80},
        timeout=60.0,
    )
    j = r.json()
    if j.get("code", 0) != 0:
        return document_id
    items = (j.get("data") or {}).get("items") or []
    for it in items:
        if not isinstance(it, dict):
            continue
        blk = it.get("block")
        if isinstance(blk, dict) and blk.get("block_type") == 1:
            bid = str(blk.get("block_id") or "")
            if bid:
                return bid
    return document_id


def docx_create_in_folder_with_plain_text(
    s: Settings, folder_token: str, title: str, content_xml: str
) -> tuple[str, str]:
    """
    在指定云盘目录创建 docx，写入由 XML 粗转的正文（多文本块）。
    返回 (open_url, document_id)；失败时 ("", "").
    """
    folder = (folder_token or "").strip()
    if not folder:
        return ("", "")
    base = _api_base(s)
    token = get_tenant_access_token(s)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    safe_title = (title or "未命名")[:800] or "未命名"
    cr = httpx.post(
        f"{base}/open-apis/docx/v1/documents",
        headers=headers,
        json={"folder_token": folder, "title": safe_title},
        timeout=60.0,
    )
    cj = cr.json()
    if cj.get("code", 0) != 0:
        log.warning("docx/v1/documents create: code=%s msg=%s", cj.get("code"), cj.get("msg"))
        return ("", "")
    doc = (cj.get("data") or {}).get("document") or {}
    doc_id = str(doc.get("document_id") or "")
    if not doc_id:
        return ("", "")
    view = _docx_view_base(s)
    open_url = f"{view.rstrip('/')}/{doc_id}"

    plain = _xmlish_to_plain(content_xml)
    parent_id = _docx_page_block_id(s, doc_id, headers)
    chunks = _chunk_text(plain)
    children: list[dict[str, Any]] = []
    for part in chunks:
        children.append(
            {
                "block_type": 2,
                "text": {
                    "style": {},
                    "elements": [{"text_run": {"content": part}}],
                },
            }
        )
    br = httpx.post(
        f"{base}/open-apis/docx/v1/documents/{doc_id}/blocks/{parent_id}/children",
        headers=headers,
        json={"children": children, "index": 0},
        timeout=120.0,
    )
    bj = br.json()
    if bj.get("code", 0) != 0:
        log.warning(
            "docx append blocks: code=%s msg=%s (文档已创建，可能无正文)",
            bj.get("code"),
            bj.get("msg"),
        )
    return (open_url, doc_id)
