from __future__ import annotations

from message_structuring.components.importance import SummaryCandidateSelector
from message_structuring.config import MessageStructuringConfig
from message_structuring.schemas import MessageContent, NormalizedMessage


def main() -> None:
    cfg = MessageStructuringConfig(
        importance_backend="hybrid",
        importance_bert_model_path="models/missing_bert",
        importance_hybrid_bert_weight=0.6,
    )
    selector = SummaryCandidateSelector(config=cfg)
    msg = NormalizedMessage(message_id="m1", task_id="t1", chat_id="c1", timestamp_ms=1)
    msg.content = MessageContent(
        normalized_text="API migration deadline tomorrow",
        plain_text="API migration deadline tomorrow",
        raw_content="{}",
        content_parse_status="ok",
    )
    ann = selector.process(msg)
    assert ann.status.value == "done"
    assert ann.score is not None
    assert "fallback_to_rule" in (ann.reason or "")
    print("Importance backend hybrid fallback test passed.")


if __name__ == "__main__":
    main()
