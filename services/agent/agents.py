from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any, Literal, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

try:
    from langgraph.checkpoint.memory import InMemorySaver
except ImportError:  # pragma: no cover - compatibility with older LangGraph.
    from langgraph.checkpoint.memory import MemorySaver as InMemorySaver

from services.agent.config import get_agent_settings
from services.agent.prompts import (
    ARTIFACT_IR_PROMPT,
    TASK_HYPOTHESIS_PROMPT,
    TOPIC_SUMMARY_PROMPT,
)

JsonDict = dict[str, Any]
ModelMode = Literal["real", "mock"]

ALLOWED_ARTIFACTS = {"doc", "canvas", "deck"}
ALLOWED_BLOCK_KINDS = {
    "cover",
    "split",
    "flow",
    "metrics",
    "cards",
    "table",
    "timeline",
    "image",
}
PLACEHOLDER_KEYS = {
    "",
    "YOUR_API_KEY_HERE",
    "YOUR_DEEPSEEK_API_KEY_HERE",
    "your-api-key",
}


class TopicSummaryState(TypedDict, total=False):
    task_id: str
    topic_id: str
    topic: JsonDict
    old_summary: str
    new_messages: list[JsonDict]
    language: str
    new_summary: str
    summary_patch_reason: str
    evidence_candidates: list[JsonDict]
    open_questions: list[str]
    model_mode: ModelMode


class TaskHypothesisState(TypedDict, total=False):
    task_id: str
    old_task: JsonDict
    topic_summaries: list[JsonDict]
    trigger_messages: list[JsonDict]
    signals: JsonDict
    language: str
    new_title: str | None
    new_summary: str | None
    goal: str | None
    deliverables: list[str]
    deadline: str | None
    status: str
    confidence: float
    model_mode: ModelMode


class ArtifactIRState(TypedDict, total=False):
    task_brief: JsonDict
    topic_tree: list[JsonDict]
    evidence_set: list[JsonDict]
    target_artifact: str
    current_ir: JsonDict | None
    options: JsonDict
    patch: list[JsonDict]
    proposed_ir_if_no_current_ir: JsonDict
    source_trace: list[JsonDict]
    warnings: list[str]
    model_mode: ModelMode


_CHECKPOINTER = InMemorySaver()
_TOPIC_GRAPH: Any | None = None
_TASK_GRAPH: Any | None = None
_ARTIFACT_GRAPH: Any | None = None


def update_structuring_summary(payload: dict) -> dict:
    """Incrementally update one topic summary or one task summary.

    The caller must pass an `update_type` of `topic_summary` or
    `task_summary`. This function never updates both surfaces in one call.
    Errors are returned as structured JSON objects.
    """
    try:
        update_type = payload.get("update_type")
        body = _as_dict(payload.get("payload"))
        options = _as_dict(payload.get("options"))
        language = str(options.get("language") or "zh-CN")

        if update_type == "topic_summary":
            topic = _as_dict(body.get("topic"))
            task_id = _required_str(body, "task_id")
            topic_id = _required_str(topic, "topic_id")
            state: TopicSummaryState = {
                "task_id": task_id,
                "topic_id": topic_id,
                "topic": topic,
                "old_summary": str(topic.get("summary") or ""),
                "new_messages": _as_list(body.get("new_messages")),
                "language": language,
            }
            thread_id = f"{task_id}:topic:{topic_id}:summary"
            result = _get_topic_graph().invoke(
                state, config={"configurable": {"thread_id": thread_id}}
            )
            result = {
                "update_type": "topic_summary",
                "topic_update": {
                    "task_id": task_id,
                    "topic_id": topic_id,
                    "new_summary": result.get("new_summary") or "",
                    "summary_patch_reason": result.get("summary_patch_reason") or "",
                    "open_questions": result.get("open_questions") or [],
                    "evidence_candidates": result.get("evidence_candidates") or [],
                },
                "debug": {
                    "used_graphs": ["topic_summary_graph"],
                    "model_mode": result.get("model_mode") or "mock",
                },
            }
            return _persist_and_return("topic_summary", result)

        if update_type == "task_summary":
            task = _as_dict(body.get("task"))
            task_id = _required_str(task, "task_id")
            state: TaskHypothesisState = {
        "task_id": task_id,
        "old_task": task,
        "topic_summaries": _as_list(body.get("topic_summaries"))
        or _as_list(body.get("topics")),
        "trigger_messages": _as_list(body.get("trigger_messages")),
        "signals": _as_dict(body.get("signals")),
        "language": language,
            }
            thread_id = f"{task_id}:task_summary"
            result = _get_task_graph().invoke(
                state, config={"configurable": {"thread_id": thread_id}}
            )
            result = {
                "update_type": "task_summary",
                "task_update": {
                    "task_id": task_id,
                    "new_title": result.get("new_title"),
                    "new_summary": result.get("new_summary"),
                    "goal": result.get("goal"),
                    "deliverables": result.get("deliverables") or [],
                    "deadline": result.get("deadline"),
                    "status": result.get("status") or "collecting",
                    "confidence": _clamp_confidence(result.get("confidence")),
                },
                "debug": {
                    "used_graphs": ["task_hypothesis_graph"],
                    "model_mode": result.get("model_mode") or "mock",
                },
            }
            return _persist_and_return("task_summary", result)

        return _persist_and_return(
            "summary_error",
            _error(
            "INVALID_UPDATE_TYPE",
            "update_type must be one of: topic_summary, task_summary",
            ),
        )
    except Exception as exc:
        return _persist_and_return(
            "summary_error", _error("AGENT_SUMMARY_FAILED", str(exc))
        )


def generate_artifact_ir_patch(payload: dict) -> dict:
    """Generate a JSON Patch and optional initial IR for doc/canvas/deck output.

    The function only emits IR schemaVersion 0.2.0 structures. It does not call
    Feishu APIs and does not generate docx, pptx, or Adapter-specific output.
    """
    try:
        target_artifact = str(payload.get("target_artifact") or "").strip()
        if target_artifact not in ALLOWED_ARTIFACTS:
            return _persist_and_return(
                "ir_error",
                _error(
                "INVALID_TARGET_ARTIFACT",
                "target_artifact must be one of: doc, canvas, deck",
                ),
            )

        state: ArtifactIRState = {
            "task_brief": _as_dict(payload.get("task_brief")),
            "topic_tree": _as_list(payload.get("topic_tree")),
            "evidence_set": _as_list(payload.get("evidence_set")),
            "target_artifact": target_artifact,
            "current_ir": payload.get("current_ir")
            if isinstance(payload.get("current_ir"), dict)
            else None,
            "options": _as_dict(payload.get("options")),
        }
        result = _get_artifact_graph().invoke(state)
        proposed = result.get("proposed_ir_if_no_current_ir") or _build_initial_ir(
            state
        )
        warnings = list(result.get("warnings") or [])
        warnings.extend(_validate_ir(proposed))

        result = {
            "artifact_type": target_artifact,
            "schemaVersion": get_agent_settings().ir_schema_version,
            "patch": result.get("patch") or [],
            "proposed_ir_if_no_current_ir": proposed,
            "source_trace": result.get("source_trace") or [],
            "warnings": _dedupe(warnings),
            "debug": {
                "used_graphs": ["artifact_ir_graph"],
                "model_mode": result.get("model_mode") or "mock",
            },
        }
        return _persist_and_return("artifact_ir_patch", result)
    except Exception as exc:
        return _persist_and_return("ir_error", _error("AGENT_IR_FAILED", str(exc)))


def build_topic_summary_graph() -> Any:
    """Build the LangGraph graph that updates one topic summary."""
    graph = StateGraph(TopicSummaryState)
    graph.add_node("update_topic_summary", _topic_summary_node)
    graph.add_edge(START, "update_topic_summary")
    graph.add_edge("update_topic_summary", END)
    return graph.compile(checkpointer=_CHECKPOINTER)


def build_task_hypothesis_graph() -> Any:
    """Build the LangGraph graph that updates the task hypothesis/summary."""
    graph = StateGraph(TaskHypothesisState)
    graph.add_node("update_task_hypothesis", _task_hypothesis_node)
    graph.add_edge(START, "update_task_hypothesis")
    graph.add_edge("update_task_hypothesis", END)
    return graph.compile(checkpointer=_CHECKPOINTER)


def build_artifact_ir_graph() -> Any:
    """Build the LangGraph graph that produces IR patch output."""
    graph = StateGraph(ArtifactIRState)
    graph.add_node("generate_artifact_ir", _artifact_ir_node)
    graph.add_edge(START, "generate_artifact_ir")
    graph.add_edge("generate_artifact_ir", END)
    return graph.compile()


def call_llm_json(
    prompt: str, payload: dict, *, schema_hint: str | None = None
) -> tuple[dict, ModelMode]:
    """Call an OpenAI-compatible chat completions endpoint and parse JSON.

    This is the provider replacement point. Fill `AGENT_LLM_BASE_URL`,
    `AGENT_LLM_API_KEY`, and `AGENT_LLM_MODEL` in config or environment
    variables. If mock mode is enabled, the key is still a placeholder, the
    provider fails, or output cannot be parsed after one repair pass, the
    function returns deterministic mock JSON.
    """
    settings = get_agent_settings()
    if settings.mock_mode or settings.api_key in PLACEHOLDER_KEYS:
        return _mock_llm_json(prompt, payload), "mock"

    try:
        url = settings.base_url.rstrip("/") + "/chat/completions"
        messages = [
            {
                "role": "system",
                "content": prompt,
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"schema_hint": schema_hint, "payload": payload},
                    ensure_ascii=False,
                ),
            },
        ]
        with httpx.Client(timeout=settings.timeout_seconds) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {settings.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.model,
                    "messages": messages,
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
        parsed = _loads_json_object(raw)
        if parsed is not None:
            return parsed, "real"
        repaired = _repair_json_text(raw)
        if repaired is not None:
            return repaired, "real"
    except Exception:
        pass
    return _mock_llm_json(prompt, payload), "mock"


def demo_update_topic_summary() -> dict:
    """Run a minimal topic summary demo call."""
    return update_structuring_summary(
        {
            "update_type": "topic_summary",
            "payload": {
                "task_id": "task_001",
                "topic": {
                    "topic_id": "topic_cause",
                    "title": "可能原因",
                    "type": "cause",
                    "summary": "权限配置可能是 onboarding 流程卡点。",
                    "message_ids": ["msg_002"],
                },
                "new_messages": [
                    {
                        "message_id": "msg_003",
                        "sender_name": "C",
                        "timestamp": "2026-05-04T18:42:00+08:00",
                        "text": "我记得模板选择也卡",
                    }
                ],
            },
            "options": {"language": "zh-CN"},
        }
    )


def demo_update_task_summary() -> dict:
    """Run a minimal task hypothesis demo call."""
    return update_structuring_summary(
        {
            "update_type": "task_summary",
            "payload": {
                "task": {
                    "task_id": "task_001",
                    "title": None,
                    "summary": None,
                    "goal": None,
                    "deliverables": [],
                    "deadline": None,
                    "status": "collecting",
                },
                "topic_summaries": [
                    {
                        "topic_id": "topic_problem",
                        "title": "问题现象",
                        "type": "problem",
                        "summary": "客户反馈 onboarding 流程较慢。",
                    },
                    {
                        "topic_id": "topic_cause",
                        "title": "可能原因",
                        "type": "cause",
                        "summary": "权限配置和模板选择可能是 onboarding 流程的主要卡点。",
                    },
                    {
                        "topic_id": "topic_delivery",
                        "title": "交付要求",
                        "type": "delivery",
                        "summary": "下周老板要看优化方案。",
                    },
                ],
                "trigger_messages": [
                    {
                        "message_id": "msg_005",
                        "sender_name": "E",
                        "timestamp": "2026-05-04T18:45:00+08:00",
                        "text": "下周老板要看优化方案",
                    }
                ],
                "signals": {
                    "maybe_deliverable_request": True,
                    "maybe_deadline": "下周",
                    "maybe_audience": "老板",
                },
            },
            "options": {"language": "zh-CN"},
        }
    )


def demo_generate_ir_patch() -> dict:
    """Run a minimal artifact IR patch demo call."""
    return generate_artifact_ir_patch(
        {
            "task_brief": {
                "task_id": "task_001",
                "title": "客户 onboarding 流程优化方案",
                "goal": "形成面向老板评审的 onboarding 优化方案",
                "deliverables": ["方案文档", "评审演示稿"],
                "deadline": "下周",
            },
            "topic_tree": [
                {
                    "topic_id": "topic_problem",
                    "title": "问题现象",
                    "type": "problem",
                    "summary": "客户反馈 onboarding 流程较慢。",
                    "evidence_ids": ["ev_001"],
                },
                {
                    "topic_id": "topic_cause",
                    "title": "可能原因",
                    "type": "cause",
                    "summary": "权限配置和模板选择可能是主要卡点。",
                    "evidence_ids": ["ev_002", "ev_003"],
                },
                {
                    "topic_id": "topic_delivery",
                    "title": "交付要求",
                    "type": "delivery",
                    "summary": "下周需要向老板展示优化方案。",
                    "evidence_ids": ["ev_005"],
                },
            ],
            "evidence_set": [
                {
                    "evidence_id": "ev_001",
                    "claim": "客户反馈 onboarding 流程较慢",
                    "source_message_ids": ["msg_001"],
                    "status": "candidate",
                }
            ],
            "target_artifact": "doc",
            "current_ir": None,
            "options": {
                "language": "zh-CN",
                "patch_only": True,
                "audience": "老板/管理层",
            },
        }
    )


def _topic_summary_node(state: TopicSummaryState) -> JsonDict:
    result, mode = call_llm_json(
        TOPIC_SUMMARY_PROMPT,
        dict(state),
        schema_hint=(
            "Return keys: new_summary, summary_patch_reason, open_questions, "
            "evidence_candidates."
        ),
    )
    fallback = _mock_topic_summary(dict(state))
    return {
        "new_summary": result.get("new_summary") or fallback["new_summary"],
        "summary_patch_reason": result.get("summary_patch_reason")
        or fallback["summary_patch_reason"],
        "open_questions": _as_list(result.get("open_questions")),
        "evidence_candidates": _normalize_evidence_candidates(
            result.get("evidence_candidates"), state.get("new_messages") or []
        ),
        "model_mode": mode,
    }


def _task_hypothesis_node(state: TaskHypothesisState) -> JsonDict:
    result, mode = call_llm_json(
        TASK_HYPOTHESIS_PROMPT,
        dict(state),
        schema_hint=(
            "Return keys: new_title, new_summary, goal, deliverables, deadline, "
            "status, confidence."
        ),
    )
    fallback = _mock_task_summary(dict(state))
    old_task = _as_dict(state.get("old_task"))
    deadline = result.get("deadline") if mode == "real" else fallback["deadline"]
    return {
        "new_title": _pick_title(old_task.get("title"), result.get("new_title"))
        or fallback["new_title"],
        "new_summary": result.get("new_summary") or fallback["new_summary"],
        "goal": result.get("goal") or fallback["goal"],
        "deliverables": _as_list(result.get("deliverables"))
        or fallback["deliverables"],
        "deadline": deadline or fallback["deadline"],
        "status": result.get("status") or fallback["status"],
        "confidence": _clamp_confidence(
            result.get("confidence", fallback["confidence"])
        ),
        "model_mode": mode,
    }


def _artifact_ir_node(state: ArtifactIRState) -> JsonDict:
    result, mode = call_llm_json(
        ARTIFACT_IR_PROMPT,
        dict(state),
        schema_hint=(
            "Return keys: patch, proposed_ir_if_no_current_ir, source_trace, "
            "warnings. IR schemaVersion must be 0.2.0."
        ),
    )
    fallback = _mock_artifact_ir(dict(state))
    proposed = result.get("proposed_ir_if_no_current_ir")
    if not isinstance(proposed, dict) or _validate_ir(proposed):
        proposed = fallback["proposed_ir_if_no_current_ir"]
    patch = result.get("patch")
    if not isinstance(patch, list):
        patch = fallback["patch"]
    return {
        "patch": patch,
        "proposed_ir_if_no_current_ir": proposed,
        "source_trace": _as_list(result.get("source_trace"))
        or fallback["source_trace"],
        "warnings": _as_list(result.get("warnings")),
        "model_mode": mode,
    }


def _get_topic_graph() -> Any:
    global _TOPIC_GRAPH
    if _TOPIC_GRAPH is None:
        _TOPIC_GRAPH = build_topic_summary_graph()
    return _TOPIC_GRAPH


def _get_task_graph() -> Any:
    global _TASK_GRAPH
    if _TASK_GRAPH is None:
        _TASK_GRAPH = build_task_hypothesis_graph()
    return _TASK_GRAPH


def _get_artifact_graph() -> Any:
    global _ARTIFACT_GRAPH
    if _ARTIFACT_GRAPH is None:
        _ARTIFACT_GRAPH = build_artifact_ir_graph()
    return _ARTIFACT_GRAPH


def _mock_llm_json(prompt: str, payload: dict) -> JsonDict:
    if prompt == TOPIC_SUMMARY_PROMPT:
        return _mock_topic_summary(payload)
    if prompt == TASK_HYPOTHESIS_PROMPT:
        return _mock_task_summary(payload)
    if prompt == ARTIFACT_IR_PROMPT:
        return _mock_artifact_ir(payload)
    return {}


def _mock_topic_summary(payload: JsonDict) -> JsonDict:
    topic = _as_dict(payload.get("topic"))
    old_summary = str(payload.get("old_summary") or topic.get("summary") or "")
    messages = _as_list(payload.get("new_messages"))
    topic_type = str(topic.get("type") or "observation")
    text_parts = [_message_text(message) for message in messages]
    text_parts = [text for text in text_parts if text]
    new_info = "；".join(text_parts)
    uncertain = any(_contains_uncertainty(text) for text in text_parts)

    if not new_info:
        new_summary = old_summary
        reason = "新增消息没有提供可用于更新摘要的信息。"
    elif old_summary:
        prefix = "新增消息不确定地补充：" if uncertain else "新增消息补充："
        new_summary = _join_sentences(old_summary, prefix + new_info)
        reason = "基于新增消息增量补充 topic 摘要。"
    else:
        prefix = "可能：" if uncertain else ""
        new_summary = prefix + new_info
        reason = "基于新增消息形成初始 topic 摘要。"

    evidence = []
    for message in messages:
        message_id = str(message.get("message_id") or "").strip()
        text = _message_text(message)
        if not message_id or not text:
            continue
        evidence.append(
            {
                "claim": text,
                "source_message_ids": [message_id],
                "type": topic_type,
                "confidence": 0.64 if _contains_uncertainty(text) else 0.78,
            }
        )

    return {
        "new_summary": new_summary,
        "summary_patch_reason": reason,
        "open_questions": [],
        "evidence_candidates": evidence,
    }


def _mock_task_summary(payload: JsonDict) -> JsonDict:
    old_task = _as_dict(payload.get("old_task") or payload.get("task"))
    topic_summaries = _as_list(payload.get("topic_summaries"))
    trigger_messages = _as_list(payload.get("trigger_messages"))
    signals = _as_dict(payload.get("signals"))
    all_text = " ".join(
        [str(topic.get("summary") or "") for topic in topic_summaries]
        + [_message_text(message) for message in trigger_messages]
        + [json.dumps(signals, ensure_ascii=False)]
    )

    has_delivery = bool(signals.get("maybe_deliverable_request")) or bool(
        re.search(r"方案|PPT|演示稿|汇报|老板|评审|交付|出个|文档", all_text, re.I)
    )
    deadline = _pick_deadline(
        all_text,
        signals.get("maybe_deadline"),
        old_task.get("deadline"),
    )
    audience = str(signals.get("maybe_audience") or "").strip() or (
        "老板" if "老板" in all_text else ""
    )
    problem = _first_summary(topic_summaries, "problem")
    cause = _first_summary(topic_summaries, "cause")
    delivery = _first_summary(topic_summaries, "delivery")
    base_subject = _infer_subject(problem or cause or delivery or all_text)

    old_title = old_task.get("title")
    if has_delivery:
        title = _pick_title(old_title, f"{base_subject}优化方案")
        goal_suffix = f"面向{audience}评审" if audience else "用于评审"
        goal = f"形成{goal_suffix}的{base_subject}优化方案"
        deliverables = _infer_deliverables(all_text)
        status = "deliverable_identified"
        confidence = 0.88 if delivery or signals else 0.74
    else:
        title = _pick_title(old_title, None)
        goal = old_task.get("goal")
        deliverables = _as_list(old_task.get("deliverables"))
        status = str(old_task.get("status") or "collecting")
        confidence = 0.62 if topic_summaries else 0.45

    summary_parts = [text for text in (problem, cause, delivery) if text]
    if summary_parts:
        new_summary = "团队正在讨论" + "；".join(summary_parts)
    else:
        new_summary = old_task.get("summary") or ""
    if has_delivery and deadline:
        new_summary = _join_sentences(
            new_summary, f"需要在{deadline}形成相关交付材料。"
        )

    return {
        "new_title": title,
        "new_summary": new_summary or None,
        "goal": goal,
        "deliverables": deliverables,
        "deadline": deadline or old_task.get("deadline"),
        "status": status,
        "confidence": confidence,
    }


def _mock_artifact_ir(payload: JsonDict) -> JsonDict:
    state: ArtifactIRState = {
        "task_brief": _as_dict(payload.get("task_brief")),
        "topic_tree": _as_list(payload.get("topic_tree")),
        "evidence_set": _as_list(payload.get("evidence_set")),
        "target_artifact": str(payload.get("target_artifact") or "doc"),
        "current_ir": payload.get("current_ir")
        if isinstance(payload.get("current_ir"), dict)
        else None,
        "options": _as_dict(payload.get("options")),
    }
    proposed = _build_initial_ir(state)
    patch = [
        {"op": "replace", "path": "/meta/title", "value": proposed["meta"]["title"]}
    ]
    for block in proposed["blocks"]:
        patch.append({"op": "add", "path": "/blocks/-", "value": block})
    return {
        "patch": patch,
        "proposed_ir_if_no_current_ir": proposed,
        "source_trace": _build_source_trace(state["topic_tree"]),
        "warnings": [],
    }


def _build_initial_ir(state: ArtifactIRState) -> JsonDict:
    settings = get_agent_settings()
    task = _as_dict(state.get("task_brief"))
    topics = _as_list(state.get("topic_tree"))
    options = _as_dict(state.get("options"))
    target = str(state.get("target_artifact") or "doc")
    title = str(task.get("title") or task.get("goal") or "未命名协作任务")
    task_id = str(task.get("task_id") or "task")
    audience = str(options.get("audience") or "")

    blocks = [
        {
            "id": "cover",
            "kind": "cover",
            "title": title,
            "subtitle": task.get("goal") or "基于 IM 讨论自动沉淀的方案材料",
        }
    ]
    for topic in topics:
        block_id = _safe_id(str(topic.get("topic_id") or topic.get("title") or "topic"))
        summary = str(topic.get("summary") or "").strip()
        blocks.append(
            {
                "id": block_id,
                "kind": "split",
                "title": str(topic.get("title") or topic.get("type") or "讨论要点"),
                "points": [summary] if summary else [],
            }
        )

    if len(topics) >= 2:
        nodes = [
            {
                "id": _safe_id(str(topic.get("topic_id") or f"topic_{index}")),
                "label": str(topic.get("title") or topic.get("type") or f"Topic {index}"),
            }
            for index, topic in enumerate(topics, start=1)
        ]
        edges = [
            {"from": nodes[index]["id"], "to": nodes[index + 1]["id"]}
            for index in range(len(nodes) - 1)
        ]
        blocks.append(
            {
                "id": "topic_flow",
                "kind": "flow",
                "title": "讨论脉络",
                "nodes": nodes,
                "edges": edges,
            }
        )

    deliverables = _as_list(task.get("deliverables"))
    if deliverables:
        blocks.append(
            {
                "id": "deliverables",
                "kind": "cards",
                "title": "交付物",
                "cards": [
                    {"title": str(item), "body": "待 Adapter 渲染为对应产物"}
                    for item in deliverables
                ],
            }
        )

    if task.get("deadline"):
        blocks.append(
            {
                "id": "timeline",
                "kind": "timeline",
                "title": "时间要求",
                "items": [{"label": str(task["deadline"]), "text": "完成材料准备"}],
            }
        )

    return {
        "schemaVersion": settings.ir_schema_version,
        "docId": f"{task_id}_{target}",
        "meta": {
            "title": title,
            "subtitle": "基于 IM 讨论自动沉淀的方案材料",
            "owner": "Agent",
            "date": date.today().isoformat(),
            "audience": audience,
        },
        "theme": {
            "name": "Executive Blue",
            "accent": "#2F6BFF",
            "accent2": "#7C3AED",
            "background": "#F8FAFC",
            "surface": "#FFFFFF",
            "text": "#0F172A",
            "muted": "#64748B",
            "success": "#16A34A",
            "warning": "#F97316",
            "fontFace": "Aptos",
        },
        "assets": {},
        "blocks": blocks,
    }


def _validate_ir(ir: JsonDict) -> list[str]:
    warnings: list[str] = []
    settings = get_agent_settings()
    if ir.get("schemaVersion") != settings.ir_schema_version:
        warnings.append("IR schemaVersion must be 0.2.0.")
    for key in ("meta", "theme", "assets", "blocks"):
        if key not in ir:
            warnings.append(f"IR missing top-level key: {key}.")
    blocks = ir.get("blocks")
    if not isinstance(blocks, list):
        warnings.append("IR blocks must be a list.")
        return warnings
    for block in blocks:
        if not isinstance(block, dict):
            warnings.append("IR block must be an object.")
            continue
        block_id = block.get("id")
        kind = block.get("kind")
        if not block_id or not kind or not block.get("title"):
            warnings.append("Each block must contain id, kind, and title.")
        if kind not in ALLOWED_BLOCK_KINDS:
            warnings.append(f"Unsupported block kind: {kind}.")
        if kind == "flow":
            node_ids = {
                str(node.get("id"))
                for node in _as_list(block.get("nodes"))
                if isinstance(node, dict)
            }
            if not node_ids:
                warnings.append(f"Flow block {block_id} must contain nodes.")
            for edge in _as_list(block.get("edges")):
                if edge.get("from") not in node_ids or edge.get("to") not in node_ids:
                    warnings.append(f"Flow block {block_id} has invalid edge refs.")
        if kind == "table" and (
            not isinstance(block.get("columns"), list)
            or not isinstance(block.get("rows"), list)
        ):
            warnings.append(f"Table block {block_id} must contain columns and rows.")
        if kind == "metrics" and not isinstance(block.get("items"), list):
            warnings.append(f"Metrics block {block_id} must contain items.")
        if kind == "cards" and not isinstance(block.get("cards"), list):
            warnings.append(f"Cards block {block_id} must contain cards.")
    return _dedupe(warnings)


def _normalize_evidence_candidates(value: Any, messages: list[JsonDict]) -> list[JsonDict]:
    fallback_ids = [
        str(message.get("message_id"))
        for message in messages
        if str(message.get("message_id") or "").strip()
    ]
    output = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        source_ids = _as_list(item.get("source_message_ids")) or fallback_ids
        source_ids = [str(source_id) for source_id in source_ids if source_id]
        if not source_ids:
            continue
        output.append(
            {
                "claim": str(item.get("claim") or ""),
                "source_message_ids": source_ids,
                "type": str(item.get("type") or "observation"),
                "confidence": _clamp_confidence(item.get("confidence")),
            }
        )
    if output:
        return output
    return _mock_topic_summary({"new_messages": messages}).get("evidence_candidates", [])


def _build_source_trace(topics: list[JsonDict]) -> list[JsonDict]:
    trace = []
    for topic in topics:
        topic_id = str(topic.get("topic_id") or "").strip()
        if not topic_id:
            continue
        trace.append(
            {
                "block_id": _safe_id(topic_id),
                "source_topic_ids": [topic_id],
                "source_evidence_ids": _as_list(topic.get("evidence_ids")),
            }
        )
    return trace


def _loads_json_object(raw: str) -> JsonDict | None:
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _repair_json_text(raw: str) -> JsonDict | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.I).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return _loads_json_object(text[start : end + 1])
    return None


def _as_dict(value: Any) -> JsonDict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _required_str(value: JsonDict, key: str) -> str:
    result = str(value.get(key) or "").strip()
    if not result:
        raise ValueError(f"Missing required field: {key}")
    return result


def _message_text(message: JsonDict) -> str:
    return str(message.get("text") or message.get("content") or "").strip()


def _contains_uncertainty(text: str) -> bool:
    return bool(re.search(r"可能|也许|大概|记得|不确定|似乎|应该", text))


def _join_sentences(*parts: Any) -> str:
    cleaned = [str(part).strip(" 。;；") for part in parts if str(part or "").strip()]
    if not cleaned:
        return ""
    return "。".join(cleaned) + "。"


def _first_summary(topics: list[JsonDict], topic_type: str) -> str:
    for topic in topics:
        if str(topic.get("type") or "") == topic_type:
            return str(topic.get("summary") or "").strip()
    return ""


def _extract_deadline(text: str) -> str:
    concrete = _extract_concrete_deadline(text)
    if concrete:
        return concrete
    for token in ("今天", "明天", "后天", "本周", "这周", "下周", "月底", "月末"):
        if token in text:
            return token
    return ""


def _extract_concrete_deadline(text: str) -> str:
    patterns = (
        r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日号]?(?:之前|以前|前|截止)?）?)",
        r"(\d{1,2}\s*月\s*\d{1,2}\s*[日号](?:之前|以前|前|截止)?)",
        r"([一二三四五六七八九十]{1,3}\s*月\s*[一二三四五六七八九十]{1,3}\s*[日号](?:之前|以前|前|截止)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", "", match.group(1).rstrip("）"))
    return ""


def _pick_deadline(text: str, signal_deadline: Any = None, old_deadline: Any = None) -> str:
    concrete = _extract_concrete_deadline(text)
    if concrete:
        return concrete
    signal = str(signal_deadline or "").strip()
    if signal:
        return signal
    extracted = _extract_deadline(text)
    if extracted:
        return extracted
    return str(old_deadline or "").strip()


def _is_more_specific_deadline(candidate: Any, current: Any) -> bool:
    cand = str(candidate or "").strip()
    curr = str(current or "").strip()
    if not cand:
        return False
    if not curr:
        return True
    return bool(_extract_concrete_deadline(cand)) and not bool(
        _extract_concrete_deadline(curr)
    )


def _summary_text_for_deadline(state: TaskHypothesisState) -> str:
    signals = _as_dict(state.get("signals"))
    return " ".join(
        [str(topic.get("summary") or "") for topic in _as_list(state.get("topic_summaries"))]
        + [_message_text(message) for message in _as_list(state.get("trigger_messages"))]
        + [json.dumps(signals, ensure_ascii=False)]
    )


def _infer_subject(text: str) -> str:
    if "onboarding" in text.lower():
        return "客户 onboarding 流程"
    if "客户" in text:
        return "客户流程"
    if "权限" in text:
        return "权限配置流程"
    return "协作任务"


def _infer_deliverables(text: str) -> list[str]:
    deliverables = []
    if re.search(r"方案|文档|doc", text, re.I):
        deliverables.append("方案文档")
    if re.search(r"PPT|演示稿|汇报|老板|评审", text, re.I):
        deliverables.append("评审演示稿")
    return deliverables or ["方案文档"]


def _pick_title(old_title: Any, candidate: Any) -> str | None:
    old = str(old_title or "").strip()
    new = str(candidate or "").strip()
    if old and (not new or len(old) >= len(new) - 2):
        return old
    return new or None


def _clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        number = 0.0
    return max(0.0, min(1.0, round(number, 2)))


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_\-]+", "_", value.strip())
    return safe.strip("_") or "block"


def _dedupe(items: list[Any]) -> list[Any]:
    output = []
    seen = set()
    for item in items:
        marker = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if marker not in seen:
            output.append(item)
            seen.add(marker)
    return output


def _persist_and_return(kind: str, result: JsonDict) -> JsonDict:
    path = _persist_output(kind, result)
    if path and isinstance(result.get("debug"), dict):
        result["debug"]["output_file"] = str(path)
    return result


def _persist_output(kind: str, result: JsonDict) -> str:
    settings = get_agent_settings()
    output_dir = settings.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = output_dir / f"{timestamp}_{_safe_id(kind)}.json"
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def _error(code: str, message: str, details: JsonDict | None = None) -> JsonDict:
    error: JsonDict = {"code": code, "message": message}
    if details:
        error["details"] = details
    return {"error": error}


__all__ = [
    "build_artifact_ir_graph",
    "build_task_hypothesis_graph",
    "build_topic_summary_graph",
    "call_llm_json",
    "demo_generate_ir_patch",
    "demo_update_task_summary",
    "demo_update_topic_summary",
    "generate_artifact_ir_patch",
    "update_structuring_summary",
]
