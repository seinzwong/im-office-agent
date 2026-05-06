from __future__ import annotations

import json
import time
from datetime import date
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
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    if not messages:
        text = str(payload.get("message_text_aggregated") or "").strip()
        messages = [
            {
                "message_id": "aggregated",
                "sender": chat_id,
                "timestamp": str(payload.get("default_window_end_unix") or int(time.time())),
                "text": text or "未获取到可汇总的群聊文本。",
            }
        ]
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    request = {
        "task": {
            "task_id": str(task.get("task_id") or f"summary_{chat_id[:8]}"),
            "title": str(task.get("title") or "群聊目标与方案总结"),
            "goal": str(
                task.get("goal")
                or "整理群聊中的主要目标以及产出的方案，提炼共识、问题、行动路径、风险与下一步。"
            ),
            "audience": str(task.get("audience") or "群聊成员"),
            "deliverables": ["doc"],
        },
        "messages": messages,
        "options": {"language": "zh-CN", "target_outputs": ["doc"], "dry_run": False},
    }
    result = generate_ir_from_messages(request)
    if result.get("ok") is True and isinstance(result.get("ir"), dict):
        return {
            "time_window": payload.get("time_window") or {},
            "ir": result["ir"],
            "warnings": result.get("warnings") or [],
        }
    return {
        "time_window": payload.get("time_window") or {},
        "ir": _fallback_summary_ir(request, chat_id),
        "warnings": [*(result.get("warnings") or []), "Fell back to deterministic summary IR."],
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


def _fallback_summary_ir(request: dict[str, Any], chat_id: str) -> dict[str, Any]:
    task = request.get("task") if isinstance(request.get("task"), dict) else {}
    messages = request.get("messages") if isinstance(request.get("messages"), list) else []
    joined = "\n".join(str(item.get("text") or "") for item in messages if isinstance(item, dict)).strip()
    body = joined[:1200] or "未获取到可汇总的群聊文本。"
    title = str(task.get("title") or "群聊目标与方案总结")
    return {
        "schemaVersion": "0.2.0",
        "docId": f"summary_{chat_id[:8]}",
        "meta": {
            "title": title,
            "subtitle": str(task.get("goal") or ""),
            "owner": "Agent",
            "date": date.today().isoformat(),
            "audience": str(task.get("audience") or "群聊成员"),
        },
        "theme": {},
        "assets": {},
        "blocks": [
            {
                "id": "cover",
                "kind": "cover",
                "title": title,
                "subtitle": str(task.get("goal") or ""),
            },
            {
                "id": "main_goal",
                "kind": "split",
                "title": "主要目标与上下文",
                "points": [body],
            },
            {
                "id": "next_steps",
                "kind": "cards",
                "title": "待确认的产出方案",
                "cards": [
                    {
                        "title": "补充确认",
                        "body": "自动生成服务暂时不可用，已先保留可读群聊内容，建议稍后重新运行 /summary。",
                    }
                ],
            },
        ],
    }


def _json_response(payload: dict[str, Any]) -> Response:
    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        media_type="application/json; charset=utf-8",
    )


__all__ = ["app"]
