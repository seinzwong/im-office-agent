from __future__ import annotations

import logging
from typing import Any

from .config import get_settings
from .feishu_openapi import drive_list_folder_files

log = logging.getLogger(__name__)


def _dev_mock_artifacts() -> list[dict[str, Any]]:
    """
    开发跳过飞书时返回多条占位数据，便于 H5 联调展示。
    """
    return [
        {
            "file_token": "dev-mock-docx-1",
            "name": "Q1 周会群聊总结（示例）",
            "type": "docx",
            "url": "https://dev-placeholder.invalid/docx/dev-1",
            "updated_time": 1745568000,
        },
        {
            "file_token": "dev-mock-wb-1",
            "name": "迭代复盘-画板（示例）",
            "type": "file",
            "url": "https://dev-placeholder.invalid/whiteboard/dev-1",
            "updated_time": 1745600000,
        },
        {
            "file_token": "dev-mock-slides-1",
            "name": "评审用演示稿-占位",
            "type": "slides",
            "url": "https://dev-placeholder.invalid/slides/dev-1",
            "updated_time": 1745481600,
        },
    ]


def _normalize_file_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    file_token = item.get("token") or item.get("file_token") or ""
    if not file_token:
        return None
    name = str(item.get("name", "") or "未命名")
    ftype = str(item.get("type", "") or "unknown")
    url = str(item.get("url", "") or "")
    return {
        "file_token": file_token,
        "name": name,
        "type": ftype,
        "url": url,
        "updated_time": item.get("modified_time") or item.get("edit_time"),
    }


def list_artifacts() -> list[dict[str, Any]]:
    s = get_settings()
    if s.dev_skip_lark or not (s.artifacts_drive_folder_token or "").strip():
        return _dev_mock_artifacts()
    try:
        raw = drive_list_folder_files(s, s.artifacts_drive_folder_token.strip())
    except Exception as e:  # noqa: BLE001
        log.warning("list_artifacts OpenAPI: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for it in raw:
        n = _normalize_file_item(it)
        if n:
            out.append(n)
    return out


def get_artifact_body_for_file_token(file_token: str) -> tuple[str, str, str]:
    """
    根据已列出的目录项构造 Agents 用正文。返回 (title, open_url, body_plain)；
    无匹配时仅返回 body_plain 为 file_token 说明。
    """
    for a in list_artifacts():
        if a.get("file_token") == file_token:
            title = str(a.get("name", "") or "")
            u = str(a.get("url", "") or "")
            body = f"{title}\n{u}".strip() or f"file_token={file_token}"
            return (title, u, body)
    return ("", "", f"file_token={file_token}")
