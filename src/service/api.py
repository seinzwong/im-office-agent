from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from message_structuring.orchestrator import MessageStructuringOrchestrator

app = FastAPI(title="MessageStructuringLayer API")
orchestrator = MessageStructuringOrchestrator()


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
    events: list[dict] = Field(default_factory=list)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/tasks/start")
def start_task(req: StartTaskRequest) -> dict:
    task = orchestrator.start_task(
        chat_id=req.chat_id,
        activation_source=req.activation_source,
        task_title=req.task_title,
    )
    return task.model_dump(mode="json")


@app.post("/tasks/{task_id}/stop")
def stop_task(task_id: str, req: StopTaskRequest) -> dict:
    task = orchestrator.stop_task(task_id, reason=req.reason)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task.model_dump(mode="json")


@app.post("/activation/state")
def activation_state(req: ActivationStateRequest) -> dict:
    return orchestrator.set_activation_state(chat_id=req.chat_id, active=req.active, task_title=req.task_title)


@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    task = orchestrator.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task.model_dump(mode="json")


@app.get("/tasks/{task_id}/messages")
def get_task_messages(task_id: str) -> dict:
    messages = orchestrator.get_messages(task_id)
    if isinstance(messages, dict) and messages.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="task not found")
    return {"messages": messages}


@app.get("/tasks/{task_id}/topics")
def get_task_topics(task_id: str) -> dict:
    topics = orchestrator.get_topics(task_id)
    if isinstance(topics, dict) and topics.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="task not found")
    return {"topics": topics}


@app.get("/tasks/{task_id}/summary")
def get_task_summary(task_id: str) -> dict:
    summary = orchestrator.get_summary(task_id)
    if summary.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="task not found")
    return summary


@app.get("/tasks/{task_id}/result")
def get_result(task_id: str) -> dict:
    result = orchestrator.get_result(task_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="task not found")
    return result


@app.post("/tasks/{task_id}/reprocess/summary")
def reprocess_summary(task_id: str) -> dict:
    result = orchestrator.reprocess_summary(task_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="task not found")
    return result


@app.post("/events/feishu")
def process_feishu_event(raw_event: dict) -> dict:
    return orchestrator.process_feishu_event(raw_event)


@app.post("/events/feishu/batch")
def process_feishu_event_batch(req: FeishuBatchRequest) -> dict:
    return orchestrator.process_feishu_events_batch(req.events)


@app.get("/debug/config")
def debug_config() -> dict:
    cfg = asdict(orchestrator.config)
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
