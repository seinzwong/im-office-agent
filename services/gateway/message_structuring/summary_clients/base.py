from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SummaryClientRequest:
    task_id: str
    message_id: str
    topic_id: str | None
    topic_title: str | None
    normalized_text: str
    plain_text: str
    importance_score: float
    has_deliverable: bool
    deliverable_signals: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SummaryClientResponse:
    should_add: bool
    summary_item: dict | None = None
    reason: str = ""


class SummaryClient(Protocol):
    def generate(self, req: SummaryClientRequest) -> SummaryClientResponse:
        ...
