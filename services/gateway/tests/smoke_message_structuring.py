from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.gateway.main import app
from services.gateway.structuring_router import get_structuring_orchestrator


FEISHU_TEXT_EVENT = {
    "schema": "2.0",
    "header": {
        "event_id": "evt_gateway_smoke_001",
        "event_type": "im.message.receive_v1",
        "create_time": "1714542000000",
        "token": "test-token",
        "app_id": "cli_test_app",
        "tenant_key": "tenant_test",
    },
    "event": {
        "sender": {
            "sender_id": {
                "union_id": "on_user_smoke_001",
                "user_id": "u_smoke_001",
                "open_id": "ou_smoke_001",
            },
            "sender_type": "user",
            "tenant_key": "tenant_test",
        },
        "message": {
            "message_id": "om_gateway_smoke_001",
            "root_id": "om_gateway_smoke_001",
            "parent_id": "om_gateway_smoke_001",
            "create_time": "1714542000001",
            "update_time": "1714542000002",
            "chat_id": "oc_gateway_smoke",
            "thread_id": "omt_gateway_smoke_thread",
            "chat_type": "group",
            "message_type": "text",
            "content": (
                "{\"text\":\"@_user_1 Decision: API rollout deadline is tomorrow "
                "18:00. Please finish PRD and send update to dev-team@example.com.\"}"
            ),
            "mentions": [
                {
                    "key": "@_user_1",
                    "id": {
                        "union_id": "on_user_tom",
                        "user_id": "u_tom",
                        "open_id": "ou_tom",
                    },
                    "mentioned_type": "user",
                    "name": "Tom",
                    "tenant_key": "tenant_test",
                }
            ],
        },
    },
}


def main() -> None:
    get_structuring_orchestrator.cache_clear()
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    chat_id = "oc_gateway_smoke"
    start = client.post(
        "/api/v1/structuring/tasks/start",
        json={"chat_id": chat_id, "activation_source": "smoke_test"},
    )
    assert start.status_code == 200, start.text
    task_id = start.json()["task_id"]

    event = deepcopy(FEISHU_TEXT_EVENT)
    event["event"]["message"]["chat_id"] = chat_id
    ingest = client.post("/api/v1/structuring/events/feishu", json=event)
    assert ingest.status_code == 200, ingest.text
    assert ingest.json()["status"] in {"processed", "duplicate"}

    result = client.get(f"/api/v1/structuring/tasks/{task_id}/result")
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["task"]["task_id"] == task_id
    assert isinstance(payload["messages"], list) and payload["messages"]
    assert isinstance(payload["topics"], list)
    assert "summary" in payload and "items" in payload["summary"]
    assert "quality" in payload
    assert payload["quality"]["total_messages"] >= 1

    print("gateway message structuring smoke test passed")


if __name__ == "__main__":
    main()
