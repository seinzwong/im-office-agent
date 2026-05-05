from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from services.gateway.message_structuring.schemas import NormalizedMessage, TopicNode


@dataclass(frozen=True)
class TopicAssignResult:
    status: Literal["done", "skipped", "error"]
    topic_id: str | None
    topic_title: str | None
    similarity: float | None
    reason: str
    backend: str
    matched_signals: list[str] = field(default_factory=list)


class ModelUnavailableError(RuntimeError):
    """Raised when local topic model files or dependencies are unavailable."""


class BaseTopicBackend(Protocol):
    def assign(
        self,
        message: NormalizedMessage,
        task_state: dict | None = None,
        topics: list[TopicNode] | None = None,
        context: dict | None = None,
    ) -> tuple[TopicAssignResult, TopicNode | None]:
        ...

    def get_topics(self, task_id: str) -> list[TopicNode]:
        ...
