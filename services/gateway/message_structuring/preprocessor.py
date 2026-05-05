from __future__ import annotations

import json
import re

from .schemas import (
    DedupInfo,
    MentionInfo,
    MessageContent,
    MessageFeatures,
    NormalizedMessage,
    SenderInfo,
)


_URL_RE = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _parse_mentions(raw_mentions: list[dict] | None) -> list[MentionInfo]:
    mentions: list[MentionInfo] = []
    for item in raw_mentions or []:
        mention_id = item.get("id") or {}
        mentions.append(
            MentionInfo(
                key=item.get("key"),
                name=item.get("name"),
                mentioned_type=item.get("mentioned_type"),
                union_id=mention_id.get("union_id"),
                user_id=mention_id.get("user_id"),
                open_id=mention_id.get("open_id"),
            )
        )
    return mentions


def _replace_mentions(text: str, mentions: list[MentionInfo]) -> str:
    output = text
    for mention in mentions:
        if mention.key and mention.name:
            output = output.replace(mention.key, f"@{mention.name}")
    return output


def _plain_text_without_mentions(text: str, mentions: list[MentionInfo]) -> str:
    output = text
    for mention in mentions:
        if mention.key:
            output = output.replace(mention.key, "")
        elif mention.name:
            output = output.replace(f"@{mention.name}", "")
    output = re.sub(r"@\S+", "", output)
    return re.sub(r"\s+", " ", output).strip()


def _extract_post_text(content_obj: dict) -> str:
    title = content_obj.get("title", "")
    lines = []
    for row in content_obj.get("content", []) or []:
        for block in row or []:
            if block.get("tag") == "text":
                lines.append(block.get("text", ""))
    pieces = [piece for piece in [title.strip(), " ".join(lines).strip()] if piece]
    return " ".join(pieces).strip()


def parse_feishu_event(raw_event: dict, task_id: str | None = None) -> NormalizedMessage:
    event = raw_event.get("event", {}) or {}
    header = raw_event.get("header", {}) or {}
    message = event.get("message", {}) or {}
    sender = event.get("sender", {}) or {}
    sender_id = sender.get("sender_id", {}) or {}

    mentions = _parse_mentions(message.get("mentions"))
    raw_content = message.get("content", "")
    message_type = (message.get("message_type") or "unknown").lower()

    parse_status = "ok"
    normalized_text = ""
    plain_text = ""
    has_image = False

    try:
        content_obj = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
        if not isinstance(content_obj, dict):
            raise ValueError("content is not dict")
        if message_type == "text":
            normalized_text = str(content_obj.get("text", "")).strip()
        elif message_type == "post":
            normalized_text = _extract_post_text(content_obj)
        elif message_type == "image":
            normalized_text = "[鍥剧墖]"
            has_image = True
        else:
            normalized_text = json.dumps(content_obj, ensure_ascii=False)
    except Exception:
        parse_status = "error"
        if message_type == "image":
            normalized_text = "[鍥剧墖]"
            has_image = True
        elif isinstance(raw_content, str):
            normalized_text = raw_content
        else:
            normalized_text = json.dumps(raw_content, ensure_ascii=False)

    normalized_text = _replace_mentions(normalized_text, mentions).strip()
    plain_text = _plain_text_without_mentions(normalized_text, mentions)

    has_mention = len(mentions) > 0 or "@all" in normalized_text.lower()
    at_all = "@all" in normalized_text.lower() or "everyone" in normalized_text.lower()
    has_url = bool(_URL_RE.search(normalized_text))
    has_file = message_type == "file" or ("附件" in normalized_text) or ("file" in normalized_text.lower())
    has_email = bool(_EMAIL_RE.search(normalized_text))
    has_file = has_file or has_email

    timestamp_ms = _safe_int(message.get("create_time"), default=_safe_int(header.get("create_time")))
    update_time_ms = _safe_int(message.get("update_time"), default=timestamp_ms)
    message_id = message.get("message_id") or f"unknown_{timestamp_ms}"
    dedup_key = f"feishu:{message_id}:{update_time_ms}"

    return NormalizedMessage(
        message_id=message_id,
        event_id=header.get("event_id"),
        task_id=task_id or "task_unassigned",
        chat_id=message.get("chat_id") or "",
        thread_id=message.get("thread_id"),
        root_id=message.get("root_id"),
        parent_id=message.get("parent_id"),
        chat_type=message.get("chat_type") or "group",
        source_platform="feishu",
        message_type=message_type,
        timestamp_ms=timestamp_ms,
        update_time_ms=update_time_ms,
        sender=SenderInfo(
            sender_type=sender.get("sender_type") or "user",
            union_id=sender_id.get("union_id"),
            user_id=sender_id.get("user_id"),
            open_id=sender_id.get("open_id"),
            display_name=None,
        ),
        content=MessageContent(
            normalized_text=normalized_text,
            plain_text=plain_text,
            raw_content=raw_content if isinstance(raw_content, str) else json.dumps(raw_content, ensure_ascii=False),
            content_parse_status=parse_status,
        ),
        mentions=mentions,
        features=MessageFeatures(
            has_url=has_url,
            has_file=has_file,
            has_image=has_image or message_type == "image",
            has_mention=has_mention,
            at_all=at_all,
            text_length=len(plain_text or normalized_text),
        ),
        dedup=DedupInfo(dedup_key=dedup_key, is_duplicate=False),
    )
