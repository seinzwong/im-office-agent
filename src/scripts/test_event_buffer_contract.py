from __future__ import annotations

import time

from message_structuring.event_buffers.memory import MemoryEventBuffer


def main() -> None:
    buffer = MemoryEventBuffer(ttl_seconds=1, max_messages=3)
    chat_id = "oc_buffer_contract_001"

    buffer.append(chat_id, {"e": 1})
    buffer.append(chat_id, {"e": 2})
    assert [x["e"] for x in buffer.get(chat_id)] == [1, 2]

    buffer.append(chat_id, {"e": 3})
    buffer.append(chat_id, {"e": 4})
    assert [x["e"] for x in buffer.get(chat_id)] == [2, 3, 4]

    time.sleep(1.1)
    assert buffer.get(chat_id) == []

    buffer.append(chat_id, {"e": 5})
    buffer.clear(chat_id)
    assert buffer.get(chat_id) == []
    print("Event buffer contract test passed.")


if __name__ == "__main__":
    main()
