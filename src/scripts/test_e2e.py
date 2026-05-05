from __future__ import annotations

import json
from pathlib import Path

from message_structuring.orchestrator import MessageStructuringOrchestrator


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    event_payload = json.loads((root / "messsage_example.json").read_text(encoding="utf-8"))
    chat_id = event_payload["event"]["message"]["chat_id"]

    orchestrator = MessageStructuringOrchestrator()
    task = orchestrator.start_task(chat_id=chat_id, activation_source="manual_api")
    process_result = orchestrator.process_feishu_event(event_payload)
    result = orchestrator.get_result(task.task_id)

    print("Updated normalized message:")
    print(json.dumps(process_result.get("message", {}), ensure_ascii=False, indent=2))
    print("\nFinal result:")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    messages = result["messages"]
    assert len(messages) >= 1, "message should exist in timeline"
    message = messages[0]
    assert message["annotations"]["importance"]["status"] == "done"
    assert message["annotations"]["deliverables"]["status"] == "done"
    assert message["annotations"]["topic"]["status"] in {"done", "skipped"}

    print("\nE2E assertions passed.")


if __name__ == "__main__":
    main()
