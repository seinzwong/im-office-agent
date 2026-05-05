from __future__ import annotations

import time
from typing import Any

from services.agent.agents import generate_ir_from_messages
from services.agent.request_normalizer import normalize_agent_ir_request
from services.gateway.adapter import publish_ir
from services.gateway.adapter.ir_schema import ensure_ir_defaults, validate_ir
from services.gateway.app.config import get_settings

JsonDict = dict[str, Any]


def run_planb_e2e(raw_request: dict) -> dict:
    started_at = time.perf_counter()
    warnings: list[str] = []

    normalized = normalize_agent_ir_request(raw_request)
    warnings.extend(normalized.get("warnings") or [])
    if normalized.get("ok") is False:
        return _finish(
            "normalize_request",
            _error_from_result(normalized),
            warnings,
            started_at,
        )

    request = normalized["request"]
    _fill_publish_context_defaults(request.get("publish_context") or {}, warnings)

    agent_result = generate_ir_from_messages(request)
    warnings.extend(agent_result.get("warnings") or [])
    if agent_result.get("ok") is not True:
        return _finish(
            "generate_ir",
            _error_from_result(agent_result, default_code="AGENT_IR_FAILED"),
            warnings,
            started_at,
        )

    ir = ensure_ir_defaults(agent_result.get("ir") or {})
    validation = validate_ir(ir)
    if validation:
        return _finish(
            "validate_ir",
            _error("IR_VALIDATION_FAILED", "IR validation failed.", validation),
            warnings,
            started_at,
        )

    publish_context = request.get("publish_context") or {}
    options = request.get("options") or {}
    publish_result = publish_ir(ir, publish_context, options)
    warnings.extend(publish_result.get("warnings") or [])
    if publish_result.get("ok") is not True:
        return _finish(
            "publish_ir",
            _error_from_result(publish_result, default_code="PUBLISH_IR_FAILED"),
            warnings,
            started_at,
            extra={
                "request": _request_summary(request),
                "ir": ir,
                "publish_result": publish_result.get("publish_result") or {},
            },
        )

    return _finish(
        "done",
        {"ok": True},
        warnings,
        started_at,
        extra={
            "request": _request_summary(request),
            "ir": ir,
            "publish_result": publish_result.get("publish_result") or {},
        },
    )


def _fill_publish_context_defaults(publish_context: JsonDict, warnings: list[str]) -> None:
    if publish_context.get("folder_token"):
        return
    settings = get_settings()
    folder_token = (settings.artifacts_drive_folder_token or "").strip()
    if folder_token:
        publish_context["folder_token"] = folder_token
        warnings.append("Filled publish_context.folder_token from artifacts_drive_folder_token.")


def _request_summary(request: JsonDict) -> JsonDict:
    options = request.get("options") or {}
    return {
        "message_count": len(request.get("messages") or []),
        "target_outputs": options.get("target_outputs") or ["doc"],
        "dry_run": bool(options.get("dry_run", True)),
    }


def _finish(
    stage: str,
    payload: JsonDict,
    warnings: list[str],
    started_at: float,
    *,
    extra: JsonDict | None = None,
) -> JsonDict:
    output = {**payload, **(extra or {})}
    output["stage"] = stage
    output["warnings"] = _dedupe([*(output.get("warnings") or []), *warnings])
    output["elapsed_ms"] = round((time.perf_counter() - started_at) * 1000, 3)
    return output


def _error_from_result(result: JsonDict, default_code: str = "PLANB_FAILED") -> JsonDict:
    if isinstance(result.get("error"), dict):
        return {"ok": False, "error": result["error"], "warnings": result.get("warnings") or []}
    return _error(default_code, "PlanB stage failed.", result)


def _error(code: str, message: str, details: Any | None = None) -> JsonDict:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"ok": False, "error": error, "warnings": []}


def _dedupe(items: list[Any]) -> list[Any]:
    output = []
    seen = set()
    for item in items:
        marker = repr(item)
        if marker not in seen:
            seen.add(marker)
            output.append(item)
    return output


__all__ = ["run_planb_e2e"]
