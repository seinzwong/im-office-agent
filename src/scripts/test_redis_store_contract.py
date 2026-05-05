from __future__ import annotations

import os

from message_structuring.schemas import NormalizedMessage
from message_structuring.stores.redis_store import RedisStructuringStore


def main() -> None:
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    prefix = os.getenv("REDIS_KEY_PREFIX", "msl")

    try:
        store = RedisStructuringStore(redis_url=redis_url, key_prefix=prefix)
        store.client.ping()
    except Exception as exc:
        print(f"SKIP: Redis unavailable ({exc})")
        return

    task = store.start_task(chat_id="oc_redis_contract_001", activation_source="test")
    assert store.get_active_task("oc_redis_contract_001") is not None

    msg = NormalizedMessage(
        message_id="m1",
        task_id=task.task_id,
        chat_id="oc_redis_contract_001",
        timestamp_ms=1,
    )
    msg.dedup.dedup_key = "feishu:m1:1"
    store.append_message(task.task_id, msg)
    assert len(store.get_messages(task.task_id)) == 1

    dup_msg = NormalizedMessage(
        message_id="m1",
        task_id=task.task_id,
        chat_id="oc_redis_contract_001",
        timestamp_ms=2,
    )
    dup_msg.dedup.dedup_key = "feishu:m1:1"
    dup = store.append_message(task.task_id, dup_msg)
    assert dup.dedup.is_duplicate is True

    stopped = store.stop_task(task.task_id, reason="test")
    assert stopped is not None and stopped.status == "stopped"
    print("Redis store contract test passed.")


if __name__ == "__main__":
    main()
