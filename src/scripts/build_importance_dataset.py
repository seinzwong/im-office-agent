from __future__ import annotations

import json
import random
from pathlib import Path


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    out_dir = root / "data" / "importance"
    out_dir.mkdir(parents=True, exist_ok=True)

    samples: list[dict] = []

    fixture_scores = {
        "feishu_text_important.json": 0.85,
        "feishu_post_message.json": 0.6,
        "feishu_image_message.json": 0.08,
        "feishu_malformed_content.json": 0.2,
    }
    for name, score in fixture_scores.items():
        payload = _load_json(root / "data" / "fixtures" / name)
        if not payload:
            continue
        text = payload.get("event", {}).get("message", {}).get("content", "")
        samples.append({"text": text, "label": score, "source": name})

    topic_eval_rows = _load_jsonl(root / "data" / "fixtures" / "topic_eval" / "topic_eval_zh.jsonl")
    for row in topic_eval_rows:
        expected = row.get("expected", {})
        event = row.get("event", {})
        msg = event.get("event", {}).get("message", {})
        raw_content = msg.get("content", "")
        should_formal = bool(expected.get("should_create_formal_topic"))
        topic_key = expected.get("topic_key", "")
        if should_formal:
            label = 0.75
            if topic_key in {"login_incident", "api_migration", "redis_removal"}:
                label = 0.85
        else:
            label = 0.08
        samples.append(
            {
                "text": raw_content,
                "label": label,
                "source": f"topic_eval:{topic_key}",
            }
        )

    handcrafted = [
        ("线上登录接口 500 了，用户无法登录", 0.9),
        ("我来处理，先回滚上一版", 0.8),
        ("明天 18:00 前交付 PRD 初稿", 0.88),
        ("收到", 0.05),
        ("好的", 0.05),
        ("@Tom hello", 0.08),
        ("本周评审 API migration 风险", 0.78),
        ("RAG 摘要模块需要补充 rerank", 0.74),
        ("会议改到周四 16:00", 0.62),
        ("哈哈哈", 0.02),
        ("请把方案文档今晚发我邮箱", 0.8),
        ("token 服务延迟升高，继续排查", 0.82),
        ("目前进展顺利，预计明天完成", 0.68),
        ("ok", 0.03),
    ]
    for text, label in handcrafted:
        samples.append({"text": text, "label": float(label), "source": "handcrafted"})

    random.Random(42).shuffle(samples)
    n = len(samples)
    train_end = int(n * 0.8)
    dev_end = int(n * 0.9)
    splits = {
        "train.jsonl": samples[:train_end],
        "dev.jsonl": samples[train_end:dev_end],
        "test.jsonl": samples[dev_end:],
    }

    for name, rows in splits.items():
        with (out_dir / name).open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Built dataset at {out_dir} (train={len(splits['train.jsonl'])}, dev={len(splits['dev.jsonl'])}, test={len(splits['test.jsonl'])})")


if __name__ == "__main__":
    main()
