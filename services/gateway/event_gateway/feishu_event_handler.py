from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from services.gateway.structuring_router import get_structuring_orchestrator

router = APIRouter(tags=["lark-events"])


@router.post("/lark/events")
def handle_lark_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle Feishu/Lark callback verification and message events.

    The target branch already exposed this route in README/router wiring, but
    the implementation file was missing. This minimal handler keeps challenge
    verification working and forwards message payloads into the structuring
    layer when an active task exists for the chat.
    """
    challenge = payload.get("challenge")
    if challenge:
        return {"challenge": challenge}

    if payload.get("type") == "url_verification" and payload.get("challenge"):
        return {"challenge": payload["challenge"]}

    return get_structuring_orchestrator().process_feishu_event(payload)


__all__ = ["router"]
