from __future__ import annotations

from message_structuring.components.topic_tracker import TopicTracker
from message_structuring.config import MessageStructuringConfig
from message_structuring.schemas import MessageContent, NormalizedMessage


def main() -> None:
    cfg = MessageStructuringConfig(
        topic_backend="hybrid",
        topic_embedding_model_path="models/missing_topic_embedding",
    )
    tracker = TopicTracker(config=cfg)
    msg = NormalizedMessage(message_id="m1", task_id="t1", chat_id="c1", timestamp_ms=1)
    msg.thread_id = "th1"
    msg.root_id = "root1"
    msg.parent_id = "root1"
    msg.content = MessageContent(
        normalized_text="线上登录接口 500 了，用户无法登录",
        plain_text="线上登录接口 500 了，用户无法登录",
        raw_content="{}",
        content_parse_status="ok",
    )
    ann, _ = tracker.process(msg)
    assert ann.status.value == "done"
    assert "fallback_to_rule" in (ann.reason or "")
    print("Topic backend hybrid fallback test passed.")


if __name__ == "__main__":
    main()
