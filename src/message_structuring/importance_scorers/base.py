from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from message_structuring.schemas import NormalizedMessage


@dataclass(frozen=True)
class ImportanceScoreResult:
    score: float
    level: str
    reason: str
    matched_signals: list[str] = field(default_factory=list)
    backend: str = "rule"
    rule_score: float | None = None
    bert_score: float | None = None


class ModelUnavailableError(RuntimeError):
    """Raised when requested local model/backend resources are unavailable."""


class BaseImportanceScorer(Protocol):
    def score(self, message: NormalizedMessage) -> ImportanceScoreResult:
        ...
