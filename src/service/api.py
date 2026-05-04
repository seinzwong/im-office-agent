from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from message_structuring.orchestrator import MessageStructuringOrchestrator

app = FastAPI(title="MessageStructuringLayer API")
orchestrator = MessageStructuringOrchestrator()


class StartTaskRequest(BaseModel):
    chat_id: str
    activation_source: str = "manual_api"
    task_title: str | None = None


class StopTaskRequest(BaseModel):
    reason: str = "manual"


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


@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    task = orchestrator.task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task.model_dump(mode="json")


@app.get("/tasks/{task_id}/result")
def get_result(task_id: str) -> dict:
    result = orchestrator.get_result(task_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="task not found")
    return result


@app.post("/events/feishu")
def process_feishu_event(raw_event: dict) -> dict:
    return orchestrator.process_feishu_event(raw_event)
