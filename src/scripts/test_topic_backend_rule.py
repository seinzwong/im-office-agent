from __future__ import annotations

from message_structuring.components.topic_tracker import TopicTracker
from message_structuring.config import MessageStructuringConfig
from message_structuring.schemas import MessageContent, NormalizedMessage


def _msg(mid: str, text: str, thread: str, root: str, has_image: bool = False, has_mention: bool = False) -> NormalizedMessage:
    m = NormalizedMessage(message_id=mid, task_id="task_topic_rule", chat_id="chat_topic_rule", timestamp_ms=1)
    m.thread_id = thread
    m.root_id = root
    m.parent_id = root
    m.content = MessageContent(normalized_text=text, plain_text=text.replace("@Tom", "").strip(), raw_content="{}", content_parse_status="ok")
    m.features.has_image = has_image
    m.features.has_mention = has_mention
    return m


def main() -> None:
    tracker = TopicTracker(config=MessageStructuringConfig(topic_backend="rule"))
    msgs = [
        _msg("m1", "线上登录接口 500 了，用户无法登录", "th_login", "root_login"),
        _msg("m2", "我来处理，先回滚上一版", "th_login", "root_login"),
        _msg("m3", "后续我排查 token 服务", "th_login", "root_login"),
        _msg("m4", "明天下午同步修复结果", "th_login", "root_login"),
    ]
    topic_ids = []
    for m in msgs:
        ann, _ = tracker.process(m)
        assert ann.status.value == "done"
        topic_ids.append(ann.topic_id)

    assert len(set(topic_ids)) <= 2, f"expected merged topics, got {topic_ids}"

    noise = _msg("m5", "@Tom hello", "th_noise", "root_noise", has_mention=True)
    ann_noise, _ = tracker.process(noise)
    assert ann_noise.status.value == "skipped"
    print("Topic backend rule test passed.")


if __name__ == "__main__":
    main()
