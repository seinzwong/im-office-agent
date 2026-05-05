from __future__ import annotations

from pathlib import Path

from message_structuring.components.importance import SummaryCandidateSelector
from message_structuring.config import MessageStructuringConfig
from message_structuring.importance_scorers import ModelUnavailableError
from message_structuring.schemas import MessageContent, NormalizedMessage


def main() -> None:
    model_path = Path("models/importance_bert")
    if not model_path.exists():
        print(f"SKIP: local BERT model not found at {model_path}")
        return

    cfg = MessageStructuringConfig(
        importance_backend="bert",
        importance_bert_model_path=str(model_path),
    )
    try:
        selector = SummaryCandidateSelector(config=cfg)
    except ModelUnavailableError as exc:
        raise RuntimeError(f"Local model path exists but BERT scorer unavailable: {exc}") from exc

    msg = NormalizedMessage(message_id="m1", task_id="t1", chat_id="c1", timestamp_ms=1)
    msg.content = MessageContent(
        normalized_text="线上登录接口 500 了，用户无法登录",
        plain_text="线上登录接口 500 了，用户无法登录",
        raw_content="{}",
        content_parse_status="ok",
    )
    ann = selector.process(msg)
    assert ann.status.value == "done"
    assert ann.score is not None and 0.0 <= ann.score <= 1.0
    print("Importance backend BERT local test passed.")


if __name__ == "__main__":
    main()
