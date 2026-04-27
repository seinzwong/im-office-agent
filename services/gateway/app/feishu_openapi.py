"""
飞书开放平台 HTTP 调用（tenant_access_token），替代 lark-cli 子进程。
"""
from __future__ import annotations

import logging
import re
import time
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
