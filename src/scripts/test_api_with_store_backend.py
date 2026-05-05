from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient


def _load_fixture(name: str) -> dict:
    root = Path(__file__).resolve().parents[2]
    return json.loads((root / "data" / "fixtures" / name).read_text(encoding="utf-8"))


def main() -> None:
    os.environ["STORE_BACKEND"] = "memory"
    module = importlib.import_module("service.api")
    module = importlib.reload(module)
    client = TestClient(module.app)

    start = client.post("/tasks/start", json={"chat_id": "oc_api_store_backend_001"})
    assert start.status_code == 200
    task_id = start.json()["task_id"]

    event = _load_fixture("feishu_text_important.json")
    event["event"]["message"]["chat_id"] = "oc_api_store_backend_001"
    r = client.post("/events/feishu", json=event)
    assert r.status_code == 200
    assert r.json()["status"] in {"processed", "duplicate"}

    result = client.get(f"/tasks/{task_id}/result")
    assert result.status_code == 200
    assert result.json()["task"]["task_id"] == task_id

    print("API with store backend test passed.")


if __name__ == "__main__":
    main()
