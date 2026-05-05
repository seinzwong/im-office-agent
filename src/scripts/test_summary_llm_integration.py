from __future__ import annotations

import json
from pathlib import Path

from message_structuring.config import MessageStructuringConfig
from message_structuring.orchestrator import MessageStructuringOrchestrator


def _load_fixture(name: str) -> dict:
    root = Path(__file__).resolve().parents[2]
    return json.loads((root / "data" / "fixtures" / name).read_text(encoding="utf-8"))


class _BrokenSummaryClient:
    def generate(self, req):  # noqa: ANN001
        raise RuntimeError("simulated summary service failure")


def test_stub_summary_generation() -> None:
    cfg = MessageStructuringConfig(summary_client_mode="stub", summary_importance_threshold=0.45)
    orchestrator = MessageStructuringOrchestrator(config=cfg)
    event = _load_fixture("feishu_text_important.json")
    task = orchestrator.start_task(event["event"]["message"]["chat_id"])
    out = orchestrator.process_feishu_event(event)
    assert out["status"] == "processed"
    result = orchestrator.get_result(task.task_id)
    assert len(result["summary"]["items"]) >= 1
    msg = result["messages"][0]
    assert msg["annotations"]["summary"]["status"] == "done"


def test_threshold_blocks_summary() -> None:
    cfg = MessageStructuringConfig(summary_client_mode="stub", summary_importance_threshold=0.95)
    orchestrator = MessageStructuringOrchestrator(config=cfg)
    event = _load_fixture("feishu_text_important.json")
    task = orchestrator.start_task(event["event"]["message"]["chat_id"])
    orchestrator.process_feishu_event(event)
    result = orchestrator.get_result(task.task_id)
    assert len(result["summary"]["items"]) == 0
    msg = result["messages"][0]
    assert msg["annotations"]["summary"]["status"] == "not_selected"


def test_summary_client_failure_fallback() -> None:
    cfg = MessageStructuringConfig(summary_client_mode="stub", summary_importance_threshold=0.45)
    orchestrator = MessageStructuringOrchestrator(config=cfg)
    orchestrator.summary_updater.summary_client = _BrokenSummaryClient()
    event = _load_fixture("feishu_text_important.json")
    task = orchestrator.start_task(event["event"]["message"]["chat_id"])
    out = orchestrator.process_feishu_event(event)
    assert out["status"] == "processed"
    result = orchestrator.get_result(task.task_id)
    msg = result["messages"][0]
    assert msg["annotations"]["summary"]["status"] == "error"
    assert "summary client failure" in (msg["annotations"]["summary"]["reason"] or "")


def main() -> None:
    test_stub_summary_generation()
    test_threshold_blocks_summary()
    test_summary_client_failure_fallback()
    print("Summary LLM integration tests passed.")


if __name__ == "__main__":
    main()
