from __future__ import annotations

import logging
from typing import Any

from ..config import get_settings
from ..feishu_openapi import im_send_interactive_markdown_to_chat, im_send_text_to_chat
from .im_summary_dispatch import im_summary_executor

log = logging.getLogger(__name__)


def _safe_display_name(name: str) -> str:
    if not name:
        return "新朋友"
    return name.replace("<", "").replace(">", "").strip()[:64] or "新朋友"


def _snapshot_user_added(data: Any) -> dict[str, Any] | None:
    ev = getattr(data, "event", None)
    if not ev:
        return None
    chat_id = (getattr(ev, "chat_id", None) or "").strip()
    if not chat_id:
        return None
    users: list[dict[str, str]] = []
    for u in getattr(ev, "users", None) or []:
        uid = getattr(u, "user_id", None)
        open_id = (getattr(uid, "open_id", None) or "").strip() if uid else ""
        user_id = (getattr(uid, "user_id", None) or "").strip() if uid else ""
        union_id = (getattr(uid, "union_id", None) or "").strip() if uid else ""
        at_id = open_id or user_id or union_id
        if not at_id:
            continue
        users.append({"at_id": at_id, "name": _safe_display_name((getattr(u, "name", None) or "").strip())})
    if not users:
        return None
    return {"chat_id": chat_id, "users": users}


def _run_welcome(snapshot: dict[str, Any]) -> None:
    s = get_settings()
    if s.dev_skip_lark:
        log.debug("join_welcome: dev_skip_lark=True，跳过发欢迎消息")
        return
    chat_id = snapshot["chat_id"]
    users: list[dict[str, str]] = snapshot["users"]
    at_block = "".join(f'<at id="{u["at_id"]}"></at>' for u in users)
    names_join = "、".join(u["name"] for u in users)
    md = (
        f"{at_block}\n\n"
        f"你好，{names_join}，欢迎加入本群！\n\n"
        "需要总结群内聊天时，请 **@本机器人** 并在同一条消息里带上 **/summary**，我会帮你整理摘要。"
    )
    if not im_send_interactive_markdown_to_chat(s, chat_id, md):
        plain = (
            f"你好，{names_join}，欢迎加入本群！"
            "需要总结聊天时请 @本机器人 并发送 /summary，我会帮你整理摘要。"
        )
        im_send_text_to_chat(s, chat_id, plain)


def submit_user_added_welcome(data: Any) -> None:
    try:
        snap = _snapshot_user_added(data)
        if not snap:
            return
        im_summary_executor().submit(_run_welcome, snap)
    except Exception:  # noqa: BLE001  # pylint: disable=broad-except
        log.exception("submit_user_added_welcome")
