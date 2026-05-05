from __future__ import annotations

from typing import Protocol


class EventBuffer(Protocol):
    def append(self, chat_id: str, event: dict) -> None:
        ...

    def get(self, chat_id: str) -> list[dict]:
        ...

    def clear(self, chat_id: str) -> None:
        ...
