from __future__ import annotations

from message_structuring.schemas import AnnotationStatus, NormalizedMessage, SummaryItem, TopicNode


class IncrementalSummaryUpdater:
    def __init__(self, topic_update_threshold: int = 4) -> None:
        self.topic_update_threshold = topic_update_threshold
        self._counter = 0
        self._message_seen: set[str] = set()

    def maybe_update(self, message: NormalizedMessage, topics: list[TopicNode]) -> SummaryItem | None:
        if message.message_id in self._message_seen:
            return None

        importance = message.annotations.importance
        deliverables = message.annotations.deliverables
        topic_ann = message.annotations.topic

        matched = set(importance.matched_signals or [])
        strong_keys = {"decision", "deadline", "action_item", "risk"}
        topic_update_hit = False
        topic_id = topic_ann.topic_id
        if topic_id:
            for topic in topics:
                if topic.topic_id == topic_id and topic.update_count >= self.topic_update_threshold:
                    topic_update_hit = True
                    break

        should_update = any(
            [
                (importance.score or 0.0) >= 0.75,
                (importance.score or 0.0) >= 0.45 and bool(deliverables.has_deliverable),
                len(deliverables.items) >= 3,
                bool(matched & strong_keys),
                topic_update_hit,
            ]
        )

        if not should_update:
            message.annotations.summary.status = AnnotationStatus.NOT_SELECTED
            message.annotations.summary.reason = "update condition not met"
            return None

        self._counter += 1
        self._message_seen.add(message.message_id)
        summary_item_id = f"sum_{self._counter:06d}"
        topic_title = topic_ann.topic_title or "misc"
        text = f"[{topic_title}] {message.content.normalized_text}".strip()
        confidence = min(
            1.0,
            max(
                0.1,
                (importance.score or 0.0) * 0.7 + (0.2 if deliverables.has_deliverable else 0.0) + (0.1 if topic_update_hit else 0.0),
            ),
        )

        item = SummaryItem(
            summary_item_id=summary_item_id,
            topic_id=topic_id,
            text=text,
            source_message_ids=[message.message_id],
            confidence=round(confidence, 4),
        )
        message.annotations.summary.status = AnnotationStatus.DONE
        message.annotations.summary.summary_item_ids = [summary_item_id]
        message.annotations.summary.reason = "summary item generated"
        return item
