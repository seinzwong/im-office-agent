from __future__ import annotations

from message_structuring.config import MessageStructuringConfig, load_config_from_env
from message_structuring.importance_scorers import (
    BertImportanceScorer,
    HybridImportanceScorer,
    ModelUnavailableError,
    RuleImportanceScorer,
)
from message_structuring.schemas import AnnotationStatus, ImportanceAnnotation, NormalizedMessage


class SummaryCandidateSelector:
    def __init__(self, config: MessageStructuringConfig | None = None) -> None:
        self.config = config or load_config_from_env()
        self.backend = self.config.importance_backend
        self.scorer = self._build_scorer()

    def _build_scorer(self):
        if self.backend == "bert":
            return BertImportanceScorer(
                model_path=self.config.importance_bert_model_path,
                device=self.config.importance_bert_device,
                max_length=self.config.importance_max_length,
            )
        if self.backend == "hybrid":
            return HybridImportanceScorer(
                bert_model_path=self.config.importance_bert_model_path,
                bert_device=self.config.importance_bert_device,
                bert_weight=self.config.importance_hybrid_bert_weight,
                max_length=self.config.importance_max_length,
            )
        return RuleImportanceScorer()

    def process(self, message: NormalizedMessage) -> ImportanceAnnotation:
        try:
            result = self.scorer.score(message)
        except ModelUnavailableError:
            raise
        except Exception as exc:
            return ImportanceAnnotation(
                status=AnnotationStatus.ERROR,
                score=None,
                level=None,
                reason=f"importance scoring failed: {exc}",
                matched_signals=[],
            )

        reason = result.reason
        if f"backend={result.backend}" not in reason:
            reason = f"backend={result.backend}; {reason}"
        return ImportanceAnnotation(
            status=AnnotationStatus.DONE,
            score=max(0.0, min(float(result.score), 1.0)),
            level=result.level if result.level in {"noise", "low", "medium", "high"} else "low",
            reason=reason,
            matched_signals=list(result.matched_signals),
        )
