from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from message_structuring.orchestrator import MessageStructuringOrchestrator


def _load_topic_eval_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def _quality_summary(noise_skip_rate: float, fragmentation: float, max_topics_per_expected_topic: int) -> str:
    if noise_skip_rate >= 0.9 and fragmentation <= 0.5 and max_topics_per_expected_topic <= 2:
        return "strong"
    if noise_skip_rate >= 0.85 and fragmentation <= 1.0 and max_topics_per_expected_topic <= 3:
        return "good"
    return "needs_improvement"


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    dataset_path = root / "data" / "fixtures" / "topic_eval" / "topic_eval_zh.jsonl"
    records = _load_topic_eval_jsonl(dataset_path)
    assert len(records) >= 100, "topic eval dataset should contain at least 100 messages"

    orchestrator = MessageStructuringOrchestrator()
    first_chat_id = records[0]["event"]["event"]["message"]["chat_id"] if "event" in records[0]["event"] else records[0]["event"]["message"]["chat_id"]
    if "event" in records[0]["event"]:
        # Compatibility fallback, not expected for generated fixture.
        first_chat_id = records[0]["event"]["event"]["message"]["chat_id"]
    else:
        first_chat_id = records[0]["event"]["message"]["chat_id"]
    task = orchestrator.start_task(chat_id=first_chat_id, activation_source="manual_api")

    expected_by_message_id: dict[str, dict] = {}
    expected_topic_keys: set[str] = set()
    expected_formal_messages = 0
    expected_noise_messages = 0

    for record in records:
        expected = record["expected"]
        event = record["event"]
        msg_id = event["event"]["message"]["message_id"]
        expected_by_message_id[msg_id] = expected
        if expected["should_create_formal_topic"]:
            expected_formal_messages += 1
            expected_topic_keys.add(expected["topic_key"])
        else:
            expected_noise_messages += 1
        result = orchestrator.process_feishu_event(event)
        assert result["status"] in {"processed", "duplicate"}

    task_result = orchestrator.get_result(task.task_id)
    messages = task_result["messages"]
    topics = task_result["topics"]

    noise_skipped = 0
    expected_topic_to_actual: dict[str, set[str]] = defaultdict(set)
    for msg in messages:
        expected = expected_by_message_id.get(msg["message_id"])
        if expected is None:
            continue
        topic_status = msg["annotations"]["topic"]["status"]
        topic_id = msg["annotations"]["topic"]["topic_id"]
        if expected["should_create_formal_topic"]:
            if topic_status == "done" and topic_id:
                expected_topic_to_actual[expected["topic_key"]].add(topic_id)
        else:
            if topic_status == "skipped":
                noise_skipped += 1

    fragmentation_values = []
    max_topics_per_expected_topic = 0
    for key in sorted(expected_topic_keys):
        topic_set = expected_topic_to_actual.get(key, set())
        max_topics_per_expected_topic = max(max_topics_per_expected_topic, len(topic_set))
        fragmentation_values.append(max(0, len(topic_set) - 1))

    same_topic_fragmentation = (
        sum(fragmentation_values) / len(fragmentation_values) if fragmentation_values else 0.0
    )
    noise_skip_rate = noise_skipped / expected_noise_messages if expected_noise_messages else 1.0
    metrics = {
        "total_messages": len(messages),
        "expected_formal_messages": expected_formal_messages,
        "actual_formal_topic_count": len(topics),
        "noise_skip_rate": round(noise_skip_rate, 4),
        "same_topic_fragmentation": round(same_topic_fragmentation, 4),
        "max_topics_per_expected_topic": max_topics_per_expected_topic,
        "merge_quality_summary": _quality_summary(noise_skip_rate, same_topic_fragmentation, max_topics_per_expected_topic),
    }

    print(json.dumps(metrics, ensure_ascii=False, indent=2))

    assert metrics["total_messages"] == len(records)
    assert noise_skip_rate >= 0.85, f"noise skip rate too low: {noise_skip_rate}"
    assert max_topics_per_expected_topic <= 3, f"topic fragmentation too high: {max_topics_per_expected_topic}"
    assert same_topic_fragmentation <= 1.2, f"same-topic fragmentation too high: {same_topic_fragmentation}"

    print("Topic evaluation test passed.")


if __name__ == "__main__":
    main()
