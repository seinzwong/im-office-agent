from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import FastAPI
from fastapi.responses import Response

from services.agent.agents import (
    generate_artifact_ir_patch,
    update_structuring_summary,
)

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="IM Office Agent Service", version="0.1.0")
log = logging.getLogger(__name__)


@app.get("/healthz")
def healthz() -> Response:
    """Return a minimal health response for service probes."""
    return _json_response({"ok": "true"})


@app.post("/agent/update-structuring-summary")
def update_structuring_summary_endpoint(payload: dict[str, Any]) -> Response:
    """HTTP wrapper for structured topic/task summary updates."""
    return _json_response(update_structuring_summary(payload))


@app.post("/agent/generate-ir-patch")
def generate_ir_patch_endpoint(payload: dict[str, Any]) -> Response:
    """HTTP wrapper for Artifact IR patch generation."""
    started_at = time.perf_counter()
    log.info(
        "agent.generate_ir_patch start task_id=%s target=%s",
        _task_id(payload),
        payload.get("target_artifact"),
    )
    result = generate_artifact_ir_patch(payload)
    log.info(
        "agent.generate_ir_patch done task_id=%s has_error=%s elapsed_ms=%s",
        _task_id(payload),
        isinstance(result.get("error") if isinstance(result, dict) else None, dict),
        _elapsed_ms(started_at),
    )
    return _json_response(result)


@app.post("/v1/invoke")
def invoke_endpoint(envelope: dict[str, Any]) -> Response:
    """Protocol-compatible wrapper used by Gateway.

    This keeps the Agent service IR-only while allowing Gateway to call the
    stable multi-agent protocol endpoint.
    """
    started_at = time.perf_counter()
    request_id = str(envelope.get("request_id") or "")
    action = str(envelope.get("action") or "")
    log.info("agent.invoke start request_id=%s action=%s", request_id, action)
    if action != "generate_artifact_ir":
        log.warning("agent.invoke unsupported_action request_id=%s action=%s", request_id, action)
        return _json_response(
            {
                "protocol_version": 1,
                "request_id": request_id,
                "ok": False,
                "error": {
                    "code": "UNSUPPORTED_ACTION",
                    "message": "action must be generate_artifact_ir",
                    "details": {"action": action},
                },
            }
        )

    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        log.warning("agent.invoke invalid_payload request_id=%s payload_type=%s", request_id, type(payload).__name__)
        return _json_response(
            {
                "protocol_version": 1,
                "request_id": request_id,
                "ok": False,
                "error": {
                    "code": "PAYLOAD_INVALID",
                    "message": "payload must be an object",
                },
            }
        )

    log.info(
        "agent.invoke generate_artifact_ir request_id=%s task_id=%s target=%s",
        request_id,
        _task_id(payload),
        payload.get("target_artifact"),
    )
    result = generate_artifact_ir_patch(payload)
    if isinstance(result, dict) and isinstance(result.get("error"), dict):
        log.warning(
            "agent.invoke error request_id=%s code=%s elapsed_ms=%s",
            request_id,
            result["error"].get("code"),
            _elapsed_ms(started_at),
        )
        return _json_response(
            {
                "protocol_version": 1,
                "request_id": request_id,
                "ok": False,
                "error": result["error"],
                "result": result,
            }
        )
    log.info(
        "agent.invoke ok request_id=%s artifact_type=%s elapsed_ms=%s",
        request_id,
        result.get("artifact_type") if isinstance(result, dict) else None,
        _elapsed_ms(started_at),
    )
    return _json_response(
        {
            "protocol_version": 1,
            "request_id": request_id,
            "ok": True,
            "result": result,
        }
    )


def _json_response(payload: dict[str, Any]) -> Response:
    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        media_type="application/json; charset=utf-8",
    )


def _task_id(payload: dict[str, Any]) -> str:
    task = payload.get("task_brief")
    return str(task.get("task_id") if isinstance(task, dict) else "")


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


__all__ = ["app"]
