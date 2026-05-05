from __future__ import annotations

import os
from typing import Any

import httpx

from services.gateway.config import get_settings
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

    def update_topic_summary(self, task_id: str, topic: TopicNode, new_messages: list[NormalizedMessage]) -> dict:
        payload = {
            "update_type": "topic_summary",
            "payload": {
                "task_id": task_id,
                "topic": topic.model_dump(mode="json"),
                "new_messages": [message.model_dump(mode="json") for message in new_messages],
            },
            "options": {"language": "zh-CN"},
        }
        return _call_agent_structuring_summary(payload)

    def update_task_summary(
        self,
        old_task: dict[str, Any],
        topic_summaries: list[TopicNode],
        trigger_messages: list[NormalizedMessage],
        signals: dict[str, Any],
    ) -> dict:
        payload = {
            "update_type": "task_summary",
            "payload": {
                "task": old_task,
                "topic_summaries": [topic.model_dump(mode="json") for topic in topic_summaries],
                "trigger_messages": [message.model_dump(mode="json") for message in trigger_messages],
                "signals": signals,
            },
            "options": {"language": "zh-CN"},
        }
        return _call_agent_structuring_summary(payload)


def _call_agent_structuring_summary(payload: dict[str, Any]) -> dict:
    base_url = (
        os.environ.get("AGENT_SUMMARY_BASE_URL")
        or os.environ.get("AGENTS_BASE_URL")
        or get_settings().agents_base_url
        or "http://127.0.0.1:8001"
    ).strip()
    timeout_seconds = float(os.environ.get("AGENT_SUMMARY_TIMEOUT_SECONDS") or "120")
    url = base_url.rstrip("/") + "/agent/update-structuring-summary"
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(url, json=payload, headers={"Content-Type": "application/json"})
            response.raise_for_status()
            parsed = response.json()
    except Exception as exc:
        return {
            "error": {
                "code": "AGENT_SUMMARY_HTTP_FAILED",
                "message": str(exc),
                "details": {"url": url},
            }
        }
    return parsed if isinstance(parsed, dict) else {
        "error": {
            "code": "AGENT_SUMMARY_INVALID_RESPONSE",
            "message": "Agent summary response must be a JSON object.",
            "details": {"url": url, "response_type": type(parsed).__name__},
        }
    }
