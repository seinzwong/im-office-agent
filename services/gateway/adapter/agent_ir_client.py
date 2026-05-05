from __future__ import annotations

import json
import os
import time
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from services.gateway.config import get_settings


DEFAULT_AGENT_TIMEOUT_SECONDS = 60.0
log = logging.getLogger(__name__)


def call_agent_generate_ir(packet: dict, options: dict | None = None) -> dict:
    """Call the external Agent service for Artifact IR generation.

    Gateway owns delivery and Adapter mapping; Agent only returns platform-neutral
    IR or IR Patch JSON. This client intentionally does not import Agent code.
    """
    started_at = time.perf_counter()
    options = options or {}
    settings = get_settings()
    base_url = str(
        options.get("agent_base_url")
        or options.get("agents_base_url")
        or settings.agents_base_url
        or ""
    ).strip()
    if not base_url:
        log.warning("agent_ir.call missing agents_base_url")
        return _stage_error(
            "AGENT_BASE_URL_MISSING",
            "agents_base_url is not configured.",
            {"hint": "Set AGENTS_BASE_URL or pass options.agent_base_url."},
            started_at,
        )

    timeout_seconds = float(options.get("agent_timeout_seconds") or DEFAULT_AGENT_TIMEOUT_SECONDS)
    token = str(options.get("agents_m2m_token") or settings.agents_m2m_token or "").strip()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    attempts: list[dict[str, Any]] = []
    preferred = str(options.get("agent_endpoint") or "protocol").strip().lower()
    endpoints = ["legacy"] if preferred == "legacy" else ["protocol", "legacy"]
    request_id = str(options.get("request_id") or uuid.uuid4())
    idempotency_key = str(options.get("idempotency_key") or request_id)
    trace_id = str(options.get("trace_id") or request_id)

    with httpx.Client(timeout=timeout_seconds) as client:
        for endpoint in endpoints:
            url, body = _build_request(base_url, endpoint, packet, request_id, idempotency_key, trace_id)
            request_file = _persist_agent_request(endpoint, url, body, request_id, trace_id)
            log.info(
                "agent_ir.call start endpoint=%s url=%s request_id=%s task_id=%s target=%s request_file=%s",
                endpoint,
                url,
                request_id,
                _task_id(packet),
                packet.get("target_artifact"),
                request_file,
            )
            try:
                response = client.post(url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                elapsed_ms = _elapsed_ms(started_at)
                log.warning(
                    "agent_ir.call error endpoint=%s request_id=%s elapsed_ms=%s error=%s",
                    endpoint,
                    request_id,
                    elapsed_ms,
                    exc,
                )
                error_attempt = {
                    "endpoint": endpoint,
                    "url": url,
                    "ok": False,
                    "request_file": str(request_file),
                    "error": f"{type(exc).__name__}: {exc}",
                }
                attempts.append(error_attempt)
                code = "AGENT_TIMEOUT" if isinstance(exc, httpx.TimeoutException) else "AGENT_REQUEST_FAILED"
                message = "Agent API timed out." if code == "AGENT_TIMEOUT" else "Agent API request failed."
                return _stage_error(
                    code,
                    message,
                    {
                        "attempts": attempts,
                        "timeout_seconds": timeout_seconds,
                        "hint": "Increase options.agent_timeout_seconds or enable Agent mock mode for local smoke tests.",
                    },
                    started_at,
                )

            attempts.append(
                {
                    "endpoint": endpoint,
                    "url": url,
                    "ok": 200 <= response.status_code < 300,
                    "status_code": response.status_code,
                    "request_file": str(request_file),
                }
            )
            log.info(
                "agent_ir.call response endpoint=%s request_id=%s status=%s elapsed_ms=%s",
                endpoint,
                request_id,
                response.status_code,
                _elapsed_ms(started_at),
            )
            if response.status_code in {404, 405} and endpoint == "protocol" and "legacy" in endpoints:
                log.warning(
                    "agent_ir.call protocol unavailable, fallback legacy request_id=%s status=%s",
                    request_id,
                    response.status_code,
                )
                continue
            if response.status_code >= 400:
                log.warning(
                    "agent_ir.call http_error endpoint=%s request_id=%s status=%s body=%s",
                    endpoint,
                    request_id,
                    response.status_code,
                    response.text[:500],
                )
                return _stage_error(
                    "AGENT_HTTP_ERROR",
                    "Agent API returned an HTTP error.",
                    {
                        "status_code": response.status_code,
                        "body": response.text[:4000],
                        "attempts": attempts,
                    },
                    started_at,
                )

            parsed = _parse_response_json(response)
            if parsed.get("ok") is False or isinstance(parsed.get("error"), dict):
                log.warning(
                    "agent_ir.call business_error endpoint=%s request_id=%s code=%s message=%s",
                    endpoint,
                    request_id,
                    parsed.get("error", {}).get("code"),
                    parsed.get("error", {}).get("message"),
                )
                return _stage_error(
                    parsed.get("error", {}).get("code") or "AGENT_BUSINESS_ERROR",
                    parsed.get("error", {}).get("message") or "Agent returned ok=false.",
                    {
                        "agent_error": parsed.get("error"),
                        "attempts": attempts,
                    },
                    started_at,
                )

            result = _unwrap_agent_result(parsed, endpoint)
            warnings = []
            if endpoint == "legacy" and preferred != "legacy":
                warnings.append("AGENT_PROTOCOL_FALLBACK: /v1/invoke was unavailable; used /agent/generate-ir-patch.")
            log.info(
                "agent_ir.call ok endpoint=%s request_id=%s elapsed_ms=%s",
                endpoint,
                request_id,
                _elapsed_ms(started_at),
            )
            return {
                "ok": True,
                "stage": "call_agent_generate_ir",
                "agent_output": result,
                "warnings": warnings,
                "debug": {
                    "endpoint": endpoint,
                    "attempts": attempts,
                    "elapsed_ms": _elapsed_ms(started_at),
                },
            }

    log.warning("agent_ir.call failed request_id=%s attempts=%s", request_id, attempts)
    return _stage_error(
        "AGENT_REQUEST_FAILED",
        "Agent API request failed.",
        {"attempts": attempts},
        started_at,
    )


def _build_request(
    base_url: str,
    endpoint: str,
    packet: dict,
    request_id: str,
    idempotency_key: str,
    trace_id: str,
) -> tuple[str, dict]:
    base = base_url.rstrip("/")
    if endpoint == "legacy":
        return f"{base}/agent/generate-ir-patch", packet
    return (
        f"{base}/v1/invoke",
        {
            "protocol_version": 1,
            "request_id": request_id,
            "idempotency_key": idempotency_key,
            "trace_id": trace_id,
            "action": "generate_artifact_ir",
            "context": {},
            "payload": packet,
        },
    )


def _persist_agent_request(endpoint: str, url: str, body: dict, request_id: str, trace_id: str) -> Path:
    output_dir = _agent_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"agent_request_{_safe_id(endpoint)}.json"
    record = {
        "kind": "agent_request",
        "endpoint": endpoint,
        "url": url,
        "request_id": request_id,
        "trace_id": trace_id,
        "saved_at": datetime.now().isoformat(timespec="microseconds"),
        "body": body,
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _agent_output_dir() -> Path:
    raw = os.environ.get("AGENT_OUTPUT_DIR") or "services/agent/output"
    path = Path(raw)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[3] / path
    return path.resolve()


def _parse_response_json(response: httpx.Response) -> dict:
    try:
        parsed = response.json()
    except Exception as exc:
        return {
            "ok": False,
            "error": {
                "code": "AGENT_INVALID_JSON",
                "message": "Agent response was not valid JSON.",
                "details": {"error": str(exc), "body": response.text[:1000]},
            },
        }
    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "error": {
                "code": "AGENT_INVALID_JSON",
                "message": "Agent response JSON must be an object.",
                "details": {"response_type": type(parsed).__name__},
            },
        }
    return parsed


def _unwrap_agent_result(parsed: dict, endpoint: str) -> dict:
    if endpoint == "protocol" and isinstance(parsed.get("result"), dict):
        return parsed["result"]
    if isinstance(parsed.get("result"), dict):
        return parsed["result"]
    return parsed


def _stage_error(code: str, message: str, details: Any | None, started_at: float) -> dict:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {
        "ok": False,
        "stage": "call_agent_generate_ir",
        "error": error,
        "warnings": [],
        "debug": {"elapsed_ms": _elapsed_ms(started_at)},
    }


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def _task_id(packet: dict) -> str:
    task = packet.get("task_brief")
    return str(task.get("task_id") if isinstance(task, dict) else "")


def _safe_id(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in value.strip())
    return safe.strip("_") or "request"


__all__ = ["call_agent_generate_ir"]
