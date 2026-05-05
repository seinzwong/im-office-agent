from __future__ import annotations

from pathlib import Path

from services.gateway.message_structuring.importance_scorers.base import ImportanceScoreResult, ModelUnavailableError
from services.gateway.message_structuring.importance_scorers.rule import _level_from_score
from services.gateway.message_structuring.schemas import NormalizedMessage


class BertImportanceScorer:
    def __init__(
        self,
        model_path: str = "models/importance_bert",
        device: str = "auto",
        max_length: int = 128,
    ) -> None:
        self.model_path = Path(model_path)
        self.max_length = max_length
        if not self.model_path.exists():
            raise ModelUnavailableError(
                f"BERT importance model path not found: {self.model_path}. "
                "Run training/download scripts first."
            )

        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except Exception as exc:  # pragma: no cover - env dependent
            raise ModelUnavailableError(
                f"transformers/torch unavailable for BERT importance scorer: {exc}"
            ) from exc

        self.torch = torch
        if device == "auto":
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        elif device in {"cpu", "cuda"}:
            self.device = device
        else:
            self.device = "cpu"
        if self.device == "cuda" and not torch.cuda.is_available():
            self.device = "cpu"

        self.tokenizer = AutoTokenizer.from_pretrained(str(self.model_path), local_files_only=True)
        self.model = AutoModelForSequenceClassification.from_pretrained(str(self.model_path), local_files_only=True)
        self.model.to(self.device)
        self.model.eval()

    def _normalize_score_from_logits(self, logits) -> float:
        torch = self.torch
        value = 0.0
        if logits is None:
            return 0.0
        if logits.ndim == 0:
            value = float(logits.item())
        elif logits.ndim == 1:
            if logits.shape[0] == 1:
                value = float(logits[0].item())
            else:
                probs = torch.softmax(logits, dim=-1)
                value = float(probs[-1].item())
        elif logits.ndim >= 2:
            row = logits[0]
            if row.shape[-1] == 1:
                value = float(row[0].item())
            else:
                probs = torch.softmax(row, dim=-1)
                value = float(probs[-1].item())
        # Regression models can output any real number, clamp for safety.
        if value < 0.0 or value > 1.0:
            # sigmoid normalization for out-of-range regression values
            value = float(torch.sigmoid(torch.tensor(value)).item())
        return max(0.0, min(value, 1.0))

    def score(self, message: NormalizedMessage) -> ImportanceScoreResult:
        text = (message.content.normalized_text or "").strip() or (message.content.plain_text or "").strip()
        if not text:
            return ImportanceScoreResult(
                score=0.0,
                level="noise",
                reason="empty message",
                matched_signals=[],
                backend="bert",
                bert_score=0.0,
            )

        torch = self.torch
        inputs = self.tokenizer(
            text,
            truncation=True,
            max_length=self.max_length,
            padding="max_length",
            return_tensors="pt",
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = self.model(**inputs)
        score = self._normalize_score_from_logits(outputs.logits)
        level = _level_from_score(score)
        return ImportanceScoreResult(
            score=score,
            level=level,
            reason=f"backend=bert score={score:.4f}",
            matched_signals=[],
            backend="bert",
            bert_score=score,
        )
