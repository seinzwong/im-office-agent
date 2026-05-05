from __future__ import annotations

import logging
from typing import Any, Tuple

from ..config import get_settings
from ..feishu_openapi import get_bot_open_id, im_send_text_to_chat
from .im_summary_dispatch import im_summary_executor

log = logging.getLogger(__name__)

NOT_READY_TEXT = "该功能还没开发好"


def dict_message_mentions_open_id(msg: dict[str, Any], open_id: str) -> bool:
    """HTTP 回调里 message 为 dict 时，判断是否包含对指定 open_id 的 @。"""
    if not open_id or not isinstance(msg, dict):
        return False
    for men in msg.get("mentions") or []:
        if not isinstance(men, dict):
            continue
        idobj = men.get("id")
        if isinstance(idobj, dict):
            oid = (idobj.get("open_id") or "").strip()
            if oid == open_id:
                return True
    return False


def sdk_message_mentions_open_id(message: Any, open_id: str) -> bool:
    """SDK EventMessage 上是否 @ 了指定 open_id。"""
    if not open_id or message is None:
        return False
    for men in getattr(message, "mentions", None) or []:
        uid = getattr(men, "id", None)
        oid = (getattr(uid, "open_id", None) or "").strip() if uid else ""
        if oid == open_id:
            return True
    return False


def im_receive_should_reply_not_ready_dict(ev: dict[str, Any]) -> Tuple[bool, str]:
    """若用户 @ 本机器人，返回 (True, chat_id)，否则 (False, '')。"""
    msg = ev.get("message", ev) or {}
    if not isinstance(msg, dict):
        msg = {}
    chat_id = (msg.get("chat_id") or (ev.get("chat_id") if isinstance(ev.get("chat_id"), str) else "") or "").strip()
    if not chat_id:
        return False, ""
    bot = get_bot_open_id(get_settings())
    if bot and dict_message_mentions_open_id(msg, bot):
        return True, chat_id
    return False, ""


def im_receive_should_reply_not_ready_sdk(message: Any) -> Tuple[bool, str]:
    """长连接 SDK 消息对象上是否 @ 本机器人。"""
    if message is None:
        return False, ""
    chat_id = (getattr(message, "chat_id", None) or "").strip()
    if not chat_id:
        return False, ""
    bot = get_bot_open_id(get_settings())
    if bot and sdk_message_mentions_open_id(message, bot):
        return True, chat_id
    return False, ""


def _send_not_ready_sync(chat_id: str) -> None:
    s = get_settings()
    if s.dev_skip_lark:
        return
    if not im_send_text_to_chat(s, chat_id, NOT_READY_TEXT):
        log.warning("im_at_bot: 发送占位回复失败 chat_id=%s...", chat_id[:16])


def submit_not_ready_reply_chat(chat_id: str) -> None:
    cid = (chat_id or "").strip()
    if not cid:
        return
    im_summary_executor().submit(_send_not_ready_sync, cid)
