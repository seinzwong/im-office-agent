from __future__ import annotations

from services.gateway.message_structuring.schemas import AnnotationStatus, NormalizedMessage, SummaryItem, TopicNode
from services.gateway.message_structuring.summary_clients.base import SummaryClient, SummaryClientRequest


class IncrementalSummaryUpdater:
    def __init__(self, summary_client: SummaryClient, importance_threshold: float = 0.45) -> None:
        self.summary_client = summary_client
        self.importance_threshold = importance_threshold
        self._counter = 0
        self._message_seen: set[str] = set()

    def maybe_update(self, message: NormalizedMessage, topics: list[TopicNode]) -> SummaryItem | None:
        if message.message_id in self._message_seen:
            return None

        importance_score = float(message.annotations.importance.score or 0.0)
        has_deliverable = bool(message.annotations.deliverables.has_deliverable)
        if importance_score < self.importance_threshold or not has_deliverable:
            message.annotations.summary.status = AnnotationStatus.NOT_SELECTED
            message.annotations.summary.reason = (
                f"threshold_not_met(score={importance_score:.3f},deliverable={has_deliverable})"
            )
            return None

        topic_id = message.annotations.topic.topic_id
        topic_title = message.annotations.topic.topic_title
        topic_map = {topic.topic_id: topic for topic in topics}
        if topic_id and topic_id in topic_map and not topic_title:
            topic_title = topic_map[topic_id].topic_title

        req = SummaryClientRequest(
            task_id=message.task_id,
            message_id=message.message_id,
            topic_id=topic_id,
            topic_title=topic_title,
            normalized_text=message.content.normalized_text,
            plain_text=message.content.plain_text,
            importance_score=importance_score,
            has_deliverable=has_deliverable,
            deliverable_signals=list(message.annotations.deliverables.matched_signals),
        )

        try:
            resp = self.summary_client.generate(req)
        except Exception as exc:
            message.annotations.summary.status = AnnotationStatus.ERROR
            message.annotations.summary.reason = f"summary client failure: {exc}"
            return None

        if not resp.should_add:
            message.annotations.summary.status = AnnotationStatus.NOT_SELECTED
            message.annotations.summary.reason = resp.reason or "summary client rejected"
            return None

        summary_payload = resp.summary_item or {}
        summary_text = str(summary_payload.get("text", "")).strip()
        if not summary_text:
            message.annotations.summary.status = AnnotationStatus.NOT_SELECTED
            message.annotations.summary.reason = "summary client returned empty text"
            return None

        self._counter += 1
        self._message_seen.add(message.message_id)
        summary_item_id = str(summary_payload.get("summary_item_id") or f"sum_{self._counter:06d}")
        confidence = float(summary_payload.get("confidence", min(1.0, max(0.1, importance_score))))
        confidence = min(1.0, max(0.0, confidence))

        item = SummaryItem(
            summary_item_id=summary_item_id,
            topic_id=summary_payload.get("topic_id") or topic_id,
            text=summary_text,
            source_message_ids=list(summary_payload.get("source_message_ids") or [message.message_id]),
            confidence=round(confidence, 4),
        )
        message.annotations.summary.status = AnnotationStatus.DONE
        message.annotations.summary.summary_item_ids = [summary_item_id]
        message.annotations.summary.reason = resp.reason or "summary item generated"
        return item

    def reset_seen(self) -> None:
        self._message_seen.clear()
