from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock


class MemoryEventBuffer:
    def __init__(self, ttl_seconds: int = 300, max_messages: int = 1000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_messages = max_messages
        self._lock = Lock()
        self._buffers: dict[str, list[tuple[float, dict]]] = defaultdict(list)

    def append(self, chat_id: str, event: dict) -> None:
        now = time.time()
        with self._lock:
            self._prune_locked(chat_id, now)
            self._buffers[chat_id].append((now, event))
            if len(self._buffers[chat_id]) > self.max_messages:
                self._buffers[chat_id] = self._buffers[chat_id][-self.max_messages :]

    def get(self, chat_id: str) -> list[dict]:
        now = time.time()
        with self._lock:
            self._prune_locked(chat_id, now)
            return [event for _, event in self._buffers.get(chat_id, [])]

    def clear(self, chat_id: str) -> None:
        with self._lock:
            self._buffers.pop(chat_id, None)

    def _prune_locked(self, chat_id: str, now: float) -> None:
        threshold = now - self.ttl_seconds
        if chat_id not in self._buffers:
            return
        self._buffers[chat_id] = [(ts, evt) for ts, evt in self._buffers[chat_id] if ts >= threshold]
