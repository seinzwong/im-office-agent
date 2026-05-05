from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

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
    SummarySection,
    TaskBrief,
    TaskSession,
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
        self.importance_component = SummaryCandidateSelector()
        self.deliverable_component = DeliverableExtractor(llm_client=None)
        self.topic_tracker = TopicTracker()
        self.summary_updater = IncrementalSummaryUpdater(
            summary_client=self._build_summary_client(),
            importance_threshold=self.config.summary_importance_threshold,
        )

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
        return self.store.start_task(chat_id, activation_source=activation_source, task_title=task_title)

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
            summary_item = self.summary_updater.maybe_update(message, topics)
            if summary_item:
                self.store.add_summary_item(task_id, summary_item)
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
        brief = TaskBrief(
            summary=" ".join([item.text for item in summary_items[:3]]).strip(),
            goal=task.task_title or "",
            deliverables=[],
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
