from __future__ import annotations

import json
import time
from typing import Any

from fastapi import Header, HTTPException
from fastapi import FastAPI
from fastapi.responses import Response

from services.agent.agents import generate_ir_from_messages

app = FastAPI(title="IM Office PlanB Agent", version="0.2.0")


@app.get("/healthz")
def healthz() -> Response:
    return _json_response({"ok": True})


@app.post("/agent/generate-ir-from-messages")
def generate_ir_from_messages_endpoint(payload: dict[str, Any]) -> Response:
    return _json_response(generate_ir_from_messages(payload))


@app.post("/v1/invoke")
def invoke_endpoint(
    envelope: dict[str, Any],
    authorization: str = Header(default=""),
) -> Response:
    # The local dev token matches gateway.yaml by default. Keep this small
    # adapter compatible with the Gateway invoke protocol.
    if authorization and authorization != "Bearer dev-m2m-secret":
        raise HTTPException(status_code=401, detail="invalid agent token")

    action = str(envelope.get("action") or "")
    payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
    request_id = str(envelope.get("request_id") or "")

    if action == "summary_from_chat":
        result = _summary_from_chat(payload)
    elif action == "deliver_whiteboard":
        result = {"whiteboard_dsl": "board: placeholder\nnodes:\n  - summary", "notes": "local dev adapter"}
    elif action == "deliver_slides":
        result = {"slide_xml_slides": "<slides><slide><title>Summary</title></slide></slides>", "notes": "local dev adapter"}
    else:
        return _json_response(
            {
                "protocol_version": 1,
                "request_id": request_id,
                "ok": False,
                "error": {"code": "UNKNOWN_ACTION", "message": f"Unknown action: {action}"},
            }
        )

    return _json_response(
        {
            "protocol_version": 1,
            "request_id": request_id,
            "ok": True,
            "result": result,
        }
    )


def _summary_from_chat(payload: dict[str, Any]) -> dict[str, Any]:
    chat_id = str(payload.get("chat_id") or "chat")
    text = str(payload.get("message_text_aggregated") or "").strip()
    time_hint = str(payload.get("time_hint_text") or "").strip()
    end_unix = _as_int(payload.get("default_window_end_unix"), int(time.time()))
    title = f"群聊总结 {chat_id[:8]}"
    body = text or "未获取到可汇总的群聊文本。"
    if time_hint:
        body = f"时间范围：{time_hint}\n\n{body}"
    return {
        "time_window": {
            "start_unix": end_unix - 86400,
            "end_unix": end_unix,
            "label": time_hint or "最近 24 小时",
        },
        "doc_title": title,
        "doc_content_xml": f"<title>{_escape_xml(title)}</title><p>{_escape_xml(body)}</p>",
    }


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _escape_xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _json_response(payload: dict[str, Any]) -> Response:
    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        media_type="application/json; charset=utf-8",
    )


__all__ = ["app"]
