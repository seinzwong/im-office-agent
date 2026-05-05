from __future__ import annotations

import logging
import time
from copy import deepcopy
from typing import Any

from .agent_ir_client import call_agent_generate_ir as _call_agent_generate_ir_http
from .artifact_publisher import publish_ir
from .ir_normalizer import normalize_agent_ir_output as _normalize_agent_ir_output
from .ir_schema import ensure_ir_defaults, validate_ir
from .markdown_adapter import ir_to_markdown
from .feishu_doc_adapter import ir_to_feishu_doc_blocks
from .feishu_board_adapter import ir_to_feishu_board_draft
from .ppt_adapter import ir_to_ppt_draft


log = logging.getLogger(__name__)


def normalize_backend_packet(raw: dict) -> dict:
    """Normalize Gateway structuring output into the Agent IR payload shape."""
    started_at = time.perf_counter()
    log.info("e2e_dry_run.stage start stage=normalize_backend_packet")
    warnings: list[str] = []
    if not isinstance(raw, dict):
        log.warning("e2e_dry_run.stage failed stage=normalize_backend_packet code=INVALID_BACKEND_PACKET")
        return _stage_error(
            "normalize_backend_packet",
            "INVALID_BACKEND_PACKET",
            "Backend packet must be an object.",
            {"type": type(raw).__name__},
            started_at,
        )

    source = _unwrap_packet(raw)
    task = _as_dict(source.get("task"))
    task_brief = _as_dict(source.get("task_brief"))
    quality = _as_dict(source.get("quality"))
    options = _as_dict(raw.get("options"))

    normalized_task = _normalize_task_brief(task, task_brief, options)
    topic_tree = _normalize_topics(source, warnings)
    evidence_set = _normalize_evidence(source, warnings)
    if not topic_tree:
        warnings.append("BACKEND_TOPICS_EMPTY: no topics found; Agent will build IR from task brief and evidence only.")
    if not evidence_set:
        warnings.append("BACKEND_EVIDENCE_EMPTY: no summary evidence found; Agent may produce a sparse IR.")
    for error in _as_list(quality.get("errors")):
        if error:
            warnings.append(f"BACKEND_QUALITY_ERROR: {error}")

    target_artifact = str(
        options.get("target_artifact")
        or raw.get("target_artifact")
        or source.get("target_artifact")
        or "all"
    ).strip()
    if target_artifact not in {"all", "doc", "board", "ppt"}:
        warnings.append(f"INVALID_TARGET_ARTIFACT: {target_artifact}; using all.")
        target_artifact = "all"

    packet_options = {
        key: value
        for key, value in {
            **_as_dict(source.get("options")),
            **options,
            "audience": options.get("audience") or _infer_audience(source),
            "dry_run": options.get("dry_run", True),
            "targets": options.get("targets") or ["doc", "board", "ppt"],
        }.items()
        if value is not None
    }

    normalized = {
        "task_brief": normalized_task,
        "topic_tree": topic_tree,
        "evidence_set": evidence_set,
        "target_artifact": target_artifact,
        "current_ir": raw.get("current_ir") if isinstance(raw.get("current_ir"), dict) else None,
        "options": packet_options,
        "source_packet_type": _source_packet_type(source),
    }
    log.info(
        "e2e_dry_run.stage ok stage=normalize_backend_packet task_id=%s topics=%s evidence=%s warnings=%s elapsed_ms=%s",
        normalized_task.get("task_id"),
        len(topic_tree),
        len(evidence_set),
        len(warnings),
        _elapsed_ms(started_at),
    )
    return {
        "ok": True,
        "stage": "normalize_backend_packet",
        "packet": normalized,
        "warnings": _dedupe(warnings),
        "debug": {"elapsed_ms": _elapsed_ms(started_at)},
    }


def call_agent_generate_ir(packet: dict) -> dict:
    options = _as_dict(packet.get("options"))
    task = _as_dict(packet.get("task_brief"))
    log.info(
        "e2e_dry_run.stage start stage=call_agent_generate_ir task_id=%s target=%s",
        task.get("task_id"),
        packet.get("target_artifact"),
    )
    return _call_agent_generate_ir_http(packet, options)


def normalize_agent_ir_output(agent_output: dict, current_ir: dict | None = None) -> dict:
    return _normalize_agent_ir_output(agent_output, current_ir)


def run_e2e_dry_run(raw_backend_packet: dict, options: dict | None = None) -> dict:
    started_at = time.perf_counter()
    options = options or {}
    log.info("e2e_dry_run start targets=%s dry_run=%s", options.get("targets"), options.get("dry_run", True))
    raw = deepcopy(raw_backend_packet) if isinstance(raw_backend_packet, dict) else raw_backend_packet
    if isinstance(raw, dict):
        raw["options"] = {**_as_dict(raw.get("options")), **options, "dry_run": True}
    warnings: list[str] = []
    stages: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "ok": False,
        "stages": stages,
        "normalized_backend_packet": {},
        "agent_output": {},
        "normalized_ir": {},
        "markdown_preview": "",
        "doc_blocks_preview": [],
        "board_draft": {},
        "ppt_draft": {},
        "warnings": warnings,
    }

    normalized_packet_result = normalize_backend_packet(raw)
    stages.append(_stage_record(normalized_packet_result))
    warnings.extend(normalized_packet_result.get("warnings") or [])
    if normalized_packet_result.get("ok") is not True:
        return _finish_failure(result, normalized_packet_result, warnings, started_at)

    packet = normalized_packet_result["packet"]
    result["normalized_backend_packet"] = packet

    agent_result = call_agent_generate_ir(packet)
    stages.append(_stage_record(agent_result))
    warnings.extend(agent_result.get("warnings") or [])
    if agent_result.get("ok") is not True:
        log.warning("e2e_dry_run.stage failed stage=call_agent_generate_ir error=%s", agent_result.get("error"))
        return _finish_failure(result, agent_result, warnings, started_at)
    log.info("e2e_dry_run.stage ok stage=call_agent_generate_ir")

    agent_output = _as_dict(agent_result.get("agent_output"))
    result["agent_output"] = agent_output

    normalize_started_at = time.perf_counter()
    log.info("e2e_dry_run.stage start stage=normalize_agent_ir_output")
    normalized_ir = normalize_agent_ir_output(agent_output, packet.get("current_ir"))
    if isinstance(normalized_ir, dict) and normalized_ir.get("ok") is False:
        stage_result = {
            "ok": False,
            "stage": "normalize_agent_ir_output",
            "error": normalized_ir.get("error") or {
                "code": "INVALID_AGENT_OUTPUT",
                "message": "Agent output could not be normalized.",
            },
            "warnings": normalized_ir.get("warnings") or [],
            "debug": {"elapsed_ms": _elapsed_ms(normalize_started_at)},
        }
        stages.append(_stage_record(stage_result))
        warnings.extend(stage_result.get("warnings") or [])
        log.warning("e2e_dry_run.stage failed stage=normalize_agent_ir_output error=%s", stage_result.get("error"))
        return _finish_failure(result, stage_result, warnings, started_at)
    normalized_ir = ensure_ir_defaults(normalized_ir)
    result["normalized_ir"] = normalized_ir
    log.info(
        "e2e_dry_run.stage ok stage=normalize_agent_ir_output blocks=%s elapsed_ms=%s",
        len(_as_list(normalized_ir.get("blocks"))),
        _elapsed_ms(normalize_started_at),
    )
    stages.append(
        {
            "stage": "normalize_agent_ir_output",
            "ok": True,
            "elapsed_ms": _elapsed_ms(normalize_started_at),
        }
    )

    validate_started_at = time.perf_counter()
    log.info("e2e_dry_run.stage start stage=validate_ir")
    validation = validate_ir(normalized_ir)
    validation_stage = {
        "stage": "validate_ir",
        "ok": not validation,
        "elapsed_ms": _elapsed_ms(validate_started_at),
    }
    if validation:
        validation_stage["error"] = {
            "code": "IR_VALIDATION_FAILED",
            "message": "IR validation failed.",
            "details": validation,
        }
        stages.append(validation_stage)
        log.warning("e2e_dry_run.stage failed stage=validate_ir errors=%s", validation)
        return _finish_failure(result, validation_stage, warnings, started_at)
    stages.append(validation_stage)
    log.info("e2e_dry_run.stage ok stage=validate_ir elapsed_ms=%s", _elapsed_ms(validate_started_at))

    preview_started_at = time.perf_counter()
    log.info("e2e_dry_run.stage start stage=publish_ir_dry_run")
    publish_options = {
        **_as_dict(options),
        **_as_dict(packet.get("options")),
        "dry_run": True,
        "targets": options.get("targets") or packet.get("options", {}).get("targets") or ["doc", "board", "ppt"],
    }
    publish_result = publish_ir(normalized_ir, publish_options)
    publish_stage = {
        "stage": "publish_ir_dry_run",
        "ok": publish_result.get("ok") is True,
        "elapsed_ms": _elapsed_ms(preview_started_at),
    }
    if publish_result.get("ok") is not True:
        publish_stage["error"] = publish_result.get("error") or {
            "code": "PUBLISH_IR_FAILED",
            "message": "Adapter dry_run publish failed.",
            "details": publish_result,
        }
        stages.append(publish_stage)
        warnings.extend(publish_result.get("warnings") or [])
        log.warning("e2e_dry_run.stage failed stage=publish_ir_dry_run error=%s", publish_stage.get("error"))
        return _finish_failure(result, publish_stage, warnings, started_at)
    stages.append(publish_stage)
    log.info("e2e_dry_run.stage ok stage=publish_ir_dry_run elapsed_ms=%s", _elapsed_ms(preview_started_at))

    warnings.extend(publish_result.get("warnings") or [])
    log.info("e2e_dry_run ok elapsed_ms=%s warnings=%s", _elapsed_ms(started_at), len(warnings))
    result.update(
        {
            "ok": True,
            "markdown_preview": publish_result.get("markdown_preview") or ir_to_markdown(normalized_ir),
            "doc_blocks_preview": publish_result.get("doc_blocks_preview") or ir_to_feishu_doc_blocks(normalized_ir),
            "board_draft": publish_result.get("board_draft") or ir_to_feishu_board_draft(normalized_ir),
            "ppt_draft": publish_result.get("ppt_draft") or ir_to_ppt_draft(normalized_ir),
            "publish_result": publish_result,
            "warnings": _dedupe(warnings),
            "debug": {"elapsed_ms": _elapsed_ms(started_at)},
        }
    )
    return result


def _normalize_task_brief(task: dict, task_brief: dict, options: dict) -> dict:
    task_id = str(task.get("task_id") or task_brief.get("task_id") or options.get("task_id") or "task").strip()
    title = str(
        task.get("task_title")
        or task.get("title")
        or task.get("display_name")
        or task_brief.get("title")
        or task_brief.get("goal")
        or "Generated Artifact"
    ).strip()
    return {
        "task_id": task_id,
        "title": title,
        "summary": str(task_brief.get("summary") or task.get("summary") or "").strip(),
        "goal": str(task_brief.get("goal") or task.get("goal") or title).strip(),
        "deliverables": [str(item) for item in _as_list(task_brief.get("deliverables") or task.get("deliverables")) if str(item).strip()],
        "deadline": task_brief.get("deadline") or task.get("deadline"),
        "status": task.get("status") or task_brief.get("status") or "collecting",
        "confidence": task_brief.get("confidence") if task_brief.get("confidence") is not None else 0.0,
    }


def _normalize_topics(source: dict, warnings: list[str]) -> list[dict]:
    topics = _as_list(source.get("topic_tree")) or _as_list(source.get("topics"))
    normalized: list[dict] = []
    for index, topic in enumerate(topics):
        if not isinstance(topic, dict):
            warnings.append(f"BACKEND_TOPIC_SKIPPED: topics[{index}] is not an object.")
            continue
        topic_id = str(topic.get("topic_id") or topic.get("id") or f"topic_{index + 1}").strip()
        title = str(topic.get("title") or topic.get("topic_title") or topic.get("type") or topic_id).strip()
        refs = []
        for ref in _as_list(topic.get("refs") or topic.get("message_refs")):
            if isinstance(ref, dict):
                refs.append(
                    {
                        "message_id": str(ref.get("message_id") or "").strip(),
                        "structured_sentence": str(ref.get("structured_sentence") or ref.get("text") or "").strip(),
                        "role": ref.get("role"),
                    }
                )
        topic_type = str(topic.get("type") or _infer_topic_type(topic, refs) or "observation")
        normalized.append(
            {
                **topic,
                "topic_id": topic_id,
                "title": title,
                "type": topic_type,
                "summary": str(topic.get("summary") or _refs_to_summary(refs) or "").strip(),
                "refs": refs,
                "keywords": _as_list(topic.get("keywords")),
                "message_count": topic.get("message_count") or len(refs),
            }
        )
    return normalized


def _normalize_evidence(source: dict, warnings: list[str]) -> list[dict]:
    evidence: list[dict] = []
    summary_items = _as_list(_as_dict(source.get("summary")).get("items")) or _as_list(source.get("evidence_set"))
    for index, item in enumerate(summary_items):
        if not isinstance(item, dict):
            warnings.append(f"BACKEND_EVIDENCE_SKIPPED: summary.items[{index}] is not an object.")
            continue
        evidence_id = str(item.get("evidence_id") or item.get("summary_item_id") or f"evidence_{index + 1}").strip()
        claim = str(item.get("claim") or item.get("text") or "").strip()
        if not claim:
            warnings.append(f"BACKEND_EVIDENCE_SKIPPED: {evidence_id} has empty text.")
            continue
        evidence.append(
            {
                "evidence_id": evidence_id,
                "claim": claim,
                "topic_id": item.get("topic_id"),
                "source_message_ids": _as_list(item.get("source_message_ids")),
                "confidence": item.get("confidence", 0.0),
                "type": item.get("type") or "summary",
            }
        )

    for message in _as_list(source.get("messages")):
        if not isinstance(message, dict):
            continue
        annotations = _as_dict(message.get("annotations"))
        importance = _as_dict(annotations.get("importance"))
        level = str(importance.get("level") or "")
        score = importance.get("score")
        if level not in {"high", "medium"} and not _score_at_least(score, 0.7):
            continue
        message_id = str(message.get("message_id") or "").strip()
        text = _message_text(message)
        if message_id and text:
            evidence.append(
                {
                    "evidence_id": f"message_{message_id}",
                    "claim": text,
                    "topic_id": _as_dict(annotations.get("topic")).get("topic_id"),
                    "source_message_ids": [message_id],
                    "confidence": score if score is not None else 0.7,
                    "type": "message",
                }
            )
    return evidence


def _unwrap_packet(raw: dict) -> dict:
    for key in ("packet", "context", "result", "structured_packet", "structured_context"):
        value = raw.get(key)
        if isinstance(value, dict):
            return value
    return raw


def _source_packet_type(source: dict) -> str:
    if "summary" in source and "topics" in source and "task" in source:
        return "StructuringResult"
    if "topic_tree" in source and "evidence_set" in source:
        return "AgentPayloadLike"
    return "UnknownPacket"


def _infer_audience(source: dict) -> str:
    task_brief = _as_dict(source.get("task_brief"))
    for item in _as_list(task_brief.get("deliverables")):
        text = str(item)
        if "\u8001\u677f" in text or "\u8bc4\u5ba1" in text:
            return "\u8001\u677f\u8bc4\u5ba1"
    return ""


def _infer_topic_type(topic: dict, refs: list[dict]) -> str:
    text = " ".join(
        [
            str(topic.get("topic_title") or ""),
            str(topic.get("title") or ""),
            str(topic.get("summary") or ""),
            " ".join(str(item) for item in _as_list(topic.get("keywords"))),
            " ".join(str(ref.get("structured_sentence") or "") for ref in refs),
        ]
    ).lower()
    if any(token in text for token in ("deadline", "ddl", "截止", "今天", "明天", "本周", "下周", "评审", "交付", "ppt", "文档")):
        return "delivery"
    if any(token in text for token in ("原因", "根因", "瓶颈", "权限", "模板", "接口", "数据", "耗时", "等待")):
        return "cause"
    if any(token in text for token in ("方案", "优化", "改进", "修复", "建议", "sop", "自动化")):
        return "solution"
    if any(token in text for token in ("问题", "异常", "报错", "慢", "卡", "风险", "延期", "阻塞")):
        return "problem"
    return "observation"


def _message_text(message: dict) -> str:
    content = _as_dict(message.get("content"))
    return str(
        message.get("text")
        or content.get("normalized_text")
        or content.get("plain_text")
        or content.get("raw_content")
        or ""
    ).strip()


def _refs_to_summary(refs: list[dict]) -> str:
    return " ".join(str(ref.get("structured_sentence") or "").strip() for ref in refs if ref.get("structured_sentence")).strip()


def _stage_record(stage_result: dict) -> dict:
    record = {
        "stage": stage_result.get("stage") or "unknown",
        "ok": stage_result.get("ok") is True,
    }
    debug = _as_dict(stage_result.get("debug"))
    if "elapsed_ms" in debug:
        record["elapsed_ms"] = debug["elapsed_ms"]
    if stage_result.get("error"):
        record["error"] = stage_result["error"]
    return record


def _finish_failure(result: dict, stage_result: dict, warnings: list[str], started_at: float) -> dict:
    result["ok"] = False
    result["stage"] = stage_result.get("stage") or "unknown"
    result["error"] = stage_result.get("error") or {
        "code": "E2E_DRY_RUN_FAILED",
        "message": "E2E dry_run failed.",
        "details": stage_result,
    }
    result["warnings"] = _dedupe(warnings + list(stage_result.get("warnings") or []))
    result["debug"] = {"elapsed_ms": _elapsed_ms(started_at)}
    return result


def _stage_error(stage: str, code: str, message: str, details: Any | None, started_at: float) -> dict:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {
        "ok": False,
        "stage": stage,
        "error": error,
        "warnings": [],
        "debug": {"elapsed_ms": _elapsed_ms(started_at)},
    }


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _score_at_least(value: Any, threshold: float) -> bool:
    try:
        return float(value) >= threshold
    except Exception:
        return False


def _dedupe(items: list[Any]) -> list[Any]:
    output = []
    seen = set()
    for item in items:
        marker = repr(item)
        if marker not in seen:
            seen.add(marker)
            output.append(item)
    return output


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


__all__ = [
    "call_agent_generate_ir",
    "normalize_agent_ir_output",
    "normalize_backend_packet",
    "run_e2e_dry_run",
]
