from __future__ import annotations

import json
from pathlib import Path

from message_structuring.components.topic_tracker import TopicTracker
from message_structuring.config import MessageStructuringConfig
from message_structuring.schemas import MessageContent, NormalizedMessage


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def main() -> None:
    model_path = Path("models/topic_embedding")
    if not model_path.exists():
        print(f"SKIP: local embedding model not found at {model_path}")
        return

    dataset_path = Path("data/fixtures/topic_eval/topic_eval_zh.jsonl")
    if not dataset_path.exists():
        print(f"SKIP: dataset not found at {dataset_path}")
        return

    cfg = MessageStructuringConfig(
        topic_backend="embedding",
        topic_embedding_model_path=str(model_path),
    )
    tracker = TopicTracker(config=cfg)

    records = _load_jsonl(dataset_path)
    task_id = "task_topic_embed_eval"
    topic_ids = set()
    skipped = 0
    total = 0
    for idx, row in enumerate(records):
        event = row["event"]
        msg = event["event"]["message"]
        text = msg.get("content", "")
        try:
            parsed = json.loads(text)
            if msg.get("message_type") == "text":
                normalized_text = str(parsed.get("text", ""))
            elif msg.get("message_type") == "post":
                normalized_text = str(parsed.get("title", "")) + " " + str(parsed.get("content", ""))
            elif msg.get("message_type") == "image":
                normalized_text = "[image]"
            else:
                normalized_text = str(parsed)
        except Exception:
            normalized_text = text

        message = NormalizedMessage(
            message_id=msg.get("message_id", f"m{idx}"),
            task_id=task_id,
            chat_id=msg.get("chat_id", "chat"),
            timestamp_ms=idx + 1,
        )
        message.thread_id = msg.get("thread_id")
        message.root_id = msg.get("root_id")
        message.parent_id = msg.get("parent_id")
        message.content = MessageContent(
            normalized_text=normalized_text,
            plain_text=normalized_text,
            raw_content=text,
            content_parse_status="ok",
        )
        message.features.has_image = msg.get("message_type") == "image"
        message.features.has_mention = "@_user_" in normalized_text or "@Tom" in normalized_text

        ann, _ = tracker.process(message)
        total += 1
        if ann.status.value == "skipped":
            skipped += 1
        if ann.topic_id:
            topic_ids.add(ann.topic_id)

    metrics = {
        "total_messages": total,
        "skipped_messages": skipped,
        "topic_count": len(topic_ids),
    }
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print("Topic embedding eval test passed.")


if __name__ == "__main__":
    main()
