from __future__ import annotations

from message_structuring.importance_scorers.base import ImportanceScoreResult, ModelUnavailableError
from message_structuring.importance_scorers.bert import BertImportanceScorer
from message_structuring.importance_scorers.rule import RuleImportanceScorer, _level_from_score
from message_structuring.schemas import NormalizedMessage


class HybridImportanceScorer:
    def __init__(
        self,
        bert_model_path: str,
        bert_device: str = "auto",
        bert_weight: float = 0.6,
        max_length: int = 128,
    ) -> None:
        self.rule = RuleImportanceScorer()
        self.bert_weight = max(0.0, min(float(bert_weight), 1.0))
        self.bert_scorer: BertImportanceScorer | None = None
        self.bert_init_error: str | None = None
        try:
            self.bert_scorer = BertImportanceScorer(
                model_path=bert_model_path,
                device=bert_device,
                max_length=max_length,
            )
        except ModelUnavailableError as exc:
            self.bert_init_error = str(exc)

    def score(self, message: NormalizedMessage) -> ImportanceScoreResult:
        rule_res = self.rule.score(message)
        if self.bert_scorer is None:
            reason = f"backend=hybrid fallback_to_rule because bert_unavailable: {self.bert_init_error}"
            return ImportanceScoreResult(
                score=rule_res.score,
                level=rule_res.level,
                reason=reason,
                matched_signals=rule_res.matched_signals,
                backend="hybrid",
                rule_score=rule_res.score,
                bert_score=None,
            )

        try:
            bert_res = self.bert_scorer.score(message)
            final_score = self.bert_weight * bert_res.score + (1.0 - self.bert_weight) * rule_res.score
            final_score = max(0.0, min(final_score, 1.0))
            return ImportanceScoreResult(
                score=final_score,
                level=_level_from_score(final_score),
                reason=(
                    f"backend=hybrid rule_score={rule_res.score:.4f} "
                    f"bert_score={bert_res.score:.4f} bert_weight={self.bert_weight:.2f}"
                ),
                matched_signals=rule_res.matched_signals,
                backend="hybrid",
                rule_score=rule_res.score,
                bert_score=bert_res.score,
            )
        except Exception as exc:
            reason = f"backend=hybrid fallback_to_rule because bert_error: {exc}"
            return ImportanceScoreResult(
                score=rule_res.score,
                level=rule_res.level,
                reason=reason,
                matched_signals=rule_res.matched_signals,
                backend="hybrid",
                rule_score=rule_res.score,
                bert_score=None,
            )
