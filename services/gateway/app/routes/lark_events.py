from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request

from ..lark_bot.im_at_bot import im_receive_should_reply_not_ready_dict, submit_not_ready_reply_chat
from ..lark_bot.im_summary_dispatch import try_parse_im_event_for_summary
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

    if not isinstance(ev, dict):
        return {"ok": "true"}

    not_ready, cid = im_receive_should_reply_not_ready_dict(ev)
    if not_ready:
        submit_not_ready_reply_chat(cid)
        return {"ok": "true"}

    params = try_parse_im_event_for_summary(ev)
    if params:
        background_tasks.add_task(
            run_summary_for_chat,
            params.chat_id,
            params.time_hint,
            params.t0_unix,
            params.text,
            params.message_id,
        )
    return {"ok": "true"}
