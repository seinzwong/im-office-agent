from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request

from ..config import get_settings
from ..feishu_openapi import reply_text_to_message
from ..pipelines.summary_from_event import run_summary_command

log = logging.getLogger(__name__)
router = APIRouter(prefix="/lark", tags=["lark"])

DEFAULT_SUMMARY_MINUTES = 60
DEFAULT_SUMMARY_LIMIT = 200
MAX_SUMMARY_LIMIT = 200
UNBOUNDED_SUMMARY_TOKENS = {"all", "0", "*", "unlimited", "none"}


def _parse_content(content: Any) -> dict[str, Any]:
    if isinstance(content, str) and content.strip().startswith("{"):
        try:
            value = json.loads(content)
            return value if isinstance(value, dict) else {"text": str(value)}
        except Exception:  # noqa: BLE001
            return {"text": content}
    if isinstance(content, str):
        return {"text": content}
    return content if isinstance(content, dict) else {"text": str(content)}


def _message_text(msg: dict[str, Any]) -> str:
    content = _parse_content(msg.get("content", ""))
    return str(content.get("text", content) or "")


def _is_bot_mentioned(msg: dict[str, Any], text: str) -> bool:
    mentions = msg.get("mentions")
    if isinstance(mentions, list) and mentions:
        return True
    return text.strip().startswith("@")


def _strip_leading_mention(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("@"):
        return stripped
    parts = stripped.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def parse_summary_command(text: str) -> tuple[int | None, int]:
    parts = text.strip().split()
    minutes: int | None = DEFAULT_SUMMARY_MINUTES
    limit = DEFAULT_SUMMARY_LIMIT
    if len(parts) >= 2:
        minutes = _parse_summary_minutes(parts[1])
    if len(parts) >= 3:
        limit = _positive_int(parts[2], DEFAULT_SUMMARY_LIMIT)
    return minutes, min(limit, MAX_SUMMARY_LIMIT)


def _parse_summary_minutes(value: Any) -> int | None:
    token = str(value or "").strip().lower()
    if token in UNBOUNDED_SUMMARY_TOKENS:
        return None
    return _positive_int(value, DEFAULT_SUMMARY_MINUTES)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return parsed if parsed > 0 else default


def _sender_mention_id(ev: dict[str, Any]) -> str:
    sender = ev.get("sender") if isinstance(ev.get("sender"), dict) else {}
    sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}
    for key in ("open_id", "user_id", "union_id"):
        value = str(sender_id.get(key) or "").strip()
        if value:
            return value
    return ""


def _opening_text(user_id: str) -> str:
    prefix = f'<at user_id="{user_id}"></at>\n' if user_id else ""
    return (
        f"{prefix}"
        "你好，我是本群的办公协作机器人。\n"
        "• 需要整理群聊要点时，请 @我 并在同一条消息里发送 /summary\n"
        "• 发送 /help 查看可用指令"
    )


def _help_text(user_id: str) -> str:
    prefix = f'<at user_id="{user_id}"></at>\n' if user_id else ""
    return (
        f"{prefix}"
        "Commands:\n"
        "- /summary: summarize the last 60 minutes, up to 200 messages\n"
        "- /summary 30: summarize the last 30 minutes\n"
        "- /summary 30 100: summarize the last 30 minutes, up to 100 messages\n"
        "- /summary all 25: no time window, fetch up to 25 recent messages\n"
        "- /help: show available commands"
    )
def _unknown_command_text(user_id: str) -> str:
    prefix = f'<at user_id="{user_id}"></at>\n' if user_id else ""
    return f"{prefix}该功能还没开发好，请检查已有指令"


def _reply_later(message_id: str, text: str) -> None:
    try:
        reply_text_to_message(get_settings(), message_id, text)
    except Exception as exc:  # noqa: BLE001
        log.warning("lark reply failed: %s", exc)


@router.post("/events")
async def lark_events(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    raw: dict[str, Any] = await request.json()
    if "challenge" in raw:
        return {"challenge": raw.get("challenge", "")}

    ev = raw.get("event") or raw
    if isinstance(ev, str):
        try:
            ev = json.loads(ev)
        except Exception:  # noqa: BLE001
            log.warning("unparsed event: %s", ev[:200])
            return {"ok": "true"}

    msg = ev.get("message", ev) or {}
    if not isinstance(msg, dict):
        return {"ok": "true"}

    chat_id = (msg.get("chat_id") or (ev.get("chat_id") or "unknown"))[:64]
    mid = (msg.get("message_id") or "unknown")[:100]
    text = _message_text(msg)
    if not _is_bot_mentioned(msg, text):
        return {"ok": "true"}

    command_text = _strip_leading_mention(text)
    user_id = _sender_mention_id(ev)
    t0 = int(time.time())

    if not command_text:
        background_tasks.add_task(_reply_later, mid, _opening_text(user_id))
    elif command_text == "/help":
        background_tasks.add_task(_reply_later, mid, _help_text(user_id))
    elif _is_summary_command(command_text):
        minutes, limit = parse_summary_command(command_text)
        if chat_id and chat_id != "unknown":
            background_tasks.add_task(
                run_summary_command,
                chat_id,
                mid,
                user_id,
                t0,
                minutes,
                limit,
            )
    elif command_text.startswith("/"):
        background_tasks.add_task(_reply_later, mid, _unknown_command_text(user_id))

    return {"ok": "true"}


__all__ = [
    "parse_summary_command",
]


def _is_summary_command(text: str) -> bool:
    return text == "/summary" or text.startswith("/summary ")
