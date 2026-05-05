from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from .components.deliverables import DeliverableExtractor
from .components.importance import SummaryCandidateSelector
from .components.topic_tracker import TopicTracker
from .preprocessor import parse_feishu_event
from .schemas import (
    AnnotationStatus,
    QualityMetrics,
    StructuringResult,
    SummaryItem,
    SummarySection,
    TaskBrief,
    TaskSession,
)
from .summary_updater import IncrementalSummaryUpdater
from .task_manager import InMemoryTaskManager
from .timeline_store import InMemoryTimelineStore


class MessageStructuringOrchestrator:
    def __init__(self) -> None:
        self.task_manager = InMemoryTaskManager()
        self.timeline_store = InMemoryTimelineStore()
        self.importance_component = SummaryCandidateSelector()
        self.deliverable_component = DeliverableExtractor(llm_client=None)
        self.topic_tracker = TopicTracker()
        self.summary_updater = IncrementalSummaryUpdater()
        self._summary_items: dict[str, list[SummaryItem]] = {}
        self._summary_item_ids: dict[str, set[str]] = {}
        self._lock = Lock()

    def start_task(self, chat_id: str, activation_source: str = "manual_api", task_title: str | None = None) -> TaskSession:
        return self.task_manager.start_task(chat_id, activation_source=activation_source, task_title=task_title)

    def stop_task(self, task_id: str, reason: str = "manual") -> TaskSession | None:
        return self.task_manager.stop_task(task_id, reason=reason)

    def process_feishu_event(self, raw_event: dict) -> dict:
        chat_id = ((raw_event.get("event") or {}).get("message") or {}).get("chat_id")
        if not chat_id:
            return {"status": "ignored", "reason": "missing_chat_id"}

        task = self.task_manager.get_active_task(chat_id)
        if task is None:
            return {"status": "ignored", "reason": "no_active_task", "chat_id": chat_id}

        message = parse_feishu_event(raw_event, task_id=task.task_id)
        self.timeline_store.append_message(task.task_id, message)
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
                        self.timeline_store.update_annotation(task.task_id, message.message_id, "topic", topic_annotation)
                    else:
                        self.timeline_store.update_annotation(task.task_id, message.message_id, component_name, result)
                except Exception as exc:
                    annotation = getattr(message.annotations, component_name)
                    annotation.status = AnnotationStatus.ERROR
                    annotation.reason = f"{component_name} failed: {exc}"
                    self.timeline_store.update_annotation(task.task_id, message.message_id, component_name, annotation)

        stored_message = self.timeline_store.get_message(task.task_id, message.message_id) or message

        statuses = [
            stored_message.annotations.importance.status,
            stored_message.annotations.deliverables.status,
            stored_message.annotations.topic.status,
        ]
        completed_statuses = {AnnotationStatus.DONE, AnnotationStatus.SKIPPED}
        if all(status in completed_statuses for status in statuses):
            topics = self.topic_tracker.get_topics(task.task_id)
            summary_item = self.summary_updater.maybe_update(stored_message, topics)
            if summary_item:
                with self._lock:
                    item_ids = self._summary_item_ids.setdefault(task.task_id, set())
                    items = self._summary_items.setdefault(task.task_id, [])
                    if summary_item.summary_item_id not in item_ids:
                        item_ids.add(summary_item.summary_item_id)
                        items.append(summary_item)
            self.timeline_store.update_annotation(task.task_id, stored_message.message_id, "summary", stored_message.annotations.summary)
        else:
            stored_message.annotations.summary.status = AnnotationStatus.SKIPPED
            stored_message.annotations.summary.reason = "skipped due to upstream component failure"
            self.timeline_store.update_annotation(task.task_id, stored_message.message_id, "summary", stored_message.annotations.summary)

        return {
            "status": "processed",
            "task_id": task.task_id,
            "message": (self.timeline_store.get_message(task.task_id, message.message_id) or stored_message).model_dump(mode="json"),
            "topic_updated": topic_node is not None,
        }

    def get_result(self, task_id: str) -> dict:
        task = self.task_manager.get_task(task_id)
        if task is None:
            return {"status": "not_found", "task_id": task_id}

        messages = self.timeline_store.get_messages(task_id)
        topics = self.topic_tracker.get_topics(task_id)
        summary_items = self._summary_items.get(task_id, [])

        errors: list[str] = []
        for msg in messages:
            for name in ("importance", "deliverables", "topic"):
                ann = getattr(msg.annotations, name)
                if ann.status == AnnotationStatus.ERROR:
                    errors.append(f"{msg.message_id}:{name}:{ann.reason}")

        processed_messages = self.timeline_store.get_processed_messages(task_id)
        brief = TaskBrief(
            summary=" ".join([item.text for item in summary_items[:3]]).strip(),
            goal=task.task_title or "",
            deliverables=[
                item.value
                for msg in messages
                for item in msg.annotations.deliverables.items
                if item.label in {"artifact", "task"}
            ][:10],
            confidence=round(
                (sum(item.confidence for item in summary_items) / len(summary_items)) if summary_items else 0.0,
                4,
            ),
        )
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
