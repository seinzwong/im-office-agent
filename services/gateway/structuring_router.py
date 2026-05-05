from __future__ import annotations

from dataclasses import asdict
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .message_structuring.orchestrator import MessageStructuringOrchestrator

router = APIRouter(prefix="/api/v1/structuring", tags=["message-structuring"])


@lru_cache(maxsize=1)
def get_structuring_orchestrator() -> MessageStructuringOrchestrator:
    return MessageStructuringOrchestrator()


class StartTaskRequest(BaseModel):
    chat_id: str
    activation_source: str = "manual_api"
    task_title: str | None = None


class StopTaskRequest(BaseModel):
    reason: str = "manual"


class ActivationStateRequest(BaseModel):
    chat_id: str
    active: bool
    task_title: str | None = None


class FeishuBatchRequest(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/tasks/start")
def start_task(req: StartTaskRequest) -> dict[str, Any]:
    task = get_structuring_orchestrator().start_task(
        chat_id=req.chat_id,
        activation_source=req.activation_source,
        task_title=req.task_title,
    )
    return task.model_dump(mode="json")


@router.post("/tasks/{task_id}/stop")
def stop_task(task_id: str, req: StopTaskRequest) -> dict[str, Any]:
    task = get_structuring_orchestrator().stop_task(task_id, reason=req.reason)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task.model_dump(mode="json")


@router.post("/activation/state")
def activation_state(req: ActivationStateRequest) -> dict[str, Any]:
    return get_structuring_orchestrator().set_activation_state(
        chat_id=req.chat_id,
        active=req.active,
        task_title=req.task_title,
    )


@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    task = get_structuring_orchestrator().get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task.model_dump(mode="json")


@router.get("/tasks/{task_id}/messages")
def get_task_messages(task_id: str) -> dict[str, Any]:
    messages = get_structuring_orchestrator().get_messages(task_id)
    if isinstance(messages, dict) and messages.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="task not found")
    return {"messages": messages}


@router.get("/tasks/{task_id}/topics")
def get_task_topics(task_id: str) -> dict[str, Any]:
    topics = get_structuring_orchestrator().get_topics(task_id)
    if isinstance(topics, dict) and topics.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="task not found")
    return {"topics": topics}


@router.get("/tasks/{task_id}/summary")
def get_task_summary(task_id: str) -> dict[str, Any]:
    summary = get_structuring_orchestrator().get_summary(task_id)
    if summary.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="task not found")
    return summary


@router.get("/tasks/{task_id}/result")
def get_result(task_id: str) -> dict[str, Any]:
    result = get_structuring_orchestrator().get_result(task_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="task not found")
    return result


@router.post("/tasks/{task_id}/reprocess/summary")
def reprocess_summary(task_id: str) -> dict[str, Any]:
    result = get_structuring_orchestrator().reprocess_summary(task_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="task not found")
    return result


@router.post("/events/feishu")
def process_feishu_event(raw_event: dict[str, Any]) -> dict[str, Any]:
    return get_structuring_orchestrator().process_feishu_event(raw_event)


@router.post("/events/feishu/batch")
def process_feishu_event_batch(req: FeishuBatchRequest) -> dict[str, Any]:
    return get_structuring_orchestrator().process_feishu_events_batch(req.events)


@router.get("/debug/config")
def debug_config() -> dict[str, Any]:
    cfg = asdict(get_structuring_orchestrator().config)
    return {
        "store_backend": cfg.get("store_backend"),
        "summary_client_mode": cfg.get("summary_client_mode"),
        "importance_backend": cfg.get("importance_backend"),
        "topic_backend": cfg.get("topic_backend"),
        "summary_importance_threshold": cfg.get("summary_importance_threshold"),
        "importance_hybrid_bert_weight": cfg.get("importance_hybrid_bert_weight"),
        "topic_embedding_assign_threshold": cfg.get("topic_embedding_assign_threshold"),
        "topic_embedding_uncertain_threshold": cfg.get("topic_embedding_uncertain_threshold"),
        "model_paths": {
            "importance_bert_model_path": cfg.get("importance_bert_model_path"),
            "importance_bert_base_model": cfg.get("importance_bert_base_model"),
            "topic_embedding_model_path": cfg.get("topic_embedding_model_path"),
            "topic_embedding_base_model": cfg.get("topic_embedding_base_model"),
        },
        "runtime": {
            "importance_bert_device": cfg.get("importance_bert_device"),
            "topic_embedding_device": cfg.get("topic_embedding_device"),
            "teammate_summary_base_url": cfg.get("teammate_summary_base_url"),
            "teammate_summary_timeout_seconds": cfg.get("teammate_summary_timeout_seconds"),
            "teammate_summary_api_key_configured": bool(cfg.get("teammate_summary_api_key")),
        },
        "redis": {
            "redis_url": cfg.get("redis_url"),
            "redis_key_prefix": cfg.get("redis_key_prefix"),
            "redis_buffer_ttl_seconds": cfg.get("redis_buffer_ttl_seconds"),
            "redis_max_buffer_messages": cfg.get("redis_max_buffer_messages"),
        },
    }


__all__ = ["get_structuring_orchestrator", "router"]
