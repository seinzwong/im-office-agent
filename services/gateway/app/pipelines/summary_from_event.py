from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Optional, Tuple

from ..agents.client import AgentsClient
from ..config import get_settings
from ..feishu_openapi import docx_create_in_folder_with_plain_text

log = logging.getLogger(__name__)


def run_summary_for_chat(
    chat_id: str,
    time_hint: str,
    t0_unix: int,
    aggregate_text: str,
    source_message_id: str = "",
) -> Optional[dict[str, Any]]:
    """用 Agents 出文档结构 + lark docs 落盘到配置目录；不落库。返回 {title, open_url, file_token, chat_id, time_hint, ok, ...}。"""
    s = get_settings()
    ag = AgentsClient()
    try:
        res = ag.invoke(
            "summary_from_chat",
            {
                "chat_id": chat_id,
                "message_text_aggregated": aggregate_text,
                "time_hint_text": time_hint,
                "default_window_end_unix": t0_unix,
            },
            trace_id=f"summary-{source_message_id}",
        )
    except Exception as e:  # noqa: BLE001
        log.exception("agents invoke: %s", e)
        res = {
            "ok": False,
            "error": {"code": "AGENTS_DOWN", "message": str(e)},
        }
    if not res.get("ok", True):
        title = f"摘要失败 {chat_id[:8]}"
        return {
            "ok": False,
            "title": title,
            "open_url": "about:blank#failed",
            "file_token": "",
            "chat_id": chat_id,
            "time_hint": time_hint,
            "source_message_id": source_message_id,
        }
    r = res.get("result", {}) or {}
    title = r.get("doc_title") or f"群聊总结 {chat_id[:8]}"
    xml = r.get("doc_content_xml") or f"<title>{title}</title><p>（无内容）</p>"
    open_url, file_token = _write_doc_or_dev(s, title, xml)
    tw: Tuple[Optional[int], Optional[int]] = (t0_unix - 86400, t0_unix)
    if (res.get("result") or {}).get("time_window"):
        tww = (res.get("result") or {}).get("time_window", {}) or {}
        st, en = tww.get("start_unix"), tww.get("end_unix")
        if st is not None and en is not None:
            tw = (st, en)
    return {
        "ok": True,
        "title": title,
        "open_url": open_url,
        "file_token": file_token,
        "chat_id": chat_id,
        "time_hint": time_hint,
        "source_message_id": source_message_id,
        "time_window_start_unix": tw[0],
        "time_window_end_unix": tw[1],
    }


def _write_doc_or_dev(settings, title: str, content_xml: str) -> tuple[str, str]:
    if settings.dev_skip_lark or not (settings.artifacts_drive_folder_token or "").strip():
        log.info("dev_skip: skip lark write for doc %s", title)
        q = urllib.parse.urlencode({"title": title[:40]})
        return (f"https://dev-placeholder.invalid/docx?{q}", "dev")
    folder = (settings.artifacts_drive_folder_token or "").strip()
    safe_xml = content_xml
    if "<" in safe_xml and len(safe_xml) > 500_000:
        safe_xml = safe_xml[:500_000] + "<p>（内容已截断）</p>"
    try:
        u, t = docx_create_in_folder_with_plain_text(settings, folder, title, safe_xml)
        if t:
            return (u, t)
        return (u or f"https://open.feishu.cn?title={urllib.parse.quote(title[:20])}", "")
    except Exception as e:  # noqa: BLE001
        log.warning("docx OpenAPI create: %s", e)
    return (f"https://open.feishu.cn?title={urllib.parse.quote(title[:20])}", "")
