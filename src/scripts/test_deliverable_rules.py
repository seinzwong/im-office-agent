from __future__ import annotations

from message_structuring.components.deliverables import DeliverableExtractor
from message_structuring.schemas import MessageContent, MentionInfo, NormalizedMessage


def _msg(text: str, *, mentions: list[MentionInfo] | None = None, message_id: str = "m1") -> NormalizedMessage:
    msg = NormalizedMessage(
        message_id=message_id,
        task_id="task_test",
        chat_id="chat_test",
        timestamp_ms=1,
    )
    msg.content = MessageContent(
        normalized_text=text,
        plain_text=text.replace("@Tom", "").strip(),
        raw_content="{}",
        content_parse_status="ok",
    )
    msg.mentions = mentions or []
    msg.features.has_mention = len(msg.mentions) > 0 or "@Tom" in text
    return msg


def main() -> None:
    detector = DeliverableExtractor()

    m1 = _msg("@Tom hello", mentions=[MentionInfo(name="Tom", key="@_user_1")], message_id="m1")
    a1 = detector.process(m1)
    assert a1.has_deliverable is False, "@Tom hello should be non-deliverable"

    m2 = _msg("@Tom 明天补 PRD", mentions=[MentionInfo(name="Tom", key="@_user_1")], message_id="m2")
    a2 = detector.process(m2)
    assert a2.has_deliverable is True, "@Tom 明天补 PRD should be deliverable"

    m3 = _msg("收到", message_id="m3")
    a3 = detector.process(m3)
    assert a3.has_deliverable is False, "收到 should be non-deliverable"

    m4 = _msg("明天 18:00 前完成方案", message_id="m4")
    a4 = detector.process(m4)
    assert a4.has_deliverable is True, "明天 18:00 前完成方案 should be deliverable"

    print("Deliverable rule tests passed.")


if __name__ == "__main__":
    main()
