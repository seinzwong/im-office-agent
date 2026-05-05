from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .components.deliverables import DeliverableExtractor
from .components.importance import SummaryCandidateSelector
from .components.topic_tracker import TopicTracker
from .config import MessageStructuringConfig, load_config_from_env
from .event_buffers.memory import MemoryEventBuffer
from .event_buffers.redis_buffer import RedisEventBuffer
from .preprocessor import parse_feishu_event
from .schemas import (
    AnnotationStatus,
    QualityMetrics,
    StructuringResult,
    SummaryItem,
    SummarySection,
    TaskBrief,
    TaskSession,
    TopicNode,
)
from .summary_clients.stub import StubSummaryClient
from .summary_clients.teammate_http import TeammateHTTPSummaryClient
from .summary_updater import IncrementalSummaryUpdater
from .stores.memory import MemoryStructuringStore
from .stores.redis_store import RedisStructuringStore


class MessageStructuringOrchestrator:
    def __init__(self, config: MessageStructuringConfig | None = None) -> None:
        self.config = config or load_config_from_env()
        self.store = self._build_store()
        self.event_buffer = self._build_event_buffer()
        self.importance_component = SummaryCandidateSelector(config=self.config)
        self.deliverable_component = DeliverableExtractor(llm_client=None)
        self.topic_tracker = TopicTracker(config=self.config)
        self.summary_updater = IncrementalSummaryUpdater(
            summary_client=self._build_summary_client(),
            importance_threshold=self.config.summary_importance_threshold,
        )
        self._task_briefs: dict[str, TaskBrief] = {}
        self._topic_pending_messages: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        self._summary_counter = 0

    def _build_store(self):
        if self.config.store_backend == "redis":
            try:
                return RedisStructuringStore(
                    redis_url=self.config.redis_url,
                    key_prefix=self.config.redis_key_prefix,
                )
            except Exception:
                return MemoryStructuringStore()
        return MemoryStructuringStore()

    def _build_event_buffer(self):
        if self.config.store_backend == "redis":
            try:
                return RedisEventBuffer(
                    redis_url=self.config.redis_url,
                    key_prefix=self.config.redis_key_prefix,
                    ttl_seconds=self.config.redis_buffer_ttl_seconds,
                    max_messages=self.config.redis_max_buffer_messages,
                )
            except Exception:
                return MemoryEventBuffer(
                    ttl_seconds=self.config.redis_buffer_ttl_seconds,
                    max_messages=self.config.redis_max_buffer_messages,
                )
        return MemoryEventBuffer(
            ttl_seconds=self.config.redis_buffer_ttl_seconds,
            max_messages=self.config.redis_max_buffer_messages,
        )

    def _build_summary_client(self):
        if self.config.summary_client_mode == "http":
            return TeammateHTTPSummaryClient(
                base_url=self.config.teammate_summary_base_url,
                api_key=self.config.teammate_summary_api_key,
                timeout_seconds=self.config.teammate_summary_timeout_seconds,
            )
        return StubSummaryClient()

    def start_task(self, chat_id: str, activation_source: str = "manual_api", task_title: str | None = None) -> TaskSession:
        task = self.store.start_task(chat_id, activation_source=activation_source, task_title=task_title)
        self._task_briefs[task.task_id] = TaskBrief(goal=task_title or "")
        return task

    def stop_task(self, task_id: str, reason: str = "manual") -> TaskSession | None:
        return self.store.stop_task(task_id, reason=reason)

    def get_task(self, task_id: str) -> TaskSession | None:
        return self.store.get_task(task_id)

    def set_activation_state(self, chat_id: str, active: bool, task_title: str | None = None) -> dict:
        if active:
            existing = self.store.get_active_task(chat_id)
            if existing is not None:
                return {"status": "already_active", "task": existing.model_dump(mode="json")}
            task = self.start_task(chat_id=chat_id, activation_source="activation_state", task_title=task_title)
            return {"status": "activated", "task": task.model_dump(mode="json")}

        existing = self.store.get_active_task(chat_id)
        if existing is None:
            return {"status": "already_inactive", "chat_id": chat_id}
        stopped = self.stop_task(existing.task_id, reason="activation_off")
        return {"status": "deactivated", "task": stopped.model_dump(mode="json") if stopped else None}

    def process_feishu_event(self, raw_event: dict) -> dict:
        chat_id = ((raw_event.get("event") or {}).get("message") or {}).get("chat_id")
        if not chat_id:
            return {"status": "ignored", "reason": "missing_chat_id"}

        task = self.store.get_active_task(chat_id)
        if task is None:
            self.event_buffer.append(chat_id, raw_event)
            return {"status": "ignored", "reason": "no_active_task", "chat_id": chat_id}

        message = parse_feishu_event(raw_event, task_id=task.task_id)
        self.store.append_message(task.task_id, message)
        if message.dedup.is_duplicate:
            return {"status": "duplicate", "message": message.model_dump(mode="json")}

        message.annotations.importance.status = AnnotationStatus.RUNNING
        message.annotations.deliverables.status = AnnotationStatus.RUNNING
        message.annotations.topic.status = AnnotationStatus.RUNNING

        topic_node = None

        def _run_importance():
            return self.importance_component.process(message)

        def _run_deliverables():
            return self.deliverable_component.process(message)

        def _run_topic():
            return self.topic_tracker.process(message)

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                "importance": pool.submit(_run_importance),
                "deliverables": pool.submit(_run_deliverables),
                "topic": pool.submit(_run_topic),
            }
            for component_name, future in futures.items():
                try:
                    result = future.result()
                    if component_name == "topic":
                        topic_annotation, topic_node = result
                        self.store.update_annotation(task.task_id, message.message_id, "topic", topic_annotation)
                    else:
                        self.store.update_annotation(task.task_id, message.message_id, component_name, result)
                except Exception as exc:
                    annotation = getattr(message.annotations, component_name)
                    annotation.status = AnnotationStatus.ERROR
                    annotation.reason = f"{component_name} failed: {exc}"
                    self.store.update_annotation(task.task_id, message.message_id, component_name, annotation)

        topics_for_store = self.topic_tracker.get_topics(task.task_id)
        self.store.set_topics(task.task_id, topics_for_store)
        stored_message = self.store.get_message(task.task_id, message.message_id) or message
        self._apply_summary_for_message(task.task_id, stored_message)

        return {
            "status": "processed",
            "task_id": task.task_id,
            "message": (self.store.get_message(task.task_id, message.message_id) or stored_message).model_dump(mode="json"),
            "topic_updated": topic_node is not None,
        }

    def process_feishu_events_batch(self, events: list[dict]) -> dict:
        results = [self.process_feishu_event(event) for event in events]
        return {"count": len(events), "results": results}

    def _apply_summary_for_message(self, task_id: str, message) -> None:
        statuses = [
            message.annotations.importance.status,
            message.annotations.deliverables.status,
            message.annotations.topic.status,
        ]
        completed_statuses = {AnnotationStatus.DONE, AnnotationStatus.SKIPPED}
        if all(status in completed_statuses for status in statuses):
            topics = self.topic_tracker.get_topics(task_id)
            did_update = False
            if message.annotations.topic.status == AnnotationStatus.DONE and message.annotations.topic.topic_id:
                did_update = self._maybe_update_topic_summary(task_id, message, topics) or did_update
            if bool(message.annotations.deliverables.has_deliverable):
                did_update = self._update_task_summary(task_id, message, topics) or did_update

            if did_update:
                message.annotations.summary.status = AnnotationStatus.DONE
                message.annotations.summary.reason = "agent summary state updated"
            else:
                message.annotations.summary.status = AnnotationStatus.NOT_SELECTED
                message.annotations.summary.reason = "waiting for topic batch of 5 or deliverable trigger"
            self.store.update_annotation(task_id, message.message_id, "summary", message.annotations.summary)
        else:
            message.annotations.summary.status = AnnotationStatus.SKIPPED
            message.annotations.summary.reason = "skipped due to upstream component failure"
            self.store.update_annotation(task_id, message.message_id, "summary", message.annotations.summary)

    def reprocess_summary(self, task_id: str) -> dict:
        task = self.store.get_task(task_id)
        if task is None:
            return {"status": "not_found", "task_id": task_id}

        self.store.clear_summary_items(task_id)
        self.summary_updater.reset_seen()
        self._topic_pending_messages[task_id].clear()
        self._task_briefs[task_id] = TaskBrief(goal=task.task_title or "")

        for message in self.store.get_messages(task_id):
            message.annotations.summary.status = AnnotationStatus.NOT_SELECTED
            message.annotations.summary.summary_item_ids = []
            message.annotations.summary.reason = "reprocessing"
            self._apply_summary_for_message(task_id, message)

        return {
            "status": "ok",
            "task_id": task_id,
            "summary_count": len(self.store.get_summary_items(task_id)),
        }

    def get_messages(self, task_id: str) -> list[dict] | dict:
        task = self.store.get_task(task_id)
        if task is None:
            return {"status": "not_found", "task_id": task_id}
        return [m.model_dump(mode="json") for m in self.store.get_messages(task_id)]

    def get_topics(self, task_id: str) -> list[dict] | dict:
        task = self.store.get_task(task_id)
        if task is None:
            return {"status": "not_found", "task_id": task_id}
        return [t.model_dump(mode="json") for t in self.store.get_topics(task_id)]

    def get_summary(self, task_id: str) -> dict:
        task = self.store.get_task(task_id)
        if task is None:
            return {"status": "not_found", "task_id": task_id}
        return {"items": [item.model_dump(mode="json") for item in self.store.get_summary_items(task_id)]}

    def get_result(self, task_id: str) -> dict:
        task = self.store.get_task(task_id)
        if task is None:
            return {"status": "not_found", "task_id": task_id}

        messages = self.store.get_messages(task_id)
        topics = self.store.get_topics(task_id)
        summary_items = self.store.get_summary_items(task_id)

        errors: list[str] = []
        for msg in messages:
            for name in ("importance", "deliverables", "topic", "summary"):
                ann = getattr(msg.annotations, name)
                if ann.status == AnnotationStatus.ERROR:
                    errors.append(f"{msg.message_id}:{name}:{ann.reason}")

        processed_messages = self.store.get_processed_messages(task_id)
        deliverables = _derive_deliverables(messages)
        brief = self._task_briefs.get(task_id) or TaskBrief(goal=task.task_title or "")
        brief.deliverables = deliverables or brief.deliverables
        if not brief.goal:
            brief.goal = task.task_title or ""
        if summary_items and not brief.confidence:
            brief.confidence = round(sum(item.confidence for item in summary_items) / len(summary_items), 4)
        quality = QualityMetrics(
            total_messages=len(messages),
            processed_messages=len(processed_messages),
            summary_candidate_count=len(summary_items),
            topic_count=len(topics),
            errors=errors,
        )

        result = StructuringResult(
            task=task,
            task_brief=brief,
            summary=SummarySection(items=summary_items),
            topics=topics,
            messages=messages,
            quality=quality,
        )
        return result.model_dump(mode="json")

    def _maybe_update_topic_summary(self, task_id: str, message, topics: list[TopicNode]) -> bool:
        topic_id = message.annotations.topic.topic_id
        if not topic_id:
            return False
        topic = _find_topic(topics, topic_id)
        if topic is None:
            return False
        pending = self._topic_pending_messages[task_id][topic_id]
        pending.append(message)
        if len(pending) < 5:
            return False

        batch = list(pending[:5])
        del pending[:5]
        result = self.summary_updater.update_topic_summary(task_id, topic, batch)
        update = result.get("topic_update") if isinstance(result, dict) else None
        if not isinstance(update, dict):
            message.annotations.summary.status = AnnotationStatus.ERROR
            message.annotations.summary.reason = "agent topic summary update returned invalid payload"
            return False

        topic.summary = str(update.get("new_summary") or topic.summary or "").strip()
        self._add_agent_evidence(task_id, topic_id, update.get("evidence_candidates"), batch)
        self.store.set_topics(task_id, topics)
        return True

    def _update_task_summary(self, task_id: str, message, topics: list[TopicNode]) -> bool:
        task = self.store.get_task(task_id)
        if task is None:
            return False
        old_brief = self._task_briefs.get(task_id) or TaskBrief(goal=task.task_title or "")
        old_task = {
            "task_id": task.task_id,
            "title": task.task_title or task.display_name,
            "summary": old_brief.summary,
            "goal": old_brief.goal or task.task_title or "",
            "deliverables": old_brief.deliverables or _derive_deliverables(self.store.get_messages(task_id)),
            "deadline": old_brief.deadline,
            "status": old_brief.status,
            "confidence": old_brief.confidence,
        }
        result = self.summary_updater.update_task_summary(
            old_task=old_task,
            topic_summaries=topics,
            trigger_messages=[message],
            signals=_task_summary_signals(message),
        )
        update = result.get("task_update") if isinstance(result, dict) else None
        if not isinstance(update, dict):
            message.annotations.summary.status = AnnotationStatus.ERROR
            message.annotations.summary.reason = "agent task summary update returned invalid payload"
            return False

        self._task_briefs[task_id] = TaskBrief(
            summary=str(update.get("new_summary") or old_brief.summary or "").strip(),
            goal=str(update.get("goal") or old_brief.goal or task.task_title or "").strip(),
            deliverables=[str(item) for item in update.get("deliverables") or old_brief.deliverables or []],
            deadline=update.get("deadline") or old_brief.deadline,
            status=str(update.get("status") or old_brief.status or "collecting"),
            confidence=_clamp_confidence(update.get("confidence"), old_brief.confidence),
        )
        self._add_message_evidence(task_id, message)
        return True

    def _add_agent_evidence(self, task_id: str, topic_id: str | None, candidates: Any, fallback_messages: list) -> None:
        for candidate in candidates if isinstance(candidates, list) else []:
            if not isinstance(candidate, dict):
                continue
            claim = str(candidate.get("claim") or "").strip()
            if not claim:
                continue
            self._summary_counter += 1
            self.store.add_summary_item(
                task_id,
                SummaryItem(
                    summary_item_id=f"sum_{self._summary_counter:06d}",
                    topic_id=candidate.get("topic_id") or topic_id,
                    text=claim,
                    source_message_ids=[str(item) for item in candidate.get("source_message_ids") or []],
                    confidence=_clamp_confidence(candidate.get("confidence"), 0.6),
                ),
            )
        if not isinstance(candidates, list) or not candidates:
            for fallback in fallback_messages:
                self._add_message_evidence(task_id, fallback)

    def _add_message_evidence(self, task_id: str, message) -> None:
        text = str(message.content.normalized_text or message.content.plain_text or "").strip()
        if not text:
            return
        self._summary_counter += 1
        self.store.add_summary_item(
            task_id,
            SummaryItem(
                summary_item_id=f"sum_{self._summary_counter:06d}",
                topic_id=message.annotations.topic.topic_id,
                text=text,
                source_message_ids=[message.message_id],
                confidence=_clamp_confidence(message.annotations.importance.score, 0.6),
            ),
        )


def _derive_deliverables(messages) -> list[str]:
    """Derive coarse deliverable labels from message annotations and text."""
    labels: list[str] = []
    for message in messages:
        text = (message.content.normalized_text or "").lower()
        signals = set(message.annotations.deliverables.matched_signals or [])
        if "deliverable_doc" in signals or "prd" in text or "方案" in text or "文档" in text:
            labels.append("方案文档")
        if "ppt" in text or "评审" in text or "汇报" in text:
            labels.append("评审 PPT")
        if "流程图" in text or "白板" in text or "board" in text:
            labels.append("流程白板")
    output = []
    seen = set()
    for label in labels:
        if label not in seen:
            output.append(label)
            seen.add(label)
    return output


def _find_topic(topics: list[TopicNode], topic_id: str) -> TopicNode | None:
    for topic in topics:
        if topic.topic_id == topic_id:
            return topic
    return None


def _task_summary_signals(message) -> dict[str, Any]:
    deliverables = message.annotations.deliverables
    return {
        "has_deliverable": bool(deliverables.has_deliverable),
        "deliverable_signals": list(deliverables.matched_signals or []),
        "importance_score": message.annotations.importance.score,
        "maybe_deadline": _extract_deadline_hint(message.content.normalized_text or ""),
    }


def _extract_deadline_hint(text: str) -> str:
    for token in ("今天", "明天", "后天", "本周", "下周", "截止", "deadline", "ddl"):
        if token.lower() in text.lower():
            return token
    return ""


def _clamp_confidence(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        number = float(default or 0.0)
    return max(0.0, min(1.0, round(number, 4)))
