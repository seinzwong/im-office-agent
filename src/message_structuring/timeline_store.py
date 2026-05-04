from __future__ import annotations

from threading import Lock
from typing import Any

from .schemas import AnnotationStatus, NormalizedMessage


class InMemoryTimelineStore:
    _ALLOWED_COMPONENTS = {"importance", "deliverables", "topic", "summary"}

    def __init__(self) -> None:
        self._lock = Lock()
        self._messages_by_task: dict[str, dict[str, NormalizedMessage]] = {}
        self._dedup_index: dict[str, set[str]] = {}

    def append_message(self, task_id: str, message: NormalizedMessage) -> NormalizedMessage:
        with self._lock:
            task_messages = self._messages_by_task.setdefault(task_id, {})
            dedup_set = self._dedup_index.setdefault(task_id, set())
            if message.dedup.dedup_key in dedup_set:
                message.dedup.is_duplicate = True
            if message.message_id in task_messages:
                message.dedup.is_duplicate = True
            task_messages[message.message_id] = message
            dedup_set.add(message.dedup.dedup_key)
            return message

    def get_messages(self, task_id: str) -> list[NormalizedMessage]:
        with self._lock:
            messages = list(self._messages_by_task.get(task_id, {}).values())
            return sorted(messages, key=lambda msg: msg.timestamp_ms)

    def get_message(self, task_id: str, message_id: str) -> NormalizedMessage | None:
        with self._lock:
            return self._messages_by_task.get(task_id, {}).get(message_id)

    def get_pending_messages(self, task_id: str, component_name: str) -> list[NormalizedMessage]:
        self._validate_component(component_name)
        with self._lock:
            result = []
            for message in self._messages_by_task.get(task_id, {}).values():
                annotation = getattr(message.annotations, component_name)
                if annotation.status in {AnnotationStatus.PENDING, AnnotationStatus.NOT_SELECTED}:
                    result.append(message)
            return sorted(result, key=lambda msg: msg.timestamp_ms)

    def update_annotation(
        self,
        task_id: str,
        message_id: str,
        component_name: str,
        annotation: Any,
    ) -> NormalizedMessage:
        self._validate_component(component_name)
        with self._lock:
            message = self._messages_by_task[task_id][message_id]
            setattr(message.annotations, component_name, annotation)
            return message

    def mark_duplicate(self, task_id: str, message_id: str) -> NormalizedMessage | None:
        with self._lock:
            message = self._messages_by_task.get(task_id, {}).get(message_id)
            if message is not None:
                message.dedup.is_duplicate = True
            return message

    def get_processed_messages(self, task_id: str) -> list[NormalizedMessage]:
        with self._lock:
            processed = []
            for message in self._messages_by_task.get(task_id, {}).values():
                statuses = [
                    message.annotations.importance.status,
                    message.annotations.deliverables.status,
                    message.annotations.topic.status,
                ]
                if all(status in {AnnotationStatus.DONE, AnnotationStatus.ERROR, AnnotationStatus.SKIPPED} for status in statuses):
                    processed.append(message)
            return sorted(processed, key=lambda msg: msg.timestamp_ms)

    def _validate_component(self, component_name: str) -> None:
        if component_name not in self._ALLOWED_COMPONENTS:
            raise ValueError(f"Unsupported component namespace: {component_name}")
