from __future__ import annotations

from message_structuring.components.importance import SummaryCandidateSelector
from message_structuring.config import MessageStructuringConfig
from message_structuring.schemas import MessageContent, NormalizedMessage


def _msg(text: str, has_mention: bool = False, has_file: bool = False) -> NormalizedMessage:
    m = NormalizedMessage(message_id="m", task_id="t", chat_id="c", timestamp_ms=1)
    m.content = MessageContent(
        normalized_text=text,
        plain_text=text,
        raw_content="{}",
        content_parse_status="ok",
    )
    m.features.has_mention = has_mention
    m.features.has_file = has_file
    return m


def main() -> None:
    cfg = MessageStructuringConfig(importance_backend="rule")
    selector = SummaryCandidateSelector(config=cfg)

    high = _msg(
        "Decision: API rollout deadline is tomorrow 18:00. Please finish PRD and send update to dev-team@example.com.",
        has_mention=True,
        has_file=True,
    )
    high_ann = selector.process(high)
    assert high_ann.status.value == "done"
    assert high_ann.score is not None and high_ann.score >= 0.45

    low = _msg("收到")
    low_ann = selector.process(low)
    assert low_ann.status.value == "done"
    assert low_ann.score is not None and low_ann.score <= 0.2
    print("Importance backend rule test passed.")


if __name__ == "__main__":
    main()
