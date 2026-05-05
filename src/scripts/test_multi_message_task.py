from __future__ import annotations

import json
from pathlib import Path

from message_structuring.orchestrator import MessageStructuringOrchestrator


def _load_jsonl(path: Path) -> list[dict]:
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    events_path = root / "data" / "fixtures" / "feishu_multi_message_task.jsonl"
    events = _load_jsonl(events_path)
    assert len(events) >= 3, "fixture should contain multiple events"

    chat_id = events[0]["event"]["message"]["chat_id"]
    orchestrator = MessageStructuringOrchestrator()
    task = orchestrator.start_task(chat_id=chat_id, activation_source="manual_api")

    for event in events:
        result = orchestrator.process_feishu_event(event)
        assert result["status"] == "processed"

    task_result = orchestrator.get_result(task.task_id)
    messages = task_result["messages"]
    assert len(messages) == len(events), "all fixture events should be captured under same task"

    for msg in messages:
        assert msg["annotations"]["importance"]["status"] == "done"
        assert msg["annotations"]["deliverables"]["status"] == "done"
        assert msg["annotations"]["topic"]["status"] == "done"

    noise_message = next(msg for msg in messages if msg["message_id"] == "om_multi_001")
    assert noise_message["annotations"]["summary"]["status"] in {"not_selected", "skipped"}

    summary_items = task_result["summary"]["items"]
    assert len(summary_items) >= 1, "high-value messages should trigger at least one summary item"
    assert all(item["source_message_ids"] for item in summary_items)

    print(json.dumps(task_result["quality"], ensure_ascii=False, indent=2))
    print("Multi-message task test passed.")


if __name__ == "__main__":
    main()
