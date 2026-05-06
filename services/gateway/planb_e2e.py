from __future__ import annotations

import time
import logging
from typing import Any

from services.agent.agents import (
    generate_board_ir_from_content_ir,
    generate_content_ir_from_messages,
    generate_slide_draft_from_content_ir,
)
from services.agent.request_normalizer import normalize_agent_ir_request
from services.gateway.adapter import publish_ir
from services.gateway.adapter.ir_schema import ensure_ir_defaults
from services.gateway.app.config import get_settings

JsonDict = dict[str, Any]
log = logging.getLogger(__name__)


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
    publish_context = request.get("publish_context") or {}
    options = dict(request.get("options") or {})
    targets = _target_outputs(options)
    content_ir = None
    board_ir = None
    slide_draft = None
    ir = _minimal_ir_from_request(request)

    if isinstance(options.get("content_ir"), dict):
        content_ir = options["content_ir"]
        warnings.append("Reused ContentIR from sidecar store.")
    else:
        log.info("planb e2e content_ir started targets=%s", targets)
        content_result = generate_content_ir_from_messages(request)
        warnings.extend(content_result.get("warnings") or [])
        if content_result.get("ok") is not True:
            return _finish(
                "generate_content_ir",
                _error_from_result(content_result, default_code="CONTENT_IR_FAILED"),
                warnings,
                started_at,
            )
        content_ir = content_result.get("content_ir") or {}
    if "ppt" in targets:
        log.info("planb e2e slide_draft started")
        slide_result = generate_slide_draft_from_content_ir(content_ir, options.get("ppt") or {})
        warnings.extend(slide_result.get("warnings") or [])
        if slide_result.get("ok") is not True:
            return _finish(
                "generate_slide_draft",
                _error_from_result(slide_result, default_code="SLIDE_DRAFT_FAILED"),
                warnings,
                started_at,
                extra={
                    "request": _request_summary(request),
                    "ir": ir,
                    "content_ir": content_ir,
                    "slide_draft": None,
                    "publish_result": {},
                },
            )
        slide_draft = slide_result.get("slide_draft") or {}
        options["slide_draft"] = slide_draft
    if "board" in targets:
        if isinstance(options.get("board_ir"), dict):
            board_ir = options["board_ir"]
            warnings.append("Reused BoardIR from request options.")
        else:
            log.info("planb e2e board_ir started")
            board_result = generate_board_ir_from_content_ir(content_ir, options.get("board") or {})
            warnings.extend(board_result.get("warnings") or [])
            if board_result.get("ok") is not True:
                return _finish(
                    "generate_board_ir",
                    _error_from_result(board_result, default_code="BOARD_IR_FAILED"),
                    warnings,
                    started_at,
                    extra={
                        "request": _request_summary(request),
                        "ir": ir,
                        "content_ir": content_ir,
                        "board_ir": None,
                        "slide_draft": slide_draft,
                        "publish_result": {},
                    },
                )
            board_ir = board_result.get("board_ir") or {}
        options["board_ir"] = board_ir
    log.info("planb e2e publish_ir started targets=%s", targets)
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
                "content_ir": content_ir,
                "board_ir": board_ir,
                "slide_draft": slide_draft,
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
            "content_ir": content_ir,
            "board_ir": board_ir,
            "slide_draft": slide_draft,
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


def _minimal_ir_from_request(request: JsonDict) -> JsonDict:
    task = request.get("task") if isinstance(request.get("task"), dict) else {}
    title = str(task.get("title") or task.get("goal") or "Generated Presentation").strip()
    subtitle = str(task.get("goal") or "").strip()
    return ensure_ir_defaults(
        {
            "schemaVersion": "0.2.0",
            "docId": f"{str(task.get('task_id') or 'task')}_ppt_ir",
            "meta": {
                "title": title,
                "subtitle": subtitle,
                "owner": "Agent",
                "audience": str(task.get("audience") or ""),
            },
            "theme": {},
            "assets": {},
            "blocks": [
                {
                    "id": "cover",
                    "kind": "cover",
                    "title": title,
                    "subtitle": subtitle,
                }
            ],
        }
    )


def _target_outputs(options: JsonDict) -> list[str]:
    targets = options.get("target_outputs") or ["doc"]
    if isinstance(targets, str):
        targets = [item.strip() for item in targets.split(",") if item.strip()]
    if not isinstance(targets, list):
        return ["doc"]
    if "all" in targets:
        return ["doc", "board", "ppt"]
    return [str(target).strip() for target in targets if str(target).strip()]


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
