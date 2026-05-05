from __future__ import annotations

from .base import SummaryClientRequest, SummaryClientResponse


class StubSummaryClient:
    def generate(self, req: SummaryClientRequest) -> SummaryClientResponse:
        if not req.has_deliverable:
            return SummaryClientResponse(should_add=False, reason="deliverable is false")

        text = req.normalized_text.strip()
        if not text:
            return SummaryClientResponse(should_add=False, reason="empty text")

        topic_title = req.topic_title or "misc"
        summary_item = {
            "text": f"[{topic_title}] {text}",
            "topic_id": req.topic_id,
            "confidence": round(min(1.0, max(0.1, req.importance_score * 0.8 + 0.2)), 4),
        }
        return SummaryClientResponse(
            should_add=True,
            summary_item=summary_item,
            reason="stub summary generated",
        )
