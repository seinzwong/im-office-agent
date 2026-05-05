from __future__ import annotations

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
