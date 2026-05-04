from __future__ import annotations

import json
from pathlib import Path

from message_structuring.preprocessor import parse_feishu_event


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    payload = json.loads((root / "messsage_example.json").read_text(encoding="utf-8"))
    normalized = parse_feishu_event(payload, task_id="task_000001")
    print(normalized.model_dump_json(indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
