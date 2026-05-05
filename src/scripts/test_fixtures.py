from __future__ import annotations

import json
from pathlib import Path

from message_structuring.orchestrator import MessageStructuringOrchestrator
from message_structuring.preprocessor import parse_feishu_event


def _load_fixture(name: str) -> dict:
    root = Path(__file__).resolve().parents[2]
    path = root / "data" / "fixtures" / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_important_text_message() -> None:
    payload = _load_fixture("feishu_text_important.json")
    message = parse_feishu_event(payload, task_id="task_fixture_001")
    assert message.message_type == "text"
    assert message.features.has_mention is True
    assert "Decision" in message.content.normalized_text

    orchestrator = MessageStructuringOrchestrator()
    task = orchestrator.start_task(payload["event"]["message"]["chat_id"])
    result = orchestrator.process_feishu_event(payload)
    task_result = orchestrator.get_result(task.task_id)

    assert result["status"] == "processed"
    assert result["message"]["annotations"]["importance"]["status"] == "done"
    assert len(task_result["summary"]["items"]) >= 1, "high-value text should trigger summary item"


def test_post_message() -> None:
    payload = _load_fixture("feishu_post_message.json")
    message = parse_feishu_event(payload, task_id="task_fixture_002")
    assert message.message_type == "post"
    assert "Weekly Project Sync" in message.content.normalized_text
    assert "Meeting moved to Friday" in message.content.normalized_text


def test_image_message() -> None:
    payload = _load_fixture("feishu_image_message.json")
    message = parse_feishu_event(payload, task_id="task_fixture_003")
    assert message.message_type == "image"
    assert message.features.has_image is True
    assert len(message.content.normalized_text.strip()) > 0


def test_malformed_content() -> None:
    payload = _load_fixture("feishu_malformed_content.json")
    message = parse_feishu_event(payload, task_id="task_fixture_004")
    assert message.content.content_parse_status == "error"
    assert message.content.raw_content.startswith("{\"text\"")

    orchestrator = MessageStructuringOrchestrator()
    task = orchestrator.start_task(payload["event"]["message"]["chat_id"])
    result = orchestrator.process_feishu_event(payload)
    assert result["status"] == "processed"
    task_result = orchestrator.get_result(task.task_id)
    assert len(task_result["messages"]) == 1


def test_duplicate_event() -> None:
    payload = _load_fixture("feishu_text_important.json")
    orchestrator = MessageStructuringOrchestrator()
    task = orchestrator.start_task(payload["event"]["message"]["chat_id"])

    first = orchestrator.process_feishu_event(payload)
    second = orchestrator.process_feishu_event(payload)
    task_result = orchestrator.get_result(task.task_id)

    assert first["status"] == "processed"
    assert second["status"] == "duplicate"
    assert len(task_result["messages"]) == 1, "duplicate event should not append new timeline message"
    assert task_result["messages"][0]["annotations"]["importance"]["status"] == "done"


def main() -> None:
    test_important_text_message()
    test_post_message()
    test_image_message()
    test_malformed_content()
    test_duplicate_event()
    print("Fixture tests passed.")


if __name__ == "__main__":
    main()
