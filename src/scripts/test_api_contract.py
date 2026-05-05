from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from service.api import app


def _load_fixture(name: str) -> dict:
    root = Path(__file__).resolve().parents[2]
    return json.loads((root / "data" / "fixtures" / name).read_text(encoding="utf-8"))


def main() -> None:
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    chat_id = "oc_api_contract_chat_001"
    start = client.post("/tasks/start", json={"chat_id": chat_id, "activation_source": "test_api"})
    assert start.status_code == 200
    task = start.json()
    task_id = task["task_id"]

    get_task = client.get(f"/tasks/{task_id}")
    assert get_task.status_code == 200
    assert get_task.json()["task_id"] == task_id

    activation = client.post("/activation/state", json={"chat_id": "oc_api_contract_chat_002", "active": True})
    assert activation.status_code == 200
    assert activation.json()["status"] in {"activated", "already_active"}

    event = _load_fixture("feishu_text_important.json")
    event["event"]["message"]["chat_id"] = chat_id
    process = client.post("/events/feishu", json=event)
    assert process.status_code == 200
    assert process.json()["status"] in {"processed", "duplicate"}

    batch_event = _load_fixture("feishu_post_message.json")
    batch_event["event"]["message"]["chat_id"] = chat_id
    batch = client.post("/events/feishu/batch", json={"events": [batch_event]})
    assert batch.status_code == 200
    assert batch.json()["count"] == 1

    messages = client.get(f"/tasks/{task_id}/messages")
    assert messages.status_code == 200
    assert len(messages.json()["messages"]) >= 1

    topics = client.get(f"/tasks/{task_id}/topics")
    assert topics.status_code == 200
    assert "topics" in topics.json()

    summary = client.get(f"/tasks/{task_id}/summary")
    assert summary.status_code == 200
    assert "items" in summary.json()

    result = client.get(f"/tasks/{task_id}/result")
    assert result.status_code == 200
    assert result.json()["task"]["task_id"] == task_id

    reprocess = client.post(f"/tasks/{task_id}/reprocess/summary")
    assert reprocess.status_code == 200
    assert reprocess.json()["status"] == "ok"

    stop = client.post(f"/tasks/{task_id}/stop", json={"reason": "test_complete"})
    assert stop.status_code == 200
    assert stop.json()["status"] == "stopped"

    print("API contract tests passed.")


if __name__ == "__main__":
    main()
