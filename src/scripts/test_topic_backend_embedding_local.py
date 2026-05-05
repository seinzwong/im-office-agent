from __future__ import annotations

from pathlib import Path

from message_structuring.components.topic_tracker import TopicTracker
from message_structuring.config import MessageStructuringConfig
from message_structuring.topic_backends import ModelUnavailableError
from message_structuring.schemas import MessageContent, NormalizedMessage


def main() -> None:
    model_path = Path("models/topic_embedding")
    if not model_path.exists():
        print(f"SKIP: local embedding model not found at {model_path}")
        return

    cfg = MessageStructuringConfig(
        topic_backend="embedding",
        topic_embedding_model_path=str(model_path),
    )
    try:
        tracker = TopicTracker(config=cfg)
    except ModelUnavailableError as exc:
        raise RuntimeError(f"Model path exists but embedding backend unavailable: {exc}") from exc

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
    assert ann.status.value in {"done", "skipped"}
    print("Topic backend embedding local test passed.")


if __name__ == "__main__":
    main()
