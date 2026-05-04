from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request

from ..pipelines.summary_from_event import run_summary_for_chat

log = logging.getLogger(__name__)
router = APIRouter(prefix="/lark", tags=["lark"])


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
    content = msg.get("content", "")
    cj: dict[str, Any]
    if isinstance(content, str) and content.strip().startswith("{"):
        try:
            cj = json.loads(content)
        except Exception:  # noqa: BLE001
            cj = {"text": content}
    elif isinstance(content, str):
        cj = {"text": content}
    else:
        cj = content if isinstance(content, dict) else {"text": str(content)}
    text = str(cj.get("text", cj) or "")
    hint = text[:200] if not text.strip().startswith("@") else " ".join(text.split()[1:200])[:200]
    t0 = int(time.time())
    if chat_id and chat_id != "unknown":
        background_tasks.add_task(
            run_summary_for_chat,
            chat_id,
            hint,
            t0,
            text or "（无文本）",
            mid,
        )
    return {"ok": "true"}
