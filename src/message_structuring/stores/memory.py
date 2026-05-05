from __future__ import annotations

from threading import Lock

from message_structuring.schemas import NormalizedMessage, SummaryItem, TaskSession, TopicNode
from message_structuring.task_manager import InMemoryTaskManager
from message_structuring.timeline_store import InMemoryTimelineStore


class MemoryStructuringStore:
    def __init__(self) -> None:
        self.task_manager = InMemoryTaskManager()
        self.timeline_store = InMemoryTimelineStore()
        self._summary_items: dict[str, list[SummaryItem]] = {}
        self._summary_item_ids: dict[str, set[str]] = {}
        self._topics: dict[str, list[TopicNode]] = {}
        self._lock = Lock()

    def start_task(self, chat_id: str, activation_source: str = "manual_api", task_title: str | None = None) -> TaskSession:
        return self.task_manager.start_task(chat_id, activation_source=activation_source, task_title=task_title)

    def stop_task(self, task_id: str, reason: str = "manual") -> TaskSession | None:
        return self.task_manager.stop_task(task_id, reason=reason)

    def get_task(self, task_id: str) -> TaskSession | None:
        return self.task_manager.get_task(task_id)

    def list_tasks(self) -> list[TaskSession]:
        return self.task_manager.list_tasks()

    def get_active_task(self, chat_id: str) -> TaskSession | None:
        return self.task_manager.get_active_task(chat_id)

    def append_message(self, task_id: str, message: NormalizedMessage) -> NormalizedMessage:
        return self.timeline_store.append_message(task_id, message)

    def get_message(self, task_id: str, message_id: str) -> NormalizedMessage | None:
        return self.timeline_store.get_message(task_id, message_id)

    def get_messages(self, task_id: str) -> list[NormalizedMessage]:
        return self.timeline_store.get_messages(task_id)

    def update_annotation(self, task_id: str, message_id: str, component_name: str, annotation: object) -> NormalizedMessage:
        return self.timeline_store.update_annotation(task_id, message_id, component_name, annotation)

    def get_processed_messages(self, task_id: str) -> list[NormalizedMessage]:
        return self.timeline_store.get_processed_messages(task_id)

    def set_topics(self, task_id: str, topics: list[TopicNode]) -> None:
        with self._lock:
            self._topics[task_id] = list(topics)

    def get_topics(self, task_id: str) -> list[TopicNode]:
        with self._lock:
            return list(self._topics.get(task_id, []))

    def clear_summary_items(self, task_id: str) -> None:
        with self._lock:
            self._summary_items[task_id] = []
            self._summary_item_ids[task_id] = set()

    def add_summary_item(self, task_id: str, item: SummaryItem) -> bool:
        with self._lock:
            ids = self._summary_item_ids.setdefault(task_id, set())
            if item.summary_item_id in ids:
                return False
            ids.add(item.summary_item_id)
            self._summary_items.setdefault(task_id, []).append(item)
            return True

    def get_summary_items(self, task_id: str) -> list[SummaryItem]:
        with self._lock:
            return list(self._summary_items.get(task_id, []))
